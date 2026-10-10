# -*- coding: utf-8 -*-
"""P1 稳定性测试（2026-08-18 代码改进 T4.1-T4.3）。

覆盖：
- T4.1 proxy 单路径重试：每次 attempt 只试一种网络路径（直连→直连→代理→代理→直连），
  AA_USE_PROXY=0/1 强制单路径；失败重试次数正确
- T4.2 key 惰性缓存：_KEY_CACHE 缓存 + AA_KEY_RELOAD=1 强制重读
- T4.3 ckpt 原子写：_atomic_write_ckpt tmp + os.replace，无残留 tmp

零 API（mock urllib.request）。写操作在临时目录。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS_DIR)
sys.path.insert(0, os.path.join(SCRIPTS, "common"))
sys.path.insert(0, os.path.join(SCRIPTS, "works"))

import aa_translate as T


class _FakeResp:
    """模拟 urllib 响应：JSON body + context manager。"""

    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self._body).encode("utf-8")


class TestProxyRetry(unittest.TestCase):
    """T4.1：proxy_seq = [直连,直连,代理,代理,直连]；AA_USE_PROXY 强制单路径。"""

    def setUp(self):
        self.orig_open = T.urllib.request.urlopen
        self.orig_build = T.urllib.request.build_opener
        self.orig_sleep = T.time.sleep
        T.time.sleep = lambda s: None      # 禁退避等待
        self.direct_calls = 0
        self.proxy_calls = 0
        self.fail_first_n = 0              # 前 N 次调用抛错
        self.calls = 0

        def fake_urlopen(req, timeout=None):
            self.calls += 1
            self.direct_calls += 1
            if self.calls <= self.fail_first_n:
                raise OSError("conn refused")
            return _FakeResp({"output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]})

        def fake_build_opener(*a, **k):
            class _Op:
                def open(self, req, timeout=None):
                    self.calls = getattr(self, "calls", 0)
                    return fake_proxy_open(req, timeout)
            return _Op()

        def fake_proxy_open(req, timeout=None):
            self.calls += 1
            self.proxy_calls += 1
            if self.calls <= self.fail_first_n:
                raise OSError("proxy refused")
            return _FakeResp({"output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]})

        T.urllib.request.urlopen = fake_urlopen
        T.urllib.request.build_opener = fake_build_opener
        # 备份并注入假 key（避免读 .env）
        self.orig_key = T.get_key
        T.get_key = lambda: "fake-key"

    def tearDown(self):
        T.urllib.request.urlopen = self.orig_open
        T.urllib.request.build_opener = self.orig_build
        T.time.sleep = self.orig_sleep
        T.get_key = self.orig_key

    def _call(self):
        return T.call_api("sys", "body", max_tokens=100, timeout=5)

    def test_success_direct_first_attempt(self):
        # 直连第一次就成功 → 1 次调用，无代理
        self.fail_first_n = 0
        self.assertEqual(self._call(), "ok")
        self.assertEqual(self.direct_calls, 1)
        self.assertEqual(self.proxy_calls, 0)

    def test_direct_fail_then_proxy(self):
        # 直连 2 次失败 → 第 3 次走代理成功
        self.fail_first_n = 2
        self.assertEqual(self._call(), "ok")
        self.assertEqual(self.direct_calls, 2)
        self.assertEqual(self.proxy_calls, 1)
        self.assertEqual(self.calls, 3)

    def test_force_direct(self):
        # AA_USE_PROXY=0 → 只试直连，代理从不使用
        os.environ["AA_USE_PROXY"] = "0"
        try:
            self.fail_first_n = 99   # 全部失败
            with self.assertRaises(RuntimeError):
                self._call()
            self.assertEqual(self.proxy_calls, 0)
            self.assertEqual(self.direct_calls, 5)   # 5 次全直连
        finally:
            os.environ.pop("AA_USE_PROXY", None)

    def test_force_proxy(self):
        # AA_USE_PROXY=1 → 只试代理
        os.environ["AA_USE_PROXY"] = "1"
        try:
            self.fail_first_n = 0
            self.assertEqual(self._call(), "ok")
            self.assertEqual(self.direct_calls, 0)
            self.assertEqual(self.proxy_calls, 1)
        finally:
            os.environ.pop("AA_USE_PROXY", None)

    def test_all_fail_raises(self):
        # 5 次全失败 → RuntimeError
        self.fail_first_n = 99
        with self.assertRaises(RuntimeError):
            self._call()
        self.assertEqual(self.calls, 5)


class TestKeyCache(unittest.TestCase):
    """T4.2：get_key 惰性缓存 + AA_KEY_RELOAD 强制重读。

    2026-10-08（方案 doc/方案归档/工作台设置页与配置文件方案_2026-10-08.md 阶段 1）：key 读取统一走 aa_env，
    不再看 `T.ENV_FILE`；隔离改用 `AA_ENV_PATH` + 把 hermes 兜底路径打空（否则会读到真 key）。
    """

    def setUp(self):
        import aa_env
        self.aa_env = aa_env
        self.tmp = tempfile.mkdtemp(prefix="hermes-test-key-")
        self.env_file = os.path.join(self.tmp, ".env")
        with open(self.env_file, "w", encoding="utf-8") as f:
            f.write('OPENCODE_GO_API_KEY="key-v1"\n')
        self.orig_cache = T._KEY_CACHE
        self.orig_env_path = os.environ.get("AA_ENV_PATH")
        self.orig_aa_env = os.environ.get("AA_ENV")            # ⚠ 优先级高于 AA_ENV_PATH
        self.orig_key = os.environ.get("OPENCODE_GO_API_KEY")  # ⚠ 真环境变量优先于 .env
        self.orig_fallback = aa_env.fallback_paths
        self.orig_sig = aa_env._CACHE.get("sig")
        # 夹具要**独占**取值来源，否则操作者 shell 里带 AA_ENV / *_API_KEY 时这 4 个用例会假失败
        # （实测：AA_ENV 指向别处 + 环境里有 OPENCODE_GO_API_KEY → 4 例全红，包内自检因此误报）
        os.environ.pop("AA_ENV", None)
        os.environ.pop("OPENCODE_GO_API_KEY", None)
        os.environ["AA_ENV_PATH"] = self.env_file
        aa_env.fallback_paths = lambda: ()          # 模拟「没有 hermes 档案」的干净机器
        aa_env._CACHE["sig"] = None                 # 别吃到父进程/上个用例缓存的 .env
        T._KEY_CACHE = None

    def tearDown(self):
        T._KEY_CACHE = self.orig_cache
        self.aa_env.fallback_paths = self.orig_fallback
        self.aa_env._CACHE["sig"] = self.orig_sig
        for k, v in (("AA_ENV_PATH", self.orig_env_path), ("AA_ENV", self.orig_aa_env),
                     ("OPENCODE_GO_API_KEY", self.orig_key)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        os.environ.pop("AA_KEY_RELOAD", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reads_key(self):
        self.assertEqual(T.get_key(), "key-v1")

    def test_caches_after_first_read(self):
        T.get_key()
        # 改文件内容，缓存应仍返回旧值
        with open(self.env_file, "w", encoding="utf-8") as f:
            f.write('OPENCODE_GO_API_KEY="key-v2"\n')
        self.assertEqual(T.get_key(), "key-v1")   # 缓存命中

    def test_reload_forced(self):
        T.get_key()
        with open(self.env_file, "w", encoding="utf-8") as f:
            f.write('OPENCODE_GO_API_KEY="key-v2"\n')
        os.environ["AA_KEY_RELOAD"] = "1"
        self.assertEqual(T.get_key(), "key-v2")   # 强制重读
        os.environ.pop("AA_KEY_RELOAD", None)
        self.assertEqual(T.get_key(), "key-v2")   # 已刷新

    def test_missing_key_raises(self):
        with open(self.env_file, "w", encoding="utf-8") as f:
            f.write("OTHER=1\n")
        T._KEY_CACHE = None
        with self.assertRaises(SystemExit):
            T.get_key()


class TestAtomicWriteCkpt(unittest.TestCase):
    """T4.3：ckpt 原子写 tmp + os.replace，无残留 tmp。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hermes-test-ckpt-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_writes_ckpt_no_tmp(self):
        ckpt = os.path.join(self.tmp, "ch1.txt.ckpt.json")
        T._atomic_write_ckpt(ckpt, {"__lines": 10, "1": "你好"})
        self.assertTrue(os.path.exists(ckpt))
        self.assertFalse(os.path.exists(ckpt + ".tmp"))   # 无残留 tmp
        with open(ckpt, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["__lines"], 10)
        self.assertEqual(data["1"], "你好")

    def test_overwrites_existing(self):
        ckpt = os.path.join(self.tmp, "ch1.txt.ckpt.json")
        T._atomic_write_ckpt(ckpt, {"__lines": 1})
        T._atomic_write_ckpt(ckpt, {"__lines": 2})
        with open(ckpt, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["__lines"], 2)

    def test_no_tmp_left_on_old(self):
        # 多次写入，tmp 从不残留
        ckpt = os.path.join(self.tmp, "ch2.txt.ckpt.json")
        for i in range(3):
            T._atomic_write_ckpt(ckpt, {"__lines": i})
        self.assertEqual(os.listdir(self.tmp), ["ch2.txt.ckpt.json"])


if __name__ == "__main__":
    unittest.main()
