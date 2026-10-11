# -*- coding: utf-8 -*-
"""fix_delivered_terms: 回修**已交付**汉化文本里「词表/补位造成的确定性错误」（零 API）。

背景（2026-10-11，ポケの子 用户报错）：
  词表脏条目/缺条目 + 段落补位落点，会在**已交付**成品里留下确定性错误。根因修好之后
  还要把存量补平，否则下次重译/校对会照旧复发。
  - 确定性可机械修：补位落点（`>>` 左侧、括号/斜杠注记内部、塞进 `>>N`/比分的字）、
    源锚定名词替换（タスキ→气势披带、とんぼ→折返、ちゅうがえり→空翻折返）。
  - 语义错译**不可**机械修（模型在「被替换过的日文」上写出来的中文，得重译）——
    本工具只把它们列进 `--todo` 清单（默认写 <work_dir>/retranslate_todo.txt）。

用法:
  python scripts/fix/fix_delivered_terms.py --work ポケの子            # 只报告
  python scripts/fix/fix_delivered_terms.py --work ポケの子 --apply     # 备份后写回
"""
import os, sys, argparse, shutil, time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE)))          # scripts/
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "common"))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "works"))

import aa_translate as T
import aa_common as C
import pre_terms as P
import registry

# 源锚定名词替换：(源必须含, 源必须不含, 译文替换表)
# 只放**词表口径**造成的名词错，不放语义/措辞问题。
TERM_RULES = [
    (("タスキ",), ("ハチマキ",), [("气势头带", "气势披带")]),
    (("とんぼ", "トンボ"), ("つばめがえし",), [
        ("先行蜻蜓", "先行折返"), ("先制蜻蜓", "先制折返"), ("鬼蜻蜓", "鬼折返"),
        ("蜻蜓技", "折返技"), ("「燕返」", "「折返」"), ("燕返", "折返"),
    ]),
]


def _hits_jp(src, base, pairs):
    try:
        _, hits = P.apply_hits(src, base=base, pairs=pairs, work=None)
    except Exception:
        return set()
    return {(h.get("jp") if isinstance(h, dict) else "") for h in hits or []}


def process(work, apply=False, todo_path=None):
    cfg = registry.get(work)
    aa_dir, out_dir, work_dir = cfg["aa_dir"], cfg["out_dir"], cfg["work_dir"]
    files = sorted(f for f in os.listdir(out_dir) if f.endswith("_汉化.txt"))
    stat = {"files": 0, "pad": 0, "term": 0, "todo": 0, "width_bad": 0}
    todo = []
    changed_files = []
    for fn in files:
        base = fn[:-len("_汉化.txt")]
        ap = os.path.join(aa_dir, base + ".txt")
        if not os.path.exists(ap):
            continue
        src = open(ap, encoding="utf-8", errors="ignore").read().split("\n")
        tr = open(os.path.join(out_dir, fn), encoding="utf-8", errors="ignore").read().split("\n")
        if len(src) != len(tr):
            print(f"[{base}] !! 行数不齐 {len(src)}/{len(tr)} → 跳过")
            continue
        stat["files"] += 1
        pairs = P.pairs_for(base, work=work)
        new = []
        n_pad = n_term = 0
        for i, (s, t) in enumerate(zip(src, tr)):
            o = T.normalize_pad_placement(t)
            if o != t:
                n_pad += 1
                t = o
            for must, mustnot, swaps in TERM_RULES:
                if not any(x in s for x in must) or any(x in s for x in mustnot):
                    continue
                for a, b in swaps:
                    if a in t and not (a == "折返" and "空翻折返" in t):
                        t2 = t.replace(a, b)
                        if t2 != t:
                            t = t2
                            n_term += 1
            if "ちゅうがえり" in s and "空翻折返" not in t and "折返" in t:
                t = t.replace("折返", "空翻折返")
                n_term += 1
            if C.disp_width(t) < C.disp_width(s):
                stat["width_bad"] += 1
            new.append(t)
        stat["pad"] += n_pad
        stat["term"] += n_term
        # 语义错译清单（用修好的词表回算：该替换却没替换的，说明中文是模型在被污染的日文上写的）
        hj = None
        for i, (s, t) in enumerate(zip(src, tr)):
            if ("德其欧" in t and "できる" in s and "できる夫" not in s and "でっき" not in s) \
               or ("波导" in t and "はどう" in s) or ("灼伤" in t and "やけど" in s):
                if hj is None:
                    hj = _hits_jp(s, base, pairs)
                need = []
                if "德其欧" in t and "できる" in s and "できる夫" not in s and "でっき" not in s:
                    need.append("できる")
                if "波导" in t and "はどう" in s and "はどう" not in hj:
                    need.append("はどう")
                if "灼伤" in t and "やけど" in s and "やけど" not in hj:
                    need.append("やけど")
                if need:
                    todo.append((fn, i + 1, "/".join(need), s, t))
                    stat["todo"] += 1
        if (n_pad or n_term) and apply:
            changed_files.append((fn, new))
    print(f"[stat] 文件 {stat['files']} / 补位修正 {stat['pad']} 行 / 名词替换 {stat['term']} 行 "
          f"/ 需重译 {stat['todo']} 行 / 变短行 {stat['width_bad']}")
    if apply and changed_files:
        bak = os.path.join(work_dir, "backup_fixdelivered_" + time.strftime("%Y%m%d%H%M%S"))
        os.makedirs(bak, exist_ok=True)
        for fn, new in changed_files:
            p = os.path.join(out_dir, fn)
            shutil.copy2(p, os.path.join(bak, fn))
            with open(p, "w", encoding="utf-8") as f:
                f.write("\n".join(new))
        print(f"[apply] 写回 {len(changed_files)} 文件，备份 {bak}")
        T.prune_backup_dirs(work_dir, "backup_fixdelivered_")
    tp = todo_path or os.path.join(work_dir, "retranslate_todo.txt")
    with open(tp, "w", encoding="utf-8") as f:
        for fn, ln, why, s, t in todo:
            f.write(f"{fn}:{ln}\t{why}\n  SR: {s}\n  TR: {t}\n")
    print(f"[todo] {len(todo)} 行 → {tp}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--todo", default=None)
    a = ap.parse_args()
    process(a.work, apply=a.apply, todo_path=a.todo)


if __name__ == "__main__":
    main()
