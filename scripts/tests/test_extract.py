# -*- coding: utf-8 -*-
"""L1 解析器回归测试：5 站点 extract() 对真实 raw 字节级对比现有 aa 语料（黄金样本）。

数据守卫：作品数据缺失（如换机器未同步）时 skip 而非 fail。
r401_index 用仓库内离线 fixture（aa_hub 素材库 2026-10-10 已删，用例同步移除）。
"""
import json
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS_DIR)
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.join(SCRIPTS, "common"))
sys.path.insert(0, os.path.join(SCRIPTS, "sites"))

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根（换机器/换盘自动跟随）

# (站点模块, raw 相对路径, 期望 txt 相对路径, 有无 colormap)
SAMPLES = [
    ("bookshelf", "さーきー/raw/12837_第2話.html", "さーきー/aa/12837_第2話.txt", True),
    ("fc2", "さーきー/raw_fc2/entry_8532.html", "さーきー/aa_fc2/entry_8532.txt", True),
    ("blog_2nt", "不幸の催眠/raw/entry_13202.html", "不幸の催眠/aa/entry_13202.txt", True),
    ("yaruobook", "魔法戦姫サマーカラーとスケベ体育教師やる夫/raw/entry_84463.html",
     "魔法戦姫サマーカラーとスケベ体育教師やる夫/aa/entry_84463.txt", False),
    ("yaruonichijou", "白头与染灰魔女/raw/61940_白頭と極点の魔女_第1話.html",
     "白头与染灰魔女/aa/61940_白頭と極点の魔女_第1話.txt", False),
]


class TestExtractRegression(unittest.TestCase):
    """extract() 输出必须与历史语料逐字节一致——改解析器前先更新黄金样本"""

    def test_all_sites_byte_identical(self):
        import importlib
        for mod, raw_rel, exp_rel, has_cm in SAMPLES:
            raw_path = os.path.join(BASE, raw_rel)
            exp_path = os.path.join(BASE, exp_rel)
            if not (os.path.exists(raw_path) and os.path.exists(exp_path)):
                self.skipTest(f"语料缺失: {raw_rel}（只读黄金样本未同步）")
            site = importlib.import_module(mod)
            eid = os.path.basename(raw_path).split("_")[0] if mod == "bookshelf" else None
            with open(raw_path, encoding="utf-8", errors="replace") as f:
                raw = f.read()
            lines, colormap, extras = site.extract(raw, eid, os.path.basename(raw_path))
            with open(exp_path, encoding="utf-8") as f:
                expected = f.read()
            self.assertEqual("\n".join(lines), expected,
                             f"{mod} 提取结果与黄金样本不一致（解析器改动? 或语料被改）")
            if has_cm:
                with open(exp_path.replace(".txt", "_colormap.json"), encoding="utf-8") as f:
                    exp_cm = json.load(f)
                self.assertEqual({str(k): v for k, v in colormap.items()}, exp_cm,
                                 f"{mod} colormap 不一致")


class TestR401Index(unittest.TestCase):
    """离线 fixture：仓库根 _cat1.html（魔法戦姫 分类页）"""

    def setUp(self):
        self.page = os.path.join(BASE, "_cat1.html")
        if not os.path.exists(self.page):
            self.skipTest("缺少离线 fixture _cat1.html")

    def test_parse_category(self):
        import r401_index as R
        data = R.parse_category(open(self.page, encoding="utf-8", errors="replace").read())
        self.assertTrue(data["title"].startswith("魔法戦姫"))
        self.assertGreater(len(data["entries"]), 30)
        e = data["entries"][0]
        for key in ("eid", "title", "url", "date"):
            self.assertIn(key, e)
        self.assertIn("?p=", e["url"])       # yaruobook 站是 ?p= 形式
