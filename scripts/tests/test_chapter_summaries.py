# -*- coding: utf-8 -*-
"""章节简介生成的作品术语表注入测试：gen_chapter_summaries._work_glossary_names。

覆盖：manual 全量 / auto 按本章语料过滤 / re: 正则规则跳过 / zh==jp 未确认跳过 /
无表返回空 / limit 截断。零依赖（临时目录 + mock registry），不调 API。

运行：.venv/Scripts/python.exe -m unittest discover -s scripts/tests -p "test_chapter_summaries.py"
"""
import json
import glob
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
for _d in ("scripts", "scripts/common", "scripts/tools", "scripts/works"):
    sys.path.insert(0, os.path.join(_REPO_ROOT, _d))

import gen_chapter_summaries as GCS  # noqa: E402

SEP = chr(12289)  # 、


class TestWorkGlossaryNames(unittest.TestCase):
    WORK = "testwork"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gcs_test_")
        self.path = os.path.join(self.tmp, "glossary_%s.json" % self.WORK)
        self._p = mock.patch.object(GCS.registry, "get",
                                    side_effect=lambda w: {"work_dir": self.tmp})
        self._p.start()

    def tearDown(self):
        self._p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, entries):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"schema": "test", "glossary": entries}, f, ensure_ascii=False)

    def test_no_table_returns_empty(self):
        self.assertEqual(GCS._work_glossary_names(self.WORK, "やる夫"), "")

    def test_manual_all_injected(self):
        self._write([
            {"jp": "やる夫", "zh": "亚鲁欧", "cat": "角色", "来源": "manual"},
            {"jp": "遠い町", "zh": "远方小镇", "cat": "专名", "来源": "manual"},
        ])
        out = GCS._work_glossary_names(self.WORK, "corpus无关")
        self.assertIn("やる夫=亚鲁欧", out)
        self.assertIn("遠い町=远方小镇", out)

    def test_auto_only_on_corpus_hit(self):
        self._write([
            {"jp": "ヒット", "zh": "命中", "cat": "角色", "来源": "auto"},
            {"jp": "ミス", "zh": "未出现", "cat": "角色", "来源": "auto"},
        ])
        out = GCS._work_glossary_names(self.WORK, "本章台词含ヒット一词")
        self.assertIn("ヒット=命中", out)
        self.assertNotIn("ミス", out)

    def test_skip_regex_and_untranslated(self):
        self._write([
            {"jp": "re:やる夫", "zh": "亚鲁欧", "cat": "正则", "来源": "manual"},
            {"jp": "ケロちゃん", "zh": "ケロちゃん", "cat": "角色", "来源": "manual"},
            {"jp": "ケロちゃん", "zh": "青蛙子", "cat": "角色", "来源": "manual"},
        ])
        out = GCS._work_glossary_names(self.WORK, "")
        self.assertNotIn("re:", out)
        # zh==jp 的未确认对跳过, 后面的确认对仍注入
        self.assertNotIn("ケロちゃん=ケロちゃん", out)
        self.assertIn("ケロちゃん=青蛙子", out)

    def test_limit(self):
        self._write([{"jp": "jp%d" % i, "zh": "zh%d" % i, "cat": "角色", "来源": "manual"}
                     for i in range(10)])
        out = GCS._work_glossary_names(self.WORK, "", limit=3)
        self.assertEqual(len(out.split(SEP)), 3)


class TestIncrementalSaveAndStop(unittest.TestCase):
    """2026-09-12：增量落盘（每章）+ 简介阶段的停止响应。"""

    WORK = "testwork"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gcs_run_")
        self.aa = os.path.join(self.tmp, "aa")
        self.wd = os.path.join(self.tmp, "work")
        os.makedirs(self.aa)
        os.makedirs(self.wd)
        self.kb = os.path.join(self.wd, self.WORK + "_knowledge.json")
        self._p_reg = mock.patch.object(
            GCS.registry, "get",
            side_effect=lambda w: {"aa_dir": self.aa, "work_dir": self.wd})
        self._p_dlg = mock.patch.object(
            GCS, "_chapter_dialogue", return_value=(["L1 [主] てすと"], "标题"))
        self._p_reg.start()
        self._p_dlg.start()
        self._reset_report_env()

    def tearDown(self):
        for k in ("AA_TASK_HB", "AA_STOP_CHAPTER_FILE", "AA_STOP_ASAP_FILE"):
            os.environ.pop(k, None)
        import task_report as TR
        TR._HB = None
        TR._STOP_FILES = None
        self._p_dlg.stop()
        self._p_reg.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _reset_report_env(self):
        for k in ("AA_TASK_HB", "AA_STOP_CHAPTER_FILE", "AA_STOP_ASAP_FILE"):
            os.environ.pop(k, None)
        import task_report as TR
        TR._HB = None
        TR._STOP_FILES = None

    def _saved_count(self):
        if not os.path.exists(self.kb):
            return 0
        with open(self.kb, encoding="utf-8") as f:
            return len(json.load(f).get("章节简介", {}))

    def test_incremental_save_per_chapter(self):
        import aa_backend
        seen = []

        def fake(r18, system, body, **kw):
            seen.append(self._saved_count())   # 调用模型前磁盘上已有多少条
            return ("亚鲁欧在测试章节里做了一件事。", None)

        with mock.patch.object(aa_backend, "call_with_fallback", side_effect=fake):
            GCS.gen_summaries(self.WORK, force=True, chapters="entry_1,entry_2,entry_3")
        # 旧实现是循环末尾统一落盘 → seen 恒 [0,0,0]
        self.assertEqual(seen, [0, 1, 2])
        self.assertEqual(self._saved_count(), 3)
        self.assertFalse(glob.glob(self.kb + ".tmp"))   # 原子写不留 tmp

    def test_stop_flag_breaks_loop_and_is_preserved(self):
        import aa_backend
        import task_report as TR
        stop = os.path.join(self.wd, ".stop_chapter")
        hb = os.path.join(self.wd, ".hb.json")
        os.environ["AA_TASK_HB"] = hb
        os.environ["AA_STOP_CHAPTER_FILE"] = stop
        TR._HB = None
        TR._STOP_FILES = None
        with open(stop, "w", encoding="utf-8"):
            pass
        calls = []

        def fake(*a, **k):
            calls.append(1)
            return ("不该被调用", None)

        with mock.patch.object(aa_backend, "call_with_fallback", side_effect=fake):
            GCS.gen_summaries(self.WORK, force=True, chapters="entry_1,entry_2")
        self.assertEqual(calls, [])                 # 见到标志即退出, 不调模型
        self.assertTrue(os.path.exists(stop))       # 标志留给外层章节边界消费
        self.assertTrue(os.path.exists(hb))         # 心跳已写(进度可见)


if __name__ == "__main__":
    unittest.main()
