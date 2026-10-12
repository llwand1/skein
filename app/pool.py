# -*- coding: utf-8 -*-
"""Skein · 灵感池（液态玻璃）

为什么单独一个文件：面板（panel.py）已经很长了，而这里是一块**自己会动**的
绘制区 —— 它和球的材质是同一套（半透明机身 / 折射壁 / 扫光带 / 色散 / 菲涅尔），
放在一起会更看不清哪段属于谁。

设计上的三条自我约束：
1. **颜色不区分类型**。类型靠形状读：文字=细横条、图片=方块、视频=宽条、
   音频=窄条、其他=小方块。四类上四种颜色就是 AI slop —— 面板只留一个强调色金，
   这里那块金只用来画**水位线**。
2. **不预设容量**。池子没有"满了"这一说（素材数量本来就不预设），所以不做
   "进度条式的水位"。数量一多，块自动变小变密 —— 读的是**密度**，不是百分比。
3. **常驻动效只重绘这一块**。球是独立小窗口，25fps 无所谓；但面板是 520×736 的
   大窗口，所以这里只 update() 自己（Qt 只重绘该控件矩形），并且照样尊重
   系统的"显示动画"开关（关了就静止）。
"""
import math
import time

import theme as T
import winutil

from PyQt6.QtCore import Qt, QRectF, QPointF, QTimer
from PyQt6.QtGui import (QPainter, QColor, QPen, QBrush, QLinearGradient,
                         QRadialGradient, QPainterPath)
from PyQt6.QtWidgets import QWidget

FPS = 20
ANIMATE = True     # uitest 会置 False：走查要的是确定性的静止状态
H = 132            # 池子固定高度：高度不表达"多少"，只给块一个可以堆的地方
PADX = 14          # 池口左右留白：也把块圈在池壁里
PAD_BOT = 12
PAD_TOP = 12
R_POOL = 12        # 池身圆角

# 每种素材在池子里的形状（宽, 高）；乘上密度系数后使用
SHAPE = {
    "text": (30.0, 5.0),
    "note": (24.0, 4.5),
    "image": (13.0, 11.0),
    "video": (34.0, 8.0),
    "audio": (18.0, 4.0),
    "file": (11.0, 11.0),
}
BLOCK_RGB = (214, 203, 182)   # 暖灰，和面板的米白同族；只靠明度/透明度分层


def _density(n):
    """块随数量缩小：10 条以内保持原尺寸，之后越堆越密。

    为什么不做"百分比水位"：素材数量是不预设的，给个"满格"等于凭空规定容量。
    """
    return max(0.45, min(1.0, math.sqrt(10.0 / max(n, 1))))


