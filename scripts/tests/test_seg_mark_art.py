#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""段级误标判据（2026-10-20）：`aa_translate.seg_mark_art` + 分类器接线。

被误标的是**夹在画面中间的单个汉字笔画碎片**（`囗`/`公`/`必`/`仆`/`叭`/`只`/`守`/`刋`…），
送模型只会被原样照抄（偶尔译成汉字 → 画面坏），却把「漏译体检」灌成噪声。
判据刻意收得很窄：只降 1 字、零假名、非引号、无数字/ASCII、且行内两侧紧邻都是 art。
"""
import os
import sys
import unittest

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common"),
           os.path.join(_SCRIPTS, "works"), os.path.join(_SCRIPTS, "fix")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import aa_translate as T          # noqa: E402


def _mk(line, kinds_and_text):
    """按 [(kind, 文本)] 顺序造 segments（字符连续、带 src_width）。"""
    segs, pos = [], 0
    for kind, txt in kinds_and_text:
        segs.append({"kind": kind, "start": pos, "end": pos + len(txt),
                     "src_width": T.disp_width(txt)})
        pos += len(txt)
    return segs


class TestSegMarkArt(unittest.TestCase):
    def setUp(self):
        os.environ.pop("AA_SEG_MARK_ART", None)

    def tearDown(self):
        os.environ.pop("AA_SEG_MARK_ART", None)

    def test_单字碎片夹在画里_降级(self):
        line = "三三三囗三三"
        segs = _mk(line, [("art", "三三三"), ("text", "囗"), ("art", "三三")])
        self.assertTrue(T.seg_mark_art(line, segs, 1))

    def test_有假名_不降级(self):
        line = "三三三あ三三"
        segs = _mk(line, [("art", "三三三"), ("text", "あ"), ("art", "三三")])
        self.assertFalse(T.seg_mark_art(line, segs, 1))

    def test_引号术语_不降级(self):
        line = "三三『免』三三"
        segs = _mk(line, [("art", "三三"), ("text", "『免』"), ("art", "三三")])
        # 3 字且带引号：长度>1 已排除；再单独验 1 字带引号的情况
        self.assertFalse(T.seg_mark_art(line, segs, 1))
        line2 = "三三『』三三"
        segs2 = _mk(line2, [("art", "三三"), ("text", "『」"), ("art", "三三")])
        self.assertFalse(T.seg_mark_art(line2, segs2, 1))

    def test_多字汉字词_不降级(self):
        """`実利`/`資格`/`仕事内容` 这类必须译（降级会把日文汉字原样留下）。"""
        for word in ("実利", "資格", "仕事内容", "緊張"):
            line = "三三" + word + "三三"
            segs = _mk(line, [("art", "三三"), ("text", word), ("art", "三三")])
            self.assertFalse(T.seg_mark_art(line, segs, 1), word)

    def test_数字与ASCII标签_不降级(self):
        for word in ("2", "Ｆ", "1"):
            line = "三三" + word + "三三"
            segs = _mk(line, [("art", "三三"), ("text", word), ("art", "三三")])
            self.assertFalse(T.seg_mark_art(line, segs, 1), word)

    def test_两侧不是art_不降级(self):
        line = "三三囗好好"
        segs = _mk(line, [("art", "三三"), ("text", "囗"), ("text", "好好")])
        self.assertFalse(T.seg_mark_art(line, segs, 1))

    def test_行首行尾段_不降级(self):
        line = "囗三三三"
        segs = _mk(line, [("text", "囗"), ("art", "三三三")])
        self.assertFalse(T.seg_mark_art(line, segs, 0))

    def test_开关关闭(self):
        line = "三三三囗三三"
        segs = _mk(line, [("art", "三三三"), ("text", "囗"), ("art", "三三")])
        os.environ["AA_SEG_MARK_ART"] = "0"
        self.assertFalse(T.seg_mark_art(line, segs, 1))

    def test_送段入口已接线(self):
        line = "三三三囗三三"
        segs = _mk(line, [("art", "三三三"), ("text", "囗"), ("art", "三三")])
        self.assertEqual(T._sendable_text_segs(line, segs), [])
        # 有假名的段照旧要送
        line2 = "三三三あ三三"
        segs2 = _mk(line2, [("art", "三三三"), ("text", "あ"), ("art", "三三")])
        self.assertEqual(T._sendable_text_segs(line2, segs2), [1])


class TestClassifierWiring(unittest.TestCase):
    """meta 生成端与运行期同口径。"""

    def test_分类器走同一判据(self):
        sys.path.insert(0, os.path.join(_SCRIPTS, "tools"))
        import line_classify_v2 as LC
        import inspect
        src = inspect.getsource(LC._demote_non_dialogue_text_segs)
        self.assertIn("_seg_mark_art", src)

    def test_分类器降级单字碎片(self):
        sys.path.insert(0, os.path.join(_SCRIPTS, "tools"))
        import line_classify_v2 as LC
        row = "三三三囗三三"
        segs = _mk(row, [("art", "三三三"), ("text", "囗"), ("art", "三三")])
        out = LC._demote_non_dialogue_text_segs(segs, row)
        self.assertEqual([s["kind"] for s in out], ["art"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
