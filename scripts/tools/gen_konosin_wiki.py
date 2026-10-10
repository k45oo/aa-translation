# -*- coding: utf-8 -*-
"""兼容壳（2026-10-06）：实现已泛化为 `scripts/tools/gen_work_wiki.py`（每部作品独立配置）。

保留本文件名只为兼容既有文档/习惯；`--crawl/--extract/--translate/--fix-kana/--build/--all`
以及新增的 `--update` 全部照旧，作品固定为「この素晴らしいシンオウに祝福を」。

其它 / 新增作品请直接用通用入口：

  python scripts/tools/gen_work_wiki.py --work <id> [--all|--update|...]
  python scripts/tools/gen_work_wiki.py --list          # 列出已登记作品

配置见 `scripts/config/work_wikis.json`（站点/输出目录/分类表/借名表/prompt 作品名）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gen_work_wiki import main  # noqa: E402

if __name__ == '__main__':
    main(default_work='この素晴らしいシンオウに祝福を')
