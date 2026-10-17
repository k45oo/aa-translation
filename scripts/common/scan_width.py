# -*- coding: utf-8 -*-
"""宽度校验：汉化 vs 原文逐行显示宽度一致性，报告写 <work_dir>/width_report.txt。

用法：python common/scan_width.py --work <id>（或裸参数 <id>）
校验排除 # / ===== 标记行；行数不一致会单独标出。

2026-08-27 口径统一（doc/宽度校验口径统一_2026-08-27.md）：
- 超宽豁免唯一口径 = 平铺版 aa_translate.line_kind/allow_overwidth
  （skip/header/text/mixed 放行, art 纯图形严格等宽——与工作台 save_line 同源）。
  旧版自带的 _ART_DENSE_CHARS 启发式已删（与 line_kind 两套标准并存曾导致
  不同工具对同一文件给出不同错位数）。
- 校验逻辑抽成可导入的 collect_mismatch(AA_DIR, OUT_DIR, files=None)，
  translate_runner 定向复验直接调用（files=本次章节），不再各自内联实现。

报告行号说明：collect_mismatch 返回 idx 为 0-based 物理行下标（与历史脚本一致）；
报告/日志展示用 i+1（1-based，与工作台行号列一致）。
"""
import os, sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(_SCRIPT_DIR), "works"))

import registry
from aa_common import disp_width, allow_overwidth, list_txt, txt_read, txt_existing  # 压缩感知


def collect_mismatch(AA_DIR, OUT_DIR, files=None, check_art_align=False, WORK_DIR=None):
    """逐行宽度校验（allow_overwidth 统一口径）。

    files: 原文文件名列表（如 ['entry_17568.txt']）；None = 全部 *.txt。
    check_art_align(2026-09-05): mixed 行 art 段列位校验——宽度对但 art 被压缩/
      左移的漂移行(整行翻译产物)scan_width 查不出, 需按 line_meta segments 对比
      art 段在汉化行的列位与原文是否一致。WORK_DIR 传作品 work 目录(取 line_meta_v2)。
    返回 rows: [(fname, idx0, wa, wb)] —— 只含需处理的不一致行：
      - short（译文变短，任何行类型都报）
      - art 行超宽（line_kind=art 不允许超宽）
    text/mixed/header/skip 行超宽视为正常扩展，不返回。
    rows_artalign(新增, 仅 check_art_align 时收集): [(fname, idx0)] mixed 行 art 列位失配。
    另返回 (n_files_checked, skipped_missing, line_count_mismatches) 供调用方汇报。
    """
    if files is None:
        names = list_txt(AA_DIR, exclude=("_台词.txt", "_汉化.txt"))
    else:
        names = sorted(files)
    rows = []
    rows_artalign = []
    skipped, count_mismatch = [], []
    # 2026-09-05: art 列位校验前置加载——WORK_DIR/line_meta_v2/<base>.json
    # 用 aa_common(转发 aa_translate) 的 load_segments_meta/_verify_art_alignment
    _seg_meta_cache = {}
    if check_art_align and WORK_DIR:
        try:
            from aa_common import load_segments_meta, _verify_art_alignment
        except Exception:
            load_segments_meta = _verify_art_alignment = None
    else:
        load_segments_meta = _verify_art_alignment = None
    for fname in names:
        src_path = txt_existing(os.path.join(AA_DIR, fname))
        dst = txt_existing(os.path.join(OUT_DIR, fname.replace(".txt", "_汉化.txt")))
        if not os.path.exists(src_path) or not os.path.exists(dst):
            skipped.append(fname)
            continue
        src = txt_read(src_path)
        tr = txt_read(dst)
        if len(src) != len(tr):
            count_mismatch.append((fname, len(src), len(tr)))
            continue
        if load_segments_meta is not None:
            base = fname[:-4]
            try:
                _seg_meta_cache[fname] = load_segments_meta(
                    os.path.join(WORK_DIR, "line_meta_v2"), base)
            except Exception:
                _seg_meta_cache[fname] = {}
        segmap = _seg_meta_cache.get(fname, {})
        for i, (a, b) in enumerate(zip(src, tr)):
            if a.startswith(('#', '=====')):   # 标记行不参与宽度校验
                continue
            wa, wb = disp_width(a), disp_width(b)
            if wa == wb:
                # 2026-09-05: 宽相等也可能 art 错位(空格压缩+U+3000硬垫)——补 art 列位校验
                if _verify_art_alignment is not None and i in segmap:
                    segs = segmap[i]
                    # 只查有高置信台词段的 mixed 行(纯 art 行不在此 meta)
                    if any(sg.get("kind") == "text" and sg.get("confidence", 1.0) >= 0.65
                           for sg in segs):
                        if not _verify_art_alignment(a, b, segs):
                            rows_artalign.append((fname, i))
                continue
            if wb < wa:
                # 短了: 必须报(缺宽度破坏左侧对齐)
                rows.append((fname, i, wa, wb))
            elif not allow_overwidth(a):
                # 超宽: 仅 art 行报(text/mixed/header/skip 允许自然扩展)
                rows.append((fname, i, wa, wb))
    return rows, rows_artalign, (len(names), skipped, count_mismatch)


