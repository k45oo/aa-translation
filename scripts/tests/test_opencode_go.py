# -*- coding: utf-8 -*-
"""opencode Go 门接入（2026-10-10）单测：session 头、_call_named 分发、关思考自适应、TOML 链首。

零 API：网络层全部用假 urlopen 顶掉。
"""
import io
import json
import os
import sys
import unittest
import urllib.error

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
for _p in (os.path.dirname(_SCRIPTS), _SCRIPTS, os.path.join(_SCRIPTS, "common"),
           os.path.join(_SCRIPTS, "works")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import aa_backend as AB   # noqa: E402
from _work_guard import repo_only   # noqa: E402


class TestZenSession(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("AA_OCGO_SESSION")
        AB.set_zen_session(None)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("AA_OCGO_SESSION", None)
        else:
            os.environ["AA_OCGO_SESSION"] = self._old
        AB.set_zen_session(None)

    def test_默认值_每章值_env覆盖(self):
        self.assertEqual(AB.zen_session(), "aa-translation")
        AB.set_zen_session("aa-fukou-entry_13276")
        self.assertEqual(AB.zen_session(), "aa-fukou-entry_13276")
        os.environ["AA_OCGO_SESSION"] = "env-wins"
        self.assertEqual(AB.zen_session(), "env-wins")

    def test_非ASCII被换成横线且截断(self):
        # HTTP 头只能 latin-1：中文直接 UnicodeEncodeError（实测踩过），必须净化
        AB.set_zen_session("aa-白头-entry_1")
        sid = AB.zen_session()
        self.assertTrue(sid.isascii(), sid)
        self.assertNotIn("白", sid)
        AB.set_zen_session("x" * 200)
        self.assertLessEqual(len(AB.zen_session()), 64)

    def test_空值回默认(self):
        AB.set_zen_session("")
        AB.set_zen_session(None)
        self.assertEqual(AB.zen_session(), "aa-translation")


class _FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestCallOpencodeGo(unittest.TestCase):
    def setUp(self):
        self.seen = []
        self.orig = AB.urllib.request.urlopen
        os.environ.pop("AA_OCGO_URL", None)
        AB.set_zen_session(None)

    def tearDown(self):
        AB.urllib.request.urlopen = self.orig
        os.environ.pop("AA_OCGO_URL", None)
        AB.set_zen_session(None)

    def _install(self, handler):
        def fake(req, timeout=None):
            body = json.loads(req.data.decode("utf-8"))
            self.seen.append({"url": req.full_url, "headers": dict(req.headers), "body": body})
            return handler(len(self.seen))
        AB.urllib.request.urlopen = fake

    def test_打到Go门_带session头与浏览器UA(self):
        ok = {"choices": [{"message": {"content": "好"}, "finish_reason": "stop"}],
              "usage": {"prompt_tokens": 10, "completion_tokens": 1}}
        self._install(lambda n: _FakeResp(ok))
        AB.set_zen_session("aa-test-ch1")
        out = AB.call_opencode_go("sys", "user", max_tokens=16, timeout=5)
        self.assertEqual(out, "好")
        got = self.seen[0]
        self.assertIn("/zen/go/v1/chat/completions", got["url"])
        self.assertEqual(got["headers"].get("X-opencode-session"), "aa-test-ch1")
        self.assertIn("Mozilla", got["headers"].get("User-agent", ""))
        self.assertEqual(got["body"]["reasoning_effort"], "none")
        self.assertEqual(got["body"]["model"], "deepseek-v4.1-flash")

    def test_400提到reasoning时自适应改thinking_disabled(self):
        def handler(n):
            if n == 1:
                raise urllib.error.HTTPError(
                    "u", 400, "bad",
                    {}, io.BytesIO(b'{"error":{"message":"none reasoning effort is not supported"}}'))
            return _FakeResp({"choices": [{"message": {"content": "好"}, "finish_reason": "stop"}],
                              "usage": {"prompt_tokens": 9, "completion_tokens": 1}})
        self._install(handler)
        out = AB.call_opencode_go("sys", "user", max_tokens=16, timeout=5)
        self.assertEqual(out, "好")
        self.assertEqual(len(self.seen), 2, "应当自动重试一次")
        self.assertEqual(self.seen[0]["body"].get("reasoning_effort"), "none")
        self.assertNotIn("reasoning_effort", self.seen[1]["body"])
        self.assertEqual(self.seen[1]["body"].get("thinking"), {"type": "disabled"})

    def test_UA可换_端点可覆盖(self):
        os.environ["AA_OCGO_URL"] = "https://example.invalid/v1/chat/completions"
        self._install(lambda n: _FakeResp({"choices": [{"message": {"content": "x"},
                                                        "finish_reason": "stop"}], "usage": {}}))
        AB.call_opencode_go("s", "u", max_tokens=8, timeout=5)
        self.assertEqual(self.seen[0]["url"], "https://example.invalid/v1/chat/completions")


class TestDispatchAndChain(unittest.TestCase):
    def test_call_named分发到Go门(self):
        called = {}
        orig = AB.call_opencode_go

        def fake(system, body, **kw):
            called.update(kw)
            return "ok"

        AB.call_opencode_go = fake
        try:
            self.assertEqual(AB._call_named("opencode_go", "s", "u", model="m"), "ok")
        finally:
            AB.call_opencode_go = orig
        self.assertEqual(called.get("model"), "m")
        self.assertIn("max_tokens", called)

    def test_未知后端仍抛(self):
        with self.assertRaises(RuntimeError):
            AB._call_named("nope_backend", "s", "u")

    @repo_only("断言生成者 TOML 里 opencode_go 的链首配置")
    def test_TOML链首与标志位(self):
        import aa_config as AC
        for r18 in (False, True):
            head = AC.backend_chain(r18)[0]
            self.assertEqual(head, ("opencode_go", "deepseek-v4.1-flash"),
                             "两条链的链首都应是 opencode Go 门的 dsv4.1-flash")
        cfg = AC.backend("opencode_go")
        self.assertEqual(cfg.get("key_env"), "OPENCODE_GO_API_KEY")
        self.assertTrue(AC.cache_safe("opencode_go"), "标了 cache=true → 章内 pin 目标")
        self.assertTrue(AC.r18_safe("opencode_go"), "实测 R18 放行")
        rate, _ = AC.pricing_for("opencode_go", "deepseek-v4.1-flash")
        self.assertTrue(rate and rate.get("out"), "计价条目必须在（否则报表算不出钱）")


class TestZenRetriesBudget(unittest.TestCase):
    """`retries=` 预算（2026-10-10）：划词/短请求传 1；**400 自适应换法那一跳不占预算**。"""

    def setUp(self):
        self.calls = []
        self.orig = AB.urllib.request.urlopen
        AB.set_zen_session(None)

    def tearDown(self):
        AB.urllib.request.urlopen = self.orig
        AB.set_zen_session(None)

    def _install(self, handler):
        def fake(req, timeout=None):
            self.calls.append(json.loads(req.data.decode("utf-8")))
            return handler(len(self.calls))
        AB.urllib.request.urlopen = fake

    @staticmethod
    def _http400():
        return urllib.error.HTTPError(
            "u", 400, "bad", {},
            io.BytesIO(b'{"error":{"message":"none reasoning effort is not supported"}}'))

    @staticmethod
    def _ok():
        return _FakeResp({"choices": [{"message": {"content": "好"}, "finish_reason": "stop"}],
                          "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    def test_retries1时400自适应仍会重发一次(self):
        def handler(n):
            if n == 1:
                raise self._http400()
            return self._ok()
        self._install(handler)
        out = AB.call_opencode_go("s", "u", max_tokens=8, timeout=5, retries=1)
        self.assertEqual(out, "好")
        self.assertEqual(len(self.calls), 2, "retries=1 时自适应换法必须还有一次机会（否则等于白关思考）")
        self.assertEqual(self.calls[0].get("reasoning_effort"), "none")
        self.assertEqual(self.calls[1].get("thinking"), {"type": "disabled"})

    def test_retries1_普通失败只发一次(self):
        def handler(n):
            raise RuntimeError("connection reset")
        self._install(handler)
        with self.assertRaises(RuntimeError):
            AB.call_opencode_go("s", "u", max_tokens=8, timeout=5, retries=1)
        self.assertEqual(len(self.calls), 1, "retries=1 = 只发一次（划词失败要快、好换下一候选）")

    def test_默认retries3(self):
        def handler(n):
            raise RuntimeError("boom")
        self._install(handler)
        with self.assertRaises(RuntimeError):
            AB.call_opencode_go("s", "u", max_tokens=8, timeout=5)
        self.assertEqual(len(self.calls), 3, "整章链上的默认仍是 3 次")


if __name__ == "__main__":
    unittest.main()
