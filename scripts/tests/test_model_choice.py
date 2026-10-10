# -*- coding: utf-8 -*-
"""工作台「付费链模型多选」+ 预估随选择变（2026-09-22）。

覆盖四层：
  ① aa_config.chain_catalog / refine_catalog —— 界面端点清单 + 默认勾选集
  ② aa_config.backend_chain 的 `AA_CHAIN` 过滤（保持链序 / 全不可用回退整链）
  ③ main._chain_env —— 勾选集=默认集不写 env（旧行为不变）；不同则 AA_CHAIN / AA_REFINE_BACKEND
  ④ estimate.estimate(models=...) —— 金额按勾选的主力端点（第一个 cache=true）算，链内区间
零依赖（unittest + 临时目录），不起服务器、不调 API。
运行：.venv/Scripts/python.exe -m unittest scripts.tests.test_model_choice -v
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
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts", "common"))
sys.path.insert(0, os.path.join(_REPO_ROOT, "aa_web_app"))

import aa_config as AC  # noqa: E402
import aa_env  # noqa: E402
from aa_web_app import main as M  # noqa: E402
from _work_guard import repo_only  # noqa: E402 （发行包内跳过生成者专属用例）


class _IsolatedEnv(unittest.TestCase):
    """夹具与操作者 .env 隔离：把 `AA_ENV_PATH` 指向空临时文件。

    2026-10-10: 工作台设置页会把模型勾选写进**仓库根 .env**（`AA_CHAIN` / `AA_REFINE_BACKEND`），
    `aa_config` import 时的 `load_dotenv()` 又把它灌进 `os.environ`；且同文件里跑设置保存的用例会
    `load_dotenv(force=True)` **重新灌回**（`os.environ.pop` 挡不住）→ 断言「默认 / 整条链」的用例假红。
    隔离后这些用例只看 TOML + 用例自己 patch 的 env。
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="aa_chain_")
        self._envp = os.path.join(self._tmp, ".env")
        open(self._envp, "w").close()
        self._old = {k: os.environ.get(k) for k in ("AA_ENV_PATH", "AA_ENV", "AA_CHAIN",
                                                    "AA_REFINE_BACKEND")}
        os.environ.pop("AA_ENV", None)
        os.environ.pop("AA_CHAIN", None)
        os.environ.pop("AA_REFINE_BACKEND", None)
        os.environ["AA_ENV_PATH"] = self._envp
        aa_env.load(force=True)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        aa_env.load(force=True)
        shutil.rmtree(self._tmp, ignore_errors=True)


class CatalogTest(unittest.TestCase):
    @repo_only("断言生成者的模型目录（包里默认链不同）")
    def test_translate_catalog_shape(self):
        items, dflt = AC.chain_catalog(False)
        self.assertTrue(items, "normal 链不该为空")
        for it in items:
            for k in ("backend", "model", "id", "label", "cache", "r18", "fallback_only",
                      "available", "rate"):
                self.assertIn(k, it)
        self.assertTrue(dflt)
        self.assertTrue(set(dflt) <= {x["id"] for x in items}, "默认集 = 条目 id")

    def test_refine_follows_paid_chain(self):
        """2026-09-22「与付费链一致」：默认 follow_default=true → 校对清单 == 付费链清单。"""
        for r18 in (False, True):
            a_items, a_def = AC.chain_catalog(r18)
            b_items, b_def = AC.refine_catalog(r18)
            self.assertEqual([x["backend"] for x in a_items], [x["backend"] for x in b_items],
                             "校对链端点/顺序必须与付费链一致")
            self.assertEqual([x["model"] for x in a_items], [x["model"] for x in b_items],
                             "校对链模型也必须随付费链")
            self.assertEqual(a_def, b_def)

    def test_default_excludes_fallback_only_and_missing_key(self):
        items, dflt = AC.chain_catalog(False)
        for it in items:
            if it["fallback_only"] or not it["available"]:
                self.assertNotIn(it["id"], dflt,
                                 "兜底端点/缺 key 的端点不该默认勾选")

    def test_catalog_keeps_unavailable_entries(self):
        """界面要把缺 key 的端点也列出来（标灰），故 catalog 不做可用性过滤。"""
        items, _d = AC.chain_catalog(False)
        cfg = AC.load().get("backend", {})
        table = AC.load().get("default", {}).get("normal_backends", [])
        self.assertEqual([x["backend"] for x in items], [t.get("backend") for t in table])
        for it in items:
            self.assertEqual(it["available"], (not cfg.get(it["backend"], {}).get("key_env"))
                             or AC._env_key_exists(cfg.get(it["backend"], {}).get("key_env", "")))

    def test_r18_catalog_drops_r18_unsafe(self):
        """R18 链不该出现 `r18=false` 的端点（如 mimo）。"""
        items, _d = AC.chain_catalog(True)
        for it in items:
            self.assertTrue(it["r18"], "r18 链含不安全端点: " + it["backend"])

    @repo_only("断言生成者的模型目录（包里默认链不同）")
    def test_refine_catalog(self):
        items, dflt = AC.refine_catalog()
        self.assertTrue(items)
        self.assertTrue(dflt)
        self.assertIn(items[0]["id"], dflt)


