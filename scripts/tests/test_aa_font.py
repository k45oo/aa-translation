# -*- coding: utf-8 -*-
"""交付页 AA 等宽字体内嵌（common/aa_font.py + gen_aa_html 注入）单元/集成测试。

口径（2026-10-08）：交付页原本只有 Windows 字体栈，且 MS PGothic 是比例字体 →
AA 列位在浏览器里不齐（实测 15px：`|` 4px、`ﾆ` 8px、`ヽ` 11px、`漢` 15px、`　` 10px，比值 1.375）；
手机没有这些字体 → 回退随机 CJK 字体。现按语料取 Sarasa Mono SC 子集（woff2）内嵌，
实测以上字符宽度全部 ∈ {半宽, 全宽} 且比值恰好 2.000。

- 单档（inline）→ 字体以 data: URI 内嵌 index.html（一个文件带走）；
- 外置（external）→ 字体写成交付夹里的 aa_font.woff2 + 相对 URL；
- `--font none` → 不内嵌（回退旧行为）。
适用于已生成交付物的机器外发（.gitignore 忽略 交付HTML/，字体文件随之走）。

运行：.venv/Scripts/python.exe -m unittest discover -s scripts/tests
"""
import os
import shutil
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS_DIR)
sys.path.insert(0, os.path.join(SCRIPTS, "common"))
sys.path.insert(0, os.path.join(SCRIPTS, "works"))

import aa_font as AF
import gen_aa_html
import registry

try:
    import fontTools  # noqa: F401
    HAVE_FONTTOOLS = True
except Exception:                                     # pragma: no cover
    HAVE_FONTTOOLS = False

SAMPLE = "漢字　ﾆヽ丶三ミ｜|＿"                          # 中日文 + AA 画笔（全/半角假名、借字、制表符）


