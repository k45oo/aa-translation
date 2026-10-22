# -*- coding: utf-8 -*-
"""task_manager 单测（2026-08-29）：注册表/队列/停止标志/调度/reconcile/usage 聚合。
全部用 tmp 目录 + 假 registry，不碰真实作品目录。spawn 用 AA_TASK_MANAGER_NO_SPAWN 关闭。"""
import json
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "common")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ["AA_TASK_MANAGER_NO_SPAWN"] = "1"

from aa_web_app import task_manager as TM


class TMBase(unittest.TestCase):
    WORKS = ("w_alpha", "w_beta", "w_gamma")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tm_test_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._old_tasks_dir = TM.TASKS_DIR
        self._old_root = TM.ROOT
        TM.TASKS_DIR = os.path.join(self.tmp, "tasks")
        TM.ROOT = self.tmp
        os.makedirs(TM.TASKS_DIR, exist_ok=True)
        # 假 registry：get(work) -> {work_dir: tmp/<work>}
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

    def tearDown(self):
        # 等待本测试启动的 runner 收尾（_watch 线程写终态）——直接 kill+clear 会留
        # 半收尾线程，跨测试污染 reconcile（pid 死 + 无 rc → 误判 crashed）。
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
             "params": {}, "status": "running", "pid": None, "rc": None,
             "created": "2026-08-29 12:00:00", "created_ts": time.time(),
             "started": None, "started_ts": None, "finished": None,
             "stop_mode": None, "killed": False}
        t.update(kw)
        TM._save_task(t)
        return t


class TestEnqueue(TMBase):
    def test_enqueue_ok(self):
        r = TM.enqueue("translate", "w_alpha", {"chapters": "1,2"})
        self.assertTrue(r["ok"], r)
        t = TM._load_task(r["task_id"])
        self.assertEqual(t["status"], "queued")
        self.assertEqual(t["work"], "w_alpha")
        self.assertIn(r["task_id"], TM._load_queue())

    def test_enqueue_bad_type_and_work(self):
        self.assertFalse(TM.enqueue("hack", "w_alpha")["ok"])
        self.assertFalse(TM.enqueue("translate", "no_such_work")["ok"])

    def test_same_work_serialized(self):
        r1 = TM.enqueue("translate", "w_alpha", {})
        self.assertTrue(r1["ok"])
        r2 = TM.enqueue("download", "w_alpha", {})
        self.assertFalse(r2["ok"])
        self.assertIn("串行", r2["error"])
        # 不同作品可入队
        self.assertTrue(TM.enqueue("translate", "w_beta", {})["ok"])

    def test_legacy_lock_blocks(self):
        # 旧 .runner.lock 写当前进程 pid（存活）→ 拒绝入队
        lf = TM._legacy_lock("w_alpha")
        with open(lf, "w") as f:
            f.write(str(os.getpid()))
        r = TM.enqueue("translate", "w_alpha", {})
        self.assertFalse(r["ok"])
        self.assertIn("旧式任务", r["error"])


