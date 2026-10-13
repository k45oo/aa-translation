# -*- coding: utf-8 -*-
"""翻译替换历史记录测试（2026-09-13）：logic.tr_hist_*。

锁住阅读器翻译替换弹窗「历史替换记录」的落盘契约：新在前、同对去重计数、
上限截断、损坏文件当空、原子写不留 .tmp。
运行：python -m unittest scripts.tests.test_replace_history
（只测纯逻辑层 logic.py，不 import main——避免拉起 app 与预热线程）
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

from aa_web_app import logic  # noqa: E402


class TrHistFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tr_hist_test_")
        self.work_dir = os.path.join(self.tmp, "work")
        os.makedirs(self.work_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestTrHist(TrHistFixture):
    def test_add_load_roundtrip_newest_first(self):
        logic.tr_hist_add(self.work_dir, [["あかり", "灯"]], applied=3, files=1, ts="2026-09-13 10:00")
        logic.tr_hist_add(self.work_dir, [["ゆかり", "缘"]], applied=1, files=1, ts="2026-09-13 11:00")
        rows = logic.tr_hist_load(self.work_dir)
        self.assertEqual([r["find"] for r in rows], ["ゆかり", "あかり"])   # 新在前
        self.assertEqual(rows[1]["repl"], "灯")
        self.assertEqual(rows[1]["applied"], 3)
        self.assertEqual(rows[1]["n"], 1)
        self.assertEqual(rows[1]["ts"], "2026-09-13 10:00")
        self.assertTrue(os.path.exists(logic.tr_hist_path(self.work_dir)))
        self.assertFalse(os.path.exists(logic.tr_hist_path(self.work_dir) + ".tmp"))   # 原子写不留 .tmp

    def test_same_pair_dedupes_and_counts(self):
        logic.tr_hist_add(self.work_dir, [["先生", "老师"]], applied=2, files=1, ts="A")
        logic.tr_hist_add(self.work_dir, [["别的", "x"]], applied=1, files=1, ts="B")
        logic.tr_hist_add(self.work_dir, [["先生", "老师"]], applied=5, files=2, ts="C")
        rows = logic.tr_hist_load(self.work_dir)
        self.assertEqual(len(rows), 2)                       # 同对不新增条目
        self.assertEqual(rows[0]["find"], "先生")             # 再应用 → 置顶
        self.assertEqual(rows[0]["n"], 2)
        self.assertEqual(rows[0]["applied"], 5)
        self.assertEqual(rows[0]["files"], 2)
        self.assertEqual(rows[0]["ts"], "C")

    def test_same_find_different_repl_is_separate(self):
        logic.tr_hist_add(self.work_dir, [["さん", "桑"]])
        logic.tr_hist_add(self.work_dir, [["さん", "先生"]])
        self.assertEqual(len(logic.tr_hist_load(self.work_dir)), 2)

    def test_empty_find_skipped_and_repl_coerced(self):
        logic.tr_hist_add(self.work_dir, [["", "x"], ["好好", None], [None, "y"]])
        rows = logic.tr_hist_load(self.work_dir)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["find"], "好好")
        self.assertEqual(rows[0]["repl"], "")               # None → ""

    def test_cap_keeps_newest(self):
        for i in range(logic.TR_HIST_MAX + 7):
            logic.tr_hist_add(self.work_dir, [[f"词{i}", "译"]], ts=str(i))
        rows = logic.tr_hist_load(self.work_dir)
        self.assertEqual(len(rows), logic.TR_HIST_MAX)
        self.assertEqual(rows[0]["find"], f"词{logic.TR_HIST_MAX + 6}")            # 最新在最前
        self.assertEqual(rows[-1]["find"], f"词{7}")                               # 最旧的 7 条被淘汰

    def test_missing_and_corrupt_file_return_empty(self):
        self.assertEqual(logic.tr_hist_load(self.work_dir), [])
        p = logic.tr_hist_path(self.work_dir)
        with open(p, "w", encoding="utf-8") as f:
            f.write("{ 不是 JSON")
        self.assertEqual(logic.tr_hist_load(self.work_dir), [])
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"nope": 1}, f)                     # 顶层不是 list
        self.assertEqual(logic.tr_hist_load(self.work_dir), [])
        with open(p, "w", encoding="utf-8") as f:
            json.dump([{"repl": "x"}, "字符串", {"find": "好", "n": "2"}], f)      # 逐条过滤 + 数值归一
        rows = logic.tr_hist_load(self.work_dir)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["n"], 2)


class TestTrHistPin(TrHistFixture):
    """手动置顶（2026-10-13）：排最前 + 不占 TR_HIST_MAX 淘汰名额。"""

    def _seed(self, n=3):
        for i in range(n):
            logic.tr_hist_add(self.work_dir, [[f"词{i}", f"译{i}"]], ts=f"t{i}")
        return logic.tr_hist_load(self.work_dir)

    def test_pin_moves_to_front_and_persists(self):
        self._seed(3)                                     # 词2（最新）在前
        ok, rows = logic.tr_hist_pin(self.work_dir, "词0", "译0", True)
        self.assertTrue(ok)
        self.assertEqual([r["find"] for r in rows], ["词0", "词2", "词1"])
        self.assertTrue(rows[0]["pin"])
        # 重新读盘（不是内存里的那份）
        rows2 = logic.tr_hist_load(self.work_dir)
        self.assertEqual([r["find"] for r in rows2], ["词0", "词2", "词1"])
        self.assertEqual([bool(r["pin"]) for r in rows2], [True, False, False])

    def test_two_pins_newest_pinned_first(self):
        self._seed(3)
        logic.tr_hist_pin(self.work_dir, "词0", "译0", True)
        logic.tr_hist_pin(self.work_dir, "词1", "译1", True)
        rows = logic.tr_hist_load(self.work_dir)
        self.assertEqual([r["find"] for r in rows], ["词1", "词0", "词2"])   # 后置顶的在置顶段最前

    def test_unpin_clears_flag_and_keeps_position(self):
        """取消置顶 = 只清标记；条目留在「未置顶段」最前（原时间位置已不可知，不假装还原）。

        之后新记录（未置顶）自然排到它上面，段内顺序很快回归「新在前」。
        """
        self._seed(3)
        logic.tr_hist_pin(self.work_dir, "词0", "译0", True)
        ok, rows = logic.tr_hist_pin(self.work_dir, "词0", "译0", False)
        self.assertTrue(ok)
        self.assertEqual([r["find"] for r in rows], ["词0", "词2", "词1"])
        self.assertEqual([bool(r["pin"]) for r in rows], [False, False, False])
        logic.tr_hist_add(self.work_dir, [["新词", "新译"]], ts="t9")
        rows = logic.tr_hist_load(self.work_dir)
        self.assertEqual([r["find"] for r in rows], ["新词", "词0", "词2", "词1"])

    def test_new_records_do_not_push_pinned_down(self):
        self._seed(3)
        logic.tr_hist_pin(self.work_dir, "词0", "译0", True)
        logic.tr_hist_add(self.work_dir, [["新词", "新译"]], ts="t9")
        rows = logic.tr_hist_load(self.work_dir)
        self.assertEqual([r["find"] for r in rows], ["词0", "新词", "词2", "词1"])

    def test_reapplying_same_pair_keeps_pin(self):
        self._seed(3)
        logic.tr_hist_pin(self.work_dir, "词0", "译0", True)
        logic.tr_hist_add(self.work_dir, [["词0", "译0"]], applied=2, files=1, ts="t9")
        rows = logic.tr_hist_load(self.work_dir)
        self.assertEqual(rows[0]["find"], "词0")
        self.assertTrue(rows[0]["pin"])
        self.assertEqual(rows[0]["n"], 2)
        self.assertEqual(rows[0]["ts"], "t9")

    def test_pinned_entries_survive_cap(self):
        for i in range(logic.TR_HIST_MAX):
            logic.tr_hist_add(self.work_dir, [[f"词{i}", "译"]], ts=str(i))
        logic.tr_hist_pin(self.work_dir, "词0", "译", True)          # 最旧那条钉住
        for i in range(logic.TR_HIST_MAX, logic.TR_HIST_MAX + 20):
            logic.tr_hist_add(self.work_dir, [[f"词{i}", "译"]], ts=str(i))
        rows = logic.tr_hist_load(self.work_dir)
        self.assertEqual(len(rows), logic.TR_HIST_MAX + 1)            # 置顶的另算，不占上限名额
        self.assertEqual(rows[0]["find"], "词0")                      # 置顶的没被挤掉
        self.assertTrue(rows[0]["pin"])
        self.assertEqual([r["find"] for r in rows[1:]][:2], ["词119", "词118"])
        self.assertNotIn("词1", [r["find"] for r in rows])            # 未置顶的旧记录照旧被淘汰（只留最近 100）

    def test_pinned_all_kept_and_new_records_still_accepted(self):
        for i in range(logic.TR_HIST_MAX + 3):
            logic.tr_hist_add(self.work_dir, [[f"词{i}", "译"]], ts=str(i))
            logic.tr_hist_pin(self.work_dir, f"词{i}", "译", True)
        rows = logic.tr_hist_load(self.work_dir)
        self.assertEqual(len(rows), logic.TR_HIST_MAX + 3)            # 全是置顶 → 全留
        self.assertTrue(all(r["pin"] for r in rows))
        # 整表置顶时新记录照样进得来（旧口径「置顶占名额」会让它在写入瞬间被丢掉）
        logic.tr_hist_add(self.work_dir, [["新词", "新译"]], ts="t9")
        rows = logic.tr_hist_load(self.work_dir)
        self.assertEqual(rows[0]["find"], "词102")                    # 最新的置顶在最前
        self.assertIn("新词", [r["find"] for r in rows])

    def test_pin_missing_returns_false_without_writing(self):
        self._seed(2)
        p = logic.tr_hist_path(self.work_dir)
        with open(p, encoding="utf-8") as f:
            before = f.read()
        ok, rows = logic.tr_hist_pin(self.work_dir, "不存在", "x", True)
        self.assertFalse(ok)
        self.assertEqual(len(rows), 2)
        with open(p, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)
        # 同 find 不同 repl 也不算命中（键是 (find, repl) 整对）
        self.assertFalse(logic.tr_hist_pin(self.work_dir, "词0", "别的译", True)[0])

    def test_load_normalizes_pin_field(self):
        p = logic.tr_hist_path(self.work_dir)
        with open(p, "w", encoding="utf-8") as f:                    # 旧文件（无 pin 字段）+ 脏值
            json.dump([{"find": "甲", "repl": "A", "n": 1},
                       {"find": "乙", "repl": "B", "pin": "yes"},
                       {"find": "丙", "repl": "C", "pin": 1},
                       {"find": "丁", "repl": "D", "pin": 0}], f)
        rows = logic.tr_hist_load(self.work_dir)
        self.assertEqual([bool(r["pin"]) for r in rows], [True, True, False, False])
        self.assertEqual([r["find"] for r in rows], ["乙", "丙", "甲", "丁"])   # 置顶在前（同组内保原序）


if __name__ == "__main__":
    unittest.main()
