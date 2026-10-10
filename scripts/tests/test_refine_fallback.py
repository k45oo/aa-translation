# -*- coding: utf-8 -*-
"""校对后端链（refine_aa）单元测试：链解析 + 跟随付费链 + 失败降级。零依赖。

链语义（2026-09-22「与付费链一致」）：默认跟随付费链（`[default].normal_backends`，
R18 作品按 r18_backends 过滤，模型也随链）；env AA_REFINE_BACKEND 显式覆盖时原样用。

运行：cd scripts/tests && python -m unittest test_refine_fallback -v
"""
import os, sys, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'fix')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'common')))

import refine_aa as R
from _work_guard import repo_only  # noqa: E402 （发行包内跳过生成者专属用例）


def _set_chain(entries):
    """测试用：设本次校对链。entries = [(backend, model_or_None)]，四个模块级视图一起同步。"""
    R._REFINE_ENTRIES = list(entries)
    R._REFINE_CHAIN = [b for b, _m in entries]
    R._REFINE_MDLSEQ = [m for _b, m in entries]
    R._REFINE_CHAIN_MODELS = {b: m for b, m in entries if m}


class TestChainParse(unittest.TestCase):
    @unittest.skipIf(os.environ.get("AA_REFINE_BACKEND"), "该用例断言默认值, env 已覆盖时跳过")
    def test_module_chain_is_fixed_fallback_value(self):
        # 模块级链 = TOML [refine_aa].backends（只在 follow_default=false 时生效）；
        # 真正生效的链由 _set_chain_for_work(work) 按付费链覆盖。
        self.assertEqual(R._REFINE_CHAIN, ["deepseek", "deepinfra"])

    def test_aliases(self):
        self.assertEqual(R._parse_refine_chain(None), ["deepseek", "deepinfra"])
        self.assertEqual(R._parse_refine_chain("glm"), ["deepinfra"])
        self.assertEqual(R._parse_refine_chain("ds"), ["deepseek"])
        self.assertEqual(R._parse_refine_chain("官网"), ["deepseek"])
        self.assertEqual(R._parse_refine_chain("mimo"), ["mimo"])
        self.assertEqual(R._parse_refine_chain("cc"), ["commandcode"])

    def test_order_and_dedup(self):
        self.assertEqual(R._parse_refine_chain("deepseek,deepinfra"), ["deepseek", "deepinfra"])
        self.assertEqual(R._parse_refine_chain("glm,deepseek,ds"), ["deepinfra", "deepseek"])
        self.assertEqual(R._parse_refine_chain("mimo,deepseek,deepinfra,cc"),
                         ["mimo", "deepseek", "deepinfra", "commandcode"])
        # 同后端**不同模型**（glm 钉模型 vs 裸 deepinfra）= 两个条目，不再折叠（2026-10-08 阶段 2）
        self.assertEqual(R._parse_refine_chain("glm,di"), ["deepinfra", "deepinfra"])
        self.assertEqual(R._parse_refine_entries("glm,di"),
                         [("deepinfra", "zai-org/GLM-5.3-Flash"), ("deepinfra", None)])

    def test_entry_id_form(self):
        """链条目 id（后端|模型）也可作为链项；重复项去重。"""
        self.assertEqual(R._parse_refine_entries("deepinfra|deepseek-ai/DeepSeek-V4-Flash-0731"),
                         [("deepinfra", "deepseek-ai/DeepSeek-V4-Flash-0731")])
        self.assertEqual(R._chain_models("deepinfra|m-x"), {"deepinfra": "m-x"})
        self.assertEqual(len(R._parse_refine_entries("ds,ds")), 1)

    def test_garbage_falls_back_to_default(self):
        self.assertEqual(R._parse_refine_chain("zzz"), ["deepseek", "deepinfra"])

    def test_glm_alias_pins_model(self):
        self.assertEqual(R._chain_models("glm"), {"deepinfra": "zai-org/GLM-5.3-Flash"})
        self.assertEqual(R._chain_models("deepinfra"), {})


