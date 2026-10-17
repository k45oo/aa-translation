# -*- coding: utf-8 -*-
"""半角假名段级翻译——精简版(限时, 直接调 call_with_fallback)

已由 fix_hankaku_segs.py 重建 segments 的行, 逐行段级翻译(单次调用, 不重试)。
"""
import os, re, sys, json

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(BASE, "scripts", "common"))
sys.path.insert(0, os.path.join(BASE, "scripts"))
from aa_common import disp_width, pad_to_width
import aa_translate as A
import aa_backend as AB

WORK_DIR = os.path.join(BASE, "aaManga", "やる夫の生娘シャブ漬け大戦略")
OUT = os.path.join(WORK_DIR, "汉化")
AA = os.path.join(WORK_DIR, "aa")
META = os.path.join(WORK_DIR, "work", "line_meta_v2")

# 待译行: (章节, 0-based行号)
PENDING = [
    ("entry_8765", 744),   # ﾕ、ﾕﾙｻﾚﾀ……。
    ("entry_8765", 849),   # ﾅﾝｶﾃﾞｷﾀｰ
    ("entry_8765", 937),   # ｶﾞｸｶﾞｸ
    ("entry_8765", 1250),  # ｱ､ｱｲｽﾀﾞｯﾂﾀﾞ...
]

SYSTEM = ("你是やる夫AA漫画的资深汉化翻译。用户发来一个【文本片段】(可能是拟声词/口语/台词), "
          "把它翻译成简体中文: 自然贴切符合漫画语气。拟声词用意译(如 ﾄﾞｷﾄﾞｷ→心跳加速), "
          "口语按语境(如 めちゃくちゃ怪しい→非常可疑)。只输出译文本身, 不要加引号/解释/格式。"
          "角色名/专名保留日文。")

def main():
    for f, idx in PENDING:
        src = open(os.path.join(AA, f + ".txt"), encoding="utf-8").read().split("\n")
        tr = open(os.path.join(OUT, f + "_汉化.txt"), encoding="utf-8").read().split("\n")
        line = src[idx]
        # 从 meta 读 segments, 取 text 段
        meta = (A.meta_load(META, f, {}) or {})
        segs = meta.get(str(idx + 1), {}).get("segments", [])
        text_segs = [s for s in segs if s["kind"] == "text" and s.get("confidence", 1.0) >= 0.65]
        if not text_segs:
            print(f"{f} L{idx+1}: 无高置信 text 段, 跳过")
            continue
        seg = text_segs[0]
        orig = line[seg["start"]:seg["end"]]
        # 单次调用(30s 超时)
        try:
            txt, fb = AB.call_with_fallback(False, SYSTEM, f"1|{orig}", max_tokens=100, timeout=30)
        except Exception as e:
            print(f"{f} L{idx+1}: 调用失败 {str(e)[:50]}")
            continue
        m = re.search(r"1\|(.+)", txt, re.S)
        zh = (m.group(1) if m else txt).strip()
        # 拼回: 图形段原样 + 译文段(pad 到段宽)
        new_line = ""
        for s2 in segs:
            if s2["kind"] == "art":
                new_line += line[s2["start"]:s2["end"]]
            else:
                t = zh if s2 is seg else line[s2["start"]:s2["end"]]
                w = s2["src_width"]
                if disp_width(t) < w:
                    t = pad_to_width(t, w)
                new_line += t
        sw = disp_width(line)
        if disp_width(new_line) < sw:
            new_line = pad_to_width(new_line, sw)
        tr[idx] = new_line
        open(os.path.join(OUT, f + "_汉化.txt"), "w", encoding="utf-8").write("\n".join(tr))
        print(f"{f} L{idx+1} [{fb}]: {orig!r} → {zh!r} ({disp_width(new_line)}/{sw})")

if __name__ == "__main__":
    main()
