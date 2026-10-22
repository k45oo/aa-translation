# -*- coding: utf-8 -*-
"""fix_pad_art_shift: 修「补位位置规范化」在**艺术行**上误伤导致的错位（零 API）。

背景（2026-10-19 用户实测 entry_48695 第 2040/2041 行「还原原文显示成功但实际没变」）
    `aa_translate.normalize_pad_placement` 的旧 `_INNER_PAD` 判据只要求「空格左邻是
    中日文/字母/数字、右邻是运算符+同类字符」——AA 画里 `二二二二　　　　　/i:i:`、
    `兆兆兆　　／兆兆`、`rx -7ニ` 全部命中，于是把画的内侧间距搬到行尾（宽度不变、
    残留/照抄/art 列位三个局部校验全过）→ 图歪了。
    它挂在 `txt_write`（**每次写 `*_汉化.txt` 都会跑**）上，所以编辑任意一行都会把本章
    这些行再改坏一次，连「右键行号 = 还原原文」都会被自己改坏 → 用户看到「成功但没变」。

判据（本工具只动**确定没有任何译文**的行）
    `tr != src` 且「去掉全部空白后逐字符相同」且「显示宽度相同」→ 该行只是间距被搬了位置
    （art 行本该等于原文；未译行同理）→ 还原成 src。
    护栏：① 该行若是**人工编辑过且现内容就是那次编辑的结果**（edits.jsonl 最新 `after` == 现值）
    跳过——尊重人工挪过的间距；② 行数不齐跳过；③ 宽度不同的行不动。

默认 dry-run；`--apply` 才写盘（备份 `work/backup_padshift_<ts>/` → 汉化 + ckpt 同步 +
`work/edits.jsonl` 流水（type=错位，阅读器可逐行还原）→ 备份目录只留最近 1 次）。

用法:
  python scripts/fix/fix_pad_art_shift.py --work ポケの子 --dry-run
  python scripts/fix/fix_pad_art_shift.py --work ポケの子 --base entry_48695 --apply
  python scripts/fix/fix_pad_art_shift.py --all-works          # 全库计数（dry-run）
  python scripts/fix/fix_pad_art_shift.py --work ポケの子 --eids 48695,48694 --apply
"""
import argparse
import json
import os
import shutil
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "works")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import aa_common as C          # noqa: E402
import aa_translate as T       # noqa: E402
import registry                # noqa: E402

BAK_PREFIX = "backup_padshift_"


def strip_ws(s):
    """去掉全部空白字符（跨行比较的基准，与 logic.classify_edit 的错位判据同口径）。"""
    return "".join(c for c in (s or "") if not c.isspace())


def _human_locked(cfg, fname):
    """{行号(1-based): 现值} —— edits.jsonl 里最新一条 `after` 就是那个人工编辑值。

    这些行的现值是**人工改出来的**（可能是人工挪的间距），本工具一律不碰。
    """
    out = {}
    p = os.path.join(cfg["work_dir"], "edits.jsonl")
    if not os.path.exists(p):
        return out
    try:
        with open(p, encoding="utf-8") as f:
            for ln in f:
                if not ln.strip():
                    continue
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if r.get("file") == fname:
                    out[r.get("line")] = r.get("after")
    except OSError:
        return out
    return out


def scan_chapter(cfg, base):
    """→ (src, tr, hits)；hits = [(idx0, before, after)]（只看不动）。"""
    sp = T.txt_existing(os.path.join(cfg["aa_dir"], base + ".txt"))
    tp = T.txt_existing(os.path.join(cfg["out_dir"], base + "_汉化.txt"))
    if not (sp and tp):
        return None, None, []
    src, tr = T.txt_read(sp), T.txt_read(tp)
    if len(src) != len(tr):
        return src, tr, []
    locked = _human_locked(cfg, base + "_汉化.txt")
    hits = []
    for i, (s, t) in enumerate(zip(src, tr)):
        if s == t or not s:
            continue
        if strip_ws(s) != strip_ws(t):                 # 有译文差异 → 不是纯间距错位
            continue
        if T.disp_width(s) != T.disp_width(t):         # 宽度不同 → 交给 scan_width/fix_width
            continue
        if locked.get(i + 1) == t:                     # 现值就是人工编辑的结果 → 尊重
            continue
        hits.append((i, t, s))
    return src, tr, hits


