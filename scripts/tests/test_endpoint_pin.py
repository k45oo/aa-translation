# -*- coding: utf-8 -*-
"""付费链「章内 pin 会缓存的端点」单测（2026-09-15 P1）。

背景（实测，见 doc/方案归档/token成本优化方案_2026-09-15.md §8）：
  · 只有官网 deepseek 会命中前缀缓存（同前缀连续调用 79.6%；冷前缀 8 并发首波仍 79.0%）
  · deepinfra/runinfra 网关同前缀连续调用 **0/0/0 零命中**
  · 旧逻辑「健康直出不钉住」让**每次调用**都轮询端点 → 同章被打散到零命中的网关
决策 D3'：章内 pin 链上第一个 [backend.X].cache=true 的端点，网关只作降级/溢出。
"""
import os
import sys
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in (SCRIPTS, os.path.join(SCRIPTS, "common")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import aa_backend as AB
import aa_config as C
from _work_guard import repo_only  # noqa: E402 （发行包内跳过生成者专属用例）

CHAIN = [("deepinfra", "zai-org/GLM-5.3-Flash"),
         ("deepseek", "deepseek-flash"),
         ("runinfra", "glm-5-3-flash")]


class _Recorder:
    """替换 _call_named：记录被调用的 (后端, 模型)；可指定某后端抛指定异常。"""

    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail or {}

    def __call__(self, b, system, body, model=None, max_tokens=0, timeout=0):
        self.calls.append((b, model))
        e = self.fail.get(b)
        if e:
            raise e
        return f"<{b}>"


class TestPinCacheableEndpoint(unittest.TestCase):
    def setUp(self):
        self._osc = {k: os.environ.get(k) for k in ("AA_PIN_CACHE_ENDPOINT", "AA_FREE_MODE", "AA_BACKEND", "AA_REFINE_BACKEND")}
        for k in ("AA_PIN_CACHE_ENDPOINT", "AA_FREE_MODE", "AA_BACKEND"):
            os.environ.pop(k, None)
        self._chain_fn = C.backend_chain
        C.backend_chain = lambda work_r18: list(CHAIN)
        self._call_named = AB._call_named
        AB.reset_preferred_backend()

    def tearDown(self):
        C.backend_chain = self._chain_fn
        AB._call_named = self._call_named
        for k, v in self._osc.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        C.reload()
        AB.reset_preferred_backend()

    def test_pins_cacheable_endpoint_even_if_not_first(self):
        """deepseek 不在链首也必须被选中（链首 deepinfra 不会缓存）。"""
        rec = _Recorder()
        AB._call_named = rec
        for _ in range(4):
            AB.call_with_fallback(False, "sys", "body")
        self.assertEqual([b for b, _ in rec.calls], ["deepseek"] * 4)
        self.assertEqual(AB._PREFERRED_BACKEND["name"], "deepseek")

    def test_pin_survives_within_chapter_and_repins_after_reset(self):
        rec = _Recorder()
        AB._call_named = rec
        AB.call_with_fallback(False, "sys", "b1")
        AB.reset_preferred_backend()          # 章边界
        AB.call_with_fallback(False, "sys", "b2")
        self.assertEqual([b for b, _ in rec.calls], ["deepseek", "deepseek"])

    def test_fallback_order_keeps_chain_order(self):
        """pin 只改「谁先被调」；失败后仍按原链序降级（deepinfra 在 deepseek 之后不会插队）。"""
        rec = _Recorder()
        AB._call_named = rec
        AB.call_with_fallback(False, "sys", "b")
        # 钉住后链 = [deepseek, deepinfra, runinfra]
        chain = [t for t in CHAIN if t[0] == "deepseek"] + [t for t in CHAIN if t[0] != "deepseek"]
        self.assertEqual([b for b, _ in chain], ["deepseek", "deepinfra", "runinfra"])

    def test_env_rollback_restores_round_robin(self):
        os.environ["AA_PIN_CACHE_ENDPOINT"] = "0"
        rec = _Recorder()
        AB._call_named = rec
        for _ in range(3):
            AB.call_with_fallback(False, "sys", "b")
        self.assertIsNone(AB._PREFERRED_BACKEND["name"])          # 不钉
        self.assertGreaterEqual(len({b for b, _ in rec.calls}), 2)  # 仍在轮询分散

    def test_degrade_then_pin_the_working_one(self):
        """被 pin 的端点限流(429) → 降级到下一候选并钉住它（不再回撞坏端点）。"""
        rec = _Recorder(fail={"deepseek": RuntimeError("HTTP 429 Too Many Requests")})
        AB._call_named = rec
        AB.call_with_fallback(False, "sys", "b")
        AB.call_with_fallback(False, "sys", "b2")
        self.assertEqual([b for b, _ in rec.calls], ["deepseek", "deepinfra", "deepinfra"])
        self.assertEqual(AB._PREFERRED_BACKEND["name"], "deepinfra")

    def test_free_mode_untouched(self):
        """免费池照旧（用户 D3' 明确不动）——免费模式根本不进本函数主路径。"""
        os.environ["AA_FREE_MODE"] = "1"
        called = {"free": 0}
        orig = AB._call_free_chain
        AB._call_free_chain = lambda system, body, max_tokens=0, timeout=0: (called.__setitem__("free", called["free"] + 1) or ("x", "free"))
        try:
            AB.call_with_fallback(False, "sys", "b")
        finally:
            AB._call_free_chain = orig
        self.assertEqual(called["free"], 1)
        self.assertIsNone(AB._PREFERRED_BACKEND["name"])

    def test_no_cacheable_endpoint_keeps_rotation(self):
        """链上没有 cache 端点（如显式 AA_BACKEND=deepinfra 的 R18 场景）→ 回落旧轮询。"""
        C.backend_chain = lambda work_r18: [("deepinfra", "m1"), ("runinfra", "m2")]
        rec = _Recorder()
        AB._call_named = rec
        for _ in range(2):
            AB.call_with_fallback(False, "sys", "b")
        self.assertIsNone(AB._PREFERRED_BACKEND["name"])

    def test_explicit_backend_not_hijacked_by_pin(self):
        """显式 AA_BACKEND=deepinfra 是用户手动选择 → 不抢着 pin 回官网（尊重显式）。"""
        os.environ["AA_BACKEND"] = "deepinfra"
        rec = _Recorder()
        AB._call_named = rec
        for _ in range(2):
            AB.call_with_fallback(False, "sys", "b")
        self.assertIsNone(AB._PREFERRED_BACKEND["name"])          # 不 pin
        self.assertIn("deepinfra", {b for b, _ in rec.calls})      # 显式端点确实被用到

    def test_cacheable_flag_from_toml(self):
        self.assertTrue(C.cache_safe("deepseek"))
        self.assertFalse(C.cache_safe("deepinfra"))
        self.assertFalse(C.cache_safe("runinfra"))
        self.assertFalse(C.cache_safe("nope"))


class TestFallbackOnlyEndpoint(unittest.TestCase):
    """兜底端点（`fallback_only = true`，如 commandcode）——2026-09-21 用户口径:
    「默认不使用，只在其他端点均失效时启用」。"""

    CH = [("deepseek", "deepseek-flash"), ("deepinfra", "m1"), ("commandcode", "muse")]

    def setUp(self):
        self._osc = {k: os.environ.get(k) for k in ("AA_PIN_CACHE_ENDPOINT", "AA_FREE_MODE", "AA_BACKEND")}
        for k in ("AA_PIN_CACHE_ENDPOINT", "AA_FREE_MODE", "AA_BACKEND"):
            os.environ.pop(k, None)
        self._chain_fn = C.backend_chain
        self._fb_fn = C.fallback_only_names
        self._cache_fn = C.cache_safe
        C.backend_chain = lambda work_r18: list(self.CH)
        C.fallback_only_names = lambda work_r18: {"commandcode"}
        self._call_named = AB._call_named
        AB.reset_preferred_backend()

    def tearDown(self):
        C.backend_chain = self._chain_fn
        C.fallback_only_names = self._fb_fn
        C.cache_safe = self._cache_fn
        AB._call_named = self._call_named
        for k, v in self._osc.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        C.reload()
        AB.reset_preferred_backend()

    def test_not_used_in_healthy_rotation(self):
        """关掉 pin 回退旧轮询时，兜底端点也不该被轮询到（否则无 cache 端点时它占 1/N 流量）。"""
        os.environ["AA_PIN_CACHE_ENDPOINT"] = "0"
        rec = _Recorder()
        AB._call_named = rec
        for _ in range(6):
            AB.call_with_fallback(False, "sys", "b")
        self.assertNotIn("commandcode", {b for b, _ in rec.calls})
        self.assertGreaterEqual(len({b for b, _ in rec.calls}), 2)   # 其余端点仍在轮询

    def test_used_only_after_all_others_fail(self):
        rec = _Recorder(fail={"deepseek": RuntimeError("HTTP 429 Too Many Requests"),
                              "deepinfra": RuntimeError("HTTP 429 Too Many Requests")})
        AB._call_named = rec
        txt, b = AB.call_with_fallback(False, "sys", "b")
        self.assertEqual(b, "commandcode")
        self.assertEqual(txt, "<commandcode>")

    def test_not_a_pin_target(self):
        """兜底端点即使被标 cache=true 也不作 pin 目标（与实际调度一致）。"""
        C.cache_safe = lambda name: name == "commandcode"
        rec = _Recorder()
        AB._call_named = rec
        for _ in range(3):
            AB.call_with_fallback(False, "sys", "b")
        self.assertIsNone(AB._PREFERRED_BACKEND["name"])
        self.assertNotIn("commandcode", {b for b, _ in rec.calls})

    def test_explicit_backend_still_honored(self):
        """显式 AA_BACKEND=commandcode 是用户手动选择 → 照旧可用（不被兜底位挡住）。"""
        os.environ["AA_BACKEND"] = "commandcode"
        rec = _Recorder()
        AB._call_named = rec
        for _ in range(3):
            AB.call_with_fallback(False, "sys", "b")
        self.assertIn("commandcode", {b for b, _ in rec.calls})

    @repo_only("断言生成者 TOML 的 fallback_only 标记")
    def test_flag_read_from_toml(self):
        """当前配置里 commandcode 是兜底端点、deepseek 不是。"""
        self.assertIn("commandcode", self._fb_fn(False))
        self.assertIn("commandcode", self._fb_fn(True))
        self.assertNotIn("deepseek", self._fb_fn(False))


class TestAutoBackendDoesNotOverrideChain(unittest.TestCase):
    """runner 自动选出的 AA_BACKEND（`AA_BACKEND_AUTO=1`）**不得**参与「组前置」（2026-10-11）。

    它只是代码默认（链首），不是用户选择；一旦组前置就会盖过 `AA_CHAIN` 勾选集，实测症状：
      · 定时任务（`AA_CHAIN=mimo` + 折扣时段）实际从链首 opencode_go 起步 → 折扣白等
      · 控制台取消勾选链首（`AA_CHAIN=deepinfra+deepseek`）→ 被取消的端点照样被用
    用户**手写**的 AA_BACKEND 仍照旧组前置（`test_用户手写仍组前置` 守着这条不被改坏）。
    """

    # 模拟 `AA_CHAIN` 过滤后的链：自动链首 opencode_go 已被排除在外
    CH = [("mimo", "mimo-v2.6-flash"), ("deepseek", "deepseek-flash")]

    def setUp(self):
        self._osc = {k: os.environ.get(k) for k in
                     ("AA_PIN_CACHE_ENDPOINT", "AA_FREE_MODE", "AA_BACKEND", "AA_BACKEND_AUTO")}
        for k in ("AA_PIN_CACHE_ENDPOINT", "AA_FREE_MODE", "AA_BACKEND", "AA_BACKEND_AUTO"):
            os.environ.pop(k, None)
        self._chain_fn = C.backend_chain
        self._fb_fn = C.fallback_only_names
        C.backend_chain = lambda work_r18: list(self.CH)
        C.fallback_only_names = lambda work_r18: set()
        self._call_named = AB._call_named
        self._round_i = AB._chain_round["i"]     # 端点轮询指针是模块级共享状态：本类多发了几次调用
        AB.reset_preferred_backend()             # 就会让下游用例的起始端点错位（如 pin 类那条
                                                 # test_explicit_backend_not_hijacked_by_pin）→ 用完还原

    def tearDown(self):
        C.backend_chain = self._chain_fn
        C.fallback_only_names = self._fb_fn
        AB._call_named = self._call_named
        AB._chain_round["i"] = self._round_i
        for k, v in self._osc.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        C.reload()
        AB.reset_preferred_backend()

    def _sweep(self, n=3):
        rec = _Recorder()
        AB._call_named = rec
        for _ in range(n):
            AB.call_with_fallback(False, "sys", "b")
        return [b for b, _ in rec.calls]

    def test_自动选的后端不被塞进链(self):
        os.environ["AA_BACKEND"] = "opencode_go"
        os.environ["AA_BACKEND_AUTO"] = "1"
        used = self._sweep()
        self.assertNotIn("opencode_go", used,
                         "自动选的后端被组前置/插头 → 盖过 AA_CHAIN 勾选集(定时 mimo/取消勾选链首都因此失效)")
        self.assertTrue(set(used) <= {"mimo", "deepseek"})

    def test_控制台勾选集全落空也只用勾选内的端点(self):
        """AA_CHAIN=deepinfra+deepseek（取消勾选链首）→ 自动链首不得混进来。"""
        C.backend_chain = lambda work_r18: [("deepseek", "deepseek-flash"),
                                            ("deepinfra", "deepseek-ai/DeepSeek-V4.1-Flash")]
        os.environ["AA_PIN_CACHE_ENDPOINT"] = "0"   # 关 pin 看端点轮询（pin 会把整章钉在一个端点）
        os.environ["AA_BACKEND"] = "opencode_go"
        os.environ["AA_BACKEND_AUTO"] = "1"
        used = self._sweep(4)
        self.assertNotIn("opencode_go", used)
        self.assertEqual(set(used), {"deepseek", "deepinfra"})

    def test_用户手写仍组前置(self):
        """没有 AA_BACKEND_AUTO 标记 = 用户手写 → 照旧组前置（行为不能被这次改动改坏）。"""
        os.environ["AA_BACKEND"] = "opencode_go"
        used = self._sweep(3)
        self.assertIn("opencode_go", used)

    def test_自动值不会把兜底端点提上来(self):
        """AA_BACKEND 自动选到兜底端点也不能让它进健康态轮询（fallback_only 语义优先）。"""
        CH = [("deepseek", "deepseek-flash"), ("commandcode", "muse")]
        C.backend_chain = lambda work_r18: list(CH)
        C.fallback_only_names = lambda work_r18: {"commandcode"}
        os.environ["AA_BACKEND"] = "commandcode"
        os.environ["AA_BACKEND_AUTO"] = "1"
        used = self._sweep(4)
        self.assertNotIn("commandcode", used)   # 只留链尾降级, 不参与轮询

    def test_自动标记写在三个自动选点(self):
        """所有「代码自己选后端」的地方都要打 AUTO 标记，否则组前置又会复活。"""
        for rel in ("runner/translate_runner.py", "aa_translate.py", "fix/refix_mixed_segs.py"):
            p = os.path.join(SCRIPTS, rel)
            with open(p, encoding="utf-8") as f:
                src = f.read()
            self.assertIn("AA_BACKEND_AUTO", src, f"{rel} 自动设了 AA_BACKEND 却没打 AUTO 标记")


if __name__ == "__main__":
    unittest.main()
