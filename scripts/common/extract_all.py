# -*- coding: utf-8 -*-
"""批量提取：raw/*.html -> aa/*.txt + colormap（+ 评论等 extras）。

用法：python common/extract_all.py --work <id> [--file entry_1234.html] [--eids 1234,4567] [--limit N]
解析器按作品注册表的 site 字段加载 sites/<site>.py。
输出命名：eid_title 作品沿用 raw 文件名（<eid>_<章名>.txt），entry_eid 作品统一 entry_<eid>.txt。
"""
import importlib, os, re, sys

# 2026-08-30: Windows stdout 默认 GBK，print 日文文件名/路径崩——强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
sys.path.insert(0, os.path.dirname(_SCRIPT_DIR))
sys.path.insert(0, os.path.join(os.path.dirname(_SCRIPT_DIR), "works"))

import registry

EID_FROM_FNAME = re.compile(r'(?:entry_)?(\d+)')


def load_site(name):
    return importlib.import_module("sites." + name)


def main():
    args = sys.argv[1:]
    work = None
    only, limit = None, None
    eids = None
    if "--work" in args:
        work = args[args.index("--work") + 1]
    for a in args:
        if a.startswith("--file"):
            only = a.split("=", 1)[1] if "=" in a else args[args.index(a) + 1]
        if a.startswith("--limit"):
            limit = int(a.split("=", 1)[1] if "=" in a else args[args.index(a) + 1])
        if a.startswith("--eids"):
            val = a.split("=", 1)[1] if "=" in a else args[args.index(a) + 1]
            eids = {x.strip() for x in val.split(",") if x.strip()}
    cfg = registry.get(work)
    site = load_site(cfg["site"])
    raw_dir, out_dir = cfg["raw_dir"], cfg["aa_dir"]
    os.makedirs(out_dir, exist_ok=True)
    files = sorted(f for f in os.listdir(raw_dir) if f.endswith(".html"))
    if only:
        files = [f for f in files if f == only]
    if eids:
        files = [f for f in files if any(e in f for e in eids)]
    if limit:
        files = files[:limit]
    for fname in files:
        raw = open(os.path.join(raw_dir, fname), encoding="utf-8", errors="replace").read()
        m = EID_FROM_FNAME.search(fname)
        eid = m.group(1) if m else ''
        lines, colormap, extras = site.extract(raw, eid, fname)
        if not lines:
            print(f"{fname}: skipped (非章节/空结果)", flush=True)
            continue
        if cfg["name_style"] == "entry_eid":
            base = f"entry_{eid}"
        else:
            base = os.path.basename(fname).replace(".html", "")
        with open(os.path.join(out_dir, base + ".txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        if colormap:
            with open(os.path.join(out_dir, base + "_colormap.json"), "w", encoding="utf-8") as f:
                import json
                json.dump(colormap, f, ensure_ascii=False)
        for extra_name, obj in extras.items():
            with open(os.path.join(out_dir, extra_name), "w", encoding="utf-8") as f:
                import json
                json.dump(obj, f, ensure_ascii=False, indent=1)
        aa = sum(1 for l in lines[2:] if l.strip())
        print(f"{base}: {len(lines)} lines, {aa} aa, {len(colormap)} colors, {len(extras)} extras", flush=True)
    print("ALL DONE")


if __name__ == "__main__":
    main()
