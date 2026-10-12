# -*- coding: utf-8 -*-
"""Skein · 编排包导出（M4）

**Skein 只做编排，不出片。** 这里把一期编译成一个「编排包」——
一份能被别的软件直接照着做的施工单：

    编排包/
      读我.md          这个包怎么用：先生成画面，再按时间轴摆进剪辑软件
      分镜表.md        人读的施工单（段/口播稿/镜头/画面来源/运镜）
      提示词.md        待生成镜头的提示词 → 丢给即梦 / 可灵 / MJ / Runway
      时间轴.json      机读时间轴（镜头级：入点/时长/素材路径/运镜/字幕）
      字幕.srt         带时间码的字幕，剪辑软件直接导
      口播稿.txt       整篇旁白（配音 / 提词都用它）
      素材清单.md      用到的现成素材绝对路径（拖进软件就行）
      剪映草稿/        （可选）pyJianYingDraft 生成的草稿目录，能导入剪映

用法：
  python export_pack.py --topic 2                 # 导出编排包
  python export_pack.py --topic 2 --target 剪映     # 顺带生成剪映草稿
  python export_pack.py --topic 2 --copy-assets   # 把素材也复制进包里
"""
import argparse
import json
import os
import shutil
import sys

import config
import store
import brain
import arrange

PACK_DIR = os.path.join(config.app_dir(), "packs")
SEC = 1000000          # 剪映时间单位：微秒


# ------------------------- 素材归集 -------------------------
def _usable(m):
    p = (m.get("path") or "").strip()
    if not p or not os.path.exists(p):
        return None
    k = m.get("kind") or config.kind_of(p)
    if k not in ("image", "video"):
        return None
    return (p, k) if arrange.check_media(p, k)[0] else None


def collect(tid, extra_dirs=None):
    """topic → segments（编排引擎的输入）。段内素材优先，不足从公共池借。"""
    topic = store.get_topic(tid)
    beats = store.list_placed(tid)
    mbb = store.materials_by_beat(tid)
    shared = []
    for m in (store.list_unassigned(tid) or []):
        u = _usable(m)
        if u:
            shared.append(u)
    for d in (extra_dirs or []):
        if os.path.isdir(d):
            for n in sorted(os.listdir(d)):
                p = os.path.join(d, n)
                k = config.kind_of(p)
                if k in ("image", "video") and arrange.check_media(p, k)[0]:
                    shared.append((p, k))

    segments, report = [], []
    for b in beats:
        own = [u for u in (_usable(m) for m in (mbb.get(b["id"]) or [])) if u]
        borrowed = 0
        while len(own) < 2 and shared:
            own.append(shared.pop(0))
            borrowed += 1
        body = (b.get("script") or "").strip()
        if not body:
            body = brain.script_for(topic, b, mbb.get(b["id"]) or []) or ""
        if not body:
            body = (b.get("question") or "").strip()
        segments.append({
            "id": b["id"],
            "title": arrange._short(b.get("question") or "", 16),
            "question": b.get("question") or "",
            "body": body,
            "hint": (b.get("shot") or "").strip(),
            "pool": own,
        })
        report.append("第 %d 段：现成画面 %d 个%s"
                      % (len(segments), len(own) - borrowed,
                         "（公共池借 %d）" % borrowed if borrowed else ""))
    return topic, segments, report


