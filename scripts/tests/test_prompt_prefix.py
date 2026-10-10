# -*- coding: utf-8 -*-
"""公共提示词前缀单测（2026-09-15 P2-b）。

实测依据: refine 三种 system 变体交错调用时命中 0%（互不吃缓存），拼公共前缀后 55.8%；
网关不缓存（deepinfra/runinfra 同前缀 0/0/0），故只在 cache=true 的后端上拼。
"""
import os
import sys
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in (SCRIPTS, os.path.join(SCRIPTS, "common")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import aa_config as C
import aa_prompt_prefix as P
from _work_guard import repo_only  # noqa: E402 （发行包内跳过生成者专属用例）


class TestCommonPrefix(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in ("AA_COMMON_PREFIX", "AA_BACKEND")}
        os.environ.pop("AA_COMMON_PREFIX", None)
        os.environ.pop("AA_BACKEND", None)

    def tearDown(self):
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        C.reload()

    def test_prepended_at_front_and_idempotent(self):
        a = P.apply("你是翻译")
        self.assertTrue(a.startswith("【恒定前缀"))
        self.assertTrue(a.endswith("你是翻译"))
        self.assertEqual(P.apply(a), a)          # 幂等

    def test_env_off(self):
        os.environ["AA_COMMON_PREFIX"] = "0"
        self.assertEqual(P.apply("你是翻译"), "你是翻译")

    def test_only_on_cache_backend(self):
        self.assertTrue(P.apply("你是翻译", "deepseek").startswith("【恒定前缀"))
        self.assertEqual(P.apply("你是翻译", "deepinfra"), "你是翻译")
        self.assertEqual(P.apply("你是翻译", "runinfra"), "你是翻译")
        self.assertEqual(P.apply("你是翻译", "commandcode"), "你是翻译")

    @repo_only("断言生成者后端表里 cache=true 的端点")
    def test_current_cache_backend(self):
        # auto: 链上第一个会缓存的端点（2026-10-10 起 normal/r18 链首 = opencode_go，cache=true）
        self.assertEqual(P.current_cache_backend(False), "opencode_go")
        self.assertEqual(P.current_cache_backend(True), "opencode_go")
        os.environ["AA_BACKEND"] = "deepinfra"
        self.assertIsNone(P.current_cache_backend(False))                # 显式网关 → 不拼
        os.environ["AA_BACKEND"] = "deepseek"
        self.assertEqual(P.current_cache_backend(False), "deepseek")

    def test_prefix_is_stable_constant(self):
        """前缀必须是常量——它一变全库缓存失效一次。"""
        self.assertEqual(len(P.COMMON_PREFIX), len(P.COMMON_PREFIX.strip("\n")) + 1)
        self.assertGreaterEqual(len(P.COMMON_PREFIX), 250)   # ≥64*3 token 量级, 值得作为缓存单元


if __name__ == "__main__":
    unittest.main()
