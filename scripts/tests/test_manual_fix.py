# -*- coding: utf-8 -*-
"""P0-C 人工修复保护（M0）单元测试。零依赖 unittest。
运行：cd scripts/tests && python -m unittest test_manual_fix -v"""
import os, sys, json, tempfile, shutil, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import aa_translate as T

class TestManualFix(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fn = "entry_3463_汉化.txt"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_record_and_detect(self):
        before = "原行 まるで奴隷"
        after = "原行 简直像奴隶"
        self.assertTrue(T.record_manual_fix(self.tmp, self.fn, 123, before, after, "人工回填"))
        self.assertTrue(T.is_manual_fix(self.tmp, self.fn, 123, after))
        # 内容变化后不再受保护
        self.assertFalse(T.is_manual_fix(self.tmp, self.fn, 123, "完全不同"))

    def test_noop_when_unchanged(self):
        self.assertFalse(T.record_manual_fix(self.tmp, self.fn, 1, "same", "same"))

    def test_other_line_and_file_unprotected(self):
        T.record_manual_fix(self.tmp, self.fn, 5, "a", "b")
        self.assertFalse(T.is_manual_fix(self.tmp, self.fn, 999, "b"))   # 不同行
        self.assertFalse(T.is_manual_fix(self.tmp, "other_汉化.txt", 5, "b"))  # 不同文件

    def test_persistence_and_hash(self):
        T.record_manual_fix(self.tmp, self.fn, 42, "x", "y")
        with open(os.path.join(self.tmp, "manual_fix.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data[self.fn]["42"]["text"], "y")
        self.assertEqual(len(data[self.fn]["42"]["hash"]), 16)

    def test_missing_file_returns_empty(self):
        self.assertEqual(T.load_manual_fixes(os.path.join(self.tmp, "nope")), {})

if __name__ == "__main__":
    unittest.main()
