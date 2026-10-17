# -*- coding: utf-8 -*-
"""修复 touhou_teisou 剩余 27 行超宽：从行尾/文本前删 U+3000 对齐 SRC 宽度。

原则：只删 U+3000（2 格），不动任何艺术/文本字符；delta 为偶数才可修（实测全偶）。
超宽行 = 宽横幅行中间嵌台词，模型重译仍超宽 → 删空格保对齐。
"""
import os, re, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))
import aa_common as C
import registry

cfg = registry.get("touhou_teisou")
AA_DIR, OUT_DIR = cfg["aa_dir"], cfg["out_dir"]

def fix_line(line, target_w):
    """删 U+3000 使宽度 == target_w；返回新行或 None（无法修复）"""
    delta = C.disp_width(line) - target_w
    if delta <= 0:
        return line if delta == 0 else None
    # 只处理偶数格差（U+3000 占 2 格）
    if delta % 2 != 0:
        return None
    n = delta // 2  # 需删的 U+3000 数
    # 1) 行尾 U+3000（先 strip 行尾再补回语义）
    stripped = line.rstrip('\u3000')
    removed = len(line) - len(stripped)
    take = min(removed, n)
    line = line[:len(line) - take]
    n -= take
    if n == 0:
        return line
    # 2) 行首 U+3000
    lstripped = line.lstrip('\u3000')
    removed2 = len(line) - len(lstripped)
    take2 = min(removed2, n)
    line = line[take2:]
    n -= take2
    if n == 0:
        return line
    # 3) 文本片段前（含中文/假名片段左侧）的空格段
    #    找最后一个连续 U+3000 段（最靠近文本的空白）
    for m in re.finditer(r'\u3000+', line):
        pass
    spans = [m for m in re.finditer(r'\u3000+', line)]
    for m in reversed(spans):
        if n == 0:
            break
        seg_len = m.end() - m.start()
        take3 = min(seg_len, n)
        line = line[:m.start()] + line[m.start() + take3:]
        n -= take3
    return line if n == 0 else None

def main():
    rows = []
    for fname in sorted(os.listdir(AA_DIR)):
        if not (fname.endswith(".txt") or fname.endswith(".txt.gz")):
            continue
        src = C.txt_read(os.path.join(AA_DIR, fname))
        dst = os.path.join(OUT_DIR, fname.replace(".txt", "_汉化.txt"))
        if not os.path.exists(dst):
            continue
        tr = open(dst, encoding="utf-8").read().split("\n")
        for i, (a, b) in enumerate(zip(src, tr)):
            if C.disp_width(a) != C.disp_width(b):
                rows.append((fname, i, a, b))
    print(f"collect {len(rows)} mismatch rows", flush=True)
    fixed, unfixable = 0, []
    by_file = {}
    for fname, i, a, b in rows:
        by_file.setdefault(fname, []).append((i, a, b))
    for fname, items in by_file.items():
        dst = os.path.join(OUT_DIR, fname.replace(".txt", "_汉化.txt"))
        lines = open(dst, encoding="utf-8").read().split("\n")
        changed = False
        for i, a, b in items:
            target = C.disp_width(a)
            new = fix_line(b, target)
            if new is None:
                unfixable.append((fname, i, a, b))
            else:
                lines[i] = new
                changed = True
                fixed += 1
        if changed:
            open(dst, "w", encoding="utf-8").write("\n".join(lines))
        print(f"{fname.split('_')[0]}: {len(items)} rows, fixed {len(items)-len([1 for x in items if (lambda p: False)(p)])}?" if False else f"{fname.split('_')[0]}: done", flush=True)
    print(f"\nTOTAL fixed {fixed}, unfixable {len(unfixable)}", flush=True)
    for fname, i, a, b in unfixable[:10]:
        print(f"  UNFIX {fname} L{i+1}: {C.disp_width(a)}->{C.disp_width(b)}")

if __name__ == "__main__":
    main()
