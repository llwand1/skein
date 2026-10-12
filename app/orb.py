# -*- coding: utf-8 -*-
"""Skein · 悬浮球（PyQt6 · 逐像素 Alpha · 液态玻璃）

"液态玻璃"拆成六件事，全部 QPainter 自绘，不依赖任何平台 API：
1. 半透明机身 —— alpha 低于 255，桌面从玻璃后透出来
2. 折射        —— 内侧一圈更亮的"玻璃壁"，边缘厚、中间薄
3. 色散        —— 受光侧两条极细的暖/冷彩边（红蓝轻微分离）
4. 菲涅尔边缘光 + 镜面高光 —— 左上强、右下弱，像真玻璃的掠射
5. 运动        —— 光源方向缓慢摆动 + 一道高光带慢慢扫过，像液体不像磨砂塑料
6. 交互        —— 悬停时高光跟着鼠标偏；按下时轻微挤压回弹

不抢焦点依旧靠 WS_EX_NOACTIVATE（winutil），与视觉无关。
"""
import math

import config
import winutil
from theme import FONT, GOLD, GOLD2, GREEN, RED, BLUE, FG, FG3

from PyQt6.QtCore import (Qt, QPointF, QRectF, QPropertyAnimation, QTimer,
                          pyqtSignal)
from PyQt6.QtGui import (QPainter, QColor, QPen, QBrush, QRadialGradient,
                         QLinearGradient, QConicalGradient, QFont, QPainterPath)
from PyQt6.QtWidgets import QWidget, QApplication

SIZE = 92        # 控件边长（含阴影留白）
R = 31.0         # 玻璃半径
PAD = 10.0       # 阴影留白
FPS = 25         # 液态动画帧率（小球开销极小）


