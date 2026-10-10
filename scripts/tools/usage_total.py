# -*- coding: utf-8 -*-
"""token 消耗合并统计：AA 管线（usage.jsonl）+ Hermes 对话（state.db session_model_usage）。


用法：
  python scripts/tools/usage_total.py                          # 全量（无时间窗）
  python scripts/tools/usage_total.py --since "2026-08-10 21:55" --until "2026-08-11 09:00"
  python scripts/tools/usage_total.py --usage <usage.jsonl>    # 指定 AA 用量文件

Hermes 侧按 session_model_usage 的 first_seen 落在 [since, until] 聚合；
AA 侧按 usage.jsonl 全部记录聚合（每行含 tag 区分环节）。
"""
import argparse, json, os, sqlite3, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根(2026-08-18 Phase 3 动态计算)
sys.path.insert(0, os.path.join(ROOT, "scripts", "common"))
import aa_config as _CFG

STATE_DB = r"<hermes>\profiles\translation\state.db"
DEFAULT_USAGE = os.environ.get("AA_USAGE_FILE") or _CFG.usage_file()


def hermes_usage(since=None, until=None):
    """从 state.db 聚合 Hermes 对话用量（按 first_seen 时间窗过滤）"""
    try:
        c = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True)
    except Exception:
        c = sqlite3.connect(STATE_DB)
    c.row_factory = sqlite3.Row
    q = "SELECT * FROM session_model_usage"
    if since or until:
        conds = []
        if since:
            conds.append("first_seen >= ?")
        if until:
            conds.append("first_seen <= ?")
        q += " WHERE " + " AND ".join(conds)
    rows = c.execute(q, [x for x in (since, until) if x]).fetchall()
    tot = {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "reasoning": 0}
    for r in rows:
        tot["calls"] += r["api_call_count"] or 0
        tot["input"] += r["input_tokens"] or 0
        tot["output"] += r["output_tokens"] or 0
        tot["cache_read"] += r["cache_read_tokens"] or 0
        tot["reasoning"] += r["reasoning_tokens"] or 0
    c.close()
    return tot, len(rows)


def aa_usage(path):
    """聚合 usage.jsonl（2026-08-31: 合并滚动文件 usage.jsonl.1/.2/.3）。"""
    tot = {"calls": 0, "input": 0, "output": 0}
    by_tag = {}
    # 主文件 + 滚动归档（.1 .2 .3）
    files = [path] + [f"{path}.{i}" for i in range(1, 4)]
    for p in files:
        if not os.path.exists(p):
            continue
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            inp = r.get("prompt", 0) or 0
            out = r.get("completion", 0) or 0
            tag = r.get("tag", "unknown")
            tot["calls"] += 1
            tot["input"] += inp
            tot["output"] += out
            by_tag.setdefault(tag, {"calls": 0, "input": 0, "output": 0})
            by_tag[tag]["calls"] += 1
            by_tag[tag]["input"] += inp
            by_tag[tag]["output"] += out
    return tot, by_tag


def fmt(n):
    if n >= 1e6:
        return f"{n/1e6:.2f}M"
    if n >= 1e3:
        return f"{n/1e3:.1f}K"
    return str(n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="起始时间 'YYYY-MM-DD HH:MM'（含）")
    ap.add_argument("--until", help="结束时间 'YYYY-MM-DD HH:MM'（含）")
    ap.add_argument("--usage", default=DEFAULT_USAGE, help="AA usage.jsonl 路径")
    args = ap.parse_args()

    since_ts = until_ts = None
    if args.since:
        since_ts = __import__("datetime").datetime.strptime(args.since, "%Y-%m-%d %H:%M").timestamp()
    if args.until:
        until_ts = __import__("datetime").datetime.strptime(args.until, "%Y-%m-%d %H:%M").timestamp()

    h, h_rows = hermes_usage(since_ts, until_ts)
    a, a_by_tag = aa_usage(args.usage)

    print("=" * 62)
    print("token 消耗合并统计")
    if args.since or args.until:
        print(f"  时间窗: {args.since or '(不限)'} ~ {args.until or '(不限)'}")
    print("-" * 62)
    print(f"Hermes 对话 (state.db, {h_rows} 条记录):")
    print(f"  calls={h['calls']}  input={fmt(h['input'])}  output={fmt(h['output'])}")
    print(f"  cache_read={fmt(h['cache_read'])}  reasoning={fmt(h['reasoning'])}")
    print(f"  净计费 (in+out) = {fmt(h['input']+h['output'])}")
    print("-" * 62)
    print(f"AA 管线 (usage.jsonl):")
    print(f"  calls={a['calls']}  input={fmt(a['input'])}  output={fmt(a['output'])}")
    print(f"  净计费 (in+out) = {fmt(a['input']+a['output'])}")
    if a_by_tag:
        print("  按环节:")
        for tag in sorted(a_by_tag):
            t = a_by_tag[tag]
            print(f"    {tag}: calls={t['calls']} in={fmt(t['input'])} out={fmt(t['output'])}")
    print("-" * 62)
    net = h["input"] + h["output"] + a["input"] + a["output"]
    print(f"合计净计费 (in+out) = {fmt(net)}")
    print(f"合计 calls = {h['calls'] + a['calls']}")


if __name__ == "__main__":
    main()
