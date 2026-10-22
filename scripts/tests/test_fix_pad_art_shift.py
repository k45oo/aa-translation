# -*- coding: utf-8 -*-
"""fix_pad_art_shift：修「补位位置规范化」在艺术行上造成的错位（2026-10-19 entry_48695 事故）。

覆盖：
  ① 只认「tr 与 src 去掉全部空白后逐字符相同 + 显示宽度相同」的行（= 该行没有任何译文）；
  ② 人工编辑过且现值就是那次编辑结果的行**不碰**（edits.jsonl 最新 after == 现值）；
  ③ `--apply` 写盘：还原成 src + 备份目录 + ckpt 同步 + edits.jsonl 流水（type=错位）。
运行：.venv/Scripts/python.exe -m unittest scripts.tests.test_fix_pad_art_shift -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS_DIR)
for _d in (SCRIPTS, os.path.join(SCRIPTS, "common"), os.path.join(SCRIPTS, "works")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import aa_translate as T                      # noqa: E402
from fix import fix_pad_art_shift as F        # noqa: E402

SRC = ["# 标题",
       " 二二二二　　　　　/i:i:i:i",              # 0 行间距被搬到行尾的 art 行（src 原样）
       " こんにちは、やる夫です",                  # 已翻译 → 不该动
       " ミニニニニ　　　 ／二二",                 # art 行（本来就一致）
       " テスト"]
TR = ["# 标题",
      " 二二二二/i:i:i:i　　　　　",               # ← 错位（只差空白位置）→ 应还原成 src
      " 你好，我是やる夫",                         # 真译文 → 不碰
      " ミニニニニ　　　 ／二二",
      " テスト　　"]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="padshift_")
        self.aa_dir = os.path.join(self.tmp, "aa")
        self.out_dir = os.path.join(self.tmp, "汉化")
        self.work_dir = os.path.join(self.tmp, "work")
        for d in (self.aa_dir, self.out_dir, self.work_dir):
            os.makedirs(d)
        self.base = "entry_1"
        with open(os.path.join(self.aa_dir, self.base + ".txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(SRC))
        self.trp = os.path.join(self.out_dir, self.base + "_汉化.txt")
        with open(self.trp, "w", encoding="utf-8") as f:
            f.write("\n".join(TR))
        with open(os.path.join(self.work_dir, self.base + ".txt.ckpt.json"), "w", encoding="utf-8") as f:
            json.dump({"__lines": len(SRC), "1": TR[1]}, f, ensure_ascii=False)
        self.cfg = {"aa_dir": self.aa_dir, "out_dir": self.out_dir,
                    "work_dir": self.work_dir, "title": "测试作品"}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _edits(self, rows):
        with open(os.path.join(self.work_dir, "edits.jsonl"), "a", encoding="utf-8") as f:
            for line, after in rows:
                f.write(json.dumps({"ts": "2026-10-19 00:00:00", "work": "测试作品",
                                    "file": self.base + "_汉化.txt", "line": line,
                                    "type": "错译", "line_type": "",
                                    "before": "旧", "after": after}, ensure_ascii=False) + "\n")


class TestScan(Base):
    def test_detects_only_pad_shift(self):
        src, tr, hits = F.scan_chapter(self.cfg, self.base)
        self.assertEqual([h[0] for h in hits], [1])               # 只命中第 2 行
        self.assertEqual(hits[0][1], TR[1])                       # before = 现值
        self.assertEqual(hits[0][2], SRC[1])                      # after = 原文
        self.assertEqual(len(src), len(tr))

    def test_width_diff_not_touched(self):
        # 宽度不同 → 交给 scan_width/fix_width，不在本工具职责内
        with open(self.trp, "w", encoding="utf-8") as f:
            f.write("\n".join([TR[0], " 二二二二/i:i:i:i", TR[2], TR[3], TR[4]]))
        _s, _t, hits = F.scan_chapter(self.cfg, self.base)
        self.assertEqual([h[0] for h in hits], [])

    def test_human_locked_line_skipped(self):
        # 人工编辑过、且现值就是那次编辑结果 → 尊重（不还原间距）
        self._edits([(2, TR[1])])
        _s, _t, hits = F.scan_chapter(self.cfg, self.base)
        self.assertEqual(hits, [])

    def test_line_count_mismatch_skipped(self):
        with open(self.trp, "w", encoding="utf-8") as f:
            f.write("\n".join(TR[:-1]))
        _s, _t, hits = F.scan_chapter(self.cfg, self.base)
        self.assertEqual(hits, [])


class TestApply(Base):
    def test_apply_restores_and_logs(self):
        src, tr, hits = F.scan_chapter(self.cfg, self.base)
        n = F.apply_chapter("test_work", self.cfg, self.base, src, tr, hits)
        self.assertEqual(n, 1)
        out = T.txt_read(self.trp)
        self.assertEqual(out[1], SRC[1])                          # 已还原成原文
        self.assertEqual(out[2], TR[2])                           # 译文行没被动
        self.assertEqual(len(out), len(SRC))
        ck = json.load(open(os.path.join(self.work_dir, self.base + ".txt.ckpt.json"), encoding="utf-8"))
        self.assertEqual(ck["1"], SRC[1])                         # ckpt 同步（防重跑复活旧值）
        self.assertEqual(ck["__lines"], len(SRC))
        self.assertTrue([d for d in os.listdir(self.work_dir) if d.startswith(F.BAK_PREFIX)])
        logs = [json.loads(l) for l in open(os.path.join(self.work_dir, "edits.jsonl"), encoding="utf-8") if l.strip()]
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["type"], "错位")
        self.assertEqual(logs[0]["line"], 2)
        self.assertEqual(logs[0]["before"], TR[1])
        self.assertEqual(logs[0]["after"], SRC[1])

    def test_scan_after_apply_is_clean(self):
        src, tr, hits = F.scan_chapter(self.cfg, self.base)
        F.apply_chapter("test_work", self.cfg, self.base, src, tr, hits)
        _s2, _t2, hits2 = F.scan_chapter(self.cfg, self.base)
        self.assertEqual(hits2, [])                               # 幂等：再跑无命中


if __name__ == "__main__":
    unittest.main(verbosity=2)
