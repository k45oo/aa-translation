# -*- coding: utf-8 -*-

"""touhouyaruosure.com（東方やる夫スレ纏め，Rails/Honox 现代站）：第七站点。

- 正文不在章节页 HTML 里：章节页壳 + `/api/chapters/<eid>/content` 返回正文
  （`<dl><dl><br/><dt>帖头</dt><dd>AA<br>...</dd>...</dl></dl>`）
- dl 时把 API 正文注入章节页 `<div id="c-chapter-content">` 的 skeleton 位，raw 存单文件
- 帖头：`NNN ： ◆作者(绿粗) ： 时间 ID(红粗)`（同 bookshelf；无 ◆ 时绿非粗/ID 黑）
- dd 开头有 ` <br/>` 模板空行，必须跳过（2nt 同款坑）
- 红色 span（rgb(255,0,0)）跨 <br> 断裂 = 骰子结果行 → 区间重叠检测还原
- 蓝色 span（#0000ff）= `>>N` 行内引用，量少不还原（bookshelf 同策略）
- 无 <b> 加粗（无 1D10）
"""
import json, os, re, sys

try:
    from _base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, AUTHOR_GREEN, ID_RED_BOLD, ID_BLACK
except ImportError:
    from sites._base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, AUTHOR_GREEN, ID_RED_BOLD, ID_BLACK
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根(2026-08-18 Phase 3 动态计算)

SITE = "touhouyaruosure"

RED_RE = re.compile(r'<span[^>]*style="[^"]*(?:red|255,\s*0,\s*0)[^"]*"[^>]*>', re.I)
HEADER_RE = re.compile(r'^(\d+\s*：\s*)(.+?)(\s*：\s*\d{4}/\d{2}/\d{2}.*?)(\s*ID:\S+.*)?$')


def extract_body(raw):
    """c-chapter-content div 内的正文（注入后的 API content）

    旧格式（2024 贞操逆転）：`<dl><dl>...` 双 dl，`</dl></dl>` 闭合
    新格式（2025 蒼星石）：`<dl class="thread">...` 单 dl，单个 `</dl>` 闭合
    """
    i = raw.find('id="c-chapter-content"')
    if i == -1:
        return ''
    start = raw.find('>', i) + 1
    # 新格式优先：单 </dl> 闭合；旧格式 </dl></dl>
    end = raw.find('</dl></dl>', start)
    if end == -1:
        end = raw.find('</dl>', start)
    if end == -1:
        return ''
    return raw[start:end + 5]


def extract_article(raw):
    """兼容 line_classify（v1/v2）调用的别名"""
    return extract_body(raw)


def parse_posts(seg):
    """返回 [(帖头行, [(aa行, color_or_None)...])...]；dl 闭合良好，按 <dt> 分割"""
    posts = []
    parts = re.split(r'(?=<dt[ >])', seg)
    for part in parts:
        dt = re.search(r'<dt[^>]*>(.*?)</dt>', part, re.S)
        if not dt:
            continue
        header = clean(dt.group(1)).strip()
        header = re.sub(r'\s*\n\s*', ' ', header)   # span 标签间换行 → 空格
        dd = re.search(r'<dd[^>]*>(.*?)</dd>', part, re.S)
        body_html = dd.group(1) if dd else ''
        # 红色 span 完整区间（span 可能跨 <br> 断裂，按 </span> 配对）
        red_ranges = []
        for m in RED_RE.finditer(body_html):
            close = body_html.find('</span>', m.end())
            if close != -1:
                red_ranges.append((m.start(), close + 7))
        rows = []
        pos = 0
        for row_html in re.split(r'(<br\s*/?>)', body_html):
            if row_html.startswith('<br'):
                pos += len(row_html)
                continue
            row_start = pos
            pos += len(row_html)
            row_end = pos
            row = clean(row_html).strip('\n')
            color = None
            # 区间重叠法：行与任一红色 span 区间重叠（含跨行断裂）
            for rs, re_ in red_ranges:
                if row_start < re_ and row_end > rs:
                    color = 'color: rgb(255, 0, 0);'
                    break
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


def get_title(raw):
    m = re.search(r'<h2[^>]*class="text-xl font-semibold mb-2"[^>]*>(.*?)</h2>', raw, re.S)
    if m:
        return clean(m.group(1)).strip()
    m = re.search(r'<title[^>]*>(.*?)</title>', raw, re.S)
    return clean(m.group(1)).strip() if m else 'unknown'


def entry_url(eid):
    return f"https://touhouyaruosure.com/chapters/{eid}"


# 页面自引用：首个 preload 就是本章正文 API（第二个是下一章预取）
SELF_URL_RE = re.compile(r'href="/api/chapters/(\d+)/content"')


def chapter_url(raw, eid):
    """原帖地址：优先页面自引用的章节 id。

    r401 系作品的 eid 是 r401 的 arcid（≠ 站点章节 id），直接 entry_url(eid)
    会 404（2026-09-18 セイレイ 209 话全是死链）；页面首个 `as="fetch"` preload
    指向本章正文，最可靠，取不到才退回 entry_url(eid)。
    """
    m = SELF_URL_RE.search(raw)
    return entry_url(m.group(1) if m else eid)


def extract(raw, eid=None, src_name=None):
    """-> (txt_lines, colormap, extras)"""
    eid = eid or (src_name or '').split('_')[0]
    title = get_title(raw)
    posts = parse_posts(extract_body(raw))
    if not posts:
        return [], {}, {}
    txt_lines, colormap = build_txt(posts, title, chapter_url(raw, eid))
    return txt_lines, colormap, {}


def render_header(line):
    m = re.match(HEADER_RE, line)
    if not m:
        return None
    author = m.group(2)
    if author.startswith('◆'):
        astyle, istyle = AUTHOR_GREEN_BOLD, ID_RED_BOLD
    else:
        astyle, istyle = AUTHOR_GREEN, ID_BLACK
    return render_common_header(line, HEADER_RE, astyle, istyle)


if __name__ == '__main__':
    RAW = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "aaManga", "やる夫のねっとり貞操逆転世界", "raw", "entry_9553.html")
    OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "aaManga", "やる夫のねっとり貞操逆転世界", "aa")
    m = re.search(r'(\d+)', os.path.basename(RAW))
    eid = m.group(1) if m else '0'
    lines, cm, _ = extract(open(RAW, encoding='utf-8', errors='replace').read(), eid, os.path.basename(RAW))
    os.makedirs(OUT, exist_ok=True)
    base = os.path.basename(RAW).replace('.html', '')
    open(os.path.join(OUT, base + '.txt'), 'w', encoding='utf-8').write('\n'.join(lines))
    open(os.path.join(OUT, base + '_colormap.json'), 'w', encoding='utf-8').write(
        json.dumps(cm, ensure_ascii=False))
    print(f'{base}: {len(lines)} lines, {len(cm)} colors')
