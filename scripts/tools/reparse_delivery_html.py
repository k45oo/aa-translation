# -*- coding: utf-8 -*-
"""从交付 HTML 反解析为 AA 原文 txt（aa/<名>.txt 格式）。

背景：交付HTML/时间停止探索者亚鲁欧/ 里有 303 个按「话数」命名的已汉化 HTML
（transtemple aaFont 导出），无可靠 eid，需反解析成管线可读的纯文本。

支持两种 HTML 结构：
  1) 正常话数: <body class="AA_Text"><p>...</p>...</body>（帖头=纯文本<p>，内容<p>含<br/>）
  2) keage 番外: <div class="AA_Text"><div>...</div>（帖头含 &nbsp;：&nbsp; 与 <span color:green>）

格式：每话一个 txt，
  第1行  # <标题>
  第2行  # 原帖地址: <url>(若可提取)
  之后  帖头行 + AA/台词行原样

用法：
  python scripts/tools/reparse_delivery_html.py --in 交付HTML/时间停止探索者亚鲁欧 --out aaManga/时间停止探索者亚鲁欧/aa --names "xxx.html,yyy.html"
  （--names 省略则处理全部 *.html）
"""
import glob
import html as html_mod
import os
import re
import sys

# 特殊空格实体 → 半角空格(1格)，避免被 disp_width 按"其他"算 2 格错位
_SPECIAL_SPACES = {
    "&nbsp;": " ", "&ensp;": " ", "&thinsp;": " ", "&emsp;": " ",
    "&#160;": " ", "&#8194;": " ", "&#8195;": " ", "&#8201;": " ",
}

# 剩余命名/数字实体交给 html.unescape（uml/acute/hellip/gt 等）
def _unescape(s):
    for k, v in _SPECIAL_SPACES.items():
        if k in s:
            s = s.replace(k, v)
    s = html_mod.unescape(s)
    # convert_charrefs 已把 &nbsp;&ensp;&thinsp; 转成 \xa0/\u2002/\u2009 等特殊空格，
    # 统一归一为半角空格（宽度1，防 disp_width 按"其他"算 2 格错位）。
    # 注意：U+3000（全角空格，AA 制图核心对齐符，宽度2）绝不替换！
    for ch in ("\xa0", "\u2002", "\u2003", "\u2009", "\u2007", "\u202f"):
        s = s.replace(ch, " ")
    return s


def _strip_tags(s):
    """去 <span> 等标签(保留内部文字)；<br>/<br/> → 换行。"""
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"</?(?:span|strong|b|i|em|a|font)[^>]*>", "", s)
    # 兜底：去剩余任何标签
    s = re.sub(r"<[^>]+>", "", s)
    return s


def normalize_line(l):
    """单行规范化：只还原实体、去 \\r。**绝不删首尾空格**（AA 制图靠空格对齐）。"""
    l = _unescape(l)
    l = l.replace("\r", "")
    return l


def parse_amida_html(raw):
    """解析正常话数格式(<body class=AA_Text> + <p> 块)。"""
    title = ""
    url = ""
    m = re.search(r'<h2[^>]*><a class="kjax"[^>]*href="([^"]+)"[^>]*>(.*?)</a></h2>', raw, re.S)
    if m:
        url = m.group(1).strip()
        title = _strip_tags(m.group(2)).strip()
    ps = re.findall(r"<p[^>]*>(.*?)</p>", raw, re.S)
    body_lines = []
    for p in ps:
        inner = _strip_tags(p).strip("\n")
        if not inner.strip():
            body_lines.append("")
            continue
        # 块内按 \n 拆行
        for ln in inner.split("\n"):
            body_lines.append(normalize_line(ln))
        body_lines.append("")   # 楼间空行
    return title, url, body_lines


def _locate_body(raw):
    """定位正文起点。返回 (start, in_pre)：

    - 优先 <body class=AA_Text> / <div class=AA_Text>（正文按标签分块）
    - 再次纯 <pre>（transtemple/其他工具导出的单块纯文本，换行=真实行、
      首尾空白有意义）：切片会跳过 <pre> 开始标签，所以要把 in_pre 带给解析器，
      否则整章会被当成一个超长 data（文件写出来看着对，内部 line 数却是 1）
    """
    for pat in (r'<body class="AA_Text">', r'<div class="AA_Text">'):
        m = re.search(pat, raw)
        if m:
            return m.end(), False
    m = re.search(r"<pre[^>]*>", raw)
    if m:
        return m.end(), True
    return None, False