# ------------------------- 剪映草稿（可选） -------------------------
def jianying_draft(arr, out_dir, name):
    """用 pyJianYingDraft 生成一个剪映草稿目录。失败就返回原因，不拖累整包。"""
    try:
        from pyJianYingDraft import (DraftFolder, VideoSegment, VideoMaterial,
                                     TextSegment, TextStyle, Timerange,
                                     TrackSpec, TrackType, ClipSettings)
    except Exception as ex:
        return None, "没装 pyJianYingDraft（pip install pyJianYingDraft）：%s" % ex
    try:
        folder = os.path.join(out_dir, "剪映草稿")
        os.makedirs(folder, exist_ok=True)
        df = DraftFolder(folder)
        script = df.create_draft(name[:50], 1080, 1920, fps=30,
                                 allow_replace=True)   # 重导直接覆盖，别报"已存在"
        vtrack = script.append_track(TrackSpec(TrackType.video, "主视频轨"))
        ttrack = script.append_track(TrackSpec(TrackType.text, "字幕轨"))

        # 时间用整数微秒**累加**，避免浮点取整造成 1ms 重叠（重叠会被库直接拒，
        # 结果就是最后一个字幕静默消失）
        acc, fails = 0, []
        for s in arr["shots"]:
            st = acc
            du = int(round(max(0.5, s["dur"]) * SEC))
            acc += du
            if s.get("source") and s["source"].get("path"):
                try:
                    mat = VideoMaterial(s["source"]["path"])
                    script.add_segment(
                        VideoSegment(mat, Timerange(st, du),
                                     source_timerange=Timerange(0, du)), vtrack)
                except Exception as ex:
                    fails.append("%s 画面：%s" % (s["id"], ex))
                    continue
            if s.get("caption"):
                try:
                    script.add_segment(
                        TextSegment(s["caption"], Timerange(st, du),
                                    style=TextStyle(size=7.0, bold=True,
                                                    color=(1.0, 1.0, 1.0), align=1),
                                    clip_settings=ClipSettings(transform_y=-0.72)),
                        ttrack)
                except Exception as ex:
                    fails.append("%s 字幕：%s" % (s["id"], ex))
        script.dump(os.path.join(folder, name[:50], "draft_content.json"))
        return os.path.join(folder, name[:50]), ("；".join(fails[:3]) if fails else "")
    except Exception as ex:
        return None, "剪映草稿没生成：%s" % ex


# ------------------------- 打包 -------------------------
def _readme(arr, topic, jy, jy_err):
    t = (topic or {}).get("name", "这一期")
    L = ["# 编排包 · %s" % t, "",
         "Skein 只做**编排**，不出片 —— 这个包是给别的软件照着做的施工单。",
         "总时长 ≈ %.1fs ｜ %d 个镜头（现成 %d · 待生成 %d）"
         % (arr["total"], len(arr["shots"]),
            arr["count"]["material"], arr["count"]["generate"]),
         "",
         "## 怎么用", "",
         "1. **先补画面**：打开 `提示词.md`，把标了「待生成」的镜头丢给即梦 / 可灵 / "
         "MJ / Runway 去生成，**按镜头号命名**存好。",
         "   （画面内容由 Skein 定好了，照着做就行，别临场改 —— 改了就跑出编排之外。）",
         "2. **摆时间轴**：",
         "   - 剪映：`剪映草稿/` 里有现成草稿（见下），或者照 `时间轴.json` / `分镜表.md` 手动摆。",
         "   - 其它软件（Pr / 达芬奇 / 必剪）：照 `时间轴.json` 的入点摆，`字幕.srt` 直接导。",
         "3. **配音**：`口播稿.txt` 整篇旁白，丢给任意 TTS，或直接自己念。",
         "4. **画面不重复**：编排阶段就保证了同一个素材只排进一个镜头，"
         "`素材清单.md` 里每个文件只出现一次。",
         ""]
    if jy:
        L += ["## 剪映草稿", "",
              "路径：`%s`" % jy,
              "把它整个目录拷到剪映的草稿目录下（剪映设置里能看到草稿路径），重启剪映就能看到。",
              ""]
    elif jy_err:
        L += ["## 剪映草稿", "", "这次没生成：%s" % jy_err,
              "不影响其它文件 —— 照 `分镜表.md` 手动摆一样能用。", ""]
    L += ["## 文件", "",
          "| 文件 | 干什么 |", "|---|---|",
          "| 分镜表.md | 人读施工单：段 / 口播稿 / 镜头 / 画面来源 / 运镜 |",
          "| 提示词.md | 待生成画面的提示词（给 AI 生图/生视频软件） |",
          "| 时间轴.json | 机读时间轴：镜头级入点、时长、素材路径、字幕 |",
          "| 字幕.srt | 带时间码字幕，剪辑软件直接导 |",
          "| 口播稿.txt | 整篇旁白 |",
          "| 素材清单.md | 用到的现成素材（绝对路径） |", ""]
    return "\n".join(L)


