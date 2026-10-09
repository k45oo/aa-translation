# -*- coding: utf-8 -*-
"""P2 薄壳一致性测试（2026-08-18 代码改进 Phase 1 去重防护）。

背景：2026-08-11 双版本漂移事故——平铺版与 common 版曾是两套独立实现，M0-M5 优化
只落其中一套导致「优化不生效」无人察觉（touhou 101 话全量在旧逻辑下翻译）。
2026-08-18 去重后 common/aa_common + common/aa_api 都是薄壳，转发 _flat_loader 加载的
同一份平铺版。本测试守护「同一对象」不变量，防止未来再分裂成两份实现。

断言：
- A.call_api is T.call_api（mock 打到同一对象）
- C.KNOWN_NAMES is T.KNOWN_NAMES
- C.manual_fix 系列 is T.manual_fix 系列
- C.disp_width is T.disp_width
- mock 语义：A.FLAT_MOD.call_api = fake 生效（translate_lines 内部走它）
"""
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS_DIR)
sys.path.insert(0, os.path.join(SCRIPTS, "common"))
sys.path.insert(0, os.path.join(SCRIPTS, "works"))

import aa_api as A
import aa_common as C
import aa_translate as T


class TestThinShellIdentity(unittest.TestCase):
    """薄壳转发必须指向平铺版同一对象（防双实现分裂）。"""

    def test_call_api_same_object(self):
        self.assertIs(A.call_api, T.call_api)

    def test_known_names_same_object(self):
        self.assertIs(C.KNOWN_NAMES, T.KNOWN_NAMES)

    def test_manual_fix_same_object(self):
        self.assertIs(C.manual_fix_path, T.manual_fix_path)
        self.assertIs(C.load_manual_fixes, T.load_manual_fixes)
        self.assertIs(C.record_manual_fix, T.record_manual_fix)
        self.assertIs(C.is_manual_fix, T.is_manual_fix)

    def test_core_functions_same_object(self):
        for name in ("disp_width", "kana_runs", "residual_suspect", "is_text_line",
                     "pad_to_width", "has_kana", "parse_result", "is_copy_failure"):
            self.assertIs(getattr(C, name), getattr(T, name), f"{name} 分裂")

    def test_segments_functions_same_object(self):
        # 段级翻译函数也必须同对象
        for name in ("translate_lines_mixed", "fix_width_mixed_lines", "rebuild_mixed_line",
                     "load_meta_segments_0based", "pad_segment"):
            self.assertIs(getattr(A, name), getattr(T, name), f"{name} 分裂")

    def test_flag_helpers_same_object(self):
        self.assertIs(A._flag_on, T._flag_on)

    def test_flat_mod_is_real_flat(self):
        # FLAT_MOD 必须是真平铺版（有 process_chapter/main 特征）
        self.assertTrue(hasattr(A.FLAT_MOD, "process_chapter"))
        self.assertTrue(hasattr(A.FLAT_MOD, "main"))
        self.assertIs(A.FLAT_MOD, T)


class TestMockSemantics(unittest.TestCase):
    """薄壳 mock 语义：A.FLAT_MOD.call_api = fake 必须生效。"""

    def test_mock_via_flat_mod_effective(self):
        orig = A.FLAT_MOD.call_api
        try:
            calls = []

            def fake(system, body, **kw):
                calls.append(body)
                return "00000|你好呀你好呀"   # 整行格式（parse_result 期望 序号|，非段级 _0）
            A.FLAT_MOD.call_api = fake
            # translate_lines 内部引用 FLAT_MOD.call_api → mock 生效
            fixed, missed, warns = T.translate_lines([(0, "こんにちは世界")])
            self.assertGreaterEqual(len(calls), 1)   # mock 被调用了
            self.assertIn(0, fixed)
        finally:
            A.FLAT_MOD.call_api = orig

    def test_mock_via_flat_module_equivalent(self):
        # 直接 import aa_translate as T; T.call_api = fake 与 A.FLAT_MOD 等价
        self.assertIs(A.FLAT_MOD, sys.modules["aa_translate"])


if __name__ == "__main__":
    unittest.main()
