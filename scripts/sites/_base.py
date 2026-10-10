# -*- coding: utf-8 -*-
"""sites/ 公共底座：HTML 清洗 + txt/colormap 组装。

各站点模块统一接口（供 common/extract_all.py 调用）：
    extract(raw, eid=None, src_name=None) -> (txt_lines, colormap, extras)
        txt_lines: 完整 txt 行（含 # 标题 / # 原帖地址 头行）
        colormap:  {完整txt行号: css样式}
        extras:    {附加文件名: 对象}（如 yaruobook 的评论 json）
    render_header(line) -> str | None    # 帖头行 → HTML（合集生成用；非帖头返回 None）
    entry_url(eid) -> str                # 原帖地址模板
    __main__: 单文件测试（python sites/<site>.py raw.html [out_dir]）
"""
import html as H
import re

# 帖头通用样式（按原站还原）
NUM_STYLE = 'color:#888'
TIME_STYLE = 'color:#999'
AUTHOR_GREEN_BOLD = 'color:#008800;font-weight:bold'
AUTHOR_GREEN = 'color:rgb(0,136,0)'
ID_RED_BOLD = 'color:#ff0000;font-weight:bold'
ID_BLUE_BOLD = 'color:#0000ff;font-weight:bold'
ID_BLACK = 'color:#000000'


def clean(s):
    t = re.sub(r'<br\s*/?>', '\n', s)
    t = re.sub(r'<[^>]+>', '', t)
    t = H.unescape(t)
    t = t.replace('\u00a0', ' ').replace('&nbsp;', ' ')
    lines = [l.rstrip() for l in t.split('\n')]
    out = []
    prev_blank = False
    for l in lines:
        blank = (l.strip() == '')
        if blank and prev_blank:
            continue
        out.append(l)
        prev_blank = blank
    return '\n'.join(out)


def build_txt(posts, title, url):
    """posts: [(帖头行, [(aa行, color_or_None)...])...] -> (txt_lines, colormap)"""
    txt_lines = [f'# {title}', f'# 原帖地址: {url}']
    colormap = {}
    for header, rows in posts:
        txt_lines.append(header)
        for row, color in rows:
            txt_lines.append(row)
            if color and row.strip():
                colormap[len(txt_lines) - 1] = color
    return txt_lines, colormap


def render_common_header(line, regex, author_style, id_style):
    """通用帖头渲染：正则 4 段 → 序号灰 / 作者 / 时间灰 / ID"""
    m = re.match(regex, line)
    if not m:
        return None
    num, author, rest, idpart = m.groups()
    out = f'<span style="{NUM_STYLE}">{H.escape(num)}</span>'
    out += f'<span style="{author_style}">{H.escape(author)}</span>'
    out += f'<span style="{TIME_STYLE}">{H.escape(rest)}</span>'
    if idpart:
        out += f'<span style="{id_style}">{H.escape(idpart)}</span>'
    return out
