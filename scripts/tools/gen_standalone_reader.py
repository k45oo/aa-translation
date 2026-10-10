#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_standalone_reader.py — 为网上已汉化资源目录生成"索引式"合集阅读器 HTML。

场景: 交付HTML/<作品>/ 下是一批网上已汉化的独立章节页(每章一个自包含 HTML,
可能附带同名的 png 渲染图)。本脚本只做"索引 + 导航": 生成一个左侧深色目录栏 +
右侧 iframe 阅读器的合集页, 点目录就地加载章节, 不复制正文内容。

用法:
  python scripts/tools/gen_standalone_reader.py --dir 交付HTML/时间停止探索者亚鲁欧 \
      --out 交付HTML/时间停止探索者亚鲁欧_汉化合集.html \
      --name 时间停止探索者亚鲁欧 \
      [--subtitle "..."]
  --probe: 只打印解析/排序结果不写文件(用于核对章节顺序)

排序规则(自然阅读序):
  1. 主章节号(第N话 / N话 / 独立数字), 如 51, 112, 143.1, 206
  2. 表侧(side=0) < 里侧(side=1)
  3. 块序号: 纯①③式(199/202-205)或"①前篇"(200)/"-③后篇"(198) → 圈号=块;
     "前篇①"(201/194/195)或纯前/中/后 → 前中后=块
  4. 屏序号(圈号, 无则0) + 尾部序号("后篇 1/2/3" 的 1/2/3)
  5. 番外/特殊篇追加在最末(固定逻辑顺序)
