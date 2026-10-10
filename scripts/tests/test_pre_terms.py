# -*- coding: utf-8 -*-
"""翻译前术语强制替换单测（2026-09-22 宝可梦链调整）。

口径：
- 词源 = wiki AA 术语 + wiki 官方译名对照 + 作品术语表（作品表优先）
- 只替换「本章语料里出现过的词」；脏译名（残留假名/替换符）、超长词（句子）、
  单字词一律不替换
- 长词优先、非重叠、短词加假名邻接守卫（防 アメリカ 里的 リカ 式碎片）
- **只改送模型的那份文本**：aa_translate 钩子接入后原文/宽度/art 校验口径不变
"""
import os
import sys
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in (SCRIPTS, os.path.join(SCRIPTS, "common")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import aa_translate as A        # noqa: E402
import pre_terms as PT          # noqa: E402
from _work_guard import repo_only  # noqa: E402 （发行包内跳过生成者专属用例）

POKEMON_WORK = "この素晴らしいシンオウに祝福を"


def _dump(path, obj):
    import json
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _pairs(rows):
    """用 extra 通道构造词表（wiki=False 绕开 wiki/作品表文件依赖）。"""
    return PT.build_pairs("", corpus="", extra=rows, wiki=False)


class TestRegexFor(unittest.TestCase):
    def test_single_and_empty_rejected(self):
        self.assertIsNone(PT.regex_for(""))
        self.assertIsNone(PT.regex_for("あ"))
        self.assertIsNone(PT.regex_for("漢"))       # 单字汉字同样拒绝

    def test_re_prefix_regex(self):
        rx = PT.regex_for("re:(?<![ァ-ヶー・])ジン(?![ァ-ヶー・])")
        self.assertIsNotNone(rx)
        self.assertIsNone(PT.regex_for("re:(["))
        self.assertTrue(rx.search("ジンで作る"))
        self.assertFalse(rx.search("ジンジャーエール"))

    def test_left_guard_blocks_katakana_fragment(self):
        rx = PT.regex_for("リカ")                   # 短词 + 假名开头 → 左守卫（片假名邻接）
        self.assertFalse(rx.search("アメリカの"))
        self.assertTrue(rx.search("リカが来た"))
        self.assertTrue(rx.search("とリカ"))          # 左侧平假名多为助词 → 放行

    def test_right_guard_blocks_katakana_continuation(self):
        rx = PT.regex_for("エース")
        self.assertFalse(rx.search("エースキラーβ"))
        self.assertTrue(rx.search("エースは強い"))   # 后接平假名（助词）仍应命中


class TestBuildPairs(unittest.TestCase):
    def test_long_first_and_no_single_char(self):
        ps = _pairs([("エースキラーβ", "王牌杀手β", "t", "c"),
                     ("エース", "王牌", "t", "c"),
                     ("あ", "啊", "t", "c")])
        self.assertEqual([p["jp"] for p in ps], ["エースキラーβ", "エース"])

    def test_dirty_and_long_and_equal_skipped(self):
        ps = _pairs([("カズマ", "和真", "t", "c"),
                     ("ダミー1", "ダメだよ", "t", "c"),        # zh 含假名 → 脏
                     ("ダミー2", "无名\ufffd", "t", "c"),       # 替换符 → 脏
                     ("ダミー3", "ダミー3", "t", "c"),          # zh==jp → 跳过
                     ("とても長い説明文がここに来ていて句点もある。", "很长的说明。", "t", "c")])
        self.assertEqual([p["jp"] for p in ps], ["カズマ"])
        self.assertEqual(ps[0]["skipped"]["dirty_zh"], 2)

    def test_corpus_filter(self):
        rows = [("カズマ", "和真", "t", "c"), ("ルサルカ", "卢莎卡", "t", "c")]
        corpus = "カズマが来た"                      # 只出现 カズマ
        ps = PT.build_pairs("", corpus=corpus, extra=rows)
        self.assertEqual([p["jp"] for p in ps], ["カズマ"])

    def test_work_table_beats_wiki(self):
        ps = PT.build_pairs("", corpus="", wiki=False,
                            extra=[("エース", "王牌", "作品表-manual", "专名")])
        self.assertEqual(ps[0]["zh"], "王牌")


class TestApply(unittest.TestCase):
    def test_longest_first_non_overlapping(self):
        ps = _pairs([("エースキラーβ", "王牌杀手β", "t", "c"), ("エース", "王牌", "t", "c")])
        new, hits = PT.apply_hits("【ライナー】『エースキラーβ』発動！", pairs=ps)
        self.assertIn("王牌杀手β", new)
        self.assertNotIn("王牌" + "杀手", new.replace("王牌杀手", ""))
        self.assertEqual([h["jp"] for h in hits], ["エースキラーβ"])

    def test_multiple_hits_positions_sorted(self):
        ps = _pairs([("カズマ", "和真", "t", "c"), ("ルサルカ", "卢莎卡", "t", "c")])
        new, hits = PT.apply_hits("ルサルカとカズマ", pairs=ps)
        self.assertEqual(new, "卢莎卡と和真")     # 只替换术语词；助词等周边日文原样留给模型
        self.assertEqual([h["jp"] for h in hits], ["ルサルカ", "カズマ"])
        self.assertEqual(hits[0]["at"], 0)

    def test_already_translated_not_touched(self):
        ps = _pairs([("カズマ", "和真", "t", "c")])
        new, hits = PT.apply_hits("和真は強い", pairs=ps)
        self.assertEqual(new, "和真は強い")
        self.assertEqual(hits, [])

    def test_work_id_derived_when_translate_runner_sets_only_dirs(self):
        """translate_runner 只设 AA_DIR/OUT_DIR/WORK_DIR（不设 WORK_ID）→ 必须反推出作品。

        这是让两次端到端 smoke 静默零替换的 bug（2026-09-22）。
        """
        class FakeA:
            WORK_ID = ""
            AA_DIR = "<repo>/aaManga/某作品/aa"
            OUT_DIR = "<repo>/aaManga/某作品/汉化"
            WORK_DIR = "<repo>/aaManga/某作品/work"

        saved = sys.modules.get("aa_translate")
        sys.modules["aa_translate"] = FakeA
        try:
            self.assertEqual(PT.work_id(), "某作品")
            self.assertEqual(PT.work_id("显式作品"), "显式作品")
        finally:
            if saved is not None:
                sys.modules["aa_translate"] = saved
            else:
                sys.modules.pop("aa_translate", None)

    def test_enabled_caches_pipeline_lookup(self):
        """管线判定每次都要读 work_meta.json/registry → enabled 必须缓存（每行都会调）。"""
        calls = {"n": 0}

        class FakeWP:
            @staticmethod
            def pipeline_of(w):
                calls["n"] += 1
                return "pokemon"

        saved = sys.modules.get("work_pipeline")
        sys.modules["work_pipeline"] = FakeWP
        PT.clear_cache()
        try:
            for _ in range(5):
                self.assertTrue(PT.enabled("某作品"))
            self.assertEqual(calls["n"], 1)
        finally:
            PT.clear_cache()
            if saved is not None:
                sys.modules["work_pipeline"] = saved
            else:
                sys.modules.pop("work_pipeline", None)

    def test_hit_positions_consistent_with_new_text(self):
        """多命中时 at 必须与最终文本一致（报告高亮依赖它）。"""
        ps = _pairs([("ライナー", "莱纳", "t", "c"),
                     ("エースキラーβ", "王牌杀手β", "t", "c"),
                     ("カズマ", "和真", "t", "c")])
        src = "【ライナー】『エースキラーβ』とカズマ"
        new, hits = PT.apply_hits(src, pairs=ps)
        self.assertEqual(new, "【莱纳】『王牌杀手β』と和真")
        for h in hits:                                    # at 处就是该 zh
            self.assertEqual(new[h["at"]:h["at"] + len(h["zh"])], h["zh"])
        self.assertEqual([h["jp"] for h in hits], ["ライナー", "エースキラーβ", "カズマ"])

    def test_overlap_keeps_longest_at_same_start(self):
        ps = _pairs([("エースキラーβ", "王牌杀手β", "t", "c"), ("エース", "王牌", "t", "c")])
        new, hits = PT.apply_hits("エースキラーβ発動", pairs=ps)
        self.assertEqual(new, "王牌杀手β発動")
        self.assertEqual(len(hits), 1)

    def test_empty_pairs_identity(self):
        t = "カズマとルサルカ"
        self.assertEqual(PT.apply_hits(t, pairs=[]), (t, []))

    def test_scan_lines_with_keep(self):
        lines = ["カズマだ", "art 行 カズマ", ""]
        ps = _pairs([("カズマ", "和真", "t", "c")])
        rows = PT.scan_lines(lines, ps, keep=lambda i, l: i == 0)
        self.assertEqual([r[0] for r in rows], [0])


class TestEnabled(unittest.TestCase):
    def test_env_off(self):
        old = os.environ.get("AA_PRE_TERMS")
        os.environ["AA_PRE_TERMS"] = "0"
        try:
            self.assertFalse(PT.enabled(POKEMON_WORK))
        finally:
            if old is None:
                os.environ.pop("AA_PRE_TERMS", None)
            else:
                os.environ["AA_PRE_TERMS"] = old

    def test_non_pokemon_pipeline_off(self):
        self.assertFalse(PT.enabled("__no_such_work_xyz__"))

    @repo_only("需要宝可梦作品/本作 wiki 素材")
    def test_pokemon_pipeline_on(self):
        if not os.path.exists(os.path.join(PT.ROOT, "aa_web_app", "data", "work_meta.json")):
            self.skipTest("work_meta.json 缺失")
        self.assertTrue(PT.enabled(POKEMON_WORK))


class TestHook(unittest.TestCase):
    """aa_translate 钩子：只影响送模型文本，异常/未注册时零副作用。"""

    def tearDown(self):
        A.set_pre_term_fn(None)

    def test_pt_not_registered_is_identity(self):
        A.set_pre_term_fn(None)
        self.assertEqual(A._pt("カズマ", "entry_8342"), "カズマ")

    def test_pt_applies_and_base_passed(self):
        seen = []

        def fn(text, base=None, work=None):
            seen.append((base, work))
            return text.replace("カズマ", "和真")

        A.set_pre_term_fn(fn)
        A.WORK_ID = "某作品"
        try:
            self.assertEqual(A._pt("カズマだ", "entry_8342"), "和真だ")
        finally:
            A.WORK_ID = ""
        # work 必须显式传（脚本方式跑时 aa_translate=__main__，反查会拿错作品）
        self.assertEqual(seen, [("entry_8342", "某作品")])

    def test_pt_swallows_exception(self):
        def boom(text, base=None, work=None):
            raise RuntimeError("x")

        A.set_pre_term_fn(boom)
        self.assertEqual(A._pt("カズマだ", "b"), "カズマだ")

    def test_pt_empty_result_falls_back(self):
        A.set_pre_term_fn(lambda t, b=None, w=None: "")
        self.assertEqual(A._pt("カズマだ", "b"), "カズマだ")

    def test_translate_lines_body_substituted(self):
        """真接线：translate_lines 拼给模型的 body 应含替换后的词。"""
        captured = []

        def fake_call(sys_p, body, work_r18=None, low_r18=False):
            captured.append(body)
            return ""

        A.set_pre_term_fn(lambda t, b=None, w=None: t.replace("カズマ", "和真"))
        old = A.call_api
        A.call_api = fake_call
        try:
            A.translate_lines([(0, "カズマだ！")], base="entry_8342")
        finally:
            A.call_api = old
            A.set_pre_term_fn(None)
        self.assertTrue(captured, "未捕获到 body")
        self.assertIn("和真だ！", captured[0])
        self.assertNotIn("カズマ", captured[0])

    def test_translate_lines_mixed_seg_substituted(self):
        captured = []

        def fake_call(sys_p, body, work_r18=None, low_r18=False):
            captured.append(body)
            return ""

        segs = [{"start": 0, "end": 4, "kind": "text", "conf": 0.9, "rule": False}]
        A.set_pre_term_fn(lambda t, b=None, w=None: t.replace("カズマ", "和真"))
        old = A.call_api
        A.call_api = fake_call
        try:
            A.translate_lines_mixed([(0, "カズマだ", segs)], base="entry_8342")
        finally:
            A.call_api = old
            A.set_pre_term_fn(None)
        self.assertTrue(captured, "未捕获到 mixed body")
        self.assertIn("00000_0|和真だ", captured[0])


class TestRefineHook(unittest.TestCase):
    """refine_aa 的"重译原文"路径（swallow/poly）同样要过术语替换。

    为什么必须接：校对会把日文原文重新送模型，不替换就会出现"替换过又被改回去"
    的漂移（实测 entry_16582 L4072 把 双看板 改写成 双招牌）。
    """

    @repo_only("需要宝可梦作品/本作 wiki 素材")
    def test_pre_terms_substitutes_for_pokemon_work(self):
        sys.path.insert(0, os.path.join(PT.ROOT, "scripts", "fix"))
        try:
            import refine_aa as RA
        except Exception as e:                                  # pragma: no cover
            self.skipTest("refine_aa 不可导入: %s" % e)
        got = RA._pre_terms("カズマとライナー", "entry_8342", POKEMON_WORK)
        self.assertEqual(got, "和真と莱纳")

    def test_pre_terms_noop_without_work(self):
        sys.path.insert(0, os.path.join(PT.ROOT, "scripts", "fix"))
        try:
            import refine_aa as RA
        except Exception as e:                                  # pragma: no cover
            self.skipTest("refine_aa 不可导入: %s" % e)
        self.assertEqual(RA._pre_terms("カズマ", "entry_8342", ""), "カズマ")


class TestWikiOverlay(unittest.TestCase):
    """补充术语.json（人工增量：通用补充 + 作品专属项）。

    口径：术语表.json/官方译名对照.json 是生成物；手写条目一律进补充术语.json。
    - terms → src=wiki-补充（跨作品通用）
    - works[<作品>] → src=作品专属（只有该作用得到），按 work 过滤
    - 这两个来源允许整句（≤60 字），wiki 批量条目仍限 20 字
    """

    def setUp(self):
        import json
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="aa_wiki_")
        self._old = os.environ.get("AA_POKEMON_WIKI_DIR")
        os.environ["AA_POKEMON_WIKI_DIR"] = self.tmp
        with open(os.path.join(self.tmp, "术语表.json"), "w", encoding="utf-8") as f:
            json.dump({"terms": [{"jp": "アースクエイク", "zh": "地震", "cat": "技"}]}, f, ensure_ascii=False)
        with open(os.path.join(self.tmp, "官方译名对照.json"), "w", encoding="utf-8") as f:
            json.dump({"official": [{"jp": "ピカチュウ", "zh": "皮卡丘", "kind": "宝可梦"}]}, f, ensure_ascii=False)
        with open(os.path.join(self.tmp, PT.WIKI_EXTRA), "w", encoding="utf-8") as f:
            json.dump({
                "terms": [{"jp": "技マシン", "zh": "招式学习器", "cat": "道具"}],
                "works": {
                    POKEMON_WORK: {
                        "terms": [{"jp": "ソノオスタジアム", "zh": "花苑竞技场", "cat": "场地"}],
                        "effects": [{"jp": "◆フィールド効果", "zh": "◆场地效果", "cat": "场地模板"}],
                    },
                    "别的作品": {"terms": [{"jp": "ソノオスタジアム", "zh": "别的译名", "cat": "场地"}]},
                },
            }, f, ensure_ascii=False)
        PT.wiki_pairs(refresh=True)          # 清缓存（含 work=None 的那份）

    def tearDown(self):
        import shutil
        if self._old is None:
            os.environ.pop("AA_POKEMON_WIKI_DIR", None)
        else:
            os.environ["AA_POKEMON_WIKI_DIR"] = self._old
        PT._wiki_cache.update({"key": None, "pairs": None, "work": None})
        PT._extra_cache.update({"key": None, "pairs": None})
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_extra_pairs_contains_both_sources(self):
        rows = PT.extra_pairs(POKEMON_WORK, refresh=True)
        got = {r[0]: (r[1], r[2]) for r in rows}
        self.assertEqual(got["技マシン"], ("招式学习器", "wiki-补充"))
        self.assertEqual(got["ソノオスタジアム"], ("花苑竞技场", "作品专属"))
        self.assertEqual(got["◆フィールド効果"], ("◆场地效果", "作品专属"))

    def test_work_section_filtered_by_work(self):
        other = {r[0]: r[1] for r in PT.extra_pairs("别的作品", refresh=True)}
        self.assertEqual(other["ソノオスタジアム"], "别的译名")
        none_work = {r[0] for r in PT.extra_pairs(None, refresh=True)}
        self.assertNotIn("ソノオスタジアム", none_work)      # 不给作品 → 不带作品专属项
        self.assertIn("技マシン", none_work)                  # 通用补充始终在

    def test_wiki_pairs_includes_overlay(self):
        srcs = {r[0]: r[2] for r in PT.wiki_pairs(work=POKEMON_WORK, refresh=True)}
        self.assertEqual(srcs["技マシン"], "wiki-补充")
        self.assertEqual(srcs["ソノオスタジアム"], "作品专属")
        self.assertEqual(srcs["アースクエイク"], "wiki-术语")

    def test_sentence_allowed_only_for_curated_sources(self):
        long_jp = "場にいる全てのポケモンの「避」が低下（0.67倍）する。"
        ps = _pairs([(long_jp, "场上所有宝可梦的「避」下降（0.67倍）。", "t", "c")])
        self.assertEqual(ps, [])                                    # wiki 批量条目：超长丢弃
        ps2 = _pairs([(long_jp, "场上所有宝可梦的「避」下降（0.67倍）。", "作品专属", "场地")])
        self.assertEqual([p["jp"] for p in ps2], [long_jp])         # 人工条目：放行
        ps3 = _pairs([(long_jp, "场上所有宝可梦的「避」下降（0.67倍）。", "wiki-补充", "场地")])
        self.assertEqual([p["jp"] for p in ps3], [long_jp])


