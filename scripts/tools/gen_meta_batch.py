# -*- coding: utf-8 -*-
"""批量补刷 line_meta_v2（跨作品，零 API / 零 token）。

用途：classify_raw_v2 修 bug 后（如 2026-09-13 行内裸换行展开 / eid 签名 / 多形态站点
parse_posts_auto），历史上被"行数不齐"整章跳过而缺 meta 的章节需要补生成。逐章走
translate_runner / extract_dialogue 也会自动补，但历史已译章不会再跑，故留本工具。

用法:
    python scripts/tools/gen_meta_batch.py [--work <id> ...] [--force] [--workers N] [--dry]

--work    仅处理指定作品（可多次）；缺省=全部 registry 作品
--missing 只补缺失的 meta（默认行为）；--force 覆盖重生成全部
--workers 进程并行数（**默认 2**：上万章的大批量调高会把 CPU 吃满、卡死机器；先 --dry 看量、小样本试跑）
--dry     只统计缺多少，不写文件
"""
import argparse
import importlib
import json
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "works"), _HERE):
    sys.path.insert(0, _p)

import registry                      # noqa: E402
from aa_common import txt_existing   # noqa: E402 压缩感知
import line_classify_v2 as LC        # noqa: E402

_SITE_CACHE = {}
_EID_RE = re.compile(r"(\d+)")


def _site(name):
    if name not in _SITE_CACHE:
        _SITE_CACHE[name] = importlib.import_module("sites." + name)
    return _SITE_CACHE[name]


def _bases(cfg):
    """(base, raw文件名) 列表：只统计 aa/ 有对应 txt 的章。"""
    raw_dir, aa_dir = cfg.get("raw_dir"), cfg.get("aa_dir")
    if not raw_dir or not os.path.isdir(raw_dir) or not aa_dir or not os.path.isdir(aa_dir):
        return []          # jikan_yaluo 等无 raw 的作品（aa 由交付 HTML 反解析而来）
    out = []
    for fn in sorted(os.listdir(raw_dir)):
        if not fn.endswith(".html"):
            continue
        eid = fn.replace("entry_", "").replace(".html", "")
        if not os.path.exists(txt_existing(os.path.join(aa_dir, f"entry_{eid}.txt"))):
            continue
        base = f"entry_{eid}" if cfg.get("name_style") == "entry_eid" else fn[:-5]
        out.append((base, fn))
    return out


def _work_one(job):
    """job=(work, [(base, raw_fn)...], force) → (work, ok, skip, bad, errs)"""
    work, items, force = job
    cfg = registry.get(work)
    site = _site(cfg["site"])
    meta_dir = os.path.join(cfg["work_dir"], "line_meta_v2")
    os.makedirs(meta_dir, exist_ok=True)
    ok = skip = bad = 0
    errs = []
    for base, fn in items:
        path = os.path.join(meta_dir, base + ".json")
        if not force and os.path.exists(path):
            skip += 1
            continue
        try:
            raw = open(os.path.join(cfg["raw_dir"], fn), encoding="utf-8", errors="replace").read()
            m = _EID_RE.search(fn)
            eid = m.group(1) if m else ""
            # ★ 行口径必须与**消费端**一致（aa_translate / extract_dialogue：读落盘 txt 的
            #   read().split('\n')），不能用 site.extract 重新提取——有的作品 txt 是旧版
            #   解析器提取的（空行口径/原帖版本不同），自解析虽自洽却与磁盘行号错位，
            #   生成出来的 meta 会整章错位（2026-09-13 事故：jikan_teishi/souseki_kami）。
            txt_path = os.path.join(cfg["aa_dir"], base + ".txt")
            if not os.path.exists(txt_path):
                skip += 1
                continue
            txt_lines = open(txt_path, encoding="utf-8").read().split("\n")
            meta = LC.classify_raw_v2(site, raw, txt_lines, eid=eid)
            if meta is None:
                bad += 1
                continue
            with open(path, "w", encoding="utf-8") as f:
                json.dump(LC._serialize(meta), f, ensure_ascii=False)
            ok += 1
        except Exception as e:
            bad += 1
            errs.append(f"{base}: {e}")
    return work, ok, skip, bad, errs[:3]