def apply_chapter(work, cfg, base, src, tr, hits):
    """写盘：备份 → 汉化 + ckpt + edits.jsonl。返回落地行数。"""
    tp = T.txt_existing(os.path.join(cfg["out_dir"], base + "_汉化.txt"))
    ckp = os.path.join(cfg["work_dir"], base + ".txt.ckpt.json")
    ts = time.strftime("%Y%m%d_%H%M%S")
    bak = os.path.join(cfg["work_dir"], BAK_PREFIX + ts)
    os.makedirs(bak, exist_ok=True)
    shutil.copy2(tp, os.path.join(bak, os.path.basename(tp)))
    ck = {}
    if os.path.exists(ckp):
        shutil.copy2(ckp, os.path.join(bak, base + ".txt.ckpt.json"))
        try:
            with open(ckp, encoding="utf-8") as _f:
                ck = json.load(_f)
        except Exception:
            ck = {}
    new = list(tr)
    for i, _before, after in hits:
        new[i] = after
        if str(i) in ck:
            ck[str(i)] = after
    T.txt_write_form(tp, new)
    if ck:
        ck["__lines"] = len(src)
        T._atomic_write_ckpt(ckp, ck)
    ts_h = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(os.path.join(cfg["work_dir"], "edits.jsonl"), "a", encoding="utf-8") as f:
        for i, before, after in hits:
            f.write(json.dumps({"ts": ts_h, "work": cfg.get("title") or work,
                                "file": base + "_汉化.txt", "line": i + 1, "type": "错位",
                                "line_type": "", "before": before, "after": after},
                               ensure_ascii=False) + "\n")
    try:
        T.prune_backup_dirs(cfg["work_dir"], BAK_PREFIX)
    except Exception:
        pass
    return len(hits)


def bases_of(cfg, a):
    """选定章节（--base / --eids / 默认全部已汉化章）。"""
    if a.base:
        return [a.base]
    if a.eids:
        return ["entry_" + e.strip() for e in a.eids.split(",") if e.strip()]
    out = []
    for b in T.list_txt(cfg["aa_dir"]):
        if b.endswith("_台词.txt") or b.endswith("_汉化.txt"):
            continue
        base = b[:-4] if b.endswith(".txt") else b
        if os.path.exists(C.txt_existing(os.path.join(cfg["out_dir"], base + "_汉化.txt"))):
            out.append(base)
    return out


def run_work(work, a, summary):
    cfg = registry.get(work)
    n_hit = n_ch = 0
    for base in bases_of(cfg, a):
        try:
            src, tr, hits = scan_chapter(cfg, base)
        except Exception as e:
            print(f"  [{base}] 跳过({type(e).__name__}: {e})", flush=True)
            continue
        if not hits:
            continue
        if a.limit:
            hits = hits[:a.limit]
        n_ch += 1
        n_hit += len(hits)
        shown = ", ".join(str(i + 1) for i, _b, _x in hits[:8])
        print(f"  [{base}] {len(hits)} 行错位：{shown}{' …' if len(hits) > 8 else ''}", flush=True)
        if a.apply:
            n = apply_chapter(work, cfg, base, src, tr, hits)
            print(f"    -> 已还原 {n} 行（备份 {BAK_PREFIX}<ts>，流水 edits.jsonl type=错位）", flush=True)
    summary.append((work, n_ch, n_hit))
    return n_hit


def main():
    ap = argparse.ArgumentParser(description="修「补位位置规范化」在艺术行上造成的错位（零 API）")
    ap.add_argument("--work", help="作品 id（registry）")
    ap.add_argument("--all-works", action="store_true", help="全部作品（默认 dry-run 计数）")
    ap.add_argument("--base", help="单章 base（entry_xxx）")
    ap.add_argument("--eids", help="章节 eid 列表，逗号分隔")
    ap.add_argument("--apply", action="store_true", help="写盘（默认 dry-run）")
    ap.add_argument("--limit", type=int, default=0, help="每章最多改几行（排查用）")
    a = ap.parse_args()

    works = [a.work] if a.work else (list(registry.WORKS) if a.all_works else [])
    if not works:
        print("[error] 需要 --work <id> 或 --all-works", file=sys.stderr)
        return 2
    print(f"[padshift] {'APPLY' if a.apply else 'dry-run'} · {len(works)} 部作品", flush=True)
    summary = []
    for w in works:
        try:
            registry.get(w)
        except BaseException as e:
            print(f"[skip] {w}: {str(e)[:40]}", flush=True)
            continue
        run_work(w, a, summary)
    tot_ch = sum(c for _w, c, _n in summary)
    tot_ln = sum(n for _w, _c, n in summary)
    print(f"\n[padshift] 合计 {tot_ch} 章 / {tot_ln} 行"
          + ("（已还原）" if a.apply else "（dry-run，加 --apply 才写盘）"), flush=True)
    for w, c, n in summary:
        if n:
            print(f"   {w}: {c} 章 / {n} 行", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
