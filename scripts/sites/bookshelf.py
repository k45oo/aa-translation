# -*- coding: utf-8 -*-

"""yaruobookshelf.jp（やる夫のブックシェルフ）：<dt>帖头</dt><dd>AA<br></dd> 切分。

- 颜色还原 = 原站红色 span 区间与行区间重叠检测（span 可能跨 <br> 断裂）
- 加粗：原站 <b> 仅用于 【1D10:X】 → colormap 标 'font-weight:bold'
- 帖头：`NNN ： ◆作者 ： 时间 ID:xxx`（作者绿粗 #008800、ID 红粗 #ff0000）
- <dl> 常不闭合，用 <dt> 分割；<dd> 截断到第一个 </dd>
"""
import json, os, re, sys

try:
    from _base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, ID_RED_BOLD
except ImportError:
    from sites._base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, ID_RED_BOLD
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根(2026-08-18 Phase 3 动态计算)

SITE = "bookshelf"

RED_RE = re.compile(r'<span[^>]*style="[^"]*(?:red|255,\s*0,\s*0)[^"]*"[^>]*>', re.I)
HEADER_RE = re.compile(r'^(\d+\s*：\s*)(.+?)(\s*：\s*\d{4}/\d{2}/\d{2}.*?)(\s*ID:\S+.*)?$')


def extract_article(raw):
    art = raw.find('<div class="article">')
    end = raw.find('<!--▲ エントリー', art)
    if end == -1:
        end = len(raw)
    return raw[art:end]


def parse_posts(seg):
    """返回 [(帖头行, [(aa行, color_or_None)...])...]；dl 常不闭合，用 <dt> 分割"""
    posts = []
    parts = re.split(r'(?=<dt[ >])', seg)
    for part in parts:
        dt = re.search(r'<dt>(.*?)</dt>', part, re.S)
        if not dt:
            continue
        header = clean(dt.group(1)).strip()
        dd = re.search(r'<dd>(.*?)</dd>', part, re.S)
        body_html = dd.group(1) if dd else ''
        # 找出所有红色 span 的完整区间（开标签可能跨 br 与内容分离，按 </span> 配对）
        red_ranges = []
        for m in RED_RE.finditer(body_html):
            close = body_html.find('</span>', m.end())
            if close != -1:
                red_ranges.append((m.start(), close + 7))
        # 按 <br> 切行，保留行区间
        rows = []
        pos = 0
        for row_html in re.split(r'(<br\s*/?>)', body_html):
            if row_html.startswith('<br'):
                pos += len(row_html)
                continue
            row_start = pos
            pos += len(row_html)
            row_end = pos
            # 整行 span 检测（原逻辑）
            m = re.search(r'^\s*<span[^>]*style="([^"]*)"[^>]*>(.*?)</span>\s*$', row_html, re.S)
            if m and not re.search(r'<[^>]+>', m.group(2)):
                rows.append((clean(m.group(2)), m.group(1)))
            else:
                color = None
                # 区间重叠法：行与任一红色 span 区间重叠（含跨行断裂的 span）
                for rs, re_ in red_ranges:
                    if row_start < re_ and row_end > rs:
                        color = 'color: rgb(255, 0, 0);'
                        break
                if color is None:
                    # 加粗检测：行内含 <b>（原站 b 仅用于 【1D10:X】）
                    if re.search(r'<b[^>]*>', row_html):
                        color = 'font-weight:bold'
                rows.append((clean(row_html), color))
        posts.append((header, rows))
    return posts


def get_title(raw):
    m = re.search(r'<h3[^>]*><a[^>]*>(.*?)</a>', raw, re.S)
    if m:
        return clean(m.group(1)).strip()
    m = re.search(r'<title[^>]*>(.*?)</title>', raw, re.S)
    return clean(m.group(1)).strip() if m else 'unknown'


def entry_url(eid):
    return f"https://yaruobookshelf.jp/blog-entry-{eid}.html"


def extract(raw, eid=None, src_name=None):
    """-> (txt_lines, colormap, extras)"""
    eid = eid or (src_name or '').split('_')[0]
    title = get_title(raw)
    posts = parse_posts(extract_article(raw))
    txt_lines, colormap = build_txt(posts, title, entry_url(eid))
    return txt_lines, colormap, {}


def render_header(line):
    return render_common_header(line, HEADER_RE, AUTHOR_GREEN_BOLD, ID_RED_BOLD)


if __name__ == '__main__':
    RAW = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "aaManga", "さーきー", "raw", "12837_第2話.html")
    OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "aaManga", "さーきー", "aa")
    eid = re.search(r'(\d+)', os.path.basename(RAW)).group(1)
    lines, cm, _ = extract(open(RAW, encoding='utf-8', errors='replace').read(), eid, os.path.basename(RAW))
    os.makedirs(OUT, exist_ok=True)
    base = os.path.basename(RAW).replace('.html', '')
    open(os.path.join(OUT, base + '.txt'), 'w', encoding='utf-8').write('\n'.join(lines))
    open(os.path.join(OUT, base + '_colormap.json'), 'w', encoding='utf-8').write(
        json.dumps(cm, ensure_ascii=False))
    print(f'{base}: {len(lines)} lines, {len(cm)} colors')
