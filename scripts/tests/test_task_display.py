# -*- coding: utf-8 -*-
"""任务界面/校对记账接线测试（2026-09-13）。

仓库无 JS 测试运行时 → 与 test_pager_wiring 同款：锁住"接线不丢"。
覆盖三处 2026-09-13 用户要求的改动：
  ① 校对( refine_aa ) 也写 usage（tag=refine）——此前 GLM 路径自建 request 从不记账
  ② 任务卡 token 按 k/M 缩写
  ③ 校对进度 off-by-one：本章完成后报 fi+1（原先只在循环头报 fi → 界面永远"待校对 1"）
运行：python -m unittest discover -s scripts/tests
"""
import inspect
import os
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(TESTS_DIR)
_ROOT = os.path.dirname(_SCRIPTS)
_TPL = os.path.join(_ROOT, "aa_web_app", "templates")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestTasksCardDisplay(unittest.TestCase):
    def setUp(self):
        self.tpl = _read(os.path.join(_TPL, "tasks.html"))

    def test_fmt_tok_units(self):
        self.assertIn("function fmtTok(", self.tpl)
        self.assertIn("'M'", self.tpl)          # 百万
        self.assertIn("'k'", self.tpl)          # 千
        self.assertIn("fmtTok(u.total)", self.tpl)      # 实际用于 token 显示
        self.assertIn('title="${u.total} tok"', self.tpl)  # 原值悬停

    def test_proofread_labels(self):
        self.assertIn("proofread:'校对'", self.tpl)      # TYPE_CN 支持校对任务
        self.assertIn("'已校对'", self.tpl)
        self.assertIn("'待校对'", self.tpl)


class TestRefineUsageAndProgress(unittest.TestCase):
    def setUp(self):
        import sys
        for p in (os.path.join(_SCRIPTS), os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "fix")):
            if p not in sys.path:
                sys.path.insert(0, p)
        import refine_aa
        self.R = refine_aa
        self.src = _read(os.path.join(_SCRIPTS, "fix", "refine_aa.py"))

    def test_log_usage_accepts_tag(self):
        import aa_backend
        sig = inspect.signature(aa_backend._log_usage)
        self.assertIn("tag", sig.parameters)

    def test_refine_logs_usage_with_refine_tag(self):
        # 2026-09-22: 校对调用走 aa_backend.call_*（不再自建 request），
        # 记账靠显式传 tag="refine" → _log_usage 落盘。
        self.assertIn('tag="refine"', self.src)
        import aa_backend
        for fn in ("call_mimo", "call_deepinfra", "call_commandcode", "call_deepseek"):
            self.assertIn("tag", inspect.signature(getattr(aa_backend, fn)).parameters,
                          f"{fn} 必须接受 tag（否则校对调用被记成 translate）")

    def test_backend_log_usage_forwards_tag(self):
        """call_* 必须把 tag 透传给 _log_usage（否则 tag=refine 丢失）。"""
        import re as _re
        import aa_backend
        for fn in ("call_mimo", "call_deepinfra", "call_commandcode"):
            src = inspect.getsource(getattr(aa_backend, fn))
            self.assertTrue(_re.search(r"_log_usage\(.*?tag=tag", src, _re.S), fn)

    def test_heartbeat_after_chapter_not_before_only(self):
        # 本章完成后必须报 fi+1（否则最后一章永不"完成"，界面显示待校对 1）
        self.assertIn('TR.heartbeat("校对", fi + 1, n_files, force=True)', self.src)

    def test_refine_chain_follows_paid_chain(self):
        """2026-09-22「与付费链一致」：无 env 时链 = 付费链（含模型），不是写死的 deepseek,deepinfra。"""
        self.assertIn("def refine_chain(work=None)", self.src)
        self.assertIn("AC.backend_chain(_work_r18(work))", self.src)
        self.assertIn("follow_default", self.src)

    def test_log_refine_usage_retired(self):
        """自建记账已退休：refine_aa 不再定义/调用 _log_refine_usage（记账在 aa_backend 内）。"""
        self.assertFalse(hasattr(self.R, "_log_refine_usage"))
        import re as _re
        self.assertIsNone(_re.search(r"_log_refine_usage\s*\(", self.src))


if __name__ == "__main__":
    unittest.main()
