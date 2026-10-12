# -*- coding: utf-8 -*-
"""Skein · 编排引擎

**项目本身不产出视频。** Skein 的职责到"编排"为止：这一段讲什么、画面是什么、
顺序怎么排、每个镜头多长、缺的画面要生成什么样的图 —— 编完导出一个**编排包**，
交给剪映 / 即梦 / 可灵 / Runway 这类软件去真正出片。

编排引擎做的事：
  1. 把每段口播稿切成镜头，给每个镜头定：**用什么画面 + 多长 + 怎么运镜 + 配哪句字幕**
  2. 手上有素材的镜头 → 直接指到那个文件（**同一个画面在整支片里只排一次**）
  3. 手上没素材的镜头 → 不空着，给出"要生成什么样的画面"的提示词，
     让 AI 生图/生视频软件照着做（这就是"编排之内"的效果：画面是 Skein 定的，不是临场发挥）
  4. 输出时间轴（含入出点）、字幕（SRT）、分镜表 —— 剪辑软件拿去就能摆

不依赖 ffmpeg、不做渲染、不配音。只依赖标准库 + Pillow（给素材做内容指纹）。
"""
import os
import re

WIDTH, HEIGHT = 1080, 1920
FPS = 25

SHOT_TARGET = 4.5          # 单个镜头目标时长（秒）
SHOT_MIN, SHOT_MAX = 2, 6  # 一段里镜头数上下限
SHOT_MAXLEN = 9.0          # 单镜头最长，超了就再切一个
MIN_IMG_SIDE = 360         # 小于这个边长的图当占位图丢掉（1x1 的坑踩过）
SPEED = 6.0                # 中文口播速度：字/秒（按 edge-tts +24% 实测标定，用于估时长）

CAMERAS = ["push_in", "pan_right", "static", "pan_left", "ken_burns"]
CAMERA_ZH = {"push_in": "缓慢推近", "pan_right": "镜头右摇", "static": "固定",
             "pan_left": "镜头左摇", "ken_burns": "斜向缓推"}

STYLE_TAGS = ("vertical 9:16, cinematic lighting, dark moody tone, "
              "high detail, no text, no watermark")


# ------------------------- 素材体检（复用：占位图防线）-------------------------
def _ahash(path):
    try:
        from PIL import Image
        with Image.open(path) as im:
            g = im.convert("L").resize((8, 8))
        px = list(g.getdata())
        avg = sum(px) / len(px)
        return "".join("1" if p > avg else "0" for p in px)
    except Exception:
        return ""


def fingerprint(path, kind):
    """内容指纹：同一张图换个文件名也认得出 —— 用于"同画面只排一次"。"""
    try:
        if kind == "image":
            h = _ahash(path)
            return ("img", h) if h else None
        if kind == "video":
            return ("vid", os.path.getsize(path))
    except Exception:
        pass
    return None


def check_media(path, kind):
    if not path or not os.path.exists(path):
        return False, "文件不在了"
    if kind == "image":
        try:
            from PIL import Image
            with Image.open(path) as im:
                im.verify()
                w, h = im.size
        except Exception:
            return False, "打不开（不是真图）"
        if min(w, h) < MIN_IMG_SIDE:
            return False, "只有 %dx%d，是占位图" % (w, h)
        return True, ""
    if kind == "video":
        return True, ""
    return False, "不是画面素材"


# ------------------------- 文案 → 句 / 时长 -------------------------
def sentences(text):
    text = (text or "").strip()
    if not text:
        return []
    parts = re.split(r"(?<=[。！？；!?])", text)
    out = [p.strip() for p in parts if p.strip()]
    return out or [text]


def estimate_dur(text):
    """按字数估时长。不出片就不需要真配音，估一个够摆时间轴的数。"""
    n = len(re.sub(r"\s", "", text or ""))
    return max(5.0, min(45.0, n / SPEED + 0.6))


