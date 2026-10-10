# -*- coding: utf-8 -*-
"""白头与染灰魔女 更新检测 —— 供周日 cron 定时查询（r401 分类 12876）。

用法：python common/check_update.py [--category 12876]

行为：
- 无更新 → stdout 为空（cron 静默，不打扰）
- 有更新 → stdout 每行「eid|标题|URL」，并写 <work_dir>/chapters_new.json
  （r401 章节 JSON，可直接喂 dl_chapters.py --json）

本地已处理 eid = aa/ ∪ raw/ ∪ 汉化/ 的文件名前缀（防中间态重复触发：
下载了没提取 / 提取了没翻译 都不会再次触发该 eid）。
"""
import json
import os
import re
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(_SCRIPT_DIR), "works"))

import r401_index as R

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根(2026-08-18 Phase 3 动态计算)
WORK = os.path.join(BASE, "白头与染灰魔女", "work")
WORK_DIRS = ("aa", "raw", "汉化")
DEFAULT_CATEGORY = "12876"          # r401: 白頭と灰かぶりの魔女


def local_eids():
    """已处理 eid 集合：aa/raw/汉化 三目录文件名的数字前缀"""
    eids = set()
    for d in WORK_DIRS:
        p = os.path.join(BASE, "白头与染灰魔女", d)
        if not os.path.isdir(p):
            continue
        for f in os.listdir(p):
            m = re.match(r'(\d+)', f)
            if m:
                eids.add(m.group(1))
    return eids


def main():
    category = DEFAULT_CATEGORY
    if "--category" in sys.argv:
        i = sys.argv.index("--category")
        category = sys.argv[i + 1] if i + 1 < len(sys.argv) else category
    html = R.fetch(f"{R.BASE}/yaruo/categories/{category}")
    data = R.parse_category(html)
    remote = {e["eid"]: e for e in data["entries"]}
    new = {k: v for k, v in remote.items() if k not in local_eids()}
    if not new:
        return  # 静默：无更新
    os.makedirs(WORK, exist_ok=True)
    out = os.path.join(WORK, "chapters_new.json")
    entries = [new[k] for k in sorted(new)]
    json.dump({"category_id": category, "title": data["title"], "entries": entries},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for e in entries:
        print(f"{e['eid']}|{e['title']}|{e['url']}")
    print(f"新章节共 {len(entries)} 话，清单: {out}")


if __name__ == "__main__":
    main()
