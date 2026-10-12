# -*- coding: utf-8 -*-
"""`aa_web_exe` 的冻结态行为（2026-10-09）。

守住两件事：

1. **数据根 / 代码根分离**：冻结态数据根 = exe 所在目录（`AA_BASE` 覆盖），源码态 = 仓库根；
2. **registry 持久化引导**：冻结态启动时把 registry 复制到 `<数据根>/scripts/works/` 并让 import 优先命中它
   —— exe 里那份属于**临时解包目录**，不这么做「＋ 添加作品」等于写进临时目录，重启就丢
   （实测表现为：作品页看不到章节）。
"""

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS = os.path.join(os.path.dirname(_HERE), "tools")


def _load_aa_web_exe():
    """按路径加载 aa_web_exe（不改 sys.path，免得抢到别的同名模块）。"""
    spec = importlib.util.spec_from_file_location("aa_web_exe_test", os.path.join(_TOOLS, "aa_web_exe.py"))
    mod = importlib.util.module_from_spec(spec)
    env_before = {k: os.environ.get(k) for k in ("AA_BASE", "AA_ENV")}
    spec.loader.exec_module(mod)
    for k, v in env_before.items():          # 模块 import 时会 setdefault，测完还原
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return mod


class _Frozen:
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


class TestBootstrapRegistry(unittest.TestCase):
    def setUp(self):
        self.m = _load_aa_web_exe()

    def test_源码态不做任何事(self):
        """源码态仓库里那份 registry 本来就是可写的 → 不该复制、不该动 sys.path。"""
        code, data = self.m.CODE, self.m.DATA
        with tempfile.TemporaryDirectory() as d:
            self.m.CODE, self.m.DATA = d, d
            try:
                self.m._bootstrap_registry()
                self.assertFalse(os.path.exists(os.path.join(d, "scripts", "works", "registry.py")))
            finally:
                self.m.CODE, self.m.DATA = code, data

    def _run_frozen(self, data, src_text="WORKS = {}\n"):
        """造一个"解包目录"（含 registry 种子）+ 一个空数据根，跑冻结态引导。

        返回 `(code_root, sys_path_快照)` —— 快照必须在这里取，因为 finally 会把 sys.path 还原
        （上一版把断言写在 helper 之外，还原后断言必然失败）。
        """
        code = tempfile.mkdtemp(prefix="exe_code_")
        self.addCleanup(shutil.rmtree, code, ignore_errors=True)
        os.makedirs(os.path.join(code, "scripts", "works"))
        with open(os.path.join(code, "scripts", "works", "registry.py"), "w", encoding="utf-8") as f:
            f.write(src_text)
        self.m.CODE, self.m.DATA = code, data
        path_before = list(sys.path)
        # 模拟 exe 的现场：解包目录的 scripts/works 已在 sys.path 上（main.py 会插），
        # 数据根那份必须排到它**前面**才有效。
        code_works = os.path.join(code, "scripts", "works")
        sys.path.insert(0, code_works)
        try:
            with _Frozen():
                self.m._bootstrap_registry()
            return code, list(sys.path)
        finally:
            sys.path[:] = path_before

    def test_冻结态_把_registry_复制到数据根并优先命中(self):
        with tempfile.TemporaryDirectory() as data:
            code, path_snap = self._run_frozen(data, 'WORKS = {"x": 1}\n')
            self.m.CODE, self.m.DATA = code, data
            dst = os.path.join(data, "scripts", "works", "registry.py")
            self.assertTrue(os.path.isfile(dst), "数据根里应出现 registry 副本")
            with open(dst, encoding="utf-8") as f:
                self.assertIn('WORKS = {"x": 1}', f.read())
            # import 必须优先命中数据根那份（否则 add_work 又写回临时解包目录）
            dst_dir = os.path.join(data, "scripts", "works")
            self.assertEqual(path_snap[0], dst_dir)
            self.assertLess(path_snap.index(dst_dir),
                            path_snap.index(os.path.join(code, "scripts", "works")))
            # 并且解包目录那份要被**删掉**：只靠 sys.path 顺序不够——脚本自己 insert(0,
            # <解包目录>/scripts/works) 就能把它翻出来（实测子任务报「未知作品: X（可用: baitou）」）
            self.assertFalse(os.path.isfile(os.path.join(code, "scripts", "works", "registry.py")),
                             "解包目录那份 registry 必须删除，物理上杜绝子进程误命中")

    def test_冻结态_已存在的数据根注册表不被覆盖(self):
        with tempfile.TemporaryDirectory() as data:
            os.makedirs(os.path.join(data, "scripts", "works"))
            mine = os.path.join(data, "scripts", "works", "registry.py")
            with open(mine, "w", encoding="utf-8") as f:
                f.write('WORKS = {"我加的作品": 1}\n')
            self._run_frozen(data, 'WORKS = {"示例": 1}\n')
            with open(mine, encoding="utf-8") as f:
                self.assertIn("我加的作品", f.read(),
                              "用户数据根里的注册表是数据，绝不能被包内种子覆盖")


