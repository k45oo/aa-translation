# -*- coding: utf-8 -*-
"""「仅 HTML 文件」简版交付（`gen_aa_html.py --plain`，2026-10-11）回归测试。

与正式交付文件夹（`交付HTML/<名>_汉化合集/`：编辑服务 exe + 源 txt.gz + data/）并列的第二条
交付路径：**自包含 .html**，文件名 `【机翻】<交付短名>_<起>-<止>.html`，落在交付 HTML 目录。

口径（改了要同步这里）：
  · 只出 .html —— 不带 exe / 编辑服务 / 手机阅读 bat / edit_meta / 源 txt.gz / data/ 目录；
  · 每文件**自包含**：章节数据内嵌（`window.AA_DATA`）+ AA 字体 base64 内嵌 → 双击 file:// 即读；
  · `--tr-only`（仅译文）→ payload 只有 `tr`（没有原文数据），页面不显示「原文」按钮；
  · `--split N` → 每 N 章一个文件（0 = 全部一个），每部分自带目录 + 上一/下一部分链接；
  · 阅读进度按**整部**章序号存（`{ch, y, f}`）→ 打开别的分页文件时给「上次读到第 N 话」提示；
  · 重跑先删本作品上一次的简版文件（`【机翻】<短名>_*.html`），章数/分页变了不留陈旧件。

正文等价性：切片渲染不得改变正文 —— 单独渲染同一章 vs 分页文件里的那一章必须逐字节相同。
"""
import base64
import contextlib
import glob
import io
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS_DIR)
sys.path.insert(0, os.path.join(SCRIPTS, "common"))
sys.path.insert(0, os.path.join(SCRIPTS, "works"))

import registry                      # noqa: E402
import gen_aa_html                   # noqa: E402

import importlib                     # noqa: E402

_PAY_RE = re.compile(r'window\.AA_DATA\[(\d+)\]=(\{[^\n]*?\});')


def _payloads(html):
    """index.html 内嵌段 → {章号: 已解压的 {tr, src|None}}（v1/v2/仅译文 三种都吃）。"""
    out = {}
    for n, raw in _PAY_RE.findall(html):
        d = json.loads(raw)
        if not d.get("src"):
            out[int(n)] = {"tr": _unz(d["tr"]), "src": None}     # 仅译文
            continue
        src = _unz(d["src"])
        if d.get("p"):                                            # v2：tr = src + 差异行
            rows = src.split("\n")
            for step in _unz(d["p"]).split("\n"):
                if not step:
                    continue
                k, _, txt = step.partition(gen_aa_html.PATCH_SEP)
                rows[int(k)] = txt
            out[int(n)] = {"tr": "\n".join(rows), "src": src}
        else:
            out[int(n)] = {"tr": _unz(d["tr"]), "src": src}
    return out


def _unz(b64):
    return gzip.decompress(base64.b64decode(b64)).decode("utf-8")


class PlainFixture(unittest.TestCase):
    """共用一次分页生成（fukou 24 章、每 10 章一个文件 = 3 个文件）。"""

    SHARED = None
    FILES = None

    @classmethod
    def setUpClass(cls):
        from _work_guard import work_cfg
        cfg = work_cfg("fukou", ("aa_dir", "out_dir"), "「仅 HTML 文件」简版用例")
        cls._orig_out = cfg["html_out"]
        cls.tmp = tempfile.mkdtemp(prefix="hermes-test-plain-")
        cfg["html_out"] = os.path.join(cls.tmp, "交付HTML", "不幸の催眠_汉化合集")
        cls.out_dir = os.path.join(cls.tmp, "交付HTML")
        os.makedirs(cls.out_dir, exist_ok=True)
        try:
            sys.argv = ["x", "fukou", "--plain", "--split", "10"]
            cls.FILES = gen_aa_html.main("fukou")
        finally:
            cfg["html_out"] = cls._orig_out

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @property
    def short(self):
        return "不幸の催眠"

    def read(self, name):
        return open(os.path.join(self.out_dir, name), encoding="utf-8", newline="").read()


