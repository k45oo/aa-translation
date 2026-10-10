# -*- coding: utf-8 -*-
"""段级艺术判据（P1-1，2026-10-10）：按作品语料标定的艺术假名表 + Rule A/B 回归测试。

背景：`work/edits.jsonl` 1104 条人工修正里 **185 处**是"艺术笔画被译成中文"（`ア`→啊、
`ノイ`→不要、`キト`→基特、`笏外`→抱歉）；根因是 `ART_HIRA`/`ART_KATA` 写死表落后于本作语料
（本作用 ア/イ/キ/ト/フ/ニ 与 借字汉字 当笔画）。P1-1 把判据换成**按作品语料统计**的表：

  · Rule A `seg_is_art`         → 选段层：段内无可译内容 → 本地照抄、不进模型
  · Rule B `art_keep_violation` → 回滚层：表内艺术假名在译文里丢失 → 该段回滚照抄/重试

本测试用**合成表**（写进临时文件后 `set_table_path`），不依赖任何作品语料；
真实语料的拦截率/误杀率曲线见 doc/方案归档/段级艺术判据_2026-10-10.md。

运行：.venv/Scripts/python.exe -m unittest discover -s scripts/tests -p "test_art_kana.py"
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS_DIR)
for _p in ("common", "works", "tools", "fix"):
    sys.path.insert(0, os.path.join(SCRIPTS, _p))

import aa_art_kana as AK          # noqa: E402
import aa_translate as T          # noqa: E402

# 合成表：纯艺术字符（1.0）/ 借字（0.95）/ 真台词字符（0.0~0.2）
FAKE = {
    "ア": 1.0, "イ": 1.0, "ノ": 1.0, "ニ": 0.99, "ﾆ": 1.0, "ト": 0.95, "フ": 1.0,
    "ヾ": 1.0, "キ": 0.17, "サ": 0.06, "テ": 0.44, "レ": 0.83, "ー": 0.79,
    # 借字汉字（只出现在画里）
    "笏": 1.0, "仍": 0.97, "芹": 1.0, "竿": 0.95,
    # 真台词汉字/假名
    "私": 0.0, "流": 0.02, "石": 0.01, "そ": 0.0, "れ": 0.0, "は": 0.01, "ふ": 0.0,
}


class _TmpTable(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="artkana_")
        self.tbl = os.path.join(self.dir, "art_kana_test.json")
        with io.open(self.tbl, "w", encoding="utf-8") as f:
            json.dump({"schema": 1, "kind": "test", "ratios": FAKE,
                       "obs": {k: [20, 0, 0] for k in FAKE}}, f, ensure_ascii=False)
        AK.set_table_path(self.tbl)
        for k in ("AA_ART_KANA_RATIO", "AA_ART_KANA_KEEP", "AA_ART_KANA_KEEP_RATIO"):
            os.environ.pop(k, None)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        AK.set_table_path(None)
        for k in ("AA_ART_KANA_RATIO", "AA_ART_KANA_KEEP", "AA_ART_KANA_KEEP_RATIO"):
            os.environ.pop(k, None)


class TestTable(_TmpTable):
    def test_art_kana_by_table(self):
        self.assertTrue(AK.is_art_kana("ア"))
        self.assertTrue(AK.is_art_kana("ニ"))          # 0.99 ≥ 0.8
        self.assertFalse(AK.is_art_kana("キ"))         # 0.17 = 台词假名
        self.assertTrue(AK.is_art_kana("ー"))          # 0.79 不够"台词"（≤0.15 才推翻）→ 写死表说了算
        self.assertTrue(AK.is_art_kana("ノ"))          # 写死表也含

    def test_override_builtin_needs_strong_evidence(self):
        """表要推翻写死表（艺术 → 台词）必须 ratio ≤0.15 且观测 ≥200。"""
        tbl = os.path.join(self.dir, "art_kana_override.json")
        ratios = {"ふ": 0.02, "マ": 0.79, "ア": 0.9}
        obs = {"ふ": [400, 0, 0], "マ": [900, 0, 0], "ア": [900, 0, 0]}
        with io.open(tbl, "w", encoding="utf-8") as f:
            json.dump({"schema": 1, "ratios": ratios, "obs": obs}, f, ensure_ascii=False)
        AK.set_table_path(tbl)
        self.assertFalse(AK.is_art_kana("ふ"))         # 0.02 + 400 行 → 明确是台词
        self.assertTrue(AK.is_art_kana("マ"))          # 0.79 不是"明确台词" → 写死表 art 生效
        self.assertTrue(AK.is_art_kana("ア"))          # ≥0.8 → 表直接判 art

    def test_override_needs_min_obs(self):
        """观测不够（<200）时不许推翻写死表——别的作品的小样本不能改全局口径。"""
        tbl = os.path.join(self.dir, "art_kana_thin.json")
        with io.open(tbl, "w", encoding="utf-8") as f:
            json.dump({"schema": 1, "ratios": {"ふ": 0.0}, "obs": {"ふ": [30, 0, 0]}},
                      f, ensure_ascii=False)
        AK.set_table_path(tbl)
        self.assertTrue(AK.is_art_kana("ふ"))

    def test_art_kanji_by_table(self):
        self.assertTrue(AK.is_art_kanji("笏"))         # 1.0 ≥ 0.9（借字阈值）
        self.assertTrue(AK.is_art_kanji("仍"))         # 0.97
        self.assertFalse(AK.is_art_kanji("私"))        # 真台词汉字
        self.assertFalse(AK.is_art_kanji("レ"))        # 非汉字 → False

    def test_decisive_kana(self):
        self.assertEqual(AK.decisive_kana("キト"), ["キ"])
        self.assertEqual(AK.decisive_kana("アニ"), [])
        self.assertEqual(AK.decisive_kana("それは"), ["そ", "れ", "は"])

    def test_missing_table_falls_back_to_builtin(self):
        AK.set_table_path("")                          # 无表
        self.assertTrue(AK.is_art_kana("ノ"))          # 写死表兜底
        self.assertFalse(AK.is_art_kana("ア"))         # 写死表里没有 → 退回"非艺术"


class TestRuleA(_TmpTable):
    """Rule A：段是不是"没有可译内容"。"""

    def test_art_fragments_are_art(self):
        for s in ("ア", "ノイ", "ニニ", "ﾆア", "笏外", "芹仍", "＜米フ", "ア〃", "xぅ笏外"):
            self.assertTrue(AK.seg_is_art(s), s)

    def test_real_dialogue_is_not_art(self):
        for s in ("それは流石にーー", "私の名前は", "キト", "ふふ、ふふふ", "夢？",
                  "AV最高っ！！", "死刑っ！！", "エリス"):
            self.assertFalse(AK.seg_is_art(s), s)

    def test_punctuation_and_hiragana_protect(self):
        self.assertFalse(AK.seg_is_art("アニ！"))       # 句末标点 → 真台词保护
        self.assertFalse(AK.seg_is_art("アニふふ"))     # ≥2 平假名 → 真台词保护

    def test_sfx_halfwidth_not_shortcut(self):
        self.assertFalse(AK.seg_is_art("ｸﾝｸﾝ"))        # 半角拟声要译（加 sfx 样式）

    def test_hetakana_idd_mark_not_evidence(self):
        """`ゝへ`（叠字 + 艺术假名）是画：叠字不算生产口径的假名证据。"""
        self.assertTrue(AK.seg_is_art("ゝへ"))
        self.assertFalse(T._rule_dialogue_run_ok("ゝへ"))
        self.assertEqual(AK.decisive_kana("ゝへ"), [])


    def test_seg_master_switch(self):
        os.environ["AA_ART_KANA_SEG"] = "0"
        try:
            self.assertFalse(AK.seg_is_art("ニニ"))          # 总开关关 → 不再拦
            self.assertTrue(AK.art_keep_violation("ニニ", "妮妮"))   # Rule B 不受它管
        finally:
            os.environ.pop("AA_ART_KANA_SEG", None)


class TestRuleB(_TmpTable):
    """Rule B：译文的艺术假名守恒。"""

    def test_repeated_art_run_lost(self):
        self.assertTrue(AK.art_keep_violation("ニニニニ", "妮妮妮妮"))
        self.assertTrue(AK.art_keep_violation("ノノ", "诺诺"))

    def test_single_art_lost_with_new_kanji(self):
        self.assertTrue(AK.art_keep_violation("―キト", "―基特"))
        self.assertTrue(AK.art_keep_violation("ア〃", "啊〃"))

    def test_gates(self):
        # ① 有平假名 → 放过（正常台词：ノーカット→无删减 这类同形误判由此排除）
        self.assertFalse(AK.art_keep_violation("のノーカット", "的无删减"))
        # ② 半角假名拟声 → 放过（sfx 约定要译）
        self.assertFalse(AK.art_keep_violation("ｸﾝｸﾝｸﾝ", "嗅嗅嗅"))
        # 关开关 → 一律不判
        os.environ["AA_ART_KANA_KEEP"] = "0"
        self.assertFalse(AK.art_keep_violation("ニニニニ", "妮妮妮妮"))

    def test_kept_chars_no_violation(self):
        self.assertFalse(AK.art_keep_violation("ニニニニ", "ニニニニ"))
        self.assertFalse(AK.art_keep_violation("キト", "キト"))


class TestWiring(_TmpTable):
    """接线：主链选段 / 段级校验 / 旧判据委托。"""

    def _segs(self, line, parts):
        segs, pos = [], 0
        for kind, text in parts:
            segs.append({"start": pos, "end": pos + len(text), "kind": kind,
                         "src_width": T.disp_width(text)})
            pos += len(text)
        assert pos == len(line), (pos, len(line))
        return segs

    def test_sendable_text_segs_skips_art(self):
        line = "|::::|ニニニニ| それは流石に |"
        segs = self._segs(line, [("art", "|::::|"), ("text", "ニニニニ"),
                                 ("art", "| "), ("text", "それは流石に"), ("art", " |")])
        self.assertEqual(T._sendable_text_segs(line, segs), [3])

    def test_rule_dialogue_run_ok_delegates(self):
        self.assertFalse(T._rule_dialogue_run_ok("ニニニ"))    # 艺术 → 无台词
        self.assertTrue(T._rule_dialogue_run_ok("流石私の"))   # 非艺术汉字 → 台词

    def test_mixed_seg_verdict_rule_b_reverts(self):
        """段级校验：译文把艺术假名译成中文 → keep（回滚照抄）/ 有台词证据 → retry。"""
        _v, why, _h = T._mixed_seg_verdict("妮妮妮妮", "ニニニニ", False, set(), 0.35, 0.6)
        self.assertEqual((_v, why), ("keep", "art_keep"))
        _v2, why2, hint2 = T._mixed_seg_verdict("基特", "―キト", False, set(), 0.35, 0.6)
        self.assertEqual(_v2, "retry")
        self.assertEqual(why2, "art_keep")
        self.assertTrue(hint2)

    def test_mixed_seg_verdict_normal_translation_ok(self):
        _v, why, _h = T._mixed_seg_verdict("那也太——", "それは流石にーー", False, set(), 0.35, 0.6)
        self.assertEqual((_v, why), ("ok", ""))

    def test_art_kana_tab_from_env(self):
        os.environ["AA_ART_KANA"] = self.tbl
        try:
            self.assertEqual(T.art_kana_tab().get("ノ"), 1.0)
            self.assertTrue(T.seg_is_art("ノイ"))
        finally:
            os.environ.pop("AA_ART_KANA", None)
            AK.reset_cache()


if __name__ == "__main__":
    unittest.main()
