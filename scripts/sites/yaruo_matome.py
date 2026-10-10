# -*- coding: utf-8 -*-


"""yaruo-matome.com（やる夫まとめ）：<div class="entry-content"> 内 <dt>/<dd> 或 <p> 流式。

新格式（2023+）：`<dt>NNN ： 作者 ： 时间 ID:xxx</dt><dd><p>AA<br></p></dd>`（作者绿粗 #008800、ID 红粗 #ff0000）
旧格式（2021-）：`<p>.NNN ： 作者 (ﾜｯﾁｮｲ xxx) ：日期 ID<br>AA行<br>...</p>` 单块多 res 流式

- 帖头：`NNN ： ◆作者 ： 时间 ID:xxx`（新格式）
- 旧格式行首带 `.` 前缀（原帖 res 标记）→ 剥掉
- 截断：相关文章 / 评论区
"""
import html as H
import json
import os
import re
import sys

try:
    from _base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, ID_RED_BOLD, \
        NUM_STYLE, TIME_STYLE, AUTHOR_GREEN, ID_BLACK
except ImportError:
    from sites._base import clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, ID_RED_BOLD, \
        NUM_STYLE, TIME_STYLE, AUTHOR_GREEN, ID_BLACK

SITE = "yaruo_matome"

HEADER_RE = re.compile(r'^(\d+\s*：\s*)(.+?)(\s*：\s*\d{4}/\d{2}/\d{2}.*?)(\s*ID:\S+.*)?$')

CONTAINER = '<div class="entry-content"'


def extract_article(raw):
    i = raw.find(CONTAINER)
    if i == -1:
        i = raw.find('<div class="entry-body">')
    if i == -1:
        i = 0
    end = len(raw)
    for marker in ('relate_dl', '関連記事', 'コメント', 'comment'):
        j = raw.find(marker, i)
        if j != -1:
            end = j
            break
    return raw[i:end]


def dd_rows(body_html):
    """dd 正文 → 行列表（**保留原文空行**）。

    该站的空行有两种写法，都算空行（2026-09-18 用同作品 yaruobook.com 镜像逐行对齐实测：
    保留时 3106/3118 行空行数一致，丢弃时只有 3044 一致，且 yb 镜像同处也是空行）：

    - 行内空行：`A <br /> &nbsp;<br /> &nbsp;<br /> B` ≡ yb 侧 `A <br><br><br> B`（2 空行）
    - 段尾空行：`A <br /> &nbsp;</p>` = 段末一个空行（`&nbsp;` 渲染出来就是空行）
    - 整段只有 `&nbsp;`（`<p> &nbsp;</p>`）= 一个空行

    `<p>` 段落边界本身不算换行（实测对齐后与 yb 一致）；只裁帖首/帖尾的模板空行。
    """
    rows = []
    for ch in body_html.split('</p>'):
        # 剥段首标签：`<p ...>` / `<dd ...>`（`\b` 防误吃 <pre> 等；写法 `<p[ >][^>]*>` 会多要一个 `>` 而吃掉整行）
        ch = re.sub(r'^\s*<(?:p|dd)\b[^>]*>', '', ch, count=1, flags=re.S)
        has_nbsp = ('&nbsp;' in ch) or ('\u00a0' in ch)
        if clean(ch).strip() == '' and not has_nbsp:
            continue                      # 段落标签之间的空白，不是作者空行
        # 逐个 <br> 段单独 clean（clean() 会把同一 chunk 内连续空行折叠，不能整段丢进去）
        for piece in re.split(r'<br\s*/?>', ch):
            r = clean(piece).lstrip('\r\n').rstrip()
            rows.append(r if r.strip() else '')
    s = 0
    while s < len(rows) and rows[s] == '':
        s += 1
    e = len(rows)
    while e > s and rows[e - 1] == '':
        e -= 1
    return rows[s:e]


def parse_posts(seg):
    """新格式：按 <dt> 切分；dt 为帖头，dd 为正文。"""
    posts = []
    parts = re.split(r'(?=<dt[ >])', seg)
    for part in parts:
        dt = re.search(r'<dt[^>]*>(.*?)</dt>', part, re.S)
        if not dt:
            continue
        header = clean(dt.group(1)).strip()
        dd = re.search(r'<dd[^>]*>(.*?)</dd>', part, re.S)
        rows = dd_rows(dd.group(1) if dd else '')
        if rows:
            posts.append((header, [(r, None) for r in rows]))
    return posts


# 旧格式帖头：`.202 ： ポン ◆uIesmhw5pA (ﾜｯﾁｮｲ 8baf-1545) ：2019/06/30(日) 20:01:24 MxD5r1i.00`
# 行首带 `.` 前缀（原帖 res 行标记）；帖头特征 = `NNN ：` + `(ﾜｯﾁｮｲ` 或日期
OLD_HEADER_FEAT_RE = re.compile(r'^\.?\d+\s*：.*(?:ﾜｯﾁｮｲ|\d{4}/\d{2}/\d{2}\()')


