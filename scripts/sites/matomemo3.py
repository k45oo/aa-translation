# -*- coding: utf-8 -*-

"""matomemo3.blog.2nt.com（やる夫まとめもZ，2NT 博客）：<dl><dt><dd> 闭合结构。

- 正文：<dl> 内 <dt>帖头</dt><dd>AA<br />...</dd>（159 组闭合良好，无 textar-aa）
- 帖头：`<dt><a name="R1">1</a> 名前：<span style="color: RED;"> ◆作者</span>：时间 ID:xxx</dt>`
  - 作者红色（color: RED;，本站作者用红而非绿）；名無し帖作者无 ◆（普通文字）
- dd 内容：`<br />\n\u3000...` 每行以 U+3000 开头，无颜色 span（正文 0 span）
- dd 开头有 `<br />\n` 模板空行，必须跳过；正文中间作者分镜空行保留
- 截断：fc2_footer（或 entry_footer / relate_dl）
- 标题：<title> 剥「やる夫まとめもZ 」前缀

作者 ◆f6nzOA/5ZY（= ◆bQlTW/r6l2 马甲，白饅頭系列）与 ◆/H7wY7ybL2（第1-2话）。
"""
import json, os, re, sys

try:
    from _base import clean, build_txt, render_common_header, AUTHOR_GREEN, ID_RED_BOLD, ID_BLACK
except ImportError:
    from sites._base import clean, build_txt, render_common_header, AUTHOR_GREEN, ID_RED_BOLD, ID_BLACK
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根

SITE = "matomemo3"

# 帖头（重建后）：`NNN 名前：作者：时间 ID:xxx`（作者 红粗；名無し 绿非粗）
HEADER_RE = re.compile(
    r'^(\d+\s+名前：\s*)(.+?)(\s*：\s*\d{4}/\d{2}/\d{2}\([^)]\)\s*\d{2}:\d{2}:\d{2}\s*)(ID:\S+.*)?$')

# 原 HTML 帖头识别：`<dt><a name="R\d+">NNN</a> 名前：...`
POST_START_RE = re.compile(
    r'<dt[^>]*>(?:\s*<a[^>]*>)?\s*(\d+)\s*(?:</a>)?\s*名前：\s*'
    r'<span[^>]*>(.*?)</span>(.*?)(?:</dt>)', re.S)


def extract_article(raw, eid):
    """定位正文：第一个 <dl> 到 fc2_footer / entry_footer / relate_dl。"""
    i = raw.find('<dl')
    if i == -1:
        i = raw.find('<div class="article">')
    j = raw.find('fc2_footer', i)
    if j == -1:
        j = raw.find('entry_footer', i)
    if j == -1:
        j = raw.find('relate_dl', i)
    if j == -1:
        j = len(raw)
    # 截到 fc2_footer 所在 div 起始
    if 'fc2_footer' in raw[i:j]:
        k = raw.rfind('<div class="', i, j)
        if k != -1:
            j = k
    return raw[i:j]


def parse_posts(seg):
    """按 <dt> 切分（dl 闭合良好，但 dt 可能无 </dt> 直接接 <dd），返回 [(帖头行, [(aa行, color_or_None)...])...]。

    兼容两种格式：
    A. `<dt><a name="R1">1</a> 名前：...ID:xxx</dt><dd>...`（有 </dt>）
    B. `<dt><a name="R656">656</a> 名前：...ID:xxx<dd>...`（无 </dt>，直接 <dd>）
    """
    posts = []
    parts = re.split(r'(?=<dt)', seg)
    for part in parts:
        dt = re.search(r'<dt[^>]*>(.*?)(?:</dt>|(?=<dd))', part, re.S)
        if not dt:
            continue
        header = re.sub(r'<[^>]+>', '', dt.group(1))
        header = __import__('html').unescape(header).replace('\u00a0', ' ').strip()
        header = re.sub(r'\s+', ' ', header)  # 锚点标签间空白 → 单空格
        # 找 dd（dd 内容到下一个 dt 或 </dd> 或 </dl> 或 part 末尾）
        dd = re.search(r'<dd[^>]*>(.*?)(?:</dd>|</dl>|(?=<dt)|$)', part, re.S)
        body_html = dd.group(1) if dd else ''
        rows = []
        for row_html in re.split(r'<br\s*/?>', body_html):
            # 空片段（模板空行/分镜空行）原样保留为空行；clean 会压缩连续空行，不能用于空行判断
            if row_html.strip() == '' or row_html.strip('\n').strip() == '':
                row = ''
            else:
                row = clean(row_html).strip('\n')
            # 2ch 帖尾「.」行是分页标记，跳过
            if row.strip() == '.':
                continue
            rows.append((row, None))
        # 只跳开头/结尾模板空行；正文中间作者分镜空行保留（2026-08-28 硬约束）
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
    m = re.search(r'<title[^>]*>(.*?)</title>', raw, re.S)
    if m:
        t = __import__('html').unescape(m.group(1)).strip()
        t = re.sub(r'^(やる夫まとめもZ|AAまとめブログ|やる夫まとめ)\s*', '', t)
        return t
    m = re.search(r'property="og:title" content="([^"]*)"', raw)
    return __import__('html').unescape(m.group(1)).strip() if m else 'unknown'


def entry_url(eid):
    return f"http://matomemo3.blog.2nt.com/blog-entry-{eid}.html"


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
    # 作者帖（◆）红粗（原站 color:RED;）；名無し 绿非粗
    if '◆' in author:
        astyle = ID_RED_BOLD
    else:
        astyle = AUTHOR_GREEN
    return render_common_header(line, HEADER_RE, astyle, ID_BLACK)


if __name__ == '__main__':
    RAW = sys.argv[1] if len(sys.argv) > 1 else ''
    OUT = sys.argv[2] if len(sys.argv) > 2 else ''
    if not RAW:
        print('usage: python sites/matomemo3.py <raw.html> [out_dir]')
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
