# -*- coding: utf-8 -*-
"""refine 行级艺术闸门（2026-10-10）回归测试。

背景（`work/edits.jsonl` 1104 条人工修正归因）：185 处「艺术假名被译成中文」里
64 处由 refine 环节引入 —— refine 现场重判 `segment_line`（Q3 不信磁盘 meta.kind），
会从「整行就是画」的行里切出艺术碎片段（`あ゛　　あ` / `う　凹　さ` / `xぅ笏外`）
当台词段走 swallow 送模型，模型把它们译成中文 → 画面被改坏。

闸门口径 = **与主链同一条判据** `aa_translate.is_translatable`（`art_line_veto` 命中、
meta kind=art 且无 3+ 真台词假名、header/blank、text 行无日语）；过滤只作用于**模型 job**，
本地路径（`_restore_art_kana` / `no_text_restore` / `rebuild_row` 对齐修复）不受影响。
开关 `AA_REFINE_ART_GATE=0` 回旧行为。

运行：.venv/Scripts/python.exe -m unittest discover -s scripts/tests -p "test_refine_art_gate.py"
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

import aa_translate as T            # noqa: E402
import refine_aa as R               # noqa: E402

# 真实语料夹具（本作 entry_3146，refine 在 2026-10-10 那批就是在这两行上把艺术译坏的）
ART_ROW = ' \u3000\u3000 \u3000\u3000|\u3000\u3000\u3000\u3000::.: \u5726 \u4e42\u300d\u3000\u3000\u3000\u3000 \u30a3f\u65a5\u7b0a\u6c14.\u3000|\u3000\u3000\u3000\u3000\u3000 h'
# entry_3148:3634 —— 真台词行（meta kind=text，闸门不该拦）
DIAL_ROW = ' \u305d\u3046\u3001\u8b77\u8eab\u8853()\u304c\u3042\u308c\u3070\u306d\uff01'
DIAL_TR = DIAL_ROW


class _Fixture(unittest.TestCase):
    """临时仓库：aa/ + 汉化/ + work/line_meta_v2/，meta 为 1-based 物理行号。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="artgate_")
        self.aa_dir = os.path.join(self.dir, "aa")
        self.out_dir = os.path.join(self.dir, "汉化")
        self.work_dir = os.path.join(self.dir, "work")
        self.meta_dir = os.path.join(self.work_dir, "line_meta_v2")
        for d in (self.aa_dir, self.out_dir, self.meta_dir):
            os.makedirs(d, exist_ok=True)
        self.src = ["# 标题", ART_ROW, DIAL_ROW]
        self.tr = ["# 标题", ART_ROW, DIAL_TR]
        self._write("entry_1")

    def _write(self, base, meta=None):
        for d, lines, name in ((self.aa_dir, self.src, base + ".txt"),
                               (self.out_dir, self.tr, base + "_汉化.txt")):
            with io.open(os.path.join(d, name), "w", encoding="utf-8", newline="") as f:
                f.write("\n".join(lines))
        meta = meta if meta is not None else {
            "2": {"kind": "art", "confidence": 0.9, "reason": ["no_dialogue"]},
            "3": {"kind": "mixed", "confidence": 0.8, "reason": ["lz_mixed"]},
        }
        with io.open(os.path.join(self.meta_dir, base + ".json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        os.environ.pop("AA_REFINE_ART_GATE", None)

    def cfg(self):
        return {"aa_dir": self.aa_dir, "out_dir": self.out_dir, "work_dir": self.work_dir}

    def run_refine(self, calls):
        """跑 refine_chapter（模型调用打桩），返回 (stats, edits, calls)。"""
        orig = R._call_glm

        def _stub(system, body, *a, **kw):
            calls.append(body)
            return "不要"          # 有意返回"像译文"的中文：闸门关掉时它就会把艺术改坏

        R._call_glm = _stub
        try:
            base, stats, edits = R.refine_chapter(self.cfg(), "entry_1")
        finally:
            R._call_glm = orig
        return base, stats, edits


class TestRowModelGate(_Fixture):
    """闸门判据本身（零 API）。"""

    def test_meta_kind_art_is_gated(self):
        meta0 = T.load_meta_0based(self.meta_dir, "entry_1")
        self.assertFalse(T.is_translatable(1, self.src[1], meta0))
        self.assertTrue(R._row_model_gate(1, self.src, meta0))

    def test_art_line_veto_row_is_gated_even_if_meta_says_text(self):
        veto = "ニニニニニニニニニニニニニニ"          # 纯笔画墙：art_line_veto 命中
        self.assertTrue(T.art_line_veto(veto))
        meta0 = {0: ("text", "lz_text_lt5")}
        self.assertTrue(R._row_model_gate(0, [veto], meta0))

    def test_real_dialogue_row_not_gated(self):
        meta0 = T.load_meta_0based(self.meta_dir, "entry_1")
        self.assertFalse(R._row_model_gate(2, self.src, meta0))

    def test_missing_meta_or_bad_index_passes_through(self):
        self.assertFalse(R._row_model_gate(1, self.src, None))
        self.assertFalse(R._row_model_gate(99, self.src, {0: ("art", "")}))
        self.assertFalse(R._row_model_gate(-1, self.src, {}))


class TestGateInRefineChapter(_Fixture):
    """端到端：艺术行不再被切段送模型；开关关闭时回旧行为。"""

    def test_art_row_is_not_sent_and_stays_untouched(self):
        calls = []
        base, stats, edits = self.run_refine(calls)
        # 艺术行（0-based 1）不进模型、也不被改——拦截层是行级闸门还是段级判据（Rule A）都算达标
        self.assertFalse([b for b in calls if "笊气" in b], calls)
        # 艺术行没有任何 ok 编辑动它
        self.assertFalse([e for e in edits if e[0] == 1 and e[5] and e[3] != e[2]], edits)
        # 送模型的是台词行（那条日文残留段）
        self.assertTrue(calls, "台词行仍应送模型")

    def test_gate_off_restores_old_row_gate_behavior(self):
        os.environ["AA_REFINE_ART_GATE"] = "0"
        calls = []
        base, stats, edits = self.run_refine(calls)
        self.assertEqual(stats.get("art_gate_skip"), 0, stats)      # 行级闸门确实关了
        meta0 = T.load_meta_0based(self.meta_dir, "entry_1")
        self.assertFalse(R._row_model_gate(1, self.src, meta0))     # 关了就不拦
        # 纵深防御：段级判据（Rule A）依旧把艺术片段挡在模型外
        self.assertFalse([b for b in calls if "笊气" in b], calls)


if __name__ == "__main__":
    unittest.main()
