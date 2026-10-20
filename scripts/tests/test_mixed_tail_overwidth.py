#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""段级「漏内容」预防的两条守卫（2026-10-20，方案 doc/方案归档/段级漏内容根因与预防方案_2026-10-20.md）：

1. **末尾段向右超宽**：行尾 text 段超宽时不再压缩/判失败（旧行为 = `_compress_seg_to_width`
   丢字 / `seg_wide` 重试 + 「可精简」提示词 → 删实词）。
   门户：该段之后没有 art 段才放开；有 art 段（边框在行尾）照旧等宽，绝不能推右 art。
2. **提示词不再授权精简**：`MIXED_SHORT_HINT` / `SYSTEM_MIXED` 规则 4 不得出现「可精简／比原文短」。
3. **AA 记号码判艺术**：`ア＾` 这类「艺术假名 + 记号」短段不进模型（否则 ア→啊）。
"""
import os
import sys
import unittest

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "works")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import aa_translate as T                      # noqa: E402
import aa_art_kana as K                       # noqa: E402


def _segs(line, arts, texts):
    """按 (字符区间, kind) 手工造 segments（带 src_width）。"""
    out = []
    for st, en in arts:
        out.append({"kind": "art", "start": st, "end": en,
                    "src_width": T.disp_width(line[st:en])})
    for st, en in texts:
        out.append({"kind": "text", "start": st, "end": en,
                    "src_width": T.disp_width(line[st:en]), "confidence": 0.9})
    out.sort(key=lambda s: s["start"])
    return out


class TestTailOverwidth(unittest.TestCase):
    """行尾 text 段允许向右超宽（art 列位不变）。"""

    LINE = "三三三三　　ああああ"          # art(8) + text(8)
    SEGS = None

    def setUp(self):
        self.LINE = "三三三三　　ああああ"
        self.SEGS = _segs(self.LINE, [(0, 6)], [(6, len(self.LINE))])
        os.environ.pop("AA_MIXED_TAIL_OVERWIDTH", None)

    def tearDown(self):
        os.environ.pop("AA_MIXED_TAIL_OVERWIDTH", None)

    def test_开关默认开(self):
        self.assertTrue(T.mixed_tail_overwidth_enabled())
        os.environ["AA_MIXED_TAIL_OVERWIDTH"] = "0"
        self.assertFalse(T.mixed_tail_overwidth_enabled())

    def test_末尾段超宽_不再压缩_整行变宽(self):
        cand = T.rebuild_mixed_line(self.LINE, self.SEGS, {1: "你好你好你好你好你好"},
                                    allow_tail_overwidth=True)
        self.assertIsNotNone(cand)
        self.assertGreater(T.disp_width(cand), T.disp_width(self.LINE))
        self.assertTrue(cand.startswith("三三三四"[:0] + self.LINE[:6]))   # art 前缀逐字节不变
        self.assertIn("你好", cand)

    def test_末尾段超宽_装配层接受(self):
        out = T._mixed_assemble(self.LINE, self.SEGS, {1: "你好你好你好你好你好"}, {},
                                allow_tail_overwidth=True)
        self.assertIsNotNone(out)
        self.assertGreater(T.disp_width(out), T.disp_width(self.LINE))

    def test_开关关_恢复旧行为_压不动则整行失败(self):
        os.environ["AA_MIXED_TAIL_OVERWIDTH"] = "0"
        out = T._mixed_assemble(self.LINE, self.SEGS, {1: "你好你好你好你好你好"}, {})
        self.assertIsNone(out)
        cand = T.rebuild_mixed_line(self.LINE, self.SEGS, {1: "你好你好你好你好你好"})
        self.assertIsNone(cand)          # 不显式传参 → 默认 False，行为与旧版一致

    def test_行尾有art_仍必须等宽(self):
        """边框在行尾（text 之后还有 art 段）→ 不能超宽，否则 art 被推右。"""
        line = "三三三　　ああああ｜"
        segs = _segs(line, [(0, 5), (len(line) - 1, len(line))], [(5, len(line) - 1)])
        self.assertFalse(T._seg_is_tail(segs, 1))
        cand = T.rebuild_mixed_line(line, segs, {1: "你好你好你好你好你好你好"},
                                    allow_tail_overwidth=True)
        self.assertIsNone(cand)

    def test_正常等宽不受影响(self):
        cand = T.rebuild_mixed_line(self.LINE, self.SEGS, {1: "你好你好"})
        self.assertTrue(cand.startswith(self.LINE[:6]))
        self.assertEqual(T.disp_width(cand), T.disp_width(self.LINE))
        self.assertTrue(T._verify_art_alignment(self.LINE, cand, self.SEGS))


class TestVerdictTail(unittest.TestCase):
    """`_mixed_seg_verdict`：末尾段超宽 → flag(overwide_tail) 而不是 retry(seg_wide)。"""

    ORIG = "ああああ"

    def setUp(self):
        os.environ.pop("AA_MIXED_TAIL_OVERWIDTH", None)

    def tearDown(self):
        os.environ.pop("AA_MIXED_TAIL_OVERWIDTH", None)

    def test_末尾段超宽_记复核不再重试(self):
        v, why, hint = T._mixed_seg_verdict("你好你好你好你好", self.ORIG, False, set(), 0.35, 0.6,
                                            is_tail=True)
        self.assertEqual((v, why, hint), ("flag", "overwide_tail", ""))

    def test_非末尾段超宽_照旧重试(self):
        v, why, _h = T._mixed_seg_verdict("你好你好你好你好", self.ORIG, False, set(), 0.35, 0.6,
                                          is_tail=False)
        self.assertEqual((v, why), ("retry", "seg_wide"))

    def test_开关关_末尾段也重试(self):
        os.environ["AA_MIXED_TAIL_OVERWIDTH"] = "0"
        v, why, _h = T._mixed_seg_verdict("你好你好你好你好", self.ORIG, False, set(), 0.35, 0.6,
                                          is_tail=True)
        self.assertEqual((v, why), ("retry", "seg_wide"))


class TestPromptsDoNotAuthorizeTrimming(unittest.TestCase):
    """提示词不得再出现"可精简/比原文短"这类授权删内容的措辞。"""

    def test_short_hint(self):
        h = T.MIXED_SHORT_HINT
        self.assertNotIn("可意译/精简", h)
        self.assertNotIn("必须**比原文短或等长**", h)
        self.assertIn("实词", h)

    def test_system_mixed_规则4(self):
        self.assertIn("宁可译文略长", T.SYSTEM_MIXED)
        self.assertNotIn("译文太长会放不下", T.SYSTEM_MIXED)


class TestArtMarkSegments(unittest.TestCase):
    """`ア＾` 这类「艺术假名 + AA 记号」短段判艺术（Rule A），不进模型。"""

    def test_记号残片判艺术(self):
        for s in ("ア＾", "＾＼", "｜|", "｀´"):
            self.assertTrue(K.seg_is_art(s), s)

    def test_真台词不受影响(self):
        for s in ("えっ？", "ふふ、ふふふ", "ありがとう", "やる夫"):
            self.assertFalse(K.seg_is_art(s), s)


class TestRefineHalfTranslateRevert(unittest.TestCase):
    """refine 半译回滚判据（2026-10-20）：模型新引入的整段日文 → 判回滚、保留旧译文。"""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(_SCRIPTS, "fix"))
        import refine_aa as R
        cls.R = R

    def test_整段日文新引入_命中(self):
        # entry_22630 L1826 实况：refine 把前半句原样吐回日文
        new = "※　全区回ったので+忘了说全区逛完后会加10奖励，不过本体的老板果然还是运气不好"
        old = "※　因为已经逛遍了全区+忘了说全区逛完后会加10奖励，不过本体的老板果然还是运气不好"
        self.assertTrue(self.R._introduced_kana_runs(new, old))

    def test_旧译文本来就有_不算新引入(self):
        new = "※　矢泽にの+忘了说全区逛完后会加10奖励"
        old = "※　矢泽にの+忘了说全区逛完后会加10奖励"
        self.assertEqual(self.R._introduced_kana_runs(new, old), [])

    def test_纯中文译文_不命中(self):
        self.assertEqual(self.R._introduced_kana_runs("我是生前做偶像的", "我生前是干"), [])

    def test_艺术假名不算残留(self):
        self.assertEqual(self.R._introduced_kana_runs("ニニニニ", ""), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
