# -*- coding: utf-8 -*-
"""refine_aa P0/P1/P2（2026-10-03）单元测试。

覆盖：
  P0 批量请求：_parse_block 解析 / _batch_size / _run_jobs_batched 回退语义 /
              整章 batch ON vs OFF 产出逐条一致且调用数更少
  P1 护栏：_term_guard_reason（改掉标准译名 / 改回日文 / 无关编辑放行）、
          _fixed_whitelist（固定表预填行）、_tm_whitelist（TM 复用行）
  P2 艺术假名：_art_kana_only（ミニニニニ / ﾆﾆﾆ / ｶﾞｶﾞｶﾞ vs 真台词）、
              _restore_art_kana（段级还原）

运行：.venv/Scripts/python.exe -m unittest scripts.tests.test_refine_batch_guard -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, ".."))
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "fix")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import refine_aa as R                       # noqa: E402
from line_classify_v2 import segment_line    # noqa: E402

SRC_HEAD = "# テスト章"
SRC_POST = "1 ：作者 ：2020/01/01(水) 00:00:00 ID:abcdef"


class TestParseBlock(unittest.TestCase):
    def test_normal_and_filter(self):
        resp = "00007|你好\n00009|今天天气真好\n00011|第三条"
        self.assertEqual(R._parse_block(resp, {7, 9, 11}),
                         {7: "你好", 9: "今天天气真好", 11: "第三条"})
        self.assertEqual(R._parse_block(resp, {7, 9}), {7: "你好", 9: "今天天气真好"})

    def test_junk_lines_and_variants(self):
        resp = ("这是解释文字，不该收\n"
                "  00005 |  带前后空格  \n"
                "00006|\n"                      # 空译文 → 丢
                "00007|\ttab 译文\n"
                "12|行号不足5位\n")
        got = R._parse_block(resp, {5, 6, 7, 12})
        self.assertEqual(got.get(5), "带前后空格")
        self.assertNotIn(6, got)
        self.assertEqual(got.get(7), "tab 译文")

    def test_duplicate_keeps_first(self):
        self.assertEqual(R._parse_block("00003|第一版\n00003|第二版", {3}), {3: "第一版"})

    def test_empty(self):
        self.assertEqual(R._parse_block("", {1}), {})
        self.assertEqual(R._parse_block(None, {1}), {})


class TestBatchSize(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("AA_REFINE_BATCH", None)

    def test_env_override_and_zero(self):
        os.environ["AA_REFINE_BATCH"] = "7"
        self.assertEqual(R._batch_size(), 7)
        os.environ["AA_REFINE_BATCH"] = "0"
        self.assertEqual(R._batch_size(), 0)         # 关闭批量
        os.environ["AA_REFINE_BATCH"] = "-5"
        self.assertEqual(R._batch_size(), 0)

    def test_invalid_falls_back(self):
        os.environ["AA_REFINE_BATCH"] = "abc"
        self.assertGreaterEqual(R._batch_size(), 1)


class TestRunJobsBatched(unittest.TestCase):
    """块回包解析 + 回退单行语义。"""

    def setUp(self):
        self._call = R._call_glm
        self.calls = []

    def tearDown(self):
        R._call_glm = self._call

    def _mk(self, i, kind="poly", body=None, apply_ret=None, single_ret="SINGLE"):
        return R._Job(i, kind, body or ("原文%d" % i),
                      lambda zh, _i=i: apply_ret if apply_ret is not None else (_i, "apply", zh),
                      lambda _i=i: (_i, "single", single_ret),
                      (i, "fallback", None))

    def test_all_rows_answered_one_call_per_block(self):
        seen = []

        def mock(system, body, **kw):
            seen.append(body)
            return "\n".join("%05d|译%d" % (int(m.group(1)), int(m.group(1)))
                             for m in __import__("re").finditer(r"^(\d{5})\|", body, __import__("re").M))
        R._call_glm = mock
        jobs = [self._mk(i) for i in (1, 2, 3)]
        out = R._run_jobs_batched(jobs, 4, chunk=10)
        self.assertEqual(len(seen), 1, "3 行同块 → 恰 1 次调用")
        self.assertEqual(set(out), {1, 2, 3})
        self.assertTrue(all(out[i][1] == "apply" for i in out))
        self.assertTrue(all(out[i][2].startswith("译") for i in out))

    def test_missing_row_falls_back_to_single(self):
        R._call_glm = lambda system, body, **kw: "00002|只有第二条"
        out = R._run_jobs_batched([self._mk(1), self._mk(2)], 1, chunk=10)
        self.assertEqual(out[2][1], "apply")
        self.assertEqual(out[1][1], "single", "漏行必须回退单行路径")

    def test_apply_none_falls_back_to_single(self):
        R._call_glm = lambda system, body, **kw: "00001|回包可用"
        j1 = R._Job(1, "swallow", "原文", lambda zh: None, lambda: (1, "single", None), None)
        out = R._run_jobs_batched([j1], 1, chunk=10)
        self.assertEqual(out[1][1], "single")

    def test_block_exception_falls_back_all_rows(self):
        def boom(system, body, **kw):
            raise RuntimeError("API 挂了")
        R._call_glm = boom
        jobs = [self._mk(i) for i in (1, 2)]
        out = R._run_jobs_batched(jobs, 2, chunk=10)
        self.assertEqual(sorted(out), [1, 2])
        self.assertTrue(all(out[i][1] == "single" for i in out))

    def test_kinds_split_into_different_blocks(self):
        seen = []
        R._call_glm = lambda system, body, **kw: (
            seen.append(("批量模式" in system, body)) or
            "\n".join("%05d|译" % int(m.group(1))
                      for m in __import__("re").finditer(r"^(\d{5})\|", body, __import__("re").M)))
        jobs = [self._mk(1, "poly"), self._mk(2, "swallow"), self._mk(3, "poly")]
        R._run_jobs_batched(jobs, 1, chunk=10)
        self.assertEqual(len(seen), 2, "poly/swallow 各自成块")
        self.assertEqual(sum(1 for _ in seen), 2)


class TestArtKanaOnly(unittest.TestCase):
    def test_art_kana_segments(self):
        for b in ("ミニニニニ", "ﾆﾆﾆ", "ｶﾞｶﾞｶﾞ", "ィニニニ", "ぅこ", "ヽへノ", "ノし"):
            self.assertTrue(R._art_kana_only(b), "应判为艺术假名: %r" % b)

    def test_real_dialogue_not_art(self):
        for b in ("こんにちは", "ありがとう", "おはようございます", "やる夫", "行くぞ",
                  "ハクタイ", "ﾊｧﾊｧ"):
            self.assertFalse(R._art_kana_only(b), "不该判成艺术: %r" % b)

    def test_non_kana_never_art(self):
        for b in ("你好", "1234", "", "…！？"):
            self.assertFalse(R._art_kana_only(b))


class TestRestoreArtKana(unittest.TestCase):
    def test_restores_translated_art_segment(self):
        s = "│●│ﾆﾆﾆ│●│"
        h = "│●│你你你哈│●│"
        out = R._restore_art_kana(s, h, segment_line(s, treat_hankaku_as_text=True))
        self.assertIsNotNone(out)
        self.assertIn("ﾆﾆﾆ", out)
        self.assertNotIn("你你你哈", out)

    def test_keeps_line_when_segment_not_translated(self):
        s = "│●│ﾆﾆﾆ│●│"
        self.assertIsNone(R._restore_art_kana(s, s, segment_line(s, treat_hankaku_as_text=True)))

    def test_half_translated_segment_untouched(self):
        s = "│●│ﾆﾆﾆ│●│"
        h = "│●│ﾆ你哈│●│"        # 仍留假名 = 半译 → 不还原（不误伤）
        self.assertIsNone(R._restore_art_kana(s, h, segment_line(s, treat_hankaku_as_text=True)))


class TestTermGuard(unittest.TestCase):
    PAIRS = [{"jp": "ハクタイ", "zh": "白岱"}, {"jp": "re:^(シンオウ)$", "zh": "神奥"}]

    def test_blocks_dropped_name(self):
        r = R._term_guard_reason("　白岱のジムはどこ？", "　训练馆在哪里？", self.PAIRS)
        self.assertIn("白岱", r)

    def test_blocks_reverted_to_japanese(self):
        r = R._term_guard_reason("　白岱のジム", "　ハクタイのジム", self.PAIRS)
        self.assertIn("日文", r)

    def test_allows_unrelated_edit(self):
        self.assertEqual(R._term_guard_reason("　你好世界", "　你好，世界", self.PAIRS), "")

    def test_allows_when_name_absent(self):
        self.assertEqual(R._term_guard_reason("　どこ？", "　在哪里？", self.PAIRS), "")

    def test_allows_kept_name(self):
        self.assertEqual(R._term_guard_reason("　白岱のジム", "　白岱的道馆", self.PAIRS), "")

    def test_no_pairs_never_blocks(self):
        self.assertEqual(R._term_guard_reason("　白岱のジム", "　ハクタイのジム", []), "")


class TestWhitelistUnittest(unittest.TestCase):
    """白名单：固定表预填行 / TM 复用行（都已是定稿口径）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="refine_wl_")
        self.work_dir = self.tmp
        self.src = ["# t", "　こんにちは", "　ありがとう", "　さようなら"]
        self.tr = ["# t", "　你好", "　谢谢", "　再见"]
        self._pop = dict(os.environ)
        os.environ["AA_USE_TM"] = "1"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._pop)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_tm_whitelist_hits_only_verbatim_rows(self):
        import aa_tm
        tm = {aa_tm.key("　こんにちは"): {"zh": "　你好"},
              aa_tm.key("　ありがとう"): {"zh": "　谢谢！"}}   # 与当前汉化不同 → 不算复用
        with open(os.path.join(self.work_dir, "tm_w.json"), "w", encoding="utf-8") as f:
            json.dump(tm, f, ensure_ascii=False)
        got = R._tm_whitelist("w", self.work_dir, self.src, self.tr)
        self.assertEqual(got, {1})

    def test_tm_whitelist_empty_without_table(self):
        self.assertEqual(R._tm_whitelist("w", self.work_dir, self.src, self.tr), set())

    def test_fixed_whitelist_uses_table(self):
        import pokemon_fixed as PF
        orig = (PF.enabled, PF.load, PF.match_copy, PF.match_whole, PF.match_struct)
        PF.enabled = lambda: True
        PF.load = lambda w, d=None: {"whole": {}}
        PF.match_copy = lambda s, t: None
        PF.match_whole = lambda s, t: ({"zh": "　谢谢"} if s.startswith("　あり") else None)
        PF.match_struct = lambda s, t, terms=None: None
        try:
            got = R._fixed_whitelist("w", self.work_dir, self.src, self.tr)
            self.assertEqual(got, {2})           # 第 2 行译文与表口径一致 → 预填行
        finally:
            (PF.enabled, PF.load, PF.match_copy, PF.match_whole, PF.match_struct) = orig


