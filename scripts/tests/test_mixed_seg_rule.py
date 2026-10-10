# -*- coding: utf-8 -*-
"""规则式台词段单测（2026-09-15 P2-a）。

背景: 段级协议（art 本地拼回）只对 meta conf>=0.65 的 text 段开放；实测不少章节 text 段
全是 0.4 → 整章退回整行协议、art 由模型复述（译文里 61.3% 字符是绘画/填充）。
本模块为这类 mixed 行补"规则抠台词段"，仍需覆盖**整行**（rebuild_mixed_line 按段顺序拼回，
空档会丢字符；_verify_art_alignment 依赖 art 段列位置）。
"""
import os
import sys
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in (SCRIPTS, os.path.join(SCRIPTS, "common")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import aa_translate as A


class TestRuleSegments(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("AA_MIXED_SEG_RULE")
        os.environ.pop("AA_MIXED_SEG_RULE", None)

    def tearDown(self):
        os.environ.pop("AA_MIXED_SEG_RULE", None) if self._old is None else os.environ.__setitem__("AA_MIXED_SEG_RULE", self._old)

    def test_covers_whole_line_and_marks_dialogue(self):
        line = "┌────┐　今日はいい天気ですね　│￣￣│"
        segs = A.mixed_rule_segments(line)
        self.assertTrue(segs)
        self.assertEqual(segs[0]["start"], 0)
        self.assertEqual(segs[-1]["end"], len(line))
        for a, b in zip(segs, segs[1:]):
            self.assertEqual(a["end"], b["start"])            # 无缝、无空档
        texts = [s for s in segs if s["kind"] == "text"]
        self.assertEqual(len(texts), 1)
        self.assertEqual(line[texts[0]["start"]:texts[0]["end"]], "今日はいい天気ですね")
        self.assertNotIn("confidence", texts[0])              # 缺省=高置信, 才会被 _confident_text_segs 选中
        self.assertEqual(A._confident_text_segs(segs), [segs.index(texts[0])])

    def test_pure_katakana_art_rejected(self):
        self.assertEqual(A.mixed_rule_segments("＼ﾆﾆﾆﾆ／　ニニニニ　ミミミ"), [])
        self.assertEqual(A.mixed_rule_segments(".:.:.:.:.:　／／／／　￣￣￣"), [])

    def test_repeat_kanji_art_rejected(self):
        """三三ミ 这类 art 描边（汉字重复）不能当台词送模型。"""
        self.assertEqual(A.mixed_rule_segments("　ヽ＿＿ 三三ミ ＿＿ﾉ"), [])

    def test_kanji_dialogue_accepted(self):
        segs = A.mixed_rule_segments("│｜　大丈夫　｜│")
        kinds = [s["kind"] for s in segs]
        self.assertIn("text", kinds)

    def test_too_short_rejected(self):
        self.assertEqual(A.mixed_rule_segments("　│あ│　　＼／"), [])

    def test_flag_default_off_and_env(self):
        """默认关（实测质量回归，见 mixed_seg_rule_enabled docstring）；显式 =1 才开。"""
        self.assertFalse(A.mixed_seg_rule_enabled())
        os.environ["AA_MIXED_SEG_RULE"] = "1"
        self.assertTrue(A.mixed_seg_rule_enabled())
        os.environ["AA_MIXED_SEG_RULE"] = "0"
        self.assertFalse(A.mixed_seg_rule_enabled())

    def test_rebuild_with_rule_segments_preserves_art_columns(self):
        """用规则段跑 rebuild + art 列校验（真代码路径），证明可安全替代整行协议。"""
        line = "┌──────┐　　今日はいい天気ですね　　│￣￣￣￣│"
        segs = A.mixed_rule_segments(line)
        ti = A._confident_text_segs(segs)
        self.assertTrue(ti)
        trans = {si: "今天天气真好" for si in ti}
        cand = A.rebuild_mixed_line(line, segs, trans)
        self.assertIsNotNone(cand)
        self.assertEqual(A.disp_width(cand), A.disp_width(line))       # 整行宽一致
        self.assertTrue(A._verify_art_alignment(line, cand, segs))     # art 列位不变
        self.assertNotIn("今日", cand)                                  # 原文已被替换

    def test_real_chapter_lines(self):
        """真章节抽查: 规则段覆盖率 + 段覆盖完整性（零 API）。"""
        import json
        d = os.path.join(SCRIPTS, "..", "aaManga", "やる夫のハーレム大王国")
        aa = os.path.join(d, "aa")
        md = os.path.join(d, "work", "line_meta_v2")
        if not os.path.exists(os.path.join(aa, "entry_25371.txt")):
            self.skipTest("样本章节不存在")
        src = open(os.path.join(aa, "entry_25371.txt"), encoding="utf-8").read().split("\n")
        meta = A.load_meta_0based(md, "entry_25371")
        n_lines = n_ok = n_meta_seg = 0
        for idx, line in enumerate(src):
            entry = meta.get(idx)
            kind = entry[0] if isinstance(entry, tuple) else entry
            if kind != "mixed" or not line.strip():
                continue
            n_lines += 1
            segs = A.mixed_rule_segments(line)
            if not segs:
                continue
            self.assertEqual(segs[0]["start"], 0)
            self.assertEqual(segs[-1]["end"], len(line))
            self.assertEqual("".join(line[s["start"]:s["end"]] for s in segs), line)   # 零丢字符
            if A._confident_text_segs(segs):
                n_ok += 1
                cand = A.rebuild_mixed_line(line, segs, {si: "测试译文" for si in A._confident_text_segs(segs)})
                if cand is not None:
                    self.assertEqual(A.disp_width(cand), A.disp_width(line))
                    self.assertTrue(A._verify_art_alignment(line, cand, segs))
        seg_meta = A.load_meta_segments_0based(md, "entry_25371") or {}
        n_meta_seg = sum(1 for i, sg in seg_meta.items() if A._confident_text_segs(sg))
        print(f"\n  entry_25371: mixed {n_lines} 行 / meta 段级可用 {n_meta_seg} 行 / 规则段级可用 {n_ok} 行")
        self.assertGreater(n_ok, 0)


class TestRuleGate20260915(unittest.TestCase):
    """抠段判据收紧（2026-09-15，AA_MIXED_SEG_RULE 启用前置）。

    两起真机事故（第101~103話 A/B）:
      ① entry_25417#605 行尾 `マっ！`（マ∈ART_KATA、っ∈ART_HIRA）被抠成段 → 模型原样照抄 → 漏译
         （整行路径基线译作「来了！」）；
      ② entry_25418#1044/#1081 绘画笔画里的 `ぅこ`（ぅ 小写、こ∈ART_HIRA）被抠成段 → 译成「呜」破坏画面。
    根因: 旧判据「含任一平假名」+ 段级残留检查按设计排除艺术假名 → 这类段两头都拦不住。
    """

    def test_small_kana_not_decisive(self):
        for run in ("マっ", "ぅこ", "ヽへノ", "ノし", "ゝへ", "ヽ"):
            self.assertFalse(A._rule_dialogue_run_ok(run), f"{run!r} 不该算台词")
        for run in ("こんにちは", "ラボ", "ダンジョン", "やる夫が来た", "さいしゅー", "今はそれよりマーク"):
            self.assertTrue(A._rule_dialogue_run_ok(run), f"{run!r} 应算台词")

    def test_art_kana_island_not_claimed(self):
        self.assertEqual(A.mixed_rule_segments("　│あ│　　＼／"), [])       # 太短老规则仍拒
        self.assertEqual(A.mixed_rule_segments("ｌ li .Ll⌒L 」 L .」.⌒ '」 ,l l .l マっ！"), [])

    def test_real_dialogue_after_symbol_still_claimed(self):
        """回归: 台词里夹全角数字/句尾装饰符时不能被判掉（上一版"边界判据"的坑）。"""
        line = "　丶:: ::i:::し-=‐::　:::::　 ! 'i,:.:.:.:.:イ　　今はそれよりマーク１の鈍足問題を解決したい。"
        texts = [line[s["start"]:s["end"]] for s in A.mixed_rule_segments(line) if s["kind"] == "text"]
        self.assertTrue(texts)
        joined = "".join(texts)
        self.assertIn("今はそれよりマーク", joined)      # 全角数字 １ 断了 run, 两段都要抠出来
        self.assertIn("鈍足問題を解決したい", joined)

    def test_rule_segments_carry_flag(self):
        segs = A.mixed_rule_segments("┌────┐　今日はいい天気ですね　│￣￣│")
        texts = [s for s in segs if s["kind"] == "text"]
        self.assertTrue(texts)
        self.assertTrue(all(s.get("rule") is True for s in texts))

    def test_rule_seg_copy_fail(self):
        self.assertTrue(A._rule_seg_copy_fail("マっ", "マっ"))            # 照抄 → 漏译
        self.assertTrue(A._rule_seg_copy_fail("ヽへノ", "ヽへノ"))
        self.assertFalse(A._rule_seg_copy_fail("こんにちは", "你好"))      # 译出 → 过
        self.assertFalse(A._rule_seg_copy_fail("こんにちは", "你好 こんにちは"))  # 部分照抄交给残留口径
        self.assertFalse(A._rule_seg_copy_fail("ゾルダート", "ゾルダート", names={"ゾルダート"}))  # 专名豁免

    def test_real_chapter_accident_lines(self):
        """真章节: 两起事故行必须不再被抠成段（回退整行协议）。"""
        d = os.path.join(SCRIPTS, "..", "aaManga", "やる夫のハーレム大王国")
        cases = (("entry_25417", 605), ("entry_25418", 1044), ("entry_25418", 1081))
        checked = 0
        for base, ln in cases:
            fp = os.path.join(d, "aa", base + ".txt")
            if not os.path.exists(fp):
                continue
            checked += 1
            line = open(fp, encoding="utf-8").read().split("\n")[ln - 1]
            texts = [s for s in A.mixed_rule_segments(line) if s["kind"] == "text"]
            self.assertEqual(texts, [], f"{base}#{ln} 不该抠出台词段")
        if not checked:
            self.skipTest("样本章节不存在")


if __name__ == "__main__":
    unittest.main()
