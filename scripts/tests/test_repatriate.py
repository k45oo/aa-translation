# -*- coding: utf-8 -*-
"""术语表反哺（作品表 → 全局）+ 候选池差集修复 测试（2026-09-20）。

对应方案: doc/方案归档/术语表反哺与候选池差集修复方案_2026-09-20.md
运行: .venv/Scripts/python.exe -m unittest scripts.tests.test_repatriate -v
零依赖（unittest + 临时目录 + mock），不碰仓库真实 roles.json/glossary.json。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
for _p in (_REPO_ROOT, os.path.join(_REPO_ROOT, "scripts"),
           os.path.join(_REPO_ROOT, "scripts", "common"),
           os.path.join(_REPO_ROOT, "scripts", "works"),
           os.path.join(_REPO_ROOT, "scripts", "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import glossary_repatriate as GR          # noqa: E402
import build_term_pool as BTP             # noqa: E402
import gen_glossary_ai as GA              # noqa: E402
import registry                           # noqa: E402


from aa_web_app import web_state as WS    # noqa: E402  共享底座（cfg_of/paths_of/invalidate_stats 等）


def _dump(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


class _Base(unittest.TestCase):
    """把模块级路径常量整体指向临时目录（绝不碰真实资产）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="repat_test_")
        self.manga = os.path.join(self.tmp, "aaManga")
        self.gl = os.path.join(self.tmp, "glossary.json")
        self.rl = os.path.join(self.tmp, "roles.json")
        self.bk = os.path.join(self.tmp, ".glossary_backup")
        self.patches = [
            mock.patch.object(GR, "BASE", self.tmp, create=False),
            mock.patch.object(GR, "GLOSSARY_JSON", self.gl, create=False),
            mock.patch.object(GR, "ROLES_JSON", self.rl, create=False),
            mock.patch.object(GR, "BACKUP_ROOT", self.bk, create=False),
            mock.patch.object(GR, "AA_MANGA", self.manga, create=False),
        ]
        for p in self.patches:
            p.start()
        GR._KNOWN_CACHE.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        GR._KNOWN_CACHE.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- 造数据 ----
    def _global(self, pairs=(), known=(), roles=()):
        _dump(self.gl, {"schema": "g", "glossary": list(pairs), "known_names": list(known),
                        "角色": []})
        _dump(self.rl, {"schema": "r", "角色": list(roles)})

    def _work(self, wid, pairs, known=()):
        wd = os.path.join(self.manga, wid, "work")
        os.makedirs(wd, exist_ok=True)
        _dump(os.path.join(wd, f"glossary_{wid}.json"),
              {"schema": "wg", "glossary": list(pairs), "known_names": list(known)})
        return wd

    def _cfg(self, wid, aa_dir=None):
        return {"work_dir": os.path.join(self.manga, wid, "work"),
                "aa_dir": aa_dir or os.path.join(self.manga, wid, "aa"),
                "out_dir": os.path.join(self.manga, wid, "汉化")}


