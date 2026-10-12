# -*- coding: utf-8 -*-
"""Skein · 自绘图标（§2.27）

为什么自绘：界面元素该是"画出来的几何"，不是借字体里现成的符号字形
（`⚙ ● × ＋ ↩ ✓ ▴ ▾ ⧉ ↑ ↓ →`）—— 字形在不同字体/系统下粗细、基线、占比都不可控，
而且一眼就看得出是临时凑的。这里每个图标都用 QPainter 画固定几何，颜色走主题令牌。

用法：
    icons.icon("close", 16, theme.FG3, theme.RED)        → QIcon（Normal/Active 两态）
    icons.IconButton("settings", obj="gear", tip="…")    → 会悬停变色的图标按钮
    icons.icon_label("dot", 8, theme.GOLD)               → 行内小图标（QLabel）
"""
from PyQt6.QtCore import Qt, QRectF, QPointF, QSize

from PyQt6.QtGui import (QPixmap, QPainter, QColor, QPen, QIcon, QPainterPath)

from PyQt6.QtWidgets import QPushButton, QLabel

import theme

NAMES = ("dot", "close", "plus", "check", "chev_up", "chev_down",
         "arrow_up", "arrow_down", "arrow_right", "copy", "settings", "undo")


def _pen(color, w=1.5):
    p = QPen(QColor(color))
    p.setWidthF(w)
    p.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return p


def paint_icon(p, name, r, color):
    """在 painter 上按名字画一个图标（几何全部相对 rect，尺寸随 size 缩放）。"""
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(_pen(color))
    p.setBrush(Qt.BrushStyle.NoBrush)
    w, h = r.width(), r.height()
    s = min(w, h)
    cx, cy = r.center().x(), r.center().y()

    def P(fx, fy):
        return QPointF(cx + fx * s, cy + fy * s)

    if name == "dot":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        d = s * 0.22
        p.drawEllipse(QPointF(cx, cy), d, d)
    elif name == "close":
        p.drawLine(P(-0.28, -0.28), P(0.28, 0.28))
        p.drawLine(P(-0.28, 0.28), P(0.28, -0.28))
    elif name == "plus":
        p.drawLine(P(-0.30, 0), P(0.30, 0))
        p.drawLine(P(0, -0.30), P(0, 0.30))
    elif name == "check":
        p.drawPolyline([P(-0.30, 0.02), P(-0.08, 0.24), P(0.32, -0.26)])
    elif name in ("chev_up", "chev_down"):
        k = -1 if name == "chev_up" else 1
        p.drawPolyline([P(-0.24, -0.10 * k), P(0, 0.12 * k), P(0.24, -0.10 * k)])
    elif name in ("arrow_up", "arrow_down"):
        k = -1 if name == "arrow_up" else 1
        p.drawLine(P(0, -0.30 * k), P(0, 0.30 * k))
        p.drawPolyline([P(-0.16, -0.12 * k), P(0, -0.30 * k), P(0.16, -0.12 * k)])
    elif name == "arrow_right":
        p.drawLine(P(-0.30, 0), P(0.30, 0))
        p.drawPolyline([P(0.12, -0.16), P(0.30, 0), P(0.12, 0.16)])
    elif name == "copy":
        p.drawRoundedRect(QRectF(cx - 0.28 * s, cy - 0.28 * s, 0.40 * s, 0.40 * s),
                          2, 2)
        p.drawRoundedRect(QRectF(cx - 0.12 * s, cy - 0.12 * s, 0.40 * s, 0.40 * s),
                          2, 2)
    elif name == "settings":
        # 三条滑杆 —— 比齿轮更简洁，语义也明确
        for i, (y, kx) in enumerate(((-0.20, -0.06), (0.0, 0.10), (0.20, -0.12))):
            p.drawLine(P(-0.30, y), P(0.30, y))
            p.setBrush(QColor(color))
            p.drawEllipse(P(kx, y), s * 0.06, s * 0.06)
            p.setBrush(Qt.BrushStyle.NoBrush)
    elif name == "undo":
        arc = QRectF(cx - 0.30 * s, cy - 0.30 * s, 0.60 * s, 0.60 * s)
        p.drawArc(arc, 150 * 16, -300 * 16)
        tipx, tipy = cx - 0.26 * s, cy - 0.15 * s
        p.drawPolyline([QPointF(tipx + 0.14 * s, tipy - 0.10 * s),
                        QPointF(tipx, tipy),
                        QPointF(tipx + 0.14 * s, tipy + 0.10 * s)])


def pixmap(name, size=14, color=theme.FG3, dpr=2):
    """画一张透明底 pixmap。dpr=2 出图 —— 高分屏上不发虚。"""
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    try:
        paint_icon(p, name, QRectF(0, 0, size, size), color)
    finally:
        p.end()
    return pm


def icon(name, size=14, color=theme.FG3, active_color=theme.GOLD):
    """Normal=常态哑色，Active=指到/按下时点亮（Qt 会把悬停映射到 Active）。"""
    ic = QIcon()
    ic.addPixmap(pixmap(name, size, color), QIcon.Mode.Normal)
    ic.addPixmap(pixmap(name, size, active_color), QIcon.Mode.Active)
    return ic


def icon_label(name, size=12, color=theme.FG3):
    """行内小图标（当字用）：固定尺寸、透明底。"""
    lab = QLabel()
    lab.setPixmap(pixmap(name, size, color))
    lab.setFixedSize(size, size)
    lab.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    return lab


class IconButton(QPushButton):
    """图标按钮：手动切 Normal/Active 两张图，悬停变色就不再依赖样式引擎。"""

    def __init__(self, name, size=16, color=theme.FG3, active_color=theme.GOLD,
                 obj="", tip="", parent=None):
        super().__init__(parent)
        self._normal = QIcon(pixmap(name, size, color))
        self._active = QIcon(pixmap(name, size, active_color))
        if obj:
            self.setObjectName(obj)
        self.setToolTip(tip)
        self.setIcon(self._normal)
        self.setIconSize(QSize(size, size))
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(size + 8, size + 8)

    def enterEvent(self, e):
        self.setIcon(self._active)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.setIcon(self._normal)
        super().leaveEvent(e)
