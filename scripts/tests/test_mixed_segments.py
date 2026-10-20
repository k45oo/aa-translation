# -*- coding: utf-8 -*-
"""P0 段级翻译测试（2026-08-18 代码改进测试升级）。

覆盖（aa_translate.py 的 mixed 段级翻译路径，AA_USE_MIXED_SEGMENTS 默认开，全部作品在用）：
- load_meta_segments_0based：v3 meta segments 加载（0-based、坏文件回退 None）
- _confident_text_segs：conf>=0.65 的 text 段下标
- pad_segment：B3 段尾补 U+3000（不改变 art 段列位置）
- rebuild_mixed_line：B3 行首空白保护 + 段尾 pad，整行宽 == SRC
- translate_lines_mixed：mock call_api，覆盖合格/照抄/残留/超宽/段宽/吞字 → fixed/missed
- fix_width_mixed_lines：mock call_api，覆盖超宽修复成功/失败落 manual

零 API（全部 mock call_api）。写操作在临时目录。
"""
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS_DIR)
sys.path.insert(0, os.path.join(SCRIPTS, "common"))
sys.path.insert(0, os.path.join(SCRIPTS, "works"))

import aa_translate as T


def make_segs(*parts):
    """构造 v3 segments 列表。parts: [(kind, text, confidence)]，自动算 start/end/src_width。"""
    segs = []
    pos = 0
    for kind, text, conf in parts:
        w = T.disp_width(text)
        segs.append({"start": pos, "end": pos + len(text), "kind": kind,
                     "src_width": w, "confidence": conf})
        pos += len(text)
    return segs


def _read_leftover(work_dir):
    with open(os.path.join(work_dir, "mixed_leftover.txt"), encoding="utf-8") as f:
        return f.read()


class _temp_work_dir:
    """临时 WORK_DIR（段级残留清单 mixed_leftover.txt 的落盘目标）——防测试写进仓库根。"""

    def __enter__(self):
        self._old = T.WORK_DIR
        self.path = tempfile.mkdtemp(prefix="hermes-test-mseg-work-")
        T.WORK_DIR = self.path
        return self.path

    def __exit__(self, *exc):
        T.WORK_DIR = self._old
        shutil.rmtree(self.path, ignore_errors=True)
        return False


