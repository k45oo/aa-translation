# -*- coding: utf-8 -*-
"""错位行字符级 diff + 分类（ART-COPY / RESIDUAL / LONG-CN / KANA-ONLY）。

用法：python common/analyze_mismatch.py --work <id>
"""
import os, re, sys, difflib

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(_SCRIPT_DIR), "works"))

import registry
from aa_common import ART_KANJI, disp_width, list_txt, txt_existing, txt_read   # 压缩感知（.txt/.txt.gz）

KANA = re.compile(r'[ぁ-んァ-ヶー]')


def has_kana(s):
    return bool(KANA.search(s))


def has_cn(s):
    """是否有真实中文字（排除艺术汉字）"""
    for c in s:
        if '\u4e00' <= c <= '\u9fff' and c not in ART_KANJI:
            return True
    return False


def main(work=None):
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    cfg = registry.get(work or (args[0] if args else "baitou"))
    AA_DIR, OUT_DIR = cfg["aa_dir"], cfg["out_dir"]
    files = list_txt(AA_DIR, exclude=("_台词.txt", "_汉化.txt"))
    all_rows = []
    for fname in files:
        src = txt_read(txt_existing(os.path.join(AA_DIR, fname)))
        dst = txt_existing(os.path.join(OUT_DIR, fname.replace(".txt", "_汉化.txt")))
        if not os.path.exists(dst):
            continue
        tr = open(dst, encoding="utf-8").read().split("\n")
        for i, (a, b) in enumerate(zip(src, tr)):
            if disp_width(a) != disp_width(b):
                all_rows.append((fname, i, a, b))
    print(f"total mismatches: {len(all_rows)}\n")
    for fname, i, a, b in all_rows:
        cn = has_cn(b)
        ka = has_kana(b)
        if not cn and not ka:
            kind = "ART-COPY (no CN/kana -> restore SRC)"
        elif cn and ka:
            kind = "RESIDUAL (CN + kana -> retranslate)"
        elif cn:
            kind = "LONG-CN (too long -> retranslate)"
        else:
            kind = "KANA-ONLY"
        print(f"### {fname.split('_')[0]} line {i} [{kind}] {disp_width(a)}->{disp_width(b)}")
        sm = difflib.SequenceMatcher(None, a, b)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op != 'equal':
                print(f"    {op:6s} SRC={a[i1:i2]!r}  TR={b[j1:j2]!r}")
        print()


if __name__ == "__main__":
    main()
