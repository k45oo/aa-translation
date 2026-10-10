# -*- coding: utf-8 -*-
"""交付 HTML 的 AA 等宽字体子集（woff2）生成 CLI —— 实现见 scripts/common/aa_font.py。

用法:
  python scripts/tools/make_aa_font.py --work <id>            # 按作品语料取字符集
  python scripts/tools/make_aa_font.py --work <id> --report   # 只报告缺字，不生成
  python scripts/tools/make_aa_font.py --chars "<字符>" [--font <ttf>] [--out-dir <dir>]

产物默认落 fonts/subset/aa_<sha1前12>.woff2（同字符集复用缓存）。
gen_aa_html 生成交付页时自动调用（`AA_AA_FONT=0` 关闭，细节见 common/aa_font.py 模块注释）。
"""
import argparse
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(BASE, "scripts", "common"))
import aa_font as F


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", help="作品 id（见 works/registry.py）")
    ap.add_argument("--chars", help="直接指定字符集")
    ap.add_argument("--font", default=F.DEFAULT_FONT)
    ap.add_argument("--out-dir", default=F.OUT_DIR)
    ap.add_argument("--report", action="store_true", help="只报告缺字，不生成")
    args = ap.parse_args()

    chars = F.base_chars()
    corpus = None
    if args.work:
        corpus = F.chars_of_work(args.work)
        print("[make_aa_font] %s 语料字符 %d" % (args.work, len(corpus)))
        chars |= corpus
    if args.chars:
        chars |= set(args.chars)

    if args.report:
        from fontTools.ttLib import TTFont
        cmap = TTFont(args.font, fontNumber=0, lazy=True).getBestCmap()
        missing = [c for c in sorted(chars) if ord(c) not in cmap]
        mc = [c for c in missing if corpus is not None and c in corpus]
        print("[make_aa_font] 共 %d 字符；字体缺 %d；其中语料里真出现 %d%s"
              % (len(chars), len(missing), len(mc),
                 ("：" + "".join(mc[:80])) if mc else ""))
        return

    out, missing, _ = F.build_subset(chars, args.font, args.out_dir)
    print("[make_aa_font] %s → %s（%d 字符，%.1f KB，字体缺 %d 字符 → 浏览器回退）"
          % (os.path.basename(args.font), out, len(chars),
             os.path.getsize(out) / 1024.0, len(missing)))


if __name__ == "__main__":
    main()
