# -*- coding: utf-8 -*-
"""aa_env（仓库根 .env 统一读写）单测 —— 方案 doc/方案归档/工作台设置页与配置文件方案_2026-10-08.md 阶段 1。
零依赖、零真实数据：全部在临时目录 + AA_ENV_PATH 隔离下跑。
"""
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
for _p in (os.path.join(_SCRIPTS, "common"), _SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import aa_env  # noqa: E402
from _work_guard import repo_only  # noqa: E402 （发行包内跳过生成者专属用例）


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aaenv_")
        self.env = os.path.join(self.tmp, ".env")
        self._old = {k: os.environ.get(k) for k in ("AA_ENV_PATH", "AA_ENV")}
        os.environ["AA_ENV_PATH"] = self.env
        os.environ.pop("AA_ENV", None)
        for k in ("AA_T_ENV_TEST", "AA_T_ENV_KEEP", "DEEPSEEK_API_KEY"):
            self._old[k] = os.environ.get(k)
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, text):
        with open(self.env, "wb") as f:
            f.write(text.encode("utf-8"))
        aa_env.load(force=True)

    def _raw(self):
        with open(self.env, "rb") as f:
            return f.read()

    def _text(self):
        with open(self.env, encoding="utf-8") as f:
            return f.read()


class TestParse(_Base):
    def test_基本解析_注释引号重复键(self):
        self._write("# 注释\n\nAA_A=1\nexport AA_B=\"two\"\nAA_A=3\nAA_C='x y'\n坏的没有等号\n")
        d = aa_env.load()
        self.assertEqual(d["AA_A"], "3")     # 后者覆盖
        self.assertEqual(d["AA_B"], "two")   # 引号去掉
        self.assertEqual(d["AA_C"], "x y")
        self.assertNotIn("坏的没有等号", d)

    @repo_only("包内已移除生成者本机的兜底 .env 路径")
    def test_路径可被_AA_ENV_PATH_覆盖(self):
        self.assertEqual(aa_env.env_path(), self.env)
        # 仓库根 = aa_env.py 上跳 3 级（scripts/common/aa_env.py → 仓库根）
        self.assertTrue(os.path.isdir(os.path.join(aa_env.repo_root(), "scripts", "common")))
        self.assertEqual(aa_env.fallback_paths(), (), "2026-10-10: hermes 兜底路径已废除")
        self.assertEqual(aa_env.read_paths(), (self.env,))     # 唯一来源 = 仓库根 .env

    def test_缺文件时不炸(self):
        self.assertEqual(aa_env.load(force=True), {})
        self.assertIsNone(aa_env.read_key("AA_NOT_THERE"))
        self.assertFalse(aa_env.has_key("AA_NOT_THERE"))


