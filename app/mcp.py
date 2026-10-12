# -*- coding: utf-8 -*-
"""Skein · MCP 客户端（§2.31 / P-021）

让原生 AI 用上 tinyfish 的 MCP 搜索服务（`https://agent.tinyfish.ai/mcp`）。

为什么**不照抄**老板给的 `npx -y mcp-remote ...` 配置：本机的 `npx` 是坏的
（解析到一个野文件、报 `Cannot find module`）。所以这里**直接拉起全局安装的
mcp-remote 的 `dist/proxy.js`**，和它讲 stdio JSON-RPC：

    spawn(node.exe, [proxy.js, <url>])
      → initialize → notifications/initialized → tools/list → tools/call

铁律（守 §2.9 静默降级）：
- **默认开**（设置里可关）；开而 node/mcp-remote 缺失 → 静默当没开，不影响任何现有功能。
- 连接/握手/调用**任何一步失败都返回空/None** —— 调用方静默降级回现成的
  Bing 通道（§2.22），**绝不卡住主流程**。
- OAuth 由 mcp-remote 自己管，token 缓存在 `~/.mcp-auth`，本模块不碰凭据。
"""
import json
import os
import queue
import shutil
import subprocess
import threading
import time

DEFAULT_URL = "https://agent.tinyfish.ai/mcp"
PROTOCOL = "2024-11-05"
CALL_TIMEOUT = 30
RESULT_CHARS = 1500     # 单次工具结果回填给模型的长度上限（防塞爆上下文 / 拖慢收口那一轮）

# 工具名前缀：MCP 工具统一挂 tf_ 前缀，避免和池子那 14 个球工具撞名
PREFIX = "tf_"

# **只读白名单**：tinyfish 有 31 个工具，绝大多数是浏览器自动化 / 监控 / 钱包等**有副作用**的。
# 我们只要它的「搜 + 读」两种能力（给 AI 补最基本的搜索），其余的**一律不暴露**给模型 ——
# 既避免误触副作用，也让模型的工具清单短到能选对（§0.7 简洁优先）。
ALLOWED = ("search", "fetch_content")


_NODE_CANDIDATES = (
    r"C:\nodejs22\node-v22.23.2-win-x64\node.exe",
    r"C:\Program Files\nodejs\node.exe",
    r"C:\Program Files (x86)\nodejs\node.exe",
)
_PROXY_CANDIDATES = (
    r"C:\nodejs22\node-v22.23.2-win-x64\node_modules\mcp-remote\dist\proxy.js",
)
_PROXY_TAIL = os.path.join("node_modules", "mcp-remote", "dist", "proxy.js")


def _first_file(paths):
    for p in paths:
        if p and os.path.isfile(p):
            return p
    return ""


def find_node(explicit=""):
    """找 node.exe：设置里填的 > PATH 里的 > 几个常见安装位。"""
    cand = [explicit] if explicit else []
    cand += [shutil.which("node")]
    cand += list(_NODE_CANDIDATES)
    return _first_file(cand)


def find_proxy(explicit=""):
    """找 mcp-remote 的 dist/proxy.js：设置里填的 > 几个常见全局目录。"""
    cand = [explicit] if explicit else []
    cand += list(_PROXY_CANDIDATES)
    appdata = os.environ.get("APPDATA") or ""
    if appdata:
        cand.append(os.path.join(appdata, "npm", _PROXY_TAIL))
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        cand.append(os.path.join(local, "npm", _PROXY_TAIL))
    return _first_file(cand)


def enabled():
    """设置里 mcp.enabled 没写过就算开（**默认开**，开箱即用"最基本的搜索能力"）。

    显式设成 false 才关。开而 node/mcp-remote 找不到 → start() 失败、工具清单为空，
    行为等同没开（静默降级），不会卡主流程。
    """
    try:
        import config
        return bool(config.setting("mcp_enabled", True))
    except Exception:
        return False


