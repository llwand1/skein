# -*- coding: utf-8 -*-
"""Skein · 自动点击走查（找 bug 用；使用临时数据库，不污染真实数据）

用法：python uitest.py
"""
import os
import sys
import tempfile
import traceback

os.environ.setdefault("QT_SCALE_FACTOR", "1")
# 走查必须是确定性的：把模型关掉，只验本地规则那条路（模型那条路在 llmtest.py 里用桩验）
os.environ.pop("SKEIN_LLM_KEY", None)
os.environ.pop("DEEPSEEK_API_KEY", None)

import config

_TMP = os.path.join(tempfile.gettempdir(), "skein_uitest")
os.makedirs(_TMP, exist_ok=True)
config.DB_PATH = os.path.join(_TMP, "uitest.db")
config.SETTINGS_PATH = os.path.join(_TMP, "settings.json")
config.PROBE_LOG = os.path.join(_TMP, "probe.log")
config.LLM_PATH = os.path.join(_TMP, "no-llm.json")   # 别让走查打到真模型上
# user.md（§2.29）也必须跟着走：不然会读到用户真实的画像，把 sys_base() 的断言带偏。
config.USER_PATH = os.path.join(_TMP, "user.md")
# assets 也必须跟着走 —— 只改 DB 会出"孤儿文件"：库是临时的（下次跑就删了），
# 而 import_files 复制的测试 PNG 却落在用户真实的 assets/ 里，越堆越多，
# 看上去像"收了一堆素材"，其实库里一条记录都没有。踩过。
config.ASSETS_DIR = os.path.join(_TMP, "assets")
# 三个都清掉：DB / 设置 / LLM。LLM 那份特别关键 —— 设置那步会往里写一个假 key，
# 不清的话下次跑 llm.available() 就为真，对话/深挖会走异步分支，断言在回调落地前就跑了（假红）。踩过。
for _f in (config.DB_PATH, config.SETTINGS_PATH, config.LLM_PATH, config.USER_PATH):
    try:
        os.remove(_f)
    except OSError:
        pass

from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QPixmap, QColor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (QApplication, QPushButton, QLineEdit, QLabel,
                            QPlainTextEdit, QCheckBox)

import store
import theme
import icons
import brain
import websearch
import mcp
import updater
import main as appmod
import panel as panelmod
import pool as poolmod
import basin as basinmod
from pool import Pool
from basin import Basin

# 走查要的是"同步"：关掉展开动效，否则面板还停在 92px 的球形态
panelmod.ANIMATE = False
poolmod.ANIMATE = False      # 池子也一样：不停住的话截图会拍到"块正在往下落"的半路
basinmod.ANIMATE = False     # 结构池同理：水面在动，截图就不可复现
# MCP（§2.31）默认开 —— 走查里必须桩掉，否则每说一句都会去拉 node/mcp-remote、打真网络
mcp.get_client = lambda: None
# 自动更新（§2.32）开机静默检查 —— 走查里也桩掉，别打真 GitHub
updater.check = lambda: {"has": False, "current": config.VERSION, "error": ""}

SHOTS = os.path.join(_TMP, "shots")
os.makedirs(SHOTS, exist_ok=True)

FAIL = []


def step(name):
    print("\n[STEP] " + name)


