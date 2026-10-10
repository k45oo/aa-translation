# -*- coding: utf-8 -*-

"""yaruohiroba.com（やる夫広場，WordPress）：第八站点。

- 正文：`<div class="AA-area">` 内 `<dl><dt>帖头</dt><dd>AA<br>...</dd></dl>`（dl 不闭合，bookshelf 模式）
- 帖头：`<br><br><br><span>1</span> 名前：<span style="color:#008800;">広場の妖精</span>　投稿日：<span>2026/08/12(水) 19:19:55</span><br><br><br>`
  —— `NNN 名前：作者(绿粗 #008800)　投稿日：时间`，无 ◆、无 ID、无骰子红、无加粗
- dd 开头 `<br><br><br>` 是模板装饰空行，必须跳过（2nt/touhou 同款坑）；正文中间空行=作者分镜，保留
- 截断：`related-heading`（関連記事）
"""
import json, os, re, sys

try:
    from _base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, NUM_STYLE, TIME_STYLE
except ImportError:
    from sites._base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, NUM_STYLE, TIME_STYLE
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根(2026-08-18 Phase 3 动态计算)

SITE = "yaruohiroba"

# 1 名前：作者　投稿日：时间（无 ID）
HEADER_RE = re.compile(r'^(\d+\s*名前：\s*)(.+?)(\s*投稿日：.*)$')


def extract_article(raw):
    i = raw.find('<div class="AA-area">')
    if i == -1:
        return ''
    start = raw.find('>', i) + 1
    j = raw.find('related-heading', start)
    if j == -1:
        j = len(raw)
    return raw[start:j]


def parse_posts(seg):
    """返回 [(帖头行, [(aa行, color_or_None)...])...]；按 <dt> 切分（dl 不闭合）"""
    posts = []
    parts = re.split(r'(?=<dt[ >])', seg)
    for part in parts:
        dt = re.search(r'<dt[^>]*>(.*?)</dt>', part, re.S)
        if not dt:
            continue
        header = clean(dt.group(1)).strip()
        dd = re.search(r'<dd[^>]*>(.*?)</dd>', part, re.S)
        body_html = dd.group(1) if dd else ''
        rows = []
        for row_html in re.split(r'<br\s*/?>', body_html):
            row = clean(row_html).strip('\n')
            rows.append((row, None))
        # 只裁 dd 开头/结尾模板空行；正文中间作者分镜空行保留（2026-08-11 定型）
        s = 0
        while s < len(rows) and rows[s][0].strip() == '':
            s += 1
        e = len(rows)
        while e > s and rows[e - 1][0].strip() == '':
            e -= 1
        rows = rows[s:e]
        posts.append((header, rows))
    return posts


def get_title(raw):
    m = re.search(r'<h1[^>]*class="post-title"[^>]*>(.*?)</h1>', raw, re.S)
    if m:
        return clean(m.group(1)).strip()
    m = re.search(r'<title[^>]*>(.*?)</title>', raw, re.S)
    return clean(m.group(1)).strip() if m else 'unknown'


def entry_url(eid):
    return f"https://yaruohiroba.com/archives/{eid}"


def extract(raw, eid=None, src_name=None):
    """-> (txt_lines, colormap, extras)"""
    eid = eid or (src_name or '').split('_')[0]
    seg = extract_article(raw)
    if not seg:
        return [], {}, {}
    posts = parse_posts(seg)
    if not posts:
        return [], {}, {}
    title = get_title(raw)
    txt_lines, colormap = build_txt(posts, title, entry_url(eid))
    return txt_lines, colormap, {}


def render_header(line):
    """帖头：序号灰 + 作者绿粗 + 投稿日灰（无 ID，3 段式）"""
    import html as H
    m = re.match(HEADER_RE, line)
    if not m:
        return None
    num, author, rest = m.groups()
    out = f'<span style="{NUM_STYLE}">{H.escape(num)}</span>'
    out += f'<span style="{AUTHOR_GREEN_BOLD}">{H.escape(author)}</span>'
    out += f'<span style="{TIME_STYLE}">{H.escape(rest)}</span>'
    return out


if __name__ == '__main__':
    RAW = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "aaManga", "やる夫広場短編集", "raw", "entry_25976.html")
    OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "aaManga", "やる夫広場短編集", "aa")
    m = re.search(r'(\d+)', os.path.basename(RAW))
    eid = m.group(1) if m else '0'
    lines, cm, _ = extract(open(RAW, encoding='utf-8', errors='replace').read(), eid, os.path.basename(RAW))
    os.makedirs(OUT, exist_ok=True)
    base = os.path.basename(RAW).replace('.html', '')
    open(os.path.join(OUT, base + '.txt'), 'w', encoding='utf-8').write('\n'.join(lines))
    open(os.path.join(OUT, base + '_colormap.json'), 'w', encoding='utf-8').write(
        json.dumps(cm, ensure_ascii=False))
    print(f'{base}: {len(lines)} lines, {len(cm)} colors')
