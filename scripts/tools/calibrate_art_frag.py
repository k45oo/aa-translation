# -*- coding: utf-8 -*-
"""艺术**片段表**标定 CLI（丙，零 API）——实现见 `scripts/common/aa_art_kana.py`。

表 `work/art_frag_<work>.json`：把「本作品里反复出现的纯假名短笔画」记下来，
命中即本地照抄、不送模型（`AA_ART_FRAG=0` 关）。

数据源两条：
  ① `work/edits.jsonl` 里 **中文字 → 假名** 的还原 span —— 用户在阅读器里亲手改回原文，
     等于亲口声明「这是画」= 真值，直接进表；
  ② 语料弱证据：整行产物 == 原文 的行里出现的短段（art 计数），要求 art ≥ --min-art
     且 art/(art+lost) ≥ --ratio。

用法:
  python scripts/tools/calibrate_art_frag.py --work <id>                  # 落盘 + 报告
  python scripts/tools/calibrate_art_frag.py --work <id> --report         # 只报告
  python scripts/tools/calibrate_art_frag.py --work <id> --from-edits-only
  python scripts/tools/calibrate_art_frag.py --dir <作品根目录> [--report]
选项: --min-art 3  --ratio 0.5  --top 60  --no-write
"""
import argparse
import difflib
import gzip
import io
import json
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(BASE, "scripts", "common"))
sys.path.insert(0, os.path.join(BASE, "scripts", "works"))
import aa_art_kana as AK          # noqa: E402
import aa_common as AC            # noqa: E402  list_txt / load_segments_meta（压缩感知）

CJK_RE = re.compile("[\u4e00-\u9fff]")
KANA_RE = re.compile("[\u3041-\u309f\u30a1-\u30fa\u30fc\uff61-\uff9f]")


def _read_lines(path):
    op = gzip.open if path.endswith(".gz") else io.open
    kw = {} if path.endswith(".gz") else {"encoding": "utf-8"}
    with op(path, "rt", **kw) as f:
        return f.read().split("\n")


def _base_of(name):
    for suf in (".txt.gz", ".txt"):
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


def _tr_path(tr_dir, base):
    for cand in (os.path.join(tr_dir, base + "_汉化.txt"),
                 os.path.join(tr_dir, base + "_汉化.txt.gz")):
        if os.path.exists(cand):
            return cand
    return None


def _work_dirs(work="", dir_arg=""):
    if dir_arg:
        root = os.path.abspath(dir_arg)
        return (os.path.join(root, "aa"), os.path.join(root, "汉化"),
                os.path.join(root, "work"), os.path.basename(root))
    import registry
    cfg = registry.get(work)
    if not cfg:
        raise SystemExit("未注册作品: " + work)
    return cfg["aa_dir"], cfg["out_dir"], cfg["work_dir"], work


def build_rows(aa_dir, tr_dir, work_dir, with_corpus=True):
    """[(src_line, tr_line, segs_or_None)] —— 只取两侧行数相等的章。"""
    rows, chapters, skipped = [], 0, 0
    if not with_corpus:
        return rows, chapters, skipped
    for fn in AC.list_txt(aa_dir):
        base = _base_of(fn)
        if base.endswith("_台词"):
            continue
        tp = _tr_path(tr_dir, base)
        if not tp:
            continue
        src, tr = _read_lines(os.path.join(aa_dir, fn)), _read_lines(tp)
        if len(src) != len(tr):
            skipped += 1
            continue
        chapters += 1
        segs_map = AC.load_segments_meta(os.path.join(work_dir, "line_meta_v2"), base)
        for i, s in enumerate(src):
            if s.startswith("#"):
                continue
            rows.append((s, tr[i], segs_map.get(i)))
    return rows, chapters, skipped


def edits_restore_texts(work_dir):
    """`edits.jsonl` 里「中文字 → 假名」的还原片段（用户确认的艺术笔画）。

    实现放在 `aa_art_kana.restore_spans_from_edits`（运行时自更新也用它），此处只包一层。
    """
    p = os.path.join(work_dir, "edits.jsonl")
    texts = AK.restore_spans_from_edits(p)
    return texts, len(texts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="", help="作品 id（works/registry.py）")
    ap.add_argument("--dir", default="", help="作品根目录（不走 registry）")
    ap.add_argument("--report", action="store_true", help="只打印，不落盘")
    ap.add_argument("--no-write", action="store_true", help="同 --report")
    ap.add_argument("--from-edits-only", action="store_true", help="只用人工修正，不扫语料")
    ap.add_argument("--min-art", type=int, default=AK.FRAG_MIN_ART)
    ap.add_argument("--ratio", type=float, default=AK.FRAG_RATIO_MIN)
    ap.add_argument("--top", type=int, default=60)
    args = ap.parse_args()
    if not args.work and not args.dir:
        ap.error("需要 --work <id> 或 --dir <目录>")

    aa_dir, tr_dir, work_dir, wid = _work_dirs(args.work, args.dir)
    rows, chapters, skipped = build_rows(aa_dir, tr_dir, work_dir,
                                         with_corpus=not args.from_edits_only)
    texts, n_spans = edits_restore_texts(work_dir)
    frags = AK.mine_frags(rows, texts, min_art=args.min_art, ratio_min=args.ratio)
    by_src = {}
    for frag, rec in frags.items():
        by_src[rec["src"]] = by_src.get(rec["src"], 0) + 1
    out = {"schema": 1, "kind": "art_frag", "date": time.strftime("%Y-%m-%d %H:%M:%S"),
           "work": wid, "chapters": chapters, "skipped": skipped,
           "rows": len(rows), "edits_spans": n_spans,
           "params": {"min_art": args.min_art, "ratio": args.ratio},
           "frags": frags}
    print("[frag] %s 章 %d 行 %d | edits 还原 span %d | 片段 %d (%s)"
          % (wid, chapters, len(rows), n_spans, len(frags),
             " ".join("%s=%d" % kv for kv in sorted(by_src.items())) or "-"), flush=True)
    top = sorted(frags.items(), key=lambda kv: (-kv[1]["edits"], -kv[1]["art"]))[:args.top]
    for frag, rec in top:
        print("  %-8r src=%-6s edits=%-4d art=%-4d kept=%-4d lost=%-4d"
              % (frag, rec["src"], rec["edits"], rec["art"], rec["kept"], rec["lost"]))
    if args.report or args.no_write:
        return 0
    p = os.path.join(work_dir, "art_frag_%s.json" % wid)
    os.makedirs(work_dir, exist_ok=True)
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, sort_keys=True, indent=1)
    print("[frag] 写入 %s" % p, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
