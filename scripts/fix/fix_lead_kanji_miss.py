#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""按「首/尾文字漏译」清单定向补译（2026-09-16）。

背景：ART_KANJI 汉字（一/二/人/王/日/入…）被判成 art 段时**不进模型**，落在行首/行尾
就成了「第一个字没译」（成品里 `日日本的黄瓜…` / `一也是一般情况…`）。
段边界已由 `tools/line_classify_v2.py` 修好（紧贴汉字回并进 text 段），本工具负责把
**已经交付**的那些行按新段边界重译一遍——只动清单里的行，art 段原样保留。

清单来源：`temp/count_lead_miss.py`（扫全库成品，判据 = 新段把汉字并进 text 段 且 汉化行同列
仍是原日文汉字）产出的 JSONL：{"work","base","line","src","tr","miss"}。

机制：复用生产段级协议 `aa_translate.translate_lines_mixed`（同一套校验：段级原样照抄 /
超宽 / 比 < MIXED_SHRINK_MIN_RATIO 的灾难性丢内容 / 整行宽 == SRC / art 列位不变 / text 段残留；
3 轮重试，末轮「接受部分成功」= 不合格段退原文，行照常产出），只把通过校验的行写回；
写回前整章备份到 `work/backup_fix_lead_kanji_<ts>/`。

用法（仓库根运行）：
  python scripts/fix/fix_lead_kanji_miss.py --rows temp/lead_miss_bad.jsonl            # 空跑，打印差异
  python scripts/fix/fix_lead_kanji_miss.py --rows temp/lead_miss_bad.jsonl --apply    # 写回
  python scripts/fix/fix_lead_kanji_miss.py --rows X.jsonl --work bar_miku --limit 10  # 先试跑 10 行
