# -*- coding: utf-8 -*-
"""对齐审计（scripts/fix/audit_alignment.py）：判据 + 写盘护栏。

背景：主翻译块「行号|原文」清单里模型偶发丢行并整体串行（把邻行译文写到这一行），
宽度校验被 pad 抹平 → 静默落地。本工具用独立重译 + 互比定位，本用例只测**不花 API** 的部分：
  ① verdict 判据边界（未翻译 / 串行错位 / 误报拒绝 / 新译不可信即拒）；
  ② guard_why 四重护栏（宽度短 / 残留假名 / 照抄 / art 列位）；
  ③ apply_chapter 写盘：只改判定行、备份、ckpt 同步、edits.jsonl 追加（可逐行还原）。
运行：.venv/Scripts/python.exe -m unittest scripts.tests.test_audit_alignment -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in (SCRIPTS, os.path.join(SCRIPTS, "common"), os.path.join(SCRIPTS, "works")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import aa_translate as T                     # noqa: E402
sys.path.insert(0, os.path.join(SCRIPTS, "fix"))
import audit_alignment as A                  # noqa: E402


class TestVerdict(unittest.TestCase):
    def test_未翻译_现译文等于原文(self):
        src = " 持ち物持ってるか、或いはなんか特性でもあるのか"
        self.assertEqual(A.verdict(src, src, "是持有物还是有什么特性吗",
                                   0.2, 0.1, None, 1.0), "未翻译")

    def test_串行错位_更像别行新译(self):
        src = " 亀だから物理でなんかあってもおかしくないか"
        cur = " 可怕的是明日香还没上阵呢"          # 其实是别行的译文
        new = " 因为是龟，物理方面有什么也不奇怪"
        self.assertEqual(A.verdict(cur, src, new, 0.07, 0.78, 2729, 0.19), "串行错位")

    def test_相邻近义行不算串行_源文太像被挡(self):
        # 2186 实测误报：两句都是喊叫声、源文相似 0.74 → 不判
        src = "【ダブルオー】チェスト――――っ！！！！！"
        cur = "【00高达】吃我一记――――！！！！！"
        new = "【00高达】冲啊啊啊啊啊啊！！！！！"
        self.assertIsNone(A.verdict(cur, src, new, 0.27, 0.75, 2360, 0.74))

    def test_正常行不判(self):
        src = " カメといえば、こうら？"
        cur = " 说到乌龟，就是龟壳？"
        new = " 说到龟的话，就是龟壳？"
        self.assertIsNone(A.verdict(cur, src, new, 0.55, 0.55, None, 1.0))

    def test_新译含假名不可信(self):
        src = " ヴァンピとも好相性だが"
        self.assertIsNone(A.verdict(src, src, " 和Vampi好相性だが", 0.0, 0.9, 1, 0.1))

    def test_新译过短不可信(self):
        src = " おっけ、これなら敏捷ワンチャン狙いに来ずそのまま落ちるかな？"
        self.assertIsNone(A.verdict(src, src, "好的", 0.0, 0.9, 1, 0.1))

    def test_阈值边界(self):
        src, new = " 何という徹底した魔法パーティ", " 何等彻底的魔法队伍"
        cur = " 骸没死所以没有夏娜"
        # best-own = 0.50 ≥ 0.45 且 best ≥ 0.65；源文相似 0.09 < 0.5 → 判串行
        self.assertEqual(A.verdict(cur, src, new, 0.50, 1.00, 2368, 0.09), "串行错位")
        # best 0.64 < D_BS → 不判
        self.assertIsNone(A.verdict(cur, src, new, 0.0, 0.64, 2368, 0.09))
        # 差值 0.39 < D_OWN → 不判
        self.assertIsNone(A.verdict(cur, src, new, 0.21, 0.60, 2368, 0.09))
        # 两行源文本体太像（近义行）→ 不判
        self.assertIsNone(A.verdict(cur, src, new, 0.0, 1.00, 2368, 0.55))


class TestGuard(unittest.TestCase):
    def test_宽度短被拦(self):
        src = " こんにちは、お元気ですか？　　　"
        self.assertIn("宽度短", A.guard_why(src, " 你好"))

    def test_照抄被拦(self):
        src = " こんにちは、お元気ですか？"
        self.assertIn("照抄", A.guard_why(src, src))

    def test_残留假名被拦(self):
        src = " 昨日のテストはどうでしたか"
        self.assertIn("残留假名", A.guard_why(src, " 昨天的テスト怎么样"))

    def test_宽度够且无假名的中文通过(self):
        src = " カメといえば、こうら？\u3000\u3000\u3000\u3000"
        self.assertEqual(A.guard_why(src, " 说到乌龟就是龟壳？\u3000\u3000\u3000").__len__() > 0, True)
        self.assertEqual(A.guard_why(src, A.T.pad_to_width(" 说到龟的话，就是龟壳？", T.disp_width(src))), [])


class TestApplyChapter(unittest.TestCase):
    """写盘隔离：aa/汉化/work 全在临时目录（不碰真作品）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="audit_align_test_")
        self.cfg = {"aa_dir": os.path.join(self.tmp, "aa"),
                    "out_dir": os.path.join(self.tmp, "汉化"),
                    "work_dir": os.path.join(self.tmp, "work"),
                    "html_out": ""}
        for d in (self.cfg["aa_dir"], self.cfg["out_dir"], self.cfg["work_dir"]):
            os.makedirs(d)
        self.base = "entry_1"
        self.src = [" # 标题", " カメといえば、こうら？", " サンクス、ならタイム切ってないか",
                    "123 ： 名無しさん ： 2026/01/01 00:00:00 ID:abc"]
        self.zh = [" # 标题", " 说到乌龟，就是龟壳？", " 谢了，那时间没到吗",
                   "123 ： 名無しさん ： 2026/01/01 00:00:00 ID:abc"]
        open(os.path.join(self.cfg["aa_dir"], self.base + ".txt"), "w", encoding="utf-8").write("\n".join(self.src))
        open(os.path.join(self.cfg["out_dir"], self.base + "_汉化.txt"), "w", encoding="utf-8").write("\n".join(self.zh))
        json.dump({"__lines": len(self.src), "1": self.zh[1], "2": self.zh[2]},
                  open(os.path.join(self.cfg["work_dir"], self.base + ".txt.ckpt.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_落地只改判定行并同步ckpt与记录(self):
        cand = A.T.pad_to_width(" 说到龟的话，就是龟壳？", T.disp_width(self.src[1]))
        rec = {"base": self.base, "line": 1, "kind": "串行错位", "cur": self.zh[1],
               "new": cand, "own": 0.1, "best": 0.9, "best_line": 2, "why": []}
        n = A.apply_chapter("测试作品", self.base, self.cfg, [rec])
        self.assertEqual(n, 1)
        zh = T.txt_read(os.path.join(self.cfg["out_dir"], self.base + "_汉化.txt"))
        self.assertEqual(zh[1], cand)                     # 判定行已改
        self.assertEqual(zh[2], self.zh[2])               # 其它行不动
        self.assertEqual(len(zh), len(self.src))          # 行数守恒
        with open(os.path.join(self.cfg["work_dir"], self.base + ".txt.ckpt.json"), encoding="utf-8") as f:
            ck = json.load(f)
        self.assertEqual(ck["1"], cand)                   # ckpt 同步（防重跑复活旧值）
        self.assertEqual(ck["__lines"], len(self.src))
        with open(os.path.join(self.cfg["work_dir"], "edits.jsonl"), encoding="utf-8") as f:
            edits = [json.loads(x) for x in f]
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]["line"], 2)             # edits.jsonl 1-based（与工作台同口径）
        self.assertEqual(edits[0]["type"], "错位")
        self.assertEqual(edits[0]["before"], self.zh[1])
        baks = [d for d in os.listdir(self.cfg["work_dir"]) if d.startswith("backup_align_")]
        self.assertEqual(len(baks), 1)
        self.assertTrue(os.path.exists(os.path.join(self.cfg["work_dir"], baks[0], self.base + "_汉化.txt")))

    def test_校验不过不写盘(self):
        rec = {"base": self.base, "line": 1, "kind": "串行错位", "cur": self.zh[1],
               "new": " 短", "own": 0.0, "best": 0.9, "best_line": 2, "why": ["宽度短"]}
        n = A.apply_chapter("测试作品", self.base, self.cfg, [rec])
        self.assertEqual(n, 0)
        zh = T.txt_read(os.path.join(self.cfg["out_dir"], self.base + "_汉化.txt"))
        self.assertEqual(zh[1], self.zh[1])
        self.assertFalse(os.path.exists(os.path.join(self.cfg["work_dir"], "edits.jsonl")))


if __name__ == "__main__":
    unittest.main()
