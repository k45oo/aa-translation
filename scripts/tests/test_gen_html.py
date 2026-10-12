# -*- coding: utf-8 -*-
"""交付文件夹回归测试（2026-10-06 起交付物 = 文件夹，不是单个 HTML 文件）。

结构：`aaManga/交付HTML/<名>_汉化合集/{index.html, aa/*.txt.gz, 汉化/*.txt.gz}`，章节数据两种形态
（2026-10-08 起按数据体量自动选，见 gen_aa_html.INLINE_MAX_BYTES）：
  · 单档 inline   → 全部章节数据内嵌在 index.html 的 `<script>` 里（data/ 不存在）
  · 外置 external → data/NNNNN.js 一章一文件（index.html 只放骨架，懒加载）
两种形态的正文必须逐字节相同（TestLayoutSwitch 断言）。

黄金样本（2026-10-07 重建）= `fixtures/delivery_golden.json` 里的**哈希清单**：
每章 `<pre class="aa">` 正文（剥 sfx 包装后）的 sha1[:16] + 行数，覆盖 4 部作品 46 章。
★ 旧单文件交付物（`aaManga/交付HTML/<名>_汉化合集.html`）已被 ae6dc930 移出 git；且它的内容也落后于
  后续人工修订（如「先生→桑」+ 宽度补位）→ 不能再当逐字节基准，只作为重建时的参照记录
  （见清单里的 `vs_legacy`）。基线口径要变时：先确认变更，再跑
  `python scripts/tools/freeze_delivery_golden.py` 重建，别改生成器去迁就旧样本。

历史：2026-08-18 saki_fc2 快照频繁过期 → 曾改为存在性记录（STALE_SNAPSHOTS），
      该写法随文件退役一并清掉，现只留 `RETIRED_SNAPSHOTS` 文字档案。
"""
import base64
import glob
import gzip
import hashlib
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

import registry
import gen_aa_html

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根

# 黄金样本（2026-10-07 重建）：旧单文件交付物（`aaManga/交付HTML/<名>_汉化合集.html`）自 ae6dc930 起
# 移出 git 跟踪 → 直接拿它比对的旧快照测试整体失效（文件不在 → skipped / 断言失败）。
# 改为**哈希清单**：`fixtures/delivery_golden.json` 存每章 `<pre class="aa">` 正文（剥 sfx 后）的
# sha1[:16] + 行数；基线 = 冻结当时的渲染，重建/更新用
# `python scripts/tools/freeze_delivery_golden.py`（渲染口径真要变时先确认变更再重建）。
GOLDEN_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "delivery_golden.json")

# 已退役、只留档不再断言的旧快照（别再写会失败的 existence 断言）
RETIRED_SNAPSHOTS = [
    ("fukou", "aaManga/交付HTML/不幸の催眠_汉化合集.html",
     "ae6dc930 移出 git；内容也已人工修订（「先生→桑」+宽度补位）→ 基线改哈希清单，见 GOLDEN_JSON"),
    ("saki_fc2", "aaManga/交付HTML/さーきー_FC2_试读合集.html",
     "ae6dc930 移出 git 且未留副本；作品本身已并入 さーきー 相关交付"),
]

PRE_RE = re.compile(r'<pre class="aa">(.*?)</pre>', re.S)
# 2026-10-06 起交付页渲染 sfx 特效（<span class="sfx">…</span>，楷体斜体）→ 与冻结样本
# 比对前先剥掉这层包装（sfx span 不嵌套、内部只有转义文本，正则足够精确）。
SFX_SPAN_RE = re.compile(r'<span class="sfx">([^<]*)</span>')


def _desfx(html):
    """去掉 sfx 样式包装 → 与冻结样本同口径（其余样式差异仍会被断言抓住）。"""
    return SFX_SPAN_RE.sub(r"\1", html)


def _sha1(s):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def _payload(path):
    """读 data/*.js → {"tr": b64, "src": b64}"""
    s = open(path, encoding="utf-8").read()
    return json.loads(s[s.index("{"):s.rindex("}") + 1])


_PAY_RE = re.compile(r'window\.AA_DATA\[(\d+)\]=(\{[^\n]*?\});')
PATCH_SEP = chr(2)          # v2 差异行「行号 <STX> 内容」（与 gen_aa_html.PATCH_SEP 同口径）


