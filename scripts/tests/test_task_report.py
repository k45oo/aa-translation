# -*- coding: utf-8 -*-
"""task_report (runner 心跳/停止协议) 单测：env 注入、心跳节流、停止标志消费、finalize。"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "common")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


class TRBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tr_test_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.hb = os.path.join(self.tmp, "hb.json")
        self.stop_chapter = os.path.join(self.tmp, "stop.chapter")
        self.stop_asap = os.path.join(self.tmp, "stop.asap")
        self._old = {k: os.environ.get(k) for k in ("AA_TASK_ID", "AA_TASK_HB",
                                                    "AA_STOP_CHAPTER_FILE", "AA_STOP_ASAP_FILE")}
        os.environ["AA_TASK_ID"] = "t_test"
        os.environ["AA_TASK_HB"] = self.hb
        os.environ["AA_STOP_CHAPTER_FILE"] = self.stop_chapter
        os.environ["AA_STOP_ASAP_FILE"] = self.stop_asap
        # 重置模块单例状态
        import task_report as TR
        TR._HB = None
        TR._last_write = 0.0
        TR._last_phase = None
        self.TR = TR

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.TR._HB = None


class TestTaskReport(TRBase):
    def test_enabled_and_heartbeat(self):
        self.assertTrue(self.TR.enabled())
        self.TR.heartbeat(phase="翻译", idx=3, total=14, force=True)
        with open(self.hb, encoding="utf-8") as _f:
            rec = json.load(_f)
        self.assertEqual(rec["phase"], "翻译")
        self.assertEqual(rec["chapter_idx"], 3)
        self.assertEqual(rec["chapter_total"], 14)
        self.assertEqual(rec["task"], "t_test")

    def test_heartbeat_throttle(self):
        # 同 phase 1s 内不重复写(节流): 立即连写两次, 文件只留上一次(force=False)
        self.TR.heartbeat(phase="下载", force=True)
        ts1 = os.path.getmtime(self.hb)
        self.TR.heartbeat(phase="下载")          # 节流跳过
        ts2 = os.path.getmtime(self.hb)
        self.assertEqual(ts1, ts2)               # 未重写

    def test_phase_change_forces_write(self):
        self.TR.heartbeat(phase="下载", force=True)
        self.TR.heartbeat(phase="翻译")          # phase 变→强制写
        with open(self.hb, encoding="utf-8") as _f:
            rec = json.load(_f)
        self.assertEqual(rec["phase"], "翻译")

    def test_check_stop_asap_priority(self):
        # asap 优先
        open(self.stop_asap, "w").write("now")
        open(self.stop_chapter, "w").write("now")
        self.assertEqual(self.TR.check_stop(), "asap")
        self.assertFalse(os.path.exists(self.stop_asap))
        self.assertTrue(os.path.exists(self.stop_chapter))   # chapter 未被消费

    def test_check_stop_only_asap(self):
        # only="asap": 只消费 asap, chapter 留给章节边界
        open(self.stop_chapter, "w").write("now")
        self.assertIsNone(self.TR.check_stop(only="asap"))
        self.assertTrue(os.path.exists(self.stop_chapter))
        self.assertEqual(self.TR.check_stop(), "chapter")

    def test_check_stop_consumes(self):
        open(self.stop_chapter, "w").write("now")
        self.assertEqual(self.TR.check_stop(), "chapter")
        self.assertIsNone(self.TR.check_stop())   # 已消费

    def test_finalize_writes_rc(self):
        open(self.hb, "w").write(json.dumps({"phase": "翻译"}))
        self.TR.finalize(41)
        with open(self.hb, encoding="utf-8") as _f:
            rec = json.load(_f)
        self.assertEqual(rec["rc"], 41)
        self.assertEqual(rec["phase"], "翻译")

    def test_disabled_when_no_hb(self):
        os.environ["AA_TASK_HB"] = ""
        self.TR._HB = None
        self.assertFalse(self.TR.enabled())
        self.TR.heartbeat(phase="x", force=True)   # 不抛
        self.assertIsNone(self.TR.check_stop())
        self.TR.finalize(0)                        # 不抛

    def test_task_stopped_error(self):
        import task_report as TR
        with self.assertRaises(TR.TaskStoppedError):
            raise TR.TaskStoppedError("base")


if __name__ == "__main__":
    unittest.main()