def export(tid, out_dir=None, want_jianying=False, copy_assets=False,
           extra_dirs=None):
    store.init()
    topic, segments, report = collect(tid, extra_dirs)
    if not segments:
        return {"dir": "", "warnings": ["这一期还没有排好的段"], "report": report}
    name = "".join(c for c in ((topic or {}).get("name") or "topic")
                   if c not in '\\/:*?"<>|').strip()[:40] or ("topic-%s" % tid)
    out_dir = out_dir or os.path.join(PACK_DIR, "topic-%s" % tid)
    os.makedirs(out_dir, exist_ok=True)

    arr = arrange.build(segments, (topic or {}).get("name", ""))

    def w(rel, txt):
        with open(os.path.join(out_dir, rel), "w", encoding="utf-8") as f:
            f.write(txt)

    jy, jy_err = (None, "")
    if want_jianying:
        jy, jy_err = jianying_draft(arr, out_dir, name)

    w("读我.md", _readme(arr, topic, jy, jy_err))
    w("分镜表.md", arrange.shot_table_md(arr))
    w("提示词.md", arrange.prompts_md(arr))
    w("时间轴.json", json.dumps(arr, ensure_ascii=False, indent=2))
    w("字幕.srt", arrange.srt(arr))
    w("口播稿.txt", "\n\n".join(
        "【第 %d 段】%s" % (s["seg"], s["script"]) for s in arr["segments"]))

    used = [(s["source"]["path"], s["source"]["name"])
            for s in arr["shots"] if s.get("source")]
    lines = ["# 素材清单", "",
             "共 %d 个现成画面，**每个只出现一次**。" % len(used), "",
             "| 镜头 | 文件 | 绝对路径 |", "|---|---|---|"]
    for s in arr["shots"]:
        if s.get("source"):
            lines.append("| %s | %s | `%s` |"
                         % (s["id"], s["source"]["name"], s["source"]["path"]))
    w("素材清单.md", "\n".join(lines))

    if copy_assets and used:
        ad = os.path.join(out_dir, "素材")
        os.makedirs(ad, exist_ok=True)
        for p, n in used:
            try:
                shutil.copy(p, os.path.join(ad, n))
            except Exception:
                pass

    return {"dir": out_dir, "arr": arr, "warnings": arr["warnings"],
            "report": report, "jianying": jy, "jianying_error": jy_err}


