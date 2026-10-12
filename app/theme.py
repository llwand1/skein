# -*- coding: utf-8 -*-
"""Skein · 视觉令牌与 QSS

设计取向（为什么不落俗套）：
- **中性色不是中性色**：绝大多数深色界面用冷蓝黑（含纯黑 #000）。这里整条灰阶都掺了
  品牌暖调（hue≈35），画布是暖墨 #100e0b，正文是米白 #f4ede1 而不是纯白。
- **一个强调色，且只用一次**：金 #FFD01F 只出现在「当前那一节 / 主按钮 / 进度」。
  确认色用奶蛙的品牌绿，红/蓝只做语义。
- **A·纪律（v0.14.0，对标 Linear）**：字号只 4 档（15 / 13 / 12 / 11）、间距只用
  4·8·12·16·24；卡片**不描边** —— 层次只靠「1px 发丝线 + 底色三档」读出来；
  对话**不用气泡**，改「分组块 + 左侧发丝线」（见面板 #turnyou / #turnai）。
- **玻璃只属于球、灵感池和那口池子**：面板落定后是哑光的；
  「装着还没归属的东西」那口池子（pool.py，只在还没有主题时出现）与球同一套材质，
  叙事结构那口池子（basin.py）也一样 —— 它们本来就是球那一滴聚起来的地方。
  别在别处再放磨砂玻璃 —— 玻璃一多就变成装饰了。
- **线不再是结构语言**（v0.10.0 起）：原来那条贯穿到底的"一绞线"（panel.Strand）
  已经删掉，顺序改由池子里球心那颗金徽上的数字承担 —— 池子里没有东西可锚了。

踩过的坑（务必遵守）：
- 本机 Qt6 里 `qlineargradient` 在 QPushButton 上不渲染 → 按钮一律纯色
- QLabel 的 QSS 背景需要 WA_StyledBackground（见 panel._mk）
- 父容器 setStyleSheet("background:transparent") 会作用整棵子树，抹掉子控件背景 → 禁用
"""
# ---------- 画布与表面（暖墨）----------
BG = "#100e0b"          # 画布
SURF = "#191612"        # 卡片表面
SURF2 = "#221e18"       # 按钮 / 输入
HAIR = "#332c23"        # 发丝边界
HAIR2 = "#4a4034"       # hover 边界 / 未纺的线

# 兼容旧名
PANEL = SURF
PANEL2 = SURF2
LINE = HAIR
LINE2 = HAIR2

# ---------- 文字（米白，不是纯白）----------
FG = "#f4ede1"
FG2 = "#b6ab97"
FG3 = "#807664"

# ---------- 唯一强调色 + 语义色 ----------
GOLD = "#FFD01F"
GOLD_DIM = "#7a6410"
GOLD2 = "#E8A800"
GREEN = "#9ecf4e"       # 奶蛙的品牌绿，提亮到深底上可读
BLUE = "#86b6e6"
RED = "#e8785f"
AMBER = "#e8a800"
VIOLET = "#b9a0f0"

FONT = "Microsoft YaHei UI"
MONO = "Cascadia Mono"

# 字号四档（A·纪律：仍然只有 4 档，只是整体上移）—— 高 DPI 屏上原来偏小（§2.34）
FS_PANEL = 26      # 面板级（原 15）
FS_TITLE = 22      # 标题级（原 13）
FS_BODY = 20       # 正文 / 按钮 / 输入（原 12）
FS_AUX = 18        # 辅助（原 11）

