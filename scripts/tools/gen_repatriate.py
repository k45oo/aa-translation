#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""作品术语表 → 全局术语表 反哺 CLI（2026-09-20）。

方案: doc/方案归档/术语表反哺与候选池差集修复方案_2026-09-20.md
核心逻辑全在 scripts/common/glossary_repatriate.py（与工作台共用，本文件只做 CLI 包装）。

用法:
  # 列出（默认；零 API、零副作用）
  python scripts/tools/gen_repatriate.py --work bar_miku
  python scripts/tools/gen_repatriate.py --work bar_miku --json          # 机器可读
  python scripts/tools/gen_repatriate.py --work bar_miku --no-embed      # 跳过嵌入率（快）

  # 落盘（先自动备份 roles.json/glossary.json 到 .glossary_backup/<ts>/）
  python scripts/tools/gen_repatriate.py --work bar_miku --apply --select auto   # 建议勾选（跨作品≥2 非短词）
  python scripts/tools/gen_repatriate.py --work bar_miku --apply --select cross  # 含短词（短专名自动包 re: 边界）
  python scripts/tools/gen_repatriate.py --work bar_miku --apply --select all
  python scripts/tools/gen_repatriate.py --work bar_miku --apply --terms ミク,アカネ

写入规则见模块 docstring：角色→roles.json+known_names；专名→glossary.json(`特殊-专名`)+known_names；
短词(≤3 字)专名自动包 `re:` 词边界（re: 对不注入 prompt，只本地替换）；
`re:` 开头 / 缺译名 / zh==jp / 语法功能词 一律跳过。
"""
import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
for _sp in (_SCRIPTS, os.path.join(_SCRIPTS, "common"),
            os.path.join(_SCRIPTS, "works"), _HERE):
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

import glossary_repatriate as GR          # noqa: E402
import registry                           # noqa: E402


def _select(items, mode, terms):
    cands = [it for it in items if not it["跳过"]]
    if terms:
        want = {t.strip() for t in terms if t.strip()}
        return [it for it in cands if it["jp"] in want]
    if mode == "auto":
        return [it for it in cands if it["选中"]]
    if mode == "cross":
        return [it for it in cands if it["跨作品"] >= 2]
    return cands


def main():
    ap = argparse.ArgumentParser(description="作品术语表 → 全局术语表 反哺（零 API）")
    ap.add_argument("--work", required=True, help="registry 作品 id")
    ap.add_argument("--apply", action="store_true", help="写入全局（缺省只列出，不落盘）")
    ap.add_argument("--select", choices=["auto", "cross", "all"], default="auto",
                    help="auto=建议勾选(跨作品≥2 且非短词) / cross=全部跨作品≥2(含短词) / all=所有可反哺项")
    ap.add_argument("--terms", help="显式指定 jp（逗号分隔），覆盖 --select")
    ap.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")
    ap.add_argument("--no-embed", action="store_true", help="跳过嵌入率统计（快，列显示 -）")
    ap.add_argument("--no-backup", action="store_true", help="不备份（默认备份到 .glossary_backup/<ts>/）")
    args = ap.parse_args()

    if args.work not in registry.WORKS:
        raise SystemExit(f"未知作品: {args.work!r}")
    res = GR.scan(args.work, with_embed=not args.no_embed)
    items = res["items"]

    if args.json and not args.apply:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return

    s = res["summary"]
    print(f"[repatriate] {args.work}: 可反哺 {s['可反哺']} / 跨作品 {s['跨作品']} / "
          f"短词 {s['短词']} / 建议勾选 {s['建议勾选']} / 跳过 {s['跳过']}")
    if not args.json:
        print(f"{'':2} {'日文':<14}{'本作品译名':<12}{'类型':<6}{'跨作品':<6}{'嵌入率':<8}{'跳过原因'}")
        for it in items:
            er = "-" if it["嵌入率"] is None else f"{it['嵌入率']:.2f}"
            mark = "*" if it["选中"] else " "
            print(f"{mark:2} {it['jp']:<14}{it['zh']:<12}{it['建议']:<6}"
                  f"{it['跨作品']:<6}{er:<8}{it['跳过']}")

    if not args.apply:
        print("（只列出。要写入加 --apply --select auto|cross|all 或 --terms a,b）")
        return

    chosen = _select(items, args.select, (args.terms or "").split(","))
    if not chosen:
        print("[repatriate] 无可写入项")
        return
    out = GR.apply(chosen, args.work, backup=not args.no_backup)
    print(f"[repatriate] {out['msg']}；备份={out['backup']}")
    for it in chosen:
        print(f"  + {it['jp']} → {it['zh']}  [{it['建议']}]"
              + (f"  （写入为 {it['jp_out']}）" if it["jp_out"] != it["jp"] else ""))
    for sk in out.get("skipped", []):
        print(f"  - 跳过 {sk['jp']}: {sk['why']}")


if __name__ == "__main__":
    main()