class TestChapterBatchEquivalence(unittest.TestCase):
    """整章：批量 ON 与单行 OFF 产出必须逐条一致，且调用数更少。

    另含 P2 艺术假名还原、P1 词表护栏的端到端触发。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="refine_batch_")
        self.aa = os.path.join(self.tmp, "aa")
        self.out = os.path.join(self.tmp, "汉化")
        self.work = os.path.join(self.tmp, "work")
        for d in (self.aa, self.out, self.work, os.path.join(self.work, "line_meta_v2")):
            os.makedirs(d, exist_ok=True)
        self.base = "entry_1"
        lines = [SRC_HEAD, SRC_POST]
        lines += ["　今日はいい天気ですね"] * 60                 # 纯 text → poly job
        lines += ["│●│ﾆﾆﾆ│●│"]                                  # 艺术假名（汉化被译坏）
        lines += ["│●│白岱のジムはどこ│●│"]                      # 词表护栏样本（mixed 段级补译）
        body = "\n".join(lines)
        with open(os.path.join(self.aa, self.base + ".txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        tr = list(lines)
        tr[62] = "│●│你你你哈│●│"                                # 艺术段被译成中文
        tr[63] = "│●│白岱のジムはどこ│●│"                          # 段仍日文 → 触发段级补译 job
        with open(os.path.join(self.out, self.base + "_汉化.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(tr))
        with open(os.path.join(self.work, "line_meta_v2", self.base + ".json"), "w", encoding="utf-8") as f:
            json.dump({}, f)
        self.cfg = {"aa_dir": self.aa, "out_dir": self.out, "work_dir": self.work,
                    "work": "w"}
        self._call = R._call_glm
        self._load = R._load_gloss_hint
        R._load_gloss_hint = lambda cfg, base: ""
        self.n_calls = 0

        def mock(system, body, **kw):
            self.n_calls += 1
            if "批量模式" in (system or ""):
                out = []
                for m in __import__("re").finditer(r"^(\d{5})\|(.*)$", body, __import__("re").M):
                    out.append("%s|%s" % (m.group(1), self._zh(m.group(2))))
                return "\n".join(out)
            return self._zh(body)
        R._call_glm = mock
        self._pairs = R._term_pairs if hasattr(R, "_term_pairs") else None
        self._pt = None

    def _zh(self, src):
        if "白岱" in src:
            return "　训练馆在哪里？"        # 丢掉标准译名 → 护栏应拦下
        return "　今天天气真好啊"

    def tearDown(self):
        R._call_glm, R._load_gloss_hint = self._call, self._load
        os.environ.pop("AA_REFINE_BATCH", None)
        os.environ.pop("AA_REFINE_TERM_GUARD", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, batch, guard=1):
        os.environ["AA_REFINE_BATCH"] = str(batch)
        os.environ["AA_REFINE_TERM_GUARD"] = str(guard)
        os.environ["AA_REFINE_WORKERS"] = "4"
        self.n_calls = 0
        b, stats, edits = R.refine_chapter(self.cfg, self.base)
        return stats, edits, self.n_calls

    def test_batch_matches_single_and_saves_calls(self):
        # 无术语表（本测试 work="w" 无表）→ 护栏无对可查，只验批量等价
        st_on, ed_on, n_on = self._run(50)
        st_off, ed_off, n_off = self._run(0)
        self.assertEqual([e[:1] + e[4:] for e in ed_on], [e[:1] + e[4:] for e in ed_off],
                         "批量与单行的 edits 必须逐条一致")
        self.assertGreaterEqual(len(ed_on), 60)
        self.assertLess(n_on, n_off, "批量调用数必须更少")
        self.assertGreaterEqual(n_off, 20, "单行路径应逐行调用（样本足够大）")
        # P2：艺术假名段被还原成原文
        art = [e for e in ed_on if e[6] == "art_kana_restore"]
        self.assertTrue(art, "应有 art_kana_restore 编辑")
        self.assertIn("ﾆﾆﾆ", art[0][3])
        self.assertEqual(st_on["art_kana_restore"], st_off["art_kana_restore"], "两臂还原数一致")

    def test_term_guard_blocks_end_to_end(self):
        import pre_terms as PT
        orig = PT.pairs_for
        PT.pairs_for = lambda base, work=None, **kw: [{"jp": "ハクタイ", "zh": "白岱"}]
        try:
            st_on, ed_on, _n = self._run(50, guard=1)
            st_off, ed_off, _n2 = self._run(50, guard=0)
        finally:
            PT.pairs_for = orig
        idx = [e for e in ed_off if "白岱" not in (e[3] or "") and "训练馆" in (e[3] or "")]
        self.assertTrue(idx, "护栏关闭时该行应被改成丢掉标准译名的版本")
        self.assertTrue(all("白岱" in (e[3] or "") for e in ed_on if "训练馆" in (e[3] or "")),
                        "护栏开启时不得写回丢掉标准译名的译文")
        self.assertEqual(st_on["term_guard_rejected"], 1)
        self.assertEqual(st_off["term_guard_rejected"], 0)


if __name__ == "__main__":
    unittest.main()
