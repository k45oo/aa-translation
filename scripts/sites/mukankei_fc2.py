# -*- coding: utf-8 -*-

"""mukankei769.blog114.fc2.com（やる夫我執＆モララーのビデオ棚，老式 FC2 表格模板）。

- 正文起点：`<p class="FontTitle">■日期　<a id="EID" name="EID">标题</a>`（id=EID 锚点，页面顶部
  有同 class 的导航 FontTitle，必须用 `<a id="{eid}"` 锚定）；到 relate_dl（関連記事）截断。
- 帖头：`NNN 名前：<font color=red><b>◆作者</b></font>　[sage]　投稿日：YYYY/MM/DD(...) HH:MM:SS ID:xxx`
  - 作者帖红粗（<font color=red><b>，罕见！其他 FC2 站是绿粗）
  - 读者帖 `<font color="green"><b>小さな名無しさん</b></font>`（带引号 green）
  - 评论区在 relate_dl 之后（<a name="commentNNNN">），是博客评论非原スレ对话，不保留
- 正文行 <br> 分隔；<hr size="1" /> 分页线（当行界，不产出空行）
- R18 站需先过 FC2 年龄门（dl_chapters 的 dl_age_gate 处理，POST age_verification=1 种 cookie）
"""
import json, os, re, sys

try:
    from _base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, AUTHOR_GREEN, ID_RED_BOLD, ID_BLACK
except ImportError:
    from sites._base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, AUTHOR_GREEN, ID_RED_BOLD, ID_BLACK
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根

SITE = "mukankei_fc2"

# 帖头（重建后）双格式，各用独立 4 组正则（render_common_header 需 4 组，不能交替）：
# - 新格式：`NNN 名前：作者[sage] 投稿日：[：]YYYY/MM/DD(...) HH:MM:SS ID:xxx`（投稿日：： 双冒号容忍）
# - 旧格式：`NNN ：作者 ：YYYY/MM/DD(...) HH:MM:SS ID:xxx`（无「名前：」，如 3085）
# 作者名可含 ◆/字母/斜杠；读者=小さな名無しさん/名無しのやる夫だお等。
HEADER_RE_NEW = re.compile(
    r'^(\d+\s+名前：\s*)(.+?)(\s*(?:\[[^\]]*\]|\bsage\b)?\s*投稿日[：:]{1,2}\s*\d{4}/\d{2}/\d{2}\(\S\)\s*\d{2}:\d{2}:\d{2}(?:\.\d+)?\s*)(ID:\S+.*)?$')
HEADER_RE_OLD = re.compile(
    r'^(\d+\s*：\s*)(.+?)(\s*：\s*\d{4}/\d{2}/\d{2}\(\S\)\s*\d{2}:\d{2}:\d{2}(?:\.\d+)?\s*)(ID:\S+.*)?$')
# 判定用合并正则（仅判断是否帖头，不取组）
HEADER_RE = re.compile(HEADER_RE_NEW.pattern + r'|' + HEADER_RE_OLD.pattern)


def extract_article(raw, eid):
    """正文区：<a id="{eid}" name="{eid}"> 标题锚点 → relate_dl。"""
    i = raw.find(f'id="{eid}" name="{eid}"')
    if i == -1:
        i = raw.find(f'id="{eid}"')
    if i == -1:
        # 兜底：FontTitle 里含该 eid
        m = re.search(r'<p class="FontTitle">[^<]*<a[^>]*id="' + re.escape(eid) + r'"[^>]*>', raw)
        if m:
            i = m.start()
        else:
            return ""
    j = raw.find('relate_dl', i)
    if j == -1:
        j = raw.find('関連記事', i)
    # fc2_footer（Tweet 按钮）在 relate_dl 前，更早截断（往前找 <div 标签边界，不留半个标签）
    k = raw.find('fc2_footer', i)
    if k != -1 and (j == -1 or k < j):
        d = raw.rfind('<div', i, k)
        j = d if d != -1 else k
    if j == -1:
        j = len(raw)
    return raw[i:j]


def _rebuild_header(hdr_html):
    """帖头 HTML → 纯文本（剥标签+unescape+去NBSP）。"""
    h = re.sub(r'<[^>]+>', '', hdr_html)
    h = __import__('html').unescape(h).replace('\u00a0', ' ')
    return re.sub(r'\s+', ' ', h).strip()


def _is_header_line(text):
    """帖头判定（双格式）：
    - 新格式：行首数字 + 名前： + 含 投稿日[：:]{1,2}YYYY/MM/DD(
    - 旧格式：行首数字 ： + 含 ：YYYY/MM/DD(（无「名前：」，如 3085）
    """
    if re.match(r'^\d+\s+名前：', text) and re.search(r'投稿日[：:]{1,2}\s*\d{4}/\d{2}/\d{2}\(', text):
        return True
    if re.match(r'^\d+\s*：', text) and re.search(r'：\s*\d{4}/\d{2}/\d{2}\(\S\)\s*\d{2}:\d{2}:\d{2}', text):
        return True
    return False


def parse_posts(seg):
    """按帖头切分，返回 [(帖头行, [(aa行, color_or_None)...])...]。

    按 <br>/<hr> 切逻辑行 → 行级判定帖头（防 body 中「引用某楼 NNN 名前：」误判）。
    """
    posts = []
    logical = re.split(r'<br\s*/?>|<hr[^>]*>', seg)
    headers = []
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
    """<title>：`站名 [标题]`——取方括号后的正文标题；无方括号取整体。"""
    m = re.search(r'<title[^>]*>(.*?)</title>', raw, re.S)
    if m:
        t = __import__('html').unescape(m.group(1)).strip()
        # 形如「やる夫我執＆モララーのビデオ棚　[愛と　＜●＞...] 【１８禁】君の眼を……」
        m2 = re.search(r'【１８禁】[^【]*$|【18禁】[^【]*$|■[^<]*$', t)
        if m2:
            return m2.group(0).strip()
        # 剥站名前缀
        t = re.sub(r'^やる夫我執＆モララーのビデオ棚[^\[]*', '', t).strip()
        return t or 'unknown'
    m = re.search(r'property="og:title" content="([^"]*)"', raw)
    return __import__('html').unescape(m.group(1)).strip() if m else 'unknown'


def entry_url(eid):
    return f"https://mukankei769.blog114.fc2.com/blog-entry-{eid}.html"


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
    # 双格式各用独立正则（render_common_header 需 4 组）
    m = HEADER_RE_NEW.match(line) or HEADER_RE_OLD.match(line)
    if not m:
        return None
    num, author, rest, idpart = m.groups()
    # 作者帖（◆）红粗 + ID 红粗（原站 <font color=red>，罕见红作者）；
    # 读者帖（小さな名無しさん等）绿非粗 + ID 黑
    if '◆' in author:
        astyle, istyle = AUTHOR_GREEN_BOLD, ID_RED_BOLD
    else:
        astyle, istyle = AUTHOR_GREEN, ID_BLACK
    return render_common_header(line, m.re, astyle, istyle)


if __name__ == '__main__':
    RAW = sys.argv[1] if len(sys.argv) > 1 else ''
    OUT = sys.argv[2] if len(sys.argv) > 2 else ''
    if not RAW:
        print('usage: python sites/mukankei_fc2.py <raw.html> [out_dir]')
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
