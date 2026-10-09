# -*- coding: utf-8 -*-
"""refine 本地重建（fix_mixed_drift.rebuild_row）内容保全回归测试（2026-09-20 事故）。

事故（用户报「数字+クレジット 只剩信用点」「框线行 |…| 翻译丢失」）:
- 译文候选字符集 _CJK_CHAR 缺 全角数字０-９ / ー(U+30FC) / 全角括号（） →
  '６６１信用点' 的 ６６１ 被 U+3000 垫掉（数字消失）；
  '期待你今后的表现哦ーー！！' 被切成两个候选 → 行尾主台词段只拿到 '！！'（整句译文被吞）。
- restore_src_nozh 只看「≥2 连续平假名」→ 已译好、仅保留日文专名（やる夫）的行被整行还原成日文。

本测试零 API、纯本地。
"""
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS_DIR)
for _p in ("common", "works", "tools", "fix"):
    sys.path.insert(0, os.path.join(SCRIPTS, _p))

import aa_translate as T                      # noqa: E402
import fix_mixed_drift as FMD                 # noqa: E402


def make_segs(*parts):
    """[(kind, text, confidence)] → segments（1 字符 = 1 下标，自动算 src_width）。"""
    segs, pos = [], 0
    for kind, text, conf in parts:
        segs.append({"start": pos, "end": pos + len(text), "kind": kind,
                     "src_width": T.disp_width(text), "confidence": conf})
        pos += len(text)
    return segs


class TestCandidateCharset(unittest.TestCase):
    """译文候选提取必须包含全角数字 / ー / 全角括号（否则重建即吞内容）。"""

    def test_fullwidth_digits_and_dash_are_one_candidate(self):
        tr = "｜　期待你今后的表现哦ーー！！　｜"
        cands = FMD.extract_translations(tr, [], set())
        self.assertIn("期待你今后的表现哦ーー！！", cands)

    def test_fullwidth_credit_with_digits(self):
        cands = FMD.extract_translations("│　６６１信用点　→　１信用点　│", [], set())
        self.assertIn("６６１信用点", cands)
        self.assertIn("１信用点", cands)

    def test_fullwidth_parens_kept(self):
        cands = FMD.extract_translations("│　（另一个账户）　１５００信用点　│", [], set())
        self.assertTrue(any("（另一个账户）" in c for c in cands), cands)


class TestRebuildKeepsContent(unittest.TestCase):
    """整行重建后：数字 / 译文 / 日文专名一个都不能少。"""

    def test_digits_survive_rebuild(self):
        src = "│　１９０クレジット　→　５０クレジット　│"
        tr = "│　１９０信用点　→　５０信用点　│"
        segs = make_segs(("art", "│　", 1.0), ("text", "１９０クレジット", 1.0),
                         ("art", "　→　", 1.0), ("text", "５０クレジット", 1.0),
                         ("art", "　│", 1.0))
        new, ok, reason = FMD.rebuild_row(src, tr, segs, [])
        self.assertTrue(ok, reason)
        self.assertIn("１９０", new)
        self.assertIn("５０", new)
        self.assertEqual(T.disp_width(new), T.disp_width(src))

    def test_tail_boxed_line_not_wiped_to_marks(self):
        src = "│　今日もやりまくるわよーーっ！！　│"
        tr = "│　今天也要大干一场哦ーー！！　│"
        segs = make_segs(("art", "│　", 1.0), ("text", "今日もやりまくるわよーーっ！！", 1.0),
                         ("art", "　│", 1.0))
        new, ok, reason = FMD.rebuild_row(src, tr, segs, [])
        self.assertTrue(ok, reason)
        self.assertIn("今天也要大干一场", new)
        self.assertEqual(T.disp_width(new), T.disp_width(src))

    def test_translated_line_with_jp_name_not_restored_to_src(self):
        """已译好但保留专名 やる夫 的行，不能因「≥2 连续平假名」被还原成日文。"""
        src = "│　やる夫——快跑——！！　│"
        tr = "│　やる夫——快跑——！！　│"
        segs = make_segs(("art", "│　", 1.0), ("text", "やる夫——快跑——！！", 1.0),
                         ("art", "　│", 1.0))
        _new, ok, reason = FMD.rebuild_row(src, tr, segs, [])
        self.assertNotEqual(reason, "restore_src_nozh")

    def test_untranslated_japanese_line_still_restored(self):
        """真漏译行（假名占优）仍照旧还原 SRC，交由翻译管线处理。"""
        src = "│　ありがとね　ハンターやる夫　│"
        tr = "│　ありがとね　ハンターやる夫　│"
        segs = make_segs(("art", "│　", 1.0), ("text", "ありがとね　ハンターやる夫", 1.0),
                         ("art", "　│", 1.0))
        _new, ok, reason = FMD.rebuild_row(src, tr, segs, [])
        self.assertIn(reason, ("restore_src_nozh", "restore_src"))


class TestDigitLossGuard(unittest.TestCase):
    """重建外壳的数字保全兜底：数字消失一律拒绝（落人工），绝不写坏数据。"""

    def test_nums_kept_helper(self):
        self.assertTrue(FMD._nums_kept("│　６６１信用点　│", "│　661信用点　│"))
        self.assertFalse(FMD._nums_kept("│　６６１信用点　│", "│　信用点　│"))

    def test_wrapper_rejects_digit_loss(self):
        src = "│　６６１クレジット　│"
        tr = "│　６６１信用点　│"
        orig = FMD._rebuild_row_impl
        try:
            FMD._rebuild_row_impl = lambda s, t, g, o: (s[:3] + "信用点" + s[6:], True, "ok")
            new, ok, reason = FMD.rebuild_row(src, tr, [], [])
            self.assertFalse(ok)
            self.assertIsNone(new)
            self.assertEqual(reason, "digit_loss")
        finally:
            FMD._rebuild_row_impl = orig

    def test_wrapper_skips_guard_for_src_restore(self):
        """还原 SRC 的分支不校验数字（那本就是把整行换成原文）。"""
        src = "│　６６１クレジット　│"
        tr = "│　信用点　│"
        orig = FMD._rebuild_row_impl
        try:
            FMD._rebuild_row_impl = lambda s, t, g, o: (s, True, "restore_src_nozh")
            new, ok, reason = FMD.rebuild_row(src, tr, [], [])
            self.assertTrue(ok)
            self.assertEqual(reason, "restore_src_nozh")
        finally:
            FMD._rebuild_row_impl = orig


if __name__ == "__main__":
    unittest.main()
