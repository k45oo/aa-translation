# -*- coding: utf-8 -*-
r"""「替换后还需不需要翻译」的标记渲染页（零 API）——先人工确认判据，再落地。

口径（2026-10-08 grilling 共识）：
* 判据 A：把该行被术语替换覆盖的跨度挖掉，剩余片段里还有假名/非艺术汉字 → 送模型；
  只剩 ASCII/标点/空白 → 替换完即译完（直出）。
* 直出三来源：①固定表/模板命中（含 L2 骨架、整句模板、本作 wiki 的【特性】/『潜能』行）
  ②替换后残留为空 ③本地照抄（整行无日文）。全部过 `pokemon_fixed.finalize` 的四重护栏
  （宽度 / 残留假名 / 术语一致 / art 列位），护栏拒的行如实标成"退回模型"。
* `--gaps`：把"本该由本作 wiki 直出、但 wiki 里没有"的行导出成待补清单（人工填中文）。

用法：
  # ① 标记渲染页（人工确认判据）
  python scripts/tools/gen_needs_render.py --work ポケの子 --base entry_31800,entry_31758
      [--html <path>] [--gaps <path>]
  # ② 全库缺口清单（零 API，不渲染 HTML；--limit N 先抽样）
  python scripts/tools/gen_needs_render.py --work ポケの子 --gaps-all [--gaps <path.md>] [--limit 30]
"""
import argparse
import collections
import html as H
import importlib
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "works")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import aa_translate as A          # noqa: E402
import pokemon_fixed as PF        # noqa: E402
import pre_terms as PT            # noqa: E402
from works import registry        # noqa: E402
import gen_aa_html as G           # noqa: E402

CSS = """body{margin:0;background:#f4f2ec;color:#222;font:14px/1.5 "Microsoft YaHei",sans-serif}
header{position:sticky;top:0;z-index:9;background:#2c2c3a;color:#eee;padding:8px 14px;
  display:flex;align-items:center;gap:10px;flex-wrap:wrap}
header b{font-size:15px}
header button{background:#3f3f55;color:#cdd6ff;border:1px solid #55557a;border-radius:4px;
  padding:4px 10px;cursor:pointer;font-size:12.5px}
header button.on{background:#8a5a3a;color:#fff;border-color:#a87a52;font-weight:bold}
.card{background:#fff;border:1px solid #ddd6c4;border-radius:6px;margin:10px 14px;padding:8px 12px}
.stats{display:flex;gap:14px;flex-wrap:wrap;font-size:13px}
.stats b{font-size:16px}
.direct b{color:#0a7a3d}.send b{color:#b45309}.skip b{color:#888}
pre.aa{font-family:"ＭＳ Ｐゴシック","MS PGothic","Mona","MS Gothic",SimSun,monospace;
  font-size:15px;line-height:17px;white-space:pre;background:#fffdf7;border:1px solid #d8c8a8;
  border-radius:4px;padding:8px 10px;overflow-x:auto;margin:0}
.row{display:flex;align-items:flex-start}
.gut{flex:0 0 46px;font-size:11px;text-align:center;font-family:"Microsoft YaHei",sans-serif;
  line-height:17px;color:#999}
.gut.d{background:#e8f7ec;color:#0a7a3d;font-weight:bold}
.gut.s{background:#fdf1e0;color:#b45309;font-weight:bold}
.gut.x{color:#ccc}
.row.d .aa{background:#f4fdf6}
.row.s .aa{background:#fffaf2}
.h{background:#ffe9a8;border-bottom:2px solid #e0a800}
.res{color:#b45309;background:#fff3d6;border-radius:2px}
table{border-collapse:collapse;width:calc(100% - 28px);margin:6px 14px 14px;font-size:12.5px;
  background:#fff}
th,td{border:1px solid #e2dccb;padding:3px 6px;vertical-align:top;text-align:left}
th{background:#f6f2e6}
td.jp{font-family:"ＭＳ Ｐゴシック",SimSun,monospace;max-width:420px}
.hidden{display:none}
h3{margin:14px 14px 4px;font-size:15px}
.legend{font-size:12.5px;color:#555;margin:2px 0 6px}
"""


GAP_FAM = ((re.compile(r"【特性】"), "【特性】"), (re.compile(r"『[^』]{1,30}』…+"), "『潜能/特性』"))

