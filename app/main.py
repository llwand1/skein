# -*- coding: utf-8 -*-
"""Skein · 入口（PyQt6）

启动：python main.py（或 run.bat / run-silent.vbs）
"""
import ctypes
import ctypes.wintypes
import os
import queue
import re
import shutil
import socket
import sys
import threading
import time
import webbrowser

import brain
import config
import llm
import mcp
import store
import updater
import websearch
import winutil
from theme import QSS, GOLD

from PyQt6.QtCore import Qt, QTimer, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QIcon, QPixmap
from PyQt6.QtWidgets import (QApplication, QSystemTrayIcon, QMenu, QMessageBox)

from orb import Orb
from panel import Panel


class App:
    def __init__(self):
        store.init()
        self.qapp = QApplication.instance() or QApplication(sys.argv)
        self.qapp.setQuitOnLastWindowClosed(False)
        self.notice = ""
        self._conn_msg = ""         # 设置里「测试连接」的结果，给面板显示
        # 自动更新（§2.32）：检查/下载的结果与忙态，给设置里那一组显示
        self._upd_msg = ""
        self._upd_busy = False
        self._upd_info = None       # updater.check() 的结果（有新版才有 url）
        self.dismissed = set()      # 被忽略的结构提议（AI 提议不烦人：忽略就不再提）
        # 对话（聊思路 / 用对话改结构）—— **落盘进库**（§2.28：一期一份、绝不跨期），
        # 取写都走 store.list_chat / store.add_chat；重启后「已照做」态也在。
        self._chat_gen = 0
        self._chat_busy = False     # 在等模型回话（界面上要说"正在想"，别让人干等）
        self._live = ""             # 正在流式吐出来的那句（§2.33）：还没落库，界面直接显示
        self._stop = None           # 流式的「停」闸（threading.Event）；新一句会重置
        # 一期记忆（§2.28）：后台蒸馏"已定口径"，失败保留旧记忆、不打断主流程
        self._mem_gen = 0
        self._mem_busy = False
        self._mem_at = 0            # 上次蒸馏时的对话条数（"满 6 条兜底"用它算）
        # —— 撤销（§2.21）：只认当前这一期，动结构前照一张快照 ——
        self._undo = []             # [(tid, snapshot, 标签)]
        # —— 素材搜索（§2.22，免 key）——
        self._search_gen = 0
        self._search_busy = False
        self._search_hits = []
        self._search_err = ""
        self._search_query = ""
        # —— AI 按期推荐搜法（§2.30，P-021）：模型给几条能直接点的搜索词 ——
        self._sq_gen = 0
        self._sq_busy = False
        self._sq = []
        # —— 模型接入（没 key 就一直是本地规则，功能不缺失）——
        self._cq = queue.Queue()    # 后台线程 → 主线程 的回调队列
        self._llm_gen = 0           # 代际计数器：过期结果一律丢弃
        self._llm_props = None      # 模型给的结构提议（None = 还没拿到，用本地的）
        self._sb_gen = 0            # 成片稿请求的代际计数器
        self._sb_busy = False       # 模型正在写逐段口播稿/画面（界面上要说清）
        # —— 编排包（M4）：Skein 只编排，出片交给剪映/即梦/可灵 ——
        self._pk = None
        self._pk_busy = False
        # 节点深挖：正在算的是哪一段（界面显示"算着呢"）、发散出来的候选方向
        self._deep_bid = None
        self._spread_bid = None
        self._spread = {}           # beat_id -> [方向…]（只活这次运行，选中才成段）
        try:
            self.qapp.setStyleSheet(QSS)
        except Exception:
            pass

        st = config.load_settings()
        tid = st.get("active_topic")
        self.current_id = tid if store.get_topic(tid) else None

        self.orb = Orb(self)
        self.panel = Panel(self)
        self.orb.clicked.connect(self.toggle_panel)
        self.orb.menuRequested.connect(self._orb_menu)

        self._mk_tray()

        self._q = queue.Queue()
        self._start_hotkey()
        self._poll = QTimer()
        self._poll.timeout.connect(self._drain)
        self._poll.start(150)

        self._tick_t = QTimer()
        self._tick_t.timeout.connect(self._tick)
        self._tick_t.start(1400)

        self.orb.show()
        self._tick()
        self._kick_llm_ping()
        self._kick_mcp_warm()
        self._kick_update_check()

    def _kick_update_check(self):
        """开机后台静默查一次更新（§2.32）：有新版本就在设置里挂个提示，不打扰。"""
        if not config.setting("update_auto_check", True):
            return
        threading.Thread(target=lambda: self._cq.put(
            lambda: self._after_check(updater.check(), quiet=True)),
            daemon=True).start()

    def _kick_mcp_warm(self):
        """开机后台把 MCP 连接先热起来（冷启动约 5s）—— 省得第一次对话白等（§2.31）。"""
        if not mcp.enabled():
            return

        def work():
            try:
                mcp.get_client()      # 连上就缓存；连不上就算了（下次对话再试）
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _kick_llm_ping(self):
        """开机探一次：key 配了不代表能用，标签要如实（不花 token）。"""
        if not llm.available():
            return

        def work():
            llm.ping()
            self._cq.put(self._after_ping)

        threading.Thread(target=work, daemon=True).start()

    def _after_ping(self):
        if self.panel.isVisible():
            self.panel.refresh()

    # ---------------- 设置（§2.25）：模型 / 内容默认 / 系统提示词 ----------------
    def test_connection(self):
        """拿设置里填的（还没保存的）值测一次连通性。后台跑，别卡界面。"""
        vals = self.panel.settings_values()
        self._conn_msg = "测试中…"
        if self.panel.isVisible():
            self.panel.refresh()

        def work():
            ok, msg = llm.test(vals.get("base"), vals.get("model"), vals.get("key"))
            self._cq.put(lambda: self._after_conn(ok, msg))

        threading.Thread(target=work, daemon=True).start()

    def _after_conn(self, ok, msg):
        self._conn_msg = msg          # llm.test 的文案本身就说得清（"连上了" / "还没填 key" / 错误）
        if self.panel.isVisible():
            self.panel.refresh()

    # ---------------- 自动更新（§2.32 / P-022）----------------
    @property
    def conn_msg(self):
        return self._conn_msg

    @property
    def upd_msg(self):
        return self._upd_msg

    @property
    def upd_busy(self):
        return self._upd_busy

    @property
    def upd_has(self):
        """有可更新的新版本（设置里才显示「下载并更新」那颗按钮）。"""
        return bool(self._upd_info and self._upd_info.get("has"))

    def check_update(self):
        """点「检查更新」：后台查，别卡界面。"""
        if self._upd_busy:
            return
        self._upd_busy = True
        self._upd_msg = "检查中…"
        if self.panel.isVisible():
            self.panel.refresh()

        def work():
            self._cq.put(lambda: self._after_check(updater.check()))

        threading.Thread(target=work, daemon=True).start()

    def _after_check(self, info, quiet=False):
        """检查结果落地。quiet＝开机那次静默检查：没新版就不吭声（不打扰）。"""
        self._upd_busy = False
        self._upd_info = info
        if info.get("error"):
            if not quiet:
                self._upd_msg = "检查失败：%s" % info["error"]
        elif info.get("has"):
            self._upd_msg = "有新版本 %s（当前 %s）" % (info["latest"], info["current"])
        elif not quiet:
            self._upd_msg = "已是最新（%s）" % info.get("current")
        if self.panel.isVisible():
            self.panel.refresh()

    def apply_update(self):
        """点「下载并更新」：后台下 + 覆盖代码，成功就自动重启。"""
        if self._upd_busy or not self.upd_has:
            return
        self._upd_busy = True
        self._upd_msg = "下载更新中…"
        if self.panel.isVisible():
            self.panel.refresh()
        url = (self._upd_info or {}).get("url")

        def work():
            ok, msg = updater.apply(url)
            self._cq.put(lambda: self._after_apply(ok, msg))

        threading.Thread(target=work, daemon=True).start()

    def _after_apply(self, ok, msg):
        self._upd_busy = False
        if ok:
            self._upd_msg = "更新完成：%s，即将重启…" % msg
            if self.panel.isVisible():
                self.panel.refresh()
            QTimer.singleShot(1200, self._restart_after_update)
        else:
            self._upd_msg = "更新失败：%s" % msg
            if self.panel.isVisible():
                self.panel.refresh()

    def _restart_after_update(self):
        updater.restart_now()      # 先安排一个"等本进程退了再拉起"的助手
        self.quit()

    def save_settings(self, vals):
        """把设置写盘：内容默认 / 系统提示词进 settings.json，模型进 llm.json。"""
        st = config.load_settings()
        st["platform"] = (vals.get("platform") or "").strip()
        st["form"] = (vals.get("form") or "").strip()
        st["target"] = (vals.get("target") or "").strip()
        st["sys_prompt"] = (vals.get("sys_prompt") or "").strip()
        # 搜索 MCP（§2.31）：默认开；开关与路径都由用户自己填/改
        st["mcp_enabled"] = bool(vals.get("mcp_enabled"))
        st["mcp_node"] = (vals.get("mcp_node") or "").strip()
        st["mcp_proxy"] = (vals.get("mcp_proxy") or "").strip()
        st["mcp_url"] = (vals.get("mcp_url") or "").strip()
        # 自动更新（§2.32）：默认开；关了就不在开机时静默检查
        st["update_auto_check"] = bool(vals.get("update_auto_check", True))
        config.save_settings(st)
        llm.save_config(vals.get("base"), vals.get("model"), vals.get("key"))
        mcp.reset()          # 设置改了（尤其开关/路径）→ 丢弃缓存的连接，下次重连
        self._conn_msg = ""
        self._notify("设置已保存。内容默认只影响之后新立的项，旧的不动。")

    # ---------------- 状态 ----------------
    def topic(self):
        return store.get_topic(self.current_id)

    def set_current(self, tid):
        self.current_id = tid
        self._mem_at = 0        # 换期：蒸馏计数跟着这一期重来（§2.28）
        self._sq = []           # 换期：AI 推荐搜法跟这一期走，旧的丢掉（§2.30）
        self._sq_busy = False
        st = config.load_settings()
        st["active_topic"] = tid
        config.save_settings(st)

    def state(self):
        inbox = store.list_inbox()
        t = self.topic()
        if not t:
            return {"has_topic": False, "inbox_n": len(inbox), "inbox_items": inbox,
                    **self._ui_extra()}
        # 结构侧只认「已编排」的段：未编排的球没有编号，一旦漏进下游，
        # brain 里 b["seq"]+1 那套段号就错位了（§2.18 语义 3）
        beats = store.list_placed(t["id"])
        mbb = store.materials_by_beat(t["id"])
        beats_v = []
        for b in beats:
            d = dict(b)
            d["mats"] = mbb.get(b["id"], [])
            d["status"] = brain.status_of(b, d["mats"])
            beats_v.append(d)
        c = brain.counts(beats, mbb)
        nx, why = brain.next_step(beats, mbb, bool(t["capped"]))
        un = store.list_unassigned(t["id"])          # 还没指到某一段的素材
        unplaced = store.list_unplaced(t["id"])      # 同一期里还没编排的球（没编号）
        # 池子要的是"这一期所有的球"：已编排的（有编号）+ 还没编排的（没编号）
        balls = [self._ball_payload(b, b.get("mats") or [], True) for b in beats_v]
        for b in unplaced:
            ms = mbb.get(b["id"], [])
            d = dict(b)
            d["mats"] = ms
            d["status"] = brain.status_of(b, ms)
            balls.append(self._ball_payload(d, ms, False))
        # 模型回话之前先用本地规则；回来后按 id 校验一遍，防止指向已被删掉的段
        base = self._llm_props if self._llm_props is not None \
            else brain.proposals(beats, mbb, un, bool(t["capped"]))
        ids = {b["id"] for b in beats}
        props = []
        for p in base:
            if p["key"] in self.dismissed:
                continue
            if p["kind"] in ("lock", "drop") and p.get("beat") not in ids:
                continue
            if p["kind"] == "merge" and not ({p.get("a"), p.get("b")} <= ids):
                continue
            props.append(p)
        return {
            "has_topic": True, "topic": t, "beats": beats_v, "mbb": mbb,
            "counts": c, "pct": brain.estimate(beats, mbb), "next": nx, "why": why,
            "capped": bool(t["capped"]), "inbox_n": len(inbox), "inbox_items": inbox,
            "unassigned": un, "proposals": props,
            "balls": balls, "n_unplaced": len(unplaced),
            "sb_busy": bool(getattr(self, "_sb_busy", False)),
            "deep_bid": self._deep_bid, "spread_bid": self._spread_bid,
            "spread": dict(self._spread or {}),
            **self._ui_extra(),
        }

    def _ui_extra(self):
        """面板要的界面态：撤销能不能用、搜索进行到哪、对话是不是在等模型。"""
        return {
            "undo_label": self._undo_label(),
            "search_busy": self._search_busy,
            "search_hits": list(self._search_hits),
            "search_err": self._search_err,
            "search_query": self._search_query,
            "suggest_busy": self._sq_busy,
            "suggest_queries": list(self._sq),
            "chat_busy": self._chat_busy,
            "live": self._live,          # 正在流式吐出来的那句（§2.33）
        }

    @staticmethod
    def _ball_payload(b, mats, placed):
        """一颗球 = 池子里的一颗段球。短评这一版由本地规则给（模型版下一版接）。

        mats 给池子画"素材球"（只要类型）；mats_raw 给面板的选中卡逐条列出来
        （要 body / path / 缩略图）。
        """
        mats = list(mats or [])
        return {
            "id": b["id"],
            "n": (int(b.get("seq", 0)) + 1) if placed else None,
            "label": b.get("question") or "",
            "deep": b.get("deep") or "",
            "mats": [m.get("kind") or "note" for m in mats],
            "mats_raw": mats,
            "status": b.get("status") or "empty",
            "note": (b.get("note") or "").strip() or brain.comment_local(b, mats),
            "px": int(b.get("px", -1)), "py": int(b.get("py", -1)),
        }

    # ---------------- 模型（后台跑，回主线程落地）----------------
    def _kick_llm(self):
        """让模型看一眼当前状态。失败/超时都无声无息，界面照旧。"""
        if not llm.available() or not self.current_id:
            return
        t = self.topic()
        if not t:
            return
        self._llm_gen += 1
        gen = self._llm_gen
        beats = store.list_placed(self.current_id)     # 只让模型看见"已编排"的段
        mbb = store.materials_by_beat(self.current_id)
        un = store.list_unassigned(self.current_id)
        capped = bool(t["capped"])

        def work():
            props = brain.llm_proposals(beats, mbb, un, capped)
            self._cq.put(lambda: self._llm_land(gen, props))

        threading.Thread(target=work, daemon=True).start()

    def _llm_land(self, gen, props):
        """只在这个函数里碰界面 —— 它跑在主线程上。"""
        if gen != self._llm_gen:
            return
        if props is not None:
            self._llm_props = props
        if self.panel.isVisible():
            self.panel.refresh()

    def _kick_llm_seed(self, tid, name, bodies):
        """立项后请模型重写一版「初始理解」和 payoff（用户还没动手时才替换）"""
        if not llm.available():
            return
        self._llm_gen += 1
        gen = self._llm_gen

        def work():
            seeds = brain.llm_seed_beats(name, bodies)
            payoff = brain.llm_payoff(name, [{"body": b} for b in bodies])
            self._cq.put(lambda: self._llm_land_seed(gen, tid, seeds, payoff))

        threading.Thread(target=work, daemon=True).start()

    def _llm_land_seed(self, gen, tid, seeds, payoff):
        if gen != self._llm_gen or self.current_id != tid:
            return
        t = store.get_topic(tid)
        if not t or t["capped"]:
            return
        beats = store.list_placed(tid)
        # 只在"用户还没开始动"时替换：怕把人家已经改过的东西盖掉
        if seeds and len(beats) == 3 and store.count_fed(tid) == 0 \
                and not any(b["locked"] for b in beats):
            for b, q in zip(beats, seeds):
                store.update_beat(b["id"], q)
            self._notify("模型看过灵感池，把「初始理解」重写了一版。")
        if payoff:
            store.set_payoff(tid, payoff)
        self._tick()
        if self.panel.isVisible():
            self.panel.refresh()

    def orb_state(self):
        t = self.topic()
        if not t:
            return {"has_topic": False, "inbox_n": len(store.list_inbox()),
                    "pct": 0, "empty": 0, "capped": False}
        beats = store.list_placed(t["id"])
        mbb = store.materials_by_beat(t["id"])
        c = brain.counts(beats, mbb)
        return {"has_topic": True, "pct": brain.estimate(beats, mbb),
                "empty": c["empty"], "capped": bool(t["capped"]),
                "inbox_n": len(store.list_inbox())}

    # ---------------- 动作 ----------------
    def toggle_panel(self):
        if self.panel.isVisible():
            self.panel.hide_panel()
        else:
            self.panel.show_panel()
            self.panel.refresh()

    def _orb_menu(self, pos):
        m = QMenu()
        m.addAction("打开 / 收起面板", self.toggle_panel)
        m.addAction("不抢焦点自检", self.run_probe)
        m.addSeparator()
        m.addAction("退出 Skein", self.quit)
        m.exec(pos)

    def add_inbox(self, body, kind="note", src="（面板速记）", path=""):
        store.add_material(None, None, kind, body, src, path)
        self._tick()

    # ---------------- 素材落盘：上传 / 拖入的文件 ----------------
    def import_files(self, paths, beat_id=None):
        """把文件复制进 assets/ 并登记成素材。原件不动。

        复制而不是记路径：素材可能来自下载目录/桌面，被删被移就断了；
        复制一份进自己的库，这个期才真的"自足"。
        返回 (登记成功的条数, 失败的说明列表)。
        """
        tid = self.current_id
        used = set()
        ok, bad = 0, []
        for p in paths:
            try:
                p = os.path.abspath(p)
                if not os.path.isfile(p):
                    bad.append("%s：不是文件" % os.path.basename(p))
                    continue
                sub = ("topic-%d" % tid) if tid else "inbox"
                dest_dir = os.path.join(config.ASSETS_DIR, sub)
                os.makedirs(dest_dir, exist_ok=True)
                name = _unique_name(dest_dir, os.path.basename(p), used)
                dest = os.path.join(dest_dir, name)
                shutil.copy2(p, dest)
                used.add(name.lower())
                body = os.path.splitext(name)[0]
                store.add_material(tid, beat_id, config.kind_of(name), body,
                                   "（上传）" if tid else "（灵感池·上传）", dest)
                ok += 1
            except Exception as e:
                bad.append("%s：%s" % (os.path.basename(p), type(e).__name__))
        self._tick()
        if ok:
            where = "第 %d 段" % beat_id if beat_id else ("这一期" if tid else "灵感池")
            self._notify("收了 %d 个文件到%s（复制进 assets/，原件没动）。" % (ok, where))
        if bad:
            self._notify("有 %d 个没能收进来：%s" % (len(bad), "；".join(bad[:3])))
        return ok, bad

    def open_assets_dir(self):
        """打开素材文件夹 —— 剪映之类可以从这里直接拖。"""
        d = os.path.join(config.ASSETS_DIR,
                         ("topic-%d" % self.current_id) if self.current_id else "inbox")
        os.makedirs(d, exist_ok=True)
        self.open_url(d)

    def _notify(self, text):
        self.notice = text
        if self.panel.isVisible():
            self.panel.refresh()

    def start_topic(self, name):
        if self.current_id:
            self.detach_topic()
        inbox = store.list_inbox()
        # payoff 与初始理解都从"这一期的主题 + 灵感池"派生，不是写死的常量
        tid = store.create_topic(name, brain.payoff_for(name, inbox))
        seeds = brain.seed_beats(name, [m["body"] for m in inbox])
        for i, q in enumerate(seeds):
            store.add_beat(tid, i, q, origin="ai")
        n = store.take_inbox_into(tid)
        self.set_current(tid)
        self.dismissed = set()
        self._llm_props = None
        self._tick()
        self._notify("已立项：%s　｜　我给的是「初始理解」（%d 段），不是清单 —— 后面的段随素材长。%s"
                     % (name, len(seeds),
                        ("灵感池 %d 条已带入（还没指派）。" % n) if n else ""))
        self._kick_llm_seed(tid, name, [m["body"] for m in inbox])

    def feed(self, beat_id, body, kind="note", src="（面板输入）"):
        if not self.current_id:
            return
        store.add_material(self.current_id, beat_id, kind, body, src)
        self._tick()
        props = self.state().get("proposals") or []
        if props:
            self._notify("喂好了。顺带看了一眼结构：%s" % props[0]["text"])
        else:
            self._notify("喂好了。已经记进这一期，不用再做什么。")
        self._kick_llm()

    def apply_proposal(self, p):
        tid = self.current_id
        if not tid:
            return
        self._snapshot("采纳提议")     # 动结构之前照一张，采纳错了能撤回（§2.21）
        k = p.get("kind")
        if k == "lock":
            store.set_beat_lock(p["beat"], 1)
            self._notify("收下了：这一段按你说的「够了」。")
        elif k == "add":
            store.add_beat(tid, p["seq"], "「%s」到底是怎么回事？" % p["subject"], origin="ai")
            self._notify("结构长出新的一段：「%s」到底是怎么回事？" % p["subject"])
        elif k == "merge":
            store.merge_beats(p["a"], p["b"])
            store.renumber(tid)
            self._notify("两段并成一段了，素材都留着。")
        elif k == "drop":
            store.delete_beat(p["beat"])
            store.renumber(tid)
            self._notify("撤下了一段。它原来的素材退回「未指派」，没丢。")
        self.dismissed.add(p["key"])
        self._llm_props = None      # 结构动过了，旧提议作废
        self._tick()
        self.panel.refresh()
        self._kick_llm()

    def dismiss_proposal(self, p):
        self.dismissed.add(p["key"])
        self.panel.refresh()

    # ---------------- 撤销（§2.21）：动结构之前先照一张 ----------------
    def _snapshot(self, label):
        """把当前这一期照进撤销栈。只认当前这一期 —— 换期后旧的撤销项作废。"""
        if not self.current_id:
            return
        snap = store.snapshot_topic(self.current_id)
        if snap:
            self._undo.append((self.current_id, snap, label))
            del self._undo[:-20]        # 只留最近 20 步，别无限涨

    def _undo_label(self):
        if self._undo and self._undo[-1][0] == self.current_id:
            return self._undo[-1][2]
        return ""

    def undo(self):
        """撤回最近一次「照做 / 采纳」—— 整期还原到那之前。"""
        if not (self._undo and self._undo[-1][0] == self.current_id):
            self._notify("没有可撤回的动作了。")
            return
        _tid, snap, label = self._undo.pop()
        store.restore_topic(self.current_id, snap)
        self.dismissed = set()
        self._llm_props = None
        self._tick()
        self.panel.refresh()
        self._notify("已撤回「%s」，结构回到动它之前。素材一条没丢。" % label)

    def uncap(self):
        """取消封顶：回到"还能接着改"的状态（封顶本来就是人按的，人也能取消）。"""
        if not self.current_id:
            return
        store.set_capped(self.current_id, 0)
        self._llm_props = None
        self._tick()
        self.panel.refresh()
        self._notify("已取消封顶 —— 可以接着改。")

    def unlock_beat(self, bid):
        """把确认过的段改回「待确认」—— 确认错了随时改回来。"""
        store.set_beat_lock(bid, 0)
        self._llm_props = None
        self._tick()
        self.panel.refresh()
        self._notify("这一段改回「待确认」了。")

    # ---------------- 素材搜索（§2.22，免 key）----------------
    def search_web(self, query):
        q = (query or "").strip()
        if not q:
            return
        self._search_query = q
        self._search_hits = []
        self._search_err = ""
        self._search_busy = True
        self._search_gen += 1
        gen = self._search_gen

        def work():
            hits, err = websearch.search(q, 8)
            self._cq.put(lambda: self._search_land(gen, hits, err))

        threading.Thread(target=work, daemon=True).start()
        self.panel.refresh()

    def _search_land(self, gen, hits, err):
        if gen != self._search_gen:
            return
        self._search_busy = False
        self._search_hits = hits
        self._search_err = err
        if self.panel.isVisible():
            self.panel.refresh()

    # ---------------- AI 按期推荐搜法（§2.30，P-021）----------------
    def suggest_queries(self, focus=None):
        """让模型按「当前这一期」推荐几条能直接点的搜索词。

        后台线程跑、代际计数丢过期结果（同 §2.9）；没模型就什么都不显示，
        不编假推荐、不阻断任何主流程。focus 非空 = 只就选中的那一颗球推荐。
        """
        if not llm.available():
            self._sq = []
            self._sq_busy = False
            if self.panel.isVisible():
                self.panel.refresh()
            return
        self._sq = []
        self._sq_busy = True
        self._sq_gen += 1
        gen = self._sq_gen
        t = self.topic() or {}
        tid = self.current_id
        beats = store.list_placed(tid) if tid else []
        mbb = store.materials_by_beat(tid) if tid else {}

        def work():
            qs = brain.llm_search_queries(t, beats, mbb, focus)
            self._cq.put(lambda: self._sq_land(gen, qs))

        threading.Thread(target=work, daemon=True).start()
        self.panel.refresh()

    def _sq_land(self, gen, qs):
        if gen != self._sq_gen:
            return
        self._sq_busy = False
        self._sq = list(qs or [])
        if self.panel.isVisible():
            self.panel.refresh()

    def add_link(self, title, url, snippet=""):
        """把一条搜索 / 来源收成素材：src 存 URL，面板上「打开源」直达原文。"""
        body = (title or url).strip()
        if snippet:
            body = "%s ｜ %s" % (body, (snippet or "").strip())
        store.add_material(self.current_id, None, "link", body[:300], url)
        self._tick()
        if self.panel.isVisible():
            self.panel.refresh()
        self._notify("收进素材了（还没指派）—— 点右边的「打开源」直达原文。")

    # ---------------- 人自己编排结构（不等 AI 提议）----------------
    def add_beat_after(self, bid, question="（还没写）"):
        """在这一段后面手动插一段，返回新段 id。

        这里**不刷界面**：面板拿到 id 后要立刻把这一行切成编辑态，
        中间刷一次会闪出一个只读行（实测很难看），所以界面由调用方负责。
        """
        if not self.current_id:
            return None
        beats = store.list_placed(self.current_id)   # 在"已编排"里找插入位
        pos = len(beats)
        for i, b in enumerate(beats):
            if b["id"] == bid:
                pos = i + 1
                break
        nid = store.insert_beat(self.current_id, pos, question, origin="human")
        store.renumber(self.current_id)
        self.dismissed = set()      # 结构变了，之前忽略过的提议可以重新提
        self._llm_props = None
        self._tick()
        return nid

    def rename_beat(self, bid, question):
        """改这一段的措辞。只动文字，不影响任何一段的位置或状态。"""
        q = (question or "").strip()
        if not q:
            return
        store.update_beat(bid, q, origin="human")
        self._tick()

    def delete_beat(self, bid, quiet=False):
        """手动撤下一段。素材退回「未指派」，不丢东西。

        护栏只护"最后一颗已编排的球"：撤掉它结构就空了。还没编排的球随便撤
        （它本来就没进顺序）。

        quiet=True 用于把"加了但没写内容"的新段回收掉 —— 那不算一次撤销动作，
        弹一句「撤下了一段」只会让人困惑。
        """
        if not self.current_id:
            return
        placed = store.list_placed(self.current_id)
        if any(b["id"] == bid for b in placed) and len(placed) <= 1:
            self._notify("就剩这一段在编排里了，撤掉结构就空了 —— 留着吧。")
            return
        store.delete_beat(bid)
        store.renumber(self.current_id)
        self._llm_props = None
        self._tick()
        if quiet:
            return                      # 调用方自己刷：它可能正待在那个控件的信号里
        self._notify("撤下了一段。它原来的素材退回「未指派」，没丢。")

    def move_beat(self, bid, delta):
        """把一段往上/往下挪一格。顺序会影响「下一步」先推哪段。"""
        if not self.current_id:
            return
        store.move_beat(bid, delta)
        self._tick()

    def move_beat_to(self, bid, index):
        """拖到第 index 位（0-based）。"""
        if not self.current_id:
            return
        store.move_beat_to(bid, index)
        self._tick()

    # ---------------- 池子（§2.18）：投球 / 编排 / 移出 / 挪落点 ----------------
    def add_ball(self, question="（还没写）"):
        """投一颗新球：先混在池里漂着，不占编号 —— 还没决定它排第几。"""
        if not self.current_id:
            return None
        bid = store.add_beat_unplaced(self.current_id, question, origin="human")
        self.dismissed = set()
        self._llm_props = None
        self._tick()
        return bid

    def place_ball(self, bid, index):
        """把它排进第 index 位（0-based）—— 球上随即出现编号。"""
        if not self.current_id:
            return
        store.place_beat(bid, index)
        self._llm_props = None
        self._tick()

    def unplace_ball(self, bid):
        """移出编排：编号没了，退成一颗粒子混在池里（素材一段都不丢）。"""
        if not self.current_id:
            return False
        if len(store.list_placed(self.current_id)) <= 1:
            self._notify("就剩这一段在编排里了，移出去结构就空了 —— 留着吧。")
            return False
        store.unplace_beat(bid)
        self._llm_props = None
        self._tick()
        return True

    def move_ball(self, bid, px, py):
        """球被拖到了新落点。位置不代表顺序，所以这里只记"它待在哪儿"。

        刻意**不刷面板**：刷新会重建整棵子树，而这次拖动正是从池子自己的
        鼠标事件里发起的 —— 重建掉它，涟漪就没了。
        """
        store.set_beat_pos(bid, px, py)
        self._tick()

    # ---------------- 对话：聊思路，也用它改结构 ----------------
    def chat_log(self, tid=None):
        """这一期的对话（从库读，按 seq）。**永远只读这一期** —— 一期一个记忆（§2.28）。"""
        tid = tid or self.current_id
        return store.list_chat(tid) if tid else []

    def _chat_append(self, tid, role, text, actions=None, done=False, summary=""):
        """往这一期的对话里加一条（落库）。返回这条 id。"""
        return store.add_chat(tid, role, text, actions=actions, done=done, summary=summary)

    def say(self, text):
        """人说的这句进线程，然后请模型接话（后台线程，结果回主线程落地）。"""
        tid = self.current_id
        text = (text or "").strip()
        if not tid or not text:
            return
        history = self.chat_log(tid)        # 这句之前的历史（给模型当"最近聊过"）
        self._chat_append(tid, "you", text)
        if not llm.available():
            # 不假装能聊：模型没接上就直说，别让人对着空气说话
            self._chat_append(tid, "sys",
                "模型没接上（看标题栏那行状态），所以这句没人回。"
                "接上模型才能聊思路 —— 那之前我能做的只有按本地规则看着素材。")
            self.panel.refresh()
            return
        self._chat_gen += 1
        gen = self._chat_gen
        self._chat_busy = True          # 界面要说"正在想"，别让人对着空气等
        self._live = ""
        self._stop = threading.Event()
        stop = self._stop
        t = self.topic()
        # 给模型看**整口池子**：已编排 + 未编排的每一颗球（含未指派素材），
        # 它才能用工具去改任意一颗球（§2.26）。落地时再按 id 复核。
        beats = store.list_beats(tid)
        mbb = store.materials_by_beat(tid)
        un = store.list_unassigned(tid)

        def work():
            # 搜索 MCP（§2.31）：设置里开了才挂 —— 没开就 None，行为与从前完全一致
            try:
                mcp_c = mcp.get_client()
            except Exception:
                mcp_c = None
            got = brain.llm_chat(t, beats, mbb, history, text, un, mcp=mcp_c,
                                 on_delta=self._mk_delta(gen, tid),
                                 should_stop=stop.is_set)
            self._cq.put(lambda: self._chat_land(gen, tid, got))

        threading.Thread(target=work, daemon=True).start()
        self.panel.refresh()

    def _mk_delta(self, gen, tid):
        """把后台线程的流式增量**节流**转发到主线程（§2.33）。

        每次 delta 都往队列塞会淹掉 Qt 事件循环 —— 按 50ms 合并一次，只推"当前全文"。
        piece=None 表示清空重来（MCP 收口那轮要覆盖上一轮的临时内容）。
        """
        acc = {"s": "", "last": 0.0}

        def on_delta(piece):
            if piece is None:
                acc["s"] = ""
            else:
                acc["s"] += piece
            now = time.time()
            if piece is None or now - acc["last"] >= 0.05:
                acc["last"] = now
                txt = acc["s"]
                self._cq.put(lambda: self._stream_land(gen, tid, txt))
        return on_delta

    def _stream_land(self, gen, tid, text):
        """流式增量回主线程：只更新那一句"活的"AI 回复，不整面板重建。"""
        if gen != self._chat_gen or tid != self.current_id:
            return
        self._live = text
        if self.panel.isVisible():
            self.panel.push_live(text)

    def stop_stream(self):
        """点「停」：让正在跑的流式请求提前收口（已拿到的部分照常落库）。"""
        if self._stop is not None:
            self._stop.set()

    def _ball_label(self, bid):
        """把球的 id 说成人话：已编排的报段号，未编排的直说。"""
        try:
            bid = int(bid)
        except (TypeError, ValueError):
            return "这颗球"
        if not self.current_id:
            return "这颗球"
        for i, b in enumerate(store.list_placed(self.current_id)):
            if b["id"] == bid:
                return "第 %d 段" % (i + 1)
        return "未编排的球"

    def _chat_land(self, gen, tid, got):
        if gen != self._chat_gen or tid != self.current_id:
            return                      # 换期了或者又说了新的一句，这句作废
        self._chat_busy = False
        self._live = ""                 # 这句已经落库了，活的流式态清掉（§2.33）
        if got is None:
            self._chat_append(tid, "sys",
                "模型这次没答上来（超时或返回的不是 JSON），这句我没接住。")
        else:
            acts = got.get("actions") or []
            for a in acts:
                a["text"] = _act_text(a, self._ball_label)
            self._chat_append(tid, "ai", got["reply"], actions=acts, done=False)
        # 兜底触发蒸馏：这一期聊满 6 条就刷新一次"已定口径"（§2.28）
        if len(self.chat_log(tid)) >= self._mem_at + 6:
            self._kick_distill()
        if self.panel.isVisible():
            self.panel.refresh()

    # ---------------- 一期记忆（§2.28）：蒸馏"已定口径" ----------------
    def _kick_distill(self):
        """把这一期的对话蒸馏成「已定口径」写进 topic.memory（后台跑，失败留旧的）。

        触发点：人点「照做」后刷新一次 + 对话满 6 条兜底。没模型/在跑就跳过。
        """
        tid = self.current_id
        if not tid or not llm.available() or self._mem_busy:
            return
        t = self.topic()
        history = store.list_chat(tid)
        if not t or len(history) < 2:
            return
        beats = store.list_beats(tid)
        mbb = store.materials_by_beat(tid)
        self._mem_gen += 1
        gen = self._mem_gen
        self._mem_busy = True
        self._mem_at = len(history)

        def work():
            got = brain.llm_distill(t, history, beats, mbb)
            self._cq.put(lambda: self._distill_land(gen, tid, got))

        threading.Thread(target=work, daemon=True).start()

    def _distill_land(self, gen, tid, bullets):
        """蒸馏结果回主线程落库。失败（None）就保留旧记忆 —— 绝不阻断主流程。"""
        self._mem_busy = False
        if gen != self._mem_gen or tid != self.current_id:
            return
        if not bullets:
            return
        store.set_topic_memory(tid, "\n".join(bullets))

    def apply_actions(self, msg):
        """把这条 AI 回复里的结构改动一次落地（人点了「照做」才走到这里）。"""
        tid = self.current_id
        if not tid:
            return
        self._snapshot("照做")          # 照做错了能一键撤回（§2.21）
        done = []
        for a in msg.get("actions") or []:
            if self._apply_one(tid, a, msg):
                done.append(a["text"])
        msg["done"] = True
        msg["summary"] = "、".join(done) if done else "（没动成，段号可能已经变了）"
        if msg.get("id"):
            store.update_chat(msg["id"], actions=msg.get("actions") or [],
                              done=True, summary=msg["summary"])
        self._llm_props = None          # 结构动过了，别的提议作废
        self._tick()
        self.panel.refresh()
        if done:
            self._notify("照做了：" + msg["summary"])
        self._kick_distill()            # 点「照做」后刷新一次这一期的记忆（§2.28）

    def _apply_one(self, tid, a, msg):
        """落地一条工具调用（§2.26）。球一律按 id 复核：不存在 / 不属于本期 / 越界 → 不动。"""
        tool = a.get("tool")
        ar = a.get("args") or {}
        placed = store.list_placed(tid)

        def _beat(bid):
            try:
                b = store.get_beat(int(bid))
            except (TypeError, ValueError):
                return None
            return b if (b and b.get("topic_id") == tid) else None

        if tool == "set_payoff":
            store.set_payoff(tid, ar.get("text", ""))
            return True

        if tool == "add_ball":
            q = ar.get("question") or "（还没写）"
            pos = ar.get("position")
            if pos:
                store.insert_beat(tid, max(0, int(pos) - 1), q, origin="ai")
                store.renumber(tid)
            else:
                store.add_beat_unplaced(tid, q, origin="ai")
            return True

        if tool == "assign_material":
            try:
                m = store.get_material(int(ar.get("material")))
            except (TypeError, ValueError):
                m = None
            b = _beat(ar.get("ball"))
            if (not m or not b or m.get("beat_id") is not None
                    or m.get("topic_id") not in (None, tid)):
                return False
            store.assign(m["id"], b["id"])
            return True

        b = _beat(ar.get("ball"))
        if not b:
            return False
        bid = b["id"]
        if tool == "rename_ball":
            store.update_beat(bid, ar.get("question", ""), origin="ai")
        elif tool == "set_note":
            store.set_beat_note(bid, ar.get("text", ""))
        elif tool == "set_deep":
            store.set_beat_deep(bid, ar.get("text", ""))
        elif tool == "set_script":
            store.set_beat_script(bid, ar.get("text", ""))
        elif tool == "set_shot":
            store.set_beat_shot(bid, ar.get("text", ""))
        elif tool == "set_lock":
            store.set_beat_lock(bid, 1 if ar.get("locked") else 0)
        elif tool == "move_ball":
            store.move_beat_to(bid, int(ar.get("position", 1)) - 1)
        elif tool == "place_ball":
            store.place_beat(bid, int(ar.get("position", 1)) - 1)
        elif tool == "unplace_ball":
            if b.get("placed", 1) and len(placed) <= 1:
                return False            # 就剩这一段在编排里，移出去结构就空了
            store.unplace_beat(bid)
        elif tool == "drop_ball":
            if len(placed) <= 1:
                return False
            store.delete_beat(bid)
            store.renumber(tid)
        elif tool == "merge_balls":
            a2, b2 = _beat(ar.get("a")), _beat(ar.get("b"))
            if not a2 or not b2 or a2["id"] == b2["id"] or len(placed) <= 1:
                return False
            store.merge_beats(a2["id"], b2["id"])
            store.renumber(tid)
        else:
            return False
        return True

    def lock_beat(self, bid):
        store.set_beat_lock(bid, 1)
        s = self.orb_state()
        if s.get("has_topic") and s["empty"] == 0:
            c = store.materials_by_beat(self.current_id)
            beats = store.list_placed(self.current_id)
            if brain.counts(beats, c)["supported"] == 0:
                self.orb.pulse()
        self._tick()
        self._notify("已确认这一段。剩下的，你说了算。")

    def cap(self):
        if not self.current_id:
            return
        store.set_capped(self.current_id, 1)
        self.orb.pulse()
        self._llm_props = None
        self._tick()
        self.panel.refresh()
        self._kick_storyboard()     # 封顶后的产出：逐段口播稿 + 画面

    # ---------------- 成片稿：逐段口播稿 + 分镜 ----------------
    def _kick_storyboard(self):
        """请模型为每一段写口播稿和画面。没模型/失败就保持本地规则那版，界面照旧。"""
        if not self.current_id or not llm.available():
            return
        tid = self.current_id
        self._sb_gen = getattr(self, "_sb_gen", 0) + 1
        gen = self._sb_gen
        self._sb_busy = True
        t = self.topic()
        beats, mbb = store.list_placed(tid), store.materials_by_beat(tid)

        def work():
            got = brain.llm_storyboard(t, beats, mbb)
            self._cq.put(lambda: self._sb_land(gen, tid, got))

        threading.Thread(target=work, daemon=True).start()

    def _sb_land(self, gen, tid, got):
        self._sb_busy = False
        if gen != getattr(self, "_sb_gen", 0) or tid != self.current_id:
            return                       # 换期了或有更新的请求，这批作废
        if got:
            for bid, d in got.items():
                if d.get("script"):
                    store.set_beat_script(bid, d["script"])
                if d.get("shot"):
                    store.set_beat_shot(bid, d["shot"])
            self._notify("成片稿写好了：每一段都有口播稿和画面。")
        else:
            self._notify("模型这次没写出成片稿，先用本地规则那版（把素材串起来）。")
        if self.panel.isVisible():
            self.panel.refresh()

    def storyboard_text(self):
        """当前这一期的分镜表（Markdown）。"""
        if not self.current_id:
            return ""
        return brain.storyboard_md(self.topic(), store.list_placed(self.current_id),
                                   store.materials_by_beat(self.current_id))

    def export_storyboard(self, path):
        """把分镜表写成文件。素材文件本来就在 assets/ 下，这里只导出这张表。"""
        txt = self.storyboard_text()
        if not txt or not path:
            return False
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(txt)
        except Exception:
            return False
        self._notify("分镜表已导出：%s" % path)
        return True

    # ---------------- 编排包（M4）：Skein 只编排，出片交给别的软件 ----------------
    def export_pack(self, want_jianying=True):
        """导出编排包（分镜表 / 提示词 / 时间轴 / 字幕 / 口播稿 / 剪映草稿）。

        项目本身不渲染视频 —— 编排完交给剪映 / 即梦 / 可灵去做。
        """
        if not self.current_id:
            self._notify("还没有这一期")
            return
        if getattr(self, "_pk_busy", False):
            self._notify("正在导出，稍等")
            return
        tid = self.current_id
        self._pk_busy = True
        self._pk = {"msg": "导出中…", "dir": "", "warnings": [],
                    "count": {}, "jianying": ""}
        import export_pack

        def work():
            try:
                res = export_pack.export(tid, want_jianying=want_jianying)
            except Exception as ex:
                res = {"dir": "", "warnings": ["导出没跑起来：%s" % ex]}
            self._cq.put(lambda r=res: self._pk_land(tid, r))

        threading.Thread(target=work, daemon=True).start()

    def _pk_land(self, tid, res):
        self._pk_busy = False
        if tid != self.current_id:
            return
        arr = res.get("arr") or {}
        self._pk = {"msg": "导出好了" if res.get("dir") else "没导出来",
                    "dir": res.get("dir", ""),
                    "warnings": res.get("warnings") or [],
                    "count": arr.get("count") or {},
                    "total": arr.get("total", 0),
                    "n_shots": len(arr.get("shots") or []),
                    "jianying": res.get("jianying", ""),
                    "jianying_error": res.get("jianying_error", "")}
        if res.get("dir"):
            c = arr.get("count") or {}
            self._notify("编排包好了：%.0fs · %d 个镜头（现成 %d · 待生成 %d）"
                         % (arr.get("total", 0), len(arr.get("shots") or []),
                            c.get("material", 0), c.get("generate", 0)))
            self.orb.pulse()
        else:
            self._notify("没导出来：" + "；".join(res.get("warnings") or ["原因不明"]))
        if self.panel.isVisible():
            self.panel.refresh()

    def fill_pack(self, gen_dir=None):
        """回填：把生成好的画面（按镜头号命名）放回时间轴，重出分镜表与剪映草稿。"""
        if not self.current_id:
            self._notify("还没有这一期")
            return
        if getattr(self, "_pk_busy", False):
            self._notify("正忙着，稍等")
            return
        tid = self.current_id
        self._pk_busy = True
        import export_pack

        def work():
            try:
                res = export_pack.fill(tid, gen_dir)
            except Exception as ex:
                res = {"filled": 0, "left": [], "error": str(ex)}
            self._cq.put(lambda r=res: self._fill_land(tid, r))

        threading.Thread(target=work, daemon=True).start()

    def _fill_land(self, tid, res):
        self._pk_busy = False
        if tid != self.current_id:
            return
        if res.get("error"):
            self._notify("回填失败：%s" % res["error"])
            if self.panel.isVisible():
                self.panel.refresh()
            return
        left = res.get("left") or []
        if left:
            self._notify("回填了 %d 个，还差 %d 个：%s（把生成图按镜头号命名再回填一次）"
                         % (res.get("filled", 0), len(left), "、".join(left[:6])))
        else:
            self._notify("画面全齐了：%d 个镜头都有画面，剪映草稿已重出。"
                         % res.get("filled", 0))
            self.orb.pulse()
        # 顺手把编排包状态刷成最新（回填会改时间轴/分镜表）
        self.export_pack(False)

    def open_pack_dir(self):
        d = (getattr(self, "_pk", None) or {}).get("dir", "") or \
            os.path.join(config.app_dir(), "packs")
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)
        except Exception:
            self._notify("打不开目录：%s" % d)

    def open_pack_file(self, name):
        d = (getattr(self, "_pk", None) or {}).get("dir", "")
        p = os.path.join(d, name) if d else ""
        if p and os.path.exists(p):
            os.startfile(p)
        else:
            self._notify("还没有这个文件，先导出一次")

    def open_movie(self):
        self.open_pack_dir()

    def rename_topic(self, name):
        """改期名 / 改 payoff：只动这两个字段，结构和素材一律不碰。

        「换一个主题」和「把主题写对」是两件事 —— 以前只有前者（切断重立）。
        """
        if not self.current_id:
            return
        nm = (name or "").strip()
        if not nm or nm == self.topic()["name"]:
            return
        store.set_topic_name(self.current_id, nm)
        self._tick()

    def set_payoff(self, text):
        if not self.current_id:
            return
        t = (text or "").strip()
        if not t or t == (self.topic().get("payoff") or "").strip():
            return
        store.set_payoff(self.current_id, t)
        self._tick()

    # ---------------- 节点深挖：详细论证 / 就这一点发散 ----------------
    def deepen(self, bid):
        """把这一点的论证补起来。没有模型就上本地规则那版（先记着它降级了）。"""
        t = self.topic()
        if not t or not bid:
            return
        mats = store.materials_by_beat(self.current_id).get(bid, [])
        b = store.get_beat(bid)          # 未编排的球也能深挖：它同样是这一期的一段
        if b is None:
            return
        self._deep_bid = bid
        if not llm.available():
            store.set_beat_deep(bid, brain.deepen_local(b, mats))
            self._deep_bid = None
            self._notify("模型没接上 —— 这版论证是本地规则理的（只帮你清点已有的）。")
            self.panel.refresh()
            return
        gen, tid = self._llm_gen, self.current_id

        def work():
            got = brain.llm_deepen(t, b, mats)
            self._cq.put(lambda: self._deep_land(gen, tid, bid, got))

        threading.Thread(target=work, daemon=True).start()

    def _deep_land(self, gen, tid, bid, got):
        if tid != self.current_id:
            return
        self._deep_bid = None
        if got:
            store.set_beat_deep(bid, got)
            self._notify("这一点的论证补好了。")
        else:
            b = store.get_beat(bid)
            if b is not None:
                store.set_beat_deep(bid, brain.deepen_local(
                    b, store.materials_by_beat(tid).get(bid, [])))
            self._notify("模型这次没答上来，先用本地规则那版（只帮你清点已有的）。")
        if self.panel.isVisible():
            self.panel.refresh()

    def spread(self, bid):
        """就这一点发散：给几条能长成新段的方向。"""
        t = self.topic()
        if not t or not bid:
            return
        mats = store.materials_by_beat(self.current_id).get(bid, [])
        beats = store.list_placed(self.current_id)     # 发散时给模型的上下文只含已编排段
        b = store.get_beat(bid)
        if b is None:
            return
        self._spread_bid = bid
        if not llm.available():
            self._spread = {bid: brain.spread_local(b, mats)}
            self._spread_bid = None
            self._notify("模型没接上 —— 这几条是本地规则从素材关键词里提的。")
            self.panel.refresh()
            return
        tid = self.current_id

        def work():
            got = brain.llm_spread(t, b, mats, beats)
            self._cq.put(lambda: self._spread_land(tid, bid, got, b, mats))

        threading.Thread(target=work, daemon=True).start()

    def _spread_land(self, tid, bid, got, b, mats):
        if tid != self.current_id:
            return
        self._spread_bid = None
        self._spread = {bid: (got or brain.spread_local(b, mats))}
        if not got:
            self._notify("模型这次没答上来 —— 这几条是本地规则提的。")
        if self.panel.isVisible():
            self.panel.refresh()

    def promote_spread(self, bid, text):
        """把一条发散出来的方向变成新的一段（插在这一段后面）。"""
        q = re.sub(r"\s*｜.*$", "", (text or "").strip()).strip()
        if not q:
            return
        nid = self.add_beat_after(bid, q)
        self._spread = {}
        if nid:
            self._notify("长出一段新的：%s" % q)
            self.panel.refresh()

    def assign(self, mid, bid):
        store.assign(mid, bid)
        self._kick_llm()

    def detach_topic(self):
        self.set_current(None)
        self._llm_props = None
        self._chat_busy = False
        self._undo = []
        self._llm_gen += 1          # 让还在飞的请求作废
        self._sb_gen = getattr(self, "_sb_gen", 0) + 1
        self._sb_busy = False
        self._mem_gen += 1          # 一期记忆的蒸馏请求也一起作废
        self._mem_busy = False
        self._mem_at = 0
        self._tick()
        self.panel.refresh()

    def open_url(self, url):
        try:
            os.startfile(url)
        except Exception:
            webbrowser.open(url)

    def run_probe(self):
        before = winutil.snapshot("probe-before")
        try:
            self.orb.raise_()
        except Exception:
            pass
        after = winutil.snapshot("probe-after")
        winutil.append_probe(before)
        winutil.append_probe(after)
        QMessageBox.information(
            None, "Skein · 不抢焦点自检",
            before + "\n\n" + after +
            "\n\n判据：两次记录的「前台窗口」与「光标控件」应保持一致。\n"
            "若点击球后浏览器光标丢失，则 M0 不通过。\n\n日志：" + config.PROBE_LOG)

    def quit(self):
        try:
            self.orb.hide()
            self.tray.hide()
        except Exception:
            pass
        self.qapp.quit()

    # ---------------- 托盘 ----------------
    def _mk_tray(self):
        pm = QPixmap(64, 64)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor("#2f3446"), 7))
        p.drawEllipse(QRectF(10, 10, 44, 44))
        p.setPen(QPen(QColor(GOLD), 7))
        p.drawArc(QRectF(10, 10, 44, 44), 90 * 16, -250 * 16)
        p.end()

        self.tray = QSystemTrayIcon(QIcon(pm))
        self.tray.setToolTip("Skein")
        m = QMenu()
        m.addAction("打开 / 收起面板", self.toggle_panel)
        m.addAction("不抢焦点自检", self.run_probe)
        m.addSeparator()
        m.addAction("退出 Skein", self.quit)
        self.tray.setContextMenu(m)
        self.tray.activated.connect(
            lambda r: self.toggle_panel() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    # ---------------- 后台 ----------------
    def _start_hotkey(self):
        """Ctrl+Alt+S 唤起面板（失败不影响主程序）"""
        MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, VK_S = 0x0001, 0x0002, 0x4000, 0x53
        WM_HOTKEY = 0x0312

        def worker():
            u = ctypes.windll.user32
            if not u.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_S):
                return
            msg = ctypes.wintypes.MSG()
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == WM_HOTKEY:
                    self._q.put(1)

        threading.Thread(target=worker, daemon=True).start()

    def _drain(self):
        try:
            while True:
                self._q.get_nowait()
                if not self.panel.isVisible():
                    self.panel.show_panel()
                self.panel.refresh()
        except queue.Empty:
            pass
        except Exception:
            pass
        # 后台线程交回来的活儿，只在这里（主线程）执行
        try:
            while True:
                self._cq.get_nowait()()
        except queue.Empty:
            pass
        except Exception:
            pass

    def _tick(self):
        try:
            self.orb.set_state(self.orb_state())
        except Exception:
            pass


