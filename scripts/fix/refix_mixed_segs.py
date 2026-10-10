#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""按 review_inline 段级失败清单定向补译（2026-10-10，存量修复 P2）。

背景（实测见 doc/方案归档/段级协议漏译根因与优化方案_2026-10-10.md）：
段级(mixed)协议旧校验把**合法译文**判成「吞字/照抄」→ 3 轮全败 → 整行**静默**落
`work/review_inline.txt`（本作 9,520 行，其中 1,982 行成品至今原样日文）；全库 54 部作品
共 210,929 行同源。协议已在 `aa_translate.translate_lines_mixed` 修好（比例判据 + 照抄分两态 +
定向提示词 + 末轮接受部分成功 + 只重发失败段），本工具把**存量**那批行按新协议重译一遍。

只动「成品仍是日文 / 仍有残留」的行；通过校验才写回（整行宽 == SRC + art 列位守恒）。
段级仍不合格的段退原文并写 `work/mixed_leftover.txt`（人工可查），有真台词的仍留 review_inline。

用法（仓库根运行）：
  python scripts/fix/refix_mixed_segs.py --work <id>                    # 空跑：列出将改的行
  python scripts/fix/refix_mixed_segs.py --work <id> --apply            # 写回（整章备份）
  python scripts/fix/refix_mixed_segs.py --work <id> --eids entry_2996 --limit 30 [--apply]
  python scripts/fix/refix_mixed_segs.py --work <id> --chunk 50 --force  # --force 连"已无残留"的行也重译
