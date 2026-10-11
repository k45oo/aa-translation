# -*- coding: utf-8 -*-
"""retranslate_lines: 给「已交付」章按**指定行清单**做定向重译（复用主块协议，零结构改动）。

用途：词表脏条目（できる→德其欧 / はどう→波导 / やけど→灼伤…）把**送模型的日文**替换脏了，
模型据此写出来的中文是错的——这类语义错译只能重译，不能机械替换。词表修好之后用本工具
把存量行重翻译一遍（行清单由 `fix_delivered_terms.py` 的 --todo 产出）。

复用 `aa_translate.translate_lines`（宽度保持 / art 列位 / 照抄 / 残留 四重校验都在它里面），
校验不过的行保持原样（不写坏），最后统一过 `normalize_lines`（补位位置）。

用法:
  python scripts/fix/retranslate_lines.py --work ポケの子 --list <todo>            # dry-run
  python scripts/fix/retranslate_lines.py --work ポケの子 --list <todo> --apply
"""
import os, sys, re, argparse, shutil, time, collections

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "common"))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "works"))

import aa_translate as T
import aa_common as C
import registry


def parse_list(path):
    """→ {fname: set(1-based 行号)}。接受 `fix_delivered_terms.py --todo` 的格式。"""
    out = collections.defaultdict(set)
    rx = re.compile(r"^(entry_\d+_汉化\.txt):(\d+)\t")
    with open(path, encoding="utf-8") as f:
        for ln in f:
            m = rx.match(ln)
            if m:
                out[m.group(1)].add(int(m.group(2)))
    return out


def process(work, list_path, apply=False, limit=None):
    cfg = registry.get(work)
    T.WORK_ID = work                      # pre_terms / glossary_hint / r18 判定都要它
    aa_dir, out_dir, work_dir = cfg["aa_dir"], cfg["out_dir"], cfg["work_dir"]
    want = parse_list(list_path)
    tot = sum(len(v) for v in want.values())
    print(f"[list] {tot} 行 / {len(want)} 章")
    bak = os.path.join(work_dir, "backup_retranslate_" + time.strftime("%Y%m%d%H%M%S"))
    done = 0
    for fn in sorted(want):
        base = fn[:-len("_汉化.txt")]
        ap, tp = os.path.join(aa_dir, base + ".txt"), os.path.join(out_dir, fn)
        if not os.path.exists(ap) or not os.path.exists(tp):
            print(f"[{base}] 缺原件/产物 → 跳过")
            continue
        src = open(ap, encoding="utf-8").read().split("\n")
        tr = open(tp, encoding="utf-8").read().split("\n")
        if len(src) != len(tr):
            print(f"[{base}] 行数不齐 → 跳过")
            continue
        idx = [i for i in sorted(want[fn]) if 1 <= i <= len(src)]
        if limit:
            idx = idx[:limit]
        if not idx:
            continue
        r18, low18 = T._chapter_work_r18(base, cfg)
        flagged = [(i - 1, src[i - 1]) for i in idx]
        try:
            fixed, missed, warns = T.translate_lines(flagged, work_r18=r18, low_r18=low18, base=base)
        except Exception as e:
            print(f"[{base}] 重译异常 {type(e).__name__}: {e}（保持原样）")
            continue
        new = list(tr)
        for i, nl in (fixed or {}).items():
            if nl and nl != src[i]:
                new[i] = nl
        n_ok = sum(1 for i in (fixed or {}) if new[i] != tr[i])
        done += n_ok
        print(f"[{base}] 目标 {len(idx)} 行 → 成功 {n_ok}，未返回/校验不过 {len(missed or [])}："
              f"{[m + 1 for m in sorted(missed or [])]}")
        if apply and n_ok:
            os.makedirs(bak, exist_ok=True)
            shutil.copy2(tp, os.path.join(bak, fn))
            new = T.normalize_lines(new)
            with open(tp, "w", encoding="utf-8") as f:
                f.write("\n".join(new))
    print(f"[stat] 重译写回 {done} 行")
    if apply:
        T.prune_backup_dirs(work_dir, "backup_retranslate_")
        print(f"[apply] 备份 {bak}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--list", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="每章最多处理多少行（试跑用）")
    a = ap.parse_args()
    process(a.work, a.list, apply=a.apply, limit=a.limit)


if __name__ == "__main__":
    main()
