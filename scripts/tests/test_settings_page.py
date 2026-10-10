# -*- coding: utf-8 -*-
"""设置页 端到端（方案 doc/方案归档/工作台设置页与配置文件方案_2026-10-08.md 阶段 6）。

用 fastapi TestClient 真发请求（不起 uvicorn），但**写盘隔离在临时 .env**（AA_ENV_PATH），
并断言「保存 → .env → os.environ → aa_config」整条生效链。零 API（不测 test-key/test-proxy 的真实联网）。
"""
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "works")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import HTTPException              # noqa: E402

import aa_env                                  # noqa: E402
import aa_config as AC                         # noqa: E402
from aa_web_app import main as M               # noqa: E402   （直接调路由函数，不装 httpx/TestClient）
from _work_guard import repo_only  # noqa: E402 （发行包内跳过生成者专属用例）

_TRACKED = ("AA_ENV", "AA_CHAIN", "AA_REFINE_BACKEND", "AA_SEL_CHAIN", "AA_SEL_ENABLE",
            "AA_PROXY", "AA_USE_PROXY", "SOME_TEST_KEY", "AA_TEST_DUMMY_KEY")


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aa_settings_")
        self.env_file = os.path.join(self.tmp, ".env")
        self._old = {k: os.environ.get(k) for k in _TRACKED}
        for k in _TRACKED:
            os.environ.pop(k, None)
        os.environ["AA_ENV_PATH"] = self.env_file
        self._old["AA_ENV_PATH"] = None
        aa_env.load(force=True)
        AC.reload()

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        aa_env.load(force=True)
        AC.reload()

    def _write_env(self, text):
        with open(self.env_file, "w", encoding="utf-8") as f:
            f.write(text)
        aa_env.load(force=True)
        AC.reload()

    def _env_text(self):
        with open(self.env_file, encoding="utf-8") as f:
            return f.read()

    def _post(self, body):
        """等价于 POST /api/settings（直接调路由函数；HTTPException 原样抛）。"""
        return M.api_settings_save(M.SettingsReq(**body))

    def _state(self):
        return M.api_settings()