class TestFollowPaidChain(unittest.TestCase):
    """默认（无 env / 无显式固定链）→ 校对链 = 付费链，端点与模型逐项相同。"""

    def setUp(self):
        self._env = os.environ.pop("AA_REFINE_BACKEND", None)
        self._chain = list(R._REFINE_CHAIN)
        self._models = dict(R._REFINE_CHAIN_MODELS)
        self._entries = list(R._REFINE_ENTRIES)

    def tearDown(self):
        if self._env is not None:
            os.environ["AA_REFINE_BACKEND"] = self._env
        R._REFINE_ENTRIES = self._entries
        R._REFINE_CHAIN = self._chain
        R._REFINE_CHAIN_MODELS = self._models

    @unittest.skipIf(os.environ.get("AA_REFINE_BACKEND"), "env 覆盖时跳过")
    @repo_only("断言生成者的付费链构成（包内默认链已换成公共可取 key 的端点）")
    def test_chain_equals_paid_chain(self):
        import aa_config as AC
        paid = AC.backend_chain(False)
        self.assertTrue(paid, "付费链不该为空")
        self.assertEqual([(b, m or None) for b, m in paid], R.refine_chain(None))

    @repo_only("断言生成者的 R18 链构成")
    def test_work_r18_uses_r18_chain(self):
        import aa_config as AC
        # 非 R18 作品 → normal 链；R18(high) 作品 → r18 链（mimo 不在其中）
        self.assertEqual([b for b, _m in R.refine_chain("__不存在__")],
                         [b for b, _m in AC.backend_chain(False)])
        _orig = R._work_r18
        R._work_r18 = lambda w: True
        try:
            self.assertEqual([b for b, _m in R.refine_chain("x")],
                             [b for b, _m in AC.backend_chain(True)])
        finally:
            R._work_r18 = _orig

    def test_work_r18_reads_work_meta(self):
        """r18_level == "high" → True；缺失/非 high → False（与 translate_runner 清理口径一致）。"""
        self.assertFalse(R._work_r18(None))
        self.assertFalse(R._work_r18("__绝对不存在的作品__"))
        try:
            import json as _json
            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            meta = _json.load(open(os.path.join(root, "aa_web_app", "data", "work_meta.json"),
                                   encoding="utf-8"))
        except Exception:
            return
        high = [w for w, v in meta.items() if (v or {}).get("r18_level") == "high"]
        low = [w for w, v in meta.items() if (v or {}).get("r18_level") not in ("high",)]
        if high:
            self.assertTrue(R._work_r18(high[0]))
        if low:
            self.assertFalse(R._work_r18(low[0]))

    def test_env_override_wins(self):
        os.environ["AA_REFINE_BACKEND"] = "glm,ds"
        try:
            self.assertEqual(R.refine_chain(None),
                             [("deepinfra", "zai-org/GLM-5.3-Flash"), ("deepseek", None)])
        finally:
            os.environ.pop("AA_REFINE_BACKEND", None)

    def test_set_chain_for_work_applies(self):
        ch = R._set_chain_for_work(None)
        self.assertEqual(ch, [b for b, _m in R.refine_chain(None)])
        self.assertEqual(R._REFINE_CHAIN, ch)

    def test_follow_default_off_uses_fixed_chain(self):
        _orig = R._follow_paid_chain
        R._follow_paid_chain = lambda: False
        try:
            self.assertEqual(R.refine_chain(None), [("deepseek", None), ("deepinfra", None)])
        finally:
            R._follow_paid_chain = _orig


