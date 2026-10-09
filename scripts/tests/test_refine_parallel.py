# -*- coding: utf-8 -*-
"""refine_aa 段级并发（P1，2026-09-15）单元测试。

覆盖：
  · job 单元语义（_job_poly / _job_swallow：成功、拒用、异常、专名保留、超宽未产出）
  · 并发度真的起来了（workers=8 时同时在飞 >1），workers=1 时严格串行
  · 并发结果与串行逐条一致、按行号有序
  · job 未产出 → 调用方回退 edit（manual_mixed / tail_translate_oversize）
  · 术语 hint 显式传参（不依赖模块全局）
  · usage tag 不再靠改 env AA_USAGE_TAG

运行：.venv/Scripts/python.exe -m unittest scripts.tests.test_refine_parallel -v
"""
import os
import sys
import json
import shutil
import tempfile
import threading
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, ".."))
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "fix"),
           os.path.join(_SCRIPTS, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import refine_aa as R      # noqa: E402

SRC_HEAD = "# テスト章"
SRC_POST = "1 ：作者 ：2020/01/01(水) 00:00:00 ID:abcdef"


def _fake_seg(kind, start, end, width):
    return {"kind": kind, "start": start, "end": end, "src_width": width}


class TestJobPoly(unittest.TestCase):
    def setUp(self):
        self._call = R._call_glm
        self.s = "　　こんにちは、今日はいい天気ですね"
        self.h = self.s

    def tearDown(self):
        R._call_glm = self._call

    def test_ok(self):
        R._call_glm = lambda system, body, **kw: "你好，今天天气真好"
        ed = R._job_poly(3, self.s, self.h, "　　", self.s.strip(), "")
        self.assertEqual(ed[4], "poly")
        self.assertTrue(ed[5])
        self.assertIn("你好", ed[3])

    def test_reject_copy(self):
        R._call_glm = lambda system, body, **kw: body          # 原样照抄日语
        ed = R._job_poly(3, self.s, self.h, "　　", self.s.strip(), "")
        self.assertEqual((ed[4], ed[5], ed[6]), ("poly", False, "zh_empty_or_copy"))

    def test_reject_pure_kana(self):
        R._call_glm = lambda system, body, **kw: "まだほんやくされていない"
        ed = R._job_poly(3, self.s, self.h, "　　", self.s.strip(), "")
        self.assertEqual((ed[5], ed[6]), (False, "zh_empty_or_copy"))

    def test_exception_becomes_fail_edit(self):
        R._call_glm = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("链全败"))
        ed = R._job_poly(3, self.s, self.h, "　　", self.s.strip(), "")
        self.assertEqual((ed[4], ed[5]), ("poly", False))
        self.assertTrue(ed[6].startswith("err:"))

    def test_gloss_hint_forwarded(self):
        seen = {}
        R._call_glm = lambda system, body, **kw: (seen.update(kw), "你好，今天天气真好")[1]
        R._job_poly(3, self.s, self.h, "　　", self.s.strip(), "【本章hint】")
        self.assertEqual(seen.get("gloss_hint"), "【本章hint】")


class TestJobSwallow(unittest.TestCase):
    # 真实形态: 10 格 ASCII art 前缀 + 尾段台词(10 假名=20 格), 整行 30 格
    PREFIX = "abcdefghij"
    S = PREFIX + "ありがとうございます"
    H = PREFIX + "ありがとうございます"

    def setUp(self):
        self._call = R._call_glm
        self.seg = _fake_seg("text", 10, 20, 20)   # 尾段: 字符 10..20, 显示宽 20

    def tearDown(self):
        R._call_glm = self._call

    def test_honorific_kept_without_model(self):
        called = []
        R._call_glm = lambda *a, **kw: called.append(1) or "不该被调用"
        ed = R._job_swallow(5, self.S, self.H, self.seg, "やる夫様", ref_proper=self.H,
                            ref_model=self.S, ok_reason="swallow_translate", gloss_hint="")
        self.assertIsNotNone(ed)
        self.assertEqual(ed[6], "swallow_proper_noun")
        self.assertEqual(called, [], "专名様 走本地保留, 不应调模型")
        self.assertTrue(ed[3].startswith(self.PREFIX))

    def test_translate_ok(self):
        R._call_glm = lambda system, body, **kw: "谢谢"
        ed = R._job_swallow(5, self.S, self.H, self.seg, "ありがとう", ref_proper=self.H,
                            ref_model=self.S, ok_reason="swallow_translate", gloss_hint="")
        self.assertIsNotNone(ed)
        self.assertEqual((ed[4], ed[5], ed[6]), ("rebuild", True, "swallow_translate"))
        self.assertIn("谢谢", ed[3])
        self.assertEqual(R.T.disp_width(ed[3]) if hasattr(R, "T") else 30, 30)

    def test_oversize_returns_none_for_caller_fallback(self):
        R._call_glm = lambda system, body, **kw: "这是一个非常非常长的翻译结果绝对塞不进窄台词段"
        ed = R._job_swallow(5, self.S, self.H, self.seg, "ありがとう", ref_proper=self.H,
                            ref_model=self.S, ok_reason="swallow_translate", gloss_hint="")
        self.assertIsNone(ed, "超宽未产出 → None, 由调用方决定回退 edit")

    def test_exception_uses_custom_err_reason(self):
        R._call_glm = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("402"))
        ed = R._job_swallow(5, self.S, self.H, self.seg, "ありがとう", ref_proper=self.H,
                            ref_model=self.S, ok_reason="tail_translate:x", gloss_hint="",
                            err_reason="tail_translate_err", honorific=False)
        self.assertTrue(ed[6].startswith("tail_translate_err:"))


