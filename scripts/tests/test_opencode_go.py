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

import contextlib        # noqa: E402
import aa_backend as AB   # noqa: E402
import aa_translate as ABA   # noqa: E402  （顶层 aa_translate：云端直连路径回归）
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
        out = AB.call_opencode_go("sys", "user", max_tokens=16, timeout=5, key="test-key")
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
        out = AB.call_opencode_go("sys", "user", max_tokens=16, timeout=5, key="test-key")
        self.assertEqual(out, "好")
        self.assertEqual(len(self.seen), 2, "应当自动重试一次")
        self.assertEqual(self.seen[0]["body"].get("reasoning_effort"), "none")
        self.assertNotIn("reasoning_effort", self.seen[1]["body"])
        self.assertEqual(self.seen[1]["body"].get("thinking"), {"type": "disabled"})

    def test_UA可换_端点可覆盖(self):
        os.environ["AA_OCGO_URL"] = "https://example.invalid/v1/chat/completions"
        self._install(lambda n: _FakeResp({"choices": [{"message": {"content": "x"},
                                                        "finish_reason": "stop"}], "usage": {}}))
        AB.call_opencode_go("s", "u", max_tokens=8, timeout=5, key="test-key")
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
        out = AB.call_opencode_go("s", "u", max_tokens=8, timeout=5, retries=1, key="test-key")
        self.assertEqual(out, "好")
        self.assertEqual(len(self.calls), 2, "retries=1 时自适应换法必须还有一次机会（否则等于白关思考）")
        self.assertEqual(self.calls[0].get("reasoning_effort"), "none")
        self.assertEqual(self.calls[1].get("thinking"), {"type": "disabled"})

    def test_retries1_普通失败只发一次(self):
        def handler(n):
            raise RuntimeError("connection reset")
        self._install(handler)
        with self.assertRaises(RuntimeError):
            AB.call_opencode_go("s", "u", max_tokens=8, timeout=5, retries=1, key="test-key")
        self.assertEqual(len(self.calls), 1, "retries=1 = 只发一次（划词失败要快、好换下一候选）")

    def test_默认retries3(self):
        def handler(n):
            raise RuntimeError("boom")
        self._install(handler)
        with self.assertRaises(RuntimeError):
            AB.call_opencode_go("s", "u", max_tokens=8, timeout=5, key="test-key")
        self.assertEqual(len(self.calls), 3, "整章链上的默认仍是 3 次")


