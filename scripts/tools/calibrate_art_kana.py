# -*- coding: utf-8 -*-
"""艺术假名表标定 CLI（零 API）——实现见 scripts/common/aa_art_kana.py。

用法:
  python scripts/tools/calibrate_art_kana.py --work <id>          # 单作品 → <work>/art_kana_<id>.json
  python scripts/tools/calibrate_art_kana.py --all                # 全作品合并 → scripts/config/aa_art_kana.json
  python scripts/tools/calibrate_art_kana.py --work <id> --report # 只打印，不落盘
  python scripts/tools/calibrate_art_kana.py --dir <作品根目录>    # 不走 registry（手工目录）

真值口径：整行产物 == 原文 = 这一步什么都不该做（装饰/艺术行）→ 该行里所有假名计"艺术"；
产物 != 原文且该字符消失 → 计"被译"。ratio = 艺术/(艺术+被译)，见 aa_art_kana 模块注释。
"""
import argparse
import gzip
import io
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(BASE, "scripts", "common"))
sys.path.insert(0, os.path.join(BASE, "scripts", "works"))
import aa_art_kana as AK          # noqa: E402


def _read_lines(path):
    opener = gzip.open if path.endswith(".gz") else io.open
    kw = {} if path.endswith(".gz") else {"encoding": "utf-8"}
    with opener(path, "rt", **kw) as f:
        return f.read().split("\n")


def _chapter_pairs(aa_dir, tr_dir, src_ext=".txt", tr_suffix="_汉化.txt"):
    """[(src_lines, tr_lines)] —— 只取两侧都在的章（行数不等 → 跳过并计数）。"""
    import glob
    pairs, skipped = [], 0
    for p in sorted(glob.glob(os.path.join(aa_dir, "*" + src_ext))):
        base = os.path.basename(p)[: -len(src_ext)]
        if base.endswith("_台词"):
            continue
        for cand in (os.path.join(tr_dir, base + tr_suffix),
                     os.path.join(tr_dir, base + tr_suffix + ".gz")):
            if os.path.exists(cand):
                src, tr = _read_lines(p), _read_lines(cand)
                if len(src) == len(tr):
                    pairs.append((src, tr))
                else:
                    skipped += 1
                break
    return pairs, skipped


def _cfg_of(work):
    import registry
    cfg = registry.get(work)
    if not cfg:
        raise SystemExit("未注册作品: " + work)
    return cfg


def _work_dirs(work, dir_arg=""):
    if dir_arg:
        root = os.path.abspath(dir_arg)
        return (os.path.join(root, "aa"), os.path.join(root, "汉化"),
                os.path.join(root, "work"), os.path.basename(root))
    cfg = _cfg_of(work)
    return cfg["aa_dir"], cfg["out_dir"], cfg["work_dir"], work