class TestRunJobsConcurrency(unittest.TestCase):
    def test_parallel_actually_overlaps(self):
        lock = threading.Lock()
        state = {"cur": 0, "max": 0}

        def job():
            with lock:
                state["cur"] += 1
                state["max"] = max(state["max"], state["cur"])
            time.sleep(0.05)
            with lock:
                state["cur"] -= 1
            return "x"

        R._run_jobs([(i, job) for i in range(8)], 8)
        self.assertGreater(state["max"], 1, "workers=8 应出现并发在飞")

    def test_workers_1_is_serial(self):
        lock = threading.Lock()
        state = {"cur": 0, "max": 0}

        def job():
            with lock:
                state["cur"] += 1
                state["max"] = max(state["max"], state["cur"])
            time.sleep(0.02)
            with lock:
                state["cur"] -= 1
            return "x"

        R._run_jobs([(i, job) for i in range(5)], 1)
        self.assertEqual(state["max"], 1)

    def test_results_by_line_and_exception_none(self):
        def boom():
            raise RuntimeError("x")
        out = R._run_jobs([(2, lambda: "b"), (1, boom), (3, lambda: "c")], 4)
        self.assertEqual(out[1], None)
        self.assertEqual(out[2], "b")

    def test_worker_count_source(self):
        old = os.environ.get("AA_REFINE_WORKERS")
        try:
            os.environ["AA_REFINE_WORKERS"] = "3"
            self.assertEqual(R._refine_workers(), 3)
            os.environ["AA_REFINE_WORKERS"] = "0"
            self.assertEqual(R._refine_workers(), 1)      # 下限 1
            os.environ.pop("AA_REFINE_WORKERS")
            self.assertGreaterEqual(R._refine_workers(), 1)
        finally:
            if old is None:
                os.environ.pop("AA_REFINE_WORKERS", None)
            else:
                os.environ["AA_REFINE_WORKERS"] = old


class TestChapterParallelEquivalence(unittest.TestCase):
    """整章：workers=1 与 workers=8 的结果必须逐条一致且按行号有序。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="refine_p1_")
        self.aa = os.path.join(self.tmp, "aa")
        self.out = os.path.join(self.tmp, "汉化")
        self.work = os.path.join(self.tmp, "work")
        for d in (self.aa, self.out, self.work, os.path.join(self.work, "line_meta_v2")):
            os.makedirs(d, exist_ok=True)
        self.base = "entry_1"
        dial = "　　こんにちは、今日はいい天気ですね"
        body = "\n".join([SRC_HEAD, SRC_POST] + [dial] * 12)   # 12 行漏译 → 12 个 job
        for p in (os.path.join(self.aa, self.base + ".txt"),
                  os.path.join(self.out, self.base + "_汉化.txt")):
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(body)
        with open(os.path.join(self.work, "line_meta_v2", self.base + ".json"),
                  "w", encoding="utf-8") as f:
            json.dump({}, f)
        self.cfg = {"aa_dir": self.aa, "out_dir": self.out, "work_dir": self.work}
        self._call = R._call_glm
        self._gloss = R._load_gloss_hint
        self.lock = threading.Lock()
        self.seen_threads = set()
        R._load_gloss_hint = lambda cfg, base: "【本章hint】"

        def mock(system, body, **kw):
            with self.lock:
                self.seen_threads.add(threading.current_thread().name)
            time.sleep(0.02)
            return f"你好，第{len(body)}号的译文"
        R._call_glm = mock

    def tearDown(self):
        R._call_glm, R._load_gloss_hint = self._call, self._gloss
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("AA_REFINE_WORKERS", None)
        os.environ.pop("AA_REFINE_BATCH", None)

    def _run(self, workers, batch=None):
        os.environ["AA_REFINE_WORKERS"] = str(workers)
        if batch is not None:
            os.environ["AA_REFINE_BATCH"] = str(batch)
        self.seen_threads.clear()
        self.calls = []
        b, stats, edits = R.refine_chapter(self.cfg, self.base)
        return stats, edits

    def test_parallel_matches_serial_and_is_ordered(self):
        # 批量开启后并发单位是"块"：单块时按设计串行 → 用 AA_REFINE_BATCH=4 让 12 行拆 3 块
        st1, ed1 = self._run(1, batch=4)
        st8, ed8 = self._run(8, batch=4)
        self.assertEqual(st1, st8)
        self.assertEqual(ed1, ed8, "并发与串行结果必须逐条一致")
        self.assertEqual([e[0] for e in ed8], sorted(e[0] for e in ed8), "edits 应按行号有序")
        self.assertEqual(len(ed8), 12)
        self.assertGreater(len(self.seen_threads), 1, "workers=8 应真的用到多个线程")
        self.assertTrue(all(e[5] for e in ed8), "mock 给的是合法中文 → 全 OK")


class TestUsageTagNoEnvMutation(unittest.TestCase):
    def test_env_untouched_and_tag_passed(self):
        import aa_backend as AB
        seen = {}
        _orig = AB.call_deepseek
        AB.call_deepseek = lambda system, body, **kw: (seen.update(kw), "译文")[1]
        _chain = R._REFINE_CHAIN
        R._REFINE_CHAIN = ["deepseek"]
        os.environ.pop("AA_USAGE_TAG", None)
        try:
            self.assertEqual(R._call_glm("S", "B"), "译文")
            self.assertNotIn("AA_USAGE_TAG", os.environ, "不应再篡改进程级 env")
            self.assertEqual(seen.get("tag"), "refine")
        finally:
            AB.call_deepseek = _orig
            R._REFINE_CHAIN = _chain


if __name__ == "__main__":
    unittest.main()