def parse_posts_old(seg):
    """旧格式（<p> 流式）：一个 <p> 块内含多个 res，帖头 `NNN ： 作者 ...`。

    - 帖头：`.202 ： ポン ◆uIesmhw5pA (ﾜｯﾁｮｲ 8baf-1545) ：2019/06/30(日) 20:01:24 MxD5r1i.00`
    - AA 行前也有 `.` 前缀（原帖行号残留）→ 剥掉
    - 无颜色 span
    """
    posts = []
    # 按帖头切分（行首 `.NNN ：` + ﾜｯﾁｮｲ/日期特征）
    parts = re.split(r'(?m)^(?=\.?\d+\s*：)', seg)
    for part in parts:
        m = OLD_HEADER_FEAT_RE.match(part)
        if not m:
            continue
        # 找帖头行结束（第一个 <br>）
        br = part.find('<br')
        if br == -1:
            continue
        header_html = part[:br]
        header = clean(header_html).strip()
        # 剥帖头行首 `.` 前缀（原帖 res 标记）
        header = re.sub(r'^\. ?', '', header)
        if not header:
            continue
        body = part[br:]
        rows = []
        for row_html in re.split(r'<br\s*/?>', body):
            row = clean(row_html).lstrip('\r\n')
            # 剥行首 `.` 前缀 + 紧跟的半角空格（原帖 res 标记，AA 对齐不需要）
            row = re.sub(r'^\. ?', '', row)
            row = row.rstrip()
            rows.append(row if row.strip() else '')   # 空行保留（原文空行硬约束）
        # 只跳帖首/帖尾模板空行
        s = 0
        while s < len(rows) and rows[s] == '':
            s += 1
        e = len(rows)
        while e > s and rows[e - 1] == '':
            e -= 1
        if rows[s:e]:
            posts.append((header, [(r, None) for r in rows[s:e]]))
    return posts


def has_dt_posts(seg):
    """检测 entry-content 内是否有正文 dt（`<dt>数字 ：` 或 `<dt><span>数字</span> ：`）"""
    return bool(re.search(r'<dt[^>]*>\s*(?:<span[^>]*>\s*)?\d+\s*(?:</span>\s*)?：', seg))


def parse_posts_auto(seg):
    """meta/台词提取入口：**与 extract() 同口径** 分派（新格式 <dt> / 旧格式 `.NNN ：` <p> 流式）。
    classify_raw_v2 走这个钩子——只调 parse_posts 会让 2021 前旧格式章返回 0 帖，
    行数校验必然不齐 → 整章 meta 跳过（2026-09-13 修 pon_nikutsubo 等）。"""
    return parse_posts(seg) if has_dt_posts(seg) else parse_posts_old(seg)


def get_title(raw):
    m = re.search(r'<title[^>]*>(.*?)</title>', raw, re.S)
    if m:
        t = H.unescape(m.group(1)).strip()
        t = re.sub(r'^(やる夫まとめくす|やる夫スレ自分チェック用|暇な時にやる夫まとめ)\s*', '', t)
        t = re.sub(r'\s*[-|]\s*[^\-|]+$', '', t)   # 剥「 - 站点名」后缀
        return t
    m = re.search(r'property="og:title" content="([^"]*)"', raw)
    return H.unescape(m.group(1)).strip() if m else 'unknown'


def entry_url(eid):
    return f"https://yaruo-matome.com/archives/{eid}"


def _site_url(raw, eid):
    for pat in (r'rel="canonical" href="([^"]+)"', r'property="og:url" content="([^"]+)"'):
        m = re.search(pat, raw)
        if m:
            return m.group(1)
    return entry_url(eid)


def extract(raw, eid=None, src_name=None):
    eid = eid or (src_name or '').split('_')[0]
    title = get_title(raw)
    seg = extract_article(raw)
    if has_dt_posts(seg):
        posts = parse_posts(seg)
    else:
        posts = parse_posts_old(seg)
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
        print('usage: python sites/yaruo_matome.py <raw.html> [out_dir]')
        sys.exit(0)
    eid = re.search(r'(\d+)', os.path.basename(RAW)).group(1)
    lines, cm, _ = extract(open(RAW, encoding='utf-8', errors='replace').read(), eid, os.path.basename(RAW))
    print(f'{os.path.basename(RAW)}: {len(lines)} lines, {len(cm)} colors')
    if OUT:
        os.makedirs(OUT, exist_ok=True)
        base = os.path.basename(RAW).replace('.html', '')
        open(os.path.join(OUT, base + '.txt'), 'w', encoding='utf-8').write('\n'.join(lines))
        open(os.path.join(OUT, base + '_colormap.json'), 'w', encoding='utf-8').write(
            json.dumps(cm, ensure_ascii=False))
        print('  ->', os.path.join(OUT, base + '.txt'))
