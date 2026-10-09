# -*- coding: utf-8 -*-
"""阅读器窗口化等价性 A/B：区间 API 的行 HTML ↔ 窗口化之前的整页内联 JSON。

背景（方案 doc/方案归档/阅读器窗口化渲染方案_2026-09-21.md §5 机器可验 #4）：客户端自 2026-09-21 起
只经 `GET /api/work/{work}/chapter/{fname}/range` 取行渲染，服务端行渲染必须与旧的
「整页三份内联 JSON（src-data/tr-data/refine-data）」逐行逐字段一致，否则窗口化会悄悄改坏正文。

**基线怎么来**：窗口化上线前抓一份整页 HTML（此时页面里还有三个内联 JSON 块）——
`curl -s "http://127.0.0.1:8765/work/<work>/read?f=<src>" -o 基线目录/<base>.html`；
或从 git 历史里 checkout 窗口化之前的 main.py/reader.html 起一个临时实例抓。
基线必须是**同一批数据**（作品/章节内容未变），否则比对必然报差异。

用法:
    python scripts/verify/reader_win_ab.py <基线文件或目录> [...]        # 需工作台在 8765 运行
    python scripts/verify/reader_win_ab.py --base-url http://127.0.0.1:8765 <路径> ...
退出码 0 = 全一致；1 = 有差异（打印前若干条，含旧/新 HTML 片段）。
注意：旧页面的 `mt` 是 px（= 行高倍数 × 16），新接口给 `mtk`（行高倍数），比对时已折算；
     旧页面 refine 行漏了 `dlg` 字段（窗口化一并修正），故 refine 模式的 dlg 只做「新值非空」提示。
"""
import argparse
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request

BASE_URL = "http://127.0.0.1:8765"
MODES = (("tr", "tr-data"), ("src", "src-data"), ("refine", "refine-data"))


def load_page(path):
    """从整页基线 HTML 里解析 READER_CFG 与三个内联 JSON 块。"""
    html = io.open(path, encoding="utf-8", newline="").read()
    cfg = {}
    m = re.search(r"window\.READER_CFG = \{(.*?)\};", html, re.S)
    if m:
        body = m.group(1)
        for key in ("work", "fname", "base", "hasTr"):
            mm = re.search(r"%s:\s*(\[[^\]]*\]|\"[^\"]*\"|[^,\n]+)" % key, body)
            if mm:
                raw = mm.group(1).strip()
                try:
                    cfg[key] = json.loads(raw)
                except Exception:
                    cfg[key] = raw.strip('"')
    blocks = {}
    for name in ("tr-data", "src-data", "refine-data"):
        mm = re.search(r'<script type="application/json" id="%s">(.*?)</script>' % name, html, re.S)
        if mm:
            blocks[name] = json.loads(mm.group(1))
    return cfg, blocks


def api_rows(work, fname, mode, base_url=BASE_URL):
    """按 200 行块把整章从区间接口拉回来（与服务端 BLOCK_MAX 一致）。"""
    out, k, api_mode = [], 0, None
    while True:
        url = "%s/api/work/%s/chapter/%s/range?from_k=%d&to_k=%d&mode=%s" % (
            base_url, urllib.parse.quote(work), urllib.parse.quote(fname), k, k + 199, mode)
        with urllib.request.urlopen(url) as r:
            j = json.loads(r.read().decode("utf-8"))
        api_mode = j["mode"]
        rows = j["lines"]
        if not rows:
            break
        out.extend(rows)
        if rows[-1]["k"] >= j["n"] - 1:
            break
        k = rows[-1]["k"] + 1
    return api_mode, out


def compare(fn, work, src_name, blocks, mode, key, base_url):
    """比对单个模式：返回 (差异列表, 说明行)。"""
    old = blocks[key]
    api_mode, new = api_rows(work, src_name, mode, base_url)
    # 旧页面 tr 模式在「本章无汉化」时下发的就是 src 行（rendered == rendered_src）
    old_cmp = blocks.get("src-data", []) if (mode == "tr" and api_mode != mode) else old
    diffs = []
    if len(old_cmp) != len(new):
        diffs.append("行数 %d ≠ %d" % (len(old_cmp), len(new)))
    for i, (a, b) in enumerate(zip(old_cmp, new)):
        if a.get("idx") != b.get("idx"):
            diffs.append("k=%d idx %s≠%s" % (i, a.get("idx"), b.get("idx")))
        if (a.get("mt") or 0) // 16 != (b.get("mtk") or 0):
            diffs.append("k=%d 楼距(行高倍数) %s≠%s" % (i, (a.get("mt") or 0) // 16, b.get("mtk")))
        if mode != "refine" and bool(a.get("dlg")) != bool(b.get("dlg")):
            diffs.append("k=%d dlg %s≠%s" % (i, a.get("dlg"), b.get("dlg")))
        if a.get("html") != b.get("html"):
            diffs.append("k=%d html 不一致\n    旧=%r\n    新=%r" % (
                i, (a.get("html") or "")[:120], (b.get("html") or "")[:120]))
        if len(diffs) > 5:
            break
    tag = "OK  " if not diffs else "FAIL"
    line = "%s %s %s[%s] 行数=%d api_mode=%s" % (tag, fn, key, mode, len(new), api_mode)
    if mode == "refine" and old and not any(r.get("dlg") for r in old):
        line += "（旧页面 refine 行无 dlg，已按修正处理）"
    return diffs, line


def main():
    ap = argparse.ArgumentParser(description="阅读器窗口化等价性 A/B：区间 API 行 HTML vs 窗口化之前的整页内联 JSON")
    ap.add_argument("paths", nargs="+", help="窗口化之前抓的整页基线 HTML（文件或目录）")
    ap.add_argument("--base-url", default=BASE_URL, help="工作台地址（默认 %s）" % BASE_URL)
    args = ap.parse_args()
    targets = []
    for p in args.paths:
        if os.path.isdir(p):
            targets += [os.path.join(p, f) for f in sorted(os.listdir(p)) if f.endswith(".html")]
        else:
            targets.append(p)
    base_url = args.base_url
    fail = 0
    for path in targets:
        cfg, blocks = load_page(path)
        if not any(k in blocks for _m, k in MODES):
            print("[skip] %s 无内联行数据（可能抓的已是窗口化页面）" % os.path.basename(path))
            continue
        work = cfg.get("work") or ""
        src_name = (cfg.get("base") or "") + ".txt"
        for mode, key in MODES:
            if key not in blocks:
                continue
            diffs, line = compare(os.path.basename(path), work, src_name, blocks, mode, key, base_url)
            print(line)
            for x in diffs[:6]:
                print("      " + x)
            fail += 1 if diffs else 0
    print("\n不一致项合计: %d" % fail)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
