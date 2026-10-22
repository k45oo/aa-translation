# -*- coding: utf-8 -*-
"""目录拖拽排序（2026-10-19）：纯重排函数 + logic.move_chapter 落盘 + 路由。

不碰真实数据：临时目录造 `aa/entry_N.txt` 当作品，patch `web_state.cfg_of`。
覆盖 2026-10-19 事故的机制：目录顺序 = `work/chapter_order.json`，写它必须
**按当前生效顺序整份落盘**（顺带补齐缺的章），且只动顺序不动正文。
运行：python -m unittest scripts.tests.test_chapter_order_move
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from fastapi import HTTPException

from aa_web_app import logic, web_state
from aa_web_app.routers import chapters

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_TPL = os.path.join(os.path.dirname(os.path.dirname(TESTS_DIR)), "aa_web_app", "templates")


class TestMoveInOrder(unittest.TestCase):
    """logic.move_in_order：把 items[i] 移到 items[j] 的前/后。"""

    def test_前移与后移(self):
        abcd = ["A", "B", "C", "D"]
        self.assertEqual(logic.move_in_order(abcd, 1, 3, "before"), ["A", "C", "B", "D"])   # B 放到 D 前
        self.assertEqual(logic.move_in_order(abcd, 1, 3, "after"), ["A", "C", "D", "B"])    # B 放到 D 后
        self.assertEqual(logic.move_in_order(abcd, 3, 1, "before"), ["A", "D", "B", "C"])   # D 放到 B 前
        self.assertEqual(logic.move_in_order(abcd, 3, 1, "after"), ["A", "B", "D", "C"])
        self.assertEqual(logic.move_in_order(abcd, 0, 2, "before"), ["B", "A", "C", "D"])
        self.assertEqual(logic.move_in_order(abcd, 0, 2, "after"), ["B", "C", "A", "D"])
        self.assertEqual(logic.move_in_order(abcd, 2, 0, "before"), ["C", "A", "B", "D"])
        self.assertEqual(logic.move_in_order(abcd, 2, 0, "after"), ["A", "C", "B", "D"])

    def test_不改入参_同下标原样(self):
        src = ["A", "B", "C"]
        logic.move_in_order(src, 0, 2, "after")
        self.assertEqual(src, ["A", "B", "C"], "纯函数：不得改入参")
        self.assertEqual(logic.move_in_order(src, 1, 1, "before"), ["A", "B", "C"])

    def test_越界抛错(self):
        with self.assertRaises(IndexError):
            logic.move_in_order(["A"], 0, 5)
        with self.assertRaises(IndexError):
            logic.move_in_order(["A"], -1, 0)
        with self.assertRaises(IndexError):
            logic.move_in_order([], 0, 0)

    def test_重复项按下标算(self):
        # 同值重复时不能用 index() 找目标，否则插错位置
        self.assertEqual(logic.move_in_order(["X", "X", "Y"], 2, 0, "before"), ["Y", "X", "X"])


class TestMoveChapter(unittest.TestCase):
    """logic.move_chapter：按生效顺序整份落盘 + 原子写 + 缓存失效。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aa_ord_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.aa = os.path.join(self.tmp, "aa")
        os.makedirs(self.aa)
        for n in (1, 2, 3, 4):
            with open(os.path.join(self.aa, "entry_%d.txt" % n), "w", encoding="utf-8") as f:
                f.write("# 标题%d\n" % n)
        self.cfg = {"aa_dir": self.aa, "out_dir": os.path.join(self.tmp, "汉化"),
                    "work_dir": os.path.join(self.tmp, "work"), "name_style": None,
                    "title": "临时作品"}
        os.makedirs(self.cfg["work_dir"], exist_ok=True)
        self.orderf = os.path.join(self.cfg["work_dir"], "chapter_order.json")
        logic.invalidate_mch_cache()

    def _order(self):
        with open(self.orderf, encoding="utf-8") as f:
            return json.load(f)["order"]

    def test_无顺序文件时按_eid_顺序起算(self):
        new = logic.move_chapter(self.cfg, "entry_3.txt", "entry_1.txt", "before")
        self.assertEqual(new, ["3", "1", "2", "4"])
        self.assertEqual(self._order(), ["3", "1", "2", "4"])
        self.assertFalse(os.path.exists(self.orderf + ".tmp"), "原子写不留 .tmp")

    def test_顺序文件部分覆盖时补齐后落盘(self):
        with open(self.orderf, "w", encoding="utf-8") as f:
            json.dump({"order": ["3"]}, f, ensure_ascii=False)
        logic.invalidate_mch_cache()
        # 生效顺序 = 文件里的 3 在前，其余按 eid 补尾 → [3,1,2,4]
        new = logic.move_chapter(self.cfg, "entry_4.txt", "entry_3.txt", "before")
        self.assertEqual(new, ["4", "3", "1", "2"])
        self.assertEqual(self._order(), ["4", "3", "1", "2"], "落盘要整份（顺带补齐缺的章）")

    def test_只动顺序不动正文(self):
        before = {}
        for n in (1, 2, 3, 4):
            p = os.path.join(self.aa, "entry_%d.txt" % n)
            with open(p, encoding="utf-8") as f:
                before[p] = f.read()
        logic.move_chapter(self.cfg, "entry_1.txt", "entry_4.txt", "after")
        for p, txt in before.items():
            with open(p, encoding="utf-8") as f:
                self.assertEqual(f.read(), txt, "正文不许被改：%s" % p)

    def test_缓存失效(self):
        logic.merged_chapters(self.cfg["aa_dir"], self.cfg["out_dir"], None, self.orderf)
        self.assertTrue(logic._MCH_CACHE, "先要有缓存")
        logic.move_chapter(self.cfg, "entry_2.txt", "entry_1.txt", "before")
        self.assertFalse(logic._MCH_CACHE, "改完顺序必须清缓存，否则页面还显示旧序")

    def test_目标不存在等报错(self):
        with self.assertRaises(ValueError):
            logic.move_chapter(self.cfg, "entry_9.txt", "entry_1.txt", "before")
        with self.assertRaises(ValueError):
            logic.move_chapter(self.cfg, "entry_1.txt", "entry_9.txt", "before")
        with self.assertRaises(ValueError):
            logic.move_chapter(self.cfg, "entry_1.txt", "entry_1.txt", "before")
        with self.assertRaises(ValueError):
            logic.move_chapter(self.cfg, "entry_1.txt", "entry_2.txt", "sideways")
        self.assertFalse(os.path.exists(self.orderf), "只校验失败不该写文件")

    def test_名里没编号时拒绝(self):
        os.remove(os.path.join(self.aa, "entry_4.txt"))
        with open(os.path.join(self.aa, "番外.txt"), "w", encoding="utf-8") as f:
            f.write("# 番外\n")
        logic.invalidate_mch_cache()
        with self.assertRaises(ValueError) as cm:
            logic.move_chapter(self.cfg, "entry_1.txt", "entry_2.txt", "before")
        self.assertIn("编号", str(cm.exception))
        self.assertFalse(os.path.exists(self.orderf))