class TestWrite(_Base):
    def test_新建文件并追加(self):
        ch = aa_env.write_values({"AA_T_ENV_TEST": "v1"})
        self.assertEqual(ch, ["AA_T_ENV_TEST"])
        self.assertEqual(self._text(), "AA_T_ENV_TEST=v1\n")

    def test_原地替换保留注释与未改动行(self):
        self._write("# 头注释\nAA_T_ENV_KEEP=old\n\n# 中间注释\nOTHER=1\n")
        before = self._raw()
        aa_env.write_values({"AA_T_ENV_KEEP": "new"})
        after = self._raw()
        self.assertEqual(before.count(b"# "), after.count(b"# "))          # 注释数不变
        self.assertIn(b"AA_T_ENV_KEEP=new", after)
        self.assertIn(b"OTHER=1", after)                                     # 未提及的键原样
        self.assertIn("AA_T_ENV_KEEP", aa_env.load())

    def test_未改动行逐字节保留_含CRLF(self):
        self._write("# 注释\r\nAA_T_ENV_KEEP=old\r\nOTHER=1\r\n")
        aa_env.write_values({"AA_T_ENV_KEEP": "new"})
        raw = self._raw()
        self.assertIn(b"OTHER=1\r\n", raw)         # CRLF 未被转成 LF
        self.assertNotIn(b"AA_T_ENV_KEEP=old", raw)

    def test_删除键删整行(self):
        self._write("# c\nAA_T_ENV_TEST=1\nAA_T_ENV_KEEP=2\n")
        ch = aa_env.write_values({}, deletes=["AA_T_ENV_TEST"])
        self.assertEqual(ch, ["AA_T_ENV_TEST"])
        raw = self._text()
        self.assertNotIn("AA_T_ENV_TEST", raw)
        self.assertIn("AA_T_ENV_KEEP=2", raw)

    def test_拒绝换行与非法键名(self):
        with self.assertRaises(ValueError):
            aa_env.write_values({"AA_T_ENV_TEST": "a\nb"})
        with self.assertRaises(ValueError):
            aa_env.write_values({"坏键名": "x"})
        self.assertFalse(os.path.exists(self.env))

    def test_原子写不留tmp(self):
        aa_env.write_values({"AA_T_ENV_TEST": "v"})
        self.assertFalse([f for f in os.listdir(self.tmp) if f.endswith(".tmp")])

    def test_值两端空白被裁掉且空值可写(self):
        aa_env.write_values({"AA_T_ENV_TEST": "  v  ", "AA_T_ENV_KEEP": ""})
        d = aa_env.load(force=True)
        self.assertEqual(d["AA_T_ENV_TEST"], "v")
        self.assertEqual(d["AA_T_ENV_KEEP"], "")


class TestPriority(_Base):
    def test_真实env优先于文件(self):
        self._write("DEEPSEEK_API_KEY=from-file\n")
        self.assertEqual(aa_env.read_key("DEEPSEEK_API_KEY"), "from-file")
        os.environ["DEEPSEEK_API_KEY"] = "from-env"
        self.assertEqual(aa_env.read_key("DEEPSEEK_API_KEY"), "from-env")

    def test_get_回落到文件(self):
        self._write("AA_T_ENV_TEST=file\n")
        self.assertEqual(aa_env.get("AA_T_ENV_TEST"), "file")
        self.assertEqual(aa_env.get("AA_T_ENV_TEST2", "dflt"), "dflt")

    def test_load_dotenv_不覆盖真实env_force则覆盖(self):
        os.environ["AA_T_ENV_TEST"] = "real"
        self._write("AA_T_ENV_TEST=file\nDEEPSEEK_API_KEY=sk-x\n")
        aa_env.load_dotenv()
        self.assertEqual(os.environ["AA_T_ENV_TEST"], "real")            # 不覆盖
        self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "sk-x")         # 灌进来
        self.assertNotIn("AA_T_ENV_KEEP", os.environ)
        aa_env.load_dotenv(force=True)
        self.assertEqual(os.environ["AA_T_ENV_TEST"], "file")            # force 覆盖
        aa_env.write_values({}, deletes=["DEEPSEEK_API_KEY"])
        self.assertNotIn("DEEPSEEK_API_KEY", os.environ)                 # 删了要摘回去


class TestMask(_Base):
    def test_mask只留尾四位(self):
        self.assertEqual(aa_env.mask("sk-1234567890abcd"), "…abcd")
        self.assertEqual(aa_env.mask("abc"), "…")
        self.assertEqual(aa_env.mask(""), "")
        self.assertEqual(aa_env.mask(None), "")


class TestConfigIntegration(_Base):
    def test_env里的AA键能覆盖TOML(self):
        import aa_config
        self._write("AA_DEFAULT_MAX_WORKERS=3\n")
        aa_env.load_dotenv(force=True)
        aa_config.reload()
        self.assertEqual(str(aa_config.get("default", "max_workers")), "3")
        # 收尾：把进程 env 恢复（其它用例/后续模块不能吃到这个值）
        os.environ.pop("AA_DEFAULT_MAX_WORKERS", None)
        aa_config.reload()


if __name__ == "__main__":
    unittest.main()
