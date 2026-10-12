# -*- coding: utf-8 -*-
"""Skein · 池子（叙事结构的水池）

为什么单独一个文件：面板（panel.py）已经很长了，而这里是一块**自己会动**的
绘制区，而且它同时管三件事 —— 画水、画球、接拖动。塞进 panel 里会更看不清
哪段属于谁。

三条语义（改这块之前先看，对应 requirements §2.18）：

1. **位置不表达顺序，顺序只写在编号里**。拖球只挪位置（往下拖＝沉得更深＝更暗），
   球漂到哪都不影响先后 —— 先后由球心那颗金徽上的数字决定。
2. **球放定就不动，只有水在动**。这里**没有**速度、没有浮力呼吸、没有随波起伏、
   素材球也不公转：位置只有一个来源，就是用户拖。水才是那个一直在动的东西。
3. **未编排的球混在同一口池里**，只是暗一档、下挂「未编排」，不占编号。

动效只重绘自己（Qt 只重绘该控件矩形），并尊重系统的「显示动画」开关；
`hideEvent` 里停表 —— 面板是 520×736 的大窗口，别让一口池子一直烧 CPU。
"""
import math
import time

import theme as T
import winutil

from PyQt6.QtCore import Qt, QRectF, QPointF, QTimer, pyqtSignal
from PyQt6.QtGui import (QPainter, QColor, QPen, QBrush, QLinearGradient,
                         QRadialGradient, QPainterPath, QFont, QFontMetrics)
from PyQt6.QtWidgets import QWidget

FPS = 20
ANIMATE = True          # uitest 会置 False：走查要的是确定性的静止状态
H = 300                 # 池子定高（面板里的那一口）

PADX = 10               # 池口左右留白
TOP_FRAC = 0.155        # 液面基线（占高度比例）
TOP_GAP = 24            # 液面基线到"零深度"球心的距离
BOT_GAP = 42            # 池底留白
R_POOL = 14             # 池身圆角

R_BASE = 20.0           # 段球半径（素材少时）—— 别做小了，编号要看得清
R_PER_MAT = 2.2         # 每颗素材给球加一点体积
R_MIN, R_MAX = 17.0, 32.0
MAX_MAT_DOTS = 8        # 素材球最多画这么多，再多收成「+n」
RIPPLE_LIFE = 1.5       # s

# 波面：三组不同波长的正弦叠加（不是一条上下平移的直线）
WAVE = ((7.0, 0.0076, 0.40), (3.2, 0.0164, -0.68), (1.6, 0.0330, 1.10))

# 天蓝玻璃球的层（位置, r, g, b）：从镜面高光到最深的一层
SKY = ((0.00, 234, 247, 255), (0.11, 169, 222, 250), (0.40, 95, 182, 232),
       (0.71, 47, 127, 184), (1.00, 23, 82, 127))
# 未编排的球：同一族色、饱和度压掉
DULL = ((0.00, 178, 196, 208), (0.42, 104, 132, 150), (1.00, 44, 68, 85))

INK = (13, 27, 38)      # 球上数字用的深墨（比金色在高饱和浅蓝上读得清）


def _wave(x, t):
    y = 0.0
    for a, k, s in WAVE:
        y += a * math.sin(k * x + t * s)
    return y


def _grad(stops):
    """把 (位置, r,g,b) 或者 (位置, r,g,b,a) 的层转成颜色列表"""
    out = []
    for s in stops:
        out.append((s[0], QColor(s[1], s[2], s[3], s[4] if len(s) > 4 else 255)))
    return out


