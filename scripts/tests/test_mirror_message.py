# -*- coding: utf-8 -*-
"""镜像提交说明（scripts/tools/push_code_mirror.py::mirror_message）用例。

测的是「说明的构成与可复现性」——远端无关（同内容同日期同 sha 的交叉核对依赖这一点）。
不联网、不 push：只构造一个假快照目录喂给纯函数。
"""
import os
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS_DIR)
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.join(SCRIPTS, "tools"))

import push_code_mirror as P      # noqa: E402


def _fake_snapshot(root):
    """造一棵带结构性计数的假快照（sites/tests/skills/wiki + 单文件）。"""
    def touch(rel):
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8").close()

    touch("scripts/sites/_base.py")
    touch("scripts/sites/__init__.py")
    for i in range(3):
        touch(f"scripts/sites/site{i}.py")
    for i in range(4):
        touch(f"scripts/tests/test_x{i}.py")
    for i in range(2):
        touch(f".codex/skills/sk{i}/SKILL.md")
    touch("doc/aa作品相关/fooAwiki/index.html")
    touch("doc/aa作品相关/fooBwiki/index.html")
    touch("glossary.json")
    touch("roles.json")
    touch("LICENSE")
    return {
        "scripts": 8, ".codex": 2, "doc": 2, "glossary.json": 1, "roles.json": 1,
        "LICENSE": 1, "README.md(生成)": 1, "doc/接口文档.md": 1,
    }


class TestMirrorMessage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aa_mirror_msg_")
        self.per = _fake_snapshot(self.tmp)

    def _msg(self, subjects=("条目一", "条目二")):
        return P.mirror_message(self.tmp, "2026-10-10", 16, 16 * 1048576, self.per, subjects=subjects)

    def test_has_date_count_and_digest(self):
        m = self._msg()
        self.assertTrue(m.startswith("AA翻译管线代码与文档快照（2026-10-10）"))
        self.assertIn("内容：16 文件 / 16.0 MB", m)
        self.assertIn("本仓库是**全新历史的单提交快照**", m)
        # 目录注记里的计数是**扫描快照**得到（排掉 _base.py / __init__.py）
        self.assertIn("sites/ 3 个站点解析器", m)
        self.assertIn("tests/ 4 个测试文件", m)
        self.assertIn("2 个技能", m)
        self.assertIn("wiki 素材（2 部）", m)

    def test_singles_and_subjects(self):
        m = self._msg(subjects=("改 A", "改 B"))
        # 合成键 README.md(生成) 显示为 README.md；单文件平铺一行
        self.assertIn("另含单文件：", m)
        self.assertIn("README.md", m)
        self.assertNotIn("(生成)", m)
        # 深路径的顶层文件（doc/接口文档.md）不进目录行，走单文件行
        self.assertIn("doc/接口文档.md", m)
        self.assertNotIn("  doc/接口文档.md ", m)
        self.assertIn("快照内的近期改动（工作库最近 2 条提交", m)
        self.assertIn("  - 改 A", m)

    def test_no_remote_information(self):
        """禁止出现远端相关字样：否则两处镜像会因 push 顺序得到不同 sha。"""
        m = self._msg()
        for bad in ("github.com", "aa-translation", "force-push 到", "上次快照", "落后"):
            self.assertNotIn(bad, m, bad)
        self.assertIn("force-push 覆盖", m)      # 说明性质可以提，但不含远端身份

    def test_deterministic_for_same_input(self):
        self.assertEqual(self._msg(), self._msg())

    def test_subjects_empty_ok(self):
        m = self._msg(subjects=())
        self.assertNotIn("快照内的近期改动", m)
        self.assertIn("上手见 `README.md`", m)


if __name__ == "__main__":
    unittest.main()
