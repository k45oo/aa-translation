# -*- coding: utf-8 -*-
"""阅读器「行重译」（Shift+右键行号）—— 判据层，不花 API。

覆盖：
  ① 闸门：未汉化章 / 行号越界 / 空行 / 帖头行 / art 行（override·meta·规则三条链）→ 4xx 不进模型
  ② 行类型链：override > work/line_meta_v2.kind > 规则（logic.line_type_of）
  ③ text 行 → translate_lines；mixed 行有可发段 → translate_lines_mixed（与主链分流同口径）
  ④ 链路 env：临时指到本作链首（AA_BACKEND_AUTO=1），退出后逐键还原；
     用户已显式设 AA_BACKEND → 原样尊重不覆盖
  ⑤ aa_translate.WORK_ID 进出还原；模型未产出 → 502（绝不返回半成品去覆盖成品）
  ⑥ pipeline=pokemon 的作品先走固定表直出（零调用），命中时完全不碰模型

运行：.venv/Scripts/python.exe -m unittest scripts.tests.test_line_retranslate -v
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
SCRIPTS = os.path.join(_REPO_ROOT, "scripts")
for _p in (_REPO_ROOT, SCRIPTS, os.path.join(SCRIPTS, "common"), os.path.join(SCRIPTS, "works"),
           os.path.join(_REPO_ROOT, "aa_web_app")):
    sys.path.insert(0, _p)

import aa_translate as T                       # noqa: E402
import work_pipeline as WP                     # noqa: E402
from aa_web_app import line_retranslate as LR  # noqa: E402

WORK = "测试作品"
SRC = [
    "# 标题",                                 # 0
    "5 ： ◆abc ： 2026-01-01",                # 1 header
    "こんにちは、やる夫です",                  # 2 text
    "",                                       # 3 blank
    "  |＼|／|  ",                            # 4 art（规则：纯 ASCII 笔画）
    "こんにちは　＜ミニ",                      # 5 mixed（由 meta 指定）
    "ミニニニニ",                              # 6 art（由 override 指定）
]
TEXT_IDX, BLANK_IDX, ART_IDX, MIX_IDX, OV_ART_IDX = 2, 3, 4, 5, 6
SEGS = [{"kind": "text", "start": 0, "end": 5, "src_width": 10},
        {"kind": "art", "start": 5, "end": 9, "src_width": 8}]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="line_retr_")
        self.aa_dir = os.path.join(self.tmp, "aa")
        self.out_dir = os.path.join(self.tmp, "汉化")
        self.work_dir = os.path.join(self.tmp, "work")
        for d in (self.aa_dir, self.out_dir, self.work_dir):
            os.makedirs(d)
        self.base = "entry_1"
        self.fname = self.base + "_汉化.txt"
        self._write(self.aa_dir, self.base + ".txt", SRC)
        self._write(self.out_dir, self.fname, SRC)
        self.cfg = {"work_dir": self.work_dir, "aa_dir": self.aa_dir,
                    "out_dir": self.out_dir, "title": "测试作品"}
        self._old_wid = T.WORK_ID

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        T.WORK_ID = self._old_wid

    def _write(self, d, name, rows):
        with open(os.path.join(d, name), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(rows))

    def _meta(self, obj):
        d = os.path.join(self.work_dir, "line_meta_v2")
        os.makedirs(d, exist_ok=True)
        import json
        with open(os.path.join(d, self.base + ".json"), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)

    def _overrides(self, obj):
        d = os.path.join(self.work_dir, "line_meta_overrides")
        os.makedirs(d, exist_ok=True)
        import json
        with open(os.path.join(d, self.base + ".json"), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)

    def call(self, idx):
        return LR.retranslate_line(WORK, self.cfg, self.fname, idx)


class TestGates(Base):
    def test_未汉化章拒绝(self):
        r = LR.retranslate_line(WORK, self.cfg, self.base + ".txt", TEXT_IDX)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], 422)

    def test_行号越界(self):
        r = self.call(99)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], 404)

    def test_空行拒绝(self):
        r = self.call(BLANK_IDX)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], 422)
        self.assertIn("空行", r["error"])

    def test_帖头行拒绝(self):
        r = self.call(1)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], 422)
        self.assertIn("帖头", r["error"])

    def test_art行按规则拒绝(self):
        r = self.call(ART_IDX)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], 422)
        self.assertIn("AA 艺术", r["error"])

    def test_art行按meta拒绝(self):
        self._meta({str(ART_IDX + 1): {"kind": "art"}})
        r = self.call(ART_IDX)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], 422)

    def test_art行按override拒绝(self):
        self._overrides({str(OV_ART_IDX + 1): "art"})
        r = self.call(OV_ART_IDX)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], 422)

    def test_闸门不碰模型(self):
        with mock.patch.object(T, "translate_lines") as m:
            self.call(ART_IDX)
            self.call(BLANK_IDX)
            self.call(1)
            m.assert_not_called()

    def test_kind链_override优先于meta(self):
        self._meta({str(TEXT_IDX + 1): {"kind": "text"}})
        self._overrides({str(TEXT_IDX + 1): "art"})
        self.assertEqual(LR.kind_of(self.cfg, self.fname, TEXT_IDX), "art")
        self.assertFalse(self.call(TEXT_IDX)["ok"])

    def test_kind链_meta优先于规则(self):
        # 规则判 text，meta 判 mixed → 以 meta 为准
        self._meta({str(TEXT_IDX + 1): {"kind": "mixed"}})
        self.assertEqual(LR.kind_of(self.cfg, self.fname, TEXT_IDX), "mixed")

    def test_kind链_无标注退回规则(self):
        self.assertEqual(LR.kind_of(self.cfg, self.fname, TEXT_IDX), "text")


class TestModelPath(Base):
    def test_text行走整行协议(self):
        called = {}

        def fake(flagged, **kw):
            called["flagged"] = flagged
            called["kw"] = kw
            return {TEXT_IDX: "你好，我是やる夫"}, [], []

        with mock.patch.object(T, "translate_lines", side_effect=fake):
            with mock.patch.object(T, "translate_lines_mixed") as mm:
                r = self.call(TEXT_IDX)
                mm.assert_not_called()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["text"], "你好，我是やる夫")
        self.assertEqual(r["kind"], "text")
        self.assertEqual(r["track"], "model")
        self.assertTrue(r["backend"])
        self.assertEqual(called["flagged"], [(TEXT_IDX, SRC[TEXT_IDX])])
        self.assertEqual(called["kw"].get("base"), self.base)
        self.assertIs(called["kw"].get("work_r18"), False)

    def test_mixed行走段级协议(self):
        self._meta({str(MIX_IDX + 1): {"kind": "mixed", "segments": SEGS}})
        called = {}

        def fake_seg(flagged, **kw):
            called["flagged"] = flagged
            return {MIX_IDX: "你好　＜ミニ"}, [], []

        with mock.patch.object(T, "translate_lines_mixed", side_effect=fake_seg):
            with mock.patch.object(T, "translate_lines") as m1:
                r = self.call(MIX_IDX)
                m1.assert_not_called()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["track"], "mixed-seg")
        self.assertEqual(called["flagged"][0][0], MIX_IDX)
        self.assertEqual(called["flagged"][0][2], SEGS)

    def test_模型未产出返回502(self):
        with mock.patch.object(T, "translate_lines",
                               return_value=({}, [TEXT_IDX], ["原样照抄"])):
            r = self.call(TEXT_IDX)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], 502)
        self.assertIn("未产出", r["error"])

    def test_模型抛异常返回502(self):
        with mock.patch.object(T, "translate_lines", side_effect=RuntimeError("boom")):
            r = self.call(TEXT_IDX)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], 502)

    def test_术语表缺失的SystemExit被接住(self):
        # glossary_prompt_hint 在作品术语表缺失时设计性 raise SystemExit；
        # 逃出请求线程会杀 uvicorn（2026-09-08 划词事故），必须转成给用户看的错误
        with mock.patch.object(T, "translate_lines",
                               side_effect=SystemExit("无作品术语表 …先生成")):
            r = self.call(TEXT_IDX)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], 409)
        self.assertIn("术语表", r["error"])

    def test_译文等于原文不覆盖(self):
        with mock.patch.object(T, "translate_lines",
                               return_value=({TEXT_IDX: SRC[TEXT_IDX]}, [], [])):
            r = self.call(TEXT_IDX)
        self.assertFalse(r["ok"])

    def test_重译引入新假名被拒(self):
        # 实测事故（souseki_kami entry_5779:84）：段级 meta 把台词尾巴 `た` 切进 art 段 →
        # 主流转结果在补位空白里凭空多一个假名（宽度/残留/art 三校验全过）→ 必须拒写
        self._write(self.out_dir, self.fname,
                    [("# 标题" if i != TEXT_IDX else "你好，我是勇者") for i in range(len(SRC))])
        self.assertEqual(LR.new_kana("你好，我是勇者", "こんにちは、勇者です"),
                         sorted("こんにちはです"))
        with mock.patch.object(T, "translate_lines",
                               return_value=({TEXT_IDX: "你好，我是勇者た"}, [], [])):
            r = self.call(TEXT_IDX)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], 422)
        self.assertIn("假名", r["error"])

    def test_原文已有假名不算新增(self):
        # 未译行（译文 == 原文）重译时原文假名照抄回显不算「新增」
        with mock.patch.object(T, "translate_lines",
                               return_value=({TEXT_IDX: SRC[TEXT_IDX] + "。"}, [], [])):
            r = self.call(TEXT_IDX)
        self.assertTrue(r["ok"], r)
        self.assertEqual(LR.new_kana(SRC[TEXT_IDX], SRC[TEXT_IDX] + "。"), [])

    def test_译文缺行时按原文行比对(self):
        # 行数不齐（该行无译文）→ 参照退回原文行，不因「当前译文为空」把所有假名判为新增
        self._write(self.out_dir, self.fname, SRC[:TEXT_IDX])
        with mock.patch.object(T, "translate_lines",
                               return_value=({TEXT_IDX: SRC[TEXT_IDX] + "。"}, [], [])):
            r = self.call(TEXT_IDX)
        self.assertTrue(r["ok"], r)


class TestGlobals(Base):
    def test_链路env与WORK_ID还原(self):
        import aa_config as AC
        old = {k: os.environ.pop(k, None)
               for k in ("AA_BACKEND", "AA_BACKEND_AUTO")}
        T.WORK_ID = ""
        try:
            with mock.patch.object(T, "translate_lines",
                                   return_value=({TEXT_IDX: "你好"}, [], [])):
                r = self.call(TEXT_IDX)
            self.assertTrue(r["ok"], r)
            self.assertNotIn("AA_BACKEND", os.environ)          # 调用后逐键还原
            self.assertNotIn("AA_BACKEND_AUTO", os.environ)
            self.assertEqual(T.WORK_ID, "")
            self.assertEqual(r["backend"], AC.auto_backend(False)[0])
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v

    def test_已设AA_BACKEND不覆盖(self):
        old = os.environ.get("AA_BACKEND")
        os.environ["AA_BACKEND"] = "deepinfra"
        try:
            with mock.patch.object(T, "translate_lines",
                                   return_value=({TEXT_IDX: "你好"}, [], [])):
                r = self.call(TEXT_IDX)
            self.assertEqual(os.environ.get("AA_BACKEND"), "deepinfra")
            self.assertEqual(r["backend"], "deepinfra")
        finally:
            if old is None:
                os.environ.pop("AA_BACKEND", None)
            else:
                os.environ["AA_BACKEND"] = old


class TestPokemonDirect(Base):
    def test_固定表直出零调用(self):
        with mock.patch.object(WP, "pipeline_of", return_value=WP.POKEMON):
            with mock.patch.object(LR, "pokemon_direct", return_value=("标准译文", "struct")):
                with mock.patch.object(T, "translate_lines") as m:
                    r = self.call(TEXT_IDX)
                    m.assert_not_called()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["text"], "标准译文")
        self.assertEqual(r["source"], "fixed")
        self.assertEqual(r["track"], "struct")

    def test_固定表未命中落回模型(self):
        with mock.patch.object(WP, "pipeline_of", return_value=WP.POKEMON):
            with mock.patch.object(LR, "pokemon_direct", return_value=(None, "")):
                with mock.patch.object(T, "translate_lines",
                                       return_value=({TEXT_IDX: "模型译文"}, [], [])):
                    r = self.call(TEXT_IDX)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["source"], "model")


if __name__ == "__main__":
    unittest.main(verbosity=2)