class TestLoadMetaSegments0based(unittest.TestCase):
    """v3 meta segments 加载（与 load_meta_0based 同款 1-based → 0-based 口径）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hermes-test-mseg-")
        self.dir = os.path.join(self.tmp, "line_meta_v2")
        os.makedirs(self.dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, raw):
        with open(os.path.join(self.dir, "entry_1.json"), "w", encoding="utf-8") as f:
            json.dump(raw, f)

    def test_mixed_line_segments_loaded(self):
        segs = make_segs(("art", "　", 1.0), ("text", "こんにちは", 1.0), ("art", "｜", 1.0))
        self._write({"3": {"kind": "mixed", "segments": segs}})
        m = T.load_meta_segments_0based(self.dir, "entry_1")
        # key "3" (1-based) → idx 2 (0-based)
        self.assertIn(2, m)
        self.assertEqual(len(m[2]), 3)

    def test_no_segments_skipped(self):
        self._write({"3": {"kind": "text"}, "4": {"kind": "mixed", "segments": []}})
        m = T.load_meta_segments_0based(self.dir, "entry_1")
        self.assertEqual(m, {})

    def test_non_dict_value_skipped(self):
        self._write({"3": "text", "4": {"kind": "mixed", "segments": make_segs(("text", "こんにちは", 1.0))}})
        m = T.load_meta_segments_0based(self.dir, "entry_1")
        self.assertNotIn(2, m)
        self.assertIn(3, m)

    def test_bad_key_skipped(self):
        self._write({"abc": {"kind": "mixed", "segments": make_segs(("text", "こんにちは", 1.0))},
                     "0": {"kind": "mixed", "segments": make_segs(("text", "こんにちは", 1.0))}})
        m = T.load_meta_segments_0based(self.dir, "entry_1")
        self.assertNotIn(-1, m)

    def test_missing_file_none(self):
        self.assertIsNone(T.load_meta_segments_0based(self.dir, "nope"))

    def test_corrupt_file_none(self):
        with open(os.path.join(self.dir, "entry_1.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertIsNone(T.load_meta_segments_0based(self.dir, "entry_1"))


class TestConfidentTextSegs(unittest.TestCase):
    def test_high_conf_text_included(self):
        segs = make_segs(("art", "　", 1.0), ("text", "こんにちは", 0.9), ("text", "おはよう", 0.7))
        self.assertEqual(T._confident_text_segs(segs), [1, 2])

    def test_low_conf_text_excluded(self):
        # 低置信（装饰假名）不送模型
        segs = make_segs(("art", "　", 1.0), ("text", "ヽ", 0.5), ("text", "こんにちは", 0.9))
        self.assertEqual(T._confident_text_segs(segs), [2])

    def test_art_never_included(self):
        segs = make_segs(("art", "｜", 1.0), ("art", "　", 1.0))
        self.assertEqual(T._confident_text_segs(segs), [])

    def test_missing_conf_defaults_high(self):
        # v3 meta 仅低置信段带 confidence，缺省视为高置信
        segs = [{"start": 0, "end": 4, "kind": "text", "src_width": 8}]
        self.assertEqual(T._confident_text_segs(segs), [0])


class TestPadSegment(unittest.TestCase):
    def test_pad_fullwidth(self):
        # 差 2 格 → 补 1 个 U+3000
        self.assertEqual(T.pad_segment("你好", 6), "你好\u3000")

    def test_pad_odd(self):
        # 差 3 格：全角×1 + 半角×1（如 "ab" 宽 2 → target 5）
        self.assertEqual(T.pad_segment("ab", 5), "ab\u3000 ")
        # 差 1 格：半角空格
        self.assertEqual(T.pad_segment("a", 2), "a ")
        # 差 4 格（偶数）：全角×2
        self.assertEqual(T.pad_segment("a", 5), "a\u3000\u3000")

    def test_no_pad_when_wide_enough(self):
        self.assertEqual(T.pad_segment("你好世界", 4), "你好世界")

    def test_no_pad_equal(self):
        self.assertEqual(T.pad_segment("你好", 4), "你好")


class TestRebuildMixedLine(unittest.TestCase):
    """B3 行首空白保护 + 段尾 pad：art 段列位置不变、整行宽 == SRC。"""

    def test_art_segments_byte_preserved(self):
        # text-art-text-art：art 段必须逐字节保留（按 src_width 列位置验证）
        # 注意：segments 必须覆盖整行无空隙（行尾 art 段不能漏）
        line = "　こんにちは｜世界　"
        segs = make_segs(("art", "　", 1.0), ("text", "こんにちは", 0.9), ("art", "｜", 1.0),
                         ("text", "世界", 0.9), ("art", "　", 1.0))
        out = T.rebuild_mixed_line(line, segs, {1: "你好", 3: "世界"})
        self.assertIsNotNone(out)
        self.assertIn("｜", out)               # art 段字节保留（框线在）
        self.assertNotIn("こんにちは", out)     # 原文台词被替换
        self.assertEqual(T.disp_width(out), T.disp_width(line))

    def test_lead_whitespace_preserved(self):
        # 行首空白保护：text 段在源行中的行首 U+3000 必须保留（曾丢空格 bug）
        line = "　　「こんにちは」｜"
        segs = make_segs(("text", "　　「こんにちは」", 0.9), ("art", "｜", 1.0))
        out = T.rebuild_mixed_line(line, segs, {0: "你好"})
        self.assertIsNotNone(out)
        self.assertTrue(out.startswith("　　"))  # lead 保留
        self.assertEqual(T.disp_width(out), T.disp_width(line))

    def test_model_lead_stripped(self):
        # 模型偶发保留行首空白 → strip 防双份
        # 源段 "　こんにちは" 宽 16，lead 1 格；译文 "　你好" strip 后 "你好" 宽 4
        # → pad 补 16-2-4=10 格（5×U+3000）→ 总 U+3000 = lead 1 + pad 5 = 6
        line = "　こんにちは｜"
        segs = make_segs(("text", "　こんにちは", 0.9), ("art", "｜", 1.0))
        out = T.rebuild_mixed_line(line, segs, {0: "　你好"})
        self.assertIsNotNone(out)
        self.assertTrue(out.startswith("　你好"))   # lead + 译文，无双份空白
        self.assertEqual(T.disp_width(out), T.disp_width(line))
        # 模型给了行首空白 + 本地 lead → 只保留一份（startswith 已验证）

    def test_missing_translation_returns_none(self):
        line = "こんにちは｜世界"
        segs = make_segs(("text", "こんにちは", 0.9), ("art", "｜", 1.0), ("text", "世界", 0.9))
        self.assertIsNone(T.rebuild_mixed_line(line, segs, {0: "你好"}))

    def test_lead_kanji_segment_rebuilt(self):
        """首文字漏译修复的端到端形态：真实 segments（segment_line 产物）里行首 ART_KANJI
        汉字『一』已在 text 段内 → 模型译文进来后整行宽 == SRC，且框线/缩进列位不变。
        旧 segments（art『一』）时 一 不进模型，译文里留着日文 一。"""
        sys.path.insert(0, os.path.join(SCRIPTS, "tools"))
        import line_classify_v2 as LC
        line = "　┤一昨日の話だ├"
        segs = LC.segment_line(line)
        ti = [i for i, s in enumerate(segs) if s["kind"] == "text"]
        self.assertEqual(len(ti), 1)
        self.assertTrue(line[segs[ti[0]]["start"]:segs[ti[0]]["end"]].startswith("一"))
        out = T.rebuild_mixed_line(line, segs, {ti[0]: "前天的事"})
        self.assertIsNotNone(out)
        self.assertNotIn("一昨日", out)
        self.assertTrue(out.startswith("　┤"))
        self.assertTrue(out.endswith("├"))
        self.assertEqual(T.disp_width(out), T.disp_width(line))


class TestTranslateLinesMixed(unittest.TestCase):
    """mock call_api 驱动 translate_lines_mixed，覆盖模型顽疾检出。"""

    def setUp(self):
        self.fake = _FakeAPI()
        self.orig = T.call_api
        T.call_api = self.fake.call_api
        # 段级残留清单落盘目标（防测试写进仓库根）
        self._wd_old = T.WORK_DIR
        self._wd = tempfile.mkdtemp(prefix="hermes-test-mseg-work-")
        T.WORK_DIR = self._wd
        # 真实行：text-art-text，art 段含框线
        self.line = "　こんにちは世界｜"
        self.segs = make_segs(("text", "　こんにちは世界", 0.9), ("art", "｜", 1.0))
        self.flagged = [(0, self.line, self.segs)]

    def tearDown(self):
        T.call_api = self.orig
        T.WORK_DIR = self._wd_old
        shutil.rmtree(self._wd, ignore_errors=True)

    def test_good_translation_fixed(self):
        # 译文宽度接近原段（差 ≤4 格）→ 段级校验通过 → fixed
        # 原段 "　こんにちは世界" 宽 16，译文需 12~16 格
        self.fake.responses = ["00000_0|你好呀你好呀"]   # 12 格，差 4（边界内）
        fixed, missed, warns = T.translate_lines_mixed(self.flagged)
        self.assertIn(0, fixed)
        self.assertEqual(missed, [])
        self.assertEqual(T.disp_width(fixed[0]), T.disp_width(self.line))

    def test_copy_jp_detected(self):
        # 模型原样照抄**真台词**段 → 判失败；3 轮仍照抄 → 末轮「接受部分成功」：
        # 行照常产出（该段退回原文，art/宽度不变）**且**计入 missed（调用方落 review_inline，不静默）
        orig = self.line[self.segs[0]['start']:self.segs[0]['end']]
        self.fake.responses = [f"00000_0|{orig}"]
        with _temp_work_dir() as wd:
            fixed, missed, warns = T.translate_lines_mixed(self.flagged)
            left = _read_leftover(wd)
        self.assertEqual(fixed[0], self.line)      # 段退原文 → 与原文逐字节相同
        self.assertEqual(missed, [0])              # 真台词没译出来 → 仍报（可见）
        self.assertIn("revert", left)

    def test_decorative_seg_copy_accepted(self):
        # 纯装饰段（ヽ 无假名无汉字）模型原样返回是正确行为 → 接受
        line = "ヽ｜"
        segs = make_segs(("text", "ヽ", 0.5), ("art", "｜", 1.0))   # 低置信 → 不送模型
        fixed, missed, warns = T.translate_lines_mixed([(0, line, segs)])
        # 无 conf≥0.65 text 段 → 无 jobs → 直接 missed（回退整行旧逻辑）
        self.assertEqual(missed, [0])

    # ---- 2026-10-10 改版新增（doc/方案归档/段级协议漏译根因与优化方案_2026-10-10.md） ----

    def test_long_legit_translation_not_shrink_rejected(self):
        # 旧口径：原段宽 22、译文 12（差 10 > 4 列）→ 误判「吞字」整行丢。
        # 新口径：比 0.55 ≥ MIN_RATIO(0.35) → 接受（中文天生比日文短）。
        line = "　そんな事言ってる内に…｜"
        segs = make_segs(("text", "　そんな事言ってる内に…", 0.9), (("art", "｜", 1.0)))
        self.fake.responses = ["00000_0|说着说着……"]
        fixed, missed, warns = T.translate_lines_mixed([(0, line, segs)])
        self.assertIn(0, fixed)
        self.assertEqual(missed, [])
        self.assertEqual(T.disp_width(fixed[0]), T.disp_width(line))

    def test_catastrophic_shrink_still_rejected_then_reverted(self):
        # 比 < 0.35（真丢内容的极端）→ 仍判失败；3 轮不改 → 末轮退原文 + 计入 missed + 记清单
        line = "　こんにちは世界｜"
        segs = make_segs(("text", "　こんにちは世界", 0.9), (("art", "｜", 1.0)))
        self.fake.responses = ["00000_0|好"]          # 比 2/16 = 0.125
        with _temp_work_dir() as wd:
            fixed, missed, warns = T.translate_lines_mixed([(0, line, segs)])
            left = _read_leftover(wd)
        self.assertEqual(fixed[0], line)
        self.assertEqual(missed, [0])
        self.assertIn("revert", left)

    def test_art_fragment_copy_keeps_line_and_not_reported(self):
        # 单字画面纹理段（凹：1 汉字，无决定性台词证据）照抄 = 正确行为 → 行产出、不报 missed
        line = "凹｜"
        segs = make_segs(("text", "凹", 0.9), (("art", "｜", 1.0)))
        self.fake.responses = ["00000_0|凹"]
        fixed, missed, warns = T.translate_lines_mixed([(0, line, segs)])
        self.assertEqual(fixed.get(0), line)
        self.assertEqual(missed, [])

    def test_multi_kanji_art_fragment_reported_but_line_kept(self):
        # 已知局限：多汉字笔画段（斧竿丐）与真台词在字符层无法区分（都不是装饰汉字、无重复字）
        # → 仍按"疑似漏译"重试并报 missed；但**行照常产出**（不整行丢），只留下列表供人工复核。
        line = "斧竿丐｜"
        segs = make_segs(("text", "斧竿丐", 0.9), (("art", "｜", 1.0)))
        self.fake.responses = ["00000_0|斧竿丐"]
        fixed, missed, warns = T.translate_lines_mixed([(0, line, segs)])
        self.assertEqual(fixed.get(0), line)
        self.assertEqual(missed, [0])

    def test_partial_accept_translates_other_segs(self):
        # 多段行：真台词段照抄（失败）→ 退原文；另一段正常译出 → 行仍产出
        line = "　こんにちは｜また明日"
        segs = make_segs((("art", "　", 1.0)),
                         (("text", "こんにちは", 0.9)),
                         (("art", "｜", 1.0)),
                         (("text", "また明日", 0.9)))
        self.fake.responses = ["00001_1|こんにちは\n00001_3|明天见"]
        fixed, missed, warns = T.translate_lines_mixed([(1, line, segs)])
        self.assertIn(1, fixed)
        self.assertIn("明天见", fixed[1])
        self.assertIn("こんにちは", fixed[1])          # 退原文段保持字节不变
        self.assertEqual(missed, [1])
        self.assertEqual(T.disp_width(fixed[1]), T.disp_width(line))

    def test_partial_disabled_restores_old_behaviour(self):
        # AA_MIXED_PARTIAL=0 → 回旧行为：整行丢（fixed 空、missed 报）
        line = "斧竿丐｜"
        segs = make_segs(("text", "斧竿丐", 0.9), (("art", "｜", 1.0)))
        self.fake.responses = ["00000_0|斧竿丐"]
        os.environ["AA_MIXED_PARTIAL"] = "0"
        try:
            fixed, missed, warns = T.translate_lines_mixed([(0, line, segs)])
        finally:
            os.environ.pop("AA_MIXED_PARTIAL", None)
        self.assertEqual(fixed, {})
        self.assertEqual(missed, [0])

    def test_retry_only_resends_failed_segs(self):
        # 第 2 轮只重发失败段（旧实现重发整行全部段 → 确定性失败烧满 3 轮）
        line = "　こんにちは｜また明日"
        segs = make_segs((("art", "　", 1.0)),
                         (("text", "こんにちは", 0.9)),
                         (("art", "｜", 1.0)),
                         (("text", "また明日", 0.9)))
        self.bodies = []

        def _fake(system, body, **kw):
            self.bodies.append(body)
            if len(self.bodies) == 1:
                return "00001_1|こんにちは\n00001_3|明天见"     # 段1 照抄 → 只重试段1
            return "00001_1|こんにちは"

        orig = T.call_api
        T.call_api = _fake
        try:
            T.translate_lines_mixed([(1, line, segs)])
        finally:
            T.call_api = orig
        self.assertEqual(len(self.bodies), 3)
        self.assertIn("_3|", self.bodies[0])          # 首轮发全部段（走独立断言，见下）
        # 第 2/3 轮 body 里只剩失败段 1
        self.assertIn("00001_1|", self.bodies[1])
        self.assertNotIn("00001_3|", self.bodies[1])

    def test_wide_retry_uses_short_hint(self):
        # 超宽段重试必须换提示词（旧实现复用同一 SYSTEM_MIXED，模型不会变短）
        line = "　こんにちは世界｜"
        segs = make_segs(("text", "　こんにちは世界", 0.9), (("art", "｜", 1.0)))
        seen = []

        def _fake(system, body, **kw):
            seen.append(system)
            return "00000_0|这是一句非常长的译文塞满整个区域"

        orig = T.call_api
        T.call_api = _fake
        try:
            T.translate_lines_mixed([(0, line, segs)])
        finally:
            T.call_api = orig
        self.assertNotIn("区域宽度**固定**", seen[0])
        # 2026-10-20: 提示词改成「允许略长、不许删实义成分」（旧文案"可意译/精简"是删实词的来源）
        self.assertIn("实词", seen[1])
        self.assertIn("允许译文略长", seen[1])
        self.assertNotIn("可意译/精简", seen[1])

    def test_leftover_dedup_and_skip_without_work_dir(self):
        # 清单按 章|行|段 去重；无工作目录时不落盘（单测/裸调用安全）
        rows = [("entry_1.txt", 5, 0, "こんにちは", "revert")]
        with _temp_work_dir() as wd:
            self.assertEqual(T.append_mixed_leftover(rows, wd), 1)
            self.assertEqual(T.append_mixed_leftover(rows, wd), 0)
            self.assertEqual(len(_read_leftover(wd).strip().split("\n")), 1)
        T.WORK_DIR = ""
        try:
            self.assertEqual(T.append_mixed_leftover(rows, None), 0)   # 无工作目录 → 不落盘
        finally:
            T.WORK_DIR = self._wd

    def test_residual_detected(self):
        # 译文含残留原文（译文+原文尾巴）→ 残留检测命中 → 该段回炉；3 轮不改 → 末轮退原文 + 报 missed
        self.fake.responses = ["00000_0|你好 こんにちは"]
        fixed, missed, warns = T.translate_lines_mixed(self.flagged)
        self.assertEqual(fixed[0], self.line)     # 退原文（不留半中半日的坏行）
        self.assertEqual(missed, [0])

    def test_wrong_width_detected(self):
        # 译文超宽 → 段级宽校验判失败 + 换「必须更短」提示词重试；3 轮不改 → 末轮退原文 + 报 missed
        self.fake.responses = ["00000_0|这是一句很长很长的译文塞满了整个气泡区域"]
        fixed, missed, warns = T.translate_lines_mixed(self.flagged)
        self.assertEqual(fixed[0], self.line)
        self.assertEqual(missed, [0])

    def test_shrink_detected(self):
        # 译文吞字到比 <0.35（灾难性丢内容）→ 判失败；3 轮不改 → 末轮退原文 + 报 missed
        self.fake.responses = ["00000_0|好"]   # 原段宽 14（正文），译文 2 → 比 0.14
        fixed, missed, warns = T.translate_lines_mixed(self.flagged)
        self.assertEqual(fixed[0], self.line)
        self.assertEqual(missed, [0])

    def test_decorative_seg_copy_accepted(self):
        # 纯装饰段（ヽ 无假名无汉字）模型原样返回是正确行为 → 接受
        line = "ヽ｜"
        segs = make_segs(("text", "ヽ", 0.5), ("art", "｜", 1.0))   # 低置信 → 不送模型
        fixed, missed, warns = T.translate_lines_mixed([(0, line, segs)])
        # 无 conf≥0.65 text 段 → 无 jobs → 直接 missed（回退整行旧逻辑）
        self.assertEqual(missed, [0])


class TestFixWidthMixedLines(unittest.TestCase):
    """fix_width 段级修复（只重译 text 段，art 段保留）。"""

    def setUp(self):
        self.fake = _FakeAPI()
        self.orig = T.call_api
        T.call_api = self.fake.call_api
        # 超宽行：译文比 SRC 宽（宽度错位待修）
        self.src = "　こんにちは世界｜"
        self.tr = "　你好你好你好你好你好｜"   # 超宽译文
        self.segs = make_segs(("text", "　こんにちは世界", 0.9), ("art", "｜", 1.0))
        self.rows = {"entry_1_汉化.txt": [(0, self.src, self.tr, self.segs)]}

    def tearDown(self):
        T.call_api = self.orig

    def test_fix_success_returns_new_line(self):
        # 模型给出更短译文 → rebuild 后整行宽 == SRC → fixed
        self.fake.responses = ["00000_0|你好"]
        fixed, failed = T.fix_width_mixed_lines(self.rows)
        self.assertIn(("entry_1_汉化.txt", 0), fixed)
        self.assertEqual(failed, [])
        self.assertEqual(T.disp_width(fixed[("entry_1_汉化.txt", 0)]), T.disp_width(self.src))

    def test_fix_copy_fail_goes_manual(self):
        # 模型照抄 → 3 轮全失败 → failed 落 manual
        orig = self.src[self.segs[0]['start']:self.segs[0]['end']]
        self.fake.responses = [f"00000_0|{orig}"]
        fixed, failed = T.fix_width_mixed_lines(self.rows)
        self.assertEqual(fixed, {})
        self.assertEqual(len(failed), 1)
        fname, idx, src, tr, segs = failed[0]
        self.assertEqual(idx, 0)

    def test_no_confident_seg_goes_manual(self):
        # 无 conf>=0.65 text 段 → 直接 failed（无法段级修）
        segs = make_segs(("art", "　", 1.0), ("text", "ヽ", 0.4), ("art", "｜", 1.0))
        rows = {"entry_1_汉化.txt": [(0, self.src, self.tr, segs)]}
        fixed, failed = T.fix_width_mixed_lines(rows)
        self.assertEqual(fixed, {})
        self.assertEqual(len(failed), 1)


class TestCompressSegToWidth(unittest.TestCase):
    """2026-09-01: 段译文超宽本地压缩（防超宽段推右 art → 宽对框歪）。"""

    def test_no_compress_when_fits(self):
        self.assertEqual(T._compress_seg_to_width("你好", 4), "你好")

    def test_strip_trailing_ideographic_space(self):
        # 超宽 2 格 → 删行尾 1 个 U+3000
        self.assertEqual(T._compress_seg_to_width("你好\u3000", 4), "你好")

    def test_strip_inner_space(self):
        # 超宽 4 格 → 删段内连续 U+3000
        self.assertEqual(T._compress_seg_to_width("你好\u3000\u3000世界", 8), "你好世界")

    def test_punct_halfwidth(self):
        # 超宽 1 格 → 全角标点转半角（-1 格）
        self.assertEqual(T._compress_seg_to_width("你好！", 5), "你好!")

    def test_uncompressible_returns_none(self):
        # 无空格无标点可删且字符宽度不可变 → None
        self.assertIsNone(T._compress_seg_to_width("你好世界", 4))

    def test_compress_exact(self):
        # 压缩后必须精确 == target_w
        self.assertEqual(T.disp_width(T._compress_seg_to_width("你好\u3000\u3000", 4)), 4)


class TestRebuildMixedLineOverwide(unittest.TestCase):
    """2026-09-01: 译文超宽时 rebuild 本地压缩, 不产出超宽段推右 art。"""

    def test_overwide_translation_compressed(self):
        # text-art: 原段宽 8, 译文超宽 12 → 本地压缩到 8（删空格）
        line = "こんにちは｜"
        segs = make_segs(("text", "こんにちは", 0.9), ("art", "｜", 1.0))
        out = T.rebuild_mixed_line(line, segs, {0: "你好你好　"})  # 8 格 + 2 空格
        self.assertIsNotNone(out)
        self.assertEqual(T.disp_width(out), T.disp_width(line))

    def test_overwide_uncompressible_returns_none(self):
        # 译文超宽且无空格可删 → None（调用方重试/落盘, 不产出超宽段）
        line = "こんにちは｜"
        segs = make_segs(("text", "こんにちは", 0.9), ("art", "｜", 1.0))
        out = T.rebuild_mixed_line(line, segs, {0: "你好你好你好你好你好"})  # 20 格, 压不动
        self.assertIsNone(out)


class TestVerifyArtAlignment(unittest.TestCase):
    """2026-09-01: art 段列位置校验（宽对框歪防御）。"""

    def test_aligned_passes(self):
        line = "　こんにちは｜世界"
        segs = make_segs(("art", "　", 1.0), ("text", "こんにちは", 0.9),
                         ("art", "｜", 1.0), ("text", "世界", 0.9))
        # 重建: art 原样, text pad 到段宽 → art 列位置不变
        out = T.rebuild_mixed_line(line, segs, {1: "你好", 3: "世界"})
        self.assertIsNotNone(out)
        self.assertTrue(T._verify_art_alignment(line, out, segs))

    def test_shifted_art_fails(self):
        line = "　こんにちは｜"
        segs = make_segs(("art", "　", 1.0), ("text", "こんにちは", 0.9), ("art", "｜", 1.0))
        # 模拟 art 段被推右: 手动构造错位行（｜ 不在原列）
        bad = "　こんにちは　｜"  # ｜ 被推右 2 格
        self.assertFalse(T._verify_art_alignment(line, bad, segs))


class _FakeAPI:
    """可编程假模型：mock call_api，按 responses 列表依次返回。"""

    def __init__(self):
        self.responses = []
        self.calls = 0

    def call_api(self, system, body, **kw):
        self.calls += 1
        return self.responses[0]


if __name__ == "__main__":
    unittest.main()
