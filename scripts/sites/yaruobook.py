# -*- coding: utf-8 -*-

"""yaruobook.com（やる夫スレ本棚，WordPress/Cocoon）：entry-content cf 内 <dl>，到 </dl> 截断。

- 帖头作者帖：`NNN ： ◆作者 ： 时间 ID:xxx`（作者绿粗 #008800、ID 红粗 #ff0000）
- 帖头名無し帖：`NNN ： 名無しさん ： 时间 ID:xxx`（作者绿非粗、ID 黑）
- 作者帖 dt/dd 带 class（author-res-dt/author-res）→ 必须 <dt[^>]*>/<dd[^>]*> 匹配
- 评论区 <ol class="commets-list"> → extras['entry_<pid>_comments.json']
"""
import json, os, re, sys

try:
    from _base import clean, render_common_header, AUTHOR_GREEN_BOLD, AUTHOR_GREEN, ID_RED_BOLD, ID_BLACK
except ImportError:
    from sites._base import clean, render_common_header, AUTHOR_GREEN_BOLD, AUTHOR_GREEN, ID_RED_BOLD, ID_BLACK
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根(2026-08-18 Phase 3 动态计算)

SITE = "yaruobook"

HEADER_RE = re.compile(r'^(\d+\s*：\s*)(.+?)(\s*：\s*\d{4}/\d{2}/\d{2}.*?)(\s*ID:\S+.*)?$')


def extract_body(raw):
    """提取 entry-content 内全部正文段（可能含多个 <dl> 块，勿用第一个 </dl> 截断）。

    yaruobook 的 entry-content 可能拆成多个 <dl> 块：标题图一帖一 dl，正文又是新的 dl
    （实测 entry_20400 有 2 个 dl、49 个 dd；旧实现取第一个 </dl> 只拿到标题图 37 行）。
    → 从 entry-content 起点到 </article>（或末尾），整段交给 parse_posts 用 <dt> 分割。
    """
    i = raw.find('class="entry-content cf"')
    if i == -1:
        i = raw.find('entry-content cf')
    if i == -1:
        return ''
    # 到 </article> 或文章尾截断（评论区/侧边栏在 article 外）
    j = raw.find('</article>', i)
    if j == -1:
        j = len(raw)
    return raw[i:j]


def extract_article(raw):
    """line_classify 依赖别名：提取正文段（= extract_body）。"""
    return extract_body(raw)


def parse_posts(seg):
    """返回 [(帖头行, [aa行...]), ...]；dl 闭合良好，按 <dt> 分割（作者帖带 class 也要匹配）"""
    posts = []
    parts = re.split(r'(?=<dt[ >])', seg)
    for part in parts:
        dt = re.search(r'<dt[^>]*>(.*?)</dt>', part, re.S)
        if not dt:
            continue
        header = clean(dt.group(1)).strip()
        dd = re.search(r'<dd[^>]*>(.*?)</dd>', part, re.S)
        body_html = dd.group(1) if dd else ''
        rows = []
        for row_html in re.split(r'<br\s*/?>', body_html):
            row = clean(row_html).strip('\n')
            rows.append(row)
        # 只跳 dd 开头/结尾模板空行；正文中间作者分镜空行保留（2026-08-11，同 yaruomatomex）
        s = 0
        while s < len(rows) and rows[s].strip() == '':
            s += 1
        e = len(rows)
        while e > s and rows[e - 1].strip() == '':
            e -= 1
        rows = rows[s:e]
        posts.append((header, rows))
    return posts


COMMENT_RE = re.compile(r'<li class="comment[^"]*"[^>]*id="comment-(\d+)"(.*?)</li>', re.S)