# 全库缺口扫描（--gaps-all）用的族表：命中即说明"这族本该直出，送模型 = 缺词"
GAP_ALL_FAM = [
    ("【特性】说明", re.compile(r"【特性】")),
    ("『潜能/特性』说明", re.compile(r"『[^』]{1,30}』…+")),
    ("・道具说明", re.compile(r"・[^…\n]{1,30}…+")),
    ("编成表", re.compile(r"^[\s\u3000]*(自陣|敵陣)：")),
    ("用招", re.compile(r"^[\s\u3000]*◎?[^「」]{1,20}の「[^」]{1,16}」！[\s@0-9/\-]*$")),
    ("指令受", re.compile(r"』を受け、自身の")),
    ("倒下", re.compile(r"は倒れた！")),
    ("皮一枚", re.compile(r"皮一枚で踏ん張った")),
    ("关键时刻", re.compile(r"の闘志に火が付いた")),
    ("受伤", re.compile(r"を負ってしまった")),
    ("经验/等级", re.compile(r"は経験値を得た！|のレベルが「")),
    ("场地行", re.compile(r"◎フィールド：《")),
    ("技xN", re.compile(r"【技x\d：")),
    ("伤害结算", re.compile(r"に「[^」]+ダメージ」！")),
]


def _gap_family(line):
    """只把「本该由本作 wiki 直出」的固定行算进待补清单（其余送模型行是正常台词）。"""
    for rx, name in GAP_FAM:
        if rx.search(line):
            return name
    return ""


def _mark(html_line, text, spans, cursor=0):
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


def _hit_spans(hits, new):
    out = []
    for h in sorted((h for h in hits if isinstance(h.get("at"), int)), key=lambda x: x["at"]):
        out.append((h["at"], h["at"] + len(h.get("zh") or "")))
    return out


def _pad(zh, target):
    try:
        t = A.disp_width(zh.rstrip("\u3000 "))
        return A.pad_to_width(zh.rstrip("\u3000 "), target) if t < target else zh
    except Exception:
        return zh


def classify(work, cfg, base, line, idx, meta, table, terms, pairs, gp):
    """→ (状态键, 标签, 来源, 显示用中文, 残留片段, 替换后文本, 命中列表)。"""
    kana, cjk, zh, residue = "", "", "", ""
    if not A.is_translatable(idx, line, meta):
        return ("skip", "—", "", "", "", "", [])
    hit = PF.match_line(line, table, terms, work=work, pairs=pairs)
    if hit:
        ok, out, why = PF.finalize(line, hit, gp)
        if ok and out:
            return ("direct", "直出", hit.get("track", ""), out, "", "", [])
        return ("send", "送模型", "固定内容命中但护栏拒:%s" % (why or "?"), "", "", "", [])
    need, residue, hits, new = PF.needs_translation(line, base=base, work=work, pairs=pairs)
    if not need:
        out = _pad(new, A.disp_width(line))
        why = "替换后残留为空" if hits else "本地照抄（整行无日文残留）"
        return ("direct", "直出", why, out, residue, new, hits)
    return ("send", "送模型", "", "", residue, new, hits)


