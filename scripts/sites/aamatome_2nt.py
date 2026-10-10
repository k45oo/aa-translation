# -*- coding: utf-8 -*-

"""aamatome.blog.2nt.com（2NT 博客，やる夫まとめ）：entry_body/textar-aa 帖头流式。

- 正文：<div class="entry_body"><div class="textar-aa"> + <div id="more"><div class="textar-aa">
  （首个 entry_body 可能是广告，用 h2 id="e<eid>" 锚点定位）
- 帖头：`657 名前： <span style="color: #008000;"><b>◆f6nzOA/5ZY</b></span>[] 投稿日：2012/06/05(火) 22:01:09 ID:bQlK.BSA<br>`
  - 作者内联 style #008000 + <b>（绿粗）；名無し帖 <span style="color: #008000;">（绿非粗）
- 正文行 <br> 分隔，U+3000 对齐；无骰子红/无加粗
- 截断：entry_footer（正文尾部）或 fc2_footer
"""
import json, os, re, sys

try:
    from _base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, AUTHOR_GREEN, ID_BLACK
except ImportError:
    from sites._base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, AUTHOR_GREEN, ID_BLACK
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根

SITE = "aamatome_2nt"

# 帖头（重建后）：`NNN 名前：作者 [] 投稿日：... ID:xxx`
HEADER_RE = re.compile(
    r'^(\d+\s+名前：\s*)(.+?)(\s*\[[^\]]*\]\s*投稿日：\s*\d{4}/\d{2}/\d{2}\(\S\)\s*\d{2}:\d{2}:\d{2}\.\d+\s*)(ID:\S+.*)?$')
# 原 HTML 帖头识别：数字 + 名前： + span(style 或 class) + 投稿日
POST_START_RE = re.compile(
    r'(\d+)\s+名前：\s*<span[^>]*>(.*?)</span>'
    r'(.*?)(?:<br\s*/?>)', re.S)


def extract_article(raw, eid):
    """定位正文：h2 id="e<eid>" 锚点后的 entry_body（跳过首个广告 entry_body）"""
    i = raw.find(f'id="e{eid}"')
    if i == -1:
        i = raw.find('<div class="entry_body">')
    # 正文 entry_body 在锚点后
    eb = raw.find('<div class="entry_body">', i)
    if eb != -1:
        i = eb
    else:
        i = raw.find('<div class="textar-aa">', i)
    j = raw.find('fc2_footer', i)
    if j != -1:
        # 截到 fc2_footer 所在 div 的起始（<div class="fc2_footer" 的 <div 之前）
        k = raw.rfind('<div class="', i, j)
        if k != -1:
            j = k
    else:
        j = raw.find('entry_footer', i)
    if j == -1:
        j = raw.find('relate_dl', i)
    if j == -1:
        j = len(raw)
    return raw[i:j]


def parse_posts(seg):
    r"""按帖头切分（POST_START_RE 锚定），返回 [(帖头行, [(aa行, color_or_None)...])...]

    帖头判定 = `\d+ 名前：` + span 作者 + `投稿日：` 日期特征。
    """
    posts = []
    starts = []
    for m in POST_START_RE.finditer(seg):
        hdr = m.group(0)
        if re.search(r'投稿日：\s*\d{4}/\d{2}/\d{2}\(', hdr):
            starts.append(m.start())
    if not starts:
        return posts
    bounds = starts + [len(seg)]
    for idx, start in enumerate(starts):
        end = bounds[idx + 1]
        part = seg[start:end]
        m = POST_START_RE.match(part)
        if not m:
            continue
        header = re.sub(r'<[^>]+>', '', m.group(0))
        header = __import__('html').unescape(header).replace('\u00a0', ' ').strip()
        body = part[m.end():]
        body = re.sub(r'</div>\s*<div[^>]*>', '<br>', body)
        body = re.sub(r'<div[^>]*>', '', body)
        rows = []
        for row_html in re.split(r'<br\s*/?>', body):
            row = clean(row_html.strip('\n'))
            if row.strip() == '.':
                continue
            rows.append(row)
        # 只跳首尾模板空行；正文中间作者分镜空行保留（2026-08-28，同 yaruobook/yaruomatomex）
        s = 0
        while s < len(rows) and rows[s].strip() == '':
            s += 1
        e = len(rows)
        while e > s and rows[e - 1].strip() == '':
            e -= 1
        rows = rows[s:e]
        posts.append((header, [(r, None) for r in rows]))
    return posts


def get_title(raw):
    m = re.search(r'<title[^>]*>(.*?)</title>', raw, re.S)
    if m:
        t = __import__('html').unescape(m.group(1)).strip()
        t = re.sub(r'^(AAまとめブログ|やる夫まとめ)\s*', '', t)
        return t
    m = re.search(r'property="og:title" content="([^"]*)"', raw)
    return __import__('html').unescape(m.group(1)).strip() if m else 'unknown'


def entry_url(eid):
    return f"http://aamatome.blog.2nt.com/blog-entry-{eid}.html"


def _site_url(raw, eid):
    for pat in (r'rel="canonical" href="([^"]+)"', r'property="og:url" content="([^"]+)"'):
        m = re.search(pat, raw)
        if m:
            return m.group(1)
    return entry_url(eid)


def extract(raw, eid=None, src_name=None):
    eid = eid or (src_name or '').split('_')[0]
    title = get_title(raw)
    posts = parse_posts(extract_article(raw, eid))
    txt_lines, colormap = build_txt(posts, title, _site_url(raw, eid))
    return txt_lines, colormap, {}


def render_header(line):
    m = re.match(HEADER_RE, line)
    if not m:
        return None
    num, author, rest, idpart = m.groups()
    if '◆' in author:
        astyle, istyle = AUTHOR_GREEN_BOLD, ID_BLACK
    else:
        astyle, istyle = AUTHOR_GREEN, ID_BLACK
    return render_common_header(line, HEADER_RE, astyle, istyle)


if __name__ == '__main__':
    RAW = sys.argv[1] if len(sys.argv) > 1 else ''
    OUT = sys.argv[2] if len(sys.argv) > 2 else ''
    if not RAW:
        print('usage: python sites/aamatome_2nt.py <raw.html> [out_dir]')
        sys.exit(0)
    m = re.search(r'(\d+)', os.path.basename(RAW))
    eid = m.group(1) if m else ''
    lines, cm, _ = extract(open(RAW, encoding='utf-8', errors='replace').read(), eid, os.path.basename(RAW))
    print(f'{os.path.basename(RAW)}: {len(lines)} lines, {len(cm)} colors')
    if OUT:
        os.makedirs(OUT, exist_ok=True)
        base = os.path.basename(RAW).replace('.html', '')
        open(os.path.join(OUT, base + '.txt'), 'w', encoding='utf-8').write('\n'.join(lines))
        open(os.path.join(OUT, base + '_colormap.json'), 'w', encoding='utf-8').write(
            json.dumps(cm, ensure_ascii=False))
        print('  ->', os.path.join(OUT, base + '.txt'))