class ChainOverrideTest(_IsolatedEnv):
    """AA_CHAIN=后端名,后端名 → backend_chain 只留这些（保持 TOML 链序）。"""

    def _names(self):
        return [b for b, _m in AC.backend_chain(False)]

    @repo_only("断言生成者的链条目 id 选择")
    def test_single_pick(self):
        names = self._names()
        pick = names[0]
        with mock.patch.dict(os.environ, {"AA_CHAIN": pick}):
            got = AC.backend_chain(False)
        self.assertEqual([b for b, _m in got], [pick])

    def test_order_follows_toml_not_input(self):
        names = self._names()
        if len(names) < 2:
            self.skipTest("链上不足两个可用端点")
        rev = list(reversed(names))
        with mock.patch.dict(os.environ, {"AA_CHAIN": ",".join(rev)}):
            got = [b for b, _m in AC.backend_chain(False)]
        self.assertEqual(got, names)

    def test_unknown_name_falls_back_to_full_chain(self):
        full = self._names()
        with mock.patch.dict(os.environ, {"AA_CHAIN": "no-such-backend"}):
            got = self._names()
        self.assertEqual(got, full, "全不可用时应回退整条链，不静默断链")

    def test_blank_means_full_chain(self):
        full = self._names()
        for val in ("", "   "):
            with mock.patch.dict(os.environ, {"AA_CHAIN": val}):
                self.assertEqual(self._names(), full)

    def test_no_env_means_full_chain(self):
        full = self._names()
        os.environ.pop("AA_CHAIN", None)
        self.assertEqual(self._names(), full)


class ChainEnvMappingTest(_IsolatedEnv):
    """main._chain_env：勾选集 == 默认集 → {}（整条链，含兜底端点，与旧行为一致）。"""

    def setUp(self):
        self._cfg = M.cfg_of
        M.cfg_of = lambda w: {"r18": False}

    def tearDown(self):
        M.cfg_of = self._cfg

    def test_empty_selection_no_env(self):
        self.assertEqual(M._chain_env("w", {}, "translate"), {})
        self.assertEqual(M._chain_env("w", {"models": []}, "translate"), {})

    def test_default_selection_no_env(self):
        _items, dflt = AC.chain_catalog(False)
        env = M._chain_env("w", {"models": dflt}, "translate")
        self.assertEqual(env, {}, "默认勾选集不该写 env（否则会摘掉兜底端点）")

    def test_subset_writes_aa_chain(self):
        names = [b for b, _m in AC.backend_chain(False)]
        if not names:
            self.skipTest("无可用端点")
        env = M._chain_env("w", {"models": [names[0]]}, "translate")
        # 校对链已跟随付费链（2026-09-22）→ 翻译任务同时写 AA_REFINE_BACKEND
        self.assertEqual(env, {"AA_CHAIN": names[0], "AA_REFINE_BACKEND": names[0]})

    def test_proofread_writes_refine_backend(self):
        items, dflt = AC.refine_catalog(False)
        if len(dflt) < 2:
            self.skipTest("校对链不足两个端点")
        env = M._chain_env("w", {"models": [dflt[0]]}, "proofread")
        self.assertEqual(env, {"AA_REFINE_BACKEND": dflt[0]})

    def test_proofread_default_no_env(self):
        _items, dflt = AC.refine_catalog(False)
        self.assertEqual(M._chain_env("w", {"models": dflt}, "proofread"), {})

    def test_refine_catalog_default_equals_paid_default(self):
        """校对页勾选集 == 付费链默认集 → 不写 env（翻译任务也同源）。"""
        _i, dflt = AC.refine_catalog(False)
        self.assertEqual(M._chain_env("w", {"models": dflt}, "translate"), {})