def render_chapter(work, cfg, base, site, table, terms, gp, with_orig=True):
    lines = A.txt_read(os.path.join(cfg["aa_dir"], base + ".txt"))
    cm = {}
    cp = os.path.join(cfg["aa_dir"], base + "_colormap.json")
    if os.path.exists(cp):
        try:
            cm = json.load(open(cp, encoding="utf-8"))
        except Exception:
            cm = {}
    meta = A.load_meta_0based(os.path.join(cfg["work_dir"], "line_meta_v2"), base)
    pairs = PT.pairs_for(base, work=work)
    plan = G._blank_plan(lines)
    rows, det = [], {"direct": [], "send": [], "skip": 0}
    st = collections.Counter()
    for i, l in enumerate(lines):
        if l.startswith("#"):
            continue
        n = plan.get(i)
        if n:
            for _ in range(n):
                rows.append('<div class="row x"><span class="gut x"></span>'
                            '<span class="aa"> </span></div>')
        stt, label, why, zh, residue, new, hits = classify(work, cfg, base, l, i, meta, table,
                                                           terms, pairs, gp)
        st[stt] += 1
        if stt == "skip":
            det["skip"] += 1
        elif stt == "direct":
            det["direct"].append({"i": i + 1, "jp": l, "zh": zh, "why": why})
            st["why_" + (why or "?")] = st.get("why_" + (why or "?"), 0) + 1
            if why in ("替换后残留为空", "本地照抄（整行无日文残留）"):
                st["why_" + why] += 1
        else:
            det["send"].append({"i": i + 1, "jp": l, "new": new, "residue": residue,
                                "why": why, "hits": hits})
        shown = new if stt == "send" else (zh or l)
        hh = G.render_line(i, shown if shown else l, cm, site)
        if stt == "send" and hits:
            hh, _m = _mark(hh, new, _hit_spans(hits, new))
        cls = {"direct": "d", "send": "s"}.get(stt, "x")
        chip = {"direct": "直出", "send": "送模型"}.get(stt, "")
        tip = H.escape("第%d行 %s %s" % (i + 1, label, why or ""))
        rows.append('<div class="row %s" title="%s"><span class="gut %s">%s</span>'
                    '<span class="aa">%s</span></div>'
                    % (cls, tip, cls, chip, hh))
    title = ""
    for l in lines[:2]:
        if l.startswith("#"):
            title = l.lstrip("# ").strip()
            break
    sec = ['<section class="chapter" data-base="%s">' % H.escape(base),
           '<h3>%s</h3>' % H.escape(title or base),
           '<div class="legend">送模型 <b>%d</b> 行 ｜ 直出 <b>%d</b> 行 ｜ 不送模型 %d 行'
           % (st.get("send", 0), st.get("direct", 0), st.get("skip", 0)),
           '　直出来源：' + "、".join("%s %d" % (k[4:], v) for k, v in sorted(st.items())
                                     if k.startswith("why_")) or "（无）",
           '</div>',
           '<pre class="aa">' + "\n".join(rows) + '</pre></section>']
    return "\n".join(sec), st, det, lines