class TestGuardAllLengths(unittest.TestCase):
    """守卫对所有长度生效（2026-09-30）。

    原来只给 ≤4 字套守卫 → `ストライク`(5 字) 吃掉了 `ストライクゾーン` / `ストライク・ジークヴルム`。
    """

    def test_right_guard_blocks_long_katakana_continuation(self):
        rx = PT.regex_for("ストライク")
        self.assertIsNone(rx.search("ストライクゾーン狭い"))          # 好球区
        self.assertIsNone(rx.search("「ストライク・ジークヴルム」"))    # 技名
        self.assertTrue(rx.search("ストライクは強い"))
        self.assertTrue(rx.search("「ストライク」"))

    def test_left_guard_blocks_long_katakana_prefix(self):
        rx = PT.regex_for("ガブリアス")
        self.assertIsNone(rx.search("メガガブリアス"))
        self.assertTrue(rx.search("、ガブリアス"))
        self.assertTrue(rx.search("ガブリアスが"))


class TestAmbiguityGuard(unittest.TestCase):
    """歧义守卫（补充术语.json「歧义」段）：禁 / 允许 / require / deny。"""

    def setUp(self):
        import json
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="aa_amb_")
        self._old = os.environ.get("AA_POKEMON_WIKI_DIR")
        os.environ["AA_POKEMON_WIKI_DIR"] = self.tmp
        with open(os.path.join(self.tmp, "术语表.json"), "w", encoding="utf-8") as f:
            json.dump({"terms": []}, f, ensure_ascii=False)
        with open(os.path.join(self.tmp, "官方译名对照.json"), "w", encoding="utf-8") as f:
            json.dump({"official": [
                {"jp": "ラッキー", "zh": "吉利蛋", "kind": "宝可梦"},
                {"jp": "めい", "zh": "逃足奇袭", "kind": "ポテンシャル"},
                {"jp": "ヌシ", "zh": "霸主", "kind": "ポテンシャル"},
                {"jp": "あの", "zh": "那个", "kind": "ポテンシャル"},
                {"jp": "カウンター", "zh": "双倍奉还", "kind": "招式"},
                {"jp": "あわ", "zh": "泡沫", "kind": "招式"},
            ]}, f, ensure_ascii=False)
        with open(os.path.join(self.tmp, PT.WIKI_EXTRA), "w", encoding="utf-8") as f:
            json.dump({"歧义": [
                {"jp": "めい", "禁": True},
                {"jp": "あわ", "禁": True},
                {"jp": "ヌシ", "允许": True},
                {"jp": "ラッキー", "require": "ポケモン|持ち物"},
                {"jp": "カウンター", "deny": "寿命", "deny距离": 6},
            ]}, f, ensure_ascii=False)
        PT.clear_cache()

    def tearDown(self):
        import shutil
        if self._old is None:
            os.environ.pop("AA_POKEMON_WIKI_DIR", None)
        else:
            os.environ["AA_POKEMON_WIKI_DIR"] = self._old
        PT.clear_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pairs(self):
        # work 用不存在的 id：只走 wiki（隔离作品表），corpus="" → 不做本章语料过滤
        return PT.build_pairs("__amb_test__", corpus="", wiki=True)

    def test_禁_drops_pair(self):
        got = {p["jp"] for p in self._pairs()}
        self.assertNotIn("めい", got)
        self.assertNotIn("あわ", got)

    def test_允许_keeps_short_kana(self):
        self.assertIn("ヌシ", {p["jp"] for p in self._pairs()})

    def test_short_kana_without_rule_dropped(self):
        ps = self._pairs()
        self.assertNotIn("あの", {p["jp"] for p in ps})       # 2 字假名没有词界 → 默认禁用
        self.assertGreaterEqual(ps[0]["skipped"]["短假名未授权"], 1)

    def test_require_needs_context(self):
        ps = self._pairs()
        new, hits = PT.apply_hits("当たったらラッキー程度だけど", pairs=ps)
        self.assertEqual(new, "当たったらラッキー程度だけど")   # 无宝可梦语境 → 不替换
        self.assertEqual(hits, [])
        new, hits = PT.apply_hits("持ち物はラッキー", pairs=ps)
        self.assertEqual(new, "持ち物は吉利蛋")
        self.assertEqual([h["jp"] for h in hits], ["ラッキー"])

    def test_deny_blocks_context(self):
        ps = self._pairs()
        new, hits = PT.apply_hits("寿命カウンターあるし", pairs=ps)
        self.assertEqual(new, "寿命カウンターあるし")
        self.assertEqual(hits, [])
        new, _ = PT.apply_hits("「カウンター」！", pairs=ps)
        self.assertEqual(new, "「双倍奉还」！")

    def test_deny_window_distance(self):
        ps = self._pairs()
        far = "寿命" + "あ" * 12 + "カウンター"          # 距离 > deny距离(6) → 不拦
        new, hits = PT.apply_hits(far, pairs=ps)
        self.assertEqual([h["jp"] for h in hits], ["カウンター"])

    def test_hits_span_of_kept_pairs(self):
        ps = self._pairs()
        new, hits = PT.apply_hits("ヌシとラッキー", pairs=ps)      # 允许 + require 不满足
        self.assertEqual(new, "霸主とラッキー")
        self.assertEqual([h["jp"] for h in hits], ["ヌシ"])