class Basin(QWidget):
    """一口水池。球是段落，水是环境 —— 动的只有水。"""

    picked = pyqtSignal(int)          # 选中一颗球（-1 = 点了空白，清空选中）
    moved = pyqtSignal(int, int, int)  # 球被拖到新落点（id, px, py；千分比）
    added = pyqtSignal()              # 双击空白 = 投一颗新球

    def __init__(self, parent=None):
        super().__init__(parent)
        self._balls = []
        self._sel = -1
        self._t = 0.0
        self._ripples = []
        self._press = None            # (x, y, bid) 按下时的现场
        self._drag = None             # 正在拖的球 id
        self._moved_enough = False
        self._hit_hint = None
        self._prev_n = 0
        self._scale = 1.0
        self.setFixedHeight(H)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("这里的每一颗球 = 这一期的一个段落；金色徽上是它的顺序。"
                        "拖动球只挪位置，顺序只看编号。")
        self._animate = bool(ANIMATE and winutil.animations_enabled())
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / FPS))
        self._timer.timeout.connect(self._advance)
        if self._animate:
            self._timer.start()

    # ---------------- 外部接口 ----------------
    def set_balls(self, balls):
        """喂进来这一期的球。新来的球按自适应布局落座 —— 落定后就不再自己动。"""
        self._balls = list(balls or [])
        for i, b in enumerate(self._balls):
            if "auto" not in b:
                b["auto"] = int(b.get("px", -1)) < 0 or int(b.get("py", -1)) < 0
            b["_i"] = i               # 画素材球时用它错开起始角，不然每颗球都朝同一个方向
        n = len(self._balls)
        if n > self._prev_n:
            for b in self._balls[n - self._prev_n:]:
                self._ripples.append((self._cx(b), self._cy(b), time.monotonic()))
        self._prev_n = n
        if self._sel not in [b["id"] for b in self._balls]:
            self._sel = -1
        self._relayout()
        self.update()

    def set_sel(self, bid):
        if bid != self._sel:
            self._sel = bid
            self.update()

    def resizeEvent(self, e):
        self._relayout()      # 宽度一确定就把自动位的球摆开（拖过的不动）
        super().resizeEvent(e)

    def hideEvent(self, e):
        self._timer.stop()            # 藏起来就别烧 CPU
        super().hideEvent(e)

    def showEvent(self, e):
        if self._animate:
            self._timer.start()
        super().showEvent(e)

    def _advance(self):
        self._t = (self._t + 1.0 / FPS) % 1000.0
        now = time.monotonic()
        if self._ripples:
            self._ripples = [r for r in self._ripples if now - r[2] < RIPPLE_LIFE]
        self.update()

    # ---------------- 几何 ----------------
    def _top_y(self):
        return self.height() * TOP_FRAC + TOP_GAP

    def _span_y(self):
        return max(30.0, self.height() - self._top_y() - BOT_GAP)

    def _surf(self, x):
        return self.height() * TOP_FRAC + _wave(x, self._t)

    def _raw_r(self, b):
        r = R_BASE + min(len(b.get("mats") or []), 6) * R_PER_MAT
        return max(R_MIN, min(R_MAX, r))

    def _relayout(self):
        """给"还没被人挪过"的球排一下：一行放不下就换行，行与行错开深度。

        排的是**落点**不是**顺序** —— 顺序永远只写在编号里（§2.18 语义 1）。
        """
        n = len(self._balls)
        if n == 0 or self.width() <= 0:
            return
        rm = max(self._raw_r(b) for b in self._balls)
        cols = max(1, int((self.width() - 2 * PADX) / (2 * rm + 12)))
        rows = (n + cols - 1) // cols
        for i, b in enumerate(self._balls):
            if not b.get("auto"):
                continue
            row, col = divmod(i, cols)
            b["px"] = int((col + 0.5) / cols * 1000)
            if rows > 1:
                b["py"] = int(60 + row * (880.0 / (rows - 1)))
            else:
                # 只有一行时也让深浅错开一点：一是像水里的球，二是挤得没那么紧
                b["py"] = int(360 + (i % 3) * 140)

    def _fit_scale(self):
        """球多了就整体缩一点：宁可变小，也不要叠在一起。"""
        n = len(self._balls)
        if n <= 1:
            return 1.0
        rm = max(self._raw_r(b) for b in self._balls)
        need = n * (2 * rm + 12)
        avail = max(80.0, self.width() - 2 * PADX)
        return max(0.52, min(1.0, avail / need))

    def _cx(self, b):
        return PADX + int(b.get("px", 500)) / 1000.0 * max(10.0, self.width() - 2 * PADX)

    def _cy(self, b):
        return self._top_y() + int(b.get("py", 500)) / 1000.0 * self._span_y()

    def r_of(self, b):
        return self._raw_r(b) * self._fit_scale()

    def _hit(self, x, y):
        """命中最上面那颗球（后画的在上）。返回球或 None。"""
        for b in reversed(self._balls):
            r = self.r_of(b)
            if (x - self._cx(b)) ** 2 + (y - self._cy(b)) ** 2 <= r * r:
                return b
        return None

    # ---------------- 鼠标：点、拖、双击 ----------------
    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        p = e.position().toPoint()
        b = self._hit(p.x(), p.y())
        self._moved_enough = False
        self._drag = None
        self._press = (p.x(), p.y(), b["id"] if b else -1)
        if b is None:
            # 点空白：先荡一圈涟漪，松手时才算"清空选中"（松手前不算，避免拖拽被误清）
            self._ripples.append((p.x(), p.y(), time.monotonic()))
            self.update()

    def mouseMoveEvent(self, e):
        p = e.position().toPoint()
        if self._press is not None:
            x0, y0, bid = self._press
            if bid >= 0:
                if not self._moved_enough and \
                        abs(p.x() - x0) + abs(p.y() - y0) > 6:
                    self._moved_enough = True
                    self._drag = bid
                if self._drag is not None:
                    self._put_to(bid, p.x(), p.y())
            return
        # 没按下时：鼠标划到哪颗球上，就把它的信息挂在 tooltip 上
        b = self._hit(p.x(), p.y())
        key = b["id"] if b else None
        if key != self._hit_hint:
            self._hit_hint = key
            self.setToolTip(self._tip_for(b))

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self._press is None:
            return
        x0, y0, bid = self._press
        self._press = None
        p = e.position().toPoint()
        if self._drag is not None:
            self._drag = None
            b = self._ball(bid)
            if b is not None:
                self._ripples.append((self._cx(b), self._cy(b), time.monotonic()))
                self.moved.emit(bid, int(b["px"]), int(b["py"]))
        elif bid >= 0:
            self.picked.emit(bid)
        else:
            self.picked.emit(-1)       # 点空白 = 取消选中

    def mouseDoubleClickEvent(self, e):
        p = e.position().toPoint()
        if self._hit(p.x(), p.y()) is None:
            self.added.emit()

    def _put_to(self, bid, x, y):
        """把球挪到光标处：横向是位置，纵向是深度（越深越暗）。

        挪过之后这颗球就不再跟着自适应布局走了（auto=False）—— 位置进库，
        重开这一期它还在原地（§2.18 语义 2）。
        """
        b = self._ball(bid)
        if b is None:
            return
        w = max(10.0, self.width() - 2 * PADX)
        px = (x - PADX) / w * 1000.0
        b["px"] = int(max(0, min(1000, px)))
        py = (y - self._top_y()) / self._span_y() * 1000.0
        b["py"] = int(max(0, min(1000, py)))
        b["auto"] = False
        self.update()

    def _ball(self, bid):
        for b in self._balls:
            if b["id"] == bid:
                return b
        return None

    def _tip_for(self, b):
        if b is None:
            return ("空白水面 —— 双击这儿投一颗新球。"
                    "球放定就不动，只有水在动。")
        n = len(b.get("mats") or [])
        head = ("第 %d 段" % b["n"]) if b.get("n") else "还没编排"
        tips = " · ".join(x for x in (
            head, "%d 个素材" % n, (b.get("label") or "").strip()) if x)
        note = (b.get("note") or "").strip()
        return tips + (("\n短评：" + note) if note else "")

    # ---------------- 绘制 ----------------
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        w, h = float(self.width()), float(self.height())
        body = QRectF(1.5, 1.5, w - 3.0, h - 3.0)
        path = QPainterPath()
        path.addRoundedRect(body, R_POOL, R_POOL)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor("#070f18")))
        p.drawPath(path)

        p.save()
        p.setClipPath(path)
        self._paint_water_back(p, body)
        self._paint_balls(p)
        self._paint_water_front(p, body)
        p.restore()
        self._paint_edges(p, path, body)
        if not self._balls:
            self._paint_empty(p, body)

    def _water_path(self, body):
        """液面以下的那块区域（沿波面走）"""
        path = QPainterPath()
        x0 = body.left() - 4
        path.moveTo(x0, self._surf(x0))
        x = x0
        while x <= body.right() + 4:
            path.lineTo(x, self._surf(x))
            x += 6.0
        path.lineTo(body.right() + 4, body.bottom() + 4)
        path.lineTo(x0, body.bottom() + 4)
        path.closeSubpath()
        return path

    def _paint_water_back(self, p, body):
        """深水：越深越暗越蓝 + 深度层线 + 水下光斑 + 涟漪"""
        p.save()
        p.setClipPath(self._water_path(body))
        h = float(self.height())
        g = QLinearGradient(0.0, h * 0.15, 0.0, h)
        g.setColorAt(0.00, QColor(20, 64, 95, 235))
        g.setColorAt(0.45, QColor(13, 36, 56, 245))
        g.setColorAt(1.00, QColor(5, 12, 20, 255))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawRect(body)

        # 深度层线：只给眼睛一个"深浅"的刻度
        p.setPen(QPen(QColor(180, 225, 250, 14), 1.0))
        y = h * 0.30
        while y < h - 8:
            p.drawLine(QPointF(body.left(), y), QPointF(body.right(), y))
            y += 62

        # 水下光斑：几条缓慢横移的亮带（相位用 _t，所以只有它在动）
        w = float(self.width())
        for j in range(4):
            cy = h * 0.26 + j * (h * 0.70 / 4) + math.sin(self._t * 0.4 + j) * 5
            pts = []
            x = body.left()
            while x <= body.right():
                yy = cy + math.sin(x * 0.014 + self._t * (0.5 + j * 0.11)) * (5 + j) \
                        + math.sin(x * 0.031 - self._t * 0.8) * 2.4
                pts.append(QPointF(x, yy))
                x += 9
            p.setPen(QPen(QColor(196, 236, 255, 17), 1.3))
            for k in range(1, len(pts)):
                p.drawLine(pts[k - 1], pts[k])

        # 涟漪：同心圆，往外扩、淡出
        now = time.monotonic()
        for (rx, ry, t0) in self._ripples:
            age = now - t0
            if age > RIPPLE_LIFE:
                continue
            r = 5.0 + age * 62.0
            a = int(max(0, 1 - age / RIPPLE_LIFE) * 96)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(190, 232, 255, a), 1.4))
            p.drawEllipse(QPointF(rx, ry), r, r)
            p.setPen(QPen(QColor(190, 232, 255, int(a * 0.5)), 1.0))
            p.drawEllipse(QPointF(rx, ry), r * 0.62, r * 0.62)
        p.restore()

    def _paint_balls(self, p):
        for b in self._balls:
            r = self.r_of(b)
            cx, cy = self._cx(b), self._cy(b)
            deep = int(b.get("py", 500)) / 1000.0
            placed = b.get("n") is not None
            alpha = 1.0 - deep * 0.34

            # 投影：越深越糊、越淡
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(0, 12, 24, int(70 * alpha))))
            p.drawEllipse(QPointF(cx, cy + r * 0.86), r * 0.92, r * 0.34)

            g = QRadialGradient(QPointF(cx - r * 0.34, cy - r * 0.40), r * 1.55)
            for stop, c in _grad(SKY if placed else DULL):
                c = QColor(c.red(), c.green(), c.blue(), int(c.alpha() * alpha))
                g.setColorAt(stop, c)
            p.setBrush(QBrush(g))
            p.drawEllipse(QPointF(cx, cy), r, r)

            # 底部内反射 + 顶部镜面高光（这两笔是"玻璃"的证据）
            p.setBrush(QBrush(QColor(186, 232, 255, int(84 * alpha))))
            p.drawEllipse(QPointF(cx + r * 0.06, cy + r * 0.46), r * 0.44, r * 0.20)
            p.setBrush(QBrush(QColor(255, 255, 255, int(210 * alpha))))
            p.drawEllipse(QPointF(cx - r * 0.34, cy - r * 0.42), r * 0.30, r * 0.19)

            self._paint_mats(p, b, cx, cy, r, alpha)

            if placed:
                # 金徽 + 深墨数字：编号是这一池里唯一"有权威"的记号
                br = r * 0.42
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(QColor(255, 208, 31, int(240 * alpha))))
                p.drawEllipse(QPointF(cx, cy), br, br)
                f = QFont(T.MONO)
                f.setPixelSize(max(10, int(br * 1.5)))
                f.setBold(True)
                p.setFont(f)
                p.setPen(QPen(QColor(int(INK[0]), int(INK[1]), int(INK[2]),
                                     int(255 * alpha))))
                p.drawText(QRectF(cx - br, cy - br, br * 2, br * 2),
                           Qt.AlignmentFlag.AlignCenter, str(b["n"]))
            else:
                f = QFont(T.FONT)
                f.setPixelSize(9)
                p.setFont(f)
                p.setPen(QPen(QColor(214, 232, 244, int(120 * alpha))))
                p.drawText(QRectF(cx - r, cy + r * 0.94, r * 2, 12),
                           Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                           "未编排")

            # 段落的措辞：只在**选中那颗**球底下写一行 —— 每颗都写会糊成一片，
            # 认哪一段靠"点一下"（悬停也有 tooltip 带完整措辞）
            label = (b.get("label") or "").strip()
            if label and b["id"] == self._sel:
                f = QFont(T.FONT)
                f.setPixelSize(9)
                p.setFont(f)
                fm = QFontMetrics(f)
                txt = fm.elidedText(label, Qt.TextElideMode.ElideRight, int(r * 3.6))
                p.setPen(QPen(QColor(226, 240, 250, int(210 * alpha))))
                p.drawText(QRectF(cx - r * 1.8, cy + r + 12, r * 3.6, 12),
                           Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, txt)

            if b["id"] == self._sel:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(255, 208, 31, 200), 1.8))
                p.drawEllipse(QPointF(cx, cy), r + 3.5, r + 3.5)

    def _paint_mats(self, p, b, cx, cy, r, alpha):
        """素材球：挂在这一段周围一圈上，静止不转。一颗素材一颗球。"""
        mats = b.get("mats") or []
        n = min(len(mats), MAX_MAT_DOTS)
        if not n:
            return
        base = 0.5 + int(b.get("_i", 0)) * 0.9      # 每颗球错开起始角
        for k in range(n):
            a = (k / float(n)) * 6.2832 + base
            rr = r + 9.0 + min(8.0, k * 1.6)
            mx = cx + math.cos(a) * rr
            my = cy + math.sin(a) * rr * 0.94
            mr = 3.4 + (k % 3) * 0.5
            g = QRadialGradient(QPointF(mx - mr * 0.35, my - mr * 0.4), mr * 1.7)
            g.setColorAt(0.0, QColor(242, 251, 255, int(230 * alpha)))
            g.setColorAt(0.55, QColor(98, 185, 232, int(215 * alpha)))
            g.setColorAt(1.0, QColor(44, 124, 177, int(220 * alpha)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawEllipse(QPointF(mx, my), mr, mr)
        if len(mats) > MAX_MAT_DOTS:
            f = QFont(T.MONO)
            f.setPixelSize(9)
            p.setFont(f)
            p.setPen(QPen(QColor(190, 214, 232, int(160 * alpha))))
            p.drawText(QRectF(cx + r + 6, cy - r - 6, 34, 12),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       "+%d" % (len(mats) - MAX_MAT_DOTS))

    def _paint_water_front(self, p, body):
        """水膜：盖在球上面的一层冷色 —— 越深越厚，这就是"分层"的来源。"""
        p.save()
        p.setClipPath(self._water_path(body))
        h = float(self.height())
        f = QLinearGradient(0.0, h * 0.15, 0.0, h)
        f.setColorAt(0.00, QColor(120, 190, 230, 22))
        f.setColorAt(0.50, QColor(80, 150, 200, 40))
        f.setColorAt(1.00, QColor(40, 100, 150, 86))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(f))
        p.drawRect(body)

        # 扫光：一道缓慢横移的环境光
        w = float(self.width())
        sw = w * 0.42
        sx = ((self._t * 46) % (w + sw * 2)) - sw
        sg = QLinearGradient(sx, 0.0, sx + sw, h)
        sg.setColorAt(0.0, QColor(255, 255, 255, 0))
        sg.setColorAt(0.5, QColor(226, 248, 255, 15))
        sg.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(sg))
        p.drawRect(body)

        # 波面亮线：一条实、一条晕
        p.setBrush(Qt.BrushStyle.NoBrush)
        pts = []
        x = body.left() - 4
        while x <= body.right() + 4:
            pts.append(QPointF(x, self._surf(x)))
            x += 5.0
        for k in range(1, len(pts)):
            p.setPen(QPen(QColor(255, 255, 255, 40), 4.6))
            p.drawLine(pts[k - 1], pts[k])
        for k in range(1, len(pts)):
            p.setPen(QPen(QColor(226, 250, 255, 158), 1.4))
            p.drawLine(pts[k - 1], pts[k])
        p.restore()

    def _paint_edges(self, p, path, body):
        p.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(QColor(255, 122, 122, 42), 1.1)
        p.setPen(pen)
        p.drawLine(QPointF(body.left() + 2.5, body.top() + 16),
                   QPointF(body.left() + 2.5, body.bottom() - 16))
        pen = QPen(QColor(122, 168, 255, 42), 1.1)
        p.setPen(pen)
        p.drawLine(QPointF(body.right() - 2.5, body.top() + 16),
                   QPointF(body.right() - 2.5, body.bottom() - 16))
        rim = QLinearGradient(body.topLeft(), body.bottomLeft())
        rim.setColorAt(0.00, QColor(255, 255, 255, 92))
        rim.setColorAt(0.45, QColor(255, 255, 255, 20))
        rim.setColorAt(1.00, QColor(255, 255, 255, 44))
        p.setPen(QPen(QBrush(rim), 1.0))
        p.drawPath(path)

    def _paint_empty(self, p, body):
        f = QFont(T.FONT)
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(QPen(QColor(T.FG3)))
        p.drawText(body.adjusted(PADX, 0, -PADX, 0), Qt.AlignmentFlag.AlignCenter,
                   "池子还是空的 —— 双击空白水面投一颗新球")
