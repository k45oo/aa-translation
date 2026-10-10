# -*- coding: utf-8 -*-
"""管线归属（pipeline 字段）单测（2026-09-22 方案 D10/D11）。

权威源 = work_meta.json 的 pipeline；registry 只兜底；env AA_PIPELINE 最高优先（调试用）。
"""
import os
import sys
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in (SCRIPTS, os.path.join(SCRIPTS, "common"), os.path.join(SCRIPTS, "works")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import work_pipeline as WP
from _work_guard import repo_only  # noqa: E402 （发行包内跳过生成者专属用例）


class TestPipeline(unittest.TestCase):
    @repo_only("需要宝可梦作品已注册（包里只有示例作品）")
    def test_pokemon_work_detected(self):
        """本作品 work_meta 已标 pipeline=pokemon（宝可梦专属链）。"""
        self.assertEqual(WP.pipeline_of("この素晴らしいシンオウに祝福を"), WP.POKEMON)
        self.assertTrue(WP.is_pokemon("この素晴らしいシンオウに祝福を"))

    def test_generic_default(self):
        self.assertEqual(WP.pipeline_of("不存在的作品xyz"), WP.GENERIC)
        self.assertEqual(WP.pipeline_of(""), WP.GENERIC)
        self.assertFalse(WP.is_pokemon("baitou"))

    def test_env_override(self):
        os.environ["AA_PIPELINE"] = "pokemon"
        try:
            self.assertTrue(WP.is_pokemon("baitou"))     # env 最高优先（调试用）
        finally:
            del os.environ["AA_PIPELINE"]

    def test_owner_process_marker(self):
        os.environ["AA_PIPELINE_OWNER"] = "pokemon"
        try:
            self.assertEqual(WP.owner_process(), "pokemon")
        finally:
            del os.environ["AA_PIPELINE_OWNER"]
        self.assertEqual(WP.owner_process(), "")


class TestRunnerGate(unittest.TestCase):
    """通用 runner 硬拦开关的判据（不真跑 runner，只验判据组合）。"""

    def _blocked(self, work, owner, allow):
        pl = WP.pipeline_of(work)
        if pl == WP.GENERIC:
            return False
        return not (allow or owner == pl)

    @repo_only("需要宝可梦作品已注册（包里只有示例作品）")
    def test_generic_blocked_for_pokemon_work(self):
        self.assertTrue(self._blocked("この素晴らしいシンオウに祝福を", "", False))

    def test_owner_handoff_allowed(self):
        self.assertFalse(self._blocked("この素晴らしいシンオウに祝福を", "pokemon", False))

    def test_escape_hatch_allowed(self):
        self.assertFalse(self._blocked("この素晴らしいシンオウに祝福を", "", True))

    def test_generic_work_never_blocked(self):
        self.assertFalse(self._blocked("baitou", "", False))


if __name__ == "__main__":
    unittest.main()
