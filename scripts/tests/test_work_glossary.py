# -*- coding: utf-8 -*-
"""作品术语表生成器测试 (2026-09-01 术语表三级重构)"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"))

import gen_work_glossary as GWG
from _work_guard import repo_only  # noqa: E402 （发行包内跳过生成者专属用例）

# 测试用假全局 glossary/roles（不碰真实文件）
_FAKE_GL = {
    "schema": "AA翻译术语表 v2",
    "glossary": [
        {"jp": "草生える", "zh": "笑死我了", "cat": "常用-梗", "来源": "manual"},
        {"jp": "白頭", "zh": "白头", "cat": "特殊-作品名", "来源": "manual"},
        {"jp": "リーチ", "zh": "立直", "cat": "特殊-麻将", "来源": "manual"},
        {"jp": "re:([一-龠]{1,4})さん", "zh": "\\1桑", "cat": "规则-正则", "来源": "manual"},
    ],
    "作品": [],
    "角色": [],
    "known_names": [],
}
_FAKE_ROLES = {
    "schema": "AA角色表 v1",
    "角色": [
        {"日文": "やる夫", "中文": "亚鲁欧", "作品": [], "分类": "角色", "备注": "", "别名": []},
        {"日文": "宮永咲", "中文": "宮永咲", "作品": ["saki"], "分类": "角色", "备注": "", "别名": []},
        {"日文": "とよね", "中文": "とよね?", "作品": ["saki"], "分类": "角色", "备注": "未确认", "别名": []},
        {"日文": "憧", "中文": "新子憧", "作品": ["saki"], "分类": "角色", "备注": "AA内昵称/简称", "别名": []},
    ],
}
_CORPUS = "やる夫は今日も憧とリーチを狙う。\n草生える\n"


class TestDeriveAuto(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 备份真实文件路径
        cls._real_gl = GWG.GLOSSARY_JSON
        cls._real_roles = GWG.ROLES_JSON
        # 写假文件到临时目录
        cls.tmp = tempfile.mkdtemp()
        GWG.GLOSSARY_JSON = os.path.join(cls.tmp, "glossary.json")
        GWG.ROLES_JSON = os.path.join(cls.tmp, "roles.json")
        with open(GWG.GLOSSARY_JSON, "w", encoding="utf-8") as f:
            json.dump(_FAKE_GL, f, ensure_ascii=False)
        with open(GWG.ROLES_JSON, "w", encoding="utf-8") as f:
            json.dump(_FAKE_ROLES, f, ensure_ascii=False)

    @classmethod
    def tearDownClass(cls):
        GWG.GLOSSARY_JSON = cls._real_gl
        GWG.ROLES_JSON = cls._real_roles

    def test_common_full_inject(self):
        out = GWG.derive_auto(_CORPUS, _FAKE_ROLES["角色"])
        # 常用-梗 全量注入(不过滤, 即使不在语料——方案决策3: 常用不过滤)
        self.assertTrue(any(e["jp"] == "草生える" for e in out))

    def test_special_filtered_by_corpus(self):
        out = GWG.derive_auto(_CORPUS, _FAKE_ROLES["角色"])
        # リーチ 在语料 -> 注入
        self.assertTrue(any(e["jp"] == "リーチ" for e in out))
        # 白頭 不在语料 -> 不注入 (特殊对按语料过滤)
        self.assertFalse(any(e["jp"] == "白頭" for e in out))

    def test_role_hit_zh_is_original(self):
        # 2026-09-01: 角色对 zh 一律日文原文(未人工确认不猜译名), role_cn 携带真实译名
        out = GWG.derive_auto(_CORPUS, _FAKE_ROLES["角色"])
        # やる夫 命中语料 -> 注入, 但 zh=原文, role_cn=亚鲁欧
        e = next((x for x in out if x["jp"] == "やる夫"), None)
        self.assertIsNotNone(e)
        self.assertEqual(e["zh"], "やる夫")
        self.assertEqual(e["role_cn"], "亚鲁欧")
        # 宮永咲 不在语料 -> 不注入
        self.assertFalse(any(x["jp"] == "宮永咲" for x in out))
        # 憧 昵称/简称条目 -> 即使命中语料也不注入(昵称只作素材索引, 不术语替换)
        self.assertFalse(any(x["jp"] == "憧" for x in out))

    def test_regex_never_injected(self):
        out = GWG.derive_auto(_CORPUS, _FAKE_ROLES["角色"])
        self.assertFalse(any(e["jp"].startswith("re:") for e in out))

    def test_cat_three_classes(self):
        # 2026-09-06 分类重构: auto 派生 cat 三类(角色/专名/正则), 无旧分类
        out = GWG.derive_auto(_CORPUS, _FAKE_ROLES["角色"])
        self.assertTrue(out)
        for e in out:
            self.assertIn(e.get("cat"), {"角色", "专名", "正则"})
        # roles 派生(やる夫) → 角色; 全局 常用-梗(草生える) → 专名
        e = next(x for x in out if x["jp"] == "やる夫")
        self.assertEqual(e["cat"], "角色")
        e2 = next(x for x in out if x["jp"] == "草生える")
        self.assertEqual(e2["cat"], "专名")


class TestWorkGlossaryIO(unittest.TestCase):
    def test_roundtrip_v1_to_v2(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "glossary_test.json")
        # v1 旧格式: 二元组
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"schema": "AA翻译作品术语表 v1", "glossary": [["やらない夫", "不做夫"]]}, f, ensure_ascii=False)
        manual, auto, _, schema = GWG.read_work_glossary(path)
        self.assertEqual(len(manual), 1)
        self.assertEqual(manual[0]["jp"], "やらない夫")
        self.assertEqual(manual[0]["来源"], "manual")
        self.assertEqual(len(auto), 0)
        self.assertEqual(schema, "AA翻译作品术语表 v1")

    def test_roundtrip_v2(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "glossary_test.json")
        data = {"schema": "AA翻译作品术语表 v2", "glossary": [
            {"jp": "やらない夫", "zh": "不做夫", "cat": "手动", "来源": "manual"},
            {"jp": "草生える", "zh": "笑死我了", "cat": "常用-梗", "来源": "auto"},
        ]}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        manual, auto, _, _ = GWG.read_work_glossary(path)
        self.assertEqual([e["jp"] for e in manual], ["やらない夫"])
        self.assertEqual([e["jp"] for e in auto], ["草生える"])

    def test_force_keeps_existing_auto(self):
        # 2026-09-06 事故回归: --force 重生成不得清空已有 auto 条目
        # (Q14-A 曾覆盖为仅正则 → bar_miku 44条→1条)。表有内容时 force 保留
        # auto + 补全局正则; 不重新派生灌 auto(用户规则: 表非空禁 auto)。
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "glossary_t.json")
        data = {"schema": "AA翻译作品术语表 v2", "glossary": [
            {"jp": "やる夫", "zh": "亚鲁欧", "cat": "角色", "来源": "manual"},
            {"jp": "やらない夫", "zh": "不做夫", "cat": "角色", "来源": "manual"},
            {"jp": "草生える", "zh": "笑死我了", "cat": "专名", "来源": "auto"},
        ]}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        # 假全局 glossary(临时)——derive_auto 依赖它
        gld = {"schema": "x", "glossary": [
            {"jp": "乙でした", "zh": "辛苦了", "cat": "常用-梗", "来源": "manual"}]}
        gpath = os.path.join(tmp, "glossary.json")
        with open(gpath, "w", encoding="utf-8") as f:
            json.dump(gld, f, ensure_ascii=False)
        _saved = (GWG.GLOSSARY_JSON, GWG.ROLES_JSON)
        GWG.GLOSSARY_JSON = gpath
        GWG.ROLES_JSON = os.path.join(tmp, "roles.json")
        with open(GWG.ROLES_JSON, "w", encoding="utf-8") as f:
            json.dump({"角色": []}, f, ensure_ascii=False)
        try:
            # 建临时 aa 语料目录(generate 需要)
            aa = os.path.join(tmp, "aa")
            os.makedirs(aa)
            with open(os.path.join(aa, "e1.txt"), "w", encoding="utf-8") as f:
                f.write("やる夫は今日も頑張る\n")
            work_root = os.path.join(tmp, "w")
            os.makedirs(work_root, exist_ok=True)
            work_dir = os.path.join(work_root, "work")
            os.makedirs(work_dir, exist_ok=True)
            _p, _an, _mn, _rg = GWG.generate(work_dir, "t", force=True)
            with open(path, encoding="utf-8") as f:
                d2 = json.load(f)
            jps = [(x.get("jp"), x.get("来源")) for x in d2["glossary"]]
            self.assertIn(("やる夫", "manual"), jps)      # manual 保留
            self.assertIn(("やらない夫", "manual"), jps)  # manual 保留
            self.assertIn(("草生える", "auto"), jps)      # 已有 auto 不被清
            self.assertGreaterEqual(len(jps), 3)
        finally:
            GWG.GLOSSARY_JSON, GWG.ROLES_JSON = _saved

    @repo_only("断言 saki 作品的术语表路径（包里只有示例作品）")
    def test_work_glossary_path(self):
        p = GWG.work_glossary_path(r"F:\aaManga\saki\work", "saki")
        self.assertTrue(p.endswith("glossary_saki.json"))
        p2 = GWG.work_glossary_path(r"F:\aaManga\saki\work")
        self.assertTrue(p2.endswith("glossary_saki.json"))


if __name__ == "__main__":
    unittest.main()
