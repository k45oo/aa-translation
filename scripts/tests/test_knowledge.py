# -*- coding: utf-8 -*-
"""作品知识库测试：work_memory 读写 / 迁移 / 关联度 / memory_hint 分流 / iter_glossary 分流。

运行：python -m unittest scripts.tests.test_knowledge -v
零依赖（unittest + 临时目录 + mock registry），不调 API。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts", "tools"))
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts", "common"))

import work_memory as WM  # noqa: E402


class FakeCfg:
    """registry.get 的假配置：work_dir 指向临时目录。"""

    def __init__(self, tmp):
        self.tmp = tmp
        self._cfg = {"work_dir": tmp, "aa_dir": os.path.join(tmp, "aa"), "id": None}

    def get(self, key, default=None):
        return self._cfg.get(key, default)

    def __getitem__(self, key):
        return self._cfg[key]


class KnowledgeBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kw_test_")
        os.makedirs(os.path.join(self.tmp, "aa"), exist_ok=True)
        self.cfg = FakeCfg(self.tmp)
        self.work = "test_work"
        self.reg_patch = mock.patch.object(WM.registry, "get", return_value=self.cfg)
        self.reg_patch.start()
        # work_meta 指向临时文件(空)
        self.meta_patch = mock.patch.object(WM, "_load_work_meta", return_value={})
        self.meta_patch.start()

    def tearDown(self):
        self.meta_patch.stop()
        self.reg_patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _path(self):
        return os.path.join(self.tmp, self.work + "_knowledge.json")


class TestKnowledgeIO(KnowledgeBase):
    def test_save_load_roundtrip(self):
        mem = {"术语": {"やる夫": "亚鲁欧"}, "人物": {"キャル": "凯露"}}
        WM.save_knowledge(self.work, mem)
        self.assertTrue(os.path.exists(self._path()))
        got = WM.load_knowledge(self.work)
        self.assertEqual(got["术语"]["やる夫"], "亚鲁欧")
        self.assertEqual(got["人物"]["キャル"], "凯露")

    def test_load_missing_returns_empty(self):
        self.assertEqual(WM.load_knowledge(self.work), {})

    def test_compat_names(self):
        # 旧接口 load_memory/save_memory 仍可用
        WM.save_memory(self.work, {"术语": {"a": "b"}})
        self.assertEqual(WM.load_memory(self.work)["术语"]["a"], "b")


class TestAssociation(KnowledgeBase):
    def test_default_serial(self):
        self.assertEqual(WM.work_association(self.work), "serial")

    def test_anthology_from_meta(self):
        self.meta_patch.stop()
        self.meta_patch = mock.patch.object(WM, "_load_work_meta",
                                            return_value={self.work: {"关联度": "anthology"}})
        self.meta_patch.start()
        self.assertEqual(WM.work_association(self.work), "anthology")

    def test_invalid_falls_back_serial(self):
        self.meta_patch.stop()
        self.meta_patch = mock.patch.object(WM, "_load_work_meta",
                                            return_value={self.work: {"关联度": "weird"}})
        self.meta_patch.start()
        self.assertEqual(WM.work_association(self.work), "serial")


class TestMemoryHint(KnowledgeBase):
    def test_empty_memory_no_hint(self):
        self.assertEqual(WM.memory_hint(self.work), "")

    def test_terms_and_people(self):
        WM.save_knowledge(self.work, {
            "术语": {"やる夫": "亚鲁欧"},
            "人物": {"キャル": "凯露(公主连结)"},
        })
        h = WM.memory_hint(self.work)
        self.assertIn("亚鲁欧", h)
        self.assertIn("キャル=凯露", h)

    def test_summary_injected_with_base(self):
        WM.save_knowledge(self.work, {"章节简介": {"entry_1": "本章主角团出发。"}})
        h = WM.memory_hint(self.work, base="entry_1")
        self.assertIn("本章主角团出发", h)
        # 无 base 不注入章节简介
        h2 = WM.memory_hint(self.work)
        self.assertNotIn("本章主角团出发", h2)

    def test_anthology_skips_people(self):
        # anthology 作品: 人物不注入(防跨章污染), 术语/章节简介仍注入
        self.meta_patch.stop()
        self.meta_patch = mock.patch.object(WM, "_load_work_meta",
                                            return_value={self.work: {"关联度": "anthology"}})
        self.meta_patch.start()
        WM.save_knowledge(self.work, {
            "术语": {"やる夫": "亚鲁欧"},
            "人物": {"キャル": "凯露"},
            "章节简介": {"entry_1": "独立短篇: 主角去冒险。"},
        })
        h = WM.memory_hint(self.work, base="entry_1")
        self.assertIn("亚鲁欧", h)
        self.assertIn("独立短篇", h)
        self.assertNotIn("キャル", h)  # 人物不注入

    def test_people_capped(self):
        WM.save_knowledge(self.work, {"人物": {f"p{i}": f"名{i}" for i in range(30)}})
        h = WM.memory_hint(self.work, max_people=20)
        self.assertLessEqual(h.count("名"), 21)  # 20人 + 前缀


class TestMigrate(KnowledgeBase):
    def test_migrate_backs_up_and_removes_old(self):
        old_p = os.path.join(self.tmp, self.work + "_memory.json")
        json.dump({"术语": {"旧词": "旧译"}}, open(old_p, "w", encoding="utf-8"))
        ok = WM.migrate_memory_to_knowledge(self.work)
        self.assertTrue(ok)
        # 旧文件已删
        self.assertFalse(os.path.exists(old_p))
        # 备份存在
        baks = [f for f in os.listdir(os.path.join(self.tmp, "backup")) if "memory" in f]
        self.assertTrue(baks)
        # 新文件含旧数据
        got = WM.load_knowledge(self.work)
        self.assertEqual(got["术语"]["旧词"], "旧译")

    def test_no_old_returns_false(self):
        self.assertFalse(WM.migrate_memory_to_knowledge(self.work))


class TestAddFromIssues(KnowledgeBase):
    def test_confirmed_term_added(self):
        issues = [{"confirmed": True, "type": "术语", "old": "やる夫", "new": "亚鲁欧"}]
        n = WM.add_from_issues(self.work, issues)
        self.assertEqual(n, 1)
        self.assertEqual(WM.load_knowledge(self.work)["术语"]["やる夫"], "亚鲁欧")

    def test_kana_new_skipped(self):
        # 建议改回日文(含假名)绝不积累
        issues = [{"confirmed": True, "type": "术语", "old": "亚鲁欧", "new": "やる夫"}]
        n = WM.add_from_issues(self.work, issues)
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
