# -*- coding: utf-8 -*-

"""yaruobook.com（やる夫スレ本棚，WordPress/Cocoon）：entry-content cf 内 <dl>，到 </dl> 截断。

- 帖头作者帖：`NNN ： ◆作者 ： 时间 ID:xxx`（作者绿粗 #008800、ID 红粗 #ff0000）
- 帖头名無し帖：`NNN ： 名無しさん ： 时间 ID:xxx`（作者绿非粗、ID 黑）
- 作者帖 dt/dd 带 class（author-res-dt/author-res）→ 必须 <dt[^>]*>/<dd[^>]*> 匹配
- 正文红色 span（骰子/安価结果 `color: rgb(255, 0, 0)` / `#ff0000` / `color:red` / `<font color="#ff0000">`）
  → colormap 整行标红（span 常跨 <br> 断裂，按区间重叠判定，同 bookshelf/fc2）
- 评论区 <ol class="commets-list"> → extras['entry_<pid>_comments.json']
"""
import html as H
import json, os, re, sys

try:
    from _base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, AUTHOR_GREEN, ID_RED_BOLD, ID_BLACK
except ImportError:
    from sites._base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, AUTHOR_GREEN, ID_RED_BOLD, ID_BLACK
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根(2026-08-18 Phase 3 动态计算)

SITE = "yaruobook"

HEADER_RE = re.compile(r'^(\d+\s*：\s*)(.+?)(\s*：\s*\d{4}/\d{2}/\d{2}.*?)(\s*ID:\S+.*)?$')

# 正文红色 span 开标签（span 的 style 或 font 的 color 属性；含 rgb/#ff0000/red 三种写法）
RED_OPEN_RE = re.compile(
    r'<(span[^>]*style="[^"]*(?:rgb\(\s*255\s*,\s*0\s*,\s*0\s*\)|#ff0000\b|\bred\b)[^"]*"[^>]*'
    r'|font[^>]*color="[^"]*(?:rgb\(\s*255\s*,\s*0\s*,\s*0\s*\)|#ff0000\b|\bred\b)[^"]*"[^>]*)>',
    re.I)
RED_STYLE = 'color: rgb(255, 0, 0);'


def _red_ranges(body_html):
    """正文里红色 span/font 的完整字符区间 [(start, end)...]。

    两条坑（2026-10-21 实测）：① 开标签与内容常被 `<br>` 隔开 → 按闭标签配对，不能按行切；
    ② 原站红字里会嵌一层无样式 `<span>>>N</span>` → 必须按同名标签**嵌套计数**找配对闭标签，
    否则取到内层 `</span>`、红区只剩前半截。
    """
    out = []
    for m in RED_OPEN_RE.finditer(body_html):
        tag = 'font' if m.group(1).lower().startswith('font') else 'span'
        pat = re.compile(r'<(/?)%s\b[^>]*>' % tag, re.I)
        depth, end = 1, None
        for t in pat.finditer(body_html, m.end()):
            if t.group(1):
                depth -= 1
                if depth == 0:
                    end = t.end()
                    break
            else:
                depth += 1
        if end:
            out.append((m.start(), end))
    return out


_TAG_OR_ENT_RE = re.compile(r'<[^>]*>|&[#0-9a-zA-Z]+;')


def _row_plain_map(row_html):
    """行 HTML → (纯文本, [每个字符在 row_html 里的 (start, end)])。

    与 `_base.clean()` 同口径剥标签 + 实体解码（`&#9314;`→③ 占 1 字符），
    供「红字片段」在清洗后的行文本里定位。
    """
    plain, spans = [], []
    pos = 0
    for m in _TAG_OR_ENT_RE.finditer(row_html):
        if m.start() > pos:                       # 标签/实体之前的普通文本
            for k in range(pos, m.start()):
                plain.append(row_html[k])
                spans.append((k, k + 1))
        tok = m.group(0)
        if not tok.startswith('<'):               # 实体：解码后按字符记同一区间
            txt = H.unescape(tok).replace('\u00a0', ' ')
            for ch in txt:
                plain.append(ch)
                spans.append((m.start(), m.end()))
        pos = m.end()
    for k in range(pos, len(row_html)):
        plain.append(row_html[k])
        spans.append((k, k + 1))
    return ''.join(plain), spans


