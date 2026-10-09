# -*- coding: utf-8 -*-

"""yomuaa.r401.net（YomuAA，AA 漫画分镜站）：<article class="aa"> 内 <dl class="aa-article">。

- 无帖头：<dt><span id="koma-N">N</span></dt> 是分镜序号，不输出
- 正文 dd 内按 <br> 切行（分镜漫画，保留中间空行）
- 标题：<title>（剥「| YomuAA」后缀）
"""
import html as H
import json
import os
import re
import sys

try:
    from _base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, ID_RED_BOLD, \
        NUM_STYLE, TIME_STYLE, AUTHOR_GREEN, ID_BLACK
except ImportError:
    from sites._base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, ID_RED_BOLD, \
        NUM_STYLE, TIME_STYLE, AUTHOR_GREEN, ID_BLACK

SITE = "yomuaa"

HEADER_RE = re.compile(r'^(\d+\s*：\s*)(.+?)(\s*：\s*\d{4}/\d{2}/\d{2}.*?)(\s*ID:\S+.*)?$')

CONTAINER = '<article class="aa"'


def extract_article(raw):
    i = raw.find(CONTAINER)
    if i == -1:
        i = raw.find('<div class="entry-content"')
    if i == -1:
        i = 0
    end = len(raw)
    for marker in ('relate_dl', '関連記事', 'コメント', '<footer', 'footer'):
        j = raw.find(marker, i)
        if j != -1:
            end = j
            break
    return raw[i:end]


def parse_posts(seg):
    """按 <dt> 切分；dt 为分镜序号（数字）→ 不输出帖头，只保留 dd 正文。"""
    posts = []
    parts = re.split(r'(?=<dt[ >])', seg)
    for part in parts:
        dt = re.search(r'<dt[^>]*>(.*?)</dt>', part, re.S)
        if not dt:
            continue
        header = clean(dt.group(1)).strip()
        if header.isdigit():       # 分镜序号 → 无帖头
            header = ""
        dd = re.search(r'<dd[^>]*>(.*?)</dd>', part, re.S)
        body_html = dd.group(1) if dd else ''
        rows = []
        for row_html in re.split(r'<br\s*/?>', body_html):
            row = clean(row_html).lstrip("\r\n")
            if row.strip():
                rows.append(row)
        s = 0
        while s < len(rows) and rows[s].strip() == '':
            s += 1
        e = len(rows)
        while e > s and rows[e - 1].strip() == '':
            e -= 1
        if rows[s:e]:
            posts.append((header, [(r, None) for r in rows[s:e]]))
    return posts


def get_title(raw):
    m = re.search(r'<title[^>]*>(.*?)</title>', raw, re.S)
    if m:
        t = H.unescape(m.group(1)).strip()
        t = re.sub(r'\s*\|\s*YomuAA\s*$', '', t)
        return t
    m = re.search(r'property="og:title" content="([^"]*)"', raw)
    return H.unescape(m.group(1)).strip() if m else 'unknown'


def entry_url(eid):
    return f"https://yomuaa.r401.net/episode/{eid}"


def _site_url(raw, eid):
    """YomuAA 页面无 canonical（og:url 是站首页），用 arcid → episode 映射。"""
    m = re.search(r'<link rel="canonical" href="([^"]+)"', raw)
    if m and 'episode' in m.group(1):
        return m.group(1)
    return entry_url(eid)


def extract(raw, eid=None, src_name=None):
    eid = eid or (src_name or '').split('_')[0]
    title = get_title(raw)
    posts = parse_posts(extract_article(raw))
    txt_lines, colormap = build_txt(posts, title, _site_url(raw, eid))
    return txt_lines, colormap, {}


def render_header(line):
    m = re.match(HEADER_RE, line)
    if not m:
        return None
    num, author, rest, idpart = m.groups()
    if '◆' in author:
        author_style, id_style = AUTHOR_GREEN_BOLD, ID_RED_BOLD
    else:
        author_style, id_style = AUTHOR_GREEN, ID_BLACK
    out = f'<span style="{NUM_STYLE}">{H.escape(num)}</span>'
    out += f'<span style="{author_style}">{H.escape(author)}</span>'
    out += f'<span style="{TIME_STYLE}">{H.escape(rest)}</span>'
    if idpart:
        out += f'<span style="{id_style}">{H.escape(idpart)}</span>'
    return out


if __name__ == '__main__':
    RAW = sys.argv[1] if len(sys.argv) > 1 else ''
    OUT = sys.argv[2] if len(sys.argv) > 2 else ''
    if not RAW:
        print('usage: python sites/yomuaa.py <raw.html> [out_dir]')
        sys.exit(0)
    eid = re.search(r'(\d+)', os.path.basename(RAW)).group(1)
    lines, cm, _ = extract(open(RAW, encoding='utf-8', errors='replace').read(), eid, os.path.basename(RAW))
    print(f'{os.path.basename(RAW)}: {len(lines)} lines, {len(cm)} colors')
    if OUT:
        os.makedirs(OUT, exist_ok=True)
        base = os.path.basename(RAW).replace('.html', '')
        open(os.path.join(OUT, base + '.txt'), 'w', encoding='utf-8').write('\n'.join(lines))
        open(os.path.join(OUT, base + '_colormap.json'), 'w', encoding='utf-8').write(
            json.dumps(cm, ensure_ascii=False))
        print('  ->', os.path.join(OUT, base + '.txt'))
