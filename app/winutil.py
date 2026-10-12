# -*- coding: utf-8 -*-
"""Skein · Win32 辅助：不抢焦点、前台窗口/光标探针

关键点：让球"不抢焦点"靠的是 WS_EX_NOACTIVATE 扩展样式，
不是 topmost。topmost 只管层级，不管激活。
"""
import ctypes
import datetime
from ctypes import wintypes

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008

HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010

user32 = ctypes.windll.user32

try:
    _GetWindowLong = user32.GetWindowLongPtrW
    _SetWindowLong = user32.SetWindowLongPtrW
except AttributeError:
    _GetWindowLong = user32.GetWindowLongW
    _SetWindowLong = user32.SetWindowLongW

_GetWindowLong.restype = ctypes.c_ssize_t
_GetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int]
_SetWindowLong.restype = ctypes.c_ssize_t
_SetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]


def hwnd_of(win):
    """拿到窗口真实的 Win32 句柄（同时支持 Qt 与 Tk）"""
    # Qt 控件
    if hasattr(win, "winId"):
        try:
            h = int(win.winId())
            if h:
                return h
        except Exception:
            pass
    # Tk 窗口
    try:
        win.update_idletasks()
    except Exception:
        pass
    try:
        f = win.wm_frame()
        if isinstance(f, str) and f.startswith("0x"):
            h = int(f, 16)
            if h:
                return h
    except Exception:
        pass
    try:
        return win.winfo_id()
    except Exception:
        return 0


def get_ex_style(win):
    return _GetWindowLong(hwnd_of(win), GWL_EXSTYLE)


def apply_noactivate(win):
    """置顶 + 不进 Alt+Tab + 鼠标点击不激活窗口"""
    h = hwnd_of(win)
    ex = _GetWindowLong(h, GWL_EXSTYLE)
    ex |= WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
    _SetWindowLong(h, GWL_EXSTYLE, ex)
    user32.SetWindowPos(h, HWND_TOPMOST, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    return h


def window_title(h):
    n = user32.GetWindowTextLengthW(h)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(h, buf, n + 1)
    return buf.value


def class_name(h):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, buf, 256)
    return buf.value


def foreground():
    return user32.GetForegroundWindow()


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


def caret_hwnd():
    """当前前台线程里，谁在持有输入光标（0 = 没有）"""
    fg = user32.GetForegroundWindow()
    tid = user32.GetWindowThreadProcessId(fg, None)
    gti = GUITHREADINFO()
    gti.cbSize = ctypes.sizeof(GUITHREADINFO)
    ok = user32.GetGUIThreadInfo(tid, ctypes.byref(gti))
    return (gti.hwndCaret if ok else 0), fg


def snapshot(tag=""):
    fg = foreground()
    caret, _ = caret_hwnd()
    return "[{ts}] {tag} | 前台窗口=0x{h:08X} 「{t}」 | 光标控件=0x{c:08X}".format(
        ts=datetime.datetime.now().strftime("%H:%M:%S"),
        tag=tag, h=fg or 0, t=window_title(fg) or "(无)", c=caret or 0)


def append_probe(line):
    import config
    try:
        with open(config.PROBE_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


SPI_GETCLIENTAREAANIMATION = 0x1042


def animations_enabled():
    """Windows「在 Windows 中显示动画」总开关。

    这是桌面端对应网页 prefers-reduced-motion 的那个开关：关掉它的人
    （前庭功能敏感、晕动症）应该拿到"无位移"的界面。读不到就按开启处理。
    """
    try:
        enabled = wintypes.BOOL(True)
        ok = user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0,
                                          ctypes.byref(enabled), 0)
        if not ok:
            return True
        return bool(enabled.value)
    except Exception:
        return True