def _decode_payload(d):
    """章节数据 dict → **已解压**的 {tr, src}。

    两种格式都吃：v1 `{tr,src}` 两份各自 gzip+b64；v2 `{src,p,v}` = src 全量 + tr 相对
    src 的差异行（AA 里艺术行/帖头/空行不翻译 → 九成行两份一样）。测试拿解压后的口径
    比正文，所以两种格式的基线断言完全共用。
    """
    src = _unz(d["src"])
    if d.get("p") is None:
        return {"tr": _unz(d["tr"]), "src": src}
    rows = src.split("\n")
    for step in _unz(d["p"]).split("\n"):
        if not step:
            continue
        k, _, txt = step.partition(PATCH_SEP)
        rows[int(k)] = txt
    return {"tr": "\n".join(rows), "src": src}


def _layout_of(folder):
    """交付夹的章节数据形态（edit_meta.json 的 layout；旧交付物按 data/ 目录推断）。"""
    try:
        m = json.load(open(os.path.join(folder, "edit_meta.json"), encoding="utf-8"))
    except OSError:
        m = {}
    return m.get("layout") or ("external" if os.path.isdir(os.path.join(folder, "data")) else "inline")


def _payloads(folder):
    """交付夹全部章节数据（**已解压**的 {tr,src}）。

    两种存放形态（external = data/*.js；inline = index.html 内嵌段）与两种数据格式
    （v1/v2）都在这里归一化 → 下游断言只见 {tr, src} 字符串。
    """
    if _layout_of(folder) == "external":
        d = os.path.join(folder, "data")
        raws = [_payload(os.path.join(d, f)) for f in sorted(os.listdir(d))]
    else:
        html = open(os.path.join(folder, "index.html"), encoding="utf-8", newline="").read()
        got = {int(n): json.loads(p) for n, p in _PAY_RE.findall(html)}
        raws = [got[k] for k in sorted(got)]
    return [_decode_payload(x) for x in raws]


def _unz(b64):
    return gzip.decompress(base64.b64decode(b64)).decode("utf-8")


