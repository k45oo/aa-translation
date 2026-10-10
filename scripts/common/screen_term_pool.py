#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""候选池 → 模型筛选 benchmark (2026-09-07, ④ 参数测试)。

本地 build_term_pool.py 产出 term_pool.json(零API候选词池) 后,
这套把候选词按批喂给模型: 模型对每个候选词判断「是否真专名 + 给中文译名」,
返回结构化 JSON。用于对比:
  - 候选池 topN ∈ {50, 100, 200, 500}
  - 每批喂词数 ∈ {20, 40, 80}
选出「有效候选数 / 空批率 / token成本 / 耗时」最优组合。

仿 gen_glossary_ai (复用 parse_terms / FUNC_WORDS, flat aa_api re-export)。
输出: <work>/term_pool_screened_<top>_<batch>.json
      {\"schema\", \"work\", \"top\", \"batch\", \"候选\":[{term,translation,type,confirm}...], \"统计\"}
"""
import json
import os
import re
import sys
import time
import argparse
from collections import Counter

# ---- path bootstrap (与 build_term_pool 同构) ----
_THIS = os.path.dirname(os.path.abspath(__file__))          # scripts/common
_BASE = os.path.dirname(os.path.dirname(_THIS))             # 仓库根
for _sp in ("scripts", "scripts/common", "scripts/works", "scripts/tools"):
    _p = os.path.join(_BASE, _sp)
    if _p not in sys.path:
        sys.path.insert(0, _p)
_spp = os.path.join(_BASE, ".venv", "Lib", "site-packages")
if os.path.isdir(_spp) and _spp not in sys.path:
    sys.path.insert(0, _spp)

import aa_api as A                     # flat: call_api / parse / kana_runs
import registry

FILTER_SYSTEM = """你是日文AA漫画(やる夫系字符画)汉化团队的术语筛选助手。
用户会发来一批【候选词】(来自作品台词的词频统计, 大部分是普通名词/助词, 少数是真专名)。
请逐个判断候选词是否为【值得统一译名的专有名词】(角色名/称号/地名/组织名/技能名/特殊词汇/反复出现的梗词)。

规则:
1. 只输出一个 JSON 数组, 不要 Markdown 代码块(禁 ```json), 不要额外文字。格式:
   [{"term":"やる夫","translation":"亚鲁欧","type":"角色","confirm":true},
    {"term":"発言","translation":"","type":"","confirm":false}]
2. confirm=true 仅当该词是「真专名且值得统一译名」; 普通名词(投票/発言/今日)、
   助词/代词(それ/これ)、语气词(そう/みたい)、纯数字/标点 → confirm=false translation=""。
3. 片假名长词(アーモンドアイ/ネオユニヴァース)多为专名, 但也要结合语境判断。
4. term 若不在输入中则忽略; 保持输入词原样输出。
5. translation 给简体中文译名; 不确定可留 "" 但 confirm 仍可为 true(疑专名交人工)。
6. confirm=false 的项 translation 留空。type 取值: 角色/专有名词/术语/拟声梗/''。
只对【有把握】的 confirm=true, 不要凑数。
"""
FILTER_SYSTEM = FILTER_SYSTEM.strip()


def _call(sample, batch, backend):
    if backend:
        os.environ["AA_BACKEND"] = backend
    os.environ.setdefault("AA_USAGE_TAG", "term_filter")
    raw = A.call_api(FILTER_SYSTEM, sample, max_tokens=4000)
    return _parse_filter(raw)


def _parse_filter(raw):
    """解析模型筛选输出(JSON 数组)。逐条核对 term 存在, 防拼接。"""
    t = re.sub(r"^```(?:json)?\s*", "", (raw or "").strip())
    t = re.sub(r"\s*```$", "", t).strip()
    arr = None
    try:
        arr = json.loads(t)
    except Exception:
        m = re.search(r"\[[\s\S]*\]", t)
        if m:
            try:
                arr = json.loads(m.group(0))
            except Exception:
                arr = None
    if not isinstance(arr, list):
        return []
    out = []
    for it in arr:
        if isinstance(it, dict) and it.get("term"):
            out.append({
                "term": str(it["term"]).strip(),
                "translation": str(it.get("translation") or "").strip(),
                "type": str(it.get("type") or "").strip(),
                "confirm": bool(it.get("confirm")),
            })
    return out


def screen_pool(pool, batch, backend=None, label=""):
    """分批把候选池喂模型筛选。返回 (results, stats)。"""
    terms = pool["候选"]
    total = len(terms)
    results, cost = [], []
    t0 = time.time()
    for i in range(0, total, batch):
        chunk = terms[i:i + batch]
        sample = json.dumps([c["term"] for c in chunk], ensure_ascii=False)
        got = _call(sample, batch, backend)
        # 以此批输入词为基准逐个核对 confirm (模型可能漏/多)
        chunk_terms = [c["term"] for c in chunk]
        got_map = {g["term"]: g for g in got}
        seq = []
        for ct in chunk_terms:
            g = got_map.get(ct)
            if g and g.get("confirm"):
                seq.append({"term": ct, "translation": g["translation"],
                            "type": g["type"], "confirm": True})
            # confirm=false 不记(需人工/丢弃)
        results.extend(seq)
        cost.append(_last_usage())
        print(f"  [filter] 批{(i//batch)+1}/{(total+batch-1)//batch}: 输入{len(chunk)} → 专名{len(seq)}", flush=True)
    dt = time.time() - t0
    stats = {"候选池规模": total, "批数": (total + batch - 1)//batch,
             "每批": batch, "有效专名": len(results),
             "有效占比": round(len(results)/total*100) if total else 0,
             "耗时s": round(dt, 1),
             "usage": Counter(c.get("tag") or "" for c in cost)}
    return results, stats


_last_usage = lambda: {}   # 简化: 不收集真实 usage, 只统计 token 由调用侧粗算


def load_pool(work_dir):
    p = os.path.join(work_dir, "term_pool.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description="候选池模型筛选 benchmark(定 topN/批量)")
    ap.add_argument("--work", required=True)
    ap.add_argument("--backend", help="AA_BACKEND 覆盖(默认链首 deepinfra zai-org/GLM-5.3-Flash)")
    ap.add_argument("--top", type=int, default=200, help="单次筛 topN(默认200)")
    ap.add_argument("--batch", type=int, default=40, help="每批喂词数(默认40)")
    ap.add_argument("--matrix", action="store_true", help="扫全部 top×batch 组合")
    args = ap.parse_args()

    cfg = registry.get(args.work)
    wd = cfg["work_dir"]
    pool = load_pool(wd)
    if not pool.get("候选"):
        raise SystemExit(f"无候选池: {wd}/term_pool.json (先跑 build_term_pool.py)")

    if args.matrix:
        tops = [50, 100, 200, 500]
        b_vals = [20, 40, 80]
        report = []
        for top in tops:
            pool_top = {"候选": pool["候选"][:top]}
            for b in b_vals:
                print(f"\n=== top{top} × 每批{b} ===")
                res, st = screen_pool(pool_top, b, args.backend)
                st["top"] = top
                st["batch"] = b
                report.append(st)
                outp = os.path.join(wd, f"term_pool_screened_{top}_{b}.json")
                json.dump({"schema": "候选池筛选 v1", "work": args.work,
                           "top": top, "batch": b, "统计": st,
                           "候选": res}, open(outp, "w", encoding="utf-8"),
                          ensure_ascii=False, indent=2)
        # 汇总
        with open(os.path.join(wd, "term_pool_benchmark.json"), "w", encoding="utf-8") as f:
            json.dump({"schema": "候选池benchmark v1", "work": args.work,
                       "backend": args.backend}, f, ensure_ascii=False, indent=2)
        print("\n================= BENCHMARK 汇总 =================")
        print(f"{'top':>4} {'批':>3} {'有效':>4} {'占比%':>6} {'耗时s':>6}")
        for r in report:
            print(f"{r['top']:>4} {r['batch']:>3} {r['有效专名']:>4} {r['有效占比']:>6} {r['耗时s']:>6}")
    else:
        pool_top = {"候选": pool["候选"][:args.top]}
        res, st = screen_pool(pool_top, args.batch, args.backend)
        st.update({"top": args.top, "batch": args.batch})
        outp = os.path.join(wd, f"term_pool_screened_{args.top}_{args.batch}.json")
        json.dump({"schema": "候选池筛选 v1", "work": args.work,
                   "top": args.top, "batch": args.batch, "统计": st,
                   "候选": res}, open(outp, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"[screen] {args.work}: top{args.top} 有效专名 {len(res)} -> {outp}")


if __name__ == "__main__":
    main()
