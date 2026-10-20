#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""scan_seg_misclass：统计「meta 段级误标」——零假名的 AA 笔画碎片被切成 text 段（**只统计，不改写**）。

判据见 `aa_translate.seg_mark_art`（零假名 + 非引号术语 + ≤4 字 + 两侧紧邻 art + 无数字/ASCII）。
这些段送模型只会被原样照抄（偶尔译成汉字 → 画面损坏），却会把「漏译体检」灌成噪声。

用法:
  python scripts/tools/scan_seg_misclass.py --work <id> [--top 20] [--json <path>]
  python scripts/tools/scan_seg_misclass.py --all-works [--limit 5]

零 API、不写任何作品文件（只读 `work/line_meta_v2` 与 `aa/*.txt`）。
"""
import argparse
import collections
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "works")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import aa_translate as T          # noqa: E402
import registry                   # noqa: E402


def scan_work(work, top=20, quiet=False):
    cfg = registry.get(work)
    if not cfg:
        return {"work": work, "error": "未知作品"}
    T.AA_DIR, T.OUT_DIR, T.WORK_DIR = cfg["aa_dir"], cfg["out_dir"], cfg["work_dir"]
    T.WORK_ID = work
    mdir = os.path.join(cfg["work_dir"], "line_meta_v2")
    if not os.path.isdir(mdir):
        return {"work": work, "error": "无 line_meta_v2"}
    cnt = collections.Counter()
    samples = {}
    n_seg = n_text = n_hit = 0
    for base in T.meta_list(mdir):
        sp = T.txt_existing(os.path.join(cfg["aa_dir"], base + ".txt"))
        if not os.path.exists(sp):
            continue
        src = T.txt_read(sp)
        segs_all = T.load_meta_segments_0based(mdir, base) or {}
        for i, segs in segs_all.items():
            if i >= len(src):
                continue
            row = src[i]
            for si, sg in enumerate(segs):
                n_seg += 1
                if sg.get("kind") != "text":
                    continue
                n_text += 1
                if T.seg_mark_art(row, segs, si):
                    n_hit += 1
                    t = row[sg["start"]:sg["end"]].strip(" \u3000")
                    cnt[t] += 1
                    samples.setdefault(t, (base, i + 1))
    rec = {"work": work, "segs": n_seg, "text_segs": n_text, "misclass": n_hit,
           "unique": len(cnt),
           "rate": round(n_hit / n_text * 100, 2) if n_text else 0.0,
           "top": [{"seg": t, "n": c, "sample": "%s:%d" % samples[t]} for t, c in cnt.most_common(top)]}
    if not quiet:
        print("[%s] 段 %d / text 段 %d | 误标 %d (%.2f%%) | 去重 %d"
              % (work, n_seg, n_text, n_hit, rec["rate"], len(cnt)), flush=True)
        for it in rec["top"][:top]:
            print("    %-10s ×%-4d %s" % (it["seg"][:10], it["n"], it["sample"]), flush=True)
    return rec


def main():
    ap = argparse.ArgumentParser(description="统计 meta 段级误标（只统计不改写）")
    ap.add_argument("--work")
    ap.add_argument("--all-works", action="store_true")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--limit", type=int, default=0, help="--all-works 时只扫前 N 部（0=全扫）")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    works = []
    if a.work:
        works = [a.work]
    elif a.all_works:
        works = [w for w in registry.all_works()] if hasattr(registry, "all_works") else []
        if not works:
            try:
                works = sorted({k for k in registry.load().keys()})
            except Exception:
                works = []
        if a.limit:
            works = works[: a.limit]
    if not works:
        print("用法: --work <id> 或 --all-works")
        return 2
    recs = [scan_work(w, a.top) for w in works]
    ok = [r for r in recs if "error" not in r]
    if ok:
        print("[汇总] 作品 %d | text 段 %d | 误标 %d (%.2f%%)"
              % (len(ok), sum(r["text_segs"] for r in ok), sum(r["misclass"] for r in ok),
                 sum(r["misclass"] for r in ok) / max(1, sum(r["text_segs"] for r in ok)) * 100))
    if a.json:
        json.dump(recs, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("[json]", a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