class TestStateView(_Base):
    @repo_only("断言生成者密钥/链在设置页的渲染状态")
    def test_页面模板可渲染且状态完整(self):
        # 模板用 jinja2 直接渲染（不起服务）：抓住 settings.html 的语法/变量错
        from jinja2 import Environment, FileSystemLoader
        tdir = os.path.join(_ROOT, "aa_web_app", "templates")
        env = Environment(loader=FileSystemLoader(tdir))
        html = env.get_template("settings.html").render(active="settings")
        for kw in ("密钥", "翻译链", "校对链", "划词", "本地代理", "settings.js"):
            self.assertIn(kw, html)

        j = self._state()
        self.assertTrue(j["ok"])
        self.assertEqual(j["env"]["path"], self.env_file)
        self.assertTrue(j["keys"], "key 清单来自 TOML，不该为空")
        # 密钥只回掩码：不能出现任何 key 的明文
        for k in j["keys"]:
            self.assertIn("masked", k)
            self.assertNotIn("value", k)
            if k["configured"]:
                real = aa_env.read_key(k["name"])
                self.assertTrue(real)
                self.assertTrue(k["masked"].endswith(real[-4:]))
                self.assertNotEqual(k["masked"], real)
        m = j["models"]
        for kind in ("translate", "proofread", "selection"):
            self.assertTrue(m[kind]["entries"])
            self.assertTrue(m[kind]["default"])
            self.assertEqual(sorted(m[kind]["selected"]), sorted(m[kind]["default"]),
                             "未配置时勾选 = 默认集")

    def test_条目带id且可分开选(self):
        j = self._state()
        ids = [e["id"] for e in j["models"]["translate"]["entries"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all("|" in i for i in ids))


class TestSaveKeys(_Base):
    def test_只写改动项_保留注释与未改动行(self):
        self._write_env("# 头注释\nKEEP_ME=1\n"
                        "AA_TEST_DUMMY_KEY=old\nSOME_TEST_KEY=old2\n")
        j = self._post({"section": "keys",
                        "set": {"AA_TEST_DUMMY_KEY": "new-value", "SOME_TEST_KEY": "new2"}})
        self.assertTrue(j["ok"])
        txt = self._env_text()
        self.assertIn("# 头注释", txt)          # 注释保留
        self.assertIn("KEEP_ME=1", txt)         # 未提及的行保留
        self.assertIn("AA_TEST_DUMMY_KEY=new-value", txt)
        self.assertIn("SOME_TEST_KEY=new2", txt)
        self.assertNotIn("old", txt)
        # 保存即生效：AA_* 前缀的直接进 os.environ（新启动的任务子进程继承）
        self.assertEqual(os.environ.get("AA_TEST_DUMMY_KEY"), "new-value")
        # 非 AA_/非白名单的 key 按名读文件即可（key 读取都走 aa_env.read_key）
        self.assertEqual(aa_env.read_key("SOME_TEST_KEY"), "new2")
        # 清单来源是 TOML（不是 .env 里有什么就列什么）——自定义键名不出现在设置页
        st = self._state()
        self.assertNotIn("SOME_TEST_KEY", [k["name"] for k in st["keys"]])
        # 掩码口径（真实 key 的行为由 TestStateView 覆盖）
        self.assertEqual(aa_env.mask("sk-abcdefgh"), "…efgh")
        self.assertEqual(aa_env.mask("new2"), "…", "≤4 字符一律全掩")

    def test_清除删除该行(self):
        self._write_env("SOME_TEST_KEY=v\nOTHER=1\n")
        j = self._post({"section": "keys", "clear": ["SOME_TEST_KEY"]})
        self.assertTrue(j["ok"])
        txt = self._env_text()
        self.assertNotIn("SOME_TEST_KEY", txt)
        self.assertIn("OTHER=1", txt)
        self.assertNotIn("SOME_TEST_KEY", os.environ)

    def test_空值不写(self):
        self._write_env("SOME_TEST_KEY=v\n")
        self._post({"section": "keys", "set": {}})
        self.assertIn("SOME_TEST_KEY=v", self._env_text())


class TestSaveModels(_Base):
    @repo_only("断言生成者密钥/链在设置页的状态（包里无密钥）")
    def test_默认集不写env_子集写id_全空删env(self):
        st = self._state()
        dflt = st["models"]["translate"]["default"]
        self._post({"section": "models", "translate": dflt, "proofread": []})
        self.assertNotIn("AA_CHAIN", self._env_text(), "== 默认集 → 不写（保住兜底端点）")

        one = dflt[0]
        self._post({"section": "models", "translate": [one], "proofread": []})
        self.assertIn("AA_CHAIN=%s" % one, self._env_text())
        self.assertEqual([b for b, _m in AC.backend_chain(False)][0], one.split("|")[0])

        self._post({"section": "models", "translate": [], "proofread": []})
        self.assertNotIn("AA_CHAIN", self._env_text(), "空 = 整条链 → 删掉 env")

    def test_未识别id被丢掉(self):
        self._post({"section": "models", "translate": ["nope|x"], "proofread": []})
        self.assertNotIn("AA_CHAIN", self._env_text())

    @repo_only("断言生成者校对链在设置页的状态")
    def test_校对链独立写(self):
        st = self._state()
        d = st["models"]["proofread"]["default"]
        self._post({"section": "models", "translate": [], "proofread": [d[0]]})
        txt = self._env_text()
        self.assertIn("AA_REFINE_BACKEND=%s" % d[0], txt)
        self.assertNotIn("AA_CHAIN", txt)


class TestSaveSelection(_Base):
    def test_开关关掉写0_打开删env(self):
        self._post({"section": "selection", "sel_enabled": False, "sel_chain": []})
        self.assertIn("AA_SEL_ENABLE=0", self._env_text())
        from aa_web_app import selection as SEL
        self.assertFalse(SEL.sel_enabled())
        self._post({"section": "selection", "sel_enabled": True, "sel_chain": []})
        self.assertNotIn("AA_SEL_ENABLE", self._env_text())
        self.assertTrue(SEL.sel_enabled())

    def test_划词链默认不写_自定义写(self):
        st = self._state()
        d = st["models"]["selection"]["default"]
        self._post({"section": "selection", "sel_enabled": True, "sel_chain": d})
        self.assertNotIn("AA_SEL_CHAIN", self._env_text())
        self._post({"section": "selection", "sel_enabled": True, "sel_chain": [d[1]]})
        self.assertIn("AA_SEL_CHAIN=%s" % d[1], self._env_text())
        from aa_web_app import selection as SEL
        self.assertEqual(len(SEL.sel_chain()), 1)
        self.assertEqual(SEL.sel_chain()[0], tuple(d[1].split("|", 1)))


class TestSaveProxy(_Base):
    def test_写入与清除与校验(self):
        self._post({"section": "proxy", "proxy_url": "http://127.0.0.1:7890", "proxy_mode": "1"})
        txt = self._env_text()
        self.assertIn("AA_PROXY=http://127.0.0.1:7890", txt)
        self.assertIn("AA_USE_PROXY=1", txt)
        self.assertEqual(AC.proxy_url(), "http://127.0.0.1:7890")
        self.assertEqual(AC.use_proxy_mode(), "1")
        self.assertIsNotNone(AC.proxy_opener(), "策略 1 → 走代理")
        # auto = 默认 → 删掉 env
        self._post({"section": "proxy", "proxy_url": "http://127.0.0.1:7890", "proxy_mode": "auto"})
        self.assertNotIn("AA_USE_PROXY", self._env_text())
        self.assertEqual(AC.use_proxy_mode(), "auto")
        # 清除 → 回 TOML
        self._post({"section": "proxy", "clear_url": True})
        self.assertNotIn("AA_PROXY", self._env_text())
        self.assertEqual(AC.proxy_url(), AC.load().get("default", {}).get("proxy"))

    def test_非法地址与策略被拒(self):
        with self.assertRaises(HTTPException) as cm:
            self._post({"section": "proxy", "proxy_url": "127.0.0.1:10808"})
        self.assertEqual(cm.exception.status_code, 422)
        with self.assertRaises(HTTPException) as cm2:
            self._post({"section": "proxy", "proxy_mode": "yes"})
        self.assertEqual(cm2.exception.status_code, 422)

    def test_未知区块被拒(self):
        with self.assertRaises(HTTPException) as cm:
            self._post({"section": "nope"})
        self.assertEqual(cm.exception.status_code, 422)

    def test_测试接口在缺key时明确报错(self):
        r = M.api_settings_test_key(M.SettingsReq(name="NO_SUCH_KEY_XYZ"))
        self.assertFalse(r["ok"])
        self.assertIn("未配置", r["error"])


if __name__ == "__main__":
    unittest.main()