class TestFallback(unittest.TestCase):
    """链首失败 → 自动降级下一个端点（deepinfra 失败 → 官网 deepseek-flash）。"""

    def setUp(self):
        self._chain = R._REFINE_CHAIN
        self._models = dict(R._REFINE_CHAIN_MODELS)
        self._entries = list(R._REFINE_ENTRIES)
        self._mdlseq = list(R._REFINE_MDLSEQ)
        self._hint = R._GLOSS_HINT
        R._GLOSS_HINT = ""
        _set_chain([("deepinfra", None), ("deepseek", None)])

    def tearDown(self):
        R._REFINE_ENTRIES = self._entries
        R._REFINE_CHAIN = self._chain
        R._REFINE_MDLSEQ = self._mdlseq
        R._REFINE_CHAIN_MODELS = self._models
        R._GLOSS_HINT = self._hint

    def test_fallback_to_deepseek(self):
        import aa_backend as AB
        _di, _ds = AB.call_deepinfra, AB.call_deepseek
        AB.call_deepseek = lambda *a, **kw: "译文A"

        def boom(*a, **kw):
            raise RuntimeError("deepinfra failed: 429")
        AB.call_deepinfra = boom
        try:
            self.assertEqual(R._call_glm("S", "B"), "译文A")
        finally:
            AB.call_deepinfra, AB.call_deepseek = _di, _ds

    def test_primary_wins_when_ok(self):
        import aa_backend as AB
        _di, _ds = AB.call_deepinfra, AB.call_deepseek
        AB.call_deepseek = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("不该调 deepseek"))
        AB.call_deepinfra = lambda *a, **kw: "译文DI"
        try:
            self.assertEqual(R._call_glm("S", "B"), "译文DI")
        finally:
            AB.call_deepinfra, AB.call_deepseek = _di, _ds

    def test_all_fail_raises(self):
        import aa_backend as AB
        _di, _ds = AB.call_deepinfra, AB.call_deepseek
        AB.call_deepseek = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("官网 402"))
        AB.call_deepinfra = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("DI 超时"))
        try:
            with self.assertRaises(RuntimeError) as cm:
                R._call_glm("S", "B")
            self.assertIn("全部失败", str(cm.exception))
        finally:
            AB.call_deepinfra, AB.call_deepseek = _di, _ds

    def test_single_backend_no_fallback(self):
        import aa_backend as AB
        _di, _ds = AB.call_deepinfra, AB.call_deepseek
        AB.call_deepseek = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("单后端不应降级"))
        _set_chain([("deepinfra", None)])
        AB.call_deepinfra = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("DI 挂了"))
        try:
            with self.assertRaises(RuntimeError):
                R._call_glm("S", "B")
        finally:
            AB.call_deepinfra, AB.call_deepseek = _di, _ds

    def test_deepseek_primary_falls_back_to_deepinfra(self):
        """官网 deepseek 主失败 → 降级 deepinfra（R18 拒答等）。"""
        import aa_backend as AB
        _di, _ds = AB.call_deepinfra, AB.call_deepseek
        AB.call_deepseek = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("官网审核拒绝"))
        AB.call_deepinfra = lambda *a, **kw: "译文DI"
        _set_chain([("deepseek", None), ("deepinfra", None)])
        try:
            self.assertEqual(R._call_glm("S", "B"), "译文DI")
        finally:
            AB.call_deepinfra, AB.call_deepseek = _di, _ds

    def test_same_backend_two_entries_use_per_position_models(self):
        """同后端两条目（deepinfra V4.1 / 0731、或 glm 钉模型 + 裸 deepinfra）按**位置**各用各的模型。"""
        import aa_backend as AB
        seen = []
        _di = AB.call_deepinfra
        AB.call_deepinfra = lambda system, body, **kw: (seen.append(kw.get("model")), "ok")[1]
        _set_chain([("deepinfra", "model-A"), ("deepinfra", "model-B")])
        try:
            self.assertEqual(R._call_glm("S", "B"), "ok")
        finally:
            AB.call_deepinfra = _di
        self.assertEqual(seen, ["model-A"], "首条目成功即返回（model-A）")

        # 第一条目失败 → 降级到第二条目，且用的是它自己的模型（不是折叠成同一个）
        seen.clear()
        AB.call_deepinfra = lambda system, body, **kw: (
            seen.append(kw.get("model")),
            "ok" if kw.get("model") == "model-B" else (_ for _ in ()).throw(RuntimeError("挂了")))[1]
        try:
            self.assertEqual(R._call_glm("S", "B"), "ok")
        finally:
            AB.call_deepinfra = _di
        self.assertEqual(seen, ["model-A", "model-B"])

    def test_mimo_head_falls_back_to_deepseek(self):
        """付费链首 mimo（R18 章节 content_filter）→ 降级 deepseek。"""
        import aa_backend as AB
        _mi, _ds = AB.call_mimo, AB.call_deepseek
        AB.call_deepseek = lambda *a, **kw: "译文DS"
        AB.call_mimo = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("content_filter"))
        _set_chain([("mimo", None), ("deepseek", None)])
        try:
            self.assertEqual(R._call_glm("S", "B"), "译文DS")
        finally:
            AB.call_mimo, AB.call_deepseek = _mi, _ds

    def test_model_override_passed(self):
        """链上模型覆盖（glm 钉 GLM）必须透传给调用函数。"""
        import aa_backend as AB
        _di = AB.call_deepinfra
        seen = {}

        def cap(*a, **kw):
            seen.update(kw)
            return "ok"
        AB.call_deepinfra = cap
        _set_chain([("deepinfra", "zai-org/GLM-5.3-Flash")])
        try:
            R._call_glm("S", "B")
            self.assertEqual(seen.get("model"), "zai-org/GLM-5.3-Flash")
            self.assertEqual(seen.get("tag"), "refine")
        finally:
            AB.call_deepinfra = _di


if __name__ == "__main__":
    unittest.main()