def _short(s, n=14):
    return re.sub(r"[？?。！!，,、：:]", "", (s or "").strip())[:n]


def _keyphrases(script):
    out = []
    for m in re.finditer(r"\d+(?:\.\d+)?(?:[万亿元%年个月天倍])?", script or ""):
        if m.group(0) not in out:
            out.append(m.group(0))
    return out


# ------------------------- 镜头 -------------------------
class Shot(object):
    def __init__(self, sid, seg, order, kind, dur, camera, caption,
                 src_path="", src_name="", prompt_zh="", prompt_en=""):
        self.id = sid
        self.seg = seg
        self.order = order
        self.kind = kind            # material = 有现成素材 / generate = 要生成
        self.dur = dur
        self.camera = camera
        self.caption = caption      # 这个镜头配的口播句（字幕）
        self.src_path = src_path
        self.src_name = src_name
        self.prompt_zh = prompt_zh
        self.prompt_en = prompt_en
        self.start = 0.0

    def as_dict(self):
        return {
            "id": self.id, "seg": self.seg, "order": self.order,
            "kind": self.kind, "start": round(self.start, 3),
            "dur": round(self.dur, 3), "end": round(self.start + self.dur, 3),
            "camera": self.camera, "camera_zh": CAMERA_ZH.get(self.camera, self.camera),
            "caption": self.caption,
            "source": {"path": self.src_path, "name": self.src_name} if self.src_path else None,
            "prompt": ({"zh": self.prompt_zh, "en": self.prompt_en}
                       if self.kind == "generate" else None),
        }


# 同一个段里可能有好几个待生成镜头 —— 每个必须**职责不同**，
# 否则提示词雷同，生成出来的画面就是同一个画面的复印件（"画面重复"的老毛病）。
ROLES = [
    "开场全景：先把场景和主体交代清楚",
    "主体特写：把关键对象推到眼前",
    "过程画面：展示正在发生的动作或流程",
    "对比或结果：把差异 / 结局摆出来",
    "收束定格：留一个能让人记住的画面",
]


def gen_prompt(seg_question, hint, caption, role="", point=""):
    """给 AI 生图/生视频软件的提示词：画面是什么由 Skein 定，不是临场发挥。

    三个来源按优先级拼：这一段独有的画面点(point) > 整段的画面要求(hint) >
    这一镜头配的口播句(caption)。再叠上镜头职责(role)，保证同段内不重样。
    """
    core = re.sub(r"\s+", " ", (point or hint or caption or seg_question or "")).strip()
    core = core[:80]                       # 保留标点，句子完整才好用
    head = ("%s。" % role) if role else ""
    zh = ("竖屏 9:16，%s%s。写实风格，电影感布光，暗色调，"
          "画面内不要出现文字与水印。" % (head, core))
    ctx = "｜这一段在讲：%s" % _short(seg_question, 30) if seg_question else ""
    return zh + ctx, core + ", " + STYLE_TAGS


def _points(hint, caption):
    """把这一段的画面要求拆成若干个"画面点"，供多个待生成镜头各取一个。"""
    src = hint or caption or ""
    pts = [p.strip() for p in re.split(r"[。；;！!]|，", src) if len(p.strip()) >= 6]
    return pts or ([src.strip()] if src.strip() else [])


def _captions_for(body, n):
    """把一段口播稿按字数比例摊到 n 个镜头上 —— 每个镜头配它那几句字幕。

    字幕只能来自口播稿（不是画面描述）：观众听到什么就得看到什么。
    """
    sents = sentences(body)
    if not sents:
        return [""] * n
    lens = [max(1, len(s)) for s in sents]
    tot = sum(lens)
    acc, spans = 0.0, []
    for L_ in lens:
        spans.append((acc / tot, (acc + L_) / tot))
        acc += L_
    out = []
    for i in range(n):
        a, b = i / n, (i + 1) / n
        got = [sents[j] for j, (s0, s1) in enumerate(spans)
               if not (s1 <= a or s0 >= b)]       # 与该镜头时间窗有重叠
        out.append("".join(got) if got else sents[min(i, len(sents) - 1)])
    return out


