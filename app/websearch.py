# -*- coding: utf-8 -*-
"""Skein · 免 key 素材搜索（只用标准库）

走 Bing 的免费通道：先试 RSS（结构化、好解析），拿不到再退 HTML 解析。
不引任何第三方依赖、不要 key；失败就把原因如实带回来，不假装搜到了。

只连固定主机（www.bing.com），不接受外部传入 URL —— 没有 SSRF 面。
"""
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 12
RSS_URL = "https://www.bing.com/search?format=rss&q=%s"
HTML_URL = "https://www.bing.com/search?q=%s"

_TAG = re.compile(r"<[^>]+>")
_ENT = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
        "&#39;": "'", "&nbsp;": " ", "&middot;": "·"}
_B_ALGO = re.compile(
    r'<li class="b_algo".*?<h2>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)</li>', re.S)
_P = re.compile(r"<p[^>]*>(.*?)</p>", re.S)


def _clean(s):
    s = _TAG.sub("", s or "")
    for k, v in _ENT.items():
        s = s.replace(k, v)
    return re.sub(r"\s+", " ", s).strip()


def _get(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def parse_rss(text):
    """Bing RSS → [{title,url,snippet}]（纯函数，可离线单测）。"""
    out = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return out
    for it in root.iter("item"):
        def g(tag):
            e = it.find(tag)
            return (e.text or "").strip() if e is not None and e.text else ""
        url = g("link")
        if url:
            out.append({"title": g("title") or url, "url": url,
                        "snippet": _clean(g("description"))})
    return out


def parse_html(text):
    """Bing 网页 → [{title,url,snippet}]（RSS 不可用时的兜底，纯函数）。"""
    out = []
    for m in _B_ALGO.finditer(text or ""):
        url, title, rest = m.group(1), _clean(m.group(2)), m.group(3)
        pm = _P.search(rest)
        out.append({"title": title or url, "url": url,
                    "snippet": _clean(pm.group(1)) if pm else ""})
    return out


def search(query, n=8):
    """返回 (hits, err)。hits = [{title,url,snippet}]；err 为空串表示成功。"""
    q = (query or "").strip()
    if not q:
        return [], "没写搜索词"
    enc = urllib.parse.quote(q)
    hits, err = [], ""
    try:
        hits = parse_rss(_get(RSS_URL % enc))
    except Exception as e:
        err = type(e).__name__
    if not hits:
        err = err or "RSS 没返回结果"
        try:
            hits = parse_html(_get(HTML_URL % enc))
            if hits:
                err = ""
        except Exception as e:
            err = err or type(e).__name__
    if not hits and not err:
        err = "没搜到"
    return hits[:n], ("" if hits else err)