class TestSubset(unittest.TestCase):
    def test_css_stack_order(self):
        # 默认（2026-10-08 用户 A/B 定案）：内嵌字体排在 Windows 字体**之后**——PGothic 在时观感不变，
        # 只在缺字体的设备（手机/非 Windows）兜底
        dflt = AF.font_css("aa_font.woff2")
        self.assertIn("@font-face", dflt)
        self.assertIn("font-family:'%s'" % AF.FAMILY, dflt)
        stack_d = dflt.split("pre.aa{font-family:")[1]
        self.assertTrue(stack_d.startswith("\"ＭＳ Ｐゴシック\""), "默认栈首必须是 PGothic（作者画 AA 用的字体）")
        self.assertIn("'%s'" % AF.FAMILY, stack_d)
        first = AF.font_css("aa_font.woff2", first=True)
        self.assertTrue(first.split("pre.aa{font-family:")[1].startswith("'%s'," % AF.FAMILY),
                        "显式 first → 内嵌字体在栈首（严格 1:2 网格，会改变作者原观感）")
        last = AF.font_css("aa_font.woff2", first=False)
        self.assertFalse(last.split("pre.aa{font-family:")[1].startswith("'%s'," % AF.FAMILY))

    def test_aa_metric_font_goes_before_sarasa_in_last_mode(self):
        """与 PGothic 度量一致的 aahub 子集：默认栈序里排在 Windows 字体之后、Sarasa 之前。

        手机/非 Windows 设备没有 MS PGothic → 落到它 → AA 列位与 PC 一致（用户手机报障的修法）；
        `first`（严格 1:2 网格）模式里不放它，免得把用户主动选的网格又破坏掉。
        """
        css = AF.font_css("aa_font.woff2", first=False, aa_ref="aa_font_aa.woff2")
        self.assertEqual(css.count("@font-face"), 2, "应有两个 @font-face（AA 度量字体 + Sarasa 兜底）")
        self.assertIn("font-family:'%s'" % AF.AA_FAMILY, css)
        stack = css.split("pre.aa{font-family:")[1]
        self.assertIn("'%s', '%s'" % (AF.AA_FAMILY, AF.FAMILY), stack,
                      "AA 度量字体必须紧跟 Windows 字体、排在 Sarasa 之前")
        self.assertTrue(stack.startswith('"ＭＳ Ｐゴシック"'), "PC 上仍以 PGothic 为准（观感不变）")
        first = AF.font_css("aa_font.woff2", first=True, aa_ref="aa_font_aa.woff2")
        self.assertNotIn(AF.AA_FAMILY, first, "严格网格模式不放 AA 度量字体")

    def test_aa_font_env_and_source_exists(self):
        self.assertTrue(os.path.exists(AF.aa_font_source()), "仓库里得有 fonts/aahub.ttf（交付靠它内嵌）")
        old = {k: os.environ.get(k) for k in ("AA_AA_FONT_AA", "AA_AA_FONT_AA_FILE")}
        try:
            os.environ.pop("AA_AA_FONT_AA", None)
            os.environ.pop("AA_AA_FONT_AA_FILE", None)
            self.assertTrue(AF.aa_font_enabled(), "默认开：手机端不「错位」靠它")
            os.environ["AA_AA_FONT_AA"] = "0"
            self.assertFalse(AF.aa_font_enabled())
            os.environ["AA_AA_FONT_AA_FILE"] = r"C:\tmp\aa.ttf"
            self.assertTrue(AF.aa_font_source().endswith("aa.ttf"))
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    @unittest.skipUnless(HAVE_FONTTOOLS, "需要 fontTools（工作台 .venv 已装）")
    def test_aahub_是_PGothic_度量(self):
        """aahub 必须与 MS PGothic 度量一致（差 <0.03em）——不然手机上还是「错位」。

        这是「手机端显示正确」的唯一依据：Android 没有 MS PGothic，只能靠内嵌字体顶上。
        没装 msgothic.ttc 的机器（非 Windows/无该字体）跳过。
        """
        ttc_path = r"C:\Windows\Fonts\msgothic.ttc"
        if not os.path.exists(ttc_path):
            self.skipTest("本机没有 msgothic.ttc")
        from fontTools.ttLib import TTCollection, TTFont
        aa = TTFont(AF.aa_font_source(), lazy=True)
        pg = None
        for f in TTCollection(ttc_path, lazy=True).fonts:
            if (f["name"].getDebugName(1) or "").strip() in ("MS PGothic", "ＭＳ Ｐゴシック"):
                pg = f
        if pg is None:
            self.skipTest("msgothic.ttc 里没有 MS PGothic")

        def widths(font):
            upem = font["head"].unitsPerEm
            return {cp: font["hmtx"][g][0] / float(upem)
                    for cp, g in font.getBestCmap().items() if g in font["hmtx"].metrics}
        wa, wp = widths(aa), widths(pg)
        # AA 画笔主力字符（半角 + 假名 + 全角标点/记号）
        chars = [chr(i) for i in range(0x21, 0x7F)] + \
                [chr(i) for i in range(0xFF61, 0xFFA0)] + \
                [chr(i) for i in range(0x3041, 0x3097)] + [chr(i) for i in range(0x30A1, 0x30FB)] + \
                list("　、。，．・：；？！ー―／＼｜…（）「」『』【】＋－±×÷＝＜＞☆★○●◎◇")
        diffs = [abs(wa[ord(c)] - wp[ord(c)]) for c in chars if ord(c) in wa and ord(c) in wp]
        self.assertGreater(len(diffs), 100, "采样太少，测不出度量一致性")
        # 实测（364 个 AA 常用字符）：平均 0.0132em；|差|>0.05em 的只有 2 个（0.5%）；
        # 最差是半角长音符 `ｰ`（U+FF70）0.141em ≈ 15px 下 2.1px——罕见字符，允许。
        self.assertLess(sum(diffs) / len(diffs), 0.03, "平均宽度差应 <0.03em（实测 0.013em）")
        self.assertLess(sum(1 for d in diffs if d > 0.05) / float(len(diffs)), 0.05,
                        "|差|>0.05em 的字符不该超过 5%（实测 0.5%）")
        self.assertLess(max(diffs), 0.15, "最差字符也不该超过 0.15em")
        # 垂直度量：AA 的纵向连续性靠它
        self.assertEqual((aa["hhea"].ascent - aa["hhea"].descent) / float(aa["head"].unitsPerEm),
                         (pg["hhea"].ascent - pg["hhea"].descent) / float(pg["head"].unitsPerEm),
                         "asc+desc 必须与 PGothic 相同（行盒内的基线位置）")

    def test_env_switches(self):
        old = {k: os.environ.get(k) for k in ("AA_AA_FONT", "AA_AA_FONT_STACK", "AA_AA_FONT_FILE")}
        try:
            os.environ.pop("AA_AA_FONT", None)
            os.environ.pop("AA_AA_FONT_STACK", None)
            self.assertTrue(AF.font_enabled())
            self.assertFalse(AF.stack_first(), "默认 = last（内嵌字体兜底，PC 观感不变）")
            os.environ["AA_AA_FONT"] = "0"
            self.assertFalse(AF.font_enabled())
            os.environ["AA_AA_FONT_STACK"] = "first"
            self.assertTrue(AF.stack_first())
            os.environ["AA_AA_FONT_FILE"] = r"C:\tmp\f.ttf"
            self.assertTrue(AF.font_source().endswith("f.ttf"))
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    @unittest.skipUnless(HAVE_FONTTOOLS, "需要 fontTools（工作台 .venv 已装）")
    def test_subset_covers_corpus_chars_and_caches(self):
        chars = AF.base_chars() | set(SAMPLE)
        out, missing, _ = AF.build_subset(chars)
        self.assertTrue(os.path.exists(out), "子集应落盘")
        self.assertTrue(out.endswith(".woff2"))
        # 同字符集 → 复用同一份缓存
        out2, _, _ = AF.build_subset(chars)
        self.assertEqual(out, out2)
        from fontTools.ttLib import TTFont
        cmap = TTFont(out).getBestCmap()
        for c in SAMPLE:
            self.assertIn(ord(c), cmap, "语料字符 %r 必须进子集" % c)

    @unittest.skipUnless(HAVE_FONTTOOLS, "需要 fontTools")
    def test_chars_of_files_reads_txt_and_gz(self):
        tmp = tempfile.mkdtemp(prefix="hermes-test-font-")
        self.addCleanup(shutil.rmtree, tmp, True)
        p1 = os.path.join(tmp, "a.txt")
        open(p1, "w", encoding="utf-8").write("漢\nあ")
        import gzip
        p2 = os.path.join(tmp, "b.txt.gz")
        with gzip.open(p2, "wt", encoding="utf-8") as f:
            f.write("ﾆX")
        got = AF.chars_of_files([p1, p2])
        self.assertEqual(got, set("漢\nあﾆX") - {"\n"}, "txt / txt.gz 都要读，换行剔除")


