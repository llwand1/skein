# -*- coding: utf-8 -*-
"""Skein · 模型接线自检（开发工具，用桩替代真模型）

真模型要 key 才能跑，但"接线对不对"不需要 key 也能验：
这里把 llm.chat 换成桩，走完整条链路（后台线程 → 主线程落地 → 面板状态）。

用法：python llmtest.py
"""
import os
import re
import sys
import json
import tempfile
import time
import traceback

os.environ.setdefault("QT_SCALE_FACTOR", "1")
os.environ["SKEIN_LLM_KEY"] = "stub-key"          # 只为让 llm.available() 为真

import config

_TMP = os.path.join(tempfile.gettempdir(), "skein_llmtest")
os.makedirs(_TMP, exist_ok=True)
config.DB_PATH = os.path.join(_TMP, "t.db")
config.SETTINGS_PATH = os.path.join(_TMP, "settings.json")
config.PROBE_LOG = os.path.join(_TMP, "probe.log")
config.LLM_PATH = os.path.join(_TMP, "no-llm.json")   # 只准用桩，绝不打真模型
config.USER_PATH = os.path.join(_TMP, "user.md")       # user.md 也隔离，别读用户真实画像
for f in (config.DB_PATH, config.SETTINGS_PATH, config.USER_PATH):
    try:
        os.remove(f)
    except OSError:
        pass

from PyQt6.QtWidgets import QApplication

import llm
import brain
import store
import theme
import mcp
import updater
import main as appmod
import panel as panelmod

panelmod.ANIMATE = False
# MCP（§2.31）默认开 —— 自检里必须桩掉：否则每次 say 都会去拉 node/mcp-remote、打真网络。
# 这条链路由下面的 _StubMcp 直接喂 brain.llm_chat 来验。
mcp.get_client = lambda: None
# 自动更新（§2.32）开机静默检查也桩掉，别打真 GitHub
updater.check = lambda: {"has": False, "current": config.VERSION, "error": ""}

FAIL = []


