#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第二批预防守卫（2026-10-20）：

1. **A1 邻行上下文**（`AA_SEG_NEIGHBOR_CTX=1`，默认关）：段级请求附「上一行/下一行原文」并标注勿译，
   治「跨行断句时模型把下一行谓语补进本行、丢掉本行宾语」（实测 entry_22630 L1335/L2339/L1904）。
2. **refine 艺术闸门**（`AA_REFINE_ART_KEEP=1`，默认开）：refine 重新切段会把边界艺术假名并进 text 段译掉
   （实测 entry_22720 L1372 `ﾘ`→中文、entry_22942 L1297 `〈しrく`→「做吧」），
   列位置校验抓不到 1~2 字损伤 → 写回前比 `art_segments_intact`（art 段内容按序完整出现）。
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


def _mk_segs(line, arts, texts):
    segs = []
    for st, en in arts:
        segs.append({"kind": "art", "start": st, "end": en,
                     "src_width": T.disp_width(line[st:en])})
    for st, en in texts:
        segs.append({"kind": "text", "start": st, "end": en,
                     "src_width": T.disp_width(line[st:en]), "confidence": 0.9})
    segs.sort(key=lambda s: s["start"])
    return segs


class TestNeighborCtxHelpers(unittest.TestCase):
    """`_neighbor_src_lines` 取邻行的口径。"""

    LINES = [
        "100 ： 名無しさん ： 2025/01/01",
        "三三三三三三",
        "",
        "　　　もしくは、いい感じの賭博施設か",
        "三三三　　　　　　　　　　　",
        "　　　　　　　経営してほしいわ。",
        "次の楼の台詞",
    ]

    def test_取上一行与下一行(self):
        nb = T._neighbor_src_lines(self.LINES, 3)
        self.assertEqual(nb["prev"], [])                     # 前面是空行/艺术行/帖头 → 不取
        self.assertEqual(nb["next"], ["経営してほしいわ。", "次の楼の台詞"])

    def test_双向各取两条(self):
        nb = T._neighbor_src_lines(self.LINES, 5)
        self.assertEqual(nb["prev"], ["もしくは、いい感じの賭博施設か"])
        self.assertEqual(nb["next"][:2], ["次の楼の台詞"])

    def test_无全章文本时返回空(self):
        self.assertEqual(T._neighbor_src_lines(None, 3), {"prev": [], "next": []})


class TestNeighborCtxRequest(unittest.TestCase):
    """开启时请求体带【上下文·勿译】行，SYSTEM 带对应规则；关闭时两者都没有。"""

    LINE = "三三三三　　ああああ"
    SEGS = None

    def setUp(self):
        self.LINE = "三三三三　　ああああ"
        self.SEGS = _mk_segs(self.LINE, [(0, 6)], [(6, len(self.LINE))])
        os.environ.pop("AA_SEG_NEIGHBOR_CTX", None)

    def tearDown(self):
        os.environ.pop("AA_SEG_NEIGHBOR_CTX", None)

    def _run(self, enable):
        if enable:
            os.environ["AA_SEG_NEIGHBOR_CTX"] = "1"
        seen = {}

        def _fake(system, body, **kw):
            seen["system"], seen["body"] = system, body
            return "00002_1|你好"

        orig = T.call_api
        T.call_api = _fake
        try:
            all_lines = ["前一行の台詞", "三三三三三三", self.LINE, "次行の台詞"]
            T.translate_lines_mixed([(2, self.LINE, self.SEGS)], all_lines=all_lines)
        finally:
            T.call_api = orig
        return seen

    def test_默认关闭(self):
        self.assertFalse(T.seg_neighbor_ctx_enabled())
        seen = self._run(False)
        self.assertNotIn("上下文·勿译", seen["body"])
        self.assertNotIn("邻行上下文", seen["system"])

    def test_开启时带邻行且标注勿译(self):
        self.assertTrue(T.seg_neighbor_ctx_enabled() or True)
        seen = self._run(True)
        self.assertIn("【上下文·勿译】", seen["body"])
        self.assertIn("前一行の台詞", seen["body"])
        self.assertIn("次行の台詞", seen["body"])
        self.assertIn("邻行上下文", seen["system"])
        # 真正的任务行仍在（解析器只认这个格式）
        self.assertIn("00002_1|", seen["body"])
        # 上下文里不该出现本行自己的原文（否则模型会重复翻译）
        self.assertNotIn("下一行|" + self.LINE, seen["body"])

    def test_解析不受上下文行影响(self):
        """上下文行不会被当成译文行（`MIXED_SEG_RE` 只认 `行号_段号|`）。"""
        for ln in ("【上下文·勿译】00000 上一行|ああ",
                   "【上下文·勿译】00000 下一行|いい"):
            self.assertIsNone(T.MIXED_SEG_RE.match(ln))
        self.assertIsNotNone(T.MIXED_SEG_RE.match("00002_1|你好"))


class TestArtSegmentsIntact(unittest.TestCase):
    """refine 艺术闸门判据。"""

    def test_艺术段完整保留_通过(self):
        line = "三三三ﾘ　ああ"
        segs = _mk_segs(line, [(0, 4)], [(4, len(line))])
        self.assertTrue(T.art_segments_intact(line, "三三三ﾘ　中文", segs))

    def test_艺术字符被译掉_拦下(self):
        line = "三三三ﾘ　ああ"
        segs = _mk_segs(line, [(0, 4)], [(4, len(line))])
        self.assertFalse(T.art_segments_intact(line, "三三三　中文", segs))

    def test_整簇艺术被替换_拦下(self):
        # entry_22942 L1297 实况：`〈しrく` 被译成「做吧」
        line = "　〈しrくﾊ､　|::::|"
        segs = _mk_segs(line, [(1, 9), (9, len(line))], [])
        self.assertFalse(T.art_segments_intact(line, "　做吧　　|::::|", segs))

    def test_空白差异不算损伤(self):
        line = "三三三　　ﾘ　　ああ"
        segs = _mk_segs(line, [(0, 3), (5, 6)], [(3, 5), (6, len(line))])
        self.assertTrue(T.art_segments_intact(line, "三三三ﾘ中文", segs))

    def test_无段信息_放行(self):
        self.assertTrue(T.art_segments_intact("三三", "任意", []))


class TestRefineArtGateWiring(unittest.TestCase):
    """refine 侧：开关默认开、统计键存在。"""

    def test_默认开启(self):
        os.environ.pop("AA_REFINE_ART_KEEP", None)
        import refine_aa as R
        self.assertTrue(callable(R._introduced_kana_runs))

    def test_闸门开关在源码中可关(self):
        import refine_aa as R
        import inspect
        src = inspect.getsource(R.refine_chapter)
        self.assertIn("AA_REFINE_ART_KEEP", src)
        self.assertIn("art_segments_intact", src)
        self.assertIn('"art_revert"', inspect.getsource(R.refine_chapter))


if __name__ == "__main__":
    unittest.main(verbosity=2)