def plan_shots(dur, pool, seg_idx, seg_title, seg_question, used_fp,
               body="", hint="", topic_name=""):
    """给这一段排镜头。

    pool: [(path, kind), ...]（调用方保证是这一段能用的真实素材）
    返回 (shots, 用掉的真实素材数, 待生成数)
    """
    need = max(SHOT_MIN, min(SHOT_MAX, int(round(dur / SHOT_TARGET))))
    take = []
    for p, k in pool:
        if len(take) >= need:
            break
        fp = fingerprint(p, k)
        if fp and fp in used_fp:
            continue                       # 这画面在别处排过了，不再排
        ok, _ = check_media(p, k)
        if not ok:
            continue
        if fp:
            used_fp.add(fp)
        take.append((p, k))

    # 素材不够 → 不重复画面，改成"待生成"镜头（给提示词，让 AI 软件去出图）
    gap = max(0, need - len(take))
    if len(take) and (dur / len(take)) > SHOT_MAXLEN:
        gap = max(gap, min(3, int(dur / SHOT_MAXLEN) + 1 - len(take)))
    if not take:
        gap = max(gap, SHOT_MIN)

    items = [(p, k, "material") for p, k in take] + [("", "", "generate")] * gap
    n = len(items)
    L = dur / n
    caps = _captions_for(body, n)
    pts = _points(hint, body)

    shots = []
    gi = 0                                  # 第几个待生成镜头（决定它的职责）
    for i, (p, k, kind) in enumerate(items):
        cam = CAMERAS[(seg_idx * 2 + i) % len(CAMERAS)]
        cap = caps[i]
        if kind == "material":
            s = Shot("S%d-%d" % (seg_idx, i + 1), seg_idx, i + 1, "material",
                     L, cam, cap, src_path=p, src_name=os.path.basename(p))
        else:
            role = ROLES[gi % len(ROLES)]
            point = pts[gi] if gi < len(pts) else (cap or hint)
            zh, en = gen_prompt(seg_question, hint, cap, role, point)
            gi += 1
            s = Shot("S%d-%d" % (seg_idx, i + 1), seg_idx, i + 1, "generate",
                     L, cam, cap, prompt_zh=zh, prompt_en=en)
        shots.append(s)
    return shots, len(take), gap


def build(segments, topic_name=""):
    """segments: [{'id','title','body','pool','hint'}...] → 完整编排（含时间轴）。"""
    used_fp = set()
    out_segs, flat, warnings = [], [], []
    t = 0.0
    for i, seg in enumerate(segments, 1):
        body = seg.get("body") or ""
        dur = estimate_dur(body)
        hint = seg.get("hint") or ""
        shots, n_real, n_gen = plan_shots(
            dur, seg.get("pool") or [], i, seg.get("title") or str(i),
            seg.get("question") or "", used_fp, body=body,
            hint=hint, topic_name=topic_name)
        for s in shots:
            s.start = t
            t += s.dur
            flat.append(s)
        out_segs.append({
            "seg": i, "beat_id": seg.get("id"),
            "title": seg.get("title") or "", "question": seg.get("question") or "",
            "script": body, "dur": round(dur, 2),
            "real": n_real, "generate": n_gen,
            "start": round(shots[0].start, 2) if shots else 0.0,
            "shots": [s.as_dict() for s in shots],
        })
        if n_gen:
            warnings.append("第 %d 段：%d 个现成画面 + %d 个待生成"
                            "（提示词已写好，交给 AI 生图/生视频软件）"
                            % (i, n_real, n_gen))
        if n_real == 0:
            warnings.append("第 %d 段一个现成画面都没有，全靠生成 —— 建议先补素材" % i)
    return {"topic": topic_name, "segments": out_segs,
            "shots": [s.as_dict() for s in flat],
            "total": round(t, 2), "warnings": warnings,
            "count": {"material": sum(1 for s in flat if s.kind == "material"),
                      "generate": sum(1 for s in flat if s.kind == "generate")}}


