# -*- coding: utf-8 -*-
"""usage.jsonl 按 tag 聚合统计（阶段2推广用）+ 成本报表（2026-09-15 P0）。

用法:
  python scripts/tools/usage_stats.py [tag 过滤子串]          # 计数/时间序列（原行为）
  python scripts/tools/usage_stats.py --cost [--days N] [--tag 子串]
                                                              # 按 tag / 端点 / 作品 / 天 折算成本

成本口径见 doc/方案归档/token成本优化方案_2026-09-15.md：
  · 单价来自 scripts/config/model_config.toml [pricing]；**缺失字段 = 未查证, 只计 token 不折算金额（不猜）**
  · 记录带 cache_hit（2026-09-15 起落盘）时按「命中价×命中 + 未命中价×其余」计；历史记录无该字段 → 金额列是**上界**（全按未命中价）
  · peak 倍率按 [pricing].windows（工作日高峰时段）套用
"""
import json, os, re, sys, time
from collections import OrderedDict

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根(2026-08-18 Phase 3 动态计算)
sys.path.insert(0, os.path.join(BASE, "scripts", "common"))
import aa_config as _CFG
from aa_usage import iter_usage_files   # 2026-08-31: 主文件 + 滚动归档 .1/.2/.3
import aa_cost                          # 2026-09-15 成本口径: 折算/高峰判定（纯计算, 可单测）

args = sys.argv[1:]
COST = "--cost" in args
DAYS = 0
if "--days" in args:
    try:
        DAYS = int(args[args.index("--days") + 1])
    except (IndexError, ValueError):
        DAYS = 0
filter_tag = None
for i, a in enumerate(args):
    if a in ("--cost", "--days"):
        continue
    if i and args[i - 1] == "--days":
        continue
    filter_tag = a
    break

cutoff = time.time() - DAYS * 86400 if DAYS else 0


def _work_of(task):
    """task id → 作品 id（读任务注册表；缺失返回 None）。"""
    if not task:
        return None
    p = os.path.join(BASE, "aa_web_app", "data", "tasks", f"{task}.json")
    try:
        with open(p, encoding="utf-8") as f:
            return (json.load(f).get("work") or None)
    except Exception:
        return None


_WORK_CACHE = {}


# ---------------- 计数模式（原行为） ----------------
tags = OrderedDict()
series = OrderedDict()
# ---------------- 成本模式聚合 ----------------
by_tag = OrderedDict()
by_ep = OrderedDict()
by_work = OrderedDict()
by_day = OrderedDict()
tot = {"cost": 0.0, "unpriced": 0, "prompt": 0, "compl": 0, "hit": 0, "hit_rows": 0, "calls": 0, "approx": 0, "reasoning": 0, "nohit_rows": 0}

for uf in iter_usage_files():
    for ln in open(uf, encoding="utf-8"):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if cutoff and (r.get("ts", 0) or 0) < cutoff:
            continue
        t = r.get("tag", "unknown")
        if filter_tag and filter_tag not in t:
            continue
        d = tags.setdefault(t, {"calls": 0, "prompt": 0, "completion": 0})
        d["calls"] += 1
        d["prompt"] += r.get("prompt", 0)
        d["completion"] += r.get("completion", 0)
        # 分钟级时间序列（推广批次）
        minute = time.strftime("%H:%M", time.localtime(r.get("ts", 0)))
        s = series.setdefault(t, {})
        s[minute] = s.get(minute, 0) + r.get("prompt", 0) + r.get("completion", 0)

        if not COST:
            continue
        cost, unpriced, approx = aa_cost.cost_of(r)
        ep = f"{r.get('backend') or '?'} | {r.get('model') or '?'}"
        task = r.get("task")
        if task is None:
            work = None
        else:
            if task not in _WORK_CACHE:
                _WORK_CACHE[task] = _work_of(task)
            work = _WORK_CACHE[task]
        day = time.strftime("%m-%d", time.localtime(r.get("ts", 0)))
        for agg, key in ((by_tag, t), (by_ep, ep), (by_work, work or "(无任务)"), (by_day, day)):
            e = agg.setdefault(key, {"calls": 0, "cost": 0.0, "unpriced": 0, "prompt": 0, "compl": 0, "hit": 0, "hit_rows": 0, "nohit_rows": 0, "approx": 0, "reasoning": 0})
            e["calls"] += 1
            e["cost"] += cost
            e["unpriced"] += unpriced
            e["prompt"] += r.get("prompt", 0) or 0
            e["compl"] += r.get("completion", 0) or 0
            e["reasoning"] += r.get("reasoning", 0) or 0
            if r.get("cache_hit") is None:
                e["nohit_rows"] += 1
            else:
                e["hit_rows"] += 1
                e["hit"] += r.get("cache_hit", 0) or 0
            if approx:
                e["approx"] += 1
        tot["calls"] += 1
        tot["cost"] += cost
        tot["unpriced"] += unpriced
        tot["prompt"] += r.get("prompt", 0) or 0
        tot["compl"] += r.get("completion", 0) or 0
        tot["reasoning"] += r.get("reasoning", 0) or 0
        if r.get("cache_hit") is None:
            tot["nohit_rows"] += 1
        else:
            tot["hit_rows"] += 1
            tot["hit"] += r.get("cache_hit", 0) or 0
        if approx:
            tot["approx"] += 1

