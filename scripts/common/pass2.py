# -*- coding: utf-8 -*-
"""pass2: 清理漏译行（模型原样复制的日语行 / 保留的日文拟声词）。

用法：python common/pass2.py --work <id>
只处理 <out_dir>/ 下 *_汉化.txt；跳过 #/= 标记行与帖头行。
"""
import os, re, sys, concurrent.futures

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(_SCRIPT_DIR), "works"))

import registry
import aa_api as A
import aa_common as C

# 2026-10-14 去重: 本地 residual_text 副本已删（零调用点；权威实现 = aa_translate.residual_text，
#   经 C 转发，本文件只用 C.residual_text_segs）。HIRA/KATA/ART_HIRA/ART_KATA 仅它用过，一并删。
HEADER_RE = re.compile(r'^\d+\s*：|^\d+\s*名前')


def _flag_on(name, default_on=True):
    v = os.environ.get(name)
    if v is None:
        return default_on
    return v not in ("0", "false", "False", "")


PASS2_SYSTEM = """你是やる夫AA漫画的汉化翻译。下面每行以「序号|」开头。

判断并处理每一行：
- 如果行中包含【未翻译的日语文字】（假名/日文汉字组成的词句），将其翻译为简体中文，同时保持行内绘画字符和整行显示宽度不变（变短用全角空格U+3000填充）。
- 如果行中的假名只是【绘画装饰】（如 うしふとこ 等散落的假名）、【角色名/专有名词】（如 キング、フランドール、イギィア）、或行中已经没有可翻译的日语文字，就【原样照抄输出】。
- 输出格式：每行「序号|处理后的完整行」，必须输出所有给出的行，不要输出其他内容。"""


def fix_lines(flagged):
    """flagged: list of (idx, line). Returns (fixed dict, still list)"""
    pending = {i: l for i, l in flagged}
    fixed = {}
    for round_ in range(3):
        if not pending:
            break
        body = "\n".join(f"{i:05d}|{l}" for i, l in pending.items())
        text = A.call_api(PASS2_SYSTEM, body)
        res = A.parse_result(text)
        for num, nl in res.items():
            if num in pending and num not in fixed:
                fixed[num] = C.pad_to_width(nl, C.disp_width(pending[num]))
        pending = {i: l for i, l in pending.items() if i not in res}
    return fixed, list(pending.keys())


def main(work=None):
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    cfg = registry.get(work or (args[0] if args else "baitou"))
    OUT_DIR = cfg["out_dir"]
    WORK_DIR = cfg["work_dir"]
    files = sorted(f for f in os.listdir(OUT_DIR) if f.endswith("_汉化.txt"))
    for fname in files:
        path = os.path.join(OUT_DIR, fname)
        lines = open(path, encoding="utf-8").read().split("\n")
        # B2 同步: mixed 段级默认开（AA_USE_MIXED_SEGMENTS=0 关闭）时按 segments 跳过 art 段残留检测
        seg_meta = {}
        if _flag_on("AA_USE_MIXED_SEGMENTS"):
            seg_meta = C.load_segments_meta(os.path.join(WORK_DIR, "line_meta_v2"),
                                            fname.replace("_汉化.txt", ""))
        flagged = [(i, l) for i, l in enumerate(lines)
                   if C.residual_text_segs(l, seg_meta.get(i)) and not l.startswith(('#', '=')) and not HEADER_RE.match(l)
                   and not C.is_manual_fix(WORK_DIR, fname, i, l)  # P0-C: 人工修复行跳过
                   and seg_meta.get(i) is None]  # 2026-09-07 grilling-C: 砍 mixed 分支——mixed 行残留跳过留 refine_aa 段级重建清(实测 pass2 整行送模型对 art 密集 mixed 行原样照抄清不动, refine 程序化切段只翻台词段才清掉 21105 的 67 行)
        if not flagged:
            print(f"{fname}: clean")
            continue
        print(f"{fname}: {len(flagged)} residual lines -> fixing", flush=True)
        # split into chunks of 40 for reliability
        chunks = [flagged[i:i+40] for i in range(0, len(flagged), 40)]
        fixed_all, missed_all = {}, []
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            futs = {ex.submit(fix_lines, c): c for c in chunks}
            for fut in concurrent.futures.as_completed(futs):
                fixed, still = fut.result()
                fixed_all.update(fixed)
                missed_all.extend(still)
        for idx, nl in fixed_all.items():
            lines[idx] = nl
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"  fixed {len(fixed_all)}, still missed: {missed_all[:15]}", flush=True)


if __name__ == "__main__":
    main()