class EntryIdTest(unittest.TestCase):
    """链条目 id（2026-10-08 阶段 2）：同后端多条目可分别勾选；旧式后端名保持兼容。"""

    def _dup_backend(self):
        items, _d = AC.chain_catalog(False)
        by = {}
        for it in items:
            by.setdefault(it["backend"], []).append(it)
        return [(b, v) for b, v in by.items() if len(v) > 1]

    def test_entry_id_形态(self):
        items, _d = AC.chain_catalog(False)
        for it in items:
            self.assertEqual(it["id"], "%s|%s" % (it["backend"], it["model"]))

    def test_同后端两条目按id单选(self):
        dups = self._dup_backend()
        if not dups:
            self.skipTest("链上无同后端多条目")
        b, entries = dups[0]
        with mock.patch.dict(os.environ, {"AA_CHAIN": entries[0]["id"]}):
            got = AC.backend_chain(False)
        self.assertEqual(len(got), 1, "条目 id 应只命中该条目")
        self.assertEqual(got[0][1], entries[0]["model"])

    def test_旧式后端名等价于该后端全部条目(self):
        dups = self._dup_backend()
        if not dups:
            self.skipTest("链上无同后端多条目")
        b, entries = dups[0]
        with mock.patch.dict(os.environ, {"AA_CHAIN": b}):
            got = AC.backend_chain(False)
        self.assertEqual(len(got), len(entries))

    @repo_only("断言生成者的链条目表")
    def test_未识别选择回退整链(self):
        with mock.patch.dict(os.environ, {"AA_CHAIN": ""}):
            full = AC.backend_chain(False)
        with mock.patch.dict(os.environ, {"AA_CHAIN": "nope|x"}):
            got = AC.backend_chain(False)
        self.assertTrue(full)
        self.assertEqual([b for b, _m in got], [b for b, _m in full],
                         "全匹配不上 → 回退整条链")


class ChainSelectionTest(_IsolatedEnv):
    """`aa_config.chain_selection` —— 设置页保存的链 = 控制台多选的默认勾选（2026-10-10）。

    背景：控制台多选此前一律按 TOML 链的「默认集」勾（= 整条链），设置页里改过的选择
    在控制台看不到 → 用户每次开任务都要手点一遍同样的取消勾选。
    """

    def test_未设env时等于默认集(self):
        items, dflt = AC.chain_catalog(False)
        self.assertTrue(dflt, "默认集不该为空")
        sel = AC.chain_selection("AA_CHAIN", items, dflt)
        self.assertEqual(sel["selected"], dflt)
        self.assertFalse(sel["custom"])

    def test_条目id子集(self):
        items, dflt = AC.chain_catalog(False)
        if len(dflt) < 2:
            self.skipTest("可用端点不足两个")
        os.environ["AA_CHAIN"] = ",".join(dflt[:2])
        sel = AC.chain_selection("AA_CHAIN", items, dflt)
        self.assertEqual(sel["selected"], dflt[:2], "按 env 顺序 = 控制台勾选集")
        self.assertTrue(sel["custom"])

    def test_旧后端名等价于该后端全部条目(self):
        items, dflt = AC.chain_catalog(False)
        os.environ["AA_CHAIN"] = items[0]["backend"]
        sel = AC.chain_selection("AA_CHAIN", items, dflt)
        same = [it["id"] for it in items if it["backend"] == items[0]["backend"]]
        self.assertEqual(sel["selected"], same)
        self.assertTrue(sel["custom"])

    def test_认不出的项回默认集(self):
        items, dflt = AC.chain_catalog(False)
        os.environ["AA_CHAIN"] = "nope|x,yet|another"
        sel = AC.chain_selection("AA_CHAIN", items, dflt)
        self.assertEqual(sel["selected"], dflt)
        self.assertFalse(sel["custom"], "全认不出 = 没自定义过")

    def test_部分认不出只保留认得的(self):
        items, dflt = AC.chain_catalog(False)
        os.environ["AA_CHAIN"] = dflt[0] + ",nope|x"
        sel = AC.chain_selection("AA_CHAIN", items, dflt)
        self.assertEqual(sel["selected"], [dflt[0]])
        self.assertTrue(sel["custom"])


