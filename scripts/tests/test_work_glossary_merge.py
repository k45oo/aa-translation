# -*- coding: utf-8 -*-
"""作品术语表增量保存（2026-09-19）。

背景（チンポ_メガテン 事故）：工作台作品页「保存作品术语表」旧语义是**整表覆盖**，
浏览器里没加载就点保存（或旧标签页拿着空状态保存）会用空表把 glossary_<work>.json
写空 —— 29 条人工术语瞬间清零，文件里只剩 known_names。

现在：PUT /api/glossary/<work> 默认 mode=merge（增量）——只 upsert 提交的条目 +
删除 deleted 里显式列出的 jp，表外既有条目原样保留；mode=replace 才整表覆盖
（且清空整表需 force=true）。

运行：.venv/Scripts/python.exe -m unittest scripts.tests.test_work_glossary_merge -v
零依赖（unittest + mock），不启动服务器。
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts", "common"))
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts", "works"))
sys.path.insert(0, os.path.join(_REPO_ROOT, "aa_web_app"))

from aa_web_app import logic                      # noqa: E402
from aa_web_app import main as M                  # noqa: E402


class MergeWorkGlossaryTest(unittest.TestCase):
    """logic.merge_work_glossary 纯逻辑。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wgmerge_")
        self.p = os.path.join(self.tmp, "glossary_w.json")
        logic.save_work_glossary("w", [
            {"jp": "ルイ", "zh": "路易", "cat": "角色", "来源": "manual"},
            {"jp": "アクア", "zh": "アクア", "cat": "角色", "来源": "auto",
             "role_cn": "阿库娅", "role_works": ["konosuba"]},
        ], ["ルイ"], path=self.p)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _load(self):
        return logic.load_work_glossary("w", path=self.p)

    def test_empty_payload_is_noop(self):
        """没加载就点保存（空表 + 空白名单）→ 一条都不能丢（事故回归）。"""
        ok, err, stat = logic.merge_work_glossary("w", [], [], path=self.p)
        self.assertTrue(ok, err)
        d = self._load()
        self.assertEqual([e["jp"] for e in d["glossary"]], ["ルイ", "アクア"])
        self.assertEqual(d["known_names"], ["ルイ"])
        self.assertEqual((stat["added"], stat["updated"], stat["removed"]), (0, 0, 0))
        self.assertEqual((stat["total"], stat["names_total"]), (2, 1))

    def test_add_and_update(self):
        """新 jp 追加；同 jp 只更新 zh/cat，不动其余条目。"""
        ok, err, stat = logic.merge_work_glossary(
            "w", [{"jp": "ルイ", "zh": "露易", "cat": "专名"},
                  {"jp": "アマテラス", "zh": "天照", "cat": "专名"}], [], path=self.p)
        self.assertTrue(ok, err)
        d = self._load()
        self.assertEqual([e["jp"] for e in d["glossary"]], ["ルイ", "アクア", "アマテラス"])
        self.assertEqual(d["glossary"][0]["zh"], "露易")
        self.assertEqual(d["glossary"][0]["cat"], "专名")
        self.assertEqual((stat["added"], stat["updated"], stat["kept"]), (1, 1, 0))
        # auto 条目与其 role_cn/role_works 原样保留（提交里没这一条）
        self.assertEqual(d["glossary"][1]["来源"], "auto")
        self.assertEqual(d["glossary"][1]["role_cn"], "阿库娅")

    def test_existing_role_meta_kept_when_blank(self):
        """表格不编辑 role_cn/role_works：提交里为空 → 沿用旧值（保留原文名提示）。"""
        ok, err, _ = logic.merge_work_glossary(
            "w", [{"jp": "アクア", "zh": "阿库娅", "cat": "角色", "role_cn": None}],
            [], path=self.p)
        self.assertTrue(ok, err)
        e = self._load()["glossary"][1]
        self.assertEqual(e["zh"], "阿库娅")
        self.assertEqual(e["role_cn"], "阿库娅")     # 旧值 role_cn 保留
        self.assertEqual(e["role_works"], ["konosuba"])

    def test_delete_requires_explicit_list(self):
        """删除只认 deleted 列表（点「删」登记），未提及的条目不删。"""
        ok, err, stat = logic.merge_work_glossary("w", [], [], ["ルイ"], path=self.p)
        self.assertTrue(ok, err)
        self.assertEqual(stat["removed"], 1)
        self.assertEqual([e["jp"] for e in self._load()["glossary"]], ["アクア"])
        # 删不存在的 jp 不计数、不报错
        ok, err, stat = logic.merge_work_glossary("w", [], [], ["__没有__"], path=self.p)
        self.assertTrue(ok, err)
        self.assertEqual(stat["removed"], 0)

    def test_known_names_union_and_delete(self):
        ok, err, stat = logic.merge_work_glossary(
            "w", [], ["キング", "  "], deleted_names=["ルイ"], path=self.p)
        self.assertTrue(ok, err)
        self.assertEqual(self._load()["known_names"], ["キング"])
        self.assertEqual((stat["names_added"], stat["names_removed"]), (1, 1))

    def test_rename_same_pair_twice_no_dup(self):
        ok, err, stat = logic.merge_work_glossary(
            "w", [["ルイ", "路易"], {"jp": "ルイ", "zh": "路易"}], [], path=self.p)
        self.assertTrue(ok, err)
        self.assertEqual([e["jp"] for e in self._load()["glossary"]], ["ルイ", "アクア"])
        self.assertEqual(stat["added"], 0)

    def test_save_work_glossary_still_whole_table(self):
        """整表覆盖语义保留（生成器/单条追加用）——3 参调用行为不变。"""
        ok, err = logic.save_work_glossary("w", [["白頭", "白头"]], [], path=self.p)
        self.assertTrue(ok, err)
        d = self._load()
        self.assertEqual([e["jp"] for e in d["glossary"]], ["白頭"])
        self.assertEqual(d["known_names"], [])