def main(work=None):
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    cfg = registry.get(work or (args[0] if args else "baitou"))
    AA_DIR, OUT_DIR = cfg["aa_dir"], cfg["out_dir"]
    REPORT = os.path.join(cfg["work_dir"], "width_report.txt")
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    rows, rows_artalign, (n_names, skipped, count_mismatch) = collect_mismatch(
        AA_DIR, OUT_DIR, check_art_align=True, WORK_DIR=cfg["work_dir"])
    for f in skipped:
        print(f"SKIP missing: {f}")
    for fname, ns, nt in count_mismatch:
        msg = f"!! LINE COUNT MISMATCH {fname}: src={ns} tr={nt}"
        print(msg)

    total_mismatch = len(rows)
    # 按 file 聚合打印（与旧版输出格式兼容: 每文件一行统计 + 明细最多40条）
    by_file = {}
    for fname, i, wa, wb in rows:
        by_file.setdefault(fname, []).append((i, wa, wb))
    report_lines = []
    for fname in sorted(set(list(by_file) + [c[0] for c in count_mismatch])):
        items = by_file.get(fname, [])
        n_src = len(txt_read(txt_existing(os.path.join(AA_DIR, fname))))
        print(f"{fname[:34]}: {n_src} lines, width-mismatch {len(items)}")
        report_lines.append(f"===== {fname} : {len(items)} mismatches =====")
        for i, wa, wb in items[:40]:
            mark = "SHORT" if wb < wa else "LONG "
            report_lines.append(f"{mark} line {i}: {wa}->{wb}")
    print(f"\nTOTAL width-mismatch lines: {total_mismatch}")
    # 2026-09-05: art 列位失配报告(mixed 行 art 被压缩/左移, 宽度对但图形错位)
    if rows_artalign:
        by_f = {}
        for fname, i in rows_artalign:
            by_f.setdefault(fname, []).append(i)
        print(f"\n!! ART-ALIGN mismatch lines: {len(rows_artalign)}"
              " (mixed 行 art 段列位与原文不符, 用 scripts/fix/fix_mixed_drift.py 重建)")
        report_lines.append(f"\n===== ART-ALIGN mismatches: {len(rows_artalign)} =====")
        for fname in sorted(by_f):
            print(f"  {fname}: {len(by_f[fname])} 行 -> {[i + 1 for i in by_f[fname][:40]]}")
            report_lines.append(f"{fname}: art-align 失配行(1-based) "
                                + ", ".join(str(i + 1) for i in by_f[fname][:200]))
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))
    print(f"report: {REPORT}")


if __name__ == "__main__":
    main()
