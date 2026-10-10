# -*- coding: utf-8 -*-
"""collect_mismatch 宽度校验口径测试（2026-08-27 口径统一）。

口径唯一来源 = 平铺版 aa_translate.line_kind/allow_overwidth：
- short（译文变短）任何行类型都报
- art 行超宽报
- text/mixed/header/skip 行超宽放行（译文自然扩展）
运行：python -m unittest discover -s scripts/tests -v
"""
import importlib.util
import os
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(os.path.dirname(TESTS_DIR))
_COMMON = os.path.join(_SCRIPTS, "common")
sys.path.insert(0, _COMMON)
sys.path.insert(0, _SCRIPTS)

_SPEC = importlib.util.spec_from_file_location(
    "scan_width_impl", os.path.join(_COMMON, "scan_width.py"))
SW = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(SW)


def _mk_pair(tmp, fname, src_lines, tr_lines):
    aa = os.path.join(tmp, "aa"); out = os.path.join(tmp, "out")
    os.makedirs(aa, exist_ok=True); os.makedirs(out, exist_ok=True)
    open(os.path.join(aa, fname), "w", encoding="utf-8").write("\n".join(src_lines))
    open(os.path.join(out, fname.replace(".txt", "_汉化.txt")), "w",
         encoding="utf-8").write("\n".join(tr_lines))


class TestCollectMismatch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        # 帖头宽 60，正文 art 宽 20，text 行宽 12
        self.header = "3672 ： ◆toJd5AYQtw ： 2019/11/29(金) 19:57:14 ID:9WOoVxGX"
        self.art = "＿＿＿＿＿＿＿＿＿＿"          # 纯图形 art 行
        self.text = "、「こんにちは。」　　"       # 全角假名 → text 行

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, src, tr):
        _mk_pair(self.tmp, "entry_1.txt", src, tr)
        rows, _ra, meta = SW.collect_mismatch(
            os.path.join(self.tmp, "aa"), os.path.join(self.tmp, "out"))
        return [r for r in rows if r[0] == "entry_1.txt"]

    def test_short_always_reported(self):
        """译文变短——任何行类型都报。"""
        tr_art = "＿＿＿＿＿＿＿"                # art 行变短
        rows = self._run([self.art], [tr_art])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2], 20)   # src_w
        self.assertEqual(rows[0][3], 14)   # tr_w

    def test_text_overwidth_allowed(self):
        """text 行译文自然变长——放行不报（旧 runner 内联版会误报）。"""
        long_tr = "「中文译文比较长的情况下也允许自然扩展哦」"
        rows = self._run([self.text], [long_tr])
        self.assertEqual(rows, [])

    def test_mixed_overwidth_allowed(self):
        """mixed 行（右侧台词+图形符）超宽——放行。"""
        m_src = "ﾆﾆﾆﾆｱｱｱｱ、「やあ」"
        m_tr = "ﾆﾆﾆﾆｱｱｱｱ、「你好呀朋友」"
        rows = self._run([m_src], [m_tr])
        self.assertEqual(rows, [])

    def test_header_overwidth_allowed(self):
        """帖头行（header）超宽——放行；变短仍报。"""
        rows = self._run([self.header],
                         ["3672 ： ◆toJd5AYQtw ： 2019/11/29(金) 19:57:14 ID:9WOoVxGXOA"])
        self.assertEqual(rows, [])
        # header 变短 → short 恒报
        rows = self._run([self.header], [self.header[:40]])
        self.assertEqual(len(rows), 1)

    def test_skip_line_ignored(self):
        """# 标题行不参与校验。"""
        rows = self._run(["# タイトル"], ["# 中文标题随便多长都可以"])
        self.assertEqual(rows, [])

    def test_count_mismatch_reported(self):
        """行数不一致——不计入错位行但要在 meta 里上报。"""
        _mk_pair(self.tmp, "entry_2.txt", [self.art, self.text],
                 [self.art, self.text, "多出来的行"])
        _, _ra, (n, skipped, cm) = SW.collect_mismatch(
            os.path.join(self.tmp, "aa"), os.path.join(self.tmp, "out"),
            files=["entry_2.txt"])
        self.assertEqual(len(cm), 1)
        self.assertEqual(cm[0][0], "entry_2.txt")

    def test_files_filter_and_missing(self):
        """files=定向章节；缺失文件进 skipped 而非报错。"""
        _mk_pair(self.tmp, "entry_3.txt", [self.art], [self.art])
        rows, _ra, (n, skipped, cm) = SW.collect_mismatch(
            os.path.join(self.tmp, "aa"), os.path.join(self.tmp, "out"),
            files=["entry_3.txt", "entry_missing.txt"])
        self.assertEqual(n, 2)
        self.assertEqual(skipped, ["entry_missing.txt"])
        self.assertEqual(cm, [])
        self.assertEqual([(r[0]) for r in rows if r[0] == "entry_3.txt"], [])


if __name__ == "__main__":
    unittest.main()