class TestPipelineType(TMBase):
    """2026-10-19: pipeline=pokemon 的作品，translate 自动归一到专属 runner。

    事故：作品「ポケの子」点「更新 → 下载并翻译」，链式 then 写的是 type=translate，
    进 translate_runner 才被「归属专属链」硬拦 → 下载成功、翻译任务 failed。
    归一放 enqueue（唯一入口），链式/CLI/任务页手工建任务都不会再撞。
    """

    def test_translate_normalized_to_pokemon(self):
        import work_pipeline as WP
        with mock.patch.object(WP, "pipeline_of", return_value=WP.POKEMON):
            self.assertEqual(TM.pipeline_type("translate", "w_alpha"), "pokemon")
            r = TM.enqueue("translate", "w_alpha", {"chapters": "1"})
            self.assertTrue(r["ok"], r)
            self.assertEqual(TM._load_task(r["task_id"])["type"], "pokemon")

    def test_other_types_and_generic_untouched(self):
        import work_pipeline as WP
        with mock.patch.object(WP, "pipeline_of", return_value=WP.POKEMON):
            self.assertEqual(TM.pipeline_type("download", "w_alpha"), "download")
            self.assertEqual(TM.pipeline_type("proofread", "w_alpha"), "proofread")
        with mock.patch.object(WP, "pipeline_of", return_value=WP.GENERIC):
            self.assertEqual(TM.pipeline_type("translate", "w_alpha"), "translate")

    def test_chain_followup_uses_pokemon_runner(self):
        """下载并翻译的链式 then（type=translate）在 pokemon 作品上落 pokemon 任务。"""
        import work_pipeline as WP
        with mock.patch.object(WP, "pipeline_of", return_value=WP.POKEMON):
            t = self._task("t9", type="download", status="running",
                           params={"chapters": "10", "then": {"type": "translate",
                                                              "chapters": "10"}})
            TM._finalize(t, 0)
            fu = TM._load_task("t9")["followup"]
            self.assertTrue(fu.get("ok"), fu)
            self.assertEqual(fu["type"], "pokemon")
            self.assertEqual(TM._load_task(fu["task_id"])["type"], "pokemon")
            self.assertEqual(TM._load_task(fu["task_id"])["params"]["chapters"], "10")


class TestWikiTask(TMBase):
    """2026-10-06: 作品官方设定 wiki 任务（gen_work_wiki.py --update / --all）。

    每部作品独立配置在 `scripts/config/work_wikis.json`；没配的作品不该入队（否则白跑一趟）。
    配置读取用真实仓库文件（TM._SCRIPTS 在 TMBase 里未改），故「ポケの子 / この素晴らしい…」为真实条目。
    """

    def test_wiki_in_types(self):
        self.assertIn("wiki", TM.TYPES)

    def test_build_command_default_is_update(self):
        cmd = TM._build_command("wiki", "ポケの子", {})
        self.assertTrue(any(str(x).endswith(os.path.join("tools", "gen_work_wiki.py")) for x in cmd), cmd)
        self.assertEqual(cmd[cmd.index("--work") + 1], "ポケの子")
        self.assertEqual(cmd[-1], "--update")          # 默认连载同步（重抓+增量）

    def test_build_command_mode_all(self):
        self.assertEqual(TM._build_command("wiki", "ポケの子", {"mode": "all"})[-1], "--all")

    def test_wiki_configured_reads_real_config(self):
        self.assertTrue(TM.wiki_configured("ポケの子"))
        self.assertTrue(TM.wiki_configured("この素晴らしいシンオウに祝福を"))
        self.assertFalse(TM.wiki_configured("不存在的作品"))
        self.assertFalse(TM.wiki_configured("_字段"))    # 配置里的说明键不算作品

    def test_enqueue_rejects_unconfigured_work(self):
        r = TM.enqueue("wiki", "w_alpha", {})
        self.assertFalse(r["ok"])
        self.assertIn("未配置", r["error"])


class TestStopCancel(TMBase):
    def test_request_stop_writes_flag(self):
        t = self._task("t1", pid=os.getpid())     # 活 pid 即可
        r = TM.request_stop("t1", "chapter")
        self.assertTrue(r["ok"], r)
        self.assertTrue(os.path.exists(TM.stop_path("t1", "chapter")))
        self.assertEqual(TM._load_task("t1")["status"], "stopping")
        self.assertEqual(TM._load_task("t1")["stop_mode"], "chapter")
        # asap 覆盖
        self.assertTrue(TM.request_stop("t1", "asap")["ok"])
        self.assertTrue(os.path.exists(TM.stop_path("t1", "asap")))
        # 坏 mode / 终态任务
        self.assertFalse(TM.request_stop("t1", "now")["ok"])
        self._task("t2", status="done", pid=None)
        self.assertFalse(TM.request_stop("t2", "chapter")["ok"])

    def test_cancel_queued(self):
        r = TM.enqueue("translate", "w_alpha", {})
        tid = r["task_id"]
        r2 = TM.cancel_queued(tid)
        self.assertTrue(r2["ok"], r2)
        self.assertNotIn(tid, TM._load_queue())
        self.assertEqual(TM._load_task(tid)["status"], "cancelled")
        # 运行中不可取消
        self._task("t3", pid=os.getpid())
        self.assertFalse(TM.cancel_queued("t3")["ok"])

    def test_force_kill_no_pid(self):
        self._task("t4", pid=None)
        self.assertFalse(TM.force_kill("t4")["ok"])

    def test_remove_final_only(self):
        # 终态可删
        self._task("t5", status="done", pid=None)
        r = TM.remove_task("t5")
        self.assertTrue(r["ok"], r)
        self.assertIsNone(TM._load_task("t5"))
        self.assertFalse(os.path.exists(TM.hb_path("t5")))
        # 运行中不可删
        self._task("t6", pid=os.getpid())
        self.assertFalse(TM.remove_task("t6")["ok"])
        # 不存在
        self.assertFalse(TM.remove_task("nope")["ok"])