print(f"{'tag':<28}{'calls':>7}{'prompt':>12}{'completion':>12}{'total':>12}")
gt = 0
for t, d in tags.items():
    tt = d["prompt"] + d["completion"]
    gt += tt
    print(f"{t:<28}{d['calls']:>7}{d['prompt']:>12,}{d['completion']:>12,}{tt:>12,}")
print(f"{'TOTAL':<28}{'':>7}{'':>12}{'':>12}{gt:>12,}")


def _row(name, e):
    tot_tok = e["prompt"] + e["compl"] + e["unpriced"]
    hit_rate = (e["hit"] / e["prompt"] * 100) if (e["hit_rows"] and e["prompt"]) else None
    hit_s = f"{hit_rate:.1f}%" if hit_rate is not None else "—"
    unp_s = f"{e['unpriced'] / tot_tok * 100:.0f}%" if tot_tok else "—"
    rsn_s = f"{e['reasoning']:,}" if e["reasoning"] else "—"
    noh_s = str(e["nohit_rows"]) if e["nohit_rows"] else ""
    print(f"{str(name)[:40]:<42}{e['calls']:>6}{e['prompt']:>12,}{hit_s:>8}"
          f"{e['compl']:>11,}{e['cost']:>10.2f}{unp_s:>7}{rsn_s:>9}{noh_s:>8}")


if COST:
    print("\n== 成本报表（元；单价 scripts/config/model_config.toml [pricing]）==")
    hdr = f"{'':<42}{'calls':>6}{'prompt':>12}{'命中率':>8}{'输出':>11}{'¥':>10}{'未定价':>7}{'思考tok':>9}{'无命中字段':>8}"
    for title, agg in (("按 tag", by_tag), ("按端点 backend | model", by_ep), ("按作品", by_work), ("按天", by_day)):
        print(f"\n--- {title} ---")
        print(hdr)
        for k, e in sorted(agg.items(), key=lambda kv: -kv[1]["cost"]):
            _row(str(k), e)
    print("\n--- 合计 ---")
    tot_tok = tot["prompt"] + tot["compl"] + tot["unpriced"]
    print(f"记录 {tot['calls']:,} 条 | 折算 ¥{tot['cost']:.2f} | 未定价 token {tot['unpriced']:,}"
          f"（占 {tot['unpriced'] / tot_tok * 100:.0f}%） | 命中字段 {tot['hit_rows']:,} 条命中 {tot['hit']:,} tok"
          f" | 思考 token {tot['reasoning']:,} | 历史无 cache_hit {tot['nohit_rows']:,} 条（金额列=上界）"
          f" | 含近似/未查证价 {tot['approx']:,} 条")
    if DAYS:
        print(f"（仅统计最近 {DAYS} 天）")

if filter_tag:
    print(f"\n{filter_tag} 分钟序列:")
    for t, s in series.items():
        for m, v in sorted(s.items()):
            print(f"  {m}: {v:,}")