class TestPlainOutput(PlainFixture):

    def test_文件名与分页范围(self):
        names = [os.path.basename(p) for p in self.FILES]
        self.assertEqual(names, ["【机翻】%s_001-010.html" % self.short,
                                 "【机翻】%s_011-020.html" % self.short,
                                 "【机翻】%s_021-024.html" % self.short])
        for p in self.FILES:
            self.assertTrue(os.path.exists(p), "没写出来: %s" % p)

    def test_只有html不带编辑服务与留档(self):
        """「仅 HTML 文件」= 一个 .html 走天下：没有 exe/bat/edit_meta/源 txt.gz/data 目录。"""
        entries = os.listdir(self.out_dir)
        for bad in ("AA编辑服务.exe", gen_aa_html.EDIT_PY, gen_aa_html.META_NAME,
                    gen_aa_html.EDIT_BAT, gen_aa_html.EDIT_BAT_LAN):
            self.assertNotIn(bad, entries, "简版交付不该带 %s" % bad)
        self.assertFalse(os.path.isdir(os.path.join(self.out_dir, "data")), "简版不该有 data/ 目录")
        self.assertFalse(os.path.isdir(os.path.join(self.out_dir, "aa")))
        self.assertFalse(os.path.isdir(os.path.join(self.out_dir, "汉化")))

    def test_自包含与页面挂点(self):
        html = self.read(os.path.basename(self.FILES[0]))
        self.assertIn("window.AA_INLINE=1;", html)          # 单档形态：回收远处章不丢数据
        self.assertNotIn('<script src="data/', html)        # 没有任何外置数据脚本
        self.assertIn("var PLAIN = 1;", html)               # 去掉编辑/替换按钮、不探测编辑服务
        self.assertIn("var TRONLY = 0;", html)
        self.assertIn("var OFF = 0;", html)
        self.assertIn('var SELF = "【机翻】%s_001-010.html";' % self.short, html)
        # 数据 + 字体都内嵌
        self.assertIn("data:font/woff2;base64,", html, "AA 字体没内嵌（手机/非 Windows 会掉字体）")
        self.assertEqual(len(_payloads(html)), 10, "第一章内嵌的章节数不对")
        # 手机阅读 + 进度 + 跨文件提示的挂点
        for anchor in ("#tocbtn", "@media (max-width:640px)", "function save()", "function xResume",
                       "if (PLAIN)"):
            self.assertIn(anchor, html, "简版页缺挂点：%s" % anchor)
        # 模板占位符必须全部替换掉
        for ph in ("__KEY__", "__TOTAL__", "__OFFSET__", "__SELF__", "__PLAIN__", "__TRONLY__"):
            self.assertNotIn(ph, html, "占位符没替换：%s" % ph)

    def test_分页链接与偏移(self):
        p1 = self.read(os.path.basename(self.FILES[0]))
        p2 = self.read(os.path.basename(self.FILES[1]))
        p3 = self.read(os.path.basename(self.FILES[2]))
        self.assertIn('href="【机翻】%s_011-020.html"' % self.short, p1)
        self.assertNotIn('href="【机翻】%s_001-010.html"' % self.short, p1)   # 第一部分没有「上一部分」
        self.assertIn('href="【机翻】%s_001-010.html"' % self.short, p2)
        self.assertIn('href="【机翻】%s_021-024.html"' % self.short, p2)
        self.assertNotIn('href="【机翻】%s_021-024.html"' % self.short, p3)   # 最后一部分没有「下一部分」
        self.assertIn("var OFF = 10;", p2)
        self.assertIn("var OFF = 20;", p3)
        for i, (name, rng) in enumerate(zip([f for f in self.FILES], ["001-010", "011-020", "021-024"])):
            self.assertIn("本文件：第 %s 话 / 共 24 话" % rng, self.read(os.path.basename(name)))
        # 目录只列本文件的章（10/10/4）
        for name, n in zip(self.FILES, (10, 10, 4)):
            self.assertEqual(len(re.findall(r'id="toc\d+"', self.read(os.path.basename(name)))), n)

    def test_章序号用整部口径(self):
        """「第 N / 24 章」是**整部**序号（分页里也要看得清读到哪）；第 2 部分首章 = 第 11 章。"""
        p2 = self.read(os.path.basename(self.FILES[1]))
        self.assertIn('<div class="chapter-ind">11 / 24 章</div>', p2)
        self.assertIn('<div class="chapter-ind">20 / 24 章</div>', p2)

    def test_正文与单独渲染逐字节相同(self):
        """分页切片不得改正文：分页文件里的某章 == 单独渲染那一章。"""
        cfg = registry.WORKS["fukou"]
        site = importlib.import_module("sites." + cfg["site"])
        chapters = gen_aa_html.chapter_order(cfg["out_dir"], cfg.get("name_style"), cfg.get("work_dir"))
        # 第 1 部分里的第 1 章（整部第 1 章）与第 2 部分里的第 1 章（整部第 11 章）各验一次
        for part_idx, ch_local in ((0, 0), (1, 0)):
            paid = _payloads(self.read(os.path.basename(self.FILES[part_idx])))
            alone = gen_aa_html._chapter_sections(
                cfg, site, [chapters[part_idx * 10 + ch_local]], cfg.get("html_css", "saki"),
                cfg["aa_dir"])[1][0]
            exp = _payloads(alone)[0]["tr"]
            self.assertEqual(paid[ch_local]["tr"], exp,
                             "第 %d 部分第 %d 章的正文与单独渲染不一致（切片改了正文）"
                             % (part_idx + 1, ch_local + 1))

    def test_页面JS语法(self):
        """内联 JS 必须语法正确（生成物级验证；源码看不出 Python 字符串吃转义的问题）。"""
        if not shutil.which("node"):
            self.skipTest("没有 node")
        for p in self.FILES:
            html = open(p, encoding="utf-8", newline="").read()
            for pat in ('split("\r', 'split("\n', 'join("\r', 'join("\n'):
                self.assertNotIn(pat, html, "交付页 JS 里出现真换行（会 SyntaxError）：%r" % pat)
            for i, blk in enumerate(re.findall(r"<script>(.*?)</script>", html, re.S)):
                f = os.path.join(self.tmp, "plain_%s_%d.js" % (os.path.basename(p)[:6], i))
                open(f, "w", encoding="utf-8", newline="").write(blk)
                r = subprocess.run(["node", "--check", f], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0,
                                 "内联 script 段 %d 语法错误：\n%s" % (i, (r.stderr or "")[:800]))


