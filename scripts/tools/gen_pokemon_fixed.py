#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""宝可梦作品固定内容表 CLI（2026-09-22）。

方案: doc/方案归档/宝可梦固定内容表方案_2026-09-22.md
核心逻辑全在 scripts/common/pokemon_fixed.py（与 pokemon_runner 共用，本文件只做 CLI 包装）。

用法:
  # 建表（wiki 派生；manual 永不覆盖）—— 零 API
  python scripts/tools/gen_pokemon_fixed.py --work <id> --build
  python scripts/tools/gen_pokemon_fixed.py --work <id> --rebuild            # derived 整体重生成
  python scripts/tools/gen_pokemon_fixed.py --work <id> --build --reuse      # 追加：从本流程定稿章回流

  # 看表 / 审表
  python scripts/tools/gen_pokemon_fixed.py --work <id> --stats
  python scripts/tools/gen_pokemon_fixed.py --work <id> --audit [--top 200]  # 待审清单 → work/fixed_audit.md

  # 回放验证（验收①：表 vs 已交付译文，零 API、不改任何文件）
  python scripts/tools/gen_pokemon_fixed.py --work <id> --diff [--chapters a,b]

  # 预填（翻译前写 ckpt done = 跳过送模型）；pokemon_runner 会自动调，一般不用手跑
  python scripts/tools/gen_pokemon_fixed.py --work <id> --prefill --chapters a,b [--dry]
