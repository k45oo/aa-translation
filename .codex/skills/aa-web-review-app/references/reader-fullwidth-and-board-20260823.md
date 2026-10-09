# 工作台阅读页全宽 + 揭示板功能（2026-08-23）

会话：给 AA 工作台加"揭示板"顶层菜单 + 阅读页改全宽布局。本篇是 SKILL.md 上方两个条目（全宽布局、揭示板 dat 解析三坑）的完整脑记，供复现。

## 阅读页全宽：用户明确要求"目录贴左、正文填充右侧空间"

**问题**：默认 `.container { max-width:1200px; margin:18px auto; padding:0 20px 80px }` 让阅读器（目录栏+正文）整体居中、两边大幅留白。用户原话"目前阅读页整体居中两边有空，需要改成目录贴左边，正文填充右侧空间"。

**实现（3 处改动，缺一不可）**：
1. `templates/base.html`：`<main class="container">` → `<main class="container{% block container_class %}{% endblock %}">`
2. `static/style.css`：`.container { ... }` 之后加 `.container.wide { max-width:none; margin:18px 0 0; padding:0 0 80px; }`
   - **`padding` 必须写成 `0 0 80px`（去左右）**。若只写 `max-width:none`，`.container` 的 `padding:0 20px` 仍在 → 目录栏 left=20、正文右缘比视口少 20px，没真正撑满。实测两道坑都踩过。
3. reader.html / manga.html / board.html：`{% extends "base.html" %}` 后立即 `{% block container_class %} wide{% endblock %}`。

**验证（浏览器 console，勿只看代码）**：
- 首页：`document.querySelector('main.container')` → `className=="container"`、`maxWidth=="1200px"`、`margin=="18px 131.5px"`（居中，不受影响——**必须保持居中**）
- 阅读页：`className=="container wide"`、`maxWidth=="none"`、`getBoundingClientRect().width==document.documentElement.clientWidth`（=视口满宽）、`.reader-nav` 的 `left==0`（贴左）、`.reader-main` 的 `right==视口宽`（撑满右）

## ⚠️ 全宽"没生效"= 浏览器缓存旧 CSS（本次最深坑）

用户报"bat 启动目录没贴左"。一开始怀疑 bat 用的 python 环境（uv 全局 python vs `.venv`），但实测两个环境都从磁盘读同一份代码，返回的 HTML 都是 `<main class="container wide"`——**服务端完全正确**。

真相：`base.html` 里 `style.css?v=20260813b` 版本号没变，用户的浏览器用**缓存里的旧 style.css**（旧版无 `.container.wide`），容器于是仍 1200 居中。**改 CSS/JS 后必须 bump `?v=`**（改成了 `v=20260823a`），用户浏览器再 Ctrl+F5。

**排查套路（可复用）**：
1. `curl -s http://127.0.0.1:8765/static/style.css?v=<当前版本>` | grep container.wide —— 确认服务端 CSS 是新的
2. `curl -s http://127.0.0.1:8765/work/fukou/read | grep -oE '<main class="[^"]*"'` —— 确认服务端 HTML 输出 `wide`
3. 两步都对 → 问题必在浏览器缓存 → bump `?v=` 版本号
（curl 请求 CJK URL（`/board/◆JWKY...`）时 git-bash 的 curl 要 urlencode 授权码，参考主 skill 的 quote() 教训）

## 揭示板功能架构（逻辑层可单测）

路由：`GET /board`（列作者）→ `GET /board/{author}?file=<dat>&page=N`（贴列表+阅读，`BOARD_PER_PAGE=400`）。
logic.py 函数（`import html` 加在顶部）：
- `board_collections()` → 扫 `_ROOT/揭示板` 下子目录，每目录=一个作者/题材连载
- `board_slaves(author)` → 读 `index.json`（权威，含 res/ts，按スレ目编号 int 排序）；缺时回退扫磁盘 `*_<eid>.dat` + 解析首行日期
- `parse_board_dat(path)` → `{title, res:[{no,name,date,id,body}]}`；**dat 每 res 一行格式 `名前<>mail<>投稿日<>本文<>[标题]`**，`len(parts)<3` 跳过，名字段 `<b>◆作者</b>` 用 `_clean_dat_field` 剥标签（`re.sub(r"<[^>]+>","",field)`），投稿日正则抓日期 + `ID[:：]([A-Za-z0-9+/=]+)`
- `_board_row_html(raw_row)` → 单行安全 HTML（见下）