class TestPlainTrOnly(unittest.TestCase):
    """仅译文：只存 tr（没有原文数据），单文件（不分页）。"""

    @classmethod
    def setUpClass(cls):
        from _work_guard import work_cfg
        cfg = work_cfg("fukou", ("aa_dir", "out_dir"), "仅译文简版用例")
        cls._orig_out = cfg["html_out"]
        cls.tmp = tempfile.mkdtemp(prefix="hermes-test-plain-tr-")
        cls.out_dir = os.path.join(cls.tmp, "交付HTML")
        cfg["html_out"] = os.path.join(cls.out_dir, "不幸の催眠_汉化合集")
        os.makedirs(cls.out_dir, exist_ok=True)
        try:
            sys.argv = ["x", "fukou", "--plain", "--tr-only"]
            cls.files = gen_aa_html.main("fukou")
        finally:
            cfg["html_out"] = cls._orig_out

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_单文件_只带译文_无原文按钮(self):
        self.assertEqual(len(self.files), 1)
        self.assertEqual(os.path.basename(self.files[0]), "【机翻】不幸の催眠_001-024.html")
        html = open(self.files[0], encoding="utf-8", newline="").read()
        self.assertIn("var TRONLY = 1;", html)
        pays = _payloads(html)
        self.assertEqual(len(pays), 24, "应内嵌全部 24 章")
        self.assertTrue(all(d["src"] is None for d in pays.values()), "仅译文不该带原文数据")
        self.assertTrue(all(d["tr"] for d in pays.values()), "仅译文仍必须有正文")
        # 页面侧：运行时删「原文」按钮（脚本紧跟在骨架之后 → 首帧前就没了）
        self.assertIn("querySelector('.modebar button[data-mode=\"src\"]')", html)


