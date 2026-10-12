# -*- coding: utf-8 -*-
"""Skein · 配置与常量"""
import os
import json

APP_NAME = "Skein"

# 版本号（唯一真相源）：打 tag / 出 Release / 应用内检查更新都以它为准（§2.32）。
VERSION = "0.21.0"
# 更新源（§2.32）：GitHub Releases 的仓库位置。
GITHUB_OWNER = "llwand1"
GITHUB_REPO = "skein"


def app_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


DB_PATH = os.path.join(app_dir(), "skein.db")
PROBE_LOG = os.path.join(app_dir(), "probe.log")
SETTINGS_PATH = os.path.join(app_dir(), "settings.json")
# 模型配置（base / model / key）。放在用户数据目录里，**不进代码仓**以免误提交
LLM_PATH = os.path.join(app_dir(), "llm.json")
# 真实素材（图片/视频/音频/长文）落盘的地方：上传或拖进来的文件都复制到这里，
# 原件不动。剪映之类可以直接从这个文件夹拖。
ASSETS_DIR = os.path.join(app_dir(), "assets")
# 跨期用户记忆（§2.29 / P-020）：应用**只读**它、内容由用户自己写；与 llm.json 同目录。
USER_PATH = os.path.join(app_dir(), "user.md")

# 刻意不放任何"排期 / 演示语料"：一期从哪来、叫什么，全部由你自己记进来的东西决定。
# （曾经这里有一份 E004/E005/E006 的排期表，会让面板一打开就带着别人写好的标题。）

PUBLISH = [
    ("抖音创作发布", "https://creator.douyin.com/"),
    ("B站投稿", "https://member.bilibili.com/platform/upload/video/frame"),
    ("小红书发布", "https://creator.xiaohongshu.com/publish/publish"),
]

# ---------- 素材文件的类型判定 ----------
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
VID_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".m4v", ".wmv"}
AUD_EXT = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".opus"}
DOC_EXT = {".txt", ".md", ".markdown", ".srt", ".vtt", ".json", ".csv"}


def kind_of(path):
    """按扩展名判断素材类型。判不出来就 'file' —— 不猜。"""
    e = os.path.splitext(path or "")[1].lower()
    if e in IMG_EXT:
        return "image"
    if e in VID_EXT:
        return "video"
    if e in AUD_EXT:
        return "audio"
    if e in DOC_EXT:
        return "text"
    return "file"


def load_settings():
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_settings(d):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------- 设置项（模型 / 内容默认 / 系统提示词 / 搜索 MCP，§2.25 / §2.31）----------
# 通用化＝不预设：内容默认（platform / form / target）一律留空，
# 空就是空 —— 不兜"抖音 / 图文 / 7 张"这种写死的默认。
# 搜索 MCP（tinyfish）默认**开**：缺省即开，显式 false 才关（§2.31 v0.19.1）。
SETTING_KEYS = ("active_topic", "platform", "form", "target", "sys_prompt",
                "mcp_enabled", "mcp_node", "mcp_proxy", "mcp_url",
                "update_auto_check")


def setting(key, default=""):
    """取一项设置。没配过就返回 default（默认空串＝不预设任何东西）。"""
    v = load_settings().get(key)
    if v is None:
        return default
    return v


# ---------- user.md（§2.29 / P-020）----------
# 只在**用户点「打开 user.md」**时才建 —— 应用这一版**不自动写内容**（P1 甲）。
# 模板里打 `by: user` 标记，给将来"自动写"留出区分来源的口子。
_USER_TEMPLATE = (
    "# 关于我（Skein 每一期都会读它，只作参考）\n"
    "<!-- by: user -->\n"
    "\n"
    "- 我是谁：\n"
    "- 我讲什么：\n"
    "- 我的口味（喜欢/讨厌的说法）：\n"
)


def ensure_user_md():
    """保证 user.md 存在并返回它的路径。不存在就落一份空模板（人点了才建）。"""
    if not os.path.exists(USER_PATH):
        try:
            with open(USER_PATH, "w", encoding="utf-8") as f:
                f.write(_USER_TEMPLATE)
        except Exception:
            pass
    return USER_PATH
