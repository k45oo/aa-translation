# -*- coding: utf-8 -*-
r"""翻译前替换的**章节渲染**（HTML，零 API）——把"送模型的那份文本"渲染成交付页同款 AA 版式。

与 `gen_pre_terms_report.py` 的区别：那个给的是**表格报告**（词表/逐行对照/落地质量），
这个给的是**章节本体**——同一套 `common/gen_aa_html.py` 渲染语义（帖头原站颜色 / colormap /
【1D10】加粗 / MS Pゴシック 等宽），替换处黄底标出，供人工"看效果"决定改不改。

口径：
* 只有 **text/mixed 行**的替换才真正生效（`aa_translate` 只把这些行送模型）；
  帖头(header)/艺术行(art)命中的替换**不会生效**，本页照原文渲染并在图例里单独计数。
* 替换后文本只用于送模型，**不保证与原文等宽**（日文名→中文名宽度会变），故替换行右侧可能错位，
  这是预期现象，不代表艺术列位坏了。

用法：
  python scripts/tools/gen_pre_terms_render.py --work pokeko --base entry_31800,entry_31758
  ... [--html <path>]          默认 <work_dir>/替换渲染_<base…>.html
  ... [--no-orig]              不内嵌原文对照（只出替换后）
"""
import argparse
import html as H
import importlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "works")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pre_terms as PT                     # noqa: E402
from works import registry                 # noqa: E402
import gen_aa_html as G                    # noqa: E402

CSS = """body{margin:0;background:#f5f2ea;color:#222;
  font-family:"Microsoft YaHei","Segoe UI",sans-serif;font-size:14px}
header{position:sticky;top:0;z-index:5;background:#2c2c3a;color:#eee;padding:8px 14px;
  display:flex;align-items:center;gap:12px;flex-wrap:wrap}
header b{font-size:15px}
header .sp{flex:1 1 auto}
header button{background:#3f3f55;color:#cdd6ff;border:1px solid #55557a;border-radius:4px;
  padding:5px 12px;cursor:pointer;font-size:13px}
header button.on{background:#8a5a3a;color:#fff;border-color:#a87a52;font-weight:bold}
main{padding:12px 16px 64px}
.chapter{margin:0 0 42px}
.chapter h2{margin:8px 0 4px;font-size:16px}
.src-link{color:#5a7ab8;font-size:12.5px;margin:0 0 6px}
.legend{font-size:12.5px;color:#555;margin:6px 0;line-height:1.7}
.legend code{background:#fff;border:1px solid #e4dac2;border-radius:3px;padding:0 3px;
  font-family:Consolas,monospace;font-size:12px}
.legend .off{color:#a06a2a}
pre.aa{font-family:"ＭＳ Ｐゴシック","MS PGothic","Mona","MS Gothic","SimSun",monospace;
  font-size:15px;line-height:17px;white-space:pre;overflow-x:auto;background:#fffdf7;
  border:1px solid #d8c8a8;border-radius:4px;padding:10px 14px}
pre.aa span.h{background:#ffe9a8;border-bottom:2px solid #e0a800}
.hidden{display:none}
"""


def _read(url):
    return PT.file_lines(url)


