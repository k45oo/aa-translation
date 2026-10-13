# -*- coding: utf-8 -*-

"""rusalka777.blog.fc2.com（FC2 博客「100%自分用やる夫まとめ」）解析器。

同一博客跨年代有两种排版，**都按行级判定**（铁律一），统一走 `_split_rows` + `HEADER_RE`：

- 新式（约 2016+，绝大多数章）：`<dl><br><dt><span>NNN</span> ： <span style="color:#0000FF;
  font-weight:bold;">◆作者</span> ： <span>日期</span> <span>ID:xxx</span></dt><dd>…<br>…</dd></dl>`
- 旧式（2011-2012 老章）：无 dl/dt/dd，正文直接以 `<br>` 流式排进 `<div class="article">`，
  帖头行 `NNN ： <Strong><FONT COLOR="#xxxxxx">作者</FONT></Strong>：YYYY/MM/DD(曜) HH:MM:SS ID:xxx`

其他要点：

- **每篇首帖没有帖头**（原作者排版时把首帖帖头去掉了，正文仍在，res 号与上一篇/下一篇连续）
  → 作为「无头前置块」原样保留（写 txt 时**不产生帖头行**，不塞空行占位）。
- 正文前有一段导航表 `<Div Align="right">…目次へ移動/第一話へ移動…`：整块删掉，
  否则链接文字会当成正文行混进 txt。
- 正文内**无颜色 span**（颜色只出现在帖头/导航），colormap 恒空。
- 行内空白照原样保留（与 `sites/yaruomatomex.py` 同口径：只剔帖子首尾的模板空行）——
  同一章在 yaruomatomex 镜像上的逐帖输出与之**逐字节一致**（重叠 139 帖实测 0 diff），
  故 `render_header`/作者样式沿用 yaruomatomex 口径（绿粗作者 + 红粗 ID）。
"""
import json, os, re, sys

try:
    from _base import (clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, ID_RED_BOLD,
                       AUTHOR_GREEN, ID_BLACK, NUM_STYLE, TIME_STYLE)
except ImportError:
    from sites._base import (clean, build_txt, render_common_header, AUTHOR_GREEN_BOLD, ID_RED_BOLD,
                             AUTHOR_GREEN, ID_BLACK, NUM_STYLE, TIME_STYLE)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根

SITE = "rusalka_fc2"

# 每篇首帖没有帖头（原作者排版时去掉，正文仍是楼主的）→ parse_posts 首项 header 为 None。
# line_classify_v2 据此把无头帖按**楼主帖**分类（is_lz=True）：否则 anon 分支会把 AA 纹理行
# （`:::::`、`Ｌr┐ ! . ,ノ /`）判成 text/mixed 送进模型（实测 16 章 179 行误送）。
HEADLESS_FIRST_IS_LZ = True

# 帖头（两种排版 clean 后同形）：`NNN ： 作者 ： YYYY/MM/DD(曜) HH:MM:SS ID:xxx`
# 旧式 `2496 ： ◆7BzL13s5r6：2012/08/17(金) …`（第二个 `：` 无空格）也吃。
HEADER_RE = re.compile(r'^(\d+\s*：\s*)(.+?)(\s*：\s*\d{4}/\d{2}/\d{2}.*?)(\s*ID:\S+.*)?$')

# 正文结束标记（取最靠前者）：评论区/相关文章/分享按钮。实测 comment_list 恒存在且最靠前。
_END_MARKERS = ('name="comment_list"', '<!--▼コメント', 'fc2_footer', 'relate_dl', '関連記事')
# 正文前导航表（<Div Align="right"><TABLE>…</TABLE></Div>）
_NAV_RE = re.compile(r'<Div[^>]*Align="right"[^>]*>.*?</Div>', re.S | re.I)


def extract_article(raw):
    i = raw.find('<div class="article">')
    if i == -1:
        i = 0
    end = len(raw)
    for mk in _END_MARKERS:
        k = raw.find(mk, i + 1)
        if k != -1 and k < end:
            end = k
    if end < len(raw):
        # 退到该标记所在标签的开头，别把 `<dl class="relate_dl` 这类残片切进正文
        k = raw.rfind('<', i, end)
        if k != -1:
            end = k
    seg = raw[i:end]
    seg = _NAV_RE.sub('', seg)                       # 导航表整块去掉
    seg = re.sub(r'</?d[ld]\b[^>]*>', '', seg, flags=re.I)   # dl/dt/dd 壳（不碰 div）
    seg = re.sub(r'<hr[^>]*>', '', seg, flags=re.I)          # 分隔线
    return seg


