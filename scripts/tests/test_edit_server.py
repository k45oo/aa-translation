# -*- coding: utf-8 -*-
"""交付页编辑服务：手机阅读（--lan）相关逻辑。

两层：
  · 纯函数：`parse_args` / `_lan_ips` / `_is_local_ip` / `write_allowed`（决定远端能不能写）
  · 真起 HTTP 服务跑一遍：`--lan` 时**远端只读**（POST 403）、本机可写、ping 里的
    `remote` / `can_edit` 与之一致。用 FakeEdit 替掉真实交付夹（不生成交付物，秒级）。
    远端客户端怎么模拟：连 127.0.0.2（回环段里非 127.0.0.1 的地址）→ 对服务端来说就是别的机器。
"""
import base64
import gzip
import http.client
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SCRIPTS, "tools"))
sys.path.insert(0, os.path.join(SCRIPTS, "common"))
sys.path.insert(0, os.path.join(SCRIPTS, "works"))

import edit_server as ES       # noqa: E402


class TestLanLogic(unittest.TestCase):
    def test_parse_args(self):
        o = ES.parse_args(["C:/x/交付夹", "--lan", "--no-browser", "--port", "9123"])
        self.assertEqual(o, {"root": "C:/x/交付夹", "port": 9123,
                             "open_browser": False, "lan": True})
        # 默认：绑 127.0.0.1（不开局域网）
        self.assertEqual(ES.parse_args([]), {"root": None, "port": ES.DEFAULT_PORT,
                                            "open_browser": True, "lan": False})
        self.assertTrue(ES.parse_args(["--lan-read"])["lan"])
        self.assertEqual(ES.parse_args(["--port=9999"])["port"], 9999)

    def test_lan_ips_no_loopback(self):
        ips = ES._lan_ips()
        self.assertIsInstance(ips, list)
        for ip in ips:
            self.assertFalse(ip.startswith("127."), "局域网地址里不该有回环: %s" % ip)
            self.assertNotIn(ip, ("0.0.0.0", "::1"))
            self.assertEqual(len(ip.split(".")), 4, "只收 IPv4: %s" % ip)

    def test_is_local_ip(self):
        self.assertTrue(ES._is_local_ip("127.0.0.1", ()))
        self.assertTrue(ES._is_local_ip("::1", ()))
        self.assertTrue(ES._is_local_ip("192.168.1.10", ["192.168.1.10"]))
        self.assertFalse(ES._is_local_ip("192.168.1.77", ["192.168.1.10"]))
        self.assertFalse(ES._is_local_ip("127.0.0.2", ["192.168.1.10"]))

    def test_write_lan_txt(self):
        """交付夹里那份「手机打开地址.txt」：第一个地址标「优先用」，写明手机只读。"""
        tmp = tempfile.mkdtemp(prefix="lan-txt-")
        try:
            t = ES.write_lan_txt(tmp, "http://127.0.0.1:8770/index.html",
                                 ["http://192.168.1.10:8770/index.html",
                                  "http://172.24.32.1:8770/index.html"])
            self.assertIn("192.168.1.10:8770/index.html", t)
            self.assertIn("← 优先用这个", t)
            self.assertIn("虚拟网卡", t)
            self.assertIn("手机上只读", t)
            self.assertIn("127.0.0.1:8770/index.html", t)
            on_disk = open(os.path.join(tmp, ES.LAN_TXT), encoding="utf-8").read()
            self.assertEqual(on_disk, t, "写进交付夹的内容应与返回值一致")
            # 只有一个地址时不标「优先」也不提虚拟网卡
            t2 = ES.write_lan_txt(tmp, "http://127.0.0.1:8770/index.html", [])
            self.assertNotIn("优先用这个", t2)
            self.assertNotIn("虚拟网卡", t2)
            self.assertIn("127.0.0.1:8770/index.html", t2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_write_allowed(self):
        # 本机模式：只绑 127.0.0.1，除了本机谁也连不上 → 不需要额外拦
        self.assertTrue(ES.write_allowed(False, "192.168.1.77"))
        # --lan：手机（别的地址）只读，PC 用回环或自己的网卡地址可写
        self.assertFalse(ES.write_allowed(True, "192.168.1.77", ["192.168.1.10"]))
        self.assertTrue(ES.write_allowed(True, "192.168.1.10", ["192.168.1.10"]))
        self.assertTrue(ES.write_allowed(True, "127.0.0.1", ["192.168.1.10"]))


class _FakeEdit:
    """替掉真实交付夹：只提供 ping/save 会碰到的那点接口。"""
    def __init__(self):
        self.meta = {"work": "fake"}
        self.title = "假作品"
        self.chapters = ["a", "b"]
        self.readonly = False
        self.stale = False
        self.root = os.getcwd()
        self.writes = []

    def version(self):
        return "test"

    def save_line(self, ch, idx, text):
        self.writes.append((ch, idx, text))
        return {"ok": True, "padded": False}


class TestLanHTTP(unittest.TestCase):
    """真起服务（绑 0.0.0.0，端口由系统分配）→ 回环=本机可写，源地址 127.0.0.2=远端只读。

    远端客户端怎么造：客户端 socket 绑 127.0.0.2 作**源地址**（`source_address=`）→
    服务端看到的 client_address 就不是 127.0.0.1，等价于「同一 Wi-Fi 的另一台机器」。
    """

    @classmethod
    def setUpClass(cls):
        cls.ed = _FakeEdit()
        try:
            cls.httpd = ES._Srv(("0.0.0.0", 0), ES.Handler)
        except OSError as e:
            raise unittest.SkipTest("起不了测试服务: %s" % e)
        cls.port = cls.httpd.server_address[1]
        cls.httpd.edit = cls.ed
        cls.httpd.lan = True
        cls.httpd.local_ips = []            # 谁都不算本机网卡地址（回环仍算本机）
        cls.th = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.th.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _req(self, method, path, obj=None, src=None):
        """src=None → 回环（本机）；src='127.0.0.2' → 远端（手机）。"""
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5,
                                       source_address=(src, 0) if src else None)
        body = json.dumps(obj).encode("utf-8") if obj is not None else None
        c.request(method, path, body=body,
                  headers={"Content-Type": "application/json"} if body else {})
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r.status, (json.loads(raw.decode("utf-8")) if raw else {})

    def test_remote_branch_reachable(self):
        """源地址 127.0.0.2 可连（连不上则跳过后面的远端断言）。"""
        try:
            self._req("GET", "/api/ping", src="127.0.0.2")
        except OSError as e:
            self.skipTest("本机不允许以 127.0.0.2 为源连接（%s）" % e)

    def test_ping_flags(self):
        s, j = self._req("GET", "/api/ping")
        self.assertEqual(s, 200)
        self.assertTrue(j["lan"])
        self.assertFalse(j["remote"], "回环客户端应算本机")
        self.assertTrue(j["can_edit"])
        try:
            s2, j2 = self._req("GET", "/api/ping", src="127.0.0.2")
        except OSError as e:
            self.skipTest("本机不允许以 127.0.0.2 为源连接（%s）" % e)
        self.assertEqual(s2, 200)
        self.assertTrue(j2["remote"], "远端（手机）必须标 remote")
        self.assertFalse(j2["can_edit"], "远端（手机）必须只读")

    def test_local_can_write(self):
        s, j = self._req("POST", "/api/save", {"ch": 0, "idx": 1, "text": "x"})
        self.assertEqual(s, 200)
        self.assertTrue(j.get("ok"))
        self.assertEqual(self.ed.writes[-1], (0, 1, "x"))

    def test_remote_write_blocked_403(self):
        before = len(self.ed.writes)
        try:
            s, j = self._req("POST", "/api/save", {"ch": 0, "idx": 1, "text": "改"}, src="127.0.0.2")
        except OSError as e:
            self.skipTest("本机不允许以 127.0.0.2 为源连接（%s）" % e)
        self.assertEqual(s, 403, "远端写必须 403，实际 %s" % s)
        self.assertFalse(j.get("ok"))
        self.assertTrue(j.get("remote"))
        self.assertEqual(len(self.ed.writes), before, "被拦的写不该落到交付物")
        s2, j2 = self._req("POST", "/api/replace", {"pairs": [["a", "b"]], "ch": 0}, src="127.0.0.2")
        self.assertEqual(s2, 403)

    def test_static_served_to_remote(self):
        """手机要能拿到页面本身（静态文件照常服务，只是写接口被拦）。"""
        try:
            s, _ = self._req("GET", "/index.html", src="127.0.0.2")
        except OSError as e:
            self.skipTest("本机不允许以 127.0.0.2 为源连接（%s）" % e)
        self.assertIn(s, (200, 404))       # FakeEdit.root 里可能没有 index.html → 404 也算通