class ScanTest(_Base):

    def test_scan_excludes_global_and_keeps_short_flag(self):
        """差集：全局已有的不进候选；短词打标；跨作品计数按作品（非条目）。"""
        self._global(pairs=[{"jp": "既存", "zh": "已有", "cat": "特殊-专名"}], known=["白名单"])
        self._work("w1", [{"jp": "既存", "zh": "已有", "cat": "专名", "来源": "manual"},
                          {"jp": "白名单", "zh": "白", "cat": "专名", "来源": "manual"},
                          {"jp": "アカネ", "zh": "茜", "cat": "角色", "来源": "manual"},
                          {"jp": "イギリス", "zh": "英国", "cat": "专名", "来源": "manual"},
                          {"jp": "ミク", "zh": "未来", "cat": "角色", "来源": "manual"}])
        self._work("w2", [{"jp": "ミク", "zh": "Miku", "cat": "角色", "来源": "manual"},
                          {"jp": "ミク", "zh": "Miku2", "cat": "角色", "来源": "manual"},
                          {"jp": "イギリス", "zh": "英国", "cat": "专名", "来源": "manual"}])
        self._work("w3", [{"jp": "アカネ", "zh": "阿卡内", "cat": "角色", "来源": "manual"}])
        with mock.patch.object(registry, "get", lambda w: self._cfg(w)):
            res = GR.scan("w1", with_embed=False)
        jps = {it["jp"]: it for it in res["items"]}
        self.assertNotIn("既存", jps)                       # 全局替换对已有
        self.assertNotIn("白名单", jps)                      # known_names 已有
        self.assertEqual(jps["ミク"]["跨作品"], 2)           # w1+w2（w2 两条重复不叠加）
        self.assertEqual(jps["アカネ"]["跨作品"], 2)
        self.assertTrue(jps["ミク"]["短词"])                 # 2 字
        self.assertTrue(jps["アカネ"]["短词"])               # 3 字（<= SHORT_LEN）
        self.assertFalse(jps["イギリス"]["短词"])            # 4 字
        self.assertTrue(jps["イギリス"]["选中"])             # 跨作品 2 且非短词 → 默认勾
        self.assertFalse(jps["ミク"]["选中"])                # 短词 → 默认不勾
        self.assertEqual(jps["ミク"]["建议"], "角色")
        self.assertEqual(jps["イギリス"]["建议"], "专名")

    def test_skip_rules(self):
        """re: 开头 / 缺译名 / zh==jp / 语法功能词 全部跳过（不进可反哺）。"""
        self._global()
        self._work("w1", [{"jp": "re:(?<![ァ])ジン(?![ァ])", "zh": "金酒", "cat": "正则", "来源": "manual"},
                          {"jp": "ノエル", "zh": "", "cat": "角色", "来源": "manual"},
                          {"jp": "初音", "zh": "初音", "cat": "角色", "来源": "manual"},
                          {"jp": "そう", "zh": "那样", "cat": "专名", "来源": "manual"},
                          {"jp": "イリヤ", "zh": "伊莉雅", "cat": "角色", "来源": "manual"}])
        with mock.patch.object(registry, "get", lambda w: self._cfg(w)):
            res = GR.scan("w1", with_embed=False)
        by = {it["jp"]: it for it in res["items"]}
        self.assertIn("正则", by["re:(?<![ァ])ジン(?![ァ])"]["跳过"])
        self.assertEqual(by["ノエル"]["跳过"], "缺译名")
        self.assertEqual(by["初音"]["跳过"], "无译名（中日同形）")
        self.assertIn("语法功能词", by["そう"]["跳过"])
        self.assertEqual(by["イリヤ"]["跳过"], "")
        self.assertEqual(res["summary"]["可反哺"], 1)

    def test_auto_entries_not_candidates(self):
        """作品表 auto 条目不算反哺候选（只认 manual）。"""
        self._global()
        self._work("w1", [{"jp": "ノエル", "zh": "诺艾尔", "cat": "角色", "来源": "auto"}])
        with mock.patch.object(registry, "get", lambda w: self._cfg(w)):
            res = GR.scan("w1", with_embed=False)
        self.assertEqual(res["summary"]["合计"], 0)

    def test_work_dir_sidecar_json_not_mistaken_for_table(self):
        """work/ 里的 glossary_candidates.json / glossary_iter.json 不是作品表（2026-09-20 实测 bug）。

        它们与真表同前缀且字母序在前，旧 glob 会首选它们 → 该作品反哺列表恒空（23 部作品受影响）。
        """
        self._global()
        wd = self._work("w1", [{"jp": "イギリス", "zh": "英国", "cat": "专名", "来源": "manual"}])
        # 旁支 JSON（无 glossary 键）
        _dump(os.path.join(wd, "glossary_candidates.json"),
              {"schema": "术语表候选 v1", "ai_候选": [{"term": "x"}]})
        _dump(os.path.join(wd, "glossary_iter.json"), {"作品": "w1", "候选": []})
        self.assertTrue(GR.work_glossary_path("w1", wd).endswith("glossary_w1.json"))
        self.assertEqual([wid for wid, _ in GR.work_glossary_files()], ["w1"])
        with mock.patch.object(registry, "get", lambda w: self._cfg(w)):
            res = GR.scan("w1", with_embed=False)
        self.assertEqual([it["jp"] for it in res["items"]], ["イギリス"])

    def test_work_glossary_path_falls_back_when_stem_differs(self):
        """文件名与 registry id 不一致（sujin_otona → 大人のお店）时仍取到真表。"""
        self._global()
        wd = os.path.join(self.manga, "w1", "work")
        os.makedirs(wd, exist_ok=True)
        _dump(os.path.join(wd, "glossary_大人のお店.json"),
              {"schema": "wg", "glossary": [{"jp": "アリス", "zh": "爱丽丝",
                                             "cat": "角色", "来源": "manual"}]})
        _dump(os.path.join(wd, "glossary_candidates.json"), {"ai_候选": []})
        self.assertTrue(GR.work_glossary_path("sujin_otona", wd).endswith("glossary_大人のお店.json"))
        self.assertEqual([e["jp"] for e in GR.entries(GR.work_glossary_path("sujin_otona", wd),
                                                      manual_only=True)], ["アリス"])

    def test_embed_rate_uses_work_corpus(self):
        """嵌入率按本作品语料算：独立出现 1 次 + 被夹 1 次 → 0.5。"""
        self._global()
        wd = self._work("w1", [{"jp": "ミク", "zh": "未来", "cat": "角色", "来源": "manual"}])
        aa = os.path.join(self.manga, "w1", "aa")
        os.makedirs(aa, exist_ok=True)
        with open(os.path.join(aa, "entry_1.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write("ミクが来た\nミクスの話\n")               # 1 独立 + 1 被夹
        with open(os.path.join(aa, "entry_1_台词.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write("ミクミクミク\n")                        # 台词文件不该计入
        cfg = {"work_dir": wd, "aa_dir": aa, "out_dir": os.path.join(self.manga, "w1", "汉化")}
        with mock.patch.object(registry, "get", lambda w: cfg):
            res = GR.scan("w1", with_embed=True)
        self.assertAlmostEqual(res["items"][0]["嵌入率"], 0.5, places=4)

    def test_work_whitelist_is_candidate(self):
        """作品表 known_names → 类型=白名单项（2026-09-20 用户要求：只写全局 known_names）。

        跨作品数按各作品白名单计；短词不设闸门（白名单不替换正文，没有误替换风险）。
        """
        self._global()
        self._work("w1", [{"jp": "イギリス", "zh": "英国", "cat": "专名", "来源": "manual"}],
                   known=["アリス", "そう", "re:(?<![ァ])x"])
        self._work("w2", [], known=["アリス"])
        with mock.patch.object(registry, "get", lambda w: self._cfg(w)):
            res = GR.scan("w1", with_embed=False)
        by = {it["jp"]: it for it in res["items"]}
        self.assertEqual(by["アリス"]["类型"], "白名单")
        self.assertEqual(by["アリス"]["zh"], "")
        self.assertEqual(by["アリス"]["跨作品"], 2)          # w1 + w2 都收进白名单
        self.assertTrue(by["アリス"]["选中"])                 # 跨作品 >=2 即默认勾（短词不设闸门）
        self.assertEqual(by["アリス"]["嵌入率"], None)        # 白名单不算嵌入率
        self.assertIn("语法功能词", by["そう"]["跳过"])
        self.assertIn("正则", by["re:(?<![ァ])x"]["跳过"])
        self.assertEqual(res["summary"]["白名单"], 1)

    def test_global_known_names_excluded(self):
        """全局白名单已有的词 → 不是反哺候选。"""
        self._global(known=["アリス"])
        self._work("w1", [], known=["アリス"])
        with mock.patch.object(registry, "get", lambda w: self._cfg(w)):
            res = GR.scan("w1", with_embed=False)
        self.assertEqual(res["items"], [])


class PayloadTest(_Base):

    def test_short_special_gets_re_boundary_and_role_does_not(self):
        """短词专名 → jp_out 包 re: 词边界；短词角色 → 不包（走 roles.json 安全通道）。"""
        self.assertIn("re:", GR.re_wrap("ミク"))
        self.assertTrue(GR.re_wrap("ミク").startswith("re:(?<!"))
        self.assertIn("(?![", GR.re_wrap("ミク"))
        # 含汉字的短词：右侧再加汉字邻接守卫（防把更长汉字词改坏）
        self.assertIn("\u4e00-\u9fff", GR.re_wrap("キル夫"))
        self.assertNotIn("\u4e00-\u9fff", GR.re_wrap("アカネ"))

    def test_build_payload_routing(self):
        """类型分流：角色→roles；专名→glossary(特殊-专名)；两侧都进 known_names。"""
        self._global()
        items = [{"jp": "アカネ", "zh": "茜", "cat": "角色"},
                 {"jp": "イギリス", "zh": "英国", "cat": "专名"},
                 {"jp": "ミク", "zh": "未来", "cat": "专名"}]
        pay = GR.build_payload(items, "w1")
        self.assertEqual([r["日文"] for r in pay["roles"]], ["アカネ"])
        self.assertEqual(pay["roles"][0]["作品"], ["w1"])
        cats = {p["jp"]: p["cat"] for p in pay["pairs"]}
        self.assertEqual(cats["イギリス"], "特殊-专名")
        self.assertEqual(pay["known"], ["アカネ", "イギリス", "ミク"])
        # 短词专名带 re: 边界，长词原样
        self.assertTrue(next(p for p in pay["pairs"] if p["zh"] == "未来")["jp"].startswith("re:"))
        self.assertEqual(next(p for p in pay["pairs"] if p["zh"] == "英国")["jp"], "イギリス")

    def test_whitelist_item_writes_known_only(self):
        """类型=白名单 → 只进 known_names（不生成替换对/角色）；语法功能词跳过。"""
        self._global()
        pay = GR.build_payload([{"jp": "アリス", "类型": "白名单"},
                                {"jp": "そう", "类型": "白名单"},
                                {"jp": "イギリス", "zh": "英国", "cat": "专名", "类型": "替换对"}],
                               "w1")
        self.assertEqual(pay["roles"], [])
        self.assertEqual([p["jp"] for p in pay["pairs"]], ["イギリス"])
        self.assertEqual(pay["known"], ["アリス", "イギリス"])
        self.assertEqual([s["jp"] for s in pay["skipped"]], ["そう"])
        self.assertIn("语法功能词", pay["skipped"][0]["why"])

    def test_whitelist_item_already_global_skipped(self):
        self._global(known=["アリス"])
        pay = GR.build_payload([{"jp": "アリス", "类型": "白名单"}], "w1")
        self.assertEqual(pay["known"], [])
        self.assertEqual(pay["skipped"][0]["why"], "全局白名单已有")


class ApplyTest(_Base):

    def _items(self):
        return [{"jp": "アカネ", "zh": "茜", "cat": "角色"},
                {"jp": "イギリス", "zh": "英国", "cat": "专名"},
                {"jp": "ミク", "zh": "未来", "cat": "专名"},
                {"jp": "初音", "zh": "初音", "cat": "专名"}]      # 无译名 → 跳过

    def test_apply_writes_backup_and_skips(self):
        self._global()
        out = GR.apply(self._items(), "w1")
        self.assertEqual((out["roles"], out["pairs"]), (1, 2))
        self.assertEqual(out["known"], 3)
        self.assertEqual([s["jp"] for s in out["skipped"]], ["初音"])
        # 备份产生
        self.assertTrue(os.path.isdir(os.path.join(self.bk, out["backup"])))
        for fn in ("glossary.json", "roles.json"):
            self.assertTrue(os.path.exists(os.path.join(self.bk, out["backup"], fn)))
        roles = json.load(open(self.rl, encoding="utf-8"))
        self.assertEqual(roles["角色"][0]["日文"], "アカネ")
        self.assertIn("反哺自", roles["角色"][0]["备注"])
        self.assertNotIn("昵称", roles["角色"][0]["备注"])        # 别触发"不注入"规则
        gl = json.load(open(self.gl, encoding="utf-8"))
        self.assertIn("アカネ", gl["known_names"])
        cats = {p["jp"]: p["cat"] for p in gl["glossary"]}
        self.assertEqual(cats["イギリス"], "特殊-专名")
        self.assertTrue(any(k.startswith("re:") for k in cats))
        self.assertTrue(gl["更新日期"])
        # 落盘 JSON 可再解析（原子写没留 .tmp）
        self.assertFalse([f for f in os.listdir(self.tmp) if f.endswith(".tmp_repatriate")])

    def test_apply_idempotent(self):
        """重复 apply 不重复写（第二次全部已在全局）。"""
        self._global()
        GR.apply(self._items(), "w1")
        out2 = GR.apply(self._items(), "w1")
        self.assertEqual((out2["roles"], out2["pairs"], out2["known"]), (0, 0, 0))
        self.assertEqual(out2["backup"], None)                    # 无写入则不备份
        roles = json.load(open(self.rl, encoding="utf-8"))["角色"]
        self.assertEqual(len(roles), 1)
        gl = json.load(open(self.gl, encoding="utf-8"))
        self.assertEqual(len([p for p in gl["glossary"] if p["zh"] == "英国"]), 1)

    def test_apply_skips_when_global_already_has_pair(self):
        self._global(pairs=[{"jp": "イギリス", "zh": "英国", "cat": "特殊-专名"}])
        out = GR.apply([{"jp": "イギリス", "zh": "英国", "cat": "专名"}], "w1")
        self.assertEqual(out["pairs"], 0)
        self.assertEqual(out["skipped"][0]["why"], "全局替换对已有")

    def test_apply_empty(self):
        self._global()
        out = GR.apply([], "w1")
        self.assertEqual(out["roles"], 0)
        self.assertIn("无可写入项", out["msg"])


class DiffSetFixTest(_Base):
    """决策 6：候选池差集口径修复。"""

    def test_load_known_includes_work_tables(self):
        import json as _j
        with mock.patch.object(BTP, "_BASE", self.tmp):
            self._global(known=["全局白名单"])
            self._work("w1", [{"jp": "ノエル", "zh": "诺艾尔", "cat": "角色", "来源": "manual"},
                              {"jp": "auto名", "zh": "自动", "cat": "专名", "来源": "auto"}])
            known = BTP.load_known("w1")
        self.assertIn("ノエル", known)
        self.assertIn("诺艾尔", known)
        self.assertIn("auto名", known)            # manual+auto 都算（决策 6 原文）
        self.assertIn("全局白名单", known)
        self.assertIsInstance(_j.loads("{}"), dict)

    def test_load_existing_includes_roles_json(self):
        self._global(known=["白名单"])
        _dump(self.rl, {"schema": "r", "角色": [
            {"日文": "灰かぶりの魔女", "中文": "灰姑娘魔女", "别名": ["灰魔女"]}]})
        with mock.patch.object(GA, "GLOSSARY_JSON", self.gl), \
             mock.patch.object(GA, "BASE", self.tmp):
            ex = GA.load_existing()
        self.assertIn("灰かぶりの魔女", ex)
        self.assertIn("灰姑娘魔女", ex)
        self.assertIn("灰魔女", ex)

    def test_dead_code_removed(self):
        self.assertFalse(hasattr(GA, "extract_full"))
        self.assertFalse(hasattr(GA, "collect_all_batches"))


class RouteTest(_Base):
    """工作台两个薄路由（函数级调用，不启动服务器）。"""

    def _scan_env(self):
        self._global()
        wd = self._work("w1", [{"jp": "イギリス", "zh": "英国", "cat": "专名", "来源": "manual"},
                               {"jp": "アカネ", "zh": "茜", "cat": "角色", "来源": "manual"},
                               {"jp": "初音", "zh": "初音", "cat": "角色", "来源": "manual"}])
        self._work("w2", [{"jp": "イギリス", "zh": "英国", "cat": "专名", "来源": "manual"}])
        cfg = {"work_dir": wd, "aa_dir": os.path.join(self.manga, "w1", "aa"),
               "out_dir": os.path.join(self.manga, "w1", "汉化")}
        return cfg

    def test_list_route(self):
        cfg = self._scan_env()
        import aa_web_app.main as M
        with mock.patch.object(WS, "cfg_of", lambda w: cfg), \
             mock.patch.object(registry, "get", lambda w: cfg):
            r = M.api_repatriate_list("w1", embed=0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["work"], "w1")
        self.assertEqual({it["jp"] for it in r["items"]}, {"イギリス", "アカネ", "初音"})

    def test_list_route_unknown_work(self):
        import aa_web_app.main as M
        from fastapi import HTTPException

        def _boom(_w):
            raise HTTPException(404, "未知作品")
        with mock.patch.object(WS, "cfg_of", _boom):
            r = M.api_repatriate_list("nope", embed=0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "未知作品")

    def test_apply_route_uses_server_side_scan(self):
        """前端只送 jp；zh/cat 由服务端重新 scan 取（送假数据也不生效）。"""
        cfg = self._scan_env()
        import aa_web_app.main as M
        body = M.RepatriateBody(selected=["イギリス", "初音"], backup=True)
        with mock.patch.object(WS, "cfg_of", lambda w: cfg), \
             mock.patch.object(registry, "get", lambda w: cfg):
            r = M.api_repatriate_apply("w1", body)
        self.assertTrue(r["ok"])
        self.assertEqual(r["pairs"], 1)               # イギリス → 特殊-专名
        self.assertEqual(r["roles"], 0)               # 初音 是 zh==jp → 路由层已剔除
        self.assertEqual(r["skipped_selection"],
                         [{"jp": "初音", "why": "无译名（中日同形）"}])
        gl = json.load(open(self.gl, encoding="utf-8"))
        self.assertEqual(gl["glossary"][0], {"jp": "イギリス", "zh": "英国",
                                             "cat": "特殊-专名", "来源": "manual",
                                             "备注": "反哺自 w1"})
        self.assertNotIn("初音", gl["known_names"])    # 被剔除项不写白名单

    def test_apply_route_rejects_empty(self):
        cfg = self._scan_env()
        import aa_web_app.main as M
        with mock.patch.object(WS, "cfg_of", lambda w: cfg), \
             mock.patch.object(registry, "get", lambda w: cfg):
            r = M.api_repatriate_apply("w1", M.RepatriateBody(selected=[]))
        self.assertFalse(r["ok"])
        self.assertIn("未勾选", r["error"])

    def test_whitelist_route_writes_work_table_only(self):
        """候选「白名单」路由：只写**本作品**表 known_names + 移除本条候选，全局不动（2026-09-20）。"""
        self._global()
        wd = self._work("w1", [{"jp": "イギリス", "zh": "英国", "cat": "专名", "来源": "manual"}])
        _dump(os.path.join(wd, "glossary_candidates.json"),
              {"ai_候选": [{"term": "アリス"}, {"term": "ボブ"}]})
        cfg = self._cfg("w1")
        import aa_web_app.main as M
        with mock.patch.object(WS, "cfg_of", lambda w: cfg), \
             mock.patch.object(registry, "get", lambda w: cfg), \
             mock.patch.object(WS, "paths_of",
                               lambda c: (c["aa_dir"], c["out_dir"], c["work_dir"])):
            r = M.api_candidate_whitelist("w1", "アリス")
        self.assertTrue(r["ok"])
        self.assertTrue(r["added"])
        self.assertEqual(r["removed"], 1)
        tbl = json.load(open(os.path.join(wd, "glossary_w1.json"), encoding="utf-8"))
        self.assertIn("アリス", tbl["known_names"])
        self.assertEqual(len(tbl["glossary"]), 1)                       # 已有替换对保留
        # 全局 glossary.json 的 known_names 不受影响
        self.assertNotIn("アリス", json.load(open(self.gl, encoding="utf-8"))["known_names"])
        cands = json.load(open(os.path.join(wd, "glossary_candidates.json"), encoding="utf-8"))
        self.assertEqual([c["term"] for c in cands["ai_候选"]], ["ボブ"])

    def test_whitelist_route_rejects_empty_term(self):
        cfg = self._scan_env()
        import aa_web_app.main as M
        with mock.patch.object(WS, "cfg_of", lambda w: cfg), \
             mock.patch.object(registry, "get", lambda w: cfg):
            r = M.api_candidate_whitelist("w1", "   ")
        self.assertFalse(r["ok"])
        self.assertIn("空词", r["error"])


if __name__ == "__main__":
    unittest.main()