def _split_rows(seg):
    """正文 HTML → 行列表。

    `<dt>`/`<dd>` 前后预断行（帖头/正文块各自独立成行——站点模板里 `</dt><dd> 文本 <br>`
    之间**没有** `<br>`，不断行会把帖头后第一行正文并进帖头行），再按 `<br>` 切分逐行 clean。
    """
    seg = re.sub(r'(?=</?dt\b)|(?=</?dd\b)', '\n', seg, flags=re.I)
    rows = []
    for chunk in re.split(r'<br\s*/?>', seg, flags=re.I):
        t = clean(chunk)
        rows.extend(t.split('\n'))
    return rows


def parse_posts(seg):
    """→ [(帖头文本 or None, [(行, color_or_None)...]), ...]

    首帖无帖头 → 第一项 header 为 None（调用方原样输出正文行，不产帖头行）。
    每个帖子只剔**首尾**模板空行；正文中间空行（作者分镜）保留。
    """
    posts = []
    header = None
    rows = []

    def flush():
        if header is None and not rows:
            return
        s = 0
        while s < len(rows) and rows[s].strip() == '':
            s += 1
        e = len(rows)
        while e > s and rows[e - 1].strip() == '':
            e -= 1
        posts.append((header, [(r, None) for r in rows[s:e]]))

    for row in _split_rows(seg):
        if HEADER_RE.match(row.strip()):
            flush()
            header = row.strip()
            rows = []
        else:
            rows.append(row)
    flush()
    return posts


def get_title(raw):
    m = re.search(r'<h3[^>]*><a[^>]*>(.*?)</a>', raw, re.S)
    if m:
        return clean(m.group(1)).strip()
    m = re.search(r'<title[^>]*>(.*?)</title>', raw, re.S)
    return clean(m.group(1)).strip() if m else 'unknown'


def entry_url(eid):
    return f"https://rusalka777.blog.fc2.com/blog-entry-{eid}.html"


def extract(raw, eid=None, src_name=None):
    """-> (txt_lines, colormap, extras)"""
    eid = eid or (src_name or '').split('_')[0]
    title = get_title(raw)
    posts = parse_posts(extract_article(raw))
    txt_lines = [f'# {title}', f'# 原帖地址: {entry_url(eid)}']
    colormap = {}
    for header, rows in posts:
        if header:                      # 无头前置块：不写帖头行
            txt_lines.append(header)
        for row, color in rows:
            txt_lines.append(row)
            if color and row.strip():
                colormap[len(txt_lines) - 1] = color
    return txt_lines, colormap, {}


def render_header(line):
    """帖头渲染：◆作者 → 绿粗+ID红粗；名無し → 绿非粗+ID黑（与 yaruomatomex 同口径）"""
    import html as H

    m = re.match(HEADER_RE, line)
    if not m:
        return None
    num, author, rest, idpart = m.groups()
    if '◆' in author:
        author_style, id_style = AUTHOR_GREEN_BOLD, ID_RED_BOLD
    else:
        author_style, id_style = AUTHOR_GREEN, ID_BLACK
    out = f'<span style="{NUM_STYLE}">{H.escape(num.strip())}</span>'
    out += f'<span style="{author_style}">{H.escape(author)}</span>'
    out += f'<span style="{TIME_STYLE}">{H.escape(rest)}</span>'
    if idpart:
        out += f'<span style="{id_style}">{H.escape(idpart)}</span>'
    return out


if __name__ == '__main__':
    RAW = sys.argv[1] if len(sys.argv) > 1 else ''
    OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "aa_sample")
    eid = re.search(r'(\d+)', os.path.basename(RAW)).group(1)
    lines, cm, _ = extract(open(RAW, encoding='utf-8', errors='replace').read(), eid, os.path.basename(RAW))
    os.makedirs(OUT, exist_ok=True)
    base = os.path.basename(RAW).replace('.html', '')
    open(os.path.join(OUT, base + '.txt'), 'w', encoding='utf-8').write('\n'.join(lines))
    open(os.path.join(OUT, base + '_colormap.json'), 'w', encoding='utf-8').write(
        json.dumps(cm, ensure_ascii=False))
    hdrs = sum(1 for l in lines if HEADER_RE.match(l.strip()))
    print(f'{base}: {len(lines)} lines, {len(cm)} colors, {hdrs} headers')
