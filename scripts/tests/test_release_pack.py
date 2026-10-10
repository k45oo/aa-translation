# -*- coding: utf-8 -*-
r"""发行包构建：**产物目录只能"挪开"、绝不能删**（2026-10-09 两次数据丢失事故的根治）。

事故一：测试实例跑在 `导出/<包>/`（数据根 = 包目录），重建时 `shutil.rmtree` 删到一半撞上被占用的
目录报错 —— 但**已删的文件回不来**（丢了 `.env`/`aaManga`/数据根 registry）。
事故二：同一场景下守卫**没拦住**（原因未定论），整棵数据根被换成干净包，用户 1 章翻译+校对产物没了。

现在的机制：`_set_aside_out_dir()` 把旧目录 `os.replace` 成 `<名>.old_<时间戳>`（原子、瞬间、可反悔），
只顺手清掉更早的 `.old_*`（保留最近 2 份）。本用例守住"原目录内容一个字节都不少地还在"。
"""

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS = os.path.join(os.path.dirname(_HERE), "tools")


def _load():
    spec = importlib.util.spec_from_file_location(
        "build_release_test", os.path.join(_TOOLS, "build_release.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSetAsideOutDir(unittest.TestCase):
    def setUp(self):
        self.m = _load()
        self.tmp = tempfile.mkdtemp(prefix="release_aside_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mk(self, name, files=("a.txt",)):
        d = os.path.join(self.tmp, name)
        os.makedirs(os.path.join(d, "aaManga", "作品"), exist_ok=True)
        for f in files:
            with open(os.path.join(d, "aaManga", "作品", f), "w", encoding="utf-8") as fh:
                fh.write(f + " 的内容")
        return d

    def test_旧目录被挪开而不是删掉(self):
        out = self._mk(self.m.NAME)
        aside = self.m._set_aside_out_dir(out)
        self.assertFalse(os.path.isdir(out), "原路径应已让位")
        self.assertTrue(aside and os.path.isdir(aside), "应出现 .old_<时间戳> 副本")
        self.assertTrue(aside.startswith(out + ".old_"))
        # 内容必须一个都不少
        inner = os.path.join(aside, "aaManga", "作品", "a.txt")
        self.assertTrue(os.path.isfile(inner), "挪开的目录里数据必须还在")
        self.assertIn("a.txt 的内容", open(inner, encoding="utf-8").read())

    def test_目录不存在时什么都不做(self):
        self.assertIsNone(self.m._set_aside_out_dir(os.path.join(self.tmp, "不存在")))

    def test_只留最近两份_更早的清掉(self):
        out = self._mk(self.m.NAME)
        parent, base = self.tmp, self.m.NAME + ".old_"
        for ts in ("20260101_000001", "20260102_000002", "20260103_000003", "20260104_000004"):
            os.makedirs(os.path.join(parent, base + ts))
        self.m._set_aside_out_dir(out, keep=2)
        left = sorted(d for d in os.listdir(parent) if d.startswith(base))
        self.assertEqual(len(left), 2, "只保留最近 2 份历史产物: %s" % left)
        self.assertEqual(left, sorted(left)[-2:], "留下的必须是最新的两份")

    def test_构建脚本里不再直接删产物目录(self):
        """用 AST 查**实际调用**（文档串/注释里提到 rmtree 不算）。"""
        import ast
        src = open(os.path.join(_TOOLS, "build_release.py"), encoding="utf-8").read()
        bad = []
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if (isinstance(fn, ast.Attribute) and fn.attr == "rmtree" and node.args
                    and isinstance(node.args[0], ast.Name) and node.args[0].id == "out_dir"):
                bad.append(node.lineno)
        self.assertFalse(bad, "产物目录只能挪开（第 %s 行仍在直接删）—— 直接删会重演数据丢失事故" % bad)


class TestLiveDataRoot(unittest.TestCase):
    """「活数据根」判定的正反例。

    2026-10-10 修正的动机：**上一次打出的包**自己就带非空 `aaManga/`（示例作品）+ 示例
    `work_meta.json`，旧判据把每个旧包都当活数据 → 同一个 `--out` 永远拒绝重打
    （挪开机制永远轮不上）。反过来，被人真跑过的包必须仍被拒。
    """

    def setUp(self):
        self.m = _load()
        self.tmp = tempfile.mkdtemp(prefix="release_live_")
        self.ex = self.m.EXAMPLE["dir"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pkg(self, name="pkg", marker=True, example_dirs=("aa", "汉化", "work", "raw"),
             meta=True, other_work=None):
        """造一个"上一次打出的包"目录。"""
        d = os.path.join(self.tmp, name)
        os.makedirs(os.path.join(d, "aaManga", self.ex), exist_ok=True)
        for sub in example_dirs:
            os.makedirs(os.path.join(d, "aaManga", self.ex, sub), exist_ok=True)
        if other_work:
            os.makedirs(os.path.join(d, "aaManga", other_work), exist_ok=True)
        if meta:
            os.makedirs(os.path.join(d, "aa_web_app", "data"), exist_ok=True)
            open(os.path.join(d, "aa_web_app", "data", "work_meta.json"), "w",
                 encoding="utf-8").write("{}")
        if marker:
            open(os.path.join(d, ".release_package"), "w", encoding="utf-8").write("{}")
        return d

    def test_上一次的包不算活数据(self):
        """回归：旧包必须允许重打（交给 _set_aside_out_dir 挪开，不删）。"""
        self.assertFalse(self.m._looks_like_live_data_root(self._pkg()), "旧包应可重打")

    def test_示例作品被真翻过就算活数据(self):
        d = self._pkg()
        open(os.path.join(d, "aaManga", self.ex, "汉化", "entry_1_汉化.txt"), "w",
             encoding="utf-8").write("译文")
        self.assertTrue(self.m._looks_like_live_data_root(d))

    def test_示例作品有校对ckpt就算活数据(self):
        d = self._pkg()
        open(os.path.join(d, "aaManga", self.ex, "work", "entry_1.txt.ckpt.json"), "w",
             encoding="utf-8").write("{}")
        self.assertTrue(self.m._looks_like_live_data_root(d))

    def test_包目录里多出别的作品就算活数据(self):
        self.assertTrue(self.m._looks_like_live_data_root(self._pkg(other_work="某人的作品")))

    def test_env填过key就算活数据(self):
        d = self._pkg()
        open(os.path.join(d, ".env"), "w", encoding="utf-8").write("DEEPSEEK_API_KEY=sk-xxx\n")
        self.assertTrue(self.m._looks_like_live_data_root(d))

    def test_env只有空模板不算(self):
        d = self._pkg()
        open(os.path.join(d, ".env"), "w", encoding="utf-8").write("# 注释\nDEEPSEEK_API_KEY=\n")
        self.assertFalse(self.m._looks_like_live_data_root(d))

    def test_无标记目录保持旧行为(self):
        d = os.path.join(self.tmp, "plain")
        os.makedirs(os.path.join(d, "aaManga", "作品"), exist_ok=True)
        self.assertTrue(self.m._looks_like_live_data_root(d), "没有包标记还带作品 → 认活")
        self.assertFalse(self.m._looks_like_live_data_root(os.path.join(self.tmp, "空")))


if __name__ == "__main__":
    unittest.main()
