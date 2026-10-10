# -*- coding: utf-8 -*-

"""inzainewtown.blog.fc2.com（だっておｗｗｗキャンセル，FC2 博客）：<div class="entry_body"> 内单块 + <br> 流式。

- 正文整体塞在一个无 class 的 `<div>`（外层 `font-size: medium` / `color: rgb(0,0,0)`），
  行间只有 `<br>`；**无** `<dt>/<dd>/<dl>`（与 yaruok / iitokolo 的 dl 结构不同）
- 帖头：`NNN 名前：NEO坊 ◆作者 [] 投稿日：YYYY/MM/DD(曜) HH:MM:SS ID:xxx [n/m]`
  （格式 B；`[n/m]` 是原 2ch 长帖分割标记，**原样保留**）
  - 原站正文无颜色 span（整块黑字）→ 帖头样式按本库统一口径还原：◆作者绿粗、ID 红粗
- 正文行 `<br>` 分隔；连续 2+ `<br>` = 作者分镜空行（保留，勿删——排版一部分）
- 标题取 `<h2 class="entry_header">`（`<title>` 带「 - だっておｗｗｗキャンセル」站点后缀）
- 截断：fc2_footer / fc2button-clap（拍手・分享按钮）/ relate_dl（関連記事）
"""
import html as H
import json
import os
import re
import sys

try:
    from _base import clean, build_txt, AUTHOR_GREEN_BOLD, ID_RED_BOLD, \
        NUM_STYLE, TIME_STYLE, AUTHOR_GREEN, ID_BLACK
except ImportError:
    from sites._base import clean, build_txt, AUTHOR_GREEN_BOLD, ID_RED_BOLD, \
        NUM_STYLE, TIME_STYLE, AUTHOR_GREEN, ID_BLACK

SITE = "inzainewtown_fc2"

# `NNN 名前：作者 [] 投稿日：时间 ID:xxx [n/m]`（楼号可缺：原站自身有 2 处漏号）
HEADER_RE = re.compile(r'^(\d*[ \t]*名前：\s*)(.+?)(\s*\[\s*\]\s*投稿日：\s*\d{4}/\d{2}/\d{2}.*?)(\s*ID:\S+.*)?$')
# 帖头识别：容许行首 ASCII 空白——部分章节整篇带 `&nbsp; &nbsp; ` 模板缩进
# （clean 把 &nbsp; 还原成空格），不容忍就直接丢帖（entry_31320/31385/34908 共丢 42 帖）；
# 另容许**缺楼号**的帖头（原站页自身漏号：entry_34389/34602 各 1 处），
# 用「名前：…投稿日：」整形状兜底，防误吃正文里出现的 名前： 字样
_HDR_RE = re.compile(r'^[ \t]*(?:\d+[ \t]*)?名前：[^\n]{0,80}?投稿日：')


def extract_article(raw):
    i = raw.find('<div class="entry_body">')
    if i == -1:
        i = raw.find('<div class="entry-content">')
    if i == -1:
        i = 0
    # 终点：标题下正文后的页脚/按钮/相关文章（都排在 entry_body 之后）
    end = len(raw)
    for marker in ('<div class="fc2_footer"', '<div class="fc2button-clap"',
                   '<dl class="relate_dl"', '関連記事', '<p class="entry_footer"'):
        j = raw.find(marker, i + 200)
        if j != -1:
            end = min(end, j)
    return raw[i:end]


def parse_posts(seg):
    """`N 名前：` 帖头 + `<br>` 正文。

    空行口径：本站每个 AA 行后是 1 个 `<br>`（行尾），作者分镜是**连续 2+ `<br>`**
    → 保留 brs-1 个空行（yaruocheck 同款口径）；单个 br 只是行尾，不产空行。
    """
    posts = []
    cur = None
    cur_rows = []
    # 帖头一律当行界：该站有的日子把每帖包进独立 <div>/<p>，「</p><div><span>NNN 名前：…」
    # 直接续在上一行正文后面（中间没有 <br>）→ 不先断行，整帖会被吞成上一帖的正文行
    seg = re.sub(r'(?<!\d)(?=[ \t]*\d+[ \t]*名前：)', '\n', seg)
    parts = re.split(r'(<br\s*/?>)', seg)
    i = 0
    n = len(parts)
    while i < n:
        raw = parts[i]
        if raw.startswith('<br'):
            brs = 0
            while i < n:
                if parts[i].startswith('<br'):
                    brs += 1
                    i += 1
                elif parts[i] == '':
                    i += 1
                else:
                    break
            if cur is not None and brs >= 2:
                cur_rows.extend([''] * (brs - 1))
            continue
        # 含 HTML 标签的碎片 = 模板缩进污染（如 `<div><span …>` 紧接帖头）→ 剥 ASCII 空白；
        # U+3000 是 AA 对齐字符，任何情况下都不能 strip
        chunk = clean(raw)
        if '<' in raw or '>' in raw:
            chunk = '\n'.join(l.lstrip(' \t') for l in chunk.split('\n'))
        # 一个碎片可能含多行：帖子模板（部分日子 FC2 把每帖包进独立 <div>）会把
        # 「</div><div>NNN 名前：…」直接续在正文碎片里 → 必须逐行判定帖头，
        # 否则整帖（含帖头）被当正文行拼进去、帖子丢失（entry_31385 曾丢 22 帖）
        for line in chunk.split('\n'):
            line = line.rstrip(' \t')
            if not line.strip():
                continue
            if _HDR_RE.match(line):
                line = line.lstrip(' \t')
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
    m = re.search(r'<h2[^>]*class="entry_header"[^>]*>(.*?)</h2>', raw, re.S)
    if m:
        t = H.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip()
        if t:
            return t
    m = re.search(r'<title[^>]*>(.*?)</title>', raw, re.S)
    if m:
        t = H.unescape(m.group(1)).strip()
        t = re.sub(r'\s*-\s*[^\-]+\s*$', '', t)   # 剥「 - 站点名」后缀
        return t
    return 'unknown'


def entry_url(eid):
    return f"https://inzainewtown.blog.fc2.com/blog-entry-{eid}.html"


def _site_url(raw, eid):
    for pat in (r'rel="canonical" href="([^"]+)"', r'property="og:url" content="([^"]+)"'):
        m = re.search(pat, raw)
        if m:
            return m.group(1)
    return entry_url(eid)


def extract(raw, eid=None, src_name=None):
    # eid 兜底：从文件名取数字段（entry_31014.html → 31014）；勿用 split('_')[0]（那只得到 entry）
    if not eid:
        m = re.search(r'(\d+)', src_name or '')
        eid = m.group(1) if m else ''
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
        print('usage: python sites/inzainewtown_fc2.py <raw.html> [out_dir]')
        sys.exit(0)
    nums = re.findall(r'(\d+)', os.path.basename(RAW))
    eid = nums[-1] if nums else ''
    lines, cm, _ = extract(open(RAW, encoding='utf-8', errors='replace').read(), eid, os.path.basename(RAW))
    print(f'{os.path.basename(RAW)}: {len(lines)} lines, {len(cm)} colors')
    if OUT:
        os.makedirs(OUT, exist_ok=True)
        base = os.path.basename(RAW).replace('.html', '')
        open(os.path.join(OUT, base + '.txt'), 'w', encoding='utf-8').write('\n'.join(lines))
        open(os.path.join(OUT, base + '_colormap.json'), 'w', encoding='utf-8').write(
            json.dumps(cm, ensure_ascii=False))
        print('  ->', os.path.join(OUT, base + '.txt'))
