# -*- coding: utf-8 -*-
"""段级「数字记号/词内斜杠」rescue 用例（2026-10-17，零 API）。

背景：`is_art_char` 把 ASCII 数字与 `. / - ~` 判 art 是对的（AA 笔画大量用它们），
段级协议按字符切段 → `1.5倍` 被切成 art `1.` + text `5倍`，送模型的是残句：
实测译文把数字翻成「1.变成5倍」「1/都说到3」「3-是3」，或整句前半原样留日文
（`◎先発で…強化（1.变成了1倍）！`），art 的 `.` 还要按列位守 → 补位塞进数字里
（`（全1　　　.33倍）`）。修法 = `_rescue_inline_numeric`（紧贴非 art 文本的数字串/
词内斜杠改判 text）。

运行：.venv/Scripts/python.exe -m unittest scripts.tests.test_segment_numeric -v
"""
import os
import sys
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (SCRIPTS, os.path.join(SCRIPTS, "tools"), os.path.join(SCRIPTS, "common")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import line_classify_v2 as LC          # noqa: E402
import aa_translate as T               # noqa: E402

NUMRE = LC._NUM_RUN_RE


def kinds_of(row):
    return [('ws' if ch.isspace() else ('art' if LC.is_art_char(ch) else 'text')) for ch in row]


def seg(row):
    return [(g['kind'], row[g['start']:g['end']]) for g in LC.segment_line(row)]


def texts(row):
    return [t for k, t in seg(row) if k == 'text']


class TestRescueInlineNumeric(unittest.TestCase):
    """字符级判据（看 rescue 的**增量**：数字本身早就是 text，救的是 `. / - ~` 这些 art 分隔符）。"""

    def rescued(self, row, idx):
        before = kinds_of(row)
        ks = list(before)
        LC._rescue_inline_numeric(row, ks)
        return [ks[i] for i in idx], [before[i] for i in idx]

    def assert_rescued(self, row, idx):
        got, before = self.rescued(row, idx)
        self.assertEqual(got, ['text'] * len(idx), f"{row!r} 应把 {idx} 判 text")
        self.assertTrue(all(b == 'art' for b in before), f"{row!r} 救之前应是 art")

    def assert_untouched(self, row):
        got, before = self.rescued(row, list(range(len(row))))
        self.assertEqual(got, before, f"{row!r} 不该被 rescue 改动")

    def test_小数与比例与比分(self):
        self.assert_rescued('1.5倍', [1])                 # '.'
        self.assert_rescued('（1.1倍）', [2])
        self.assert_rescued('1/3以下', [1])                # '/'
        self.assert_rescued('3-3だ', [1])                  # '-'
        self.assert_rescued('1.1～1.5倍', [1])             # '.'（'～' 本来就是 text）

    def test_词内斜杠(self):
        self.assert_rescued('あく/じめん', [2])
        self.assert_rescued('物理/特殊', [2])
        self.assert_rescued('テラス/タイプ', [3])

    def test_艺术行反例不动(self):
        for row in ('／⌒＼',                # 全角斜杠是笔画（两侧 art/空白）
                    '人/王',                # 人=ART_KANJI
                    'ｶﾞｶﾞｶﾞ/ﾆﾆﾆ',           # 半角假名纹理
                    'l. :. :.',             # 'l' 是 art 字符、'.' 后是空白
                    '1人',                  # 数字贴 ART_KANJI（人）→ 画里的数字
                    '　1.5　',              # 两侧空白 → 不是词内
                    '12 34',                # 不跨空白
                    '／／'):                 # 无数字
            self.assert_untouched(row)


class TestSegmentLineNumeric(unittest.TestCase):
    """整行分段：数字记号必须留在**同一个 text 段**里（残句送模型=broke 数字）。"""

    ROWS = [
        '◎先発で場に出たジェンティルドンナは、『先発』によって自身の全能力を強化（1.1倍）した！',
        'とはいえ、「砂嵐」で「特防」が1.5倍になってるってことで………',
        'わかってますって、1つめのジム戦は「2-2」！そして2つめと3つめは「3-3」だってことくらいっ！！',
        '【掘り出しもの市の戦利品】　このレスから本日5/9付け安価より募集します',
        '特性「ハードロック」が、相手の「こうかばつぐん」の技のダメージを緩和（0.67倍）した！',
        '『F-ZERO』…　優先度+1以上の技の威力を強化（1.1～1.5倍）する。',
        '『先發』による「防」にかかる1.1倍の強化を無視できる、と',
        '『火竜娘の激昂』…　　　1/試/自動　自身の体力が1/2以下になった時、自身の「特攻」が上がる。',
        'あく/じめんの相手には効果がない',
    ]

    def assert_no_split(self, row):
        """任何数字记号都不得跨越段边界（段边界不能落在记号内部）。"""
        for m in NUMRE.finditer(row):
            s, e = m.span()
            for g in LC.segment_line(row):
                self.assertFalse(g['start'] < e and g['end'] > s and g['start'] > s and g['end'] < e,
                                 f"数字记号 {m.group(0)!r} 被切开: {seg(row)}")

    def test_数字记号不被切开(self):
        for row in self.ROWS:
            self.assert_no_split(row)

    def test_整句成为一个文本段(self):
        self.assertEqual(texts(self.ROWS[0]), ['先発で場に出たジェンティルドンナは、『先発』によって自身の全能力を強化（1.1倍）した！'])
        self.assertEqual(texts('あく/じめんの相手には効果がない'), ['あく/じめんの相手には効果がない'])

    def test_段覆盖整行且宽度守恒(self):
        for row in self.ROWS:
            segs = LC.segment_line(row)
            self.assertEqual(segs[0]['start'], 0)
            self.assertEqual(segs[-1]['end'], len(row))
            for a, b in zip(segs, segs[1:]):
                self.assertEqual(a['end'], b['start'])
            self.assertEqual(sum(g['src_width'] for g in segs), T.disp_width(row))

    def test_艺术行仍不送模型(self):
        # 画面笔画（点阵/斜杠/装饰假名）不得因本次 rescue 变成文本段
        for row in ('　／⌒＼　　ﾉ　　　人/王',
                    ":. :. :. :. :. l. :. :.'`;:; ,.: ;oV\"´"):
            self.assertEqual([k for k, _ in seg(row)], ['art'] * len(seg(row)), row)


if __name__ == "__main__":
    unittest.main()