class Pool(QWidget):
    def __init__(self, on_click=None):
        super().__init__()
        self._on_click = on_click
        self._items = []
        self._prev_n = 0
        self._phase = 0.0
        self._sway = 0.0
        self._drops = []           # [(kind, t0)]：刚落进来的块，从池口往下落
        self.setFixedHeight(H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("灵感池：看到什么先扔这儿，不挂靠任何一期。点一下看里面有什么")
        self._animate = bool(ANIMATE and winutil.animations_enabled())
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / FPS))
        self._timer.timeout.connect(self._advance)
        if self._animate:
            self._timer.start()

    # ---------------- 外部接口 ----------------
    def set_items(self, items):
        """喂进来新东西时，让那一块从池口落下 —— 这就是"在填充"的感觉。"""
        items = list(items or [])
        n = len(items)
        if n > self._prev_n and self._animate:
            for it in items[:min(n - self._prev_n, 6)]:
                self._drops.append([it.get("kind") or "note", time.monotonic()])
            if not self._timer.isActive():
                self._timer.start()
        self._prev_n = n
        self._items = items
        self.update()

    def hideEvent(self, e):
        self._timer.stop()          # 藏起来就别烧 CPU
        super().hideEvent(e)

    def showEvent(self, e):
        if self._animate:
            self._timer.start()
        super().showEvent(e)

    def _advance(self):
        self._phase = (self._phase + 1.0 / FPS) % 1000.0
        # 水位线缓慢摆动：两个不同周期叠加，避免规律感（和球的高光同一手法）
        self._sway = math.sin(self._phase * 0.62) * 1.5 + math.sin(self._phase * 0.27) * 0.8
        if self._drops:
            self._drops = [d for d in self._drops if time.monotonic() - d[1] < 0.34]
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._on_click is not None:
            self._on_click()

    # ---------------- 绘制 ----------------
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        w, h = float(self.width()), float(self.height())
        body = QRectF(1.5, 1.5, w - 3.0, h - 3.0)
        path = QPainterPath()
        path.addRoundedRect(body, R_POOL, R_POOL)

        self._paint_glass(p, path, body)
        p.save()
        p.setClipPath(path)
        level = self._measure_level(body)
        self._paint_liquid(p, body, level)
        self._paint_blocks(p, body)
        self._paint_waterline(p, body, level)
        self._paint_sheen(p, body)
        p.restore()
        self._paint_edges(p, path, body)
        if not self._items:
            self._paint_empty(p, body)

    def _measure_level(self, body):
        """水位该画在哪：素材堆到哪，液面就在哪（空池时贴底，留一层薄液）。"""
        n = len(self._items)
        if not n:
            return body.bottom() - PAD_BOT
        s = _density(n)
        max_h = max(SHAPE.get(k, SHAPE["file"])[1] for k in SHAPE) * s
        row_gap = 3.0 * s
        rows = 1
        line_x = body.left() + PADX
        right = body.right() - PADX
        for it in self._items:
            bw = SHAPE.get(it.get("kind") or "note", SHAPE["file"])[0] * s
            if line_x + bw > right and line_x > body.left() + PADX:
                line_x = body.left() + PADX
                rows += 1
            line_x += bw + 2.4 * s
        # 画得下的部分（跟 _paint_blocks 用同一套限制）
        room = body.height() - PAD_BOT - PAD_TOP
        fit_rows = max(1, int(room / (max_h + row_gap)))
        rows = min(rows, fit_rows)
        return body.bottom() - PAD_BOT - rows * (max_h + row_gap)

    def _liquid_top(self, body, level):
        """液面高度。液体和水位线共用这一个值 —— 差 1px 就会看出缝。"""
        return max(body.top() + PAD_TOP - 2.0, level - 7.0)

    def _paint_liquid(self, p, body, level):
        """液面以下的液体。

        没有这一层，池子只是一块圆角玻璃板 —— 素材也看不出"泡在里面"。
        颜色仍取玻璃自己那一族的冷灰蓝，不另开一个色相。
        """
        top = self._liquid_top(body, level)
        bot = body.bottom() - PAD_BOT + 5
        if bot - top < 3.0:
            return
        g = QLinearGradient(0.0, top, 0.0, bot)
        g.setColorAt(0.00, QColor(168, 204, 214, 56))
        g.setColorAt(0.30, QColor(130, 172, 196, 40))
        g.setColorAt(1.00, QColor(96, 138, 168, 32))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawRect(QRectF(body.left() + 1.5, top, body.width() - 3.0, bot - top))

    def _paint_glass(self, p, path, body):
        """机身：半透明，桌面透出来 —— 与球同一族色，但更浅（池子是"装东西的"，不是实心滴）"""
        g = QLinearGradient(body.topLeft(), body.bottomRight())
        g.setColorAt(0.00, QColor(88, 99, 126, 148))
        g.setColorAt(0.52, QColor(38, 44, 58, 196))
        g.setColorAt(1.00, QColor(14, 16, 22, 232))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawPath(path)

        # 中央压暗：保证上面的文字（空态提示）读得清
        vig = QRadialGradient(QPointF(body.center().x(), body.center().y()),
                              max(body.width(), body.height()) * 0.62)
        vig.setColorAt(0.0, QColor(0, 0, 0, 42))
        vig.setColorAt(0.72, QColor(0, 0, 0, 16))
        vig.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(vig))
        p.drawPath(path)

    def _paint_sheen(self, p, body):
        """池内折射壁 + 顶部环境光 + 缓慢扫过的高光带（"液体在流"）"""
        # 内侧一圈更亮的玻璃壁
        wall = QLinearGradient(body.topLeft(), body.bottomLeft())
        wall.setColorAt(0.0, QColor(255, 255, 255, 30))
        wall.setColorAt(0.5, QColor(255, 255, 255, 0))
        wall.setColorAt(1.0, QColor(255, 255, 255, 22))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(wall))
        p.drawRect(body)

        # 扫光带
        band_x = ((self._phase * 0.075) % 1.6) - 0.3
        bx = body.left() + band_x * body.width()
        band = QLinearGradient(bx - body.height() * 0.5, body.top(),
                               bx + body.height() * 0.5, body.bottom())
        band.setColorAt(0.0, QColor(255, 255, 255, 0))
        band.setColorAt(0.5, QColor(255, 255, 255, 17))
        band.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(band))
        p.drawRect(body)

    def _paint_blocks(self, p, body):
        """素材块从池底往上堆（会画在液体里，所以是"泡着"的样子）。"""
        p.setPen(Qt.PenStyle.NoPen)
        n = len(self._items)
        if not n:
            return
        s = _density(n)
        left = body.left() + PADX
        right = body.right() - PADX
        max_h = max(SHAPE.get(k, SHAPE["file"])[1] for k in SHAPE) * s
        row_gap = 3.0 * s
        line_x = left
        line_y = body.bottom() - PAD_BOT
        placed = 0

        for it in self._items:
            bw, bh = SHAPE.get(it.get("kind") or "note", SHAPE["file"])
            bw, bh = bw * s, bh * s
            if line_x + bw > right and line_x > left:
                line_x = left
                line_y -= (max_h + row_gap)
            if line_y - bh < body.top() + PAD_TOP:
                break                      # 画不下了就停：读的是密度，不是总量
            k = it.get("kind") or "note"
            # 刚落进来的那几块，从池口"掉"到自己的位置
            dy = 0.0
            for dk, t0 in self._drops:
                if dk == k:
                    el = min(1.0, (time.monotonic() - t0) / 0.34)
                    dy = -(1.0 - el * el) * (line_y - body.top() - PAD_TOP + bh)
                    break
            a = 128 + int(34 * ((placed % 3) / 2.0))
            p.setBrush(QColor(BLOCK_RGB[0], BLOCK_RGB[1], BLOCK_RGB[2], a))
            p.drawRoundedRect(QRectF(line_x, line_y - bh + dy, bw, bh),
                              min(2.0, bh / 2.4), min(2.0, bh / 2.4))
            line_x += bw + 2.4 * s
            placed += 1

    def _paint_waterline(self, p, body, level):
        """水位线：这是全面板唯一用金的地方，表示"这一池攒到哪了"。

        刻意不做"满格百分比"——素材数量不预设，给刻度就等于凭空规定容量。
        """
        if not self._items:
            p.setPen(QPen(QColor(T.HAIR2), 1.0))
            p.drawLine(QPointF(body.left() + PADX, body.bottom() - PAD_BOT),
                       QPointF(body.right() - PADX, body.bottom() - PAD_BOT))
            return
        y = self._liquid_top(body, level) + self._sway
        g = QLinearGradient(body.left() + PADX, 0.0, body.right() - PADX, 0.0)
        g.setColorAt(0.00, QColor(255, 208, 31, 40))
        g.setColorAt(0.30, QColor(255, 208, 31, 200))
        g.setColorAt(0.70, QColor(255, 208, 31, 200))
        g.setColorAt(1.00, QColor(255, 208, 31, 40))
        p.setPen(QPen(QBrush(g), 1.6))
        p.drawLine(QPointF(body.left() + PADX, y), QPointF(body.right() - PADX, y))

    def _paint_edges(self, p, path, body):
        """色散细边 + 菲涅尔边缘光（和球同一套，只是拉直成了矩形）"""
        p.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(QColor(255, 122, 122, 46), 1.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(body.left() + 3, body.top() + 18),
                   QPointF(body.left() + 3, body.bottom() - 18))
        pen = QPen(QColor(122, 168, 255, 46), 1.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(body.right() - 3, body.top() + 18),
                   QPointF(body.right() - 3, body.bottom() - 18))

        rim = QLinearGradient(body.topLeft(), body.bottomLeft())
        rim.setColorAt(0.00, QColor(255, 255, 255, 116))
        rim.setColorAt(0.40, QColor(255, 255, 255, 26))
        rim.setColorAt(1.00, QColor(255, 255, 255, 54))
        pen = QPen(QBrush(rim), 1.1)
        p.setPen(pen)
        p.drawPath(path)

    def _paint_empty(self, p, body):
        p.setPen(QColor(T.FG3))
        f = p.font()
        f.setFamily(T.FONT)
        f.setPixelSize(12)
        p.setFont(f)
        p.drawText(body.adjusted(PADX, 0, -PADX, -PAD_BOT - 6),
                   Qt.AlignmentFlag.AlignCenter,
                   "还是空的 —— 随手记点什么，或者把文件拖到面板上")
