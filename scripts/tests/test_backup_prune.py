# -*- coding: utf-8 -*-
"""备份目录保留策略（2026-10-17 用户指定：同类 `backup_<kind>_<ts>/` 只保留最近 1 次运行）。

覆盖：
  ① `aa_translate.prune_backup_dirs` 本体：保留最新 N、只删目录、只删同前缀、返回释放字节；
  ② 默认值 BACKUP_KEEP_PER_KIND == 1；keep=0 边界；
  ③ 写入方接线（refine/fix/pokemon 各自建完备份后调用）——见 test_fix_lead_kanji 的连跑用例。
运行：.venv/Scripts/python.exe -m unittest scripts.tests.test_backup_prune -v
"""
import os
import shutil
import sys
import tempfile
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in (SCRIPTS, os.path.join(SCRIPTS, "common")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import aa_translate as T
import aa_common as C


class TestPruneBackupDirs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prune_test_")
        self.wd = os.path.join(self.tmp, "work")
        os.makedirs(self.wd)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mk(self, name, payload=b"x" * 10, as_dir=True):
        p = os.path.join(self.wd, name)
        if as_dir:
            os.makedirs(p, exist_ok=True)
            with open(os.path.join(p, "entry_1_汉化.txt"), "wb") as f:
                f.write(payload)
        else:
            with open(p, "wb") as f:
                f.write(payload)
        return p

    def _names(self, prefix="backup_refine_aa_"):
        return sorted(d for d in os.listdir(self.wd) if d.startswith(prefix))

    def test_keeps_newest_one(self):
        for ts in ("20260101010101", "20260102010101", "20260103010101",
                   "20260104010101", "20260105010101"):
            self._mk("backup_refine_aa_" + ts)
        n, freed = T.prune_backup_dirs(self.wd, "backup_refine_aa_")
        self.assertEqual(n, 4)
        self.assertEqual(self._names(), ["backup_refine_aa_20260105010101"])
        self.assertGreater(freed, 0)

    def test_default_keep_is_one(self):
        self.assertEqual(T.BACKUP_KEEP_PER_KIND, 1)
        self.assertEqual(C.BACKUP_KEEP_PER_KIND, 1)          # aa_common 是同一份实现

    def test_keep_one_and_zero(self):
        for ts in ("20260101010101", "20260102010101", "20260103010101"):
            self._mk("backup_refine_aa_" + ts)
        self.assertEqual(T.prune_backup_dirs(self.wd, "backup_refine_aa_", keep=1)[0], 2)
        self.assertEqual(self._names(), ["backup_refine_aa_20260103010101"])
        self.assertEqual(T.prune_backup_dirs(self.wd, "backup_refine_aa_", keep=0)[0], 1)
        self.assertEqual(self._names(), [])

    def test_only_matching_prefix_is_touched(self):
        self._mk("backup_refine_aa_20260101010101")
        self._mk("backup_refine_aa_20260102010101")
        self._mk("backup_refine_aa_20260103010101")
        self._mk("backup_tpl_20260101010101")
        self._mk("backup_tpl_20260102010101")
        self._mk("backup_tpl_20260103010101")
        self._mk("refine_ckpt", as_dir=True)
        self._mk("edits.jsonl", as_dir=False)
        T.prune_backup_dirs(self.wd, "backup_tpl_", keep=1)
        self.assertEqual(self._names("backup_tpl_"), ["backup_tpl_20260103010101"])
        self.assertEqual(len(self._names()), 3)                    # refine 那类没被连坐（3 个都还在）
        self.assertTrue(os.path.isdir(os.path.join(self.wd, "refine_ckpt")))
        self.assertTrue(os.path.exists(os.path.join(self.wd, "edits.jsonl")))

    def test_nothing_to_prune_when_at_or_below_keep(self):
        self._mk("backup_tpl_20260102010101")
        self.assertEqual(T.prune_backup_dirs(self.wd, "backup_tpl_"), (0, 0))
        self.assertEqual(T.prune_backup_dirs(self.wd, "backup_nonexistent_"), (0, 0))

    def test_files_with_prefix_are_not_deleted(self):
        # 只删目录：同前缀的**文件**不动（如手工留的 backup_refine_aa_notes.txt）
        self._mk("backup_refine_aa_notes.txt", as_dir=False)
        for ts in ("20260101010101", "20260102010101", "20260103010101"):
            self._mk("backup_refine_aa_" + ts)
        T.prune_backup_dirs(self.wd, "backup_refine_aa_")
        self.assertTrue(os.path.exists(os.path.join(self.wd, "backup_refine_aa_notes.txt")))

    def test_quiet_suppresses_print(self):
        import io
        import contextlib
        for ts in ("20260101010101", "20260102010101", "20260103010101"):
            self._mk("backup_refine_aa_" + ts)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            T.prune_backup_dirs(self.wd, "backup_refine_aa_", quiet=True)
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
