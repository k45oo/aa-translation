# -*- coding: utf-8 -*-
"""TM（翻译记忆）单测（2026-09-15 P3）。

口径: key=去空白归一化；只同作品；三重护栏（宽度/残留假名/术语表）；宽度不同本地 pad。
实测依据: 归一化 key 覆盖 21.1% 送模型行、护栏通过 99.2%（见 doc/方案归档/token成本优化方案_2026-09-15.md §9）。
"""
import os
import sys
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in (SCRIPTS, os.path.join(SCRIPTS, "common")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import aa_tm
import aa_translate as A


class TestKey(unittest.TestCase):
    def test_normalize_spaces(self):
        self.assertEqual(aa_tm.key("　　｜　こんにちは　｜"), "｜こんにちは｜")
        self.assertEqual(aa_tm.key("a b　c"), "abc")
        self.assertEqual(aa_tm.key(""), "")


class TestGuard(unittest.TestCase):
    def test_width_reject(self):
        src = "　│短い│"
        ok, why = aa_tm.guard(src, "这个译文明显太长了放不下│", ())
        self.assertFalse(ok)
        self.assertEqual(why, "width")

    def test_residual_reject(self):
        """译文里还残留日文假名 → 丢弃交回模型（宽度先过，才轮到残留判定）。"""
        ok, why = aa_tm.guard("　　　こんにちは　　　　", "　こんにちは", ())
        self.assertFalse(ok)
        self.assertEqual(why, "residual")

    def test_glossary_reject(self):
        src = "　やる夫が来た　"
        ok, why = aa_tm.guard(src, "那家伙来了", [("やる夫", "亚陆欧")])
        self.assertFalse(ok)
        self.assertEqual(why, "glossary")
        ok2, _ = aa_tm.guard(src, "亚陆欧来了", [("やる夫", "亚陆欧")])
        self.assertTrue(ok2)

    def test_empty_reject(self):
        self.assertFalse(aa_tm.guard("　あ　", "　　", ())[0])


class TestReuse(unittest.TestCase):
    def test_same_width_reuse_asis(self):
        src = "　│　こんにちは　│"
        zh = "　│　你好　　　　│"
        self.assertEqual(A.disp_width(src), A.disp_width(zh))
        tm = {aa_tm.key(src): {"zh": zh, "w": A.disp_width(src), "base": "entry_1"}}
        self.assertEqual(aa_tm.reuse(tm, src, ()), zh)

    def test_narrower_source_gets_repadded(self):
        """归一化 key 命中的行可能填充不同 → 本地 pad 到源宽（零 token）。"""
        src_old = "　こんにちは　　　　　　"
        zh_old = "　你好　　　　　　　　　"
        src_new = "　　　こんにちは　　"          # 同样内容、不同填充
        tm = {aa_tm.key(src_old): {"zh": zh_old, "w": A.disp_width(src_old), "base": "e"}}
        out = aa_tm.reuse(tm, src_new, ())
        self.assertIsNotNone(out)
        self.assertEqual(A.disp_width(out), A.disp_width(src_new))

    def test_too_wide_translation_dropped(self):
        src = "　│あ│"
        tm = {aa_tm.key(src): {"zh": "　│非常非常长的译文放不下│", "w": A.disp_width(src), "base": "e"}}
        self.assertIsNone(aa_tm.reuse(tm, src, ()))

    def test_miss_and_flag(self):
        self.assertIsNone(aa_tm.reuse({}, "　あ　", ()))
        os.environ.pop("AA_USE_TM", None)
        self.assertTrue(aa_tm.enabled())            # 2026-09-15 起默认开
        os.environ["AA_USE_TM"] = "0"
        self.assertFalse(aa_tm.enabled())
        os.environ.pop("AA_USE_TM", None)

    def test_art_column_drift_rejected(self):
        """art 列位漂移（内容相同但填充位置不同）必须丢弃 —— 否则宽对框歪。"""
        src_old = "┌─┐　こんにちは　└─┘"
        zh_old = "┌─┐　你好　　　　└─┘"
        src_new = "┌─┐　　　こんにちは└─┘"          # 归一化后同 key，但 art 列位不同
        self.assertEqual(aa_tm.key(src_old), aa_tm.key(src_new))
        tm = {aa_tm.key(src_old): {"zh": zh_old, "w": aa_tm.A.disp_width(src_old) if False else None, "base": "e"}}
        import aa_translate as A
        tm[aa_tm.key(src_old)]["w"] = A.disp_width(src_old)
        self.assertIsNone(aa_tm.reuse(tm, src_new, ()))


class TestBuildRoundTrip(unittest.TestCase):
    def test_build_and_lookup(self):
        import json, tempfile
        d = tempfile.mkdtemp(prefix="tm_test_")
        aa = os.path.join(d, "aa"); out = os.path.join(d, "汉化"); wk = os.path.join(d, "work")
        for p in (aa, out, wk, os.path.join(wk, "refine_ckpt")):
            os.makedirs(p, exist_ok=True)
        src = ["# 标题", "　│　こんにちは　│", "　　　　　　　", "　やる夫が来た　"]
        tr = ["# 标题", "　│　你好　　　　│", "　　　　　　　", "　亚陆欧来了　　　"]
        open(os.path.join(aa, "entry_1.txt"), "w", encoding="utf-8").write("\n".join(src))
        open(os.path.join(out, "entry_1_汉化.txt"), "w", encoding="utf-8").write("\n".join(tr))
        open(os.path.join(wk, "refine_ckpt", "entry_1.json"), "w", encoding="utf-8").write("{}")
        st = aa_tm.build("t_work", aa_dir=aa, out_dir=out, work_dir=wk, quiet=True)
        self.assertGreaterEqual(st["kept"], 2)                     # 两行台词都应入表
        tm = aa_tm.load("t_work", wk)
        self.assertEqual(aa_tm.reuse(tm, "　│　こんにちは　│", ()), tr[1])
        # 未校对章不回填
        open(os.path.join(out, "entry_2_汉化.txt"), "w", encoding="utf-8").write("\n".join(tr))
        open(os.path.join(aa, "entry_2.txt"), "w", encoding="utf-8").write("\n".join(src))
        st2 = aa_tm.build("t_work", aa_dir=aa, out_dir=out, work_dir=wk, quiet=True)
        self.assertEqual(st2["kept"], 0)

    def test_legacy_proofread_list_counts_as_finalized(self):
        """2026-10-01: 旧流程 backup_refine_aa_* 备份已清理 → 其章名单落盘到
        work/tm_proofread_legacy.txt，build(only_proofread=True) 必须认它（等价于 ckpt）。"""
        import tempfile
        d = tempfile.mkdtemp(prefix="tm_legacy_")
        aa = os.path.join(d, "aa"); out = os.path.join(d, "汉化"); wk = os.path.join(d, "work")
        for p in (aa, out, wk):
            os.makedirs(p, exist_ok=True)
        src = ["# 标题", "　│　こんにちは　│"]
        tr = ["# 标题", "　│　你好　　　　│"]
        open(os.path.join(aa, "entry_1.txt"), "w", encoding="utf-8").write("\n".join(src))
        open(os.path.join(out, "entry_1_汉化.txt"), "w", encoding="utf-8").write("\n".join(tr))
        # 无 ckpt、无备份目录 → 不收
        self.assertEqual(aa_tm.build("t_legacy", aa_dir=aa, out_dir=out, work_dir=wk,
                                     quiet=True)["kept"], 0)
        # 名单文件出现 → 收（注释行与空行忽略）
        with open(os.path.join(wk, aa_tm.LEGACY_PROOFREAD_FILE), "w", encoding="utf-8") as f:
            f.write("# 注释\n\nentry_1\n")
        st = aa_tm.build("t_legacy", aa_dir=aa, out_dir=out, work_dir=wk, quiet=True)
        self.assertGreaterEqual(st["kept"], 1)
        tm = aa_tm.load("t_legacy", wk)
        self.assertEqual(aa_tm.reuse(tm, "　│　こんにちは　│", ()), tr[1])


if __name__ == "__main__":
    unittest.main()
