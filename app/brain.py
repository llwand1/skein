# -*- coding: utf-8 -*-
"""Skein · 大脑

**这里是把"写死的常量"全部收进来的地方。** M0/M1 用本地规则跑闭环、不联网；
M3 接 LLM 时只需要把这 6 个函数的实现换掉，界面和 store 一行都不用动：

    seed_beats(name, inbox)        立项时的「初始理解」——每期都不同
    payoff_for(name, materials)    这一期最想让人记住的一下——从素材里长出来
    proposals(beats, mbb, ...)     AI 对结构想做的改动：插入/合并/归档/收段（人裁决）
    next_step(beats, mbb, capped)  实时「下一步」+ 带数字的理由
    estimate(beats, mbb)           完成度估算：同一段边际递减，封顶 92%
    script_for / shot_for / storyboard   成片稿：逐段口播稿 + 分镜表（"交出去"的那个物件）

铁律（requirements §2.5 / P-010 / P-013）：
- 不给固定清单：立项只给「初始理解」，结构随素材增删。
- 完成度只是估算，**上限 92%，永远到不了 100**；「够了」永远由人点。
- 没有任何一步是提前定好的：连问题措辞都由当期主题 + 素材派生。
"""
import json
import re
from collections import Counter

import config
import llm

CAP = 92  # 完成度估算上限：永远到不了 100

KIND_NAME = {"text": "文字", "image": "图片", "video": "视频", "note": "速记",
             "audio": "音频", "file": "文件", "link": "链接"}


def kind_name(k):
    """素材类型给人看的名字。判不出来就把原值原样显示，不编。"""
    return KIND_NAME.get(k, k or "")

# ---------------- 轻量"读得懂" ----------------
_LAT = re.compile(r"[A-Za-z][A-Za-z0-9_+#.\-]{1,}")
_EPISODE = re.compile(r"^[Ee]\d{2,4}$")


def terms(texts, k=6, min_df=1):
    """只认"术语"（ASCII 技术词），并按 document frequency（出现在几份素材里）计数。

    为什么不做中文 n-gram：没有分词器时，滑动窗口切出来的全是「向量数」「量数据」
    这种半截词。往问题里注入半截词比不注入更糟，所以宁可少猜 ——
    而 MCP / RAG / Agent / WebGL 这类术语恰好是这个创作者内容里最硬的锚点。
    """
    df = Counter()
    for t in texts:
        for w in set(_LAT.findall(t or "")):
            if len(w) >= 2 and not _EPISODE.match(w):
                df[w] += 1
    return [w for w, c in df.most_common(k) if c >= min_df]


def subject_of(name):
    """从期名里取出"主体词"：E005 · MCP 到底创新在哪 → MCP"""
    s = name.split("·")[-1].strip() if "·" in name else (name or "").strip()
    for cut in ("到底", "讲清楚", "是什么", "怎么", "如何", "为什么", "为啥"):
        if cut in s:
            s = s.split(cut)[0].strip()
    return s or (name or "").strip()


def _sim(a, b):
    """字符 2-gram Jaccard，用来判断两段是不是在问同一件事"""
    def grams(s):
        s = re.sub(r"[\s，。？！、：；「」（）()\-]", "", s or "")
        return {s[i:i + 2] for i in range(max(0, len(s) - 1))}
    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / float(len(ga | gb))


# ---------------- 状态 ----------------
def status_of(beat, mats):
    if beat.get("locked"):
        return "locked"
    return "supported" if mats else "empty"


def counts(beats, mbb):
    c = {"empty": 0, "supported": 0, "locked": 0}
    for b in beats:
        c[status_of(b, mbb.get(b["id"]))] += 1
    return c


# ---------------- ② 每个球一句短评（池子用）----------------
def comment_local(beat, mats):
    """池子里每颗球下面的那句短评。本版是本地规则（模型版下一版接）。

    只说**手上能看出来的事**：攒到什么程度、素材是什么成分、还是空的要说清。
    刻意**不评价"讲清了没有"** —— 那是人的判断（P-010）。
    """
    mats = list(mats or [])
    n = len(mats)
    st = status_of(beat, mats)
    comp = ""
    if n:
        c = Counter((m.get("kind") or "note") for m in mats)
        comp = "、".join("%s×%d" % (kind_name(k), v) for k, v in c.most_common(2))
    if st == "locked":
        body = "已确认。%d 个素材%s。" % (n, ("（" + comp + "）") if comp else "")
    elif not n:
        body = "这一球还空着 —— 先想清楚它要回答什么，或者喂一条素材进来。"
    elif n == 1:
        body = "只有 1 个素材（%s），单点还撑不住一整段。" % comp
    else:
        body = "%d 个素材（%s）；手上是这样，够不够由你定。" % (n, comp)
    if not beat.get("placed", 1):
        return "还没排进顺序：" + body
    return body


# ---------------- ① 初始理解（每期不同）----------------
# 三档问法：主轴（反常识 / 为什么难 / 核心机制），每档给几种说法，
# 由期名决定用哪一档 —— 换一期，问题不只是换词，句式也跟着变。
_Q1 = ["「%s」最反直觉的地方，到底是哪一点？",
       "关于「%s」，最容易被讲错的是哪一句？",
       "「%s」里最不像常识的那一步是什么？"]
_Q2 = ["为什么「%s」不能只用「%s」来解释？",
       "「%s」为什么不是「%s」就能说清的？",
       "把「%s」全归到「%s」上，会漏掉什么？"]
_Q2_PLAIN = ["为什么「%s」不是看上去那么简单？",
             "「%s」到底难在哪一步？",
             "把「%s」想简单，最容易在哪翻车？"]
_Q3 = ["「%s」和「%s」之间，机制是怎么接上的？",
       "把「%s」拆开看，核心机制是怎么转起来的？",
       "「%s」真正在转的那一环，在哪？"]