class TestAltSense(unittest.TestCase):
    """同形两义「替译」（歧义段 `zh` + require，2026-10-09）。

    `ラム` 在宝可梦作品里两义：道具简写（`ラムのみ`=木子果，裸写 →木子）与角色/宝可梦
    个体名（ラム→拉姆）。原来只有「允许」，于是道具义也被吃成 拉姆——已出货
    「カービィがラムだった」→「卡比是拉姆」、「無難にラム＆オボンだな」→「拉姆＆欧文」。
    带 `zh` 的歧义条目 = 替译：语境命中 → 出 `zh`，否则出词表默认译名（两义都替换）；
    没有 `zh` 的条目语义不变（`ラッキー` 式"换/不换"）。
    """

    def setUp(self):
        import json
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="aa_alt_")
        self._old = os.environ.get("AA_POKEMON_WIKI_DIR")
        os.environ["AA_POKEMON_WIKI_DIR"] = self.tmp
        with open(os.path.join(self.tmp, "术语表.json"), "w", encoding="utf-8") as f:
            json.dump({"terms": []}, f, ensure_ascii=False)
        with open(os.path.join(self.tmp, "官方译名对照.json"), "w", encoding="utf-8") as f:
            json.dump({"official": [
                {"jp": "ラムのみ", "zh": "木子果", "kind": "道具"},
                {"jp": "オボンのみ", "zh": "文柚果", "kind": "道具"},
                {"jp": "ラッキー", "zh": "吉利蛋", "kind": "宝可梦"},
            ]}, f, ensure_ascii=False)
        with open(os.path.join(self.tmp, PT.WIKI_EXTRA), "w", encoding="utf-8") as f:
            json.dump({
                "terms": [{"jp": "ラム", "zh": "木子", "cat": "道具"},
                          {"jp": "オボン", "zh": "文柚", "cat": "道具"}],
                "歧义": [{"jp": "ラム", "zh": "拉姆", "允许": True,
                          "require": "レム|【ラム】|名前】"},
                         {"jp": "ラッキー", "require": "ポケモン|持ち物"}],
            }, f, ensure_ascii=False)
        PT.clear_cache()

    def tearDown(self):
        import shutil
        if self._old is None:
            os.environ.pop("AA_POKEMON_WIKI_DIR", None)
        else:
            os.environ["AA_POKEMON_WIKI_DIR"] = self._old
        PT.clear_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pairs(self):
        return PT.build_pairs("__alt_test__", corpus="", wiki=True)

    def test_item_sense_is_default(self):
        ps = self._pairs()
        new, hits = PT.apply_hits("無難にラム＆オボンだな", pairs=ps)
        self.assertEqual(new, "無難に木子＆文柚だな")
        self.assertEqual([(h["jp"], h["zh"]) for h in hits], [("ラム", "木子"), ("オボン", "文柚")])
        self.assertNotIn("alt", hits[0])                    # 默认义不带替译标记

    def test_alt_when_context_matches(self):
        ps = self._pairs()
        new, hits = PT.apply_hits("【ラム】頑張るっちゃ、ダーリン！", pairs=ps)
        self.assertEqual(new, "【拉姆】頑張るっちゃ、ダーリン！")
        self.assertTrue(hits[0].get("alt"))
        new, hits = PT.apply_hits("レムとラムがどっちがどっちか", pairs=ps)
        self.assertEqual(new, "レムと拉姆がどっちがどっちか")

    def test_full_name_pair_still_wins(self):
        ps = self._pairs()
        new, hits = PT.apply_hits("【持ち物】ラムのみ", pairs=ps)
        self.assertEqual(new, "【持ち物】木子果")
        self.assertEqual([h["jp"] for h in hits], ["ラムのみ"])

    def test_alt_respects_window(self):
        ps = self._pairs()
        far = "レム" + "あ" * 80 + "ラムだ"                 # 距离 > require距离(60) → 默认义
        new, _ = PT.apply_hits(far, pairs=ps)
        self.assertTrue(new.endswith("木子だ"), new[-8:])

    def test_require_without_zh_keeps_old_semantics(self):
        ps = self._pairs()
        new, hits = PT.apply_hits("当たったらラッキー程度", pairs=ps)
        self.assertEqual(new, "当たったらラッキー程度")      # 无宝可梦语境 → 不替换（不是出别的译名）
        self.assertEqual(hits, [])
        new, _ = PT.apply_hits("持ち物はラッキー", pairs=ps)
        self.assertEqual(new, "持ち物は吉利蛋")


