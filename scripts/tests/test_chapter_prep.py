# -*- coding: utf-8 -*-
"""L5 测试：chapter_prep（章节术语 + R18 文本判定）与 iter_glossary（迭代候选去重）。"""
import os, sys, tempfile, unittest, json

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "common"))

import chapter_prep as CP
import iter_glossary as IG


class TestEid(unittest.TestCase):
    def test_entry_prefix(self):
        self.assertEqual(CP._eid_of_filename("entry_14463.txt"), "14463")
        self.assertEqual(CP._eid_of_filename("35828_虚空のエントランス.txt"), "35828")
        self.assertEqual(IG._eid_of_filename("entry_14463.txt"), "14463")

    def test_unknown(self):
        self.assertEqual(CP._eid_of_filename("no_eid.txt"), "no_eid.txt")


class TestR18(unittest.TestCase):
    def _prep(self, lines, eid="test"):
        d = os.path.join(tempfile.mkdtemp(), "prep")
        os.makedirs(d, exist_ok=True)
        src = os.path.join(d, f"{eid}_章.txt")
        open(src, "w", encoding="utf-8").write("# 标题\n" + "\n".join(lines))
        # 2026-08-28: 传 level='low'(最严 STRONG≥1/NORMAL≥2), 测词表命中而非分档行为
        return CP.prep_chapter(src, tempfile.mkdtemp(), level="low")

    def test_strong_hit_yes(self):
        r = self._prep(["今日も元気だ。", "それって中出しされたの？", "ねぇ"])
        self.assertEqual(r["r18"]["hit"], "yes")

    def test_one_normal_suspect(self):
        r = self._prep(["今日も元気だ。", "おっぱいが好きなんだね"])
        self.assertEqual(r["r18"]["hit"], "suspect")

    def test_two_normal_yes(self):
        # 2026-08-28: 词表校准后 パンツ/官能 降SOFT(不gate); NORMAL 词用 おっぱい/乳首 → 2个不同NORMAL=yes
        r = self._prep(["おっぱい", "乳首"])
        self.assertEqual(r["r18"]["hit"], "yes")

    def test_plain_no(self):
        r = self._prep(["今日も元気に頑張るぞ", "白頭は魔法を使った"])
        self.assertEqual(r["r18"]["hit"], "no")

    def test_art_line_not_counted(self):
        # 艺术行（含 発情 但非台词行）不参与判定
        r = self._prep(["/ ィノ | /ヽ ヾ　あのさ、", "発情猫でもあるのか"])
        # 台词行含 発情 → yes（艺术行被跳过，但第二行是台词）
        self.assertEqual(r["r18"]["hit"], "yes")


class TestIter(unittest.TestCase):
    def setUp(self):
        # 隔离真实 glossary 干扰：候选判定不依赖 known_names 内容
        self._orig_kn = IG._known_names
        IG._known_names = lambda: set()

    def tearDown(self):
        IG._known_names = self._orig_kn

    def test_dedupe_across_runs(self):
        d = tempfile.mkdtemp()
        work_dir = os.path.join(d, "work")
        src_dir = os.path.join(d, "aa")
        os.makedirs(work_dir)
        os.makedirs(src_dir)
        aa = os.path.join(src_dir, "entry_111.txt")
        open(aa, "w", encoding="utf-8").write("また会おう\nまた会おう")
        cfg = {"aa_dir": src_dir, "work_dir": work_dir, "id": "t"}
        IG.iter_work(cfg, ["111"])
        first = IG._load_iter(work_dir)
        n1 = len(first["候选"])
        IG.iter_work(cfg, ["111"])
        n2 = len(IG._load_iter(work_dir)["候选"])
        self.assertGreater(n1, 0, "隔离 known 后应有候选")
        self.assertEqual(n1, n2, "重跑不应新增重复候选")


if __name__ == "__main__":
    unittest.main()
