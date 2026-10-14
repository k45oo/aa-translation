# -*- coding: utf-8 -*-
"""候选确认 API 测试：列表排序 / 确认 / 删除 / 待确认区清理联动。

运行：python -m unittest scripts.tests.test_candidates -v
零依赖（unittest + 临时文件 + mock registry），不启动服务器、不调 API。
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
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts", "tools"))
sys.path.insert(0, os.path.join(_REPO_ROOT, "aa_web_app"))


def _mk_cand_file(tmpdir, cands):
    p = os.path.join(tmpdir, "glossary_candidates.json")
    data = {"schema": "术语表候选 v1", "work": "test", "生成日期": "2026-08-28",
            "采样": {}, "ai_候选": cands, "ai_去重跳过": [], "高频假名片段": [], "统计": {}}
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return p


class _FakeCfg(dict):
    """模拟 registry.WORKS[work] 配置（dict：work_dir/aa_dir/out_dir）。"""
    def __init__(self, tmpdir):
        super().__init__(work_dir=tmpdir, aa_dir=tmpdir, out_dir=tmpdir)


class CandidatesAPITest(unittest.TestCase):
    """对 main.py 候选 API 函数做函数级测试（不依赖 httpx/TestClient）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cand_test_")
        self.cands = [
            {"term": "角色B", "translation": "乙", "type": "角色", "确认": False, "来源": "e1.txt,e2.txt,e3.txt"},
            {"term": "术语A", "translation": "甲", "type": "术语", "确认": False, "来源": "e1.txt"},
            {"term": "已确认", "translation": "丙", "type": "角色", "确认": True, "来源": "e1.txt"},
        ]
        _mk_cand_file(self.tmp, self.cands)
        self.cfg = _FakeCfg(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _load(self):
        with open(os.path.join(self.tmp, "glossary_candidates.json"), encoding="utf-8") as f:
            return json.load(f)

    @mock.patch("aa_web_app.web_state.cfg_of")
    def test_list_sorted_and_excludes_confirmed(self, m_cfg):
        """列表: 排除已确认 + 按 type 顺序(术语→角色) + 来源数降序。"""
        m_cfg.return_value = self.cfg
        import aa_web_app.main as M
        r = M.api_candidates("test")
        self.assertTrue(r["ok"])
        self.assertEqual(r["total"], 2)
        types = [c["type"] for c in r["candidates"]]
        self.assertEqual(types, ["术语", "角色"])  # 术语(0) 在 角色(3) 前

    @mock.patch("aa_web_app.web_state.cfg_of")
    def test_confirm_sets_flag_and_updates(self, m_cfg):
        """确认: 设 确认:true + 更新译名/作品。"""
        m_cfg.return_value = self.cfg
        import aa_web_app.main as M
        r = M.api_candidate_confirm("test", "术语A", {"translation": "甲甲", "作品": "外部IP"})
        self.assertTrue(r["ok"])
        data = self._load()
        hit = next(c for c in data["ai_候选"] if c["term"] == "术语A")
        self.assertTrue(hit["确认"])
        self.assertEqual(hit["translation"], "甲甲")
        self.assertEqual(hit["作品"], "外部IP")

    @mock.patch("aa_web_app.web_state.cfg_of")
    def test_delete_removes(self, m_cfg):
        """删除: 永久移除。"""
        m_cfg.return_value = self.cfg
        import aa_web_app.main as M
        r = M.api_candidate_delete("test", "角色B")
        self.assertTrue(r["ok"])
        data = self._load()
        terms = [c["term"] for c in data["ai_候选"]]
        self.assertNotIn("角色B", terms)
        self.assertEqual(len(terms), 2)

    @mock.patch("aa_web_app.web_state.cfg_of")
    def test_confirm_unknown_term(self, m_cfg):
        """确认不存在的 term → 报错。"""
        m_cfg.return_value = self.cfg
        import aa_web_app.main as M
        r = M.api_candidate_confirm("test", "不存在", {})
        self.assertFalse(r["ok"])

    @mock.patch("aa_web_app.web_state.cfg_of")
    @mock.patch("aa_web_app.logic.save_work_glossary")
    @mock.patch("registry.get")
    @mock.patch("work_memory.load_knowledge")
    @mock.patch("work_memory.save_knowledge")
    def test_promote_work_glossary_writes(self, m_save_kn, m_load_kn, m_reg, m_save, m_cfg):
        """promote-work-glossary: 确认候选写入作品表(cat 归类/不覆盖 manual 同 jp)。

        2026-09-06 grilling②: 角色→cat 角色, 术语→cat 专名, re:→正则;
        manual 同 jp 跳过; 角色同时进知识库人物。
        """
        m_cfg.return_value = self.cfg
        # registry.get 返回同 cfg(work_memory._work_dir 用) → 知识库写 tmp 目录
        m_reg.return_value = self.cfg
        m_save_kn.return_value = (True, "")
        m_load_kn.return_value = {"人物": {}, "术语": {}}
        # 预置一个 manual 同 jp 的作品表(角色B 已人工确认) + 白名单(必须原样带回)
        gl_path = os.path.join(self.tmp, "glossary_test.json")
        with open(gl_path, "w", encoding="utf-8") as f:
            json.dump({"schema": "AA翻译作品术语表 v2", "glossary": [
                {"jp": "角色B", "zh": "乙乙", "cat": "角色", "来源": "manual"}],
                "known_names": ["ルイ"]}, f, ensure_ascii=False)
        # 候选: 1 确认角色 + 1 确认术语 + 1 确认 re: + 1 未确认
        cands = [
            {"term": "新角色X", "translation": "新角色X译", "type": "角色", "确认": True, "来源": "e1.txt"},
            {"term": "角色B", "translation": "乙(撞 manual)", "type": "角色", "确认": True, "来源": "e1.txt"},
            {"term": "地名Y", "translation": "Y地", "type": "专有名词", "确认": True, "来源": "e1.txt"},
            {"term": "re:([一-龠]{1,4})殿", "translation": "\\1大人", "type": "术语", "确认": True, "来源": "e1.txt"},
            {"term": "未确认Z", "translation": "Z", "type": "角色", "确认": False, "来源": "e1.txt"},
        ]
        _mk_cand_file(self.tmp, cands)
        m_save.return_value = (True, "")
        import aa_web_app.main as M
        r = M.api_candidates_promote_work_glossary("test")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["written"], 3)  # 新角色X + 地名Y + re:殿(角色B撞manual跳过, 未确认Z不处理)
        # save_work_glossary 收到合并对: 3 新条目 cat 归类正确
        merged = m_save.call_args[0][1]
        by_jp = {p["jp"]: p for p in merged}
        self.assertEqual(by_jp["新角色X"]["cat"], "角色")
        self.assertEqual(by_jp["地名Y"]["cat"], "专名")
        self.assertEqual(by_jp["re:([一-龠]{1,4})殿"]["cat"], "正则")
        self.assertEqual(by_jp["角色B"]["zh"], "乙乙")  # manual 同 jp 未被覆盖
        # 2026-09-19: known_names 原样带回（旧代码传 [] → 白名单被静默清空）
        self.assertEqual(m_save.call_args[0][2], ["ルイ"])
        # 知识库: 角色进人物
        saved_mem = m_save_kn.call_args[0][1]
        self.assertIn("新角色X", saved_mem["人物"])
        self.assertIn("地名Y", saved_mem["术语"])
        self.assertNotIn("re:([一-龠]{1,4})殿", saved_mem["术语"])  # 正则不进知识库
        # 候选文件: 已写入项移除, 未确认保留
        data = self._load()
        terms = [c["term"] for c in data["ai_候选"]]
        self.assertNotIn("新角色X", terms)
        self.assertIn("未确认Z", terms)


