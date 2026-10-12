# -*- coding: utf-8 -*-
"""Skein · LLM 适配层（OpenAI 兼容）

只走标准库，不新增依赖。配置优先级：**环境变量 > 本地配置文件 > 默认值**。

    %LOCALAPPDATA%\\Skein\\llm.json     ← 主要走这条（不进代码仓，不会被误提交）
        {"base": "...", "model": "...", "key": "..."}

    SKEIN_LLM_BASE / SKEIN_LLM_MODEL / SKEIN_LLM_KEY   ← 可选覆盖（给别人用）

两条铁律：
1. **失败即静默降级**：任何异常都返回 None，调用方保留本地规则的结果，界面不受影响。
   模型不在线时，Skein 仍然是一个能跑完闭环的工具，只是"没那么懂".
2. **不在主线程发请求**：统一走 chat_async，Qt 界面不会被网络卡住。
"""
import json
import os
import re
import threading
import urllib.error
import urllib.request

# 注意：这个模块里有一个函数也叫 config()，会遮蔽 config 模块 —— 所以改名导入
import config as appconfig

TIMEOUT = 25
LAST_ERROR = None   # 最近一次调用失败的原因（只给界面用，不含 key）

DEFAULT_BASE = "https://apihub.agnes-ai.com/v1"
DEFAULT_MODEL = "agnes-3.0-flash"


def _from_file():
    try:
        with open(appconfig.LLM_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def config():
    f = _from_file()
    return {
        "base": (os.environ.get("SKEIN_LLM_BASE") or f.get("base")
                 or DEFAULT_BASE).rstrip("/"),
        "model": os.environ.get("SKEIN_LLM_MODEL") or f.get("model") or DEFAULT_MODEL,
        "key": (os.environ.get("SKEIN_LLM_KEY") or f.get("key") or "").strip(),
    }


def available():
    return bool(config()["key"])


def status():
    """给界面看的一行字。故意不暴露 base/key。

    配了 key 不等于能用 —— 最近一次调用失败就如实说"不通"，别让人以为它在工作。
    """
    c = config()
    if not c["key"]:
        return "本地规则"
    if LAST_ERROR:
        return "模型不通 · 本地兜底"
    return "模型 · " + c["model"]


def save_config(base, model, key):
    """把模型配置写进 llm.json（key 不进代码仓，落在用户数据目录）。"""
    d = {"base": (base or "").strip(),
         "model": (model or "").strip(),
         "key": (key or "").strip()}
    try:
        with open(appconfig.LLM_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def test(base, model, key, timeout=12):
    """拿还没保存的值测一次连通性（GET /models，不花 token）。返回 (ok, 说明)。

    和 ping 的区别：ping 只测"已生效的配置"，这里测"输入框里那份"——
    设置面板得能在按下保存前先告诉你连不连得上。
    """
    b = (base or "").strip().rstrip("/") or DEFAULT_BASE
    k = (key or "").strip()
    if not k:
        return False, "还没填 key"
    try:
        req = urllib.request.Request(b + "/models",
                                     headers={"Authorization": "Bearer " + k})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            json.load(r)
        return True, "连上了"
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


def ping(timeout=12):
    """便宜的连通性检查：只 GET /models，不花 token。返回 True/False，并更新 LAST_ERROR。"""
    global LAST_ERROR
    c = config()
    if not c["key"]:
        return False
    try:
        req = urllib.request.Request(c["base"] + "/models",
                                     headers={"Authorization": "Bearer " + c["key"]})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            json.load(r)
        LAST_ERROR = None
        return True
    except Exception as e:
        LAST_ERROR = "%s: %s" % (type(e).__name__, e)
        return False


def chat_message(messages, temperature=0.6, timeout=TIMEOUT, tools=None, tool_choice=None):
    """一次 chat.completions，返回原始 message dict（含 content / tool_calls）。

    tools 非空 = 走模型**原生工具调用**（function calling）。有些 OpenAI 兼容网关
    不支持 tools，会直接报错或把它忽略 —— 调用方（brain.llm_chat）负责降级，这里只做一次尝试。
    """
    c = config()
    if not c["key"]:
        raise RuntimeError("SKEIN_LLM_KEY 未设置")
    payload = {"model": c["model"], "messages": messages, "temperature": temperature}
    if tools:
        payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        c["base"] + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + c["key"]})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    return d["choices"][0]["message"]


def chat(messages, temperature=0.6, timeout=TIMEOUT):
    return chat_message(messages, temperature=temperature, timeout=timeout).get("content") or ""


def chat_async(messages, cb, **kw):
    """后台线程调用；cb(text or None) 保证被调用一次。"""
    def work():
        try:
            cb(chat(messages, **kw))
        except Exception:
            cb(None)
    threading.Thread(target=work, daemon=True).start()


def _first_json(text):
    """模型常把 JSON 包在 ``` 里，或前后带废话 —— 抠出第一段合法 JSON。"""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()
    for i, ch in enumerate(t):
        if ch in "[{":
            depth, end = 0, None
            for j in range(i, len(t)):
                if t[j] in "[{":
                    depth += 1
                elif t[j] in "]}":
                    depth -= 1
                    if depth == 0:
                        end = j + 1
                        break
            if end:
                try:
                    return json.loads(t[i:end])
                except Exception:
                    return None
    return None


def ask_json(system, user, temperature=0.5, timeout=TIMEOUT):
    """要一段 JSON 回来；拿到不合法就返回 None（调用方降级）。"""
    global LAST_ERROR
    try:
        txt = chat([{"role": "system", "content": system},
                    {"role": "user", "content": user}],
                   temperature=temperature, timeout=timeout)
    except Exception as e:
        LAST_ERROR = "%s: %s" % (type(e).__name__, e)
        return None
    got = _first_json(txt)
    if got is None:
        LAST_ERROR = "返回的不是 JSON"
        return None
    LAST_ERROR = None
    return got