class TestPruneFinished(TMBase):
    """一键清理结束任务（2026-09-19）：只清终态，运行中/排队中必须保留。"""

    def test_cleans_all_final_keeps_active(self):
        self._task("f1", status="done", pid=None)
        self._task("f2", status="stopped", pid=None)
        self._task("f3", status="failed", pid=None)
        self._task("f4", status="crashed", pid=None)
        self._task("f5", status="cancelled", pid=None)
        self._task("f6", status="stalled", pid=None)
        # 活跃/排队：绝不删（删排队 = 静默丢待跑工作）
        self._task("a1", status="running", pid=os.getpid())
        self._task("a2", status="stopping", pid=os.getpid())
        self._task("a3", status="starting", pid=os.getpid())
        TM._save_queue(["a3"])
        r = TM.prune_finished_tasks()
        self.assertTrue(r["ok"], r)
        self.assertEqual(sorted(r["removed"]), ["f1", "f2", "f3", "f4", "f5", "f6"])
        self.assertEqual(r["count"], 6)
        self.assertEqual(r["kept"], {"running": 1, "starting": 1, "stopping": 1})
        for tid in ("f1", "f6"):
            self.assertIsNone(TM._load_task(tid), tid)
        for tid in ("a1", "a2", "a3"):
            self.assertIsNotNone(TM._load_task(tid), tid)
        self.assertIn("a3", TM._load_queue())          # 排队任务保留在队列里

    def test_queue_entry_of_final_task_dropped(self):
        """终态任务若残留队列条目 → 一并清掉（remove_task 语义）。"""
        self._task("f7", status="done", pid=None)
        TM._save_queue(["f7"])
        r = TM.prune_finished_tasks()
        self.assertEqual(r["removed"], ["f7"])
        self.assertEqual(TM._load_queue(), [])

    def test_nothing_to_clean(self):
        self._task("a4", status="running", pid=os.getpid())
        r = TM.prune_finished_tasks()
        self.assertEqual(r["count"], 0)
        self.assertEqual(r["removed"], [])
        self.assertIn("保留", r["msg"])

    def test_empty_dir(self):
        r = TM.prune_finished_tasks()
        self.assertTrue(r["ok"])
        self.assertEqual(r["count"], 0)
        self.assertNotIn("保留", r["msg"])


class TestPidAlive(TMBase):
    def test_alive_true(self):
        self.assertTrue(TM._pid_alive(os.getpid()))

    def test_dead_false(self):
        self.assertFalse(TM._pid_alive(99999999))
        self.assertFalse(TM._pid_alive(None))
        self.assertFalse(TM._pid_alive(0))