def seed_beats(name, inbox_bodies=()):
    """立项时只给 3 段，且措辞由主题 + 灵感池派生 —— 换一期就换一套问题。

    只注入**一个**术语：初始理解就该是"初始"的，把素材里的概念一次列全
    反而又变成清单了。剩下的概念留给后续的结构提议去补。
    """
    s = subject_of(name)
    # 术语要"不在主体词里"，这样「为什么 A 不能只用 B 解释」里的 B 才真的是另一个概念
    ts = [w for w in terms(list(inbox_bodies) + [name], 6) if w not in s]
    a = ts[0] if ts else None
    pick = (sum(ord(c) for c in name) + len(s)) % 3

    q1 = _Q1[pick] % s
    q2 = (_Q2[pick] % (s, a)) if a else (_Q2_PLAIN[pick] % s)
    q3 = _Q3[1 + (pick % 2)] % s
    return [q1, q2, q3]


# ---------------- ② payoff（从素材里长出来）----------------
_ASSERT = ("不是", "而是", "其实", "本质", "关键", "核心", "最小", "误区",
           "错在", "真相", "恰恰", "只是", "并不")


def payoff_for(name, materials=()):
    """从素材里挑最像"断言"的一句当 payoff；实在没有就留一个由主题派生的待定句。"""
    best, score = "", 0
    for m in materials:
        body = (m.get("body") or "").strip().replace("\n", " ")
        if len(body) < 6:
            continue
        sc = sum(1 for h in _ASSERT if h in body)
        sc += 1 if 10 <= len(body) <= 56 else 0
        if sc > score:
            best, score = body, sc
    if best and score >= 1:
        return best if len(best) <= 56 else best[:54] + "…"
    return "（待定）%s —— 这一期你最想让人记住哪一下？" % subject_of(name)


# ---------------- ③ 结构提议（人裁决）----------------
def proposals(beats, mbb, unassigned, capped):
    """AI 想对结构做的改动。每条都只是提议，采纳与否由人点。"""
    out = []
    if capped or not beats:
        return out

    # ① 某段堆了不少素材还没确认 → 再多也不改变讲法了
    for b in beats:
        ms = mbb.get(b["id"]) or []
        if len(ms) >= 3 and not b.get("locked"):
            out.append({
                "kind": "lock", "beat": b["id"], "key": "lock-%d" % b["id"],
                "text": "第 %d 段已经堆了 %d 条素材 —— 再加也不会改变讲法了，建议收「够了」"
                        % (b["seq"] + 1, len(ms)),
                "action": "够了，收这一段"})
            break

    # ② 没指派的素材**反复**在说某个术语，但结构里没有这一段 → 插一段
    known = " ".join(b["question"] for b in beats)
    kw = [k for k in terms([m.get("body") or "" for m in unassigned], 8, min_df=2)
          if k not in known]
    if kw:
        out.append({
            "kind": "add", "seq": len(beats), "subject": kw[0], "key": "add-%s" % kw[0],
            "text": "待指派素材里反复出现「%s」，但结构里没有这一段 —— 要不要补一段专门问它？"
                    % kw[0],
            "action": "插入一段：%s" % kw[0]})

    # ③ 两段问的像是同一件事 → 合并
    for i in range(len(beats)):
        for j in range(i + 1, len(beats)):
            if _sim(beats[i]["question"], beats[j]["question"]) >= 0.45:
                out.append({
                    "kind": "merge", "a": beats[i]["id"], "b": beats[j]["id"],
                    "key": "merge-%d-%d" % (beats[i]["id"], beats[j]["id"]),
                    "text": "第 %d 段和第 %d 段问的像是同一件事，可以并成一段"
                            % (beats[i]["seq"] + 1, beats[j]["seq"] + 1),
                    "action": "合并这两段"})
                break
        else:
            continue
        break

    # ④ 排在后面又一条素材都没有 → 也许这一期不需要它
    if len(beats) > 4:
        for b in reversed(beats):
            if not (mbb.get(b["id"]) or []) and b["seq"] >= 3:
                out.append({
                    "kind": "drop", "beat": b["id"], "key": "drop-%d" % b["id"],
                    "text": "第 %d 段一直空着，又排在后面 —— 也许这一期不需要它"
                            % (b["seq"] + 1),
                    "action": "先撤下这一段"})
                break
    return out[:3]


# ---------------- ④ 下一步（带数字的理由）----------------
def next_step(beats, mbb, capped):
    """实时推「下一步」：每收到一条素材就重算"""
    if capped:
        return "你已封顶 —— 去准备发布", "已按你的封顶收口，不再估算完成度。"
    if not beats:
        return "先给这一期定个方向", "结构还是空的。喂一条素材，我照着它长出第一段。"

    empt = [b for b in beats if status_of(b, mbb.get(b["id"])) == "empty"]
    if empt:
        b = empt[0]
        return ("补第 %d 段：%s" % (b["seq"] + 1, b["question"]),
                "还有 %d 段一条素材都没有，先补最前面那段最划算。" % len(empt))

    sup = [b for b in beats if status_of(b, mbb.get(b["id"])) == "supported"]
    if sup:
        b = min(sup, key=lambda x: len(mbb.get(x["id"]) or []))
        n = len(mbb.get(b["id"]) or [])
        return ("确认第 %d 段够不够（点「够了」）" % (b["seq"] + 1),
                "每段都有素材了。第 %d 段目前只有 %d 条 —— 讲清没有你说了算，我说不了。"
                % (b["seq"] + 1, n))

    return ("结构上不缺了 —— 你可以封顶收口",
            "覆盖已满。要不要封顶？上限由你定，我给不了 100%。")


# ---------------- ⑤ 完成度（边际递减）----------------
def estimate(beats, mbb):
    """覆盖 + 支撑强度。同一段多加素材**边际递减**（解释力饱和），封顶 92%。

    第 2 条 +0.17、第 3 条 +0.085、第 4 条 +0.043…… 几何递减，极限 0.84。
    所以"再多喂几条"永远推不到满 —— 剩下的那一格只能由人点。
    """
    if not beats:
        return 0
    total = 0.0
    for b in beats:
        if b.get("locked"):
            total += 1.0
            continue
        n = len(mbb.get(b["id"]) or [])
        if n <= 0:
            continue
        total += min(0.85, 0.50 + 0.34 * (1.0 - 0.5 ** (n - 1)))
    return min(CAP, int(round(total / len(beats) * 100.0)))


