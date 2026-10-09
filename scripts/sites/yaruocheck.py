# -*- coding: utf-8 -*-

"""yaruocheck.jp（やる夫チェック，FC2 系）：<div class="ently_body"> 内 N 名前： 帖头 + <br> 正文。

- 帖头：`NNN 名前： ◆作者 E-mail:sage ：日期 ID:xxx`（作者绿粗 #008800、ID 红粗 #ff0000）
- 正文按 <br> 切行；开头模板空行跳过
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

SITE = "yaruocheck"

HEADER_RE = re.compile(r'^(\d+\s*名前：\s*)(.+?)(\s*(?:E-mail:[^\s]*\s*)?[：:]\s*\d{4}/\d{2}/\d{2}.*?)(\s*ID:\S+.*)?$')


def extract_article(raw):
    i = raw.find('<div class="ently_body">')
    if i == -1:
        i = raw.find('<div class="entry_body">')
    if i == -1:
        i = raw.find('<div class="ently_text">')
    if i == -1:
        i = 0
    # 终点：正文后（评论区/相关文章/广告）
    end = len(raw)
    for marker in ('コメント', 'comment-form', 'dmm-widget', '関連記事', '<ins class="dmm'):
        j = raw.find(marker, i + 300)
        if j != -1:
            end = j
            break
    return raw[i:end]


def parse_posts(seg):
    """`N 名前：` 帖头 + <br> 正文。

    空行处理（2026-08-28 修复）：yaruocheck 站模板是**每个 AA 行后 1 个 <br>+空**（模板空行），
    但作者分镜是**连续 2+ 个 <br>**（原帖空行）。旧逻辑 `if not line.strip(): continue`
    全删（丢分镜空行）；新逻辑保留分隔符 split，**跳单个空段（模板），保留连续空段（分镜）**。
    """
    posts = []
    cur = None
    cur_rows = []
    parts = re.split(r'(<br\s*/?>)', seg)  # 保留 <br> 标记，数连续 br
    i = 0
    n = len(parts)
    while i < n:
        raw = parts[i]
        if raw.startswith('<br'):
            # 数连续 br（跳过 br 之间的 '' 空产物）
            brs = 0
            while i < n:
                if parts[i].startswith('<br'):
                    brs += 1
                    i += 1
                elif parts[i] == '':
                    i += 1  # br 之间的空产物，跳过
                else:
                    break
            if cur is not None and brs >= 2:
                # 连续 2+ br = 作者分镜，保留 n-1 个空行
                cur_rows.extend([''] * (brs - 1))
            continue
        line = clean(raw).lstrip("\r\n")
        if line.strip() == '':
            # 纯空内容（模板缩进残留）——跳过（不算分镜）
            i += 1
            continue
        if re.match(r'^\d+\s*名前：', line):
            if cur:
                posts.append((cur, [(r, None) for r in cur_rows]))
            cur, cur_rows = line, []
        else:
            if cur is not None:
                cur_rows.append(line)
        i += 1
    if cur:
        posts.append((cur, [(r, None) for r in cur_rows]))
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
    return f"http://yaruocheck.jp/blog-entry-{eid}.html"


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
        print('usage: python sites/yaruocheck.py <raw.html> [out_dir]')
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