class TestBackendAliasAndCloudFallback(unittest.TestCase):
    """2026-10-11 事故回归：`AA_BACKEND=opencode_go` 必须被 `aa_backend.backend()` 认出。

    漏登记 → backend() 返回 "cloud" → `aa_translate.call_api` 走云端直连
    (gateway=commandcode, model=AA_MODEL 里的 opencode 裸模型名 `deepseek-v4.1-flash`)
    → 网关回 400 unsupported_model、每块重试 5 次后失败 → **整章静默留日文**
    （实测 3 个作品 254 块全灭：供給源 217 / ハーレム大王国 26 / ドスケベナイト 11）。
    """

    def setUp(self):
        self._env = {k: os.environ.get(k) for k in
                     ("AA_BACKEND", "AA_BACKEND_AUTO", "AA_MODEL", "AA_OCGO_MODEL",
                      "OPENCODE_GO_API_KEY", "AA_FREE_MODE", "AA_OCGO_URL", "AA_CHAIN",
                      "AA_PIN_CACHE_ENDPOINT", "AA_COMMON_PREFIX", "AA_USAGE_FILE",
                      "AA_USE_PROXY")}
        self._orig_urlopen = AB.urllib.request.urlopen
        AB.reset_preferred_backend()
        os.environ.pop("AA_FREE_MODE", None)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        AB.urllib.request.urlopen = self._orig_urlopen
        AB.reset_preferred_backend()
        ABA._WARNED_UNKNOWN_BACKEND.clear()

    def test_backend别名认出opencode两门(self):
        for val in ("opencode_go", "opencode-go", "ocgo"):
            os.environ["AA_BACKEND"] = val
            self.assertEqual(AB.backend(), "opencode_go", f"{val} → opencode_go")
        for val in ("opencode_zen", "opencode-zen", "oczen"):
            os.environ["AA_BACKEND"] = val
            self.assertEqual(AB.backend(), "opencode_zen", f"{val} → opencode_zen")

    def test_两门有专属模型env(self):
        import aa_config as AC
        self.assertEqual(AC.model_env("opencode_go"), "AA_OCGO_MODEL",
                         "缺项会回 AA_MODEL(=云端直连的模型名), 把 opencode 裸模型名漏给错了网关")
        self.assertEqual(AC.model_env("opencode_zen"), "AA_OCZEN_MODEL")

    def test_自动后端模型env不落AA_MODEL(self):
        """translate_runner 的 `setdefault(model_env(链首), 模型)` 不得写进 AA_MODEL。"""
        import aa_config as AC
        for r18 in (False, True):
            b, m = AC.auto_backend(r18)
            self.assertNotEqual(AC.model_env(b), "AA_MODEL",
                                f"链首 {b} 的 model_env 回落到 AA_MODEL → 会覆盖云端直连模型名")

    def test_AA_BACKEND_opencode_go时不走云端直连(self):
        """真发一次 call_api（urlopen 假体）：必须打到 Go 门，而不是 commandcode 云端直连。"""
        seen = {}

        def fake(req, timeout=None):
            seen["url"] = req.full_url
            seen["model"] = json.loads(req.data.decode("utf-8"))["model"]
            return _FakeResp({"choices": [{"message": {"content": "1|好"},
                                           "finish_reason": "stop"}],
                              "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

        AB.urllib.request.urlopen = fake
        os.environ["AA_BACKEND"] = "opencode_go"
        os.environ["AA_OCGO_MODEL"] = "deepseek-v4.1-flash"   # runner setdefault 写进来的那个值
        os.environ["OPENCODE_GO_API_KEY"] = "test-key"
        os.environ["AA_CHAIN"] = "opencode_go"   # 钉住端点：否则端点轮询会先试别的候选(顺序不定)
        os.environ["AA_USAGE_FILE"] = os.devnull
        ABA.call_api("sys", "1|テスト", max_tokens=16, timeout=5)
        self.assertIn("/zen/go/v1/chat/completions", seen.get("url", ""))
        self.assertNotIn("commandcode", seen.get("url", ""))
        self.assertEqual(seen.get("model"), "deepseek-v4.1-flash")


class TestChainBackendRegistrations(unittest.TestCase):
    """链条目上的后端名必须**三处齐备**，只改一处就是静默坏：

    ① `aa_backend.backend()` 别名表（缺 → 回落云端直连，见上）
    ② `aa_config.model_env()` 专属模型 env（缺 → 落到 AA_MODEL）
    ③ `_call_named` 分发（缺 → “未知后端” 抛错）
    ④ `selection._call_fn` 划词分发（缺 → 该候选被静默跳过，2026-10-10 事故）
    """

    @repo_only("按生成者 TOML 链枚举后端名")
    def test_链上每个后端三处齐备(self):
        import ast
        import aa_config as AC
        names = set()
        for r18 in (False, True):
            for it in AC.chain_catalog(r18)[0]:
                names.add(it["backend"])
        for it in AC.selection_catalog()[0]:
            names.add(it["backend"])
        self.assertTrue(names)

        with open(os.path.join(_SCRIPTS, "common", "aa_backend.py"), encoding="utf-8") as _f:
            src = _f.read()
        dispatched = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.FunctionDef) and node.name == "_call_named":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Compare) and sub.comparators and \
                            isinstance(sub.comparators[0], ast.Constant):
                        dispatched.add(sub.comparators[0].value)

        old = os.environ.get("AA_BACKEND")
        try:
            for n in sorted(names):
                os.environ["AA_BACKEND"] = n
                self.assertEqual(AB.backend(), n,
                                 f"{n}: backend() 别名表缺 → 会静默回落云端直连(400+整章留日文)")
                self.assertNotEqual(AC.model_env(n), "AA_MODEL",
                                    f"{n}: model_env() 缺专属 env → 落到 AA_MODEL")
                self.assertIn(n, dispatched, f"{n}: _call_named 缺分发")
        finally:
            if old is None:
                os.environ.pop("AA_BACKEND", None)
            else:
                os.environ["AA_BACKEND"] = old

    def test_未知AA_BACKEND会喊一次(self):
        """未知后端名字要 stderr 提醒（否则只能靠翻日志反推“为什么打到别家网关”）。"""
        ABA._WARNED_UNKNOWN_BACKEND.clear()
        _bak = {k: os.environ.get(k) for k in ("AA_BACKEND", "AA_USE_PROXY")}
        os.environ["AA_BACKEND"] = "nope_backend"
        os.environ["AA_USE_PROXY"] = "0"        # 只走 urlopen 那一路，避免真发代理请求
        err = io.StringIO()
        orig_open, orig_sleep = ABA.urllib.request.urlopen, ABA.time.sleep

        def boom(*a, **k):
            raise RuntimeError("no network")

        ABA.urllib.request.urlopen = boom
        ABA.time.sleep = lambda *a: None
        try:
            with contextlib.redirect_stderr(err):
                with self.assertRaises(RuntimeError):
                    ABA.call_api("s", "1|x", max_tokens=8, timeout=1)
        finally:
            ABA.urllib.request.urlopen = orig_open
            ABA.time.sleep = orig_sleep
            for k, v in _bak.items():           # 全量还原（含 AA_USE_PROXY，否则污染 test_stability）
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            ABA._WARNED_UNKNOWN_BACKEND.clear()
        self.assertIn("nope_backend", err.getvalue())


if __name__ == "__main__":
    unittest.main()
