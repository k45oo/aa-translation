---
name: aa-site-parsers
description: Use when 新增/扩展AA站点解析器或登记同作异站镜像(FC2帖头双格式/R18年龄门/add_work注册).
---

# AA 站点解析器开发（新增站点/扩展下载）

新增 AA 漫画站点解析器的完整链路：解析器 → add_work 域名注册 → registry 条目 → dl_chapters 扩展（下载侧特殊处理）→ extract_all 验证。**2026-08-31 两个 FC2 新站实战定稿**（iitokolo.blog.fc2.com / mukankei769.blog114.fc2.com）。

## 铁律一：帖头切分必须行级判定（704 楼号丢失事故）

**body 中「引用某楼 704 名前：」文本会被跨行正则误判成新帖头**、吞楼号/串行。
任何站点解析器帖头切分必须：
1. 按 `<br>`/`<hr>`（FC2 长文分页线）切逻辑行
2. 逐行 `_rebuild_header`（剥标签+unescape+去NBSP）后行级判定
3. 判定条件（双格式任一）：`^\d+ 名前：` + `投稿日[：:]{1,2}YYYY/MM/DD(` 或 `^\d+ ：` + `：YYYY/MM/DD(…) HH:MM:SS`
4. 禁单个跨行 finditer 正则扫数字→投稿日

## 铁律二：帖头双格式必须独立正则（render_common_header 4 组限制）

- `_base.render_common_header` 要求正则 **恰好 4 组**——双格式帖头 `|` 交替会 8 组崩（ValueError: too many values to unpack）。
- 正确姿势：`HEADER_RE_NEW` / `HEADER_RE_OLD` 两个独立 4 组正则，`render_header` 里 `HEADER_RE_NEW.match(line) or HEADER_RE_OLD.match(line)`，传 `m.re` 给 render_common_header 重新 match。

## 铁律三：FC2 年龄门（dl_chapters `dl_age_gate` 配置）

FC2 R18 博客（如 mukankei769.blog114.fc2.com）有年龄确认页：
1. GET 章节页 → 响应前 5000 字节含 `age_verify` = 撞门。
2. POST `age_verification=1` + `entry_number=<eid>`（**必须匹配该章，错值 404**；URL 正则 `blog-entry-(\d+)` 提取）→ 种 cookie `age_check`/`blog_view`（**domain 全站通用**，每进程一次）。
3. **必须用带 cookie jar 的 opener 重取**——直连无 cookie 无限撞门（11KB 假文件事故）。5 并发共享一个模块级 CookieJar。
4. registry 条目加 `dl_age_gate: true`，`dl_chapters.fetch(url, age_gate=True)` 检测撞门自动确认。

**tavily/通用提取工具无法解析 FC2 R18**（只拿年龄门页）；r401 分类页 tavily 也只拿侧栏导航。一律 urllib + POST 模拟。

## 注册链路（新增站点 3 处必改）

1. `scripts/sites/<site>.py` 新解析器（统一接口 `extract(raw,eid,src_name)→(txt_lines,colormap,extras)` + `render_header`）。
2. `aa_web_app/add_work.py`：`SUPPORTED_EXLINK_DOMAINS`（域名→域名校验）+ `EXLINK_TO_SITE`（域名→解析器名）**双表都要加**；`build_registry_entry` 对 r401 系按首条 entry url 域名推断 site（`EXLINK_TO_SITE.get(dom, "yaruobook")`），特殊站自动带配置（如 mukankei 系 `dl_age_gate=true`）。
3. registry 条目：r401 系 `r401_cid` + `site`；已完结作品可加 `chapters_source="cache"` 走 work/chapters.json（可手工剔除链接页如「各作品リンク集」）。

**CLI 注册后 enqueue 前必须 `importlib.reload(registry)`**——否则 task_manager 内存旧快照报「未知作品」（磁盘已写，重跑显示已存在）。

## 分类页直连登记（add_work 未覆盖的站点）

`add_work.parse_url` 只认 yaruobook 三站域名与 `rss.r401.net`；其它站（yaruomatomex / yaruohiroba / FC2 系…）用户直接把分类页 URL 丢过来时会报「不支持的网站」。这类作品按下面手工登记，**产物必须与 UI 注册段完全一致**（work/chapters.json + registry 条目 + work_meta），否则 translate_runner / 工作台看不到。