class WorkModelsSelectionTest(_IsolatedEnv):
    """/api/work/<id>/models：`selected` = 设置页保存的链；`head` 按**生效链**算。"""

    def setUp(self):
        super().setUp()
        self._cfg = M.cfg_of
        M.cfg_of = lambda w: {"r18": False}

    def tearDown(self):
        M.cfg_of = self._cfg
        super().tearDown()

    def _j(self, type="translate"):
        j = M.api_work_models("w", type=type)
        self.assertTrue(j.get("ok"), j)
        return j

    @repo_only("断言生成者的付费链")
    def test_勾选集来自AA_CHAIN而非默认集(self):
        items, dflt = AC.chain_catalog(False)
        if len(dflt) < 2:
            self.skipTest("可用端点不足两个")
        os.environ["AA_CHAIN"] = dflt[1]
        j = self._j()
        self.assertEqual(j["default"], dflt, "default 仍是整条链（界面「全勾回默认」口径）")
        self.assertEqual(j["selected"], [dflt[1]], "默认勾选 = 设置页保存的选择")
        self.assertTrue(j["custom"])
        want = next(it for it in items if it["id"] == dflt[1])["label"]
        self.assertEqual(j["head"], want, "主力端点 = 生效链的头一个端点")

    def test_未设AA_CHAIN时勾选集等于默认集(self):
        os.environ.pop("AA_CHAIN", None)
        j = self._j()
        self.assertEqual(j["selected"], j["default"])
        self.assertFalse(j["custom"])
        self.assertTrue(j["head"])

    @repo_only("断言生成者的付费链")
    def test_链首仍可缓存端点时主力为它(self):
        items, dflt = AC.chain_catalog(False)
        os.environ["AA_CHAIN"] = dflt[0]
        j = self._j()
        self.assertEqual(j["selected"], [dflt[0]])
        self.assertEqual(j["head"], next(it for it in items if it["id"] == dflt[0])["label"])

    def test_校对端点读AA_REFINE_BACKEND(self):
        items, dflt = AC.chain_catalog(False)          # 校对链跟随付费链 → 同一批条目
        os.environ.pop("AA_CHAIN", None)
        os.environ["AA_REFINE_BACKEND"] = dflt[0]
        j = self._j("proofread")
        self.assertEqual(j["selected"], [dflt[0]])
        self.assertTrue(j["custom"])
        del os.environ["AA_REFINE_BACKEND"]

    def test_选中项不在本作品链上时回默认集(self):
        """给 R18 作品选了不接 R18 的端点（交集为空）→ 界面显示整条 r18 链的默认勾选。"""
        r18_chain, r18_dflt = AC.refine_catalog(True)
        norm_ids = {it["id"] for it in AC.chain_catalog(False)[0]}
        outside = sorted(norm_ids - {it["id"] for it in r18_chain})
        if not outside:
            self.skipTest("normal 链条目全在 r18 链上，构造不出链外选择")
        os.environ["AA_CHAIN"] = outside[0]
        M.cfg_of = lambda w: {"r18": True}
        j = self._j()                                  # type=translate + r18 作品 → r18 链
        self.assertEqual(j["default"], r18_dflt)
        self.assertEqual(j["selected"], r18_dflt)
        self.assertFalse(j["custom"])


