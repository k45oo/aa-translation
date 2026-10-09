# -*- coding: utf-8 -*-
"""校对(refine_aa)阶段的流式日志 + 活性超时 + 失败上报（2026-09-14 事故修复的回归测试）。

事故：translate_runner._run_refine_proofread 用 subprocess.run(capture_output=True,
timeout=3600)——① 总时长硬上限对 20 章大作品严重不足（实测需 ~95min），被砍在第 12 章；
② 超时异常被上层当「不阻塞」吞掉 → rc=0 任务报 done，卡片「待译 8」其实是 8 章从未校对；
③ capture_output 吞掉全程 stdout → 校对阶段日志整段缺失。
本测试用假子进程（零 token、零网络）覆盖新实现：流式透传 / 双阈值超时杀进程 /
未校对 eid 列表 / 抛 ProofreadIncomplete + runner 按失败上报。

运行：.venv/Scripts/python.exe -m unittest discover -s scripts/tests -p "test_refine_stream.py" -v
"""
import os
import shutil
import sys
import tempfile
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "works"),
           os.path.join(_SCRIPTS, "runner")):
    sys.path.insert(0, _p)
import translate_runner as R


class TestRunStreamProc(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _script(self, body):
        """写一个假子进程脚本，返回 [python, -u, path]。"""
        p = os.path.join(self.tmp, "fake.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        return sys.executable, p

    def test_streams_lines_and_returns_rc0(self):
        py, path = self._script("print('a')\nprint('b')\n")
        seen = []
        rc, killed, err = R._run_stream_proc([py, "-u", path], None, lambda s: None,
                                             on_line=seen.append,
                                             total_limit=60, idle_limit=60)
        self.assertEqual(rc, 0)
        self.assertIsNone(killed)
        self.assertEqual(seen, ["a", "b"])

    def test_rc_nonzero_passthrough(self):
        py, path = self._script("raise SystemExit(3)\n")
        rc, killed, err = R._run_stream_proc([py, "-u", path], None, lambda s: None,
                                             total_limit=60, idle_limit=60)
        self.assertEqual(rc, 3)
        self.assertIsNone(killed)   # 非超时：由调用方按 rc 判定

    def test_idle_timeout_kills_hung_child(self):
        """无新输出超阈值 → 看门狗杀进程（真挂死/半开连接场景）。"""
        py, path = self._script("import time\nprint('start', flush=True)\ntime.sleep(120)\n")
        t = time.time()
        rc, killed, err = R._run_stream_proc([py, "-u", path], None, lambda s: None,
                                             total_limit=300, idle_limit=2)
        self.assertLess(time.time() - t, 30, "应被看门狗杀掉, 不能等满 120s")
        self.assertTrue(killed and "无新输出" in killed, killed)
        self.assertNotEqual(rc, 0)

    def test_total_timeout_kills_chatty_child(self):
        """持续输出但总时长超预算 → 也杀（防慢速无限跑）。"""
        py, path = self._script(
            "import time\n"
            "for i in range(120):\n"
            "    print('tick', i, flush=True)\n"
            "    time.sleep(0.5)\n")
        t = time.time()
        rc, killed, err = R._run_stream_proc([py, "-u", path], None, lambda s: None,
                                             total_limit=2, idle_limit=60)
        self.assertLess(time.time() - t, 30)
        self.assertTrue(killed and "总时长" in killed, killed)

    def test_stdout_visible_before_exit(self):
        """关键回归：边跑边可见（旧 capture_output 实现要等进程退出才拿得到）。"""
        py, path = self._script(
            "import time\n"
            "print('first', flush=True)\n"
            "time.sleep(3)\n"
            "print('last', flush=True)\n")
        got_at = {}

        def _on_line(line):
            got_at[line] = time.time()

        t0 = time.time()
        R._run_stream_proc([py, "-u", path], None, lambda s: None, on_line=_on_line,
                           total_limit=60, idle_limit=60)
        self.assertIn("first", got_at)
        self.assertIn("last", got_at)
        self.assertLess(got_at["first"] - t0, 2.5, "首行应实时透传")
        self.assertGreater(got_at["last"] - got_at["first"], 2.0)


class TestRefineProofread(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_with_fake(self, body, eids):
        """把 _REFINE_SCRIPT 换成假脚本跑 _run_refine_proofread，返回 (异常, 日志)。"""
        p = os.path.join(self.tmp, "fake_refine.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        logs = []
        old = R._REFINE_SCRIPT
        R._REFINE_SCRIPT = p
        err = None
        try:
            R._run_refine_proofread("fake_work", {}, eids, logs.append)
        except Exception as e:      # noqa: BLE001 - 测试要断言异常类型
            err = e
        finally:
            R._REFINE_SCRIPT = old
        return err, "\n".join(logs)

    def test_missing_eids_helper(self):
        self.assertEqual(R._refine_missing(["1", "2", "3"], {"entry_1", "entry_3"}), ["2"])
        self.assertEqual(R._refine_missing(["1"], set()), ["1"])

    def test_all_chapters_done_ok(self):
        body = "print('[entry_1] 无问题')\nprint('[entry_2] 三类修复: 还原0 重建1 补译0')\n"
        err, logs = self._run_with_fake(body, ["1", "2"])
        self.assertIsNone(err)
        self.assertIn("全部校对完成", logs)
        # 流式透传：每章一行都进了日志
        self.assertIn("[entry_1]", logs)
        self.assertIn("[entry_2]", logs)

    def test_nonzero_rc_raises_with_missing_list(self):
        """子进程异常退出 → 抛 ProofreadIncomplete, 日志给出未校对 eid + 补跑命令。"""
        body = "print('[entry_1] 无问题')\nraise SystemExit(3)\n"
        err, logs = self._run_with_fake(body, ["1", "2", "3"])
        self.assertIsInstance(err, R.ProofreadIncomplete)
        self.assertIn("未校对 2/3 章", logs)
        self.assertIn("2,3", logs)
        self.assertIn("补跑", logs)
        self.assertIn("--type proofread", logs)

    def test_timeout_raises_incomplete(self):
        """超时被杀 → 同样按「未跑完」上报，而不是静默当成功。"""
        body = ("import time\n"
                "print('[entry_9] 无问题')\n"
                "time.sleep(120)\n")
        logs = []
        old_script, old_total, old_idle = R._REFINE_SCRIPT, R._REFINE_TOTAL_TIMEOUT, R._REFINE_IDLE_TIMEOUT
        p = os.path.join(self.tmp, "hang_refine.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        R._REFINE_SCRIPT = p
        R._REFINE_TOTAL_TIMEOUT, R._REFINE_IDLE_TIMEOUT = 300, 2
        err = None
        try:
            R._run_refine_proofread("fake_work", {}, ["9", "10"], logs.append)
        except Exception as e:      # noqa: BLE001
            err = e
        finally:
            R._REFINE_SCRIPT, R._REFINE_TOTAL_TIMEOUT, R._REFINE_IDLE_TIMEOUT = old_script, old_total, old_idle
        self.assertIsInstance(err, R.ProofreadIncomplete)
        joined = "\n".join(logs)
        self.assertIn("无新输出", joined)
        self.assertIn("未校对 1/2 章", joined)


class TestRunnerReportsFailure(unittest.TestCase):
    """源码级回归守卫：旧实现的三个缺陷形态不得复活。"""

    def setUp(self):
        with open(os.path.join(_SCRIPTS, "runner", "translate_runner.py"), encoding="utf-8") as f:
            self.src = f.read()

    def test_no_capture_output_hard_timeout(self):
        # 旧实现特征：subprocess.run(capture_output=True, ..., env=None, timeout=3600)
        self.assertNotIn("env=None, timeout=3600", self.src)
        self.assertNotIn("subprocess.run(cmd, cwd=_SCRIPTS, capture_output=True", self.src)
        self.assertNotIn("[refine-proofread] 失败(不阻塞)", self.src)

    def test_proofread_failure_marks_task_failed(self):
        self.assertIn("proofread_err = str(e)", self.src)
        self.assertIn("if proofread_err:", self.src)
        self.assertIn("return 1", self.src)
        # 关键：main() 的非零返回必须转成进程退出码, 否则任务管理器读 returncode=0 → 仍标 done
        self.assertIn("sys.exit(_rc)", self.src)

    def test_refine_limits_env_overridable(self):
        for key in ("AA_REFINE_TIMEOUT", "AA_REFINE_IDLE_TIMEOUT", "AA_REFINE_PER_CHAPTER"):
            self.assertIn(key, self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