class PromoteCleansPendingTest(unittest.TestCase):
    """审批1: --promote 落地后同步清理知识库 待确认区 对应项。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="promote_test_")
        # 候选: 1 确认角色 + 1 未确认
        cands = [
            {"term": "シックス先生", "translation": "六老师", "type": "角色", "确认": True, "来源": "e1.txt"},
            {"term": "手抜きさん", "translation": "敷衍先生", "type": "术语", "确认": False, "来源": "e1.txt"},
        ]
        _mk_cand_file(self.tmp, cands)
        # 知识库: 待确认区含同一角色 + 别的不相关项
        self.know_p = os.path.join(self.tmp, "test_knowledge.json")
        know = {
            "schema": "作品知识库 v1", "更新日期": "2026-08-27",
            "术语": {}, "人物": {},
            "待确认": {"术语": {"别的不相关": "x"}, "人物": {"シックス先生": "六老师(待定)"}},
        }
        with open(self.know_p, "w", encoding="utf-8") as f:
            json.dump(know, f, ensure_ascii=False, indent=1)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    @mock.patch("aa_web_app.web_state.cfg_of")
    def test_promote_cleans_pending(self, m_cfg):
        """promote 后待确认区对应项被清（保留不相关项）。"""
        # 模拟 work_memory.load_knowledge/save_knowledge 指向 tmp
        import aa_web_app.main as M
        # 直接测 gen_glossary_ai._promote_confirmed（它是实际实现）
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "gen_glossary_ai", os.path.join(_REPO_ROOT, "scripts", "common", "gen_glossary_ai.py"))
        gga = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gga)

        # gen_glossary_ai 内部用 work_memory.load_knowledge/save_knowledge
        sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts", "tools"))
        import work_memory as WM
        with mock.patch.object(WM, "load_knowledge") as m_load, \
             mock.patch.object(WM, "save_knowledge") as m_save:
            # load_knowledge 返回 tmp 知识库
            with open(self.know_p, encoding="utf-8") as f:
                mem = json.load(f)
            m_load.return_value = mem
            gga._promote_confirmed("test", self.tmp)
            # save 的知识库: 待确认.人物 应清掉 シックス先生
            saved = m_save.call_args[0][1]
            pend = saved.get("待确认", {})
            self.assertNotIn("シックス先生", pend.get("人物", {}))
            self.assertIn("别的不相关", pend.get("术语", {}))


if __name__ == "__main__":
    unittest.main()