# ------------------------- 回填：生成好的画面放回时间轴 -------------------------
def fill(tid, gen_dir=None, out_dir=None):
    """AI 软件生成完画面后，按**镜头号**把它们放回时间轴，重出分镜表与剪映草稿。

    gen_dir 里放 `S1-2.png` / `S2-3.png` 这样以镜头号命名的图
    （生成时就按镜头号存，这一步就是零成本的）。
    返回 {"filled":n, "left":[...], "dir":...}
    """
    store.init()
    out_dir = out_dir or os.path.join(PACK_DIR, "topic-%s" % tid)
    arr_path = os.path.join(out_dir, "时间轴.json")
    if not os.path.exists(arr_path):
        return {"filled": 0, "left": [], "dir": "",
                "error": "还没有编排包，先跑一次 export_pack"}
    with open(arr_path, "r", encoding="utf-8") as f:
        arr = json.load(f)
    gen_dir = gen_dir or os.path.join(out_dir, "生成画面")
    if not os.path.isdir(gen_dir):
        return {"filled": 0, "left": [], "dir": out_dir,
                "error": "没有生成画面目录：%s" % gen_dir}

    filled, left = 0, []
    for s in arr["shots"]:
        if s["kind"] != "generate":
            continue
        hit = None
        for ext in (".png", ".jpg", ".jpeg", ".webp", ".mp4"):
            p = os.path.join(gen_dir, s["id"] + ext)
            if os.path.exists(p):
                hit = p
                break
        if not hit:                      # 退一步：目录里名字带镜头号的也算
            for n in sorted(os.listdir(gen_dir)):
                if n.startswith(s["id"]):
                    hit = os.path.join(gen_dir, n)
                    break
        if not hit:
            left.append(s["id"])
            continue
        s["kind"] = "material"
        s["source"] = {"path": hit, "name": os.path.basename(hit)}
        s["prompt"] = None
        filled += 1

    arr["count"] = {
        "material": sum(1 for s in arr["shots"] if s["kind"] == "material"),
        "generate": sum(1 for s in arr["shots"] if s["kind"] == "generate"),
    }

    def w(rel, txt):
        with open(os.path.join(out_dir, rel), "w", encoding="utf-8") as f:
            f.write(txt)

    w("时间轴.json", json.dumps(arr, ensure_ascii=False, indent=2))
    w("分镜表.md", arrange.shot_table_md(arr))
    w("提示词.md", arrange.prompts_md(arr))
    lines = ["# 素材清单", "",
             "共 %d 个画面（含 AI 生成后回填的），**每个只出现一次**。" %
             sum(1 for s in arr["shots"] if s.get("source")), "",
             "| 镜头 | 文件 | 绝对路径 |", "|---|---|---|"]
    for s in arr["shots"]:
        if s.get("source"):
            lines.append("| %s | %s | `%s` |"
                         % (s["id"], s["source"]["name"], s["source"]["path"]))
    w("素材清单.md", "\n".join(lines))

    jy, jy_err = (None, "")
    if not left:                          # 全齐了才重出草稿，免得半套
        t = store.get_topic(tid)
        nm = "".join(c for c in ((t or {}).get("name") or "topic")
                     if c not in '\\/:*?"<>|').strip()[:40] or ("topic-%s" % tid)
        jy, jy_err = jianying_draft(arr, out_dir, nm)
        w("读我.md", _readme(arr, t, jy, jy_err))
    return {"filled": filled, "left": left, "dir": out_dir,
            "jianying": jy, "jianying_error": jy_err}


def main():
    ap = argparse.ArgumentParser(description="Skein 编排包导出")
    ap.add_argument("--topic", type=int, required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--target", default="全部", choices=["全部", "剪映"])
    ap.add_argument("--copy-assets", action="store_true")
    ap.add_argument("--stills", default="", help="外挂图片目录")
    ap.add_argument("--fill", nargs="?", const="", default=None,
                    help="回填：把该目录里按镜头号命名的生成图放回时间轴"
                         "（不给目录就用包里的 生成画面/）")
    a = ap.parse_args()

    if a.fill is not None:
        r = fill(a.topic, a.fill or None, a.out or None)
        if r.get("error"):
            print("回填失败：" + r["error"])
            return 1
        print("回填 %d 个镜头，还差 %s" % (r["filled"], r["left"] or "无"))
        if r.get("jianying"):
            print("剪映草稿已重出：%s" % r["jianying"])
        elif r.get("jianying_error"):
            print("! %s" % r["jianying_error"])
        return 0

    res = export(a.topic, a.out or None, a.target == "剪映", a.copy_assets,
                 [a.stills] if a.stills else None)
    for r in res.get("report") or []:
        print("  " + r)
    if not res.get("dir"):
        print("没导出：" + "；".join(res.get("warnings") or []))
        return 1
    print("\n编排包：%s" % res["dir"])
    arr = res.get("arr") or {}
    print("  总时长 ≈ %.1fs ｜ 镜头 %d（现成 %d · 待生成 %d）"
          % (arr.get("total", 0), len(arr.get("shots") or []),
             (arr.get("count") or {}).get("material", 0),
             (arr.get("count") or {}).get("generate", 0)))
    for w_ in res.get("warnings") or []:
        print("  ! " + w_)
    if res.get("jianying"):
        print("  剪映草稿：%s" % res["jianying"])
    elif res.get("jianying_error"):
        print("  ! %s" % res["jianying_error"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
