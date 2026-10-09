# -*- coding: utf-8 -*-
"""`aa_spawn` 单测：起子任务的命令前缀在「源码态 / 冻结 exe 态」两种形态（2026-10-09）。

背景：工作台的任务管理器用 `subprocess` 起子任务（下载/翻译/校对/生成合集），命令一律以
`sys.executable` 开头。冻结成 exe 后 `sys.executable` 是 exe 自己 → 直接 `[exe, 脚本, …]`
会被入口当成"再起一份工作台"（参数被无视、端口被占、父进程等一个永不退出的服务），
用户看到的就是**任务卡住 / 下载失败**。修法：冻结态改走 `[exe, "--cli", 脚本, …]`。

这里守住两件事：① 前缀形态正确；② 子进程环境带上数据根（`AA_BASE`/`AA_ENV`）。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"))
import aa_spawn as S  # noqa: E402


class _Frozen:
    """临时把进程伪装成 PyInstaller 冻结态（`sys.frozen` 存在与否就是判据）。"""

    def __enter__(self):
        self._had = hasattr(sys, "frozen")
        self._old = getattr(sys, "frozen", None)
        sys.frozen = True
        return self

    def __exit__(self, *exc):
        if self._had:
            sys.frozen = self._old
        else:
            del sys.frozen
        return False


class TestPyPrefix(unittest.TestCase):
    def test_源码态_原样传给解释器(self):
        self.assertFalse(S.frozen())
        self.assertEqual(S.py_prefix(), [sys.executable])
        self.assertEqual(S.py_prefix(["-u"]), [sys.executable, "-u"])

    def test_冻结态_改走_cli_模式(self):
        with _Frozen():
            self.assertTrue(S.frozen())
            self.assertEqual(S.py_prefix(), [sys.executable, "--cli"])
            # -u 是给独立解释器的；冻结态没有，丢弃（入口自己开行缓冲）
            self.assertEqual(S.py_prefix(["-u"]), [sys.executable, "--cli"])

    def test_冻结态_拼接脚本与参数(self):
        with _Frozen():
            script = os.path.join("scripts", "runner", "dl_runner.py")
            cmd = S.py_prefix(["-u"]) + [script, "--work", "baitou"]
            self.assertEqual(cmd[:2], [sys.executable, "--cli"])
            # --cli 后紧跟脚本路径（入口就是按 argv[0] 取脚本）
            self.assertEqual(cmd[2], script)
            self.assertEqual(cmd[3:], ["--work", "baitou"])


class TestChildEnv(unittest.TestCase):
    def test_源码态_不动环境(self):
        env = S.child_env()
        self.assertEqual(env.get("AA_BASE"), os.environ.get("AA_BASE"))
        self.assertEqual(env.get("AA_ENV"), os.environ.get("AA_ENV"))

    def test_冻结态_数据根默认_exe_所在目录(self):
        with _Frozen():
            env = S.child_env({})
            exe_dir = os.path.dirname(os.path.abspath(sys.executable))
            self.assertEqual(env["AA_BASE"], exe_dir)
            self.assertEqual(env["AA_ENV"], os.path.join(exe_dir, ".env"))

    def test_冻结态_不覆盖显式设置(self):
        with _Frozen():
            env = S.child_env({"AA_BASE": r"D:\data", "AA_ENV": r"D:\data\my.env"})
            self.assertEqual(env["AA_BASE"], r"D:\data")
            self.assertEqual(env["AA_ENV"], r"D:\data\my.env")

    def test_不污染入参(self):
        base = {}
        with _Frozen():
            S.child_env(base)
        self.assertEqual(base, {}, "child_env 不该改调用方传进来的 dict")


if __name__ == "__main__":
    unittest.main()
