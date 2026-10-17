# -*- coding: utf-8 -*-
"""行锚点回显（治「块内串行错位」，2026-10-17）：判据 + 端到端（mock 模型，零 API）。

背景：主翻译块「行号|原文」清单里模型偶发丢行后**整体串行**——把后面某行的译文写到本行的行号下
（实测 entry_43337：2768←tr(2770)、2911←tr(2915)；错位值仍按本行源宽补位 → 四重校验全过）。
做法：请求行写成「行号|锚点|原文」并要求回显锚点；模型串行时回显的是**别行的锚点** → 判「未验证」重发。
本用例覆盖：
  ① line_anchor 取锚点（跳前导空白/边框装饰）；
  ② parse_result_anchored：相符 / 串行（别行专属锚点）/ 无锚点回退旧口径 / 重复锚点不误判；
  ③ SYSTEM_ANCHOR 提示词已含锚点格式（防「改了 SYSTEM 忘改锚点版」）；
  ④ translate_lines 端到端：串行响应被拒 → 按原文重发 → 落地正确译文；模型不给锚点 → 回退旧行为；
     AA_ANCHOR=0 → 请求体无锚点。
运行：.venv/Scripts/python.exe -m unittest scripts.tests.test_anchor -v
"""
import os
import re
import sys
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in (SCRIPTS, os.path.join(SCRIPTS, "common"), os.path.join(SCRIPTS, "works")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import aa_translate as T          # noqa: E402


class TestLineAnchor(unittest.TestCase):
    def test_跳过前导空白与装饰(self):
        self.assertEqual(T.line_anchor(" 持ち物持ってるか、或いはなんか特性でもあるのか"), "持ち")
        self.assertEqual(T.line_anchor(" 　｜　こんにちは、お元気ですか？　｜"), "こん")
        self.assertEqual(T.line_anchor("│││やっぱり俺は魔女になるしかないのか"), "やっ")

    def test_不足与全装饰(self):
        self.assertEqual(T.line_anchor("あ"), "あ")
        self.assertEqual(T.line_anchor("　　｜｜"), "")
        self.assertEqual(T.line_anchor(""), "")

    def test_ASCII行(self):
        self.assertEqual(T.line_anchor(" 123 ： 名無しさん"), "12")


class TestParseAnchored(unittest.TestCase):
    PENDING = {2690: " 持ち物持ってるか、或いはなんか特性でもあるのか",
               2692: " カメといえば、こうら？",
               2694: " 亀だから物理でなんかあってもおかしくないか"}

    def test_锚点相符收下(self):
        res, unver, plain, ok = T.parse_result_anchored(
            "02690|持ち|是持有物还是有什么特性吗", self.PENDING)
        self.assertEqual(res, {2690: "是持有物还是有什么特性吗"})
        self.assertEqual((unver, plain, ok), ([], 0, 1))

    def test_串行_回显别行锚点被拒(self):
        # 模型把 2692 的内容写到 2690 的行号下（回显 2692 的锚点）
        res, unver, plain, ok = T.parse_result_anchored(
            "02690|カメ|说到龟的话，就是龟壳？", self.PENDING)
        self.assertEqual(res, {})
        self.assertEqual(unver, [2690])
        self.assertEqual((plain, ok), (0, 0))

    def test_无锚点回退旧口径(self):
        res, unver, plain, ok = T.parse_result_anchored(
            "02690|是持有物还是有什么特性吗", self.PENDING)
        self.assertEqual(res, {2690: "是持有物还是有什么特性吗"})
        self.assertEqual((unver, plain, ok), ([], 1, 0))

    def test_重复锚点不误判(self):
        pend = {10: " カメック！？", 12: " カメック？？"}
        res, unver, plain, ok = T.parse_result_anchored("00010|カメ|卡梅克！？", pend)
        self.assertEqual(res, {10: "卡梅克！？"})
        self.assertEqual(unver, [])

    def test_锚点被模型翻掉_剥字段不落进译文(self):
        # 实测：锚点 `【カ` 被模型译成 `【卡`（内容其实正确）→ 剥掉锚点字段收下，绝不把「【卡|」留在译文里
        pend = {2471: "【カメック】この「統率：C」が！！！"}
        res, unver, plain, ok = T.parse_result_anchored("02471|【卡|【卡梅克】这个「统率：C」！！！", pend)
        self.assertEqual(res, {2471: "【卡梅克】这个「统率：C」！！！"})
        self.assertEqual((unver, plain, ok), ([], 1, 0))

    def test_艺术行照抄不误剥(self):
        pend = {5: "　　｜　絵　｜　", 7: " こんにちは世界"}
        res, unver, plain, ok = T.parse_result_anchored("00005|  ｜　絵　｜　", pend)
        self.assertEqual(res, {5: "  ｜　絵　｜　"})
        self.assertEqual(unver, [])


class TestSystemAnchorPrompt(unittest.TestCase):
    def test_锚点版提示词含格式与示例(self):
        self.assertNotEqual(T.SYSTEM_ANCHOR, T.SYSTEM)
        self.assertIn("序号|锚点|翻译后的完整行", T.SYSTEM_ANCHOR)
        self.assertIn("0017|こん|", T.SYSTEM_ANCHOR)
        self.assertIn("0017|こん|　　　　｜　你好，你还好吗？", T.SYSTEM_ANCHOR)
        self.assertIn("序号|锚点|", T.SYSTEM_ANCHOR)


class _FakeAnchorAPI:
    """可编程假模型：请求体为「行号|锚点|原文」；mode 决定回显什么。"""

    def __init__(self, mode="good"):
        self.mode = mode
        self.calls = 0
        self.bodies = []

    def call_api(self, system, body, **kw):
        self.calls += 1
        self.bodies.append(body)
        rows = []
        for m in re.finditer(r'^(\d{1,5})\|(.*)$', body, re.M):
            i, rest = m.group(1), m.group(2)
            parts = rest.split("|", 1)
            anc, txt = (parts[0], parts[1]) if len(parts) == 2 else ("", rest)
            rows.append((i, anc, txt))
        out = []
        for k, (i, anc, _txt) in enumerate(rows):
            if self.mode == "plain":
                out.append(f"{i}|你好")                                  # 模型无视锚点格式
            elif k + 1 < len(rows) and (self.mode == "shift" or self.calls == 1):
                out.append(f"{i}|{rows[k + 1][1]}|错位{rows[k + 1][1]}" if anc
                           else f"{i}|错位{rows[k + 1][2]}")             # 串行：下一行的锚点+内容
            else:
                out.append(f"{i}|{anc}|你好" if anc else f"{i}|你好")
        return "\n".join(out)


class TestTranslateLinesAnchor(unittest.TestCase):
    FLAGGED = [(0, "こんにちは世界"), (5, "さようならですか"), (10, "おはようございます")]

    def setUp(self):
        self.fake = _FakeAnchorAPI()
        self.orig = T.call_api
        T.call_api = self.fake.call_api
        self._env = os.environ.pop("AA_ANCHOR", None)

    def tearDown(self):
        T.call_api = self.orig
        if self._env is not None:
            os.environ["AA_ANCHOR"] = self._env

    def test_串行响应被拒并按原文重发(self):
        self.fake.mode = "shift"
        fixed, missed, warns = T.translate_lines(self.FLAGGED)
        self.assertEqual(len(fixed), 3)
        self.assertEqual(missed, [])
        self.assertGreaterEqual(self.fake.calls, 2)                     # 主译 + 重发
        for i, _l in self.FLAGGED:
            self.assertNotIn("错位", fixed[i])                          # 错位内容没落地
            self.assertIn("你好", fixed[i])
        self.assertTrue(any("锚点不符" in w for w in warns), warns)

    def test_模型不给锚点_回退旧口径(self):
        self.fake.mode = "plain"
        fixed, missed, warns = T.translate_lines(self.FLAGGED)
        self.assertEqual(len(fixed), 3)
        self.assertEqual(self.fake.calls, 1)                            # 不多花调用
        self.assertTrue(any("锚点未回显" in w for w in warns), warns)

    def test_关开关_请求体无锚点(self):
        os.environ["AA_ANCHOR"] = "0"
        fixed, _m, _w = T.translate_lines(self.FLAGGED)
        self.assertEqual(len(fixed), 3)
        for b in self.fake.bodies:
            for ln in b.splitlines():
                if re.match(r'^\d{1,5}\|', ln):
                    self.assertEqual(len(ln.split("|")), 2, ln)         # 旧口径：两段式

    def test_开开关_请求体带锚点(self):
        fixed, _m, _w = T.translate_lines(self.FLAGGED)
        self.assertEqual(len(fixed), 3)
        first = [ln for ln in self.fake.bodies[0].splitlines() if re.match(r'^\d{1,5}\|', ln)]
        self.assertTrue(first and all(len(ln.split("|")) == 3 for ln in first), first)


if __name__ == "__main__":
    unittest.main()
