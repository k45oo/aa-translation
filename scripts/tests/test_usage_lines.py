# -*- coding: utf-8 -*-
"""usage.jsonl 模型↔行号(lines)落盘逻辑测试(2026-09-04)。"""
import os, sys, json, tempfile, unittest, threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"))

import aa_backend


class TestUsageLines(unittest.TestCase):
    def setUp(self):
        self._old_file = os.environ.get("AA_USAGE_FILE")
        self._uf = os.path.join(tempfile.gettempdir(), "test_usage_lines_ut.jsonl")
        os.environ["AA_USAGE_FILE"] = self._uf
        if os.path.exists(self._uf):
            os.remove(self._uf)

    def tearDown(self):
        aa_backend.clear_usage_lines()
        if self._old_file is None:
            os.environ.pop("AA_USAGE_FILE", None)
        else:
            os.environ["AA_USAGE_FILE"] = self._old_file
        if os.path.exists(self._uf):
            os.remove(self._uf)

    def _read(self):
        recs = []
        if os.path.exists(self._uf):
            with open(self._uf, encoding="utf-8") as f:
                for l in f:
                    if l.strip():
                        recs.append(json.loads(l))
        return recs

    def test_lines_written(self):
        aa_backend.clear_usage_lines()
        aa_backend.set_usage_lines([55, 56, 860])
        aa_backend._log_usage(100, 50, model="m", backend="b")
        recs = self._read()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["lines"], [55, 56, 860])
        self.assertEqual(recs[0]["model"], "m")

    def test_possibly_no_lines_when_cleared(self):
        aa_backend.clear_usage_lines()
        aa_backend._log_usage(5, 5, model="m2", backend="b")
        recs = self._read()
        self.assertEqual(len(recs), 1)
        self.assertNotIn("lines", recs[0])

    def test_thread_isolation(self):
        """thread-local context 隔离: 各线程 set 的 lines 互不覆盖(不并发写文件诱发竞态)。"""
        results = {}
        barrier = threading.Barrier(3)

        def worker(idx, lines):
            aa_backend.clear_usage_lines()
            aa_backend.set_usage_lines(lines)
            # 让所有线程都 set 完后才读自己——验证 context 是 per-thread 的
            barrier.wait()
            results[idx] = getattr(aa_backend._USAGE_CTX, "lines", None)

        ts = [threading.Thread(target=worker, args=(i, [i * 10, i * 10 + 1])) for i in range(3)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        for i in range(3):
            self.assertEqual(results[i], [i * 10, i * 10 + 1])


if __name__ == "__main__":
    unittest.main()