# ---------------- ⑦ 成片稿：逐段口播稿 + 分镜（"交出去"的那个物件）----------------
def script_for(topic, beat, mats):
    """本地规则的逐段口播稿：把"这一段的素材"连成能念的话。

    刻意不编素材里没有的事实 —— 本地规则只能做"把手上有的串起来"，
    真正的写稿是模型那一版（llm_storyboard）。
    """
    segs = [(m.get("body") or "").strip() for m in mats if (m.get("body") or "").strip()]
    segs = [s for s in segs if s]
    if not segs:
        return ""
    if len(segs) == 1:
        return segs[0] if segs[0].endswith(("。", "！", "？")) else segs[0] + "。"
    out = [segs[0] if segs[0].endswith(("。", "！", "？")) else segs[0] + "。"]
    for s in segs[1:3]:
        out.append("也就是说，" + s + ("" if s.endswith(("。", "！", "？")) else "。"))
    return "".join(out)


def shot_for(beat, mats):
    """本地规则的分镜：这一段要什么画面。

    有图片/视频就直接说用它；没有就如实写"待定 + 需要什么样的画面"，
    不假装知道该配什么。
    """
    pics = [m for m in mats if m.get("kind") in ("image", "video")]
    if pics:
        names = "、".join(((m.get("body") or "").strip()[:20] or "（无题）") for m in pics[:3])
        return "用已有的：%s" % names
    return "待定 —— 需要一段能证明「%s」的画面（截图 / 录屏 / 示意图）" % beat["question"][:26]


def deepen_local(beat, mats):
    """本地规则的"详细论证"：把这一段现有的素材排成论点 + 支撑。

    只做"把手上有的理一遍"，素材撑不住的地方**如实标出来缺什么** ——
    本地规则没有能力替你想出新论据，也不该假装能。
    """
    segs = [(m.get("body") or "").strip() for m in mats if (m.get("body") or "").strip()]
    out = ["要立住的点：%s" % beat["question"], ""]
    if not segs:
        out.append("手上还一条素材都没有 —— 先喂一条能支撑这句话的东西，哪怕是一句原话。")
        return "\n".join(out)
    out.append("手上有的：")
    for i, s in enumerate(segs, 1):
        out.append("  %d. %s" % (i, s))
    out.append("")
    if len(segs) < 2:
        out.append("只有一条 —— 论证还站不住：缺一个能对上它的另一面（反例、数据、或别人的说法）。")
    else:
        out.append("两条以上了，接下来该确认的是：这几条说的是不是同一件事？"
                   "有没有互相矛盾的地方？哪一条最硬？")
    out.append("（这段是本地规则理的，只能帮你清点已有的；真正想出新论据要模型在线。）")
    return "\n".join(out)


def spread_local(beat, mats):
    """本地规则的"发散"：从这一段的关键词里提出可以延伸的方向。

    每个方向都带"依据"（是从哪条素材看出来的），不凭空给点子。
    """
    kw = terms([(m.get("body") or "") for m in mats], 4, min_df=1)
    out = []
    for k in kw[:3]:
        out.append("「%s」还有没有更值得问的一层？（从这一段的素材里看出来的）" % k)
    if not out:
        out.append("这一段还没有素材，先喂两条再发散 —— 否则只能瞎猜。")
    return out


def storyboard(topic, beats, mbb):
    """把这一期编成"能交出去"的分镜表。纯拼装，不改任何数据。

    每行 = 段号 / 这一段要回答什么 / 口播稿 / 要什么画面 / 手上素材数。
    """
    rows = []
    for b in beats:
        ms = mbb.get(b["id"]) or []
        rows.append({
            "seq": b["seq"] + 1,
            "question": b["question"],
            "script": (b.get("script") or "").strip() or script_for(topic, b, ms),
            "shot": (b.get("shot") or "").strip() or shot_for(b, ms),
            "have": len(ms),
            "files": [(m.get("body") or "") for m in ms if (m.get("path") or "")],
        })
    return rows


def _md_cell(s):
    """表格单元格里的 | 和换行要处理掉，否则导出的表会散架。"""
    return " ".join((s or "").split()).replace("|", "\\|") or "—"


def storyboard_md(topic, beats, mbb):
    """分镜表的 Markdown（导出文件 / 复制到剪贴板用）。"""
    out = ["# %s" % topic["name"], ""]
    pf = (topic.get("payoff") or "").strip()
    if pf:
        out += ["**最想让人记住的一下**：%s" % pf, ""]
    out += ["| 段 | 这一段要回答 | 口播稿 | 要什么画面 | 手上素材 |",
            "|---|---|---|---|---|"]
    for r in storyboard(topic, beats, mbb):
        out.append("| %d | %s | %s | %s | %d |"
                   % (r["seq"], _md_cell(r["question"]), _md_cell(r["script"]),
                      _md_cell(r["shot"]), r["have"]))
    out += ["", "> 由 Skein 生成 · 每一段的素材文件都在 `assets/` 下，可直接拖进剪辑软件。"]
    return "\n".join(out) + "\n"


# ================= LLM 版 =================
# 上面六个函数是"接口契约"，这里是它们的模型实现。任何一步失败都返回 None，
# 调用方保留本地规则的结果 —— 模型掉线时 Skein 依然能跑完闭环，只是"没那么懂"。
# 系统提示词（人设）**不写死**：可以在设置里改；留空就用下面这段**通用**兜底 ——
# 不预设任何具体人设 / 领域，Skein 对谁都是一样的通用叙事工具（§2.25）。
SYS_FALLBACK = ("你在帮一个知识类短视频创作者整理一期节目的叙事骨架。"
                "骨架的每一项都必须是**一个必须回答的问题**，不是任务名、不是标题。"
                "素材数量不预设：一件事讲清楚最重要，一段讲不清就多写几段。"
                "只输出 JSON，不要解释、不要客套。")


def sys_base():
    """当前系统提示词：设置里填了就用人的，没填就用通用兜底。

    末尾再拼上 user.md（跨期用户记忆，§2.29）—— **只在文件存在且非空时**才拼；
    没有这个文件时行为与从前完全一致。
    """
    try:
        v = str(config.load_settings().get("sys_prompt") or "").strip()
    except Exception:
        v = ""
    base = v or SYS_FALLBACK
    u = load_user_md()
    return base + (USER_TAIL + u if u else "")


# ---------------- user.md（§2.29 / P-020）：跨期用户记忆，**只读** ----------------
USER_MAX_LINES = 200    # 最多读 200 行
USER_MAX_CHARS = 8000   # 或 8KB —— 防有人贴一整本书把上下文撑爆
USER_TAIL = "\n\n【关于这位用户的习惯（他自己记的，只作参考）】\n"