def _unique_name(dest_dir, name, taken):
    """同名不覆盖：依次退到 name-1 / name-2。既看本批已用过的，也看盘上已有的。"""
    base, ext = os.path.splitext(name)
    cand, i = name, 1
    while cand.lower() in taken or os.path.exists(os.path.join(dest_dir, cand)):
        cand = "%s-%d%s" % (base, i, ext)
        i += 1
    return cand


def _act_text(a, label_of=None):
    """把一条工具调用说成人话（给"它会动这些"和事后小结用）。"""
    tool = a.get("tool")
    ar = a.get("args") or {}
    lab = label_of or (lambda b: "第 %d 段" % b)

    def cut(s, n=16):
        s = (s or "").strip()
        return s if len(s) <= n else s[:n] + "…"

    if tool == "rename_ball":
        return "%s改成「%s」" % (lab(ar.get("ball")), cut(ar.get("question")))
    if tool == "set_note":
        return "%s换一句短评「%s」" % (lab(ar.get("ball")), cut(ar.get("text")))
    if tool == "set_deep":
        return "补%s的论证「%s」" % (lab(ar.get("ball")), cut(ar.get("text")))
    if tool == "set_script":
        return "写%s口播稿「%s」" % (lab(ar.get("ball")), cut(ar.get("text")))
    if tool == "set_shot":
        return "改%s画面「%s」" % (lab(ar.get("ball")), cut(ar.get("text")))
    if tool == "set_lock":
        return "%s%s" % (lab(ar.get("ball")), "标为够了" if ar.get("locked") else "取消确认")
    if tool == "add_ball":
        pos = ar.get("position")
        return "投一颗新球（%s）「%s」" % ("第 %s 位" % pos if pos else "先放池里",
                                        cut(ar.get("question")))
    if tool == "drop_ball":
        return "撤下%s" % lab(ar.get("ball"))
    if tool == "move_ball":
        return "%s挪到第 %s 位" % (lab(ar.get("ball")), ar.get("position"))
    if tool == "place_ball":
        return "%s排进第 %s 位" % (lab(ar.get("ball")), ar.get("position"))
    if tool == "unplace_ball":
        return "%s移出编排" % lab(ar.get("ball"))
    if tool == "merge_balls":
        return "%s和%s合并" % (lab(ar.get("a")), lab(ar.get("b")))
    if tool == "set_payoff":
        return "payoff 换成「%s」" % cut(ar.get("text"), 20)
    if tool == "assign_material":
        return "把素材 #%s 指给%s" % (ar.get("material"), lab(ar.get("ball")))
    return "改一下"


_LOCK_SOCK = None


def _already_running():
    """单实例：用本地端口占用做锁（比命名互斥体可靠，且不会弹阻塞对话框）"""
    global _LOCK_SOCK
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 50913))
        s.listen(1)
        _LOCK_SOCK = s
        return False
    except OSError:
        return True


def main():
    if _already_running():
        return          # 已经在跑：静默退出，不打扰
    app = App()
    sys.exit(app.qapp.exec())


if __name__ == "__main__":
    main()