def parse_universal(raw):
    """通用解析：忠实还原 AA 内容（不变动空格/空行）。

    - 用 HTMLParser 精确复刻"渲染行"：`<br>` 是唯一真实行分隔；块级标签
      （div/p/dd/dl/section）是楼容器，遇有内容的块边界补一个换行（不额外造空行）。
    - 只还原实体（&nbsp;等特殊空格→半角，防 disp_width 误算 2 格；其余交 html.unescape）。
    - **绝不 strip 行首尾空格**，U+3000（全角对齐）原样保留；空行完整保留。
    """
    from html.parser import HTMLParser

    class _AAExtract(HTMLParser):
        BLOCK = {"p", "div", "dd", "dt", "dl", "section", "article", "li"}

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.lines = [""]
            self.in_pre = False

        def _break(self):
            self.lines.append("")

        def _flush_if_content(self):
            if self.lines[-1].strip():
                self._break()

        def handle_starttag(self, tag, attrs):
            if tag == "br":
                self._break()
            elif tag == "pre":
                # <pre> 内换行=真实行，且空白全有意义（AA 对齐核心）
                self._flush_if_content()
                self.in_pre = True
            elif tag in self.BLOCK:
                self._flush_if_content()
            else:
                pass

        def handle_endtag(self, tag):
            if tag == "br":
                self._break()
            elif tag == "pre":
                self.in_pre = False
            elif tag in self.BLOCK:
                self._flush_if_content()

        def handle_startendtag(self, tag, attrs):
            if tag == "br":
                self._break()

        def handle_data(self, data):
            if not data:
                return
            if self.in_pre:
                # pre 语义：字面换行即行界，行首行尾空白（含纯空白行）原样保留
                data = data.replace("\r", "")
                parts = data.split("\n")
                self.lines[-1] += _unescape(parts[0])
                for part in parts[1:]:
                    self.lines.append(_unescape(part))
                return
            # 丢弃块间字面空白；保留含 U+3000 或可视字符的有效内容
            if any(c == "\u3000" or not c.isspace() for c in data):
                self.lines[-1] += _unescape(data)

    title, url = "", ""
    m = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>\s*</h2>', raw, re.S)
    if not m:
        m = re.search(r"<h2[^>]*>(.*?)</h2>", raw, re.S)
    if m:
        if m.lastindex == 2 and m.group(1).startswith("http"):
            url = m.group(1).strip()
        title = _unescape(re.sub(r"<[^>]+>", "", m.group(m.lastindex)).strip())
    if not title or re.fullmatch(r"[A-Za-z0-9_]+", title):
        # 导出工具写死的占位标题（如 <title>AA_Translated</title>）→ 丢弃
        title = ""
    start, in_pre = _locate_body(raw)
    body = raw[start:] if start is not None else raw
    parser = _AAExtract()
    parser.in_pre = in_pre          # <pre> 切片已跳过开始标签，得手动带进去
    try:
        parser.feed(body)
    except Exception:
        pass
    lines = []
    for ln in parser.lines:
        ln = ln.replace("\r", "")
        lines.append(normalize_line(ln))
    if not title:
        # 兜底：正文首个非空行（"站点 | 作品名　第N話" 这种取竖线后半段）
        # 跳过 "翻译：xxx / 作者：xxx" 这类标注行，别把署名当标题
        for ln in lines:
            t = ln.strip()
            if not t:
                continue
            if len(t) > 80:
                break
            if re.match(r"^(翻译|翻譯|作者|来源|來源|原帖)\s*[:：]", t):
                continue
            parts = re.split(r"\s*[|｜]\s*", t)
            title = parts[-1].strip() if len(parts) > 1 else t
            break
    return title, url, lines


def reparse_file(path):
    """统一走通用解析器（兼容 AA_Text body/div、OrinrinEditor <dl><dd>、纯 <br> 流）。"""
    raw = open(path, encoding="utf-8-sig").read()
    return parse_universal(raw)


def main():
    args = [a for a in sys.argv[1:]]
    in_dir = out_dir = names = None
    i = 0
    while i < len(args):
        if args[i] == "--in" and i + 1 < len(args):
            in_dir = args[i + 1]; i += 2
        elif args[i] == "--out" and i + 1 < len(args):
            out_dir = args[i + 1]; i += 2
        elif args[i] == "--names" and i + 1 < len(args):
            names = [n.strip() for n in args[i + 1].split(",") if n.strip()]
            i += 2
        else:
            i += 1
    if not in_dir or not out_dir:
        print("用法: reparse_delivery_html.py --in <dir> --out <dir> [--names a.html,b.html]")
        return
    files = [os.path.join(in_dir, n) for n in names] if names else \
        sorted(glob.glob(os.path.join(in_dir, "*.html")))
    os.makedirs(out_dir, exist_ok=True)
    n_ok = n_fail = 0
    for f in files:
        base = os.path.splitext(os.path.basename(f))[0]
        try:
            title, url, lines = reparse_file(f)
            if not title:
                title = base          # 标题兜底：用文件名
            out_path = os.path.join(out_dir, base + ".txt")
            with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("# " + title + "\n")
                if url:
                    fh.write("# 原帖地址: " + url + "\n")
                for ln in lines:
                    fh.write(ln + "\n")
            n_ok += 1
            print(f"OK  {base}.txt  (标题: {title[:40]}, {len(lines)}行)")
        except Exception as e:
            n_fail += 1
            print(f"FAIL {base}: {e}")
    print(f"\n完成: 成功 {n_ok}, 失败 {n_fail} → {out_dir}")


if __name__ == "__main__":
    main()
