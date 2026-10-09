# -*- coding: utf-8 -*-
"""translate_runner 章节清单来源（2026-09-13：只用本地 aa/，不再实时拉分类页）。

下载与翻译已解耦（translate_runner 不再自动调 dl_runner）：实时清单给出的「原站有但
本地没下」的章根本译不了（aa_translate 只处理 aa/ 里存在的 txt），却每次白拉 1000+ 条。
运行：.venv/Scripts/python.exe -m unittest test_runner_entries -v
"""
import json, os, shutil, sys, tempfile, unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "works"),
           os.path.join(_SCRIPTS, "runner")):
    sys.path.insert(0, _p)
import translate_runner as R


class TestLoadEntriesLocalOnly(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.aa = os.path.join(self.tmp, "aa")
        self.out = os.path.join(self.tmp, "汉化")
        self.work = os.path.join(self.tmp, "work")
        for d in (self.aa, self.out, self.work):
            os.makedirs(d, exist_ok=True)
        for name in ("entry_20.txt", "entry_10.txt", "entry_30_台词.txt"):
            with open(os.path.join(self.aa, name), "w", encoding="utf-8") as f:
                f.write("x")
        # 原站顺序：20 在 10 之前；99 原站有但本地没下载
        with open(os.path.join(self.work, "chapter_order.json"), "w", encoding="utf-8") as f:
            json.dump({"order": ["20", "10", "99"]}, f)
        self.cfg = {"aa_dir": self.aa, "out_dir": self.out, "work_dir": self.work,
                    "site": "yaruobook", "chapters_source": "cache"}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_only_local_aa_and_site_order(self):
        eids = [e["eid"] for e in R.load_entries(self.cfg)]
        self.assertEqual(eids, ["20", "10", "30"])   # 99 未下载 → 排除；30 无顺序 → 追加

    def test_no_dl_runner_import(self):
        """翻译清单不得触发 dl_runner（旧的实时拉分类页路径）。"""
        sys.modules.pop("dl_runner", None)
        R.load_entries(self.cfg)
        self.assertNotIn("dl_runner", sys.modules)

    def test_fallback_eid_order_without_order_file(self):
        os.remove(os.path.join(self.work, "chapter_order.json"))
        eids = [e["eid"] for e in R.load_entries(self.cfg)]
        self.assertEqual(eids, ["10", "20", "30"])

    def test_empty_aa_returns_none(self):
        shutil.rmtree(self.aa)
        self.assertIsNone(R.load_entries(self.cfg))


if __name__ == "__main__":
    unittest.main()
