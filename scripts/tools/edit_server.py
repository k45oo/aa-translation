# -*- coding: utf-8 -*-
"""交付合集自带的「编辑服务」：在交付文件夹里运行它，浏览器即可改行 / 翻译替换并**写回源**。

两条运行方式：
  · 仓库内调试： `python scripts/tools/edit_server.py <交付文件夹> [--port 8770] [--no-browser] [--lan]`
  · 交付文件夹内： **AA编辑服务.exe**（PyInstaller 单文件；打包脚本 scripts/tools/build_edit_server.py）
     双击 `编辑服务.bat` = 本机改；双击 `手机阅读(局域网).bat` = 加 --lan，手机也能开（只读）

`--lan`（手机阅读用）：绑 0.0.0.0 并把手机可访问的 http://<局域网IP>:端口/ 打进控制台；
**远端客户端一律只读**（POST 写入接口 403）——手机=浏览器看，改行/替换仍在 PC 上做。

HTTP 接口（全部 JSON + 允许 `Origin: null` 的 CORS，故 file:// 打开交付页也能调）：
  GET  /api/ping                  服务/作品/数据版本/是否只读/渲染器是否与交付一致(stale)/数据形态(layout)
  GET  /api/map?ch=N              第 N 章「渲染行 → txt 行号」映射（点击定位用）
  GET  /api/line?ch=N&idx=I       单行数据：原文 / 译文 / sfx 标记 / 渲染后的行 HTML
  GET  /api/chapter?ch=N          整章重渲染后的 tr HTML（替换批量应用后页面直接换正文，绕开脚本缓存）
  POST /api/save                  保存单行（宽度自动补位；重渲染该行；重写该章章节数据）
  POST /api/replace               翻译替换（preview=true 只预览；AA 艺术行整行跳过）

章节数据有两种形态（`edit_meta.json` 的 `layout` 字段；由 gen_aa_html 按数据体量选定）：
  · `external` —— data/NNNNN.js 一章一文件（大书，懒加载）；写回改该文件。
  · `inline`   —— 全部数据内嵌在 index.html 的 `<script>` 里（小书，单档零依赖阅读）；写回
    改 index.html 里 `window.AA_DATA[n]={…};` 那一段。旧交付物没有该字段 → 按 data/ 目录
    是否存在推断。

写回目标（**都写**，两边保持一致）：
  ① 交付文件夹内 `汉化/<base>_汉化.txt.gz`（主：这是交付页自己的数据源）
  ② `edit_meta.json` 里记录的**源**目录 `汉化/<base>_汉化.txt`（本机存在时；拷贝到别处则跳过）。
     源目录在 meta 里存的是**相对仓库根**的路径（交付物不含本机绝对路径）→ 由交付夹位置
     反推仓库根再拼回来；旧交付物/手写 meta 里的绝对路径仍原样使用（见 `repo_root`）。
  两者都不存在 → 只读（前端给出提示）。

渲染/宽度/类型判定一律复用仓库现成实现（`aa_web_app/logic.py` + `scripts/common/gen_aa_html.py`），
本文件只做「读文件 → 调它们 → 写文件」的编排，不复制任何渲染或替换逻辑。
启动时会把随包的 `scripts/` 树接上 sys.path：仓库内跑用仓库的，exe 里跑用打包进去的。
"""
import base64
import gzip
import importlib
import json
import os
import re
import socket
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

LINK_NAME = "index.html"
META_NAME = "edit_meta.json"
LOG_NAME = "edits.jsonl"          # 交付页的修改留档（交付文件夹内一份 + 源 work/ 一份）
DEFAULT_PORT = 8770
PORT_TRIES = 10
_EXE = "AA编辑服务.exe"
LAN_TXT = "手机打开地址.txt"        # --lan 时写进交付文件夹，方便把地址发到手机


