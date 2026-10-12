# -*- coding: utf-8 -*-
"""Skein · 数据层（SQLite 单文件）

设计要点（对应 requirements §2.6）：
- 灵感池 = Material.topic_id IS NULL
- 未指派 = Material.beat_id IS NULL（不占饱和状态）
- 没有任何"应该有多少条素材"的字段：数量不预设
"""
import json
import sqlite3
import datetime
import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS topic(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  platform TEXT DEFAULT '',
  form TEXT DEFAULT '',
  target_count INTEGER DEFAULT 0,
  payoff TEXT DEFAULT '',
  capped INTEGER DEFAULT 0,
  status TEXT DEFAULT 'active',
  memory TEXT DEFAULT '',
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS beat(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic_id INTEGER NOT NULL,
  seq INTEGER NOT NULL,
  question TEXT NOT NULL,
  origin TEXT DEFAULT 'ai',
  locked INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS material(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic_id INTEGER,
  beat_id INTEGER,
  kind TEXT DEFAULT 'note',
  body TEXT DEFAULT '',
  src TEXT DEFAULT '',
  created_at TEXT
);
-- 对话落盘（§2.28）：**一期一份**，只按 topic_id 取，绝不跨期。
-- actions_json 装 {"actions":[…],"done":bool,"summary":str} —— 让"已照做"态重启后不丢。
CREATE TABLE IF NOT EXISTS chat(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic_id INTEGER NOT NULL,
  seq INTEGER NOT NULL,
  role TEXT NOT NULL,
  text TEXT DEFAULT '',
  actions_json TEXT DEFAULT '',
  ts TEXT
);
"""

# 后加的列：老库要能原地升上来，不然用户手上的库一打开就报错。
# 值 = 列定义；只在 PRAGMA 里没有这一列时才 ALTER。
MIGRATIONS = {
    "topic": {
        "memory": "TEXT DEFAULT ''",     # 这一期"已定的口径"（§2.28 一期记忆）
    },
    "beat": {
        "script": "TEXT DEFAULT ''",     # 这一段的口播稿（逐段讲法）
        "shot": "TEXT DEFAULT ''",       # 这一段要什么画面（分镜）
        "deep": "TEXT DEFAULT ''",       # 展开后"详细论证这一点"的产物
        # —— v0.10.0 池子（§2.18）——
        # placed=1 已经排进顺序（有编号）；0 = 同一期里还没编排的球（不占编号）
        # 老库的段一律默认 1：它们本来就是排好序的
        "placed": "INTEGER DEFAULT 1",
        "note": "TEXT DEFAULT ''",       # 每个球一句短评（本版由本地规则给，模型版下一版接）
        "px": "INTEGER DEFAULT -1",      # 球在池里的横向位置（千分比，-1 = 还没放过）
        "py": "INTEGER DEFAULT -1",      # 深度（千分比）：越深越暗，位置不代表顺序
    },
    "material": {
        "path": "TEXT DEFAULT ''",       # 文件素材在本地的路径（图片/视频/音频）
    },
}


def _migrate(c):
    for table, cols in MIGRATIONS.items():
        have = {r["name"] for r in c.execute("PRAGMA table_info(%s)" % table).fetchall()}
        for col, decl in cols.items():
            if col not in have:
                c.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, decl))


def _c():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def init():
    with _c() as c:
        c.executescript(SCHEMA)
        _migrate(c)


# ---------------- topic ----------------
def create_topic(name, payoff="", platform=None, form=None, target=None):
    """立项。平台 / 形态 / 目标张数从设置里取 —— 没设就留空，不兜任何写死的默认。

    传了值就按传的来（调用方要覆盖时才传）；三个参数默认 None＝"听设置的"。
    """
    if platform is None:
        platform = str(config.setting("platform") or "").strip()
    if form is None:
        form = str(config.setting("form") or "").strip()
    if target is None:
        raw = str(config.setting("target") or "").strip()
        try:
            target = int(raw) if raw else 0
        except (TypeError, ValueError):
            target = 0
    with _c() as c:
        cur = c.execute(
            "INSERT INTO topic(name,payoff,platform,form,target_count,capped,status,created_at)"
            " VALUES(?,?,?,?,?,0,'active',?)", (name, payoff, platform, form, target, _now()))
        return cur.lastrowid


def get_topic(tid):
    if not tid:
        return None
    with _c() as c:
        r = c.execute("SELECT * FROM topic WHERE id=?", (tid,)).fetchone()
        return dict(r) if r else None


def set_capped(tid, val=1):
    with _c() as c:
        c.execute("UPDATE topic SET capped=? WHERE id=?", (val, tid))


def set_payoff(tid, text):
    with _c() as c:
        c.execute("UPDATE topic SET payoff=? WHERE id=?", (text, tid))


def set_topic_name(tid, name):
    """改期名。结构、素材都不动 —— 只是这一期换个名字。"""
    with _c() as c:
        c.execute("UPDATE topic SET name=? WHERE id=?", (name, tid))


# ---------------- 一期记忆（§2.28）：这一期"已定的口径" ----------------
def set_topic_memory(tid, text):
    """写这一期的内容记忆（3~6 条口径的纯文本，行分隔）。蒸馏产物由它落库。"""
    with _c() as c:
        c.execute("UPDATE topic SET memory=? WHERE id=?", (text or "", tid))


def get_topic_memory(tid):
    return str((get_topic(tid) or {}).get("memory") or "")


# ---------------- 对话落盘（§2.28）：**一期一份，绝不跨期** ----------------
def add_chat(tid, role, text, actions=None, done=False, summary=""):
    """把一条对话写进这一期的 chat 表。返回这条的 id（主线程回写"已照做"态要用）。

    actions 非空时把 {actions,done,summary} 一起塞进 actions_json —— 重启后
    「已照做」态才不会丢回"待照做"。
    """
    if not tid:
        return None
    payload = ""
    if actions:
        payload = json.dumps({"actions": actions, "done": bool(done),
                              "summary": summary or ""}, ensure_ascii=False)
    with _c() as c:
        r = c.execute("SELECT COALESCE(MAX(seq),-1) m FROM chat WHERE topic_id=?",
                      (tid,)).fetchone()
        cur = c.execute(
            "INSERT INTO chat(topic_id,seq,role,text,actions_json,ts) VALUES(?,?,?,?,?,?)",
            (tid, r["m"] + 1, role, text or "", payload, _now()))
        return cur.lastrowid


def list_chat(tid):
    """读这一期的对话（按 seq）。**永远只读这一期** —— 这是"一期一个记忆"的硬约束。"""
    if not tid:
        return []
    with _c() as c:
        rows = c.execute("SELECT * FROM chat WHERE topic_id=? ORDER BY seq, id",
                         (tid,)).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        st = {}
        raw = (d.get("actions_json") or "").strip()
        if raw:
            try:
                st = json.loads(raw)
            except Exception:
                st = {}
        m = {"id": d["id"], "role": d.get("role") or "", "text": d.get("text") or ""}
        if st:
            m["actions"] = st.get("actions") or []
            m["done"] = bool(st.get("done"))
            m["summary"] = st.get("summary") or ""
        out.append(m)
    return out


def update_chat(cid, actions=None, done=None, summary=None):
    """改一条对话的状态（主要是"照做"落地后回写 done/summary）。"""
    with _c() as c:
        r = c.execute("SELECT actions_json FROM chat WHERE id=?", (cid,)).fetchone()
        if not r:
            return
        try:
            st = json.loads(r["actions_json"]) if (r["actions_json"] or "").strip() else {}
        except Exception:
            st = {}
        if actions is not None:
            st["actions"] = actions
        if done is not None:
            st["done"] = bool(done)
        if summary is not None:
            st["summary"] = summary
        c.execute("UPDATE chat SET actions_json=? WHERE id=?",
                  (json.dumps(st, ensure_ascii=False), cid))


def delete_chat(cid):
    with _c() as c:
        c.execute("DELETE FROM chat WHERE id=?", (cid,))


def clear_chat(tid):
    with _c() as c:
        c.execute("DELETE FROM chat WHERE topic_id=?", (tid,))


# ---------------- beat ----------------
def add_beat(tid, seq, question, origin="ai"):
    with _c() as c:
        cur = c.execute("INSERT INTO beat(topic_id,seq,question,origin,locked) VALUES(?,?,?,?,0)",
                        (tid, seq, question, origin))
        return cur.lastrowid


def list_beats(tid):
    with _c() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM beat WHERE topic_id=? ORDER BY seq", (tid,)).fetchall()]


# ---------------- 池子（§2.18）：已编排 / 未编排 ----------------
def list_placed(tid):
    """已编排的段（有编号的那些）。

    下游（brain 的提议/完成度/分镜、对话里的段号）**只认这一串** ——
    未编排的球 seq 排在后面，一旦漏进去，b["seq"]+1 那套段号就错位了。
    """
    with _c() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM beat WHERE topic_id=? AND placed=1 ORDER BY seq, id",
            (tid,)).fetchall()]


def list_unplaced(tid):
    """同一期里还没编排的球（没编号）。它们也在这一期里，只是还没排进顺序。"""
    with _c() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM beat WHERE topic_id=? AND (placed IS NULL OR placed=0)"
            " ORDER BY seq, id", (tid,)).fetchall()]


def get_beat(bid):
    with _c() as c:
        r = c.execute("SELECT * FROM beat WHERE id=?", (bid,)).fetchone()
        return dict(r) if r else None


def add_beat_unplaced(tid, question="（还没写）", origin="human"):
    """投一颗新球：先混在池里漂着，不占编号 —— 还没决定它排第几。"""
    with _c() as c:
        r = c.execute("SELECT COALESCE(MAX(seq),-1) m FROM beat WHERE topic_id=?",
                      (tid,)).fetchone()
        cur = c.execute(
            "INSERT INTO beat(topic_id,seq,question,origin,locked,placed)"
            " VALUES(?,?,?,?,0,0)", (tid, r["m"] + 1, question, origin))
        return cur.lastrowid


def reorder(tid):
    """把已编排的排到 seq 0..n-1、未编排接在后面。

    为什么整段重排而不是"换号"：seq 有重复值时换号会换出诡异顺序，重排是幂等的。
    为什么未编排必须排在后面：brain.py 用 b["seq"]+1 当段号，未编排的球不能挤进来。
    """
    with _c() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT id FROM beat WHERE topic_id=?"
            " ORDER BY placed DESC, seq, id", (tid,)).fetchall()]
        for k, r in enumerate(rows):
            c.execute("UPDATE beat SET seq=? WHERE id=?", (k, r["id"]))


def place_beat(bid, index):
    """把它排进第 index 位（0-based）—— 球上随即出现编号。"""
    with _c() as c:
        row = c.execute("SELECT topic_id FROM beat WHERE id=?", (bid,)).fetchone()
        if not row:
            return
        tid = row[0]
        placed = [r["id"] for r in c.execute(
            "SELECT id FROM beat WHERE topic_id=? AND placed=1 AND id<>?"
            " ORDER BY seq, id", (tid, bid)).fetchall()]
        j = max(0, min(int(index), len(placed)))
        placed.insert(j, bid)
        for k, i in enumerate(placed):
            c.execute("UPDATE beat SET seq=?, placed=1 WHERE id=?", (k, i))
        rest = [r["id"] for r in c.execute(
            "SELECT id FROM beat WHERE topic_id=? AND id<>?"
            " AND (placed IS NULL OR placed=0) ORDER BY seq, id", (tid, bid)).fetchall()]
        for k, i in enumerate(rest):
            c.execute("UPDATE beat SET seq=?, placed=0 WHERE id=?", (len(placed) + k, i))


def unplace_beat(bid):
    """把它移出编排：编号没了，退成一颗粒子混在池里（素材一段都不丢）。"""
    with _c() as c:
        row = c.execute("SELECT topic_id FROM beat WHERE id=?", (bid,)).fetchone()
        if not row:
            return
        tid = row[0]
        c.execute("UPDATE beat SET placed=0 WHERE id=?", (bid,))
        rows = [dict(r) for r in c.execute(
            "SELECT id FROM beat WHERE topic_id=?"
            " ORDER BY placed DESC, seq, id", (tid,)).fetchall()]
        for k, r in enumerate(rows):
            c.execute("UPDATE beat SET seq=? WHERE id=?", (k, r["id"]))


def set_beat_note(bid, text):
    """每个球一句短评（本版由本地规则给，模型版下一版接）。"""
    with _c() as c:
        c.execute("UPDATE beat SET note=? WHERE id=?", (text or "", bid))


def set_beat_pos(bid, px, py):
    """球在池里的落点（千分比）。顺序不靠位置，所以这只是"它待在哪儿"。"""
    with _c() as c:
        c.execute("UPDATE beat SET px=?, py=? WHERE id=?",
                  (max(0, min(1000, int(px))), max(0, min(1000, int(py))), bid))


def set_beat_lock(bid, val=1):
    with _c() as c:
        c.execute("UPDATE beat SET locked=? WHERE id=?", (val, bid))


def insert_beat(tid, seq, question, origin="human"):
    """在第 seq 个位置插一段（人手动加的）：这一位之后的整体后移。
    origin='human' 与 AI 加的区分开 —— 谁把这个节点放进去的，事后再看还认得出。"""
    with _c() as c:
        c.execute("UPDATE beat SET seq=seq+1 WHERE topic_id=? AND seq>=?", (tid, seq))
        cur = c.execute("INSERT INTO beat(topic_id,seq,question,origin,locked) VALUES(?,?,?,?,0)",
                        (tid, seq, question, origin))
        return cur.lastrowid


def move_beat(bid, delta):
    """把一段在**已编排**里上移/下移一格（delta = -1 / +1）。到边界就不动。

    未编排的球不参与排序 —— 它没有编号，谈不上"往上挪一格"。
    """
    with _c() as c:
        row = c.execute("SELECT topic_id FROM beat WHERE id=?", (bid,)).fetchone()
        if not row:
            return
        ids = [r["id"] for r in c.execute(
            "SELECT id FROM beat WHERE topic_id=? AND placed=1 ORDER BY seq, id",
            (row[0],)).fetchall()]
    if bid not in ids:
        return
    i = ids.index(bid)
    move_beat_to(bid, i + (1 if delta > 0 else -1))


def move_beat_to(bid, index):
    """把它挪到**已编排**里的第 index 位（0-based）。

    未编排的球传进来 = "排进第 index 位"（等价 place_beat），因为池子里
    "把球捞进队列"和"改编号"本来就是同一个动作的两个说法。

    实现上整段重排 seq，不去和邻居"换号"—— 换号在 seq 有重复值时会换出
    诡异顺序，重排是幂等的。收尾时把未编排的 seq 统一压到已编排之后，
    这样 brain.py 里 b["seq"]+1 永远只数到已编排的球。
    """
    with _c() as c:
        row = c.execute("SELECT topic_id, placed FROM beat WHERE id=?", (bid,)).fetchone()
        if not row:
            return
        tid = row["topic_id"]
        placed = [r["id"] for r in c.execute(
            "SELECT id FROM beat WHERE topic_id=? AND placed=1 ORDER BY seq, id",
            (tid,)).fetchall()]
        if bid in placed:
            i = placed.index(bid)
            j = max(0, min(int(index), len(placed) - 1))
            if j != i:
                placed.insert(j, placed.pop(i))
        else:
            placed.insert(max(0, min(int(index), len(placed))), bid)
        for k, r in enumerate(placed):
            c.execute("UPDATE beat SET seq=?, placed=1 WHERE id=?", (k, r["id"]))
        rest = [r["id"] for r in c.execute(
            "SELECT id FROM beat WHERE topic_id=?"
            " AND (placed IS NULL OR placed=0) ORDER BY seq, id", (tid,)).fetchall()]
        for k, r in enumerate(rest):
            c.execute("UPDATE beat SET seq=? WHERE id=?", (len(placed) + k, r["id"]))


def update_beat(bid, question=None, origin="ai"):
    if question is None:
        return
    with _c() as c:
        c.execute("UPDATE beat SET question=?, origin=? WHERE id=?", (question, origin, bid))


def set_beat_script(bid, text):
    """存这一段的口播稿（逐段讲法）"""
    with _c() as c:
        c.execute("UPDATE beat SET script=? WHERE id=?", (text or "", bid))


def set_beat_shot(bid, text):
    """存这一段要什么画面（分镜里的画面描述）"""
    with _c() as c:
        c.execute("UPDATE beat SET shot=? WHERE id=?", (text or "", bid))


def set_beat_deep(bid, text):
    """存"详细论证这一点"的产物（展开后看到的深挖内容）"""
    with _c() as c:
        c.execute("UPDATE beat SET deep=? WHERE id=?", (text or "", bid))


def delete_beat(bid):
    """撤下一段：段里的素材退回"未指派"，不丢东西"""
    with _c() as c:
        c.execute("UPDATE material SET beat_id=NULL WHERE beat_id=?", (bid,))
        c.execute("DELETE FROM beat WHERE id=?", (bid,))


def merge_beats(keep_id, drop_id):
    """合并两段：素材并到保留的那段，被并的那段撤下"""
    with _c() as c:
        c.execute("UPDATE material SET beat_id=? WHERE beat_id=?", (keep_id, drop_id))
        c.execute("DELETE FROM beat WHERE id=?", (drop_id,))


def renumber(tid):
    """删/并过之后把 seq 收拢成连续的 0..n-1"""
    with _c() as c:
        rows = c.execute("SELECT id FROM beat WHERE topic_id=? ORDER BY seq, id", (tid,)).fetchall()
        for i, r in enumerate(rows):
            c.execute("UPDATE beat SET seq=? WHERE id=?", (i, r["id"]))


# ---------------- material ----------------
def add_material(topic_id, beat_id, kind, body, src="", path=""):
    with _c() as c:
        cur = c.execute("INSERT INTO material(topic_id,beat_id,kind,body,src,path,created_at)"
                        " VALUES(?,?,?,?,?,?,?)",
                        (topic_id, beat_id, kind, body, src, path, _now()))
        return cur.lastrowid


def get_material(mid):
    with _c() as c:
        r = c.execute("SELECT * FROM material WHERE id=?", (mid,)).fetchone()
        return dict(r) if r else None


def delete_material(mid):
    with _c() as c:
        c.execute("DELETE FROM material WHERE id=?", (mid,))


def assign(mid, beat_id):
    with _c() as c:
        c.execute("UPDATE material SET beat_id=? WHERE id=?", (beat_id, mid))


def list_inbox():
    with _c() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM material WHERE topic_id IS NULL ORDER BY id DESC").fetchall()]


def take_inbox_into(tid):
    """立项时把灵感池全部归到这一期（仍保持未指派）"""
    with _c() as c:
        cur = c.execute("UPDATE material SET topic_id=? WHERE topic_id IS NULL", (tid,))
        return cur.rowcount


def list_unassigned(tid):
    with _c() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM material WHERE topic_id=? AND beat_id IS NULL ORDER BY id DESC",
            (tid,)).fetchall()]


def materials_by_beat(tid):
    d = {}
    with _c() as c:
        rows = c.execute("SELECT * FROM material WHERE topic_id=? AND beat_id IS NOT NULL ORDER BY id",
                         (tid,)).fetchall()
    for r in rows:
        d.setdefault(r["beat_id"], []).append(dict(r))
    return d


def count_fed(tid):
    with _c() as c:
        r = c.execute("SELECT COUNT(*) n FROM material WHERE topic_id=? AND beat_id IS NOT NULL",
                      (tid,)).fetchone()
        return r["n"]


# ---------------- 撤销（§2.21）：整期快照 ----------------
def snapshot_topic(tid):
    """把这一期照一张快照（topic / beat / 这一期的 material）。

    为什么整期照而不是逐条算逆操作：对话「照做」与提议「采纳」会牵动
    段增删、重排、素材改派、payoff、capped —— 逐条算逆操作最容易漏的正是
    「撤下一段时素材退回未指派」这类跨表副作用。整块照下来、整块换回去，
    是唯一不会漏的做法（数据量是"个人一期"，快照很小）。
    """
    if not tid:
        return None
    with _c() as c:
        t = c.execute("SELECT * FROM topic WHERE id=?", (tid,)).fetchone()
        if not t:
            return None
        return {
            "topic": dict(t),
            "beat": [dict(r) for r in c.execute(
                "SELECT * FROM beat WHERE topic_id=?", (tid,)).fetchall()],
            "material": [dict(r) for r in c.execute(
                "SELECT * FROM material WHERE topic_id=?", (tid,)).fetchall()],
        }


def restore_topic(tid, snap):
    """把这一期还原到快照那一刻：整表换回去（行 id 原样保留）。

    只碰这一期自己的行，灵感池（topic_id IS NULL）不受影响。
    """
    if not tid or not snap or not snap.get("topic"):
        return
    t = snap["topic"]
    with _c() as c:
        cols = [k for k in t.keys() if k != "id"]
        c.execute("UPDATE topic SET %s WHERE id=?" % ",".join("%s=?" % k for k in cols),
                  [t[k] for k in cols] + [tid])
        c.execute("DELETE FROM material WHERE topic_id=?", (tid,))
        c.execute("DELETE FROM beat WHERE topic_id=?", (tid,))
        for table, key in (("beat", "beat"), ("material", "material")):
            for r in snap.get(key) or []:
                ks = list(r.keys())
                c.execute("INSERT INTO %s(%s) VALUES(%s)" % (
                    table, ",".join(ks), ",".join("?" * len(ks))), [r[k] for k in ks])