`_safe_board_author` 防路径穿越：拒绝 `/` `\\` `.` `..`，`os.path.realpath(d).startswith(realpath(BOARD_ROOT)+os.sep)`。

## dat 解析三坑（逐一定因）

① **HTML 实体**：`html.unescape` 解码 `&gt;`→`>`、`&lt;`→`<`、`&#9829;`→`♥`、`&#10084;`→`❤`、`&#8943;`→`…`。
② **`<b>` 加粗**：正文 `【1D11:8】` 等骰子结果被 `<b>...</b>` 包（20 处）。顺序必须：`unescape` → `replace("<b>","").replace("</b>","")`（**先剥原站标签**，否则 `html.escape` 把它转成可见文本 `&lt;b&gt;`，随后 regex 又加一个 `<b>` → 双重加粗且显示字面标签）→ `html.escape(quote=False)` 防其它 `<>` 注入 → `re.sub(r"【(1D[\d:]+)】", r"<b>【\1】</b>", row)` 重建。**作者名字段的 `<b>◆作者</b>` 在 dat 里是 `</b>X<b>` 倒序（2ch 特征），名字解析时已剥，别当正文**。
③ **空行保留**：`body_raw.split("<br>")` 后**不 filter**（原站漫画分镜/段落空行必须在）；模板 `{% if row.strip() %}<span class="aal">{{ row|safe }}</span>{% else %}<span class="aal">&nbsp;</span>{% endif %}`。**`|safe` 必须**（row 已是 unescape+escape+加粗后的安全 HTML，再被 Jinja 转义就变字面）。空行 `.aal` 高 16px=等宽行高，占一行。

**骰子行标红**：yarufox FOX 板**原站就没有红色 span**（全文件 grep `color:`/`<font`/`<span style` = 0 处），不标红即正确还原——**勿自行扩展红字**。

**验证（console）**：
- 加粗：`document.querySelectorAll('.bres')[7].querySelectorAll('.bres-body b').length //=1`
- 空行：`[...document.querySelectorAll('.bres')[0].querySelectorAll('.bres-body .aal')].filter(s=>!s.textContent.trim()||s.textContent==='\u00a0').length`
- 引用：`.filter(s=>s.textContent.includes('>>')).length`
- ♥：`.filter(s=>s.textContent.includes('♥')).length`

## 揭示板全局搜索（当前作者全部 dat，2026-08-23）

**路由**：`GET /api/board/search?author=<名>&q=<关键词>&limit=N`（200，cap 500）。`_safe_board_author` 校验后调 `logic.board_search`，每条结果补 `page`（=`(no-1)//400+1`，按 `BOARD_PER_PAGE=400` 算跳转页）。返回 `{count, results:[{file,eid,label,no,name,date,preview,page}]}`。

**logic.py 新增**：
- `_BOARD_PARSE_CACHE`（模块级 dict `path->(mtime,parsed)`）+ `_board_parse_cached(path)`：`parse_board_dat` 的 mtime 缓存，搜索遍历 14 个 ~5MB 贴免重复读盘。**注意 `parse_board_dat` 依赖 `_clean_dat_field`**——别把它删了（曾误删导致 NameError）。
- `_board_plain(row)`：`_board_row_html` 输出（含 `<b>`/实体）→ 纯文本，`html.unescape(re.sub(r"<[^>]+>","",row))`，供匹配+预览。
- `_board_hit_preview(body_plain, ql)`：提取含键词行的可读预告。`_AA_ONLY_RE` 剥纯绘图字符（**正则 `-` 必须转义或在字符类末尾**——`[…—–-·…]` 会把 `–-·` 当范围报 `bad character range`；用 `\u2014\u2013\u00b7` 十六进制转义 + `-` 放最末）。优先级：含词行剥绘图符 → 含词行原样 → 首个非空行。
- `board_search(author,q,limit)`：遍历 `board_slaves` 全部贴 → `_board_parse_cached` → 每楼 body 拼纯文本 `lower()` 匹配。**按贴序+楼号升序，到 limit 提前 return**。

