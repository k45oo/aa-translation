---
name: aa-html-reparse
description: Use when 把已汉化交付HTML反解析还原成AA原文txt(多工具导出格式, 忠实保留空格空行).
---

# AA 交付 HTML 反解析（HTML→原文 txt）

## 触发
把 `aaManga/交付HTML/<作品>/` 下已汉化的单话 HTML（transtemple aaFont / keage / OrinrinEditor 等导出）反解析还原成 `aa/<名>.txt` 格式的纯文本，供管线/工作台/对照使用。

## 核心原则（用户反复强调，血的教训）
1. **绝不删空格**：AA 制图靠空格对齐。行首的 **U+3000（全角空格，宽2）是对齐核心，绝不能 strip/替换**。行尾空格也保留。
2. **绝不删空行**：空行是作者分镜/楼间分隔，是漫画一部分。**不加也不删**。
3. **忠实还原每行的字符序列**：反解析结果用等宽字体渲染，必须与交付 HTML 渲染**逐行显示宽度一致**。

## 脚本
`scripts/tools/reparse_delivery_html.py`（零依赖 stdlib）：
```
python scripts/tools/reparse_delivery_html.py --in aaManga/交付HTML/<作品> --out aaManga/<作品>/aa [--names a.html,b.html]
```
- `--names` 省略 = 处理全部 `*.html`
- 每话一个 txt：第1行 `# 标题`、第2行 `# 原帖地址`（若有）、然后帖头行+AA行+空行原样

## 关键实现（HTMLParser 精确复刻渲染行）
- 用 `html.parser.HTMLParser` 而非正则 re.sub（正则会把块标签转 \n 造成空行叠加、丢内容）
- `<br>` 是唯一真实行分隔 → 遇 br 断行
- 块级标签（p/div/dd/dt/dl/section/article/li）是楼容器：**仅当当前行已有内容时**在其边界补一个换行（不额外造空行）
- `handle_data`：丢弃标签间字面空白（HTML 源码缩进换行），保留含 U+3000 或可视字符的有效内容
- `convert_charrefs=True` 自动还原实体；但 `&nbsp;` 变 `\xa0`（U+00A0）

## 宽度关键（错位根因）
- **U+3000 全角空格 = 2 格**，绝不能动（AA 对齐核心）
- `&nbsp;`/`&ensp;`/`&thinsp;`（浏览器渲染 1 格）→ **必须归一为半角空格 `' '`（1格）**——若保留 `\xa0`/`\u2002` 等，管线的 disp_width 按"其他=2格"算 → 行宽虚高错位
- 半角片假名 U+FF61-FF9F = 1 格（MS Gothic 等宽实测）

## 验证方法（决定性）
- **用真实等宽宽度对比**：`dw(s) = half(ASCII+半角片假名) + narrow(U+2000-200A 等窄空格) + 2*rest`。窄空格必须算 1 格！
- 对比反解析 txt vs 交付 HTML 的同一行，全部非空行宽度一致 = 对齐正确
- **坑**：仓库 `aa_common.disp_width` 把 `\xa0`/`\u2002` 等当"其他=2格"——用它对比 HTML 会把窄空格误判为错位（假阳性）。验证用"窄空格=1格"的真实宽度。

## 多工具导出格式（交付 HTML 混杂）
| 格式 | 特征 | 处理 |
|---|---|---|
| transtemple AA_Text body | `<body class="AA_Text"><p>` 逐楼 | HTMLParser 统一 |
| transtemple AA_Text div | `<body class="AA_Text"><div>翻译</div><div>帖...<br>` | 同上 |
| keage 番外 | `<div class="AA_Text">` + `&nbsp;：&nbsp;` 帖头 + `<span color:green>` | 同上 |
| OrinrinEditor | `<dl><dd>帖头<br>...` | 同上 |
定位正文起点：优先 `<body class="AA_Text">`，否则 `<div class="AA_Text">`。

## 标题/实体坑
- 标题提取（`<h2>`）后**必须 html.unescape**——否则 `&hellip;`（…）/`&hearts;`（♥）/`&amp;`（&）/`&nbsp;`（空格）残留字面文本
- `&amp;` 在 `&amp;nbsp;` 这类双转义里要小心顺序：先还原特殊空格实体，再 html.unescape

## 排序/去重（作品无 eid 时）
- 交付 HTML 可能无可靠 eid（href 是假模板 `blog-entry-38927` 全一样）→ 按**文件名/话数**组织
- 用户提供 `章节顺序.txt`（HTML 文件名列表）= 权威阅读顺序 → 生成 `work/chapter_order.json`（`{"order": [文件名,...]}`，本作无 eid 存文件名）
- **md5 查重**：同内容不同命名的文件（如 `146话` vs `146话 (1)`、`亚鲁欧 第189话后篇` vs `189话后篇`）会混入——用 md5 识别重复，保留顺序文件收录的那个，删多余
- 脚本重跑会重新生成已删的重复文件（HTML 还在源目录）→ 重跑后按 chapter_order.json 清理（`set(order)` 过滤删除多余 txt）

## 校验清单
1. 反解析 txt 非空行 vs 交付 HTML 渲染：**宽度 0 不一致**
2. U+3000 开头行数 > 0（有 AA 图的作品）
3. `\xa0` 残留 = 0（&nbsp; 已归一半角）
4. 空行完整（不增不减）
5. 标题无 `&xxx;` 实体残留
6. 文件数与 chapter_order.json 一致

## 案例：时间停止探索者亚鲁欧（2026-08-24）
- 交付 `aaManga/交付HTML/时间停止探索者亚鲁欧/`（303 个 HTML，4 种格式混合）
- 反解析到 `aaManga/时间停止探索者亚鲁欧/aa/`，按 `章节顺序.txt` 排序，最终 300 txt
- 作者 ◆uIesmhw5pA（ポン）与 jikan_teishi 同作者；注册 registry `jikan_yaluo`