class TestGenIntegration(unittest.TestCase):
    """fukou（24 章）实跑三种模式：默认内嵌、外置写文件、--font none。"""

    def _gen(self, *extra):
        from _work_guard import work_cfg
        cfg = work_cfg("fukou", ("aa_dir", "out_dir"), "合集字体子集集成用例")
        orig = cfg["html_out"]
        tmp = tempfile.mkdtemp(prefix="hermes-test-fontgen-")
        self.addCleanup(shutil.rmtree, tmp, True)
        cfg["html_out"] = os.path.join(tmp, "out")
        try:
            sys.argv = ["x", "fukou", "--no-source"] + list(extra)
            gen_aa_html.main("fukou")
            return cfg["html_out"]
        finally:
            cfg["html_out"] = orig

    @unittest.skipUnless(HAVE_FONTTOOLS, "需要 fontTools")
    def test_inline_embeds_base64_font(self):
        out = self._gen()
        html = open(os.path.join(out, "index.html"), encoding="utf-8").read()
        self.assertIn("@font-face", html)
        self.assertIn("'%s'" % AF.FAMILY, html)
        self.assertIn("data:font/woff2;base64,", html, "单档必须内嵌字体（一个文件带走）")
        self.assertNotIn(gen_aa_html.FONT_NAME, html.split("@font-face")[1][:200],
                         "单档不该引用同夹字体文件")

    @unittest.skipUnless(HAVE_FONTTOOLS, "需要 fontTools")
    def test_external_writes_font_file(self):
        out = self._gen("--external")
        fp = os.path.join(out, gen_aa_html.FONT_NAME)
        self.assertTrue(os.path.exists(fp), "外置形态应把子集写到交付夹")
        self.assertGreater(os.path.getsize(fp), 10240)
        html = open(os.path.join(out, "index.html"), encoding="utf-8").read()
        self.assertIn("url(%s)" % gen_aa_html.FONT_NAME, html)
        self.assertNotIn("data:font/woff2;base64,", html, "外置形态不内嵌 base64")

    def test_edit_server_serves_woff2_as_font_mime(self):
        """交付夹里的字体经编辑服务（127.0.0.1:8770）访问时必须是 font/woff2。"""
        sys.path.insert(0, os.path.join(SCRIPTS, "tools"))
        import edit_server
        self.assertEqual(edit_server.Handler._MIME.get(".woff2"), "font/woff2")

    @unittest.skipUnless(HAVE_FONTTOOLS, "需要 fontTools")
    def test_font_none_disables(self):
        out = self._gen("--font", "none")
        html = open(os.path.join(out, "index.html"), encoding="utf-8").read()
        self.assertNotIn("@font-face", html)
        self.assertFalse(os.path.exists(os.path.join(out, gen_aa_html.FONT_NAME)))

    @unittest.skipUnless(HAVE_FONTTOOLS, "需要 fontTools")
    def test_font_stack_default_last(self):
        """默认栈序 = PGothic 在前（作者画 AA 用的字体），内嵌字体只兜底。"""
        out = self._gen()                       # 不带 --font-stack
        html = open(os.path.join(out, "index.html"), encoding="utf-8").read()
        stack = html.rsplit("pre.aa{font-family:", 1)[1].split(";")[0]
        self.assertTrue(stack.startswith("\"ＭＳ Ｐゴシック\""), stack)
        self.assertIn("'%s'" % AF.FAMILY, stack)

    @unittest.skipUnless(HAVE_FONTTOOLS, "需要 fontTools")
    def test_font_stack_first(self):
        out = self._gen("--font-stack", "first")
        html = open(os.path.join(out, "index.html"), encoding="utf-8").read()
        stack = html.rsplit("pre.aa{font-family:", 1)[1].split(";")[0]
        self.assertTrue(stack.startswith("'%s'" % AF.FAMILY))


if __name__ == "__main__":
    unittest.main()
