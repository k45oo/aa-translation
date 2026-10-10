# -*- coding: utf-8 -*-
r"""翻译前替换验收报告（HTML，零 API）——宝可梦链"翻译前替换"人工验收用。

产物 = 一个自包含 HTML（无外部依赖），四块：

  A 术语替换明细：本章将替换哪些词（wiki / 作品表各多少、命中次数、来源）
  B 逐行对照：原文 → 替换后（送模型的那份文本），附现交付译文与判定
    （正确 / 未译 / 疑似乱翻）——这就是"翻前替换效果"的人工验收面
  C 固定表预填：现在正在跑的整行替换（copy / whole / rule / struct），原文 → 表译文
  D 术语质量：每个词在已交付译文里的落地情况（正确 / 未译 / 疑似乱翻 计数 + 样例）

用法：
  python scripts/tools/gen_pre_terms_report.py --work <id> --base entry_8342 [--html <path>]
  python scripts/tools/gen_pre_terms_report.py --work <id> --base entry_8342 --no-wiki  # 只作品表
"""
import argparse
import collections
import html
import io
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "works")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import aa_translate as A                     # noqa: E402
import pre_terms as PT                       # noqa: E402

CSS = """
:root { --bg:#f7f7f5; --fg:#1c1c1e; --mut:#6b7280; --line:#e3e3e0; --hit:#fff3c4;
        --ok:#0a7a3d; --warn:#b45309; --bad:#b91c1c; --card:#fff; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
       font:13px/1.5 "Segoe UI","Yu Gothic UI",Meiryo,sans-serif; }
header { position:sticky; top:0; z-index:5; background:var(--card); border-bottom:1px solid var(--line);
         padding:10px 16px; }
h1 { font-size:16px; margin:0 0 4px; }
h2 { font-size:14px; margin:22px 16px 8px; padding-bottom:4px; border-bottom:1px solid var(--line); }
.mut { color:var(--mut); }
.cards { display:flex; flex-wrap:wrap; gap:8px; margin-top:8px; }
.card { background:#fff; border:1px solid var(--line); border-radius:8px; padding:6px 10px; min-width:104px; }
.card b { display:block; font-size:17px; }
.card span { font-size:11px; color:var(--mut); }
table { border-collapse:collapse; width:calc(100% - 32px); margin:0 16px 8px; background:#fff;
        border:1px solid var(--line); }
th, td { border-bottom:1px solid var(--line); padding:4px 6px; vertical-align:top; text-align:left; }
th { background:#fafafa; position:sticky; top:0; font-weight:600; font-size:12px; }
td.num, th.num { text-align:right; white-space:nowrap; }
.mono { font-family:"Consolas","MS Gothic",monospace; white-space:pre-wrap; word-break:break-all; }
.hit { background:var(--hit); border-bottom:1px solid #e0c34a; font-weight:600; }
.ok { color:var(--ok); } .warn { color:var(--warn); } .bad { color:var(--bad); }
.pill { display:inline-block; padding:0 6px; border-radius:9px; font-size:11px; border:1px solid var(--line);
        background:#fafafa; margin-right:4px; }
.track { font-size:11px; color:var(--mut); }
#q { padding:4px 8px; width:260px; border:1px solid var(--line); border-radius:6px; }
footer { margin:24px 16px; color:var(--mut); font-size:11px; }
"""

JS = """
function flt(){const q=document.getElementById('q').value.trim();
  document.querySelectorAll('tr[data-k]').forEach(tr=>{
    tr.style.display = (!q || tr.dataset.k.includes(q)) ? '' : 'none';});}
"""


def esc(s):
    return html.escape(s if s is not None else "")


def mark(src, hits, new):
    """在"替换后"文本里把替换位高亮（按 hit 的 at 列）。"""
    if not hits:
        return esc(new)
    out, pos = [], 0
    for h in hits:
        at = h.get("at", 0)
        if at < pos:
            at = pos
        out.append(esc(new[pos:at]))
        out.append('<span class="hit">%s</span>' % esc(h["zh"]))
        pos = at + len(h["zh"])
    out.append(esc(new[pos:]))
    return "".join(out)