def load_user_md():
    """读 user.md。文件不存在 / 空 → 返回 ""（不兜任何默认）；限长 200 行 / 8KB。"""
    try:
        with open(config.USER_PATH, "r", encoding="utf-8") as f:
            txt = f.read()
    except Exception:
        return ""
    txt = (txt or "").strip()
    if not txt:
        return ""
    lines = txt.splitlines()
    if len(lines) > USER_MAX_LINES:
        txt = "\n".join(lines[:USER_MAX_LINES])
    return txt[:USER_MAX_CHARS]


def _idx(v, beats):
    try:
        i = int(v)
    except (TypeError, ValueError):
        return None
    return i if 0 <= i < len(beats) else None


def _ctx(beats, mbb, per_beat=2):
    """给模型看的结构快照：**必须带上素材原文**。

    只给"第 N 段（2 条素材）"这种统计，模型就只能凭空编理由 ——
    实测它真的编出过一条不存在的素材标题来支撑"这段够了"。
    """
    rows = []
    for i, b in enumerate(beats):
        ms = mbb.get(b["id"]) or []
        rows.append("%d. [%s] %s（%d 条素材）"
                    % (i, status_of(b, ms), b["question"], len(ms)))
        for m in ms[:per_beat]:
            body = (m.get("body") or "").strip().replace("\n", " ")[:70]
            if body:
                rows.append("     素材：%s" % body)
    return "\n".join(rows) or "（还没有结构）"


def _bodies(materials, n=12):
    rows = [("- " + (m.get("body") or "").strip()[:120])
            for m in materials[:n] if (m.get("body") or "").strip()]
    return "\n".join(rows) or "（空）"


def llm_seed_beats(name, inbox_bodies):
    got = llm.ask_json(sys_base(),
        "期名：%s\n灵感池里已经收了这些素材：\n%s\n\n"
        "给出 **3 个**必须回答的问题，作为这一期的「初始理解」。"
        "别一次列全 —— 后面会随素材增删，这里只要最关键的 3 个。\n"
        '输出 {"beats":["问题1","问题2","问题3"]}'
        % (name, "\n".join("- " + t for t in inbox_bodies) or "（空）"))
    if isinstance(got, dict) and isinstance(got.get("beats"), list):
        bs = [str(x).strip() for x in got["beats"] if str(x).strip()]
        if len(bs) >= 3:
            return bs[:3]
    return None


def llm_payoff(name, materials):
    got = llm.ask_json(sys_base(),
        "期名：%s\n已有素材：\n%s\n\n"
        "这一期「最想让人记住的那一下」是什么？要一句能被记住的断言"
        "（不是提问、不是标题、不写「本期讲…」），不超过 40 字。\n"
        '输出 {"payoff":"..."}' % (name, _bodies(materials)))
    if isinstance(got, dict):
        p = str(got.get("payoff") or "").strip()
        if 6 <= len(p) <= 60:
            return p
    return None


def llm_search_queries(topic, beats, mbb, focus=None):
    """按「当前这一期」推荐几条能直接拿去搜的查询词（P-021 / §2.30）。

    搜索排序不是我们的强项，我们强在"懂这一期在讲什么" —— 所以把
    期名 / payoff / 结构快照（每段问题 + 前 2 条素材原文）交给模型，
    让它给出 2~4 条**具体查询词**（不是话题名、不是问句），点一下就走
    现成的 Bing 免 key 通道搜（§2.22）。

    focus 非空 = 只就选中的那一颗球推荐（这一段的搜法）。
    返回 list[str] 或 None（没模型 / 失败 / 不够 2 条 → None，调用方静默降级）。
    """
    if not llm.available():
        return None
    topic = topic or {}
    beats = beats or []
    mbb = mbb or {}
    focus_line = ""
    if isinstance(focus, dict):
        ms = (mbb.get(focus.get("id")) or [])[:2]
        bodies = "；".join((m.get("body") or "").strip()[:40]
                          for m in ms if (m.get("body") or "").strip())
        focus_line = ("\n只针对这一段推荐：%s%s\n"
                      % (focus.get("question") or "",
                         ("（手上素材：%s）" % bodies) if bodies else ""))
    got = llm.ask_json(
        sys_base() + " 现在只做一件事：帮他想几条**具体能直接搜的查询词**。",
        "期名：%s\npayoff：%s\n当前结构：\n%s\n%s\n"
        "这一期该去网上搜什么？给 2~4 条**具体的搜索查询词** —— "
        "要能直接粘进搜索框搜，尽量带上这一期里的专有名词 / 技术术语；"
        "不要话题名、不要问句、不要解释、不要编号。每条不超过 30 字。\n"
        '输出 {"queries":["查询词1","查询词2"]}'
        % (topic.get("name") or "", topic.get("payoff") or "（未定）",
           _ctx(beats, mbb), focus_line))
    if not isinstance(got, dict) or not isinstance(got.get("queries"), list):
        return None
    out = []
    for q in got["queries"]:
        s = str(q).strip().strip("「」\"'").lstrip("-·•*0123456789.、) ").strip()
        if 2 <= len(s) <= 40 and s not in out:
            out.append(s)
    return out[:4] if len(out) >= 2 else None