QSS = """
* { font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; }
/* 字号只有 4 档：面板级 / 标题级 / 正文 / 辅助 —— 见 theme.FS_*，别再插新数字 */
QWidget { color: #f4ede1; font-size: %(body)dpx; }

/* #root 只当内容容器：背景/圆角/投影都由 Panel.paintEvent 画（展开动效要逐帧改圆角）*/
#root {
  background: transparent;
  border: none;
}

/* ---------- 标题栏 ---------- */
#titlebar { background: transparent; border-bottom: 1px solid rgba(244,237,225,0.07); }
#appname  { font-size: %(title)dpx; font-weight: 600; color: #f4ede1; letter-spacing: .3px; }
#subtitle { font-size: %(aux)dpx; color: #807664; }
/* 关闭 / 设置：都是自绘图标按钮（§2.27）—— 底色透明、无色，变色由 IconButton 自己切图 */
QPushButton#close {
  background: transparent; border: none; padding: 0px;
}
QPushButton#gear {
  background: transparent; border: none; padding: 0px;
}

/* ---------- 卡片（A·纪律：不描边 —— 层次只靠底色三档 + 1px 发丝线）----------
   底色三档：画布（paintEvent 画）/ 区块 #191612 / 抬起 #221e18。描边一律省掉，
   需要分隔就在顶部拉一条发丝线；只有"通知""AI 提议"这类有语义的框才保留边框。 */
QFrame#card { background: #191612; border: none; border-top: 1px solid #332c23; }
/* nowcard = 常驻的「喂」入口：唯一带一点金调的卡，因为它是唯一的主动作 */
QFrame#nowcard { background: #1d1a0e; border: none; border-top: 1px solid #4d4013; }
QFrame#payoff { background: #191612; border: none; border-top: 1px solid #332c23; }
QFrame#notice { background: #1e1a11; border: 1px solid #514322; border-radius: 11px; }
/* 节点展开后的深挖区：靠底色差分层，只留左侧一条发丝线当"这是从上面长出来的" */
QFrame#deepbox { background: #121110; border: none; border-left: 1px solid #2a2620; }
/* AI 提议用虚线：虚线在语义上就是"草稿 / 待定"，这一处边框是有意思的，不是装饰 */
QFrame#proposal { background: #1c1913; border: none; border-top: 1px dashed #4a4034; }

/* ---------- 文本 ---------- */
QLabel#h1  { font-size: %(panel)dpx; font-weight: 600; color: #f4ede1; }
QLabel#h2  { font-size: %(title)dpx; font-weight: 700; color: #f4ede1; }
QLabel#cap { font-size: %(aux)dpx; color: #807664; letter-spacing: .9px; font-weight: 700;
             font-family: "Cascadia Mono", "Consolas", monospace; }
QLabel#dim { font-size: %(aux)dpx; color: #807664; }
QLabel#fg2 { font-size: %(body)dpx; color: #b6ab97; }
QLabel#gold { font-size: %(body)dpx; font-weight: 600; color: #FFD01F; }
QLabel#blue { font-size: %(aux)dpx; font-weight: 700; color: #86b6e6; }
QLabel#now  { font-size: %(panel)dpx; font-weight: 700; color: #f4ede1; }

/* ---------- 表单 ---------- */
QPlainTextEdit {
  background: #141210; border: 1px solid #332c23; border-radius: 11px;
  padding: 11px 14px; color: #f4ede1; selection-background-color: #3a3323;
}
QPlainTextEdit:focus { border: 1px solid #FFD01F; }
QLineEdit {
  background: #141210; border: 1px solid #332c23; border-radius: 11px;
  padding: 12px 14px; color: #f4ede1; selection-background-color: #3a3323;
}
QLineEdit:hover { border-color: #4a4034; }
QLineEdit:focus { border: 1px solid #FFD01F; }
QComboBox {
  background: #141210; border: 1px solid #332c23; border-radius: 11px;
  padding: 9px 14px; color: #f4ede1;
}
QComboBox:hover { border-color: #4a4034; }
QComboBox::drop-down { border: none; width: 28px; }
QComboBox QAbstractItemView {
  background: #191612; border: 1px solid #332c23; color: #f4ede1;
  selection-background-color: #3a3323; outline: none;
}

/* ---------- 按钮 ---------- */
QPushButton {
  background: #1d1915; border: 1px solid #332c23; border-radius: 11px;
  padding: 10px 16px; color: #f4ede1; font-size: %(body)dpx;
}
QPushButton:hover { background: #252019; border-color: #4a4034; }
QPushButton:pressed { background: #17140f; }
/* 没名字就不给立项：禁用态要看得出来，别让人以为点了没反应 */
QPushButton:disabled { background: #1a1712; border: 1px solid #2b251e; color: #6b6254; }
QPushButton#primary {
  background: #FFD01F; border: 1px solid #FFDE63; color: #1a160d;
  font-weight: 700; padding: 14px 20px; border-radius: 12px; font-size: %(title)dpx;
}
QPushButton#primary:hover { background: #FFDE63; border-color: #FFF0B8; }
QPushButton#primary:pressed { background: #E8BE12; }
QPushButton#primary:disabled {
  background: #332c12; border: 1px solid #42371a; color: #877a52;
}
QPushButton#gold {
  background: #FFD01F; border: 1px solid #FFDE63; color: #1a160d; font-weight: 700;
}
QPushButton#gold:hover { background: #FFDE63; }
QPushButton#ghostgold {
  background: transparent; border: 1px solid #6b5a1c; color: #FFD01F; font-weight: 600;
}
QPushButton#ghostgold:hover { border-color: #FFD01F; }
/* AI 提议的「采纳」：同 ghostgold 的样子，但单独一个 objectName ——
   走查要靠它准确数出"有几条提议"，不能和别的金边按钮混在一起 */
QPushButton#propok {
  background: transparent; border: 1px solid #6b5a1c; color: #FFD01F; font-weight: 600;
}
QPushButton#propok:hover { border-color: #FFD01F; }
QPushButton#green {
  background: #1d2410; border: 1px solid #3f5220; color: #9ecf4e; font-weight: 600;
}
QPushButton#green:hover { border-color: #9ecf4e; background: #222b13; }
QPushButton#plain { background: transparent; border: 1px solid #332c23; color: #b6ab97; }
QPushButton#plain:hover { border-color: #4a4034; color: #f4ede1; }

/* ---------- 触发式构件（§2.20）---------- */
/* 触发条上的芯片：收着是哑色、体积很小；开着描金，表示这一抽屉正展开 */
QPushButton#chip, QPushButton#chipon {
  background: #17140f; border: 1px solid #332c23; border-radius: 16px;
  padding: 5px 14px; color: #b6ab97; font-size: %(body)dpx;
}
QPushButton#chip:hover { border-color: #4a4034; color: #f4ede1; }
QPushButton#chipon { border-color: #6b5a1c; color: #FFD01F; }
QPushButton#chipon:hover { border-color: #FFD01F; }
/* 收口：一个小勾 */
QPushButton#cap {
  background: transparent; border: 1px solid #6b5a1c; color: #FFD01F;
  border-radius: 16px; padding: 5px 14px; font-size: %(body)dpx; font-weight: 700;
}
QPushButton#cap:hover { border-color: #FFD01F; }
/* 「?」长说明：一点小圆角，点开就地展开一行 */
QPushButton#q {
  background: transparent; border: 1px solid #332c23; border-radius: 9px;
  color: #807664; font-size: %(aux)dpx; padding: 0;
}
QPushButton#q:hover { border-color: #4a4034; color: #f4ede1; }

/* ---------- 对话（§2.24 A·纪律：不用气泡，改分组块 + 左侧发丝线）----------
   层次靠角色小标 + 一条 1px 竖线读出来，不靠色块：色块一多就变装饰了。
   「你」的线暖一点、"AI"的线冷一点，只是一条线，不是一块底色。 */
#turnyou { border: none; border-left: 1px solid #4d4013; }
#turnai  { border: none; border-left: 1px solid #332c23; }
QLabel#whoyou { font-size: %(aux)dpx; font-weight: 700; color: #8a7742; letter-spacing: .9px; }
QLabel#whoai  { font-size: %(aux)dpx; font-weight: 700; color: #807664; letter-spacing: .9px; }
/* 每条消息那个小小的「复制」 */
QPushButton#mini {
  background: transparent; border: none; color: #6b6254; font-size: %(aux)dpx; padding: 0;
}
QPushButton#mini:hover { color: #FFD01F; }
/* 封顶之后那枚可点的「已确认足够」 */
QPushButton#capdone {
  background: #14200c; border: 1px solid #3f5220; color: #9ecf4e;
  border-radius: 16px; padding: 5px 14px; font-size: %(body)dpx; font-weight: 700;
}
QPushButton#capdone:hover { border-color: #9ecf4e; }

/* ---------- 两条长链路的容器：卡片不描边之后，靠底色差抬一层就已经够了 ---------- */
QFrame#chatcard   { background: #1b1814; border: none; border-top: 1px solid #3a3226; }
QFrame#searchcard { background: #1a1712; border: none; border-top: 1px solid #3a3226; }
/* 设置抽屉（§2.25）：和对话/搜索一样的抬升层，只在顶部拉一条发丝线 */
QFrame#settingscard { background: #1a1712; border: none; border-top: 1px solid #3a3226; }

/* ---------- 对话里的富文本（§2.33）：自身不滚动、背景透给分组块 ---------- */
QTextBrowser#rich, QTextBrowser#richturn {
  background: transparent; border: none; padding: 0;
}
QTextBrowser#rich:focus, QTextBrowser#richturn:focus { border: none; outline: none; }

/* ---------- 滚动区 ---------- */
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 12px; margin: 4px 2px 4px 0; }
QScrollBar::handle:vertical { background: #3a3327; border-radius: 6px; min-height: 40px; }
QScrollBar::handle:vertical:hover { background: #4a4034; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

/* ---------- 菜单 ---------- */
QMenu { background: #191612; border: 1px solid #332c23; padding: 6px; }
QMenu::item { padding: 8px 26px 8px 16px; border-radius: 8px; color: #f4ede1; }
QMenu::item:selected { background: #3a3323; }
""" % {"panel": FS_PANEL, "title": FS_TITLE, "body": FS_BODY, "aux": FS_AUX}