def verdict(text_lines, i, zh, jp):
    """现交付译文对该词的落地判定。"""
    if i >= len(text_lines):
        return "-", ""
    row = text_lines[i]
    if zh and zh in row:
        return "正确", "ok"
    if jp and jp in row:
        return "未译", "warn"
    return "疑似乱翻", "bad"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--base", required=True, help="章节 base，如 entry_8342")
    ap.add_argument("--html", help="输出 HTML 路径（默认 <work_dir>/翻前替换验收_<base>.html）")
    ap.add_argument("--no-wiki", action="store_true", help="只用作品术语表（对照）")
    ap.add_argument("--limit", type=int, default=0, help="每块最多列多少行（0=全部）")
    a = ap.parse_args()

    import registry
    cfg = registry.get(a.work)
    aa_dir, out_dir, work_dir = cfg["aa_dir"], cfg["out_dir"], cfg["work_dir"]
    A.WORK_ID = a.work

    src = PT.file_lines(os.path.join(aa_dir, a.base + ".txt"))
    if not src:
        print("原文不存在: %s" % os.path.join(aa_dir, a.base + ".txt"))
        return 1
    han = PT.file_lines(os.path.join(out_dir, a.base + "_汉化.txt"))
    meta = A.load_meta_0based(os.path.join(work_dir, "line_meta_v2"), a.base)

    def keep(i, l):
        try:
            return A.is_translatable(i, l, meta)
        except Exception:
            return False

    pairs = PT.build_pairs(a.work, a.base, wiki=not a.no_wiki)
    dirty = pairs[0]["skipped"]["dirty_zh"] if pairs else 0
    rows = PT.scan_lines(src, pairs, keep=keep)
    sent_lines = [i for i, l in enumerate(src) if keep(i, l)]

    # 词统计 + 落地质量
    per = collections.defaultdict(lambda: {"n": 0, "ok": 0, "miss": 0, "bad": 0, "ex": []})
    order = []
    for i, _l, _new, hits in rows:
        for h in hits:
            k = (h["jp"], h["zh"], h["src"], h["cat"])
            if k not in per:
                order.append(k)
            per[k]["n"] += 1
            v, _cls = verdict(han, i, h["zh"], h["jp"])
            per[k]["ok" if v == "正确" else ("miss" if v == "未译" else "bad")] += 1
            if v != "正确" and len(per[k]["ex"]) < 3:
                per[k]["ex"].append((i + 1, _l, han[i] if i < len(han) else ""))

    # 固定表预填
    hits_path = os.path.join(work_dir, "fixed_hits", a.base + ".json")
    fixed = {}
    if os.path.exists(hits_path):
        try:
            fixed = json.load(io.open(hits_path, encoding="utf-8")).get("hits") or {}
        except (OSError, ValueError):
            fixed = {}
    track_cnt = collections.Counter(v.get("track", "") for v in fixed.values())
    src_cnt = collections.Counter(v.get("src", "") for v in fixed.values())

    out_path = a.html or os.path.join(work_dir, "翻前替换验收_%s.html" % a.base)
    lim = a.limit

    def trunc(t, n=110):
        t = t.replace("\u3000", "　")
        return t if len(t) <= n else t[:n] + "…"

    with io.open(out_path, "w", encoding="utf-8", newline="\n") as f:
        w = f.write
        w("<!DOCTYPE html>\n<html lang=\"zh\"><head><meta charset=\"utf-8\">\n")
        w("<title>翻译前替换验收 · %s · %s</title>\n<style>%s</style></head><body>\n"
          % (esc(a.work), esc(a.base), CSS))
        w("<header><h1>翻译前替换 · 人工验收 — %s / %s</h1>\n" % (esc(a.work), esc(a.base)))
        w('<div class="mut">生成 %s ｜ 词源：wiki AA 术语 %d + wiki 官方译名 %d + 作品表 %d'
          '（wiki %s）｜ 本章可用词 %d（脏译名跳过 %d）</div>\n'
          % (time.strftime("%Y-%m-%d %H:%M"), len([1 for x in PT.wiki_pairs() if x[2] == "wiki-术语"]),
             len([1 for x in PT.wiki_pairs() if x[2] == "wiki-官方"]), len(PT.work_pairs(a.work)),
             "关（--no-wiki）" if a.no_wiki else "开", len(pairs), dirty))
        w('<div class="cards">'
          '<div class="card"><b>%d</b><span>原文行</span></div>'
          '<div class="card"><b>%d</b><span>送模型行</span></div>'
          '<div class="card"><b>%d</b><span>术语命中行</span></div>'
          '<div class="card"><b>%d</b><span>替换次数</span></div>'
          '<div class="card"><b>%d</b><span>固定表预填行</span></div>'
          '<div class="card"><b>%d</b><span>命中词数</span></div>'
          '<span style="flex:1"></span>'
          '<input id="q" placeholder="过滤：词 / 行号 / 来源" oninput="flt()"></div>\n'
          % (len(src), len(sent_lines), len(rows), sum(len(r[3]) for r in rows), len(fixed), len(per)))
        w("</header>\n")

        # ---- A 术语明细 ----
        w("<h2>A · 本章将替换的词（翻译前强制替换对）</h2>\n")
        w('<table><thead><tr><th>#</th><th>原文词</th><th>替换为</th><th>来源</th><th>类别</th>'
          '<th class="num">命中</th><th class="num">现译文正确</th><th class="num">未译</th>'
          '<th class="num">疑似乱翻</th><th>样例（现交付译文）</th></tr></thead><tbody>\n')
        for n, (jp, zh, srcn, cat) in enumerate(
                sorted(order, key=lambda k: (-per[k]["n"], k[0])), 1):
            s = per[(jp, zh, srcn, cat)]
            ex = s["ex"][0] if s["ex"] else None
            w('<tr data-k="%s %s %s">' % (esc(jp), esc(zh), esc(srcn)))
            w('<td class="num">%d</td><td class="mono">%s</td><td class="mono">%s</td>'
              '<td class="track">%s</td><td class="track">%s</td>'
              '<td class="num">%d</td><td class="num ok">%d</td><td class="num warn">%d</td>'
              '<td class="num bad">%d</td>'
              % (n, esc(jp), esc(zh), esc(srcn), esc(cat), s["n"], s["ok"], s["miss"], s["bad"]))
            w('<td class="mono mut">%s</td></tr>\n'
              % (esc(trunc(ex[2], 90)) if ex else "—"))
        w("</tbody></table>\n")

        # ---- B 逐行对照 ----
        w("<h2>B · 逐行对照：原文 → 替换后（送模型的那份文本）</h2>\n")
        w('<table><thead><tr><th class="num">行</th><th>原文</th><th>替换后（送模型）</th>'
          '<th>现交付译文</th><th>判定</th></tr></thead><tbody>\n')
        shown = 0
        for i, l, new, hits in rows:
            if lim and shown >= lim:
                break
            shown += 1
            vs = sorted({verdict(han, i, h["zh"], h["jp"])[0] for h in hits})
            v_txt = " / ".join(vs)
            cls = "ok" if vs == ["正确"] else ("bad" if "疑似乱翻" in vs else "warn")
            w('<tr data-k="%s">' % esc(" ".join(h["jp"] for h in hits)))
            w('<td class="num">%d</td><td class="mono">%s</td><td class="mono">%s</td>'
              '<td class="mono mut">%s</td><td class="%s">%s</td></tr>\n'
              % (i + 1, esc(trunc(l, 96)), mark(l, hits, new),
                 esc(trunc(han[i] if i < len(han) else "", 96)), cls, esc(v_txt)))
        w("</tbody></table>\n")

        # ---- C 固定表预填 ----
        w("<h2>C · 固定表预填（现在就在跑的整行替换）</h2>\n")
        w('<div class="mut" style="margin:0 16px 6px">命中 %d 行：%s ｜ 条目来源：%s</div>\n'
          % (len(fixed), " / ".join("%s %d" % (k, v) for k, v in track_cnt.most_common()),
             " / ".join("%s %d" % (k, v) for k, v in src_cnt.most_common())))
        w('<table><thead><tr><th class="num">行</th><th>轨</th><th>条目来源</th><th>原文</th>'
          '<th>表译文（直接落盘，不送模型）</th></tr></thead><tbody>\n')
        shown = 0
        for k in sorted(fixed, key=lambda x: int(x)):
            if lim and shown >= lim:
                break
            shown += 1
            v = fixed[k]
            i = int(k)
            w('<tr data-k="%s %s"><td class="num">%d</td><td class="track">%s</td>'
              '<td class="track">%s</td><td class="mono">%s</td><td class="mono">%s</td></tr>\n'
              % (esc(v.get("track", "")), esc(v.get("src", "")), i + 1, esc(v.get("track", "")),
                 esc(v.get("src", "")), esc(trunc(src[i], 96) if i < len(src) else ""),
                 esc(trunc(v.get("zh", ""), 96))))
        w("</tbody></table>\n")

        # ---- D 汇总 ----
        bad_terms = [(k, s) for k, s in per.items() if s["bad"] or s["miss"]]
        w("<h2>D · 术语落地质量（现交付译文）</h2>\n")
        w('<div class="mut" style="margin:0 16px 6px">总 %d 词 / %d 次替换：正确 %d ｜ 未译 %d ｜ '
          '疑似乱翻 %d（按次计）</div>\n'
          % (len(per), sum(s["n"] for s in per.values()),
             sum(s["ok"] for s in per.values()), sum(s["miss"] for s in per.values()),
             sum(s["bad"] for s in per.values())))
        w('<table><thead><tr><th>原文词</th><th>标准译名</th><th>来源</th><th class="num">命中</th>'
          '<th class="num">未译</th><th class="num">疑似乱翻</th><th>样例（行号 · 原文 · 现译文）</th>'
          "</tr></thead><tbody>\n")
        for (jp, zh, srcn, cat), s in sorted(bad_terms, key=lambda kv: -(kv[1]["miss"] + kv[1]["bad"])):
            ex = " ｜ ".join("L%d %s ⇒ %s" % (n, trunc(x, 46), trunc(y, 46))
                            for n, x, y in s["ex"][:2])
            w('<tr data-k="%s"><td class="mono">%s</td><td class="mono">%s</td>'
              '<td class="track">%s</td><td class="num">%d</td><td class="num warn">%d</td>'
              '<td class="num bad">%d</td><td class="mono mut">%s</td></tr>\n'
              % (esc(jp), esc(jp), esc(zh), esc(srcn), s["n"], s["miss"], s["bad"], esc(ex)))
        w("</tbody></table>\n")

        w("<footer>路径：%s ｜ 零 API 生成；判定口径：现译文含标准译名=正确 / 含日文原文=未译 / "
          "两者都无=疑似乱翻（需人工看）。</footer>\n" % esc(out_path))
        w("<script>%s</script>\n</body></html>\n" % JS)

    print("报告: %s" % out_path)
    print("术语命中 %d 行 / %d 次；可用词 %d（脏译名跳过 %d）；固定表预填 %d 行"
          % (len(rows), sum(len(r[3]) for r in rows), len(pairs), dirty, len(fixed)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