"""
import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
for _sp in (_SCRIPTS, os.path.join(_SCRIPTS, "common"),
            os.path.join(_SCRIPTS, "works"), _HERE):
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

import pokemon_fixed as PF          # noqa: E402
import registry                     # noqa: E402


def _cfg(work):
    return registry.get(work)


def _audit(work, top):
    cfg = _cfg(work)
    work_dir = cfg["work_dir"]
    t = PF.load(work, work_dir)
    if not t:
        print("表不存在，先 --build")
        return 2
    # 高频统计：扫本作品原文，统计 struct/whole 命中行的出现次数
    import glob
    import aa_translate as A
    terms = PF.wiki_terms()
    aa_dir = cfg["aa_dir"]
    meta_dir = os.path.join(work_dir, "line_meta_v2")
    freq_s, freq_w = {}, {}
    for base in A.list_txt(aa_dir, exclude=("_台词.txt", "_汉化.txt")):
        if base.endswith("_台词"):
            continue
        lines = A.txt_read(A.txt_existing(os.path.join(aa_dir, base + ".txt")))
        meta = A.load_meta_0based(meta_dir, base)
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                if not A.is_translatable(i, line, meta):
                    continue
            except Exception:
                continue
            hit = PF.match_struct(line, t, terms)
            if hit:
                freq_s[hit["term"]] = freq_s.get(hit["term"], 0) + 1
                continue
            hw = PF.match_whole(line, t)
            if hw:
                k = PF.key(line)
                freq_w[k] = freq_w.get(k, 0) + 1

    p = os.path.join(work_dir, "fixed_audit.md")
    af = t["audit"].get("auto_fail") or []
    with open(p, "w", encoding="utf-8") as fo:
        fo.write(f"# 固定表待审清单 — {work}\n\n")
        fo.write(f"- 生成: {time.strftime('%Y-%m-%d %H:%M:%S')}（零 API）\n")
        fo.write(f"- 表: `{PF.table_path(work, work_dir)}`\n\n")
        fo.write(f"## ① 闸一自动拒绝（{len(af)} 条，**未入表**，该行照旧交模型）\n\n")
        for a in af[:120]:
            fo.write(f"- `{a.get('jp')}` [{a.get('why')}] desc={a.get('desc')!r}\n")
        fo.write(f"\n## ② 人工抽检：struct 命中 top {top}（按全书出现频次）\n\n")
        fo.write("| 频次 | 术语 | wiki译名 | 表内译文 |\n|---|---|---|---|\n")
        for term, c in sorted(freq_s.items(), key=lambda x: -x[1])[:top]:
            st = t["derived"]["struct"].get(PF.norm(term)) or {}
            fo.write(f"| {c} | {term} | {st.get('zh','')} | {str(st.get('desc',''))[:70]} |\n")
        if freq_w:
            fo.write(f"\n## ③ 整行轨条目（{len(freq_w)} 条命中 seen）\n\n")
            for k, c in sorted(freq_w.items(), key=lambda x: -x[1])[:top]:
                fo.write(f"- {c:5d}  {k[:70]}\n")
        if t.get("conflicts"):
            fo.write(f"\n## ④ conflicts（{len(t['conflicts'])} 条，与已定稿译文不一致，未自动改）\n\n")
            for cf in t["conflicts"][:120]:
                fo.write(f"- {json.dumps(cf, ensure_ascii=False)}\n")
        sus = t.get("audit", {}).get("suspect") or []
        if sus:
            fo.write(f"\n## ⑤ 可疑译文（{len(sus)} 条，**未入表**，等人工修）\n\n")
            fo.write("| 类型 | 来源 | 原行 | 可疑译文 | 全书频次 |\n|---|---|---|---|---|\n")
            for s in sus[:120]:
                fo.write(f"| {s.get('kind','')} | {s.get('_src','')} | {s.get('src','')[:48]} "
                         f"| {s.get('zh','')[:48]} | {s.get('n', 0)} |\n")
            fo.write("\n修法：改好译文后写进表的 `manual.whole`（人工区永不被 `--rebuild` 覆盖），"
                     "或直接手改 `derived.whole` 后重跑 `--prefill`。\n")
    print(f"[audit] → {p}\n  闸一拒 {len(af)} | struct top {len(freq_s)} 术语 | whole 命中 "
          f"{len(freq_w)} 条 | conflicts {len(t.get('conflicts') or [])}")
    print(f"  最高频 10: {[t for t, _ in sorted(freq_s.items(), key=lambda x: -x[1])[:10]]}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="宝可梦作品固定内容表（零 API）")
    ap.add_argument("--work", required=True)
    ap.add_argument("--build", action="store_true", help="建/更新表（derived 重生成，manual 保留）")
    ap.add_argument("--rebuild", action="store_true", help="同 --build 但忽略旧表（manual 仍保留）")
    ap.add_argument("--reuse", action="store_true", help="建表时追加：从本流程定稿章回流整行条目")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--audit", action="store_true", help="输出待审清单 work/fixed_audit.md")
    ap.add_argument("--top", type=int, default=200, help="--audit 抽检条数（默认 200）")
    ap.add_argument("--diff", action="store_true", help="回放：表 vs 已交付译文（验收①）")
    ap.add_argument("--scan", action="store_true", help="扫全书统计固定行族频次与状态行覆盖率")
    ap.add_argument("--seed", action="store_true",
                    help="种子轨：◎/【】 top-N 高频行的译文入表（优先取已交付译文）")
    ap.add_argument("--fill-seed", action="store_true",
                    help="把 pending_seed（无已交付译文）交模型一次性翻译并入表")
    ap.add_argument("--prefill", action="store_true", help="把命中行写进 ckpt done（跳过送模型）")
    ap.add_argument("--copy", action="store_true",
                    help="建 copy 轨（纯图形行 + 已交付未改动行；零翻译风险整行复用）")
    ap.add_argument("--min-freq", type=int, default=2, help="--copy 的最少全书出现次数（默认 2）")
    ap.add_argument("--chapters", help="章节 base（逗号分隔或 all）；--diff/--prefill/--scan 用")
    ap.add_argument("--families", default="◎,【】", help="--seed 的族（默认 ◎,【】）")
    ap.add_argument("--dry", action="store_true", help="--prefill 只统计不落盘")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    cfg = _cfg(a.work)
    if not cfg:
        print(f"registry 无此作品: {a.work}")
        return 2
    aa_dir, out_dir, work_dir = cfg["aa_dir"], cfg["out_dir"], cfg["work_dir"]
    bases = None
    if a.chapters:
        if a.chapters.strip() == "all":
            import glob
            bases = [os.path.basename(f)[:-4] for f in sorted(glob.glob(os.path.join(aa_dir, "entry_*.txt")))
                     if not os.path.basename(f).endswith("_台词.txt")]
        else:
            bases = [x.strip() for x in a.chapters.split(",") if x.strip()]

    rc = 0
    if a.build or a.rebuild:
        st = PF.build(a.work, aa_dir, out_dir, work_dir, rebuild=a.rebuild, reuse=a.reuse)
        rc = 0 if "error" not in st else 1
        if a.json:
            print(json.dumps(st, ensure_ascii=False, indent=1))
    if a.stats:
        s = PF.stats(a.work, work_dir)
        print(json.dumps(s, ensure_ascii=False, indent=1) if a.json else
              f"表 {s.get('path')}\n  counts {s.get('counts')}\n  struct by cat {s.get('struct_by_cat')}"
              f"\n  manual {s.get('manual')} | 闸一拒 {s.get('auto_fail')} | conflicts {s.get('conflicts')}")
    if a.audit:
        rc = _audit(a.work, a.top)
    if a.scan:
        res = PF.scan_families(a.work, aa_dir, work_dir, bases=bases)
        if "error" in res:
            print(res["error"])
            return 1
        print(f"  族: {res['families']}")
        print(f"  状态行组合 {len(res['state_combos'])}（未覆盖 {len(res['state_unmatched'])}）"
              f" | ◎ 不同行 {len(res['top']['◎'])} | 【】 不同行 {len(res['top']['【】'])}")
        if a.json:
            print(json.dumps({k: v for k, v in res.items() if k != "top"},
                             ensure_ascii=False, indent=1))
    if a.seed:
        fams = tuple((a.families or "◎,【】").split(","))
        st = PF.seed(a.work, top=a.top, families=fams, aa_dir=aa_dir, out_dir=out_dir,
                     work_dir=work_dir, dry=a.dry)
        if "error" in st:
            print(st["error"])
            return 1
        if a.json:
            print(json.dumps(st, ensure_ascii=False, indent=1))
    if a.fill_seed:
        st = PF.fill_seed(a.work, work_dir=work_dir, dry=a.dry)
        if "error" in st:
            print(st["error"])
            return 1
        if a.json:
            print(json.dumps(st, ensure_ascii=False, indent=1))
    if a.diff:
        res = PF.replay(a.work, bases=bases, aa_dir=aa_dir, out_dir=out_dir, work_dir=work_dir)
        if "error" in res:
            print(res["error"])
            return 1
        p = PF.write_replay_report(a.work, res, work_dir)
        print(f"[diff] 章 {res['chapters']} | 送模型行 {res['lines']:,} | 命中 {res['hit']:,} "
              f"= {res['hit_pct']:.1f}%（struct {res['hit_struct']:,} / whole {res['hit_whole']:,}"
              f" / rule {res.get('hit_rule', 0):,} / copy {res.get('hit_copy', 0):,}）"
              f"\n       与已交付逐字相同 {res['same']:,} / 不同 {res['diff']:,} = {res['same_pct']:.1f}%"
              f"\n       护栏拒 {res['reject']} | 报告 {p}")
        if a.json:
            print(json.dumps({k: v for k, v in res.items() if k != "examples"},
                             ensure_ascii=False, indent=1))
    if a.copy:
        st = PF.collect_copy(a.work, aa_dir, out_dir, work_dir, min_freq=a.min_freq)
        if "error" in st:
            print(st["error"])
            return 1
        if a.json:
            print(json.dumps(st, ensure_ascii=False))
    if a.prefill:
        st = PF.prefill(a.work, bases or [], aa_dir, out_dir, work_dir, dry=a.dry)
        if "error" in st:
            print(st["error"])
            return 1
        if a.json:
            print(json.dumps(st, ensure_ascii=False, indent=1))
        elif st.get("disabled"):
            print("[fixed] 固定表已关闭（AA_FIXED_TABLE=0）→ 未预填任何行")
    return rc


if __name__ == "__main__":
    sys.exit(main())