class GlossaryPutRouteTest(unittest.TestCase):
    """PUT /api/glossary/<work> 路由：默认增量 + replace 需 force。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wgput_")
        self.p = os.path.join(self.tmp, "glossary_w.json")
        logic.save_work_glossary("w", [
            {"jp": "ルイ", "zh": "路易", "cat": "角色", "来源": "manual"},
            {"jp": "メシアン", "zh": "弥赛亚", "cat": "专名", "来源": "manual"},
        ], ["ルイ"], path=self.p)
        self._patches = [
            mock.patch.object(M, "cfg_of", return_value={"work_dir": self.tmp}),
            mock.patch.object(M.logic, "work_glossary_path", return_value=self.p),
            mock.patch.object(M, "invalidate_stats", return_value=None),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        import shutil
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _load(self):
        return json.load(open(self.p, encoding="utf-8"))

    def test_default_mode_is_merge_and_empty_body_keeps_file(self):
        """事故场景：空 body（没加载就点保存）→ 文件一条不丢。"""
        r = M.api_work_glossary_put("w", M.WorkGlossary())
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["mode"], "merge")
        d = self._load()
        self.assertEqual([e["jp"] for e in d["glossary"]], ["ルイ", "メシアン"])
        self.assertEqual(d["known_names"], ["ルイ"])
        self.assertEqual((r["added"], r["removed"], r["total"]), (0, 0, 2))

    def test_merge_adds_and_deletes(self):
        r = M.api_work_glossary_put("w", M.WorkGlossary(
            glossary=[{"jp": "ルイ", "zh": "路易", "cat": "角色"},
                      {"jp": "ヤタガラス", "zh": "八咫乌", "cat": "专名"}],
            deleted=["メシアン"]))
        self.assertTrue(r["ok"], r)
        d = self._load()
        self.assertEqual([e["jp"] for e in d["glossary"]], ["ルイ", "ヤタガラス"])
        self.assertEqual((r["added"], r["kept"], r["removed"]), (1, 1, 1))

    def test_replace_empty_needs_force(self):
        with self.assertRaises(M.HTTPException) as cm:
            M.api_work_glossary_put("w", M.WorkGlossary(mode="replace"))
        self.assertEqual(cm.exception.status_code, 422)
        self.assertEqual(len(self._load()["glossary"]), 2)      # 未被清空
        r = M.api_work_glossary_put("w", M.WorkGlossary(mode="replace", force=True))
        self.assertTrue(r["ok"], r)
        d = self._load()
        self.assertEqual(d["glossary"], [])
        self.assertEqual(d["known_names"], [])

    def test_replace_with_content_no_force(self):
        r = M.api_work_glossary_put("w", M.WorkGlossary(
            mode="replace", glossary=[["ルイ", "路易"]]))
        self.assertTrue(r["ok"], r)
        self.assertEqual([e["jp"] for e in self._load()["glossary"]], ["ルイ"])


if __name__ == "__main__":
    unittest.main()