1. 抓分类页（走代理）：`curl -s -x http://127.0.0.1:10808 -A "<Chrome UA>" "<分类页 URL>" -o "$LOCALAPPDATA/Temp/cat.html"`。条目 = `h3 > a[href=blog-entry-(\d+).html]` 的 href+标题——分类页会把该作品全部话列全（页面里还夹着别的作品的「最新更新」条目，按 h3 标题里的作品名过滤）。**核对话数与用户说的「全 N 话」**，并确认作者 tripcode 与用户给的一致。
2. 目录：只建 `aa/ raw/ work/`（见下面「别预建空 汉化/」）。
3. `work/chapters.json`：直接调 `aa_web_app.add_work.save_chapters_json(work_dir, entries)`；entries = `[{eid,title,url,date}]` 按原站正序（旧→新）。
4. registry 条目：`site=<该站解析器名>`、`url_pattern="https://<host>/blog-entry-{eid}.html"`、`name_style="entry_eid"`、`chapters_source="cache"`、`min_size=50000`、`r18` 按标题、`html_out` 落 `aaManga/交付HTML/<id>_汉化合集.html`。插入**复用 `main._append_registry_entry(work_id, entry)`**（与 UI 同一实现的「行级安全插入」），别手拼 dict 文本、别 find+切片拼接。
5. `work_meta.json` 走 `logic.load_work_meta` / `logic.save_work_meta`（author 用 `logic.normalize_author`；cn_name/jp_title/r18_level/tags/关联度 按根 AGENTS.md 口径；标题带 【R18】 的整部走 `r18_level="high"`）。
6. 入队下载：`python aa_web_app/task_manager.py enqueue --type download --work <id> --chapters all`（硬约束：一律 task_manager，不直跑 dl_runner）。

`chapters_source="cache"` 对非 yaruobook 站同样成立：`dl_runner.load_entries` 先试 `_fetch_yaruobook_entries`，非 yb 站解析不出 `?cat=` → 返回 None → 落回 work/chapters.json 缓存（这就是「r401 未收录的站」的正规入口，不必加 `--json`）。

**别顺手配 r401_cid**：r401 上同名作品可能指向**另一个镜像站**（实测：matomex 源 8 话，r401 同名条目的 exlink 是 asitayaruo.com 只有 6 话）。配之前核对 r401 条目的 exlink 域名 + 话数是否等于用户指定源；不一致就不配——否则 `check_updates` 因本地 eid 与原站全不匹配，报「站点迁移/换源，请手动确认」假警报，而用户要的源根本不在 r401。

**别预建空 `汉化/` 目录**：`main.work_stats` 用 `os.path.isdir(out_dir)` 分「未译作品 / 已译作品」两支——目录一存在就走已译分支 → 刚下载未译的作品页显示「章节（0）」。只建 `aa/ raw/ work/`；`aa_translate` 翻译时会自己 makedirs out_dir。

**工作台运行中改了 registry 必须热加载**：`curl -s -X POST "http://127.0.0.1:8765/api/reload?warm=false&clear_cache=true"`（不 reload → 内存旧快照报「未知作品」；不带 `clear_cache=true` → `_STATS_CACHE`/`_MCH_CACHE` 300s 内仍显示旧的 0 章）。用 HTTP 验证非 ASCII 作品 id 一律 `urllib.parse.quote` —— curl 直接塞 UTF-8 路径到服务端会变乱码、报「未知作品」，别误判成没注册成功。

**登记后校验**（全零 token）：`py_compile scripts/works/registry.py`；`git diff --stat` 应只多自己那一条；`grep -cP '\r[^\n]'` 应 0——registry 是 CRLF，**用 python 重写它必须 `open(..., newline="")` 读写**（默认 text 模式把 `\r\n` 读成 `\n`、写回整文件变 LF）；下载后抽一章核 `帖头数 / 作者帖数 / [?] 数`（`[?]` 应 0；少量「非作者帖」是 `名無しさん＠狐板`，正常）+ `chapter_order.json` 正序。

**清单/顺序文件由下载任务自动落盘**：r401 系新作品只要有 `r401_cid`，下载后 `work/chapters.json`（440 条）+ `work/chapter_order.json`（原站正序）就已在位，不必手工生成。

**`aa_web_app/data/work_meta.json` 插入新作品条目**：整文件一个作品一个 key、**CRLF**——插入做文本级（锚在参照作品条目结束处 `\r\n  },\r\n` 之后），别 `json.dump` 整文件（会把全文件换成 LF，diff 满屏）。改完 `json.load` 校验 + 数 `\r\n`；同作异站要**沿用同一 `pipeline`**（`pipeline=pokemon` 作品的新站副本别写成 generic，否则 runner 走错链 / 被拒跑），`author`/简介/`关联度` 照旧作抄。

