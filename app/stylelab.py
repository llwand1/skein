# -*- coding: utf-8 -*-
"""Skein · 风格实验台（开发工具）

同一套结构，换上不同材质/字色，渲染成 PNG，用来比"哪种不落俗套"。
不做业务，只做像素。用法：python stylelab.py
输出：%TEMP%\\skein_shots\\style_*.png
"""
import os
import sys
from string import Template

os.environ.setdefault("QT_SCALE_FACTOR", "1")

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPainter, QColor, QFont, QPixmap
from PyQt6.QtWidgets import QApplication

import theme
import basin as basinmod
import panel as panelmod
from panel import Panel

basinmod.ANIMATE = False     # 取样要确定性：水面的相位每张图都得一样

OUT = os.path.join(os.environ.get("TEMP", "."), "skein_shots")
os.makedirs(OUT, exist_ok=True)

QSS = Template("""
* { font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; }
QWidget { color: $fg; font-size: 12px; }
#root { background: transparent; border: none; }

#titlebar { background: transparent; border-bottom: 1px solid $line; }
#branddot { color: $gold; font-size: 8px; }
#appname  { font-size: 13px; font-weight: 600; color: $fg; letter-spacing: .3px; }
#subtitle { font-size: 11px; color: $fg3; }
QPushButton#close { background: transparent; border: none; color: $fg3; font-size: 15px; padding: 0 6px; }
QPushButton#close:hover { color: $red; }

QFrame#stepbar { background: $surf2; border: 1px solid $hair; border-radius: 12px; }
QLabel#step-on {
  background: $gold; color: $on_gold; border-radius: 7px;
  padding: 5px 9px; font-size: 11px; font-weight: 700;
}
QLabel#step-done { background: transparent; color: $green; border-radius: 7px; padding: 5px 9px; font-size: 11px; font-weight: 600; }
QLabel#step-off  { background: transparent; color: $fg3; border-radius: 7px; padding: 5px 9px; font-size: 11px; }
QLabel#step-arw  { color: $hair2; font-size: 11px; }

QFrame#card { background: $surf; border: 1px solid $hair; border-radius: 12px; }
QFrame#nowcard { background: $now_bg; border: 1px solid $now_bd; border-left: 3px solid $gold; border-radius: 12px; }
QFrame#payoff { background: $surf; border: 1px solid $hair; border-left: 3px solid $payoff_bd; border-radius: 12px; }
QFrame#notice { background: $notice_bg; border: 1px solid $notice_bd; border-radius: 11px; }
QFrame#row-empty, QFrame#row-supported, QFrame#row-locked {
  background: $surf; border: 1px solid $hair; border-radius: 11px;
}
QFrame#row-empty:hover, QFrame#row-supported:hover, QFrame#row-locked:hover { border-color: $hair2; }

QLabel#h1  { font-size: 14px; font-weight: 600; color: $fg; }
QLabel#h2  { font-size: 12px; font-weight: 700; color: $fg; }
QLabel#cap { font-size: 10px; color: $fg3; letter-spacing: .9px; font-weight: 700;
             font-family: "Cascadia Mono", "Consolas", monospace; }
QLabel#dim { font-size: 11px; color: $fg3; }
QLabel#fg2 { font-size: 11.5px; color: $fg2; }
QLabel#gold { font-size: 11.5px; font-weight: 600; color: $gold_text; }
QLabel#blue { font-size: 11px; font-weight: 700; color: $blue; }
QLabel#now  { font-size: 15px; font-weight: 700; color: $fg; }

QPlainTextEdit { background: $input_bg; border: 1px solid $hair; border-radius: 9px;
  padding: 8px 10px; color: $fg; selection-background-color: $sel; }
QPlainTextEdit:focus { border: 1px solid $gold; }
QComboBox { background: $input_bg; border: 1px solid $hair; border-radius: 9px; padding: 6px 10px; color: $fg; }
QComboBox:hover { border-color: $hair2; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: $surf; border: 1px solid $hair; color: $fg;
  selection-background-color: $sel; outline: none; }

QPushButton { background: $btn_bg; border: 1px solid $hair; border-radius: 8px;
  padding: 7px 12px; color: $fg; font-size: 12px; }
QPushButton:hover { background: $btn_hover; border-color: $hair2; }
QPushButton#primary { background: $gold; border: 1px solid $gold_edge; color: $on_gold;
  font-weight: 700; padding: 11px 14px; border-radius: 9px; font-size: 13px; }
QPushButton#primary:hover { background: $gold_hover; }
QPushButton#green { background: $green_bg; border: 1px solid $green_bd; color: $green; font-weight: 600; }
QPushButton#green:hover { border-color: $green; }
QPushButton#plain { background: transparent; border: 1px solid $hair; color: $fg2; }
QPushButton#plain:hover { border-color: $hair2; color: $fg; }

QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 4px 2px 4px 0; }
QScrollBar::handle:vertical { background: $hair2; border-radius: 4px; min-height: 32px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QMenu { background: $surf; border: 1px solid $hair; padding: 5px; }
QMenu::item { padding: 6px 22px 6px 14px; border-radius: 7px; color: $fg; }
QMenu::item:selected { background: $sel; }
""")

