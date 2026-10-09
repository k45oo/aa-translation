# -*- coding: utf-8 -*-

"""himanatokiniyaruo.com（暇な時にやる夫まとめ）：<div class="entry-body"><dl>。

- 帖头：`NNN ： ◆作者 ： 时间 ID:xxx`（作者绿粗 #008800、ID 红粗 #ff0000）
- 正文 dd 内按 <br> 切行；开头模板空行跳过
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

SITE = "himanatokiniyaruo"

HEADER_RE = re.compile(r'^(\d+\s*：\s*)(.+?)(\s*：\s*\d{4}/\d{2}/\d{2}.*?)(\s*ID:\S+.*)?$')

CONTAINER = '<div class="entry-body">'


def extract_article(raw):
    i = raw.find(CONTAINER)
    if i == -1:
        i = raw.find('<div class="entry-content"')
    if i == -1:
        i = 0
    end = len(raw)
    for marker in ('relate_dl', '関連記事', 'コメント'):
        j = raw.find(marker, i)
        if j != -1:
            end = j
            break
    return raw[i:end]


def parse_posts(seg):
    """按 <dt> 切分；dt 为帖头，dd 为正文。

    2026-09-18 修：**保留正文空行**。该站 authored 空行 = `<br><br>`（空 chunk），
    旧代码 `if row.strip()` 全丢。真值验证：同作品同章（第104話）yaruomatomex 镜像
    230 空行、保留版 230/230 逐行一致（0 不一致），丢弃版 0 空行 / 80 处不一致。
    """
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
            row = clean(row_html).lstrip("\r\n").rstrip()
            rows.append(row if row.strip() else '')   # 空行保留（原文空行硬约束）
        # 只跳帖首/帖尾模板空行
        s = 0
        while s < len(rows) and rows[s] == '':
            s += 1
        e = len(rows)
        while e > s and rows[e - 1] == '':
            e -= 1
        if rows[s:e]:
            posts.append((header, [(r, None) for r in rows[s:e]]))
    return posts


def get_title(raw):
    m = re.search(r'<title[^>]*>(.*?)</title>', raw, re.S)
    if m:
        t = H.unescape(m.group(1)).strip()
        t = re.sub(r'^(やる夫まとめくす|やる夫スレ自分チェック用|暇な時にやる夫まとめ)\s*', '', t)
        t = re.sub(r'\s*[-|]\s*[^\-|]+$', '', t)   # 剥「 - 站点名」后缀
        return t
    m = re.search(r'property="og:title" content="([^"]*)"', raw)
    return H.unescape(m.group(1)).strip() if m else 'unknown'


def entry_url(eid):
    return f"https://himanatokiniyaruo.com/blog-entry-{eid}.html"


def _site_url(raw, eid):
    for pat in (r'rel="canonical" href="([^"]+)"', r'property="og:url" content="([^"]+)"'):
        m = re.search(pat, raw)
        if m:
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
        print('usage: python sites/himanatokiniyaruo.py <raw.html> [out_dir]')
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
