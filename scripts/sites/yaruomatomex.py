# -*- coding: utf-8 -*-

"""yaruomatomex.com（やる夫まとめくす）：<div class="article"> + <dl><dt>帖头</dt><dd>AA<br></dd></dl>。

- 帖头：`NNN ： ◆作者 ： 时间 ID:xxx`（作者绿粗 #008800、ID 红粗 #ff0000）
  - 名無し帖：`NNN ： 名無しさん ： ... ID:xxx`（作者绿非粗 #008800、ID 黑无样式）
- dd 开头/行间有模板空行片段（`<br>  <br>`）：必须跳过空行片段，否则 AA 图被拉长
- <dl> 常不闭合（第一帖闭合 + <a name="more"> 后不闭合），用 <dt> 分割；<dd> 截断到第一个 </dd>
- 截断：fc2_footer（Tweet 按钮）→ 其后是广告/関連記事
- 正文 dd 内无骰子红/无加粗 span（颜色只还原帖头）
"""
import json, os, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根(2026-08-18 Phase 3 动态计算)

try:
    from _base import (clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, ID_RED_BOLD,
                       NUM_STYLE, TIME_STYLE, AUTHOR_GREEN, ID_BLACK)
except ImportError:
    from sites._base import (clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, ID_RED_BOLD,
                             NUM_STYLE, TIME_STYLE, AUTHOR_GREEN, ID_BLACK)

SITE = "yaruomatomex"

HEADER_RE = re.compile(r'^(\d+\s*：\s*)(.+?)(\s*：\s*\d{4}/\d{2}/\d{2}.*?)(\s*ID:\S+.*)?$')

def extract_article(raw):
    art = raw.find('<div class="article">')
    if art == -1:
        art = 0
    end = raw.find('fc2_footer', art)
    if end == -1:
        end = raw.find('relate_dl', art)
    if end == -1:
        end = len(raw)
    return raw[art:end]

def parse_posts(seg):
    """返回 [(帖头行, [(aa行, color_or_None)...])...]；dl 不闭合，用 <dt> 分割。

    空行处理（2026-08-11 修正）：只跳过 dd 开头/结尾的模板空行（站点模板
    `<br>  <br>` 装饰）；正文中间的空行是作者留的分镜/段落间隔，是漫画的
    一部分，必须保留（旧版一刀切跳过导致画面过度压缩）。
    """
    posts = []
    parts = re.split(r'(?=<dt[ >])', seg)
    for part in parts:
        dt = re.search(r'<dt>(.*?)</dt>', part, re.S)
        if not dt:
            continue
        header = clean(dt.group(1)).strip()
        dd = re.search(r'<dd>(.*?)</dd>', part, re.S)
        body_html = dd.group(1) if dd else ''
        rows = []
        for row_html in re.split(r'<br\s*/?>', body_html):
            row = clean(row_html)
            rows.append(row)
        # 开头连续模板空行（站点装饰）
        s = 0
        while s < len(rows) and rows[s].strip() == '':
            s += 1
        # 结尾连续模板空行（站点装饰）
        e = len(rows)
        while e > s and rows[e - 1].strip() == '':
            e -= 1
        # 中间空行（作者分镜）保留
        rows = [(r, None) for r in rows[s:e]]
        posts.append((header, rows))
    return posts

def get_title(raw):
    m = re.search(r'<h3[^>]*><a[^>]*>(.*?)</a>', raw, re.S)
    if m:
        return clean(m.group(1)).strip()
    m = re.search(r'<title[^>]*>(.*?)</title>', raw, re.S)
    return clean(m.group(1)).strip() if m else 'unknown'

def entry_url(eid):
    return f"https://yaruomatomex.com/blog-entry-{eid}.html"

def extract(raw, eid=None, src_name=None):
    """-> (txt_lines, colormap, extras)"""
    eid = eid or (src_name or '').split('_')[0]
    title = get_title(raw)
    posts = parse_posts(extract_article(raw))
    txt_lines, colormap = build_txt(posts, title, entry_url(eid))
    return txt_lines, colormap, {}

def render_header(line):
    """帖头渲染：◆作者 → 绿粗+ID红粗；名無し → 绿非粗+ID黑"""
    import html as H

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
    RAW = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "raw_sample_3146.html")
    OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "aa_sample")
    eid = re.search(r'(\d+)', os.path.basename(RAW)).group(1)
    lines, cm, _ = extract(open(RAW, encoding='utf-8', errors='replace').read(), eid, os.path.basename(RAW))
    os.makedirs(OUT, exist_ok=True)
    base = os.path.basename(RAW).replace('.html', '')
    open(os.path.join(OUT, base + '.txt'), 'w', encoding='utf-8').write('\n'.join(lines))
    open(os.path.join(OUT, base + '_colormap.json'), 'w', encoding='utf-8').write(
        json.dumps(cm, ensure_ascii=False))
    print(f'{base}: {len(lines)} lines, {len(cm)} colors')
