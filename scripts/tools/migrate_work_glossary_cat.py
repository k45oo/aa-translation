#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""作品术语表 cat 迁移工具 (2026-09-06, 分类重构 ①)

作品术语表 glossary_<work>.json 的 cat 从旧自由分类(常用-梗/特殊-麻将/特殊-角色/
手动/空)迁移到新三类:
    角色 / 专名 / 正则

映射规则(保守):
    cat 含「角色」  -> 角色      (特殊-角色 → 角色)
    cat 含「正则」  -> 正则      (规则-正则 → 正则)
    其余(含空/手动/常用-梗/特殊-麻将/特殊-作品名...) -> 专名

v1 旧二元组条目([日文, 中文]) 无 cat 字段, 不动(读取层兼容为 专名/manual)。
只迁移 dict 条目; 幂等(已是三类值不重复迁移)。--dry 只预览不写。

用法:
    python scripts/tools/migrate_work_glossary_cat.py [--work <id>] [--dry] [--all]
"""
import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根
sys.path.insert(0, os.path.join(BASE, "scripts"))
sys.path.insert(0, os.path.join(BASE, "scripts", "works"))

CAT_ROLE, CAT_NAME, CAT_REGEX = "角色", "专名", "正则"
ALLOWED = {CAT_ROLE, CAT_NAME, CAT_REGEX}


def migrate_file(path, dry=False):
    """迁移单个作品表文件。返回 (changed, total, 明细列表)。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    changed = 0
    detail = []
    for p in data.get("glossary", []):
        if not isinstance(p, dict):
            continue  # v1 二元组不动
        old = str(p.get("cat", ""))
        if old in ALLOWED:
            continue  # 已是新分类
        if "角色" in old:
            new = CAT_ROLE
        elif "正则" in old or old.startswith("re:"):
            new = CAT_REGEX
        else:
            new = CAT_NAME
        p["cat"] = new
        changed += 1
        detail.append((old, new, p.get("jp", "")))
    if changed and not dry:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    return changed, detail


def find_tables(work=None):
    """找全部作品术语表(排除 iter/candidates/副本)。返回 [(abs_path, work_id)]。"""
    out = []
    aa_root = os.path.join(BASE, "aaManga")
    for w in os.listdir(aa_root):
        wd = os.path.join(aa_root, w, "work")
        if not os.path.isdir(wd):
            continue
        for fn in os.listdir(wd):
            if not (fn.startswith("glossary_") and fn.endswith(".json")):
                continue
            if "iter" in fn or "candidates" in fn or "副本" in fn:
                continue
            wid = fn[len("glossary_"):-len(".json")]
            if work and wid != work:
                continue
            out.append((os.path.join(wd, fn), wid))
    return out


def main():
    ap = argparse.ArgumentParser(description="作品术语表 cat 迁移到三类(角色/专名/正则)")
    ap.add_argument("--work", default=None, help="只迁移指定作品(registry id/表名片段)")
    ap.add_argument("--all", action="store_true", help="迁移全部作品表")
    ap.add_argument("--dry", action="store_true", help="只预览不写盘")
    args = ap.parse_args()
    if not (args.work or args.all):
        ap.error("需 --work <id> 或 --all")
    tables = find_tables(args.work)
    total_changed = 0
    for path, wid in tables:
        changed, detail = migrate_file(path, dry=args.dry)
        total_changed += changed
        if changed or args.dry:
            print(f"[migrate] {wid}: {changed} 条" + (" (dry)" if args.dry else " (已写)"))
            for old, new, jp in detail:
                print(f"    {old} -> {new}  | {jp}")
    print(f"[migrate] 共 {len(tables)} 表, 迁移 {total_changed} 条" + (" (dry-run, 未写盘)" if args.dry else ""))


if __name__ == "__main__":
    main()
