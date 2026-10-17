# -*- coding: utf-8 -*-
"""M5 指标采集汇总 (metrics_report.py)
聚合 AA_USAGE_FILE 落盘的 JSONL（兼容旧格式 tag/prompt/completion/total/ms 与新格式 ts），
输出 M5/统计所需的翻译指标：calls/prompt/completion/total/每文本行token/各环节占比。
另可汇总分类指标（若提供了 line_meta 目录，统计 kind 分布）。

用法:
  python scripts/metrics_report.py --usage キッコーマン/work/usage.jsonl [--meta キッコーマン/work/line_meta_v2] [--json out.json]
"""
import os, sys, json, argparse, collections, glob, gzip

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根(2026-08-18 Phase 3 修正: verify/ 需 3 级)


def parse_usage(path):
    """返回 {tag: {calls, prompt, completion, total, ms_sum}}。兼容 ts/ms 字段。"""
    agg = collections.defaultdict(lambda: {'calls': 0, 'prompt': 0, 'completion': 0, 'total': 0, 'ms': 0.0})
    n_skipped = 0
    for line in open(path, encoding='utf-8'):
        try:
            r = json.loads(line)
        except Exception:
            n_skipped += 1
            continue
        tag = r.get('tag', 'translate')
        a = agg[tag]
        a['calls'] += 1
        a['prompt'] += r.get('prompt', 0) or 0
        a['completion'] += r.get('completion', 0) or 0
        a['total'] += r.get('total', 0) or (r.get('prompt', 0) + r.get('completion', 0))
        a['ms'] += r.get('ms', 0) or 0
    return agg, n_skipped


def meta_kind_dist(meta_dir):
    """统计 meta 目录所有 json 的 kind 分布（分类指标）。"""
    dist = collections.Counter()
    n_files = 0
    n_rows = 0
    for p in sorted(glob.glob(os.path.join(meta_dir, '*.json'))
                    + glob.glob(os.path.join(meta_dir, '*.json.gz'))):
        n_files += 1
        try:
            if p.endswith('.gz'):
                with gzip.open(p, 'rt', encoding='utf-8') as f:
                    raw = json.load(f)
            else:
                with open(p, encoding='utf-8') as f:
                    raw = json.load(f)
        except Exception:
            continue
        for v in raw.values():
            n_rows += 1
            kind = v['kind'] if isinstance(v, dict) else v
            dist[kind] += 1
    return dist, n_files, n_rows


def main():
    sys.path.insert(0, os.path.join(ROOT, "scripts", "common"))
    import aa_config as _CFG
    ap = argparse.ArgumentParser()
    ap.add_argument('--usage', default=os.environ.get("AA_USAGE_FILE") or _CFG.usage_file(),
                    help='usage.jsonl 路径(默认: model_config.toml [usage].file)')
    ap.add_argument('--meta', default=None, help='line_meta 目录(可选,统计kind分布)')
    ap.add_argument('--json', default=None, help='输出 JSON 汇总(可选)')
    args = ap.parse_args()

    agg, skipped = parse_usage(args.usage)
    print(f'=== 翻译指标汇总 (usage: {args.usage}) ===')
    print(f'解析跳过行: {skipped}')
    tot_calls = tot_p = tot_c = tot_total = 0
    rows = []
    for tag, a in sorted(agg.items(), key=lambda kv: -kv[1]['total']):
        print(f"{tag:10s} calls={a['calls']:5d} prompt={a['prompt']:>9d} "
              f"completion={a['completion']:>9d} total={a['total']:>9d} "
              f"ms={a['ms']:.0f}")
        tot_calls += a['calls']; tot_p += a['prompt']; tot_c += a['completion']; tot_total += a['total']
        rows.append({'tag': tag, **a})
    print(f"\n合计 calls={tot_calls} prompt={tot_p} completion={tot_c} total={tot_total}")
    if tot_calls:
        print(f"每次调用平均: prompt={tot_p/tot_calls:.0f} completion={tot_c/tot_calls:.0f} total={tot_total/tot_calls:.0f}")

    meta_info = None
    if args.meta and os.path.isdir(args.meta):
        dist, n_files, n_rows = meta_kind_dist(args.meta)
        print(f"\n=== 分类指标 (meta: {args.meta}) === 文件={n_files} 行={n_rows}")
        for kind, n in dist.most_common():
            pct = 100.0 * n / max(n_rows, 1)
            print(f"  {kind:8s} {n:5d} ({pct:.1f}%)")
        meta_info = {'files': n_files, 'rows': n_rows, 'dist': dict(dist)}

    if args.json:
        out = {
            'usage_file': args.usage,
            'skipped': skipped,
            'per_tag': rows,
            'totals': {'calls': tot_calls, 'prompt': tot_p, 'completion': tot_c, 'total': tot_total},
            'per_call': {'prompt': tot_p / max(tot_calls, 1), 'completion': tot_c / max(tot_calls, 1)},
            'meta': meta_info,
        }
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"\nJSON: {args.json}")


if __name__ == '__main__':
    main()
