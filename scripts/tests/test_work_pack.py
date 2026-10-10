# -*- coding: utf-8 -*-
"""作品导入导出（aa_web_app.work_pack）：白名单口径 / manifest / registry 源码 / rar 往返。

运行：.venv\\Scripts\\python.exe -m unittest discover -s scripts/tests -v
rar 往返用例需要 WinRAR 的 Rar.exe（找不到就 skip）；其余用例零依赖。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
sys.path.insert(0, _REPO_ROOT)

from aa_web_app import work_pack as wp  # noqa: E402  (会补 scripts 各层 sys.path)
import registry  # noqa: E402

REGISTRY_STUB = '''# -*- coding: utf-8 -*-
BASE = %r
WORKS = {
    "other": dict(
        aa_dir=BASE + r"\\aaManga\\other\\aa",
    ),
}


def get(work):
    return WORKS.get(work)
'''


def _mk(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class PackFixture(unittest.TestCase):
    WORK = "テスト作品"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aapack_src_")
        self.dsts = []
        self._old_base = registry.BASE
        self._old_meta = wp.WORK_META_FILE
        self._old_reg_path = wp.registry_path
        registry.BASE = self.tmp
        self.cfg = {
            "title": "测试作品（汉化名）", "site": "yaruobook", "name_style": "entry_eid",
            "r18": True, "url_pattern": "https://example.com/?p={eid}", "min_size": 1000,
            "html_out": os.path.join(self.tmp, "aaManga", "交付HTML", "%s_汉化合集.html" % self.WORK),
            "aa_dir": os.path.join(self.tmp, "aaManga", self.WORK, "aa"),
            "out_dir": os.path.join(self.tmp, "aaManga", self.WORK, "汉化"),
            "work_dir": os.path.join(self.tmp, "aaManga", self.WORK, "work"),
            "raw_dir": os.path.join(self.tmp, "aaManga", self.WORK, "raw"),
        }
        self.make_repo(self.tmp)
        wp.WORK_META_FILE = os.path.join(self.tmp, "data", "work_meta.json")
        _mk(wp.WORK_META_FILE, json.dumps({self.WORK: {"cn_name": "测试作品", "author": "◆abc",
                                                       "r18_level": "high"}}, ensure_ascii=False))

    def tearDown(self):
        registry.BASE = self._old_base
        wp.WORK_META_FILE = self._old_meta
        wp.registry_path = self._old_reg_path
        shutil.rmtree(self.tmp, ignore_errors=True)
        for d in getattr(self, "dsts", []):
            shutil.rmtree(d, ignore_errors=True)

    def make_repo(self, root):
        w = os.path.join(root, "aaManga", self.WORK)
        _mk(os.path.join(w, "aa", "entry_1.txt"), "aaa")
        _mk(os.path.join(w, "aa", "entry_1_colormap.json"), "{}")
        _mk(os.path.join(w, "汉化", "entry_1_汉化.txt"), "汉化")
        _mk(os.path.join(w, "汉化", "entry_1_汉化.txt.bak"), "旧备份")           # 备份 → 排除
        _mk(os.path.join(w, "raw", "entry_1.html"), "<html>")
        # work：人工资产（白名单）
        _mk(os.path.join(w, "work", "glossary_%s.json" % self.WORK), "{}")
        _mk(os.path.join(w, "work", "%s_knowledge.json" % self.WORK), "{}")
        _mk(os.path.join(w, "work", "chapter_order.json"), "[]")
        _mk(os.path.join(w, "work", "glossary_iter.json"), "{}")
        _mk(os.path.join(w, "work", "gold_dialogue", "entry_1.json"), "{}")
        # work：过程 / 记录 / 备份（排除）
        _mk(os.path.join(w, "work", "entry_1.txt.ckpt.json"), "{}")
        _mk(os.path.join(w, "work", "edits.jsonl"), "{}")
        _mk(os.path.join(w, "work", "runner_log.txt"), "log")
        _mk(os.path.join(w, "work", "tm_%s.json" % self.WORK), "{}")
        _mk(os.path.join(w, "work", "stats_cache.json"), "{}")
        _mk(os.path.join(w, "work", "backup_refine_aa_20261001", "entry_1.txt"), "old")
        _mk(os.path.join(w, "work", "prep", "entry_1.json"), "{}")
        # 交付合集 + registry stub
        _mk(os.path.join(root, "aaManga", "交付HTML", "%s_汉化合集.html" % self.WORK), "<html>")
        _mk(os.path.join(root, "scripts", "works", "registry.py"), REGISTRY_STUB % root)


class TestWhitelist(PackFixture):
    def test_asset_and_excluded_names(self):
        for ok in ("chapter_order.json", "glossary_x.json", "x_knowledge.json",
                   "pokemon_fixed_x.json", "manual_fix.txt"):
            self.assertTrue(wp.is_work_asset(ok), ok)
        for no in ("entry_1.txt.ckpt.json", "edits.jsonl", "runner_log.txt", "tm_x.json",
                   "stats_cache.json", "prep", "refine_ckpt", "sfx_marks", "review_inline.txt",
                   "gold_dialogue", "line_meta_overrides"):
            self.assertFalse(wp.is_work_asset(no), no)      # 资产目录单独按 ASSET_DIRS 判定
        self.assertEqual(wp.ASSET_DIRS, ("gold_dialogue", "line_meta_overrides"))
        for ex in ("a.bak", "a.txt.bak_repair", "backup_x", "a.tmp"):
            self.assertTrue(wp.excluded_name(ex), ex)

    def test_plan_keeps_assets_drops_process(self):
        p = wp.plan_export(self.cfg, self.WORK)
        names = [os.path.basename(i.rstrip("/")) for i in p["items"]]
        self.assertIn("glossary_%s.json" % self.WORK, names)
        self.assertIn("gold_dialogue", names)
        self.assertIn("chapter_order.json", names)
        for bad in ("entry_1.txt.ckpt.json", "edits.jsonl", "runner_log.txt",
                    "tm_%s.json" % self.WORK, "stats_cache.json", "backup_refine_aa_20261001",
                    "prep", "entry_1_汉化.txt.bak"):
            self.assertNotIn(bad, names, bad)
        # 过程文件都在 skipped 里（供 UI 显示排除项）
        self.assertIn("edits.jsonl", p["work_skipped_names"])
        self.assertIn("entry_1.txt.ckpt.json", p["work_skipped_names"])
        self.assertIn("raw", p["dirs_rel"])   # raw 目录存在但未被包含

    def test_plan_counts_only_included_files(self):
        p = wp.plan_export(self.cfg, self.WORK)
        # aa2 + 汉化1 + 资产4 + gold_dialogue/1 + aaManga/交付HTML 1 = 9（bak/过程/备份/raw 都不计）
        self.assertEqual(p["files"], 9)
        self.assertEqual(p["raw_files"], 1)
        q = wp.plan_export(self.cfg, self.WORK, include_raw=True)
        self.assertEqual(q["files"], 10)
        self.assertGreater(q["bytes"], p["bytes"])

    def test_manifest_carries_registry_and_meta(self):
        p = wp.plan_export(self.cfg, self.WORK)
        m = wp.build_manifest(self.cfg, self.WORK, p)
        self.assertEqual(m["format"], wp.FORMAT)
        self.assertEqual(m["work"], self.WORK)
        self.assertEqual(m["root_rel"], os.path.join("aaManga", self.WORK))
        self.assertEqual(m["dirs"]["out"], "汉化")
        self.assertNotIn("aa_dir", m["registry"])          # 路径不写进 manifest（导入端按 BASE 重拼）
        self.assertEqual(m["registry"]["name_style"], "entry_eid")
        self.assertEqual(m["work_meta"]["cn_name"], "测试作品")


class TestRegistrySource(PackFixture):
    def test_entry_source_uses_base_relative_paths(self):
        entry = {"site": "x", "aa_dir": os.path.join(self.tmp, "aaManga", self.WORK, "aa"),
                 "out_dir": os.path.join(self.tmp, "aaManga", self.WORK, "汉化"),
                 "r18": True, "min_size": 1000,
                 "generator": ("works/postprocess.py", []),
                 "url_pattern": "https://e/{eid}"}
        src = wp.registry_entry_source(self.WORK, entry, self.tmp)
        self.assertIn('aa_dir=BASE + r"\\aaManga\\%s\\aa"' % self.WORK, src)
        self.assertIn("r18=True", src)
        self.assertIn("min_size=1000", src)
        self.assertIn('generator=("works/postprocess.py", ())', src)
        # 生成的是可执行 Python（BASE 由 registry.py 自身提供）
        ns = {"BASE": self.tmp}
        exec("WORKS = {\n" + src + "\n}", ns)
        self.assertIn(self.WORK, ns["WORKS"])
        self.assertTrue(ns["WORKS"][self.WORK]["aa_dir"].startswith(self.tmp))

    def test_append_source_into_registry_file(self):
        rp = os.path.join(self.tmp, "scripts", "works", "registry.py")
        old = wp.registry_path
        wp.registry_path = lambda: rp
        try:
            wp._append_registry_source(wp.registry_entry_source(
                self.WORK, {"site": "x", "aa_dir": os.path.join(self.tmp, "aaManga", self.WORK, "aa")}, self.tmp))
        finally:
            wp.registry_path = old
        ns = {}
        exec(open(rp, encoding="utf-8").read(), ns)
        self.assertIn(self.WORK, ns["WORKS"])
        self.assertIn("other", ns["WORKS"])         # 原有条目没被破坏
        self.assertEqual(ns["WORKS"][self.WORK]["aa_dir"], os.path.join(self.tmp, "aaManga", self.WORK, "aa"))


@unittest.skipUnless(wp.find_rar(), "未找到 Rar.exe（WinRAR），跳过 rar 往返用例")
class TestRarRoundtrip(PackFixture):
    def _export(self, include_raw=False):
        out = os.path.join(self.tmp, "导出")
        return wp.run_export(self.cfg, self.WORK, include_raw=include_raw, out_dir=out)

    def _new_dst(self, prefix):
        """目标仓库空壳（registry stub + aaManga/ + aaManga/交付HTML/），并把 BASE/registry 指过去。"""
        dst = tempfile.mkdtemp(prefix=prefix)
        self.dsts.append(dst)
        _mk(os.path.join(dst, "scripts", "works", "registry.py"), REGISTRY_STUB % dst)
        os.makedirs(os.path.join(dst, "aaManga"), exist_ok=True)
        os.makedirs(os.path.join(dst, "aaManga", "交付HTML"), exist_ok=True)
        registry.BASE = dst
        wp.WORK_META_FILE = os.path.join(dst, "data", "work_meta.json")
        rp = os.path.join(dst, "scripts", "works", "registry.py")
        self._old_reg_path = wp.registry_path
        wp.registry_path = lambda: rp
        return dst

    def test_export_peek_and_install(self):
        res = self._export()
        self.assertTrue(os.path.exists(res["path"]))
        self.assertGreater(res["size"], 0)
        m = wp.peek_manifest(res["path"])
        self.assertEqual(m["work"], self.WORK)
        self.assertFalse(m["include_raw"])
        dst = self._new_dst("aapack_dst_")
        prev = wp.inspect_import(m, res["path"])
        self.assertEqual(prev["conflicts"], [])
        r = wp.install(res["path"], options={"registry": True, "work_meta": True})
        self.assertTrue(r["ok"])
        w = os.path.join(dst, "aaManga", self.WORK)
        self.assertTrue(os.path.exists(os.path.join(w, "aa", "entry_1.txt")))
        self.assertTrue(os.path.exists(os.path.join(w, "汉化", "entry_1_汉化.txt")))
        self.assertTrue(os.path.exists(os.path.join(w, "work", "glossary_%s.json" % self.WORK)))
        self.assertTrue(os.path.exists(os.path.join(w, "work", "gold_dialogue", "entry_1.json")))
        # 过程/记录/备份没被带过来；raw 默认不带
        self.assertFalse(os.path.exists(os.path.join(w, "work", "edits.jsonl")))
        self.assertFalse(os.path.exists(os.path.join(w, "work", "entry_1.txt.ckpt.json")))
        self.assertFalse(os.path.exists(os.path.join(w, "work", "backup_refine_aa_20261001")))
        self.assertFalse(os.path.exists(os.path.join(w, "raw")))
        self.assertFalse(os.path.exists(os.path.join(w, "汉化", "entry_1_汉化.txt.bak")))
        self.assertTrue(os.path.exists(os.path.join(dst, "aaManga", "交付HTML", "%s_汉化合集.html" % self.WORK)))
        # 包内 manifest 不落地到仓库
        self.assertFalse(os.path.exists(os.path.join(dst, wp.MANIFEST_NAME)))
        # registry 已追加（BASE 相对写法）+ work_meta 已恢复
        rp = os.path.join(dst, "scripts", "works", "registry.py")
        src = open(rp, encoding="utf-8").read()
        self.assertIn('aa_dir=BASE + r"\\aaManga\\%s\\aa"' % self.WORK, src)
        ns = {}
        exec(src, ns)
        self.assertIn(self.WORK, ns["WORKS"])
        self.assertEqual(ns["WORKS"][self.WORK]["aa_dir"], os.path.join(w, "aa"))
        self.assertEqual(ns["WORKS"][self.WORK]["html_out"],
                         os.path.join(dst, "aaManga", "交付HTML", "%s_汉化合集.html" % self.WORK))
        meta = json.load(open(wp.WORK_META_FILE, encoding="utf-8"))
        self.assertEqual(meta[self.WORK]["cn_name"], "测试作品")
        # 目标仓库里已存在同名目录 → 冲突拒绝（不覆盖）
        with self.assertRaises(wp.PackError):
            wp.install(res["path"], options={"registry": True, "work_meta": True})

    def test_export_with_raw(self):
        res = self._export(include_raw=True)
        m = wp.peek_manifest(res["path"])
        self.assertTrue(m["include_raw"])
        dst = self._new_dst("aapack_dst2_")
        wp.install(res["path"], options={"registry": True, "work_meta": True})
        self.assertTrue(os.path.exists(os.path.join(dst, "aaManga", self.WORK, "raw", "entry_1.html")))

    def test_peek_rejects_foreign_archive(self):
        bogus = os.path.join(self.tmp, "bogus.rar")
        _mk(os.path.join(self.tmp, "junk", "a.txt"), "hello")
        rar = wp.find_rar()
        wp._run_rar(rar, ["a", "-m1", "-idq", "-y", bogus, "junk"], cwd=self.tmp)
        with self.assertRaises(wp.PackError):
            wp.peek_manifest(bogus)


if __name__ == "__main__":
    unittest.main()