class TestLoopbackOnlyByDefault(unittest.TestCase):
    """不给 --lan 时必须只绑回环（否则等于把可写的服务暴露给整个网段）。"""

    def test_default_bind_is_loopback(self):
        self.assertFalse(ES.parse_args([])["lan"])
        # serve() 里 host 的选法
        self.assertEqual("0.0.0.0" if ES.parse_args(["--lan"])["lan"] else "127.0.0.1", "0.0.0.0")
        self.assertEqual("0.0.0.0" if ES.parse_args([])["lan"] else "127.0.0.1", "127.0.0.1")


class TestRepoRootAndSrcPaths(unittest.TestCase):
    """交付物不含本机绝对路径：meta 的源目录存相对仓库根，服务按交付夹位置反推仓库根。

    约定布局 = `<仓库根>/aaManga/交付HTML/<标题>_汉化合集`；交付夹被拷走后找不到仓库根
    → 源写回跳过（只写交付副本），且绝对路径（旧交付物/手写 meta）仍原样使用。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="edit-repo-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "aaManga", "交付HTML", "某作品_汉化合集")
        self.src = os.path.join(self.tmp, "aaManga", "某作品", "汉化")
        os.makedirs(self.root)
        os.makedirs(self.src)

    def _ed(self, meta):
        """只测源目录解析：跳过 Edit.__init__ 的交付夹校验/站点导入。"""
        ed = ES.Edit.__new__(ES.Edit)
        ed.root = os.path.abspath(self.root)
        ed.meta = meta
        return ed

    def test_repo_root_from_delivery(self):
        self.assertEqual(os.path.abspath(ES.repo_root(self.root)),
                         os.path.abspath(self.tmp))

    def test_repo_root_missing(self):
        outside = tempfile.mkdtemp(prefix="edit-norepo-")
        self.addCleanup(shutil.rmtree, outside, True)
        os.makedirs(os.path.join(outside, "deep", "deeper"))
        self.assertEqual(ES.repo_root(os.path.join(outside, "deep", "deeper")), "")

    def test_repo_root_env_override(self):
        outside = tempfile.mkdtemp(prefix="edit-norepo2-")
        self.addCleanup(shutil.rmtree, outside, True)
        os.environ["AA_REPO_ROOT"] = self.tmp
        self.addCleanup(os.environ.pop, "AA_REPO_ROOT", None)
        self.assertEqual(os.path.abspath(ES.repo_root(outside)), os.path.abspath(self.tmp))

    def test_relative_src_resolved(self):
        ed = self._ed({"out_dir": "aaManga/某作品/汉化"})
        self.assertEqual(ed._src_of("out_dir"), self.src)
        self.assertEqual(ed._src_of("aa_dir"), "")            # 缺字段 → ""

    def test_relative_src_without_repo(self):
        """拷到别处（上溯找不到 aaManga/）→ 解析不出源，只写交付副本。"""
        ed = self._ed({"out_dir": "aaManga/某作品/汉化"})
        ed.root = tempfile.mkdtemp(prefix="edit-copied-")
        self.addCleanup(shutil.rmtree, ed.root, True)
        self.assertEqual(ed._src_of("out_dir"), "")

    def test_absolute_src_kept(self):
        """旧交付物/手写 meta 里的绝对路径原样使用（向后兼容）。"""
        ed = self._ed({"out_dir": self.src})
        self.assertEqual(ed._src_of("out_dir"), self.src)


def _gz_b64(t):
    """与生成器同口径：gzip(确定性 mtime=0) + base64。"""
    return base64.b64encode(gzip.compress(t.encode("utf-8"), 6, mtime=0)).decode("ascii")


def _pay_of(html, n):
    """从 index.html 抠第 n 章的章节数据 dict（v1/v2 都认）。"""
    m = re.search(r'window\.AA_DATA\[%d\]=(\{[^\n]*?\});' % n, html)
    return json.loads(m.group(1)) if m else None


def _unz_payload(d):
    """章节数据 dict → (tr_html, src_html)：v1 两份；v2 = src 全量 + tr 差异行。"""
    src = gzip.decompress(base64.b64decode(d["src"])).decode("utf-8")
    if d.get("p") is None:
        return gzip.decompress(base64.b64decode(d["tr"])).decode("utf-8"), src
    rows = src.split("\n")
    for step in gzip.decompress(base64.b64decode(d["p"])).decode("utf-8").split("\n"):
        if step:
            k, _, txt = step.partition(chr(2))
            rows[int(k)] = txt
    return "\n".join(rows), src


class TestInlineWriteBack(unittest.TestCase):
    """单档形态写回（2026-10-08）：`layout=inline` 时改的是 index.html 里的内嵌 payload。

    交付夹两种形态共用同一份编辑服务：外置写 data/NNNNN.js，内嵌写 index.html 里
    `window.AA_DATA[n]={…};` 那一段。这里用**手造的 index.html** 验写回本身（秒级，
    不跑生成器）：只动目标章、src 段逐字节不变、结构不被破坏、缺章要报错。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="edit-inline-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.entry = os.path.join(self.tmp, ES.LINK_NAME)

    def _write_entry(self, chs):
        rows = ['<html><body><pre class="aa" id="pre0"></pre>', "<script>",
                "window.AA_INLINE=1;"]
        for n, tr, src in chs:
            rows.append('window.AA_DATA[%d]={"tr":"%s","src":"%s"};'
                        % (n, _gz_b64(tr), _gz_b64(src)))
        rows += ["</script></body></html>"]
        with open(self.entry, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(rows) + "\n")

    def _entry(self):
        return open(self.entry, encoding="utf-8", newline="").read()

    def _pay(self, n):
        """index.html 里第 n 章的 (tr b64, src b64, 全量文本)。"""
        m = re.search(r'window\.AA_DATA\[%d\]=\{"tr":"([A-Za-z0-9+/=]*)","src":"([A-Za-z0-9+/=]*)"\};'
                      % n, self._entry())
        self.assertIsNotNone(m, "index.html 里找不到第 %d 章的章节数据段" % n)
        return m.group(1), m.group(2), self._entry()

    def _ed(self, layout="inline"):
        ed = ES.Edit.__new__(ES.Edit)
        ed.root = self.tmp
        ed.meta = {"layout": layout, "chapters": ["a", "b"], "site": "yarubook"}
        ed.layout = layout
        ed._ctx = {}
        return ed

    def test_rewrite_touches_only_target_chapter(self):
        self._write_entry([(0, "译一", "原一"), (1, "译二", "原二")])
        ed = self._ed()
        p = ed.rewrite_data(0, {"tr_html": "新的一章", "src": ["原一"], "cm": {}, "plan": {}})
        self.assertEqual(os.path.abspath(p), os.path.abspath(self.entry))
        tr0, src0, html = self._pay(0)
        self.assertEqual(gzip.decompress(base64.b64decode(tr0)).decode("utf-8"), "新的一章")
        self.assertEqual(src0, _gz_b64("原一"), "src 段必须沿用原 b64（逐字节不变）")
        tr1, src1, _ = self._pay(1)
        self.assertEqual((tr1, src1), (_gz_b64("译二"), _gz_b64("原二")), "其它章不能被动")
        # 结构没被破坏：两段都在、且第 1 段后面紧跟第 2 段（没吃掉换行/尾巴）
        self.assertEqual(html.count("window.AA_DATA["), 2)
        self.assertIn('};\nwindow.AA_DATA[1]={"tr":"', html)

    def test_missing_chapter_raises(self):
        self._write_entry([(0, "译一", "原一")])
        with self.assertRaises(RuntimeError):
            self._ed().rewrite_data(5, {"tr_html": "x", "src": [], "cm": {}, "plan": {}})

    def test_version_is_entry_mtime(self):
        self._write_entry([(0, "译一", "原一")])
        self.assertGreater(self._ed().version(), 0)

    def test_external_layout_still_writes_data_file(self):
        os.makedirs(os.path.join(self.tmp, "data"))
        self._write_entry([(0, "译一", "原一")])
        with open(os.path.join(self.tmp, "data", "00000.js"), "w",
                  encoding="utf-8", newline="") as f:
            f.write('window.AA_DATA[0]={"tr":"%s","src":"%s"};\n' % (_gz_b64("译一"), _gz_b64("原一")))
        ed = self._ed("external")
        ed.rewrite_data(0, {"tr_html": "外置改写", "src": ["原一"], "cm": {}, "plan": {}})
        s = open(os.path.join(self.tmp, "data", "00000.js"), encoding="utf-8").read()
        tr, src = _unz_payload(json.loads(s[s.index("{"):s.rindex("}") + 1]))
        self.assertEqual(tr, "外置改写")
        self.assertEqual(src, "原一", "src 段沿用原文件")
        self.assertNotIn("外置改写", self._entry(), "外置形态不该改 index.html")

    def test_rewrite_keeps_diff_format(self):
        """真实 AA 那样子（九成行原文/译文一致）→ 写回后仍走差异行格式，不退回「两份都存」。

        差异行必须能从**现成 src 段**解出来（服务不解压原文 txt、也不重渲染原文）。
        """
        src = ["　｜　（＾ω＾）　｜　" * 4] * 30
        tr = list(src)
        tr[3] = "改过的一行"
        payload = ES.G.chapter_payload(0, tr, src)
        with open(self.entry, "w", encoding="utf-8", newline="") as f:
            f.write('<html><body><script>\nwindow.AA_INLINE=1;\n' + payload + '</script></body></html>')
        self.assertIsNotNone(_pay_of(self._entry(), 0).get("p"), "前置条件：原章应是 v2 格式")

        tr2 = list(tr)
        tr2[9] = "又改了一行"
        self._ed().rewrite_data(0, {"tr_html": "\n".join(tr2), "src": src, "cm": {}, "plan": {}})
        d = _pay_of(self._entry(), 0)
        self.assertIsNotNone(d.get("p"), "写回后退回了 v1（差异行没算出来）")
        got_tr, got_src = _unz_payload(d)
        self.assertEqual(got_tr, "\n".join(tr2))
        self.assertEqual(got_src, "\n".join(src))


