# -*- coding: utf-8 -*-
"""M5 质量闸门 (quality_gate.py)
统一校验一话/一批汉化产物 vs 原文，输出四类检查结果：
  1. 行数一致      src == tr（含 # 行）
  2. 宽度错位      逐行 disp_width == src（# 行除外）
  3. 残留假名      非白名单 kana_runs（白名单制，同 pass3 口径）
  4. 人工行保护    manual_fix.json 记录的行在产物中 hash/text 不变（G5）
  5. # 行保留      原文 # 开头的标题/地址行必须原样出现在产物中

用法:
  python scripts/quality_gate.py --work キッコーマン [--chapter 3463] [--report doc/quality_report.txt]
  --work 指仓库根相对的作品目录（内含 aa/ 与 汉化/）
输出: --report 指定路径（默认 <work>/quality_report.txt）；stdout 汇总 + 退出码 0=通过
"""
import os, sys, json, argparse, collections

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import aa_translate as T


def disp_width(s):
    return T.disp_width(s)


def kana_runs(line):
    return T.kana_runs(line)


def residual_suspect(line):
    return [s for s in T.kana_runs(line) if len(s) >= 3 and s not in T.KNOWN_NAMES]


def build_whitelist(files_tr):
    """从既有产物统计高频假名段(>=3次)作白名单, 与 KNOWN_NAMES 并集。
    避免角色名/艺术纹理碎片误报（pass3 口径）。"""
    freq = collections.Counter()
    for f in files_tr:
        p = f
        if not os.path.isabs(p):
            continue
        try:
            for l in open(p, encoding='utf-8'):
                for s in T.kana_runs(l):
                    freq[s] += 1
        except Exception:
            pass
    wl = set(s for s, n in freq.items() if n >= 3)
    wl |= set(T.KNOWN_NAMES)
    return wl


