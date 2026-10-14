# -*- coding: utf-8 -*-
"""作者字段口径测试（2026-09-13 用户报「详情悬浮框作者为空、保存导致作者丢失」）。

根因两条：
  ① 详情弹窗只读 work_meta.author，列表页还有「帖头 ◆ 自动提取」兜底 → 两处口径不一致，
     未手动填过作者的作品详情显示空；
  ② 弹窗旧版保存把空输入加前缀写成 '◆'（真值）→ 覆盖列表页提取出来的作者 → 信息丢失。
修复：logic.normalize_author / author_display 统一口径 + 前端空输入送 '' + 后端清洗 '◆'。
运行：python -m unittest discover -s scripts/tests
"""
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
for _p in (_REPO_ROOT, os.path.join(_REPO_ROOT, "scripts", "common")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from aa_web_app import logic  # noqa: E402


class TestNormalizeAuthor(unittest.TestCase):
    def test_bare_diamond_is_cleared(self):
        self.assertEqual(logic.normalize_author("◆"), "")
        self.assertEqual(logic.normalize_author("  ◆  "), "")

    def test_empty_and_none(self):
        self.assertEqual(logic.normalize_author(""), "")
        self.assertEqual(logic.normalize_author(None), "")

    def test_real_values_kept(self):
        self.assertEqual(logic.normalize_author("◆sujin"), "◆sujin")
        self.assertEqual(logic.normalize_author(" ◆pon "), "◆pon")
        self.assertEqual(logic.normalize_author("合集（多作者）"), "合集（多作者）")


class TestAuthorDisplay(unittest.TestCase):
    """临时目录（无 aa 文件 → 提取必为空），用手动值/覆盖表驱动优先级。"""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="author_test_")
        self.wd = os.path.join(self.tmp, "work")
        self.aa = os.path.join(self.tmp, "aa")
        os.makedirs(self.wd)
        os.makedirs(self.aa)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_manual_wins(self):
        a, auto = logic.author_display("w", {"author": "◆manual"}, self.wd, self.aa,
                                       {"w": "◆override"})
        self.assertEqual(a, "◆manual")
        self.assertFalse(auto)

    def test_override_used_when_meta_empty(self):
        a, auto = logic.author_display("w", {}, self.wd, self.aa, {"w": "◆?"})
        self.assertEqual(a, "◆?")
        self.assertTrue(auto)

    def test_dirty_diamond_self_heals(self):
        # 历史脏数据（保存事故写进去的 '◆'）读取时按未设处理 → 回落到提取/覆盖
        a, auto = logic.author_display("w", {"author": "◆"}, self.wd, self.aa, {"w": "◆X"})
        self.assertEqual(a, "◆X")
        self.assertTrue(auto)

    def test_all_empty(self):
        a, auto = logic.author_display("w", {}, self.wd, self.aa, None)
        self.assertEqual(a, "")
        self.assertFalse(auto)


class TestWiring(unittest.TestCase):
    """锁住「详情=列表」同口径 + 保存不再写 '◆'（静态接线检查）。"""

    def setUp(self):
        # 2026-10-14 main.py 拆成 web_state.py + routers/*，接线检查改扫**整个应用包**
        # （断言的是「接线存在」，与它落在哪个文件无关）
        app_dir = os.path.join(_REPO_ROOT, "aa_web_app")
        srcs = []
        for root, dirs, files in os.walk(app_dir):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", "static", "templates")]
            for fn in sorted(files):
                if fn.endswith(".py"):
                    srcs.append(open(os.path.join(root, fn), encoding="utf-8").read())
        self.main_src = "\n".join(srcs)
        self.idx_src = open(os.path.join(_REPO_ROOT, "aa_web_app", "templates", "index.html"),
                            encoding="utf-8").read()

    def test_main_uses_shared_helpers(self):
        self.assertIn('existing["author"] = logic.normalize_author(body.author)', self.main_src)   # PUT
        self.assertIn("logic.normalize_author(body.get(\"author\"))", self.main_src)              # 添加作品
        self.assertIn('"author_auto"', self.main_src)                                             # 详情返回提取值
        self.assertIn('"author": logic.author_display(', self.main_src)                            # 列表同口径

    def test_frontend_prefill_and_save(self):
        self.assertIn("(d.author || d.author_auto || '')", self.idx_src)     # 空则回落到提取值
        self.assertIn("author: _au ? '◆' + _au : ''", self.idx_src)          # 空输入送 ''，不再写 '◆'


if __name__ == "__main__":
    unittest.main()