class TestOrderRoute(unittest.TestCase):
    """POST /api/work/<w>/chapter-order/move：返回新顺序 + 记修改记录。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aa_ord_rt_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.aa = os.path.join(self.tmp, "aa")
        os.makedirs(self.aa)
        for n in (1, 2, 3):
            with open(os.path.join(self.aa, "entry_%d.txt" % n), "w", encoding="utf-8") as f:
                f.write("# 标题%d\n" % n)
        self.cfg = {"aa_dir": self.aa, "out_dir": os.path.join(self.tmp, "汉化"),
                    "work_dir": os.path.join(self.tmp, "work"), "name_style": None,
                    "title": "临时作品"}
        os.makedirs(self.cfg["work_dir"], exist_ok=True)
        logic.invalidate_mch_cache()
        p = mock.patch.object(web_state, "cfg_of", lambda w: self.cfg)
        p.start()
        self.addCleanup(p.stop)

    def test_移动并记流水(self):
        r = chapters.api_chapter_order_move(
            "w", chapters.OrderMoveBody(from_src="entry_3.txt", to_src="entry_1.txt", place="before"))
        self.assertTrue(r["ok"])
        self.assertEqual(r["total"], 3)
        self.assertEqual(r["order"], ["3", "1", "2"])
        with open(os.path.join(self.cfg["work_dir"], "chapter_order.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["order"], ["3", "1", "2"])
        # 修改记录（edits.jsonl）留档：类型「目录顺序」
        with open(os.path.join(self.cfg["work_dir"], "edits.jsonl"), encoding="utf-8") as f:
            rows = [json.loads(ln) for ln in f if ln.strip()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["type"], "目录顺序")
        self.assertEqual(rows[0]["file"], "（目录顺序）")
        self.assertIn("entry_3.txt", rows[0]["before"])
        self.assertIn("第 1/3 位", rows[0]["after"])

    def test_参数校验(self):
        with self.assertRaises(HTTPException) as cm:
            chapters.api_chapter_order_move(
                "w", chapters.OrderMoveBody(from_src="", to_src="entry_1.txt"))
        self.assertEqual(cm.exception.status_code, 422)
        with self.assertRaises(HTTPException) as cm:
            chapters.api_chapter_order_move(
                "w", chapters.OrderMoveBody(from_src="entry_1.txt", to_src="entry_2.txt", place="above"))
        self.assertEqual(cm.exception.status_code, 422)
        with self.assertRaises(HTTPException) as cm:
            chapters.api_chapter_order_move(
                "w", chapters.OrderMoveBody(from_src="entry_9.txt", to_src="entry_1.txt"))
        self.assertEqual(cm.exception.status_code, 404)

    def test_阅读页接好了拖拽(self):
        with open(os.path.join(_TPL, "reader.html"), encoding="utf-8") as f:
            tpl = f.read()
        self.assertIn("chapter-order/move", tpl, "阅读页要 POST 到排序接口")
        self.assertIn("pointerdown", tpl, "要监听长按起点")
        self.assertIn("HOLD = 500", tpl, "长按阈值")
        self.assertIn("CUR_IDX = i", tpl, "拖完要同步键盘翻章的下标")


if __name__ == "__main__":
    unittest.main()