def _disp_w(s):
    """显示宽度（ASCII=1、其他=2，项目统一口径）；aa_common 可用时用权威实现"""
    try:
        from aa_common import disp_width
        return disp_width(s)
    except Exception:
        return sum(1 if ord(c) < 128 else 2 for c in s)


def _row_red_texts(row_html, red_ranges, row_start):
    """这一行里红字片段：[{"text": 片段文本, "cols": [起始显示列, 结束显示列]}, ...]

    `text` 给原文行精确匹配（译文改了这段就匹配不上）；
    `cols` 是**原行显示列**，译文换了文字时按列位回填（渲染端兜底）。
    """
    if not red_ranges:
        return []
    plain, spans = _row_plain_map(row_html)
    out = []
    for rs, re_ in red_ranges:
        a, b = rs - row_start, re_ - row_start
        idxs = [i for i, (s, e) in enumerate(spans) if s < b and e > a]
        if not idxs:
            continue
        seg = plain[idxs[0]:idxs[-1] + 1]
        lead = len(seg) - len(seg.lstrip(' \u3000'))
        trail = len(seg) - len(seg.rstrip(' \u3000'))
        start = idxs[0] + lead
        end = idxs[-1] + 1 - trail
        txt = plain[start:end]
        if not txt or any(x['text'] == txt for x in out):
            continue
        w1 = _disp_w(plain[:start])
        out.append({'text': txt, 'cols': [w1, w1 + _disp_w(txt)]})
    return out




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
    """返回 [(帖头行, [(aa行, color_or_None)...]), ...]；dl 闭合良好，按 <dt> 分割（作者帖带 class 也要匹配）"""
    posts = []
    parts = re.split(r'(?=<dt[ >])', seg)
    for part in parts:
        dt = re.search(r'<dt[^>]*>(.*?)</dt>', part, re.S)
        if not dt:
            continue
        header = clean(dt.group(1)).strip()
        dd = re.search(r'<dd[^>]*>(.*?)</dd>', part, re.S)
        body_html = dd.group(1) if dd else ''
        red_ranges = _red_ranges(body_html)
        rows = []
        pos = 0
        for row_html in re.split(r'(<br\s*/?>)', body_html):
            if row_html.startswith('<br'):
                pos += len(row_html)
                continue
            row_start, pos = pos, pos + len(row_html)
            row_end = pos
            row = clean(row_html).strip('\n')
            # 红字片段（原站只给 `③>>843`/`【1D100:75】` 这类片段上色，不是整行）
            reds = _row_red_texts(row_html, red_ranges, row_start)
            color = None
            if reds:
                color = {'style': RED_STYLE, 'spans': reds}
                # 整行都被红字 span 包住（如 `【1D100:43】≦75で妊娠` 独占一行）→ 标 all，
                # 译文里片段对不上时仍可按整行上色
                if len(reds) == 1 and reds[0]['text'] == row.strip(' \u3000'):
                    color['all'] = True
            rows.append((row, color))
        # 只跳 dd 开头/结尾模板空行；正文中间作者分镜空行保留（2026-08-11，同 yaruomatomex）
        s = 0
        while s < len(rows) and rows[s][0].strip() == '':
            s += 1
        e = len(rows)
        while e > s and rows[e - 1][0].strip() == '':
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
    txt_lines, colormap = build_txt(posts, title, real_url(raw, eid))
    extras = {}
    if comments:
        extras[f'entry_{eid}_comments.json'] = comments
    return txt_lines, colormap, extras


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
    if cm:
        open(os.path.join(OUT, base + '_colormap.json'), 'w', encoding='utf-8').write(
            json.dumps(cm, ensure_ascii=False))
    for fname, obj in extras.items():
        open(os.path.join(OUT, fname), 'w', encoding='utf-8').write(json.dumps(obj, ensure_ascii=False, indent=1))
    print(f'{base}: {len(lines)} lines, {len(cm)} colors, {len(extras)} extras')
