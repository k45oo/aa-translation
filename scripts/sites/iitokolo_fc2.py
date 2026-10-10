# -*- coding: utf-8 -*-

"""iitokolo.blog.fc2.com（FC2 博客，BAR『初音ミク』系）：<div class="article"> 直排流式。

- 正文区：<div class="article"> 起，到 <dl class="relate_dl">（関連記事）截断
- 帖头两种（无 <dl>/<dt>/<dd>，直接排入 article）：
  - 作者帖：`NNN 名前：◆作者[] 投稿日：YYYY/MM/DD(...) HH:MM:SS ID:xxx`（无 <strong> 包裹，纯文本）
  - 读者帖：`NNN 名前：<strong><font color="#000088"><b>どこかの名無しさん</b></font></strong>[sage] 投稿日：...`
  - 作者名 ◆MIZ/REl/eE（含 /，HEADER_RE 须容忍）
- 颜色：作者 #008800 绿粗、读者 #000088 蓝粗（原站嵌在 <font color>，非 class）
- 正文 <br> 分隔；空行 = <br><br> 保留（分镜空行是排版，勿删）
- 截断：relate_dl（関連記事）前；无 fc2_footer
"""
import json, os, re, sys

try:
    from _base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, AUTHOR_GREEN, ID_BLUE_BOLD, ID_BLACK
except ImportError:
    from sites._base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, AUTHOR_GREEN, ID_BLUE_BOLD, ID_BLACK
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根

SITE = "iitokolo_fc2"

# 帖头（重建后）双格式，各用独立 4 组正则（render_common_header 需 4 组，不能交替）：
# - 新格式（2013+）：`NNN 名前：作者[] 投稿日：YYYY/MM/DD(…) HH:MM:SS ID:xxx`
# - 旧格式（2012-2013）：`NNN ：作者 ：YY/MM/DD HH:MM:SS ID:xxx`（无「名前：」无「投稿日：」）
# 作者名可含 ◆/字母/斜杠；名無し=どこかの名無しさん。括号组：num 作者 余下 ID部分
HEADER_RE_NEW = re.compile(
    r'^(\d+\s+名前：\s*)(.+?)(\s*\[[^\]]*\]\s*投稿日：\s*\d{4}/\d{2}/\d{2}\(\S\)\s*\d{2}:\d{2}:\d{2}\s*)(ID:\S+.*)?$')
HEADER_RE_OLD = re.compile(
    r'^(\d+\s*：\s*)(.+?)(\s*：\s*\d{2}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}\s*)(ID:\S+.*)?$')
# 判定用合并正则（仅判断是否帖头，不取组）
HEADER_RE = re.compile(HEADER_RE_NEW.pattern + r'|' + HEADER_RE_OLD.pattern)


def extract_article(raw):
    i = raw.find('<div class="article">')
    if i == -1:
        i = raw.find('<div class="entry_body">')
    j = raw.find('<dl class="relate_dl"', i)
    if j == -1:
        j = raw.find('関連記事', i)
    if j == -1:
        j = len(raw)
    return raw[i:j]


def _rebuild_header(hdr_html):
    """帖头 HTML → 纯文本（剥标签+unescape+去NBSP）。"""
    h = re.sub(r'<[^>]+>', '', hdr_html)
    h = __import__('html').unescape(h).replace('\u00a0', ' ')
    return re.sub(r'\s+', ' ', h).strip()


def parse_posts(seg):
    """按帖头切分，返回 [(帖头行, [(aa行, color_or_None)...])...]。

    策略（2026-08-31 修正，704 楼号丢失事故）：不依赖单个跨行正则扫数字→投稿日
    （body 中「引用某楼 704 名前：」文本会让数字匹配错位、吞楼号/串行）。
    改为：按 <br>/<hr> 切逻辑行 → 行级判定帖头 → 该行即帖头行（整行剥标签后保留，含 ID）。

    帖头双格式（2026-08-31 补，早期章 1630-1637）：
    - 新格式（2013+）：`NNN 名前：作者[] 投稿日：YYYY/MM/DD(…) HH:MM:SS ID:xxx`
    - 旧格式（2012-2013）：`NNN ：作者 ：YY/MM/DD HH:MM:SS ID:xxx`（无「名前：」无「投稿日：」，短日期）
    """
    posts = []
    # 统一分隔符：<br> 与 <hr>（FC2 长文分页线）都当行界
    logical = re.split(r'<br\s*/?>|<hr[^>]*>', seg)
    headers = []  # (行索引, 帖头文本)
    for idx, line_html in enumerate(logical):
        text = _rebuild_header(line_html)
        if not text:
            continue
        if _is_header_line(text):
            headers.append((idx, text))
    if not headers:
        return posts
    bounds = [h[0] for h in headers] + [len(logical)]
    for i, (idx, header) in enumerate(headers):
        end = bounds[i + 1]
        rows = []
        for row_html in logical[idx + 1:end]:
            row = clean(row_html)
            if row.strip() == '.':
                continue
            rows.append(row)
        # 只跳首尾模板空行；正文中间作者分镜空行保留（2026-08-28 硬约束）
        s = 0
        while s < len(rows) and rows[s].strip() == '':
            s += 1
        e = len(rows)
        while e > s and rows[e - 1].strip() == '':
            e -= 1
        rows = rows[s:e]
        posts.append((header, [(r, None) for r in rows]))
    return posts


# 帖头判定（双格式）：
# - 新: `数字 名前：...投稿日：YYYY/MM/DD(`
# - 旧: `数字 ：...：YY/MM/DD HH:MM:SS`
def _is_header_line(text):
    if re.match(r'^\d+\s+名前：', text) and re.search(r'投稿日：\s*\d{4}/\d{2}/\d{2}\(', text):
        return True
    # 旧格式：`数字 ：` 开头 + `：短日期` 中间（作者名可含 ◆/字母/空格）
    if re.match(r'^\d+\s*：', text) and re.search(r'：\s*\d{2}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}', text):
        return True
    return False


def get_title(raw):
    m = re.search(r'<title[^>]*>(.*?)</title>', raw, re.S)
    if m:
        t = __import__('html').unescape(m.group(1)).strip()
        # 剥「面白かったやる夫スレをまとめるサイト」前缀
        t = re.sub(r'^面白かったやる夫スレをまとめるサイト\s*', '', t)
        return t
    m = re.search(r'property="og:title" content="([^"]*)"', raw)
    return __import__('html').unescape(m.group(1)).strip() if m else 'unknown'


def entry_url(eid):
    return f"https://iitokolo.blog.fc2.com/blog-entry-{eid}.html"


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
    # 双格式各用独立正则（render_common_header 需 4 组）
    m = HEADER_RE_NEW.match(line) or HEADER_RE_OLD.match(line)
    if not m:
        return None
    num, author, rest, idpart = m.groups()
    # 作者帖（◆）绿粗 + ID 蓝粗；名無し读者帖无样式（原站读者也是蓝，但作者才是主角）
    if '◆' in author:
        astyle, istyle = AUTHOR_GREEN_BOLD, ID_BLUE_BOLD
    else:
        astyle, istyle = AUTHOR_GREEN, ID_BLACK
    return render_common_header(line, m.re, astyle, istyle)


if __name__ == '__main__':
    RAW = sys.argv[1] if len(sys.argv) > 1 else ''
    OUT = sys.argv[2] if len(sys.argv) > 2 else ''
    if not RAW:
        print('usage: python sites/iitokolo_fc2.py <raw.html> [out_dir]')
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
