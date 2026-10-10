# -*- coding: utf-8 -*-
r"""作品删除：registry 条目必须写在 **app 实际 import 的那份** registry.py 上。

背景（2026-10-09 实测，MM / bar_miku 两部作品中招）：
`_remove_registry_entry` 用 `os.path.join(_SCRIPTS, "works", "registry.py")`，而冻结 exe 里
`_SCRIPTS` 指向 PyInstaller **解包目录** —— 那份 registry 在启动引导时已被删掉（防 shell 子任务
误命中数据根之外的副本）→ 删除作品在"盘上文件已删完"之后报 500 →
用户看到**「文件已删除但记录还在」**（作品列表里留一个点不开又删不掉的僵尸条目）。

现在一律走 `registry.__file__`（app 实际 import 的那份 = 数据根），并在条目不存在 → 返回 False
（接口据此在**删任何文件之前**中止）。
"""

import os
import sys
import tempfile
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_TESTS_DIR))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
for _p in (os.path.join(_REPO_ROOT, "scripts"),
           os.path.join(_REPO_ROOT, "scripts", "common"),
           os.path.join(_REPO_ROOT, "scripts", "works")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# 真实注册表形态：条目是 `    "id": dict(` 起、`    ),` 收（删除逻辑按这两端切）
KEEP_AND_GONE = ('WORKS = {\n'
                 '    "keep": dict(\n'
                 '        title="keep",\n'
                 '    ),\n'
                 '    "gone": dict(\n'
                 '        title="gone",\n'
                 '    ),\n'
                 '}\n')
KEEP_ONLY = ('WORKS = {\n'
             '    "keep": dict(\n'
             '        title="keep",\n'
             '    ),\n'
             '}\n')


def _main():
    import aa_web_app.main as M
    return M


class TestDeleteRegistryPath(unittest.TestCase):
    def setUp(self):
        self.M = _main()
        import registry
        self.reg = registry
        self.old_file = getattr(registry, "__file__", None)

    def tearDown(self):
        if self.old_file is not None:
            self.reg.__file__ = self.old_file

    def test_路径取的是app实际import的那份(self):
        """不是 `_SCRIPTS/works/registry.py`（冻结态那是解包目录，且已被引导删掉）。"""
        p = self.M._registry_path()
        self.assertEqual(os.path.normcase(p), os.path.normcase(self.reg.__file__))

    def test_删条目写在registry_file那份上(self):
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "registry.py")
            with open(fake, "w", encoding="utf-8", newline="") as f:
                f.write(KEEP_AND_GONE)
            self.reg.__file__ = fake
            self.assertTrue(self.M._remove_registry_entry("gone"))
            txt = open(fake, encoding="utf-8").read()
            self.assertNotIn('"gone"', txt)
            self.assertIn('"keep"', txt, "别的条目不能被顺手删掉")

    def test_条目不存在返回False(self):
        """接口据此在**删任何文件之前**中止（旧实现是删完文件才发现摘不掉）。"""
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "registry.py")
            with open(fake, "w", encoding="utf-8", newline="") as f:
                f.write(KEEP_ONLY)
            self.reg.__file__ = fake
            self.assertFalse(self.M._remove_registry_entry("不存在"))

    def test_registry_file缺失时回退到_SCRIPTS那份(self):
        """极端情况（数据根那份被删）：回退老路径，别直接炸。"""
        self.reg.__file__ = os.path.join(tempfile.gettempdir(), "绝对不存在_registry.py")
        p = self.M._registry_path()
        self.assertTrue(p.endswith(os.path.join("scripts", "works", "registry.py")))

    def test_两处写入口都用同一路径(self):
        """追加（＋添加作品）与删除必须写同一个文件，否则加了删不掉/删了还在。"""
        import inspect
        src = inspect.getsource(self.M._append_registry_entry)
        self.assertIn("_registry_path()", src)
        self.assertIn("_registry_path()", inspect.getsource(self.M._remove_registry_entry))


if __name__ == "__main__":
    unittest.main()