def calibrate_one(work="", dir_arg=""):
    aa_dir, tr_dir, work_dir, wid = _work_dirs(work, dir_arg)
    pairs, skipped = _chapter_pairs(aa_dir, tr_dir)
    ratios, obs, rows = AK.calibrate_pairs(pairs)
    return {"work": wid, "chapters": len(pairs), "skipped": skipped, "rows": rows,
            "ratios": ratios, "obs": obs, "work_dir": work_dir, "aa_dir": aa_dir}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", help="作品 id（works/registry.py）")
    ap.add_argument("--dir", default="", help="作品根目录（不走 registry）")
    ap.add_argument("--all", action="store_true", help="全部作品合并 → 内置基线表")
    ap.add_argument("--report", action="store_true", help="只报告，不落盘")
    ap.add_argument("--top", type=int, default=40, help="报告条数")
    args = ap.parse_args()

    if args.all:
        import registry
        works = [w["id"] if isinstance(w, dict) else w for w in registry.all_works()]
        agg_art, agg_kept, agg_lost, rows, chaps = {}, {}, {}, 0, 0
        for wid in sorted(works):
            try:
                cfg = registry.get(wid)
                if not cfg or not os.path.isdir(cfg["aa_dir"]):
                    continue
                pairs, _sk = _chapter_pairs(cfg["aa_dir"], cfg["out_dir"])
                if not pairs:
                    continue
                ratios, obs, r = AK.calibrate_pairs(pairs)
                rows += r
                chaps += len(pairs)
                for c, (a, k, l) in obs.items():
                    agg_art[c] = agg_art.get(c, 0) + a
                    agg_kept[c] = agg_kept.get(c, 0) + k
                    agg_lost[c] = agg_lost.get(c, 0) + l
                print("[calibrate] %-28s 章 %3d 行 %7d" % (wid, len(pairs), r), flush=True)
            except Exception as e:
                print("[calibrate] %s 跳过: %s" % (wid, e), flush=True)
        t = sorted(set(agg_art) | set(agg_kept) | set(agg_lost))
        obs = {c: [agg_art.get(c, 0), agg_kept.get(c, 0), agg_lost.get(c, 0)] for c in t}
        ratios = {c: round(obs[c][0] / float(sum(obs[c])), 4)
                  for c in t if sum(obs[c]) > 0}
        out = {"schema": AK.SCHEMA, "date": time.strftime("%Y-%m-%d %H:%M:%S"),
               "kind": "global-baseline", "works": len(works), "chapters": chaps,
               "rows": rows, "ratios": ratios, "obs": obs}
        if args.report:
            _print_report(out, args.top)
            return 0
        os.makedirs(os.path.dirname(AK.BUNDLED_TABLE), exist_ok=True)
        with io.open(AK.BUNDLED_TABLE, "w", encoding="utf-8", newline="\n") as f:
            json.dump(out, f, ensure_ascii=False, sort_keys=True, indent=1)
        print("[calibrate] 写入 %s（%d 字符，%d 章）" % (AK.BUNDLED_TABLE, len(ratios), chaps))
        _print_report(out, args.top)
        return 0

    if not args.work and not args.dir:
        ap.error("需要 --work <id> 或 --dir <目录> 或 --all")

    res = calibrate_one(args.work, args.dir)
    out = {"schema": AK.SCHEMA, "date": time.strftime("%Y-%m-%d %H:%M:%S"),
           "kind": "work", "work": res["work"], "chapters": res["chapters"],
           "rows": res["rows"], "skipped": res["skipped"],
           "ratios": res["ratios"], "obs": res["obs"]}
    if not args.report:
        p = os.path.join(res["work_dir"], "art_kana_%s.json" % res["work"])
        with io.open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(out, f, ensure_ascii=False, sort_keys=True, indent=1)
        print("[calibrate] 写入 %s" % p)
    _print_report(out, args.top)
    return 0


def _print_report(out, top):
    ratios, obs = out["ratios"], out["obs"]
    sig = [(c, ratios[c], obs[c][0], sum(obs[c][1:])) for c in ratios
           if sum(obs[c]) >= AK.min_obs()]
    sig.sort(key=lambda x: -x[1])
    print("[calibrate] %s 章 %d 行 %d 字符 %d（观测≥%d 的 %d 个）"
          % (out.get("work") or "全局", out["chapters"], out["rows"], len(ratios),
             AK.min_obs(), len(sig)))
    print("  阈值 %.2f → 艺术字符 %d 个；示例：" % (AK.ratio_th(),
                                                 sum(1 for c, r, _a, _l in sig if r >= AK.ratio_th())))
    top_art = [x for x in sig if x[1] >= AK.ratio_th()][:top]
    top_dia = sorted([x for x in sig if x[1] < AK.ratio_th()], key=lambda x: x[1])[:12]
    print("   艺术: " + " ".join("%s%.2f" % (c, r) for c, r, _a, _l in top_art))
    print("   台词: " + " ".join("%s%.2f" % (c, r) for c, r, _a, _l in top_dia))


if __name__ == "__main__":
    sys.exit(main())
