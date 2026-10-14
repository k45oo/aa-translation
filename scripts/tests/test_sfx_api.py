# -*- coding: utf-8 -*-
"""sfx 样式标记的 API 层测试（2026-09-15）：行保存/行渲染/行读取与 sfx 标记的交互。

覆盖三处契约（函数级，不起服务器）：
  ① PUT /line/{idx}（api_save_line）：sfx 标记与文本保存解耦——内容未变也能加/清标记；
  ② GET /line/{idx}/html（api_line_html）：**必须透传 src_line + 标记**（旧实现丢 src_line，
     保存后重渲染的行会掉 sfx 特效）；
  ③ GET /line/{idx}（api_line_get）：回带该行标记（编辑面板「清除 sfx」据此显示）。

运行：python -m unittest scripts.tests.test_sfx_api
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
SCRIPTS = os.path.join(_REPO_ROOT, "scripts")
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.join(SCRIPTS, "common"))

from aa_web_app import logic  # noqa: E402


class SfxApiFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sfx_api_test_")
        self.aa_dir = os.path.join(self.tmp, "aa")
        self.out_dir = os.path.join(self.tmp, "汉化")
        self.work_dir = os.path.join(self.tmp, "work")
        for d in (self.aa_dir, self.out_dir, self.work_dir):
            os.makedirs(d)
        self.base = "entry_1"
        self.src = self._write(self.aa_dir, self.base + ".txt",
                               ["# 标题", "原文", "ﾄﾞｷｯ", "他悄悄说道"])
        self.tr = self._write(self.out_dir, self.base + "_汉化.txt",
                              ["# 标题", "原文", "心动", "他悄悄说道"])
        self.cfg = {"work_dir": self.work_dir, "aa_dir": self.aa_dir,
                    "out_dir": self.out_dir, "site": "yaruonichijou",
                    "title": "测试作品"}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, d, name, lines):
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines))
        return p

    def _main(self):
        import aa_web_app.main as M
        return M

    def _marks(self):
        return logic.load_sfx_marks(self.work_dir, self.base)


class TestSaveLineSfx(SfxApiFixture):
    def test_sfx_mark_written_with_text(self):
        M = self._main()
        body = M.LineSave(text="他小声说道", type="漏译", sfx=[[1, "小声"]])
        with mock.patch("aa_web_app.web_state.cfg_of", return_value=self.cfg), \
                mock.patch("aa_web_app.logic.load_work_glossary",
                           return_value={"known_names": set()}):
            r = M.api_save_line("test", self.base + "_汉化.txt", 3, body)
        self.assertEqual(r["result"], "ok")
        self.assertEqual(r["sfx_added"], 1)
        self.assertTrue(r["sfx_changed"])
        self.assertEqual(self._marks()["4"], [[1, "小声"]])          # 1-based 行号 = idx+1
        self.assertEqual(r["sfx_marks"], [[1, "小声"]])

    def test_sfx_written_even_when_text_unchanged(self):
        """内容没变（只想要样式）也要落标记——旧实现会因 unchanged 提前返回而丢掉。"""
        M = self._main()
        body = M.LineSave(text="心动", type="错位", sfx=[[0, "心动"]])
        with mock.patch("aa_web_app.web_state.cfg_of", return_value=self.cfg), \
                mock.patch("aa_web_app.logic.load_work_glossary",
                           return_value={"known_names": set()}):
            r = M.api_save_line("test", self.base + "_汉化.txt", 2, body)
        self.assertEqual(r["result"], "unchanged")
        self.assertTrue(r["sfx_changed"])
        self.assertEqual(self._marks()["3"], [[0, "心动"]])

    def test_sfx_clear_overlapping_and_clear_all(self):
        M = self._main()
        logic.add_sfx_mark(self.work_dir, self.base, 4, 1, "悄悄")
        logic.add_sfx_mark(self.work_dir, self.base, 4, 20, "咚")
        with mock.patch("aa_web_app.web_state.cfg_of", return_value=self.cfg), \
                mock.patch("aa_web_app.logic.load_work_glossary",
                           return_value={"known_names": set()}):
            r = M.api_save_line("test", self.base + "_汉化.txt", 3,
                                M.LineSave(text="他悄悄说道", type="错译", sfx_clear=[[2, 2]]))
            self.assertEqual(r["result"], "unchanged")
            self.assertEqual(r["sfx_cleared"], 1)                    # [2,4) 与 [1,3) 重叠 → 清 1
            self.assertEqual(self._marks()["4"], [[20, "咚"]])        # 不重叠的保留
            r2 = M.api_save_line("test", self.base + "_汉化.txt", 3,
                                 M.LineSave(text="他悄悄说道", type="错译", sfx_clear_all=True))
        self.assertEqual(r2["sfx_cleared"], 1)
        self.assertEqual(self._marks(), {})

    def test_mark_on_edited_text_persisted_verbatim(self):
        M = self._main()
        with mock.patch("aa_web_app.web_state.cfg_of", return_value=self.cfg), \
                mock.patch("aa_web_app.logic.load_work_glossary",
                           return_value={"known_names": set()}):
            M.api_save_line("test", self.base + "_汉化.txt", 1,
                            M.LineSave(text="原文", type="错位", sfx=[[0, "原文"]]))
        self.assertEqual(self._marks()["2"], [[0, "原文"]])


class TestLineHtmlSfx(SfxApiFixture):
    def test_auto_sfx_survives_line_rerender(self):
        """回归：api_line_html 必须透传 src_line，否则保存后重渲染的行掉 sfx 特效。"""
        M = self._main()
        with mock.patch("aa_web_app.web_state.cfg_of", return_value=self.cfg):
            r = M.api_line_html("test", self.base + "_汉化.txt", 2)
        self.assertIn('<span class="sfx">心动</span>', r["html"])

    def test_manual_mark_rendered(self):
        logic.add_sfx_mark(self.work_dir, self.base, 4, 1, "悄悄")
        M = self._main()
        with mock.patch("aa_web_app.web_state.cfg_of", return_value=self.cfg):
            r = M.api_line_html("test", self.base + "_汉化.txt", 3)
        self.assertEqual(r["html"], '他<span class="sfx">悄悄</span>说道')

    def test_no_marks_no_sfx(self):
        M = self._main()
        with mock.patch("aa_web_app.web_state.cfg_of", return_value=self.cfg):
            r = M.api_line_html("test", self.base + "_汉化.txt", 3)
        self.assertEqual(r["html"], "他悄悄说道")


class TestLineGetSfx(SfxApiFixture):
    def test_line_get_returns_marks(self):
        logic.add_sfx_mark(self.work_dir, self.base, 4, 1, "悄悄")
        M = self._main()
        with mock.patch("aa_web_app.web_state.cfg_of", return_value=self.cfg):
            r = M.api_line_get("test", self.base + "_汉化.txt", 3)
        self.assertEqual(r["sfx"], [[1, "悄悄"]])                     # 行 4 有标记
        with mock.patch("aa_web_app.web_state.cfg_of", return_value=self.cfg):
            r2 = M.api_line_get("test", self.base + "_汉化.txt", 1)
        self.assertEqual(r2["sfx"], [])                               # 行 2 无标记


if __name__ == "__main__":
    unittest.main()
