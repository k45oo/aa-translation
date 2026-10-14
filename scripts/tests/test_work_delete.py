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

from aa_web_app import web_state as WS  # noqa: E402  常量定义处（_PROGRESS_FILE 等）


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
    """被测路由（delete/_registry_path/_remove_registry_entry 等）2026-10-14 拆分后归属
    `aa_web_app/routers/works.py`；本用例会**重绑模块属性**（_DATA/_WORK_META_FILE/…），
    必须绑在**真正读取它们的模块**上，绑到 aa_web_app.main 只是改了兼容层副本、不生效。"""
    from aa_web_app.routers import works as M
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


def _make_junction(link, target):
    """建目录 junction（Windows `mklink /J`，不需要管理员）。失败抛 OSError。"""
    import subprocess
    r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0 or not os.path.isdir(link):
        raise OSError((r.stdout or b"").decode("mbcs", "replace"))


class TestDeleteBoundaryRoots(unittest.TestCase):
    """删除边界白名单必须容忍 `aaManga/` 是 junction（数据目录指到别的盘）。

    事故（2026-10-12 实测，ポケの子（？）(FC2)）：本机 `<repo>\\aaManga` 是指到
    `E:\\aaManga` 的 junction → 作品目录 realpath 落在仓库根之外，而旧检查只许 realpath 在
    `registry.BASE` 内 → **每一次删除**都 400「删除路径越界」（107 部作品全中，不只这一部）。
    """

    def setUp(self):
        self.M = _main()
        import registry
        self.reg = registry
        self._old = (registry.BASE, self.M._DATA)
        self.addCleanup(self._restore)

    def _restore(self):
        self.reg.BASE, self.M._DATA = self._old

    def test_aaManga是指到别处的junction时作品目录仍放行(self):
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, "repo")
            data = os.path.join(d, "data")
            os.makedirs(repo)
            os.makedirs(os.path.join(data, "作品", "aa"))
            link = os.path.join(repo, "aaManga")
            try:
                _make_junction(link, data)
            except OSError as e:
                self.skipTest("无法建 junction（非 Windows / 无权限）: %s" % e)
            self.reg.BASE = repo
            self.M._DATA = repo
            roots = self.M._delete_roots()
            self.assertTrue(self.M._in_delete_roots(os.path.join(link, "作品", "aa"), roots),
                            "junction 下的作品目录（realpath 在仓库外）必须放行")
            self.assertTrue(self.M._in_delete_roots(os.path.join(link, "交付HTML", "x"), roots))

    def test_白名单之外的路径仍然拒绝(self):
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, "repo")
            os.makedirs(repo)
            self.reg.BASE = repo
            self.M._DATA = repo
            roots = self.M._delete_roots()
            # 仓库根之外的任意路径（包括与仓库同级、以及系统目录）一律拒绝
            self.assertFalse(self.M._in_delete_roots(os.path.join(d, "别处", "x"), roots))
            self.assertFalse(self.M._in_delete_roots(r"C:\Windows", roots))
            self.assertFalse(self.M._in_delete_roots(os.path.dirname(repo), roots))

    def test_真实注册表每部作品的删除面都在白名单内(self):
        """回归判据：真仓库上任何一部作品的删除面不得再被判越界。

        注意 realpath 很贵（本机 junction 上一次 ≈0.2ms）：真删路径（带 all_work_dirs）只对
        几部样品跑，其余用独占根口径——否则 107 部 × 428 目录 = 4.6 万次 realpath ≈ 40s。
        """
        import registry as reg
        from aa_web_app import logic
        roots = self.M._delete_roots()
        all_dirs = self.M._all_work_dirs()
        sample = [w for w in ("pokeko_fc2", "ポケの子", "saki_fc2", "baitou") if w in reg.WORKS]
        bad = []
        for wid, cfg in reg.WORKS.items():
            surf = logic.delete_surface(cfg, all_dirs if wid in sample else None)
            for _k, p in list(surf['items']) + [('html', x) for x in self.M._deliver_artifacts(cfg)]:
                if not self.M._in_delete_roots(p, roots):
                    bad.append((wid, p))
        self.assertEqual(bad, [], "删除面被判越界的作品: %s" % bad[:10])
        self.assertTrue(sample, "样品作品名变了？")


class TestDeleteRouteWithJunctionData(unittest.TestCase):
    """端到端（直接调路由函数，不经 HTTP）：aaManga 是 junction 时「删除作品」要真的跑完。

    旧实现下这一步抛 400「删除路径越界」，盘上一个文件都不动（用户看到的就是「删除失败」）。
    """

    def setUp(self):
        self.M = _main()
        import registry as reg
        self.reg = reg
        self._old = (reg.BASE, getattr(reg, "__file__", None),
                     self.M._DATA, self.M._WORK_META_FILE, WS._PROGRESS_FILE)

    def tearDown(self):
        base, rfile, data, meta, prog = self._old
        self.reg.BASE = base
        if rfile:
            self.reg.__file__ = rfile
        self.M._DATA = data
        self.M._WORK_META_FILE = meta
        WS._PROGRESS_FILE = prog

    def test_junction数据目录下删除跑通(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, "repo")
            data = os.path.join(d, "data")
            os.makedirs(os.path.join(repo, "scripts", "works"))
            wd = os.path.join(data, "作品X")
            for sub in ("aa", "汉化", "work", "raw"):
                os.makedirs(os.path.join(wd, sub))
            with open(os.path.join(wd, "aa", "a.txt"), "w", encoding="utf-8") as f:
                f.write("# 标题\n正文\n")
            link = os.path.join(repo, "aaManga")
            try:
                _make_junction(link, data)
            except OSError as e:
                self.skipTest("无法建 junction（非 Windows / 无权限）: %s" % e)
            # 自己的 registry.py（删除要改写它），条目格式与真表一致
            regfile = os.path.join(repo, "scripts", "works", "registry.py")
            with open(regfile, "w", encoding="utf-8", newline="") as f:
                f.write('WORKS = {\n'
                        '    "作品X": dict(\n'
                        '        title="作品X",\n'
                        '        aa_dir=BASE + r"\\aaManga\\作品X\\aa",\n'
                        '        out_dir=BASE + r"\\aaManga\\作品X\\汉化",\n'
                        '        work_dir=BASE + r"\\aaManga\\作品X\\work",\n'
                        '        raw_dir=BASE + r"\\aaManga\\作品X\\raw",\n'
                        '    ),\n'
                        '}\n')
            meta_f = os.path.join(d, "work_meta.json")
            with open(meta_f, "w", encoding="utf-8") as f:
                json.dump({"作品X": {"author": "a"}}, f)
            self.reg.BASE = repo
            self.reg.__file__ = regfile
            self.reg.WORKS["作品X"] = {
                "title": "作品X",
                **{k: os.path.join(link, "作品X", s)
                   for k, s in (("aa_dir", "aa"), ("out_dir", "汉化"),
                                ("work_dir", "work"), ("raw_dir", "raw"))},
            }
            self.M._DATA = repo
            self.M._WORK_META_FILE = meta_f
            WS._PROGRESS_FILE = os.path.join(d, "progress.json")
            r = self.M.api_delete("作品X", {"confirm": True})      # 旧实现这里 400
            self.assertTrue(r.get("ok"))
            self.assertFalse(os.path.exists(wd), "作品目录（junction 实际指向处）应被删掉")
            txt = open(regfile, encoding="utf-8").read()
            self.assertNotIn('"作品X"', txt, "registry 条目应被摘掉")
            self.assertNotIn("作品X", json.load(open(meta_f, encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