# ────────────────────────── 手机阅读（--lan）：地址枚举 + 写入权限判定 ──────────────────────────
def _lan_ips():
    """本机非回环 IPv4（手机能访问的地址）。UDP connect 只设定默认出口网卡、不发包。"""
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("223.5.5.5", 80))
            ips.append(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return [ip for ip in ips if not ip.startswith("127.") and ip not in ("0.0.0.0", "::1")]


def _is_local_ip(ip, local_ips=()):
    """本机客户端：回环，或本机自己的网卡地址（PC 用 http://<局域网IP>:8770 打开也算本机）。"""
    return ip in ("127.0.0.1", "::1", "localhost") or ip in set(local_ips)


def write_allowed(lan, client_ip, local_ips=()):
    """写入接口准入：只有 --lan 模式要拦远端（手机只读）；本机模式（绑 127.0.0.1）谁也进不来。"""
    return (not lan) or _is_local_ip(client_ip, local_ips)


def write_lan_txt(root, url, lan_urls):
    """把手机要用的地址写成交付夹里的 `手机打开地址.txt`（好发到手机点开）。返回写入内容。"""
    lines = ["手机阅读地址（手机与电脑连同一个 Wi-Fi，用手机浏览器打开下面这个）", ""]
    for i, lu in enumerate(lan_urls or [url]):
        lines.append(lu + ("      ← 优先用这个" if i == 0 and lan_urls else ""))
    if len(lan_urls) > 1:
        lines += ["", "（第二个及以后常是虚拟机/虚拟网卡，手机连不上，别用）"]
    lines += ["", "手机上只读；改行/替换请在电脑上打开 %s" % url,
              "关掉电脑上那个黑窗口服务就停了。"]
    text = "\n".join(lines) + "\n"
    try:
        with open(os.path.join(root, LAN_TXT), "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass
    return text


def parse_args(argv):
    """CLI → {root, port, open_browser, lan}（main 与测试共用）。"""
    root, port, open_browser, lan = None, DEFAULT_PORT, True, False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--port", "-p") and i + 1 < len(argv):
            port = int(argv[i + 1]); i += 2; continue
        if a.startswith("--port="):
            port = int(a.split("=", 1)[1]); i += 1; continue
        if a == "--no-browser":
            open_browser = False; i += 1; continue
        if a in ("--lan", "--lan-read"):
            lan = True; i += 1; continue
        if not a.startswith("-"):
            root = a
        i += 1
    return {"root": root, "port": port, "open_browser": open_browser, "lan": lan}


# ────────────────────────── 运行环境（仓库内 / exe 内都能找到 scripts 树） ──────────────────────────
def _bootstrap_paths():
    here = os.path.dirname(os.path.abspath(__file__))
    bases = []
    if getattr(sys, "frozen", False):                      # PyInstaller 单文件
        bases.append(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    bases += [here, os.path.dirname(here), os.path.dirname(os.path.dirname(here))]
    for b in bases:
        for p in (b,
                  os.path.join(b, "scripts"),
                  os.path.join(b, "scripts", "common"),
                  os.path.join(b, "scripts", "works"),
                  os.path.join(b, "aa_web_app")):
            if os.path.isdir(p) and p not in sys.path:
                sys.path.insert(0, p)


_bootstrap_paths()
# 动态 import（不用 `import logic`）：PyInstaller 静态分析看不见它们，
# 打包时才会老老实实把 scripts/ 与 aa_web_app/logic.py 当**数据**带进 exe，
# 运行时再从 _MEIPASS 里按路径加载（版本永远与打进去的那份一致）。
import importlib                                  # noqa: E402
logic = importlib.import_module("logic")          # aa_web_app/logic.py（纯逻辑层，无 FastAPI 依赖）
G = importlib.import_module("gen_aa_html")        # 渲染（render_line / _blank_plan / _gz_b64）


# ────────────────────────── 小工具 ──────────────────────────
def _pick(base_dir, name):
    """base_dir/name 的实际存在路径（`.txt` / `.txt.gz` 透明）；都没有返回 None。"""
    if not base_dir:
        return None
    for cand in (os.path.join(base_dir, name), os.path.join(base_dir, name + ".gz")):
        if os.path.exists(cand):
            return cand
    return None


REPO_ANCHOR = "aaManga"      # 仓库根判据（仓库根下必有这个目录）


def repo_root(root):
    """交付夹所在的**仓库根**；找不到返回 ""。

    `edit_meta.json` 的源目录存的是相对仓库根的路径（交付物不含本机绝对路径，见
    `gen_aa_html._meta_src_path`），所以要按交付夹位置反推仓库根：
    env `AA_REPO_ROOT` 指名优先 → 否则自交付夹逐级向上找「含 aaManga/ 的目录」
    （约定布局 = `<仓库根>/aaManga/交付HTML/<标题>_汉化合集`）。
    交付夹被拷到别处（桌面/别人机器）时找不到 → 源写回自动跳过，只写交付副本。
    """
    env = os.environ.get("AA_REPO_ROOT") or ""
    if env and os.path.isdir(os.path.join(env, REPO_ANCHOR)):
        return os.path.abspath(env)
    d = os.path.abspath(root)
    for _ in range(5):
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
        if os.path.isdir(os.path.join(d, REPO_ANCHOR)):
            return d
    return ""


def _base_of(tr_fname):
    """汉化文件名 → 基名（entry_1_汉化.txt / entry_1_汉化.txt.gz → entry_1）"""
    b = os.path.basename(tr_fname)
    for suf in ("_汉化.txt.gz", "_汉化.txt"):
        if b.endswith(suf):
            return b[:-len(suf)]
    return b


def _short(s, n=60):
    s = s if isinstance(s, str) else str(s)
    return s if len(s) <= n else s[:n] + "…"


class Edit:
    """交付文件夹的编辑核心：定位行李、读写、重渲染。线程内串行（都加 self.lock）。"""

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.lock = threading.RLock()
        mp = os.path.join(self.root, META_NAME)
        if not os.path.exists(mp):
            raise SystemExit("不是交付文件夹（缺 %s）：%s" % (META_NAME, self.root))
        with open(mp, encoding="utf-8") as f:
            self.meta = json.load(f)
        # 渲染器版本自检：exe 里打的是打包那天的脚本；与本次交付生成时的指纹不一致 =
        # 编辑后重渲染可能与页面渲染有出入 → 提示重新打包（不拦，只是提示）
        try:
            self.stale = bool(self.meta.get("render_ver")) and \
                self.meta["render_ver"] != G.render_ver()
        except Exception:
            self.stale = False
        self.chapters = list(self.meta.get("chapters") or [])
        if not self.chapters:
            raise SystemExit("%s 里没有 chapters 清单（重新生成合集即可）" % META_NAME)
        # 章节数据形态：inline（内嵌在 index.html）/ external（data/NNNNN.js）。
        # meta.layout 为准；旧交付物没有该字段 → 按 data/ 目录在不在推断（向后兼容）。
        self.layout = self.meta.get("layout") or (
            "external" if os.path.isdir(os.path.join(self.root, "data")) else "inline")
        self.site = importlib.import_module("sites." + self.meta["site"])
        self.title = self.meta.get("title") or self.meta.get("work") or ""
        # ① 交付文件夹内的源（自包含）
        self.loc_aa = os.path.join(self.root, "aa")
        self.loc_out = os.path.join(self.root, "汉化")
        self.loc_work = os.path.join(self.root, "work")
        # ② 生成时记录的仓库源（本机存在则同步写）；meta 里存**相对仓库根**的路径
        self.src_aa = self._src_of("aa_dir")
        self.src_out = self._src_of("out_dir")
        self.src_work = self._src_of("work_dir")
        self._ctx = {}

    def _src_of(self, key):
        """meta 的源目录 → 绝对路径：绝对路径原样用（旧交付物/手写 meta），相对路径按仓库根拼。"""
        v = self.meta.get(key) or ""
        if not v or os.path.isabs(v):
            return v
        root = repo_root(self.root)
        return os.path.join(root, v.replace("/", os.sep)) if root else ""

    # ---- 路径解析：读优先「交付副本（订单页看到的就是它）」，写两者都写 ----
    def tr_local(self, base):
        return _pick(self.loc_out, base + "_汉化.txt")

    def tr_src(self, base):
        return _pick(self.src_out, base + "_汉化.txt")

    def aa_of(self, base):
        return _pick(self.loc_aa, base + ".txt") or _pick(self.src_aa, base + ".txt")

    def tr_of(self, base):
        """读用：交付副本优先，否则源。都没有 → None。"""
        return self.tr_local(base) or self.tr_src(base)

    def colormap(self, base):
        for cand in (os.path.join(self.loc_aa, base + "_colormap.json"),
                     os.path.join(self.src_aa, base + "_colormap.json")):
            if cand and os.path.exists(cand):
                try:
                    with open(cand, encoding="utf-8") as f:
                        d = json.load(f)
                    if isinstance(d, dict):
                        return d
                except Exception:
                    pass
        return {}

    def sfx_marks(self, base):
        for wd in (self.loc_work, self.src_work):
            if wd:
                p = os.path.join(wd, "sfx_marks", base + ".json")
                if os.path.exists(p):
                    try:
                        with open(p, encoding="utf-8") as f:
                            d = json.load(f)
                        if isinstance(d, dict):
                            return d
                    except Exception:
                        pass
        return {}

    def work_dirs_write(self):
        """sfx 标记写哪：交付文件夹内的 work/（主，没有就建）+ 源 work_dir（本机存在时）。"""
        dirs = [self.loc_work]
        if self.src_work and os.path.isdir(self.src_work):
            dirs.append(self.src_work)
        return dirs

    @property
    def readonly(self):
        return not (os.path.isdir(self.loc_out) or (self.src_out and os.path.isdir(self.src_out)))

    # ---- 章节上下文（读文件 + 渲染 + 行号映射） ----
    def ctx(self, n, fresh=False):
        with self.lock:
            if not fresh and n in self._ctx:
                return self._ctx[n]
            f = self.chapters[n]
            base = _base_of(f)
            tr_path = self.tr_of(base)
            if not tr_path:
                raise KeyError("第 %d 章没有可读的汉化 txt（%s）" % (n + 1, f))
            lines = logic.read_lines(tr_path)
            aa_path = self.aa_of(base)
            src = logic.read_lines(aa_path) if aa_path else []
            if len(src) < len(lines):
                src = src + [""] * (len(lines) - len(src))
            cm = self.colormap(base)
            marks = self.sfx_marks(base)
            plan = G._blank_plan(lines)
            rendered = G._render_body(lines, cm, self.site, plan,
                                      src_lines=src, marks=marks)
            # 渲染行 k → txt 行号（1-based；0 = 为楼间间距补出来的空行，不可编辑）
            ln = []
            for i, l in enumerate(lines):
                if l.startswith("#"):
                    continue
                ln.extend([0] * plan.get(i, 0))
                ln.append(i + 1)
            c = {"n": n, "f": f, "base": base, "lines": lines, "src": src,
                 "cm": cm, "marks": marks, "plan": plan,
                 "tr_html": "\n".join(rendered), "ln": ln, "tr_path": tr_path}
            self._ctx[n] = c
            return c

    def invalidate(self, n=None):
        with self.lock:
            if n is None:
                self._ctx.clear()
            else:
                self._ctx.pop(n, None)

    def render_line_html(self, c, idx0):
        """单行（0-based txt 行）渲染 → HTML。"""
        return G.render_line(idx0, c["lines"][idx0], c["cm"], self.site,
                              src_line=(c["src"][idx0] if idx0 < len(c["src"]) else None),
                              sfx_marks=c["marks"])

    def _src_b64_of(self, n, c):
        """该章「原文」段的 b64：优先沿用交付物里现成的那份（逐字节不变，原文没改过）。

        external → data/NNNNN.js；inline → index.html 里内嵌的那段。都没有（异常情况）才
        用当前上下文重新渲染一份兜底。
        """
        s = ""
        if self.layout == "inline":
            try:
                with open(os.path.join(self.root, LINK_NAME), encoding="utf-8", newline="") as f:
                    html = f.read()
                # 不假定键序：v1 是 {tr,src}、v2 是 {src,p,v} —— 一律取 "src"
                m = re.search(r'window\.AA_DATA\[%d\]=\{[^\n}]*?"src":"([A-Za-z0-9+/=]*)"' % n, html)
                s = m.group(1) if m else ""
            except OSError:
                s = ""
        else:
            p = os.path.join(self.root, "data", "%05d.js" % n)
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    m = re.search(r'"src":"([A-Za-z0-9+/=]*)"', f.read())
                s = m.group(1) if m else ""
        # 兜底：交付物里没有现成 src 段（异常情况）→ 用当前上下文重渲染。
        # 手造/残缺交付夹可能没有 site（测试用）→ 退回空串，别把 AttributeError 抛出去。
        if s or not getattr(self, "site", None):
            return s
        return G._gz_b64("\n".join(G._render_body(c["src"], c["cm"], self.site, c["plan"])))

    def _rewrite_inline(self, n, payload):
        """单档形态：替换 index.html 里 `window.AA_DATA[n]={...};` 那一段（原子写）。"""
        p = os.path.join(self.root, LINK_NAME)
        with open(p, encoding="utf-8", newline="") as f:
            html = f.read()
        # payload 里只有 b64 字符集（含 / + =），没有 `}` 和换行 → 匹配到第一个 `};` 就是结尾
        met = re.compile(r'window\.AA_DATA\[%d\]=\{[^\n]*?\};' % n)
        new, cnt = met.subn(lambda _m: payload.rstrip("\n"), html, count=1)
        if cnt != 1:
            raise RuntimeError("index.html 里找不到第 %d 章的章节数据段（单档形态）" % (n + 1))
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(new)
        os.replace(tmp, p)
        return p

    @staticmethod
    def _src_lines_of(b64):
        """交付物里 src 段的 b64 → 渲染行列表（算差异行用）；拿不到就 None（→ 只能出 v1）。"""
        if not b64:
            return None
        try:
            return gzip.decompress(base64.b64decode(b64)).decode("utf-8").split("\n")
        except Exception:
            return None

    def rewrite_data(self, n, c=None):
        """把该章渲染结果写回章节数据（external → data/NNNNN.js；inline → index.html 内嵌段）。

        src 段沿用原文件里的 b64，逐字节不变（原文没变，不必重新渲染）；payload 是 v1 还是
        v2（src+差异行）由 `gen_aa_html.chapter_payload` 现算 —— 原文渲染行能从现成 src b64
        解出来，所以改一行也不会把该章退回「两份都存」的旧格式。
        """
        c = c or self.ctx(n)
        src_b64 = self._src_b64_of(n, c)
        payload = G.chapter_payload(n, c["tr_html"].split("\n"),
                                    self._src_lines_of(src_b64), src_b64=src_b64 or None)
        if self.layout == "inline":
            return self._rewrite_inline(n, payload)
        p = os.path.join(self.root, "data", "%05d.js" % n)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(payload)
        os.replace(tmp, p)
        return p

    def version(self):
        """数据版本（前端加 ?v= 破缓存）：external = data/*.js 最大 mtime；inline = index.html 的。"""
        if self.layout == "inline":
            try:
                return int(os.path.getmtime(os.path.join(self.root, LINK_NAME)))
            except OSError:
                return 0
        d = os.path.join(self.root, "data")
        v = 0
        try:
            for fn in os.listdir(d):
                v = max(v, int(os.path.getmtime(os.path.join(d, fn))))
        except OSError:
            pass
        return v

    # ---- 写盘：交付副本 + 源，一处失败不影响另一处 ----
    def _log(self, etype, fname, rows, line_type=""):
        """修改留档：交付文件夹 edits.jsonl（+ 源的 work/edits.jsonl 存在时也写一份）。"""
        if not rows:
            return
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        recs = [json.dumps({"ts": ts, "work": self.meta.get("work"), "file": fname,
                            "line": r[0], "type": etype, "line_type": line_type,
                            "before": r[1], "after": r[2]}, ensure_ascii=False) + "\n"
                for r in rows]
        for path in [os.path.join(self.root, LOG_NAME)] + \
                    ([os.path.join(self.src_work, LOG_NAME)] if os.path.isdir(self.src_work) else []):
            try:
                with open(path, "a", encoding="utf-8") as f:
                    f.writelines(recs)
            except OSError:
                pass

    def save_line(self, n, idx, text):
        """保存第 n 章第 idx 行（1-based txt 行号，只改内容不增删行）。"""
        with self.lock:
            c = self.ctx(n)
            if not 1 <= idx <= len(c["lines"]):
                return {"ok": False, "error": "行号越界：第 %d 行（本章共 %d 行）" % (idx, len(c["lines"]))}
            old = c["lines"][idx - 1]
            if text == old:
                return {"ok": True, "text": old, "unchanged": True,
                        "html": self.render_line_html(c, idx - 1)}
            aa_path = self.aa_of(c["base"])
            base = c["base"]
            targets = [p for p in (self.tr_local(base), self.tr_src(base)) if p]
            if not targets:
                return {"ok": False, "error": "找不到可写的汉化 txt（交付副本与源都缺失）"}
            res = logic.save_line(aa_path or c["tr_path"], targets[0], idx - 1, text)
            if res.get("result") == "error":
                return {"ok": False, "error": res.get("message") or "保存失败"}
            new_text = res.get("text", text)
            # 源（另一份）同步：同一行写同一文本；已补位过的文本不会二次补位
            for p in targets[1:]:
                r2 = logic.save_line(aa_path or c["tr_path"], p, idx - 1, new_text)
                if r2.get("result") == "error":
                    return {"ok": False, "error": "源同步失败：%s" % r2.get("message")}
            c["lines"][idx - 1] = new_text
            c["tr_html"] = "\n".join(G._render_body(c["lines"], c["cm"], self.site, c["plan"],
                                                    src_lines=c["src"], marks=c["marks"]))
            self.rewrite_data(n, c)
            src_line = c["src"][idx - 1] if idx - 1 < len(c["src"]) else ""
            self._log("行修改", c["f"], [(idx, old, new_text)], logic.line_type_of(src_line))
            return {"ok": True, "text": new_text, "padded": bool(res.get("padded")),
                    "residual": res.get("residual") or [],
                    "html": self.render_line_html(c, idx - 1), "ver": self.version()}

    def replace(self, pairs, scope="ch", n=0, preview=True):
        """翻译替换：pairs=[[查找, 替换为, 是否加 sfx], ...]；AA 艺术行整行跳过。"""
        with self.lock:
            norm = []
            for p in (pairs or []):
                if not p or not str(p[0]).strip():
                    continue
                norm.append((str(p[0]), str(p[1]) if len(p) > 1 else "", bool(p[2]) if len(p) > 2 else False))
            if not norm:
                return {"ok": False, "error": "没有可用的替换对（查找串不能为空）"}
            finds = [p[0] for p in norm]
            sfx_finds = set(p[0] for p in norm if p[2])
            idxs = [n] if scope == "ch" else list(range(len(self.chapters)))
            files, total = [], 0
            for i in idxs:
                c = self.ctx(i)
                base = c["base"]
                # 艺术行保护：交付侧没有 override/line_meta_v2，用与工作台同源的 line_type_of
                skip_idx = frozenset(j for j, s in enumerate(c["src"]) if logic.line_type_of(s) == "aa")
                tr_path = self.tr_of(base)
                aa_path = self.aa_of(base) or tr_path
                sfx_out = {}
                changed, skipped = logic.apply_glossary_chapter(
                    aa_path, tr_path, [(f, r) for f, r, _ in norm], dry_run=True,
                    skip_idx=skip_idx, sfx_pairs=sfx_finds, sfx_out=sfx_out)
                if not changed:
                    continue
                row = {"n": i, "file": c["f"], "changed": len(changed),
                       "art_skipped": len(skip_idx),
                       "sample": [[ch[0] + 1, _short(ch[1]), _short(ch[2]), bool(ch[3:])]
                                  for ch in changed[:20]]}
                total += len(changed)
                if not preview:
                    # 交付副本（主）+ 源（本机存在时）→ 按 dry_run 得到的 after 直接落盘
                    rows = [(ch[0], ch[2]) for ch in changed]
                    self._apply_rows(tr_path, rows)
                    sp = self.tr_src(base)
                    if sp and sp != tr_path:
                        self._apply_rows(sp, rows)
                    if sfx_out:
                        for wd in self.work_dirs_write():
                            logic.merge_sfx_marks(wd, base, sfx_out)
                    self.invalidate(i)
                    c2 = self.ctx(i)                     # 重读 + 重渲染（含新 sfx 标记）
                    self.rewrite_data(i, c2)
                    self._log("翻译替换", c2["f"], [(ch[0] + 1, ch[1], ch[2]) for ch in changed],
                              "art跳过 %d 行" % len(skip_idx))
                files.append(row)
            return {"ok": True, "preview": bool(preview), "files": files, "total": total,
                    "scope": scope, "ver": self.version()}

    @staticmethod
    def _apply_rows(path, rows):
        """把 (0-based行号, 新文本) 落到指定 txt（.gz 透明）。"""
        if not path or not rows:
            return
        lines = logic.read_lines(path)
        for i, t in rows:
            if 0 <= i < len(lines):
                lines[i] = t
        logic.write_lines(path, lines)


# ────────────────────────── HTTP 层 ──────────────────────────
class Handler(BaseHTTPRequestHandler):
    server_version = "AAEditServer/1.0"
    protocol_version = "HTTP/1.1"

    # --- 基础 ---
    def log_message(self, fmt, *args):        # 静音默认日志（只留自己的 print）
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    # --- 路由 ---
    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        ed = self.server.edit
        try:
            if u.path == "/api/ping":
                return self._json({"ok": True, "work": ed.meta.get("work"), "title": ed.title,
                                   "chapters": len(ed.chapters), "ver": ed.version(),
                                   "layout": getattr(ed, "layout", ""),
                                   "readonly": ed.readonly, "stale": bool(ed.stale),
                                   "lan": bool(getattr(self.server, "lan", False)),
                                   "remote": not write_allowed(
                                       getattr(self.server, "lan", False), self.client_address[0],
                                       getattr(self.server, "local_ips", ())),
                                   "can_edit": (not ed.readonly) and write_allowed(
                                       getattr(self.server, "lan", False), self.client_address[0],
                                       getattr(self.server, "local_ips", ()))})
            if u.path == "/api/map":
                c = ed.ctx(int(q["ch"]))
                return self._json({"ok": True, "n": c["n"], "f": c["f"],
                                   "lines": len(c["ln"]), "ln": c["ln"]})
            if u.path == "/api/chapter":
                c = ed.ctx(int(q["ch"]))
                return self._json({"ok": True, "n": c["n"], "f": c["f"],
                                   "lines": len(c["lines"]), "tr": c["tr_html"]})
            if u.path == "/api/line":
                n, i = int(q["ch"]), int(q["idx"])
                c = ed.ctx(n)
                if not 1 <= i <= len(c["lines"]):
                    return self._json({"ok": False, "error": "行号越界"}, 404)
                return self._json({"ok": True, "idx": i, "src": c["src"][i - 1],
                                   "tr": c["lines"][i - 1],
                                   "line_type": logic.line_type_of(c["src"][i - 1]),
                                   "sfx": (c["marks"].get(str(i)) or []),
                                   "html": ed.render_line_html(c, i - 1)})
            return self._static(u.path)
        except KeyError as e:
            return self._json({"ok": False, "error": "缺少参数 %s" % e}, 400)
        except Exception as e:
            traceback.print_exc()
            return self._json({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, 500)

    def do_POST(self):
        u = urlparse(self.path)
        ed = self.server.edit
        if not write_allowed(getattr(self.server, "lan", False), self.client_address[0],
                             getattr(self.server, "local_ips", ())):
            # --lan（手机阅读）时远端只读：手机上只看不改，改行/替换仍在 PC 上做
            return self._json({"ok": False, "remote": True,
                               "error": "局域网访问为只读：改行 / 替换请在电脑上用 http://127.0.0.1:%d/ 打开"
                                        % self.server.server_address[1]}, 403)
        try:
            b = self._body()
            if u.path == "/api/save":
                r = ed.save_line(int(b["ch"]), int(b["idx"]), b.get("text", ""))
                return self._json(r, 200 if r.get("ok") else 400)
            if u.path == "/api/replace":
                r = ed.replace(b.get("pairs") or [], scope=b.get("scope") or "ch",
                               n=int(b.get("ch") or 0), preview=bool(b.get("preview")))
                return self._json(r, 200 if r.get("ok") else 400)
            return self._json({"ok": False, "error": "未知接口 %s" % u.path}, 404)
        except Exception as e:
            traceback.print_exc()
            return self._json({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, 500)

    # --- 静态文件（限在交付文件夹内） ---
    _MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
             ".json": "application/json; charset=utf-8", ".css": "text/css; charset=utf-8",
             ".txt": "text/plain; charset=utf-8", ".gz": "application/octet-stream",
             ".exe": "application/octet-stream", ".png": "image/png", ".jpg": "image/jpeg",
             # 交付页 AA 等宽字体（外置形态放在交付夹里，见 common/aa_font.py）；
             # 缺这条会以 octet-stream 发出去，Chromium 对字体 MIME 更挑剔
             ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".otf": "font/otf"}

    def _static(self, path):
        root = self.server.edit.root
        rel = path.lstrip("/") or LINK_NAME
        full = os.path.abspath(os.path.join(root, rel.replace("/", os.sep)))
        if not (full == root or full.startswith(root + os.sep)) or not os.path.isfile(full):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self._cors()
            self.end_headers()
            return
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", self._MIME.get(os.path.splitext(full)[1].lower(),
                                                        "application/octet-stream"))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)


class _Srv(ThreadingHTTPServer):
    """关掉「客户端提前断开」的 traceback：刷新/关页面时 http.server 会往控制台甩
    ConnectionResetError 堆栈，用户（双击 exe 看一个黑窗口）会以为服务坏了。"""
    daemon_threads = True

    def handle_error(self, request, client_address):
        et = sys.exc_info()[0]
        if et in (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            return
        return ThreadingHTTPServer.handle_error(self, request, client_address)


def serve(root, port=DEFAULT_PORT, open_browser=True, lan=False):
    ed = Edit(root)
    local_ips = _lan_ips()
    host = "0.0.0.0" if lan else "127.0.0.1"
    httpd = None
    for p in range(port, port + PORT_TRIES):
        try:
            httpd = _Srv((host, p), Handler)
            port = p
            break
        except OSError:
            continue
    if httpd is None:
        raise SystemExit("端口 %d~%d 都被占用" % (port, port + PORT_TRIES - 1))
    httpd.edit = ed
    httpd.lan = bool(lan)
    httpd.local_ips = local_ips
    url = "http://127.0.0.1:%d/%s" % (port, LINK_NAME)
    lan_urls = ["http://%s:%d/%s" % (ip, port, LINK_NAME) for ip in local_ips]
    # flush：exe 双击时 stdout 是控制台（行缓冲没问题），但被 bat/管道重定向时是块缓冲
    # → 不 flush 会看不到地址提示，用户以为没起来
    P = lambda s="": print(s, flush=True)
    P("=" * 62)
    P("  AA 交付页编辑服务")
    P("  作品：%s" % ed.title)
    P("  交付夹：%s" % ed.root)
    P("  章节：%d 章   写回：%s" % (len(ed.chapters),
        "只读（找不到汉化 txt）" if ed.readonly else "交付副本" +
        ("＋源 %s" % ed.src_out if ed.src_out and os.path.isdir(ed.src_out) else "")))
    if ed.stale:
        P("  ⚠ 本服务的渲染器与本次交付不是同一版 → 请用仓库重新打包（build_edit_server.py）")
    P("  电脑打开：%s" % url)
    if lan:
        if lan_urls:
            P("  ── 手机阅读（同一 Wi-Fi）─────────────────────────")
            for i, lu in enumerate(lan_urls):
                P("  %s%s" % ("手机打开：" if i == 0 else "          ", lu))
            P("  （优先用第一个=默认上网那块网卡；多出来的常是虚拟机/虚拟网卡，手机连不上）")
            P("  （也写进了交付夹里的 %s，可发到手机点开）" % LAN_TXT)
        else:
            P("  ⚠ 没找到局域网 IP（检查 Wi-Fi/网线）；手机可能连不上")
        P("  ⚠ 手机端只读；改行/替换请在电脑上用上面那个 127.0.0.1 地址")
        P("  ⚠ 首次运行会弹 Windows 防火墙 → 要选「允许访问」（专用网络）")
        write_lan_txt(ed.root, url, lan_urls)
    P("  关掉本窗口即停止服务（编辑请在这个页面里做）")
    P("=" * 62)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        P("\n已停止")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    opt = parse_args(argv)
    root, port, open_browser, lan = opt["root"], opt["port"], opt["open_browser"], opt["lan"]
    if root is not None:
        # 启动器里 "%~dp0" 收尾是反斜杠 → Windows 会把 \" 当转义引号，参数带脏字符；
        # 这里顺手容错（去引号/去尾斜杠/退回上一级）
        root = root.strip().strip('"')
        for cand in (root, root.rstrip("\\/"), os.path.dirname(root.rstrip("\\/"))):
            if cand and os.path.exists(os.path.join(cand, META_NAME)):
                root = cand
                break
    if root is None:
        # 默认：exe 所在目录（交付文件夹）；仓库内直跑时用 cwd
        cand = (os.path.dirname(os.path.abspath(sys.executable))
                if getattr(sys, "frozen", False) else os.getcwd())
        root = cand if os.path.exists(os.path.join(cand, META_NAME)) else os.getcwd()
    if not os.path.exists(os.path.join(root, META_NAME)):
        P = lambda s="": print(s, flush=True)
        P("用法：%s <交付文件夹> [--port 8770] [--no-browser] [--lan]" %
          (os.path.basename(sys.executable) if getattr(sys, "frozen", False) else "edit_server.py"))
        P("      交付文件夹里应有 %s / %s" % (LINK_NAME, META_NAME))
        P("      --lan = 手机也能读（绑 0.0.0.0，远端只读；交付夹里「手机阅读(局域网).bat」就是它）")
        return 2
    serve(root, port, open_browser, lan)
    return 0


if __name__ == "__main__":
    sys.exit(main())
