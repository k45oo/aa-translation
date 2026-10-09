# 交付 HTML → AA 原文 txt 反解析(2026-08-24)

把已汉化的交付 HTML(transtemple aaFont 等导出)反解析回管线可读的 `aa/<名>.txt`,
供后续汉化/对照。核心目标:**逐字节保留 AA 列对齐**,绝不能动空格和空行。

工具:`scripts/tools/reparse_delivery_html.py --in <交付HTML目录> --out <aa目录> [--names a.html,...]`
(无 --names 则处理全部 *.html;输出每话一个 txt:`# 标题` / `# 原帖地址:` / 帖头+AA 行原样)

## 铁律(用户 2026-08-24 三度强调,违反即全错位)
1. **绝不 strip 行首尾空格**。AA 制图靠空格对齐,coloumn 里的 U+3000(全角空格,宽 2)
   是每行核心对齐符。`strip()` / `rstrip` 会删掉它们 → 整篇 AA 错位。
   用户原话:"不要自作聪明乱删空格和空行,这些都是不能删的"。
2. **空行完整保留**,不删也不多加。
3. **U+3000 绝不替换成半角**。只有 `&nbsp;`/`&ensp;`/`&thinsp;`(及 `\xa0 \u2002 \u2003
   \u2009 \u2007 \u202f` 等窄空格 / &nbsp 实体转换值)→ 归一为**半角空格 `' '`(宽 1)**,
   因为浏览器在 AA 等宽字体里这些窄空格显示 1 格。

## HTML 三种导出格式(混合出现,必须通用解析)
同一交付目录可能混:
- `<body class="AA_Text"><p>...</p>`(逐楼 p,正常格式,如 113 话)
- `<div class="AA_Text"><div>...</div>`(整正文一个大 div 用 <br> 拆行,如 003 话)
- OrinrinEditor:`<body class="AA_Text"><dl><dd>...</dd>`(如 112 话(上))

**不能只按 `<p>` 切块**——无 `<p>` 结构的文件会被解析成 30B 空文件(003 话等 52 个踩坑)。
正解 = HTMLParser 通用解析:
- 定位正文起点:优先 `<body class="AA_Text">`,否则 `<div class="AA_Text">`
  (番外无 body,只有 div.AA_Text)
- `<br>` 是唯一真实行分隔;块级标签(div/p/dd/dl/section)是楼容器,遇**有内容的块边界**
  补一个换行(不额外造空行)
- 丢弃块间字面空白(标签间缩进/换行),保留含 U+3000 或可视字符的有效内容
  (`if any(c=='\u3000' or not c.isspace() for c in data): self.lines[-1]+=data`)
- convert_charrefs=True 会把 `&nbsp;` 变 `\xa0`,需再归一为半角;`&uml;`(¨)/`&acute;`(´)/
  `&and;`(∧)/`&hellip;`(…)等交 html.unescape

## 验证:防"d_w 高估窄空格导致误判错位"
`disp_width`(宽度规则 ASCII=1/其他=2)会把 U+2002、U+2009 等窄空格按"其他"算 2 格,
但它们在 AA 等宽字体真实显示 1 格 → 用它对比会出现大面积"txt 比 HTML 窄 1"的**假错位**
(003 话曾误报 461 处,实际 0 错)。正确对比:
```python
def dw_real(s):
    half   = len(re.findall(r'[\x00-\xff\uFF61-\uFF9F]', s))
    narrow = len(re.findall(r'[\u2000-\u200a\u202f\u00a0]', s))
    rest   = len(s)-half-narrow
    return half + narrow + 2*rest   # 窄空格按 1 格
```
对比交付 HTML 的渲染行(HTMLParser 产出,同样归一 &nbsp;)与反解析 txt 的非空行:
**窄空格=1 格时 003 全 1406 行 0 差异 = 真对齐**。

## 其他坑
- 文件名 vs 内容不一致:如"第19话 前篇.txt"实为**第189话 前篇**(标题可证),与正确命名的
  `第189话 前篇.txt` 重复 → 按标题内容判断,不要只看文件名;用 md5/正文前几行核对重复。
- 章节顺序:交付 HTML 无可靠 eid(href 全指向假 `blog-entry-38927`),只有"话数名"文件名。
  排序用外部章节清单(`章节顺序.txt`)→ 生成 `<work>/chapter_order.json`,`order` 存文件名
  (本作无 eid,不能像 r401 那样存 arcid)。

## 上脚 record
- 脚本重跑会从 still-in-交付目录 的 HTML 重新生成已删除的重复 txt(146话(1)、亚鲁欧189后篇、
  19话前篇),批量后需按 chapter_order 白名单清理 `order_set 里没有的 .txt`。
- 新建作品(如《时间停止探索者亚鲁欧》jikan_yaluo)注册 registry 供工作台首页显示;
  work_meta.json 加条目 author 归到相同 ◆tripcode(ポン＠◆uIesmhw5pA)使作者分组合并。