class TestWikiTranslationFixes(unittest.TestCase):
    """译名口径修正（补充术语.json「修正」段 → 术语表.json）+ 真实歧义数据体检。"""

    def test_half_is_konketsu(self):
        got = {jp: zh for jp, zh, _src, _cat in PT.wiki_pairs(work=POKEMON_WORK, refresh=True)}
        self.assertEqual(got.get("ハーフ"), "混血")
        self.assertEqual(got.get("ハーフブリード"), "混血")

    def test_real_wiki_ambiguity_rules_present(self):
        """本作实测的毒条目必须还有规则（防误删）；缺了会立刻复发已出货的错译。"""
        rules = PT.ambiguity_rules(refresh=True)
        for jp in ("めい", "あの", "あわ", "まひ", "クラブ", "プラス",
                   "ラッキー", "ストライク", "ファイヤー", "カウンター"):
            self.assertIn(jp, rules, "歧义段缺 %s" % jp)
        self.assertTrue(rules["めい"]["禁"])
        self.assertTrue(rules["あの"]["禁"])
        self.assertTrue(rules["ヌシ"]["允许"])
        self.assertIsNotNone(rules["ラッキー"]["require"])
        self.assertIsNotNone(rules["カウンター"]["deny"])

    def test_item_abbrev_pairs_and_alt_rule(self):
        """道具简写词条 + ラム 的替译规则（両作全库站点穷举后定的口径，防误删）。

        用户口径「对应原文（原文简写就简写）」：裸 ラム→木子 / オボン→文柚；
        全长 ラムのみ→木子果 / オボンのみ→文柚果；角色义 ラム→拉姆 只在语境词表命中时落地。
        """
        got = {}
        for jp, zh, _src, _cat in PT.wiki_pairs(work=POKEMON_WORK, refresh=True):
            got.setdefault(jp, zh)                      # 先到者优先 = 人工补充(extra)在前
        self.assertEqual(got.get("ラム"), "木子")
        self.assertEqual(got.get("オボン"), "文柚")
        self.assertEqual(got.get("ラムのみ"), "木子果")
        self.assertEqual(got.get("オボンのみ"), "文柚果")
        rules = PT.ambiguity_rules(refresh=True)
        self.assertEqual(rules["ラム"]["zh"], "拉姆")     # 角色义 = 替译
        self.assertIsNotNone(rules["ラム"]["require"])
        self.assertTrue(rules["ラム"]["允许"])            # 顺带豁免 ≤2 假名默认不替换

    @repo_only("需要 ポケの子 本作 wiki 素材")
    def test_work_wiki_character_pair_not_shadow_item_sense(self):
        """ポケの子：本作wiki-人工 的 `ラム→拉姆` 不能当默认（那正是果义被吃成拉姆的成因）。"""
        if not os.path.isdir(PT.work_wiki_dir("ポケの子")):
            self.skipTest("ポケの子 wiki 缺失")
        ps = PT.build_pairs("ポケの子", corpus="", wiki=True)
        hit = [x for x in ps if x["jp"] == "ラム"]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["zh"], "木子")                    # 默认 = 道具义
        self.assertEqual((hit[0].get("amb") or {}).get("zh"), "拉姆")   # 替译 = 角色义