# ------------------------- 导出用：SRT / 分镜表 / 提示词 -------------------------
def srt(arr):
    def ts(x):
        h = int(x // 3600)
        m = int((x % 3600) // 60)
        s = x % 60
        return "%02d:%02d:%02d,%03d" % (h, m, int(s), int(round((s - int(s)) * 1000)))
    out, i = [], 0
    for seg in arr["segments"]:
        for line in sentences(seg["script"]) or [seg["script"]]:
            if not line:
                continue
            i += 1
            # 句子在这一段里的占比 → 时间
            total = max(1, len(sentences(seg["script"]) or [seg["script"]]))
            idx = (sentences(seg["script"]) or [seg["script"]]).index(line)
            st = seg["start"] + seg["dur"] * idx / total
            en = seg["start"] + seg["dur"] * (idx + 1) / total
            out.append(str(i))
            out.append("%s --> %s" % (ts(st), ts(en)))
            out.append(line)
            out.append("")
    return "\n".join(out)


def shot_table_md(arr):
    L = ["# 分镜表", "",
         "> Skein 只做编排，不出片。这张表 = 交给剪映 / 即梦 / 可灵 的施工单。",
         "> **同一个画面在整支片里只排一次**；标 `待生成` 的镜头按提示词去生成。", ""]
    L.append("总时长 ≈ %.1fs ｜ %d 个镜头（现成 %d · 待生成 %d）"
             % (arr["total"], len(arr["shots"]),
                arr["count"]["material"], arr["count"]["generate"]))
    L.append("")
    for seg in arr["segments"]:
        L.append("## 第 %d 段 · %.1fs · %s" % (seg["seg"], seg["dur"], seg["title"]))
        L.append("")
        L.append("**口播稿**：%s" % seg["script"])
        L.append("")
        L.append("| 镜头 | 入点 | 时长 | 画面来源 | 运镜 | 配的字幕 |")
        L.append("|---|---|---|---|---|---|")
        for s in seg["shots"]:
            src = ("现成：`%s`" % s["source"]["name"]) if s["source"] else "**待生成**"
            L.append("| %s | %.1fs | %.1fs | %s | %s | %s |"
                     % (s["id"], s["start"], s["dur"], src,
                        s["camera_zh"], (s["caption"] or "")[:24]))
        L.append("")
    return "\n".join(L)


def prompts_md(arr):
    """给 AI 生图 / 生视频软件的提示词清单（只列待生成的镜头）。"""
    L = ["# 待生成画面 · 提示词", "",
         "下面每个镜头对应分镜表里的一个 `待生成`。按顺序丢给即梦 / 可灵 / MJ / Runway，",
         "生成完按镜头号放回时间轴，**画面内容由 Skein 定好，不要临场改**。", ""]
    n = 0
    for seg in arr["segments"]:
        gens = [s for s in seg["shots"] if s["kind"] == "generate"]
        if not gens:
            continue
        L.append("## 第 %d 段 · %s" % (seg["seg"], seg["title"]))
        L.append("")
        for s in gens:
            n += 1
            L.append("### %s（%.1fs · %s）" % (s["id"], s["dur"], s["camera_zh"]))
            L.append("")
            L.append("- 中文提示词：%s" % s["prompt"]["zh"])
            L.append("- 英文/通用标签：%s" % s["prompt"]["en"])
            L.append("- 配的字幕：%s" % (s["caption"] or "（无）"))
            L.append("")
    if not n:
        L.append("这一期素材够用，没有待生成的镜头。")
    return "\n".join(L)
