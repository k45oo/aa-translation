# -*- coding: utf-8 -*-
"""工作台「更新 → 下载并翻译」链式（2026-09-13）。

背景：translate_runner 不再自动补下载，所以 action=translate 必须走
download 任务 + params['then']=translate（下载正常完成后由 task_manager 链式入队翻译）。
函数级测试，不启动服务器、不调外部 API。
运行：.venv/Scripts/python.exe -m unittest scripts.tests.test_update_chain -v
"""
import os
import sys
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts", "common"))
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts", "works"))
sys.path.insert(0, os.path.join(_REPO_ROOT, "aa_web_app"))

from aa_web_app import main as M  # noqa: E402


class UpdateChainTest(unittest.TestCase):
    WORKS = {"w1": {"title": "测试作品"}}
    UPD = {"ok": True, "updates": [{"eid": "100"}, {"eid": "101"}]}

    def setUp(self):
        self.calls = []

        def _enq(ttype, work, params):
            self.calls.append((ttype, work, params))
            return {"ok": True, "task_id": "t-next"}

        self._patches = [
            mock.patch.object(M.add_work, "check_updates", return_value=dict(self.UPD)),
            mock.patch.object(M.registry, "WORKS", dict(self.WORKS)),
            mock.patch.object(M.TM, "enqueue", side_effect=_enq),
            mock.patch.object(M.TM, "wiki_configured", return_value=True),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_translate_enqueues_download_with_then(self):
        r = M.api_work_update("w1", {"action": "translate"})
        self.assertTrue(r["ok"], r)
        ttype, work, params = self.calls[0]
        self.assertEqual(ttype, "download")                 # 先下载
        self.assertEqual(work, "w1")
        self.assertEqual(params["chapters"], "100,101")
        self.assertEqual(params["then"]["type"], "translate")   # 完成后接翻译
        self.assertEqual(params["then"]["chapters"], "100,101")
        self.assertIn("自动接翻译", r["msg"])
        self.assertEqual(r["task_id"], "t-next")

    def test_download_action_has_no_chain(self):
        r = M.api_work_update("w1", {"action": "download"})
        self.assertTrue(r["ok"], r)
        ttype, _w, params = self.calls[0]
        self.assertEqual(ttype, "download")
        self.assertNotIn("then", params)
        self.assertNotIn("自动接翻译", r["msg"])

    def test_free_mode_env_passed_to_followup(self):
        M.api_work_update("w1", {"action": "translate", "free_mode": True})
        params = self.calls[0][2]
        self.assertEqual(params["then"]["env"], {"AA_FREE_MODE": "1"})

    def test_no_updates_no_task(self):
        with mock.patch.object(M.add_work, "check_updates", return_value={"ok": True, "updates": []}):
            r = M.api_work_update("w1", {"action": "translate"})
        self.assertTrue(r["ok"])
        self.assertIsNone(r["task_id"])
        self.assertEqual(self.calls, [])

    def test_bad_action_rejected(self):
        r = M.api_work_update("w1", {"action": "nope"})
        self.assertFalse(r["ok"])
        self.assertEqual(self.calls, [])

    # ---- 2026-10-06: 「同步本作 wiki」（下载 → wiki 同步 → 翻译 三链） ----

    def test_sync_wiki_chains_download_wiki_translate(self):
        r = M.api_work_update("w1", {"action": "translate", "sync_wiki": True})
        self.assertTrue(r["ok"], r)
        params = self.calls[0][2]
        self.assertEqual(params["then"]["type"], "wiki")              # 下载完成后先同步 wiki
        self.assertEqual(params["then"]["then"]["type"], "translate")  # wiki 任务自身再带翻译
        self.assertEqual(params["then"]["then"]["chapters"], "100,101")
        self.assertIn("wiki 同步", r["msg"])

    def test_sync_wiki_ignored_when_work_unconfigured(self):
        with mock.patch.object(M.TM, "wiki_configured", return_value=False):
            r = M.api_work_update("w1", {"action": "translate", "sync_wiki": True})
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.calls[0][2]["then"]["type"], "translate")   # 退化为旧两段链
        self.assertNotIn("wiki 同步", r["msg"])

    def test_no_updates_but_sync_wiki_still_enqueues(self):
        with mock.patch.object(M.add_work, "check_updates", return_value={"ok": True, "updates": []}):
            r = M.api_work_update("w1", {"action": "translate", "sync_wiki": True})
        self.assertTrue(r["ok"], r)
        self.assertEqual([c[0] for c in self.calls], ["wiki"])     # 章节没新的也同步一次 wiki
        self.assertIn("wiki 同步", r["msg"])

    def test_sync_wiki_endpoint(self):
        r = M.api_work_sync_wiki("w1")
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.calls[0][0], "wiki")
        self.assertEqual(self.calls[0][2].get("mode"), "update")

    def test_sync_wiki_endpoint_rejects_unconfigured(self):
        with mock.patch.object(M.TM, "wiki_configured", return_value=False):
            r = M.api_work_sync_wiki("w1")
        self.assertFalse(r["ok"])
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