def _gzip_text(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [l.rstrip("\r") for l in f.read().split("\n")]


class TestDeliveryFolder(unittest.TestCase):
    """交付文件夹（gen_aa_html 生成）：结构 / 懒加载 / 源留档 / 页面 JS / 正文基线。

    fukou 那份在 setUpClass 里只生成一次（本类各用例共用，省掉三遍生成）。
    """

    SHARED = None

    @classmethod
    def setUpClass(cls):
        from _work_guard import work_cfg
        cfg = work_cfg("fukou", ("aa_dir", "out_dir"), "交付合集生成用例")
        cls._orig_out = cfg["html_out"]
        cls._tmp = tempfile.mkdtemp(prefix="hermes-test-html-")
        cfg["html_out"] = os.path.join(cls._tmp, "out")      # 交付文件夹（不带 .html）
        try:
            sys.argv = ["x", "fukou"]
            gen_aa_html.main("fukou")
            cls.SHARED = cfg["html_out"]
        finally:
            cfg["html_out"] = cls._orig_out

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_structure_and_page_hooks(self):
        """交付文件夹结构、译/原切换与懒加载挂点。"""
        entry = os.path.join(self.SHARED, "index.html")
        self.assertTrue(os.path.exists(entry), "交付文件夹缺 index.html")
        html = open(entry, encoding="utf-8").read()
        # 1. 结构与两个新功能的挂点
        self.assertIn('class="modebar"', html)
        self.assertIn('data-mode="tr"', html)
        self.assertIn('data-mode="src"', html)
        self.assertIn("localStorage", html)
        self.assertIn("DecompressionStream", html)
        # 缩放控件（与简版共用同一份页面模板：译文/原文…之后是 A−/100%/A＋/全览）
        self.assertIn('id="zdown"', html)
        self.assertIn('id="zfit"', html)
        self.assertIn("--aa-zoom", html)
        self.assertIn("minimum-scale=", html)        # 手机上能缩到 100% 以下（放开下限）
        # 2. 章节数据按形态放置；**懒加载**：index.html 不得用 <script src> 预载任何章节数据
        layout = _layout_of(self.SHARED)
        datas = sorted(glob.glob(os.path.join(self.SHARED, "data", "*.js")))
        if layout == "external":
            self.assertTrue(datas, "external 形态缺 data/*.js")
            self.assertNotIn("window.AA_INLINE=1", html, "外置形态不该设 AA_INLINE")
        else:
            self.assertEqual(layout, "inline")
            self.assertFalse(datas, "单档形态不该再有 data/*.js（数据内嵌在 index.html）")
            self.assertIn("window.AA_INLINE=1;", html, "单档形态必须标记 AA_INLINE（drop 分支要用）")
            self.assertEqual(len(_payloads(self.SHARED)), len(gen_aa_html.chapter_order(
                registry.WORKS["fukou"]["out_dir"], registry.WORKS["fukou"].get("name_style"),
                registry.WORKS["fukou"].get("work_dir"))), "单档内嵌的章节数与章节目录不一致")
        self.assertEqual(html.count('<script src="data/'), 0,
                         "章节数据被预载进 index.html（章节多时会卡首屏）")
        self.assertIn('"data/" + pad(n) + ".js"', html)
        # 回收远处章节：单档形态没有可重读的 data/*.js → 不能丢 b64（丢了回访就白屏）
        self.assertIn("if (!window.AA_INLINE && window.AA_DATA)", html)
        # 懒加载的两个易错点（防回归）：
        # ① data/*.js 会新建 window.AA_DATA → 启动时捕获引用就永远是空数组
        self.assertNotIn("var DATA = window.AA_DATA", html)
        self.assertIn("function dget(n)", html)
        # ② 骨架里第 0 章默认可见 → 首跳必须显式关掉其余章（否则恢复章节时两章同显）
        self.assertIn("hideAll(n)", html)
        # 3. sfx 特效落地（2026-10-06：交付页也渲染 sfx，不再只有工作台有）
        n_sfx = sum(len(SFX_SPAN_RE.findall(d["tr"])) for d in _payloads(self.SHARED))
        self.assertGreater(n_sfx, 0, "交付页未渲染任何 sfx 特效（应至少命中一处半角假名）")

    def test_lan_read_assets(self):
        """手机阅读（--lan）：交付夹带「手机阅读(局域网).bat」；页面按打开方式给准确指引。"""
        bat = os.path.join(self.SHARED, gen_aa_html.EDIT_BAT_LAN)
        self.assertTrue(os.path.exists(bat), "缺 %s" % gen_aa_html.EDIT_BAT_LAN)
        t = open(bat, "rb").read().decode("gbk")           # GBK + CRLF（cmd 里中文不乱码）
        self.assertIn("--lan", t, "局域网 bat 没带 --lan")
        self.assertIn("\r\n", t)
        self.assertTrue(os.path.exists(os.path.join(self.SHARED, gen_aa_html.EDIT_BAT)))
        html = open(os.path.join(self.SHARED, "index.html"), encoding="utf-8").read()
        for anchor in ("function loadErr", "function showLoadErr", "loaderr on",
                       '"content:"', '"file:"', gen_aa_html.EDIT_BAT_LAN,
                       "手机阅读(局域网)"):
            self.assertIn(anchor, html, "交付页缺手机阅读/载入失败指引挂点：%s" % anchor)
        # 指引面板在手机上必须是可见例外（手机段把 #edithint 整段 display:none 掉了）
        self.assertIn("#edithint.loaderr{display:block!important", html)

    def test_tr_matches_golden_hashes(self):
        """正文基线：每章 `<pre class="aa">` 正文（剥 sfx 后）sha1 + 行数须与冻结清单一致。

        清单 `fixtures/delivery_golden.json`（2026-10-07 建，缘由见文件顶部注释）；渲染口径
        真要变时先确认变更，再跑 scripts/tools/freeze_delivery_golden.py 重建基线。
        """
        if not os.path.exists(GOLDEN_JSON):
            self.skipTest("缺交付页黄金样本清单: fixtures/delivery_golden.json")
        gold = json.load(open(GOLDEN_JSON, encoding="utf-8"))
        self.assertTrue(gold["works"], "黄金样本清单为空")
        for work, exp in sorted(gold["works"].items()):
            cfg = registry.WORKS[work]
            orig_out = cfg["html_out"]
            tmp = tempfile.mkdtemp(prefix="hermes-test-gold-")
            try:
                if work == "fukou":                            # 本类已生成过 → 复用
                    folder = self.SHARED
                else:
                    cfg["html_out"] = os.path.join(tmp, "out")
                    sys.argv = ["x", work, "--no-source"]
                    gen_aa_html.main(work)
                    folder = cfg["html_out"]
                pays = _payloads(folder)
                names = json.load(open(os.path.join(folder, "edit_meta.json"),
                                       encoding="utf-8"))["chapters"]
                self.assertEqual(len(pays), exp["chapters"],
                                 f"{work} 章节清单变了（基线 {exp['chapters']} 章）")
                for i, (d, h, n) in enumerate(zip(pays, exp["tr_sha1"], exp["tr_lines"])):
                    tr, src = _desfx(d["tr"]), d["src"]
                    self.assertEqual(tr.count("\n") + 1, n,
                                     f"{work} 第 {i+1} 章行数与基线不一致")
                    self.assertEqual(_sha1(tr), h,
                                     f"{work} 第 {i+1} 章（{names[i]}）正文与基线不一致 →"
                                     f" 渲染口径/源文本变了？确认后跑"
                                     f" scripts/tools/freeze_delivery_golden.py 重建基线")
                    # 原文与译文行数一致（译/原切换时行位置必须对齐）
                    self.assertEqual(src.count("\n"), tr.count("\n"),
                                     f"{work} 第 {i+1} 章原文/译文行数不一致")
            finally:
                cfg["html_out"] = orig_out
                shutil.rmtree(tmp, ignore_errors=True)

    def test_retired_snapshots_documented(self):
        """退役的旧快照只留档、不断言存在（别再留会失败的存在性断言）。"""
        self.assertTrue(RETIRED_SNAPSHOTS, "退役快照清单被清空了？至少应留 fukou 一条改为哈希基线的记录")
        for work, name, why in RETIRED_SNAPSHOTS:
            self.assertTrue(why.strip(), f"{work} 退役原因不能为空")
            print(f"[retired] {work} / {name}: {why}")

    def test_source_copies_written(self):
        """默认带 aa/ 与 汉化/ 的 .txt.gz 源留档（内容可解回原文行）。"""
        folder = self.SHARED
        from _work_guard import work_cfg
        cfg = work_cfg("fukou", ("out_dir",), "源留档用例")
        tr_gz = sorted(glob.glob(os.path.join(folder, "汉化", "*_汉化.txt.gz")))
        self.assertTrue(tr_gz, "缺 汉化/*.txt.gz 源留档")
        self.assertTrue(glob.glob(os.path.join(folder, "aa", "*.txt.gz")), "缺 aa/ 原文压缩源")
        self.assertEqual(len(tr_gz), len(_payloads(folder)))
        base = os.path.basename(tr_gz[0])[:-len("_汉化.txt.gz")]
        self.assertEqual(_gzip_text(tr_gz[0]),
                         gen_aa_html._read_lines(os.path.join(cfg["out_dir"], base + "_汉化.txt")))

    def test_page_js_syntax(self):
        """交付页内联 JS 必须语法正确。

        坑（2026-10-07 实测踩到）：页面 JS 写在 gen_aa_html 的 Python 三引号（非 raw）字符串里，
        JS 里写反斜杠+n 会被 Python 先解码成**真换行** → JS 字符串断行，整页脚本 SyntaxError，
        编辑/替换/行号列全挂，而 index.html 本身还是「生成成功」的。所以必须验**生成物**，
        验源码是验不出来的。需要换行的地方统一用页面里的 `NL`（String.fromCharCode(10)）。
        """
        html = open(os.path.join(self.SHARED, "index.html"),
                    encoding="utf-8", newline="").read()
        for pat in ('split("\r', 'split("\n', 'join("\r', 'join("\n'):
            self.assertNotIn(pat, html, f"交付页 JS 里出现真换行（会 SyntaxError）：{pat!r}")
        self.assertIn("var NL = String.fromCharCode(10)", html)
        if shutil.which("node"):
            for i, blk in enumerate(re.findall(r"<script>(.*?)</script>", html, re.S)):
                f = os.path.join(self._tmp, "page_%d.js" % i)
                open(f, "w", encoding="utf-8", newline="").write(blk)
                r = subprocess.run(["node", "--check", f], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0,
                                 f"内联 script 段 {i} 语法错误：\n" + (r.stderr or "")[:800])
        # 编辑模式挂点（行号列 + 行内编辑行 + 只读态按钮）
        for anchor in ('class="aa-wrap"', 'class="aa-gut"', "function spanRows", "function paintGut",
                       "function openEdit", "function editBtns", 'data-tip=',
                       ".aa-gut b[data-line]", "body.editing"):
            self.assertIn(anchor, html, f"交付页缺编辑挂点：{anchor}")


class TestPayloadFormat(unittest.TestCase):
    """章节数据格式 v1/v2（2026-10-08 压缩）：怎么选、还原是否逐字节一致。

    v2 = src 全量 + tr 相对 src 的**差异行**（AA 里艺术行/帖头/空行不翻译 → 抽 8 章实测
    九成行两份逐字节相同，v1 等于把同一内容存两遍，810 章那部 data 168MB）。这里只测
    「生成器选格式」与「解码还原」；真实正文口径由 delivery_golden 哈希基线覆盖（它走
    同一份 `_decode_payload`）。
    """

    def _pay(self, tr, src, **kw):
        p = gen_aa_html.chapter_payload(0, tr, src, **kw)
        return p, json.loads(p[p.index("{"):p.rindex("}") + 1])

    def test_v2_when_rows_mostly_identical(self):
        """九成行相同（真实 AA 的样子）→ 走 v2，还原后 tr/src 逐字节等于原值。"""
        art = "　｜　（＾ω＾）　｜　" * 6
        src = [art] * 40
        tr = list(src)
        tr[7] = "改过的一行" + "译" * 20
        p, d = self._pay(tr, src)
        self.assertIsNotNone(d.get("p"), "差异行格式没被选用：%s" % p[:120])
        got = _decode_payload(d)
        self.assertEqual(got["tr"], "\n".join(tr))
        self.assertEqual(got["src"], "\n".join(src))

    def test_all_rows_differ_still_roundtrips(self):
        """每行都不同（差异行不划算）→ 无论选哪种格式，正文都必须还原一致。"""
        src = ["原文%d%s" % (i, "あ" * 40) for i in range(20)]
        tr = ["译文%d%s" % (i, "中" * 40) for i in range(20)]
        _p, d = self._pay(tr, src)
        self.assertEqual(_decode_payload(d)["tr"], "\n".join(tr))

    def test_row_count_mismatch_falls_back_v1(self):
        """行数不一致（两种渲染不可按行对齐）→ 只能 v1，且不能崩。"""
        _p, d = self._pay(["a", "b", "c"], ["a", "b"])
        self.assertIsNone(d.get("p"))
        self.assertIn("tr", d)
        self.assertEqual(_decode_payload(d)["src"], "a\nb")

    def test_src_b64_reused_untouched(self):
        """传 src_b64 时（编辑服务写回）必须沿用它，不重压原文。"""
        b64 = gen_aa_html._gz_b64("a\nb")
        _p, d = self._pay(["x", "b"], ["a", "b"], src_b64=b64)
        self.assertEqual(d["src"], b64)

    def test_tr_patch_lists_only_changed_rows(self):
        sep = gen_aa_html.PATCH_SEP
        self.assertEqual(gen_aa_html.tr_patch(["a", "B", "c"], ["a", "b", "c"]), "1" + sep + "B")
        self.assertEqual(gen_aa_html.tr_patch(["a"], ["a"]), "")
        self.assertIsNone(gen_aa_html.tr_patch(["a"], ["a", "b"]))


class TestLayoutSwitch(unittest.TestCase):
    """单档 / 外置 自动分档（2026-10-08）：阈值、强制开关、两种形态内容一致。

    fukou = 24 章、数据 3.7MB → 默认走**单档**（≤20MB 内嵌进 index.html，双击/直发都能看）；
    `--external` / 小阈值强制外置。分档只改「数据放哪」，正文必须逐字节相同。
    """

    def _gen(self, *extra):
        from _work_guard import work_cfg
        cfg = work_cfg("fukou", ("aa_dir", "out_dir"), "交付形态用例")
        orig = cfg["html_out"]
        tmp = tempfile.mkdtemp(prefix="hermes-test-layout-")
        self.addCleanup(shutil.rmtree, tmp, True)
        cfg["html_out"] = os.path.join(tmp, "out")
        try:
            sys.argv = ["x", "fukou", "--no-source"] + list(extra)
            gen_aa_html.main("fukou")
            return cfg["html_out"]
        finally:
            cfg["html_out"] = orig

    def test_layout_pick_and_content_identity(self):
        """默认（小书）→ 单档；--external → 外置；两种形态的章节数据逐字节相同。"""
        small = self._gen()
        self.assertEqual(_layout_of(small), "inline", "3.7MB 数据 < 20MB 阈值 → 应走单档")
        self.assertFalse(os.path.isdir(os.path.join(small, "data")), "单档形态不该留 data/ 目录")
        html_small = open(os.path.join(small, "index.html"), encoding="utf-8").read()
        self.assertIn("window.AA_INLINE=1;", html_small, "单档必须设 AA_INLINE（drop 分支依赖它）")
        # index.html 里也不许出现 <script src> 拉章节数据（单档是内嵌）
        self.assertEqual(html_small.count('<script src="data/'), 0)

        ext = self._gen("--external", )
        self.assertEqual(_layout_of(ext), "external", "--external 必须外置")
        self.assertEqual(len(glob.glob(os.path.join(ext, "data", "*.js"))), 24, "外置应一章一文件")
        html_ext = open(os.path.join(ext, "index.html"), encoding="utf-8").read()
        self.assertNotIn("window.AA_INLINE=1", html_ext, "外置形态不该设 AA_INLINE")
        self.assertEqual(json.load(open(os.path.join(ext, "edit_meta.json"),
                                        encoding="utf-8"))["layout"], "external")
        # 关键等价性：分档只改存放形态，不改内容
        self.assertEqual(_payloads(small), _payloads(ext), "两种形态的章节数据必须逐字节相同")

    def test_inline_max_threshold(self):
        """--inline-max 0.001（约 1KB）→ 阈值失效，自动判定也走外置；值不能被当成作品 id。"""
        f = self._gen("--inline-max", "0.001")
        self.assertEqual(_layout_of(f), "external")
        self.assertTrue(glob.glob(os.path.join(f, "data", "*.js")),
                        "--inline-max 太小 → 应自动外置")


class TestHelpers(unittest.TestCase):
    def test_deliver_dir_entry_both_formats(self):
        folder = {"html_out": r"C:\x\交付HTML\A_汉化合集"}
        legacy = {"html_out": r"C:\x\交付HTML\A_汉化合集.html"}
        self.assertEqual(gen_aa_html.deliver_dir(folder), r"C:\x\交付HTML\A_汉化合集")
        self.assertTrue(gen_aa_html.deliver_entry(folder).endswith("index.html"))
        self.assertTrue(gen_aa_html.deliver_is_folder(folder))
        self.assertEqual(gen_aa_html.deliver_entry(legacy), legacy["html_out"])
        self.assertFalse(gen_aa_html.deliver_is_folder(legacy))

    def test_read_lines_transparent_gz_and_chapter_order(self):
        """_read_lines / chapter_order 必须透明吃 .txt.gz（压缩包后仍能出合集）。"""
        tmp = tempfile.mkdtemp(prefix="hermes-test-gz-")
        try:
            aa = os.path.join(tmp, "aa")
            tr = os.path.join(tmp, "汉化")
            os.makedirs(aa)
            os.makedirs(tr)
            body = ["# 标题", "1 ： 作者 ： 2020/01/01 ID:abc", "台词だよ"]
            for n in ("entry_11", "entry_22"):
                with gzip.open(os.path.join(tr, n + "_汉化.txt.gz"), "wt", encoding="utf-8") as f:
                    f.write("\n".join(body))
            # 同名 .txt 与 .txt.gz 并存 → 只算一章（不重复）
            with open(os.path.join(tr, "entry_11_汉化.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(body))
            got = gen_aa_html.chapter_order(tr)
            self.assertEqual([os.path.basename(x) for x in got],
                             ["entry_11_汉化.txt", "entry_22_汉化.txt.gz"])
            self.assertEqual(gen_aa_html._read_lines(got[1]), body)
            self.assertEqual(gen_aa_html._base_of(got[1]), "entry_22")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_blank_plan_matches_legacy_rendering(self):
        """楼间间距计划 = 旧内联实现：帖头前补足 ≥2 空行；已够 2 空行则不补。"""
        lines = ["# t", "1 ： a ： d", "あ", "2 ： a ： d", "い", "", "3 ： a ： d"]
        plan = gen_aa_html._blank_plan(lines)
        self.assertEqual(plan, {3: 2, 6: 1})    # 第2个帖头前 0 空行→补2；第3个前 1 空行→补1
        # 帖头前已有 ≥2 空行 → 一个都不补（幂等）
        enough = ["1 ： a ： d", "あ", "", "", "2 ： a ： d"]
        self.assertEqual(gen_aa_html._blank_plan(enough), {})
        # '#' 行不计入空行游程，也不参与计划
        self.assertEqual(gen_aa_html._blank_plan(["# t", "1 ： a ： d"]), {})


if __name__ == "__main__":
    unittest.main()