def llm_proposals(beats, mbb, unassigned, capped):
    """让模型看结构 + 未指派素材，提最多 3 条改动。返回内部 proposal 结构，或 None。"""
    if capped or not beats:
        return None
    got = llm.ask_json(sys_base(),
        "当前结构：\n%s\n\n还没指派的素材：\n%s\n\n"
        "看这些，结构该怎么动？给 0~3 条，按重要性排。kind 只能是：\n"
        "  add   补一段 —— subject 填要问的那个概念\n"
        "  lock  某段素材已经够了、建议收口 —— beat 填段号（从 0 数）\n"
        "  merge 两段在问同一件事 —— a / b 填段号（从 0 数）\n"
        "  drop  某段多余 —— beat 填段号（从 0 数）\n"
        "每条配一句给人看的 why（要说清依据）。没什么值得动的就返回空数组。\n"
        '输出 {"proposals":[{"kind":"add","subject":"X","why":"..."}]}'
        % (_ctx(beats, mbb), _bodies(unassigned)))
    if not isinstance(got, dict) or not isinstance(got.get("proposals"), list):
        return None
    out = []
    for p in got["proposals"][:3]:
        if not isinstance(p, dict):
            continue
        kind = str(p.get("kind") or "").strip()
        why = str(p.get("why") or "").strip() or "模型觉得这里该动一下"
        if kind == "add":
            sub = str(p.get("subject") or "").strip()
            if not (2 <= len(sub) <= 24):
                continue
            out.append({"kind": "add", "seq": len(beats), "subject": sub,
                        "key": "llm-add-%s" % sub,
                        "text": why, "action": "插入一段：%s" % sub})
        elif kind in ("lock", "drop"):
            i = _idx(p.get("beat"), beats)
            if i is None:
                continue
            b = beats[i]
            # 一条素材都没有就建议"够了"，和"饱和"的定义直接矛盾 —— 丢掉
            if kind == "lock" and len(mbb.get(b["id"]) or []) < 2:
                continue
            out.append({"kind": kind, "beat": b["id"],
                        "key": "llm-%s-%d" % (kind, b["id"]), "text": why,
                        "action": "够了，收这一段" if kind == "lock" else "先撤下这一段"})
        elif kind == "merge":
            i, j = _idx(p.get("a"), beats), _idx(p.get("b"), beats)
            if i is None or j is None or i == j:
                continue
            a, b2 = beats[min(i, j)], beats[max(i, j)]
            out.append({"kind": "merge", "a": a["id"], "b": b2["id"],
                        "key": "llm-merge-%d-%d" % (a["id"], b2["id"]),
                        "text": why, "action": "合并这两段"})
    return out


# ---------------- AI 工具调用（§2.26）：改池子里的**任意一颗球** ----------------
# 混合协议：优先让模型走**原生 tool_calls**（llm.chat_message 带 tools），
# 网关不支持 tools 时自动退回结构化 JSON（同一份 name/args 形状）。
# 两种来源最后都归一成同一份 actions = [{"tool":名,"args":{…},"text":人话}]。
# 寻址一律用**球的 id**（上下文里的 #id）：覆盖"已编排 + 未编排"每一颗球，
# 且不会像"第 N 段"那样在落地过程中因增删重排而漂移。
_INT_ARGS = {"ball", "a", "b", "material", "position"}
_BOOL_ARGS = {"locked"}

# (工具名, 说明, {参数: 说明}, [必填参数])
_POOL_TOOLS = [
    ("rename_ball", "改一颗球要回答的问题（它还是这颗球，只是换了问法）",
     {"ball": "整数，球的 id（见上下文里的 #id）", "question": "新问题，2~80 字"},
     ["ball", "question"]),
    ("set_note", "给一颗球写/改一句短评",
     {"ball": "整数，球的 id", "text": "短评，2~500 字"}, ["ball", "text"]),
    ("set_deep", "存一颗球的详细论证（展开后看到的内容）",
     {"ball": "整数，球的 id", "text": "论证正文，2~500 字"}, ["ball", "text"]),
    ("set_script", "写/改一颗球的口播稿",
     {"ball": "整数，球的 id", "text": "口播稿，6~400 字"}, ["ball", "text"]),
    ("set_shot", "写/改一颗球要什么画面",
     {"ball": "整数，球的 id", "text": "画面描述，4~200 字"}, ["ball", "text"]),
    ("set_lock", "确认 / 取消确认一颗球（够了 / 还没够）",
     {"ball": "整数，球的 id", "locked": "true=确认够了，false=取消确认"}, ["ball", "locked"]),
    ("add_ball", "投一颗新球；给了 position 就排进那一位，否则先放池里漂着",
     {"question": "新球要回答的问题，2~80 字", "position": "可选，排到第几位（1 起）"},
     ["question"]),
    ("drop_ball", "撤下一颗球（它的素材退回未指派，不丢）",
     {"ball": "整数，球的 id"}, ["ball"]),
    ("move_ball", "把一颗球挪到第 position 位",
     {"ball": "整数，球的 id", "position": "第几位（1 起）"}, ["ball", "position"]),
    ("place_ball", "把未编排的球排进第 position 位（球上随即出现编号）",
     {"ball": "整数，球的 id", "position": "第几位（1 起）"}, ["ball", "position"]),
    ("unplace_ball", "把一颗球移出编排（编号没了，退成池里的球，素材不丢）",
     {"ball": "整数，球的 id"}, ["ball"]),
    ("merge_balls", "合并两颗球（素材都并到保留的那颗）",
     {"a": "保留的那颗球 id", "b": "被并掉的那颗球 id"}, ["a", "b"]),
    ("set_payoff", "改这一期「最想让人记住的一下」",
     {"text": "一句话断言，6~60 字"}, ["text"]),
    ("assign_material", "把一条**未指派**的素材指派给某颗球",
     {"material": "整数，素材 id（见上下文里的 #mid）", "ball": "整数，球的 id"},
     ["material", "ball"]),
]


