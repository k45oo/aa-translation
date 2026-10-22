# -*- coding: utf-8 -*-
"""行编辑类型自动分类（2026-10-01，口径由用户逐条确认）。

覆盖三层：
  ① logic.classify_edit 判据表：错位(纯空白) / 校对(中文换中文) / 漏译(日文被吃掉)
     / 错译(还原原文或混合行重写) / art 行不判(None) / pad 补位不参与判定
  ② logic.jp_degree：日文度 = (假名数, 与原文的汉字多重集交集)
  ③ api_save_line 契约：body.type 空串 → 后端判定并回带 type/type_auto；显式值 → 人工优先；
     art 行 → 沿用旧默认「错位」；结果同时落 work/edits.jsonl
运行：.venv/Scripts/python.exe -m unittest scripts.tests.test_edit_type -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
SCRIPTS = os.path.join(_REPO_ROOT, "scripts")
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.join(SCRIPTS, "common"))
sys.path.insert(0, os.path.join(_REPO_ROOT, "aa_web_app"))

from aa_web_app import logic  # noqa: E402


class TestClassifyEdit(unittest.TestCase):
    def test_pure_whitespace_change_is_misaligned(self):
        # 行内/行尾空白挪位，文本字符完全一致 → 错位
        self.assertEqual(logic.classify_edit("　勇者よ", "　　勇者　　よ", "　勇者よ　"), "错位")
        self.assertEqual(logic.classify_edit("あいう", "あ い う", "あいう"), "错位")

    def test_pad_does_not_become_misaligned(self):
        # 提交值比旧值短（后端会补 U+3000 到源宽）：提交值本身是中文 → 校对，不该判错位
        self.assertEqual(logic.classify_edit("あいうえおかき", "中文译文", "中文"), "校对")

    def test_chinese_to_chinese_is_proofread(self):
        self.assertEqual(logic.classify_edit("白頭の魔女", "白头魔女", "白发魔女"), "校对")
        self.assertEqual(logic.classify_edit("亜紀", "亚纪", "有希"), "校对")

    def test_kana_eaten_is_missing_translation(self):
        self.assertEqual(logic.classify_edit("白頭の魔女", "白頭の魔女", "白头魔女"), "漏译")
        self.assertEqual(logic.classify_edit("白頭の魔女", "白头の魔女", "白头魔女"), "漏译")

    def test_kanji_only_japanese_copied_line_is_missing_translation(self):
        # 纯汉字日文盲区（假名 0）：旧行照抄原文「白頭」→ 改「白头」必须判漏译，不是校对
        self.assertEqual(logic.classify_edit("白頭", "白頭", "白头"), "漏译")
        self.assertEqual(logic.classify_edit("魔女", "魔女", "巫女"), "漏译")

    def test_restore_src_is_wrong_translation(self):
        src = "やる夫は勇者です"
        self.assertEqual(logic.classify_edit(src, "亚鲁欧是勇者", src), "错译")

    def test_mixed_row_same_kana_count_is_wrong_translation(self):
        # 混合行重写：假名数不变但中文变了 → 错译（不是校对，也不是漏译）
        self.assertEqual(logic.classify_edit("白頭の魔女", "白头的魔女", "白头の魔女"), "错译")

    def test_art_line_not_classified(self):
        self.assertIsNone(logic.classify_edit("ミニニニ", "ミニニ", "ミニニニニ", kind="aa"))
        self.assertIsNone(logic.classify_edit("ミニニニ", "ミニニニ", "ミニニニ", kind="aa"))

    def test_missing_src_falls_back_to_text_rules(self):
        # 原文缺失（"" ）不该崩：无假名两行 → 校对；日文被吃掉 → 漏译
        self.assertEqual(logic.classify_edit("", "白头魔女", "白发魔女"), "校对")
        self.assertEqual(logic.classify_edit("", "白头の魔女", "白头魔女"), "漏译")

    def test_jp_degree_shape(self):
        self.assertEqual(logic.jp_degree("あいう", "あいう"), (3, 3))
        self.assertEqual(logic.jp_degree("中文", "白頭"), (0, 0))
        self.assertEqual(logic.jp_degree("白頭", "白頭の魔女"), (0, 2))
        self.assertEqual(logic.jp_degree("ｶﾞｶﾞ", ""), (4, 0))      # 半角片假名+浊点都算假名
        self.assertEqual(logic.jp_degree("俺 の 名", "俺の名"), (1, 3))   # 空白不参与重合

    def test_manual_types_constant(self):
        # 重译（2026-10-19）：阅读器 Shift+右键行重译 / 手工重翻行显式落的类型，
        # 用于在「修改记录」里分辨机器重译行（≠自动分类出的 错译/漏译/校对）。
        self.assertEqual(logic.MANUAL_EDIT_TYPES, ("错译", "漏译", "错位", "校对", "重译"))
        self.assertIn(logic.AUTO_EDIT_DEFAULT, logic.MANUAL_EDIT_TYPES)


class EditTypeApiFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="edit_type_test_")
        self.aa_dir = os.path.join(self.tmp, "aa")
        self.out_dir = os.path.join(self.tmp, "汉化")
        self.work_dir = os.path.join(self.tmp, "work")
        for d in (self.aa_dir, self.out_dir, self.work_dir):
            os.makedirs(d)
        self.base = "entry_1"
        self._write(self.aa_dir, self.base + ".txt",
                    ["# 标题", "白頭の魔女", "あいうえお", "ミニニニニ"])
        self._write(self.out_dir, self.base + "_汉化.txt",
                    ["# 标题", "白头魔女", "啊呜诶哦", "ミニニニニ"])
        self.cfg = {"work_dir": self.work_dir, "aa_dir": self.aa_dir,
                    "out_dir": self.out_dir, "title": "测试作品"}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, d, name, lines):
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines))
        return p

    def _main(self):
        import aa_web_app.main as M
        return M

    def _save(self, M, idx, body):
        with mock.patch("aa_web_app.web_state.cfg_of", return_value=self.cfg), \
                mock.patch("aa_web_app.logic.load_work_glossary",
                           return_value={"known_names": set()}):
            return M.api_save_line("test", self.base + "_汉化.txt", idx, body)

    def _mark_art(self, line_no_1based):
        p = logic.overrides_path(self.work_dir, self.base)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({str(line_no_1based): "art"}, f)      # override 存 kind（art），非展示名 aa

    def _edits(self):
        p = os.path.join(self.work_dir, "edits.jsonl")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]


class TestSaveLineAutoType(EditTypeApiFixture):
    def test_empty_type_classifies_chinese_to_chinese(self):
        M = self._main()
        r = self._save(M, 1, M.LineSave(text="白发魔女"))     # type 默认 ""
        self.assertEqual(r["result"], "ok")
        self.assertEqual(r["type"], "校对")
        self.assertTrue(r["type_auto"])
        rows = self._edits()
        self.assertEqual([(x["line"], x["type"]) for x in rows], [(2, "校对")])
        self.assertEqual(rows[0]["before"], "白头魔女")
        self.assertEqual(rows[0]["after"].rstrip("\u3000"), "白发魔女")   # 落盘值已补宽

    def test_empty_type_classifies_missing_translation(self):
        M = self._main()
        # 旧行是照抄的日文（含假名）→ 译成中文 = 漏译
        logic.write_lines(logic.txt_path(self.out_dir, self.base + "_汉化.txt"),
                          ["# 标题", "白頭の魔女", "啊呜诶哦", "ミニニニニ"])
        r = self._save(M, 1, M.LineSave(text="白头魔女"))
        self.assertEqual(r["result"], "ok")
        self.assertEqual(r["type"], "漏译")
        self.assertTrue(r["type_auto"])

    def test_pad_shortline_is_not_misaligned(self):
        M = self._main()
        r = self._save(M, 2, M.LineSave(text="啊呜"))       # 比源行短 → 后端补宽
        self.assertEqual(r["result"], "ok")
        self.assertTrue(r["padded"])
        self.assertEqual(r["type"], "校对")                   # 不是「错位」
        self.assertTrue(r["type_auto"])

    def test_explicit_type_wins(self):
        M = self._main()
        r = self._save(M, 1, M.LineSave(text="白发魔女", type="错译"))
        self.assertEqual(r["type"], "错译")
        self.assertFalse(r["type_auto"])
        self.assertEqual(self._edits()[0]["type"], "错译")

    def test_new_type_proofread_accepted_explicitly(self):
        M = self._main()
        r = self._save(M, 1, M.LineSave(text="白发魔女", type="校对"))
        self.assertEqual(r["type"], "校对")
        self.assertFalse(r["type_auto"])

    def test_unknown_explicit_type_falls_back_to_default(self):
        M = self._main()
        r = self._save(M, 1, M.LineSave(text="白发魔女", type="乱写"))
        self.assertEqual(r["type"], "错位")                   # 旧行为：未知值落「错位」
        self.assertFalse(r["type_auto"])

    def test_pure_whitespace_edit_is_misaligned(self):
        M = self._main()
        r = self._save(M, 3, M.LineSave(text="ミニニニ ニ"))   # 只加一个空格 → 文本字符不变
        self.assertEqual(r["result"], "ok")
        self.assertEqual(r["type"], "错位")
        self.assertTrue(r["type_auto"])

    def test_art_line_keeps_default_type(self):
        M = self._main()
        self._mark_art(4)                                     # 第 4 行标为 art
        r = self._save(M, 3, M.LineSave(text="ミニニニニニ"))   # 改纯图形行
        self.assertEqual(r["result"], "ok")
        self.assertEqual(r["type"], "错位")                    # art 行不判 → 沿用旧默认
        self.assertTrue(r["type_auto"])

    def test_restore_src_classified_wrong_translation(self):
        M = self._main()
        r = self._save(M, 1, M.LineSave(text="白頭の魔女"))     # 中文 → 原文 = 错译
        self.assertEqual(r["type"], "错译")
        self.assertTrue(r["type_auto"])


if __name__ == "__main__":
    unittest.main()