class TestPlainPlan(unittest.TestCase):
    """分页计划（纯函数，不生成）。"""

    @classmethod
    def setUpClass(cls):
        from _work_guard import work_cfg
        cls.cfg = work_cfg("fukou", ("out_dir",), "分页计划用例")

    def test_全部与阈值(self):
        ch = list(range(24))
        self.assertEqual([n for n, _, _ in gen_aa_html.plain_plan(self.cfg, ch, 0)],
                         ["【机翻】不幸の催眠_001-024.html"])
        self.assertEqual([n for n, _, _ in gen_aa_html.plain_plan(self.cfg, ch, 200)],
                         ["【机翻】不幸の催眠_001-024.html"])
        self.assertEqual([n for n, _, _ in gen_aa_html.plain_plan(self.cfg, ch, 1)][:2],
                         ["【机翻】不幸の催眠_001-001.html", "【机翻】不幸の催眠_002-002.html"])
        got = gen_aa_html.plain_plan(self.cfg, ch, 7)
        self.assertEqual([(s, e) for _, s, e in got],
                         [(1, 7), (8, 14), (15, 21), (22, 24)])

    def test_短名取交付夹基名(self):
        cfg = dict(self.cfg)
        cfg["html_out"] = r"X:\交付HTML\やる夫のねっとり貞操逆転世界_汉化合集"
        self.assertEqual(gen_aa_html.plain_short_name(cfg), "やる夫のねっとり貞操逆転世界")
        cfg["html_out"] = r"X:\交付HTML\白头与染灰魔女_汉化合集.html"      # 旧单文件作品
        self.assertEqual(gen_aa_html.plain_short_name(cfg), "白头与染灰魔女")


class TestPlainCleanupAndGuard(unittest.TestCase):
    """重跑删陈旧简版文件（只删自己前缀的）+ `--plain --one` 互斥。"""

    def setUp(self):
        from _work_guard import work_cfg
        self.cfg = work_cfg("fukou", ("aa_dir", "out_dir"), "简版清理用例")
        self._orig_out = self.cfg["html_out"]
        self.tmp = tempfile.mkdtemp(prefix="hermes-test-plain-cl-")
        self.out = os.path.join(self.tmp, "交付HTML")
        self.cfg["html_out"] = os.path.join(self.out, "不幸の催眠_汉化合集")
        os.makedirs(self.out, exist_ok=True)

    def tearDown(self):
        self.cfg["html_out"] = self._orig_out
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_重跑清掉上次分页文件(self):
        stale = os.path.join(self.out, "【机翻】不幸の催眠_999-999.html")
        other = os.path.join(self.out, "【机翻】别的作品_001-010.html")
        keep = os.path.join(self.out, "index.html")
        for p in (stale, other, keep):
            open(p, "w", encoding="utf-8").write("x")
        sys.argv = ["x", "fukou", "--plain", "--split", "20"]
        gen_aa_html.main("fukou")
        self.assertFalse(os.path.exists(stale), "同一作品的陈旧简版文件没被清掉")
        self.assertTrue(os.path.exists(other), "别的作品的简版文件被误删")
        self.assertTrue(os.path.exists(keep), "交付 HTML 目录里的其它文件被误删")
        self.assertEqual(sorted(glob.glob(os.path.join(self.out, "【机翻】不幸の催眠_*.html"))),
                         [os.path.join(self.out, "【机翻】不幸の催眠_001-020.html"),
                          os.path.join(self.out, "【机翻】不幸の催眠_021-024.html")])

    def test_plain与one互斥(self):
        sys.argv = ["x", "fukou", "--plain", "--one", "entry_1"]
        with self.assertRaises(SystemExit):
            gen_aa_html.main("fukou")