"""
import json
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.path.insert(0, os.path.join(ROOT, 'scripts', 'common'))
sys.path.insert(0, os.path.join(ROOT, 'scripts', 'works'))
sys.path.insert(0, os.path.join(ROOT, 'scripts', 'tools'))

import aa_translate as T          # noqa: E402
import registry                    # noqa: E402


def _arg(name, default=None):
    a = sys.argv
    if name in a and a.index(name) + 1 < len(a):
        return a[a.index(name) + 1]
    return default


def cols_of(line):
    """显示列 → 字符（[(col, ch, width)]）。"""
    out = []
    col = 0
    for ch in line:
        w = T.disp_width(ch)
        out.append((col, ch, w))
        col += w
    return out


def cols_slice(line, c0, c1):
    """取显示列区间 [c0, c1) 的字符。"""
    return ''.join(ch for col, ch, w in cols_of(line) if col >= c0 and col + w <= c1)


def _miss_seg_changed(src_row, cand, segs, miss):
    """补译后，覆盖漏字列的那个 text 段是否真的变了（判「修好了没」）。

    刻意**不**用「同列是否仍是原日文汉字」做判据——一/日/人 在中文里同样是常用字
    （`一般来说是这样的。` 完全合法），按字面比会把好译文判成失败（实测踩过）。
    段列位比较是安全的：rebuild 保证 art 列位不变、text 段按 src_width pad 到原位。
    """
    cols = [m['col'] for m in (miss or [])] or [0]
    for si in T._confident_text_segs(segs):
        sg = segs[si]
        c0 = T.disp_width(src_row[:sg['start']])
        if not any(c0 <= c < c0 + sg['src_width'] for c in cols):
            continue
        old = cols_slice(src_row, c0, c0 + sg['src_width'])
        new = cols_slice(cand, c0, c0 + sg['src_width'])
        return new != old
    return False        # 没有 text 段覆盖漏字列 → 段划分没生效


def load_rows(path):
    rows = []
    with open(path, encoding='utf-8') as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                rows.append(json.loads(ln))
    return rows


class _WorkResolver:
    """清单里的 work 是 aaManga 目录名（count 工具按 meta 路径取），registry id 往往不同
    （bar_miku ↔ BAR初音ミクへようこそR）→ 先按 id 查，再按目录名反查。"""

    def __init__(self):
        self._by_dir = {}
        for wid, cfg in (registry.all_works() or {}).items():
            d = os.path.basename(os.path.dirname(os.path.normpath(cfg.get('aa_dir', ''))))
            if d:
                self._by_dir.setdefault(d, (wid, cfg))

    def resolve(self, name):
        try:
            cfg = registry.get(name)      # 未知 id 会 SystemExit（不是返回 None）
        except SystemExit:
            cfg = None
        if cfg:
            return name, cfg
        return self._by_dir.get(name, (None, None))


def process(rows, do_write, limit=None, workers=1, results_path=None):
    """按作品串行、**作品内跨章并发**（大多数章只有 1 行待补，按章并发等于串行）。

    T.WORK_ID 是模块级全局（术语注入/后端链读它），所以并发只在同一作品内做；
    跨作品并发请用 `--shard i/n`（按章分组轮分，同章只被一个进程写）。
    写回：所有行跑完后按章串行 备份→写→复核（不与模型调用混在一起，避免半成品落盘）。
    """
    by_work = {}
    for r in rows:
        by_work.setdefault(r['work'], []).append(r)

    resolver = _WorkResolver()
    results = []
    gloss_warned = set()
    rf = open(results_path, 'a', encoding='utf-8') if results_path else None

    def _rec(d):
        results.append(d)
        if rf:
            rf.write(json.dumps(d, ensure_ascii=False) + '\n')
            rf.flush()

    from concurrent.futures import ThreadPoolExecutor
    import threading

    for work, wrows in sorted(by_work.items(), key=lambda kv: -len(kv[1])):
        wid, cfg = resolver.resolve(work)
        if not cfg:
            for r in wrows:
                _rec({**r, 'ok': False, 'reason': 'no_registry'})
            continue
        work = wid
        T.WORK_ID = work
        T.AA_DIR = cfg['aa_dir']
        T._glossary_pairs_cache = None
        by_base = {}
        for r in wrows:
            by_base.setdefault(r['base'], []).append(r)
        print(f'=== {work}（{len(wrows)} 行 / {len(by_base)} 章 | 并发 {workers}）', flush=True)

        cache = {}
        lock = threading.Lock()

        def _chapter(base):
            """按章懒加载 src/tr/meta（并发下加锁，同一章只读一次）。"""
            with lock:
                if base in cache:
                    return cache[base]
            src_p = T.txt_existing(os.path.join(cfg['aa_dir'], base + '.txt'))
            tr_p = T.txt_existing(os.path.join(cfg['out_dir'], base + '_汉化.txt'))
            if not os.path.exists(src_p) or not os.path.exists(tr_p):
                data = ('file_missing', None, None, None, None, None, None)
            else:
                src = T.txt_read(src_p)
                tr = T.txt_read(tr_p)
                if len(src) != len(tr):
                    data = (f'len_mismatch {len(src)}!={len(tr)}', None, None, None, None, None, None)
                else:
                    meta = T.load_meta_segments_0based(
                        os.path.join(cfg['work_dir'], 'line_meta_v2'), base) or {}
                    r18, low = T._chapter_work_r18(base, cfg)
                    data = (None, base, src, tr, meta, r18, low)
            with lock:
                cache[base] = data
            return data

        def _do_row(r):
            """单行：懒加载本章 → 建段 → 段级协议重译 → 三道校验。返回 (row, ok, reason, cand)。"""
            err, base, src, tr, seg_meta, work_r18, low_r18 = _chapter(r['base'])
            if err:
                return r, False, err, None
            idx = int(r['line']) - 1
            if not (0 <= idx < len(src)):
                return r, False, 'line_out_of_range', None
            s, h = src[idx], tr[idx]
            segs = seg_meta.get(idx)
            if not segs:
                from line_classify_v2 import segment_line
                segs = segment_line(s, treat_hankaku_as_text=True)
            if not T._confident_text_segs(segs):
                return r, False, 'no_confident_text_seg', None
            try:
                fixed, _missed, _warns = T.translate_lines_mixed(
                    [(idx, s, segs)], work_r18=work_r18, low_r18=low_r18, base=base)
            except SystemExit:
                # 作品术语表硬前置：无 glossary_<wid>.json → 无法安全重译
                # （不自动生成：作品表是人工资产）。逐行标 no_glossary，不中断整批。
                return r, False, 'no_glossary', None
            if idx not in fixed:
                return r, False, 'model_fail', None
            cand = fixed[idx]
            # 补译结果必须：宽度 == SRC、art 段逐字节保留、覆盖漏字列的 text 段确实变了
            if T.disp_width(cand) != T.disp_width(s):
                return r, False, 'width_fail', None
            if not T._verify_art_alignment(s, cand, segs):
                return r, False, 'art_align_fail', None
            if not _miss_seg_changed(s, cand, segs, r.get('miss')):
                # 2026-10-10: 段级协议末轮会「接受部分成功」（不合格段退回原文）→ 行仍在 fixed 里，
                # 但目标段没变 = 模型没译出来 → 与"整行没产出"同为 model_fail（旧版靠 missed 判）
                return r, False, 'model_fail', None
            if cand == h:
                return r, False, 'no_change', None
            return r, True, 'repaired', cand

        if workers > 1 and len(wrows) > 1:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                row_res = list(ex.map(_do_row, wrows))
        else:
            row_res = [_do_row(r) for r in wrows]

        # 汇总 + 按章写回（串行；只写通过校验的行）
        accepted = {}                      # base → {idx: cand}
        for r, ok, reason, cand in row_res:
            if reason == 'no_glossary' and work not in gloss_warned:
                gloss_warned.add(work)
                print(f'  跳过（缺作品术语表，需先手动生成）: {work}', flush=True)
            if not ok:
                _rec({**r, 'ok': False, 'reason': reason})
                continue
            idx = int(r['line']) - 1
            _, base, src, tr, _m, _r, _l = cache.get(r['base']) or (None, None, None, None, None, None, None)
            if base is None:
                _rec({**r, 'ok': False, 'reason': 'cache_miss'})
                continue
            accepted.setdefault(base, {})[idx] = cand
            print(f'  L{idx + 1} 补译OK 宽{T.disp_width(cand)}==SRC{T.disp_width(src[idx])}'
                  f' | {tr[idx].strip()[:28]} → {cand.strip()[:28]}', flush=True)
            _rec({**r, 'ok': True, 'reason': reason, 'new': cand})

        if do_write:
            _ts = time.strftime('%Y%m%d%H%M%S')      # 2026-10-01: 每次运行一个备份目录
            for base, acc in sorted(accepted.items()):
                tr_p = T.txt_existing(os.path.join(cfg['out_dir'], base + '_汉化.txt'))
                tr_now = T.txt_read(tr_p)
                bak = os.path.join(cfg['work_dir'], f'backup_fix_lead_kanji_{_ts}')
                os.makedirs(bak, exist_ok=True)
                shutil.copy2(tr_p, os.path.join(bak, os.path.basename(tr_p)))
                for idx, cand in acc.items():
                    tr_now[idx] = cand
                T.txt_write(tr_p, tr_now)
                back = T.txt_read(tr_p)
                bad = [i for i, c in acc.items() if back[i] != c]
                print(f'  【{base}】写回 {len(acc)} 行（备份 {os.path.basename(bak)}）'
                      f' 复核不符 {len(bad)}', flush=True)
            if accepted:      # 只保留最近 2 次运行的备份目录（更早的整目录删，2026-10-01）
                T.prune_backup_dirs(cfg['work_dir'], 'backup_fix_lead_kanji_')
        if limit and len(results) >= limit:
            break
    if rf:
        rf.close()
    return results



def main():
    rows_p = _arg('--rows', os.path.join(ROOT, 'temp', 'lead_miss_bad.jsonl'))
    do_write = '--apply' in sys.argv
    limit = int(_arg('--limit', 0) or 0)
    work = _arg('--work')
    eids = _arg('--eids')
    if not os.path.exists(rows_p):
        print(f'清单不存在: {rows_p}')
        return 1
    rows = load_rows(rows_p)
    if work:
        res = _WorkResolver()
        rows = [r for r in rows if r['work'] == work or res.resolve(r['work'])[0] == work]
    if eids:
        keep = [k for k in eids.split(',') if k]
        rows = [r for r in rows if any(k in r['base'] for k in keep)]
    if limit:
        rows = rows[:limit]
    shard = _arg('--shard')
    if shard:
        # 跨进程分片：按 (作品, 章节) 分组轮分，保证同一章只被一个进程写（防丢更新）
        si, sn = (int(x) for x in shard.split('/'))
        groups = {}
        for r in rows:
            groups.setdefault((r['work'], r['base']), []).append(r)
        keys = sorted(groups)
        rows = [r for idx, k in enumerate(keys) if idx % sn == si for r in groups[k]]
    workers = int(_arg('--workers', 1) or 1)
    # 段宽收缩容忍（2026-10-10 口径变更）：生产段级路径不再用固定的 4 列阈值，改成「比 <
    # MIXED_SHRINK_MIN_RATIO 才否决」（默认 0.35 = 只挡灾难性丢内容，中文天然比日文短）。
    # `--shrink-ratio r`（旧口径：允许收缩的比例）等价换算：允许收缩 r ⟺ 译文最短 = 源宽×(1-r)。
    sr = _arg('--shrink-ratio')
    if sr not in (None, ''):
        os.environ['AA_MIXED_SHRINK_MIN_RATIO'] = '%.3f' % max(0.0, 1.0 - float(sr))
    print(f'清单 {rows_p}: {len(rows)} 行 | {"写回" if do_write else "空跑(dry-run)"}'
          f' | 并发 {workers} | 段宽否决下限 {T.mixed_shrink_min_ratio():.0%}（复核线 {T.mixed_shrink_flag_ratio():.0%}）'
          + (f' | 分片 {shard}' if shard else ''), flush=True)
    ts = time.strftime('%Y%m%d%H%M%S')
    out_p = os.path.join(ROOT, 'temp', f'fix_lead_kanji_miss_{ts}.jsonl')
    open(out_p, 'w', encoding='utf-8').close()      # 先清空，process 内增量追加（中断也不丢记录）
    results = process(rows, do_write, limit=None, workers=workers, results_path=out_p)
    ok = sum(1 for r in results if r.get('ok'))
    from collections import Counter
    reasons = Counter(r['reason'] for r in results if not r.get('ok'))
    print(f'\n总计 {len(results)} 行: 补译OK {ok} | 未通过 {len(results) - ok}')
    for k, v in reasons.most_common():
        print(f'   {k}: {v}')
    print(f'明细: {out_p}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
