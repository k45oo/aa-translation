# -*- coding: utf-8 -*-
"""P6 接口文档同步测试: 重跑生成器与 doc/接口文档.md 比对，不一致即失败。

用法: python -m unittest test_interface_docs（scripts/tests 下）
"""
import os
import subprocess
import sys
import unittest
from _work_guard import repo_only  # noqa: E402 （发行包内跳过生成者专属用例）

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GEN = os.path.join(ROOT, "scripts", "tools", "gen_interface_docs.py")
OUT = os.path.join(ROOT, "doc", "接口文档.md")
PY = sys.executable


class TestInterfaceDocs(unittest.TestCase):
    @repo_only("按完整仓库树重算生成物，发行包剔除过 legacy/logs/benchmark 等目录")
    def test_docs_up_to_date(self):
        """生成器输出与 doc/接口文档.md 一致（改 CLI/env 未重跑即失败）"""
        self.assertTrue(os.path.exists(GEN), f"生成器缺失: {GEN}")
        self.assertTrue(os.path.exists(OUT), f"接口文档缺失: {OUT}")
        r = subprocess.run([PY, GEN, "--check"], capture_output=True, text=True,
                           cwd=ROOT, encoding="utf-8")
        self.assertEqual(
            r.returncode, 0,
            f"接口文档过期或生成器报错:\nstdout={r.stdout}\nstderr={r.stderr}")
        self.assertIn("OK", r.stdout)

    @repo_only("按完整仓库树重算清单，发行包目录集不同")
    def test_inventory_fresh(self):
        """inventory 与文件树一致（新增/删除脚本未重跑 gen_inventory 即失败）"""
        inv_path = os.path.join(ROOT, "scripts", "_inventory.json")
        self.assertTrue(os.path.exists(inv_path), "缺 _inventory.json")
        import json
        inv = json.load(open(inv_path, encoding="utf-8"))
        inv_files = set(inv["files"].keys())
        disk = set()
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, "scripts")):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fn in filenames:
                if fn.endswith(".py"):
                    rp = os.path.relpath(os.path.join(dirpath, fn), ROOT).replace("\\", "/")
                    disk.add(rp)
        missing = disk - inv_files
        stale = inv_files - disk
        self.assertEqual(missing, set(), f"inventory 缺文件（重跑 gen_inventory.py）: {missing}")
        self.assertEqual(stale, set(), f"inventory 过期文件（重跑 gen_inventory.py）: {stale}")


if __name__ == "__main__":
    unittest.main()