class TestReconcile(TMBase):
    DEAD_PID = 99999999

    def test_dead_pid_with_hb_rc(self):
        self._task("t1", pid=self.DEAD_PID)
        TM._write_json(TM.hb_path("t1"), {"phase": "翻译", "rc": TM.RC_STOPPED})
        t = TM.get_task("t1")
        self.assertEqual(t["status"], "stopped")
        self.assertEqual(t["rc"], TM.RC_STOPPED)

    def test_dead_pid_no_hb_is_crashed(self):
        self._task("t2", pid=self.DEAD_PID)
        t = TM.get_task("t2")
        self.assertEqual(t["status"], "crashed")

    def test_dead_pid_rc0_done(self):
        self._task("t3", pid=self.DEAD_PID)
        TM._write_json(TM.hb_path("t3"), {"rc": 0})
        self.assertEqual(TM.get_task("t3")["status"], "done")

    def test_alive_pid_untouched(self):
        self._task("t4", pid=os.getpid())
        self.assertEqual(TM.get_task("t4")["status"], "running")

    def test_hb_merged(self):
        self._task("t5", pid=os.getpid())
        TM._write_json(TM.hb_path("t5"), {"phase": "清理", "chapter_idx": 3, "chapter_total": 14})
        t = TM.get_task("t5")
        self.assertEqual(t["hb"]["phase"], "清理")
        self.assertEqual(t["hb"]["chapter_idx"], 3)


class TestScheduler(TMBase):
    def test_tick_starts_head_and_finalizes(self):
        # 假命令：立即退出 0
        old = TM._build_command
        TM._build_command = lambda tt, w, p: [sys.executable, "-c", "pass"]
        try:
            r = TM.enqueue("translate", "w_alpha", {})
            started = TM.scheduler_tick()
            self.assertEqual(started, [r["task_id"]])
            t = TM.get_task(r["task_id"])
            self.assertIn(t["status"], ("running", "done"))   # pass 极快退出时可能已 done
            self.assertTrue(t["pid"])
            self.assertNotIn(r["task_id"], TM._load_queue())
            # 等 watchdog 收尾（crashed 无 rc=竞态误判，继续等 _watch 覆盖）
            for _ in range(100):
                tt = TM.get_task(r["task_id"])
                if tt["status"] == "done":
                    break
                if tt["status"] in ("failed", "crashed") and tt.get("rc") is not None:
                    break
                time.sleep(0.1)
            self.assertEqual(TM.get_task(r["task_id"])["status"], "done")
            self.assertEqual(TM.get_task(r["task_id"])["rc"], 0)
        finally:
            TM._build_command = old

    def test_max_parallel_and_fifo(self):
        old_cmd, old_mp = TM._build_command, TM.MAX_PARALLEL
        TM._build_command = lambda tt, w, p: [sys.executable, "-c", "import time;time.sleep(30)"]
        TM.MAX_PARALLEL = 2
        procs = []
        try:
            ids = [TM.enqueue("translate", w, {})["task_id"] for w in self.WORKS]
            started = TM.scheduler_tick()
            started += TM.scheduler_tick()   # 第二轮补第二个
            self.assertEqual(len(started), 2)
            running = [t for t in TM.list_tasks() if t["status"] == "running"]
            queued = [t for t in TM.list_tasks() if t["status"] == "queued"]
            self.assertEqual(len(running), 2)
            self.assertEqual(len(queued), 1)
            self.assertEqual(queued[0]["task_id"], ids[2])   # FIFO: 第三个排队
            procs = list(TM._watch_procs.values())
        finally:
            TM._build_command = old_cmd
            TM.MAX_PARALLEL = old_mp
            for p in procs:
                try:
                    p.kill()
                except Exception:
                    pass
            time.sleep(0.3)

    def test_runner_spawn_detached(self):
        """2026-08-31 重启不中断: runner 必须 CREATE_NO_WINDOW|CREATE_NEW_PROCESS_GROUP
        启动（不弹窗 + 独立进程组，bat 杀工作台不连坐 runner）。"""
        import subprocess as _sp
        old_cmd, old_popen = TM._build_command, _sp.Popen
        captured = {}
        try:
            TM._build_command = lambda tt, w, p: [sys.executable, "-c", "pass"]

            def _fake_popen(*a, **kw):
                captured.update(kw)
                return old_popen(*a, **kw)

            _sp.Popen = _fake_popen
            r = TM.enqueue("translate", "w_alpha", {})
            TM.scheduler_tick()
            self.assertIn("creationflags", captured)
            expected = (0x08000000 | 0x200)   # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
            self.assertEqual(captured["creationflags"], expected)
            self.assertTrue(captured.get("close_fds"))
            # 收尾
            for _ in range(50):
                if TM.get_task(r["task_id"])["status"] == "done":
                    break
                time.sleep(0.1)
        finally:
            TM._build_command = old_cmd
            _sp.Popen = old_popen