**前端 board.html**（author 分支末尾，`{% if author %}` 包裹面板 + 搜索 script）：
- 入口：board-head 的 meta 行 `🔍 全局搜索` 按钮（`#board-search-btn`）。
- 面板 `.board-search-panel`：`position:fixed; z-index:150; width:460px`（no 全屏遮罩——边搜边看正文）。头部 `.bs-head`（背景深色，`cursor:move`）。
- **拖动**：`head mousedown`（先记 `panel.getBoundingClientRect()`，设 `panel.style.left/top`）→ `document mousemove`（`Math.max(44, ...)` 防拖出顶部）→ `document mouseup` 释放。**opен 时 `panel.style.right='auto'` 且用 left+top 定位**（初始 `max(10, innerWidth-w-18)`, top:60），否则 right 残留会冲突。
- **搜索**：`input` 事件防抖 350ms + Enter 即时。`fetch` 给 `/api/board/search`，`lastReq` 令牌丢弃过时响应。结果 `escapeHtml` 渲染 `.bs-item`，href=`/board/<author>?file=<file>&page=<page>#r<no>`。**body.html 每楼 `id="r{{ r.no }}"`（绝对楼号，跨分页稳定）**——锚点 `#r<no>` 在切到正确 page 后浏览器自动滚动定位。
- **转义坑（2026-08-23 修正）**：result 渲染 `[替换空白]` 只写单个 `\s`；曾因 JSON 转义漂移在文件里残留多余反斜杠（等于匹配字面反斜杠不匹配空白，不抛错但多余）。
- **跳转后面板保留 + 手动关闭（本次需求）**：点击结果时 `saveState(q, currentResults)` 存 sessionStorage（key `'bsq:'+author`，author 用原始字符）；`<a>` 整页跳转后新页面 IIFE 读存档 → `openPanel()` + `requestAnimationFrame` 渲染结果。手动关闭（✕）`closePanel()`=移除 `.open`+`clearState()`（删 sessionStorage），跳转不删 → 面板保留，点 ✕ 才清。
- **已知边界**：thorium 验证时跳转后 `panelOpen:true`、status 正确但 `#bs-list` 偶发为空；同数据手动 `map` 能填满（存储数据齐全），多为 console 注入与页面状态交叉污染所致，非代码缺陷——用户重新输入关键词即恢复。核心诉求（面板保留/手动关闭）已稳定验证。

**验证（浏览器 console）**：
- 面板开：`document.getElementById('board-search-panel').classList.contains('open')`
- 结果：`document.querySelectorAll('.bs-item').length`；status `共 N 条`
- 跳转：点 `.bs-item` → URL 变 `?file=...&page=10#r3655`、`!!document.getElementById('r3655')`、`getBoundingClientRect().top===0`（锚点对齐视口顶）
- 拖动：mousedown(100,200)→mousemove(200,300)→mouseup，面板 `getBoundingClientRect` 位移 = (100,100)

## 揭示板原始数据获取（r401 未收录话数 → 原始揭示板）

背景：作者 コンプレックス◆JWKY.d5ark 作品在 r401/yaruobook 收录不全（本作 r401_cid=25548 只到第 10 话），**完整连载在原始揭示板 yarufox.sakura.ne.jp 的 FOX 板**。用户给的线索：最新贴 `test/read.cgi/FOX/<eid>` + 前スレ链，历史贴已归档到 `FOX/kako/<前4位>/<前5位>/<eid>.html`（对应第 N スレ目）。
- **识别归档**：`curl read.cgi/FOX/<eid>/` 返回 1268B 提示页（"過去ログ倉庫に"），内嵌 `/FOX/kako/<a>/<b>/<eid>.dat` 真实地址；活跃贴（>100KB 正文）直接返回完整 HTML。
- **抓权威文本用 `.dat` 端点**（非 HTML）：`test/read.cgi/FOX/<eid>/dat`、kako 则 `/FOX/kako/<a>/<b>/<eid>.dat`，CP932 解码（`d.decode("cp932")`）。HTML 版是浏览器转码过的（title 乱码），别用。
- **章节对应**：作者第 N スレ目≠文件名 eid 顺序。스レ编号以贴内标题【Nスレ目】与日期为准；用户消息里给的编号（雑談所1=某 eid）与贴内真实スレ目可能错位——按贴内标题权威校准，时间戳排序。
- r401 未收录的话数（如本作第 11-15 话）：用站点搜索页 `yaruobook.jp/?s=<作品名>` 抓文章卡片解析 eid（卡片结构 `entry-card`，含 `entry-card-title` 与 `?p=<eid>`），再走标准 dl_chapters 提取。
