# -*- coding: utf-8 -*-
"""fix_num_split: 回修「数字记号被切坏」的已交付行（零 API，机械修）。

来历（2026-10-17）：`tools/line_classify_v2` 的段级切分曾把数字切在中间（`1.5倍`→art `1.`+text `5倍`），
送模型的是残句，于是已交付行出现三类坏值：
  ① **插词**：数字分隔符后紧跟中文动词——`（1.变成了1倍）`（源 `1.1倍`）、`1/都说到3`（源 `1/3`）、
     `3-是3这件事`（源 `3-3`）
  ② **补位**：数字内部被塞 U+3000（art 的 `.` 要守列位）——`（全1　　　.33倍）`
  ③ **半切/丢号**：数字被吃掉或整句仍日文（机械修不了）
修法（逐行，最小编辑）：
  ① 删掉分隔符后的插入词；② 去掉数字内部的空白；
  两者都必须过验收：**源行数字记号原样出现在修后行**、宽度补到源宽、不引入新假名、
  改动只落在被修的跨度内。验收不过 → 只进 `--todo-out` 清单（交 `retranslate_lines.py` 重译）。
写盘顺序 = 备份 `work/backup_num_<ts>/` → 汉化 + ckpt 同步 + `work/edits.jsonl` 流水（阅读器可逐行还原）。

用法:
  python scripts/fix/fix_num_split.py --work ポケの子                      # dry-run 列表
  python scripts/fix/fix_num_split.py --work ポケの子 --apply              # 写盘
  python scripts/fix/fix_num_split.py --work ポケの子 --todo-out temp/todo.txt
"""
import argparse
import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "works"), _HERE):
    sys.path.insert(0, _p)

import aa_translate as T          # noqa: E402
import registry                   # noqa: E402

NUM = re.compile(r'[0-9０-９]+(?:[.．/／\-－～~][0-9０-９]+)+')
# ① 插词：数字 + 分隔符 + **汉字串** + 数字（模型把数字当句末，在中间插了词：
#    `1.变成了1倍`、`1/都说到3`、`3-是3`、`1.无视1倍`）。修法=删掉这段汉字（要求全是汉字）。
INSERT = re.compile(r'(?<=[0-9０-９])([.．/／\-－～~])([一-龥]{1,8})(?=[0-9０-９])')
# ② 补位：数字内部夹了空白
PAD_IN = re.compile(r'([0-9０-９])[ \u3000]+([.．/／\-－～~])[ \u3000]*([0-9０-９])')
KANA = re.compile(r'[\u3041-\u309f\u30a1-\u30fa]')
# 三类坏值指纹（用于区分「本次修法该管的行」与「数字被合法意译」的行，如 1/3→三分之一）
FP_INSERT = re.compile(r'[0-9０-９]\s*[.．/／\-－～~]\s*[一-龥]')
FP_PAD = re.compile(r'[0-9０-９][ \u3000]+[.．][0-9０-９]')


def _kana_runs(s):
    return len(KANA.findall(s.replace('ー', '')))


def repair_line(src, zh):
    """→ (new_zh or None, 原因)。机械修，最小编辑 + 严格验收。"""
    if not NUM.search(src):
        return None, '源行无数字记号'
    toks = [m.group(0) for m in NUM.finditer(src)]
    miss = [t for t in toks if t not in zh]
    if not miss:
        return None, '数字记号已完整'
    out = zh
    # ② 先拆补位（在数字内部），再处理插词
    for _ in range(4):
        m = PAD_IN.search(out)
        if not m:
            break
        out = out[:m.start()] + m.group(1) + m.group(2) + m.group(3) + out[m.end():]
    for _ in range(4):
        m = INSERT.search(out)
        if not m:
            break
        out = out[:m.end(1)] + out[m.end(2):]     # 只删分隔符后那段汉字，保留两侧数字
    if out == zh:
        return None, '无机械可修点'
    if any(t not in out for t in toks):
        return None, f'修后仍缺数字 {[t for t in toks if t not in out]}'
    if _kana_runs(out) > _kana_runs(zh):
        return None, '修后引入假名'
    if T.disp_width(out.split('\u3000')[0]) > T.disp_width(src.split('\u3000')[0]) + 8:
        return None, '修后明显超宽'
    # 补宽到源宽（只补不裁）
    if T.disp_width(out) < T.disp_width(src):
        out = T.pad_to_width(out, T.disp_width(src))
    return out, '机械修'


def scan(work, cfg):
    aa_dir, out_dir = cfg["aa_dir"], cfg["out_dir"]
    rows = []
    skipped = 0
    for fn in sorted(os.listdir(aa_dir)):
        if not fn.endswith('.txt') or fn.endswith('_台词.txt'):
            continue
        base = fn[:-4]
        zp = os.path.join(out_dir, base + '_汉化.txt')
        if not os.path.exists(zp):
            continue
        S = T.txt_read(os.path.join(aa_dir, fn))
        Z = T.txt_read(zp)
        if len(S) != len(Z):
            continue
        for i, s in enumerate(S):
            if not s or s.startswith('#') or not NUM.search(s):
                continue
            if T.line_kind(s) == 'art':
                continue
            z = Z[i]
            if z == s:
                continue
            new, why = repair_line(s, z)
            if new and new != z:
                rows.append({"base": base, "line": i, "cur": z, "new": new, "why": "",
                             "kind": "数字被切", "src": s})
            elif why != '数字记号已完整' and (FP_INSERT.search(z) or FP_PAD.search(z)):
                # 有坏值指纹但机械修不了（数字被换词/吃掉）→ 交重译
                rows.append({"base": base, "line": i, "cur": z, "new": "", "why": why,
                             "kind": "数字被切", "src": s})
            else:
                skipped += 1        # 数字不见了但无坏值指纹：多为合法意译（1/3→三分之一），不管
    return rows, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--todo-out", default="")
    a = ap.parse_args()
    cfg = registry.get(a.work)
    T.WORK_ID = a.work
    rows, skipped = scan(a.work, cfg)
    ok = [r for r in rows if not r["why"]]
    todo = [r for r in rows if r["why"]]
    print(f"[fix_num_split] {a.work}: 机械可修 {len(ok)} 行 | 交重译 {len(todo)} 行"
          f" | 数字形变但无坏值指纹（多为合法意译，如 1/3→三分之一）{skipped} 行")
    for r in ok[:10]:
        print(f"  {r['base']}:{r['line'] + 1}")
        print(f"     OLD: {r['cur'].strip()[:90]}")
        print(f"     NEW: {r['new'].strip()[:90]}")
    for r in todo[:10]:
        print(f"  [todo] {r['base']}:{r['line'] + 1} {r['why']}")
    if a.todo_out and todo:
        with open(a.todo_out, 'w', encoding='utf-8') as f:
            for r in todo:
                f.write(f"{r['base']}_汉化.txt:{r['line'] + 1}\t[{r['why']}] {r['src'].strip()[:60]}\n")
        print(f"  → 清单 {a.todo_out}")
    if not a.apply or not ok:
        return 0
    import audit_alignment as AA          # 复用写盘通道（备份/ckpt/edits.jsonl/prune）
    by_ch = {}
    for r in ok:
        by_ch.setdefault(r["base"], []).append(r)
    tot = 0
    for base, rs in by_ch.items():
        tot += AA.apply_chapter(a.work, base, cfg, rs)
    print(f"[fix_num_split] 已落地 {tot} 行（备份 work/backup_align_*，流水 edits.jsonl）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
