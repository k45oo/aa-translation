# -*- coding: utf-8 -*-

"""man001.blog.2nt.com（2NT 博客，やる夫まとめ R-18）：<h3>entry 锚点 + <div class="article">。

- 帖头：`NNN 名前： ◆作者[] 投稿日：... ID:xxx`（作者绿粗 #008800、ID 蓝粗 #0000ff）
- 每行 AA 后是 <br>\n<br>（模板空行）：必须跳过空行片段，否则 AA 图被拉长
- 本作品无骰子红色/无加粗
- 截断：relate_dl（関連記事）
"""
import json, os, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根(2026-08-18 Phase 3 动态计算)

try:
    from _base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, ID_BLUE_BOLD
except ImportError:
    from sites._base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, ID_BLUE_BOLD

SITE = "blog_2nt"

HEADER_RE = re.compile(r'^(\d+\s*名前：\s*)(.+?)(\s*投稿日：.*?)(\s*ID:\S+.*)?$')

def extract_article(raw, eid):
    m = re.search(r'id="entry' + eid + r'"[^>]*>', raw)
    i = m.end() if m else raw.find('<div class="article">')
    # 正文从该 h3 之后的 <div class="article"> 开始
    art = raw.find('<div class="article">', i)
    if art != -1:
        i = art + len('<div class="article">')
    j = raw.find('relate_dl', i)
    if j == -1:
        j = len(raw)
    return raw[i:j]

def parse_posts(seg):
    """返回 [(帖头行, [(aa行, color_or_None)...])...]；2nt 站无 dl 结构，帖头行以 '数字 名前：' 开始"""
    posts = []
    cur_header = None
    cur_rows = []
    for raw_line in re.split(r'<br\s*/?>', seg):
        raw_line = raw_line.rstrip('\n')
        # 帖头行: 行首数字 + 名前：
        m = re.match(r'^\s*(\d+)\s*名前：', html_unescape_strip(raw_line))
        if m:
            if cur_header is not None:
                posts.append((cur_header, cur_rows))
            # 帖头颜色: 作者名绿色
            hd = clean(raw_line).strip()
            cur_header = hd
            cur_rows = []
            continue
        row = clean(raw_line).strip('\n')
        if row.strip() == '':
            continue
        # 2nt 模板每行后都有 <br>+空行, 跳过空行(原帖 AA 连续)
        cur_rows.append((row, None))
    if cur_header is not None:
        posts.append((cur_header, cur_rows))
    return posts

def html_unescape_strip(s):
    import html as H

    return H.unescape(re.sub(r'<[^>]+>', '', s))

def get_title_eid(raw, eid):
    m = re.search(r'<h3><a[^>]*id="entry' + eid + r'"[^>]*>(.*?)</a></h3>', raw, re.S)
    if not m:
        m = re.search(r'<h3><a[^>]*>(.*?)</a></h3>', raw, re.S)
    title = clean(m.group(1)).strip() if m else f'entry_{eid}'
    return title

def entry_url(eid):
    return f"http://man001.blog.2nt.com/blog-entry-{eid}.html"

def extract(raw, eid=None, src_name=None):
    """-> (txt_lines, colormap, extras)"""
    eid = eid or (src_name or '').replace('entry_', '').replace('.html', '')
    seg = extract_article(raw, eid)
    posts = parse_posts(seg)
    title = get_title_eid(raw, eid)
    txt_lines, colormap = build_txt(posts, title, entry_url(eid))
    return txt_lines, colormap, {}

def render_header(line):
    return render_common_header(line, HEADER_RE, AUTHOR_GREEN_BOLD, ID_BLUE_BOLD)

if __name__ == '__main__':
    RAW = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "aaManga", "不幸の催眠", "raw", "entry_13202.html")
    OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "aaManga", "不幸の催眠", "aa")
    m = re.search(r'entry_(\d+)', os.path.basename(RAW))
    eid = m.group(1) if m else '0'
    lines, cm, _ = extract(open(RAW, encoding='utf-8', errors='replace').read(), eid, os.path.basename(RAW))
    os.makedirs(OUT, exist_ok=True)
    base = os.path.basename(RAW).replace('.html', '')
    open(os.path.join(OUT, base + '.txt'), 'w', encoding='utf-8').write('\n'.join(lines))
    open(os.path.join(OUT, base + '_colormap.json'), 'w', encoding='utf-8').write(
        json.dumps(cm, ensure_ascii=False))
    print(f'{base}: {len(lines)} lines, {len(cm)} colors')
