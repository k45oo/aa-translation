# -*- coding: utf-8 -*-
"""改动留档：逐行记「改前/改后」到单文件（2026-10-01，取代整文件备份目录）。

覆盖四层：
  ① main._log_edits_bulk / _log_edit —— 一行一条落 <work_dir>/edits.jsonl（时间/章节/行/类型/改前/改后）
  ② main._plan_writes —— 计划带 changes=[(idx0,before,after)]（翻译替换留档的数据源，直写/重算两支都带）
  ③ 记录可逐行还原 + 被 count_unsynced_edits 计入「待重新生成」条数
  ④ logic.save_line / apply_glossary_chapter 不再产生 work/backup/<ts>/
零依赖（unittest + 临时目录），不起服务器、不调 API。
运行：.venv/Scripts/python.exe -m unittest scripts.tests.test_line_history -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts", "common"))
sys.path.insert(0, os.path.join(_REPO_ROOT, "aa_web_app"))

from aa_web_app import logic  # noqa: E402
from aa_web_app import main as M  # noqa: E402


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aa_hist_test_")
        self.work_dir = os.path.join(self.tmp, "work")
        self.out_dir = os.path.join(self.tmp, "汉化")
        self.aa_dir = os.path.join(self.tmp, "aa")
        for d in (self.work_dir, self.out_dir, self.aa_dir):
            os.makedirs(d)
        self.cfg = {"title": "测试作品", "work_dir": self.work_dir,
                    "out_dir": self.out_dir, "aa_dir": self.aa_dir}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, d, name, lines):
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return p

    def log(self):
        p = os.path.join(self.work_dir, "edits.jsonl")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]


class TestEditLog(Fixture):
    def test_bulk_one_record_per_line(self):
        n = M._log_edits_bulk(self.cfg, "entry_1_汉化.txt",
                              [(0, "白頭の魔女", "白头魔女"), (7, "あ", "啊")],
                              "翻译替换")
        self.assertEqual(n, 2)
        rows = self.log()
        self.assertEqual([r["line"] for r in rows], [1, 8])      # 1-based 行号
        self.assertEqual([r["before"] for r in rows], ["白頭の魔女", "あ"])
        self.assertEqual([r["after"] for r in rows], ["白头魔女", "啊"])
        self.assertEqual({r["type"] for r in rows}, {"翻译替换"})
        self.assertEqual(rows[0]["file"], "entry_1_汉化.txt")
        self.assertEqual(rows[0]["work"], "测试作品")
        self.assertTrue(rows[0]["ts"])

    def test_single_edit_same_format(self):
        M._log_edit(self.cfg, "entry_2_汉化.txt", 3, "错译", "旧译", "新译", "text")
        rows = self.log()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["line"], 4)
        self.assertEqual(rows[0]["line_type"], "text")
        self.assertEqual((rows[0]["before"], rows[0]["after"]), ("旧译", "新译"))

    def test_empty_rows_writes_nothing(self):
        self.assertEqual(M._log_edits_bulk(self.cfg, "x", [], "翻译替换"), 0)
        self.assertEqual(self.log(), [])

    def test_appends_not_overwrites(self):
        M._log_edit(self.cfg, "a_汉化.txt", 0, "漏译", "1", "2")
        M._log_edits_bulk(self.cfg, "b_汉化.txt", [(1, "3", "4")], "翻译替换")
        rows = self.log()
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["file"] for r in rows], ["a_汉化.txt", "b_汉化.txt"])

    def test_records_count_as_unsynced_and_allow_restore(self):
        """记录 = 唯一留档：计入「待重新生成」条数，且足以把整章逐行还原。"""
        tr = self.write(self.out_dir, "entry_1_汉化.txt", ["白頭の魔女", "中文"])
        changes = [(0, "白頭の魔女", "白头魔女")]
        M._log_edits_bulk(self.cfg, "entry_1_汉化.txt", changes, "翻译替换")
        lines = logic.read_lines(tr)
        for idx, _before, after in changes:
            lines[idx] = after
        logic.write_lines(tr, lines)                     # 真落盘
        self.assertEqual(logic.count_unsynced_edits(self.work_dir, None), 1)
        lines = logic.read_lines(tr)                     # 用 before 还原
        for idx, before, _after in changes:
            lines[idx] = before
        logic.write_lines(tr, lines)
        self.assertEqual(logic.read_lines(tr), ["白頭の魔女", "中文"])


class TestReplacePlanCarriesChanges(Fixture):
    """翻译替换留档的数据源：_plan_writes 两支都要带 changes。"""

    def test_direct_write_branch_keeps_changes(self):
        tr = self.write(self.out_dir, "entry_1_汉化.txt", ["白頭の魔女", "中文"])
        plan = [{"file": "entry_1_汉化.txt", "tr_path": tr,
                 "changes": [(0, "白頭の魔女", "白头魔女")], "sfx": {}}]
        pairs = M._norm_manual_pairs([["白頭", "白头"]])
        out = M._plan_writes(self.cfg, plan, pairs)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["changes"], [(0, "白頭の魔女", "白头魔女")])
        self.assertEqual(out[0]["lines"][0], "白头魔女")    # 与落盘内容同一份
        self.assertEqual(out[0]["idxs"], [0])

    def test_recompute_branch_changes_match_written_lines(self):
        # 预览后该章被改过（before 不匹配）→ 走重算支，changes 必须与将写入的行一致
        self.write(self.aa_dir, "entry_1.txt", ["白頭の魔女", "あいう"])
        tr = self.write(self.out_dir, "entry_1_汉化.txt", ["白頭の魔女", "中文"])
        plan = [{"file": "entry_1_汉化.txt", "tr_path": tr,
                 "changes": [(0, "白頭の魔女", "白头魔女")], "sfx": {}}]
        with open(tr, "w", encoding="utf-8") as f:          # 预览后又被改
            f.write("白頭の魔女（改过）\n中文")
        pairs = M._norm_manual_pairs([["白頭", "白头"]])
        out = M._plan_writes(self.cfg, plan, pairs)
        self.assertEqual(len(out), 1)
        for idx, before, after in out[0]["changes"]:
            self.assertEqual(logic.read_lines(tr)[idx], before)   # before == 盘上现值
            self.assertEqual(out[0]["lines"][idx], after)         # after == 将写入值


class TestNoWholeFileBackup(Fixture):
    """2026-10-01：行保存/术语替换不再整文件备份到 work/backup/<ts>/。"""

    def test_save_line_creates_no_backup_dir(self):
        src = self.write(self.aa_dir, "entry_1.txt", ["# 标题", "あいう"])
        tr = self.write(self.out_dir, "entry_1_汉化.txt", ["# 标题", "中文"])
        r = logic.save_line(src, tr, 1, "文文")
        self.assertEqual(r["result"], "ok")
        self.assertFalse(os.path.exists(os.path.join(self.work_dir, "backup")))

    def test_apply_glossary_creates_no_backup_dir(self):
        src = self.write(self.aa_dir, "entry_1.txt", ["白頭の魔女", "あいう"])
        tr = self.write(self.out_dir, "entry_1_汉化.txt", ["白頭の魔女", "中文"])
        changed, _ = logic.apply_glossary_chapter(src, tr, [["白頭", "白头"]],
                                                  dry_run=False)
        self.assertEqual(len(changed), 1)
        self.assertFalse(os.path.exists(os.path.join(self.work_dir, "backup")))


if __name__ == "__main__":
    unittest.main()