## 帖头格式变体速查（FC2 系实测）

| 变体 | 示例 | 处理 |
|---|---|---|
| 新格式 | `NNN 名前：◆作者[] 投稿日：YYYY/MM/DD(…) HH:MM:SS ID:xxx` | 标准 |
| 旧格式 | `NNN ：◆作者 ：YY/MM/DD HH:MM:SS ID:xxx` | 无「名前：」短日期 |
| 双冒号 | `投稿日：：2016/11/28` | `[：:]{1,2}`（全角 0xff1a！半角 `:?` 匹配不到） |
| 作者红粗 | `<font color=red><b>◆作者` | 罕见，按原站还原（其他 FC2 站绿粗） |
| 小数秒 | `20:40:35.59` | `(?:\.\d+)?` |
| 连载计数 | `ID:xxx [6/59]` | ID 组 `\S+.*` 吃掉尾部 |
| 裸 sage | `名無しさん sage 投稿日`（2010 老帖无方括号） | `(?:\[[^\]]*\]|\bsage\b)?` |
| 作者=楼号 | `149 名前：143` | 作者名可纯数字 |

## 正文容器变体速查

- 裸 `<div>` + 纯 `<br>`（无 dl/dt/dd、无 class 的 FC2 博客，如 `inzainewtown.blog.fc2.com`）：整篇 AA 塞在一个 `<div style="font-size: medium;">` 里，行程间只有 `<br>`；帖头 `NNN 名前：作者 [] 投稿日：YYYY/MM/DD(…) HH:MM:SS ID:xxx [n/m]`。**模板直接抄 `sites/yaruocheck.py`**：`extract_article` 命中 `<div class="entry_body">`，截断标记补 `fc2_footer` / `relate_dl`（正文后紧跟 fc2button 分享块，不截会把按钮 HTML 当正文行），`parse_posts` 的「单 `<br>` = 模板空行跳过、连续 2+ `<br>` = 分镜空行保留」正合用；禁改成整段 `clean()`（见下节丢空行坑）。

  抄 `yaruocheck` 时三个必改点（不改都**静默丢帖**，只有全库自检能发现，单章抽查看不出）：
  1. **帖头判定必须容许行首 ASCII 空白**：部分章节整篇带 `&nbsp; &nbsp; ` 模板缩进 → `clean()` 后帖头行以空格开头，`^\d+\s*名前：` 漏判 → 整帖被并进上一帖（实测 3 章丢 42 帖）。判定写 `^[ \t]*(?:\d+[ \t]*)?名前：[^\n]{0,80}?投稿日：`；**U+3000 绝不能用 `str.strip()` / `lstrip()` 无参处理**——全角空格是 AA 对齐字符，会被一起吃掉、列位全错，只允许 `lstrip(' \t')` / `rstrip(' \t')`。
  2. **帖头要当行界预断行**：该站有的日子把每帖包进独立 `<div>`/`<p>`，`</p><div><span>NNN 名前：…` 直接续在上一行正文后面（中间没有 `<br>`）→ 逐 `<br>` 切分的循环根本看不到帖头。切分前先 `seg = re.sub(r'(?<!\d)(?=[ \t]*\d+[ \t]*名前：)', '\n', seg)`；**`(?<!\d)` 负向后顾不能省**——漏了会在数字串内部插换行，`10 名前：` 被拆成 `1` + `0 名前：`（全库 440 章当场全坏，靠 sha1 快照 diff 才发现）。
  3. **原站自带缺楼号的帖头**（`名前：NEO坊 ◆… 投稿日：… ID:…`，整部只有 2 处但要认）：判定正则楼号段可缺省（同上），`render_header` 的 `HEADER_RE` 楼号段写 `\d*`；否则该行既不被当帖头、渲染又返回 None（会被当正文行送模型）。
  行级口径：含标签的碎片按 `\n` 逐行判定帖头、逐行 `lstrip(' \t')` + `rstrip(' \t')`（与 `_base.clean` 的 per-line rstrip 同口径，ASCII 尾空白可去、U+3000 不动）；纯文本碎片原样保留。
- `iitokolo_fc2`：`<div class="article">` 直排（无 dl/dt/dd），`</div><div>` 包行先转 `<br>`。
- `mukankei_fc2`：老式表格模板，`<p class="FontTitle">■日期　<a id="EID">标题` 起——**页面顶部有同 class 导航 FontTitle，必须用 id=eid 锚点**；截到 fc2_footer/relate_dl，`rfind('<div', i, k)` 找标签边界不留半个标签。
- 评论 `<a name="commentNNNN">` 在 relate_dl **之后** = 博客评论非原スレ对话，不保留（对比 fc2.py 的 comment_cut 是原スレ对话必须保留）。