class TestInlineEndToEnd(unittest.TestCase):
    """生成**单档**交付夹 → 服务真改一行 → 数据写回 index.html 内嵌段（端到端）。

    fukou = 24 章 3.7MB → 默认单档。这一步覆盖「生成器的内嵌写法」与「服务的 inline 写回」
    是否真的对得上（两边正则/结构一致），比手造 fixture 更接近真机。
    """

    @classmethod
    def setUpClass(cls):
        import gen_aa_html
        from _work_guard import work_cfg
        cfg = work_cfg("fukou", ("aa_dir", "out_dir"), "编辑服务内联交付 e2e")
        cls._orig = cfg["html_out"]
        cls.tmp = tempfile.mkdtemp(prefix="edit-inline-e2e-")
        cfg["html_out"] = os.path.join(cls.tmp, "out")
        try:
            sys.argv = ["x", "fukou"]
            gen_aa_html.main("fukou")
            cls.folder = cfg["html_out"]
        finally:
            cfg["html_out"] = cls._orig

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_layout_inferred_when_meta_lacks_field(self):
        """旧交付物（meta 无 layout 字段）→ 按 data/ 目录在不在推断形态（向后兼容）。"""
        p = os.path.join(self.folder, ES.META_NAME)
        orig = open(p, encoding="utf-8").read()
        self.addCleanup(lambda: open(p, "w", encoding="utf-8", newline="").write(orig))
        self.addCleanup(shutil.rmtree, os.path.join(self.folder, "data"), True)
        meta = json.loads(orig)
        meta.pop("layout", None)
        with open(p, "w", encoding="utf-8", newline="") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        self.assertEqual(ES.Edit(self.folder).layout, "inline", "无 data/ 目录 → 单档")
        os.makedirs(os.path.join(self.folder, "data"), exist_ok=True)
        self.assertEqual(ES.Edit(self.folder).layout, "external", "有 data/ 目录 → 外置")

    def test_save_line_updates_inline_payload(self):
        ed = ES.Edit(self.folder)
        self.assertEqual(ed.layout, "inline")
        c = ed.ctx(0)
        idx = next(i for i in range(1, len(c["lines"]) + 1)
                   if ES.logic.line_type_of(c["src"][i - 1]) == "text")
        entry = os.path.join(self.folder, ES.LINK_NAME)
        before = open(entry, encoding="utf-8", newline="").read()
        tr_before, _ = _unz_payload(_pay_of(before, 0))
        r = ed.save_line(0, idx, c["lines"][idx - 1] + "Ｘ")
        self.assertTrue(r.get("ok"), r)
        after = open(entry, encoding="utf-8", newline="").read()
        d0 = _pay_of(after, 0)
        self.assertIsNotNone(d0, "写回后 index.html 的章节数据段结构被破坏")
        tr_after, _src = _unz_payload(d0)
        self.assertNotEqual(tr_after, tr_before, "改行后 index.html 里的译文应更新")
        self.assertIn("Ｘ", tr_after)
        self.assertEqual(tr_after.count("\n"), tr_before.count("\n"), "写回不该增删行")
        self.assertIsNotNone(d0.get("p"), "写回后退回了 v1（差异行没重算）")
        # 第 1 章的数据段必须原样（只动被改的那一章）
        self.assertEqual(_pay_of(after, 1), _pay_of(before, 1))


if __name__ == "__main__":
    unittest.main()