def _verify_one(job):
    """job=(work, [(base, raw_fn)...], fix) → (work, ok, bad, bad_list)

    对齐判据：meta 必须覆盖 3..N 连续行（N=落盘 txt 的 split('\\n') 行数）→
    len(meta) == N-2 且 max(key) == N。不满足 = 生成时行口径与磁盘不同，**整章行号错位**
    （比缺 meta 更坏：会把错位的 kind 喂给翻译），默认删除（fix=False 只报告）。"""
    work, items, fix = job
    cfg = registry.get(work)
    meta_dir = os.path.join(cfg["work_dir"], "line_meta_v2")
    ok = bad = 0
    bad_list = []
    for base, _fn in items:
        path = os.path.join(meta_dir, base + ".json")
        if not os.path.exists(path):
            continue
        txt_path = os.path.join(cfg["aa_dir"], base + ".txt")
        if not os.path.exists(txt_path):
            continue
        try:
            n = len(open(txt_path, encoding="utf-8").read().split("\n"))
            meta = json.load(open(path, encoding="utf-8"))
            keys = [int(k) for k in meta.keys() if str(k).isdigit()]
            aligned = (not keys and n <= 2) or (keys and len(keys) == n - 2 and max(keys) == n and min(keys) == 3)
            if aligned:
                ok += 1
            else:
                bad += 1
                bad_list.append(f"{base}: meta {len(keys)} 行/max {max(keys) if keys else 0} vs txt {n} 行")
                if fix:
                    os.remove(path)
        except Exception as e:
            bad_list.append(f"{base}: {e}")
    return work, ok, bad, bad_list[:3]


def _resegment_one(job):
    """job=(work, [base...], apply) → 就地按**本次数字记号修法**刷新既有 meta 的段表。

    用途：`tools/line_classify_v2.py` 的 `_rescue_inline_numeric`（2026-10-17 修）生效后，
    已有 meta 里还是旧分段（raw HTML 多已清理 → 整章重生成不可行）。

    为什么不用 `segment_line` 整行重算：那会把**历年其它判据漂移**（art_kana 表 / _demote /
    窄汉字合并…）一起灌进存量 meta（实测 ポケの子 会动 3.8 万行），与本次修法无关、风险不可控。
    做法：从**存量段表**重建逐字符 kind（保留原有粒度与空白归属）→ 只跑 `_rescue_inline_numeric`
    → 同类相邻段合并回段表（新段按 `_seg_confidence` 补 confidence）。
    只改真正变化的行；刷新后整行只剩一个 text 段 → kind 由 mixed 改 text 并删 segments
    （text 行消费端用整行）。返回 (work, 刷新章数, 改动行数, errs)
    """
    work, bases, apply = job
    cfg = registry.get(work)
    meta_dir = os.path.join(cfg["work_dir"], "line_meta_v2")
    nch = changed = 0
    errs = []
    for base in bases:
        path = os.path.join(meta_dir, base + ".json")
        src = os.path.join(cfg["aa_dir"], base + ".txt")
        if not (os.path.exists(path) and os.path.exists(src)):
            continue
        try:
            meta = json.load(open(path, encoding="utf-8"))
            lines = open(src, encoding="utf-8").read().split("\n")
            dirty = False
            for k, rec in meta.items():
                if not str(k).isdigit() or not isinstance(rec, dict):
                    continue
                old = rec.get("segments")
                if not isinstance(old, list):
                    continue                 # text/art 行没有段表，消费端用整行 → 无事可做
                i = int(k) - 1
                if not (0 <= i < len(lines)):
                    continue
                row = lines[i]
                ks = [None] * len(row)
                for sg in old:
                    for j in range(max(0, sg["start"]), min(len(row), sg["end"])):
                        ks[j] = sg["kind"]
                if any(x not in ("art", "text") for x in ks):
                    continue                 # 段表没覆盖整行（旧格式）→ 不猜，留给整章重生成
                before = list(ks)
                LC._rescue_inline_numeric(row, ks)
                if ks == before:
                    continue                 # 本次修法不涉及的行不动
                # 同类相邻合并回段表
                new, j = [], 0
                while j < len(row):
                    e = j
                    while e < len(row) and ks[e] == ks[j]:
                        e += 1
                    sg = {"start": j, "end": e, "kind": ks[j],
                          "src_width": LC.disp_width(row[j:e])}
                    conf = LC._seg_confidence(sg["kind"], row[j:e])
                    if conf < 0.65:
                        sg["confidence"] = round(conf, 2)
                    new.append(sg)
                    j = e
                if len(new) == 1 and new[0]["kind"] == "text" and new[0]["start"] == 0 \
                        and new[0]["end"] == len(row):
                    rec["kind"] = "text"
                    rec.pop("segments", None)
                    rec["reason"] = list(rec.get("reason") or []) + ["reseg_text"]
                else:
                    rec["segments"] = new
                dirty = True
                changed += 1
            if dirty:
                nch += 1
                if apply:
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(meta, f, ensure_ascii=False)
        except Exception as e:
            errs.append(f"{base}: {e}")
    return work, nch, changed, errs[:3]