class TestUsage(TMBase):
    def test_task_field_preferred(self):
        now = time.time()
        t = self._task("tu", status="done", created_ts=now - 100, finished_ts=now)
        rows = [
            {"tag": "translate", "ts": now - 50, "prompt": 10, "completion": 5,
             "total": 15, "task": "tu"},
            {"tag": "translate", "ts": now - 50, "prompt": 100, "completion": 50,
             "total": 150},                       # 同窗但无 task → 不计
        ]
        with open(os.path.join(TM.ROOT, "usage.jsonl"), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        u = TM.aggregate_usage(t)
        self.assertEqual(u["total"], 15)
        self.assertEqual(u["calls"], 1)
        self.assertNotIn("approx", u)

    def test_window_fallback_approx(self):
        now = time.time()
        t = self._task("tw", status="done", created_ts=now - 100, finished_ts=now)
        with open(os.path.join(TM.ROOT, "usage.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"tag": "pass2", "ts": now - 50, "prompt": 3,
                                "completion": 2, "total": 5}) + "\n")
        u = TM.aggregate_usage(t)
        self.assertEqual(u["total"], 5)
        self.assertTrue(u.get("approx"))

    def test_tags_breakdown(self):
        """分阶段明细（2026-10-11）：翻译任务先翻译后校对是**同一个 task id**，
        合计数 = 两阶段之和，tags 给出各阶段自己的量（翻译 0 也照实记 0 次）。"""
        now = time.time()
        t = self._task("tt", status="done", created_ts=now - 100, finished_ts=now)
        rows = [
            {"tag": "translate", "ts": now - 90, "prompt": 100, "completion": 20,
             "total": 120, "task": "tt", "backend": "opencode_go"},
            {"tag": "refine", "ts": now - 50, "prompt": 300, "completion": 60,
             "total": 360, "task": "tt", "backend": "deepseek"},
            {"tag": "refine", "ts": now - 40, "prompt": 30, "completion": 6,
             "total": 36, "task": "tt", "backend": "deepseek"},
        ]
        with open(os.path.join(TM.ROOT, "usage.jsonl"), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        u = TM.aggregate_usage(t)
        self.assertEqual(u["total"], 516)            # 合计 = 翻译 120 + 校对 396
        self.assertEqual(u["calls"], 3)
        self.assertEqual(u["tags"]["translate"]["total"], 120)
        self.assertEqual(u["tags"]["translate"]["calls"], 1)
        self.assertEqual(u["tags"]["refine"]["total"], 396)
        self.assertEqual(u["tags"]["refine"]["calls"], 2)
        self.assertEqual(u["backends"], ["deepseek", "opencode_go"])

    def test_rotated_file_rows_counted(self):
        """滚动文件也要算（2026-10-11 修）：aa_usage 超 10MB 把主文件改名成 .1 ——
        翻译阶段的归因会落在 .1，只读主文件的任务卡就"只剩校对消耗"。
        实测 t20260929-180702-bd35 的 614 行翻译归因被 .1 吞掉。"""
        now = time.time()
        t = self._task("tr", status="done", created_ts=now - 100, finished_ts=now)
        with open(os.path.join(TM.ROOT, "usage.jsonl.1"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"tag": "translate", "ts": now - 90, "prompt": 10,
                                "completion": 1, "total": 11, "task": "tr"}) + "\n")
        with open(os.path.join(TM.ROOT, "usage.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"tag": "refine", "ts": now - 50, "prompt": 20,
                                "completion": 2, "total": 22, "task": "tr"}) + "\n")
        u = TM.aggregate_usage(t)
        self.assertEqual(u["total"], 33)
        self.assertEqual(u["tags"]["translate"]["total"], 11)
        self.assertEqual(u["tags"]["refine"]["total"], 22)
        self.assertNotIn("approx", u)


    def test_env_passthrough_to_runner(self):
        """任务参数 env 透传(AA_FREE_MODE=1)应注入子进程环境。"""
        old = TM._build_command
        TM._build_command = lambda tt, w, p: [sys.executable, "-c",
            "import os,sys; sys.exit(0 if os.environ.get('AA_FREE_MODE')=='1' else 1)"]
        try:
            r = TM.enqueue("translate", "w_gamma", {"chapters": "new", "env": {"AA_FREE_MODE": "1"}})
            tid = r["task_id"]
            TM.scheduler_tick()
            for _ in range(60):
                st = TM.get_task(tid)["status"]
                if st == "done":
                    break
                if st in ("failed", "crashed") and TM.get_task(tid).get("rc") is not None:
                    break      # 有 rc 的终态才是真终态（rc None=crashed 竞态，继续等）
                time.sleep(0.1)
            self.assertEqual(TM.get_task(tid)["status"], "done")
            self.assertEqual(TM.get_task(tid)["rc"], 0)
        finally:
            TM._build_command = old

    def test_no_env_passthrough_default(self):
        """未带 env 参数时子进程环境里的 AA_FREE_MODE 应为空(默认继承主进程 env)。"""
        old = TM._build_command
        TM._build_command = lambda tt, w, p: [sys.executable, "-c",
            "import os,sys; sys.exit(0 if os.environ.get('AA_FREE_MODE') not in ('1',) else 1)"]
        try:
            r = TM.enqueue("translate", "w_alpha", {})
            tid = r["task_id"]
            TM.scheduler_tick()
            for _ in range(60):
                st = TM.get_task(tid)["status"]
                if st == "done":
                    break
                if st in ("failed", "crashed") and TM.get_task(tid).get("rc") is not None:
                    break      # 有 rc 的终态才是真终态（rc None=crashed 竞态，继续等）
                time.sleep(0.1)
            self.assertEqual(TM.get_task(tid)["status"], "done")
            self.assertEqual(TM.get_task(tid)["rc"], 0)
        finally:
            TM._build_command = old


