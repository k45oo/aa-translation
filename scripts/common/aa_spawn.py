# -*- coding: utf-8 -*-
r"""起子进程跑本项目脚本时的统一"解释器前缀"（2026-10-09：冻结 exe 兼容）。

**为什么需要它**：工作台与 runner 用 `subprocess` 起子任务（下载 / 翻译 / 校对 / 生成合集 /
调度器…），命令一律以 `sys.executable` 开头。源码运行没问题；但**冻结成 exe 后
`sys.executable` 就是 exe 自己** —— `[AA工作台.exe, scripts/runner/dl_runner.py, --work, x]`
等于"把工作台再起一份"：脚本参数被入口无视、端口被占、父进程等一个永不退出的服务，
表现就是**任务卡住 / 下载失败**。

统一改成：冻结态把目标脚本交给 exe 的 `--cli` 模式（入口 `scripts/tools/aa_web_exe.py`
用 `runpy` 在本进程里执行它），源码态保持原样。

用法：
    from aa_spawn import py_prefix
    subprocess.run(py_prefix(["-u"]) + [os.path.join(_SCRIPTS, "runner", "dl_runner.py"), ...])
"""

import os
import sys

# 冻结态没有独立解释器，这些"给解释器看"的 flag 由入口统一处理（-u → 入口开行缓冲）
_FROZEN_DROP_FLAGS = {"-u"}


def frozen():
    """当前是不是 PyInstaller 冻结态（exe）。"""
    return bool(getattr(sys, "frozen", False))


def py_prefix(extra=()):
    """命令前缀：`py_prefix(["-u"]) + [脚本, *参数]`。

    源码态 → `[python, "-u"]`；冻结态 → `[exe, "--cli"]`（`-u` 丢弃，入口自开行缓冲）。
    """
    extra = [str(x) for x in extra]
    if frozen():
        return [sys.executable, "--cli"] + [x for x in extra if x not in _FROZEN_DROP_FLAGS]
    return [sys.executable] + extra


def child_env(base=None):
    """子进程环境：确保 `AA_BASE`/`AA_ENV` 指向**数据根**（冻结态 = exe 所在目录）。

    工作台的子任务是本项目脚本，它们靠 `AA_BASE` 找作品数据；冻结时若没传下去，
    子进程会拿 PyInstaller 解包目录当数据根（读不到作品、写的数据随进程丢失）。
    """
    env = dict(os.environ if base is None else base)
    if frozen():
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        env.setdefault("AA_BASE", exe_dir)
        env.setdefault("AA_ENV", os.path.join(exe_dir, ".env"))
    return env