def _build_tools():
    """把 _POOL_TOOLS 拼成 OpenAI tools 的 function schema。"""
    out = []
    for name, desc, params, required in _POOL_TOOLS:
        props = {}
        for k, v in params.items():
            t = ("integer" if k in _INT_ARGS
                 else ("boolean" if k in _BOOL_ARGS else "string"))
            props[k] = {"type": t, "description": v}
        out.append({"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required}}})
    return out


TOOLS = _build_tools()


def _pool_ctx(beats_all, mbb, unassigned=()):
    """给模型看的**整口池子**：每一颗球都带上它的 #id，含未编排的。

    只给统计不给原文，模型就会编素材——所以每颗球附最多 2 条素材原文（沿用 _ctx 的做法）。
    """
    placed = [b for b in beats_all if b.get("placed", 1)]
    unplaced = [b for b in beats_all if not b.get("placed", 1)]
    rows = []
    for i, b in enumerate(placed):
        ms = mbb.get(b["id"]) or []
        rows.append("#%d [第 %d 段 · %s] %s（%d 条素材）"
                    % (b["id"], i + 1, status_of(b, ms), b["question"], len(ms)))
        for m in ms[:2]:
            body = (m.get("body") or "").strip().replace("\n", " ")[:60]
            if body:
                rows.append("      素材：%s" % body)
    for b in unplaced:
        ms = mbb.get(b["id"]) or []
        rows.append("#%d [未编排 · %s] %s（%d 条素材）"
                    % (b["id"], status_of(b, ms), b["question"], len(ms)))
        for m in ms[:2]:
            body = (m.get("body") or "").strip().replace("\n", " ")[:60]
            if body:
                rows.append("      素材：%s" % body)
    head = "\n".join(rows) or "（还没有结构）"
    un = "\n".join("- #%d %s" % (m["id"], (m.get("body") or "").strip()[:80])
                   for m in unassigned if (m.get("body") or "").strip()) or "（无）"
    return head + "\n\n还没指派的素材：\n" + un


def _norm_tool(name, args):
    """把一条工具调用归一化成内部动作；名字不认识或参数越界一律返回 None（宁可不动）。"""
    if not isinstance(args, dict):
        return None

    def _i(k):
        return int(args.get(k))

    def _s(k):
        return str(args.get(k) or "").strip()

    try:
        if name == "rename_ball":
            q = _s("question")
            if 2 <= len(q) <= 80:
                return {"tool": name, "args": {"ball": _i("ball"), "question": q}}
        elif name in ("set_note", "set_deep"):
            t = _s("text")
            if 2 <= len(t) <= 500:
                return {"tool": name, "args": {"ball": _i("ball"), "text": t}}
        elif name == "set_script":
            t = _s("text")
            if 6 <= len(t) <= 400:
                return {"tool": name, "args": {"ball": _i("ball"), "text": t}}
        elif name == "set_shot":
            t = _s("text")
            if 4 <= len(t) <= 200:
                return {"tool": name, "args": {"ball": _i("ball"), "text": t}}
        elif name == "set_lock":
            return {"tool": name,
                    "args": {"ball": _i("ball"), "locked": bool(args.get("locked"))}}
        elif name == "add_ball":
            q = _s("question")
            if not (2 <= len(q) <= 80):
                return None
            d = {"question": q}
            if args.get("position") not in (None, ""):
                d["position"] = max(1, min(999, _i("position")))
            return {"tool": name, "args": d}
        elif name in ("drop_ball", "unplace_ball"):
            return {"tool": name, "args": {"ball": _i("ball")}}
        elif name in ("move_ball", "place_ball"):
            return {"tool": name, "args": {"ball": _i("ball"),
                                           "position": max(1, min(999, _i("position")))}}
        elif name == "merge_balls":
            a, b = _i("a"), _i("b")
            if a != b:
                return {"tool": name, "args": {"a": a, "b": b}}
        elif name == "set_payoff":
            t = _s("text")
            if 6 <= len(t) <= 60:
                return {"tool": name, "args": {"text": t}}
        elif name == "assign_material":
            return {"tool": name, "args": {"material": _i("material"), "ball": _i("ball")}}
    except (TypeError, ValueError):
        return None
    return None


def _actions_from_calls(calls):
    """把原生 tool_calls 归一化成 actions（arguments 可能是 JSON 字符串）。"""
    out = []
    for c in (calls or [])[:8]:
        if not isinstance(c, dict):
            continue
        fn = c.get("function") or {}
        raw = fn.get("arguments")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = None
        a = _norm_tool(str(fn.get("name") or "").strip(), raw)
        if a:
            out.append(a)
    return out


def _actions_from_json(got):
    """结构化 JSON 兜底：{"reply":…, "actions":[{"tool":…, …args}]}。"""
    out = []
    for a in (got.get("actions") or [])[:8]:
        if not isinstance(a, dict):
            continue
        args = {k: v for k, v in a.items() if k != "tool"}
        na = _norm_tool(str(a.get("tool") or "").strip(), args)
        if na:
            out.append(na)
    return out


_CHAT_TAIL = (" 现在你在和创作者**当面聊**这一期怎么讲：顺着他的思路聊，该反对就反对，"
              "别客套、别列套话。你可以改池子里的**任意一颗球**（含还没编排的球）——"
              "改问题、写短评、补论证、写口播稿/画面、确认或撤下、挪位/编排、移出编排、"
              "投新球、合并、把素材指到某颗球、改 payoff。球一律用上下文里的 #id。"
              "改动不要只在嘴上说：能调用工具就直接调用；"
              "如果你的接口不能调用工具，就把改动放进一个 JSON —— "
              '{"reply":"…","actions":[{"tool":"rename_ball","ball":12,"question":"…"}]}。'
              "**回复务必短**：说人话、两三句就够，不分点、不复述资料 —— "
              "联网搜到的东西只挑你要用的那一句写进去（对话越聊越长，P-021）。")


# ---------------- 一期记忆蒸馏（§2.28）：把对话收成"已定口径" ----------------
_MEM_TAIL = (" 现在做一件事：把这一期**已经定下来的口径**蒸馏出来，给后面的对话当长期记忆。"
             "只要结论，不要过程、不要寒暄。")


def llm_distill(topic, history, beats_all, mbb):
    """把这一期的对话 + 结构蒸馏成 3~6 条「已定口径」（供后续对话注入）。

    返回 bullet 列表，或 None（没模型 / 失败 / 没聊过）—— 调用方**保留旧记忆**，不打断主流程。
    刻意**只收结论**：聊天原文由"最近聊过"单独注入，这里不重复搬原文。
    """
    if not llm.available():
        return None
    lines = []
    for h in history[-24:]:
        if h.get("role") not in ("you", "ai"):
            continue
        t = (h.get("text") or "").strip()
        if t:
            lines.append("%s：%s" % ("他" if h.get("role") == "you" else "你", t))
    convo = "\n".join(lines)
    if len(convo) < 20:
        return None
    got = llm.ask_json(
        sys_base() + _MEM_TAIL,
        "期名：%s\n当前结构：\n%s\n\n这一期聊过的：\n%s\n\n"
        "提炼 3~6 条「这一期已经定下来的口径」，每条一句话、不超过 40 字。"
        "只写已经确定的结论 / 取舍 / 决定；还没定的、寒暄的、客套的都不要。\n"
        '输出 {"memory":["口径1","口径2"]}'
        % (topic["name"], _ctx(beats_all, mbb), convo),
        temperature=0.4)
    if not isinstance(got, dict) or not isinstance(got.get("memory"), list):
        return None
    out = []
    for x in got["memory"][:6]:
        s = str(x).strip().lstrip("-·•* \t").strip()
        if 2 <= len(s) <= 60:
            out.append(s)
    return out[:6] or None


def _clip(s, n):
    """把一段文本截到 n 字（超了加省略号）—— 对话越聊越长，喂回模型的上下文要封顶（P-021）。"""
    s = (s or "").strip()
    return s if len(s) <= n else s[:n].rstrip() + "…"


def llm_chat(topic, beats_all, mbb, history, user_text, unassigned=(), mcp=None,
             on_delta=None, should_stop=None):
    """和模型聊这一期，并让它用工具改池子里的球。返回 {"reply": str, "actions": [...]}。

    actions 只是**提议**：界面上要人点一下「照做」才落地（动不动永远由人裁决，P-010/P-013）。
    先试原生 tool_calls；不支持就退回结构化 JSON。球用 id 寻址，落地时再按 id 复核。
    上下文里带上**这一期的记忆**（已定口径）与**最近聊过** —— 一期一份，绝不跨期（§2.28）。

    mcp 非空 = 这次对话额外挂上 tinyfish MCP 的**只读搜索工具**（§2.31）：
    模型调它 → **当场执行**（只读、把结果回填）→ 最多再转 1 轮收口；池子改球工具**照旧只当提议**。
    mcp=None 时行为与从前**完全一致**。

    on_delta 非空 = **流式**（§2.33）：每收到一段增量就回调 on_delta(str)，界面边收边显示；
    on_delta(None) 表示"清空重来"（收口那轮覆盖上一轮的临时内容）。
    should_stop 非空 = 可中断（返回 True 就提前收口，把已拿到的部分交出去）。
    """
    his = "\n".join("%s：%s" % ("他" if h.get("role") == "you" else "你",
                                _clip(h.get("text"), 240))
                    for h in history[-6:] if h.get("role") in ("you", "ai"))
    mem = (topic.get("memory") or "").strip()
    mem_block = ""
    if mem:
        bullets = "\n".join("· " + ln.strip() for ln in mem.splitlines() if ln.strip())
        mem_block = "【这一期已定的口径·记忆】\n%s\n\n" % bullets
    # 工具清单 = 池子那 14 个（提议，人点照做才落地）+（可选）MCP 只读搜索工具（即时执行）
    mcp_tools = []
    if mcp is not None:
        try:
            mcp_tools = mcp.tool_schemas() or []
        except Exception:
            mcp_tools = []
    mcp_names = set(t["function"]["name"] for t in mcp_tools)
    tools_all = TOOLS + mcp_tools

    system = sys_base() + _CHAT_TAIL
    if mcp_tools:
        # 只把工具塞进 tools 还**不够**：提示词得明说"你能联网"，否则模型会如实回"我没搜索工具"。
        system += ("\n\n你现在**能联网**：可以调用 %s 联网搜索、读取网页正文。"
                   "需要查事实 / 资料 / 最新情况时就直接调用（结果会当场返回给你接着用），"
                   "别声称自己没有联网能力。" % "、".join(sorted(mcp_names)))
    user = ("期名：%s\npayoff：%s\n当前池子：\n%s\n\n"
            "%s【最近聊过】\n%s\n\n他刚说：%s\n\n"
            "先回他（说人话，两三句，别分点、别客套）；要改池子就直接调用相应工具。"
            % (topic["name"], topic.get("payoff") or "（未定）",
               _pool_ctx(beats_all, mbb, unassigned), mem_block, his, user_text))

    # 单次请求的超时：挂了搜索工具后请求明显更慢，默认 25s 太紧（P-021 实测踩过）。
    round_timeout = 45

    def _ask(msgs, with_tools=True):
        """一次请求：给了 on_delta 就走**流式**（边收边推给界面），否则一次性拿（老路）。"""
        kw = dict(temperature=0.7, timeout=round_timeout)
        if with_tools:
            kw.update(tools=tools_all, tool_choice="auto")
        if on_delta is None:
            return llm.chat_message(msgs, **kw)
        return llm.chat_stream(msgs, on_delta, should_stop=should_stop, **kw)

    got = None
    if llm.available():
        try:
            msg = _ask([{"role": "system", "content": system},
                        {"role": "user", "content": user}])
        except Exception as e:
            llm.LAST_ERROR = "%s: %s" % (type(e).__name__, e)
            msg = None
        if isinstance(msg, dict):
            calls = msg.get("tool_calls") or []
            reply = str(msg.get("content") or "").strip()
            # 搜索工具**当场执行**；池子改球工具**绝不当场落地**（只攒成提议，等人点「照做」）。
            search_calls = [c for c in calls
                            if str(((c.get("function") or {}).get("name")) or "") in mcp_names]
            pool_calls = [c for c in calls if c not in search_calls]
            actions = _actions_from_calls(pool_calls)
            if search_calls:
                hits = []
                for c in search_calls:
                    fn = c.get("function") or {}
                    nm = str(fn.get("name") or "").strip()
                    raw = fn.get("arguments")
                    if isinstance(raw, str):
                        try:
                            raw = json.loads(raw)
                        except Exception:
                            raw = {}
                    out = mcp.call(nm, raw if isinstance(raw, dict) else {})
                    if out:
                        hits.append(out)
                digest = _clip("\n\n".join(hits), 1500)
                # 收口：**只把搜到的内容丢回去**，池子/历史/工具一概不带 —— 又快又不易超时
                # （早先带全套再请求，实测收口那一轮能跑到 45s+ 直接超时，P-021）。
                if on_delta is not None:
                    on_delta(None)      # 收口这轮覆盖掉上一轮的临时内容（§2.33）
                try:
                    m2 = _ask([{"role": "system", "content": system},
                               {"role": "user", "content":
                                "这是我刚联网搜到的内容：\n%s\n\n"
                                "请用两三句话，只挑跟这一期大纲相关、最有用的那点写成回复；"
                                "别复述、别分点。" % (digest or "（没搜到）")}],
                              with_tools=False)
                    reply = str((m2 or {}).get("content") or "").strip() or reply
                except Exception as e:
                    llm.LAST_ERROR = "%s: %s" % (type(e).__name__, e)
                if not reply and digest:
                    # 收口超时也不空手：直接把搜到的东西挑一段给他
                    reply = "搜到几条，挑你可能要用的：\n" + _clip(digest, 500)
            if not actions and reply:
                # 有些网关接受了 tools 但没实现：正文里可能带 JSON 块
                j = llm._first_json(reply)
                if isinstance(j, dict) and (j.get("actions") or j.get("reply")):
                    actions += _actions_from_json(j)
                    reply = str(j.get("reply") or "").strip() or reply
            if reply or actions:
                got = {"reply": reply, "actions": actions}
        if got is None:
            # tools 这条路整个失败（如 400）→ 纯 JSON 协议再试一次
            j = llm.ask_json(system, user, temperature=0.7, timeout=round_timeout)
            if isinstance(j, dict):
                got = {"reply": str(j.get("reply") or "").strip(),
                       "actions": _actions_from_json(j)}
    if got is None:
        return None
    reply = got.get("reply") or ""
    actions = got.get("actions") or []
    if not reply and not actions:
        return None
    if not reply:
        reply = "我打算这么改。"
    # 回复也要"短"：搜索结果一多，模型容易整段复述 —— 再兜一道长度上限（对话越聊越长，P-021）
    if len(reply) > 400:
        reply = reply[:400].rstrip() + "…"
    return {"reply": reply, "actions": actions}


_SB_TAIL = (" 现在到了收尾：把结构编成一张能给剪辑用的分镜表。"
            "口播稿要口语、能直接念，不是要点；画面要具体到「观众看到什么」。"
            "素材里支持不住的事实一律不要写进稿子。")


def llm_deepen(topic, beat, mats):
    """详细论证这一点：模型替你把这一段的推理链补起来。

    要求它**只用这一段的素材**，撑不住的地方明说"这里还缺东西"——
    宁可写出缺口，也不要编一条看起来很有力的假论据。
    """
    got = llm.ask_json(
        sys_base() + " 现在只处理**一段**：把它的论证补起来。",
        "期名：%s\n要论证的这一点：%s\n这一段手上的素材：\n%s\n\n"
        "把这一点讲扎实：先一句话说清主张，再给 2~4 条支撑（可以引用上面的素材原文），"
        "最后指出**还缺什么**才能让人信。\n"
        "严禁编造素材里没有的事实、数据、案例；素材撑不住就直说缺。\n"
        '输出 {"deep":"…"}'
        % (topic["name"], beat["question"], _bodies(mats)),
        temperature=0.6)
    if isinstance(got, dict):
        d = str(got.get("deep") or "").strip()
        if 20 <= len(d) <= 1200:
            return d
    return None


def llm_spread(topic, beat, mats, beats):
    """就这一点发散：给几条可以延伸的方向（每条都要说清依据）。"""
    known = "；".join(b["question"] for b in beats)
    got = llm.ask_json(
        sys_base() + " 现在只处理**一段**：从它往外发散。",
        "期名：%s\n这一点：%s\n这一段手上的素材：\n%s\n"
        "这一期已有的其他段（别再重复它们）：\n%s\n\n"
        "从这一点能往外发散出什么？给 2~4 个方向，每个方向写成"
        "**一个具体的问题**（能当新的一段用），并附一句依据说清「凭什么从这段长出来」。\n"
        "别给泛泛的话题名，别重复已有的段。\n"
        '输出 {"items":[{"q":"…","why":"…"}]}'
        % (topic["name"], beat["question"], _bodies(mats), known),
        temperature=0.8)
    if not isinstance(got, dict) or not isinstance(got.get("items"), list):
        return None
    out = []
    for it in got["items"][:4]:
        if not isinstance(it, dict):
            continue
        q = str(it.get("q") or "").strip()
        why = str(it.get("why") or "").strip()
        if 4 <= len(q) <= 60:
            out.append(q if not why else "%s ｜%s" % (q, why[:24]))
    return out or None


def llm_storyboard(topic, beats, mbb):
    """一次把每一段的「口播稿 + 要什么画面」写出来。

    返回 {beat_id: {"script":…, "shot":…}}，或 None（调用方降级到本地规则）。
    """
    if not beats:
        return None
    got = llm.ask_json(
        sys_base() + _SB_TAIL,
        "期名：%s\npayoff：%s\n现在的结构：\n%s\n\n"
        "为每一段写两样东西：\n"
        "  script —— 这一段的口播稿，2~4 句，口语，能直接对着镜头念；\n"
        "  shot   —— 这一段要什么画面，一句话说清观众该看到什么"
        "（截图 / 录屏 / 示意图 / B-roll），手上已有图片或视频素材的优先用它们。\n"
        "只写素材支持得住的内容，素材里没有的事实不要编。\n"
        '按段号（1 起）输出 {"items":[{"at":1,"script":"…","shot":"…"}]}'
        % (topic["name"], topic.get("payoff") or "（未定）",
           _ctx(beats, mbb, per_beat=3)))
    if not isinstance(got, dict) or not isinstance(got.get("items"), list):
        return None
    out = {}
    for it in got["items"][:len(beats) + 4]:
        if not isinstance(it, dict):
            continue
        try:
            at = int(it.get("at"))
        except (TypeError, ValueError):
            continue
        i = _idx(at - 1, beats)
        if i is None:
            continue
        sc = str(it.get("script") or "").strip()
        sh = str(it.get("shot") or "").strip()
        if len(sc) < 6 and len(sh) < 4:
            continue
        out[beats[i]["id"]] = {"script": sc[:400], "shot": sh[:200]}

    # 模型经常漏写最后一段 —— 分镜表不能有空行：这一段没稿，
    # 编排包里它的时长估不出来、字幕也没得配。漏的用本地规则补上（不编事实）。
    for b in beats:
        if b.get("id") in out:
            continue
        mats = mbb.get(b["id"]) or []
        sc = script_for(topic, b, mats)
        sh = shot_for(b, mats)
        if (sc or "").strip() or (sh or "").strip():
            out[b["id"]] = {"script": (sc or "")[:400],
                            "shot": (sh or "")[:200], "local": True}
    return out or None


def llm_draft(topic, beats, mbb):
    """已废弃：封顶后的产出改成"逐段分镜表"（llm_storyboard）。

    留着这个空壳是为了让还在调用它的地方立刻报错、而不是悄悄跑一段没人看的结果。
    """
    return None