class TestE2EStop(TMBase):
    """端到端协议：enqueue→scheduler 启动假 runner→request_stop(asap)→runner 见标志退出 41→reconcile 成 stopped。
    验证 task_manager + task_report + 停止标志文件 + 心跳 rc 全链路。"""

    def test_stop_asap_end_to_end(self):
        fake = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fake_runner.py")
        old = TM._build_command
        TM._build_command = lambda tt, w, p: [sys.executable, fake]
        try:
            r = TM.enqueue("translate", "w_alpha", {})
            tid = r["task_id"]
            started = TM.scheduler_tick()
            self.assertEqual(started, [tid])
            t = TM.get_task(tid)
            self.assertEqual(t["status"], "running")
            self.assertTrue(t["pid"])
            # 发尽快停 → 假 runner 应 finalize(41)+exit 41 → watchdog/reconcile 终态 stopped
            rr = TM.request_stop(tid, "asap")
            self.assertTrue(rr["ok"], rr)
            for _ in range(80):
                if TM.get_task(tid)["status"] == "stopped":
                    break
                time.sleep(0.1)
            t = TM.get_task(tid)
            self.assertEqual(t["status"], "stopped")
            self.assertEqual(t["rc"], TM.RC_STOPPED)
            self.assertNotIn(tid, TM._load_queue())
        finally:
            TM._build_command = old

    def test_no_stop_exits_zero(self):
        fake = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fake_runner.py")
        old = TM._build_command
        TM._build_command = lambda tt, w, p: [sys.executable, fake]
        try:
            r = TM.enqueue("download", "w_beta", {})
            tid = r["task_id"]
            TM.scheduler_tick()
            for _ in range(80):
                if TM.get_task(tid)["status"] in ("done", "failed", "crashed"):
                    break
                time.sleep(0.1)
            self.assertEqual(TM.get_task(tid)["status"], "done")
            self.assertEqual(TM.get_task(tid)["rc"], 0)
        finally:
            TM._build_command = old