def check(cond, msg):
    print(("   ok   " if cond else "   FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


# ---------------- 桩：按调用内容返回不同 JSON ----------------
REPLY = {"text": ""}
LAST_MSG = {}     # 记下最近一次发给模型的 user 串（验"记忆注入了没有"）


def _ids(text):
    """从上下文里抠出球的 id（#12 …）—— 桩要按真 id 组装工具调用，不能瞎编段号。"""
    return [int(x) for x in re.findall(r"#(\d+)", text or "")]


def fake_chat(messages, **kw):
    user = messages[-1]["content"]
    # 注意：对话的 prompt 里也会提到"最想让人记住"这些词，所以这条分支放最前面。
    # 这条只在**降级路径**（网关不支持 tools）里被走到：返回结构化 JSON 协议。
    if "他刚说" in user:
        ids = _ids(user)
        b1 = ids[0] if ids else 1
        return ('{"reply":"降级这条也能接住你——还是那句话，成本压在谁身上是两件事。",'
                '"actions":[{"tool":"rename_ball","ball":%d,"question":"降级改的第 1 段？"},'
                '{"tool":"add_ball","question":"成本到底压在谁身上？","position":1}]}' % b1)
    if "已经定下来的口径" in user:      # 一期记忆蒸馏（llm_distill 的 JSON 协议）
        return '{"memory":["这一期讲成本压在谁身上","第 1 段先讲反直觉"]}'
    if "该去网上搜什么" in user:        # AI 按期推荐搜法（§2.30）：含重复 + 超长，应被去掉
        return ('{"queries":["MCP 协议 原理","MCP 与 function calling 的区别",'
                '"MCP 协议 原理",'
                '"这是一条特别特别特别特别特别特别特别特别长的查询词它一定会超过四十个字符上限啊啊啊"]}')
    if "初始理解" in user:
        return '```json\n{"beats":["模型版问题一？","模型版问题二？","模型版问题三？"]}\n```'
    if "最想让人记住" in user:
        return '好的，这是结果：\n{"payoff":"模型版：一句话记住这一期"}'
    if "把这一点讲扎实" in user:      # 注意：只匹配 user 里独有的句子（system 里那句看不到了）
        return ('{"deep":"主张：这一段的核心不是功能而是边界。支撑一：素材里那句原文；'
                '支撑二：反例。还缺：一条一手证据。"}')
    if "往外发散" in user:
        # 一条合法 + 一条太短（应当被丢掉）
        return ('{"items":[{"q":"边界到底画在哪？","why":"素材里反复提到边界"},'
                '{"q":"短","why":"太短了应当被丢"}]}')
    if "口播稿" in user and "画面" in user:
        # 成片稿：两条合法 + 一条越界（第 99 段）必须被丢掉
        return ('{"items":[{"at":1,"script":"这一段先说反直觉的那点，别铺垫。",'
                '"shot":"黑底白字打出那句话，2 秒"},'
                '{"at":2,"script":"接着把成本压给谁讲清楚。","shot":"一段录屏"},'
                '{"at":99,"script":"越界的这条应当被丢掉。","shot":"x"}]}')
    if "结构该怎么动" in user:
        return ('{"proposals":[{"kind":"add","subject":"WebGPU","why":"素材里反复提到它"},'
                '{"kind":"lock","beat":0,"why":"这一段已经三条了"},'
                '{"kind":"merge","beat":0,"a":1,"b":99,"why":"越界应当被丢掉"}]}')
    return REPLY["text"]


def fake_message(messages, **kw):
    """桩：模型**原生工具调用**（brain.llm_chat 先走 llm.chat_message）。

    真网关走的是 tool_calls；arguments 是 JSON 字符串。这里三条调用：
    rename_ball(第 1 颗) / add_ball(第 2 位) / drop_ball(球 999999，落地时越界被丢)。
    球 id 从 user 上下文里的 #id 里取真值。
    """
    last = messages[-1]
    # MCP 收口（§2.31）：把搜到的内容丢回来的那一轮 —— 这时给最终回复。
    if "联网搜到" in (last.get("content") or ""):
        return {"content": "我查了下：MCP 由 Anthropic 于 2024 年提出；顺手把第 1 段问题收窄了一点。"}
    user = last["content"]
    LAST_MSG["user"] = user             # 记下这一轮发给模型的上下文（验记忆注入用）
    if messages and isinstance(messages[0], dict) and messages[0].get("role") == "system":
        LAST_MSG["system"] = messages[0].get("content") or ""
    # 挂了 MCP 搜索工具（tools 里出现 tf_search）→ 先调一次搜索，顺带提议改一颗球
    if "他刚说" in user and "tf_search" in str(kw.get("tools") or ""):
        ids = _ids(user)
        b1 = ids[0] if ids else 1
        calls = [
            {"id": "s1", "type": "function",
             "function": {"name": "tf_search",
                          "arguments": json.dumps({"query": "MCP 创新点"})}},
            {"id": "s2", "type": "function",
             "function": {"name": "rename_ball",
                          "arguments": json.dumps({"ball": b1, "question": "MCP 到底新在哪？"})}},
        ]
        return {"content": "", "tool_calls": calls}
    if "他刚说" in user:
        ids = _ids(user)
        b1 = ids[0] if ids else 1
        calls = [
            {"id": "c1", "type": "function",
             "function": {"name": "rename_ball",
                          "arguments": json.dumps({"ball": b1, "question": "模型改的第 1 段？"})}},
            {"id": "c2", "type": "function",
             "function": {"name": "add_ball",
                          "arguments": json.dumps({"question": "成本压力具体压在谁身上？",
                                                   "position": 2})}},
            {"id": "c3", "type": "function",
             "function": {"name": "drop_ball", "arguments": json.dumps({"ball": 999999})}},
        ]
        return {"content": "这么切我同意，但第 2 段得拆开——成本压在谁身上是两件事。",
                "tool_calls": calls}
    return {"content": ""}


class _StubMcp:
    """桩 MCP 客户端（§2.31）：只给一个只读搜索工具 tf_search。

    tool_schemas() 给模型看；call() 记下被调用的参数并回一段"搜索结果"。
    """
    def __init__(self):
        self.calls = []

    def tool_schemas(self):
        return [{"type": "function", "function": {
            "name": "tf_search",
            "description": "联网搜索（只读）",
            "parameters": {"type": "object",
                           "properties": {"query": {"type": "string"}},
                           "required": ["query"]}}}]

    def call(self, tool_name, args):
        self.calls.append((tool_name, dict(args or {})))
        return "搜索结果：MCP（Model Context Protocol）由 Anthropic 于 2024 年 11 月开源。"



def pump(qapp, secs):
    """跑事件循环，把后台线程交回来的活儿排干"""
    end = time.time() + secs
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.02)


def run():
    qapp = QApplication.instance() or QApplication([])
    qapp.setStyleSheet(theme.QSS)
    llm.chat = fake_chat              # 换桩（JSON 协议 / 其余各步）
    llm.chat_message = fake_message   # 原生 tool_calls 那条路
    assert llm.available(), "桩 key 没生效"

    # ---------- 1 JSON 抠取 ----------
    print("\n[STEP] json 抠取（模型很爱拿 ``` 包 JSON）")
    check(llm._first_json('```json\n{"a":1}\n```') == {"a": 1}, "带围栏能解析")
    check(llm._first_json('废话在前 [1,2] 废话在后') == [1, 2], "前后有废话也能解析")
    check(llm._first_json('不是 json') is None, "不是 json 返回 None")

    # ---------- 2 提议映射与校验 ----------
    print("\n[STEP] 提议映射（越界/非法一律丢掉，不能拿去改结构）")
    beats = [{"id": 11, "seq": 0, "question": "q0", "locked": 0},
             {"id": 12, "seq": 1, "question": "q1", "locked": 0}]
    # 第 0 段给两条素材：模型说"够了"才站得住（只有 0~1 条时我们的护栏会丢掉它）
    mbb = {11: [{"id": 1, "body": "a"}, {"id": 2, "body": "b"}]}
    props = brain.llm_proposals(beats, mbb, [], False)
    kinds = [p["kind"] for p in props]
    check(len(props) == 2, "三条里合法的两条留下（实际 %s）" % kinds)
    check("merge" not in kinds, "越界段号（b=99）的合并被丢掉")
    check(all(p.get("key") for p in props), "每条都带 dedup key")
    check(brain.llm_proposals(beats, {}, [], False) is not None
          and len([p for p in brain.llm_proposals(beats, {}, [], False)
                   if p["kind"] == "lock"]) == 0,
          "没素材的段说「够了」会被护栏丢掉")
    check(brain.llm_proposals(beats, mbb, [], True) is None, "已封顶就不提结构改动")

    # ---------- 3 端到端 ----------
    print("\n[STEP] 端到端：立项 → 后台模型 → 主线程落地")
    app = appmod.App()
    app.notice = ""
    app.add_inbox("MCP 到底新在哪")
    app.add_inbox("RAG 的召回率怎么评估")
    app.start_topic("E005 · MCP 到底创新在哪")

    local_seed = [b["question"] for b in store.list_beats(app.current_id)]
    pump(qapp, 1.2)

    seed = [b["question"] for b in store.list_beats(app.current_id)]
    check(seed and seed[0] == "模型版问题一？",
          "模型版初始理解替换上来了（原：%s）" % local_seed[0])
    check(store.get_topic(app.current_id)["payoff"] == "模型版：一句话记住这一期",
          "payoff 也被模型改写了")

    app.panel.show_panel()
    app.panel.refresh()
    app.feed(store.list_beats(app.current_id)[0]["id"], "一条素材")
    pump(qapp, 1.2)
    props = app.state().get("proposals") or []
    check(any(p["kind"] == "add" and "WebGPU" in p["action"] for p in props),
          "面板上真的出现了模型的提议：%s" % [p["kind"] for p in props])

    # 采纳它 → 结构应该多一段
    n0 = len(store.list_beats(app.current_id))
    app.apply_proposal([p for p in props if p["kind"] == "add"][0])
    n1 = len(store.list_beats(app.current_id))
    check(n1 == n0 + 1, "采纳后结构 +1 段（%d→%d）" % (n0, n1))

    # ---------- 4 对话：聊思路，并用对话改结构 ----------
    print("\n[STEP] 对话 · 模型接话 + 用对话改结构（人点「照做」才动）")
    app.panel.show_panel()
    app.panel.refresh()
    n_before = len(store.list_beats(app.current_id))
    q0_before = store.list_beats(app.current_id)[0]["question"]
    app.say("我想从「成本」这个角度切")
    pump(qapp, 1.2)

    log = app.chat_log(app.current_id)
    check([m["role"] for m in log] == ["you", "ai"],
          "线程是 我一句 + 它一句（实际 %s）" % [m["role"] for m in log])
    ai = log[-1]
    check("成本" in ai["text"], "回复真的接住了我的话：%s" % ai["text"][:24])
    tools = [a.get("tool") for a in (ai.get("actions") or [])]
    check(len(tools) == 3, "三条原生工具调用都带过来了（实际 %s；模型层报错：%s）"
          % (tools, llm.LAST_ERROR))
    check(tools == ["rename_ball", "add_ball", "drop_ball"],
          "工具名按调用顺序保留：%s" % tools)
    check(len(store.list_beats(app.current_id)) == n_before,
          "**没点照做之前，结构一个像素都没动**")
    try:                                    # 留一张图，便于肉眼核对"照做"那块长什么样
        png = os.path.join(_TMP, "chat_with_actions.png")
        app.panel.grab().save(png)
        print("   shot " + png)
    except Exception as e:
        print("   shot-fail " + str(e))

    app.apply_actions(ai)
    beats = store.list_beats(app.current_id)
    check(len(beats) == n_before + 1, "照做后按第 2 位插进来一段（%d → %d）"
          % (n_before, len(beats)))
    check(beats[0]["question"] == "模型改的第 1 段？", "第 1 段被改名了")
    check(beats[1]["question"] == "成本压力具体压在谁身上？", "新段就在第 2 位")
    check(beats[0]["question"] != q0_before, "改名确实生效")
    check("撤下" not in (ai.get("summary") or ""), "越界那条（球 999999）被丢掉，没进小结")
    check(ai.get("done") is True and ai.get("summary"), "这条标记成已照做：%s" % ai.get("summary"))

    # ---------- 4b 降级：网关不支持 tools → 退回结构化 JSON ----------
    print("\n[STEP] 对话降级 · 网关不支持 tool_calls 时退回 JSON 协议")
    prev = app.current_id

    def boom_tools(*a, **k):
        raise RuntimeError("网关不支持 tools")
    real_msg = llm.chat_message
    llm.chat_message = boom_tools            # 逼它走 JSON 兜底（fake_chat 的「他刚说」分支）
    app.start_topic("E006 · 降级路径")
    pump(qapp, 1.0)
    app.panel.show_panel()
    app.panel.refresh()
    n_before2 = len(store.list_beats(app.current_id))
    app.say("还是从成本切")
    pump(qapp, 1.0)
    log2 = app.chat_log(app.current_id)
    ai2 = log2[-1] if log2 else {}
    tools2 = [a.get("tool") for a in (ai2.get("actions") or [])]
    check(ai2.get("role") == "ai" and tools2 == ["rename_ball", "add_ball"],
          "JSON 兜底也接住了（角色 %s / 动作 %s）" % (ai2.get("role"), tools2))
    check(len(store.list_beats(app.current_id)) == n_before2,
          "降级路径同样：没点照做之前结构没动")
    app.apply_actions(ai2)
    check(len(store.list_beats(app.current_id)) == n_before2 + 1,
          "降级那条照做后也能落地")
    llm.chat_message = real_msg
    app.set_current(prev)
    app.panel.show_panel()
    app.panel.refresh()

    # ---------- 4c 一期记忆：蒸馏「已定口径」+ 注入下一轮对话 ----------
    print("\n[STEP] 一期记忆 · 点照做/聊满后蒸馏，下一轮对话带上它（一期一份，绝不跨期）")
    store.set_topic_memory(app.current_id, "旧口径：这条应当被刷新掉")
    app._mem_busy = False           # 前面的蒸馏若还在跑，重置一下，好让本次真的触发
    app._kick_distill()
    pump(qapp, 1.2)
    mem = store.get_topic_memory(app.current_id)
    check("这一期讲成本压在谁身上" in mem,
          "蒸馏刷新了这一期的记忆：%s" % mem.replace("\n", " / "))
    check("旧口径" not in mem, "旧的记忆被新蒸馏整体覆盖，不是往上堆")

    # 下一轮对话要带上这段记忆（跨轮「记性」）
    app.say("那第 1 段我按反直觉开头")
    pump(qapp, 1.2)
    sent = LAST_MSG.get("user") or ""
    check("【这一期已定的口径·记忆】" in sent, "下一轮对话真的带上了这一期的记忆")
    check("讲成本压在谁身上" in sent, "记忆的内容进了上下文")

    # 一期一份：换一期后读不到这一期的记忆
    _t2 = store.create_topic("记忆隔离测试")
    check(store.get_topic_memory(_t2) == "", "另一期没有这一期的记忆（严格隔离）")

    # ---------- 4d AI 按期推荐搜法（§2.30）：模型给几条能直接点的查询词 ----------
    print("\n[STEP] AI 按期推荐搜法 · 按这一期给查询词（去重 / 超长丢掉）")
    app.suggest_queries()
    pump(qapp, 1.2)
    qs = list(app._sq or [])
    check(qs == ["MCP 协议 原理", "MCP 与 function calling 的区别"],
          "推荐词按序保留、去重、丢掉超长那条：%s" % qs)
    check(app._sq_busy is False, "拿到推荐词后不再是「正在想」")
    # 带焦点（选中某颗球）也应能拿到推荐，不炸
    got = brain.llm_search_queries(
        {"name": "E005"}, [{"id": 11, "seq": 0, "question": "q0", "locked": 0}],
        {11: [{"id": 1, "body": "素材A"}]}, focus={"id": 11, "question": "q0"})
    check(got and len(got) >= 2, "带焦点也能拿到推荐：%s" % got)

    # ---------- 4e 原生 AI 接 tinyfish MCP（§2.31）：搜索即时执行，改球仍是提议 ----------
    print("\n[STEP] 原生 AI × MCP · 搜索工具当场执行（只读），改球仍走照做闸门")
    t = store.get_topic(app.current_id)
    beats_all = store.list_beats(app.current_id)
    mbb2 = store.materials_by_beat(app.current_id)
    stub = _StubMcp()
    got = brain.llm_chat(t, beats_all, mbb2, [], "帮我查一下 MCP 的创新点", (), mcp=stub)
    check(stub.calls == [("tf_search", {"query": "MCP 创新点"})],
          "搜索工具被**当场执行**、参数原样传下去：%s" % stub.calls)
    check(got is not None and "MCP" in (got.get("reply") or ""),
          "回填搜索结果后模型给出最终回复：%s" % (got or {}).get("reply", "")[:24])
    acts = [a.get("tool") for a in (got or {}).get("actions") or []]
    check(acts == ["rename_ball"],
          "同轮的改球只当**提议**（没经 MCP 执行，等人照做）：%s" % acts)
    check("能联网" in (LAST_MSG.get("system") or "")
          and "tf_search" in (LAST_MSG.get("system") or ""),
          "挂了 MCP → 系统提示词明说它能联网（治『我没搜索工具』这个假回话）")
    # mcp=None：不挂搜索工具，行为与从前完全一致（还是那三条池子工具）
    got2 = brain.llm_chat(t, beats_all, mbb2, [], "从成本切", (), mcp=None)
    check(got2 and [a.get("tool") for a in got2["actions"]] == ["rename_ball", "add_ball", "drop_ball"],
          "mcp=None 走老路（池子工具照旧）：%s"
          % [a.get("tool") for a in (got2 or {}).get("actions") or []])
    # 收口那轮超时也不丢整条（P-021 实测的坑）：搜完做收口请求时抛错 → 用模型上一句兜底
    def _search_then_boom(messages, **kw):
        if "联网搜到" in (messages[-1].get("content") or ""):
            raise TimeoutError("收口超时")
        return {"content": "我先去搜一眼。", "tool_calls": [
            {"id": "s9", "type": "function",
             "function": {"name": "tf_search", "arguments": json.dumps({"query": "AI"})}}]}

    stub2 = _StubMcp()
    _saved = llm.chat_message
    llm.chat_message = _search_then_boom
    g3 = brain.llm_chat(t, beats_all, mbb2, [], "搜一下", (), mcp=stub2)
    llm.chat_message = _saved
    check(g3 is not None and (g3.get("reply") or "").strip(),
          "收口轮超时也不丢整条（有兜底回复）：%s" % ((g3 or {}).get("reply"),))

    # ---------- 5 节点深挖：详细论证 / 发散 -------------
    print("\n[STEP] 节点深挖 · 详细论证这一点 + 就这一点发散")
    bid0 = store.list_beats(app.current_id)[0]["id"]
    app.deepen(bid0)
    pump(qapp, 1.2)
    b0 = [b for b in store.list_beats(app.current_id) if b["id"] == bid0][0]
    check("主张：" in (b0["deep"] or ""), "论证存进这一段了：%s" % (b0["deep"] or "")[:20])

    app.spread(bid0)
    pump(qapp, 1.2)
    cand = (app._spread or {}).get(bid0) or []
    check(len(cand) == 1, "发散只留下合法那条（实际 %d 条）" % len(cand))
    check("边界到底画在哪" in cand[0], "方向带依据：%s" % cand[0][:26])
    n0 = len(store.list_beats(app.current_id))
    app.promote_spread(bid0, cand[0])
    nb = store.list_beats(app.current_id)
    check(len(nb) == n0 + 1, "升成一段（%d → %d）" % (n0, len(nb)))
    check(nb[1]["question"] == "边界到底画在哪？",
          "新段问句是那条方向、去掉了依据：%s" % nb[1]["question"])

    # ---------- 6 成片稿：封顶后逐段口播稿 + 分镜 ----------
    print("\n[STEP] 成片稿 · 封顶后模型写逐段口播稿与画面")
    app.cap()
    pump(qapp, 1.5)
    beats_v = store.list_beats(app.current_id)
    check(any(b["script"] for b in beats_v), "每一段都有口播稿了")
    check(any(b["shot"] for b in beats_v), "每一段都有画面了")
    check(beats_v[0]["script"] == "这一段先说反直觉的那点，别铺垫。",
          "第 1 段口播稿来自模型：%s" % beats_v[0]["script"][:20])
    check(all("越界" not in (b["script"] or "") for b in beats_v), "越界那条被丢掉")
    sb = app.storyboard_text()
    check("口播稿" in sb and "要什么画面" in sb, "分镜表 Markdown 有该有的列")
    check("| 1 |" in sb, "分镜表从第 1 段开始排")
    out = os.path.join(_TMP, "storyboard.md")
    check(app.export_storyboard(out) and os.path.getsize(out) > 100,
          "分镜表能导出成 Markdown（%d 字节）" % (os.path.getsize(out) if os.path.exists(out) else 0))

    # ---------- 5b 池子：每个球都有一句短评 ----------
    print("\n[STEP] 池子 · 每颗球都有短评（本版是本地规则，模型版下一版接）")
    balls = app.state()["balls"]
    check(bool(balls), "池子里有球（%d 颗）" % len(balls))
    check(all((b.get("note") or "").strip() for b in balls), "每颗球都有短评，没有空着的")
    check(all(isinstance(b.get("mats"), list) for b in balls), "每颗球都带着它的素材")

    # ---------- 6 失败必须静默降级 ----------
    print("\n[STEP] 模型挂了不能影响闭环")
    def boom(*a, **k):
        raise RuntimeError("网络断了")
    llm.chat = boom
    llm.chat_message = boom          # tools 那条路也得一起断
    ok = True
    try:
        app.feed(store.list_beats(app.current_id)[0]["id"], "再喂一条")
        pump(qapp, 0.8)
        app.say("这句也没人接吗")
        pump(qapp, 0.8)
        _ = app.state()
    except Exception:
        ok = False
        traceback.print_exc()
    check(ok, "模型抛异常时界面照常工作")
    check(any(m["role"] == "sys" for m in app.chat_log(app.current_id)),
          "聊崩了会给一句诚实的说明，不是静默吞掉")
    check(brain.llm_proposals is not None, "本地兜底实现仍在")
    app.suggest_queries()
    pump(qapp, 0.6)
    check(app._sq == [] and app._sq_busy is False,
          "推荐搜法失败 → 静默清空、不阻断（实际 %s）" % app._sq)

    print("\n================ 结果 ================")
    if FAIL:
        print("失败 %d 项：" % len(FAIL))
        for f in FAIL:
            print("  - " + f)
    else:
        print("全部通过")
    return len(FAIL)


if __name__ == "__main__":
    try:
        n = run()
    except Exception:
        traceback.print_exc()
        n = 99
    sys.exit(1 if n else 0)
