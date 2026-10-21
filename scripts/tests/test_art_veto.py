# -*- coding: utf-8 -*-
"""艺术行否决（art_line_veto / AA_ART_VETO）单元测试。零依赖 unittest。

运行：.venv/Scripts/python.exe -m unittest discover -s scripts/tests
口径：整行纯艺术笔画（无假名可译）→ 本地照抄不进模型；
     真词/真台词（平假名、实义汉字、真片假名词、半角假名 sfx、笑声、句末标点、引号）必须保留。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import aa_translate as T

# 白头与染灰魔女 实际艺术行（模型原样复述）
ART_LINES = [
    '∨ |　:|　√∨　-二二V　/⌒V　∧/‐‐二二二--　　￣　ノ(　　/　∨　＿乂⌒　/i　　　／￣',
    '￣^|　|Ｖ￣-- - -─　 ∨二二∨二￣￣＿ _ _　--二二-ｰ\'＾7\'´￣√二─ ﾍ　 /∧ :／',
    '　　　　　　　　　　l　i三＞ｲ三三三ニニニニﾆ三三三　i;　\',ヽ　/　　 /ニｲ /／三三1',
    '　　|　　　 ｉ　 |: :.′ニニニニﾆ=-}　}　　　__　　-=≦⌒ : : : : : : : .: .:.:.:.:.:.:.:.',
    '　　　　　　 　 　　　　　　　　　　　　　　　　　　　　 ミニニニニニニニニﾆ}-} {-{ニニ',
    '／ニニ/|　　 ＼: :≧=-ｧ\'　　　 _ｊｋ i{/: : : : :∨/: : : : :∨/: : ＼} |　　　　　 (乂 ） ‐l',
    '丁二丁¨||¨|| /. :/. :.Ⅶiニ=-.:-=ニニﾆ=-ヽ._／⌒ヽ:.　　　　ヽ ｉ￣￣/ | [二二||=|:|:|:|`、',
]
# 必须保留（真内容：台词/专名/sfx/笑声/标点/引号）
KEEP_LINES = [
    '　　　　　　　　　　　ハハハ',                                  # 笑声（同字重复但非 AA 墙用字）
    'ｲｲ……',                                                     # 半角假名台词
    '　　　　　　　　　ｸﾙｯ',                                       # 半角假名 sfx（相异 >=2）
    '　　　　　　　／::::⌒（__人__）⌒:::::::ヽ　　ｸﾞﾋﾞｯ',            # 行尾 sfx
    'ノ: : : : :／: : : : : i: : : :..｀寸x:丶: : : : : : : : : 丶　　　　　ウオッカ　　　　　　　３０ｍｌ',
    '／　─　⊂三乂　ヽ　　　　　　　　　　　ライナー',                  # 行尾角色名
    '│アブドゥーグ　　 　 .│ : : : : : : : : : : : : : : : : : :丶',
    '|::::::/　　　　　 i;;;;;;|　　　　　　『 マティーニ 』',            # 引号包裹
    '　　　　　　　　　　ナニソレ　ワカンナイ',                        # 纯片假名台词
    '　　ｲﾀﾀﾀﾀ',                                                   # 带重复的半角 sfx
    '440 ： ◆GESU1/dEaE ： 2021/05/06(木) 23:19:36 ID:nGcM5Umt',   # 帖头
    '　　　　　　　　　　　　　　　え！？',                            # 句末标点（假名行）
    '*....|ぁ|',                                                   # 太短：不进入判据
]


class TestArtLineVeto(unittest.TestCase):
    def test_art_lines_vetoed(self):
        for l in ART_LINES:
            self.assertTrue(T.art_line_veto(l), '应否决: %r' % l)

    def test_real_lines_kept(self):
        for l in KEEP_LINES:
            self.assertFalse(T.art_line_veto(l), '不该否决: %r' % l)

    def test_wall_run_vetoes(self):
        self.assertTrue(T.art_line_veto('　　　　ニニニニニニ'))

    def test_same_char_repeat_not_wall_kept(self):
        # ハハハ/あああ 等非 AA 墙用字 → 视为台词/笑声，不否决
        self.assertFalse(T.art_line_veto('　　　　ハハハ'))
        self.assertFalse(T.art_line_veto('　　　　ああああ'))

    def test_real_kanji_kept(self):
        # 实义汉字（非 ART_KANJI）→ 内容行
        self.assertFalse(T.art_line_veto('　　　　　世界平和'))

    def test_hiragana_kept(self):
        self.assertFalse(T.art_line_veto('　　　　それはそうだな'))

    def test_too_short_not_vetoed(self):
        for l in ('', '　', 'ニニ', '　　ﾆﾆ'):
            self.assertFalse(T.art_line_veto(l), '短行不该否决: %r' % l)

    def test_env_switch(self):
        old = os.environ.get('AA_ART_VETO')
        old_row = os.environ.get('AA_ART_ROWSEG')
        try:
            os.environ['AA_ART_VETO'] = '0'
            self.assertFalse(T.art_veto_enabled())
            # 2026-10-21: 艺术行还有第二道闸门（甲-lite `line_is_art`，`AA_ART_ROWSEG`）——
            # 两道都关才真的送模型（单关 veto 时仍被甲-lite 拦，这是有意的分层）
            os.environ['AA_ART_ROWSEG'] = '0'
            self.assertTrue(T.is_translatable(0, ART_LINES[0], {0: 'text'}))
            self.assertTrue(T.is_translatable(0, ART_LINES[0], None))
            os.environ.pop('AA_ART_ROWSEG', None)
            self.assertFalse(T.is_translatable(0, ART_LINES[0], {0: 'text'}))
            os.environ['AA_ART_VETO'] = '1'
            self.assertTrue(T.art_veto_enabled())
            self.assertFalse(T.is_translatable(0, ART_LINES[0], {0: 'text'}))
            self.assertFalse(T.is_translatable(0, ART_LINES[0], None))
        finally:
            if old is None:
                os.environ.pop('AA_ART_VETO', None)
            else:
                os.environ['AA_ART_VETO'] = old
            if old_row is None:
                os.environ.pop('AA_ART_ROWSEG', None)
            else:
                os.environ['AA_ART_ROWSEG'] = old_row

    def test_meta_text_line_still_vetoed(self):
        # meta 判 text 的艺术行也必须被否决（本次改动的主要目的）
        self.assertFalse(T.is_translatable(3, ART_LINES[1], {3: ('text', '')}))
        # meta 判 mixed 的同样否决
        self.assertFalse(T.is_translatable(3, ART_LINES[1], {3: 'mixed'}))

    def test_real_line_still_sent(self):
        real = '　　　　なるほど、そういうことか'
        self.assertTrue(T.is_translatable(5, real, {5: 'text'}))
        self.assertTrue(T.is_translatable(5, real, None))


if __name__ == '__main__':
    unittest.main()
