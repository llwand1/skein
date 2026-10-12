# -*- coding: utf-8 -*-
"""Skein · 面板（PyQt6）

面板是**平的**，不是流程 —— 没有"第几步"的闸门。
  素材怎么攒本来就不固定：人可能同时在随手记、在喂某一段、在确认另一段，
  所以这些入口必须**同时在**，而不是一次只放一个出来。
  面板里常驻的只有一件事：「喂」（输入框 + 目标下拉：灵感池 / 池子里的任意一颗球）。
  池子（basin.Basin）、AI 提议、完成度、收口按钮都常驻；AI 的"下一步"只是**一行提示**，不挡路。

结构区是**一口水池**（§2.18）：一颗球＝视频的一个段落，顺序只写在球心的金徽上，
没编号的球是"还没排进顺序"的段落。原先那条贯穿到底的"一绞线"（Strand）与节拍行
（BeatRow）都删掉了 —— 池子里没有东西可锚，顺序改由编号承担。

展开动效（"液态展开"）：
  球在被点的那一下被"拉开"——面板窗口从**球的矩形**长成**面板矩形**，
  圆角前段一直保持接近胶囊（表面张力），后段才落到 16px；
  材质在后段由"玻璃"凝结成"纸面"。形状没到位之前内容不出面，出来时再裁进轮廓里。
  进 480ms（ease-out-quart）/ 出 360ms（ease-in-cubic，= 入场的 75%）。
  系统关掉"显示动画"时（winutil.animations_enabled）自动退化为瞬时展开。

PyQt 的两个坑（踩过，改前先看）：
  1. 槽的参数个数只能**少于等于**信号的参数个数。`returnPressed` 是 0 参信号，
     接 `lambda _: ...` 会在按键那一刻抛 TypeError —— 而 PyQt 对槽里的未处理异常
     是直接 qFatal（进程整体退出，退出码 0xC0000409），连 traceback 都会随
     stderr 缓冲一起丢掉，极难定位。
  2. 刷新会重建整棵子树，而刷新常常就是从那棵树里某个控件的信号里发起的。
     所以旧内容走 takeWidget + deleteLater，**不能**让它被 setWidget 同步删掉。
"""
import math
import os
import re
import time

import theme as T
import brain
import basin as basinmod
import config
import icons
import llm
import winutil
from basin import Basin
from pool import Pool
from theme import (QSS, GOLD, GREEN, BLUE, RED, AMBER, VIOLET, FG3)

from PyQt6.QtCore import (Qt, QEvent, QRectF, QRect, QPoint, QPointF, QTimer,
                          QPropertyAnimation, QEasingCurve)
from PyQt6.QtGui import (QPainter, QColor, QPen, QBrush, QLinearGradient,
                         QRadialGradient, QPainterPath, QRegion, QShortcut,
                         QKeySequence, QPixmap)
from PyQt6.QtWidgets import (QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout,
                             QPushButton, QPlainTextEdit, QComboBox, QLineEdit,
                             QScrollArea, QMenu, QApplication, QFileDialog,
                             QCheckBox, QTextBrowser, QSizePolicy,
                             QGraphicsOpacityEffect)

DOT = {"empty": RED, "supported": AMBER, "locked": GREEN}
STXT = {"empty": "未支撑", "supported": "待确认", "locked": "已确认"}
TITLEBAR_H = 48

PAD = 16           # 窗口四周留白：也是玻璃/纸面相对窗口的内缩量
R_PANEL = 16       # 展开完成后的圆角
W_FULL, H_FULL = 520, 736
GUTTER = 16        # 内容左侧留白（原来 34px 是给那条"一绞线"留的，线删了就收回来）
MORPH_OPEN = 480   # ms
MORPH_CLOSE = 360  # ms（出场 = 入场的 75%）
CONTENT_AT = 0.72  # 形状走到这里才放内容出来（此时圆角已落到 16px，内容不会再溢出轮廓）
SHELL_LO, SHELL_HI = 0.25, 0.72   # 圆角"表面张力"区间：之前几乎胶囊，之后落到 16px
ANIMATE = True     # uitest 会置 False，让走查保持同步


# ---------------- Markdown → 富文本（§2.33）----------------
# 用 markdown 库把回复转成 HTML，样式贴 theme（A·纪律：字号只用 12/13/15）。
_MD_CSS = (
    "p { margin: 0 0 7px 0; }"
    "h1 { color: %s; font-size: 15px; font-weight: 600; margin: 10px 0 5px 0; }"
    "h2 { color: %s; font-size: 13px; font-weight: 700; margin: 9px 0 5px 0; }"
    "h3 { color: %s; font-size: 12px; font-weight: 700; margin: 8px 0 4px 0; }"
    "a { color: %s; }"
    "code { font-family: 'Cascadia Mono','Consolas',monospace; font-size: 11px; color: %s; }"
    "pre { background-color: #141210; padding: 8px; }"
    "blockquote { color: %s; margin: 6px 0 6px 10px; }"
    "ul, ol { margin: 4px 0 7px 0; }"
    "li { margin: 2px 0; }"
    "th, td { padding: 3px 8px; }"
    % (T.FG, T.FG, T.FG, T.BLUE, T.AMBER, T.FG3)
)
_MD = {"m": None}


def md_html(text, caret=False):
    """把回复按 Markdown 渲染成 HTML；caret=True 时末尾挂一个流式光标。"""
    src = text or ""
    try:
        if _MD["m"] is None:
            import markdown as _m
            _MD["m"] = _m.Markdown(extensions=["fenced_code", "tables", "sane_lists", "nl2br"])
        m = _MD["m"]
        body = m.convert(src)
        m.reset()          # 复用一个实例必须 reset，否则状态会串到下一条
    except Exception:
        import html as _h
        body = "<p>%s</p>" % _h.escape(src).replace("\n", "<br>")
    if caret:
        body += ('<p style="margin:0"><span style="color:%s;font-size:13px;">▍</span></p>' % T.GOLD)
    return '<div style="font-size:12px;color:%s;line-height:150%%;">%s</div>' % (T.FG2, body)


class RichText(QTextBrowser):
    """一小段 HTML 的渲染体：**自己不滚动**（滚动交给外层 QScrollArea），高度按内容自适应。

    为什么不装 QtWebEngine：它太重，而且和我们这套 QPainter 自绘美学不是一路。
    QTextBrowser 够用（Qt 的富文本子集），布局也简单。
    """

    def __init__(self, html="", app=None, parent=None):
        super().__init__(parent)
        self.setObjectName("rich")
        self.setOpenExternalLinks(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.document().setDefaultStyleSheet(_MD_CSS)
        self.document().setDocumentMargin(0)
        self._fitting = False
        self._app = app
        self.anchorClicked.connect(self._on_link)
        if html:
            self.set_html(html)

    def _on_link(self, url):
        if self._app is not None and url and url.toString():
            self._app.open_url(url.toString())

    def set_html(self, html):
        self.setHtml(html)
        self._fit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def _fit(self):
        """按 viewport 宽度重排，再把高度卡到内容高度 —— 不然会在外层滚动区里自己滚。"""
        if self._fitting:
            return
        self._fitting = True
        try:
            w = self.viewport().width()
            if w > 0:
                self.document().setTextWidth(w)
                h = int(self.document().size().height()) + 2
                if abs(h - self.height()) > 1:
                    self.setFixedHeight(max(18, h))
        finally:
            self._fitting = False


class ThinkingDots(QWidget):
    """三点呼吸的等待态（正在想 / 正在查资料…）—— 纯 QPainter，不用字形。"""

    def __init__(self, text="正在想", parent=None):
        super().__init__(parent)
        self._text = text
        self._t = 0
        self.setFixedHeight(18)
        self._tm = QTimer(self)
        self._tm.timeout.connect(self._tick)
        if ANIMATE:
            self._tm.start(90)

    def set_text(self, text):
        self._text = text
        self.update()

    def _tick(self):
        self._t = (self._t + 1) % 40
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        f = self.font()
        f.setPointSizeF(9.5)
        p.setFont(f)
        p.setPen(QPen(QColor(FG3)))
        p.drawText(QRect(0, 0, 78, self.height()),
                   int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self._text)
        for i in range(3):
            a = 0.28 + 0.72 * (0.5 + 0.5 * math.sin((self._t / 40.0 * 2 * math.pi) - i * 0.7))
            c = QColor(GOLD)
            c.setAlphaF(a)
            p.setBrush(QBrush(c))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(82 + i * 9, self.height() / 2.0 - 2, 4, 4))
        p.end()


def _smoothstep(a, b, x):
    """把 x 从 [a,b] 平滑映射到 [0,1]（两端导数为 0，所以圆角收得"有惯性"）"""
    if b <= a:
        return 1.0 if x >= b else 0.0
    t = (x - a) / (b - a)
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


class _InlineEdit(QLineEdit):
    """行内改措辞用的输入框：Enter 提交（returnPressed），Esc 退出编辑。

    为什么要单独一层：面板自己有一条 Esc = 收起面板的快捷键，它会在按键送到
    焦点控件之前先被拦下。所以这里只能接管 Esc，并让 Panel 在编辑期间把那条
    快捷键临时关掉（见 Panel.refresh 末尾）。
    """

    def __init__(self, on_esc):
        super().__init__()
        self._on_esc = on_esc

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self._on_esc()
            return
        super().keyPressEvent(e)