# ---------------------------------------------------------------- 变体
# 1) 暖墨：构图不动，把"中性色"从冷灰换成带品牌暖调的墨色（参考 color-and-contrast
#    "neutrals should not be neutral"），文字用米白而不是纯白。
WARM = dict(
    bg="#100e0b", surf="#191612", surf2="#221e18", hair="#332c23", hair2="#4a4034",
    line="rgba(255,255,255,0.05)",
    fg="#f4ede1", fg2="#b6ab97", fg3="#807664",
    gold="#FFD01F", gold_text="#FFD01F", gold_edge="#FFE27A", gold_hover="#FFDE63",
    on_gold="#1a160d",
    green="#9ecf4e", green_bg="#1d2410", green_bd="#3f5220",
    blue="#86b6e6", red="#e8785f", amber="#e8a800",
    now_bg="#1d1a0e", now_bd="#4d4013",
    payoff_bd="#6b5a1c", notice_bg="#1e1a11", notice_bd="#514322",
    input_bg="#141210", btn_bg="#1d1915", btn_hover="#252019", sel="#3a3323",
)

# 2) 纸面：整块反相成暖白纸，深棕字 + 品牌绿 + 金。参照奶蛙讲堂本身是"讲课"内容，
#    纸面更像一本讲义，也彻底躲开"深色面板 + 霓虹"这个一致性套路。
PAPER = dict(
    bg="#efe9dc", surf="#f8f4ec", surf2="#efe8da", hair="#ddd4c3", hair2="#c3b7a1",
    line="rgba(60,45,20,0.08)",
    fg="#2e2820", fg2="#6b6152", fg3="#9b9080",
    gold="#FFD01F", gold_text="#b07d00", gold_edge="#e8bc28", gold_hover="#FFDE63",
    on_gold="#2b2205",
    green="#4f8f1e", green_bg="#eaf3dd", green_bd="#b7d492",
    blue="#2f6fb0", red="#c0392b", amber="#a97a00",
    now_bg="#fdf6e0", now_bd="#e6d398",
    payoff_bd="#d8c48a", notice_bg="#fbf0e2", notice_bd="#e0cdb0",
    input_bg="#fffdf8", btn_bg="#f3eee3", btn_hover="#e9e2d4", sel="#e6dcc6",
)

VARIANTS = [("warm", "① 暖墨 Warm Ink", WARM), ("paper", "② 纸面 Paper", PAPER)]


def apply_variant(p):
    panelmod.GOLD = p["gold"]
    panelmod.GOLD2 = p["gold_edge"]
    panelmod.FG3 = p["fg3"]
    panelmod.GREEN = p["green"]
    panelmod.RED = p["red"]
    panelmod.AMBER = p["amber"]
    panelmod.BLUE = p["blue"]
    panelmod.DOT = {"empty": p["red"], "supported": p["amber"], "locked": p["green"]}
    panelmod.FILL = {"empty": p["now_bg"], "supported": p["now_bg"], "locked": p["green_bg"]}
    theme.SURF = p["surf"]
    theme.HAIR = p["hair"]
    theme.FG = p["fg"]
    theme.FG2 = p["fg2"]
    theme.FG3 = p["fg3"]
    theme.GOLD = p["gold"]
    return QSS.substitute(p)