## 坑：解析器绝不能丢正文空行（yaruo-matome.com 实测）

`if row.strip(): rows.append(row)` 这种写法会**静默清掉全站空行**（原文空行是硬约束）。yaruo-matome.com 的空行有三种写法，都要算空行：

| 页面 HTML | 含义 |
|---|---|
| `A <br /> &nbsp;<br /> &nbsp;<br /> B` | A 与 B 之间 **2 空行** |
| `A <br /> &nbsp;</p>` | 段尾 **1 空行** |
| `<p> &nbsp;</p>`（整段只有 &nbsp;） | **1 空行** |

要点：
- **不能整段丢给 `clean()`**——`_base.clean` 会折叠同一 chunk 内连续空行（`'A\n\n\nB'` → 只剩 1 空行），必须按 `<br>` 逐段单独 clean 再拼行（见 `sites/yaruo_matome.py:dd_rows`）。
- `<p>` 段落边界**不算换行**（实测对齐后与另一镜像一致）；只裁**帖首/帖尾**模板空行，正文中间必须留。
- 验收口径：`grep -c '^$'` 每作品应 > 0，且与镜像逐行对齐时空行数一致。旧代码受害：サマナー 145 话 0 空行 → 修后 18833 空行（144/145 章有）。

## 同类站点与已知陷阱（2026-09-18 一并修）

同款丢空行的站点不止一个，修时先全库扫：

| 站点 | 空行写法 | 状态 |
|---|---|---|
| `yaruo_matome.com` | `&nbsp;` 行（行内/段尾/空段三形态） | 已修 `dd_rows` |
| `himanatokiniyaruo.com` | `<br><br>`（空 chunk） | 已修 `parse_posts` |
| `blog_2nt.com` | 站模板每行后插空行 | **故意丢**（原帖 AA 连续） |
| `yomuaa.r401.net` | 无空行标记 | 无需处理（空行来自分镜序号标题） |

改完用「回扫」确认没漏：`grep -c '^$' aaManga/<作品>/aa/*.txt`，某作品全 0 就是没修到（例外：blog_2nt 系）。

### dd_rows 里踩过的两个坑（改前请重跑这两条断言）

1. **剥段首标签不要写成 `<p[ >][^>]*>`**：它多要一个 `>`，`<p>` 不匹配 → 正则回溯吃到**下一个** `<p`/`<dd`，整行被吞（r401_kizoku 5 章首行丢失）。正确：`^\s*<(?:p|dd)\b[^>]*>`。
2. **不要把「整段只有 &nbsp;」一律折叠成一个空行**：`&nbsp;<br />&nbsp;` 是**两个**空行。判定改成「chunk 里没有 &nbsp; 且去标签后为空 → 跳过（标签间空白）；否则按 `<br>` 逐段记行」。

## 给已翻译作品补历史空行（aa + 汉化 同步）

解析器修好后，存量作品 aa 重提即可；**已翻译**的作品必须把新增空行同位置插回 `_汉化.txt`，否则 aa↔汉化 错行。做法（实测 5 部、118 章、零内容改动）：

1. 备份 `aa/` + `汉化/`（Temp）。
2. 跑 `extract_all.py --work <id>` 重提 aa。
3. 用 `difflib.SequenceMatcher(old_aa, new_aa)` 断言差异**只有新增空行**（tag=insert 且全是空白行），否则停下来人工看。
4. 把空行插进**旧**汉化：**插入点用 i1（旧行号），不是 j1（新行号）** —— 用 j1 会让前面未插入的块整体偏移（实测差 6 行）。
5. 断言 `len(new_汉化) == len(new_aa)`，再跑 `scan_width.collect_mismatch(aa, 汉化)` 做 A/B（新旧 rows 数必须相同 → 无宽度回归）。
6. 重生成交付 HTML（旧合集会停在修前的 aa）。

---

## 定手段：标记得不到解释时，用 r401 同名镜像做真值

不要凭 HTML 猜"这个 `&nbsp;` 是不是工件"。r401 分类页的**「同名検索」**能找到同作品的其他镜像（如 `categories?s=<标题>` → 本作同时存在 `22218`(yaruo-matome) 与 `21887`(yaruobook.com)）。拿两个镜像的**同章**做逐行对齐：