class _EstFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aa_model_choice_")
        self.aa_dir = os.path.join(self.tmp, "aa")
        self.tr_dir = os.path.join(self.tmp, "tr")
        self.wk = os.path.join(self.tmp, "work")
        for d in (self.aa_dir, self.tr_dir, self.wk):
            os.makedirs(d)
        from aa_web_app import estimate as EST
        self.EST = EST
        self._old = EST.cfg_of
        EST.cfg_of = lambda w: {"aa_dir": self.aa_dir, "out_dir": self.tr_dir,
                                "work_dir": self.wk, "name_style": "entry_eid"}
        EST._SRC_CACHE.clear()
        EST._SKIP_CACHE.clear()
        with open(os.path.join(self.aa_dir, "entry_1.txt"), "w",
                  encoding="utf-8", newline="\r\n") as f:
            f.write("あ" * 1000)

    def tearDown(self):
        self.EST.cfg_of = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)


class EstimateModelsTest(_EstFixture):
    @repo_only("断言生成者的模型链/章内 pin 目标")
    def test_default_uses_chain_pin_target(self):
        r = self.EST.estimate("w", "translate", ["1"])
        self.assertTrue(r["ok"])
        _items, dflt = AC.chain_catalog(False)
        self.assertEqual(r["models_sel"], dflt)
        head = [x for x in AC.chain_catalog(False)[0]
                if x["id"] in dflt and x["cache"]]
        if head:
            self.assertEqual(r["rate"], head[0]["label"],
                             "默认主力端点 = 链上第一个 cache=true（章内 pin 目标）")

    def test_selection_changes_priced_endpoint(self):
        items = [x for x in AC.chain_catalog(False)[0]
                 if x["available"] and x["rate"].get("miss") is not None and x["rate"].get("out") is not None]
        if len(items) < 2:
            self.skipTest("链上不足两个已定价端点")
        # 2026-10-08 阶段 2: 按**条目 id** 勾选 —— 同后端多条目（deepinfra 两条）也能单选
        a = self.EST.estimate("w", "translate", ["1"], models=[items[0]["id"]])
        b = self.EST.estimate("w", "translate", ["1"], models=[items[-1]["id"]])
        self.assertEqual(a["models_sel"], [items[0]["id"]])
        self.assertEqual(b["models_sel"], [items[-1]["id"]])
        self.assertEqual(a["rate"], items[0]["label"])
        self.assertEqual(b["rate"], items[-1]["label"])
        self.assertGreater(a["cost"], 0)
        self.assertGreater(b["cost"], 0)
        # 单价不同 → 金额随选择变（同 token 口径）
        if items[0]["rate"] != items[-1]["rate"]:
            self.assertNotEqual(round(a["cost"], 4), round(b["cost"], 4),
                                "选了不同单价的端点，金额必须跟着变")

    @repo_only("断言生成者的模型链选择集")
    def test_range_over_selection(self):
        names = [b for b, _m in AC.backend_chain(False)]
        r = self.EST.estimate("w", "translate", ["1"], models=names)
        self.assertLessEqual(r["cost_min"], r["cost_max"])
        single = self.EST.estimate("w", "translate", ["1"], models=[names[0]])
        self.assertEqual(single["cost_min"], single["cost_max"])

    def test_unknown_selection_falls_back_to_chain(self):
        r = self.EST.estimate("w", "translate", ["1"], models=["nope"])
        self.assertTrue(r["ok"])
        self.assertTrue(r["models_sel"], "全不可用时应回退整条链")

    def test_free_mode_zero_cost_any_selection(self):
        names = [b for b, _m in AC.backend_chain(False)]
        r = self.EST.estimate("w", "translate", ["1"], free=True, models=names)
        self.assertEqual((r["cost"], r["cost_min"], r["cost_max"]), (0.0, 0.0, 0.0))
        self.assertTrue(r["free"])

    @repo_only("断言生成者的模型链/校对链")
    def test_proofread_models_use_refine_chain(self):
        items, dflt = AC.refine_catalog()
        with open(os.path.join(self.tr_dir, "entry_1_汉化.txt"), "w",
                  encoding="utf-8", newline="\r\n") as f:
            f.write("啊" * 1000)
        r = self.EST.estimate("w", "proofread", ["1"], models=dflt)
        self.assertTrue(r["ok"])
        self.assertEqual(r["models_sel"], dflt)
        self.assertEqual([x for x in items if x["id"] == dflt[0]][0]["label"], r["rate"])


if __name__ == "__main__":
    unittest.main()
