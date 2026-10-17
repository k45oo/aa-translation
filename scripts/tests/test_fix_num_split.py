# -*- coding: utf-8 -*-
"""fix_num_split：回修「数字记号被切坏」的已交付行（2026-10-17，零 API）。

三类坏值（段级切分把数字切在中间 → 模型见残句）：
  ① 插词 `（1.变成了1倍）`（源 1.1倍）、`1/都说到3`（源 1/3）、`3-是3`（源 3-3）
  ② 补位 `（全1　　　.33倍）`（art 的 `.` 要守列位）
  ③ 机械修不了（数字被换成词/整句仍日文）→ 只入 todo 清单
本用例只测纯函数 `repair_line`（写盘通道复用 audit_alignment.apply_chapter，另有用例覆盖）。

运行：.venv/Scripts/python.exe -m unittest scripts.tests.test_fix_num_split -v
"""
import os
import sys
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (SCRIPTS, os.path.join(SCRIPTS, "fix"), os.path.join(SCRIPTS, "common"),
           os.path.join(SCRIPTS, "works")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import fix_num_split as F          # noqa: E402
import aa_translate as T           # noqa: E402


class TestRepairLine(unittest.TestCase):
    def rep(self, src, zh):
        new, why = F.repair_line(src, zh)
        return (new if new else None), why     # 不 strip：补宽结果要原样看

    def test_插词_小数(self):
        new, _ = self.rep("　全能力を強化（1.1倍）した！　", "　强化了全能力（1.变成了1倍）！　")
        self.assertIn("（1.1倍）", new)

    def test_插词_比例(self):
        new, _ = self.rep("　殆ど1/3まで言ってただろ　", "　几乎1/都说到3了吧　")
        self.assertIn("1/3", new)

    def test_插词_比分(self):
        new, _ = self.rep("　「2-2」！そして「3-3」だってこと　", "　「2-2」！そして「3-是3这件事　")
        self.assertIn("3-3", new)

    def test_插词_使其达到型(self):
        new, _ = self.rep("　命中率を低下（0.85倍）させる　", "　命中率下降（0.使其达到85倍）。　")
        self.assertIn("（0.85倍）", new)

    def test_补位(self):
        new, _ = self.rep("　※『エース』です（全1.33倍）　", "　※『王牌』（全1　　　.33倍）　")
        self.assertIn("（全1.33倍）", new)

    def test_修后补宽到源宽(self):
        src = "　強化（1.1倍）した！　　　　　　　　　　"
        new, _ = self.rep(src, "　强化（1.变成了1倍）！　　　　　　　　　　")
        self.assertEqual(T.disp_width(new), T.disp_width(src))

    def test_数字已完整则不动(self):
        src, zh = "　強化（1.1倍）した！　", "　强化（1.1倍）了！　"
        self.assertIsNone(self.rep(src, zh)[0])

    def test_数字被换成词则不硬修(self):
        # （1.翻倍）里 '2' 丢了 → 机械修不该拼出「1.2倍」以外的猜测，交重译
        new, why = self.rep("　命中率を強化（1.2倍）する。　", "　强化命中率（1.翻倍）。　")
        self.assertIsNone(new)
        self.assertTrue(why)

    def test_原样保留的行不误修(self):
        s = "　わかってますって、1つめのジム戦は「2-2」！　"
        self.assertIsNone(self.rep(s, s)[0])

    def test_不引入假名(self):
        src = "　強化（1.5倍）する。　"
        zh = "　强化（1.变成5倍）する。　"
        new, why = self.rep(src, zh)
        if new:
            self.assertEqual(len(T.kana_runs(new)), len(T.kana_runs(zh)))

    def test_art行与无数字行跳过(self):
        self.assertEqual(self.rep("　／⌒＼　", "　／⌒＼　")[1], "源行无数字记号")
        self.assertEqual(self.rep("　こんにちは　", "　你好　")[1], "源行无数字记号")


if __name__ == "__main__":
    unittest.main()
