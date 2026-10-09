# -*- coding: utf-8 -*-

"""yaruo-manyoshu.com（やる夫萬葉集，FC2 博客）：<div class="article"> 内流式 <br> 布局。

- 帖头：`NNN 名前：<span style="color: rgb(0, 128, 0);"><strong>作者</strong></span> [] 投稿日：... ID:xxx [n/m]`
  - 作者绿粗 #008800（内联 style，非 ◆ 判定——本站搬运帖全带内联作者样式）
  - ID 无颜色（原帖搬运，只保留作者绿粗）
- 正文：U+3000 全角空格对齐 AA 行，行间 <br> 分隔；无骰子红/无 &nbsp;
- 截断：fc2button-clap（拍手按钮）→ 其后是相关文章/底部导航
- 帖头号是原 2ch 楼 res 号（2324 起），非连续从 1 开始
"""
import json, os, re, sys

try:
    from _base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, AUTHOR_GREEN, ID_BLACK
except ImportError:
    from sites._base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, AUTHOR_GREEN, ID_BLACK
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根

SITE = "manyoshu"

# 帖头（重建后）：`NNN 名前：作者 [] 投稿日：... ID:xxx [n/m]`
# 作者帖：`NNN 名前：ポン ◆xxx [] 投稿日：...`；名無し帖：`NNN 名前：名無しさん...[sage] 投稿日：...`
HEADER_RE = re.compile(
    r'^(\d+\s+名前：\s*)(.+?)(\s*\[[^\]]*\]\s*投稿日：\s*\d{4}/\d{2}/\d{2}\(\S\)\s*\d{2}:\d{2}:\d{2}\.\d+\s*)(ID:\S+.*)?$')
# 原 HTML 帖头识别：数字 + 名前： + （可选内联样式）+ 投稿日：+ 日期
# 作者帖带 <span style="color: rgb(0, 128, 0);"><strong>…</strong></span>；
# 名無し读者帖无样式（直接 名無しさん…[sage] 投稿日：…）
POST_START_RE = re.compile(
    r'(\d+)\s+名前：(?:<span[^>]*style="([^"]*)"[^>]*>(.*?)</span>)?'
    r'(.*?)(?:<br\s*/?>|</div>\s*<div[^>]*>)', re.S)
RED_RE = re.compile(r'<span[^>]*style="[^"]*(?:red|255,\s*0,\s*0)[^"]*"[^>]*>', re.I)


def extract_article(raw):
    i = raw.find('<div class="article">')
    if i == -1:
        i = raw.find('<div class="entry_body">')
    # 正文在 <div class="article"> 内，到 fc2_footer（拍手/页脚容器）截断
    j = raw.find('fc2_footer', i)
    if j != -1:
        # 截到 fc2_footer 所在 div 的起始（<div class="fc2_footer" 的 <div 之前）
        k = raw.rfind('<div', i, j)
        if k != -1:
            j = k
    else:
        j = raw.find('fc2button-clap', i)
    if j == -1:
        j = raw.find('<!--/article-->', i)
    if j == -1:
        j = raw.find('relate_dl', i)
    if j == -1:
        j = len(raw)
    return raw[i:j]


def rebuild_header(dt_html):
    """重建帖头行：保留作者 span 样式，其余剥标签 → header_text + (author_style, id_style)"""
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
    """按帖头切分（POST_START_RE 锚定），返回 [(帖头行, [(aa行, color_or_None)...])...]

    帖头判定 = 数字 + 名前： 起 + 该行含 投稿日：YYYY/MM/DD( 日期特征。
    作者帖带内联样式 span；名無し读者帖无样式。AA 艺术行里的 数字 名前：
    无日期特征，不会误判。
    """
    posts = []
    # 找所有候选帖头起点
    starts = []
    for m in POST_START_RE.finditer(seg):
        # 严格：帖头行必须含投稿日日期特征
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
        # 帖头文本：有 span（作者帖）rebuild 提取样式；无 span（名無し帖）原样剥标签
        if m.group(2):
            header_html = m.group(0)
            header = rebuild_header(header_html).strip()
        else:
            header = re.sub(r'<[^>]+>', '', m.group(0))
            header = __import__('html').unescape(header).replace('\u00a0', ' ').strip()
        body = part[m.end():]
        # 统一行分隔：FC2 部分文章用 </div><div> 包行（非 <br>），转成 <br> 统一切分
        body = re.sub(r'</div>\s*<div[^>]*>', '<br>', body)
        body = re.sub(r'<div[^>]*>', '', body)
        # 行切分：<br> 分隔；跳过帖头后的模板空行
        rows = []
        for row_html in re.split(r'<br\s*/?>', body):
            # HTML 源码每 AA 行前有 \n（<br>\n<AA>），strip 掉避免 clean() 产生空行
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
        # 空正文帖（原帖空楼）也保留帖头，保证 res 完整性
        posts.append((header, [(r, None) for r in rows]))
    return posts


def get_title(raw):
    m = re.search(r'<title[^>]*>(.*?)</title>', raw, re.S)
    if m:
        t = __import__('html').unescape(m.group(1)).strip()
        t = re.sub(r'^(やる夫萬葉集|やる夫万葉集)\s*', '', t)
        return t
    m = re.search(r'property="og:title" content="([^"]*)"', raw)
    return __import__('html').unescape(m.group(1)).strip() if m else 'unknown'


def entry_url(eid):
    return f"https://yaruo-manyoshu.com/blog-entry-{eid}.html"


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
        print('usage: python sites/manyoshu.py <raw.html> [out_dir]')
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