def _chunks(items, n):
    """把 (base, fn) 列表切成 n 段（大作品也能并行）。"""
    size = max(1, (len(items) + n - 1) // n)
    return [items[i:i + size] for i in range(0, len(items), size)]


def main():
    ap = argparse.ArgumentParser(description="批量补刷 line_meta_v2（零 API）")
    ap.add_argument("--work", action="append", default=[], help="仅处理指定作品（可多次）")
    ap.add_argument("--force", action="store_true", help="覆盖重生成（默认只补缺失）")
    ap.add_argument("--workers", type=int, default=2,
                    help="进程并行数（默认 2；上万章的大批量别调高——曾 6 进程跑满 CPU 卡死用户机器）")
    ap.add_argument("--dry", action="store_true", help="只统计不写")
    ap.add_argument("--verify", action="store_true",
                    help="校验已有 meta 与落盘 txt 的行号对齐（错位则删；配合 --dry 只报告）")
    ap.add_argument("--resegment", action="store_true",
                    help="就地按当前 segment_line 刷新**已有** meta 的段表（切分判据修好后用；零 API、不需要 raw）")
    a = ap.parse_args()

    works = a.work or list(registry.all_works())

    if a.resegment:
        # 段级切分判据修好后刷新存量 meta（只改真正变化的行；text 行无段表 → 不动）
        rjobs = []
        for wid in works:
            cfg = registry.all_works().get(wid)
            if not cfg:
                print(f"[skip] registry 无作品 {wid}")
                continue
            meta_dir = os.path.join(cfg["work_dir"], "line_meta_v2")
            if not os.path.isdir(meta_dir):
                continue
            bases = sorted(fn[:-5] for fn in os.listdir(meta_dir) if fn.endswith(".json"))
            if not bases:
                continue
            for ch in _chunks(bases, a.workers):
                rjobs.append((wid, ch, not a.dry))
        print(f"resegment: {len(rjobs)} 个任务块 / workers={a.workers}"
              f"（{'dry-run 只报' if a.dry else '写入'}）", flush=True)
        tot_chap = tot_ch = 0
        with ProcessPoolExecutor(max_workers=a.workers) as ex:
            for work, nch, changed, errs in ex.map(_resegment_one, rjobs):
                tot_chap += nch
                tot_ch += changed
                if changed:
                    print(f"  {work[:44]:46s} 章 {nch:4d} | 行 {changed:6d}", flush=True)
                for e in errs:
                    print(f"  [bad] {work} {e}", flush=True)
        print(f"RESEGMENT DONE | 刷新章 {tot_chap} | 改动行 {tot_ch}"
              + ("（dry-run 未写）" if a.dry else ""), flush=True)
        return 0

    jobs = []
    planned = 0
    for wid in works:
        cfg = registry.all_works().get(wid)
        if not cfg:
            print(f"[skip] registry 无作品 {wid}")
            continue
        raw_dir = cfg.get("raw_dir")
        if not raw_dir or not os.path.isdir(raw_dir):
            print(f"[skip] {wid}: 无 raw_dir")
            continue
        items = _bases(cfg)
        meta_dir = os.path.join(cfg["work_dir"], "line_meta_v2")
        if not a.force:
            items = [(b, f) for b, f in items
                     if not os.path.exists(os.path.join(meta_dir, b + ".json"))]
        if not items:
            continue
        planned += len(items)
        for ch in _chunks(items, a.workers):
            jobs.append((wid, ch, a.force))
    print(f"待处理 {planned} 章 / {len(jobs)} 个任务块 / workers={a.workers}", flush=True)
    if a.dry or not jobs:
        if not a.verify:
            return 0

    if a.verify:
        # 对齐校验（零 API）：错位的 meta 比缺失更危险 → 默认删除让消费端回退 is_text_line
        vjobs = []
        for wid in works:
            cfg = registry.all_works().get(wid)
            if not cfg:
                continue
            items = _bases(cfg)
            if not items:
                continue
            for ch in _chunks(items, a.workers):
                vjobs.append((wid, ch, not a.dry))
        tot_ok = tot_bad = 0
        agg = {}
        with ProcessPoolExecutor(max_workers=a.workers) as ex:
            for work, ok, bad, bl in ex.map(_verify_one, vjobs):
                tot_ok += ok; tot_bad += bad
                if bad:
                    agg[work] = agg.get(work, 0) + bad     # 同一作品多个分块 → 累加，勿覆盖
                    for x in bl:
                        print(f"  [错位] {work} {x}", flush=True)
        print("--- 错位 meta 按作品 ---", flush=True)
        for w, b in sorted(agg.items(), key=lambda x: -x[1]):
            print(f"  {w[:44]:46s} 错位 {b:5d}", flush=True)
        print(f"VERIFY DONE | 对齐 {tot_ok} | 错位 {tot_bad}"
              + ("（未删，--dry）" if a.dry else "（已删除，消费端回退 is_text_line）"), flush=True)
        return 0

    tot_ok = tot_skip = tot_bad = 0
    agg = {}
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(_work_one, j) for j in jobs]
        for i, fu in enumerate(as_completed(futs), 1):
            work, ok, skip, bad, errs = fu.result()
            tot_ok += ok; tot_skip += skip; tot_bad += bad
            acc = agg.setdefault(work, [0, 0, 0])
            acc[0] += ok; acc[1] += skip; acc[2] += bad
            if bad:
                for e in errs:
                    print(f"  [bad] {work} {e}", flush=True)
            if i % 5 == 0 or i == len(futs):
                print(f"  进度 {i}/{len(futs)} | 已生成 {tot_ok} | 不齐/异常 {tot_bad}", flush=True)

    print("--- 按作品 ---", flush=True)
    for w, (ok, sk, bad) in sorted(agg.items(), key=lambda x: -x[1][0]):
        if ok or bad:
            print(f"  {w[:44]:46s} 生成 {ok:5d} | 跳过 {sk:4d} | 不齐/异常 {bad:4d}")
    print(f"ALL DONE | 生成 {tot_ok} | 跳过 {tot_skip} | 不齐/异常 {tot_bad}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