去重: 带" (1)" 的文件若存在同名正式版则忽略。
图片不整合: 只索引 .html 章节(companion png / 仅png章节不进合集)。
"""
import os
import re
import sys
import json
import argparse
import urllib.parse
from datetime import datetime

CIRCLED = {chr(0x2460 + i): i + 1 for i in range(20)}  # ①-⑳
CIRCLED.update({chr(0x2474 + i): i + 1 for i in range(20)})  # ⑴-⒇
CIRCLED_RE = re.compile('[①-⑳⑴-⒇]')

PART_WORDS = [('前篇', 0), ('中篇', 1), ('后篇', 2),
              ('前', 0), ('中', 1), ('后', 2), ('上', 0), ('下', 2)]

EXTRAS_ORDER = [
    '替身&角色介绍', '本篇未出场的替身',
    '番外篇 超色修学旅行', '番外篇 剧情概述+补充',
    '关于多库欧的追体验', '得到了',
]
EXTRAS_KEY = {k: i for i, k in enumerate(EXTRAS_ORDER)}


def strip_dedup(base):
    """去掉 ' (1)' 后缀用于去重判断。"""
    return re.sub(r'\s*\(1\)\s*$', '', base)


def parse_chapter_num(base):
    """返回 (主章节号 float, 是否为番外)。纯数字标题如 '时间停止探索者51' → 51。"""
    m = re.search(r'(\d+(?:\.\d+)?)\s*话', base)
    if m:
        return float(m.group(1)), False
    m = re.search(r'(\d+(?:\.\d+)?)\s*$', base)
    if m:
        return float(m.group(1)), False
    return None, True


def find_part(base):
    """返回 (block: 前0/中1/后2, 该词在字符串中的位置); 无 → (None, -1)。"""
    for word, rank in PART_WORDS:
        idx = base.find(word)
        if idx != -1:
            return rank, idx
    return None, -1


def find_circled(base):
    m = CIRCLED_RE.search(base)
    if m:
        return CIRCLED[m.group(0)], m.start()
    return 0, -1


def find_tail(base):
    """'后篇 1' / '后篇 3' 的尾部序号, 无 → 0。"""
    m = re.search(r'[前中后上]篇?\s+(\d+)\s*$', base)
    m2 = re.search(r'(\d+)\s*$', base)
    if m:
        return int(m.group(1))
    if m2 and not re.search(r'话', base):
        return int(m2.group(1))
    return 0


def sort_key(entry):
    base = entry['base']
    num, extra = parse_chapter_num(base)
    if extra:
        idx = next((i for k, i in EXTRAS_KEY.items() if k in base), 99)
        return (1, idx, 0, 0, 0, 0, base)
    side = 1 if ('里侧' in base or '【里】' in base) else 0
    part, part_pos = find_part(base)
    circled, circ_pos = find_circled(base)
    if circled and part is not None and circ_pos < part_pos:
        block, screen = (circled - 1) * 10, part  # 圈号主: "-③后篇"/"①前篇"
        tail = find_tail(base)
    elif circled and part is None:
        block, screen = (circled - 1) * 10, 0
        tail = find_tail(base)
    elif part is not None:
        block, screen = part * 10, circled  # 篇主: "前篇①"/"中篇"
        tail = find_tail(base)
    else:
        block, screen, tail = 0, 0, find_tail(base)
    return (0, num, side, block, screen, tail, base)


def collect(dirpath):
    files = os.listdir(dirpath)
    html_bases = {f[:-5] for f in files if f.lower().endswith('.html')}
    entries = []
    for base in sorted(html_bases):
        if strip_dedup(base) in html_bases and '(1)' in base:
            continue  # 有正式版, 忽略 (1)
        entries.append({'base': base, 'html': True, 'png': False})
    entries.sort(key=sort_key)
    return entries


def collect_ordered(dirpath, order_path):
    """按顺序文件收集: 顺序文件逐行指定实际文件名(可带/不带 .html),
    目录中其余文件按启发式排序追加。顺序文件里缺失的文件跳过并警告。"""
    html_bases = {f[:-5] for f in os.listdir(dirpath) if f.lower().endswith('.html')}
    entries = []
    used = set()
    missing = []
    with open(order_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.lower().endswith('.html'):
                line = line[:-5]
            if line in html_bases:
                entries.append({'base': line, 'html': True, 'png': False})
                used.add(line)
            else:
                missing.append(line)
    for base in sorted(html_bases):
        if base in used:
            continue
        if strip_dedup(base) in html_bases and '(1)' in base:
            continue
        entries.append({'base': base, 'html': True, 'png': False})
    if missing:
        print(f'[警告] 顺序文件中 {len(missing)} 个文件不存在(已跳过):', file=sys.stderr)
        for m in missing[:10]:
            print(f'  - {m}', file=sys.stderr)
    return entries


def fmt_label(base):
    """显示用的章节标签: 去掉 '时间停止探索者(亚鲁欧)' 前缀, 保留小话名。"""
    label = base
    label = re.sub(r'^时间停止探索者(?:亚鲁欧)?\s*', '', label)
    if re.search(r'^\d+(?:\.\d+)?$', label):
        label = '第' + label + '话'
    return label


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', required=True, help='章节文件所在目录(相对仓库根)')
    ap.add_argument('--out', required=True, help='输出合集 HTML 路径(相对仓库根)')
    ap.add_argument('--name', help='作品显示名(默认取目录名); 同时作为 iframe 相对路径前缀')
    ap.add_argument('--subtitle', default='网上已汉化资源整合, 点目录即读')
    ap.add_argument('--order', help='顺序文件路径(若不指定但 <dir>/章节顺序.txt 存在则自动使用)')
    ap.add_argument('--r18', help='R18 判定 json 路径(缺省自动找 <dir>/r18_verdict.json); '
                                  '无则不标红 R 角标')
    ap.add_argument('--probe', action='store_true', help='只打印排序结果不写文件')
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    dirpath = args.dir if os.path.isabs(args.dir) else os.path.join(root, args.dir)
    name = args.name or os.path.basename(dirpath.rstrip('/\\'))

    order_path = args.order
    if not order_path:
        default_order = os.path.join(dirpath, '章节顺序.txt')
        if os.path.exists(default_order):
            order_path = default_order
            print(f'使用自动发现的顺序文件: {default_order}')

    if order_path:
        if not os.path.isabs(order_path):
            order_path = os.path.join(root, order_path)
        entries = collect_ordered(dirpath, order_path)
    else:
        entries = collect(dirpath)

    if args.probe:
        print(f'{len(entries)} 个章节(仅html, 去重后), 排序如下:')
        for e in entries:
            print(f'  [{sort_key(e)[0]}] {e["base"]:45s} -> {fmt_label(e["base"])}')
        return

    outpath = args.out if os.path.isabs(args.out) else os.path.join(root, args.out)
    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M')
    rel = os.path.relpath(dirpath, os.path.dirname(outpath)).replace('\\', '/')
    # name 作为 iframe 相对路径前缀, 与 rel 对齐
    prefix = rel
    subtitle = args.subtitle + f' · 源目录 {rel}'
    # 用 rel 而不是 name 作为连接前缀, 保证与输出文件位置一致
    # R18 判定映射: dict[base] -> True/False
    r18map = None
    r18_path = args.r18
    if not r18_path:
        auto = os.path.join(dirpath, 'r18_verdict.json')
        if os.path.exists(auto):
            r18_path = auto
    if r18_path:
        r18_path = r18_path if os.path.isabs(r18_path) else os.path.join(root, r18_path)
        if os.path.exists(r18_path):
            try:
                data = json.load(open(r18_path, encoding='utf-8'))
                r18map = {(k[:-5] if k.endswith('.html') else k):
                          (v.get('r18') == 'yes' if isinstance(v, dict) else bool(v))
                          for k, v in data.items()}
                n_yes = sum(1 for v in r18map.values() if v)
                print(f'[r18] 载入 {r18_path}: {n_yes} 章标 R')
            except Exception as e:
                print(f'[r18] 载入失败: {e}')
        else:
            print(f'[r18] 未找到判定文件: {r18_path}')
    html = build_html_entries(entries, name, subtitle, prefix, generated_at, r18map)
    os.makedirs(os.path.dirname(outpath), exist_ok=True)
    with open(outpath, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'已生成: {outpath}  ({len(entries)} 章节)')


def _js_str(s):
    """JS 字符串字面量: 转义反斜杠与双引号。"""
    s = s.replace(chr(92), chr(92)*2).replace('"', '&quot;')
    return s


def build_html_entries(entries, name, subtitle, prefix, generated_at, r18map=None):
    """r18map: dict[base] -> True/False(该章含 R18)。True 时目录标签右上角加红 R。"""
    r18map = r18map or {}
    nav = []
    for i, e in enumerate(entries):
        label = fmt_label(e['base'])
        is_r18 = bool(r18map.get(e['base']))
        badge = '<sup class="r18-badge">R</sup>' if is_r18 else ''
        nav.append(
            f'<a href="#chap-{i}" data-i="{i}" class="nav-item"'
            f' data-src="{urllib.parse.quote(prefix + "/" + e["base"] + ".html")}"'
            f' data-label="{_js_str(label)}"'
            f' data-r18="{"1" if is_r18 else "0"}"'
            f' title="{_js_str(label)}{" [R18]" if is_r18 else ""}"'
            f'>{label}{badge}</a>')
    navhtml = '\n'.join(nav)
    items_js = ',\n'.join(
        f'  {{src: "{urllib.parse.quote(prefix + "/" + e["base"] + ".html")}",'
        f' label: "{_js_str(fmt_label(e["base"]))}"}}'
        for e in entries)
    doc = _TEMPLATE.replace('{{TITLE}}', name).replace('{{SUBTITLE}}', subtitle)
    doc = doc.replace('{{N}}', str(len(entries)))
    doc = doc.replace('{{NAV}}', navhtml)
    doc = doc.replace('{{ITEMS}}', items_js)
    doc = doc.replace('{{GENERATED}}', generated_at)
    # 模板中 JS/CSS 的 {{ }} 折叠为单括号(占位符替换必须在折叠之前)
    doc = doc.replace('{{', '{').replace('}}', '}')
    return doc


_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}} 汉化合集</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{height:100%}}
body{{font-family:"Microsoft YaHei","PingFang SC",sans-serif;background:#eae6df}}
#sidebar{{position:fixed;left:0;top:0;bottom:0;width:240px;background:#2c2c3a;color:#cfd2e3;
  overflow-y:auto;z-index:100;padding-bottom:24px}}
#sidebar::-webkit-scrollbar{{width:6px}}
#sidebar::-webkit-scrollbar-thumb{{background:#4f4f6a;border-radius:3px}}
.sb-title{{padding:16px 14px;border-bottom:1px solid #3f3f55}}
.sb-title h1{{font-size:17px;color:#fff;margin:0 0 6px}}
.sb-title p{{font-size:11px;color:#8a8ea8;line-height:1.6;margin:2px 0}}
.toc-label{{display:block;color:#8a8ea8;font-size:12px;padding:10px 14px 4px}}
.nav-item{{display:flex;align-items:center;gap:6px;padding:8px 14px;color:#b9bdd4;
  text-decoration:none;font-size:13px;border-left:3px solid transparent;cursor:pointer}}
.nav-item:hover{{background:#3f3f55;color:#fff}}
.nav-item.active{{background:#8a5a3a;color:#fff;border-left-color:#e8b84b}}
.r18-badge{{color:#ff4d4d;font-size:10px;font-weight:700;vertical-align:super;
  line-height:1;background:rgba(255,77,77,.12);border:1px solid rgba(255,77,77,.5);
  border-radius:3px;padding:0 2px;margin-left:1px;flex:none}}
#main{{margin-left:240px;height:100vh;display:flex;flex-direction:column}}
#toolbar{{display:flex;align-items:center;gap:10px;background:#2c2c3a;color:#eee;
  padding:8px 14px;flex:none}}
#toolbar .cur{{flex:1;font-size:13px;color:#fff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
#toolbar button,#toolbar a{{background:#3f3f55;color:#cdd6ff;border:1px solid #55557a;
  border-radius:4px;padding:5px 12px;cursor:pointer;font-size:12px;text-decoration:none}}
#toolbar button:hover,#toolbar a:hover{{background:#4f4f6a;color:#fff}}
#frame{{flex:1;border:0;width:100%;background:#fff}}
@media (max-width:820px){{
  #sidebar{{position:static;width:auto;height:auto;max-height:40vh;border-bottom:1px solid #3f3f55}}
  #main{{margin-left:0;height:calc(100vh - 40vh)}}
  #toolbar{{position:sticky;top:0;z-index:50}}
}}
</style>
</head>
<body>
<div id="sidebar">
  <div class="sb-title">
    <h1>{{TITLE}} 汉化合集</h1>
    <p>{{SUBTITLE}}<br>{{N}} 章节 · 网上已汉化资源索引 · {{GENERATED}}</p>
  </div>
  <span class="toc-label">目录(共 {{N}} 话, 按话数排序):</span>
  {{NAV}}
</div>
<div id="main">
  <div id="toolbar">
    <button id="prev" title="上一话(←)">上一话</button>
    <button id="next" title="下一话(→)">下一话</button>
    <span class="cur" id="cur"></span>
    <a class="open-ext" id="openExt" href="#" target="_blank" title="在新窗口打开当前章节独立页">新窗口</a>
  </div>
  <iframe id="frame" src="about:blank"></iframe>
</div>
<script>
var ITEMS = [
{{ITEMS}}
];
var idx = 0;
function show(i, push) {{
  if (i < 0) i = ITEMS.length - 1;
  if (i >= ITEMS.length) i = 0;
  idx = i;
  var it = ITEMS[i];
  document.getElementById('frame').src = it.src;
  document.getElementById('cur').textContent = it.label + ' · ' + (i + 1) + '/' + ITEMS.length;
  document.getElementById('openExt').href = it.src;
  var links = document.querySelectorAll('.nav-item');
  links.forEach(function (a, k) {{
    a.classList.toggle('active', k === i);
    if (k === i) a.scrollIntoView({{block: 'nearest'}});
  }});
  if (push !== false) history.pushState(null, '', '#chap-' + i);
}}
function curFromHash() {{
  var m = location.hash.match(/chap-(\\d+)/);
  return m ? parseInt(m[1]) : 0;
}}
document.querySelectorAll('.nav-item').forEach(function (a) {{
  a.addEventListener('click', function (e) {{
    show(parseInt(a.getAttribute('data-i')), true);
    e.preventDefault();
  }});
}});
document.getElementById('prev').addEventListener('click', function () {{ show(idx - 1, true); }});
document.getElementById('next').addEventListener('click', function () {{ show(idx + 1, true); }});
window.addEventListener('hashchange', function () {{ show(curFromHash(), false); }});
document.addEventListener('keydown', function (e) {{
  if (e.key === 'ArrowLeft') show(idx - 1, true);
  if (e.key === 'ArrowRight') show(idx + 1, true);
}});
(function () {{
  show(curFromHash(), false);
}})();
</script>
</body>
</html>
"""


if __name__ == '__main__':
    main()