class Orb(QWidget):
    clicked = pyqtSignal()
    menuRequested = pyqtSignal(object)      # 全局 QPoint

    def __init__(self, app):
        super().__init__(None)
        self.app = app
        self._state = {"has_topic": False, "inbox_n": 0, "pct": 0, "empty": 0, "capped": False}
        self._press = None
        self._moved = False
        self._hover = False
        self._hover_pt = QPointF(0.0, 0.0)   # 归一化 -1..1，供高光跟随
        self._squash = 0.0                   # 按下挤压
        self._swell = 0.0                    # 面板展开时"鼓一下"
        self._phase = 0.0                    # 动画相位（秒）

        self.setFixedSize(SIZE, SIZE)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setToolTip("Skein · 点开这一期")

        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / FPS))
        self._timer.timeout.connect(self._advance)

        pos = config.load_settings().get("orb_pos") or [1216, 600]
        self.move(int(pos[0]), int(pos[1]))

    # ---------------- 生命周期 ----------------
    def showEvent(self, e):
        super().showEvent(e)
        self._clamp_to_screen()
        try:
            winutil.apply_noactivate(self)
        except Exception:
            pass
        winutil.append_probe(winutil.snapshot("orb-shown"))
        if not self._timer.isActive():
            self._timer.start()

    def _clamp_to_screen(self):
        """拖出屏幕 / 换分辨率后还能把球找回来"""
        scr = self.screen() or QApplication.primaryScreen()
        if not scr:
            return
        g = scr.availableGeometry()
        x = min(max(self.x(), g.left()), g.right() - self.width() + 1)
        y = min(max(self.y(), g.top()), g.bottom() - self.height() + 1)
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def hideEvent(self, e):
        super().hideEvent(e)
        self._timer.stop()

    def _advance(self):
        self._phase += 1.0 / FPS
        if self._swell > 0.002:
            self._swell *= 0.90
        else:
            self._swell = 0.0
        self.update()

    def set_state(self, s):
        if s != self._state:
            self._state = s
            self.update()

    def pulse(self):
        a = QPropertyAnimation(self, b"windowOpacity", self)
        a.setDuration(560)
        a.setKeyValueAt(0.0, 1.0)
        a.setKeyValueAt(0.32, 0.42)
        a.setKeyValueAt(1.0, 1.0)
        a.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._anim = a

    def swell(self):
        """面板从球里"长出去"的那一下，球自己也鼓一下再回落。

        不做淡出/隐藏：球是常驻的监督者，而且"点球收起面板"要一直成立。
        """
        self._swell = 1.0
        self.update()

    # ---------------- 绘制 ----------------
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        c = SIZE / 2.0
        t = self._phase
        s = self._state

        self._paint_shadow(p, c)

        # 按下挤压 / 展开鼓包（液体被压扁、被推出去）
        sq = self._squash
        sw = self._swell
        if sq > 0.01 or sw > 0.01:
            p.save()
            p.translate(c, c)
            p.scale(1.0 - 0.035 * sq + 0.07 * sw,
                    1.0 + 0.035 * sq + 0.07 * sw)
            p.translate(-c, -c)

        self._paint_glass(p, c, t)
        self._paint_content(p, c, s)
        if self._hover:
            self._paint_hover(p, c)

        if sq > 0.01 or sw > 0.01:
            p.restore()

    def _paint_shadow(self, p, c):
        sh = QRadialGradient(QPointF(c, c + 3.0), R + PAD)
        sh.setColorAt(0.00, QColor(0, 0, 0, 150))
        sh.setColorAt(0.55, QColor(0, 0, 0, 74))
        sh.setColorAt(0.82, QColor(0, 0, 0, 22))
        sh.setColorAt(1.00, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(sh))
        p.drawEllipse(QPointF(c, c + 3.0), R + PAD, R + PAD)

    def _paint_glass(self, p, c, t):
        """真正的"玻璃"在这里：机身 + 折射壁 + 色散 + 菲涅尔 + 运动高光"""
        # 光源方向缓慢摆动（两个不同周期的正弦叠加，避免规律感）
        sway = math.sin(t * 0.55) * 13.0 + math.sin(t * 0.23) * 6.0
        # 悬停时高光跟随鼠标（让光"活"起来）
        hx = self._hover_pt.x() * 0.30 if self._hover else 0.0
        hy = self._hover_pt.y() * 0.30 if self._hover else 0.0
        lx = -R * 0.30 + hx * R
        ly = -R * 0.44 + hy * R

        # 1) 机身：半透明，桌面透出来 = 透光玻璃
        body = QRadialGradient(QPointF(c + lx * 0.85, c + ly * 0.85), R * 1.95)
        body.setColorAt(0.00, QColor(104, 116, 148, 172))
        body.setColorAt(0.42, QColor(50, 57, 78, 206))
        body.setColorAt(0.78, QColor(24, 27, 38, 230))
        body.setColorAt(1.00, QColor(13, 15, 22, 242))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(body))
        p.drawEllipse(QPointF(c, c), R, R)

        # 2) 中央轻微压暗，保证文字可读
        vig = QRadialGradient(QPointF(c, c), R * 0.96)
        vig.setColorAt(0.0, QColor(0, 0, 0, 48))
        vig.setColorAt(0.72, QColor(0, 0, 0, 20))
        vig.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(vig))
        p.drawEllipse(QPointF(c, c), R, R)

        # 圆内部分（折射壁 / 环境光 / 扫光带）统一裁剪，避免溢出玻璃轮廓
        p.save()
        clip = QPainterPath()
        clip.addEllipse(QPointF(c, c), R - 0.4, R - 0.4)
        p.setClipPath(clip)

        # 3) 折射：内侧一圈更亮的玻璃壁
        wall = QRadialGradient(QPointF(c, c), R)
        wall.setColorAt(0.60, QColor(255, 255, 255, 0))
        wall.setColorAt(0.86, QColor(255, 255, 255, 18))
        wall.setColorAt(0.965, QColor(255, 255, 255, 32))
        wall.setColorAt(1.00, QColor(255, 255, 255, 6))
        p.setBrush(QBrush(wall))
        p.drawEllipse(QPointF(c, c), R, R)

        # 4) 顶部环境光 / 底部内阴影
        amb = QLinearGradient(0.0, c - R, 0.0, c + R)
        amb.setColorAt(0.00, QColor(255, 255, 255, 28))
        amb.setColorAt(0.45, QColor(255, 255, 255, 4))
        amb.setColorAt(1.00, QColor(0, 0, 0, 42))
        p.setBrush(QBrush(amb))
        p.drawEllipse(QPointF(c, c), R, R)

        # 5) 缓慢扫过的高光带 —— "液体在流"
        band_x = ((t * 0.13) % 1.54) - 0.27
        bx = c - R + band_x * 2 * R
        band = QLinearGradient(bx - R * 0.45, c - R, bx + R * 0.45, c + R)
        band.setColorAt(0.0, QColor(255, 255, 255, 0))
        band.setColorAt(0.5, QColor(255, 255, 255, 20))
        band.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(band))
        p.drawEllipse(QPointF(c, c), R, R)
        p.restore()

        # 6) 色散：受光侧两条极细的暖/冷彩边（都压在玻璃轮廓内，才不会像飘在外面的杂线）
        p.save()
        p.translate(c, c)
        p.rotate(sway)
        p.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(QColor(255, 122, 122, 58), 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(QRectF(-R + 0.7, -R + 0.7, (R - 0.7) * 2, (R - 0.7) * 2), 26 * 16, 122 * 16)
        pen = QPen(QColor(122, 168, 255, 58), 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(QRectF(-R + 1.4, -R + 1.4, (R - 1.4) * 2, (R - 1.4) * 2), 46 * 16, 110 * 16)
        p.restore()

        # 7) 菲涅尔边缘光 + 镜面高光（整组随光源转动）
        p.save()
        p.translate(c, c)
        p.rotate(sway)

        rim = QLinearGradient(-R, -R, R * 0.5, R)
        rim.setColorAt(0.00, QColor(255, 255, 255, 150))
        rim.setColorAt(0.30, QColor(255, 255, 255, 44))
        rim.setColorAt(0.58, QColor(255, 255, 255, 8))
        rim.setColorAt(1.00, QColor(255, 255, 255, 72))
        pen = QPen(QBrush(rim), 1.6)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(0, 0), R - 0.8, R - 0.8)

        spec = QRadialGradient(QPointF(-R * 0.30, -R * 0.44), R * 0.95)
        spec.setColorAt(0.0, QColor(255, 255, 255, 94))
        spec.setColorAt(0.55, QColor(255, 255, 255, 24))
        spec.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(spec))
        p.drawEllipse(QPointF(-R * 0.28, -R * 0.44), R * 0.70, R * 0.50)

        dot = QRadialGradient(QPointF(-R * 0.42, -R * 0.54), R * 0.26)
        dot.setColorAt(0.0, QColor(255, 255, 255, 170))
        dot.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(dot))
        p.drawEllipse(QPointF(-R * 0.42, -R * 0.54), R * 0.22, R * 0.16)
        p.restore()

    def _paint_content(self, p, c, s):
        if s.get("has_topic"):
            self._paint_topic(p, c, s)
        else:
            self._paint_empty(p, c, s)

    def _paint_topic(self, p, c, s):
        # 进度环内缩，整体落在玻璃轮廓以内（否则 4px 描边会探出球外，像一圈杂光环）
        rr = R - 3.2
        rect = QRectF(c - rr, c - rr, rr * 2, rr * 2)
        pen = QPen(QColor("#414a63"), 3.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(rect, 90 * 16, -360 * 16)

        if s.get("capped"):
            pen = QPen(QColor(GREEN), 3.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(rect, 90 * 16, -360 * 16)
            self._text(p, c, "OK", QColor(GREEN), 17, QFont.Weight.Bold)
        else:
            pct = float(s.get("pct") or 0)
            if pct > 0.5:
                g = QConicalGradient(QPointF(c, c), 90)
                g.setColorAt(0.00, QColor(GOLD))
                g.setColorAt(0.55, QColor(GOLD2))
                g.setColorAt(0.99, QColor(GOLD))
                pen = QPen(QBrush(g), 3.4)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                p.drawArc(rect, 90 * 16, int(-359.9 * 16 * min(pct, 100) / 100.0))
            self._text(p, c, "≈%d%%" % int(pct), QColor(FG), 14, QFont.Weight.DemiBold)
            if (s.get("empty") or 0) > 0:
                self._badge(p, c, s["empty"], QColor(RED))

    def _paint_empty(self, p, c, s):
        rr = R - 2.4
        pen = QPen(QColor("#8f96a8"), 1.5)
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setDashPattern([2.4, 2.6])
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(c, c), rr, rr)
        self._text(p, c - 1, "+", QColor("#c6cbd8"), 26, QFont.Weight.Light)
        if (s.get("inbox_n") or 0) > 0:
            self._badge(p, c, s["inbox_n"], QColor(BLUE))

    def _paint_hover(self, p, c):
        gl = QRadialGradient(QPointF(c, c), R + 6)
        gl.setColorAt(0.78, QColor(255, 208, 31, 0))
        gl.setColorAt(1.00, QColor(255, 208, 31, 52))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(gl))
        p.drawEllipse(QPointF(c, c), R + 6, R + 6)

    def _text(self, p, c, text, color, px, weight):
        f = QFont(FONT)
        f.setPixelSize(px)
        f.setWeight(weight)
        p.setFont(f)
        p.setPen(QPen(color))
        p.drawText(QRectF(0, 0, SIZE, SIZE), Qt.AlignmentFlag.AlignCenter, text)

    def _badge(self, p, c, n, color):
        w, h = 26.0, 18.0
        x = c + R - 4.0
        y = c - R + 3.0
        path = QPainterPath()
        path.addRoundedRect(QRectF(x - w / 2, y - h / 2, w, h), h / 2, h / 2)
        p.setPen(QPen(QColor("#0f1115"), 2.0))
        p.setBrush(QBrush(color))
        p.drawPath(path)
        f = QFont(FONT)
        f.setPixelSize(12)
        f.setWeight(QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(QPen(QColor("#ffffff")))
        p.drawText(QRectF(x - w / 2, y - h / 2, w, h), Qt.AlignmentFlag.AlignCenter, str(n))

    # ---------------- 交互 ----------------
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = (e.globalPosition().toPoint(), self.pos())
            self._moved = False
            self._squash = 1.0
            self.update()

    def mouseMoveEvent(self, e):
        lp = e.position()
        self._hover_pt = QPointF((lp.x() - SIZE / 2) / R, (lp.y() - SIZE / 2) / R)
        if not self._press:
            if self._hover:
                self.update()
            return
        gp, sp = self._press
        delta = e.globalPosition().toPoint() - gp
        # 超过阈值之前**一个像素都不动**：否则手抖一下球就跑，一次点击也可能被
        # 判成拖动（实测出现过点一下球自己挪了 4px）。
        if not self._moved:
            if delta.manhattanLength() <= 6:
                return
            self._moved = True
        self.move(sp + delta)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or not self._press:
            return
        self._press = None
        self._squash = 0.0
        if self._moved:
            self._clamp_to_screen()
            st = config.load_settings()
            st["orb_pos"] = [self.x(), self.y()]
            config.save_settings(st)
            try:
                winutil.apply_noactivate(self)
            except Exception:
                pass
        else:
            winutil.append_probe(winutil.snapshot("orb-clicked"))
            self.clicked.emit()
        self.update()

    def contextMenuEvent(self, e):
        self.menuRequested.emit(e.globalPos())

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self._hover_pt = QPointF(0.0, 0.0)
        self.update()
