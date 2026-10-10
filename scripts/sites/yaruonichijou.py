# -*- coding: utf-8 -*-

"""yaruonichijou.com（やる夫達のいる日常）：<span class="aa"> 括号配对法提取。

- 正文在 <span class="aa">...</span>，内部有嵌套 <span style="color:..."> → 必须括号配对
- 输出格式沿用白头系：`# 标题` + `# source: <文件名>` + `===== AA块 N =====` 分隔
- 无 colormap（该站提取时不还原颜色）
"""
import json, os, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根(2026-08-18 Phase 3 动态计算)

SITE = "yaruonichijou"


def extract_aa(raw):
    """Balanced <span class="aa"> extraction."""
    blocks = []
    for m in re.finditer(r'<span class="aa">', raw):
        start = m.end()
        depth, i = 1, start
        while depth > 0 and i < len(raw):
            nxt_open = raw.find('<span', i)
            nxt_close = raw.find('</span>', i)
            if nxt_close == -1:
                break
            if nxt_open != -1 and nxt_open < nxt_close:
                depth += 1; i = nxt_open + 5
            else:
                depth -= 1; i = nxt_close + 7
        blocks.append(raw[start:i-7])
    return blocks


def clean(b):
    t = re.sub(r'<br\s*/?>', '\n', b)
    t = re.sub(r'<[^>]+>', '', t)
    return __import__('html').unescape(t)


def get_title(raw):
    m = re.search(r'<h2 class="ently_title">.*?<a[^>]*>(.*?)</a>', raw, re.S)
    return re.sub(r'<[^>]+>', '', m.group(1)).strip() if m else None


def entry_url(eid):
    return f"https://yaruonichijou.com/blog-entry-{eid}.html"


def extract(raw, eid=None, src_name=None):
    """-> (txt_lines, colormap, extras)
    逐字节复刻旧 extract_aa.py（# 原帖地址 行替代 # source 行，对齐 restore 后的白头语料）：
    先按旧写入器拼出完整文本再 split('\n')，块间恰好一个空行、文件尾带一个 '\n'。"""
    blocks = extract_aa(raw)
    ttl = get_title(raw) or (src_name or 'unknown')
    if 'moegirl' in (src_name or ''):
        return [], {}, {}   # 萌娘百科页不是章节（旧 extract_aa 同样跳过）
    texts = [clean(b) for b in blocks]
    m = re.search(r'(\d+)', src_name or '')
    eid = eid or (m.group(1) if m else '')
    buf = f"# {ttl}\n# 原帖地址: {entry_url(eid)}\n"
    for i, t in enumerate(texts):
        buf += f"\n===== AA块 {i+1} =====\n"
        buf += t
        if not t.endswith('\n'):
            buf += '\n'
    return buf.split('\n'), {}, {}


def render_header(line):
    # 白头系帖头渲染由 works/postprocess.py 负责（专用样式）
    return None


if __name__ == '__main__':
    RAW = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "aaManga", "白头与染灰魔女", "raw", "61940_白頭と極点の魔女_第1話.html")
    OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "aaManga", "白头与染灰魔女", "aa")
    lines, cm, _ = extract(open(RAW, encoding='utf-8', errors='replace').read(), src_name=os.path.basename(RAW))
    os.makedirs(OUT, exist_ok=True)
    base = os.path.basename(RAW).replace('.html', '')
    open(os.path.join(OUT, base + '.txt'), 'w', encoding='utf-8').write('\n'.join(lines))
    print(f'{base}: {len(lines)} lines')
