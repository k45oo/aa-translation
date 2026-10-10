---
name: aa-manga-translation
description: Use when 汉化AA漫画/やる夫系2ch字符画. 抓取→分块翻译→艺术行保护→漏译清理.
---

# AA 漫画汉化管线（やる夫系）

## 项目位置
所有脚本与数据统一在 `<repo>\`（2026-08 迁移）：
- `scripts\common\` — 项目通用（r401_index 网站目录读取 / aa_common+aa_api+aa_translate 核心 / scan_width / pass2 / pass3 / fix_width / dl_chapters / extract_all / gen_aa_html / analyze_mismatch / gen_glossary）
- `scripts\sites\` — 网站通用（每站一个解析模块：yaruonichijou / bookshelf / fc2 / blog_2nt / yaruobook，接口 extract(raw,eid,src_name)→(txt_lines,colormap,extras) + render_header）
- `scripts\works\` — 专用（registry.py 作品注册表=唯一参数源；translate_*/gen_* 薄壳；pass4~10/pass5_*/pass6*/pass7*/pass8*/pass9*/fix_* 作品专属清理；postprocess 白头专用；gen_yaruobook_html 评论区专用；saki_runner/kaiki_runner）
- `scripts\legacy\` — 历史归档（旧平铺脚本自包含，勿用）；`scripts\tools\` — 非管线工具
- 作品目录：`白头与染灰魔女\`（aa 42话/汉化/raw/work）、`さーきー\`（aa 17话 + aa_fc2 73话）、`不幸の催眠\`（24话）、`魔法戦姫サマーカラーとスケベ体育教師やる夫\`（59话）
- 合集（2026-10-06 起 = **交付文件夹**，不再是单文件 HTML）：`aaManga/交付HTML/<作品>_汉化合集/`（`index.html` + `data/*.js` + `aa|汉化/*.txt.gz`；旧单文件为历史遗留）
**2026-08-08 重构为 3 类分层**：通用工具带 `--work <id>` 参数（id 见 works/registry.py），不再改脚本顶部常量。

## 触发
用户要求汉化 AA（ASCII Art）漫画——典型来源：`rss.r401.net/yaruo/categories/<id>`（やる夫RSS+索引，分类页是 HTML 不是 XML）、`yaruonichijou.com/blog-entry-XXXXX.html`（やる夫達のいる日常，正文）。实例：《白頭と灰かぶりの魔女》（中文圈通译《白头与染灰魔女》）。先用萌娘百科「AA作品」条目确认官方中文译名（zh.moegirl.org.cn 直连可用）。

## 步骤
1. **抓索引页**：v2rayN 代理 + 浏览器 UA（直连可能 403/超时）：
   `curl -sL -x http://127.0.0.1:10808 -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Chrome/126.0.0.0" "https://rss.r401.net/yaruo/categories/<id>" -o page.html`
   页面按日期倒序列章节，链接形如 `https://yaruonichijou.com/blog-entry-XXXXX.html`（最新在顶部，含分页）。
2. **批量下载章节页**（每页 0.6-1.5MB，10 话≈10MB；5 并发 urllib+代理）→ scripts/dl_chapters.py（改 CHAPTERS 列表）。
3. **提取 AA 文本**：正文在 `<span class="aa">...</span>`，内部有嵌套 `<span style="color:...">` 颜色标签 → **必须括号配对法提取**（`<span class="aa">(.*?)</span>` 会被嵌套 span 提前截断）。`<br>` 转 `\n`，去其余标签，html.unescape。页面里的 img（banner/计数器）与正文无关。→ scripts/extract_aa.py。
4. **预标记文本行**（核心原则：**艺术行绝不经过模型**，只把文本行发给模型，本地拼接回原位）：
   - 含全角假名 `[ぁ-んァ-ヿー]` → 文本行
   - 含汉字但不在艺术汉字黑名单 → 文本行（黑名单用语料统计生成：无假名行中高频且与绘画字符共现的汉字，如 乂彡人凸凹二三四圭日丶土…）
   - 跳过：2ch 帖头（`^\d+\s*：`，如 `539 ： ◆5QXHO4/GJY ： 2022/07/15(金) 20:03:43 ID:...`——元数据不翻译）、`#`/`=====` 标记行、空行
5. **分块翻译**（scripts/aa_translate.py，主管线）：
   - 每块 **≤50 条文本行**（模型对超长块会只译 4/145 行就"完成"），格式 `00017|行内容`；模型只输出 `序号|翻译后的完整行`
   - 提示词要点：① 绘画字符（U+3000 全角空格、│｜／＼＿￣ 边框、半角片假名 ﾉﾐﾑﾊﾞｯｽﾄ、ASCII 艺术符）绝对不可动；② 整行显示宽度不变（ASCII=1 格、其他=2 格），译文变短用 U+3000 填充原文字区域（气泡/文本框内），保持边框位置；③ 角色名/专有名词保留日文（キング、ほむら…）；④ 列出的行必须全部输出，无文字可译的绘画行原样照抄；⑤ 输出仅 `序号|行`，无其他内容
   - 校验：`pad_to_width`（译文短则补 U+3000，行尾是边框字符时补在边框前）；漏检 = 原行含全角假名但模型未返回 → 单独补译一轮
6. **漏译清理 + 校对**（2026-09-07 起 = refine_aa 一链，pass2/pass3 已从翻译链移除）：模型会**原样复制日语行假装完成**（如 `離れていいわよキング` 照抄）→ translate_runner 清理链 = scan_width → fix_width → **refine_aa**（段级重建：text 整行漏译补译 + mixed 行右侧台词段重建 + 纯 art 行还原，一次遍历全覆盖；默认开，`--no-proofread` 禁）。旧法 pass2/pass3（残留检测→循环发送→顽固行导出 review 人工回填）仅独立 CLI/legacy 可用。详见 aa-translation-proofreading 技能。
7. **术语统一 + 交付**：glossary 子串替换（白頭→白头、灰かぶりの魔女→染灰魔女）+ 宽度再校正；标题单独翻译；生成 HTML 合集（`<pre>` + MS Gothic/等宽字体 + 章节锚点导航 + html.escape 防 `<>` 艺术符破坏页面）→ scripts/postprocess.py。

## 第二站点：yaruobookshelf.jp（やる夫のブックシェルフ）
- 结构：正文在 `<div class="article">`，帖子 `<dt>帖头</dt><dd>AA 行<br>分隔</dd>`——**`<dl>` 常不闭合**，用 `<dt>` 切分；`<dd>` 截断到第一个 `</dd>`（否则尾部混入 Tweet 按钮/广告/脚本）。
- 帖头样式：作者名绿色加粗、ID 红色加粗、序号/时间灰色 → 提取时正则解析重建（`^(\d+\s*：)(.+?)(\s*：\s*\d{4}/\d{2}/\d{2}.*?)(\s*ID:\S+.*)?$`，用**时间戳锚定**而非 ◆ 前缀——多数帖子是站点搬运者「本棚のやる夫さん」无 ◆）。
- 颜色还原：红色 span 用于骰子**结果**行（非全部选项！）；span 可能跨 `<br>` 断裂（`<span style=red><br>１０．…</span>`）或行内片段（`</span>！`）——用「**红色区间与行区间重叠检测**」而非整行匹配；`>>232` 引用是**蓝色**（整行 span 检测捕获任意颜色）。**只还原原站标红的行，不要自己扩展**（用户明确要求）。
- 加粗：原站 `<b>` 仅用于 `【1D10:X】` → colormap 标 `font-weight:bold`，**渲染时只加粗括号片段本身**（正则 `【1D10:[^】]*】` 包 span），整行加粗会把 AA 艺术撑变形。
- 每章开头加原帖地址：txt 第 2 行 `# 原帖地址: https://yaruobookshelf.jp/blog-entry-<eid>.html`；生成器缺失时从文件名 eid 构造。
- 产出：《さーきー》（咲-saki- 同人）17 话全汉化（2026-08）——E:\对话\さーきー_合集.html + さーきー_汉化\。目录17按钮+单章阅读+每章原帖链接+帖头颜色+骰子红+1D10加粗。

## 第三站点：yaruok.blog.fc2.com（FC2 博客，やる夫まとめ）
- rss.r401.net/yaruo/categories/<id> 分类页可发现（21248=さーきー 71话）。链接形如 `http://yaruok.blog.fc2.com/blog-entry-<eid>.html`，页面是 FC2 模板（UTF-8）。
- 结构：正文在 `<div class="entry_body">` + `<div class="entry_more">` 两段 `<dl><dt>帖头</dt><dd>AA<br></dd></dl>`；**评论区 `<dt class="comment_cut">` 是原スレ的名無し帖（FC2 自动折叠），必须保留提取**（bookshelf 已汉化版包含这些对话）；`<dt class="relate_dt">`（関連記事）截断。
- 帖头：`<span>序号</span> ： <span style="color:#008800;font-weight:bold;">◆作者</span> ： <span>时间</span> <span style="color:#0000ff;font-weight:bold;">ID:xxx</span>`——**作者绿粗、ID 蓝粗**（与 bookshelf 的 ID 红粗不同！按原站还原）；名無し作者绿非粗、ID 黑。
- **章节编号与 bookshelf 不同步**（FC2 chN ≈ bookshelf ch(N+1)，FC2 ch16 ≡ bookshelf 第17话）——跨站续译前先比对帖头时间戳/楼号确认边界。scripts/extract_fc2.py + extract_fc2_all.py + dl_fc2.py + translate_fc2.py（PILOT_EIDS 限制范围）+ gen_fc2_html.py（ID 蓝）。
- 汉化产物命名 `entry_<eid>_汉化.txt`；扫描/修复脚本用 _fc2 后缀版本（fix_width 等要跳过未翻译文件）。
- **批量流水线**: translate_fc2.py（PILOT_EIDS 空=全部）→ pass2_fc2 → pass3_fc2 → scan_width_fc2 → fix_width_fc2 → pass5_fc2（顽固行人工替换表）→ pass6/6b（整行精简）→ pass7/9（review 真漏译批量重译,带宽度+残留双校验,失败导出 manual_review2.txt 等人工复查）→ gen_fc2_html.py。73话全量(71+2番外)完成(2026-08-08): 宽度0错位、残留除人工清单外清零、HTML 775标红==colormap、1D10加粗0缺失。
- 坑: pass2/pass3 补译会引入宽度错位,必须跑完 fix_width; **fix_width 重译时模型输出原文(宽度恰匹配)会被当作成功,把已人工修好的行覆盖回原文**——人工修复(pass5)后若再跑 fix_width,必须复验被修行; fix_width 失败行用「恢复SRC原文」兜底(等人工复查); 角色名白名单要防拆分碎片(ART_HIRA 含 り/こ/し/と 会把 よりん拆成 よん、レジェンゴ拆成 ジェンゴ)——碎片也进白名单; 语法词(ある/でも/さすが/すぎる等)绝不能进白名单(会掩盖漏译); gen_fc2_html 的章节标题从 txt 第1行解析话数,番外用原标题。
- **pass5 的 pad_to_width 只补不裁**:替换译文比原文宽时直接超宽落盘(校验三条件里的宽度会抓到)——精简译文后再 pad,或先算 disp_width 确认 ≤SRC 再替换。

## 第四站点：man001.blog.2nt.com（2NT 博客，やる夫まとめ R-18）
- rss.r401.net/yaruo/categories/<id> 分类页可发现（12068=不幸の催眠 24话：第1-23话+最終話+目次）。链接 `http://man001.blog.2nt.com/blog-entry-<eid>.html`。
- 结构：正文 = `<h3><a id="entry<eid>">标题</a></h3>` 之后第一个 `<div class="article">`，到 `relate_dl`（関連記事）截断。帖头 `1 名前： <span style="color:#008800;">◆作者</span>[] 投稿日：… ID:xxx`——作者绿粗、ID 蓝粗；本作品**无骰子红色/无加粗**。
- **每行 AA 后是 `<br>\n<br>`（模板空行）**：必须跳过空行片段（`clean(...).strip('\n')` + 空行 continue），否则行间空行、AA 图拉长；og:description 是原帖权威文本（帖头后 1 空行、AA 连续）可对照。
- 帖头格式 `NNN 名前：`（非 `NNN ：`）——HEADER_RE 已加 `|^\d+\s*名前` 兼容。
- 产出：《不幸の催眠》24 话全汉化（2026-08-08）——`不幸の催眠\汉化\entry_<eid>_汉化.txt` + `不幸の催眠_汉化合集.html`（3MB，目录+单章+帖头色）。脚本：dl_2nt.py / extract_2nt.py / extract_2nt_all.py / translate_2nt.py / gen_2nt_all_html.py / pass2_2nt.py / pass3_2nt.py / scan_width_2nt.py / fix_width_2nt.py。

## 第五站点：yaruobook.com（やる夫スレ本棚，WordPress/Cocoon 主题）
- rss.r401.net/yaruo/categories/24713 分类页可发现（魔法戦姫サマーカラーとスケベ体育教師やる夫，作者◆N99UpbkNMc，R-18）。链接 `https://yaruobook.com/?p=<pid>`；分类页条目链接是 `/yaruo/archives/<id>` 与 `yaruobook.com/?p=<pid>` 两条。
- 结构：正文 = `class="entry-content cf"` 内 `<dl><dt>帖头</dt><dd>AA<br></dd></dl>`，**到 `</dl>` 截断**（其后是 dmm/dlsite 广告 + お得情報，必须排除）；`</dl>` 只有一个。
- **作者帖 dt/dd 带 class（`<dt class="author-res-dt">` / `<dd class="author-res">`）**——`<dt>`/`<dd>` 正则不匹配，必须用 `<dt[^>]*>`/`<dd[^>]*>`。
- 帖头两种变体：作者帖 `NNN ： ◆XXX ： 时间 ID:xxx`（作者绿粗 #008800、ID 红粗 #ff0000）；名無し帖 `NNN ： 名無しさん＠狐板 ： 时间 ID:xxx`（作者绿非粗、ID 黑）。判断用行内是否含 ◆。
- 评论：`<ol class="commets-list">` 内 `<li class="comment ..." id="comment-N">`；作者 `<cite class="fn">`、时间 `comment-meta`、内容 `comment-content`（截断到 `cld-like` 点赞区，**rfind `<div` 到标签开头再截**否则残留 `<div class="`）。评论翻译单独存 `*_comments_zh.json`（content_zh 字段），HTML 附页尾。
- 蓝色 span（`color: #0000ff;`）是 AA 行内引用片段（＞1 等），量少不还原颜色。部分话（如第9、10话）纯文字回帖多、AA 少，正常。
- 产出：第1-10话汉化（2026-08-08）——`魔法戦姫サマーカラーとスケベ体育教師やる夫\汉化\entry_<pid>_汉化.txt` + `魔法戦姫サマーカラーとスケベ体育教師やる夫_汉化合集.html`。脚本：dl_yaruobook.py / extract_yaruobook(_all).py / translate_yaruobook.py / translate_comments_yaruobook.py / pass2/3_yaruobook.py / scan_width_yaruobook.py / fix_width_yaruobook.py / gen_yaruobook_html.py。**2026-08-08 全59话（第1-58话+最終話）完成**：8MB HTML，281 条评论全译；第54话(88642)原站无评论是正常的（渲染 58 个评论区块）。
- **批量大作品的清理节奏**：59 话级别时 pass3 全量一次 10-20 分钟，跑 2-3 轮 review→pass_review→人工替换的循环才收敛；每次 pass3 后跑 fix_width 会引入「模型输出原文被当成功」的覆盖，务必用「TR==SRC 且 is_text_line」判定复查被修行；角色名白名单按 kana_runs 实际拆分结果补（如 サマーカラー 剔除ー后是 サマ+カラ 两段，都要进白名单），否则永远残留。

## token 用量统计（2026-08-10 加入 aa_api.py）
- 环境变量 `AA_USAGE_FILE` 指向 JSONL 路径 + `AA_USAGE_TAG` 标环节（translate/pass2/pass3/fix_width...），每次 call_api 成功追加 `{"tag","prompt","completion","total","ms"}`；未设置时零开销。
- 实测（キッコーマン 21-60 话，40 话）：单话平均 46.4 calls / 81,309 total tokens（prompt 53,240 + completion 28,069）/ API 耗时 436s；全流程 1854 calls / 3.25M tokens。环节占比：translate 87.3% API 耗时 / pass3 7.6% / pass2 3.4% / fix_width 1.7%（translate 占比极高是因为主译+补译都走它）。
- 时间戳统计法：每环节前后 `date +%s > work/tN.txt`，差分算墙钟占比。抓取+提取仅 ~1%，pass3 清理墙钟 ≈ translate 的 1/3。

## 第六站点：yaruomatomex.com（やる夫まとめくす）
- rss.r401.net/yaruo/categories/21034 = 引きこもりやる夫がたまに学校行ったら異世界行き　～異世界転生キッコーマン～（ゼロの使い魔パロディ R-18，作者◆GSpyI2J3e6，分类 21034 共 140+ 话，分页 20 条/页：page=1 最新、page=4 第1-20话）。链接 `https://yaruomatomex.com/blog-entry-<eid>.html`，站点 sites/185（やる夫まとめくす，40 作品）。
- 结构：正文 = 第一个 `<div class="article">` 到 `fc2_footer`（Tweet 按钮）截断；第一帖 `<dl>` 闭合 + `<a name="more">` 后 `<dl>` **不闭合**（bookshelf 模式），用 `<dt>` 切分、`<dd>` 截断到第一个 `</dd>`。
- 帖头 `NNN ： ◆作者 ： 时间(含微秒 .400567) ID:xxx`——**作者绿粗 #008800 / ID 红粗 #ff0000**（同 bookshelf/yaruobook 作者帖）；名無し `NNN ： 常態の名無しさん ： ... ID:xxx`（作者绿非粗、ID 黑）。◆ 判断作者/名無し。
- **空行判断（2026-08-11 定型）**：dd 按 `<br>` 切分后只裁**开头/结尾**连续模板空行；**正文中间空行是作者分镜/段落间隔，必须保留**（长连续组如 8 个=作者留白翻页，对照 raw 原始 br 数验证）。已修：yaruomatomex/touhou/yaruobook（旧版一刀切删空，帖头前 0 空行）；fc2 **不要修**（旧版保留全部空行，帖头前 2 空行=站点自带帖间距模板，旧版正确）。迁移=重新提取+旧汉化非空行序列回填（零 API）。
- **dd 开头有 `<br>  <br>` 模板空行**：只跳过 dd 开头/结尾的**模板空行**（站点装饰）；**正文中间的空行是作者留的分镜/段落间隔，是漫画的一部分，必须保留**（2026-08-11 修正：旧版一刀切跳过所有空片段导致画面过度压缩，kikkoman 80 章已按「非空行序列对齐」零 API 迁移恢复 10,280 个空行）；og:description 权威文本对照（帖头后无空行直接 AA）。
- 正文 dd 内无任何 span（无骰子红/无加粗）——colormap 恒 0，颜色只还原帖头。
- 产出：《異世界転生キッコーマン》第 1-20 话汉化（2026-08-08）——`キッコーマン\汉化\entry_<eid>_汉化.txt` + `キッコーマン_汉化合集.html`。解析器 scripts/sites/yaruomatomex.py（registry id=kikkoman）。glossary 增补：キュルケ/できない夫/ルイズ（ゼロの使い魔角色）+ 本作艺术假名误报（てヌ/たト/フく/ぅイ/ソア/タカリ/ィぅツ/てん/ムく/てテ/イん/ちiり）。
- 坑：r401_index.py category 子命令不支持 `?page=N`——分页用 curl 抓后 `html <本地文件> --json`；aa_translate --pilot 曾只认 `--pilot=3146`，已修成空格/等号双格式；pass2/pass3 曾 import 错模块（pass2 用 aa_api 调 pad_to_width、pass3 用 aa_common 调 call_api），已修为 aa_api as A + aa_common as C。

## yaruo-matome.com（やる夫まとめ，2026-08-28 新增）
- rss.r401.net/yaruo/categories/24629 = 紲星あかりがトップエロ配信者になるまで（R-18，registry id=akari_haisin，34 话，eid 23844-24104）。链接 `https://yaruo-matome.com/archives/<eid>`。
- 结构：正文 `<div class="entry-content" id="entry-content">` 内 `<dl><dt>帖头</dt><dd>AA<br>...</dd></dl>`（dl 闭合）；截断 `relate_dl`/`関連記事`/评论。
- **帖头新格式（2023+）带 span**：`<dt><span>2337</span> ： <span>ルピア ◆1v1ZPWQmKI</span> ： <span>2022/07/12(火) 20:42:18.64</span> <span>ID:AQiop/G9</span></dt>`——**`has_dt_posts` 判定正则必须匹配 `<dt><span>数字</span> ：`**（原版 `<dt>\s*\d+` 匹配不到 → 误走 parse_posts_old → 0 篇 → 只提取标题行 → 汉化全空）。2026-08-28 已修：`<dt[^>]*>\s*(?:<span[^>]*>\s*)?\d+\s*(?:</span>\s*)?：`
- dd 开头 ` &nbsp;<br />` 模板空行跳过；正文中间空行保留（作者留白）。
- 帖头 `NNN ： ◆作者(绿粗) ： 时间 ID(红粗)`（作者 ◆ 绿粗、ID 红粗，同 bookshelf/yaruobook）；无骰子红/加粗。
- 产出：akari_haisin 34 话汉化（2026-08-28 进行中）。

## 第七站点：touhouyaruosure.com（東方やる夫スレ纏め，Rails/Honox 现代站）
- rss.r401.net/yaruo/categories/15829 = やる夫のねっとり貞操逆転世界（R-18 オリジナル，全 101 话，eid 9553-9653 **连续**；分页 40 条/页，page=3 是最后页 21 条）。链接 `https://touhouyaruosure.com/chapters/<eid>`。
- **正文不在章节页 HTML**：页面是 Honox island 壳，正文经 `/api/chapters/<eid>/content` 返回（`<dl><dl><br/><dt>帖头</dt><dd>AA<br>...</dd>...</dl></dl>`，dl 闭合良好）。`works/dl_touhou.py` 抓两处并把 API 正文注入 `<div id="c-chapter-content">` 的 skeleton 位，raw 存单文件（~1-2MB）供 extract_all 用。
- 解析器 `scripts/sites/touhouyaruosure.py`（registry id=touhou_teisou）：`<dt>` 切分；dd 开头 ` <br/>` 模板空行必须跳过（2nt 同款坑）；红色 span（rgb(255,0,0)）跨 `<br>` 断裂 = 原站红字/骰子行，区间重叠还原；蓝色 span（#0000ff）= `>>N` 引用，量少不还原；无 `<b>`（无 1D10）；帖头 `NNN ： ◆作者(绿粗) ： 时间 ID(红粗)`，无 ◆ 时绿非粗/ID 黑。
- **colormap 键是 0-based 行号**（build_txt 的 `len-1` 与 gen_aa_html `render_line(i,...)` 的 enumerate 索引一致；さーきー 旧版是 1-based 历史产物）——验证时按 1-based 查会错位 1 行误判。
- 产出：《やる夫のねっとり貞操逆転世界》101 话汉化（2026-08-10）——`やる夫のねっとり貞操逆転世界\汉化\entry_<eid>_汉化.txt` + `交付HTML\やる夫のねっとり貞操逆転世界_汉化合集.html`。批量流水线 `works/touhou_runner.py`（等批次→pass2→pass3→scan/fix_width→gen_aa_html）。
- 坑：该站 25 行红色是**原站红字非骰子**，用户确认不影响；角色名 やる夫 已在白名单，入速出/育代 由 pass3 全量频次自动入。

## token 用量统计（2026-08-10 加入 aa_api.py）
- 结构：正文 = 第一个 `<div class="article">` 到 `fc2_footer`（Tweet 按钮）截断；第一帖 `<dl>` 闭合 + `<a name="more">` 后 `<dl>` **不闭合**（bookshelf 模式），用 `<dt>` 切分、`<dd>` 截断到第一个 `</dd>`。
- 帖头 `NNN ： ◆作者 ： 时间(含微秒 .400567) ID:xxx`——**作者绿粗 #008800 / ID 红粗 #ff0000**（同 bookshelf/yaruobook 作者帖）；名無し `NNN ： 常態の名無しさん ： ... ID:xxx`（作者绿非粗、ID 黑）。◆ 判断作者/名無し。
- **空行判断（2026-08-11 定型）**：dd 按 `<br>` 切分后只裁**开头/结尾**连续模板空行；**正文中间空行是作者分镜/段落间隔，必须保留**（长连续组如 8 个=作者留白翻页，对照 raw 原始 br 数验证）。已修：yaruomatomex/touhou/yaruobook（旧版一刀切删空，帖头前 0 空行）；fc2 **不要修**（旧版保留全部空行，帖头前 2 空行=站点自带帖间距模板，旧版正确）。迁移=重新提取+旧汉化非空行序列回填（零 API）。
- **dd 开头有 `<br>  <br>` 模板空行**：只跳过 dd 开头/结尾的**模板空行**（站点装饰）；**正文中间的空行是作者留的分镜/段落间隔，是漫画的一部分，必须保留**（2026-08-11 修正：旧版一刀切跳过所有空片段导致画面过度压缩，kikkoman 80 章已按「非空行序列对齐」零 API 迁移恢复 10,280 个空行）；og:description 权威文本对照（帖头后无空行直接 AA）。
- 正文 dd 内无任何 span（无骰子红/无加粗）——colormap 恒 0，颜色只还原帖头。
- 产出：《異世界転生キッコーマン》第 1-20 话汉化（2026-08-08）——`キッコーマン\汉化\entry_<eid>_汉化.txt` + `キッコーマン_汉化合集.html`。解析器 scripts/sites/yaruomatomex.py（registry id=kikkoman）。glossary 增补：キュルケ/できない夫/ルイズ（ゼロの使い魔角色）+ 本作艺术假名误报（てヌ/たト/フく/ぅイ/ソア/タカリ/ィぅツ/てん/ムく/てテ/イん/ちiり）。
- 坑：r401_index.py category 子命令不支持 `?page=N`——分页用 curl 抓后 `html <本地文件> --json`；aa_translate --pilot 曾只认 `--pilot=3146`，已修成空格/等号双格式；pass2/pass3 曾 import 错模块（pass2 用 aa_api 调 pad_to_width、pass3 用 aa_common 调 call_api），已修为 aa_api as A + aa_common as C。

## 事故教训（2026-08-08 严重）
- **heredoc 批量生成派生脚本时，路径替换串必须与源文件实际内容完全一致**，否则 OUT_DIR 仍是旧作品目录 → pass2 会误改已完成作品（白头 42 话被 pass2/pass3 误重译 14 话）。生成后**必须 grep 验证所有目录常量**再运行。
- 恢复途径：`<作品>_汉化合集.html` 是最终交付物含全部汉化内容 → 从 HTML 恢复 txt（`restore_baitou_from_html.py`：每章 section 的 `<pre class="aa">` 内容 + src 前 2 行 # 头；**锚点对齐**：body 中 `=====` 位置 vs src 中位置，`tr = src[:si] + body[bi:]`，再裁剪到 src 行数；HTML `<pre>` 首行可能是渲染产生的空行）。
- 恢复后跑 fix_width（新基准）修掉历史 pad 错位；白头的 ORDER/TITLES 硬编码在 postprocess.py，跑它重写 txt（合集再手动 `gen_aa_html.py --work baitou`）。

## 第八站点：yaruohiroba.com（やる夫広場，WordPress，2026-08-19）
- 短篇集：【２０２６年　夏の短編祭】两篇（Hコース 25976 / Iコース 25682，均 R-18）。链接 `https://yaruohiroba.com/archives/<eid>`。
- 结构：正文 = `<div class="AA-area">` 内 `<dl><dt>帖头</dt><dd>AA<br>...</dd></dl>`（dl 不闭合，bookshelf 模式）；截断 `related-heading`。
- 帖头 `1 名前：<span style="color:#008800;">広場の妖精</span>　投稿日：<span>2026/08/12(水) 19:19:55</span>`——**作者绿粗 #008800、无 ID、无骰子红、无加粗**（3 段式帖头，render_common_header 的 4 段解包会崩，需自实现 render_header）。
- dd 开头 `<br><br><br>` 模板空行跳过（2nt/touhou 同款）；正文中间空行保留。
- 解析器 `scripts/sites/yaruohiroba.py`（registry id=yaruohiroba）；**HEADER_RE 无 ID 组 → render_header 手写 3 段**（NUM_STYLE/作者绿粗/TIME_STYLE）。
- 产出：`やる夫広場短編集\汉化\entry_<eid>_汉化.txt` + `交付HTML\やる夫広場短編集_汉化合集.html`（legacy 模板）。
- 坑：**opencode-go 网关可能 429 限流（与并发无关，直接测 call_api 确认）→ 换 AA_BACKEND=mimo**；**zhipu 对 R-18 内容 1301 过滤**（glm 系有安全审查）→ R-18 作品直接用 mimo（AA_BACKEND=mimo AA_MIMO_MODEL=mimo-v2.5 无过滤）；mimo 对 50 行大块输出不全（+0/50~+25/50），漏译行靠 pass2/3 + 子串替换回填；**混合行（艺术+台词）整行重译会毁艺术符**（模型把 ｸﾁﾌｷｵﾜﾘ 译成"嘴破了"）→ 必须子串替换（find 日文段→中文→pad），绝不整行重译。
- **kana_runs 通用修复（2026-08-19）**：纯重复单字符 run（メメメ…/ぃぃぃ…）是艺术纹理/拟声非台词，len(set(s))==1 直接跳过——防大量艺术误报进 pass3。

## r401 原站流程（2026-08-20 定型：记录原帖地址 + 保存原站目录顺序）
新增作品（尤其 r401 分类页来源）必须固化以下两步，否则番外位置乱/原帖地址错：
1. **原帖地址写入**：提取时 `# 原帖地址` 行用 **r401 分类页 exlink 的真实 URL**（`parse_category` 的 `e.url`），不用 fallback 构造（`entry_url(eid)` 会错——Rance 站真实是 `episode/<n>` 非 `episode/<arcid>`，yaruo-matome 是 `/archives/<eid>` 非 `blog-entry-`）。raw 页面 canonical/og:url 优先，Rance 类（og:url=站首页）必须从 r401 分类页重拉 `arcid→episode/<n>` 映射批量重写 aa 地址行。
2. **原站目录顺序**：r401 分类页条目是**倒序（最新在前）**，反转 = 原站发布顺序（番外/クリアリザルト夹在正片间）。拉全部分页存 `work_dir/chapter_order.json` = `{"order": [arcid,...]}`；`logic.merged_chapters(aa_dir, out_dir, name_style, order_path)` 存在 order_path 则按原站顺序排（eid 匹配，未收录的排最后），否则回退「第N話/eid」排序。reader_page 传 `os.path.join(cfg["work_dir"], "chapter_order.json")`。
3. **9 部已存顺序的作品**：jinrou(23586)、r401_dungeon(24032)、r401_kizoku(25538)、r401_ryouri(21162)、r401_jyusatsu(20386)、r401_metsubou(23281)、r401_rance(25286)、r401_hyakka(24467)、r401_kimyou(22525) 的 chapter_order.json 已生成；**新作品下载后立即执行**（r401_index fetch_all_pages 逻辑可复用）。
4. **工作台原站按钮**：registry 每作品 `r401_cid` 字段（r401 分类 id，index.html 作品名旁「原站」小按钮跳 `rss.r401.net/yaruo/categories/<cid>`）。老作品 cid 用 r401 搜索按标题匹配（映射存 temp/r401_cid_map.json）。
5. **站点解析器每站一文件**（勿合并）：yaruomatomex / yaruo_matome / himanatokiniyaruo / yaruocheck / yomuaa 等各独立，统一接口 extract(raw,eid,src_name)→(txt_lines,colormap,extras) + render_header；标题 get_title 优先 `<title>`（剥「站点名 前缀」和「| 站名」后缀），Rance 站 og:title=站名不可用。

## 模型选型实战（2026-08-20 時間停止探索者やる夫 实测）
- **翻译用文本模型，视觉用视觉模型，分开选**（用户明确要求）：
  - 翻译 R18 → **commandcode/meta/muse-spark-1.2-contributor**（4s/块、质量好、无审查、¥$0.10/0.20）
  - 翻译非 R18 → 同样 **muse-spark 首选 + mimo-v2.5 兜底**（2026-08-20 起 R18/非R18 翻译统一，已移除 GLM-4-9B/zhipu 翻译项）
  - 视觉分类 R18 → **Qwen3-VL-32B + Qwen3-VL-30B-A3B 双模并行**（SiliconFlow）
  - **视觉分类 非R18** → **glm-4.6v + glm-4.6v-flashx 分章并行**
    - 两组兜底 = mimo2.5-cmd（commandcode 的 xiaomi/mimo-v2.5 视觉端点）
  - **分章并行语义（2026-08-20 用户要求, vision_classify.py）**：按章 round-robin 给每章分配 1 个主力模型（两主模型各负责约一半章, 章级 ProcessPool 并行）；**同一存疑块只有 1 个模型判断**——主力解析率<70% 或异常才换组内下一模型（串行 fallback），兜底在组内第 3 位。**不是块内多模投票/并行**
- **TOML 配置**：`[vision].models_r18` / `[vision].models_normal` 分组；`aa_config.vision_models_for(r18)` 选组；翻译 `[default].r18_backends`/`normal_backends` 均为 commandcode/muse-spark → commandcode/mimo-v2.5
- **muse-spark 关键坑（2026-08-20 实测）**：
  - `reasoning_effort=low` **必须**（thinking:disabled 无效会 finish=length 空；reasoning_effort=none 直接 400）
  - **max_tokens 必须给足（≥500）**：reasoning 吃 ~94% token，max_tokens=200 时 content 只剩 3 token 空响应
  - 每请求烧大量 reasoning token（273/290），但 commandcode 套餐计费可接受
  - 10 话 7 分钟跑完（qwen-vl 25 分钟都跑不完 1 话 + read timeout）——**视觉模型干翻译效率灾难**
- **auto_backend 配置**：`scripts/config/model_config.toml [default].r18_backends` 首位=commandcode/muse-spark；registry 每作品 `r18` 字段驱动（翻译+视觉都读）
- **yaruobook 站新作品流程**：r401 分类页 `?page=N` 越旧越靠后（page7=最早）；分类可能只收录部分话数——第 1-10 话从 page 7 的 HTML 直接正则抓 `data-rid-arcid-param` + `<a href="?p=">` 拿 eid；合集 HTML 用标题「第N話」排序（eid 2012 是第4话，按 eid 数字排会乱）
- **usage 统计**：`call_*` 后端函数已补 `_log_usage`（2026-08-20 commit 1140238），auto_backend 走 call_* 也落盘 usage.jsonl

## 术语表（2026-08 重构）
- **主数据源 `<repo>\glossary.json`**（人工维护，一行一个；`scripts/gen_glossary.py` 从维基+旧白名单生成一次）
- 结构：`作品`（saki=咲-Saki-/yaruo=やる夫系/madoka=小圆/danganronpa=弹丸/haruhi=凉宫/touhou=东方/other/texture=艺术纹理）、`角色`（日文/中文/作品/分类/备注）、`glossary`（替换对）、`known_names`（扁平白名单，兼容旧脚本）
- 消费方：`aa_translate.py` 的 KNOWN_NAMES、`postprocess.py` 的 GLOSSARY 均改从 JSON 加载（缺失时回退内置兜底表）
- 维基《咲-Saki-》角色列表解析：`<dt>角色名<span>（<span lang="ja">日文（かな）</span>`，按 mw-heading2/3 分块，24校+职业雀士+播报员+记者+亲人+外传=153 角色
- 加角色时：编辑 glossary.json 的 `角色`（新角色）和 `known_names`（可只加后者，残留校验立刻生效）；**拆分碎片也要加**（ART_HIRA 会把 よりん 拆成 よん）
- **AI 预提取（2026-08-21 新增 `scripts/common/gen_glossary_ai.py`）**：新作开工前 `python scripts/common/gen_glossary_ai.py --work <id> [--chars 4000] [--dry] [--backend <后端>]` → 生成 `<work>/glossary_candidates.json`（AI 提取候选术语（严格 JSON、温度低、与 glossary.json 去重）+ 零 API 全语料 kana_runs 高频片段统计（频次≥3，known_names 补录同口径）；语法功能词/纯符号自动打警示 note 不删除）。审校后人工合并进 glossary.json。参考：ai本地AA翻译器0.04 术语表功能（前 3000 字符样本 + 严格 JSON 输出 + markdown 剥壳）。
- **章节预处理（2026-08-21 新增 `common/chapter_prep.py`，translate_runner 翻译前自动跑）**：本章术语统计（全集子串匹配）+ **章节级 R18 文本启发式判定**（STRONG 命中 1 词 或 ≥3 个不同 NORMAL 词 → yes；1~2 NORMAL → suspect；零 API）→ `<work>/prep/<base>.json` + summary.json。**注意：作品级 r18=False 也可能个别章藏 R18 要素（如白頭 42 章判 yes=4：発情/セックス/ちんこ/全裸）——判定对全部作品统一跑，work_r18 仅作参考不 gate**。词表坑：短词子串误撞（えろ→考えろ、ブラ→ラブラブ、裸→全裸待機梗）已排除；普通身体词（胸/触る）不参与。
- **迭代术语更新（2026-08-21 新增 `common/iter_glossary.py`，translate_runner 翻译后自动跑）**：每章后 kana_runs 高频片段（count≥2、len(set)>1、与 known_names 差集）+ 可选 `--ai` 模型提取 → 增量写 `<work>/glossary_iter.json`（不去重问题-去重聚合）；审校：编辑迭代文件设 `确认:true` → `iter_glossary --merge` 合入 glossary.json（known_names + 角色）。**顺序翻译用 translate_runner `--ordered`**（按 chapter_order.json 原站顺序）。`--no-prep`/`--no-iter` 关闭内置阶段。
- **术语表注入（`AA_USE_GLOSSARY` 默认开，2026-08-21 已落地）模型遵循度实测（详见 doc/术语表注入实验_2026-08-21.md + doc/翻译模型对比_2026-08-21.md）**：注入收益**看模型**——遵循者 mimo / deepseek-v4-flash / glm-4.7-flash / **muse-spark（新措辞下）**（off→full 白頭一致率 0~3→14/14）；免疫者 glm-4.5-air / glm-4.1v-thinking。**落地改动（aa_translate.py）**：① `glossary_prompt_hint` 措辞去掉「角色名则保留日文」豁免，改为「本作标准译名必须用，即使角色名也勿保留日文」（实测 muse 白頭 0→14/14）；② 按作品过滤——`_filter_glossary_pairs` 按模块级 `AA_DIR` 语料只注入本作出现过的对（白头 38→14、トレセン 6），缓存按 (aa_dir, json_path) 双键（monkeypatch `_GLOSSARY_JSON` 的测试需要）；`_glossary_pairs_cache` 显式赋值可整体覆盖（测试/实验用）。纯文本文件用 heredoc 字节级改造，注意 terminal JSON 层把 `\\n` 解码成真换行的坑（用 chr(92)+'n' 构造源码转义）。坑：`parse_result` 只认 `\\d{1,5}` 行号，实验自造 10 位 id 会全批 missed（非模型问题）。新模型结论：mimo-v2.5-pro 可用；qwen3.7-flash 批量超时不可用；laguna-2.1-free 照抄日文不可用。

## 角色名残留统一（2026-08-29 barasui_murabito 实战，463→0 清零）
已译文本里角色名漏译成日文（如 薔薇水晶/真紅/蒼星石/姫様 残留）是常见返修项。**确定性零模型清理配方**（不依赖重译，改完宽度自动校正+备份）：
1. **兜底扫描**：`python scripts/common/scan_role_residual.py --work <id>` → `<work>/role_residual.txt` 列出 日文名(应译:中文名)×命中数并写 `## 命中 N 处`。**注意该报告行号是 1-based 且可能偏移 1**（报告 line 578，实际行 579），核对必先看行内容再改。
2. **建替换对**：从 `glossary.json` 的「角色」表取 (日文→中文) 对——**全局 glossary 的替换对（`glossary` 数组）往往是空的，角色表的中文译名才是来源**，必须自己拼 pairs。排序**最长的在前**（防 スペ 先吞掉 スペシャル 的子串）。敬称也建对（姫様→公主，短译名减少超宽）。
3. **第一轮用 `aa_web_app/logic.apply_glossary_chapter(aa_path, tr_path, pairs, dry_run=True, backup_root=...)`**：正则+宽度按 SRC 校正（短则补 U+3000，替换后超宽则跳过记 skipped）。注意它**跳过 `#`/`=====` 行**（标题/分割不引换）。dry_run 看可替换/跳过数，预览通过后 `dry_run=False` 落盘（自动 backup）。
4. **顽固超宽行（替换后 nw>sw 被 skip）**：多为 text/mixed 行（艺术+右侧台词）。AGENTS.md 硬规则：`line_kind` 为 `text`/`mixed` 的**允许超宽**，`art` 行严格等宽。所以第三轮对非 `art` 行**强制 `str.replace` 不校宽**（`line_kind(src)=='art'` 跳过防破坏 AA 图形），一次清掉剩余残留。注意 **SRC 原文本身含日文角色名**时，替换成中文后宽度会比 SRC 宽——text 行允许，属正常。
5. **复验闭环**：`scan_role_residual --work` 归零 + `scan_width --work` 0 错位（确认没破坏宽度）。**合集绝不自动生成**（2026-10-06 起 `translate_runner`/`task_manager` **没有 `--gen`**，全仓库无任何自动生成路径）；需交付时手动 `python scripts/common/gen_aa_html.py --work <id>`（写交付文件夹）或工作台「重生成合集」。
6. **误报识别**：作品标题「薔薇水晶と白饅頭な村人A」保留日文是**正确行为**（标题不引换），扫描器会命中但别改；人工判断是否真漏译。

## 坑
- **force 重译显式 eids 绝不与清单求交（2026-09-07）**: translate_runner 旧 `force` 分支把 `--chapters <eids>` 与实时清单/本地 aa 求交——eid 口径不一致(arcid vs 真实 id/未 extract)会静默清空勾选章 → sel_eids=[] → only_cleanup 对全部已译章跑 pass2/pass3 = 「强制重译跑所有章节」。修: force+显式 eids 直接用勾选原值, 仅 force+new(未指定)才展开本地全部。
- **chapter 停止在并行下不等整批（2026-09-07）**: 旧实现一次性 submit 全部章进 CHAPTER_WORKERS executor, `check_stop` 只在某 fut 完成时触发 → 停止后已提交的整批章仍跑完(日志「章节边界退出」后还跑十几分钟)。修: 改滑动窗口——每完成一章 check_stop, 停止后不再补提交, 等已在飞(≤workers-1)收尾再退出; 日志准确报「等 N 个在飞章收尾」。
- **zhipu 后端已整体移除（2026-09-07）**: 额度耗尽出链后残留的 call_zhipu/守卫计数/ZhipuGuardError/退出码30/guard_rejudge 全流程已删(guard_rejudge.py/r18_zhipu_probe.py 已 git rm)。low_r18 参数保留但语义变为「忽略显式 AA_BACKEND」(防 normal 链首覆盖 yes 章), 不再 zhipu 优先。日志里 [zhipu] 字样全清。
- **半角片假名/半角符号 U+FF61-FF9F 渲染为 1 格**（MS Gothic 等宽字体实测半宽），disp_width 必须按 1 格算（`ord(c)<0x100 or c in HANKAKU`）；否则含 ﾘﾊｱｲ 的 AA 行宽度虚高、译文 pad 失真、视觉压缩。**半角拉丁字母（ｗ U+FF57 等）渲染为 2 格不算半角**。改 disp_width 后必须同步所有 heredoc 派生脚本（scan_width_fc2 曾自带旧副本导致"0 错位"假象）——统一 `from aa_translate import disp_width`。
- **2nt 站（man001.blog.2nt.com）HTML 每行 AA 后是 `<br>\n<br>`（模板空行）**——提取必须跳过空行片段（`clean(...).strip('\n')` + 空行 continue），否则每行间多空行、AA 图被拉长；og:description 是原帖权威文本可对照（帖头后 1 空行、AA 连续）。
- **deepseek-v4-flash 经 opencode-go 网关必须带 `"reasoning":{"effort":"none"}`**——否则全部 output token 被 reasoning 吃掉（status=incomplete，reasoning_tokens=300/300，回答为空）。同时必须带浏览器 UA（否则 Cloudflare 403）。直连偶发 SSL UNEXPECTED_EOF → 重试 + v2rayN 代理(127.0.0.1:10808)兜底。
- 半角片假名（ﾉﾐﾑﾊﾞｯｽﾄ）100% 是艺术；全角假名绝大多数是文字，但 つ（嘴）へ（眉）だり（肢体）いうしふとこ 也常被用作画——残留检测必须排除。且艺术假名远不止这些：ィヲァフテソヅメトトソイイえごドぅぃ 等散落全角假名全是脸部/花纹画，检测器会大量误报——**以"TR==SRC（模型原样）"判定艺术行跳过**。
- 模型两大顽疾：① **译文+原文残留**——翻译正确但把原文尾部留在译文后（如 `你从哪里来的？　名前は？どこからきたの？`），宽度可能一致，纯宽度校验发现不了；② **原样复制日语行假装完成**。→ 校验必须三条件：宽度==SRC、行数一致、残留假名清零。
- **残留清零用白名单制**：统计全语料 kana-run 片段频次，频次≥3 的（名字+艺术纹理，如 オリジナル/もどかの/ヒリ）∪ 已知名字 = 白名单；输出行中只剩白名单片段才算修好。**短残留（2字如 だいたいさー/そうだよ）会被 run≥3 过滤漏掉**——过滤器用 ≥2。
- 人工修复（pass4/pass5 子串替换）会被后续 fix 流程覆盖——**每个 pass 后立即全量验证**（scan_width + 残留扫描），确认落盘。
- **pad 目标必须是 SRC（原文）宽度**，不是 TR 当前宽度；TR 比 SRC 宽时先 strip 行尾 U+3000 再 pad。
- 宽行（200+ 列）中间嵌文字（横幅、尖叫台词）也能处理：模型看到整行、保留两侧艺术，如 `;;'...王王王...你们这群家伙给我适可而止！！`。
- 角色名会跨块不一致（伊吉娅 vs イギィア）——靠 glossary 后处理统一；名字保留日文是汉化惯例。
- 中文译文合法地包含汉字 → 残留检测**只能用假名**（汉字检测会误报）。
- **KNOWN_NAMES 必须用集合字面量 `{...}`**——`set('字符串')` 会把名字拆成单字符，整个白名单静默失效（回归篇/さーきー早期都靠频次≥3 兜底掩盖了这个 bug）。
- **ckpt 必须带文件指纹**（`__lines` 行数）：txt 重新提取后旧 ckpt 行号错位复用 → 498 行整体错位。process_chapter 读 ckpt 时校验行数，不符则丢弃。
- **colormap 行号 = 完整 txt 行号**（含 `#` 标题/地址行）；HTML 生成渲染时**先 enumerate 再过滤** `#` 行，先过滤再 enumerate 会整体偏移 1-2 行（标红错位根因）。
- **is_text_line 不要用日文标点（、…！？）独立判文本行**——艺术行常用 `、` 作装饰耳形，误发模型会被改空格宽度。纯标点行无需翻译（中日标点相同）。
- **环境变量注入后台 runner 的坑（2026-08-29 实战）**：`AA_BACKEND=zhipu python ...` 这种**前缀式**写法在 git-bash + Hermes background 下**不一定传进 translate_runner 的 subprocess 子进程**（`subprocess.run(env=dict(os.environ))` 只继承实际进程环境）。要**验收到**：启动命令直接用 `env AA_BACKEND=zhipu python ...`（Bash `env VAR=val cmd` 确保子进程环境带 `VAL`），并事后用 psutil 读子进程真实环境确认：`psutil.Process(pid).environ()["AA_BACKEND"]`。——免费/降级链到底走了哪个后端别靠猜，`usage.jsonl` 的 `backend/model` 字段（按 `ts` 时间窗过滤**本作品时段**）才是权威。共享 `usage.jsonl` 会被**其他并发作品**（r401_ryouri/akari_series 等）污染，统计前先按时间窗过滤。`AA_FREE_MODE` 传给 aa_translate 时 `call_api` 走 `_call_free_chain`（真免费模型），否则落 cloud 直连付费 mimo。
- **commandcode 空响应 + zhipu 间歇 429 → 清理阶段整批失败（2026-08-29）**：fix_width/pass2 清理时对 commandcode 系（muse-spark/mimo）**小 max_tokens（如 50）请求常返回"空响应: {...}"**，大块（超长 body）则 HTTP 400——两者都算可降级错误，`call_with_fallback` 会切下一候选。zhipu 免费额度**间歇 429（"余额不足或无可用资源包"）**，高峰/额度将尽时一批里修 2-3 章就触发，导致**某几章的 fix_batch 整批失败**（`fix_batch 异常 全后端失败`），失败行落 `manual_fix.txt`。清理阶段遇此别慌：先测当前可用后端（`AA_BACKEND=zhipu` 短请求探针），锁定可用链后**只重跑失败章**（translate_runner 对已译章自动走 only_cleanup 方向），不必全量重跑。
- **`manual_fix.txt` 是假阳性重灾区（2026-08-29）**：fix_width 失败清单里的行**多为纯 AA 艺术行**（`is_text_line=False`、SRC==TR、不含台词假名），却被当初宽度基准偏移误纳入。**别照行数当漏译量**——抽查几行：SRC==TR 且无台词假名 = 艺术行假阳性；真正该补译的 text 行少之又少（barasui 2443 行里真 text 漏译只 37 行）。权威口径是 `scan_width` 的 `line_kind`/`allow_overwidth`（art 严格等宽、text/mixed 允许超宽）——它报 0 错位即宽对齐，`manual_fix` 行不必重跑 fix_width。

## 连载更新（白头与染灰魔女）
- r401 分类 12876 = 白頭と灰かぶりの魔女（最新 2026-07 第三五話 74899）；分类页 40 条（回归篇第 1、2 话历史漏收录，不影响本地 42 话对比）
- cron「白头更新检查」(job 8d6e51100520) 每周日 00:00：`aa_check_update.py`（profile scripts 薄壳）→ 项目 `common/check_update.py`（远程 eid vs 本地 aa∪raw∪汉化）→ 无更新静默 / 有更新写 work/chapters_new.json 并注入 agent
- agent 流程：dl_chapters --json → extract_all --work baitou → aa_translate --pilot → pass3 → scan/fix_width → 新章节标题译后追加进 works/postprocess.py TITLES/ORDER → postprocess.py 出合集
- 前提：周日 0 点机器开机 + v2rayN 代理运行；手动触发 cronjob(action='run', job_id='8d6e51100520')

## 测试（2026-08 建立，零依赖 unittest）
`python -m unittest discover -s scripts/tests -v`（37 用例，~6s）：
- L0 test_aa_common.py：disp_width/pad_to_width/kana_runs/residual_suspect/is_text_line 边界（半角片假名=1格、边框前补、艺术假名剔除、こ/だ 也是艺术假名）
- L1 test_extract.py：5 站 extract() 对真实 raw 字节级对比现有语料（黄金样本只读）+ r401 离线解析(_cat1.html) + aa_hub 检索
- L2 test_pipeline.py：mock call_api（假模型含 原样照抄/译文残留/宽度错 顽疾）→ translate_lines 校验循环；ckpt 指纹丢弃；章节并行加速比断言
- L3 test_gen_html.py：交付文件夹结构 + `data/*.js` 解出的译文 vs fukou 历史单文件交付的 `<pre>` 逐字节一致（旧单文件已冻结，不再被生成器覆写）
- 教训：mock 必须打在 **call_api**（translate_lines 的真实入口）而不是 translate_lines 本身，否则校验逻辑测不到；"また明日" 只有 2 假名（明/日是汉字）本就该放过——短残留靠 pass2 不靠 suspect
- 黄金样本/交付物被改 → 测试挂是特性；改解析器/生成器前先更新期望

## 宝可梦作品专属链（pipeline=pokemon，2026-09-22）

宝可梦 AA 作品（如 `この素晴らしいシンオウに祝福を`，810 话）有**大量固定格式行**（状态行、`◎…`、`【…】`、`『名称』…说明`），
且**本地宝可梦 wiki 高度参与**。这类作品走独立链：翻译前用**固定内容表**把命中行写进 ckpt（该行不进块、不送模型），译名与本地 wiki 强绑定。

- **归属判据**：`work_meta.json` 的 `pipeline` 字段（`generic`/`pokemon`，作品详情页下拉可改）；统一走 `scripts/common/work_pipeline.py`。
  **通用 `translate_runner` 对 `pipeline!=generic` 的作品硬拦**（报错给出正确入口），逃生口 `AA_ALLOW_GENERIC=1`；专属 runner 子进程带 `AA_PIPELINE_OWNER=pokemon` 内部交接豁免。
- **入口**：`python scripts/works/pokemon_runner.py --work <id> [--chapters new|eids|1-20] [--rebuild-table] [--dry-prefill]`
  或任务管理器类型 `pokemon`（web 作品页「翻译」按钮也按 pipeline 自动选类型）。
- **链序**：作品术语表自检（缺表即中断）→ 固定表自检/建表 → 逐章 ckpt 预填 → 子进程交通用链（env `AA_USE_TM=0`）→ 翻译/清理/校对/合集全复用通用实现。
- **四轨匹配（优先级：规则 > copy > 结构化 > 整行）**
  1. **规则轨**：状态行 `ぼうぎょ：C` → 6 词词表（こうげき/ぼうぎょ/とくこう/とくぼう/すばやさ/選手の質），全书仅 60 个 (词,等级) 组合 → 100% 规则化。
  2. **copy 轨（零翻译风险）**：纯图形行（AA 碎片/框线/ASCII）+ 已交付章里 `汉化行 == 原文行` 的行。命中即**原样照抄该次源行**（连 padding 都不动）→ 不涉及任何译文，无漂移风险。
  3. **结构化轨**：`[前缀]『名称』…说明`，名称查 wiki 译名、说明归一化后比对 `effect`，重组保留源前缀与说明起始列。
  4. **整行轨**：归一化 key 完全一致（`◎`/`【】` 高频行的种子译文）。
- **建表/同步 CLI**
  - `scripts/tools/gen_pokemon_fixed.py --rebuild --copy --seed --fill-seed --prefill --diff --scan`
  （`--diff` = 零 token 回放已交付章；`--prefill --dry` = 只统计命中率；`--copy` 建 copy 轨，全库扫描约 1-2 分钟）
  - `scripts/tools/gen_pokemon_glossary.py --from-wiki`（wiki→作品术语表，**入门三重门**：语料里以 `『名称』` 出现 ≥2 次或有官方译名 + 词长 ≥3 + 非黑名单；`来源=wiki` 且 cat 不含「常用」→ 按章语料过滤注入）
  - `scripts/tools/gen_pokemon_glossary.py --repatriate-wiki [--apply]`（人工确认译名回写 wiki 源并重建，写前备份）
- **表**：`work/pokemon_fixed_<work>.json`（`derived`(struct/whole/copy/rules) + `manual`(永不覆盖) + `conflicts` + `audit`），`git add -f` 入库。
- **护栏不准省的几条**
  - 建表期只拦译文本身的问题（空/残留假名/照抄）；宽度与 art 列位是**逐次出现**的属性 → 留到预填期四重 `guard()` 逐行把关。
  - **`is_pure_art` 别用 `aa_translate.kana_runs`**：那是残留检测器（已排除艺术假名），会把 `ワー！ﾊﾟﾁﾊﾟﾁﾊﾟﾁ` 这类欢呼台词判成纯图形 → 永久跳过翻译。正确判据 = 无汉字 + （无全角假名 或 ≥2 ASCII 图形笔画）+ 平假名 <2；再加**交付证据否决**（某章交付里模型改动过的 key 一律禁入 copy 轨）。
  - 预填判错的后果由**校对兜底**（实测 4 行漏译被 refine_aa 补回），但别依赖它。
  - `suspect_zh()`：译文里 ASCII 词被全角空格劈开（`宝可梦w　iki`）＝模型改坏迹象 → **不入表**，记 `audit.suspect` 等人工修。
- **实测覆盖率（零 token）**：抽样 22 章未译章 **23.7%**、回放 30 已交付章 **34.7%**（逐字相同 98.2%）；真机 2 章 smoke 预填 27.8%、52 秒跑完含校对。
- **经验教训**：固定内容表的最大收益不在 `『名』…说明`（wiki 权威轨天花板仅 1.0%），而在**本不该送模型的行**（纯图形/模型从未改动的行）——它们占 14.7%/23%。先量测「哪些行其实不需要翻译」，再谈替换。
- **艺术行否决 = 上面这条教训的通用化（2026-10-08，参考 zseineo/infrared-singularity 的候选级提取）**：`aa_translate.art_line_veto()` 接在 `is_translatable()` 最前——行级判定会漏掉「含全角片假名的 AA 图行」（AA 用 ミ/ニ/ハ/ノ 及借字 三/二/乂/彡 作笔画），送过去模型只原样复述。判据：整行**无平假名 + 无非艺术汉字 + 无句末标点/引号**，且命中艺术信号（AA 墙同字≥3 或 艺术字符占比 ≥0.5）→ 本地照抄；**保护**：真片假名词（ライナー/ウオッカ）、连续成串的半角假名 sfx（ｸﾙｯ/ｲｲ/ｲﾀﾀﾀﾀ；浊点/半浊点/长音不算、散落孤立的不算）、笑声（ハハハ）、引号包裹任一命中即不否决。`AA_ART_VETO=0` 关；日志 `[art-veto] N 行纯艺术笔画 → 本地照抄`。
  - **实测**：全库抽样（104 作品 × 前 5 章）送模型**行 −27.3% / 字符 −29.7%**；艺术密集作品单章 −57~63%（白头第4话 3357→1360 行；VOICEROID −57%；文本密集的この素晴らしいシンオウ −24%）。质量用**已交付汉化当地面真值**（汉化==原文 = 模型原样复述）：51 章里否决的 6.8 万行 99.5% 属此类，误杀 0.5%（多为空白差与模型把笔画译成汉字的事故行，如 アニニニ→啊你你你）。
  - **方法可复用**：拿已交付汉化当标注集，量「新判据会砍掉的行里有多少是汉化==原文（赚）/ != 原文（误杀）」，是零成本调这块阈值与保护规则的标准做法（阈值 0.4/0.5/0.6、保护规则的取舍都靠它定）。
  - 用例 `scripts/tests/test_art_veto.py`；同源思路（候选级锚点+打分、AA 墙/眼睛/手 硬否决、文档级名牌救援）在对方仓库 `aa_tool/text_extraction.py`，值得再挖的是「行尾对话区」与「两侧同类假名 run 被完整覆盖则不算碎片」。
- **术语强制替换必须带语境守卫（2026-09-30，实测 150+ 行已出货错译）**：`pre_terms` 把 wiki 官方译名无条件打在送模型文本上，「宝可梦名 = 日语常用词」的同形条目必然误伤：`ラッキー`(吉利蛋)→`当たったらラッキー程度`→「也就吉利蛋程度」、`プラス`(正电)→`プラス補正`→「正电补正」、`クラブ`(大钳蟹)→`だいすきクラブ`→「喜欢宝可梦的大钳蟹」、`カウンター`(双倍奉还)→`寿命カウンター`→「寿命双倍奉还」、`ファイヤー`(火焰鸟)→`かけ声がファイヤー`。**三条铁律**：① **≤2 纯假名字目默认不替换**——没有词界可言，`めい`(逃足奇袭) 会把 `煌めいている` 打成 `煌逃足奇袭ている`、`あわ`(泡沫) 吃掉 `あわよくば`/`しあわせ`、`まひ`(麻痹) 吃掉 `いまひとつ`；要保留必须显式授权（`ヌシ`→霸主 全库 867 行正例 → 允许）。② **假名邻接守卫必须对所有长度生效**（原只 ≤4 字 → `ストライク` 吃掉 `ストライクゾーン`/`ストライク・ジークヴルム`）。③ 语境守卫（`禁`/`require`/`deny`，写在 `补充术语.json` 的「歧义」段，按 jp 匹配）在命中处判定；**require 里绝不要写 `Xの|Xに|Xが`**（自身+助词＝恒真，等于没写——实测踩过后 `ラッキー` 仍误替换）。诊断手法：拿已交付汉化与原文逐行对照、按「命中数 / 确认错行数」列清单，别按感觉调词表；修完必须复算全库（本项目 575 章复算 ≈9 分钟）。批量改动后**TM 表是放大器**：旧错译被 TM 零调用直出，只改文件不清 TM 会复发。方案+数据：`doc/方案归档/宝可梦术语替换歧义守卫方案_2026-09-30.md`（第 6 节 = 下面的替译）。
- **同形「两义」要用替译，不能只靠上面的守卫（2026-10-09，用户报「果类道具简写翻错」）**：守卫只能决定"换不换"；当一个日文**两义都该替换**、只是译名不同时，只写 `允许` 会让另一义被吃光——`ラム` 的角色译名（拉姆）把道具简写 `ラム`(=木子果) 全换成「拉姆」，已出货「カビィがラムだった」→「卡比是拉姆」、「ラム＆オボンだな」→「拉姆＆欧文」；裸 `オボン`(=文柚果) 此前无词条，模型乱出（欧文/橙橙果/释陀果/Obon）。做法：① `terms` 写**默认义**（`ラム→木子`、`オボン→文柚`；用户口径「对应原文：原文简写就简写」，全长仍是 `ラムのみ→木子果`）② 歧义条目加 **`zh`**（`{"jp":"ラム","zh":"拉姆","require":"…"}`）= 语境命中出 `zh`、否则出默认义，命中带 `alt` 标记（无 `zh` 的条目语义不变）。词表必须**穷举全库站点**标定：両作 376 站点逐条核对（角色 95 / 道具 281，零误判），但 `指令`/`手持ち`/`アシスト` 这类词**不能**写进 require（`狙撃指令…ラム持ち` 是道具义，实测误判），尾巴几条读者帖语境词是人工看完全部站点补的。单测 `test_pre_terms.py::TestAltSense`（默认义/替译义/全长优先/窗口距离/无 zh 不变）。
  - **存量修复套路（同日，改 167 行 / 105 章 + TM 132 条）**：机制落地后对已交付文本做定向修，分三类——① **确定性改名**（只换道具名跨度，错译候选表实测收集，**候选数必须等于源出现次数**才动手，同行的 ラム+オボン 逐词源各算一次且已满足词源的译名跨度要**预留**防互吃）② **未译系统行**（源行整句照抄日文）按**同族已交付行**口径补译，名称沿用汉化里已有写法 ③ **语义漂移行**（模型把 ラム 译成 `乱数`/`光墙`/`麻痹` 或整个丢掉）显式逐行覆盖。复验四件：残留错译 0、行数逐文件一致、改动行宽度==源宽（不足 pad）、角色义站点一条未动；同步改 `work/tm_<work>.json`（不清 TM，旧错译会被零调用直出回来）。

## 相关技能
- 汉化管线本体：本技能（原站提取→翻译→清理→交付）
- **交付 HTML 反解析（HTML→原文txt）**：见 `aa-html-reparse`（多工具导出格式忠实还原，U+3000/空行铁律，标题实体转义，章节排序去重）——2026-08-24 时间停止探索者亚鲁欧 300 话案例

## 素材库：AAhub（<repo>\AAhub）— ⚠️ 2026-10-10 已删除
> 素材库 2.3G 与工具 `scripts/common/aa_hub.py` 已随「AAHub 辅助分类」搁置方案收尾**删除/归档**（`aa_hub.py` → `scripts/legacy/`）。以下为历史记录；需要时重下 `https://dataset.aahub.org/datasets/mlt_v32_1.zip`（544MB）。
- aahub.org HukuTemp 素材包 v32.1：**15023 个 .mlt**，2.9GB，Shift-JIS(CP932) 编码，五十音分类（あ行~わ・を・ん）+ 汎用AA + 2ch + 実在人物パロディ
- mlt 格式：第 1 行=更新日期，`[SPLIT]` 分隔同文件多张 AA；文件名=角色名（やる夫01（基本01）.mlt 含 456 张表情）
- 工具 `python scripts/common/aa_hub.py`：`search <关键词> [--limit N]`（文件名 AND 匹配）/ `show <名称片段> [--n 图序号] [--head 行数]`（SJIS→UTF8）/ `chars`（单图字符统计，可扩 ART_KANJI）/ `dirs`；浏览器直接打开 mlt 也能看
- 用途：查角色原版 AA 画法、确认角色名日文原名（进 glossary）、统计绘画字符集

## 矫正工作台（本地 Web 应用，2026-08 新增）
- 启动：双击仓库根 `一键重启工作台.bat`（杀旧进程+重启+开浏览器）或 `python scripts/tools/aa_web.py` → http://127.0.0.1:8765（.venv 依赖：fastapi/uvicorn/jinja2；Hermes 终端 PYTHONPATH 劫持已由启动器弹掉）
- **矫正（2026-08-09 简化，无独立编辑页）**：阅读器内完成——目录与正文之间是行号列（与正文逐行对齐，txt 行号 1-based；# 行不显示行号），点击行号弹窗一次改一行：类型单选（错译/漏译/自定义）+ SRC 整行展示 + TR 文本框（wrap=off 完整行）+ 实时宽度徽章；保存走既有校验/备份管线（自动 pad 到 SRC 宽、超宽拒绝），正文行实时重渲染 + 行号闪烁；**每次修改追加 work/edits.jsonl**（ts/章节/行/类型/改前/改后），阅读页「修改记录」按钮查最近 100 条
- **阅读进度（2026-08-09）**：滚动节流（800ms）+ 翻章前保存到根目录 `.aaweb_progress.json`（gitignored）；阅读器无 `?f=` 时自动续读上次章节+行（toast 提示），作品页有「续读」按钮 + 章节列表 ★ 标记
- **图片漫画（仅阅览）**：`/manga` 列表 + `/manga/<名>` 阅读器（240px 侧边栏 + 整章图片堆叠 + 键盘翻章 + 章节进度）；注册表 `main.py IMAGE_MANGA`（名称→章节目录，子目录=章节，图片按文件名排序）；已注册东方心绮谭（comic_seamless 裁边版）、白馒头（manhuagui_ 单话）；`/manga-img/<名>/<路径>` 有 realpath 目录穿越防护
- **性能（2026-08-09）**：作品页三层缓存（内存 TTL 300s + `work/stats_cache.json` 按 mtime+size 增量 + 启动后台预热线程）；`aa_common.disp_width` 改 C 级正则 `2*len - len(findall([\x00-\xff\uFF61-\uFF9F]))`（10x）；`chapter_stats` 对 TR==SRC 行跳过宽度计算。实测：白头冷载 12s→5.7s（一次性），热载 0.01s
- 其余功能：跨章节搜索（命中跳 `?line=N` 高亮）、术语表编辑（作品块只读，紧凑 JSON+无 BOM）、重新生成合集（纯本地零 token，md5 字节稳定实测）、应用术语表（预览→确认→备份）
- **作品专属术语表（2026-08-26 定型）**：每作品独立文件 `glossary_<作品id>.json`，**存该作品 `work/` 目录**（`aa_web_app/logic.py work_glossary_path` 从 registry 解析 work_dir；未注册作品回退项目根兼容单测）。`{"schema":"AA翻译作品术语表 v1","更新日期","glossary":[[日,中]],"known_names":[]}`。**全局 `glossary.json` 的替换对会串作品**（如 アリス 在不同作品出处/译名不同）→ 作品级独立文件隔离，只对本作品生效（工作台应用术语表 `全局对+作品对` 叠加）。注意：翻译管线 `aa_translate`/校对 `proofread` 目前**不读**这份作品文件（它们只读全局 glossary.json），作品级替换靠工作台「应用术语表」落盘。
- 安全：保存前快照 `work/backup/<ts>/` 滚动 10 份可回滚；宽度==SRC 硬约束
- 代码：仓库根 `aa_web_app/`（与 scripts 同级；main.py 路由 / logic.py 纯逻辑）+ scripts/tests/test_aaweb.py（20 用例，零依赖）；首页统计 TTL 300s（冷算全作品≈25s，保存/重新生成失效缓存）
- 坑：kill 后台 uvicorn 要按端口杀真进程（netstat -ano | grep :8765 → taskkill /F /PID）；Jinja2 自动转义会吃掉 render_line 的 HTML → 模板必须 `|safe`；路由参数用 `{fname}` 单段（`{fname:path}` 贪婪匹配会吞掉 `/line/{idx}` 子路由返回 404）

## 脚本（scripts/，3 类分层，2026-08-08 重构）
- **common/（项目通用）**：r401_index.py（网站目录读取，search/category/site 子命令，--json 输出章节清单喂 dl_chapters）；aa_common.py（disp_width/pad/kana/白名单唯一源）；aa_api.py（call_api/parse_result/translate_lines，reasoning none + UA 两坑）；aa_translate.py（--work <id> [--pilot eid,...]）；scan_width.py / pass2.py / pass3.py / fix_width.py / analyze_mismatch.py（均 --work）；dl_chapters.py（--json 或 --work）；extract_all.py（raw→aa，按 registry site 字段选解析器）；gen_aa_html.py（三套模板 saki/fc2/legacy；2026-10-06 起写**交付文件夹** = index.html + data/*.js(gzip+base64 已渲染 HTML) + aa|汉化 txt.gz 源，含译/原切换与阅读进度；`--no-source` 关源留档、`--one <base>` 单章试读）；gen_glossary.py
- **sites/（网站通用）**：yaruonichijou.py（span.aa 括号配对，输出 # 标题+# 原帖地址+===== 块分隔）；bookshelf.py（dt/dd+红色区间重叠+1D10 加粗）；fc2.py（entry_body/entry_more+comment_cut 保留+ID 蓝粗）；blog_2nt.py（article+跳过模板空行+名前：帖头）；yaruobook.py（entry-content+评论区）；_base.py（clean/build_txt/帖头颜色常量）。统一接口 extract(raw,eid,src_name)→(txt_lines,colormap,extras)
- **works/（专用）**：registry.py（作品注册表：site/aa_dir/out_dir/work_dir/raw_dir/name_style/url_pattern/html_css/label_style/min_size）；translate_*.py 与 gen_*_html.py 为薄壳；pass4~pass10、pass5_*、pass6*、pass7*、pass8*、pass9*、fix_pad_fc2、fix_comments_yaruobook、fix_manual_yaruobook、pass_review_yaruobook、restore_baitou_from_html、postprocess（白头 ORDER/TITLES）、gen_yaruobook_html（评论区渲染）、gen_saki_html/gen_2nt_html（单章试读）、saki_runner/kaiki_runner
- **legacy/（归档）**：旧平铺 aa_translate/extract_*/dl_*/pass2/3_*/scan_width_*/fix_width_*/fix_residual*、旧 gen_*/translate_*，自包含可运行（互相引用用 legacy 自身 aa_translate），勿再使用
- 新增作品流程：r401_index search→category --json→registry 加条目→dl_chapters --json→extract_all --work→aa_translate --work（先 1 话试读评估）
- **通用 runner（2026-08-21 起推荐入口）**：下载 `python scripts/runner/dl_runner.py --work <id> [--chapters all|new|eids|1-20]`（零 token：r401 全分页清单→下载→提取→固化 chapter_order.json）；翻译 `python scripts/runner/translate_runner.py --work <id> [--chapters new|eids] [--force]`（aa_translate --pilot→定向 scan/fix_width/refine_aa 校对只处理本次章节；**合集一律手动生成——2026-10-06 起 runner 无 `--gen`**，用 `common/gen_aa_html.py --work <id>` 或工作台「重生成合集」；默认只译未译章）。**日志**：两个 runner 均写 `<work_dir>/runner_log.txt`（`--log <file>` 覆盖，`--log none` 禁用），每次运行按时间戳归档旧日志为 `runner_log_<ts>.txt`（gitignore）；工作台任务页轮询同文件尾部。方案 17 项决策见 `doc/runner自动化方案_2026-08-21.md`。坑：清理环节绝不全量扫描（误改已交付）；`chapter_order.json` 若本地数据来自与 r401 不同的镜像（jjikan_teishi 本地 yaruobook 270话 vs r401 19401 yaruo-matome 268 eid 脱节），须按本地 aa 标题话数重建顺序（不要用 r401 eid）。
- **leak-fix 误翻 art 行事故（2026-08-26，r401_metsubou entry_30613 13 行人工修复）**：`translate_runner._fix_chapter_leaks` 判定"漏译"= 原文含假名 + 汉化==原文 + 非艺术行；艺术行判定用 `_ART` 字符占比≥30%，但字符集太小（AA 艺术常用假名/符号 ｱぅさハｧﾐ､Ⅵ∨υゝﾍﾌﾊ 不在其中）→ art 行占比被低估（20-29%）逃过跳过 → 又含全角假名（ホぅさ）+ 汉化==原文 → 判漏译 → 送模型 → 误翻成拟声中文（嘿咻/嘎吱嘎吱/啊啦啦）。**已修**：接入 line_meta（`load_meta_0based`），kind=art 行硬跳过，meta 缺失回退旧判定。**教训**：判定"漏译/补译"的环节必须与主翻译同口径（line_meta），裸规则（字符占比）永远追不上 AA 艺术用字；runner_log 里 `[leak-fix]` 标签是排查误翻的第一线索；line_meta 全程存在时主翻译（is_translatable）art 行 0 误放行，误翻必在清理/补译环节。