def gaps_all(work, cfg, out, quiet=False, limit=0):
    """全库缺口扫描：把所有「本该直出却因缺词回落模型」的行按族导出成待补清单（零 API）。

    为什么不复用渲染页：416 章渲染页会到几百 MB；缺口清单只需要判定结果。
    `limit>0` 只扫前 N 章（先抽样看形态再全量）。
    """
    import glob
    import time
    table = PF.load(work, cfg["work_dir"]) or {}
    terms = PF.merged_terms(work, cfg["work_dir"])
    wd = cfg["work_dir"]
    files = sorted(f for f in glob.glob(os.path.join(cfg["aa_dir"], "entry_*.txt"))
                   if not f.endswith("_台词.txt"))
    if limit:
        files = files[:limit]
    gap = collections.defaultdict(collections.Counter)
    sample = {}
    t0 = time.time()
    for k, f in enumerate(files):
        base = os.path.basename(f)[:-4]
        lines = A.txt_read(f)
        meta = A.load_meta_0based(os.path.join(wd, "line_meta_v2"), base)
        pairs = PT.pairs_for(base, work=work)
        gp = [(j, z) for j, z in A._load_glossary_pairs(base) if not j.startswith("re:")]
        for i, l in enumerate(lines):
            r = None
            for fam, rx in GAP_ALL_FAM:
                if not rx.search(l):
                    continue
                if r is None:
                    r = classify(work, cfg, base, l, i, meta, table, terms, pairs, gp)
                if r[0] == "send":
                    gap[fam][l.strip()] += 1
                    sample.setdefault((fam, l.strip()), (base, i + 1, r[4] or ""))
        if not quiet and (k + 1) % 50 == 0:
            print("  ... %d/%d 章 %.0fs" % (k + 1, len(files), time.time() - t0), flush=True)
    tot = sum(len(v) for v in gap.values())
    with open(out, "w", encoding="utf-8") as h:
        h.write("# 待补清单（%s·全库 %d 章）\n\n" % (work, len(files)))
        h.write("这些行**本该由替换轨/固定内容直出，却因缺词回落模型**。补法二选一：\n\n"
                "* **名字/道具/招式名缺失** → `doc/aa作品相关/%swiki/人工修正.json` 的 `本地译名`"
                "（`[\"日文名\",\"中文名\"]`），或 `补充术语.json` 的 `terms` / `works.%s.terms`\n"
                "* **整行说明缺失** → `doc/aa作品相关/%swiki/人工修正.json` 的 `修正`"
                "（键=日文原文行、值=中文）\n\n"
                "补完重跑 `python scripts/tools/gen_pokemon_aa_wiki.py --build`，再跑本工具"
                "（`--base <章>`）即可看到该行变直出。去重条目合计 **%d** 行。\n"
                % (work, work, work, tot))
        for fam, _rx in GAP_ALL_FAM:
            c = gap.get(fam)
            if not c:
                continue
            h.write("\n## %s（去重 %d 条，出现 %d 次）\n\n"
                    "| 次数 | 日文原文 | 中文（填这里） |\n| --- | --- | --- |\n"
                    % (fam, len(c), sum(c.values())))
            for line, cnt in c.most_common():
                h.write("| %d | `%s` |  |\n" % (cnt, line.replace("|", "\\|")))
            b, idx, res = sample[(fam, sorted(c, key=lambda x: -c[x])[0])]
            h.write("\n<sub>最高频样例：%s:%d；残留片段：`%s`</sub>\n"
                    % (b, idx, res[:80].replace("|", "\\|")))
    if not quiet:
        print("待补清单: %s（去重 %d 条；各族 %s；耗时 %.0fs）"
              % (out, tot, {k: len(v) for k, v in gap.items()}, time.time() - t0))
    return tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--base")
    ap.add_argument("--html")
    ap.add_argument("--gaps", help="待补清单输出路径（同名的 .md）")
    ap.add_argument("--gaps-all", action="store_true",
                    help="全库缺口扫描（不渲染 HTML；--base 可省）")
    ap.add_argument("--limit", type=int, default=0,
                    help="--gaps-all 时只扫前 N 章（先抽样看形态，0=全库）")
    ap.add_argument("--no-orig", action="store_true")
    a = ap.parse_args()
    cfg = registry.get(a.work)
    if a.gaps_all:
        out = a.gaps or os.path.join(cfg["work_dir"], "待补说明.md")
        if not out.endswith(".md"):
            out += ".md"
        gaps_all(a.work, cfg, out, limit=a.limit)
        return
    if not a.base:
        raise SystemExit("缺 --base（或用 --gaps-all 跑全库缺口）")
    work = a.work
    bases = [b.strip() for b in a.base.split(",") if b.strip()]
    site = importlib.import_module("sites." + cfg["site"])
    table = PF.load(work, cfg["work_dir"]) or {}
    terms = PF.merged_terms(work, cfg["work_dir"])
    secs, total, dets = [], collections.Counter(), []
    gap = collections.Counter()
    for base in bases:
        if not os.path.exists(os.path.join(cfg["aa_dir"], base + ".txt")):
            print("原文不存在，跳过：%s" % base)
            continue
        gp = [(j, z) for j, z in A._load_glossary_pairs(base) if not j.startswith("re:")]
        sec, st, det, lines = render_chapter(work, cfg, base, site, table, terms, gp, not a.no_orig)
        secs.append(sec)
        total.update(st)
        dets.append((base, det))
        for d in det["send"]:
            fam = _gap_family(d["jp"])
            if fam:
                c, _f = gap.get(d["jp"].strip(), (0, fam))
                gap[d["jp"].strip()] = (c + 1, fam)
        print("%s：送模型 %d ｜ 直出 %d（%s）｜ 不送模型 %d"
              % (base, st.get("send", 0), st.get("direct", 0),
                 "、".join("%s %d" % (k[4:], v) for k, v in sorted(st.items()) if k.startswith("why_")),
                 st.get("skip", 0)))
    if not secs:
        raise SystemExit("没有可渲染的章节")

    det_html = []
    for base, det in dets:
        if det["direct"]:
            det_html.append('<details open><summary>%s 直出行（%d）</summary><table>'
                            '<tr><th style="width:60px">行</th><th>日文原文</th><th>直出中文</th>'
                            '<th style="width:150px">来源</th></tr>' % (base, len(det["direct"])))
            for d in det["direct"]:
                det_html.append('<tr><td>%d</td><td class="jp">%s</td><td class="jp">%s</td>'
                                '<td>%s</td></tr>' % (d["i"], H.escape(d["jp"]),
                                                      H.escape(d["zh"] or ""), H.escape(d["why"] or "")))
            det_html.append("</table></details>")
        if det["send"]:
            det_html.append('<details><summary>%s 送模型行（%d）</summary><table>'
                            '<tr><th style="width:60px">行</th><th>日文原文</th><th>替换后</th>'
                            '<th>残留片段（要译的东西）</th></tr>' % (base, len(det["send"])))
            for d in det["send"]:
                det_html.append('<tr><td>%d</td><td class="jp">%s</td><td class="jp">%s</td>'
                                '<td class="jp"><span class="res">%s</span></td></tr>'
                                % (d["i"], H.escape(d["jp"]), H.escape(d["new"] or ""),
                                   H.escape(d["residue"] or "")))
            det_html.append("</table></details>")

    out = a.html or os.path.join(cfg["work_dir"], "需翻译标记_%s.html" % "_".join(bases))
    toc = "".join('<button class="ch" data-base="%s">%s</button>' % (H.escape(b), H.escape(b))
                  for b in bases)
    doc = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>%(title)s 需翻译标记</title><style>%(css)s</style></head><body>
