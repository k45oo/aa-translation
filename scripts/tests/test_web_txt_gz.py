# -*- coding: utf-8 -*-
"""工作台（aa_web_app）读 aa/汉化 的压缩感知回归 —— 2026-10-17 全量体检后补。

守的是「裸 open / 裸 exists 读正文」这一类的静默坏法（压缩前全对、压缩后不报错但结果空）：
  ① `logic.search_lines`（阅读器全库搜索）—— 裸 `open(path,"rb")` 读到 gzip 字节 → **永远 0 命中**
     （用户报障「搜索不可用」）。
  ② `logic.search_work`（作品页小搜索）—— 裸 `exists(规范名)` → 原文上下文恒空。
  ③ `logic.material_frequency` / `material_chapters`（素材统计）—— 裸 `open(规范名)` → 计数恒 0。
  ④ `web_state._chapter_full`（章节清单标题/原帖地址）—— 裸 open 读 .gz 抛 UnicodeDecodeError。
  ⑤ `cand_hits.build`（候选出处命中）—— 裸 open(errors="replace") 读乱码 → 命中恒 0。
  ⑥ `gen_variant_extract.extract_lines`（变体抽取）—— 裸 exists(规范名) 恒假 → 恒空。

运行：.venv/Scripts/python.exe -m unittest scripts.tests.test_web_txt_gz -v
"""
import gzip
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "works"),
           os.path.join(_ROOT, "aa_web_app")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

JP_LINE = "　こんにちは、ミクさん 今日もいい天気ですね"
ZH_LINE = "　你好，初音未来小姐 今天天气真不错"
ART_LINE = "／＼／＼／l／"
SRC = ["# 测试作品 第1话", "# 原帖地址: https://example.test/entry_1.html",
       "1 ： ◆TEST ： 2026-01-01 ID:abc",
       ART_LINE, JP_LINE, ""]
TR = ["# 测试作品 第1话", "# 原帖地址: https://example.test/entry_1.html",
      "1 ： ◆TEST ： 2026-01-01 ID:abc",
      ART_LINE, ZH_LINE, ""]


def _write_gz(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))


class WebTxtGzBase(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="aa_webgz_")
        self.aa = os.path.join(self.d, "aa")
        self.out = os.path.join(self.d, "汉化")
        self.wd = os.path.join(self.d, "work")
        for x in (self.aa, self.out, self.wd):
            os.makedirs(x, exist_ok=True)
        # 模拟全库压缩后的形态：磁盘只有 .gz
        _write_gz(os.path.join(self.aa, "entry_1.txt.gz"), SRC)
        _write_gz(os.path.join(self.out, "entry_1_汉化.txt.gz"), TR)
        # 行级 meta（line_kinds 索引来源；也走 .json.gz）
        meta = {"3": {"kind": "art", "confidence": 0.9, "reason": ["no_dialogue"]},
                "4": {"kind": "text", "confidence": 1.0, "reason": []}}
        import json
        os.makedirs(os.path.join(self.wd, "line_meta_v2"), exist_ok=True)
        with gzip.open(os.path.join(self.wd, "line_meta_v2", "entry_1.json.gz"), "wt",
                       encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        self.cfg = {"aa_dir": self.aa, "out_dir": self.out, "work_dir": self.wd,
                    "name_style": None, "work": "T"}

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)


class TestSearch(WebTxtGzBase):
    def test_阅读器全库搜索_压缩章也能命中(self):
        from aa_web_app import logic
        r = logic.search_lines(self.cfg, "T", "ミク", scope="both", limit=50)
        self.assertGreater(r["total"], 0, f"日文侧应命中（搜索不可用的根因）: {r}")
        self.assertEqual(r["scanned"], 1)
        it = r["items"][0]
        self.assertIn("ミク", it["jp"])
        self.assertEqual(it["chapter_no"], 1)

    def test_阅读器全库搜索_中文侧与单侧scope(self):
        from aa_web_app import logic
        zh = logic.search_lines(self.cfg, "T", "初音未来", scope="zh", limit=50)
        self.assertGreater(zh["total"], 0, zh)
        jp = logic.search_lines(self.cfg, "T", "初音未来", scope="jp", limit=50)
        self.assertEqual(jp["total"], 0, "只搜日文侧不该命中中文串")

    def test_作品页小搜索_带原文上下文(self):
        from aa_web_app import logic
        hits = logic.search_work(self.out, self.aa, "初音未来")
        self.assertTrue(hits, "应命中汉化行")
        self.assertIn("ミク", hits[0]["src"], f"原文上下文应被读出（裸 exists 会恒空）: {hits[0]}")


class TestMaterial(WebTxtGzBase):
    def test_素材统计_压缩后仍计数(self):
        from aa_web_app import logic
        freq = logic.material_frequency(self.out, ["初音未来"])
        self.assertTrue(freq.get("初音未来"), f"素材频次应 >0（裸 open 会恒 0）: {freq}")
        ch = logic.material_chapters(self.out, "初音未来")
        self.assertTrue(ch, f"素材命中章应非空（裸 open 会恒 0）: {ch}")


class TestChapterInfo(WebTxtGzBase):
    def test_章节清单标题与原帖地址_压缩后可读(self):
        from aa_web_app import web_state
        r = web_state._chapter_full(self.cfg, "entry_1_汉化.txt")
        self.assertIsNotNone(r, "压缩章也必须能取到章节信息")
        self.assertEqual(r.get("title"), "测试作品 第1话")
        self.assertEqual(r.get("src_url"), "https://example.test/entry_1.html")

    def test_原帖前置读取不再抛异常(self):
        from aa_web_app import logic
        head = logic._txt_head(os.path.join(self.aa, "entry_1.txt.gz"), 3)
        self.assertIn("# 测试作品 第1话", head[0])


class TestCandidateHits(WebTxtGzBase):
    def test_候选出处命中_压缩后仍能扫到(self):
        from aa_web_app import cand_hits
        hits, counts = cand_hits.build(self.aa, ["ミク"])
        self.assertTrue(counts.get("ミク"), f"候选命中数应 >0（裸 open 读乱码会恒 0）: {counts}")
        self.assertEqual(list((counts.get("ミク") or {}).keys()), ["entry_1"],
                         "章名必须是 base（txt_key），不能带 .txt/.gz")


class TestVariantExtract(WebTxtGzBase):
    def test_变体抽取_压缩后仍能取行(self):
        import registry
        from aa_web_app import gen_variant_extract as gve
        cfg = dict(self.cfg)
        cfg["aa_dir"] = self.aa
        cfg["out_dir"] = self.out
        with mock.patch.object(gve.registry, "get", lambda w: cfg):
            rows = gve.extract_lines("T", "ミク")
        self.assertTrue(rows, "变体抽取应取到含词的行（裸 exists 恒假 → 恒空）")
        self.assertEqual(rows[0][1], 5)          # 1-based 行号
        self.assertIn("初音未来", rows[0][3])     # 对应汉化行


if __name__ == "__main__":
    unittest.main()