1. 抽两边的"非空行序列"，`difflib.SequenceMatcher` 对齐内容行；
2. 统计对齐行之间"空行数"是否一致 —— 一致率高的那一版就是对的（实测变体 A 3106/3118 一致 vs 变体 B 3044/3118）。

同一招也用来验证新站解析器：`A <br /> &nbsp;<br /> &nbsp;<br /> B` ≡ yb 侧 `A <br><br><br> B` 就是这么定的。

## 铁律三点五：域名表拼写必须与真实域名逐字一致（UI 报「新网站…解析器未覆盖」）

`add_work.SUPPORTED_EXLINK_DOMAINS` / `EXLINK_TO_SITE` 的 key 是**精确匹配**的域名串。
曾把 `yaruo-matome.com` 误写成 `yaruomatome.com`（少连字符，该域名 DNS 不解析）——
症状：从 r401 分类页添加该站作品时 `/api/parse-url` 一律 **422「该作品章节在新网站 yaruo-matome.com，解析器未覆盖」**，
让人误判成「要写新解析器」，其实解析器 `sites/yaruo_matome.py` 早就有（存量 4 部作品靠手工登记才没暴露）。

排查/修复：
1. 别信映射表，先 `curl -sI` 两个拼写（本仓走代理）确认哪个真解析——**解析器存在 ≠ 域名表写对**。
2. 改 key 前先 `grep -rn "<域名>" add_work.py`——两处（SUPPORTED_EXLINK_DOMAINS / EXLINK_TO_SITE）都要改。
3. 改完**必须重启工作台**（`add_work` 是 main 启动时 import，`/api/reload` 只 reload registry/paths，不 reload add_work），再 `POST /api/parse-url` 验 200。

## 铁律三点六：touhouyaruosure（api_inject）r401 新作品必须带三件套，且原帖地址不能拿 eid 拼

touhouyaruosure 的正文不在章节页 HTML 里（JS skeleton + `/api/chapters/<id>/content`）：

1. registry 条目必须 `dl_type="api_inject"` + `min_size=100000`——漏了走普通 dl_chapters，raw 是 1.3MB 左右的 JS 空壳
   （209 个文件大小几乎一样大 = 特征），extract_all 全部 "skipped (非章节/空结果)" → **aa 目录 0 文件**。
   `add_work.build_registry_entry` 已按 site 自动补这两项（2026-09-18），**存量条目要手工补**。
2. **重下前必须 `rm raw/*.html`**：dl_touhou 对已存在且 > min_size 的文件直接 skip，空壳 1.38MB > 100000 → 永远重下不了。
   下载完看 raw 大小（带正文约 2.8MB vs 空壳 1.4MB）。
3. **原帖地址（`# 原帖地址` 第 2 行）别用 eid 拼**：r401 系作品的 eid 是 **r401 arcid（如 565853）**，站点章节 id 是另一个数（如 11850），
   `/chapters/<arcid>` 返回 **404**——曾整部 209 话全是死链。
   真实地址取页面自引用：首个 `href="/api/chapters/(\d+)/content"` 的 preload（第二个是下一章预取），
   已落在 `sites/touhouyaruosure.chapter_url()`；修完 `extract_all --work <id>` 重提一遍（零 API）即可刷新全部地址。
   校验：抓 1 个地址返回 200（拿 99999 之类假 id 对比应 404）。

## 铁律三点七：新来源与既有作品重叠 —— 判重看正文，裁剪留备份

r401 分类页/镜像站常把**同一作者的同一批帖子**在多个站各收一份，新登记的作品可能与库里某部高度重叠。

- **禁用标题判重**：同一篇帖子在别的镜像站标题会带 `雑予告`/`嘘予告`/`没ネタ`/`永久保存用`/`必修` 前缀，或被拆成「第N話」（另一源合成一页），标题比对会把同一内容算成两篇。
  **没跑过正文比对前不要向用户报「独有 N 篇」这类数字**；报错了当场纠正并给出判据（用户会据此做保留/删除决定）。
