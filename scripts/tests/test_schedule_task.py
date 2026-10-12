# -*- coding: utf-8 -*-
"""定时任务（2026-09-29，方案 doc/方案归档/定时任务与tokenplan折扣方案_2026-09-29.md）。

覆盖四层：
  ① task_manager.parse_schedule_at / schedule_due —— 时间串解析、到点判定
  ② task_manager 的 `scheduled` 语义 —— enqueue 不占同作品串行、未到点不启动且**不阻塞**后面任务、
     到点转 queued 再启动、可取消、过期/坏串当普通任务立即排队
  ③ main.api_translate_start 的定时入口 —— 写 schedule_at + 钉 AA_CHAIN/AA_REFINE_BACKEND=mimo；
     R18 作品 / 免费链 / 过期时刻一律拒绝
  ④ main._task_public 的 scheduled_at + estimate._cost(at_ts=) 的折扣窗口金额
零依赖（unittest + 临时目录），不起服务器、不调 API。
运行：.venv/Scripts/python.exe -m unittest scripts.tests.test_schedule_task -v
"""
import json
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
for _p in (_REPO_ROOT, os.path.join(_REPO_ROOT, "scripts"), os.path.join(_REPO_ROOT, "scripts", "common"),
           os.path.join(_REPO_ROOT, "scripts", "works"), os.path.join(_REPO_ROOT, "aa_web_app")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ["AA_TASK_MANAGER_NO_SPAWN"] = "1"

from aa_web_app import main as M              # noqa: E402
from aa_web_app import task_manager as TM     # noqa: E402
from aa_web_app import estimate as EST        # noqa: E402

MIMO_MODEL = "mimo-v2.6-flash"


def _ts(s):
    """'2026-09-30 00:00' → unix 秒（本地时区，与 time.mktime 同口径）。"""
    return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M"))


def _fmt(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


class TMBase(unittest.TestCase):
    WORKS = ("w_alpha", "w_beta", "w_gamma")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tm_sched_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._old_tasks_dir = TM.TASKS_DIR
        self._old_root = TM.ROOT
        TM.TASKS_DIR = os.path.join(self.tmp, "tasks")
        TM.ROOT = self.tmp
        os.makedirs(TM.TASKS_DIR, exist_ok=True)
        self.fake_reg = types.ModuleType("registry")
        work_dirs = {}
        for w in self.WORKS:
            wd = os.path.join(self.tmp, w)
            os.makedirs(wd, exist_ok=True)
            work_dirs[w] = wd
        self.fake_reg.get = lambda w: ({"work_dir": work_dirs[w], "title": w}
                                       if w in work_dirs else (_ for _ in ()).throw(KeyError(w)))
        self._old_reg = sys.modules.get("registry")
        sys.modules["registry"] = self.fake_reg
        self._old_cmd = TM._build_command
        TM._build_command = lambda tt, w, p: [sys.executable, "-c", "pass"]

    def tearDown(self):
        TM._build_command = self._old_cmd
        for tid, p in list(TM._watch_procs.items()):
            try:
                p.wait(timeout=3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        TM._watch_procs.clear()
        TM.TASKS_DIR = self._old_tasks_dir
        TM.ROOT = self._old_root
        if self._old_reg is not None:
            sys.modules["registry"] = self._old_reg
        else:
            sys.modules.pop("registry", None)

    def _task(self, tid, **kw):
        t = {"task_id": tid, "type": "translate", "work": self.WORKS[0],
             "params": {}, "status": "queued", "pid": None, "rc": None,
             "created": "2026-09-29 12:00:00", "created_ts": time.time(),
             "started": None, "started_ts": None, "finished": None,
             "stop_mode": None, "killed": False}
        t.update(kw)
        TM._save_task(t)
        return t

    @staticmethod
    def _wait_status(tid, statuses, timeout=5.0):
        """等 _watch 收尾（假 runner 立即退出）。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            t = TM.get_task(tid)
            if t and t["status"] in statuses and (t["status"] == "running" or t.get("rc") is not None):
                return t
            time.sleep(0.05)
        return TM.get_task(tid)


class TestParseScheduleAt(unittest.TestCase):
    def test_iso_variants(self):
        self.assertEqual(TM.parse_schedule_at("2026-09-30 00:00"), _ts("2026-09-30 00:00"))
        self.assertEqual(TM.parse_schedule_at("2026-09-30T00:05"), _ts("2026-09-30 00:05"))   # datetime-local
        self.assertEqual(TM.parse_schedule_at("2026-09-30 00:00:30"), _ts("2026-09-30 00:00") + 30)

    def test_unix_forms(self):
        self.assertEqual(TM.parse_schedule_at(1790889600), 1790889600.0)
        self.assertEqual(TM.parse_schedule_at("1790889600"), 1790889600.0)

    def test_bad_input_is_none(self):
        for v in (None, "", "  ", "明天", "2026/09/30 00:00", True, False, []):
            self.assertIsNone(TM.parse_schedule_at(v), repr(v))

    def test_schedule_due(self):
        self.assertTrue(TM.schedule_due({"params": {}}))                       # 无 schedule_at = 立即
        self.assertTrue(TM.schedule_due({"params": {"schedule_at": time.time() - 5}}))
        self.assertFalse(TM.schedule_due({"params": {"schedule_at": time.time() + 600}}))
        self.assertTrue(TM.schedule_due({"params": {"schedule_at": "坏"}}))     # 坏值不阻塞


class TestEnqueueScheduled(TMBase):
    def test_future_goes_to_scheduled(self):
        r = TM.enqueue("translate", "w_alpha", {"chapters": "1", "schedule_at": "2099-01-01 00:00"})
        self.assertTrue(r["ok"], r)
        self.assertTrue(r.get("scheduled"))
        self.assertIn("定时", r["msg"])
        t = TM._load_task(r["task_id"])
        self.assertEqual(t["status"], "scheduled")
        self.assertAlmostEqual(t["params"]["schedule_at"], _ts("2099-01-01 00:00"), places=0)
        self.assertIn(r["task_id"], TM._load_queue())          # 仍在队列里等着

    def test_scheduled_does_not_hold_same_work(self):
        """定时任务不占同作品串行——否则一个 00:00 的任务会把该作品锁一整天。"""
        r1 = TM.enqueue("translate", "w_alpha", {"schedule_at": "2099-01-01 00:00"})
        self.assertTrue(r1["ok"], r1)
        r2 = TM.enqueue("translate", "w_alpha", {"chapters": "2"})
        self.assertTrue(r2["ok"], r2)                          # 允许
        self.assertEqual(TM._load_task(r2["task_id"])["status"], "queued")

    def test_running_task_still_blocks_scheduled(self):
        self._task("t_run", status="running", pid=os.getpid())   # running（活 pid）
        r = TM.enqueue("translate", "w_alpha", {"schedule_at": "2099-01-01 00:00"})
        self.assertFalse(r["ok"])                             # 运行中仍串行
        self.assertIn("串行", r["error"])

    def test_past_or_bad_schedule_runs_now(self):
        for v, w in (("2000-01-01 00:00", "w_beta"), ("不是时间", "w_gamma")):
            r = TM.enqueue("translate", w, {"schedule_at": v})
            self.assertTrue(r["ok"], r)
            self.assertFalse(r.get("scheduled"))
            self.assertEqual(TM._load_task(r["task_id"])["status"], "queued")

    def test_scheduled_not_reconciled_as_crashed(self):
        """scheduled 无 pid → reconcile 不能把它判 crashed。"""
        r = TM.enqueue("translate", "w_alpha", {"schedule_at": "2099-01-01 00:00"})
        t = TM.get_task(r["task_id"])
        self.assertEqual(t["status"], "scheduled")


class TestSchedulerScheduled(TMBase):
    def test_not_due_does_not_start(self):
        r = TM.enqueue("translate", "w_alpha", {"schedule_at": "2099-01-01 00:00"})
        self.assertEqual(TM.scheduler_tick(), [])
        self.assertEqual(TM._load_task(r["task_id"])["status"], "scheduled")
        self.assertIn(r["task_id"], TM._load_queue())

    def test_not_due_does_not_block_following_task(self):
        """未到点的 scheduled 留在队列但不作候选——后面的任务照常启动（其余任务 FIFO 不变）。"""
        r_s = TM.enqueue("translate", "w_alpha", {"schedule_at": "2099-01-01 00:00"})
        r_n = TM.enqueue("translate", "w_beta", {})
        started = TM.scheduler_tick()
        self.assertEqual(started, [r_n["task_id"]])
        self.assertEqual(TM._load_task(r_s["task_id"])["status"], "scheduled")
        self._wait_status(r_n["task_id"], ("done",), timeout=6)

    def test_due_promotes_and_starts(self):
        r = TM.enqueue("translate", "w_alpha", {"schedule_at": "2099-01-01 00:00"})
        t = TM._load_task(r["task_id"])
        t["params"]["schedule_at"] = time.time() - 5          # 到点
        TM._save_task(t)
        started = TM.scheduler_tick()
        self.assertEqual(started, [r["task_id"]])
        self.assertIn(TM._load_task(r["task_id"])["status"], ("running", "done"))
        self.assertNotIn(r["task_id"], TM._load_queue())
        self._wait_status(r["task_id"], ("done",), timeout=6)
        self.assertEqual(TM.get_task(r["task_id"])["rc"], 0)

    def test_due_but_work_busy_waits_as_queued(self):
        """到点但同作品在跑 → 转 queued 等下一轮（不是丢掉、也不是并发跑）。"""
        old = TM._build_command
        TM._build_command = lambda tt, w, p: [sys.executable, "-c", "import time;time.sleep(30)"]
        procs = []
        try:
            r_s = TM.enqueue("translate", "w_alpha", {"schedule_at": "2099-01-01 00:00"})
            r_n = TM.enqueue("translate", "w_alpha", {})        # 允许（scheduled 不占同作品串行）
            self.assertEqual(TM.scheduler_tick(), [r_n["task_id"]])   # 未到点 → 先起普通任务
            self.assertEqual(TM._load_task(r_s["task_id"])["status"], "scheduled")
            t = TM._load_task(r_s["task_id"])
            t["params"]["schedule_at"] = time.time() - 5         # 到点，但同作品正忙
            TM._save_task(t)
            self.assertEqual(TM.scheduler_tick(), [])
            self.assertEqual(TM._load_task(r_s["task_id"])["status"], "queued")   # 转排队等下一轮
            self.assertIn(r_s["task_id"], TM._load_queue())
            procs = list(TM._watch_procs.values())
        finally:
            TM._build_command = old
            for p in procs:
                try:
                    p.kill()
                except Exception:
                    pass
            time.sleep(0.2)

    def test_dead_queue_entry_pruned(self):
        """队列里任务文件丢了 → 剔除，不卡住后面的任务（老行为回归）。"""
        r = TM.enqueue("translate", "w_alpha", {})
        r2 = TM.enqueue("translate", "w_beta", {})
        os.remove(TM.task_path(r["task_id"]))
        started = TM.scheduler_tick()
        self.assertEqual(started, [r2["task_id"]])
        self._wait_status(r2["task_id"], ("done",), timeout=6)


class TestCancelScheduled(TMBase):
    def test_cancel_scheduled(self):
        r = TM.enqueue("translate", "w_alpha", {"schedule_at": "2099-01-01 00:00"})
        tid = r["task_id"]
        rr = TM.cancel_queued(tid)
        self.assertTrue(rr["ok"], rr)
        self.assertNotIn(tid, TM._load_queue())
        self.assertEqual(TM._load_task(tid)["status"], "cancelled")
        self.assertEqual(TM.scheduler_tick(), [])             # 取消后不会再启动

    def test_fmt_task_shows_schedule(self):
        r = TM.enqueue("translate", "w_alpha", {"schedule_at": "2099-01-01 00:00"})
        line = TM._fmt_task(TM._load_task(r["task_id"]))
        self.assertIn("scheduled", line)
        self.assertIn("定时 01-01 00:00 启动", line)      # 显示月-日 时:分（同任务页口径）


class MainScheduleTest(unittest.TestCase):
    """api_translate_start 的定时入口（零 API，patch 掉 cfg_of / enqueue）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sched_route_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # 翻译硬前置：作品术语表（路由会先检查它）
        with open(os.path.join(self.tmp, "glossary_w1.json"), "w", encoding="utf-8") as f:
            json.dump({"glossary": [{"jp": "ねこ", "zh": "猫"}]}, f)
        self.r18 = False
        self.calls = []
        self._patches = [
            mock.patch.object(M, "cfg_of",
                              side_effect=lambda w: {"work_dir": self.tmp, "title": "测试", "r18": self.r18}),
            mock.patch.object(M.TM, "enqueue", side_effect=self._enq),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _enq(self, ttype, work, params):
        self.calls.append((ttype, work, params))
        tid = "t-sched"
        pr = dict(params)
        if pr.get("schedule_at"):
            return {"ok": True, "task_id": tid, "scheduled": True, "msg": "定时任务已排队（09-30 00:00 启动）: " + tid}
        return {"ok": True, "task_id": tid, "msg": "任务已入队: " + tid}

    def test_scheduled_pins_mimo(self):
        r = M.api_translate_start("w1", {"eids": ["100"], "schedule_at": "2099-01-01T00:00"})
        self.assertTrue(r["ok"], r)
        self.assertTrue(r.get("scheduled"))
        _tt, _w, params = self.calls[0]
        self.assertAlmostEqual(params["schedule_at"], _ts("2099-01-01 00:00"), places=0)
        self.assertEqual(params["env"], {"AA_CHAIN": "mimo", "AA_REFINE_BACKEND": "mimo"})
        self.assertEqual(params["chapters"], "100")
        self.assertNotIn("AA_FREE_MODE", params["env"])

    def test_scheduled_rejected_for_r18(self):
        self.r18 = True
        r = M.api_translate_start("w1", {"eids": ["100"], "schedule_at": "2099-01-01T00:00"})
        self.assertFalse(r["ok"])
        self.assertIn("R18", r["error"])
        self.assertEqual(self.calls, [])

    def test_scheduled_rejected_with_free_mode(self):
        r = M.api_translate_start("w1", {"eids": ["100"], "free_mode": True,
                                         "schedule_at": "2099-01-01T00:00"})
        self.assertFalse(r["ok"])
        self.assertIn("免费链", r["error"])
        self.assertEqual(self.calls, [])

    def test_scheduled_rejected_when_past_or_bad(self):
        for v in ("2000-01-01T00:00", "明天"):
            r = M.api_translate_start("w1", {"eids": ["100"], "schedule_at": v})
            self.assertFalse(r["ok"], v)
            self.assertEqual(self.calls, [], v)

    def test_no_schedule_keeps_old_behavior(self):
        r = M.api_translate_start("w1", {"eids": ["100"]})
        self.assertTrue(r["ok"], r)
        _tt, _w, params = self.calls[0]
        self.assertNotIn("schedule_at", params)
        self.assertNotIn("env", params)                       # 默认（整条链）不写 env

    def test_task_public_exposes_scheduled_at(self):
        ts = _ts("2099-01-01 00:00")
        d = M._task_public({"task_id": "t1", "status": "scheduled", "type": "translate", "work": "w1",
                            "params": {"chapters": "1", "schedule_at": ts}})
        self.assertEqual(d["scheduled_at"], time.strftime("%m-%d %H:%M", time.localtime(ts)))
        self.assertEqual(d["schedule_ts"], ts)
        d2 = M._task_public({"task_id": "t2", "status": "queued", "type": "translate", "work": "w1",
                             "params": {}})
        self.assertNotIn("scheduled_at", d2)


class TestDiscountEstimate(unittest.TestCase):
    """折扣窗口金额（estimate._cost(at_ts=)）——小米 token plan 每日 00:00-08:00 ×0.8。"""

    def test_off_window_ts_discounts(self):
        ts_in = _ts("2026-09-30 03:00")
        base, peak, cached, unpriced, at_cost, off_at = EST._cost(
            1_000_000, 0, "mimo", MIMO_MODEL, at_ts=ts_in)
        self.assertFalse(unpriced)
        self.assertAlmostEqual(base, 1.0, places=4)           # 未命中 1.0 元/M
        self.assertAlmostEqual(at_cost, 0.8, places=4)
        self.assertTrue(off_at)

    def test_out_of_window_ts_keeps_base(self):
        ts_out = _ts("2026-09-30 12:00")
        base, _p, _c, _u, at_cost, off_at = EST._cost(
            1_000_000, 0, "mimo", MIMO_MODEL, at_ts=ts_out)
        self.assertAlmostEqual(at_cost, base, places=6)
        self.assertFalse(off_at)

    def test_no_at_ts_means_no_at_cost(self):
        base, _p, _c, _u, at_cost, off_at = EST._cost(1_000_000, 0, "mimo", MIMO_MODEL)
        self.assertAlmostEqual(at_cost, base, places=6)
        self.assertFalse(off_at)


if __name__ == "__main__":
    unittest.main()
