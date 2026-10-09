# -*- coding: utf-8 -*-

"""yarumo.blog83.fc2.com（やる模，FC2 博客）：<div class="contents_body"> 内 <p> 帖头流式。

- 帖头：`<p>1 名前：<span class="auth"> ◆/H7wY7ybL2</span>[] 投稿日：2011/02/25(金) 00:45:04 ID:ZAf7FGDU<br>`
  - 作者 span.auth 绿粗（#008800，原站 class 样式）
  - 名無し读者帖头无 span.auth（纯文本 `NNN 名前：名無し... 投稿日：...`）
- 正文行 <br> 分隔，U+3000 对齐；无骰子红/无加粗
- 帖头号是原 2ch 楼 res 号（1 起，删除贴跳号）
- 截断：fc2_footer（页脚容器）
"""
import json, os, re, sys

try:
    from _base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, AUTHOR_GREEN, ID_BLACK
except ImportError:
    from sites._base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, AUTHOR_GREEN, ID_BLACK
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根

SITE = "yarumo_fc2"

# 帖头（重建后）：`NNN 名前：作者 [] 投稿日：... ID:xxx`
HEADER_RE = re.compile(
    r'^(\d+\s+名前：\s*)(.+?)(\s*\[[^\]]*\]\s*投稿日：\s*\d{4}/\d{2}/\d{2}\(\S\)\s*\d{2}:\d{2}:\d{2}\.\d+\s*)(ID:\S+.*)?$')
# 原 HTML 帖头识别：数字 + 名前： + span(class=auth/mob) + 投稿日：日期
# 作者帖带 <span class="auth">◆…</span>；名無し帖带 <span class="mob">どこかの名無しさん</span>
# 无 span 的 `数字 名前：` 是作者引用的读者 res（引用块），作为正文保留，不当帖头
POST_START_RE = re.compile(
    r'(\d+)\s+名前：<span[^>]*class="(?:auth|mob)"[^>]*>(.*?)</span>'
    r'(.*?)(?:<br\s*/?>)', re.S)


def extract_article(raw):
    i = raw.find('<div class="contents_body">')
    if i == -1:
        i = raw.find('<div class="entry_body">')
    j = raw.find('fc2_footer', i)
    if j != -1:
        # 截到 fc2_footer 所在 div 的起始（<div class="fc2_footer" 的 <div 之前）
        k = raw.rfind('<div class="', i, j)
        if k != -1:
            j = k
    else:
        j = raw.find('relate_dl', i)
    if j == -1:
        j = len(raw)
    return raw[i:j]


def parse_posts(seg):
    r"""按帖头切分（POST_START_RE 锚定），返回 [(帖头行, [(aa行, color_or_None)...])...]

    帖头判定 = `\d+ 名前：` 起 + 该行含 `投稿日：YYYY/MM/DD(` 日期特征。
    作者帖带 span.auth；名無し读者帖无样式。
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
        # 帖头重建：span 内作者 + 其余（span 无 style 属性，统一绿粗由 render_header 判定）
        header = re.sub(r'<[^>]+>', '', m.group(0))
        header = __import__('html').unescape(header).replace('\u00a0', ' ').strip()
        body = part[m.end():]
        # 统一行分隔：FC2 部分文章用 </div><div> 包行（非 <br>），转成 <br> 统一切分
        body = re.sub(r'</div>\s*<div[^>]*>', '<br>', body)
        body = re.sub(r'<div[^>]*>', '', body)
        rows = []
        for row_html in re.split(r'<br\s*/?>', body):
            row = clean(row_html.strip('\n'))
            if row.strip() == '.':
                continue
            rows.append(row)
        # 只跳首尾模板空行；正文中间作者分镜空行保留（2026-08-28，同 yaruobook/yaruomatomex——
        # 之前全删空行导致 yukika_robot 原文 0 空行, AA 排版错位/行号偏移）
        s = 0
        while s < len(rows) and rows[s].strip() == '':
            s += 1
        e = len(rows)
        while e > s and rows[e - 1].strip() == '':
            e -= 1
        rows = rows[s:e]
        # 空正文帖（原帖空楼）也保留帖头，保证 res 完整性
        posts.append((header, [(r, None) for r in rows]))
    return posts


def get_title(raw):
    m = re.search(r'<title[^>]*>(.*?)</title>', raw, re.S)
    if m:
        t = __import__('html').unescape(m.group(1)).strip()
        t = re.sub(r'^(やる模|やる夫まとめ)\s*', '', t)
        return t
    m = re.search(r'property="og:title" content="([^"]*)"', raw)
    return __import__('html').unescape(m.group(1)).strip() if m else 'unknown'


def entry_url(eid):
    return f"http://yarumo.blog83.fc2.com/blog-entry-{eid}.html"


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
    # 作者帖（◆）绿粗；名無し读者帖无样式（ID 黑）
    if '◆' in author:
        astyle, istyle = AUTHOR_GREEN_BOLD, ID_BLACK
    else:
        astyle, istyle = AUTHOR_GREEN, ID_BLACK
    return render_common_header(line, HEADER_RE, astyle, istyle)


if __name__ == '__main__':
    RAW = sys.argv[1] if len(sys.argv) > 1 else ''
    OUT = sys.argv[2] if len(sys.argv) > 2 else ''
    if not RAW:
        print('usage: python sites/yarumo_fc2.py <raw.html> [out_dir]')
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