- **判据**：每章取正文（去 `#` 头行、去空白）的 **14 字 shingle 集合**，对旧作**逐章**算「最佳单章覆盖率」。必须用最佳**单章**——用旧作全部章的并集会被共用 AA 词汇/常用台词整体抬高，看不出区别。
- **先做基线自检再定阈值**：随机取旧作 10 章 vs 其余全部，最高配对通常 ≤0.5（>0.5 的那几对是同一故事被拆成多话的兄弟章，属正常天花板）。据此：
  | 最佳覆盖率 | 结论 |
  |---|---|
  | ≥0.9 | 同一篇帖子镜像（跨站重复） |
  | 0.5~0.9 | 同故事跨源拆/合（一源拆第1~3話、另一源一页）→ 仍是重复 |
  | <0.3 | 真独有 |
  行级复核（去 `#` 头行后 `difflib.SequenceMatcher`）可确认 0.96+ 的差异只是 AA 空气行错位/帖头 id。
- 现成探针：`scripts/overlap_check.py`（逐章最佳匹配 + 基线自检 + 独有清单）。

### 裁剪流程（只保留独有章）
1. 先算 keep eid 集合；**被删章的 `raw/` + `aa/` 文件 move 到 `%LOCALAPPDATA%/Temp/<work>_trim_<时间戳>/`，绝不硬删**——同站内容零 token 可重下，但备份让决定可回滚（用户改主意时不用重下）。
2. 同步裁剪 `work/chapters.json`（`{"entries": [...]}` 整体重写）与 `work/chapter_order.json`（`{"order": [...]}`）；`work/stats_cache.json` 若只有 `__src` 无需动，有逐章条目要删对应 key。
3. `POST /api/reload?warm=false&clear_cache=true`（删减路径 **warm=false**，别起全量预热线程），再抓 `/work/<urllib.parse.quote(work_id)>` 看「章节（N）」与阅读链接都在。
4. 保留章体检：帖头数、`[?]`=0、`# 原帖地址` 抓一条应 200。
5. 回复里写清：备份路径 + 判据（覆盖率）+ 每个被删章对应的旧作章节，让用户能复核而不是只能相信结论。

### 同一作品的另一站点（新站镜像）：先判同一性，再定名字（绝不混进已有汉化的作品）

用户丢来「某作品在另一个站点」的分类页时按序做：

1. **拿新站清单**：`python scripts/common/r401_index.py category <cid> --all --json <原生路径>.json`。
   `--all` 必须带——不加只回分类页**一页 40 条**（页尾「共 40 条」，看着像全集）；**未知参数会被当 cid 拿去 fetch**（回 urllib 404 traceback，别误判成站点挂了）。JSON 结构 `{title, site_link, entries:[{eid,title,url,date,arcid}]}`，条目**新→旧**倒序（`dl_runner.load_entries` 自己 reverse）。JSON 写到 bash `$TMPDIR`/`/tmp` 后原生 python 读不到（MSYS `/tmp` ≠ Windows 临时目录），一律给 `F:/...` 原生路径或 `$LOCALAPPDATA/Temp`。
2. **判同一性：比帖头，不看标题**。取本地任一章 `aa/*.txt` 的帖头与新站任一页的帖头，比 **res 号 + `投稿日` 时间戳 + `ID:`** —— 三项全同即同一篇帖子（同作异站）。一条 `search_files`（terminal 里 `grep` 亦可）定案，零下载，别抓全文、别用 tavily 抽正文。
3. **镜像的章号不可信**：实测 yaruobook 镜像的「第1話/第2話」与 r401/原站的「第一話/第二話」整体错位（本地「第2話」的 res 1 = 原站「第一話」的 res 1，本地「第1話」却是 res 353-680 一段）⇒ **禁按话数标签做跨站章节映射**，两侧话数差也不能当「新站多/少 N 话」报给用户。
4. **归属决策交用户**：同作异站一律**独立作品 id**，绝不与已有汉化/交付物的作品共目录（否则 ckpt/术语表/交付夹互相污染）。给 2~4 个候选（独立目录 `<作品>_<站点>` / 同目录 + `_fc2` 子目录 / 反向占名让旧版改名）+ 推荐在前 + 一句理由，等回话再建 registry/work_meta/chapters.json。先例：`saki_fc2` = 同目录 + `aa_fc2`/`work_fc2`/`raw_fc2` 子目录；`jikan_yaluo`（r401 19401）vs `jikan_teishi` = 两个独立目录。
5. 落地照「分类页直连登记」六步走；下载仍走 `python aa_web_app/task_manager.py enqueue --type download --work <id> --chapters all`（硬约束），零 token。

## 铁律四：存量 r401 作品的 site 可能错配 → 原文 txt 全 0KB

**r401 多站分类的存量作品（非本次新加）可能 registry `site` 标错**（当时注册默认 yaruobook），而真实来源是别的站。症状：**aa/ 目录所有 txt 均为 0KB**（extract_all 用错解析器 → 0 行），但 raw html 大小正常。

