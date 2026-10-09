# -*- coding: utf-8 -*-
"""P3 quality_gate registry id 映射测试（2026-08-18 Phase 5）。

覆盖：`python quality_gate.py --work kikkoman` 这类 registry id 输入——
当仓库根下没有同名目录时，映射到 registry 条目的 out_dir 的 dirname（作品根）。
验证映射逻辑（不依赖真实作品数据）：mock 临时 registry + 临时作品目录，
跑 main() 断言它正确找到 aa/ 汉化/ 并产出报告。

零 API。写操作在临时目录。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS_DIR)
sys.path.insert(0, os.path.join(SCRIPTS, "verify"))
sys.path.insert(0, os.path.join(SCRIPTS, "works"))

import quality_gate as Q


class TestRegistryIdMapping(unittest.TestCase):
    """--work <registry id> 映射到 registry out_dir 的 dirname。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hermes-test-qg-")
        # 临时作品目录：<tmp>/キッコーマン/{aa,汉化}
        self.work = os.path.join(self.tmp, "キッコーマン")
        self.aa = os.path.join(self.work, "aa")
        self.han = os.path.join(self.work, "汉化")
        for d in (self.aa, self.han):
            os.makedirs(d)
        # 一话原文 + 汉化（宽度一致、无残留）
        with open(os.path.join(self.aa, "entry_1.txt"), "w", encoding="utf-8") as f:
            f.write("# 标题\n# 原帖地址: https://example.com/blog-entry-1.html\nこんにちは世界\n　　　｜　絵　｜\n")
        with open(os.path.join(self.han, "entry_1_汉化.txt"), "w", encoding="utf-8") as f:
            f.write("# 标题\n# 原帖地址: https://example.com/blog-entry-1.html\n你好呀你好呀\u3000\n　　　｜　絵　｜\n")

        # 临时 registry 条目：kikkoman 指向临时作品
        self.orig_registry = Q.sys.modules.get("registry")
        sys.modules["registry"] = types_module()
        sys.modules["registry"].get = lambda work: {
            "out_dir": self.han,
            "aa_dir": self.aa,
        }
        # 临时 registry 模块还需要 WORKS（quality_gate 可能访问）
        sys.modules["registry"].WORKS = {"kikkoman": {"out_dir": self.han, "aa_dir": self.aa}}
        sys.modules["registry"].BASE = self.tmp

    def tearDown(self):
        sys.modules["registry"] = self.orig_registry
        # 清理 main() 里 Q.ROOT 改动导致 insert 的假 works 路径
        try:
            sys.path.remove(os.path.join(self.tmp, "scripts", "works"))
        except ValueError:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_registry_id_maps_to_workdir(self):
        # 模拟: 仓库根无 <id> 目录（os.path.isdir 用真实 ROOT，id 不是真实目录）
        # 跑 main() 验证映射后能处理临时作品
        orig_argv = sys.argv
        orig_root = Q.ROOT
        Q.ROOT = self.tmp   # 让 work_dir = <tmp>/kikkoman 不存在 → 触发 registry 映射
        try:
            report = os.path.join(self.tmp, "report.txt")
            sys.argv = ["quality_gate.py", "--work", "kikkoman", "--report", report]
            rc = Q.main()
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(report))
            with open(report, encoding="utf-8") as f:
                content = f.read()
            # 报告应包含通过/无错位（中文报告）
            self.assertIn("宽度错位行: 0", content)
            self.assertIn("失败: 0", content)
        finally:
            Q.ROOT = orig_root
            sys.argv = orig_argv

    def test_registry_mapping_resolves_to_aa_han(self):
        # 直接验证映射逻辑: out_dir 的 dirname == 作品目录
        cfg = {"out_dir": self.han}
        resolved = os.path.dirname(cfg["out_dir"])
        self.assertEqual(resolved, self.work)
        self.assertTrue(os.path.isdir(os.path.join(resolved, "aa")))
        self.assertTrue(os.path.isdir(os.path.join(resolved, "汉化")))


def types_module():
    """构造一个最小 registry 伪模块（支持属性访问）。"""
    import types
    return types.ModuleType("registry")


if __name__ == "__main__":
    unittest.main()
