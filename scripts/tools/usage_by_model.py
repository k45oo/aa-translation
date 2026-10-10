# -*- coding: utf-8 -*-
"""按模型聚合翻译行号——方便后期比对哪个模型翻的哪段、质量如何。

用法:
    python scripts/tools/usage_by_model.py [--task <id>] [--tag <子串>] [--min 200]
      --task 过滤单任务(如 t20260904-125128-555a)
      --tag  过滤 tag 子串(如 translate)
      --min  只显示累计行数>=min 的模型
依赖: usage.jsonl 记录带 lines 字段(2026-09-04 起 translate_lines 落盘)。

输出: 每个 (backend, model) 的调用数 / token / 翻译总行数 / 去重行数 / 行号范围。
"""
import json, os, sys, argparse
from collections import OrderedDict

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(BASE, "scripts", "common"))
from aa_usage import iter_usage_files

ap = argparse.ArgumentParser()
ap.add_argument("--task", default="", help="过滤单任务 ID")
ap.add_argument("--tag", default="", help="过滤 tag 子串")
ap.add_argument("--min", type=int, default=0, help="最少累计行数")
a = ap.parse_args()

# model -> 聚合
agg = OrderedDict()
for uf in iter_usage_files():
    for ln in open(uf, encoding="utf-8"):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if a.task and r.get("task") != a.task:
            continue
        if a.tag and a.tag not in r.get("tag", ""):
            continue
        lines = r.get("lines")
        if not lines:
            continue
        key = (r.get("backend", "?"), r.get("model", "?"))
        d = agg.setdefault(key, {"calls": 0, "prompt": 0, "completion": 0, "rows": set(), "total_rows": 0})
        d["calls"] += 1
        d["prompt"] += r.get("prompt", 0)
        d["completion"] += r.get("completion", 0)
        d["total_rows"] += len(lines)
        d["rows"].update(lines)

def fmt_range(rows):
    if not rows:
        return "-"
    ss = sorted(rows)
    # 压缩成 范围+碎片 紧凑格式
    parts, start = [], ss[0]
    prev = ss[0]
    for n in ss[1:]:
        if n != prev + 1:
            parts.append(f"{start}" if start == prev else f"{start}-{prev}")
            start = n
        prev = n
    parts.append(f"{start}" if start == prev else f"{start}-{prev}")
    s = ",".join(parts[:6])
    if len(parts) > 6:
        s += f"…(+{len(parts)-6}段)"
    return s

if not agg:
    print("无带 lines 的记录(usage.jsonl 里 2026-09-04 起的翻译调用才有)。")
    sys.exit(0)

print(f"{'backend':<12}{'model':<30}{'calls':>6}{'prompt':>9}{'行总':>8}{'行去重':>8}  行范围")
for (b, m), d in agg.items():
    if len(d["rows"]) < a.min:
        continue
    tot = d["prompt"] + d["completion"]
    print(f"{b:<12}{m:<30}{d['calls']:>6}{tot:>9,}{d['total_rows']:>8}{len(d['rows']):>8}  {fmt_range(d['rows'])}")
print(f"\n共 {len(agg)} 个 (backend,model) 组合")

if a.task:
    allrows = set().union(*(d["rows"] for d in agg.values()))
    print(f"任务 {a.task} 共翻译去重 {len(allrows)} 行")
