# -*- coding: utf-8 -*-
"""Skein · 视觉取样（开发工具）

把球与面板直接渲染成 PNG，便于在不开太多窗口的情况下检查质感。
用法：python shot.py
输出：%TEMP%\\skein_shots\\*.png
"""
import os
import sys

os.environ.setdefault("QT_SCALE_FACTOR", "1")   # 取样时锁定 1x，避免 DPI 干扰

from PyQt6.QtCore import Qt, QPointF, QRectF, QRect
from PyQt6.QtGui import QPainter, QColor, QFont, QPixmap
from PyQt6.QtWidgets import QApplication

import theme
import brain
import basin as basinmod
import panel as panelmod
import pool as poolmod
from orb import Orb, SIZE
from panel import Panel

# 取样要的是确定性：动效全停住，否则每张图的水面/块都不一样
panelmod.ANIMATE = False
poolmod.ANIMATE = False
basinmod.ANIMATE = False

OUT = os.path.join(os.environ.get("TEMP", "."), "skein_shots")
os.makedirs(OUT, exist_ok=True)


class FakeApp:
    """给 Orb / Panel 喂假数据，不碰数据库"""

    def __init__(self, mode="topic"):
        self.mode = mode

    # 面板里的按钮回调：取样时不需要真的做事
    def detach_topic(self, *a):
        pass

    def start_topic(self, *a):
        pass

    def cap(self, *a):
        pass

    def lock_beat(self, *a):
        pass

    def assign(self, *a):
        pass

    def add_inbox(self, *a):
        pass

    def feed(self, *a):
        pass

    def open_url(self, *a):
        pass

    def apply_proposal(self, *a):
        pass

    def dismiss_proposal(self, *a):
        pass

    # 对话（取样时不聊，给个空线程就够）
    def chat_log(self, *a):
        return []

    def say(self, *a):
        pass

    def apply_actions(self, *a):
        pass

    # 池子上的动作（取样时点不到，但留着免得 AttributeError）
    def add_ball(self, *a):
        return None

    def place_ball(self, *a):
        pass

    def unplace_ball(self, *a):
        return False

    def move_ball(self, *a):
        pass

    def add_beat_after(self, *a):
        return None

    def rename_beat(self, *a):
        pass

    def delete_beat(self, *a):
        pass

    def move_beat(self, *a):
        pass

    def import_files(self, *a):
        return 0, []

    def open_assets_dir(self, *a):
        pass

    def storyboard_text(self, *a):
        return ""

    def export_storyboard(self, *a):
        return False

    def _kick_storyboard(self, *a):
        pass

    # 节点深挖 / 改主题
    def rename_topic(self, *a):
        pass

    def set_payoff(self, *a):
        pass

    def deepen(self, *a):
        pass

    def spread(self, *a):
        pass

    def promote_spread(self, *a):
        pass

    def state(self):
        inbox = [
            {"kind": "速记", "body": "以后可以做一期讲 MCP 到底新在哪"},
            {"kind": "文字", "body": "它不是函数调用的替代品 —— 集成层的最小公约数"},
        ]
        if self.mode == "inbox":
            return {"has_topic": False, "inbox_n": len(inbox), "inbox_items": inbox}

        beats = [
            {"id": 1, "seq": 0, "placed": 1, "deep": "", "px": -1, "py": -1,
             "question": "开头那句反常识，立不立得住？", "status": "locked",
             "mats": [{"kind": "文字", "body": "它不是函数调用的替代品 —— 集成层的最小公约数"}]},
            {"id": 2, "seq": 1, "placed": 1, "deep": "", "px": -1, "py": -1,
             "question": "为什么它不能只是函数调用？", "status": "locked",
             "mats": [{"kind": "文字", "body": "function calling 只管模型怎么表达，不管函数在哪"}]},
            {"id": 3, "seq": 2, "placed": 1, "deep": "", "px": -1, "py": -1,
             "question": "那核心机制到底是什么？", "status": "supported",
             "mats": [{"kind": "文字", "body": "协议把「发现能力」和「使用能力」标准化了"},
                      {"kind": "视频", "body": "协议分层示意"}]},
            {"id": 4, "seq": 3, "placed": 1, "deep": "", "px": -1, "py": -1,
             "question": "拿什么证明它在被用？（一手优先）", "status": "empty", "mats": []},
            {"id": 5, "seq": 4, "placed": 0, "deep": "", "px": -1, "py": -1,
             "question": "（还没编排）协议会不会变成新的中间商？", "status": "empty", "mats": []},
        ]
        # 池子要的是"这一期所有的球"：已编排的（有编号）+ 还没编排的
        balls = []
        for b in beats:
            mats = [dict(m) for m in (b["mats"] or [])]
            balls.append({
                "id": b["id"], "n": (b["seq"] + 1) if b["placed"] else None,
                "label": b["question"], "deep": b["deep"],
                "mats": [m["kind"] for m in mats], "mats_raw": mats,
                "status": b["status"],
                "note": brain.comment_local(b, mats),
                "px": b["px"], "py": b["py"],
            })
        return {
            "has_topic": True,
            "topic": {"id": 1, "name": "E005 · MCP 到底创新在哪", "form": "图文",
                      "target_count": 7, "payoff": "MCP 不是「又一个函数调用」，它是集成层的最小公约数",
                      "capped": 0},
            "beats": beats,
            "balls": balls, "n_unplaced": 1,
            "mbb": {}, "counts": {"empty": 1, "supported": 1, "locked": 2},
            "pct": 62,
            "next": "补第 4 段：拿什么证明它在被用？（一手优先）",
            "why": "第 4 段还是空的，先补它最划算。",
            "capped": False,
            "inbox_n": 2, "inbox_items": inbox,
            "unassigned": [{"id": 9, "kind": "图片", "body": "协议分层示意图（发现能力 / 使用能力）"}],
            "proposals": [
                {"kind": "lock", "beat": 3, "key": "k1",
                 "text": "第 3 段已经堆了 4 条素材 —— 再加也不会改变讲法了，建议收「够了」",
                 "action": "够了，收这一段"},
                {"kind": "add", "seq": 5, "subject": "WebGL", "key": "k2",
                 "text": "待指派素材里反复出现「WebGL」，但结构里没有这一段 —— 要不要补一段专门问它？",
                 "action": "插入一段：WebGL"},
            ],
        }