"""
import json
import os
import re
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.path.insert(0, os.path.join(ROOT, 'scripts', 'common'))
sys.path.insert(0, os.path.join(ROOT, 'scripts', 'works'))
sys.path.insert(0, os.path.join(ROOT, 'scripts', 'tools'))

import aa_translate as T          # noqa: E402


def _arg(name, default=None):
    a = sys.argv
    if name in a and a.index(name) + 1 < len(a):
        return a[a.index(name) + 1]
    return default


def _flag(name):
    return name in sys.argv


def load_targets(work_dir, only_mixed=True):
    """从 review_inline.txt 取目标行 → {base: {行号: 记录下来的原文}}（按 章|行 去重）。

    ⚠️ 行号口径：`translate_lines_mixed` 写的 `line N` 是 **0-based 索引**（实测 415/415 与
    src[N] 逐字节相同）；`refine_aa` 写的那批可能是 1-based。这里**原样保留**，由 _resolve_idx
    按"记录原文 == 该行"判定，绝不在这里做 ±1（曾因两边各减一次导致取错行）。
    """
    p = os.path.join(work_dir, 'review_inline.txt')
    if not os.path.exists(p):
        return {}
    out = {}
    with open(p, encoding='utf-8', errors='replace') as f:
        for ln in f:
            if only_mixed and 'mixed-fallback' not in ln:
                continue
            m = re.match(r'^(\S+\.txt) \| line (\d+) \| (.*)$', ln.rstrip('\n'))
            if not m:
                continue
            base = m.group(1)[:-4]
            tail = m.group(3)
            for tag in (' [mixed-fallback]', '[mixed-fallback]'):
                if tail.endswith(tag):
                    tail = tail[:-len(tag)].rstrip()
            out.setdefault(base, {})[int(m.group(2))] = tail
    return out


def _demote(segs, src_line):
    """P1 口径（无决定性台词证据的 text 段降为 art）——存量 meta 未重生时补上，零 API。"""
    try:
        import line_classify_v2 as LC
        return LC._demote_non_dialogue_text_segs(segs, src_line)
    except Exception:
        return segs


def _resolve_idx(seg_meta, src, n, want=None):
    """review_inline 的 `line N` 是 **0-based**（= translate_lines_mixed 的 num；实测 415/415 行
    与 src[N] 逐字节相同）；refine_aa 写的那批可能是 1-based。

    判定优先级：**记录原文 == src[cand]**（最可靠）→ 段落完整覆盖该行（segs 覆盖整行）。
    ⚠️ 绝不在调用方再做 ±1：两边各减一次会取到隔壁行，而"宽度恰好相等"时覆盖率判据不会报警。
    返回 0-based 行号或 None。"""
    for idx in (n, n - 1):
        if not (0 <= idx < len(src)):
            continue
        if want is not None and src[idx] == want:
            return idx
        segs = seg_meta.get(idx)
        if not segs or (want is not None and want.strip() != src[idx].strip()):
            continue
        if segs[0]['start'] == 0 and segs[-1]['end'] == len(src[idx]):
            return idx
    return None


def _pick_backend(work, cfg):
    """按 translate_runner 的同一规则设置 `AA_BACKEND`（不设 = 云端直接模式 mimo-v2.5 → 大块输出不全，
    实测 akari_haisin 19.9% 中文行事故）。用户已显式设 AA_BACKEND 时尊重之。

    规则（work_meta.r18_level）: high/medium → r18 链首; low/空 → normal 链首。
    """
    if os.environ.get("AA_BACKEND"):
        print('[backend] 沿用已设 AA_BACKEND=%s' % os.environ['AA_BACKEND'], flush=True)
        return
    try:
        import aa_config as AC
        lvl = ''
        try:
            with open(os.path.join(ROOT, 'aa_web_app', 'data', 'work_meta.json'), encoding='utf-8') as f:
                lvl = (json.load(f).get(work, {}) or {}).get('r18_level', '') or ''
        except Exception:
            pass
        b, m = AC.auto_backend(lvl in ('high', 'medium'))
        os.environ['AA_BACKEND'] = b
        if m:
            os.environ.setdefault(AC.model_env(b), m)
        os.environ['AA_BACKEND_AUTO'] = '1'      # 自动选 → 章内 pin/公共前缀照旧参与
        print('[backend] r18_level=%s → %s/%s（r18 链首/normal 链首）' % (lvl or '空', b, m), flush=True)
    except Exception as e:
        print('[backend] 选择失败(%s), 用默认' % e, flush=True)


def process(cfg, targets, chunk=50, do_write=False, limit=0, force=False):
    aa_dir, out_dir, work_dir = cfg['aa_dir'], cfg['out_dir'], cfg['work_dir']
    ts = time.strftime('%Y%m%d%H%M%S')     # 本次运行的备份目录戳（每次运行一个目录）
    meta_dir = os.path.join(work_dir, 'line_meta_v2')
    accepted, stats, done, n_acc = {}, {'lines': 0, 'skip_ok': 0, 'no_seg': 0, 'no_change': 0}, 0, 0
    for base in sorted(targets):
        src_p = T.txt_existing(os.path.join(aa_dir, base + '.txt'))
        tr_p = T.txt_existing(os.path.join(out_dir, base + '_汉化.txt'))
        if not (os.path.exists(src_p) and os.path.exists(tr_p)):
            continue
        seg_meta = T.load_meta_segments_0based(meta_dir, base) or {}
        if not seg_meta:
            continue
        src, tr = T.txt_read(src_p), T.txt_read(tr_p)
        items = []
        for n, want in sorted(targets[base].items()):
            idx = _resolve_idx(seg_meta, src, n, want)
            if idx is None:
                stats['no_seg'] += 1
                continue
            if idx >= len(tr):
                continue
            segs = seg_meta.get(idx)
            segs = _demote(segs, src[idx])
            if not T._confident_text_segs(segs):
                stats['no_seg'] += 1
                continue
            # 成品已无残留且与原文不同 → 跳过（别覆盖 refine/人工的成果）
            if not force and tr[idx] != src[idx] and not T.residual_text_segs(tr[idx], segs):
                stats['skip_ok'] += 1
                continue
            items.append((idx, src[idx], segs))
        if not items:
            continue
        stats['lines'] += len(items)
        if limit:
            items = items[:max(0, limit - done)]
        r18, low = T._chapter_work_r18(base, cfg)
        for c0 in range(0, len(items), max(1, chunk)):
            grp = items[c0:c0 + max(1, chunk)]
            fixed, missed, _warn = T.translate_lines_mixed(
                grp, work_r18=r18, low_r18=low, base=base)
            for idx, _l, _s in grp:
                cand = fixed.get(idx)
                if cand is None or cand == tr[idx]:
                    stats['no_change'] += 1
                    continue
                accepted.setdefault(base, {})[idx] = cand
                n_acc += 1
                print('  %s L%d 补译 %d→%d | %s'
                      % (base, idx + 1, T.disp_width(tr[idx]), T.disp_width(cand),
                         cand.strip()[:40]), flush=True)
            print('  [%s] 本批 %d 行: 产出 %d / 未产出 %d'
                  % (base, len(grp), sum(1 for i, _l, _s in grp if i in (accepted.get(base) or {})),
                     len(missed)), flush=True)
        done += len(items)
        if do_write and accepted.get(base):
            # 每章立刻写回（2026-10-10：整批写回一旦中断/超时=全丢；跑 30 章要几小时）
            _write_chapter(out_dir, work_dir, base, accepted.pop(base), ts)
        if limit and done >= limit:
            break
    if do_write:
        T.prune_backup_dirs(work_dir, 'backup_refix_mixed_')
    return n_acc, stats


def _write_chapter(out_dir, work_dir, base, acc, ts=None):
    """单章写回：整章备份（**每次运行一个目录**，ts 由调用方在开跑时生成一次）+ 行数护栏 + 读回复核。

    ⚠️ 备份目录 ts 必须每次运行固定一个（不是每章一个）：`prune_backup_dirs` 只保留最近 2 个
    同前缀目录，每章一个目录会让本次运行自己的备份被当成"旧运行"删掉（2026-10-10 踩过）。"""
    if not acc:
        return
    ts = ts or time.strftime('%Y%m%d%H%M%S')
    tr_p = T.txt_existing(os.path.join(out_dir, base + '_汉化.txt'))
    tr_now = T.txt_read(tr_p)
    bak = os.path.join(work_dir, 'backup_refix_mixed_' + ts)
    os.makedirs(bak, exist_ok=True)
    shutil.copy2(tr_p, os.path.join(bak, os.path.basename(tr_p)))
    n_before = len(tr_now)
    for idx, cand in acc.items():
        tr_now[idx] = T.one_line(cand)
    # 写盘行数护栏（AGENTS：写盘前比对行数，不一致拒写）
    assert len(tr_now) == n_before, '%s 写回后行数变化 %d→%d（拒写）' % (base, n_before, len(tr_now))
    T.txt_write(tr_p, tr_now)
    back = T.txt_read(tr_p)
    bad = [i for i in acc if back[i] != T.one_line(acc[i])]
    print('  【%s】写回 %d 行（备份 %s）复核不符 %d'
          % (base, len(acc), os.path.basename(bak), len(bad)), flush=True)


def main():
    work = _arg('--work')
    if not work:
        print('用法: python scripts/fix/refix_mixed_segs.py --work <id> [--apply] '
              '[--eids a,b] [--limit N] [--chunk 50] [--force]')
        return 2
    import registry
    cfg = registry.get(work)
    if not cfg:
        print('未知作品: %s' % work)
        return 2
    T.AA_DIR, T.OUT_DIR, T.WORK_DIR = cfg['aa_dir'], cfg['out_dir'], cfg['work_dir']
    T.WORK_ID = work
    _pick_backend(work, cfg)
    targets = load_targets(cfg['work_dir'])
    eids = _arg('--eids')
    if eids:
        keep = [k for k in eids.split(',') if k]
        targets = {b: v for b, v in targets.items() if any(k in b for k in keep)}
    only = _arg('--only')
    if only:
        targets = {b: v for b, v in targets.items() if only in b}
    n_lines = sum(len(v) for v in targets.values())
    do_write = _flag('--apply')
    print('作品 %s | review_inline 段级失败行 %d（%d 章）| %s'
          % (work, n_lines, len(targets), '写回' if do_write else '空跑(dry-run)'), flush=True)
    if not n_lines:
        print('无目标行（review_inline.txt 缺失或无 [mixed-fallback] 行）')
        return 0
    n_acc, stats = process(cfg, targets, chunk=int(_arg('--chunk', 50) or 50),
                           do_write=do_write, limit=int(_arg('--limit', 0) or 0),
                           force=_flag('--force'))
    print('\n目标 %d 行 | 送协议 %d | 有改动 %d | 跳过(已无残留) %d | 无段落信息 %d | 无改动 %d'
          % (n_lines, stats['lines'], n_acc, stats['skip_ok'], stats['no_seg'], stats['no_change']))
    if not do_write:
        print('（空跑：加 --apply 写回）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