诊断（不猜）：
1. `ls aaManga/<id>/aa/ | wc` + 抽一个 `wc -l` → 全 0KB 即疑。
2. 读 `work/chapters.json` 每条的 `url`，统计 exlink **域名分布**（`Counter`）——真实解析器 = 域名对应的 site。
3. 改 registry `site` 到正确解析器，补 `url_pattern`；touhouyaruosure 系还要 `dl_type="api_inject"`（正文需 API 注入）+ `min_size=100000`。
4. `extract_all.py --work <id>` 全量重提；touhou 系删旧 raw 用 `dl_runner` 重下（api_inject）。

实测：やる夫のサクラ大戦シリーズ（man001.2nt → blog_2nt，163 话）、恋愛騒動七番勝負（touhouyaruosure + api_inject，24 话）——都是 r401 分类但 `site=yaruobook` 错配，修后 0KB 清零。

**铁律五：改 registry 路径含 `\r` 必须写双反斜杠（patch 坑复发）**

Python 源码字符串字面量里，**路径反斜杠必须写成 2 个**（`\\raw` 表示 1 个字面 `\`）——写成 1 个 `\raw` 会被 Python 解释为 `\r`(CR)+`aw`，运行时路径含真实 CR → `os.makedirs` WinError 123「文件名语法不正确」、目录名被拆断。

诊断：import registry 后 `[(i,hex(ord(c))) for i,c in enumerate(raw_dir) if ord(c)==13]` 有 CR，但磁盘 hex `od` 是 `5c 72 61 77`（\raw）——**源码 1 反斜杠 → 运行时 CR**。其他字段（aa_dir/out_dir/work_dir）都是 `\\` 双反斜杠正常成 `\\`（1 字面）——唯独 raw_dir 若写 1 反斜杠就中招。

修复：字节级替换 `name_b + b"\\raw'"`（1 反斜杠）→ `name_b + b"\\raw'"`（2 反斜杠）。**改含 `\r` 路径的代码用 python 脚本字节替换（勿用 patch 工具/heredoc）**，改完 `py_compile` + `od -c` 校验；**落盘后最快的体检是 `grep -nP '\r[^\n]' scripts/works/registry.py`（应 0 行）**。注意 patch 工具本身也是触发点：`new_string` 里的 `\r` 段（`\raw`、`\release`、`\renamed`…）经参数层解码就成真 CR，写前就该把它写成双反斜杠或改用 python 写文件。

## 验证清单（新增解析器后）

1. 单文件 `python scripts/sites/<site>.py <raw.html> [out_dir]` → 行数/帖头数合理。
2. 全量 render 检查：`miss = sum(1 for l in hdrs if not S.render_header(l))` 应 0。
3. 微小文件（<5 行）排查：非正文页 vs 解析遗漏。
4. `extract_all.py --work <id>` 全量重提 + 帖头/行数统计。
5. dl_runner 端到端（先 `--chapters` 2 章验证年龄门/解析，再全量）。
6. **全库结构自检（零 token，必跑）**：逐章断言 `"\n".join(site.extract(raw))` == 存的 `aa/*.txt`（逐字节）+ `帖头数(raw 全文件) == 帖头数(txt 行首)`。任一处不等 = 漏帖/漏行；上面两个丢帖 bug 只有这一条抓得出来。
7. **跨镜像逐帖比对当保真判据**：同作若另有镜像（yaruobook 等），按 `res 号 + ID:` 配对同一帖、正文逐字节比——一致率≈100% 才叫解析对了；镜像自己插的分隔线（`━━━` 之类）差 1~2 处属正常。比「话数/标题」毫无意义（章号跨站错位）。
8. **改解析器后重提全库前先存 sha1 快照**，重提后 diff 只应列出预期文件；一次改动波及全部文件 = 断行/切分位置写错（如漏写负向后顾），立刻回退再改。
9. **新增 `scripts/` 下任何 `.py` 后必须重跑生成物**：`python scripts/tools/gen_inventory.py` + `python scripts/tools/gen_interface_docs.py`。否则 `.venv` 全量测试必红两条 `test_interface_docs`（inventory 缺文件 / 接口文档过期）——这不是环境问题，是门禁，别去改用例。

## 作品发现搜索（aa_search.py，2026-09-01 新增 / 09-03 增强）

`scripts/tools/aa_search.py` — 跨全集关键词发现（r401 + yaruobook com/jp/net 4 源并行，纯标准库），与 r401_index/yaruobook_index（读指定分类）互补。端点结构/正则/坑全部实测见 `references/aa-search-endpoints-2026-09-01.md`（含 09-03 增强）。

```bash
python scripts/tools/aa_search.py "<关键词>" [--site r401|com|jp|net] [--author <名>] [--tag "R-18表現含む"]... [--r18|--no-r18] [--since D] [--until D] [--started-after D] [--no-count] [--json out.json] [--report [path]]
python scripts/tools/aa_search.py --tag A --tag B [--site r401]   # 无关键词 = 纯标签浏览，多 tag AND 交集
python scripts/tools/aa_search.py ranking --period week|month|all
python scripts/tools/aa_search.py aliases [--promote]
```

关键语义（2026-09-03 实测定稿）：
- **本地「已收录」标注** = r401_cid / work/site_url.json / 规范化标题 三重比对 registry（work_meta jp_title/cn_name 参与）。
- **⚠️ yaruobook `?s=` 是 WordPress 站内全文搜索**——命中章节标题常不含关键词（正文命中）；每张卡自带 `<span class="cat-label cat-label-<cat_id>">作品名</span>` = 所属作品，按 cat_id 聚合，禁止剥章节后缀猜作品名。
- **⚠️ yaruobook 全文搜索超慢且超时必须按站放宽**：com 单页 ~150s、jp/net 30-60s——旧 120s 统一超时把 com **每次都杀**（com 组长期为空的根因，非站无数据）；现 `YB_TIMEOUT={com:300, jp:180, net:180}`。全搜多源并行须 `as_completed(timeout)+shutdown(wait=False)`（勿 `with ThreadPoolExecutor`——退出隐式等慢源拖死）。
- **r401 `?s=` 返回「最近更新流」非精确匹配**——作者线必须精确过滤（trip 前缀含 ◆→完全包含；正名 `split("◆")[0]` 完全相等，禁子串）；作者页抓前 3 页 + 过滤合集标题。
- **R18 判定**：r401 = tag26(R-18表現含む) 全量 cid 集合（约 2854 个，1 天 TTL 缓存 `data/search/r401_r18_cids.json`，全量 72 页并发 8 路 ~8s）——**tag26 宽松**：正常向作品有擦边章也归入（ポケモンユカリ/ポケモン赤 实测均标 R18），用户确认维持此语义；yaruobook = 标题+简介关键词表 `scripts/config/search_categories.json`（人工可维护，简介级判定标「推定」）。
- **多标签 AND**：`--tag` 可重复；keyword 模式 = 关键词结果 ∩ 各 tag cid 交集（tag 是过滤器不进结果集）；无关键词 = 纯标签浏览（tag 是结果源）。浏览单 tag R18 优先读缓存免 72 页重拉。
- **章节总数/开坑日期**（仅对最终结果并发 8 路抓，`--no-count` 关）：r401=(页数-1)×40+末页条数（末页号从 nav `&gt;&gt;` 链接拿）；yaruobook 分类页 `/?cat=<id>`（**非** `?s=`！）末页跳转 1 次 (N-1)×300+末页条数（用户 2026-09-03 指正勿逐章数）；开坑日期同批。
- `--since/--until` 对最新更新日期预过滤（零成本）；`--started-after` **仅标注不裁剪**（★ 标记，用户确认）。
- **排序**：标题命中靠前 → 未收录在前（找新作）→ 日期倒序；r401 与 yaruobook **独立分组统计不合并**（同作两源各一条）；r401 同标题多 cid（同作多分类归档）自动归并。
- `--report` 不带值自动落 `doc/aa搜索报告/<关键词>_<YYYYMMDD_HHMM>.md`；带值走显式路径。
- tavily（--web）对找 AA 作品无增益（只回角色 wiki 词条），已砍。作者马甲/重复作品候选流：`scripts/config/search_authors.json` / `search_duplicates.json`（candidates→人工设 确认:true→aliases --promote）。

## 参考

- `scripts/overlap_check.py` —— 新来源 vs 既有作品的正文级判重探针（逐章最佳匹配 + 基线自检 + 独有清单），裁剪前先跑它。
- `references/aa-search-endpoints-2026-09-01.md`：aa_search 各端点结构实测（r401 archives/authors/tag/rank + yaruobook 全文搜索 cat-label/性能 + 09-03 增强）。
- `references/fc2-sites-2026-08-31.md`：两个新站完整笔记（结构/坑/修复过程）。
- 汉化管线本体见 `aa-manga-translation`（user-owned）；本技能只管**解析器开发**类。
