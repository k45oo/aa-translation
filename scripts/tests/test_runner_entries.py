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


class TestEnsureOrderMerge(unittest.TestCase):
    """dl_runner.ensure_order：**已存在的 chapter_order.json 只追加新章**（原站顺序），不重排。

    2026-10-19 事故：旧实现「只在缺失时生成」→ 新章由消费端按 eid 补尾，而原站顺序未必等于
    eid 顺序（ポケの子 entry_95272=第433話 的 eid 反而大于 entry_95271=第434話）→ 目录里颠倒。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.work)
        self.cfg = {"work_dir": self.work}
        self.path = os.path.join(self.work, "chapter_order.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, order):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"order": order}, f, ensure_ascii=False, indent=1)

    def _read(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)["order"]

    def test_无文件时全量写入(self):
        import dl_runner as D
        D.ensure_order(self.cfg, [{"eid": "20"}, {"eid": "10"}])
        self.assertEqual(self._read(), ["20", "10"])          # 原站顺序，不按 eid 排

    def test_已存在时只追加新章且保持原站顺序(self):
        import dl_runner as D
        self._write(["20", "10"])
        # 原站清单：老两章 + 新三章（注意 95272 在 95271 之前 = 本题的原始报障）
        D.ensure_order(self.cfg, [{"eid": "10"}, {"eid": "20"}, {"eid": "30"},
                                  {"eid": "95272"}, {"eid": "95271"}])
        self.assertEqual(self._read(), ["20", "10", "30", "95272", "95271"])
        # 再跑一次幂等
        D.ensure_order(self.cfg, [{"eid": "30"}, {"eid": "95272"}, {"eid": "95271"}])
        self.assertEqual(self._read(), ["20", "10", "30", "95272", "95271"])

    def test_无新章时不改文件(self):
        import dl_runner as D
        self._write(["20", "10"])
        before = os.path.getmtime(self.path)
        D.ensure_order(self.cfg, [{"eid": "10"}, {"eid": "20"}])
        self.assertEqual(self._read(), ["20", "10"])
        self.assertEqual(os.path.getmtime(self.path), before, "无新章不该重写文件")

    def test_文件损坏时保持原样(self):
        import dl_runner as D
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ 坏 JSON")
        D.ensure_order(self.cfg, [{"eid": "10"}])
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{ 坏 JSON", "解析失败必须原样不动，别覆盖")


if __name__ == "__main__":
    unittest.main()
