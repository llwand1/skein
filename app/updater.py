# -*- coding: utf-8 -*-
"""Skein · 自动更新（§2.32 / P-022）

从 GitHub Releases 拉最新 tag，比当前 `config.VERSION` 新就提示；
点「更新」→ 下这个 tag 的源码包 → 覆盖 `app/` 与根目录脚本 → 自动重启。

边界（守 §2.9 静默降级）：
- **只更新代码**（`app/*.py` + 根目录 .py/.bat/.vbs）；用户数据在 `%LOCALAPPDATA%`，一概不碰。
- 检查 / 下载 / 解压**任何一步失败都返回说明、不抛** —— 调用方照旧用旧版跑，不卡主流程。
- 打包版（PyInstaller）走的是 exe，源码包覆盖不动它 —— 这时只提示、不自动更新。
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

import config

TIMEOUT = 20
_UA = {"User-Agent": "Skein-Updater", "Accept": "application/vnd.github+json"}
_DETACHED = 0x00000008 | 0x08000000      # DETACHED_PROCESS | CREATE_NO_WINDOW


def _root():
    """项目根目录（app/ 的上一级）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _api():
    return "https://api.github.com/repos/%s/%s/releases/latest" % (
        config.GITHUB_OWNER, config.GITHUB_REPO)


def _fetch(url, raw=False):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = r.read()
    return data if raw else json.loads(data.decode("utf-8"))


def _vt(s):
    """'v0.20.0' → (0,20,0)。取每段开头的数字，比不出来返回 ()。"""
    s = (s or "").strip().lstrip("vV")
    out = []
    for seg in s.split("."):
        num = ""
        for ch in seg:
            if ch.isdigit():
                num += ch
            else:
                break
        out.append(int(num) if num else 0)
    return tuple(out) if out else ()


def check():
    """查最新版。返回 {has, current, latest, url, notes, error}（永不抛）。"""
    cur = config.VERSION
    try:
        d = _fetch(_api())
    except Exception as e:
        return {"has": False, "current": cur, "error": "%s: %s" % (type(e).__name__, e)}
    tag = str(d.get("tag_name") or "").strip()
    a, b = _vt(tag), _vt(cur)
    if not a or not b:
        return {"has": False, "current": cur, "latest": tag, "error": "版本号比不出来"}
    return {"has": a > b, "current": cur, "latest": tag,
            "url": str(d.get("zipball_url") or "").strip(),
            "notes": str(d.get("body") or "").strip(), "error": ""}


def _copy_py(src, dst):
    n = 0
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        rel = os.path.relpath(dirpath, src)
        outdir = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(outdir, exist_ok=True)
        for fn in filenames:
            if fn.endswith(".py"):
                shutil.copy2(os.path.join(dirpath, fn), os.path.join(outdir, fn))
                n += 1
    return n


def apply(url):
    """下源码包并覆盖代码。返回 (ok, msg)，永不抛。"""
    if getattr(sys, "frozen", False):
        return False, "打包版请下载新版 exe 覆盖；应用内自更新只支持源码方式运行"
    if not url:
        return False, "没有下载地址"
    try:
        blob = _fetch(url, raw=True)
    except Exception as e:
        return False, "下载失败：%s: %s" % (type(e).__name__, e)
    tmp = tempfile.mkdtemp(prefix="skein-upd-")
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            z.extractall(tmp)
        subs = [os.path.join(tmp, n) for n in os.listdir(tmp)]
        top = subs[0] if len(subs) == 1 and os.path.isdir(subs[0]) else tmp
        root = _root()
        n = 0
        src_app = os.path.join(top, "app")
        if os.path.isdir(src_app):
            n += _copy_py(src_app, os.path.join(root, "app"))
        for fn in os.listdir(top):
            if fn.endswith((".py", ".bat", ".vbs")):
                shutil.copy2(os.path.join(top, fn), os.path.join(root, fn))
                n += 1
        if not n:
            return False, "包里没找到可更新的文件"
        return True, "更新了 %d 个文件" % n
    except Exception as e:
        return False, "解压/覆盖失败：%s: %s" % (type(e).__name__, e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def restart_now():
    """起一个脱离的助手：等一会儿（让本进程先退出，免得两个实例抢窗口）再重新拉起。"""
    if getattr(sys, "frozen", False):
        target = [sys.executable]
    else:
        target = ["wscript.exe", os.path.join(_root(), "run-silent.vbs")]
    code = ("import time, subprocess; time.sleep(2.0); "
            "subprocess.Popen(%r, close_fds=True)" % (target,))
    try:
        subprocess.Popen([sys.executable, "-c", code], creationflags=_DETACHED)
    except Exception:
        pass
