# -*- coding: utf-8 -*-
"""艺术片段表（丙，2026-10-21）：挖掘 / 命中 / 守卫 / 接线。

背景：甲（整行是画）/乙（短 kana 片段）走结构判据，够不到「同一幅画的固定笔画串」——
尤其 no-meta 的整行协议行（`ﾉﾉ斗ぅて`/`ﾐｬｯネ`/`ニム`，模型会把整行重写）。
丙把这些片段记成**作品级表** `work/art_frag_<work>.json`，命中即本地照抄。

运行：.venv/Scripts/python.exe -m unittest discover -s scripts/tests -p "test_art_frag.py"
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS_DIR)
for _p in ("common", "works", "tools", "fix"):
    sys.path.insert(0, os.path.join(SCRIPTS, _p))

import aa_art_kana as AK          # noqa: E402
import aa_translate as T          # noqa: E402


class _TmpFrag(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="artfrag_")
        self.tbl = os.path.join(self.dir, "art_frag_test.json")
        self.frags = {"んう": {"src": "edits", "edits": 3, "art": 0, "lost": 0, "kept": 0},
                      "ﾐｬｯネ": {"src": "edits", "edits": 2, "art": 0, "lost": 0, "kept": 0},
                      "ィぅて": {"src": "edits", "edits": 1, "art": 0, "lost": 0, "kept": 0},
                      "これ": {"src": "edits", "edits": 9, "art": 9, "lost": 0, "kept": 0}}
        with io.open(self.tbl, "w", encoding="utf-8") as f:
            json.dump({"schema": 1, "kind": "art_frag", "frags": self.frags}, f, ensure_ascii=False)
        os.environ["AA_ART_FRAG_FILE"] = self.tbl
        os.environ.pop("AA_ART_FRAG", None)
        os.environ.pop("AA_ART_FRAG_AUTO", None)
        AK.reset_cache()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        for k in ("AA_ART_FRAG_FILE", "AA_ART_FRAG", "AA_ART_FRAG_AUTO"):
            os.environ.pop(k, None)
        AK.reset_cache()

    def _segs(self, line, parts):
        segs, pos = [], 0
        for kind, text in parts:
            segs.append({"start": pos, "end": pos + len(text), "kind": kind,
                         "src_width": T.disp_width(text)})
            pos += len(text)
        assert pos == len(line)
        return segs


class TestFragTable(_TmpFrag):
    def test_表加载(self):
        self.assertEqual(AK.frags().get("んう", {}).get("edits"), 3)
        self.assertTrue(AK.frag_enabled())

    def test_等值命中(self):
        line = " :|　ｲ んう ﾉ|: i"
        segs = self._segs(line, [("art", " :|　ｲ "), ("text", "んう"), ("art", " ﾉ|: i")])
        self.assertTrue(AK.seg_frag_art("んう", line=line, segs=segs))

    def test_子串命中_长片段(self):
        line = " :|　ィぅて芹 ﾉ|: i"
        segs = self._segs(line, [("art", " :|　"), ("text", "ィぅて芹"), ("art", " ﾉ|: i")])
        self.assertTrue(AK.seg_frag_art("ィぅて芹", line=line, segs=segs))   # ⊃ 表内 `ィぅて`

    def test_常用词停用表(self):
        self.assertFalse(AK.seg_frag_art("これ", line=" :|　これ ﾉ|: i",
                                         segs=[{"kind": "art"}, {"kind": "text"}]))

    def test_非画行不表态(self):
        line = "それは んう です"
        segs = self._segs(line, [("text", "それは "), ("text", "んう"), ("text", " です")])
        self.assertFalse(AK.seg_frag_art("んう", line=line, segs=segs))     # 行内无 art 段

    def test_段落形状守卫(self):
        self.assertFalse(AK.seg_frag_art("んう！"))        # 标点
        self.assertFalse(AK.seg_frag_art("んう2"))         # 数字
        self.assertFalse(AK.seg_frag_art("んうヾヾヾ"))     # 超长（>6）
        self.assertFalse(AK.seg_frag_art("あの子"))         # 含实义汉字

    def test_开关(self):
        os.environ["AA_ART_FRAG"] = "0"
        try:
            self.assertFalse(AK.seg_frag_art("んう"))
            self.assertFalse(AK.line_frag_art(" :|　んう ﾉ|: i"))
        finally:
            os.environ.pop("AA_ART_FRAG", None)


class TestLineFrag(_TmpFrag):
    def test_假名全被覆盖才判画(self):
        art = " :　|　j厶 八　 :|: i|::"      # 画密够（非空白里 art 字符过半）
        self.assertTrue(AK.line_frag_art(art + " んう " + art))
        # 行里多一个表外假名（`て`）→ 不是整行画
        self.assertFalse(AK.line_frag_art(art + " んうて " + art))

    def test_系统标记与标点守卫(self):
        self.assertFalse(AK.line_frag_art(" :|　んう|: i　※　【1D3:1】"))
        self.assertFalse(AK.line_frag_art(" :|　んう、|: i"))

    def test_画密度守卫(self):
        self.assertFalse(AK.line_frag_art("んう"))                    # 纯文本，不是画行

    def test_接线_is_translatable(self):
        line = " :|　ﾉﾉ斗" + "んう" + "|: i"
        meta = {0: ("text", "lz_short_dialogue")}
        self.assertFalse(T.is_translatable(0, line, meta))


class TestMining(_TmpFrag):
    def test_edits_真值直接进表(self):
        frags = AK.mine_frags([], ["んう", "ﾐｬｯネ", "これ"])
        self.assertIn("んう", frags)
        self.assertIn("ﾐｬｯネ", frags)
        self.assertNotIn("これ", frags)          # 停用表里的常用词不进表
        self.assertEqual(frags["んう"]["src"], "edits")

    def test_语料弱证据阈值(self):
        src = " :|　んら ﾉ|: i"
        segs = [{"start": 0, "end": 4, "kind": "art"},
                {"start": 4, "end": 6, "kind": "text"},
                {"start": 6, "end": len(src), "kind": "art"}]
        # 整行未动 3 次 → 进表
        frags = AK.mine_frags([(src, src, segs)] * 3, [])
        self.assertIn("んら", frags)
        # 只有 2 次 → 不到阈值
        self.assertNotIn("んら", AK.mine_frags([(src, src, segs)] * 2, []))

    def test_语料里有译文就不算画(self):
        src = " :|　んら ﾉ|: i"
        tr = " :|　嗯啦 ﾉ|: i"
        segs = [{"start": 0, "end": 4, "kind": "art"},
                {"start": 4, "end": 6, "kind": "text"},
                {"start": 6, "end": len(src), "kind": "art"}]
        frags = AK.mine_frags([(src, tr, segs)] * 5, [])
        self.assertNotIn("んら", frags)


class TestAutoRefresh(_TmpFrag):
    def test_edits_比表新时现挖补表(self):
        wd = os.path.join(self.dir, "work")
        os.makedirs(wd)
        ep = os.path.join(wd, "edits.jsonl")
        rows = [
            {"ts": "2026-10-21 10:00:00", "file": "entry_1_汉化.txt", "line": 5,
             "before": " :|　唔 ﾉ|: i", "after": " :|　ぅて ﾉ|: i"},
        ]
        with io.open(ep, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.environ["AA_ART_FRAG_FILE"] = self.tbl          # 静态表里没有 `ぅて`
        AK.reset_cache()
        tab = AK.frags(work_dir=wd)
        self.assertIn("ぅて", tab)                          # 从 edits 现挖补进来
        self.assertIn("んう", tab)                          # 静态表仍在


if __name__ == "__main__":
    unittest.main()