class TestPlainSizeHint(unittest.TestCase):
    """体量：**只提示不自动分页**（2026-10-11 用户口径）；载入条自述体量 + 看门狗 + boot 兜底。"""

    @classmethod
    def setUpClass(cls):
        from _work_guard import work_cfg
        cls.cfg = work_cfg("fukou", ("aa_dir", "out_dir"), "简版体量用例")
        cls.tmp = tempfile.mkdtemp(prefix="hermes-test-plain-sz-")
        cls._orig_out = cls.cfg["html_out"]
        cls.cfg["html_out"] = os.path.join(cls.tmp, "不幸の催眠_汉化合集")
        os.makedirs(cls.tmp, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.cfg["html_out"] = cls._orig_out
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _gen(self, sub, extra=()):
        out = os.path.join(self.tmp, sub)
        os.makedirs(out, exist_ok=True)
        sys.argv = ["x", "fukou", "--plain", "--tr-only", "--out-dir", out] + list(extra)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            files = gen_aa_html.main("fukou")
        return files, buf.getvalue()

    def test_估算函数(self):
        chapters = gen_aa_html.chapter_order(self.cfg["out_dir"], self.cfg.get("name_style"),
                                             self.cfg.get("work_dir"))
        est = gen_aa_html.plain_data_estimate(self.cfg, chapters)
        txt = sum(os.path.getsize(f) for f in chapters)
        lin = est - gen_aa_html.PLAIN_EST_FIXED          # 去掉固定开销看线性部分
        self.assertGreaterEqual(lin, txt * 0.24, "线性估算低于实测比值 0.24 → 提示会漏报")
        self.assertLessEqual(lin, txt * 0.40, "线性估算高得离谱")
        self.assertTrue(1024 * 1024 <= gen_aa_html.PLAIN_EST_FIXED <= 3 * 1024 * 1024)
        self.assertEqual(gen_aa_html.plain_data_estimate(self.cfg, []), gen_aa_html.PLAIN_EST_FIXED)

    def test_选全部就只有一个文件_再大也不自动分页(self):
        """用户口径：不要自动分页。`--inline-max` 只影响交付夹那条路，简版「全部」恒为单文件。"""
        files, out = self._gen("all", ("--inline-max", "0.6"))
        self.assertEqual(len(files), 1, "简版选「全部」必须出单文件（不自动分页）")
        self.assertEqual(os.path.basename(files[0]), "【机翻】不幸の催眠_001-024.html")

    def test_体量大只打提示(self):
        saved = gen_aa_html.PLAIN_HINT_BYTES
        gen_aa_html.PLAIN_HINT_BYTES = 1024          # 假装阈值 1KB → 必然触发提示
        try:
            files, out = self._gen("hint")
        finally:
            gen_aa_html.PLAIN_HINT_BYTES = saved
        self.assertEqual(len(files), 1, "提示不影响产物")
        self.assertIn("[plain] 提示：单文件内嵌数据估算", out)
        self.assertIn("想更快可改选分页", out)

    def test_载入条自述体量与看门狗(self):
        files, _ = self._gen("wd")
        html = open(files[0], encoding="utf-8", newline="").read()
        self.assertIn("正在载入章节数据…（本文件内嵌约", html)   # 载入条自述体量
        self.assertIn("载入看门狗", html)                       # 卡住时给可操作提示
        self.assertIn("window.__aaReady = 1;", html)            # boot 成功后关掉看门狗
        self.assertIn("function boot0()", html)                 # boot 已包 try/catch
        self.assertIn("载入失败：", html)

    def test_缩放控件与viewport放开缩放下限(self):
        """手机端「大 AA 看不全」：A−/A＋/全览（只乘字号 → 等比缩放，AA 列位不变）。

        另：viewport 必须写 minimum-scale —— 不写它，手机上缩不过「页面铺满」那道下限
        （用户实测：只能放大不能缩小）。
        """
        files, _ = self._gen("zoom")
        html = open(files[0], encoding="utf-8", newline="").read()
        vp = re.search(r'<meta name="viewport" content="([^"]*)"', html).group(1)
        self.assertIn("minimum-scale=", vp)          # 放开缩放下限（能缩到 100% 以下）
        self.assertIn("user-scalable=yes", vp)       # 显式允许（少数浏览器只认显式声明）
        for i in ("zdown", "zup", "zlabel", "zfit"):
            self.assertIn('id="%s"' % i, html)
        self.assertIn("function bindZoom()", html)
        self.assertIn("function zFit()", html)
        self.assertIn('ZKEY = PKEY + ":zoom"', html)   # 倍率随作品存档
        self.assertIn("localStorage.setItem(ZKEY", html)
        self.assertIn(":root{--aa-zoom:1}", html)
        # 字号 / 行高 / 行号列 / 编辑行高 全部按 --aa-zoom 走（桌面 15/17 与手机 11.5/13 两套 base 都要）
        for base in ("calc(15px * var(--aa-zoom,1))", "calc(17px * var(--aa-zoom,1))",
                     "calc(11.5px * var(--aa-zoom,1))", "calc(13px * var(--aa-zoom,1))",
                     "calc(14px * var(--aa-zoom,1))"):
            self.assertIn(base, html, "字号/行高没跟着 --aa-zoom 走：%s" % base)

    def test_内嵌两个字体且AA度量体在Sarasa之前(self):
        """手机端不「错位」的关键：与 PGothic 度量一致的 AA 字体（现 Saitamaar）排在 Sarasa 之前、PGothic 之后。"""
        from aa_font import AA_FAMILY
        files, _ = self._gen("aa")
        html = open(files[0], encoding="utf-8", newline="").read()
        self.assertEqual(html.count("@font-face"), 2, "应内嵌 AA 度量字体 + Sarasa 两个")
        self.assertIn("font-family:'%s'" % AA_FAMILY, html)
        stack = re.search(r"pre\.aa\{font-family:([^;}]*)", html.split("@font-face")[-1]).group(1)
        self.assertIn("'%s', 'AA-SarasaMono'" % AA_FAMILY, stack)      # 顺序不能反
        self.assertTrue(stack.strip().startswith('"ＭＳ Ｐゴシック"'), "PC 仍以 PGothic 为准")
        # 手机端行高比 = 桌面 15/17（AA 纵向连续性靠这个比例）
        # 桌面（fukou 走 legacy CSS）：字号/行高按 --aa-zoom 走（倍率默认 1）
        self.assertIn("font-size:calc(15px * var(--aa-zoom,1));line-height:calc(17px * var(--aa-zoom,1))", html)
        # 手机覆盖（CSS_UI）：同样按 --aa-zoom 走
        self.assertIn("font-size:calc(11.5px * var(--aa-zoom,1));line-height:calc(13px * var(--aa-zoom,1))", html)
        self.assertNotIn("line-height:14px", html, "手机行高比必须跟桌面一致，旧值 14px 会让画上下错位")


class TestMakeHtmlRoute(unittest.TestCase):
    """工作台路由：参数映射到生成器开关（不真跑生成器）。"""

    @classmethod
    def setUpClass(cls):
        from _work_guard import work_cfg
        cls.cfg = work_cfg("fukou", ("out_dir",), "make-html 路由用例")

    def _call(self, **kw):
        from aa_web_app.routers import deliver as M    # noqa: E402  run_generator 的调用方（api_make_html 在交付域）   # noqa: E402  run_generator 的调用方
        seen = {}
        orig = M.run_generator

        def fake(work, extra=None, plain=False):
            seen["work"] = work
            seen["extra"] = list(extra or [])
            seen["plain"] = plain
            return 0, "ok", ""
        M.run_generator = fake
        try:
            return M.api_make_html("fukou", M.MakeHtmlBody(**kw)), seen
        finally:
            M.run_generator = orig

    def test_默认参数(self):
        res, seen = self._call()
        self.assertTrue(res["ok"])
        self.assertTrue(seen["plain"], "必须走统一生成器的 --plain 分支")
        self.assertEqual(seen["extra"], ["--plain", "--split", "0"])
        self.assertIn("dir", res)
        self.assertIsInstance(res["files"], list)

    def test_仅译文与分页和输出目录(self):
        res, seen = self._call(tr_only=True, split=100, out_dir=r"X:\tmp\out")
        self.assertEqual(seen["extra"], ["--plain", "--split", "100", "--tr-only", "--out-dir", r"X:\tmp\out"])
        self.assertTrue(res["ok"])
        res2, seen2 = self._call(split=-5)                 # 负数归 0（全部一个文件）
        self.assertEqual(seen2["extra"], ["--plain", "--split", "0"])

    def test_自动分页提示回传给前端(self):
        """生成器打了 `[plain] …自动分页…` → 路由要在 note 里带回（前端显示「已自动分页」）。"""
        from aa_web_app.routers import deliver as M    # noqa: E402  run_generator 的调用方（api_make_html 在交付域）   # noqa: E402  run_generator 的调用方
        orig = M.run_generator

        def fake(work, extra=None, plain=False):
            return 0, "chapters: 71\n[plain] 内嵌数据估算 27.3 MB 超过单文件上限 20 MB → **自动分页**\n[plain] 3 个自包含 HTML", ""
        M.run_generator = fake
        try:
            res = M.api_make_html("fukou", M.MakeHtmlBody())
        finally:
            M.run_generator = orig
        self.assertTrue(res["note"].startswith("[plain] 内嵌数据估算"))
        self.assertIn("自动分页", res["note"])


if __name__ == "__main__":
    unittest.main()