class TestFollowupChain(TMBase):
    """链式后续（2026-09-13）：「更新 → 下载并翻译」= download 正常完成后自动接 translate。"""

    def test_chain_on_success(self):
        t = self._task("t1", type="download", status="running", pid=None,
                       params={"chapters": "100,101",
                               "then": {"type": "translate", "chapters": "100,101"}})
        TM._finalize(t, 0)
        fu = TM._load_task("t1").get("followup") or {}
        self.assertTrue(fu.get("ok"), fu)
        nxt = TM._load_task(fu["task_id"])
        self.assertEqual(nxt["type"], "translate")
        self.assertEqual(nxt["work"], self.WORKS[0])
        self.assertEqual(nxt["params"]["chapters"], "100,101")
        self.assertEqual(nxt["status"], "queued")

    def test_chain_uses_parent_chapters_when_omitted(self):
        t = self._task("t2", type="download", status="running",
                       params={"chapters": "7,8", "then": {"type": "translate"}})
        TM._finalize(t, 0)
        fu = TM._load_task("t2")["followup"]
        self.assertEqual(TM._load_task(fu["task_id"])["params"]["chapters"], "7,8")

    def test_chain_env_passthrough(self):
        t = self._task("t3", type="download", status="running",
                       params={"chapters": "1", "then": {"type": "translate",
                                                          "env": {"AA_FREE_MODE": "1"}}})
        TM._finalize(t, 0)
        nxt = TM._load_task(TM._load_task("t3")["followup"]["task_id"])
        self.assertEqual(nxt["params"]["env"], {"AA_FREE_MODE": "1"})

    def test_no_chain_on_failure_and_stop(self):
        for tid, rc in (("t4", 1), ("t5", TM.RC_STOPPED)):
            t = self._task(tid, type="download", status="running",
                           params={"chapters": "1", "then": {"type": "translate"}})
            TM._finalize(t, rc)
            fu = TM._load_task(tid)["followup"]
            self.assertFalse(fu["ok"], fu)
            self.assertIn("不链式", fu["error"])
            self.assertEqual(len([x for x in TM.list_tasks() if x["type"] == "translate"]), 0)

    def test_finalize_idempotent_no_double_chain(self):
        t = self._task("t6", type="download", status="running",
                       params={"chapters": "1", "then": {"type": "translate"}})
        TM._finalize(t, 0)
        TM._finalize(TM._load_task("t6"), 0)     # 第二次必须空转
        self.assertEqual(len([x for x in TM.list_tasks() if x["type"] == "translate"]), 1)

    def test_chain_end_to_end_with_fake_runner(self):
        """真调度器 E2E：download 跑完（假命令 rc=0）→ 自动入队 translate 并启动。"""
        old = TM._build_command
        TM._build_command = lambda tt, w, p: [sys.executable, "-c", "pass"]
        try:
            r = TM.enqueue("download", "w_gamma",
                           {"chapters": "5", "then": {"type": "translate", "chapters": "5"}})
            tid = r["task_id"]
            self.assertEqual(TM.scheduler_tick(), [tid])
            fu_tid = None
            deadline = time.time() + 60          # 机器忙（并行跑用例）时子进程/心跳都慢，预算给足
            while time.time() < deadline:
                t = TM.get_task(tid) or {}
                fu = t.get("followup") or {}
                if fu.get("task_id"):
                    fu_tid = fu["task_id"]
                    break
                time.sleep(0.1)
            self.assertTrue(fu_tid, "60s 内没等到链式后续任务（记录=%r）" % (TM.get_task(tid),))
            # 后续 translate 任务也应被调度器启动并正常收尾
            for _ in range(100):
                TM.scheduler_tick()
                if TM.get_task(fu_tid)["status"] in ("done", "failed", "crashed"):
                    break
                time.sleep(0.1)
            nxt = TM.get_task(fu_tid)
            self.assertEqual(nxt["type"], "translate")
            self.assertEqual(nxt["status"], "done")
            self.assertEqual(nxt["rc"], 0)
        finally:
            TM._build_command = old


if __name__ == "__main__":
    unittest.main()
