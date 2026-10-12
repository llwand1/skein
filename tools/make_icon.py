# -*- coding: utf-8 -*-
"""Skein · 桌面图标生成（PyQt6）

一次生成多尺寸 app.ico（16/32/48/64/128/256），随源码走，随时可重生成。
设计：暖墨圆角砖 + 一枚开口金色环 —— 对应产品那颗常驻悬浮球与品牌金（#FFD01F）。

用法：python tools\make_icon.py
"""
import os
import struct
import sys

from PyQt6.QtCore import Qt, QRectF, QPointF, QBuffer, QByteArray
from PyQt6.QtGui import (QGuiApplication, QImage, QPainter, QColor, QPen,
                         QRadialGradient, QLinearGradient, QBrush)

SIZES = [16, 32, 48, 64, 128, 256]
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.ico")


def render(size):
    """按目标尺寸直接绘制（每档都抗锯齿重画，小尺寸不糊）。"""
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    s = float(size)
    inset = s * 0.045
    rect = QRectF(inset, inset, s - inset * 2, s - inset * 2)
    radius = rect.width() * 0.225

    # 砖体：暖墨斜向渐变（左上加暖，右下压下）
    g = QLinearGradient(rect.topLeft(), rect.bottomRight())
    g.setColorAt(0.0, QColor("#2b2419"))
    g.setColorAt(1.0, QColor("#120f0b"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(g))
    p.drawRoundedRect(rect, radius, radius)

    # 极淡内描边：玻璃厚度感
    edge = QPen(QColor(244, 237, 225, 22))
    edge.setWidthF(max(1.0, s * 0.008))
    hw = edge.widthF() / 2.0
    p.setPen(edge)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(rect.adjusted(hw, hw, -hw, -hw), radius, radius)

    # 金环几何
    cx = cy = s / 2.0
    rr = s * 0.275          # 环半径
    w = s * 0.078           # 环粗细
    ring = QRectF(cx - rr, cy - rr, rr * 2, rr * 2)

    # 环下柔光，让金环浮起来
    glow = QRadialGradient(QPointF(cx, cy), rr + w)
    glow.setColorAt(0.0, QColor(255, 208, 31, 0))
    glow.setColorAt(0.72, QColor(255, 208, 31, 0))
    glow.setColorAt(1.0, QColor(255, 208, 31, 40))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(glow))
    p.drawEllipse(QRectF(cx - rr - w, cy - rr - w, (rr + w) * 2, (rr + w) * 2))

    # 金环本体：圆头开口环，从 120° 起顺时针扫 300°，缺口留在左上
    arc = QPen()
    arc.setWidthF(w)
    arc.setCapStyle(Qt.PenCapStyle.RoundCap)
    ag = QLinearGradient(ring.topLeft(), ring.bottomRight())
    ag.setColorAt(0.0, QColor("#FFE45C"))
    ag.setColorAt(1.0, QColor("#E8A800"))
    arc.setBrush(QBrush(ag))
    p.setPen(arc)
    p.drawArc(ring, int(120 * 16), int(-300 * 16))

    p.end()
    return img


def to_png(img):
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def build():
    QGuiApplication(sys.argv)  # QPainter 需要 GUI 应用上下文
    entries = [(sz, to_png(render(sz))) for sz in SIZES]

    # 组装 ICO（Vista+ 支持 PNG 压缩条目）
    n = len(entries)
    dirs = b""
    blobs = b""
    offset = 6 + 16 * n
    for sz, data in entries:
        dim = 0 if sz >= 256 else sz
        dirs += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)

    with open(OUT, "wb") as f:
        f.write(struct.pack("<HHH", 0, 1, n) + dirs + blobs)
    print("wrote %s (%d bytes, %d sizes)" % (OUT, os.path.getsize(OUT), n))


if __name__ == "__main__":
    build()