def parse_comments(raw):
    """返回 [{'id','author','date','content','likes','depth'}...]"""
    i = raw.find('<ol class="commets-list">')
    if i == -1:
        return []
    j = raw.find('</ol>', i)
    seg = raw[i:j]
    out = []
    for m in COMMENT_RE.finditer(seg):
        cid, body = m.group(1), m.group(2)
        author = re.search(r'<cite class="fn">(.*?)</cite>', body, re.S)
        date = re.search(r'<div class="comment-meta[^"]*">.*?>\s*([^<]*?)\s*</a>', body, re.S)
        content_m = re.search(r'<div class="comment-content">(.*?)</div>', body, re.S)
        likes = re.search(r'cld-like-count-wrap[^>]*>\s*(\d+)?\s*<', body)
        depth = re.search(r'class="comment[^"]*depth-(\d+)', m.group(0))
        # content 截断到 like 区域（cld-like 在 comment-content 内部，不能混入计数；截到所在 <div 开头）
        raw_content = content_m.group(1) if content_m else ''
        cut = raw_content.find('cld-like')
        if cut != -1:
            cut = raw_content.rfind('<div', 0, cut)
            raw_content = raw_content[:cut]
        txt = clean(raw_content).strip() if raw_content else ''
        out.append({
            'id': int(cid),
            'author': __import__('html').unescape(author.group(1)).strip() if author else '',
            'date': __import__('html').unescape(date.group(1)).strip() if date else '',
            'content': txt,
            'likes': int(likes.group(1)) if likes and likes.group(1) else 0,
            'depth': int(depth.group(1)) if depth else 1,
        })
    return out


def get_title(raw, pid):
    m = re.search(r'<h1[^>]*>(.*?)</h1>', raw, re.S)
    return clean(m.group(1)).strip() if m else f'entry_{pid}'


def entry_url(eid):
    return f"https://yaruobook.com/?p={eid}"


_URL_FROM_RAW = re.compile(
    r'<link[^>]*rel="canonical"[^>]*href="([^"]+)"'
    r'|<meta[^>]*property="og:url"[^>]*content="([^"]+)"')


def real_url(raw, eid):
    """优先取 raw 页面 canonical/og:url 的真实地址（别馆 yaruobook.net 与
    yaruobook.com 同解析器但域名不同——硬编码 entry_url 会写错地址行），
    缺失时 fallback entry_url(eid)。"""
    m = _URL_FROM_RAW.search(raw)
    if m:
        url = m.group(1) or m.group(2)
        if url and url.startswith("http"):
            return url
    return entry_url(eid)


def extract(raw, eid=None, src_name=None):
    """-> (txt_lines, colormap, extras)；extras 含评论区 json"""
    eid = eid or (src_name or '').replace('entry_', '').replace('.html', '')
    title = get_title(raw, eid)
    posts = parse_posts(extract_body(raw))
    comments = parse_comments(raw)
    txt_lines = [f'# {title}', f'# 原帖地址: {real_url(raw, eid)}']
    for header, rows in posts:
        txt_lines.append(header)
        for row in rows:
            txt_lines.append(row)
    extras = {}
    if comments:
        extras[f'entry_{eid}_comments.json'] = comments
    return txt_lines, {}, extras


def render_header(line):
    m = re.match(HEADER_RE, line)
    if not m:
        return None
    author = m.group(2)
    # 2026-08-25: 楼主判定支持 ◆ 和 ★(sujin 系作者用「すじん ★」, 无 ◆)——含 ◆/★ 即视为楼主绿粗
    if '◆' in author or '★' in author:
        astyle, istyle = AUTHOR_GREEN_BOLD, ID_RED_BOLD
    else:
        astyle, istyle = AUTHOR_GREEN, ID_BLACK
    return render_common_header(line, HEADER_RE, astyle, istyle)


if __name__ == '__main__':
    RAW = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "aaManga", "魔法戦姫サマーカラーとスケベ体育教師やる夫", "raw", "entry_84463.html")
    OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "aaManga", "魔法戦姫サマーカラーとスケベ体育教師やる夫", "aa")
    m = re.search(r'entry_(\d+)', os.path.basename(RAW))
    pid = m.group(1) if m else '0'
    lines, cm, extras = extract(open(RAW, encoding='utf-8', errors='replace').read(), pid, os.path.basename(RAW))
    os.makedirs(OUT, exist_ok=True)
    base = os.path.basename(RAW).replace('.html', '')
    open(os.path.join(OUT, base + '.txt'), 'w', encoding='utf-8').write('\n'.join(lines))
    for fname, obj in extras.items():
        open(os.path.join(OUT, fname), 'w', encoding='utf-8').write(json.dumps(obj, ensure_ascii=False, indent=1))
    print(f'{base}: {len(lines)} lines, {len(extras)} extras')