class Panel(QWidget):
    def __init__(self, app):
        super().__init__(None)
        self.app = app
        self.input = None
        self.combo = None
        self.topic_input = None
        self.chat_input = None
        self._balls = []             # 这一期池子里的球（喂素材的目标按它取 id）
        self._pool = None            # 池子控件（漂在刷新里重建，留个引用方便测试）
        self._sel_bid = -1           # 池子里选中的那颗球（纯界面状态，不进库）
        self._submit_fn = None
        self._drag = None
        # 行内改措辞：哪一颗球正在被改；以及它是不是"刚投进来的新球"
        # （新球没写内容＝当没投过；改老球清空了＝放弃，不动原值）
        self._edit_bid = None
        self._edit_new = False
        # 改期名 / 改 payoff 各自一个编辑态
        self._edit_topic = False
        self._edit_payoff = False
        self._pool_open = False      # 灵感池摊开列表了吗（收着时只显示池子本身）
        # 触发式区块：默认都收着，点了才展开（§2.20 —— 常驻的只有「聊」，其余不占版面）
        self._show_feed = False      # 「喂」抽屉
        self._show_pool = False      # 池子（叙事结构）抽屉
        self._show_props = False     # AI 提议抽屉
        self._show_search = False    # 素材搜索抽屉（§2.22）
        self._show_settings = False  # 设置抽屉（§2.25）：模型 / 内容默认 / 系统提示词
        # 设置表单的当前值（不落盘，除非按「保存」）—— 刷新会重建输入的控件，
        # 值放这儿才不会在重建时被清掉。打开抽屉时从磁盘取一次当初始值。
        self._set_form = None
        self._help = set()           # 点开的「?」长说明（key → 展开那一行）
        self._links_open = set()     # 摊开的链接组（key → 这一条里的网址全列出来，供挑）
        self._search_input = None    # 搜索框（刷新会重建）
        self._chat_anchor = False    # 这次刷新要不要把对话滚到底
        self._chat_seen = 0          # 上一轮对话条数（变多了就滚到底）
        # 对话体验（§2.33）：历史折叠 / 流式活体 / 光标闪烁
        self._hist_open = False      # 历史是否展开（默认只看最近一轮）
        self._live_rich = None       # 正在流式吐的那句的渲染体（直接改它，不重建面板）
        self._live_text = ""
        self._caret = True
        self._caret_tm = None

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(QSS)
        self.resize(W_FULL, H_FULL)
        # 文件可以直接拖到面板上（图片 / 视频 / 录音 / 长文），落在哪个目标由下拉决定
        self.setAcceptDrops(True)

        # —— 液态展开状态 ——
        self._morph = 1.0                       # 0 = 还在球里，1 = 完全展开
        self._animate = bool(ANIMATE and winutil.animations_enabled())
        self._animating = False
        self._ticker = None                     # 16ms 推进器（几何 + 形态一条进度）
        self._from = self._to = self._target = None
        self._dur = 1.0
        self._mode = "open"
        self._t0 = 0.0
        self._home = None                       # 本次展开的来源球矩形

        self.move(930, 60)

        # 内容固定尺寸 + 手工定位：窗口在长的时候，Qt 不需要重新布局这棵子树
        self.root = QFrame(self)
        self.root.setObjectName("root")
        self.root.resize(W_FULL - 2 * PAD, H_FULL - 2 * PAD)
        self.root.move(PAD, PAD)

        v = QVBoxLayout(self.root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        bar = QWidget()
        bar.setObjectName("titlebar")
        bar.setFixedHeight(TITLEBAR_H)
        hb = QHBoxLayout(bar)
        hb.setContentsMargins(16, 0, 10, 0)
        hb.setSpacing(8)
        hb.addWidget(icons.icon_label("dot", 8, GOLD))
        hb.addWidget(self._mk("Skein", "appname"))
        hb.addWidget(self._mk("· 一绞线", "subtitle"))
        hb.addStretch(1)
        self.brainlab = self._mk(llm.status(), "subtitle")   # 说清现在是谁在判断
        hb.addWidget(self.brainlab)
        gear = icons.IconButton("settings", 16, obj="gear",
                                tip="设置：模型 / 内容默认 / 系统提示词")
        gear.clicked.connect(self._toggle_settings)
        hb.addWidget(gear)
        close = icons.IconButton("close", 16, color=FG3, active_color=RED,
                                 obj="close", tip="收起面板")
        close.clicked.connect(self.hide_panel)
        hb.addWidget(close)
        v.addWidget(bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        v.addWidget(self.scroll, 1)

        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._submit)
        # Esc 收起面板。但改措辞时 Esc 应该是"退出编辑"，所以编辑期间把它关掉
        #（快捷键会先于焦点控件收到按键，不关掉的话 Esc 会把整个面板收走）。
        self._esc = QShortcut(QKeySequence("Esc"), self, activated=self.hide_panel)
        # 整块面板都能拖起来（见 eventFilter）：装应用级过滤器，指针离开面板也还跟着
        qapp = QApplication.instance()
        if qapp is not None:
            qapp.installEventFilter(self)

        self.hide()

    # ---------------- 基础构件 ----------------
    @staticmethod
    def _mk(text, obj):
        lab = QLabel(text)
        lab.setObjectName(obj)
        # QLabel 要显式打开这个属性，QSS 的 background 才会画（实测踩过）
        lab.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        return lab

    @staticmethod
    def _lab(text, obj="fg2", wrap=True):
        lab = QLabel(text)
        lab.setObjectName(obj)
        lab.setWordWrap(wrap)
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return lab

    @staticmethod
    def _card(name="card"):
        # A·纪律：间距只用 4·8·12·16·24 这几个刻度，内边距一律 12
        f = QFrame()
        f.setObjectName(name)
        v = QVBoxLayout(f)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)
        return f, v

    @staticmethod
    def _btn(text, obj="", align_left=False):
        b = QPushButton(text)
        if obj:
            b.setObjectName(obj)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        if align_left:
            b.setStyleSheet("text-align:left;padding:8px 12px;")
        return b

    # ---------------- 触发式构件（§2.20）----------------
    def _chip(self, text, tip, handler, on=False, key="", icon_name=""):
        """触发条上的小芯片：点一下展开/收起对应抽屉。收着是哑色，开着描金。

        key 是稳定标识（QSS 的 objectName 要留给 chip/chipon，所以身份用动态属性 k）。
        """
        b = QPushButton(text)
        b.setObjectName("chipon" if on else "chip")
        if key:
            b.setProperty("k", key)
        if icon_name:
            b.setIcon(icons.icon(icon_name, 12, GOLD if on else FG3))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setToolTip(tip)
        b.clicked.connect(handler)
        return b

    def _qmark(self, key, text):
        """长说明收进一个「?」：悬停出提示，点一下就地展开那一行。"""
        b = QPushButton("?")
        b.setObjectName("q")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFixedSize(16, 16)
        b.setToolTip(text)
        b.clicked.connect(lambda _=False, k=key: self._toggle_help(k))
        return b

    def _toggle_help(self, key):
        if key in self._help:
            self._help.discard(key)
        else:
            self._help.add(key)
        self.refresh()

    def _help_line(self, lay, key, text):
        if key in self._help:
            lay.addWidget(self._lab(text, "dim"))

    def _toggle_feed(self):
        self._show_feed = not self._show_feed
        self.refresh()

    def _toggle_pool_view(self):
        self._show_pool = not self._show_pool
        self.refresh()

    def _toggle_props(self):
        self._show_props = not self._show_props
        self.refresh()

    def _toggle_search(self):
        self._show_search = not self._show_search
        self.refresh()

    # ---------------- 设置抽屉（§2.25）：模型 / 内容默认 / 系统提示词 ----------------
    def _toggle_settings(self):
        self._show_settings = not self._show_settings
        if self._show_settings and self._set_form is None:
            self._set_form = self._load_settings_form()
        self.refresh()

    def _load_settings_form(self):
        """从磁盘取一次当前设置，作为表单的初始值。"""
        st = config.load_settings()
        lc = llm.config()
        return {
            "base": lc.get("base") or "",
            "model": lc.get("model") or "",
            "key": lc.get("key") or "",
            "platform": str(st.get("platform") or ""),
            "form": str(st.get("form") or ""),
            "target": str(st.get("target") or ""),
            "sys_prompt": str(st.get("sys_prompt") or ""),
            "mcp_enabled": bool(st.get("mcp_enabled", True)),
            "mcp_node": str(st.get("mcp_node") or ""),
            "mcp_proxy": str(st.get("mcp_proxy") or ""),
            "mcp_url": str(st.get("mcp_url") or ""),
            "update_auto_check": bool(st.get("update_auto_check", True)),
        }

    def settings_values(self):
        """给 app 用的：表单此刻的值（不落盘）。"""
        return dict(self._set_form or self._load_settings_form())

    def _test_conn(self):
        self.app.test_connection()

    def _check_update(self):
        self.app.check_update()

    def _apply_update(self):
        self.app.apply_update()

    def _save_settings(self):
        self.app.save_settings(self.settings_values())

    def _open_user_md(self):
        """打开跨期用户记忆 user.md（§2.29）—— 不存在就先落一份带 by:user 的空模板。"""
        self.app.open_url(config.ensure_user_md())

    def _set_row(self, v, label, key, ph="", password=False):
        """一行：左标签 + 右输入框。值同步进 self._set_form，刷新重建也不丢。"""
        row = QHBoxLayout()
        row.setSpacing(8)
        lab = self._lab(label, "dim")
        lab.setFixedWidth(58)
        row.addWidget(lab)
        ed = QLineEdit(self._set_form.get(key, ""))
        ed.setObjectName("set_" + key)
        if ph:
            ed.setPlaceholderText(ph)
        if password:
            ed.setEchoMode(QLineEdit.EchoMode.Password)
        ed.textChanged.connect(lambda t, k=key: self._set_form.__setitem__(k, t))
        row.addWidget(ed, 1)
        v.addLayout(row)
        return ed

    def _settings_section(self, lay, s):
        """设置：三组一起。入口一直是标题栏那枚齿轮，这里只是把它展开。"""
        f, v = self._card("settingscard")
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(self._mk("设置", "h2"), 1)
        head.addWidget(self._qmark("settings",
            "三组都不预设：模型留空就走本地规则；内容默认留空＝不兜任何默认值；"
            "系统提示词留空＝用通用兜底。"))
        v.addLayout(head)

        # —— 组 1：模型 ——
        v.addWidget(self._lab("模型（OpenAI 兼容）", "cap"))
        self._set_row(v, "base_url", "base", "https://…/v1")
        self._set_row(v, "model", "model", "模型名")
        self._set_row(v, "key", "key", "sk-…", password=True)
        crow = QHBoxLayout()
        crow.setSpacing(8)
        tb = self._btn("测试连接", "plain")
        tb.clicked.connect(self._test_conn)
        crow.addWidget(tb)
        conn = self._lab(getattr(self.app, "conn_msg", "") or "", "dim")
        crow.addWidget(conn, 1)
        v.addLayout(crow)
        self._help_line(v, "settings", "配了 key 才会走模型；不通会在标题栏如实写「模型不通 · 本地兜底」。")

        # —— 组 2：内容默认（留空＝不预设）——
        v.addWidget(self._lab("内容默认（留空＝不预设）", "cap"))
        self._set_row(v, "平台", "platform", "如：抖音 / B站（留空不预设）")
        self._set_row(v, "形态", "form", "如：图文 / 口播（留空不预设）")
        self._set_row(v, "目标张数", "target", "如：7（留空不预设）")

        # —— 组 3：系统提示词（人设）——
        v.addWidget(self._lab("系统提示词（留空＝通用兜底）", "cap"))
        sp = QPlainTextEdit(self._set_form.get("sys_prompt", ""))
        sp.setObjectName("set_sys_prompt")
        sp.setPlaceholderText("留空＝不预设人设，用通用叙事助手兜底")
        sp.setFixedHeight(96)
        sp.textChanged.connect(lambda: self._set_form.__setitem__("sys_prompt", sp.toPlainText()))
        v.addWidget(sp)

        # —— 组 4：跨期记忆 · user.md（§2.29：只读，应用不自动写，人自己改）——
        v.addWidget(self._lab("跨期记忆 · user.md（只读，你自己写）", "cap"))
        urow = QHBoxLayout()
        urow.setSpacing(8)
        ub = self._btn("打开 user.md", "plain")
        ub.setToolTip("跨期记住「你是谁 / 你的口味」—— 每一期都会读它，只作参考")
        ub.clicked.connect(self._open_user_md)
        urow.addWidget(ub)
        urow.addWidget(self._lab("应用不自动写它；内容由你自己编辑。", "dim"), 1)
        v.addLayout(urow)

        # —— 组 5：搜索 MCP（tinyfish，§2.31）。默认**开**：AI 聊天时能联网搜 ——
        v.addWidget(self._lab("搜索 MCP · tinyfish（默认开）", "cap"))
        mrow = QHBoxLayout()
        mrow.setSpacing(8)
        cb = QCheckBox("开启：AI 聊天时可用联网搜索（只读工具）")
        cb.setObjectName("set_mcp_enabled")
        cb.setChecked(bool(self._set_form.get("mcp_enabled")))
        cb.toggled.connect(lambda on: self._set_form.__setitem__("mcp_enabled", bool(on)))
        mrow.addWidget(cb)
        mrow.addStretch(1)
        v.addLayout(mrow)
        self._set_row(v, "node 路径", "mcp_node", "留空＝自动找 node.exe")
        self._set_row(v, "proxy 路径", "mcp_proxy", "留空＝自动找 mcp-remote 的 proxy.js")
        self._set_row(v, "服务地址", "mcp_url", "默认 https://agent.tinyfish.ai/mcp")
        v.addWidget(self._lab(
            "只放行只读工具（search / fetch_content）；OAuth 由 mcp-remote 自己管，token 存在 ~/.mcp-auth。",
            "dim"))

        # —— 组 6：更新（§2.32）。开机静默检查；有新版这里才出现「下载并更新」——
        v.addWidget(self._lab("更新", "cap"))
        urow2 = QHBoxLayout()
        urow2.setSpacing(8)
        ck = self._btn("检查更新", "plain")
        ck.setObjectName("set_check_update")
        ck.clicked.connect(self._check_update)
        urow2.addWidget(ck)
        if getattr(self.app, "upd_has", False):
            up = self._btn("下载并更新", "primary")
            up.setObjectName("set_apply_update")
            up.clicked.connect(self._apply_update)
            urow2.addWidget(up)
        urow2.addWidget(self._lab(getattr(self.app, "upd_msg", "") or "", "dim"), 1)
        v.addLayout(urow2)
        ac = QCheckBox("启动时自动检查更新")
        ac.setObjectName("set_update_auto_check")
        ac.setChecked(bool(self._set_form.get("update_auto_check")))
        ac.toggled.connect(lambda on: self._set_form.__setitem__("update_auto_check", bool(on)))
        v.addWidget(ac)
        v.addWidget(self._lab(
            "当前版本 v%s；更新只覆盖代码，不动你的数据（%s）。"
            % (config.VERSION, r"%LOCALAPPDATA%\Skein"), "dim"))

        srow = QHBoxLayout()
        srow.setSpacing(8)
        save = self._btn("保存设置", "primary")
        save.clicked.connect(self._save_settings)
        srow.addWidget(save)
        hint = self._lab("内容默认只影响之后新立的项，旧的不动。", "dim")
        srow.addWidget(hint, 1)
        v.addLayout(srow)
        lay.addWidget(f)

    # ---------------- 小工具：链接 / 复制 ----------------
    _URL = re.compile(r"https?://[^\s\u3000<>\"'，。；）)]+")

    @classmethod
    def _urls(cls, text):
        return cls._URL.findall(text or "")

    @staticmethod
    def _domain(url):
        try:
            return url.split("//", 1)[-1].split("/", 1)[0] or url
        except Exception:
            return url

    def _link_btn(self, url, wide=False):
        """一条链接长成「打开 域名」小按钮 —— 点了直接开浏览器到原文。"""
        d = self._domain(url)
        if len(d) > 20:
            d = d[:19] + "…"
        b = self._btn("打开 " + d, "plain", align_left=wide)
        b.setToolTip(url)
        b.clicked.connect(lambda _=False, uu=url: self.app.open_url(uu))
        return b

    def _toggle_links(self, key):
        if key in self._links_open:
            self._links_open.discard(key)
        else:
            self._links_open.add(key)
        self.refresh()

    def _link_row(self, v, urls):
        """正文里的网址摆成「打开 域名」小按钮 —— 点了开浏览器到原文（§2.23）。

        默认只露 2 条（一排按钮在窄面板里放不下），其余的收进「还有 N 条」；
        点一下就摊成竖排、一条一行 —— 一条回复里带好几个出处时，人才有得挑。
        """
        urls = list(urls or [])
        if not urls:
            return
        key = "link:" + "|".join(urls)
        if key in self._links_open:
            for u in urls:
                v.addWidget(self._link_btn(u, wide=True))
            less = self._btn("收起", "plain")
            less.setIcon(icons.icon("chev_up", 12, FG3))
            less.clicked.connect(lambda _=False, k=key: self._toggle_links(k))
            v.addWidget(less)
            return
        row = QHBoxLayout()
        row.setSpacing(6)
        for u in urls[:2]:
            row.addWidget(self._link_btn(u))
        if len(urls) > 2:
            more = self._btn("还有 %d 条" % (len(urls) - 2), "plain")
            more.setIcon(icons.icon("chev_down", 12, FG3))
            more.setToolTip("摊开这一条里的全部链接，一条一行挑")
            more.clicked.connect(lambda _=False, k=key: self._toggle_links(k))
            row.addWidget(more)
        row.addStretch(1)
        v.addLayout(row)

    def _copy_text(self, text):
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(text or "")
            self.app._notify("已复制。")

    def _copy_btn(self, text):
        b = icons.IconButton("copy", 14, obj="mini", tip="复制这条")
        b.clicked.connect(lambda _, t=text: self._copy_text(t))
        return b

    # ---------------- 窗口 ----------------
    def show_panel(self):
        if self.isVisible() and not self._animating:
            self.raise_()
            self.refresh()
            return
        if not self._animate:
            self.root.setVisible(True)
            self._morph = 1.0
            self._place_near(self._orb_rect())
            self.show()
            self.raise_()
            self.activateWindow()
            return
        self._open_animated()

    def hide_panel(self):
        if not self.isVisible():
            return
        if not self._animate or self._animating:
            self._finish_hide()
            return
        self._close_animated()

    def toggle(self):
        if self.isVisible():
            self.hide_panel()
        else:
            self.show_panel()
            self.refresh()

    # ---------------- 液态展开 ----------------
    def _orb_rect(self):
        """球当前所在矩形（展开的起点）"""
        orb = getattr(self.app, "orb", None)
        if orb is not None:
            try:
                return QRect(orb.x(), orb.y(), orb.width(), orb.height())
            except RuntimeError:
                pass
        return QRect(930, 60, 92, 92)

    def _place_near(self, orb_rect):
        """面板落在球旁边（哪边宽敞去哪边），并保证完整在屏幕内"""
        scr = self.screen() or QApplication.primaryScreen()
        g = scr.availableGeometry() if scr else QRect(0, 0, 1440, 900)
        gap = 10
        x = orb_rect.left() - W_FULL - gap
        if x < g.left() + 8:
            x = orb_rect.right() + gap
        x = min(max(x, g.left() + 8), g.right() - W_FULL - 7)
        y = orb_rect.center().y() - H_FULL // 2
        y = min(max(y, g.top() + 8), g.bottom() - H_FULL - 7)
        return QRect(x, y, W_FULL, H_FULL)

    def _clamp(self):
        """任何情况下都别把面板顶到屏幕外（换分辨率 / 拖过头都还能找回来）"""
        scr = self.screen() or QApplication.primaryScreen()
        if not scr:
            return
        g = scr.availableGeometry()
        x = min(max(self.x(), g.left() + 8), g.right() - self.width() - 8)
        y = min(max(self.y(), g.top() + 8), g.bottom() - self.height() - 8)
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def _corner_radius(self, m=None):
        """圆角：前段维持"表面张力"（几乎胶囊），后段才落到 16px。
        m→1 时该项无意义（清掉遮罩），返回 R_PANEL。"""
        m = self._morph if m is None else m
        if m >= 0.999:
            return float(R_PANEL)
        box = self._box()
        r_round = min(box.width(), box.height()) / 2.0
        k = _smoothstep(SHELL_LO, SHELL_HI, m)
        return r_round * (1 - k) + R_PANEL * k

    def _box(self):
        """玻璃/纸面相对窗口的内缩矩形（= 内容根容器的位置与尺寸）"""
        return QRectF(PAD, PAD,
                      max(0.0, self.width() - 2 * PAD),
                      max(0.0, self.height() - 2 * PAD))

    def _apply_content_mask(self):
        """把内容裁进正在生长的轮廓里。

        没有这一步，形状还是胶囊时内容就已经方方正正地溢出外面了
        （m=0.58 那帧胶片上就是这个毛病）。展开完成后窗口与 root 尺寸相等，
        遮罩直接清掉，避免常驻裁剪影响文字的次像素抗锯齿。
        """
        if self._morph >= 0.999:
            if self.root.mask():
                self.root.clearMask()
            return
        box = self._box()
        if box.width() < 2 or box.height() < 2:
            self.root.clearMask()
            return
        rad = self._corner_radius()
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, box.width(), box.height()), rad, rad)
        self.root.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def _set_morph(self, v):
        self._morph = float(v)
        if self._morph >= CONTENT_AT and not self.root.isVisible():
            self.root.setVisible(True)
        self._apply_content_mask()
        self.update()

    # 用一个显式计时器推进"几何 + 形态"，比 QPropertyAnimation 少一层不确定性：
    # 位置、圆角、材质、内容露出全在同一条进度上，出问题也看得见。
    def _start_morph(self, frm, to, dur, mode):
        self._from, self._to = frm, to
        self._dur, self._mode = float(dur), mode
        self._t0 = time.monotonic()
        self._animating = True
        if self._ticker is None:
            self._ticker = QTimer(self)
            self._ticker.setInterval(16)
            self._ticker.timeout.connect(self._morph_tick)
        self._ticker.start()

    def _morph_tick(self):
        t = min(1.0, (time.monotonic() - self._t0) * 1000.0 / self._dur)
        if self._mode == "open":
            e = 1.0 - (1.0 - t) ** 4          # ease-out-quart：入场，来得快、落得稳
        else:
            e = t ** 3                         # ease-in-cubic：出场，先慢后快
        a, b = self._from, self._to
        self.setGeometry(QRect(int(a.x() + (b.x() - a.x()) * e),
                               int(a.y() + (b.y() - a.y()) * e),
                               int(a.width() + (b.width() - a.width()) * e),
                               int(a.height() + (b.height() - a.height()) * e)))
        self._set_morph(e)
        if t >= 1.0:
            self._ticker.stop()
            if self._mode == "open":
                self._open_done()
            else:
                self._finish_hide()

    def _open_animated(self):
        orb = getattr(self.app, "orb", None)
        self._home = self._orb_rect()
        self._target = self._place_near(self._home)
        self.root.setVisible(False)
        self._morph = 0.0
        self.setGeometry(self._home)
        self.show()
        self.raise_()
        self.activateWindow()
        if orb is not None:
            try:
                orb.swell()
            except Exception:
                pass
        self._start_morph(self._home, self._target, MORPH_OPEN, "open")

    def _open_done(self):
        self._animating = False
        self.setGeometry(self._target)
        self.root.setVisible(True)
        self._morph = 1.0
        self.root.clearMask()
        self.update()
        self.refresh()

    def _close_animated(self):
        target = self._home or self._orb_rect()
        self._start_morph(self.geometry(), target, MORPH_CLOSE, "close")

    def _finish_hide(self):
        self._animating = False
        self.root.setVisible(True)
        self.root.clearMask()
        self._morph = 1.0
        self.hide()
        orb = getattr(self.app, "orb", None)
        if orb is not None:
            try:
                orb.swell()
            except Exception:
                pass

    # ---------------- 背景绘制（玻璃 → 纸面） ----------------
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        r = QRectF(PAD, PAD, w - 2 * PAD, h - 2 * PAD)
        if r.width() < 4 or r.height() < 4:
            return

        m = max(0.0, min(1.0, self._morph))
        rad = self._corner_radius(m)
        # 材质：玻璃 → 纸面（后段凝结）
        mat = 0.0 if m < 0.55 else min(1.0, ((m - 0.55) / 0.40) ** 1.1)
        glassA = 1.0 - mat

        # 1) 柔和外投影（叠几层圆角矩形当廉价模糊）
        p.setPen(Qt.PenStyle.NoPen)
        layers = 18
        for i in range(layers, 0, -1):
            a = int(52 * (1 - i / float(layers)) ** 2.0)
            if a <= 0:
                continue
            p.setBrush(QColor(0, 0, 0, a))
            p.drawRoundedRect(r.adjusted(-i, -i + 5, i, i + 8), rad + i, rad + i)

        path = QPainterPath()
        path.addRoundedRect(r, rad, rad)

        # 2) 玻璃机身（未凝结时才画）
        if glassA > 0.004:
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            g.setColorAt(0.00, QColor(96, 108, 138, int(176 * glassA)))
            g.setColorAt(0.55, QColor(40, 46, 62, int(214 * glassA)))
            g.setColorAt(1.00, QColor(14, 16, 23, int(242 * glassA)))
            p.setBrush(QBrush(g))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(path)

        # 3) 纸面（凝结之后）
        if mat > 0.004:
            p.setBrush(QColor(T.SURF))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(path)

        # 4) 玻璃上的光：镜面 + 色散 + 扫光（仅未凝结时）
        if glassA > 0.02:
            p.save()
            p.setClipPath(path)
            scale = max(70.0, min(r.width(), r.height()) * 0.85)
            spec = QRadialGradient(QPointF(r.left() + r.width() * 0.26,
                                            r.top() + r.height() * 0.10), scale)
            spec.setColorAt(0.0, QColor(255, 255, 255, int(120 * glassA)))
            spec.setColorAt(0.5, QColor(255, 255, 255, int(26 * glassA)))
            spec.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setBrush(QBrush(spec))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(path)

            sheen = (self._sheen() % 1.5) - 0.25
            sg = QLinearGradient(r.left() + sheen * r.width(), r.top(),
                                 r.left() + (sheen + 0.35) * r.width(), r.bottom())
            sg.setColorAt(0.0, QColor(255, 255, 255, 0))
            sg.setColorAt(0.5, QColor(255, 255, 255, int(22 * glassA)))
            sg.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setBrush(QBrush(sg))
            p.drawPath(path)

            pen = QPen(QColor(255, 118, 112, int(60 * glassA)), 1.4)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
            pen = QPen(QColor(118, 164, 255, int(60 * glassA)), 1.4)
            p.setPen(pen)
            p.drawPath(path)
            p.restore()

        # 5) 发丝边界 + 顶部受光边（纸面阶段的主导边）
        edge = 1.0 - 0.45 * mat
        rim = QLinearGradient(r.topLeft(), r.bottomLeft())
        rim.setColorAt(0.0, QColor(255, 255, 255, int(150 * edge)))
        rim.setColorAt(0.35, QColor(255, 255, 255, int(40 * edge)))
        rim.setColorAt(1.0, QColor(255, 255, 255, int(70 * edge)))
        p.setPen(QPen(QBrush(rim), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        if mat > 0.5:
            a = int(255 * (mat - 0.5) * 2)
            c = QColor(T.HAIR)
            c.setAlpha(a)
            p.setPen(QPen(c, 1.0))
            p.drawPath(path)

    def _sheen(self):
        """扫光带的相位：跟随窗口几何变化，展开时正好扫过一次"""
        try:
            return float(self._morph)
        except Exception:
            return 0.0

    def refresh(self):
        if not self.isVisible():
            return
        try:
            self.brainlab.setText(llm.status())   # 模型通不通要在界面上看得见
        except RuntimeError:
            pass
        keep = ""
        keep_chat = ""
        try:
            if self.input is not None:
                keep = self.input.toPlainText()
        except RuntimeError:
            keep = ""
        try:
            if self.chat_input is not None:
                keep_chat = self.chat_input.toPlainText()
        except RuntimeError:
            keep_chat = ""
        s = self.app.state()
        # 编辑目标可能已经不存在了（刚被撤下 / 换了期）—— 状态要清干净，
        # 否则 _edit_bid 悬着，Esc 会一直是"退出编辑"，人就关不掉面板。
        ball_ids = {b["id"] for b in (s.get("balls") or [])}
        if self._edit_bid is not None and self._edit_bid not in ball_ids:
            self._edit_bid = None
            self._edit_new = False
        if self._sel_bid not in ball_ids:
            self._sel_bid = -1
        self._esc.setEnabled(not self._editing())
        if not s.get("has_topic"):
            # 回到无主题态：选中与编辑态都跟着清掉，别带到下一期去
            self._sel_bid = -1
            self._edit_topic = False
            self._edit_payoff = False
        # 每次动作都重建整棵子树，滚动位置会被重置到顶部 —— 那就是"点一下就闪一下"。
        # 记住它，重建完还回去（正在改措辞时除外：那一行得滚进视野）。
        vbar = self.scroll.verticalScrollBar()
        keep_scroll = None if self._editing() else vbar.value()
        # 注意：这里绝不能 setStyleSheet("background:transparent")。
        # Qt 会把父级样式表作用到整个子树，子控件的 QSS background 会被一起抹掉。
        w = QWidget()
        # 建的过程中先别画：一次动作里要加几十个控件，边加边画看着就是"闪"
        w.setUpdatesEnabled(False)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(GUTTER, 8, 16, 16)
        lay.setSpacing(12)
        self.input = None
        self.combo = None
        self.topic_input = None
        self.chat_input = None
        # 球列表独立于「喂 / 池子」这两个抽屉都要能拿到（未指派素材的「指派到…」也用它）
        self._balls = list(s.get("balls") or [])
        self._submit_fn = None
        self._pool = None
        self._search_input = None

        # 设置抽屉：放在最前 —— 它是"全局的"，跟有没有主题无关
        if self._show_settings:
            self._settings_section(lay, s)

        notice = getattr(self.app, "notice", "")
        if notice:
            nf, nv = self._card("notice")
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(self._lab(notice, "fg2"), 1)
            x = icons.IconButton("close", 14, color=FG3, active_color=RED,
                                 obj="close", tip="关掉这条提示")
            x.clicked.connect(self._dismiss)
            row.addWidget(x)
            nv.addLayout(row)
            lay.addWidget(nf)

        if s.get("has_topic"):
            # 常驻的只有「聊」；喂 / 池子 / 提议都收在触发条后面，点了才展开。
            self._topic_head(lay, s)
            self._hint_line(lay, s)
            self._triggerbar(lay, s)
            if self._show_feed:
                self._feedbar(lay, s)
            if self._show_search:
                self._search_section(lay, s)
            if self._show_pool:
                self._pool_section(lay, s)
            self._chat(lay, s)
            if self._show_props:
                self._proposals(lay, s)
            self._unassigned(lay, s)
            self._output(lay, s)
            self._movie(lay, s)
        else:
            self._seed_head(lay, s)
            self._triggerbar(lay, s)
            if self._show_feed:
                self._feedbar(lay, s)
            if self._show_search:
                self._search_section(lay, s)
            self._inbox_detail(lay, s)
        lay.addStretch(1)
        # 旧内容**不能同步删**：setWidget() 会立刻 delete 上一棵树，而刷新往往
        # 就是从那棵树里的某个控件（按钮 / 行内输入框 / 池子）的信号里发起的 ——
        # 同步删等于在对方还在处理事件时把它拆了（实测：按回车提交措辞必崩）。
        # takeWidget 只把归属权交出来，不删；真正的删除交给事件循环。
        old = self.scroll.takeWidget()
        if old is not None:
            old.hide()
            old.setParent(None)
            old.deleteLater()
        self.scroll.setWidget(w)
        w.setUpdatesEnabled(True)
        if keep_scroll:
            # 等布局把新的 maximum 算出来再还原（同一个事件循环里还原会被夹成 0）
            QTimer.singleShot(0, lambda v=keep_scroll: vbar.setValue(min(v, vbar.maximum())))
        if self.input is not None and keep:
            self.input.setPlainText(keep)
        if self.chat_input is not None and keep_chat:
            self.chat_input.setPlainText(keep_chat)
        if self._chat_anchor:                      # 对话有新消息：滚到底（排在还原之后）
            self._chat_anchor = False
            QTimer.singleShot(0, lambda: vbar.setValue(vbar.maximum()))

    # ---------------- 主题头（有主题常驻 / 无主题给立项入口）----------------
    def _topic_head(self, lay, s):
        """主题头。期名可以**就地改** —— 「把主题写对」和「换一个主题」是两件事。"""
        t = s["topic"]
        head = QHBoxLayout()
        head.setSpacing(8)
        if self._edit_topic:
            ed = _InlineEdit(self._cancel_inline)
            ed.setObjectName("edittopic")
            ed.setText(t["name"])
            ed.setPlaceholderText("这一期叫什么？")
            ed.returnPressed.connect(lambda _e=None, w=ed: self._commit_topic(w))
            head.addWidget(ed, 1)
            QTimer.singleShot(0, lambda w=ed: self._enter_edit(w))
        else:
            nm = self._lab(t["name"], "h1")
            # 内容默认留空＝不预设：没填的就不显示（别兜一个假的"抖音 · 目标 7 张"）
            bits = []
            if (t.get("form") or "").strip():
                bits.append(t["form"].strip())
            if int(t.get("target_count") or 0) > 0:
                bits.append("目标 %d 张" % int(t["target_count"]))
            bits.append("素材数量不预设")
            nm.setToolTip(" · ".join(bits))
            head.addWidget(nm, 1)
            rn = self._btn("改期名", "plain")
            rn.setFixedWidth(66)
            rn.clicked.connect(self._edit_topic_now)
            head.addWidget(rn)
        sw = self._btn("切换", "plain")
        sw.setFixedWidth(58)
        sw.setToolTip("把它挂起，回到无主题态（这时才能起新的一期）")
        sw.clicked.connect(self.app.detach_topic)
        head.addWidget(sw)
        lay.addLayout(head)
        self._payoff_line(lay, s)

    def _payoff_line(self, lay, s):
        """payoff 压成一行（不再独占一张卡）。「改」就地编辑。"""
        t = s["topic"]
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._lab("PAYOFF", "cap"))
        if self._edit_payoff:
            ed = _InlineEdit(self._cancel_inline)
            ed.setObjectName("editpayoff")
            ed.setText(t["payoff"] or "")
            ed.setPlaceholderText("一句能被记住的断言（不是提问、不是标题）")
            ed.returnPressed.connect(lambda _e=None, w=ed: self._commit_payoff(w))
            row.addWidget(ed, 1)
            QTimer.singleShot(0, lambda w=ed: self._enter_edit(w))
        else:
            row.addWidget(self._lab(t["payoff"] or "（未填）", "fg2"), 1)
            pe = self._btn("改", "plain")
            pe.setFixedWidth(40)
            pe.setToolTip("改这一期最想让人记住的那一句")
            pe.clicked.connect(self._edit_payoff_now)
            row.addWidget(pe)
        lay.addLayout(row)

    def _edit_topic_now(self):
        self._edit_topic = True
        self.refresh()

    def _commit_topic(self, ed):
        name = ed.text().strip()
        self._edit_topic = False
        if name and name != self.app.topic()["name"]:
            self.app.rename_topic(name)
        QTimer.singleShot(0, self.refresh)

    def _seed_head(self, lay, s):
        """立项入口。刻意不给任何预置的期名/排期 —— 叫什么由你打，别无中生有。"""
        n = len(s["inbox_items"])
        f, v = self._card()
        cap = self._mk("还没有主题", "cap")
        cap.setToolTip(("灵感池里有 %d 条了 —— 给这一期起个名，就把它们一起带进来。" % n) if n
                       else "先随手记点什么，或者直接给这一期起个名。")
        v.addWidget(cap)
        ed = QLineEdit()
        ed.setPlaceholderText("这一期讲什么？（例：MCP 到底创新在哪）")
        ed.setClearButtonEnabled(True)
        v.addWidget(ed)
        self.topic_input = ed
        txt = "立项" + ("（带入这 %d 条）" % n if n else "")
        b = self._btn(txt, "primary")
        b.setEnabled(bool(ed.text().strip()))
        ed.textChanged.connect(lambda _t, bb=b: bb.setEnabled(bool(_t.strip())))
        ed.returnPressed.connect(self._start_from_inbox)
        b.clicked.connect(self._start_from_inbox)
        v.addWidget(b)
        lay.addWidget(f)

    # ---------------- 聊 · 和 AI 一起想这一期怎么讲（也能用它改结构）----------------
    def _chat(self, lay, s):
        log = self.app.chat_log(s["topic"]["id"])
        f, v = self._card("chatcard")
        head = QHBoxLayout()
        head.addWidget(self._mk("聊 · 和 AI 一起想这一期怎么讲", "cap"), 1)
        head.addWidget(self._qmark("chat",
            "说思路，不用管格式 —— 比如「我想从『成本』这个角度切」「第 2 段太笼统了」。"
            "它接话；要动结构的话会先列出来，你点「照做」才动。"
            "它话里带的网址会变成「打开 域名」按钮，点了直接开浏览器；"
            "一条里不止两个的话，点「还有 N 条」摊开——一条一行挑。"))
        v.addLayout(head)
        self._help_line(v, "chat",
                        "说思路，不用管格式；要动结构会先列出来，点了「照做」才动。")
        grew = len(log) > self._chat_seen     # 来了新消息：这次刷新滚到底，并给它一个入场动效
        if grew:
            self._chat_anchor = True
        self._chat_seen = len(log)
        # 历史**默认只露最近一轮**（1 问 1 答）；点开才全量（§2.33）
        total = len(log)
        if total > 2:
            bar = self._btn("收起历史" if self._hist_open
                            else "展开历史（%d 条）" % (total - 2), "plain")
            bar.setObjectName("hist_toggle")
            bar.setToolTip("收起来只留最近一轮" if self._hist_open else "点开看这一期前面聊过的")
            bar.clicked.connect(self._toggle_hist)
            v.addWidget(bar)
            show = log[-40:] if self._hist_open else log[-2:]
        else:
            show = log
        self._live_rich = None
        for i, m in enumerate(show):
            box = self._bubble(m)
            v.addWidget(box)
            if grew and i == len(show) - 1 and m.get("role") != "sys":
                self._fade_in(box)            # 最新那条淡入（只动它，别每条都动）
        # 正在流式吐的那句（还没落库）：直连它、就地改，不整面板重建
        live = s.get("live") or ""
        if s.get("chat_busy"):
            if live:
                box, rich = self._live_bubble(live)
                self._live_rich, self._live_text = rich, live
                v.addWidget(box)
            else:
                v.addWidget(ThinkingDots("正在想"))
            if ANIMATE:
                self._start_caret()
        else:
            self._stop_caret()
        self.chat_input = QPlainTextEdit()
        self.chat_input.setPlaceholderText("和它说点什么…（Enter 换行，Ctrl+Enter 发送）")
        self.chat_input.setFixedHeight(56)
        self.chat_input.setEnabled(not s.get("chat_busy"))
        v.addWidget(self.chat_input)
        row = QHBoxLayout()
        row.addStretch(1)
        if s.get("chat_busy"):
            st = self._btn("停", "plain")
            st.setObjectName("chat_stop")
            st.setToolTip("别等了 —— 已经在吐的那句留着，不再往下说")
            st.clicked.connect(lambda: self.app.stop_stream())
            row.addWidget(st)
        b = self._btn("说给它", "primary")
        b.setEnabled(not s.get("chat_busy"))
        b.clicked.connect(self._say)
        row.addWidget(b)
        v.addLayout(row)
        lay.addWidget(f)

    # ---- §2.33 对话体验：历史折叠 / 流式活体 / 光标闪烁 / 入场淡入 ----
    def _toggle_hist(self):
        self._hist_open = not self._hist_open
        self.refresh()

    def _fade_in(self, w):
        """最新那条消息淡入 —— 只给"新来的"那一块，别每条每次刷新都重播。"""
        if not ANIMATE:
            return
        eff = QGraphicsOpacityEffect(w)
        w.setGraphicsEffect(eff)
        a = QPropertyAnimation(eff, b"opacity", w)
        a.setDuration(240)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        a.start()
        w._fade_anim = a          # 保引用，别被 GC 掉（动效会中途消失）

    def _start_caret(self):
        if self._caret_tm is None:
            self._caret_tm = QTimer(self)
            self._caret_tm.timeout.connect(self._blink_caret)
            self._caret_tm.start(520)

    def _stop_caret(self):
        if self._caret_tm is not None:
            self._caret_tm.stop()
            self._caret_tm.deleteLater()
            self._caret_tm = None
        self._caret = True

    def _blink_caret(self):
        self._caret = not self._caret
        self.push_live(self._live_text)

    def push_live(self, text):
        """流式增量：**只更新那一句活体**，不整面板重建（§2.33）。"""
        self._live_text = text or ""
        if self._live_rich is None:
            self.refresh()        # 还没有活体（刚换期/刚展开）→ 重建一次把它建出来
            return
        try:
            self._live_rich.set_html(md_html(self._live_text, caret=self._caret))
        except RuntimeError:
            return
        try:                      # 边流边钉到底
            sb = self.scroll.verticalScrollBar()
            sb.setValue(sb.maximum())
        except RuntimeError:
            pass

    def _live_bubble(self, text):
        """正在流式吐的那条 AI 消息（和落库后的 _bubble 长得一样，但没有动作/复制）。"""
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        body = QWidget()
        body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        body.setObjectName("turnai")
        bv = QVBoxLayout(body)
        bv.setContentsMargins(12, 0, 0, 0)
        bv.setSpacing(4)
        hd = QHBoxLayout()
        hd.setSpacing(6)
        hd.addWidget(self._mk("AI", "whoai"))
        hd.addStretch(1)
        bv.addLayout(hd)
        rich = RichText(md_html(text, caret=self._caret), app=self.app)
        rich.setObjectName("richturn")
        bv.addWidget(rich)
        v.addWidget(body)
        return box, rich

    def _bubble(self, m):
        """一条对话 —— A·纪律：不用气泡，改「分组块 + 左侧发丝线」（§2.24）。

        层次靠角色小标 + 一条 1px 竖线读出来，不靠色块 —— 色块一多就变成装饰了。
        动作永远靠右、跟触发条同一基线；正文里的网址默认露 2 条，
        多的点「还有 N 条」摊成一条一行挑（§2.23）。
        """
        role = m.get("role") or "ai"
        text = m.get("text") or ""
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        if role == "sys":
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(icons.icon_label("dot", 6, FG3))
            row.addWidget(self._lab(text, "dim"), 1)
            v.addLayout(row)
            return box

        you = role == "you"
        body = QWidget()
        # QLabel 之外的控件也要显式打开 WA_StyledBackground，QSS 的 border 才画
        body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        body.setObjectName("turnyou" if you else "turnai")
        bv = QVBoxLayout(body)
        bv.setContentsMargins(12, 0, 0, 0)     # 让出左边那条发丝线
        bv.setSpacing(4)
        hd = QHBoxLayout()
        hd.setSpacing(6)
        hd.addWidget(self._mk("你" if you else "AI", "whoyou" if you else "whoai"))
        hd.addWidget(self._copy_btn(text))
        hd.addStretch(1)
        bv.addLayout(hd)
        rt = RichText(md_html(text), app=self.app)   # 回复按 Markdown 渲染（§2.33）
        rt.setObjectName("richturn")
        bv.addWidget(rt)
        self._link_row(bv, self._urls(text))

        acts = m.get("actions") or []
        if not you and acts:
            if m.get("done"):
                r0 = QHBoxLayout()
                r0.setSpacing(6)
                r0.addWidget(icons.icon_label("check", 12, GREEN))
                r0.addWidget(self._lab("已照做：" + (m.get("summary") or "")
                                       + "（点上面「撤回」可回退）", "dim"), 1)
                bv.addLayout(r0)
            else:
                bv.addWidget(self._lab("它会动这些：" + "；".join(a["text"] for a in acts), "dim"))
                r2 = QHBoxLayout()                 # 动作永远靠右，跟触发条同一基线
                r2.addStretch(1)
                b = self._btn("照做", "ghostgold")
                b.clicked.connect(lambda _, mm=m: self.app.apply_actions(mm))
                r2.addWidget(b)
                bv.addLayout(r2)
        v.addWidget(body)
        return box

    def _say(self):
        if self.chat_input is None:
            return
        text = self.chat_input.toPlainText().strip()
        if not text:
            return
        self.chat_input.setPlainText("")
        self.app.say(text)
        self.refresh()

    def _target_beat(self):
        """下拉选的是哪个目标：0（灵感池）→ None；i+1 → 第 i 颗球的 id。

        直接取 id，不再拿下标去猜第几段 —— 池子里有"未编排"的球，
        下标算术一旦错位就会把素材挂到别的球上。
        """
        idx = self.combo.currentIndex() if self.combo else 0
        if idx <= 0 or not self._balls:
            return None
        return self._balls[min(idx - 1, len(self._balls) - 1)]["id"]

    def _pick_files(self):
        """选文件收进来。落在哪个目标，跟着下拉走 —— 和文字素材同一套目标。"""
        balls = self._balls
        hint = "图片 / 视频 / 录音 / 长文都行"
        if balls and self.combo and self.combo.currentIndex() > 0:
            i = min(self.combo.currentIndex() - 1, len(balls) - 1)
            hint = "将会挂到「%s」：" % (balls[i]["label"] or "（还没写）")[:24]
        paths, _ = QFileDialog.getOpenFileNames(
            self, "收素材 —— " + hint, os.path.expanduser("~"),
            "所有文件 (*.*);;图片 (*.png *.jpg *.jpeg *.gif *.webp *.bmp);;"
            "视频 (*.mp4 *.mov *.mkv *.avi *.webm);;音频 (*.mp3 *.wav *.m4a *.flac)")
        if paths:
            self.app.import_files(paths, self._target_beat())
            self.refresh()

    # ---------------- 拖文件到面板上就收进来 ----------------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls()]
        paths = [p for p in paths if p]
        if paths:
            self.app.import_files(paths, self._target_beat())
            self.refresh()
            e.acceptProposedAction()

    # ---------------- 素材一行（文字 / 文件；图片带缩略图）----------------
    def _mat_line(self, m, cut=44):
        """素材怎么显示：文件素材带缩略图 + 「打开」；带 URL 的素材给「打开源」；
        纯文字就是一行字。"""
        label = "［%s］" % brain.kind_name(m.get("kind"))
        path = (m.get("path") or "").strip()
        src = (m.get("src") or "").strip()
        body = (m.get("body") or "").strip() or "（无题）"
        is_url = src.startswith("http://") or src.startswith("https://")
        if not path and not is_url:
            return self._lab("%s%s" % (label, body[:cut]), "dim")
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        if m.get("kind") == "image" and path:
            pm = QPixmap(path)
            if not pm.isNull():
                thumb = QLabel()
                thumb.setPixmap(pm.scaled(64, 44,
                                          Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
                thumb.setFixedSize(66, 46)
                thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
                thumb.setStyleSheet("border:1px solid %s;border-radius:4px;" % T.HAIR)
                h.addWidget(thumb)
        h.addWidget(self._lab("%s%s" % (label, body[:cut]), "dim"), 1)
        if path:
            b = self._btn("打开", "plain")
            b.setFixedWidth(48)
            b.setToolTip(path)
            b.clicked.connect(lambda _, p=path: self.app.open_url(p))
            h.addWidget(b)
        if is_url:
            b = self._btn("打开源", "plain")
            b.setFixedWidth(58)
            b.setToolTip(src)
            b.clicked.connect(lambda _, u=src: self.app.open_url(u))
            h.addWidget(b)
        return box

    # ---------------- 「喂」入口（唯一常驻的动作，位置永远不变）----------------
    def _feedbar(self, lay, s):
        f, v = self._card("nowcard")
        cap = self._mk("喂", "cap")
        cap.setToolTip("想到什么就喂进来，先不用想挂哪一段")
        v.addWidget(cap)
        balls = s.get("balls") or []
        self._balls = balls
        self.combo = QComboBox()
        self.combo.addItem("灵感池（不挂靠）")          # index 0 恒定 = 不挂靠
        for b in balls:
            tag = ("第 %d 段" % b["n"]) if b["n"] else "未编排"
            self.combo.addItem("%s：%s" % (tag, (b["label"] or "（还没写）")[:20]))
        v.addWidget(self.combo)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("喂素材 —— 一句话 / 一个想法 / 一段原文都行（Ctrl+Enter）")
        self.input.setFixedHeight(74)
        v.addWidget(self.input)
        self._submit_fn = self._feed
        row = QHBoxLayout()
        row.setSpacing(8)
        fbtn = self._btn("文件", "plain")
        fbtn.setIcon(icons.icon("plus", 12, FG3))
        fbtn.setToolTip("把图片 / 视频 / 录音 / 长文收进来（复制进 assets/，原件不动）"
                        "—— 也可以直接拖文件到面板上")
        fbtn.clicked.connect(self._pick_files)
        row.addWidget(fbtn)
        b = self._btn("喂进去", "primary")
        b.clicked.connect(self._feed)
        row.addWidget(b, 1)
        v.addLayout(row)
        lay.addWidget(f)

    # ---------------- 搜素材（§2.22，免 key）：结果可收进素材 + 直达原文 ----------------
    def _search_section(self, lay, s):
        f, v = self._card("searchcard")
        head = QHBoxLayout()
        head.addWidget(self._mk("搜素材", "cap"), 1)
        head.addWidget(self._qmark("search",
            "免 key 搜索（Bing 免费通道：先试 RSS，拿不到再退网页解析）。"
            "结果点「收进素材」就进这一期（还没指派）；点「打开源」直接开浏览器到原文。"))
        v.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(8)
        ed = QLineEdit()
        ed.setPlaceholderText("搜什么？（例：MCP 协议 为什么需要）")
        ed.setText(s.get("search_query") or "")
        ed.returnPressed.connect(lambda _e=None, w=ed: self._do_search(w.text()))
        self._search_input = ed
        row.addWidget(ed, 1)
        b = self._btn("搜一下", "ghostgold")
        b.setEnabled(not s.get("search_busy"))
        b.clicked.connect(lambda _, w=ed: self._do_search(w.text()))
        row.addWidget(b)
        v.addLayout(row)

        # AI 按期推荐搜法（§2.30）：搜索排序不是我们的强项，AI 强在懂这一期 ——
        # 让它给几条能直接点的查询词，点一下就填进框里走上面的搜索。
        srow = QHBoxLayout()
        srow.setSpacing(8)
        sg = self._btn("AI 帮我想几个搜法", "plain", align_left=True)
        sg.setToolTip("让模型看这一期在讲什么，给几条能直接搜的查询词 —— 点一下就搜。"
                      "选中一颗球时，只推这一段的搜法。")
        sg.setEnabled(not s.get("suggest_busy"))
        sg.clicked.connect(lambda _=False: self._suggest())
        srow.addWidget(sg)
        srow.addStretch(1)
        v.addLayout(srow)
        if s.get("suggest_busy"):
            v.addWidget(self._lab("正在想搜法…", "dim"))
        qs = s.get("suggest_queries") or []
        if qs and not s.get("suggest_busy"):
            crow = QHBoxLayout()
            crow.setSpacing(6)
            crow.addWidget(self._lab("试试：", "dim"))
            for q in qs:
                on = (s.get("search_query") or "").strip() == q
                crow.addWidget(self._chip(q, "点一下就用这条去搜",
                                          lambda _=False, w=q: self._do_search(w), on=on))
            crow.addStretch(1)
            v.addLayout(crow)

        if s.get("search_busy"):
            v.addWidget(self._lab("搜着呢…", "gold"))
        err = (s.get("search_err") or "").strip()
        if err and not s.get("search_busy"):
            v.addWidget(self._lab("没搜到（%s）—— 换个词，或过会儿再试。" % err, "dim"))

        hits = s.get("search_hits") or []
        if hits and not s.get("search_busy"):
            v.addWidget(self._lab("搜到 %d 条。收进来的都带着原文链接。" % len(hits), "dim"))
        for h in hits:
            v.addWidget(self._lab(h["title"], "fg2"))
            if h.get("snippet"):
                v.addWidget(self._lab(h["snippet"][:120], "dim"))
            r = QHBoxLayout()
            r.setSpacing(6)
            r.addWidget(self._lab(self._domain(h["url"]), "dim"), 1)
            ob = self._btn("打开源", "plain")
            ob.setToolTip(h["url"])
            ob.clicked.connect(lambda _, u=h["url"]: self.app.open_url(u))
            r.addWidget(ob)
            fb = self._btn("收进素材", "ghostgold")
            fb.setToolTip("收进这一期（还没指派）—— 之后在「未指派素材」里指给某一段")
            fb.clicked.connect(lambda _, hh=h: self._add_hit(hh))
            r.addWidget(fb)
            v.addLayout(r)
        lay.addWidget(f)

    def _do_search(self, text):
        self.app.search_web(text)
        self.refresh()

    def _suggest(self):
        """让 AI 按当前这一期推荐搜法；选了球就带上焦点（§2.30）。"""
        focus = None
        if self._sel_bid is not None and self._sel_bid >= 0:
            b = next((x for x in (self._balls or []) if x.get("id") == self._sel_bid), None)
            if b:
                focus = {"id": b.get("id"), "question": b.get("label") or ""}
        self.app.suggest_queries(focus)
        self.refresh()

    def _add_hit(self, h):
        self.app.add_link(h.get("title") or h.get("url"), h.get("url"), h.get("snippet") or "")
        self.refresh()

    # ---------------- AI 看着说（压成一行，why 收进悬停）----------------
    def _hint_line(self, lay, s):
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(icons.icon_label("arrow_right", 12, GOLD))
        row.addWidget(self._lab(s["next"], "gold"), 1)
        row.addWidget(self._qmark("hint", s["why"]))
        lay.addLayout(row)

    # ---------------- 触发条（常驻但极窄：点开才展开对应抽屉）----------------
    def _triggerbar(self, lay, s):
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(self._chip("喂", "喂素材进来：文字 / 文件 / 直接拖到面板上都行",
                                 self._toggle_feed, self._show_feed,
                                 key="feed", icon_name="plus"))
        row.addWidget(self._chip("搜索", "免 key 搜素材（Bing 免费通道）—— 结果可一键收成素材、直达原文",
                                 self._toggle_search, self._show_search, key="search"))
        if s.get("has_topic"):
            n = len(s.get("balls") or [])
            row.addWidget(self._chip("池子 · %d" % n,
                                     "叙事结构：一颗球＝一个段落。拖球只挪位置，顺序只看球心的编号。",
                                     self._toggle_pool_view, self._show_pool, key="pool"))
            props = s.get("proposals") or []
            if props:
                row.addWidget(self._chip("提议 · %d" % len(props),
                                         "AI 想对结构做的增 / 并 / 撤 —— 动不动由你说了算",
                                         self._toggle_props, self._show_props, key="props"))
        row.addStretch(1)
        undo = s.get("undo_label") or ""
        if undo:
            row.addWidget(self._chip("撤回",
                                     "撤回最近一次「%s」—— 结构回到动它之前，素材一条不丢" % undo,
                                     self.app.undo, key="undo", icon_name="undo"))
        if s.get("has_topic"):
            self._cap_chip(row, s)
        lay.addLayout(row)

    def _cap_chip(self, row, s):
        """收口＝一个小勾按钮（悬停说明上限），旁边跟估算百分比。

        封顶后换成同位置一枚可点的「已确认足够」—— 点一下取消封顶（§2.21）：
        封顶本来就是人按下的，人当然也能取消。
        """
        if s.get("capped"):
            b = QPushButton("已确认足够")
            b.setObjectName("capdone")
            b.setIcon(icons.icon("check", 12, GREEN, GREEN))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip("完成与否由你定义。点一下取消封顶，可以接着改。")
            b.clicked.connect(self.app.uncap)
            row.addWidget(b)
            return
        if not s.get("beats"):
            return
        pct = QLabel("≈ %d%%" % s["pct"])
        pct.setStyleSheet("color:%s;font-size:11.5px;"
                          "font-family:'Cascadia Mono','Consolas',monospace;" % FG3)
        pct.setToolTip("完成度估算 · 上限钉在 92%，最后一格由你点。")
        row.addWidget(pct)
        b = icons.IconButton("check", 14, color=GOLD, active_color=GOLD,
                             obj="cap", tip="够了，收口 —— 上限由你定")
        b.clicked.connect(self.app.cap)
        row.addWidget(b)

    # ---------------- AI 提议（结构动不动，人裁决）----------------
    def _proposals(self, lay, s):
        props = s.get("proposals") or []
        if not props:
            return
        f, v = self._card("proposal")
        v.addWidget(self._mk("AI 提议 · 结构要不要动，你说了算", "cap"))
        for p in props:
            v.addWidget(self._lab(p["text"], "fg2"))
            row = QHBoxLayout()
            row.setSpacing(8)
            ok = self._btn(p["action"], "propok")
            ok.clicked.connect(lambda _, pp=p: self.app.apply_proposal(pp))
            row.addWidget(ok)
            no = self._btn("先不用", "plain")
            no.clicked.connect(lambda _, pp=p: self.app.dismiss_proposal(pp))
            row.addWidget(no)
            row.addStretch(1)
            v.addLayout(row)
        lay.addWidget(f)

    # ---------------- 叙事结构 ＝ 一口池子（§2.18，常驻，可同时确认多段）----------------
    def _pool_section(self, lay, s):
        balls = s.get("balls") or []
        self._balls = balls
        n_pl = len([b for b in balls if b["n"]])
        f, v = self._card()
        head = QHBoxLayout()
        head.addWidget(self._mk("池子 · 这一期", "cap"), 1)
        head.addWidget(self._lab("共 %d 颗 · 已编排 %d · 未编排 %d"
                                 % (len(balls), n_pl, len(balls) - n_pl), "dim"))
        head.addWidget(self._qmark("basin",
            "拖球只挪位置（往下＝更深更暗）—— 顺序只看球心那颗金徽上的数字。"
            "双击空白水面＝投一颗新球。"))
        v.addLayout(head)

        pool = Basin()
        pool.set_balls(balls)
        pool.set_sel(self._sel_bid)
        pool.picked.connect(self._pick_ball)
        pool.moved.connect(self._move_ball)
        pool.added.connect(self._add_ball)
        self._pool = pool
        v.addWidget(pool)

        row = QHBoxLayout()
        row.setSpacing(8)
        add = self._btn("投一颗新球（还没编排）", "ghostgold")
        add.clicked.connect(self._add_ball)
        row.addWidget(add)
        row.addStretch(1)
        v.addLayout(row)
        lay.addWidget(f)
        self._ball_detail(lay, s, balls)

    def _ball_detail(self, lay, s, balls):
        """选中那颗球的操作卡：措辞 / 短评 / 编号 / 素材 / 深挖。

        以前这些散在节拍行里（行内＋↑↓改×），现在集中在选中态 ——
        池子里一颗球太小，装不下一排按钮。
        """
        b = next((x for x in balls if x["id"] == self._sel_bid), None)
        if b is None:
            f, v = self._card()
            head = QHBoxLayout()
            head.addWidget(self._mk("点一颗球", "cap"), 1)
            head.addWidget(self._qmark("ball",
                "点池子里的任意一颗球：看它攒了什么、改措辞、排编号、"
                "详细论证这一点、或者就这一点发散。"))
            v.addLayout(head)
            need = [x for x in balls if x["n"] and x["status"] == "supported"]
            if need:
                # 堆了素材还没确认的球最容易漏 —— 给一个直接跳过去的入口
                v.addWidget(self._lab("有 %d 颗球堆了素材、还没确认。" % len(need), "fg2"))
                bb = self._btn("看下一颗待确认的球", "ghostgold")
                bb.clicked.connect(self._next_supported)
                v.addWidget(bb)
            lay.addWidget(f)
            return

        placed = b["n"] is not None
        f, v = self._card()
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(self._mk(("第 %d 段" % b["n"]) if placed else "还没编排", "gold"), 1)
        pill = QLabel(STXT.get(b["status"], ""))
        pill.setStyleSheet("color:%s;font-size:11px;" % DOT.get(b["status"], FG3))
        head.addWidget(pill)
        v.addLayout(head)

        # 措辞（就地改，回车提交 / Esc 退出）
        if self._edit_bid == b["id"]:
            ed = _InlineEdit(self._cancel_edit)
            ed.setText(b["label"])
            ed.setPlaceholderText("这一段要回答什么？")
            ed.setObjectName("editq")
            ed.returnPressed.connect(lambda bid=b["id"], e=ed: self._commit_question(bid, e))
            v.addWidget(ed)
            QTimer.singleShot(0, lambda e=ed: self._enter_edit(e))
        else:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(self._lab(b["label"] or "（还没写）", "fg2"), 1)
            ebtn = self._btn("改措辞", "plain")
            ebtn.setFixedWidth(60)
            ebtn.clicked.connect(lambda _, bid=b["id"]: self._edit_beat(bid))
            row.addWidget(ebtn)
            v.addLayout(row)

        # 每个球一句短评（本版由本地规则给，模型版下一版接）
        note = (b.get("note") or "").strip()
        if note:
            v.addWidget(self._lab("短评：" + note, "dim"))

        # 编号：填数字＝排到第几位；留空＝退出编排（位置不代表顺序，只认这个数）
        nrow = QHBoxLayout()
        nrow.setSpacing(8)
        num = QLineEdit()
        num.setObjectName("editnum")
        num.setFixedWidth(58)
        num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        num.setText(str(b["n"]) if placed else "")
        num.setPlaceholderText("第几位")
        num.setToolTip("填数字＝排到第几位（顺序只认它）；留空＝退出编排")
        nrow.addWidget(self._lab("编号", "dim"))
        nrow.addWidget(num)
        ok = self._btn("排到这一位", "ghostgold")
        ok.clicked.connect(lambda _, bid=b["id"], w=num: self._set_num(bid, w.text()))
        num.returnPressed.connect(lambda bid=b["id"], w=num: self._set_num(bid, w.text()))
        nrow.addWidget(ok, 1)
        v.addLayout(nrow)

        acts = QHBoxLayout()
        acts.setSpacing(6)
        if placed:
            up = self._icon_btn("arrow_up", "miniup", "往前挪一格",
                                lambda: self._move(b["id"], -1))
            dn = self._icon_btn("arrow_down", "minidown", "往后挪一格",
                                lambda: self._move(b["id"], 1))
            acts.addWidget(up)
            acts.addWidget(dn)
            acts.addWidget(self._mini_btn("撤下", "撤下这一段（素材退回未指派）",
                                          lambda: self._del(b["id"])))
            if b["status"] == "supported":
                lk = self._btn("够了，这段讲清了", "green")
                lk.clicked.connect(lambda _, bid=b["id"]: self._lock(bid))
                acts.addWidget(lk)
            elif b["status"] == "locked":
                un = self._btn("撤销确认", "plain")
                un.setToolTip("改回「待确认」—— 确认错了随时改回来（§2.21）")
                un.clicked.connect(lambda _, bid=b["id"]: self.app.unlock_beat(bid))
                acts.addWidget(un)
        else:
            acts.addWidget(self._mini_btn("撤下", "撤下这颗球（素材退回未指派）",
                                          lambda: self._del(b["id"])))
        acts.addStretch(1)
        v.addLayout(acts)

        for m in (b.get("mats_raw") or []):
            v.addWidget(self._mat_line(m, 66))
        self._deep_area(v, b, s)
        lay.addWidget(f)

    def _icon_btn(self, name, obj, tip, handler, size=14):
        """自绘图标小按钮（§2.27）：不用字体字形，用 QPainter 画的几何。"""
        b = icons.IconButton(name, size, obj=obj, tip=tip)
        b.clicked.connect(handler)
        return b

    def _mini_btn(self, text, tip, handler):
        b = QPushButton(text)
        b.setObjectName("plain")
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFixedWidth(52 if len(text) > 1 else 30)
        b.clicked.connect(handler)
        return b

    # ---------------- 池子上的动作 ----------------
    def _pick_ball(self, bid):
        self._sel_bid = bid
        self.refresh()

    def _move_ball(self, bid, px, py):
        """落点进库，但**不刷面板** —— 刷新会把正在处理这次拖动的池子拆掉（涟漪就没了）。"""
        self.app.move_ball(bid, px, py)

    def _add_ball(self):
        """投一颗新球，并立刻进改措辞态 —— 投了就说它要讲什么，不多一步。"""
        bid = self.app.add_ball()
        if bid:
            self._sel_bid = bid
            self._edit_bid = bid
            self._edit_new = True       # 新球：没写内容就当没投过
        self.refresh()

    def _set_num(self, bid, txt):
        """改编号：填数字＝排到第几位（1 起）；留空＝退出编排。"""
        txt = (txt or "").strip()
        if not txt:
            self.app.unplace_ball(bid)
            self.refresh()
            return
        try:
            i = int(txt)
        except ValueError:
            self.refresh()              # 填了不是数字的东西：什么都不动
            return
        self.app.place_ball(bid, max(0, i - 1))
        self.refresh()

    def _next_supported(self):
        """选中跳到下一颗"堆了素材、还没确认"的球（没有就不动）。"""
        nxt = next((x["id"] for x in (self.app.state().get("balls") or [])
                    if x["n"] and x["status"] == "supported"), None)
        if nxt is not None:
            self._sel_bid = nxt
            self.refresh()

    def _lock(self, bid):
        """确认这一段。确认完自动把选中挪到下一颗「待确认」的球 ——
        操作卡是跟着选中走的，不自动跳的话「逐段确认」就得点一次、再点一次球。"""
        self.app.lock_beat(bid)
        nxt = next((x["id"] for x in (self.app.state().get("balls") or [])
                    if x["n"] and x["status"] == "supported"), None)
        self._sel_bid = nxt if nxt is not None else bid
        self.refresh()

    def _edit_payoff_now(self):
        self._edit_payoff = True
        self.refresh()

    def _commit_payoff(self, ed):
        txt = ed.text().strip()
        self._edit_payoff = False
        if txt:
            self.app.set_payoff(txt)
        QTimer.singleShot(0, self.refresh)

    def _unassigned(self, lay, s):
        if not s["unassigned"]:
            return
        f, v = self._card()
        cap = self._lab("未指派素材", "cap")
        cap.setToolTip("指派到某一段之后才算支撑")
        v.addWidget(cap)
        for m in s["unassigned"]:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(self._mat_line(m, 34), 1)
            bb = self._btn("指派到…")
            bb.setFixedWidth(84)
            bb.clicked.connect(lambda _, mm=m: self._assign_menu(mm, bb))
            row.addWidget(bb)
            v.addLayout(row)
        lay.addWidget(f)

    # ---------------- 封顶之后：成片稿（分镜表）+ 发布页 ----------------
    def _output(self, lay, s):
        if not s["capped"]:
            return
        self._storyboard(lay, s)

        f, v = self._card()
        cap = self._lab("一键跳发布页", "cap")
        cap.setToolTip("只打开页面，发送永远由你本人完成")
        v.addWidget(cap)
        for name, url in config.PUBLISH:
            b = self._btn(name)
            b.clicked.connect(lambda _, u=url: self.app.open_url(u))
            v.addWidget(b)
        lay.addWidget(f)

    # ---------------- 成片稿：这是要交出去的那个物件 ----------------
    def _storyboard(self, lay, s):
        rows = brain.storyboard(s["topic"], s["beats"], s["mbb"])
        f, v = self._card()
        head = QHBoxLayout()
        head.addWidget(self._mk("成片稿 · 分镜表", "cap"), 1)
        head.addWidget(self._qmark("sb",
            "每一行 = 一段：口播稿怎么念、画面要什么、手上有没有。"
            "素材文件都在 assets/ 里，可以直接拖进剪辑软件。"))
        v.addLayout(head)
        if s.get("sb_busy"):
            v.addWidget(self._lab("模型正在写逐段口播稿…（先看得到的是本地规则那版）", "gold"))

        for r in rows:
            head = QHBoxLayout()
            head.setSpacing(8)
            head.addWidget(self._mk("%d" % r["seq"], "gold"))
            head.addWidget(self._lab(r["question"], "fg2"), 1)
            mark = "有 %d 个素材" % r["have"] if r["have"] else "还没素材"
            head.addWidget(self._lab(mark, "dim"))
            v.addLayout(head)
            v.addWidget(self._lab("口播稿：" + (r["script"] or "（还没有 —— 喂点素材，它会写）"),
                                  "fg2"))
            v.addWidget(self._lab("画面：" + (r["shot"] or "（待定）"), "dim"))
            if r["files"]:
                v.addWidget(self._lab("文件：" + "、".join(r["files"]), "dim"))

        row = QHBoxLayout()
        row.setSpacing(8)
        b1 = self._btn("复制分镜表", "plain")
        b1.clicked.connect(self._copy_storyboard)
        row.addWidget(b1)
        b2 = self._btn("导出 Markdown…", "ghostgold")
        b2.clicked.connect(self._export_storyboard)
        row.addWidget(b2)
        row.addStretch(1)
        b3 = self._btn("打开素材文件夹", "plain")
        b3.clicked.connect(lambda: self.app.open_assets_dir())
        row.addWidget(b3)
        v.addLayout(row)
        if not s.get("sb_busy"):
            b4 = self._btn("重写成片稿（让模型按现在的素材再写一版）", "plain")
            b4.clicked.connect(lambda: (self.app._kick_storyboard(), self.refresh()))
            v.addWidget(b4)
        lay.addWidget(f)

    def _copy_storyboard(self):
        txt = self.app.storyboard_text()
        if not txt:
            return
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(txt)
            self.app._notify("分镜表已复制到剪贴板。")

    # ---------------- 编排包（M4）：只编排，出片交给别的软件 ----------------
    def _movie(self, lay, s):
        pk = getattr(self.app, "_pk", None) or {}
        busy = getattr(self.app, "_pk_busy", False)
        f, v = self._card()
        head = QHBoxLayout()
        head.addWidget(self._mk("编排包 · 交给别的软件出片", "cap"), 1)
        head.addWidget(self._qmark("movie",
            "Skein 只做编排，不出片：这一段讲什么、画面是什么、多长、什么顺序 —— "
            "编完导出成施工单，剪映 / 即梦 / 可灵照着做。同一个画面在这支片里只排一次；"
            "缺的画面给提示词，让 AI 去生成。"))
        v.addLayout(head)

        rows = brain.storyboard(s["topic"], s["beats"], s["mbb"])
        tight = [r for r in rows if r["have"] < 2]
        if tight:
            v.addWidget(self._lab(
                "素材偏紧：" + "、".join("第 %d 段 %d 个" % (r["seq"], r["have"])
                                        for r in tight) +
                " —— 不够的部分会以「待生成」进包，提示词一起写好。", "gold"))
        elif rows:
            v.addWidget(self._lab("素材体检：每段都有现成画面。", "fg2"))

        if busy:
            v.addWidget(self._lab("导出中…", "gold"))
        elif pk.get("dir"):
            c = pk.get("count") or {}
            v.addWidget(self._lab(
                "上次导出：%.0fs · %d 个镜头（现成 %d · 待生成 %d）"
                % (pk.get("total", 0), pk.get("n_shots", 0),
                   c.get("material", 0), c.get("generate", 0)), "fg2"))
            if pk.get("jianying"):
                v.addWidget(self._lab("剪映草稿也生成了，直接拷进剪映草稿目录。", "fg2"))
            elif pk.get("jianying_error"):
                v.addWidget(self._lab("剪映草稿这次没生成：%s" % pk["jianying_error"], "gold"))
        for w in (pk.get("warnings") or [])[:3]:
            v.addWidget(self._lab("！ " + w, "gold"))

        row = QHBoxLayout()
        row.setSpacing(8)
        b = self._btn("导出编排包" if not busy else "导出中…",
                      "primary" if not busy else "")
        b.setEnabled(not busy)
        b.clicked.connect(lambda: self.app.export_pack(True))
        row.addWidget(b)
        if pk.get("dir"):
            b2 = self._btn("打开包", "plain")
            b2.clicked.connect(self.app.open_pack_dir)
            row.addWidget(b2)
            if (pk.get("count") or {}).get("generate"):
                bf = self._btn("回填生成画面", "ghostgold")
                bf.setToolTip("把 生成画面/ 里按镜头号命名的图（S1-2.png…）放回时间轴，重出剪映草稿")
                bf.clicked.connect(lambda: self.app.fill_pack())
                row.addWidget(bf)
        v.addLayout(row)

        if pk.get("dir"):
            row2 = QHBoxLayout()
            row2.setSpacing(6)
            for nm, tip in (("分镜表.md", "人读施工单"),
                            ("提示词.md", "给 AI 生图/生视频软件"),
                            ("时间轴.json", "镜头级时间轴"),
                            ("字幕.srt", "带时间码字幕")):
                bb = self._btn(nm, "plain")
                bb.setToolTip(tip)
                bb.clicked.connect(lambda _, n=nm: self.app.open_pack_file(n))
                row2.addWidget(bb)
            row2.addStretch(1)
            v.addLayout(row2)
        lay.addWidget(f)

    def _export_storyboard(self):
        t = self.app.topic()
        if not t:
            return
        safe = "".join(c for c in t["name"] if c not in '\\/:*?"<>|').strip() or "skein"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出分镜表", os.path.join(os.path.expanduser("~"), "Desktop", safe + ".md"),
            "Markdown (*.md);;文本 (*.txt)")
        if path:
            self.app.export_storyboard(path)

    # ---------------- 明细：无主题 ----------------
    def _inbox_detail(self, lay, s):
        items = s["inbox_items"]
        f, v = self._card()
        head = QHBoxLayout()
        head.addWidget(self._mk("灵感池", "cap"), 1)
        if items:
            head.addWidget(self._lab("攒了 %d 条" % len(items), "dim"))
        v.addLayout(head)
        # 池子本体：能看出"在填充"（水位线 + 块从池口落下），点一下才摊开列表
        pool = Pool(self._toggle_pool)
        pool.set_items(items)
        pool.setToolTip("点一下看里面有什么" if items else "空池子 —— 还没决定给谁的东西都先进这儿")
        v.addWidget(pool)
        if self._pool_open:
            for m in items:
                v.addWidget(self._mat_line(m, 54))
        lay.addWidget(f)

    def _toggle_pool(self):
        self._pool_open = not self._pool_open
        self.refresh()

    def _assign_menu(self, m, anchor):
        menu = QMenu(self)
        for b in self._balls:
            tag = ("第 %d 段" % b["n"]) if b["n"] else "未编排"
            act = menu.addAction("%s：%s" % (tag, (b["label"] or "（还没写）")[:22]))
            act.triggered.connect(lambda _, mid=m["id"], bid=b["id"]: self._do_assign(mid, bid))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    # ---------------- 动作 ----------------
    def _dismiss(self):
        try:
            self.app.notice = ""
        except Exception:
            pass
        self.refresh()

    def _start_from_inbox(self):
        """用你打的期名立项。空名字不做任何事（不编默认名）。"""
        ed = getattr(self, "topic_input", None)
        name = ed.text().strip() if ed is not None else ""
        if not name:
            return
        self.app.start_topic(name)
        self.refresh()

    def _feed(self):
        """喂一条素材。目标是哪个由下拉决定：index 0 = 灵感池（不挂靠），i+1 = 第 i 颗球。"""
        v = self.input.toPlainText().strip() if self.input else ""
        if not v:
            return
        self.input.setPlainText("")
        bid = self._target_beat()
        if bid is None:
            self.app.add_inbox(v)                       # 不挂靠：先存进灵感池
        else:
            self.app.feed(bid, v)
        self.refresh()

    def _do_assign(self, mid, bid):
        self.app.assign(mid, bid)
        self.refresh()

    # ---------------- 结构编排（选中那颗球的操作卡里那几个动作）----------------
    def _edit_beat(self, bid):
        self._edit_bid = bid
        self._edit_new = False
        self.refresh()

    def _enter_edit(self, ed):
        """把这一行滚进视野，光标放进去 —— 新段常常落在折叠区外，
        不滚过去的话人会以为"点了没反应"。"""
        self.scroll.ensureWidgetVisible(ed, 0, 24)
        ed.setFocus()
        ed.selectAll()

    def _commit_question(self, bid, ed):
        """Enter 提交。改措辞的刷新要**等这一轮按键处理完**再做：
        刷新会重建整棵子树，连带把这个正在处理按键的输入框删掉。"""
        q = ed.text().strip()
        was_new = self._edit_new
        self._edit_bid = None
        self._edit_new = False
        if not q:
            if was_new:
                self.app.delete_beat(bid, quiet=True)
        else:
            self.app.rename_beat(bid, q)
        QTimer.singleShot(0, self.refresh)

    def _cancel_edit(self):
        bid, was_new = self._edit_bid, self._edit_new
        self._edit_bid = None
        self._edit_new = False
        if was_new and bid is not None:
            self.app.delete_beat(bid, quiet=True)
        QTimer.singleShot(0, self.refresh)

    def _cancel_inline(self):
        """期名 / payoff 的 Esc：退出编辑，不动原值。"""
        self._edit_topic = False
        self._edit_payoff = False
        QTimer.singleShot(0, self.refresh)

    def _editing(self):
        """有任何一个行内编辑在开着吗（决定 Esc 到底是"退出编辑"还是"收起面板"）"""
        return (self._edit_bid is not None or self._edit_topic
                or self._edit_payoff)

    def _move(self, bid, delta):
        self.app.move_beat(bid, delta)
        self.refresh()

    def _del(self, bid):
        self.app.delete_beat(bid)

    # ---------------- 展开后的深挖区：详细论证 / 就这一点发散 ----------------
    def _deep_area(self, v, b, s):
        """点标题展开出来的东西。

        两种"深入"分开做，因为它们要的结果不一样：
        - 详细论证这一点 → 要一段**能站得住的推理**（存进 beat.deep，是这一期的内容）
        - 就这一点发散   → 要几个**能长成新段的方向**（临时候选，选中了才变成段）
        """
        box = QFrame()
        box.setObjectName("deepbox")
        d = QVBoxLayout(box)
        d.setContentsMargins(11, 9, 11, 10)
        d.setSpacing(7)

        busy_d = s.get("deep_bid") == b["id"]
        busy_s = s.get("spread_bid") == b["id"]
        row = QHBoxLayout()
        row.setSpacing(8)
        tip = "只用这一段的素材，撑不住的地方它会直说缺，不编假论据"
        b1 = self._btn("详细论证这一点", "ghostgold")
        b1.setToolTip(tip)
        b1.setEnabled(not busy_d)
        b1.clicked.connect(lambda _, bid=b["id"]: self._deepen(bid))
        row.addWidget(b1)
        b2 = self._btn("就这一点发散", "ghostgold")
        b2.setToolTip(tip)
        b2.setEnabled(not busy_s)
        b2.clicked.connect(lambda _, bid=b["id"]: self._spread(bid))
        row.addWidget(b2)
        row.addStretch(1)
        d.addLayout(row)

        if busy_d:
            d.addWidget(self._lab("正在把这一点的论证补起来…", "gold"))
        deep = (b.get("deep") or "").strip()
        if deep:
            d.addWidget(self._lab(deep, "fg2"))
        elif not busy_d:
            d.addWidget(self._lab("还没有论证 —— 上面两个按钮里挑一个。", "dim"))

        cand = (s.get("spread") or {}).get(b["id"]) or []
        if busy_s:
            d.addWidget(self._lab("正在想可以往外发散什么…", "gold"))
        elif cand:
            d.addWidget(self._mk("发散出来的方向（选中就长成新的一段）", "cap"))
            for t in cand:
                r = QHBoxLayout()
                r.setSpacing(8)
                r.addWidget(self._lab(t, "fg2"), 1)
                up = self._btn("升成一段", "green")
                up.clicked.connect(lambda _, bid=b["id"], tt=t: self._promote(bid, tt))
                r.addWidget(up)
                d.addLayout(r)
        v.addWidget(box)

    def _deepen(self, bid):
        self.app.deepen(bid)
        self.refresh()

    def _spread(self, bid):
        self.app.spread(bid)
        self.refresh()

    def _promote(self, bid, text):
        self.app.promote_spread(bid, text)

    def _submit(self):
        """Ctrl+Enter：光标在对话框里就是"说给它"，否则是"喂进去"。"""
        try:
            if self.chat_input is not None and QApplication.focusWidget() is self.chat_input:
                self._say()
                return
        except RuntimeError:
            pass
        if self._submit_fn:
            self._submit_fn()

    # ---------------- 拖动 ----------------
    # 整块面板都能拖（不只是 48px 的标题栏）。做法是装一个应用级事件过滤器：
    # 面板里绝大多数控件是标签/卡片/空白，它们不吃鼠标，本来会把事件透上去，
    # 但"透上去"这条路要穿过 QScrollArea，能不能到最后面这一步不好保证 ——
    # 过滤器在事件被投递之前就能看到，稳，而且不依赖任何命中测试的边角语义。
    # 吃到鼠标的（按钮 / 输入框 / 下拉 / 滚动条 / 池子里的球）一律跳过，不抢它们的手势。
    _MOUSEY = ("QPushButton", "QLineEdit", "QPlainTextEdit", "QTextEdit",
               "QComboBox", "QAbstractSlider", "QAbstractScrollArea")

    def _press_on_decor(self, w):
        """这次按下是不是落在"装饰面"上（能理解为"拖这张纸"）。

        走到内容根就够了：内容根底下才是那条 QScrollArea —— 只要在到它之前
        没撞上吃鼠标的控件，这次按下就该被理解成"拖面板"。
        """
        root = self.scroll.widget()
        while w is not None and w is not self:
            if isinstance(w, Basin) or any(c in type(w).__name__ for c in self._MOUSEY):
                return False
            if w is root:
                return True
            w = w.parentWidget()
        return w is self

    def eventFilter(self, obj, e):
        t = e.type()
        if t == QEvent.Type.MouseButtonPress and self.isVisible():
            if isinstance(obj, QWidget) and obj.window() is self \
                    and self._press_on_decor(obj):
                self._drag = (e.globalPosition().toPoint(), self.pos(), False)
        elif t == QEvent.Type.MouseMove and self._drag:
            gp, sp, moved = self._drag
            d = e.globalPosition().toPoint() - gp
            if not moved:
                # 和球那边同一套阈值：手抖几像素不算拖，否则一"点"面板就挪位置
                if d.manhattanLength() <= 6:
                    return False
                moved = True
            self._drag = (gp, sp, moved)
            self.move(sp + d)
        elif t == QEvent.Type.MouseButtonRelease and self._drag:
            self._drag = None
            self._clamp()
        return False

    def mousePressEvent(self, e):
        # 什么都不做。按下要不要拖由 eventFilter 决定 —— 被接受不了的按下会一路
        # 冒泡到面板（Strand → viewport → QScrollArea → Panel），这里要是一并处理
        # 就会把过滤器刚记下的拖动状态清掉（实测踩过）。
        pass

    def mouseMoveEvent(self, e):
        pass

    def mouseReleaseEvent(self, e):
        self._clamp()
