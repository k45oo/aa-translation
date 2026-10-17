# -*- coding: utf-8 -*-
"""半角假名段级翻译修复 (2026-08-25)

背景: mix 行整行翻译导致超宽错位。正确做法=段级翻译(segments)。
这些行 meta 已有 segments 但 text 段 confidence=0.55(低)被过滤; L745 无 segments。

流程:
1. 为 6 行重建 segments: 右侧半角假名段→text(conf=1.0), 其余→art
2. 写回 line_meta_v2/<章>.json
3. 用 translate_lines_mixed 段级翻译
4. 写回汉化 txt(宽度校正)

用法: python scripts/tools/fix_hankaku_segs.py
"""
import os, re, sys, json

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(BASE, "scripts"))
sys.path.insert(0, os.path.join(BASE, "scripts", "common"))
from aa_common import disp_width, pad_to_width

WORK = "sujin_shabu"
BASE_DIR = os.path.join(BASE, "aaManga", "やる夫の生娘シャブ漬け大戦略")
OUT = os.path.join(BASE_DIR, "汉化")
AA = os.path.join(BASE_DIR, "aa")
META = os.path.join(BASE_DIR, "work", "line_meta_v2")

# (章节, 0-based行号, 台词段文本[原文中待翻译的半角假名串]) 
# 台词段=右侧半角假名连续段(拟声词/口语)
TARGETS = [
    ("entry_36129", 1051, "ﾒﾁｬｸﾁｬｱﾔｼｲｹﾄﾞ"),
    ("entry_8765", 599, "ｷｭｳｯ（突如道端で倒れてそのまま壁際までゴロゴロ"),
    ("entry_8765", 744, "ﾕ、ﾕﾙｻﾚﾀ……。"),
    ("entry_8765", 849, "ﾅﾝｶﾃﾞｷﾀｰ"),
    ("entry_8765", 937, "ｶﾞｸｶﾞｸ"),
    ("entry_8765", 1250, "ｱ､ｱｲｽﾀﾞｯﾂﾀﾞ..."),
]

HANKAKU = re.compile(r'[\uff61-\uff9f]')


def rebuild_segments(line, seg_txt):
    """为行重建 segments: 找到 seg_txt 在行中的位置→text段, 其余→art段。"""
    # 找到 seg_txt 的起始位置(可能带前导空格)
    pos = line.find(seg_txt)
    if pos < 0:
        # 尝试宽松匹配(去掉尾部标点)
        core = seg_txt.rstrip('。、…．')
        pos = line.find(core)
        if pos < 0:
            return None
        seg_txt = core
    end = pos + len(seg_txt)
    segs = []
    if pos > 0:
        segs.append({"start": 0, "end": pos, "kind": "art", "src_width": disp_width(line[:pos])})
    segs.append({"start": pos, "end": end, "kind": "text", "src_width": disp_width(seg_txt), "confidence": 1.0})
    if end < len(line):
        segs.append({"start": end, "end": len(line), "kind": "art", "src_width": disp_width(line[end:])})
    return segs


def main():
    import aa_translate as A
    for f, idx, seg_txt in TARGETS:
        src_p = os.path.join(AA, f + ".txt")
        tr_p = os.path.join(OUT, f + "_汉化.txt")
        src = open(src_p, encoding="utf-8").read().split("\n")
        tr = open(tr_p, encoding="utf-8").read().split("\n")
        line = src[idx]
        # 1. 重建 segments
        segs = rebuild_segments(line, seg_txt)
        if not segs:
            print(f"{f} L{idx+1}: 找不到台词段 {seg_txt!r}, 跳过")
            continue
        # 2. 写回 meta
        meta = A.meta_load(META, f, {}) or {}
        meta[str(idx + 1)] = {"segments": segs}
        A.meta_dump(META, f, meta)
        # 3. 段级翻译
        try:
            fixed, missed, warns = A.translate_lines_mixed([(idx, line, segs)], work_r18=False)
        except Exception as e:
            print(f"{f} L{idx+1}: 段级翻译失败 {e}")
            continue
        if idx in fixed:
            new_line = fixed[idx]
            # 4. 宽度校正
            sw = disp_width(src[idx])
            if disp_width(new_line) < sw:
                new_line = pad_to_width(new_line, sw)
            tr[idx] = new_line
            open(tr_p, "w", encoding="utf-8").write("\n".join(tr))
            print(f"{f} L{idx+1}: {seg_txt!r} → {new_line[new_line.find('｜')+1:].strip()[-40:]!r} ({disp_width(new_line)}/{sw})")
        else:
            print(f"{f} L{idx+1}: 段级翻译失败 missed={missed[:3]}")
    # 5. 全量验证
    for f, idx, _ in TARGETS:
        src = open(os.path.join(AA, f + ".txt"), encoding="utf-8").read().split("\n")
        tr = open(os.path.join(OUT, f + "_汉化.txt"), encoding="utf-8").read().split("\n")
        s, t = src[idx], tr[idx]
        print(f"  {f} L{idx+1}: {disp_width(t)}/{disp_width(s)} | {t.strip()[-35:]!r}")


if __name__ == "__main__":
    main()