class TestCliDispatchOrder(unittest.TestCase):
    def test_cli_分支也要先准备数据根(self):
        r"""`--cli` 子任务（下载/翻译/候选提取）必须**先**拿到数据根的 registry。

        实测踩过：`--cli` 分支写在 `_bootstrap_registry()` 之前 return → 子进程命中的是解包目录那份
        （只有示例作品），使用者一按「生成术语候选」就报「未知作品: X（可用: baitou）」。
        """
        m = _load_aa_web_exe()
        calls = []
        orig = (m._ensure_dirs, m._bootstrap_registry, m._run_cli)
        m._ensure_dirs = lambda: calls.append("dirs")
        m._bootstrap_registry = lambda: calls.append("registry")
        m._run_cli = lambda args: (calls.append("cli:" + " ".join(args)), 0)[1]
        argv = sys.argv
        sys.argv = ["AA工作台.exe", "--cli", "x.py", "--work", "w"]
        try:
            rc = m.main()
        finally:
            sys.argv = argv
            (m._ensure_dirs, m._bootstrap_registry, m._run_cli) = orig
        self.assertEqual(calls, ["dirs", "registry", "cli:x.py --work w"],
                         "引导顺序：目录 → 数据根 registry → 子任务")
        self.assertEqual(rc, 0)


class TestCliScriptResolution(unittest.TestCase):
    def test_解包目录的绝对路径改跑数据根那份(self):
        r"""工作台起的子任务传的是**解包目录**里的绝对脚本路径。

        跑那份时脚本用 `__file__` 反推 `_BASE` = 解包目录 → 它把 `<解包目录>/scripts/works`
        插到 sys.path 最前，盖掉引导放进来的数据根注册表 → 子任务只认包里示例作品，报
        「未知作品: X（可用: baitou）」。所以绝对路径落在解包目录时要换成数据根里那份。
        """
        m = _load_aa_web_exe()
        with tempfile.TemporaryDirectory() as d:
            code = os.path.join(d, "code")
            data = os.path.join(d, "data")
            rel = os.path.join("scripts", "common", "gen.py")
            for root in (code, data):
                os.makedirs(os.path.join(root, "scripts", "common"))
                with open(os.path.join(root, rel), "w", encoding="utf-8") as f:
                    f.write("# probe\n")
            m.CODE, m.DATA = code, data
            got = m._resolve_cli_script(os.path.join(code, rel))
            self.assertEqual(os.path.normcase(got), os.path.normcase(os.path.join(data, rel)))
            # 数据根没这份时不能瞎换（否则"找不到脚本"）
            os.remove(os.path.join(data, rel))
            got2 = m._resolve_cli_script(os.path.join(code, rel))
            self.assertEqual(os.path.normcase(got2), os.path.normcase(os.path.join(code, rel)))

    def test_相对路径优先数据根(self):
        m = _load_aa_web_exe()
        with tempfile.TemporaryDirectory() as d:
            code = os.path.join(d, "code")
            data = os.path.join(d, "data")
            os.makedirs(os.path.join(code, "scripts"))
            os.makedirs(os.path.join(data, "scripts"))
            for root in (code, data):
                with open(os.path.join(root, "scripts", "x.py"), "w", encoding="utf-8") as f:
                    f.write("# probe\n")
            m.CODE, m.DATA = code, data
            got = m._resolve_cli_script("scripts/x.py")
            self.assertEqual(os.path.normcase(got), os.path.normcase(os.path.join(data, "scripts", "x.py")))


class TestDataRoot(unittest.TestCase):
    def test_源码态数据根就是仓库根(self):
        m = _load_aa_web_exe()
        repo = os.path.dirname(os.path.dirname(_TOOLS))
        self.assertEqual(os.path.normcase(m.DATA), os.path.normcase(repo))


if __name__ == "__main__":
    unittest.main()