def _meta_kinds(work_dir, base):
    """line_meta_v2/<base>.json → {行号: kind}（只判「text/mixed 才送模型」，缺 meta 全按 text）。

    不引 aa_web_app（CLI 工具保持零 web 依赖）：单章 0.4MB 的 meta 直接读即可。
    """
    p = os.path.join(work_dir, "line_meta_v2", base + ".json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f) or {}
    except Exception:
        return {}
    return {str(k): (v.get("kind") if isinstance(v, dict) else None) for k, v in d.items()}


def _colormap(aa_dir, base):
    p = os.path.join(aa_dir, base + "_colormap.json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _src_url(lines, base, cfg):
    for l in lines[:6]:
        if l.startswith("#") and "原帖地址" in l:
            return l.split("http", 1)[-1].strip() and "http" + l.split("http", 1)[-1].strip()
    try:
        from works import paths
        eid = base.split("_")[-1]
        u = G.src_url_of(base + ".txt", cfg, eid)
        return u or ""
    except Exception:
        return ""


def _mark(html_line, text, spans, cursor=0):
    """在渲染后的行 HTML 里给 span 段包 <span class="h">：按转义后子串顺序定位。

    render_line 可能已插入颜色/加粗 span——命中段跨 span 时找不到，跳过（返回未高亮计数）。
    """
    miss = 0
    for a, b in spans:
        piece = H.escape(text[a:b])
        if not piece:
            continue
        j = html_line.find(piece, cursor)
        if j < 0:
            j = html_line.find(piece)
        if j < 0:
            miss += 1
            continue
        html_line = (html_line[:j] + '<span class="h">' + piece + '</span>'
                     + html_line[j + len(piece):])
        cursor = j + len(piece) + 32
    return html_line, miss


def render_chapter(work, cfg, base, site, kinds, with_orig=True):
    """返回 (章节 HTML, 统计 dict)。kinds 见 _meta_kinds（text/mixed 才是送模型行）。"""
    lines = _read(os.path.join(cfg["aa_dir"], base + ".txt"))
    cm = _colormap(cfg["aa_dir"], base)
    plan = G._blank_plan(lines)
    stat = {"lines": 0, "terms": 0, "rows": 0, "off_terms": 0, "off_rows": 0, "miss_hl": 0,
            "pairs": {}}
    rep_html, orig_html = [], []
    for i, l in enumerate(lines):
        if l.startswith("#"):
            continue
        n = plan.get(i)
        if n:
            rep_html.extend([""] * n)
            orig_html.extend([""] * n)
        orig_html.append(G.render_line(i, l, cm, site))
        new, hits = PT.apply_hits(l, base=base, work=work)
        kind = kinds.get(str(i + 1)) or "text"          # 缺 meta → 按送模型行处理（保守）
        if hits and kind in ("text", "mixed"):
            hh, miss = _mark(G.render_line(i, new, cm, site), new,
                             sorted((h["at"], h["at"] + len(h["zh"])) for h in hits
                                    if isinstance(h.get("at"), int)))
            rep_html.append(hh)
            stat["lines"] += 1
            stat["terms"] += len(hits)
            stat["rows"] += 1 if l.strip() else 0
            stat["miss_hl"] += miss
            for h in hits:
                k = (h["jp"], h["zh"])
                stat["pairs"][k] = stat["pairs"].get(k, 0) + 1
        else:
            rep_html.append(G.render_line(i, l, cm, site))
            if hits:
                stat["off_terms"] += len(hits)
                stat["off_rows"] += 1
    title = ""
    for l in lines[:2]:
        if l.startswith("#"):
            title = l.lstrip("# ").strip()
            break
    url = _src_url(lines, base, cfg)
    top = [(k, v) for k, v in sorted(stat["pairs"].items(), key=lambda x: -x[1])]
    words = "、".join("%s→%s×%d" % (k[0], k[1], v) for k, v in top[:26]) or "（无）"
    url_html = ('<p class="src-link">原帖地址：<a href="%s" target="_blank">%s</a></p>'
                % (H.escape(url), H.escape(url))) if url else ""
    parts = [f'<section class="chapter" data-base="{H.escape(base)}">',
             f'<h2>{H.escape(title or base)}</h2>', url_html,
             '<p class="legend">'
             f'送模型行（text/mixed）替换：<b>{stat["lines"]} 行 / {stat["terms"]} 处</b>'
             + (f'，其中 {stat["miss_hl"]} 处未能高亮（命中段跨颜色/加粗 span）'
                if stat["miss_hl"] else "")
             + f'<br>词表命中：{H.escape(words)}'
             + (f'<br><span class="off">帖头/艺术行命中 {stat["off_terms"]} 处（不送模型 → 不生效，'
                f'本页照原文渲染）</span>' if stat["off_terms"] else "")
             + '</p>']
    if with_orig:
        parts.append('<pre class="aa orig hidden">' + "\n".join(orig_html) + "</pre>")
    parts.append('<pre class="aa rep">' + "\n".join(rep_html) + "</pre>")
    parts.append("</section>")
    return "\n".join(parts), stat


def main():
    ap = argparse.ArgumentParser(description="翻译前替换的章节渲染（零 API）")
    ap.add_argument("--work", required=True)
    ap.add_argument("--base", required=True, help="章节 base，逗号分隔可多章")
    ap.add_argument("--html", help="输出路径（默认 <work_dir>/替换渲染_<base…>.html）")
    ap.add_argument("--no-orig", action="store_true", help="不内嵌原文对照")
    a = ap.parse_args()

    cfg = registry.get(a.work)
    bases = [b.strip() for b in a.base.split(",") if b.strip()]
    sys.path.insert(0, _SCRIPTS)
    site = importlib.import_module("sites." + cfg["site"])

    secs, stats = [], []
    for base in bases:
        if not os.path.exists(os.path.join(cfg["aa_dir"], base + ".txt")):
            print("原文不存在，跳过：%s" % base)
            continue
        kinds = _meta_kinds(cfg["work_dir"], base)
        sec, st = render_chapter(a.work, cfg, base, site, kinds, not a.no_orig)
        secs.append(sec)
        stats.append((base, st))
        print("%s：替换生效 %d 行 / %d 处；未生效 %d 处%s"
              % (base, st["lines"], st["terms"], st["off_terms"],
                 ("；未高亮 %d" % st["miss_hl"]) if st["miss_hl"] else ""))

    if not secs:
        raise SystemExit("没有可渲染的章节")

    out = a.html or os.path.join(cfg["work_dir"], "替换渲染_%s.html" % "_".join(bases))
    toc = "".join('<button type="button" class="ch" data-base="%s">%s</button>'
                  % (H.escape(b), H.escape(b)) for b in bases)
    doc = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{H.escape(cfg.get('title') or a.work)} 翻译前替换渲染</title>
<style>{CSS}</style></head><body>
<header>
  <b>{H.escape(cfg.get('title') or a.work)}·翻译前替换渲染</b>
  <span>{H.escape(' '.join(bases))}</span>
  <span class="sp"></span>
  <span>{toc}</span>
  <button type="button" id="m-rep" class="on">替换后</button>
  <button type="button" id="m-orig">原文</button>
</header>
<main>{''.join(secs)}</main>
<script>
const secs=[...document.querySelectorAll('.chapter')];
document.querySelectorAll('header .ch').forEach(b=>b.addEventListener('click',()=>{{
  secs.forEach(s=>s.classList.toggle('hidden',s.dataset.base!==b.dataset.base));}}));
function mode(rep){{document.getElementById('m-rep').classList.toggle('on',rep);
  document.getElementById('m-orig').classList.toggle('on',!rep);
  document.querySelectorAll('pre.aa.rep').forEach(p=>p.classList.toggle('hidden',!rep));
  document.querySelectorAll('pre.aa.orig').forEach(p=>p.classList.toggle('hidden',rep));}}
document.getElementById('m-rep').addEventListener('click',()=>mode(true));
document.getElementById('m-orig').addEventListener('click',()=>mode(false));
if(secs.length>1)secs.slice(1).forEach(s=>s.classList.add('hidden'));
</script></body></html>
"""
    with open(out, "w", encoding="utf-8") as f:
        f.write(doc)
    print("渲染: %s" % out)


if __name__ == "__main__":
    main()
