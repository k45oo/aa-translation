# -*- coding: utf-8 -*-

"""yaruok.blog.fc2.com（FC2 博客，やる夫まとめ）：entry_body/entry_more 两段 <dl>。

- 帖头：`NNN ： ◆作者 ： 时间 ID:xxx`（作者绿粗 #008800、ID 蓝粗 #0000ff）
- 骰子红 rgb(255,0,0) 区间重叠检测；【1D10:X】<b> 加粗 → colormap
- 评论区 <dt class="comment_cut"> 是原スレ名無し帖（FC2 自动折叠），必须保留
- 相关文章 <dt class="relate_dt"> 截断
- 帖头用 rebuild_header 重建（span 片段解析，作者/ID 样式提取）
"""
import json, os, re, sys

try:
    from _base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, AUTHOR_GREEN, ID_BLUE_BOLD, ID_BLACK
except ImportError:
    from sites._base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, AUTHOR_GREEN, ID_BLUE_BOLD, ID_BLACK
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根(2026-08-18 Phase 3 动态计算)

SITE = "fc2"

RED_RE = re.compile(r'<span[^>]*style="[^"]*(?:red|255,\s*0,\s*0)[^"]*"[^>]*>', re.I)
HEADER_RE = re.compile(r'^(\d+\s*：\s*)(.+?)(\s*：\s*\d{4}/\d{2}/\d{2}.*?)(\s*ID:\S+.*)?$')


def extract_article(raw):
    i = raw.find('<div class="entry_body">')
    if i == -1:
        i = raw.find('<div class="entry_more">')
    j = raw.find('<dt class="relate_dt"', i)
    if j == -1:
        j = raw.find('関連記事', i)
    return raw[i:j]


def rebuild_header(dt_html):
    """重建帖头行；返回 header_text（样式不落 txt，仅 HTML 渲染时用）"""
    parts = []  # (text, style_or_None)
    pos = 0
    for m in re.finditer(r'<span[^>]*style="([^"]*)"[^>]*>(.*?)</span>', dt_html, re.S):
        pre = dt_html[pos:m.start()]
        pre = re.sub(r'<[^>]+>', '', pre)
        if pre:
            parts.append((pre, None))
        style = m.group(1)
        txt = re.sub(r'<[^>]+>', '', m.group(2))
        parts.append((txt, style))
        pos = m.end()
    tail = re.sub(r'<[^>]+>', '', dt_html[pos:])
    if tail:
        parts.append((tail, None))
    header = ''.join(t for t, _ in parts)
    header = __import__('html').unescape(header).replace('\u00a0', ' ')
    return header


def parse_posts(seg):
    """返回 [(帖头行, [(aa行, color_or_None)...])...]"""
    posts = []
    parts = re.split(r'(?=<dt[ >])', seg)
    for part in parts:
        if 'relate_dt' in part[:80]:
            continue
        dt = re.search(r'<dt[^>]*>(.*?)</dt>', part, re.S)
        if not dt:
            continue
        header = rebuild_header(dt.group(1)).strip()
        if not header:
            continue
        dd = re.search(r'<dd[^>]*>(.*?)</dd>', part, re.S)
        body_html = dd.group(1) if dd else ''
        red_ranges = []
        for m in RED_RE.finditer(body_html):
            close = body_html.find('</span>', m.end())
            if close != -1:
                red_ranges.append((m.start(), close + 7))
        rows = []
        pos = 0
        for row_html in re.split(r'(<br\s*/?>)', body_html):
            if row_html.startswith('<br'):
                pos += len(row_html)
                continue
            row_start = pos
            pos += len(row_html)
            row_end = pos
            m = re.search(r'^\s*<span[^>]*style="([^"]*)"[^>]*>(.*?)</span>\s*$', row_html, re.S)
            if m and not re.search(r'<[^>]+>', m.group(2)):
                rows.append((clean(m.group(2)), m.group(1)))
            else:
                color = None
                for rs, re_ in red_ranges:
                    if row_start < re_ and row_end > rs:
                        color = 'color: rgb(255, 0, 0);'
                        break
                if color is None:
                    if re.search(r'<b[^>]*>', row_html):
                        color = 'font-weight:bold'
                rows.append((clean(row_html), color))
        posts.append((header, rows))
    return posts


def get_title_eid(raw):
    m = re.search(r'<h1 class="entry_title"[^>]*id="entry(\d+)"[^>]*>(.*?)</h1>', raw, re.S)
    if m:
        return clean(m.group(2)).strip(), m.group(1)
    m = re.search(r'<h1[^>]*>(.*?)</h1>', raw, re.S)
    return (clean(m.group(1)).strip() if m else 'unknown'), ''


def entry_url(eid):
    return f"https://yaruok.blog.fc2.com/blog-entry-{eid}.html"


def extract(raw, eid=None, src_name=None):
    """-> (txt_lines, colormap, extras)"""
    title, eid2 = get_title_eid(raw)
    if not eid:
        eid = eid2 or (src_name or '').replace('entry_', '').replace('.html', '')
    posts = parse_posts(extract_article(raw))
    txt_lines, colormap = build_txt(posts, title, entry_url(eid))
    return txt_lines, colormap, {}


def render_header(line):
    m = re.match(HEADER_RE, line)
    if not m:
        return None
    num, author, rest, idpart = m.groups()
    if author.startswith('◆'):
        astyle, istyle = AUTHOR_GREEN_BOLD, ID_BLUE_BOLD
    else:
        astyle, istyle = AUTHOR_GREEN, ID_BLACK
    return render_common_header(line, HEADER_RE, astyle, istyle)


if __name__ == '__main__':
    RAW = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "aaManga", "さーきー", "raw_fc2", "entry_8532.html")
    OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "aaManga", "さーきー", "aa_fc2")
    m = re.search(r'entry_(\d+)', os.path.basename(RAW))
    eid = m.group(1) if m else ''
    lines, cm, _ = extract(open(RAW, encoding='utf-8', errors='replace').read(), eid, os.path.basename(RAW))
    os.makedirs(OUT, exist_ok=True)
    base = os.path.basename(RAW).replace('.html', '')
    open(os.path.join(OUT, base + '.txt'), 'w', encoding='utf-8').write('\n'.join(lines))
    open(os.path.join(OUT, base + '_colormap.json'), 'w', encoding='utf-8').write(
        json.dumps(cm, ensure_ascii=False))
    print(f'{base}: {len(lines)} lines, {len(cm)} colors')