class Client:
    """一个常驻的 mcp-remote 子进程：拉一次、握手一次、之后复用。

    线程安全（后台线程会来调）：所有请求串行化，避免两条请求抢同一根管道。
    """

    def __init__(self, node, proxy, url=DEFAULT_URL, timeout=CALL_TIMEOUT):
        self.node = node
        self.proxy = proxy
        self.url = url or DEFAULT_URL
        self.timeout = timeout
        self._proc = None
        self._inbox = queue.Queue()
        self._id = 0
        self._tools = []          # [{"name","description","inputSchema"}]
        self._lock = threading.RLock()
        self.last_error = ""

    # ---------- 生命周期 ----------
    def start(self):
        """起进程 + 握手 + 拉工具清单。失败返回 False（调用方静默降级）。"""
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return True
            if not (self.node and self.proxy):
                self.last_error = "没找到 node 或 mcp-remote"
                return False
            try:
                self._proc = subprocess.Popen(
                    [self.node, self.proxy, self.url],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                    bufsize=1, creationflags=0x08000000)   # CREATE_NO_WINDOW
            except Exception as e:
                self.last_error = "%s: %s" % (type(e).__name__, e)
                self._proc = None
                return False
            threading.Thread(target=self._read_loop, args=(self._proc,),
                             daemon=True).start()
            try:
                self._request("initialize", {
                    "protocolVersion": PROTOCOL, "capabilities": {},
                    "clientInfo": {"name": "Skein", "version": "0.19.2"}})
                self._request("notifications/initialized", None, notify=True)
                res = self._request("tools/list", {})
                self._tools = (res or {}).get("tools") or []
                return True
            except Exception as e:
                self.last_error = "%s: %s" % (type(e).__name__, e)
                self.close()
                return False

    def close(self):
        with self._lock:
            p, self._proc = self._proc, None
        if p:
            try:
                p.terminate()
            except Exception:
                pass

    def ready(self):
        return bool(self._tools)

    # ---------- 工具清单 ----------
    def tool_schemas(self):
        """转成 OpenAI tools 的 function schema（名字挂 tf_ 前缀）。

        只放行只读白名单 ALLOWED —— 其余工具（浏览器自动化 / 监控 / 钱包…）不暴露。
        """
        out = []
        for t in self._tools:
            name = str(t.get("name") or "").strip()
            if name not in ALLOWED:
                continue
            sch = t.get("inputSchema")
            if not isinstance(sch, dict) or not sch.get("type"):
                sch = {"type": "object", "properties": {}}
            out.append({"type": "function", "function": {
                "name": PREFIX + name,
                "description": str(t.get("description") or "")[:400],
                "parameters": sch}})
        return out

    def raw_name(self, tool_name):
        """tf_xxx → xxx；非白名单工具返回 None（调用方据此拒绝）。"""
        n = str(tool_name or "")
        if not n.startswith(PREFIX):
            return None
        raw = n[len(PREFIX):]
        return raw if raw in ALLOWED else None

    # ---------- 调用 ----------
    def call(self, tool_name, args):
        """执行一次 tools/call；返回给模型看的文本（失败返回一句说明，不抛）。"""
        raw = self.raw_name(tool_name)
        if raw is None:
            return "（不是 MCP 工具）"
        with self._lock:
            if not (self._proc and self._proc.poll() is None):
                if not self.start():
                    return "（MCP 连接不可用：%s）" % (self.last_error or "未连接")
            try:
                res = self._request("tools/call",
                                    {"name": raw, "arguments": args or {}},
                                    timeout=self.timeout)
            except Exception as e:
                self.last_error = "%s: %s" % (type(e).__name__, e)
                return "（MCP 调用失败：%s）" % self.last_error
        return _flatten(res)

    # ---------- 内部 ----------
    def _read_loop(self, proc):
        try:
            for line in proc.stdout:
                line = (line or "").strip()
                if not line:
                    continue
                try:
                    self._inbox.put(json.loads(line))
                except Exception:
                    continue          # 非 JSON 行（如日志）直接丢
        except Exception:
            pass

    def _request(self, method, params, notify=False, timeout=None):
        proc = self._proc
        if not proc or not proc.stdin:
            raise RuntimeError("进程没起来")
        if notify:
            msg = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                msg["params"] = params
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
            return None
        self._id += 1
        i = self._id
        msg = {"jsonrpc": "2.0", "id": i, "method": method}
        if params is not None:
            msg["params"] = params
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()
        end = time.time() + (timeout or self.timeout)
        while True:
            left = end - time.time()
            if left <= 0:
                raise TimeoutError("等 %s 回包超时" % method)
            try:
                m = self._inbox.get(timeout=left)
            except queue.Empty:
                raise TimeoutError("等 %s 回包超时" % method)
            if m.get("id") != i:
                continue              # 别的回包/通知，跳过
            if "error" in m:
                err = m["error"]
                raise RuntimeError(str(err.get("message") if isinstance(err, dict) else err))
            return m.get("result")


def _flatten(res):
    """把 tools/call 的 content 摊平成一段文本（只要 text 段）。"""
    if not isinstance(res, dict):
        return ""
    parts = []
    for it in (res.get("content") or []):
        if isinstance(it, dict) and it.get("type") == "text":
            parts.append(str(it.get("text") or ""))
    txt = "\n".join(p for p in parts if p).strip()
    if not txt:
        txt = json.dumps(res, ensure_ascii=False)
    return txt[:RESULT_CHARS]


# ---------------- 全局单例（后台线程/主线程共用，内部有锁）----------------
_CLIENT = None
_CLIENT_LOCK = threading.Lock()


def get_client():
    """按设置取（并缓存）一个客户端；设置里没开、或**这次连不上**都返回 None。

    连不上**不缓存**坏实例：下次再来会重试（早先缓存坏实例 → 一次失败就永久没搜索，P-021 踩过）。
    """
    global _CLIENT
    if not enabled():
        return None
    with _CLIENT_LOCK:
        if _CLIENT is not None:
            return _CLIENT
        try:
            import config
            node = find_node(str(config.setting("mcp_node", "") or ""))
            proxy = find_proxy(str(config.setting("mcp_proxy", "") or ""))
            url = str(config.setting("mcp_url", "") or "") or DEFAULT_URL
        except Exception:
            node, proxy, url = "", "", DEFAULT_URL
        c = Client(node, proxy, url)
        if not c.start():
            return None
        _CLIENT = c
        return _CLIENT


def reset():
    """丢弃缓存（设置改了就重新连）。"""
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is not None:
            _CLIENT.close()
        _CLIENT = None


if __name__ == "__main__":
    # 自检入口：python mcp.py —— 打印真实工具清单（调试用，不影响应用）
    node = find_node()
    proxy = find_proxy()
    print("node  =", node or "（没找到）")
    print("proxy =", proxy or "（没找到）")
    c = Client(node, proxy)
    if not c.start():
        print("连接失败：", c.last_error)
    else:
        print("工具清单（%d 个）：" % len(c._tools))
        for t in c._tools:
            print("  -", t.get("name"), "｜", str(t.get("description") or "")[:60])
        c.close()
