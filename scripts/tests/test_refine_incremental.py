# -*- coding: utf-8 -*-
"""refine_aa 章级增量 ckpt + 单次调用预算（2026-09-15 提速三件套）单元测试。

覆盖：
  · 链来源优先级 env > TOML [refine_aa].backends > 代码默认
  · 单次调用预算/重试 env > TOML > 默认（25s / 2 次）
  · ckpt 指纹：命中跳过 / 汉化改动失效 / 原文改动失效 / done=false 不跳
  · process_work：第二次 --write 零调用跳过；--force 强制重校
  · _call_glm 把 timeout/retries 传给 call_deepseek

运行：.venv/Scripts/python.exe -m unittest scripts.tests.test_refine_incremental -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, ".."))
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "fix"),
           os.path.join(_SCRIPTS, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import refine_aa as R          # noqa: E402

SRC_HEAD = "# テスト章"
SRC_POST = "1 ：作者 ：2020/01/01(水) 00:00:00 ID:abcdef"
SRC_DIAL = "　　こんにちは、今日はいい天気ですね"


class TestChainSource(unittest.TestCase):
    def test_parse_garbage_falls_back_to_default_chain(self):
        self.assertEqual(R._parse_refine_chain("zzz"), ["deepseek", "deepinfra"])
        self.assertEqual(R._parse_refine_chain(None), ["deepseek", "deepinfra"])

    def test_env_beats_toml_and_default(self):
        old = os.environ.get("AA_REFINE_BACKEND")
        os.environ["AA_REFINE_BACKEND"] = "glm"
        try:
            self.assertEqual(R._refine_chain_spec(), "glm")
            self.assertEqual(R._parse_refine_chain(R._refine_chain_spec()), ["deepinfra"])
        finally:
            if old is None:
                os.environ.pop("AA_REFINE_BACKEND", None)
            else:
                os.environ["AA_REFINE_BACKEND"] = old

    def test_default_chain_is_deepseek_primary_with_fallback(self):
        """无 env 时的**代码兜底链**（TOML 付费链读不到时的最后一级）= deepseek,deepinfra。

        2026-10-10: 运行时链**跟随付费链**（`_set_chain_for_work` 会把 `_REFINE_CHAIN` 改成付费链，
        链首已是 opencode_go）——原先直接断言 `_REFINE_CHAIN` 的初值，跨用例顺序一变就红；
        改为断言兜底常量，并复位模块全局（本用例不改运行态）。
        """
        if os.environ.get("AA_REFINE_BACKEND"):
            self.skipTest("env 覆盖了链, 该用例断言默认值")
        dflt = R._parse_refine_entries(R.DEFAULT_REFINE_CHAIN)
        self.assertEqual(dflt[0][0], "deepseek")
        self.assertEqual(len(dflt), 2)
        R._REFINE_CHAIN = [b for b, _m in R._REFINE_ENTRIES]      # 复位被别的用例改过的全局

    def test_call_budget_env_overrides(self):
        old_t, old_r = os.environ.get("AA_REFINE_CALL_TIMEOUT"), os.environ.get("AA_REFINE_CALL_RETRY")
        os.environ["AA_REFINE_CALL_TIMEOUT"] = "7"
        os.environ["AA_REFINE_CALL_RETRY"] = "1"
        try:
            self.assertEqual(R._call_timeout(), 7)
            self.assertEqual(R._call_retry(), 1)
        finally:
            for k, v in (("AA_REFINE_CALL_TIMEOUT", old_t), ("AA_REFINE_CALL_RETRY", old_r)):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        # 默认 = TOML [refine_aa]（25s / 2 次）；TOML 缺失时回退代码默认
        self.assertGreaterEqual(R._call_timeout(), 1)
        self.assertGreaterEqual(R._call_retry(), 1)

    def test_budget_falls_back_to_code_default_without_toml(self):
        import aa_config as AC
        _orig = AC.get
        AC.get = lambda *a, **k: None
        try:
            self.assertEqual(R._call_timeout(), 25)
            self.assertEqual(R._call_retry(), 2)
        finally:
            AC.get = _orig


class TestCallGlmKwargs(unittest.TestCase):
    def test_timeout_and_retries_passed_to_deepseek(self):
        import aa_backend as AB
        seen = {}

        def fake(system, body, **kw):
            seen.update(kw)
            return "译文"

        _orig = AB.call_deepseek
        AB.call_deepseek = fake
        _chain, _gloss = R._REFINE_CHAIN, R._GLOSS_HINT
        R._REFINE_CHAIN = ["deepseek"]
        R._GLOSS_HINT = ""
        try:
            self.assertEqual(R._call_glm("S", "B"), "译文")
            self.assertEqual(seen.get("timeout"), R._call_timeout())
            self.assertEqual(seen.get("retries"), R._call_retry())
        finally:
            AB.call_deepseek = _orig
            R._REFINE_CHAIN, R._GLOSS_HINT = _chain, _gloss


class TestCkptFingerprint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="refine_ckpt_")
        self.aa = os.path.join(self.tmp, "aa")
        self.out = os.path.join(self.tmp, "汉化")
        self.work = os.path.join(self.tmp, "work")
        for d in (self.aa, self.out, self.work):
            os.makedirs(d, exist_ok=True)
        self.base = "entry_1"
        self._write(os.path.join(self.aa, self.base + ".txt"), SRC_HEAD + "\n" + SRC_DIAL)
        self._write(os.path.join(self.out, self.base + "_汉化.txt"), SRC_HEAD + "\n" + SRC_DIAL)
        self.cfg = {"aa_dir": self.aa, "out_dir": self.out, "work_dir": self.work}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _write(p, s):
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(s)

    def test_missing_ckpt_not_ok(self):
        self.assertFalse(R._ckpt_ok(self.cfg, self.base))

    def test_save_then_ok(self):
        R._ckpt_save(self.cfg, self.base, {"partial_kana": 0}, [])
        self.assertTrue(R._ckpt_ok(self.cfg, self.base))
        p = R._ckpt_path(self.cfg, self.base)
        self.assertTrue(os.path.exists(p))
        self.assertTrue(json.load(open(p, encoding="utf-8"))["done"])

    def test_汉化改动使其失效(self):
        R._ckpt_save(self.cfg, self.base, {}, [])
        self._write(os.path.join(self.out, self.base + "_汉化.txt"), "# 首行\n人工改了一行")
        self.assertFalse(R._ckpt_ok(self.cfg, self.base))

    def test_原文改动使其失效(self):
        R._ckpt_save(self.cfg, self.base, {}, [])
        self._write(os.path.join(self.aa, self.base + ".txt"), "# 首行\n原文也改了")
        self.assertFalse(R._ckpt_ok(self.cfg, self.base))

    def test_done_false_not_ok(self):
        p = R._ckpt_path(self.cfg, self.base)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"done": False, "src_sha1": "x", "tr_sha1": "y"}, f)
        self.assertFalse(R._ckpt_ok(self.cfg, self.base))

    def test_corrupt_ckpt_not_ok(self):
        p = R._ckpt_path(self.cfg, self.base)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("{不是 json")
        self.assertFalse(R._ckpt_ok(self.cfg, self.base))


class TestProcessWorkIncremental(unittest.TestCase):
    """端到端：同一章第二次 --write 应零调用跳过；--force 强制重校。

    夹具刻意做成**不收敛章**（模型 mock 返回无中文的日文 → poly 路径 ok=False 不写盘，
    每轮都会重新调用模型）—— 这正是实盘最亏的场景（真・女神転生 每轮 112 次调用），
    也是"跳过"能被观测到的前提（收敛章第二轮本来就 0 调用，测不出差别）。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="refine_pw_")
        self.aa = os.path.join(self.tmp, "aa")
        self.out = os.path.join(self.tmp, "汉化")
        self.work = os.path.join(self.tmp, "work")
        for d in (self.aa, self.out, self.work, os.path.join(self.work, "line_meta_v2")):
            os.makedirs(d, exist_ok=True)
        self.base = "entry_1"
        body = "\n".join([SRC_HEAD, SRC_POST, SRC_DIAL, SRC_DIAL])
        with open(os.path.join(self.aa, self.base + ".txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        # 汉化 = 原文 → 两行台词判漏译（TR==SRC 强信号）
        with open(os.path.join(self.out, self.base + "_汉化.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        with open(os.path.join(self.work, "line_meta_v2", self.base + ".json"), "w", encoding="utf-8") as f:
            json.dump({}, f)
        self.cfg = {"aa_dir": self.aa, "out_dir": self.out, "work_dir": self.work,
                    "work_id": "test_work"}
        self._reg, self._call = R._reg_get, R._call_glm
        self.calls = []
        R._reg_get = lambda w: self.cfg
        # 无中文返回 → poly 判 zh_empty_or_copy(ok=False) → 不写盘、指纹不变、每轮重调
        # (注意：必须用**纯假名**——"翻訳"这类日文汉字在 CJK 区间内, 会被判成已翻译)
        R._call_glm = lambda system, body, **kw: (self.calls.append(body), "まだほんやくされていない")[1]

    def tearDown(self):
        R._reg_get, R._call_glm = self._reg, self._call
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_first_write_calls_and_saves_ckpt(self):
        res = R.process_work("test_work", eids=["1"], do_write=True)
        self.assertGreater(len(self.calls), 0, "首次校对应调用模型")
        self.assertFalse(res[0][1].get("skipped_ckpt"))
        self.assertTrue(R._ckpt_ok(self.cfg, self.base))
        d = json.load(open(R._ckpt_path(self.cfg, self.base), encoding="utf-8"))
        self.assertEqual(d["unfixed"], 2, "两条修不动的行应记 unfixed")

    def test_second_write_skips_with_zero_calls(self):
        R.process_work("test_work", eids=["1"], do_write=True)
        self.calls.clear()
        res = R.process_work("test_work", eids=["1"], do_write=True)
        self.assertEqual(len(self.calls), 0, "命中 ckpt 不应有任何模型调用")
        self.assertTrue(res[0][1].get("skipped_ckpt"))

    def test_force_ignores_ckpt(self):
        R.process_work("test_work", eids=["1"], do_write=True)
        self.calls.clear()
        res = R.process_work("test_work", eids=["1"], do_write=True, force=True)
        self.assertGreater(len(self.calls), 0, "--force 应强制重校")
        self.assertFalse(res[0][1].get("skipped_ckpt"))

    def test_no_write_never_skips(self):
        R.process_work("test_work", eids=["1"], do_write=True)
        self.assertTrue(R._ckpt_ok(self.cfg, self.base))
        self.calls.clear()
        R.process_work("test_work", eids=["1"], do_write=False)   # dry-run 供 A/B 对比
        self.assertGreater(len(self.calls), 0, "dry-run 不应跳过")

    def test_changed_translation_reprocesses(self):
        R.process_work("test_work", eids=["1"], do_write=True)
        self.calls.clear()
        # 模拟工作台手改 / 术语替换 → 指纹失效 → 必须重校
        with open(os.path.join(self.out, self.base + "_汉化.txt"), "w",
                  encoding="utf-8", newline="\n") as f:
            f.write("\n".join([SRC_HEAD, SRC_POST, "　　你好，今天天气真好", SRC_DIAL]))
        self.assertFalse(R._ckpt_ok(self.cfg, self.base))
        R.process_work("test_work", eids=["1"], do_write=True)
        self.assertGreater(len(self.calls), 0, "汉化改动后应重校")


if __name__ == "__main__":
    unittest.main()
