# -*- coding: utf-8 -*-
"""L0 纯函数测试：aa_common 的宽度/填充/假名/文本行判定规则。

运行：python -m unittest discover -s scripts/tests -v
零依赖（unittest 标准库），确定性，无 IO。
"""
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(TESTS_DIR), "common"))

import aa_common as C


class TestDispWidth(unittest.TestCase):
    def test_ascii_1(self):
        self.assertEqual(C.disp_width("abc ABC 123"), 11)

    def test_fullwidth_2(self):
        self.assertEqual(C.disp_width("あいう"), 6)
        self.assertEqual(C.disp_width("漢字"), 4)
        self.assertEqual(C.disp_width("　"), 2)          # U+3000

    def test_hankaku_kana_1(self):
        # 半角片假名 U+FF61-FF9F 渲染 1 格（MS Gothic 实测）——历史事故点
        self.assertEqual(C.disp_width("ﾘﾊｱｲ"), 4)
        self.assertEqual(C.disp_width("ﾉﾐﾑﾊﾞｯｽﾄ"), 8)

    def test_halfwidth_latin_2(self):
        # 半角拉丁字母（ｗ U+FF57）渲染 2 格，不算半角——历史事故点
        self.assertEqual(C.disp_width("ｗ"), 2)
        self.assertEqual(C.disp_width("ｗｗｗ"), 6)


class TestPadToWidth(unittest.TestCase):
    def test_equal(self):
        self.assertEqual(C.pad_to_width("abc", 3), "abc")

    def test_pad_fullwidth(self):
        self.assertEqual(C.pad_to_width("あ", 6), "あ\u3000\u3000")

    def test_pad_odd(self):
        # 差 3 格：全角×1 + 半角×1
        self.assertEqual(C.pad_to_width("ab", 5), "ab\u3000 ")
        # 差 1 格：半角空格
        self.assertEqual(C.pad_to_width("a", 2), "a ")

    def test_pad_before_box_char(self):
        # 行尾原本有空白、非空白尾字符是边框：补在边框前（保留原尾随空白）
        self.assertEqual(C.pad_to_width("あ│　", 8), "あ\u3000│\u3000")

    def test_no_shrink(self):
        # 超宽不裁（返回原行）
        self.assertEqual(C.pad_to_width("abcdef", 3), "abcdef")


class TestKanaRuns(unittest.TestCase):
    def test_basic(self):
        # こ 是艺术假名（嘴形）被剔除
        self.assertEqual(C.kana_runs("こんにちは世界"), ["んにちは"])

    def test_art_hira_excluded(self):
        # 艺术假名（嘴/眉/肢体：つだりへ…）不计数
        self.assertEqual(C.kana_runs("つだりへ"), [])

    def test_short_run_filtered(self):
        self.assertEqual(C.kana_runs("あ"), [])          # 单字 <2

    def test_art_kata_excluded(self):
        self.assertEqual(C.kana_runs("ノミニハマレヘ"), [])


class TestResidualSuspect(unittest.TestCase):
    def test_pure_cn_ok(self):
        self.assertEqual(C.residual_suspect("你好世界，这里是台词"), [])

    def test_kana_suspect(self):
        # こ 是艺术假名，剔除后剩 んにちは
        self.assertIn("んにちは", C.residual_suspect("你好こんにちは"))

    def test_known_name_ok(self):
        # 白名单内角色名不算残留
        self.assertEqual(C.residual_suspect("这是キング说的话"), [])

    def test_short_residual_not_suspect(self):
        # kana_runs ≥2 但 suspect ≥3：2 字段不报（短残留靠 pass2 的计数规则）
        self.assertEqual(C.kana_runs("そよ"), ["そよ"])
        self.assertEqual(C.residual_suspect("そよ"), [])

    def test_long_residual_suspect(self):
        # おめでとう → 剔除 と/う → おめで（≥3）
        self.assertEqual(C.residual_suspect("おめでとう"), ["おめで"])


class TestIsTextLine(unittest.TestCase):
    def test_header_skip(self):
        self.assertFalse(C.is_text_line("539 ： ◆5QXHO4/GJY ： 2022/07/15(金) 20:03:43 ID:6mjsUxpY00"))
        self.assertFalse(C.is_text_line("1 名前： ◆XXX 投稿日：2022/01/01 ID:abc"))

    def test_marker_skip(self):
        self.assertFalse(C.is_text_line("# 标题"))
        self.assertFalse(C.is_text_line("===== AA块 1 ====="))

    def test_kana_is_text(self):
        self.assertTrue(C.is_text_line("　　　　｜　こんにちは、お元気ですか？　｜"))

    def test_art_kanji_not_text(self):
        # 纯艺术汉字（黑名单内）不标记
        self.assertFalse(C.is_text_line("　｜￣￣｜　圭　土　｜￣￣｜　"))

    def test_real_kanji_is_text(self):
        self.assertTrue(C.is_text_line("これは魔女の力です"))

    def test_pure_punct_not_text(self):
        # 日文标点行不标记（艺术行常用 、 作装饰耳形）
        self.assertFalse(C.is_text_line("　、。、　、。、　"))


if __name__ == "__main__":
    unittest.main()
