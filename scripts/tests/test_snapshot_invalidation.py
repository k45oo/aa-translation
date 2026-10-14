# -*- coding: utf-8 -*-
r"""首页快照失效覆盖（2026-10-14，B 轴「静默」专项）。

设计约定（`aa_web_app/index_cache.py` 头注释）：**显式改动走 `invalidate()` 局部重算**。
首页快照把 `work_meta` 派生的字段烘进去了（`_build_index_data` 读 `load_work_meta` →
`tags`/`materials`+出现次数+占比、原文话数/未译差值），一旦漏标脏，首页要等
`DEFAULT_MAX_AGE=600s` 过期才更新 —— 用户改完标签/素材/状态**看不到变化、以为没保存**。

本文件锁三条路由：
  - `api_work_meta_put`（tags/materials/status/author）
  - `api_work_meta_mat_scan`（知识库分支 + 角色表分支都写 materials）
  - `api_work_update`（下载新章 → 话数/素材频率；只在真的入队时才标脏）
另锁一条同源静默：mat_scan 知识库分支原来**忽略 `save_work_meta` 的返回值**，
保存失败照样回 `ok=True`（改后必须抛 500）。
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_TESTS_DIR))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
for _p in (os.path.join(_REPO_ROOT, "scripts"),
           os.path.join(_REPO_ROOT, "scripts", "common"),
           os.path.join(_REPO_ROOT, "scripts", "works")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import registry                              # noqa: E402
from fastapi import HTTPException            # noqa: E402
from aa_web_app import web_state as WS       # noqa: E402
from aa_web_app.routers import works as W    # noqa: E402
from aa_web_app.routers import site_url as SU  # noqa: E402

WORKS_SRC = os.path.join(_REPO_ROOT, "aa_web_app", "routers", "works.py")


class MetaPutInvalidation(unittest.TestCase):
    """PUT /api/work-meta/<work> → 必须标脏首页快照。"""

    def test_meta_put_marks_dirty(self):
        spy = mock.MagicMock()
        with mock.patch.object(WS, "cfg_of", return_value={"work_dir": "/tmp/x"}), \
                mock.patch.object(W.logic, "load_work_meta", return_value={}), \
                mock.patch.object(W.logic, "save_work_meta", return_value=True), \
                mock.patch.object(W, "_INDEX_SNAP", spy):
            r = W.api_work_meta_put("作品X", W.WorkMeta(tags=["tag1"], status="连载中"))
        self.assertTrue(r["ok"])
        spy.invalidate.assert_called_once_with("作品X")

    def test_meta_put_save_failure_raises_not_ok(self):
        with mock.patch.object(WS, "cfg_of", return_value={}), \
                mock.patch.object(W.logic, "load_work_meta", return_value={}), \
                mock.patch.object(W.logic, "save_work_meta", return_value=False), \
                mock.patch.object(W, "_INDEX_SNAP", mock.MagicMock()):
            with self.assertRaises(HTTPException) as cm:
                W.api_work_meta_put("作品X", W.WorkMeta())
        self.assertEqual(cm.exception.status_code, 500)


class MatScanInvalidation(unittest.TestCase):
    """POST /api/work-meta/<work>/mat-scan → 写 materials，必须标脏。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="matsnap_")
        with open(os.path.join(self.tmp, "a_汉化.txt"), "w", encoding="utf-8") as f:
            f.write("甲\n乙\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_knowledge_branch_marks_dirty(self):
        spy = mock.MagicMock()
        with mock.patch.object(WS, "cfg_of", return_value={"out_dir": self.tmp}), \
                mock.patch.object(W.logic, "load_work_meta", return_value={}), \
                mock.patch.object(W.logic, "save_work_meta", return_value=True), \
                mock.patch.object(W, "_mat_scan_from_knowledge",
                                  return_value=[("甲", 5), ("乙", 4)]), \
                mock.patch.object(W, "_INDEX_SNAP", spy):
            r = W.api_work_meta_mat_scan("作品X")
        self.assertTrue(r["ok"])
        self.assertEqual(r["source"], "knowledge")
        spy.invalidate.assert_called_once_with("作品X")

    def test_knowledge_branch_save_failure_raises(self):
        """原实现忽略 save 返回值 → 保存失败也回 ok=True（静默丢素材）。"""
        with mock.patch.object(WS, "cfg_of", return_value={"out_dir": self.tmp}), \
                mock.patch.object(W.logic, "load_work_meta", return_value={}), \
                mock.patch.object(W.logic, "save_work_meta", return_value=False), \
                mock.patch.object(W, "_mat_scan_from_knowledge",
                                  return_value=[("甲", 5)]), \
                mock.patch.object(W, "_INDEX_SNAP", mock.MagicMock()):
            with self.assertRaises(HTTPException) as cm:
                W.api_work_meta_mat_scan("作品X")
        self.assertEqual(cm.exception.status_code, 500)

    def test_两个写分支都有标脏_静态兜底防漏改(self):
        """知识库分支 + 角色表分支各一处，加上 meta_put 一处 = 至少 3 处。"""
        src = open(WORKS_SRC, encoding="utf-8").read()
        self.assertGreaterEqual(src.count("_INDEX_SNAP.invalidate(work)"), 3,
                                "api_work_meta_put / mat-scan 两分支都必须标脏首页快照")


class WorkUpdateInvalidation(unittest.TestCase):
    """POST /api/work/<work>/update → 有新课入队才标脏。"""

    def _run(self, updates, spy):
        with mock.patch.dict(registry.WORKS, {"作品X": {"id": "作品X"}}, clear=False), \
                mock.patch.object(SU.add_work, "check_updates",
                                  return_value={"ok": True, "updates": updates}), \
                mock.patch.object(SU.TM, "enqueue",
                                  return_value={"ok": True, "task_id": "t1"}), \
                mock.patch.object(SU, "_INDEX_SNAP", spy):
            return SU.api_work_update("作品X", {"action": "download"})

    def test_有新章时标脏(self):
        spy = mock.MagicMock()
        r = self._run([{"eid": "9001"}, {"eid": "9002"}], spy)
        self.assertTrue(r["ok"])
        spy.invalidate.assert_called_once_with("作品X")

    def test_已最新时不标脏(self):
        spy = mock.MagicMock()
        r = self._run([], spy)
        self.assertTrue(r["ok"])
        self.assertFalse(spy.invalidate.called, "没有新章就不该标脏（避免无谓全量重算）")


if __name__ == "__main__":
    unittest.main()
