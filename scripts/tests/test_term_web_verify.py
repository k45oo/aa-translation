# -*- coding: utf-8 -*-
"""候选联网核名（scripts/common/term_web_verify.py）单元测试。

运行：.venv/Scripts/python.exe -m unittest scripts.tests.test_term_web_verify -v
口径（方案 doc/方案归档/候选联网核名与出处高亮方案_2026-10-15.md）：
- 来源分层（官方简中 > 中文维基 > 萌娘 > 其它）与中文名判据；
- **模型只能引用证据**：不在证据里的译名/出处/简称一律丢弃（防幻觉）；
- 状态判定：出处与本作 IP 不符、或有多源不同字面 → `多源冲突`（绝不自动确认）；
- 缓存 `doc/术语检索缓存.json`：人工定名条目永不覆盖、逐词可续；
- 无 `TAVILY_API_KEY` → 整体跳过（候选生成不受影响）。
零依赖（unittest + 临时目录），不联网。
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts", "common"))
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts", "works"))

import term_web_verify as M      # noqa: E402


def ev(title, url, content=""):
    return {"title": title, "url": url, "content": content}


class TestTierAndNames(unittest.TestCase):
    def test_来源分层(self):
        self.assertEqual(M.tier_of("https://zh.moegirl.org.cn/杏目"), 3)
        self.assertEqual(M.tier_of("https://zh.wikipedia.org/wiki/杏目"), 2)
        self.assertEqual(M.tier_of("https://wiki.biligame.com/umamusume/杏目"), 1)
        self.assertEqual(M.tier_of("https://dic.pixiv.net/a/アーモンドアイ"), 0)
        self.assertEqual(M.tier_of(""), 0)

    def test_中文名判据(self):
        self.assertTrue(M.is_cn_name("杏目"))
        self.assertTrue(M.is_cn_name("玛雅诺冲锋号"))
        self.assertFalse(M.is_cn_name("アーモンドアイ"))     # 有假名
        self.assertFalse(M.is_cn_name("" ))
        self.assertFalse(M.is_cn_name("あ" * 25))            # 过长

    def test_简称判据_不含平假名(self):
        self.assertTrue(M.is_alias_like("マヤ"))
        self.assertTrue(M.is_alias_like("ビッグE"))
        for bad in ("をしましたが", "である", "が存在します", "の", ""):
            self.assertFalse(M.is_alias_like(bad), bad)


class TestExtract(unittest.TestCase):
    def test_出处从括号与引号抽(self):
        evidence = [
            ev("アーモンドアイ(ウマ娘) - ピクシブ百科事典", "https://dic.pixiv.net/a/x"),
            ev("マヤ", "https://dic.pixiv.net/a/maya", "『ウマ娘 プリティーダービー』に登場するキャラクター"),
        ]
        c = M.extract_candidates("アーモンドアイ", evidence)
        self.assertIn("ウマ娘", {x["v"] for x in c["出处"]})

    def test_IP中文名被排除(self):
        """把 IP 自己的中文名当词条译名是实测事故（ダイヤモンド → 闪耀！优俊少女）。"""
        evidence = [ev("闪耀！优俊少女 - 哔哩哔哩", "https://wiki.biligame.com/umamusume/ダイヤモンド")]
        c = M.extract_candidates("ダイヤモンド", evidence, ip_zh="闪耀！优俊少女")
        self.assertEqual(c["译名"], [])
        c2 = M.extract_candidates("ダイヤモンド", evidence, ip_zh="")
        self.assertEqual([x["v"] for x in c2["译名"]], ["闪耀！优俊少女"])

    def test_话题守卫_与词无关的中文源不进译名候选(self):
        evidence = [ev("钻石（消歧义） - 维基百科", "https://zh.wikipedia.org/wiki/钻石", "本页是消歧义页")]
        c = M.extract_candidates("ダイヤモンド", evidence)
        self.assertEqual(c["译名"], [])                     # 证据里没有该日文词 → 不收

    def test_乱页名被过滤(self):
        evidence = [ev("一覧 - 萌娘百科", "https://zh.moegirl.org.cn/一覧", "ダイヤモンド 一覧")]
        c = M.extract_candidates("ダイヤモンド", evidence)
        self.assertEqual(c["译名"], [])


class TestValidatePick(unittest.TestCase):
    def setUp(self):
        self.ev = [
            ev("アーモンドアイ(ウマ娘) - ピクシブ百科事典", "https://dic.pixiv.net/a/a"),
            ev("杏目 - 维基百科", "https://zh.wikipedia.org/wiki/杏目", "杏目（日語：アーモンドアイ）是日本賽馬"),
        ]
        self.cds = M.extract_candidates("アーモンドアイ", self.ev)

    def test_证据里的名字通过(self):
        pick = {"出处": "ウマ娘", "译名": "杏目", "依据": 1, "简称": []}
        out = M.validate_pick(pick, self.cds, evidence=self.ev, work_ip="ウマ娘")
        self.assertEqual((out["出处"], out["译名"]), ("ウマ娘", "杏目"))

    def test_证据里没有的译名被丢弃(self):
        pick = {"出处": "ウマ娘", "译名": "杏仁眼", "依据": 1}       # 模型自己造的
        out = M.validate_pick(pick, self.cds, evidence=self.ev, work_ip="ウマ娘")
        self.assertIsNone(out["译名"])

    def test_假名译名被丢弃(self):
        pick = {"出处": "ウマ娘", "译名": "アーモンドアイ"}
        out = M.validate_pick(pick, self.cds, evidence=self.ev)
        self.assertIsNone(out["译名"])

    def test_出处不在证据也不在候选则丢弃(self):
        pick = {"出处": "某不存在的作品"}
        out = M.validate_pick(pick, self.cds, evidence=self.ev, work_ip="ウマ娘")
        self.assertIsNone(out["出处"])

    def test_简称需在证据里且像名字(self):
        ev2 = list(self.ev) + [ev("マヤノトップガン(ウマ娘)", "https://dic.pixiv.net/a/m",
                                  "愛称はマヤ。をしましたが である")]
        cds = M.extract_candidates("マヤノトップガン", ev2)
        pick = {"出处": "ウマ娘", "译名": None, "简称": ["マヤ", "をしましたが"]}
        out = M.validate_pick(pick, cds, evidence=ev2, work_ip="ウマ娘")
        self.assertEqual(out["简称"], ["マヤ"])


class TestDecide(unittest.TestCase):
    def _cds(self, names):
        return {"出处": [{"v": "ウマ娘", "url": "u", "tier": 0, "ev": 0}],
                "译名": [{"v": n, "url": "u", "tier": 2, "ev": 0} for n in names], "简称": []}

    def test_单源一致(self):
        st, nm = M.decide_status("アーモンドアイ", {"出处": "ウマ娘", "译名": "杏目"},
                                 self._cds(["杏目"]), work_ip="ウマ娘 プリティーダービー")
        self.assertEqual((st, nm), ("一致", "杏目"))

    def test_多源不同字面即冲突(self):
        st, nm = M.decide_status("x", {"出处": "ウマ娘", "译名": "甲"}, self._cds(["甲", "乙"]),
                                 work_ip="ウマ娘")
        self.assertEqual(st, "多源冲突")
        self.assertEqual(nm, "甲")

    def test_出处与本作IP不符即冲突(self):
        """实测：トップガン 无锚定 → 壮志凌云/出处=映画（另一个 IP 的正确答案）必须挡住。"""
        cds = {"出处": [{"v": "映画", "url": "u", "tier": 0, "ev": 0}],
               "译名": [{"v": "壮志凌云", "url": "u", "tier": 2, "ev": 0}], "简称": []}
        st, nm = M.decide_status("トップガン", {"出处": "映画", "译名": "壮志凌云"}, cds,
                                 work_ip="ウマ娘")
        self.assertEqual(st, "多源冲突")

    def test_无锚定时不因出处判冲突(self):
        st, _ = M.decide_status("x", {"出处": "映画", "译名": "壮志凌云"}, self._cds(["壮志凌云"]))
        self.assertEqual(st, "一致")

    def test_什么都没有即未命中(self):
        st, nm = M.decide_status("x", {}, {"出处": [], "译名": [], "简称": []}, work_ip="ウマ娘")
        self.assertEqual((st, nm), ("未命中", ""))

    def test_ip匹配_包含关系(self):
        cds = {"出处": [{"v": "ウマ娘", "url": "u", "tier": 0, "ev": 0}], "译名": [], "简称": []}
        self.assertTrue(M.ip_matches("ウマ娘 プリティーダービー", cds, {}))
        self.assertFalse(M.ip_matches("ウマ娘", {"出处": [], "译名": [], "简称": []}, {"出处": "映画"}))


class TestCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = os.path.join(self.tmp.name, "cache.json")

    def test_往返与原子写(self):
        c = {"词": {"a": {"状态": "一致", "译名": "甲"}}}
        M.save_cache(c, self.p)
        self.assertTrue(os.path.exists(self.p))
        self.assertFalse(os.path.exists(self.p + ".tmp"))
        d = M.load_cache(self.p)
        self.assertEqual(d["词"]["a"]["译名"], "甲")
        self.assertEqual(d["schema"], M.SCHEMA)

    def test_坏缓存当空(self):
        open(self.p, "w", encoding="utf-8").write("{坏")
        self.assertEqual(M.load_cache(self.p)["词"], {})

    def test_schema不符当空(self):
        json.dump({"schema": "老版本", "词": {"a": 1}}, open(self.p, "w", encoding="utf-8"))
        self.assertEqual(M.load_cache(self.p)["词"], {})

    def test_人工定名跳过(self):
        self.assertTrue(M.cache_skip({"状态": "人工定名"}))
        self.assertTrue(M.cache_skip({"状态": "一致", "人工定名": True}))
        self.assertFalse(M.cache_skip({"状态": "一致"}))


class TestApply(unittest.TestCase):
    def test_一致才自动确认(self):
        c = {"term": "アーモンドアイ", "translation": "", "type": "专有名词"}
        notes = M.apply_entry(c, {"状态": "一致", "出处": "ウマ娘", "译名": "杏目",
                                  "证据": [{"title": "t", "url": "u"}]})
        self.assertEqual(c["translation"], "杏目")
        self.assertEqual(c["作品"], "ウマ娘")
        self.assertTrue(c["确认"])
        self.assertIn("检索证据", c)
        self.assertIn("确认", notes)

    def test_冲突不确认_且预填建议译名(self):
        c = {"term": "x", "translation": "", "type": "角色"}
        M.apply_entry(c, {"状态": "多源冲突", "出处": "映画", "译名": "壮志凌云",
                          "证据": [{"title": "t", "url": "u"}]})
        self.assertEqual(c["translation"], "壮志凌云")       # 预填建议
        self.assertFalse(c.get("确认"))                       # 但不自动确认
        self.assertEqual(c["检索证据"]["状态"], "多源冲突")

    def test_不覆盖已有人工译名(self):
        c = {"term": "x", "translation": "人工定的名", "type": "角色"}
        M.apply_entry(c, {"状态": "多源冲突", "译名": "模型建议"})
        self.assertEqual(c["translation"], "人工定的名")

    def test_不覆盖已有作品字段(self):
        c = {"term": "x", "translation": "", "作品": "人工填的IP"}
        M.apply_entry(c, {"状态": "一致", "出处": "ウマ娘", "译名": "杏目"})
        self.assertEqual(c["作品"], "人工填的IP")

    def test_一致时保留原译名备查(self):
        c = {"term": "トップガン", "translation": "壮志凌云"}
        M.apply_entry(c, {"状态": "一致", "出处": "ウマ娘", "译名": "玛雅诺冲锋号"})
        self.assertEqual(c["translation"], "玛雅诺冲锋号")
        self.assertEqual(c["检索证据"]["原译名"], "壮志凌云")

    def test_简称提示不自动落别名(self):
        cands = [{"term": "マヤ", "translation": ""}, {"term": "マヤノトップガン"}]
        n = M.mark_aliases(cands, {"词": {"マヤノトップガン": {"简称": ["マヤ"]}}})
        self.assertEqual(n, 1)
        self.assertEqual(cands[0]["简称_of"], "マヤノトップガン")
        self.assertNotIn("别名_of", cands[0])                 # 决策 5：不自动落别名


class TestKeyMissing(unittest.TestCase):
    def test_无key整体跳过(self):
        # 不断言具体作品：临时 work_dir + 候选文件（发行包里 registry 只有示例作品，
        # 写死 jinrou 会 `未知作品` 直接 SystemExit —— 与「无 key 跳过」语义无关）
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "glossary_candidates.json"), "w", encoding="utf-8") as f:
            f.write('{"ai_候选": []}')
        with mock.patch.dict(os.environ, {"TAVILY_API_KEY": ""}, clear=False):
            with mock.patch.object(M, "tavily_key", return_value=""), \
                 mock.patch.object(M.registry, "get", return_value={"work_dir": d}):
                r = M.verify_work("some_work")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "无 TAVILY_API_KEY")


class TestFailureNotCached(unittest.TestCase):
    """限流/网络失败**不得**被记成「未命中」——那会让该词永远不再重查（实测 432 限流）。"""

    def test_检索全失败_不写缓存(self):
        with mock.patch.object(M, "tavily_key", return_value="k"), \
             mock.patch.object(M, "collect_evidence", return_value=([], 4)), \
             mock.patch.object(M, "load_cache", return_value={"词": {}}), \
             mock.patch.object(M, "save_cache") as sv, \
             mock.patch.object(M.registry, "get", return_value={
                 "work_dir": self._wd(), "aa_dir": self._wd(), "title": "t"}):
            r = M.verify_work("w")
        self.assertEqual(r["检索失败"], 1)
        self.assertEqual(r["处理"], 0)
        for call in sv.call_args_list:                     # 只允许写「作品IP」不涉及词的调用
            self.assertNotIn("w", (call.args[0].get("词") or {}))

    def test_tavily失败返回None而非空表(self):
        import urllib.error

        class _Boom(urllib.error.HTTPError):
            def __init__(self):
                super().__init__("u", 432, "rate", {}, None)

        with mock.patch.object(urllib.request, "build_opener") as bo, \
             mock.patch.object(M.aa_config, "proxy_opener", return_value=None):
            bo.return_value.open.side_effect = _Boom()
            self.assertIsNone(M.tavily_search("q", "k", retries=0))

    def _wd(self):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        with open(os.path.join(d, "glossary_candidates.json"), "w", encoding="utf-8") as f:
            json.dump({"ai_候选": [{"term": "x", "translation": ""}]}, f, ensure_ascii=False)
        return d


if __name__ == "__main__":
    unittest.main()