class TestWorkWikiSource(unittest.TestCase):
    """本作设定 wiki 词源（2026-10-03）。

    口径：本作 wiki（`doc/aa作品相关/<作品>wiki`）的页面标题/术语表 + `人工修正.json`
    的 `本地译名` 也进词表；优先级 作品术语表(2) > 本作 wiki·人工补充(1) > 通用 AA wiki(0)。
    目录解析 env `AA_WORK_WIKI_DIR` > `work_meta[work]["wiki_dir"]` > 约定 `<作品>wiki`。
    """

    def setUp(self):
        import json
        import tempfile
        self.g = tempfile.mkdtemp(prefix="aa_wiki_g_")
        self.wk = tempfile.mkdtemp(prefix="aa_wiki_wk_")
        self._og = os.environ.get("AA_POKEMON_WIKI_DIR")
        self._ow = os.environ.get("AA_WORK_WIKI_DIR")
        self._off = os.environ.get("AA_PRE_TERMS_WORK_WIKI")
        os.environ.pop("AA_PRE_TERMS_WORK_WIKI", None)
        os.environ["AA_POKEMON_WIKI_DIR"] = self.g
        os.environ["AA_WORK_WIKI_DIR"] = self.wk
        _dump(os.path.join(self.g, "术语表.json"),
              {"terms": [{"jp": "ギャラドス", "zh": "通用鲤龙", "cat": "宝可梦"}]})
        _dump(os.path.join(self.g, "官方译名对照.json"), {"official": []})
        _dump(os.path.join(self.g, PT.WIKI_EXTRA), {"terms": [], "works": {}})
        _dump(os.path.join(self.wk, PT.WORK_WIKI_TERMS), {
            "terms": [{"jp": "ギャラドス", "zh": "本作鲤龙", "cat": "宝可梦"},
                      {"jp": "オリジナルわざ", "zh": "原创招式", "cat": "原创招式"}],
            "pages": [{"name": "サトシ", "title": "小智", "cat": "人物"},
                      {"name": "レンジャー", "title": "游侠", "cat": "人物"}],
        })
        _dump(os.path.join(self.wk, PT.WORK_WIKI_EXTRA),
              {"本地译名": [["ギャラドス", "人工鲤龙"], ["カイリュー", "快龙"]],
               "修正": {"不改": "不动"}})
        PT.clear_cache()

    def tearDown(self):
        import shutil
        for k, v in (("AA_POKEMON_WIKI_DIR", self._og), ("AA_WORK_WIKI_DIR", self._ow),
                     ("AA_PRE_TERMS_WORK_WIKI", self._off)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        PT.clear_cache()
        shutil.rmtree(self.g, ignore_errors=True)
        shutil.rmtree(self.wk, ignore_errors=True)

    def test_dir_env_wins_and_generic_dir_never_selected(self):
        self.assertEqual(os.path.abspath(PT.work_wiki_dir(POKEMON_WORK)),
                         os.path.abspath(self.wk))
        os.environ.pop("AA_WORK_WIKI_DIR")
        got = PT.work_wiki_dir(POKEMON_WORK)                  # 约定：doc/aa作品相关/<作品>wiki
        self.assertTrue(got.endswith(os.path.join("aa作品相关", POKEMON_WORK + "wiki")), got)
        self.assertEqual(PT.work_wiki_dir("并不存在的作品"), "")   # 没有就不给词源（不抛错）
        self.assertEqual(PT.work_wiki_dir(None), "")             # 无 work 且无 env

    def test_pairs_from_pages_terms_and_manual(self):
        got = {}
        for j, z, s, _c in PT.work_wiki_pairs(POKEMON_WORK, refresh=True):
            got.setdefault(j, (z, s))          # 先到者优先 = 人工表在前
        self.assertEqual(got["ギャラドス"], ("人工鲤龙", "本作wiki-人工"))   # 人工表在前
        self.assertEqual(got["カイリュー"], ("快龙", "本作wiki-人工"))
        self.assertEqual(got["サトシ"], ("小智", "本作wiki"))
        self.assertEqual(got["オリジナルわざ"], ("原创招式", "本作wiki"))
        self.assertNotIn("不改", got)                          # `修正` 是整行覆盖，不进词表

    def test_priority_work_table_then_work_wiki_then_generic(self):
        got = {p["jp"]: p for p in PT.build_pairs("", corpus="", wiki=True)
               if p["jp"] == "ギャラドス"}
        self.assertEqual(got["ギャラドス"]["zh"], "人工鲤龙")
        self.assertEqual(got["ギャラドス"]["src"], "本作wiki-人工")
        ps = PT.build_pairs("", corpus="", wiki=True,
                            extra=[("ギャラドス", "作品表鲤龙", "作品表-manual", "宝可梦")])
        self.assertEqual({p["jp"]: p["zh"] for p in ps}["ギャラドス"], "作品表鲤龙")

    def test_env_off_drops_source(self):
        os.environ["AA_PRE_TERMS_WORK_WIKI"] = "0"
        self.assertEqual(PT.work_wiki_pairs(POKEMON_WORK, refresh=True), [])
        srcs = {p["src"] for p in PT.build_pairs("", corpus="", wiki=True)}
        self.assertFalse([s for s in srcs if s.startswith("本作wiki")])
        self.assertIn("wiki-术语", srcs)                        # 通用 wiki 不受影响

    def test_sentence_cap_applies_to_work_wiki(self):
        long_jp = "場にいる全てのポケモンの「避」が低下（0.67倍）する。"
        ps = _pairs([(long_jp, "场上所有宝可梦的「避」下降（0.67倍）。", PT.WORK_WIKI_SRC, "页面")])
        self.assertEqual(ps, [])                                # 生成条目：超长丢弃
        man = _pairs([(long_jp, "场上所有宝可梦的「避」下降（0.67倍）。", PT.WORK_WIKI_SRC_MANUAL, "人工译名")])
        self.assertEqual(man, [])                               # 人工译名也不放行整句（那走作品专属）


if __name__ == "__main__":
    unittest.main()
