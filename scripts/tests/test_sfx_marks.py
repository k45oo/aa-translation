# -*- coding: utf-8 -*-
"""sfx 样式（半角假名拟声词译文特效）标记测试（2026-09-15）。

两层契约：
  ① logic.sfx_marks_*：<work_dir>/sfx_marks/<base>.json 的读写（重叠去重 / 按区间清 / 损坏容错）；
  ② gen_aa_html.sfx_spans / render_line(sfx_marks=...)：人工标记与「原文半角假名宽度对齐」自动渲染
     叠加（并集），人工标记偏移漂移时按最近的同文本出现处兜底，文本消失即忽略。

运行：python -m unittest scripts.tests.test_sfx_marks
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts", "common"))

from aa_web_app import logic              # noqa: E402
import gen_aa_html                         # noqa: E402


class SfxFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sfx_marks_test_")
        self.work_dir = os.path.join(self.tmp, "work")
        os.makedirs(self.work_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def marks(self):
        return logic.load_sfx_marks(self.work_dir, "entry_1")


class TestSfxMarksFile(SfxFixture):
    def test_add_and_load_roundtrip(self):
        ok, err, act = logic.add_sfx_mark(self.work_dir, "entry_1", 7, 12, "悄悄话")
        self.assertTrue(ok, err)
        self.assertEqual(act, "add")
        self.assertEqual(self.marks()["7"], [[12, "悄悄话"]])
        p = logic.sfx_marks_path(self.work_dir, "entry_1")
        self.assertTrue(p.endswith(os.path.join("sfx_marks", "entry_1.json")))
        self.assertTrue(os.path.exists(p))
        self.assertFalse(os.path.exists(p + ".tmp"))            # 原子写不留 .tmp
        with open(p, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["7"], [[12, "悄悄话"]])

    def test_overlapping_add_replaces_old(self):
        logic.add_sfx_mark(self.work_dir, "entry_1", 7, 12, "悄悄话")
        logic.add_sfx_mark(self.work_dir, "entry_1", 7, 14, "低语")   # 与上一条重叠
        self.assertEqual(self.marks()["7"], [[14, "低语"]])

    def test_non_overlapping_add_accumulates(self):
        logic.add_sfx_mark(self.work_dir, "entry_1", 7, 12, "悄悄话")
        logic.add_sfx_mark(self.work_dir, "entry_1", 7, 40, "咚")
        logic.add_sfx_mark(self.work_dir, "entry_1", 8, 0, "呼")
        m = self.marks()
        self.assertEqual(len(m["7"]), 2)
        self.assertEqual(m["8"], [[0, "呼"]])

    def test_empty_text_skipped_without_writing(self):
        ok, err, act = logic.add_sfx_mark(self.work_dir, "entry_1", 7, 3, "")
        self.assertEqual(act, "skip")
        self.assertFalse(os.path.exists(logic.sfx_marks_path(self.work_dir, "entry_1")))

    def test_clear_by_range_only_overlapping(self):
        logic.add_sfx_mark(self.work_dir, "entry_1", 7, 10, "悄悄话")
        logic.add_sfx_mark(self.work_dir, "entry_1", 7, 40, "咚")
        ok, err, n = logic.clear_sfx_marks(self.work_dir, "entry_1", 7, [12, 2])
        self.assertTrue(ok, err)
        self.assertEqual(n, 1)
        self.assertEqual(self.marks()["7"], [[40, "咚"]])

    def test_clear_range_miss_is_noop(self):
        logic.add_sfx_mark(self.work_dir, "entry_1", 7, 10, "悄悄话")
        ok, err, n = logic.clear_sfx_marks(self.work_dir, "entry_1", 7, [100, 5])
        self.assertEqual((ok, n), (True, 0))
        self.assertEqual(self.marks()["7"], [[10, "悄悄话"]])

    def test_clear_all_removes_line_key(self):
        logic.add_sfx_mark(self.work_dir, "entry_1", 7, 10, "悄悄话")
        logic.add_sfx_mark(self.work_dir, "entry_1", 7, 40, "咚")
        ok, err, n = logic.clear_sfx_marks(self.work_dir, "entry_1", 7, None)
        self.assertEqual((ok, n), (True, 2))
        self.assertEqual(self.marks(), {})

    def test_clear_on_line_without_marks(self):
        self.assertEqual(logic.clear_sfx_marks(self.work_dir, "entry_1", 7, None), (True, "", 0))

    def test_missing_and_corrupt_file(self):
        self.assertEqual(self.marks(), {})
        p = logic.sfx_marks_path(self.work_dir, "entry_1")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("{ not json")
        self.assertEqual(self.marks(), {})
        # 脏条目（长度不足/非数字偏移/空文本）被清洗掉
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps({"7": [[3, "好"], [1], ["x", "坏"], [5, ""]], "8": "不是列表"},
                               ensure_ascii=False))
        self.assertEqual(self.marks(), {"7": [[3, "好"]]})


class TestSfxRender(unittest.TestCase):
    """渲染层：仅纯函数，不碰磁盘。"""

    def test_no_marks_behaviour_unchanged(self):
        self.assertEqual(gen_aa_html.render_line(0, "普通一行", {}, _Site(), None, None),
                         "普通一行")
        self.assertNotIn("sfx", gen_aa_html.render_line(0, "普通一行", {}, _Site(), "原文", None))

    def test_manual_mark_wraps_segment(self):
        html = gen_aa_html.render_line(0, "他悄悄说道", {}, _Site(), None, [[1, "悄悄"]])
        self.assertEqual(html, '他<span class="sfx">悄悄</span>说道')

    def test_dict_marks_keyed_by_1based_line(self):
        marks = {"3": [[1, "悄悄"]]}
        self.assertIn('<span class="sfx">悄悄</span>',
                      gen_aa_html.render_line(2, "他悄悄说道", {}, _Site(), None, marks))
        self.assertNotIn("sfx", gen_aa_html.render_line(1, "他悄悄说道", {}, _Site(), None, marks))

    def test_drifted_offset_falls_back_to_nearest_text(self):
        # 标记记的是偏移 1；行内前面插入了两字 → 实际在偏移 3，兜底仍命中
        html = gen_aa_html.render_line(0, "啊呀他悄悄说道", {}, _Site(), None, [[1, "悄悄"]])
        self.assertEqual(html, '啊呀他<span class="sfx">悄悄</span>说道')

    def test_missing_text_mark_ignored(self):
        self.assertNotIn("sfx", gen_aa_html.render_line(0, "他小声说道", {}, _Site(), None, [[1, "悄悄"]]))

    def test_mark_escaped(self):
        html = gen_aa_html.render_line(0, "a<b>c", {}, _Site(), None, [[1, "<b>"]])
        self.assertEqual(html, 'a<span class="sfx">&lt;b&gt;</span>c')

    def test_manual_mark_on_pure_hankaku_line_merges_with_auto(self):
        src, tr = "ﾄﾞｷｯ", "心动"
        auto = gen_aa_html.render_line(0, tr, {}, _Site(), src, None)
        self.assertIn('<span class="sfx">心动</span>', auto)          # 自动：宽度对齐
        both = gen_aa_html.render_line(0, tr, {}, _Site(), src, [[0, "心动"]])
        self.assertEqual(both.count('<span class="sfx">'), 1)          # 与自动区间重叠 → 合并

    def test_wide_translation_styled_whole_by_manual_mark(self):
        # 原文半角假名 3 格 vs 全角译文 8 格：自动只覆盖宽度内的前 1 字（拟声词被切半），
        # 人工标记把整段译文补齐（这正是「sfx 替换」按钮存在的意义）
        src, tr = "ｷｭｯ", "心头一紧"
        auto = gen_aa_html.render_line(0, tr, {}, _Site(), src, None)
        self.assertEqual(auto, '<span class="sfx">心</span>头一紧')
        both = gen_aa_html.render_line(0, tr, {}, _Site(), src, [[0, "心头一紧"]])
        self.assertEqual(both, '<span class="sfx">心头一紧</span>')

    def test_unresolvable_mark_ignored(self):
        # 行内已找不到标记文本（更短了）→ 忽略该标记，不猜区间
        self.assertEqual(gen_aa_html.render_line(0, "短", {}, _Site(), None, [[0, "短得多的文本"]]),
                         '短')


class _Site:
    """最小 site stub：render_header 恒返回空（render_line 只用到这一个方法）。"""

    @staticmethod
    def render_header(line):
        return None


if __name__ == "__main__":
    unittest.main()
