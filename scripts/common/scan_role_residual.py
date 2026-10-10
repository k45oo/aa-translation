# -*- coding: utf-8 -*-
"""全局角色名残留扫描（2026-08-28，用户要求加入管线）

扫描已汉化文本里残留的"全局角色日文名"——这些本该翻译成中文译名
（如 やる夫→亚鲁欧、キャル→凯露），漏译/未统一会在译文里残留日文。

与 iter_glossary（候选提取）不同：这里是**校验**，只报告残留不写候选。
与 proofread（模型复审）不同：这是**零 token 正则**，直接命中即报。

用法：python scripts/common/scan_role_residual.py --work <id>
输出：<work_dir>/role_residual.txt（残留清单），退出码 0=无残留 1=有残留
"""
import json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "common"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "works"))

def load_global_roles():
    """读全局 roles.json 角色日文名 → {日文名: 中文译名}（按长度降序，长名优先匹配）

    2026-09-01 三级重构: 角色块迁出 glossary.json → roles.json。
    """
    p = os.path.join(ROOT, "roles.json")
    g = json.load(open(p, encoding="utf-8"))
    roles = {}
    for c in g.get("角色", []):
        jp = (c.get("日文") or "").strip()
        cn = (c.get("中文") or "").strip()
        if jp and cn and jp != cn and len(jp) >= 2:  # 日文=中文的跳过; 单字名(衣/玄/憧)误报率高跳过
            roles[jp] = cn
    # 按长度降序（长名优先，避免 やる夫 先匹配 やる夫婦 之类子串）
    return dict(sorted(roles.items(), key=lambda kv: -len(kv[0])))

def scan_work(work, roles):
    """扫描作品的汉化目录，返回残留清单 [(fname, line_no, 日文名, 中文译名, 行内容)...]"""
    sys.path.insert(0, os.path.join(ROOT, "scripts", "works"))
    import registry
    cfg = registry.get(work)
    out_dir = cfg["out_dir"]
    work_dir = cfg.get("work_dir") or os.path.join(ROOT, "aaManga", cfg.get("title", work))
    hits = []
    for fname in sorted(os.listdir(out_dir)):
        if not fname.endswith("_汉化.txt"):
            continue
        lines = open(os.path.join(out_dir, fname), encoding="utf-8").read().split("\n")
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            if line.strip().startswith("#"):  # 标题行保留日文是约定(AGENTS.md), 跳过
                continue
            for jp, cn in roles.items():
                if jp in line:
                    hits.append((fname, i, jp, cn, line))
    return hits, work_dir

def main():
    args = sys.argv[1:]
    work = None
    if "--work" in args:
        work = args[args.index("--work") + 1]
    if not work:
        print("用法: python scripts/verify/scan_role_residual.py --work <id>")
        return 2
    roles = load_global_roles()
    hits, work_dir = scan_work(work, roles)
    # 写报告
    rep = os.path.join(work_dir, "role_residual.txt")
    with open(rep, "w", encoding="utf-8") as f:
        f.write(f"# 全局角色名残留扫描 ({work})\n")
        f.write(f"# 时间: 2026-08-28\n")
        f.write(f"# 规则: 汉化文本残留全局角色日文名(未译成中文译名)\n")
        f.write(f"# 命中 {len(hits)} 处\n\n")
        for fname, ln, jp, cn, line in hits:
            f.write(f"### {fname} line {ln}\n")
            f.write(f"残留: {jp} (应译: {cn})\n")
            f.write(f"TR: {line.strip()[:80]}\n\n")
    print(f"角色名残留: {len(hits)} 处 → {rep}")
    if hits:
        # 按日文名聚合显示
        from collections import Counter
        c = Counter(h[2] for h in hits)
        for jp, n in c.most_common(10):
            print(f"  {jp} ({roles[jp]}): {n} 处")
    return 1 if hits else 0

if __name__ == "__main__":
    sys.exit(main())