def shot_orb_strip(app):
    states = [
        ("① 空态", {"has_topic": False, "inbox_n": 0, "pct": 0, "empty": 0, "capped": False}),
        ("② 灵感池有货", {"has_topic": False, "inbox_n": 3, "pct": 0, "empty": 0, "capped": False}),
        ("③ 有骨架 ≈62%", {"has_topic": True, "inbox_n": 0, "pct": 62, "empty": 2, "capped": False}),
        ("④ 已封顶", {"has_topic": True, "inbox_n": 0, "pct": 92, "empty": 0, "capped": True}),
    ]
    gap, pad = 24, 22
    w = len(states) * SIZE + (len(states) - 1) * gap + pad * 2
    h = SIZE + pad * 2 + 34
    out = QPixmap(w, h)
    out.fill(QColor("#0f1115"))
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    for i, (label, st) in enumerate(states):
        orb = Orb(app)
        orb.resize(SIZE, SIZE)
        orb.set_state(st)
        orb.show()
        QApplication.processEvents()
        pm = orb.grab()
        orb.hide()
        orb.deleteLater()
        QApplication.processEvents()
        x = pad + i * (SIZE + gap)
        p.drawPixmap(QRectF(x, pad, SIZE, SIZE), pm, QRectF(0, 0, pm.width(), pm.height()))
        p.setPen(QColor("#9aa3b2"))
        f = QFont(theme.FONT)
        f.setPixelSize(13)
        p.setFont(f)
        p.drawText(QRectF(x - 6, pad + SIZE + 8, SIZE + 12, 22),
                   Qt.AlignmentFlag.AlignCenter, label)
    p.end()
    out = out.scaled(out.width() * 3, out.height() * 3,
                     Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    path = os.path.join(OUT, "orb_states.png")
    out.save(path)
    print("saved", path)


def shot_panel(fake, name):
    p = Panel(fake)
    p.show()
    p.refresh()
    QApplication.processEvents()
    w, h = p.width(), p.height()
    out = QPixmap(w, h)
    out.fill(QColor("#232733"))
    p.render(out)
    p.hide()
    path = os.path.join(OUT, name)
    out.save(path)
    print("saved", path, "%dx%d" % (w, h))


def shot_morph():
    """把"液态展开"按进度采样成一条胶片，便于逐帧看形状/圆角/材质"""
    fake = FakeApp("topic")
    p = Panel(fake)
    p._animate = False
    p.root.setVisible(False)      # 与真实展开一致：内容由 _set_morph 在 CONTENT_AT 才放出来
    orb = QRect(1170, 451, 92, 92)
    full = QRect(674, 116, 486, 736)          # _place_near 在这块屏上的结果
    ks = [0.00, 0.12, 0.26, 0.42, 0.58, 0.72, 0.88, 1.00]
    scale = 0.40
    cw, ch = 216, 330
    out = QPixmap(cw * len(ks), ch + 30)
    out.fill(QColor("#2b3a4a"))               # 模拟桌面壁纸，玻璃才看得出来
    q = QPainter(out)
    q.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    q.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    for i, k in enumerate(ks):
        r = QRect(int(orb.x() + (full.x() - orb.x()) * k),
                  int(orb.y() + (full.y() - orb.y()) * k),
                  int(orb.width() + (full.width() - orb.width()) * k),
                  int(orb.height() + (full.height() - orb.height()) * k))
        p.setGeometry(r)
        p._set_morph(k)
        p.show()
        if k >= 0.72:
            p.refresh()
        QApplication.processEvents()
        pm = p.grab()
        tw = max(8, int(pm.width() * scale))
        th = max(8, int(pm.height() * scale))
        x = i * cw + (cw - tw) // 2
        y = ch - th
        q.drawPixmap(QRect(x, y, tw, th), pm)
        q.setPen(QColor("#9aa3b2"))
        f = QFont(theme.FONT)
        f.setPixelSize(12)
        q.setFont(f)
        q.drawText(QRect(i * cw, ch + 6, cw, 22),
                   Qt.AlignmentFlag.AlignCenter, "m=%.2f" % k)
    p.hide()
    q.end()
    path = os.path.join(OUT, "morph_strip.png")
    out.save(path)
    print("saved", path)


def main():
    qapp = QApplication(sys.argv)
    qapp.setStyleSheet(theme.QSS)
    shot_orb_strip(FakeApp("inbox"))
    shot_panel(FakeApp("inbox"), "panel_inbox.png")
    shot_panel(FakeApp("topic"), "panel_topic.png")
    shot_morph()
    print("OUT DIR:", OUT)


if __name__ == "__main__":
    main()
