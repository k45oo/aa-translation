# -*- coding: utf-8 -*-
r"""候选池续跑包装（`run_full_pool_until_done.py`）的失败上报。

守住一条：子进程**崩了**（非 0 退出、又没留下 `pool_progress.json`）时，包装脚本必须把失败
报出去，不能打印「已完成」并 return 0 —— 实测打包版缺 `janome.tokenizer` 时就是这样：
候选一条没生成，界面却显示成功，用户反复点、反复「还是不行」。
"""

import importlib.util
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_COMMON = os.path.join(os.path.dirname(_HERE), "common")


def _load(*extra_path):
    for p in extra_path:
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location(
        "run_pool_test", os.path.join(_COMMON, "run_full_pool_until_done.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeRun:
    def __init__(self, rc):
        self.returncode = rc


class TestRunPoolExit(unittest.TestCase):
    def setUp(self):
        self.m = _load(_COMMON, os.path.join(os.path.dirname(_HERE), "works"), os.path.dirname(_HERE))

    def _run(self, rc_seq, work_dir, max_rounds=3):
        """把 subprocess.run / registry.get / sleep 换成假的，跑 main()。"""
        seq = list(rc_seq)

        def fake_run(cmd, **kw):
            return _FakeRun(seq.pop(0) if seq else 1)

        orig = (self.m.subprocess.run, self.m.registry.get, self.m.time.sleep, sys.argv)
        self.m.subprocess.run = fake_run
        self.m.registry.get = lambda w: {"work_dir": work_dir}
        self.m.time.sleep = lambda s: None
        sys.argv = ["run_full_pool_until_done.py", "--work", "w", "--max-rounds", str(max_rounds)]
        try:
            return self.m.main()
        finally:
            (self.m.subprocess.run, self.m.registry.get, self.m.time.sleep) = orig[:3]
            sys.argv = orig[3]

    def test_子进程崩溃不再被报成已完成(self):
        """--full 崩（rc=1）且没有 progress 文件 → 必须返回非 0（旧实现返回 0）。"""
        with tempfile.TemporaryDirectory() as d:
            # 阶段0 补台词要 0（rc=0 即进入筛选），随后 --full 全轮都崩
            rc = self._run([0, 1, 1, 1], d)
        self.assertNotEqual(rc, 0, "子进程崩溃必须让包装脚本以非 0 退出，否则界面会把失败报成成功")

    def test_正常完成仍返回0(self):
        """--full 退出 0 且没有 progress 文件 = 真跑完 → 0。"""
        with tempfile.TemporaryDirectory() as d:
            rc = self._run([0, 0], d)
        self.assertEqual(rc, 0)

    def test_跑满轮数仍未完成算失败(self):
        """每轮都留 progress（还在积累）→ 跑满 max-rounds 也是失败，不是成功。"""
        with tempfile.TemporaryDirectory() as d:
            prog = os.path.join(d, "pool_progress.json")
            with open(prog, "w", encoding="utf-8") as f:
                f.write("{}")
            rc = self._run([0] + [1] * 10, d, max_rounds=2)
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
