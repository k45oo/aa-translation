# -*- coding: utf-8 -*-
"""翻译替换历史记录测试（2026-09-13）：logic.tr_hist_*。

锁住阅读器翻译替换弹窗「历史替换记录」的落盘契约：新在前、同对去重计数、
上限截断、损坏文件当空、原子写不留 .tmp。
运行：python -m unittest scripts.tests.test_replace_history
（只测纯逻辑层 logic.py，不 import main——避免拉起 app 与预热线程）
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts", "common"))

from aa_web_app import logic  # noqa: E402


class TrHistFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tr_hist_test_")
        self.work_dir = os.path.join(self.tmp, "work")
        os.makedirs(self.work_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestTrHist(TrHistFixture):
    def test_add_load_roundtrip_newest_first(self):
        logic.tr_hist_add(self.work_dir, [["あかり", "灯"]], applied=3, files=1, ts="2026-09-13 10:00")
        logic.tr_hist_add(self.work_dir, [["ゆかり", "缘"]], applied=1, files=1, ts="2026-09-13 11:00")
        rows = logic.tr_hist_load(self.work_dir)
        self.assertEqual([r["find"] for r in rows], ["ゆかり", "あかり"])   # 新在前
        self.assertEqual(rows[1]["repl"], "灯")
        self.assertEqual(rows[1]["applied"], 3)
        self.assertEqual(rows[1]["n"], 1)
        self.assertEqual(rows[1]["ts"], "2026-09-13 10:00")
        self.assertTrue(os.path.exists(logic.tr_hist_path(self.work_dir)))
        self.assertFalse(os.path.exists(logic.tr_hist_path(self.work_dir) + ".tmp"))   # 原子写不留 .tmp

    def test_same_pair_dedupes_and_counts(self):
        logic.tr_hist_add(self.work_dir, [["先生", "老师"]], applied=2, files=1, ts="A")
        logic.tr_hist_add(self.work_dir, [["别的", "x"]], applied=1, files=1, ts="B")
        logic.tr_hist_add(self.work_dir, [["先生", "老师"]], applied=5, files=2, ts="C")
        rows = logic.tr_hist_load(self.work_dir)
        self.assertEqual(len(rows), 2)                       # 同对不新增条目
        self.assertEqual(rows[0]["find"], "先生")             # 再应用 → 置顶
        self.assertEqual(rows[0]["n"], 2)
        self.assertEqual(rows[0]["applied"], 5)
        self.assertEqual(rows[0]["files"], 2)
        self.assertEqual(rows[0]["ts"], "C")

    def test_same_find_different_repl_is_separate(self):
        logic.tr_hist_add(self.work_dir, [["さん", "桑"]])
        logic.tr_hist_add(self.work_dir, [["さん", "先生"]])
        self.assertEqual(len(logic.tr_hist_load(self.work_dir)), 2)

    def test_empty_find_skipped_and_repl_coerced(self):
        logic.tr_hist_add(self.work_dir, [["", "x"], ["好好", None], [None, "y"]])
        rows = logic.tr_hist_load(self.work_dir)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["find"], "好好")
        self.assertEqual(rows[0]["repl"], "")               # None → ""

    def test_cap_keeps_newest(self):
        for i in range(logic.TR_HIST_MAX + 7):
            logic.tr_hist_add(self.work_dir, [[f"词{i}", "译"]], ts=str(i))
        rows = logic.tr_hist_load(self.work_dir)
        self.assertEqual(len(rows), logic.TR_HIST_MAX)
        self.assertEqual(rows[0]["find"], f"词{logic.TR_HIST_MAX + 6}")            # 最新在最前
        self.assertEqual(rows[-1]["find"], f"词{7}")                               # 最旧的 7 条被淘汰

    def test_missing_and_corrupt_file_return_empty(self):
        self.assertEqual(logic.tr_hist_load(self.work_dir), [])
        p = logic.tr_hist_path(self.work_dir)
        with open(p, "w", encoding="utf-8") as f:
            f.write("{ 不是 JSON")
        self.assertEqual(logic.tr_hist_load(self.work_dir), [])
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"nope": 1}, f)                     # 顶层不是 list
        self.assertEqual(logic.tr_hist_load(self.work_dir), [])
        with open(p, "w", encoding="utf-8") as f:
            json.dump([{"repl": "x"}, "字符串", {"find": "好", "n": "2"}], f)      # 逐条过滤 + 数值归一
        rows = logic.tr_hist_load(self.work_dir)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["n"], 2)


if __name__ == "__main__":
    unittest.main()