class FakeApp:
    def detach_topic(self, *a): pass
    def start_topic(self, *a): pass
    def cap(self, *a): pass
    def lock_beat(self, *a): pass
    def assign(self, *a): pass
    def add_inbox(self, *a): pass
    def feed(self, *a): pass
    def open_url(self, *a): pass
    def add_beat_after(self, *a): return None
    def rename_beat(self, *a): pass
    def delete_beat(self, *a): pass
    def move_beat(self, *a): pass
    # 池子上那几个动作
    def add_ball(self, *a): return None
    def place_ball(self, *a): pass
    def unplace_ball(self, *a): return False
    def move_ball(self, *a): pass
    # 节点深挖 / 改主题
    def rename_topic(self, *a): pass
    def set_payoff(self, *a): pass
    def deepen(self, *a): pass
    def spread(self, *a): pass
    def promote_spread(self, *a): pass
    notice = ""

    def state(self):
        beats = [
            {"id": 1, "seq": 0, "placed": 1, "deep": "", "px": -1, "py": -1,
             "question": "开头那句反常识，立不立得住？", "status": "locked",
             "mats": [{"kind": "文字", "body": "它不是函数调用的替代品 —— 集成层的最小公约数"}]},
            {"id": 2, "seq": 1, "placed": 1, "deep": "", "px": -1, "py": -1,
             "question": "为什么它不能只是函数调用？", "status": "locked",
             "mats": [{"kind": "文字", "body": "function calling 只管模型怎么表达，不管函数在哪"}]},
            {"id": 3, "seq": 2, "placed": 1, "deep": "", "px": -1, "py": -1,
             "question": "那核心机制到底是什么？", "status": "supported",
             "mats": [{"kind": "文字", "body": "协议把「发现能力」和「使用能力」标准化了"}]},
            {"id": 4, "seq": 3, "placed": 1, "deep": "", "px": -1, "py": -1,
             "question": "拿什么证明它在被用？（一手优先）", "status": "empty", "mats": []},
            {"id": 5, "seq": 4, "placed": 0, "deep": "", "px": -1, "py": -1,
             "question": "（还没编排）收口：那到底该怎么看它？", "status": "empty", "mats": []},
        ]
        balls = []
        for b in beats:
            mats = [dict(m) for m in (b["mats"] or [])]
            balls.append({"id": b["id"], "n": (b["seq"] + 1) if b["placed"] else None,
                          "label": b["question"], "deep": b["deep"],
                          "mats": [m["kind"] for m in mats], "mats_raw": mats,
                          "status": b["status"], "note": "",
                          "px": b["px"], "py": b["py"]})
        return {
            "has_topic": True,
            "topic": {"id": 1, "name": "E005 · MCP 到底创新在哪", "form": "图文",
                      "target_count": 7, "payoff": "MCP 不是「又一个函数调用」，它是集成层的最小公约数",
                      "capped": 0},
            "beats": beats, "balls": balls, "n_unplaced": 1,
            "mbb": {}, "counts": {"empty": 1, "supported": 1, "locked": 2},
            "pct": 62, "next": "补第 4 段：拿什么证明它在被用？（一手优先）",
            "why": "第 4 段还是空的，先补它最划算。", "capped": False,
            "inbox_n": 2, "inbox_items": [],
            "unassigned": [{"id": 9, "kind": "image", "body": "协议分层示意图"}],
        }


def render(p, name, label):
    p.show()
    p.refresh()
    QApplication.processEvents()
    w, h = p.width(), p.height()
    shot = QPixmap(w, h)
    shot.fill(QColor("#c9cdd4"))
    p.render(shot)
    p.hide()
    path = os.path.join(OUT, name)
    shot.save(path)
    print("saved", path)
    return shot, label


def main():
    qapp = QApplication(sys.argv)
    panels = []
    for key, label, pal in VARIANTS:
        qss = apply_variant(pal)
        qapp.setStyleSheet(qss)
        p = Panel(FakeApp())
        p.setStyleSheet(qss)
        fixed, _ = render(p, "style_%s.png" % key, label)
        panels.append((fixed, label, pal["bg"]))
    # 拼一张对比图
    gap, pad, hdr = 28, 26, 34
    w = sum(x.width() for x, _, _ in panels) + gap * (len(panels) - 1) + pad * 2
    h = panels[0][0].height() + pad * 2 + hdr
    out = QPixmap(w, h)
    out.fill(QColor("#c9cdd4"))
    q = QPainter(out)
    q.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    x = pad
    for fixed, label, bg in panels:
        q.drawPixmap(x, pad, fixed)
        q.setPen(QColor("#3a3f46"))
        f = QFont(theme.FONT)
        f.setPixelSize(15)
        f.setWeight(QFont.Weight.DemiBold)
        q.setFont(f)
        q.drawText(QRectF(x, pad + fixed.height() + 4, fixed.width(), 26),
                   Qt.AlignmentFlag.AlignCenter, label)
        x += fixed.width() + gap
    q.end()
    out.save(os.path.join(OUT, "style_compare.png"))
    print("saved", os.path.join(OUT, "style_compare.png"))


if __name__ == "__main__":
    main()
