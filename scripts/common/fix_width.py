# -*- coding: utf-8 -*-
"""fix_width: 宽度错位行带原文重译，强制宽度一致；失败行恢复 SRC 原文（等人工复查）。

用法：python common/fix_width.py --work <id>
失败清单写 <work_dir>/manual_fix.txt。
注意：模型输出原文(宽度恰匹配)会被当成成功——人工修复(pass5)后若再跑 fix_width，
必须复验被修行（TR==SRC 且 is_text_line 的行）。
"""
import os, sys, json, concurrent.futures

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(_SCRIPT_DIR), "works"))

import registry
import aa_common as C
import aa_api as T

FIX_SYSTEM = """你是やる夫AA漫画的汉化翻译。以下行的【当前译文宽度超过了原文行】，需要重新翻译修复。每行格式「序号|原文行」。

要求：
1. 把原文行中的日语文字翻译成简体中文，译文必须【比原文日语文字更短或等长】（可精简、意译）。
2. 输出行必须与原文行保持【完全相同的显示宽度】（ASCII半角字符=1格，其他字符=2格）：先摆好行内所有绘画字符（全角空格U+3000、│｜／＼＿￣边框、半角片假名、ASCII艺术符），把译文放进原文字区域，空余处用全角空格U+3000填充，译文绝不允许溢出到绘画区域。
3. 角色名/专有名词保留日文。不要留下任何未翻译的日语词句。
4. 输出「序号|修复后的完整行」，所有行都要输出，不要输出其他内容。"""


def collect(AA_DIR, OUT_DIR, files=None):
    """逐行宽度错位收集（2026-08-29 对齐 scan_width 的 allow_overwidth 唯一口径）。

    此前用裸 disp_width(a)!=disp_width(b) 一刀切，把 text/mixed 行译文自然超宽
    也当错位送模型重译（模型只能照抄日文/加宽），产生大量假阳性落 manual_fix.txt。

    files: 原文文件名列表（如 ['entry_17568.txt']）；None = 全部 *.txt。
    返回 rows: [(fname, idx, src, tr)] —— 只含需处理的不一致行：
      - short（译文变短，任何行类型都报）
      - art 行超宽（allow_overwidth=False，超宽破坏图形）
    text/mixed/header/skip 行超宽视为正常扩展，不返回。
    """
    if files is None:
        names = C.list_txt(AA_DIR, exclude=("_台词.txt", "_汉化.txt"))
    else:
        names = sorted(files)
    rows = []
    for fname in names:
        src_path = C.txt_existing(os.path.join(AA_DIR, fname))
        if not os.path.exists(src_path):
            continue
        dst = C.txt_existing(os.path.join(OUT_DIR, fname.replace(".txt", "_汉化.txt")))
        if not os.path.exists(dst):
            continue
        src = C.txt_read(src_path)
        tr = C.txt_read(dst)
        for i, (a, b) in enumerate(zip(src, tr)):
            if a.startswith(('#', '=====')):   # 标记行不参与宽度校验
                continue
            wa, wb = C.disp_width(a), C.disp_width(b)
            if wa == wb:
                continue
            if wb < wa:
                rows.append((fname, i, a, b))          # 短了: 任何行都修
            elif not C.allow_overwidth(a):
                rows.append((fname, i, a, b))          # 超宽: 仅 art 行修
    return rows


def fix_batch(rows):
    """rows: list of (fname, idx, src, tr) for ONE file. Returns (fixed_dict, failed_list)"""
    fixed, failed = {}, []
    body = "\n".join(f"{i:05d}|{src}" for _, i, src, _ in rows)
    for attempt in range(2):
        if not rows:
            break
        text = T.call_api(FIX_SYSTEM, body, max_tokens=16384)
        res = T.parse_result(text)
        for num, nl in res.items():
            row = next((r for r in rows if r[1] == num), None)
            if not row:
                continue
            fname, idx, src, tr = row
            w = C.disp_width(nl)
            if w == C.disp_width(src):
                fixed[idx] = nl
            else:
                # try pad if short
                padded = C.pad_to_width(nl, C.disp_width(src))
                if C.disp_width(padded) == C.disp_width(src):
                    fixed[idx] = padded
                else:
                    failed.append(row)
        rows = [r for r in rows if r[1] not in fixed]
        body = "\n".join(f"{i:05d}|{src}" for _, i, src, _ in rows)
    return fixed, rows


def main(work=None):
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    cfg = registry.get(work or (args[0] if args else "baitou"))
    AA_DIR, OUT_DIR = cfg["aa_dir"], cfg["out_dir"]
    MANUAL = os.path.join(cfg["work_dir"], "manual_fix.txt")
    rows = collect(AA_DIR, OUT_DIR)
    print(f"collect {len(rows)} mismatch rows", flush=True)
    by_file = {}
    for r in rows:
        by_file.setdefault(r[0], []).append(r)
    all_fixed, all_failed = {}, []
    for fname, frows in by_file.items():
        fixed, failed = fix_batch(frows)
        # apply
        dst = C.txt_out_path(os.path.join(OUT_DIR, fname.replace(".txt", "_汉化.txt")))
        lines = C.txt_read(dst)
        for idx, nl in fixed.items():
            lines[idx] = nl
        C.txt_write_form(dst, lines)
        all_fixed.update(fixed)
        all_failed.extend(failed)
        print(f"{fname.split('_')[0]}: fixed {len(fixed)}/{len(frows)}, failed {len(failed)}", flush=True)
    print(f"\nTOTAL fixed {len(all_fixed)}, failed {len(all_failed)}", flush=True)
    with open(MANUAL, "w", encoding="utf-8") as f:
        for fname, idx, src, tr in all_failed:
            f.write(f"### {fname} line {idx} (src_w={C.disp_width(src)} tr_w={C.disp_width(tr)})\nSRC: {src!r}\nTR : {tr!r}\n\n")
    print(f"manual list: {MANUAL}")


if __name__ == "__main__":
    main()