<header><b>%(title)s·替换后「需不需要翻译」标记</b>
<span>%(bases)s</span><span style="flex:1"></span>%(toc)s
<button id="f-all" class="on">全部</button><button id="f-d">只看直出</button><button id="f-s">只看送模型</button>
</header>
<div class="card stats">
  <span class="send">送模型 <b>%(send)d</b></span>
  <span class="direct">直出 <b>%(direct)d</b></span>
  <span class="skip">不送模型 <b>%(skip)d</b></span>
  <span>直出来源：%(whys)s</span></div>
%(secs)s
<h3>明细</h3>
%(det)s
<script>
const secs=[...document.querySelectorAll('.chapter')];
document.querySelectorAll('header .ch').forEach(b=>b.addEventListener('click',()=>{
  secs.forEach(s=>s.classList.toggle('hidden',s.dataset.base!==b.dataset.base));}));
function filt(m){document.querySelectorAll('.row.s').forEach(r=>r.classList.toggle('hidden',m==='d'));
  document.querySelectorAll('.row.d').forEach(r=>r.classList.toggle('hidden',m==='s'));}
document.getElementById('f-all').onclick=()=>filt('a');
document.getElementById('f-d').onclick=()=>filt('d');
document.getElementById('f-s').onclick=()=>filt('s');
if(secs.length>1)secs.slice(1).forEach(s=>s.classList.add('hidden'));
</script></body></html>
""" % {"title": H.escape(cfg.get("title") or a.work), "css": CSS, "bases": H.escape(" ".join(bases)),
       "toc": toc, "secs": "\n".join(secs), "det": "\n".join(det_html),
       "send": total.get("send", 0), "direct": total.get("direct", 0),
       "skip": total.get("skip", 0),
       "whys": H.escape("、".join("%s %d" % (k[4:], v) for k, v in sorted(total.items())
                                  if k.startswith("why_"))) or "（无）"}
    with open(out, "w", encoding="utf-8") as f:
        f.write(doc)
    print("渲染: %s" % out)
    if a.gaps:
        p = a.gaps
        if not p.endswith(".md"):
            p += ".md"
        with open(p, "w", encoding="utf-8") as f:
            f.write("# 待补说明（%s）\n\n"
                    "这里只列**本该由本作 wiki 直出、但 wiki 里没有**的固定行"
                    "（`【特性】名…说明` / `『潜能名』…　　　说明`）——其余「送模型行」是正常台词，"
                    "不需要补。\n\n填中文后写进 `doc/aa作品相关/%swiki/人工修正.json` 的 `修正` 段"
                    "（键=日文原文行、值=中文），重跑 `scripts/tools/gen_needs_render.py` 即变「直出」。\n\n"
                    "| 出现次数 | 族 | 日文原文 | 中文（填这里） |\n| --- | --- | --- | --- |\n"
                    % (a.work, a.work))
            for jp, (c, fam) in sorted(gap.items(), key=lambda kv: -kv[1][0]):
                f.write("| %d | %s | %s |  |\n"
                        % (c, fam, jp.replace("|", "\\|")))
        print("待补清单: %s（%d 行）" % (p, len(gap)))


if __name__ == "__main__":
    main()