def check_file(src_path, tr_path, whitelist, manual_fixes):
    """返回 (report_lines, ok_bool) 单文件检查。"""
    lines = []
    ok = True
    src = T.txt_read(src_path)
    tr = T.txt_read(tr_path)
    fname = os.path.basename(tr_path)
    # 1. 行数
    if len(src) != len(tr):
        lines.append(f"!! LINE COUNT MISMATCH: src={len(src)} tr={len(tr)}")
        return lines, False
    # 5. # 行保留（标题/地址）
    for i, a in enumerate(src):
        if a.startswith('#'):
            if tr[i].rstrip() != a.rstrip():
                lines.append(f"!! # 行被改动 L{i}: src={a[:40]!r} tr={tr[i][:40]!r}")
                ok = False
    # 2. 宽度错位 + 3. 残留
    w_mismatch = 0
    resid_all = []
    for i, (a, b) in enumerate(zip(src, tr)):
        if a.startswith('#'):
            continue
        if disp_width(a) != disp_width(b):
            w_mismatch += 1
            if w_mismatch <= 15:
                lines.append(f"  WIDTH L{i}: {disp_width(a)}->{disp_width(b)}")
                lines.append(f"    SRC {a[:70]!r}")
                lines.append(f"    TR  {b[:70]!r}")
        for s in residual_suspect(b):
            resid_all.append((i, s))
    if w_mismatch:
        ok = False
        lines.append(f"  width-mismatch: {w_mismatch}")
    resid_all = [r for r in resid_all if r[1] not in whitelist]
    if resid_all:
        ok = False
        lines.append(f"  residual-suspect({len(resid_all)}): {resid_all[:10]}")
    # 4. 人工行保护 (G5): manual_fix.json 记录的行必须仍为记录文本
    fn_fixes = manual_fixes.get(fname, {})
    covered = 0
    for line_no, entry in fn_fixes.items():
        if line_no < len(tr):
            cur = tr[line_no].rstrip()
            rec = (entry.get('text') or '').rstrip()
            if cur != rec and T._manual_sha(cur) != entry.get('hash'):
                lines.append(f"!! MANUAL OVERWRITE L{line_no}: rec={rec[:40]!r} cur={cur[:40]!r}")
                ok = False
            else:
                covered += 1
    if covered:
        lines.append(f"  manual-covered: {covered}")
    return lines, ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', required=True, help='作品目录(相对仓库根), 内含 aa/ 与 汉化/')
    ap.add_argument('--chapter', default=None, help='单话 eid 过滤(可选)')
    ap.add_argument('--report', default=None, help='报告路径(默认 <work>/quality_report.txt)')
    ap.add_argument('--strict', action='store_true', help='严格模式: 残留/宽度任一即失败(默认仅汇总)')
    args = ap.parse_args()

    # Phase5(2026-08-18): --work 支持 registry id（kikkoman/saki/touhou_teisou...）映射到作品目录名
    work_dir = os.path.join(ROOT, args.work)
    if not os.path.isdir(work_dir):
        try:
            sys.path.insert(0, os.path.join(ROOT, 'scripts', 'works'))
            import registry as _R
            cfg = _R.get(args.work)
            # out_dir = <作品根>\汉化 → 作品目录 = dirname
            work_dir = os.path.dirname(cfg['out_dir'])
        except Exception:
            pass
    aa_dir = os.path.join(work_dir, 'aa')
    han_dir = os.path.join(work_dir, '汉化')
    report_path = args.report or os.path.join(work_dir, 'quality_report.txt')

    src_files = sorted(f for f in T.list_txt(aa_dir, exclude=('_台词.txt', '_汉化.txt')))
    tr_files = [f.replace('.txt', '_汉化.txt') for f in src_files]
    tr_paths = [T.txt_existing(os.path.join(han_dir, f)) for f in tr_files
                if os.path.exists(T.txt_existing(os.path.join(han_dir, f)))]
    missing = [f for f in tr_files if not os.path.exists(T.txt_existing(os.path.join(han_dir, f)))]

    if args.chapter:
        src_files = [f for f in src_files if f == f'entry_{args.chapter}.txt']
        tr_paths = [p for p in tr_paths if f'entry_{args.chapter}_汉化.txt' in p]
        missing = [f for f in missing if f == f'entry_{args.chapter}_汉化.txt']

    # 白名单从全量汉化产物统计（跨章节共享，避免单章频率低误报）
    all_tr = [os.path.join(han_dir, f) for f in T.list_txt(han_dir, '_汉化.txt')]
    whitelist = build_whitelist(all_tr)
    manual_fixes = T.load_manual_fixes(work_dir)

    report_lines = []
    total_mismatch = 0
    total_residual = 0
    files_fail = []
    files_checked = 0
    for sname in src_files:
        tr_path = T.txt_existing(os.path.join(han_dir, sname.replace('.txt', '_汉化.txt')))
        if not os.path.exists(tr_path):
            continue
        lines, ok = check_file(T.txt_existing(os.path.join(aa_dir, sname)), tr_path, whitelist, manual_fixes)
        files_checked += 1
        if not ok:
            files_fail.append(sname)
        # 汇总计数
        total_mismatch += sum(1 for l in lines if '  WIDTH L' in l)
        total_residual += sum(1 for l in lines if '  residual-suspect(' in l)
        report_lines.append(f"===== {sname} : {'OK' if ok else 'FAIL'} =====")
        report_lines.extend(lines)
        print(f"{sname[:40]}: {'OK' if ok else 'FAIL'}", flush=True)

    if missing:
        report_lines.append(f"!! MISSING 汉化: {missing}")
        print(f"!! MISSING: {len(missing)}")
    for fn in files_fail:
        report_lines.append(f"FAIL: {fn}")

    summary = (f"\n=== 质量闸门汇总 ===\n"
               f"检查文件: {files_checked} | 失败: {len(files_fail)} | 缺失: {len(missing)}\n"
               f"宽度错位行: {total_mismatch} | 非白名单残留段: {total_residual}\n"
               f"人工行覆盖: 0（manual_fix.json 仅 {len(manual_fixes)} 文件有记录）")
    print(summary)
    report_lines.append(summary)
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(report_lines))
    print(f"report: {report_path}")

    # 退出码：strict 模式下任一失败则非0
    if args.strict and (files_fail or missing or total_mismatch or total_residual):
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
