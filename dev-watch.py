# -*- coding: utf-8 -*-
"""Skein · 开发期自动重启（改完代码自动生效，不用再手动重启）

用法：在项目根目录跑 `python dev-watch.py`
- 启动时先把正在跑的 Skein 全杀掉（避免多开干扰窗口查找）
- 盯着 `app/` 下所有 .py 的修改时间，一变就重启
- 自己 Ctrl+C 退出（退的时候**不杀**正在跑的 Skein，让它照常留着）

为什么不用热重载：这是 PyQt6 GUI，模块没法安全热替换；重启进程是最省事、
最不会残留脏状态的做法（改一行就重启一次，成本也就一两秒）。

只在开发期用，不进发布包。
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "app")
POLL = 1.0        # 轮询间隔（秒）
DEBOUNCE = 1.5    # 一次改动后等这么久再重启：连续保存多个文件只重启一次
NO_WINDOW = 0x08000000


def snapshot():
    """app/ 下所有 .py 的 {绝对路径: mtime}（跳过 __pycache__）。"""
    out = {}
    for dirpath, dirnames, filenames in os.walk(APP):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if fn.endswith(".py"):
                p = os.path.join(dirpath, fn)
                try:
                    out[p] = os.stat(p).st_mtime
                except OSError:
                    pass
    return out


def _kill_cmd():
    """认哪些进程是"这台的 Skein"：命令行里同时出现 task-orb 和 app\\main.py。

    这样既能杀掉自己起的，也能杀掉用户双击快捷方式起的 —— 多开会让窗口查找出错。
    只按 python/pythonw 过滤，所以不会误伤本监视器自己（它是 dev-watch.py）。
    """
    return ("Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' or Name='python.exe'\" | "
            "Where-Object { $_.CommandLine -like '*task-orb*' -and $_.CommandLine -like '*main.py*' } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")


def kill_all():
    subprocess.run(["powershell", "-NoProfile", "-Command", _kill_cmd()],
                   capture_output=True, creationflags=NO_WINDOW)


def start():
    """带控制台起（不是 pythonw）：改了代码若有语法错，报错直接打在这块终端里，看得见。"""
    subprocess.Popen([sys.executable, os.path.join(APP, "main.py")],
                     cwd=APP, creationflags=NO_WINDOW)


def restart(why=""):
    kill_all()
    time.sleep(0.6)          # 等进程真的退干净，否则新起的会和旧的撞窗口
    start()
    print("   -> 已重启 %s" % why, flush=True)


def main():
    print("Skein 开发监视中：改 app/ 下的 .py 就自动重启。Ctrl+C 退出。", flush=True)
    kill_all()
    time.sleep(0.5)
    start()
    print("   -> 已启动", flush=True)
    last = snapshot()
    while True:
        time.sleep(POLL)
        cur = snapshot()
        if cur == last:
            continue
        changed = sorted(os.path.relpath(p, ROOT) for p in cur if last.get(p) != cur.get(p))
        time.sleep(DEBOUNCE)         # 连续保存合并成一次
        last = snapshot()
        restart("（%s）" % ", ".join(changed[:3]))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n监视器退出（Skein 保持运行）。", flush=True)