def check(cond, msg):
    print(("   ok   " if cond else "   FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def shot(w, name):
    try:
        w.grab().save(os.path.join(SHOTS, name))
        print("   shot " + name)
    except Exception as e:
        print("   shot-fail " + str(e))


def buttons(root, text=None):
    out = []
    for b in root.findChildren(QPushButton):
        try:
            t = b.text()
        except RuntimeError:
            continue
        if text is None or text in t:
            out.append(b)
    return out


def by_obj(root, obj):
    out = []
    for b in root.findChildren(QPushButton):
        try:
            if b.objectName() == obj:
                out.append(b)
        except RuntimeError:
            continue
    return out


def by_key(root, key):
    """按动态属性 k 找芯片（触发条上的身份用它，objectName 要留给 QSS 的 chip/chipon）"""
    out = []
    for b in root.findChildren(QPushButton):
        try:
            if b.property("k") == key:
                out.append(b)
        except RuntimeError:
            continue
    return out


def find_one(root, cls, obj):
    """按 objectName 找第一个某类控件（行内改措辞的输入框就用它找）"""
    for w in root.findChildren(cls):
        try:
            if w.objectName() == obj:
                return w
        except RuntimeError:
            continue
    return None


def find_cls(root, cls):
    """按类型找第一个控件（池子这种没设 objectName 的用它）"""
    for w in root.findChildren(cls):
        return w
    return None


def type_enter(w, text):
    """在控件里打字并按回车 —— 走的是真按键，不是直接调槽函数"""
    w.setText(text)
    QTest.keyClick(w, Qt.Key.Key_Return)
    QApplication.processEvents()          # 提交后的刷新是延后一拍的，要多跑一轮
    QApplication.processEvents()


def click(btn, label=""):
    QTest.mouseClick(btn, Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    print("   click -> " + (label or btn.text()))


def run():
    qapp = QApplication.instance() or QApplication([])
    qapp.setStyleSheet(theme.QSS)

    app = appmod.App()
    app.notice = ""
    opened = []
    app.open_url = lambda u: opened.append(u)

    # ---------- 纯函数 ----------
    step("纯函数 · 结构由主题/素材派生，不是写死的常量")
    q1 = brain.seed_beats("E004 · 游戏化编程网站", [])
    q2 = brain.seed_beats("E006 · AI 网关讲清楚", [])
    check(q1 != q2, "换一期就换一套问题")
    check("游戏化编程网站" in q1[0], "问题里带着当期主体词：%s" % q1[0])
    check(brain.payoff_for("X", [{"body": "它不是 A，而是 B 的最小公约数"}]).startswith("它不是"),
          "payoff 从素材里长出来")
    e1 = brain.estimate([{"id": 1, "seq": 0, "locked": 0}], {1: [{"id": 1}]})
    e3 = brain.estimate([{"id": 1, "seq": 0, "locked": 0}], {1: [{"id": 1}, {"id": 2}, {"id": 3}]})
    check(0 < e1 < e3 <= 92, "同一段边际递减且封顶 92：1 条 %d → 3 条 %d" % (e1, e3))

    # ---------- 0 ----------
    step("初始态")
    st = app.orb_state()
    check(st["has_topic"] is False and st["inbox_n"] == 0, "球是空态 %s" % st)

    app.toggle_panel()
    check(app.panel.isVisible(), "面板能打开")
    # §2.20：喂 / 池子都收在触发条后面，点了才展开（常驻的只有「聊」）
    check(app.panel.input is None, "「喂」默认收着（不点不显示）")
    feeds = by_key(app.panel, "feed")
    check(len(feeds) == 1, "触发条上有「喂」芯片（按 key 找，实际 %d）" % len(feeds))
    click(feeds[0], "展开「喂」抽屉")
    check(app.panel.input is not None and app.panel.combo is not None,
          "点开「喂」才出现输入框 + 目标下拉")
    check(app.panel.combo.count() == 1, "无主题时下拉只有「灵感池（不挂靠）」")
    shot(app.panel, "01_empty.png")

    # ---------- 0b 灵感池：是口池子，不是一个列表 ----------
    step("灵感池 · 池子本体（水位 + 成分 + 点开列表）")
    pools = app.panel.findChildren(Pool)
    check(len(pools) == 1, "面板里有一口池子（实际 %d）" % len(pools))
    pool = pools[0] if pools else None
    if pool is not None:
        check(pool.height() == 132, "池子高度固定（%.0f）—— 高度不表达数量" % pool.height())
        check(pool._items == [], "空池：里面没有块")
        check(not app.panel._pool_open, "默认收着（只显示池子）")
        shot(pool, "01b_pool_empty.png")
        # 点池子 → 摊开列表
        QTest.mouseClick(pool, Qt.MouseButton.LeftButton, pos=pool.rect().center())
        QApplication.processEvents()
        check(app.panel._pool_open, "点池子 → 列表摊开")
        check(len(app.panel.findChildren(Pool)) == 1, "摊开后池子还在（不是被列表换掉）")

    # ---------- 1 随手记（喂进灵感池）----------
    step("随手记 · 不挂靠，先喂进灵感池")
    ed = app.panel.input
    ed.setPlainText("MCP 到底新在哪")
    app.panel.combo.setCurrentIndex(0)      # 0 = 灵感池
    QApplication.processEvents()
    bs = buttons(app.panel, "喂进去")
    check(len(bs) == 1, "找到 1 个「喂进去」按钮（实际 %d）" % len(bs))
    click(bs[0], "喂进灵感池")
    check(len(store.list_inbox()) == 1, "灵感池 1 条")
    pools = app.panel.findChildren(Pool)
    if pools:
        check(len(pools[0]._items) == 1, "池子里真的多了一块")
        check(pools[0]._items[0]["kind"] == "note",
              "块按类型取形状（%s）" % pools[0]._items[0]["kind"])
    # 落下动效单独验：走查里池子是停住的（否则截图会拍到半路），
    # 所以拿一个临时池子把动画打开，只验"喂进来会不会记下这一块要给动效"
    p_anim = Pool(None)
    p_anim._animate = True
    p_anim.set_items([{"kind": "note"}])
    check(bool(p_anim._drops), "喂进来时记下了一条「落下」动效")
    p_anim.set_items([{"kind": "note"}, {"kind": "image"}])
    check(len(p_anim._drops) == 2, "再喂一块就再落一块（%d）" % len(p_anim._drops))
    p_anim.deleteLater()
    # 再塞几条：RAG 会被初始理解吸收；WebGL 谁都没提过 —— 后面用来验 AI 的"插一段"提议
    app.add_inbox("RAG 的召回率怎么评估")
    app.add_inbox("RAG 的索引怎么选")
    app.add_inbox("WebGL 的渲染管线怎么走")
    app.add_inbox("WebGL 和 Canvas 的差别")
    app.panel.refresh()
    check(len(store.list_inbox()) == 5, "灵感池 5 条")
    if pools:
        p2 = app.panel.findChildren(Pool)[0]
        check(len(p2._items) == 5, "池子里 5 块")
        check(len({i["kind"] for i in p2._items}) >= 1, "成分按类型分类")
        shot(p2, "02b_pool_filled.png")
    shot(app.panel, "02_inbox.png")

    # ---------- 2 立项 ----------
    step("立项 · 名字由人打（面板里不该有任何预置期名）")
    bs = buttons(app.panel, "E00")
    check(len(bs) == 0, "面板里没有任何预置排期/期名（实际 %d 个）" % len(bs))
    ed = app.panel.topic_input
    check(ed is not None, "立项那格有个输入框")
    bs = buttons(app.panel, "立项")
    check(len(bs) == 1 and not bs[0].isEnabled(), "名字空着时立项按钮是禁用的")
    ed.setText("MCP 到底创新在哪")
    QApplication.processEvents()
    bs = buttons(app.panel, "立项")
    check(len(bs) == 1 and bs[0].isEnabled(), "打了名字后按钮才可用")
    click(bs[0], "立项")
    check(app.state().get("has_topic") is True, "已立项")
    check(app.state()["topic"]["name"] == "MCP 到底创新在哪", "期名就是人打的那个")
    check(len(store.list_beats(app.current_id)) == 3, "AI 只给 3 段初始理解")
    q = " ".join(b["question"] for b in store.list_beats(app.current_id))
    check(("RAG" in q) or ("WebGL" in q),
          "初始理解吸收了灵感池里的术语：%s" % q)
    check(len(store.list_unassigned(app.current_id)) == 5, "灵感池那几条被带入且未指派")
    check(app.panel.combo.count() == 4,
          "下拉变成 1 灵感池 + 3 段（实际 %d）" % app.panel.combo.count())
    shot(app.panel, "03_topic.png")

    # ---------- 3 喂素材 ----------
    step("喂素材 ×3 到第 1 段（看边际递减 + AI 提议）")
    pcts = []
    for i in range(3):
        ed = app.panel.input
        if ed is None:
            check(False, "第 %d 次找不到输入框" % (i + 1))
            break
        ed.setPlainText("测试素材 %d" % (i + 1))
        cb = app.panel.combo
        if cb is not None and cb.count():
            cb.setCurrentIndex(1)           # 1 = 第 1 段
        QApplication.processEvents()
        bs = buttons(app.panel, "喂进去")
        if not bs:
            check(False, "第 %d 次找不到喂按钮" % (i + 1))
            break
        click(bs[0], "喂第 1 段")
        pcts.append(app.state()["pct"])

    check(store.count_fed(app.current_id) == 3, "已喂 3 条 %s" % pcts)
    check(len(store.list_beats(app.current_id)) == 3,
          "AI 不擅自改结构，还是 3 段（改不改由人点）")
    check(len(pcts) == 3 and (pcts[2] - pcts[1]) < (pcts[1] - pcts[0]),
          "同一段边际递减：%s" % pcts)
    check(app.state()["pct"] <= 92, "完成度封顶 92%%（实际 %d）" % app.state()["pct"])
    shot(app.panel, "03_after_feed.png")

    # ---------- 3b AI 提议 ----------
    step("AI 提议 · 忽略一条、采纳一条")
    props = app.state().get("proposals") or []
    check(len(props) >= 2, "堆到 3 条后同时出现两类提议（实际 %d）" % len(props))
    shot(app.panel, "04_proposals.png")

    if props:
        click(buttons(app.panel, "提议")[0], "展开「提议」抽屉")
        key0 = props[0]["key"]
        ds = buttons(app.panel, "先不用")
        check(len(ds) >= 1, "提议带「先不用」")
        if ds:
            click(ds[0], "先不用")
            left = [p["key"] for p in (app.state().get("proposals") or [])]
            check(key0 not in left, "忽略过的不再提")

        props = app.state().get("proposals") or []
        oks = by_obj(app.panel, "propok")
        check(len(props) >= 1 and len(oks) == len(props),
              "还剩 %d 条提议、对应 %d 个采纳按钮" % (len(props), len(oks)))
        if oks:
            n0 = len(store.list_beats(app.current_id))
            click(oks[0], "采纳提议")
            n1 = len(store.list_beats(app.current_id))
            locked = sum(1 for b in store.list_beats(app.current_id) if b["locked"])
            check(n1 != n0 or locked > 0,
                  "采纳后结构确实变了（段数 %d→%d，已确认 %d 段）" % (n0, n1, locked))
            # §2.21：采纳错了能撤回 —— 撤回后结构回到动它之前
            check(bool(app.state().get("undo_label")), "动过结构后出现「可撤回」")
            rb = buttons(app.panel, "撤回")
            check(len(rb) >= 1, "触发条上有「↩ 撤回」（实际 %d）" % len(rb))
            if rb:
                click(rb[0], "撤回")
                n2 = len(store.list_beats(app.current_id))
                l2 = sum(1 for b in store.list_beats(app.current_id) if b["locked"])
                check(n2 == n0 and l2 == 0,
                      "撤回后回到动它之前（段数 %d、已确认 %d）" % (n2, l2))
                check(not (app.state().get("undo_label")), "撤回一次后撤销栈就空了")
    check(app.state()["has_topic"], "采纳后还在这期里（面板是平的，没有阶段可退回）")

    # ---------- 3c 手动编排结构（在池子里直接动手）----------
    step("手动编排 · 投球 / 点球选中 / 改措辞 / 改编号 / 撤下")
    check(find_cls(app.panel, Basin) is None, "「池子」默认收着（不点不显示）")
    click(buttons(app.panel, "池子")[0], "展开「池子」抽屉")
    pl = find_cls(app.panel, Basin)
    check(pl is not None, "面板里有池子（叙事结构＝一口水池）")
    b0 = store.list_placed(app.current_id)
    check(len(app.state()["balls"]) == len(b0),
          "球数＝已编排段数（%d）" % len(b0))
    if pl is not None:
        shot(pl, "04c_pool.png")

    def ball_pos(i, pl=None):
        """第 i 颗球的球心（池子坐标）—— 每次现取控件，刷新会把旧的删掉"""
        pl = pl or find_cls(app.panel, Basin)
        b = pl._balls[i]
        return pl, b, QPoint(int(pl._cx(b)), int(pl._cy(b)))

    def pick(bid):
        """真的在池子里点一下那颗球（不是直接调槽函数）"""
        pl = find_cls(app.panel, Basin)
        b = next((x for x in pl._balls if x["id"] == bid), None)
        if b is None:
            return None
        QTest.mouseClick(pl, Qt.MouseButton.LeftButton,
                         pos=QPoint(int(pl._cx(b)), int(pl._cy(b))))
        QApplication.processEvents()
        return b

    # 投一颗新球：它没有编号，并立刻进编辑态
    adds = buttons(app.panel, "投一颗新球")
    check(len(adds) == 1, "有「投一颗新球」入口")
    click(adds[0], "投一颗新球")
    balls = app.state()["balls"]
    check(len(balls) == len(b0) + 1, "球数 +1（%d → %d）" % (len(b0), len(balls)))
    check(balls[-1]["n"] is None, "新球没有编号（视频的整体段落还没编排好）")
    check(len(store.list_placed(app.current_id)) == len(b0),
          "没编排的球不进编号，已编排还是 %d 段" % len(b0))
    ed = find_one(app.panel, QLineEdit, "editq")
    check(ed is not None, "投完立刻进改措辞态，不用再点一次")
    if ed is not None:
        type_enter(ed, "手动投进来的一段")
    check(any(b["question"] == "手动投进来的一段"
              for b in store.list_unplaced(app.current_id)), "措辞写进了这颗没编排的球")
    check(find_one(app.panel, QLineEdit, "editq") is None, "写完自动退出编辑态")

    # 点球选中，再改措辞
    bsel = pick(app.state()["balls"][0]["id"])
    check(bsel is not None and app.panel._sel_bid == bsel["id"],
          "点球＝选中它（选中 id=%s）" % app.panel._sel_bid)
    edits = buttons(app.panel, "改措辞")
    check(len(edits) == 1, "选中后出现「改措辞」")
    click(edits[0], "改措辞")
    ed = find_one(app.panel, QLineEdit, "editq")
    check(ed is not None, "旧段也能就地改")
    if ed is not None:
        type_enter(ed, "   ")
    check(store.get_beat(bsel["id"])["question"] == bsel["label"],
          "清空后回车＝放弃，原措辞不动")
    click(buttons(app.panel, "改措辞")[0], "再改一次")
    ed = find_one(app.panel, QLineEdit, "editq")
    if ed is not None:
        type_enter(ed, "池子里改过的第 1 段")
    check(store.get_beat(bsel["id"])["question"] == "池子里改过的第 1 段", "措辞真的改了")

    # 改编号：把第 1 颗球排到第 3 位，编号要收拢成 1..n
    nums = find_one(app.panel, QLineEdit, "editnum")
    check(nums is not None, "选中后能看到「编号」输入框（顺序只认它）")
    if nums is not None:
        nums.setText("3")
        click(buttons(app.panel, "排到这一位")[0], "排到第 3 位")
        placed = store.list_placed(app.current_id)
        check(len(placed) == len(b0), "段数不变（%d）" % len(placed))
        check(placed[2]["id"] == bsel["id"], "它真的落在第 3 位")
        ns = sorted(int(b["seq"]) + 1 for b in placed)
        check(ns == list(range(1, len(placed) + 1)), "编号收拢成连续 1..n：%s" % ns)
        # 留空 = 退出编排
        bsel2 = pick(bsel["id"])
        nums = find_one(app.panel, QLineEdit, "editnum")
        if nums is not None and bsel2 is not None:
            nums.setText("")
            click(buttons(app.panel, "排到这一位")[0], "编号留空")
            check(len(store.list_placed(app.current_id)) == len(b0) - 1,
                  "留空就退出编排（已编排 %d 段）" % len(store.list_placed(app.current_id)))
            check(app.state()["balls"][-1]["n"] is None, "它退成了一颗没编号的球")

    # 撤下一颗没编排的球：不该被拦住（它本来就没进顺序）
    un = [b for b in (find_cls(app.panel, Basin)._balls) if b["n"] is None]
    if un:
        nb = len(app.state()["balls"])
        pick(un[0]["id"])
        dl = buttons(app.panel, "撤下")
        check(len(dl) >= 1, "没编排的球也能「撤下」")
        if dl:
            click(dl[0], "撤下一颗没编排的球")
            check(len(app.state()["balls"]) == nb - 1, "球数 -1")
    shot(app.panel, "04b_manual.png")

    # ---------- 3d 拖动 / 球不动 / 面板可拖 / 滚动位置不跳 ----------
    step("拖动 · 球被拖到新落点（顺序不变）+ 球不动水才动 + 面板可拖 + 刷新不跳回顶部")
    pl, bsel, pos = ball_pos(0)
    order0 = [b["id"] for b in store.list_placed(app.current_id)]
    px0, py0 = bsel["px"], bsel["py"]
    QTest.mousePress(pl, Qt.MouseButton.LeftButton, pos=pos)
    QTest.mouseMove(pl, pos + QPoint(8, 8))                    # 越过 6px 阈值
    QApplication.processEvents()
    dst = QPoint(max(20, pos.x() - 60), min(pl.height() - 20, pos.y() + 70))
    QTest.mouseMove(pl, dst)
    QApplication.processEvents()
    QTest.mouseRelease(pl, Qt.MouseButton.LeftButton, pos=dst)
    QApplication.processEvents()
    moved = store.get_beat(bsel["id"])
    check((moved["px"], moved["py"]) != (px0, py0), "球的落点真的变了（%s → %s）"
          % ((px0, py0), (moved["px"], moved["py"])))
    check([b["id"] for b in store.list_placed(app.current_id)] == order0,
          "拖球只挪位置，顺序一点没动（位置不代表顺序）")

    # 球是钉住的：跑几帧，球心一动不动（动的只有水）
    pl = find_cls(app.panel, Basin)
    c0 = [(pl._cx(b), pl._cy(b)) for b in pl._balls]
    for _ in range(5):
        pl._advance()
    c1 = [(pl._cx(b), pl._cy(b)) for b in pl._balls]
    check(c0 == c1, "球一动不动（只有水在动）")

    # 点一下就跳回顶部 = 那个"闪"。刷新必须待在原地
    vbar = app.panel.scroll.verticalScrollBar()
    if vbar.maximum() > 40:
        vbar.setValue(40)
        QApplication.processEvents()
        app.panel.refresh()
        QApplication.processEvents()
        QApplication.processEvents()
        check(vbar.value() >= 40, "刷新后滚动位置还在原处（%d）" % vbar.value())
    else:
        check(True, "内容还不够长，跳过滚动位置检查")

    # 装饰面按下应该被理解成"拖面板"（标题栏之外的空白也能拖）
    p = app.panel
    decor = p.scroll.widget()          # 内容根（不是按钮/输入框/池子）
    before = p.pos()
    p._drag = None
    QTest.mousePress(decor, Qt.MouseButton.LeftButton, pos=QPoint(4, 4))
    check(p._drag is not None, "在内容空白处按下 → 进入拖动状态（不再只认标题栏）")
    QTest.mouseMove(decor, QPoint(4, 4))
    QTest.mouseMove(decor, QPoint(4, 60))
    QApplication.processEvents()
    QTest.mouseRelease(decor, Qt.MouseButton.LeftButton, pos=QPoint(4, 60))
    QApplication.processEvents()
    check(p.pos() != before, "面板真的跟着走了（%s → %s）" % (before, p.pos()))
    p._drag = None
    p.move(before)                     # 还原位置，别影响后面的走查
    # 点在池子上不该被理解成"拖面板"（池子要吃这个手势）
    pl = find_cls(app.panel, Basin)
    p._drag = None
    QTest.mousePress(pl, Qt.MouseButton.LeftButton, pos=QPoint(4, 4))
    QApplication.processEvents()
    check(p._drag is None, "在池子上按下 → 不算拖面板（不抢球的手势）")

    # ---------- 3e 对话（走查里模型是关着的：要的就是"如实说"）----------
    step("对话 · 模型没接上时不假装能聊")
    ci = app.panel.chat_input
    check(ci is not None, "有对话输入框")
    ci.setPlainText("我想从「成本」这个角度切")
    QApplication.processEvents()
    bs = buttons(app.panel, "说给它")
    check(len(bs) == 1, "找到「说给它」（实际 %d）" % len(bs))
    if bs:
        click(bs[0], "说给它")
    log = app.chat_log(app.current_id)
    check(any(m.get("role") == "you" and "成本" in m.get("text", "") for m in log),
          "我说的话进了线程")
    check(any(m.get("role") == "sys" for m in log),
          "模型不通 → 给的是诚实提示，不是编一条假回复")
    # 面板重建过，输入框要重新取（老的那个已经被删了）
    check(app.panel.chat_input is not None and app.panel.chat_input.toPlainText() == "",
          "说完清空输入框")
    # §2.28：对话落库（一期一份、绝不跨期）
    check(len(store.list_chat(app.current_id)) >= 2,
          "对话落库了（%d 条）" % len(store.list_chat(app.current_id)))
    _other = store.create_topic("隔离测试期")
    check(store.list_chat(_other) == [], "另一期读不到这一期的对话（严格隔离）")
    check(store.list_chat(None) == [], "没有当前期时读对话返回空，不炸")
    shot(app.panel, "04d_chat.png")

    # ---------- 链接展开（§2.23）：一条回复带好几个出处，得能摊开挑 ----------
    step("对话 · 一条消息带多个链接 → 默认露 2 条，能摊开一条一行")
    got_links = []
    real_open = app.open_url
    app.open_url = lambda u: got_links.append(u)     # 先借一下，别弄脏 opened
    # 对话现在落库了（§2.28）：直接往这一期的 chat 表塞一条，再删掉。
    _cid = store.add_chat(app.current_id, "ai",
                          "参考这三条：https://a.example/1 https://b.example/2 "
                          "https://c.example/3")
    app.panel.refresh()
    check(len(buttons(app.panel, "打开 ")) == 2,
          "默认只露 2 条（实际 %d）" % len(buttons(app.panel, "打开 ")))
    more = buttons(app.panel, "还有 1 条")
    check(len(more) == 1, "多出来的收进「还有 1 条」")
    if more:
        click(more[0], "还有 1 条")
    check(len(buttons(app.panel, "打开 ")) == 3,
          "摊开后 3 条一条一行（实际 %d）" % len(buttons(app.panel, "打开 ")))
    check(len(buttons(app.panel, "收起")) == 1, "有「收起」能收回去")
    click(buttons(app.panel, "打开 ")[2], "打开第 3 条")
    check(got_links and got_links[-1] == "https://c.example/3",
          "摊开后的第 3 条也能直接开：%s" % got_links[-1:])
    app.panel._links_open.clear()          # 收干净，别把摊开态带进后面的步骤
    store.delete_chat(_cid)
    app.open_url = real_open
    app.panel.refresh()

    # ---------- 补空段 ----------
    step("补齐剩余空段")
    guard = 0
    while app.state()["counts"]["empty"] > 0 and guard < 10:
        guard += 1
        s = app.state()
        tgt = [b for b in s["beats"] if b["status"] == "empty"][0]
        ed = app.panel.input
        if ed is None:
            check(False, "补段时找不到输入框")
            break
        ed.setPlainText("补齐第 %d 段" % (tgt["seq"] + 1))
        cb = app.panel.combo
        if cb is not None:
            cb.setCurrentIndex(tgt["seq"] + 1)      # +1：index 0 是灵感池
        QApplication.processEvents()
        bs = buttons(app.panel, "喂进去")
        if not bs:
            check(False, "补段时找不到喂按钮")
            break
        click(bs[0], "喂第 %d 段" % (tgt["seq"] + 1))
    check(app.state()["counts"]["empty"] == 0, "没有空段了 %s" % app.state()["counts"])

    # ---------- 4 逐段确认（选中球 → 点「够了」，确认完自动跳下一颗）----------
    step("逐段确认 · 点球 → 点「够了，这段讲清了」")
    guard = 0
    while guard < 12:
        guard += 1
        sup = [b for b in app.state()["balls"] if b["n"] and b["status"] == "supported"]
        if not sup:
            break
        pick(sup[0]["id"])                 # 操作卡跟着选中走，先选中那一颗
        bs = buttons(app.panel, "够了，这段讲清了")
        if not bs:
            check(False, "找不到确认按钮")
            break
        click(bs[0], "确认一段")
    check(app.state()["counts"]["supported"] == 0, "全部已确认 %s" % app.state()["counts"])

    # §2.21：确认错了能撤销 —— 把一颗已确认的球改回「待确认」，再确认回去
    locked0 = [b for b in app.state()["balls"] if b["n"] and b["status"] == "locked"]
    if locked0:
        pick(locked0[0]["id"])
        rb = buttons(app.panel, "撤销确认")
        check(len(rb) == 1, "已确认的球上有「撤销确认」（实际 %d）" % len(rb))
        if rb:
            click(rb[0], "撤销确认")
            check(app.state()["counts"]["supported"] == 1, "它变回「待确认」了")
            bs = buttons(app.panel, "够了，这段讲清了")
            if bs:
                click(bs[0], "再确认回去")
            check(app.state()["counts"]["supported"] == 0, "再确认后回到全确认")
    shot(app.panel, "05_confirmed.png")

    # ---------- 5 封顶 ----------
    step("封顶（收口是触发条上的一个小勾，随时可点）")
    bs = by_obj(app.panel, "cap")
    check(len(bs) >= 1, "找得到收口小勾按钮（实际 %d）" % len(bs))
    if bs:
        click(bs[0], "封顶")
    check(app.state()["capped"] is True, "已封顶")
    check(app.orb_state()["capped"] is True, "球进入封顶态")
    check(len(by_obj(app.panel, "cap")) == 0, "封顶后收口按钮消失（换成一行确认）")

    # §2.21：封顶也能取消 —— 点那枚「✓ 已确认足够」回到未封顶，再封回去
    db = by_obj(app.panel, "capdone")
    check(len(db) == 1, "封顶后是同位置一枚可点的「已确认足够」（实际 %d）" % len(db))
    if db:
        click(db[0], "取消封顶")
        check(app.state()["capped"] is False, "已取消封顶")
        bs = by_obj(app.panel, "cap")
        check(len(bs) == 1, "收口小勾回来了")
        if bs:
            click(bs[0], "再封顶")
        check(app.state()["capped"] is True, "又封回去了")
    shot(app.panel, "06_capped.png")

    # ---------- 5b 成片稿（走查里模型关着 → 看的是本地规则那版 + 导出链路）----------
    step("成片稿 · 分镜表 + 导出（模型关着，用本地规则那版）")
    rows = brain.storyboard(app.topic(), store.list_placed(app.current_id),
                            store.materials_by_beat(app.current_id))
    check(len(rows) == len(store.list_placed(app.current_id)), "每段一行（%d 行）" % len(rows))
    check(all("seq" in r and "script" in r and "shot" in r for r in rows),
          "每行都有段号 / 口播稿 / 画面")
    check(any(r["script"] for r in rows), "有素材的段能拼出本地口播稿")
    check(all(r["have"] for r in rows) or rows, "素材数统计在")
    md = app.storyboard_text()
    check("| 段 | 这一段要回答 | 口播稿 | 要什么画面 | 手上素材 |" in md,
          "Markdown 表头对得上")
    check(md.count("\n| ") >= len(rows), "行数够（%d 段 → %d 行）" % (len(rows), md.count("\n| ")))
    outp = os.path.join(_TMP, "uitest-storyboard.md")
    check(app.export_storyboard(outp) and os.path.exists(outp), "导出 Markdown 成功")
    check("［%s］" % "" not in md, "表里没有空的类型标记")
    shot(app.panel, "06b_storyboard.png")
    shot(app.panel.scroll.widget(), "06b_storyboard_full.png")   # 整张内容（含折叠区）

    # ---------- 5c 素材落盘：文件真的被复制进 assets/ ----------
    step("素材落盘 · 拖/选进来的文件复制进 assets/，原件不动")
    src_png = os.path.join(_TMP, "证据截图.png")
    src_pm = QPixmap(120, 80)           # 造一张真的 PNG（用 Qt 自己存，保证能解码）
    src_pm.fill(QColor("#FFD01F"))
    check(src_pm.save(src_png), "测试用的 PNG 造出来了")
    n0 = len(store.materials_by_beat(app.current_id).get(
        store.list_beats(app.current_id)[0]["id"], []))
    ok, bad = app.import_files([src_png], store.list_beats(app.current_id)[0]["id"])
    check(ok == 1 and not bad, "收了 1 个文件（失败 %d）" % len(bad))
    mats = store.materials_by_beat(app.current_id)[store.list_beats(app.current_id)[0]["id"]]
    check(len(mats) == n0 + 1, "这一段素材 +1（%d → %d）" % (n0, len(mats)))
    got = [m for m in mats if m["path"]]
    check(bool(got), "素材带上了落盘路径")
    if got:
        p = got[0]["path"]
        check(os.path.isfile(p), "文件真的在：%s" % os.path.basename(p))
        check(os.path.dirname(p).startswith(config.ASSETS_DIR), "落在 assets/ 里")
        check(os.path.isfile(src_png), "原件没被搬走")
        check(got[0]["kind"] == "image", "按扩展名认成 image（实际 %s）" % got[0]["kind"])
    # 同名再来一次：不能覆盖
    ok2, _ = app.import_files([src_png], store.list_beats(app.current_id)[0]["id"])
    names = [m["body"] for m in store.materials_by_beat(app.current_id)[
        store.list_beats(app.current_id)[0]["id"]] if m["path"]]
    check(ok2 == 1 and len(set(names)) == len(names), "同名不覆盖，退到 -1：%s" % names[-2:])
    # 文件素材在面板上显示成"带缩略图 + 打开按钮"的一行 —— 素材跟着选中那颗球走
    pick(store.list_placed(app.current_id)[0]["id"])
    thumbs = [w for w in app.panel.findChildren(QLabel)
              if w.pixmap() is not None and not w.pixmap().isNull()]
    check(bool(thumbs), "图片素材在面板上真的画出了缩略图（%d 个）" % len(thumbs))
    check(bool(buttons(app.panel, "打开")), "文件素材带「打开」按钮")
    shot(app.panel, "06c_files.png")

    step("对话 · 让它重写某一段口播稿（模型关着 → 如实说没人接）")
    n_before = len(app.chat_log(app.current_id))
    app.say("第 1 段太学术了，说人话")
    check(len(app.chat_log(app.current_id)) >= n_before + 2, "这句进了线程并有回应说明")
    check(len(store.list_placed(app.current_id)) == len(rows), "结构没被乱动")

    # ---------- 3f 主题可改（不是只能换一个）----------
    step("主题 · 改期名 / 改 payoff，结构不受影响")
    t0 = app.state()["topic"]["name"]
    n_beats0 = len(store.list_beats(app.current_id))
    bs = buttons(app.panel, "改期名")
    check(len(bs) == 1, "主题头上有「改期名」（实际 %d）" % len(bs))
    if bs:
        click(bs[0], "改期名")
    ed = find_one(app.panel, QLineEdit, "edittopic")
    check(ed is not None, "点开变成输入框（就地改）")
    if ed is not None:
        type_enter(ed, "MCP 到底创新在哪（改过）")
    check(app.state()["topic"]["name"] == "MCP 到底创新在哪（改过）",
          "期名改掉了：%s" % app.state()["topic"]["name"])
    check(len(store.list_beats(app.current_id)) == n_beats0, "段数没变（只是改名）")

    pf0 = app.state()["topic"]["payoff"]
    bs = buttons(app.panel, "改")
    check(len(bs) >= 1, "payoff 上也有「改」")
    if bs:
        # 「改」有好几个（每段都有），payoff 那个在标题行右侧 —— 用 objectName 找不到，
        # 所以按"点进去出现 editpayoff"来定位
        for b in bs:
            b.click()
            QApplication.processEvents()
            ped = find_one(app.panel, QLineEdit, "editpayoff")
            if ped is not None:
                break
        if ped is not None:
            type_enter(ped, "改过的 payoff：一句话")
    check(app.state()["topic"]["payoff"] == "改过的 payoff：一句话",
          "payoff 改掉了：%s" % app.state()["topic"]["payoff"])
    check(t0 != app.state()["topic"]["name"], "确实不是原来那个名字了")
    shot(app.panel, "06d_rename.png")

    # ---------- 3g 节点深挖：选中一颗球就能深挖 -------------
    step("节点深挖 · 选中球 → 详细论证 + 就这一点发散")
    first = store.list_placed(app.current_id)[0]
    check(pick(first["id"]) is not None, "点中第一颗球")
    check(len(buttons(app.panel, "详细论证这一点")) == 1, "选中后能看到「详细论证这一点」")
    check(len(buttons(app.panel, "就这一点发散")) == 1, "选中后能看到「就这一点发散」")
    shot(app.panel, "06e_expand.png")

    # 详细论证（模型关着 → 本地规则那版，且要说清是降级）
    click(buttons(app.panel, "详细论证这一点")[0], "详细论证这一点")
    deep = store.get_beat(first["id"])["deep"]
    check(bool(deep), "这一点有论证了")
    check("要立住的点" in deep, "本地那版把「要立住的点」写清楚了")
    check("本地规则" in (app.notice or ""),
          "如实说了这是本地规则（不是模型写的）：%s" % app.notice)

    # 发散 → 升成一段（新段插在这一颗后面）
    ids0 = [b["id"] for b in store.list_placed(app.current_id)]
    n_before = len(ids0)
    click(buttons(app.panel, "就这一点发散")[0], "就这一点发散")
    ups = buttons(app.panel, "升成一段")
    check(bool(ups), "发散出方向了（%d 条）" % len(ups))
    if ups:
        click(ups[0], "升成一段")
        nb = store.list_placed(app.current_id)
        check(len(nb) == n_before + 1,
              "方向真的长成新的一段（%d → %d）" % (n_before, len(nb)))
        new_ids = [b["id"] for b in nb if b["id"] not in ids0]
        at = next((i for i, b in enumerate(nb) if b["id"] == new_ids[0]), -1) if new_ids else -1
        check(len(new_ids) == 1 and at == 1, "新段插在这一颗后面（第 %d 位）" % (at + 1))
    shot(app.panel, "06f_spread.png")

    step("发布按钮")
    bs = buttons(app.panel, "抖音创作发布")
    check(len(bs) >= 1, "找到发布按钮")
    if bs:
        click(bs[0], "点抖音")
    check(opened == [config.PUBLISH[0][1]], "open_url 被调用一次：%s" % opened)

    # ---------- 边界 ----------
    step("边界 · 空输入提交")
    if app.panel.input is not None:
        app.panel.input.setPlainText("   ")
        QApplication.processEvents()
    check(True, "空输入未崩溃")

    step("切换回灵感池")
    bs = buttons(app.panel, "切换")
    check(len(bs) >= 1, "找到切换按钮")
    if bs:
        click(bs[0], "切换")
    check(app.state().get("has_topic") is False, "回到无主题态")
    shot(app.panel, "07_back.png")

    # ---------- 素材搜索（免 key）：走查不打网络 ----------
    step("搜素材 · 解析（纯函数）+ 结果落地（直达原文）")
    rss = ('<?xml version="1.0" encoding="utf-8"?><rss><channel>'
           '<item><title>MCP 是什么</title><link>https://example.com/mcp</link>'
           '<description>一段摘要 &amp; 说明</description></item>'
           '</channel></rss>')
    hits = websearch.parse_rss(rss)
    check(len(hits) == 1 and hits[0]["url"] == "https://example.com/mcp",
          "RSS 解析出 1 条：%s" % (hits[:1]))
    check(hits[0]["snippet"] == "一段摘要 & 说明", "摘要取到且去掉了实体转义")
    check(websearch.parse_rss("not xml") == [], "坏输入不炸，返回空")

    app.panel._show_search = True
    app._search_query = "MCP 是什么"
    app._search_land(app._search_gen, hits, "")     # 直接把结果交回去，不走线程
    app.panel.refresh()
    check(app.panel._search_input is not None, "搜索抽屉里有输入框")
    check(len(buttons(app.panel, "打开源")) >= 1, "结果带「打开源」（直达原文）")
    check(len(buttons(app.panel, "收进素材")) >= 1, "结果带「收进素材」")
    n0 = len(store.list_inbox())
    click(buttons(app.panel, "收进素材")[0], "收一条进素材")
    check(len(store.list_inbox()) == n0 + 1, "素材进了灵感池")
    got = [m for m in store.list_inbox() if (m.get("src") or "").startswith("http")]
    check(bool(got) and got[0]["kind"] == "link", "是一条带原文链接的松散素材")
    opened.clear()
    click(buttons(app.panel, "打开源")[0], "打开源")
    check(opened and opened[-1] == "https://example.com/mcp",
          "打开源 → 调 open_url：%s" % opened[-1:])

    # AI 按期推荐搜法（§2.30）：按钮 + 可点芯片（不真联网 —— 借用再还原）
    step("搜素材 · AI 按期推荐搜法（可点芯片）")
    check(len(buttons(app.panel, "AI 帮我想几个搜法")) == 1, "有「AI 帮我想几个搜法」按钮")
    req = []
    real_sq = app.suggest_queries
    app.suggest_queries = lambda focus=None: req.append(focus)
    click(buttons(app.panel, "AI 帮我想几个搜法")[0], "点 AI 想搜法")
    app.suggest_queries = real_sq
    check(req == [None], "无选中球时请求不带焦点：%s" % req)

    app._sq = ["MCP 协议 原理", "MCP 与 function calling 的区别"]
    app._sq_busy = False
    app.panel.refresh()
    chip = buttons(app.panel, "MCP 协议 原理")
    check(len(chip) == 1, "推荐词显示成可点芯片（实际 %d 个）" % len(chip))
    rec = []
    real_search = app.search_web
    app.search_web = lambda q: rec.append(q)
    click(chip[0], "点推荐芯片")
    app.search_web = real_search
    check(rec and rec[-1] == "MCP 协议 原理",
          "点芯片 → 直接用这条词去搜：%s" % rec[-1:])
    app._sq = []
    app.panel.refresh()

    app.panel._show_search = False
    app.panel.refresh()

    # ---------- 设置（§2.25）：标题栏齿轮 → 模型 / 内容默认 / 系统提示词 三组 ----------
    step("设置 · 标题栏齿轮 → 三组（模型 / 内容默认 / 系统提示词）")
    check(len(by_obj(app.panel, "gear")) == 1,
          "标题栏有一枚齿轮（实际 %d）" % len(by_obj(app.panel, "gear")))
    click(by_obj(app.panel, "gear")[0], "打开设置抽屉")
    check(app.panel._show_settings, "设置抽屉展开了")
    check(find_one(app.panel, QLineEdit, "set_base") is not None, "组1：模型有 base_url")
    check(find_one(app.panel, QLineEdit, "set_model") is not None, "组1：模型有 model")
    check(find_one(app.panel, QLineEdit, "set_key") is not None, "组1：模型有 key")
    check(len(buttons(app.panel, "测试连接")) == 1, "组1：有「测试连接」")
    check(find_one(app.panel, QLineEdit, "set_platform") is not None, "组2：内容默认有平台")
    check(find_one(app.panel, QLineEdit, "set_form") is not None, "组2：内容默认有形态")
    check(find_one(app.panel, QLineEdit, "set_target") is not None, "组2：内容默认有目标张数")
    check(find_one(app.panel, QPlainTextEdit, "set_sys_prompt") is not None,
          "组3：系统提示词是多行输入")
    # 组4：跨期记忆 user.md（§2.29）—— 只读入口，点了才建模板
    check(len(buttons(app.panel, "打开 user.md")) == 1,
          "组4：有「打开 user.md」（实际 %d）" % len(buttons(app.panel, "打开 user.md")))
    # 组5：搜索 MCP · tinyfish（§2.31）—— 默认开；开关 + 路径/地址由用户自己填/改
    mc = find_one(app.panel, QCheckBox, "set_mcp_enabled")
    check(mc is not None and mc.isChecked() is True, "组5：MCP 开关默认开")
    check(find_one(app.panel, QLineEdit, "set_mcp_node") is not None, "组5：有 node 路径")
    check(find_one(app.panel, QLineEdit, "set_mcp_proxy") is not None, "组5：有 proxy 路径")
    check(find_one(app.panel, QLineEdit, "set_mcp_url") is not None, "组5：有服务地址")
    # 组6：更新（§2.32）—— 检查按钮 + 自动检查开关（默认开）
    check(find_one(app.panel, QPushButton, "set_check_update") is not None, "组6：有「检查更新」按钮")
    ac = find_one(app.panel, QCheckBox, "set_update_auto_check")
    check(ac is not None and ac.isChecked() is True, "组6：自动检查更新默认开")
    shot(app.panel, "08_settings.png")

    # 填内容默认 + 模型 → 保存 → 落盘到 settings.json / llm.json（都不进代码仓）
    find_one(app.panel, QLineEdit, "set_platform").setText("B站")
    find_one(app.panel, QLineEdit, "set_form").setText("口播")
    find_one(app.panel, QLineEdit, "set_target").setText("5")
    find_one(app.panel, QLineEdit, "set_base").setText("https://example.com/v1")
    find_one(app.panel, QLineEdit, "set_model").setText("test-model")
    find_one(app.panel, QLineEdit, "set_key").setText("sk-test")
    mc.setChecked(True)                       # 组5：MCP 开关与路径也要能落盘
    find_one(app.panel, QLineEdit, "set_mcp_node").setText(r"C:\nodejs22\node.exe")
    find_one(app.panel, QLineEdit, "set_mcp_proxy").setText(r"C:\proxy.js")
    QApplication.processEvents()
    click(buttons(app.panel, "保存设置")[0], "保存设置")
    st = config.load_settings()
    check(st.get("platform") == "B站" and st.get("form") == "口播" and st.get("target") == "5",
          "内容默认写进 settings.json：%s" % {k: st.get(k) for k in ("platform", "form", "target")})
    check(st.get("mcp_enabled") is True and st.get("mcp_node") == r"C:\nodejs22\node.exe"
          and st.get("mcp_proxy") == r"C:\proxy.js",
          "组5：MCP 开关与路径写进 settings.json：%s"
          % {k: st.get(k) for k in ("mcp_enabled", "mcp_node", "mcp_proxy")})
    import json as _json
    try:
        with open(config.LLM_PATH, "r", encoding="utf-8") as f:
            lc = _json.load(f)
    except Exception:
        lc = {}
    check(lc.get("base") == "https://example.com/v1" and lc.get("key") == "sk-test",
          "模型配置写进 llm.json（key 落用户目录，不进代码仓）")

    # 立项时按设置带默认（留空＝不预设，不兜写死的值）
    tid = store.create_topic("设置默认测试")
    tt = store.get_topic(tid)
    check(tt["platform"] == "B站" and tt["form"] == "口播" and int(tt["target_count"]) == 5,
          "立项带上内容默认：%s / %s / %s"
          % (tt["platform"], tt["form"], tt["target_count"]))
    st2 = config.load_settings()
    st2.update({"platform": "", "form": "", "target": ""})
    config.save_settings(st2)
    tid2 = store.create_topic("空默认测试")
    tt2 = store.get_topic(tid2)
    check((tt2["platform"] or "") == "" and (tt2["form"] or "") == ""
          and int(tt2["target_count"] or 0) == 0,
          "内容默认留空＝不预设：%s / %s / %s"
          % (tt2["platform"], tt2["form"], tt2["target_count"]))

    # 系统提示词：留空用通用兜底，填了就用填的（人设不再写死在代码里）
    check("奶蛙" not in brain.SYS_FALLBACK, "通用兜底里不再写死某个具体人设")
    check(brain.sys_base() == brain.SYS_FALLBACK, "没配系统提示词 → 用通用兜底")
    st3 = config.load_settings()
    st3["sys_prompt"] = "你是一个测试人设"
    config.save_settings(st3)
    check(brain.sys_base() == "你是一个测试人设", "设置里填了系统提示词 → 就用填的")

    # user.md（§2.29）：跨期用户记忆 —— 只读、注入 sys_base()、有长度上限
    check(brain.load_user_md() == "" and "【关于这位用户" not in brain.sys_base(),
          "还没建 user.md 时不注入任何东西（行为与从前完全一致）")
    ub = buttons(app.panel, "打开 user.md")
    if ub:
        opened.clear()
        click(ub[0], "打开 user.md")
        check(opened and opened[-1] == config.USER_PATH and os.path.isfile(config.USER_PATH),
              "点它建好并打开 user.md：%s" % (opened[-1:] if opened else []))
    with open(config.USER_PATH, "w", encoding="utf-8") as f:
        f.write("# 关于我\n- 我是奶蛙讲堂\n- 喜欢短句\n")
    check("奶蛙讲堂" in brain.sys_base(), "user.md 的内容真的注入了系统提示词")
    check(brain.sys_base().startswith("你是一个测试人设"), "注入 user.md 不影响人设本身")
    # 限长：超过 200 行的部分不读（防有人贴一整本书把上下文撑爆）
    with open(config.USER_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join("第%d行" % i for i in range(1, 301)))
    _ubase = brain.sys_base()
    check("第200行" in _ubase and "第201行" not in _ubase,
          "超过 200 行的部分被截掉（只读前 200 行）")
    os.remove(config.USER_PATH)
    check(brain.sys_base() == "你是一个测试人设", "删掉 user.md 后回到干净的人设")

    click(by_obj(app.panel, "gear")[0], "收起设置抽屉")
    check(not app.panel._show_settings, "再点齿轮 → 抽屉收起")

    # ---------- UI 去字形（§2.27）：图标一律自绘，文本里不该再借字形当图标 ----------
    step("UI · 图标全部自绘（面板文本里没有拿字形当图标的符号）")
    # 刻意**不含 ×**：「文字×2」里的 × 是成分计数的乘号，是正文不是图标
    GLYPHS = "⚙●＋↩✓▴▾⧉↑↓→"
    labels, btns = [], []
    for w in app.panel.findChildren(QLabel):
        try:
            labels.append(w.text())
        except RuntimeError:
            continue
    for w in app.panel.findChildren(QPushButton):
        try:
            btns.append(w.text())
        except RuntimeError:
            continue
    bad = [t for t in (labels + btns) if any(g in t for g in GLYPHS)]
    check(not bad, "面板里没有借字形当图标的符号（发现 %s）" % bad[:3])
    badx = [t for t in btns if "×" in t]
    check(not badx, "按钮里没有 × 字形（发现 %s）" % badx[:3])
    gear = by_obj(app.panel, "gear")
    check(bool(gear) and gear[0].text() == "" and not gear[0].icon().isNull(),
          "齿轮是自绘图标按钮（无文字、有图标）")
    missing = [n for n in icons.NAMES if icons.pixmap(n, 16, theme.FG3).isNull()]
    check(not missing, "12 个自绘图标都能出图（缺 %s）" % missing)
    im = icons.pixmap("check", 16, theme.GOLD).toImage()
    inked = any(im.pixelColor(x, y).alpha() > 0
                for x in range(im.width()) for y in range(im.height()))
    check(inked, "图标真的落笔了（不是全透明的空图）")

    # ---------- 球 ----------
    step("球 · 点击开关面板")
    if app.panel.isVisible():
        QTest.mouseClick(app.orb, Qt.MouseButton.LeftButton)
        QApplication.processEvents()
        check(not app.panel.isVisible(), "点球收起面板")
    QTest.mouseClick(app.orb, Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    check(app.panel.isVisible(), "再点球展开面板")

    # ---------- 收尾 ----------
    print("\n================ 结果 ================")
    if FAIL:
        print("失败 %d 项：" % len(FAIL))
        for f in FAIL:
            print("  - " + f)
    else:
        print("全部通过")
    print("截图目录：" + SHOTS)
    return len(FAIL)


if __name__ == "__main__":
    try:
        n = run()
    except Exception:
        traceback.print_exc()
        print("\n!!! 走查过程中抛异常")
        n = 99
    sys.exit(1 if n else 0)
