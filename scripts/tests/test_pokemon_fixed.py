# -*- coding: utf-8 -*-
"""宝可梦固定内容表单测（2026-09-22 方案）。

口径：四轨匹配（copy > 规则 > 结构化 > 整行）；copy 轨零翻译风险（原样照抄）；
建表期护栏宽松（只拦译文本身问题），宽度/art 列位留到 prefill 期逐行 guard。
实测依据见 doc/方案归档/宝可梦固定内容表方案_2026-09-22.md §1.7。
"""
import os
import re
import shutil
import sys
import unittest
from unittest import mock

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in (SCRIPTS, os.path.join(SCRIPTS, "common")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import pokemon_fixed as PF
import aa_translate as A


def _table():
    t = PF.empty_table()
    t["derived"]["struct"] = {
        "気合い": {"jp": "気合い", "zh": "气魄", "effect_n": "稀に気合いで相手の攻撃を耐える。",
                   "desc": "偶尔凭气魄扛住对手的攻击。", "cat": "ポテンシャル", "_src": "wiki"},
    }
    t["derived"]["whole"] = {"◎こうかはばつぐんだ！": {"zh": "◎效果绝佳！", "w": 14}}
    t["derived"]["copy"] = {"|::ﾘﾍヾ___,,,j:/Ｖ:/ﾐ/!I": {"_src": "pure_art", "n": 31},
                            "『名もなき行』（未改动）": {"_src": "unchanged", "n": 3}}
    return t


class TestKey(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(PF.key("　｜　気合い　｜"), "｜気合い｜")


class TestTracks(unittest.TestCase):
    def test_copy_beats_others(self):
        t = _table()
        hit = PF.match_line("|::ﾘﾍヾ___,,,j:/Ｖ:/ﾐ/!I", t)
        self.assertEqual(hit["track"], "copy")
        self.assertTrue(hit["copy"])

    def test_copy_returns_source_line_verbatim(self):
        t = _table()
        line = "　|::ﾘﾍヾ___,,,j:/Ｖ:/ﾐ/!I　"     # 带首尾全角空格
        hit = PF.match_line(line, t)
        ok, zh, why = PF.finalize(line, hit)
        self.assertTrue(ok)
        self.assertEqual(zh, line)               # 连 padding 都不动

    def test_rule_track_state_line(self):
        t = _table()
        hit = PF.match_line("ぼうぎょ：C", t)
        self.assertEqual(hit["track"], "rule")
        ok, zh, _ = PF.finalize("ぼうぎょ：C", hit)
        self.assertTrue(ok)
        self.assertTrue(zh.startswith("防御：C"))                  # 尾部 pad 到源宽
        self.assertEqual(A.disp_width(zh), A.disp_width("ぼうぎょ：C"))

    def test_rule_beats_copy_for_kana_only_state_line(self):
        """`とくこう：B`（全假名、无汉字、无 ASCII 笔画）曾落进 copy 轨被原样照抄。"""
        t = _table()
        t["derived"]["copy"][PF.key(" とくこう：B")] = {"_src": "pure_art", "n": 99}
        hit = PF.match_line(" とくこう：B", t)
        self.assertEqual(hit["track"], "rule")
        ok, zh, _ = PF.finalize(" とくこう：B", hit)
        self.assertTrue(zh.startswith(" 特攻：B"))

    def test_rule_track_full_width_padding_kept_width(self):
        t = _table()
        line = "　　とくこう：S＋　"
        ok, zh, _ = PF.finalize(line, PF.match_line(line, t))
        self.assertTrue(ok)
        self.assertEqual(A.disp_width(zh), A.disp_width(line))

    def test_struct_track(self):
        t = _table()
        line = "・『気合い』…　　　　　　　稀に気合いで相手の攻撃を耐える。"
        hit = PF.match_line(line, t)
        self.assertEqual(hit["track"], "struct")
        ok, zh, _ = PF.finalize(line, hit)
        self.assertTrue(ok)
        self.assertIn("气魄", zh)
        self.assertEqual(A.disp_width(zh), A.disp_width(line))

    def test_struct_track_preserves_desc_column(self):
        """说明起始列必须复刻源行（2026-09-22 修：曾漏算 desc 组前导空白 → 说明被顶到 … 后面）。"""
        t = _table()
        line = "・『気合い』…　　　　　　　稀に気合いで相手の攻撃を耐える。"
        ok, zh, _ = PF.finalize(line, PF.match_line(line, t))
        self.assertTrue(ok)

        def col(s):
            m = re.search(r"』…([\s\u3000]*)(\S)", s)
            return A.disp_width(s[: m.start(2)])

        self.assertEqual(col(zh), col(line))
        self.assertEqual(A.disp_width(zh), A.disp_width(line))

    def test_whole_track(self):
        t = _table()
        hit = PF.match_line("◎こうかはばつぐんだ！", t)
        self.assertEqual(hit["track"], "whole")

    def test_no_match(self):
        self.assertIsNone(PF.match_line("普通の台詞です", _table()))


class TestGuards(unittest.TestCase):
    def test_build_guard_rejects_kana_and_echo(self):
        self.assertFalse(PF.guard_build("残留ひらがなです")[0])
        self.assertFalse(PF.guard_build("")[0])
        self.assertFalse(PF.guard_build("気合い", "気合い")[0])        # 照抄原文
        self.assertTrue(PF.guard_build("气魄")[0])

    def test_pure_art_detection(self):
        self.assertTrue(PF.is_pure_art("|::ﾘﾍヾ___,,,j:/Ｖ:/ﾐ/!I"))
        self.assertTrue(PF.is_pure_art(".:.:.:.:.｀`ヽ,､､ノヽob.〔」ﾞ'''''l〕.::;:;"))
        self.assertFalse(PF.is_pure_art("ぼうぎょ：C"))            # 状态行 → 规则轨
        self.assertFalse(PF.is_pure_art("【持ち物】"))             # 汉字 = 可能有内容
        self.assertFalse(PF.is_pure_art(""))
        # 2026-09-22 真机教训：欢呼台词（无 ASCII 笔画）曾被判纯图形 → 永久跳过翻译
        self.assertFalse(PF.is_pure_art(" 　ワー！ﾊﾟﾁﾊﾟﾁﾊﾟﾁ　　　"))
        self.assertFalse(PF.is_pure_art(" とくこう：B"))

    def test_has_real_text_gate_for_copy_evidence(self):
        """交付证据（tr==src）不足以证明"无需翻译"：行内有真日文内容时必须禁入 copy 轨。

        5 章真机实证：`…【現在のパーティ】（データは>>4-7にて）` 在 3 个已交付章里模型没译（旧交付漏译），
        却因 tr==src 被当"稳定"证据收进 copy 轨 → 新章照抄日文。另有一整类整行台词漏译同源。
        """
        self.assertTrue(PF.has_real_text(" 【現在のパーティ】（データは>>4-7にて）"))
        self.assertTrue(PF.has_real_text("そのくらいにはスクールで暴れ回った"))
        self.assertTrue(PF.has_real_text("森田？"))                     # 非艺术汉字
        self.assertFalse(PF.has_real_text("///二二二ﾆﾆﾆ二7//／"))       # 艺术汉字 二
        self.assertFalse(PF.has_real_text(".｀`ヽ,､､ノヽob.〔」ﾞ'''''l〕"))  # 全角片假名当笔画
        self.assertFalse(PF.has_real_text("|::ﾘﾍヾ___,,,j:/Ｖ:/ﾐ/!I"))

    def test_suspect_zh_catches_model_damage(self):
        """模型把 ASCII 词劈开的迹象（`◎宝可梦w　iki【…】`）→ 闸三，不入表。"""
        self.assertEqual(PF.suspect_zh("◎宝可梦w　iki【ttp://…】"), "ascii_split")
        self.assertEqual(PF.suspect_zh("◎宝可梦wiki【ttp://…】"), "")


class TestTableIO(unittest.TestCase):
    def test_derived_sections_present(self):
        t = PF.empty_table()
        for sec in ("struct", "whole", "copy", "rules"):
            self.assertIn(sec, t["derived"])

    def test_manual_never_overwritten_by_build(self):
        """--rebuild 只重生成 derived，manual 区永不动（D9）。"""
        t = PF.empty_table()
        t["manual"]["whole"] = {"X": {"zh": "人工"}}
        t2 = PF.empty_table()
        # 模拟 build 的重置动作
        t2["derived"] = {"struct": {}, "whole": {}, "copy": {}, "rules": t2["derived"]["rules"]}
        self.assertNotIn("X", t2["derived"]["whole"])
        self.assertEqual(t["manual"]["whole"]["X"]["zh"], "人工")


class TestL2Template(unittest.TestCase):
    """L2 模板/槽位规则表：骨架逐字一致，槽位同宽短形，槽位全解析 + 无假名残留才接管。"""

    TERMS = {"したでなめる": "舌舔", "なぎはらう": "横扫", "くろいほのお": "暗黑火焰"}

    def test_move_line_fully_ruled(self):
        """报告的那一行：状态行前缀 + 招式行 → 零模型全规则出译文，列位不动。"""
        line = " とくこう：B+\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000" \
               "◎「したでなめる」…\u3000物/霊/単/30/100/○/10割「麻痺」"
        hit = PF.match_line(line, _table(), self.TERMS)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["track"], "tpl")
        for want in ("特攻：", "◎「舌舔」", "物/灵/单/30/100/○/", "10成「麻痹」"):
            self.assertIn(want, hit["zh"])
        self.assertFalse(re.search(r"[ぁ-ゖァ-ヺ]", hit["zh"]))     # 无假名残留
        ok, zh, why = PF.finalize(line, hit)                  # 落盘路径：guard + pad 到源宽
        self.assertTrue(ok, why)                              # art 列位校验要过
        self.assertEqual(A.disp_width(zh), A.disp_width(line))

    def test_effect_prose_falls_back_to_model(self):
        """效果是散文（要模型翻）→ 规则不接管，整行交回模型。"""
        line = "◎「くろいほのお」…\u3000物/炎/単/130/85/×/自身の「攻」を上げる。"
        self.assertIsNone(PF.match_tpl(line, _table(), self.TERMS))

    def test_unknown_move_falls_back(self):
        line = "◎「未知のわざ」…\u3000物/炎/単/130/85/×/2割「火傷」"
        self.assertIsNone(PF.match_tpl(line, _table(), self.TERMS))

    def test_wider_replacement_falls_back(self):
        """译文比原文宽（列位必然动）→ pre 轨不接管。"""
        t = _table()
        t["slots_override"] = {"范围": {"単": "单体"}}          # 単(2列) → 单体(4列)
        line = "◎「なぎはらう」…\u3000物/炎/単/100/100/○/2割「火傷」"
        self.assertIsNone(PF.match_tpl(line, t, self.TERMS))

    def test_composite_type_mapped_charwise(self):
        line = "◎「なぎはらう」…\u3000物/悪（炎）/全/100/100/○/2割「火傷」"
        hit = PF.match_tpl(line, _table(), self.TERMS)
        self.assertIsNotNone(hit)
        self.assertIn("物/恶（火）/全/", hit["zh"])
        self.assertIn("2成「灼伤」", hit["zh"])

    def test_normalize_canonicalizes_translated_skeleton(self):
        """已译行：模型写的骨架拉回同一口径（鋼→钢、単→单、とくこう→特攻）。"""
        zh = " とくこう：C+\u3000\u3000\u3000◎「アンカーショット」…\u3000物/鋼/単/80/100/○/命中时，3回合内使对方陷入“黑眼神”状态"
        out = PF.normalize_tpl(zh, _table(), self.TERMS)
        self.assertIn("特攻：", out)
        self.assertIn("物/钢/单/80/100/○/", out)
        self.assertIn("命中时，3回合内使对方陷入", out)          # 正文一字不动

    def test_normalize_leaves_canonical_line_alone(self):
        """已规范的招式行 + 普通台词 → 原样返回（幂等）。"""
        for line in (" 特攻：　　B+\u3000◎「舌舔」\u3000\u3000\u3000\u3000…\u3000物/灵/单/30/100/○/10成「麻痹」",
                     "普通の台詞です、何も変わらない。"):
            self.assertEqual(PF.normalize_tpl(line, _table(), self.TERMS), line)

    def test_slot_overrides_from_extra_json(self):
        """槽位字典可人工覆盖（补充术语.json 的 `槽位` 段），同宽才生效。"""
        t = _table()
        t["slots_override"] = {"范围": {"単": "个"}}            # 同宽覆盖
        hit = PF.match_tpl("◎「なぎはらう」…\u3000物/炎/単/100/100/○/2割「火傷」", t, self.TERMS)
        self.assertIn("/个/", hit["zh"])


class TestL2SentenceTemplate(unittest.TestCase):
    """整句模板（补充术语.json 的 `模板` 段）：系统行零模型出译文，槽位解析不出就交回模型。"""

    TERMS = {"ガブリアス": "烈咬陆鲨", "カズマ": "和真", "ハクタイスタジアム": "百代竞技场",
             "熟達回復指令": "熟练回复指令"}

    def test_template_compiled_from_extra_json(self):
        rules = PF.tpl_templates()
        self.assertTrue(rules, "补充术语.json 的 模板 段应编译出规则")
        self.assertTrue(all(r.get("mode") == "template" for r in rules))
        self.assertTrue(all(r["rx"].pattern.startswith("^") for r in rules))

    def test_field_line_and_move_out(self):
        """用户上报的两类：`◎フィールド：《…》（>>N）`、`◎{训练师}は「{宝}」を繰り出した！`"""
        for line, want in (
            ("◎フィールド：《ハクタイスタジアム》（>>4076）", "◎场地：《百代竞技场》（>>4076）"),
            ("◎カズマは「ガブリアス」を繰り出した！", "◎和真派出了「烈咬陆鲨」！"),
        ):
            hit = PF.match_tpl(line, _table(), self.TERMS)
            self.assertIsNotNone(hit, line)
            self.assertTrue(hit.get("no_art"), "整句模板应标记跳过 art 锚点校验")
            ok, zh, why = PF.finalize(line, hit)
            self.assertTrue(ok, why)
            self.assertEqual(zh.rstrip("\u3000 "), want)
            self.assertEqual(A.disp_width(zh), A.disp_width(line))     # 段尾补位到源宽

    def test_single_kana_slot_rejects(self):
        """槽位里有单个假名（术语表没这条，如 `玉藻の前`）→ 规则不接管，绝不吐半日文。"""
        self.assertIsNone(PF.match_tpl("◎誰かは「玉藻の前」を繰り出した！", _table(), self.TERMS))

    def test_work_glossary_terms_merges_and_filters(self):
        """作品表译名并入 L2 槽位解析；脏译名（含假名）不入。"""
        import json
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        with open(os.path.join(d, "glossary_W.json"), "w", encoding="utf-8") as f:
            json.dump({"glossary": [{"jp": "カズマ", "zh": "和真"},
                                    {"jp": "ダメ", "zh": "まだ日本語"},
                                    {"jp": "同形", "zh": "同形"}]}, f, ensure_ascii=False)
        got = PF.work_glossary_terms("W", d)
        self.assertEqual(got.get(PF.norm("カズマ"), {}).get("zh"), "和真")
        self.assertNotIn(PF.norm("ダメ"), got)              # 译名含假名 → 丢弃
        self.assertNotIn(PF.norm("同形"), got)              # jp == zh → 丢弃


class TestTplTolerance(unittest.TestCase):
    """模板引擎通用容错：行首装饰（`◎`/`―――`/全角空格）原样回显、`「」`⇄`『』` 互认并按源风格回显。

    依据：皮一枚族 16 行原先全栽在这两处（`◎―――――エクシアは『気合い』によって、皮一枚で踏ん張った！@1`
    ——模板没写行首 `◎`/破折号，且模板用 `「」` 而源行用 `『』`）。
    """

    TERMS = {"レヴィ": "莱薇", "きあいのタスキ": "气势披带", "気合い": "气合",
             "したでなめる": "舌舔"}

    def test_leading_decoration_echoed(self):
        line = "◎――――――レヴィは「きあいのタスキ」によって、皮一枚で踏ん張った！@1"
        hit = PF.match_tpl(line, _table(), self.TERMS)
        self.assertIsNotNone(hit)
        self.assertTrue(hit["zh"].startswith("◎――――――"), hit["zh"])
        self.assertIn("莱薇靠「气势披带」", hit["zh"])
        self.assertFalse(re.search(r"[ぁ-ゖァ-ヺ]", hit["zh"]))
        ok, zh, why = PF.finalize(line, hit)
        self.assertTrue(ok, why)
        self.assertEqual(A.disp_width(zh), A.disp_width(line))

    def test_brackets_mirror_source_style(self):
        """源行用 `『』`（潜能/队伍名）→ 译文回显 `『』`；模板里写的是 `「」`。"""
        line = "レヴィは『気合い』によって、皮一枚で踏ん張った！@1"
        hit = PF.match_tpl(line, _table(), self.TERMS)
        self.assertIsNotNone(hit)
        self.assertIn("『气合』", hit["zh"])
        self.assertNotIn("「气合」", hit["zh"])

    def test_move_line_single_quotes_accepted(self):
        """招式行骨架用 `『技名』` 也认，且回显源引号。"""
        line = "◎『したでなめる』…\u3000物/霊/単/30/100/○/10割「麻痺」"
        hit = PF.match_tpl(line, _table(), self.TERMS)
        self.assertIsNotNone(hit)
        self.assertIn("『舌舔』", hit["zh"])

    def test_dash_only_line_not_matched(self):
        """整行都是装饰 → 不许误判成模板命中（前缀组只吃装饰、骨架仍必须匹配）。"""
        self.assertIsNone(PF.match_tpl("――――――――――", _table(), self.TERMS))

    def test_echo_brackets_unit(self):
        self.assertEqual(PF._echo_brackets("『あ』", "「い」"), "『い』")
        # 引号数量对不上 → 原样返回，绝不猜
        self.assertEqual(PF._echo_brackets("「あ」", "「い」』"), "「い」』")

    def test_connector_variants_accepted(self):
        """连接词容错：模板写 `「{源}」によって`，源行的 `により`/`を込めて`/`状態によって`/`で` 都吃。"""
        for line, want in (
            ("◎鬼鮫は「きあいのタスキ」により、皮一枚で踏ん張った！@0/1", "靠「气势披带」"),
            ("レヴィは「きあいのタスキ」を込めて、皮一枚で踏ん張った！", "靠「气势披带」"),
            ("レヴィは「きあいのタスキ」状態によって、皮一枚で踏ん張った！", "靠「气势披带」"),
            ("レヴィは「きあいのタスキ」で、皮一枚で踏ん張った！", "靠「气势披带」"),
        ):
            hit = PF.match_tpl(line, _table(), self.TERMS)
            self.assertIsNotNone(hit, line)
            self.assertIn(want, hit["zh"])
            self.assertFalse(re.search(r"[ぁ-ゖァ-ヺ]", hit["zh"]))

    def test_connector_tolerance_only_after_brackets(self):
        """放宽只在**引号收尾处**生效：`「…」によって` 编译出同义集；词内的 `に`/`で` 不动。

        做法是编译期对「`」` 紧跟连接词」的位置做 re.sub（lookbehind 只用于定位，不进 pattern），
        所以下面既断言真放宽了，也断言没把普通字面改花。
        """
        got = [r for r in PF.tpl_templates() if "皮一枚" in r["jp"] and "によって" in r["jp"]]
        self.assertTrue(got, "补充术语.json 应有 `…によって、皮一枚で踏ん張った！` 模板")
        self.assertIn("[」』](?:状態)?(?:によって|により", got[0]["rx"].pattern)
        self.assertEqual(set(PF._CONN_SRC.strip("(?:").rstrip(")").split("|")),
                         {"によって", "により", "を込めて", "を込め", "に", "で"})


    def test_align_quotes_unit(self):
        """引号序号对齐：只覆盖**源行第 k 个引号内容在标准表里确有 zh** 的那些引号。"""
        pairs = [{"jp": "対闘耐性", "zh": "对斗耐性"}, {"jp": "エース", "zh": "王牌"}]
        # 第 1 个引号名对齐（wiki 中文是「对格耐性」），第 2 个「闘」表里没有 → 原样
        self.assertEqual(PF._align_quotes("『対闘耐性』…　「闘」技", "『对格耐性』…　「格斗」技", pairs),
                         "『对斗耐性』…　「格斗」技")
        # 引号数量对不上 → 整条不动，绝不猜
        self.assertEqual(PF._align_quotes("『あ』", "『x』「y」", pairs), "『x』「y」")
        # 无标准条目 / 同形 → 原样
        self.assertEqual(PF._align_quotes("『不明』", "『不明中文』", pairs), "『不明中文』")
        self.assertEqual(PF._align_quotes("『エース』", "『王牌』", pairs), "『王牌』")
        self.assertEqual(PF._quote_spans("『a』「b」"), [(0, 3, "a"), (3, 6, "b")])
        # 弯引号（用户填表常用）也算引号对，同样参与对齐（实测：用户填的“范围效果”被拉回标准「全体招式」）
        self.assertEqual(PF._quote_spans("使对手的“范围效果”无效"), [(4, 10, "范围效果")])
        self.assertEqual(PF._align_quotes("相手の「全体技」を無効化", "使对手的“范围效果”无效",
                                          [{"jp": "全体技", "zh": "全体招式"}]),
                         "使对手的“全体招式”无效")


    def test_wiki_key_normalization(self):
        """属性表记差归一：wiki 页写平假名（`「こうげき」`）、章正文写汉字短形（`「攻」`）→ 查表键也认。"""
        self.assertEqual(PF.norm_alt("『攻撃指令』…味方1体の「こうげき」を上げる。"),
                         PF.norm_alt("『攻撃指令』…味方1体の「攻」を上げる。"))
        self.assertEqual(PF.norm_alt('『対闘耐性』…「ぼうぎょ」を上げる'), PF.norm_alt('『対闘耐性』…「防」を上げる'))
        # 波ダッシュ分歧：wiki 页 U+301C `〜` vs 章节正文 U+FF5E `～`（实测该族 top40 里 27 条栽在这）
        self.assertEqual(PF.norm_alt("強化（1.1〜1.4倍）"), PF.norm_alt("強化（1.1～1.4倍）"))
        d = {PF.norm_alt("「こうげき」"): "zh"}
        self.assertEqual(PF.wiki_lookup(d, PF.norm("「攻」")), "zh")      # 归一后命中
        self.assertEqual(PF.wiki_lookup(d, PF.norm("「こうげき」")), "zh")  # 原样命中
        self.assertIsNone(PF.wiki_lookup(d, PF.norm("「命中」")))
        self.assertIsNone(PF.wiki_lookup(None, "x"))

    def test_names_consistent_exact_span_only(self):
        """译名自检只对「引号内容整体等于该 jp」强制：子串不再误伤（`つるぎ`⊂`せいなるつるぎ`）。"""
        pairs = [{"jp": "つるぎ", "zh": "斩"}, {"jp": "エース", "zh": "王牌"}]
        # 引号内是更长的名字（せいなるつるぎ→圣剑）→ 不管
        self.assertTrue(PF._names_consistent("技「せいなるつるぎ」を繰り出す。", "使出招式「圣剑」。", pairs))
        # 引号内容整体就是标准名 → 输出没用标准 zh 就拒
        self.assertFalse(PF._names_consistent("『エース』…説明", "『ACE』…説明", pairs))
        self.assertTrue(PF._names_consistent("『エース』…説明", "『王牌』…説明", pairs))
        self.assertTrue(PF._names_consistent("没有引号的行", "……", pairs))


class TestMergedTermsPriority(unittest.TestCase):
    """合并词表优先级：通用 wiki(0) < 本作 wiki 名字(1) < 人工补充/作品专属(1，人工优先) < 作品表(2)。

    事故（2026-10-18）：本作 wiki names 里 `ヒワマキジム → 桧皮道馆` 在旧实现里**最后 update**，
    压掉了人工补充（`补充术语.json`）的正确条目「茵郁道馆」→ 模板轨 `◎フィールド：《{场}》` 回填
    把已正确的行改成错译。修法 = extra_pairs 用覆盖而非 setdefault，且排在 names 之后。
    """

    def _run(self, wiki, names, extra, worktab):
        import pre_terms as PT
        with mock.patch.object(PF, "wiki_terms", return_value=wiki), \
             mock.patch.object(PF, "work_wiki_sources", return_value={"names": names}), \
             mock.patch.object(PT, "extra_pairs", return_value=extra), \
             mock.patch.object(PF, "work_glossary_terms", return_value=worktab):
            return PF.merged_terms("X", "/tmp/nowhere")

    def test_人工补充压过本作wiki名字(self):
        out = self._run({"z": "0"}, {"ヒワマキジム": "桧皮道馆"},
                        [("ヒワマキジム", "茵郁道馆", "wiki-补充", "场地")], {})
        self.assertEqual(out["ヒワマキジム"], "茵郁道馆")

    def test_人工补充压过生成物wiki(self):
        """`补充术语.json` 是源、`术语表.json` 是生成物：生成物滞后时以源为准。"""
        out = self._run({"指示安価": "指示底价"}, {}, [("指示安価", "指示安价", "wiki-补充", "系统行")], {})
        self.assertEqual(out["指示安価"], "指示安价")

    def test_作品表最高优先(self):
        out = self._run({}, {"でっきー": "本作wiki口径"}, [("でっきー", "补充口径", "wiki-补充", "专名")],
                        {"でっきー": "作品表口径"})
        self.assertEqual(out["でっきー"], "作品表口径")

    def test_本作wiki名字仍能并进来(self):
        """回归：本作借名个体（インコマン 等）只在 names 里 → 必须仍然可解析。"""
        out = self._run({"z": "0"}, {"インコマン": "印可男"}, [], {})
        self.assertEqual(out["インコマン"], "印可男")


if __name__ == "__main__":
    unittest.main()
