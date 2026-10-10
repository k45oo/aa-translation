---
name: aa-delivery
description: Use when AA漫画交付适配: 响应式HTML合集、CDP手机实测. 图片版走aa-image-export.
---

# AA 交付物多端适配(响应式 HTML / 图片版指引)

> 2026-09-07 合并自 aa-delivery-formats + aa-manga-delivery(两者删除)。图片渲染/字体语义/CBZ 的**现行权威 = `aa-image-export`**(本技能只做 HTML 侧 + 验证方法论 + 入口指引)。

## 触发
汉化完成后交付:重新生成 HTML 合集、手机端适配验证、转竖屏图片版(→ aa-image-export)。

## 两条交付路径
- **A. 响应式 HTML(主交付,零 token)**: `python scripts/common/gen_aa_html.py --work <id> [--no-source]`(统一生成器, 写出**交付文件夹**)。图片版 → 见 aa-image-export。
- **B. 竖屏长图/CBZ(手机)**: → **`aa-image-export`**(aa_to_images.py + aa_to_cbz.py,字体语义复刻 HTML 的现行权威;本技能早期实现已被其取代)。

## 交付文件夹结构(2026-10-06 起,取代单文件 HTML;2026-10-08 起两形态自动分档)
`aaManga/交付HTML/<标题>_汉化合集/` —— registry `html_out` **就是该文件夹路径**(写入=文件夹;判据/mtime/导出/删除用 `gen_aa_html.deliver_entry(cfg)` = 其中的 `index.html`,别用文件夹 mtime/存在性——Windows 目录 mtime 改内容不变):
- `index.html` — 目录栏 + 章节骨架(`<pre class="aa" id="preN">`)+ 右上角 `译文/原文` 切换 + **阅读进度记忆**
- **章节数据两形态**(payload 总量 ≤ `gen_aa_html.INLINE_MAX_BYTES`=20MB 自动选,记在 `edit_meta.json` 的 `layout`;`--inline`/`--external`/`--inline-max <MB>` 覆盖):
  - **inline 单档** — 全部数据内嵌在 `index.html` 末尾的 `<script>window.AA_INLINE=1;…</script>`,`data/` **不存在**;双击 `file://`/发微信/QQ/手机文件管理器打开都能看(顺带消掉 content:// 只授权单文件→读不到 `data/` 的老毛病),零服务依赖
  - **external 外置** — `data/NNNNN.js` — 一章一个:`window.AA_DATA[n]={tr,src}`, gzip+base64 的**已渲染 HTML**;页面用 `DecompressionStream` 解压后填进 `<pre>`(大书才走这条)
- 两形态页面 JS **同一份**,唯一分支在 `drop()`:`if (!window.AA_INLINE && window.AA_DATA) window.AA_DATA[n]=null` —— 单档没有可重读的 `data/*.js`,置空就再也读不回来(回访白屏,已由测试钉住)
- `aa/<base>.txt.gz` + `汉化/<base>_汉化.txt.gz` — 源留档(默认带, `--no-source` 关);另把 `aa/<base>_colormap.json` 与 `work/sfx_marks/<base>.json` 一并带上（交付页编辑后重渲染要用）
- `AA编辑服务.exe` + `编辑服务.bat` + `edit_meta.json` — 交付页**编辑服务**（PC 端改行/翻译替换写回源；见下节）。exe 由 `tools/build_edit_server.py` 产出，生成器自动拷进交付夹；没打包过则改拷 `edit_server.py` 兜底
- 单章试读 `--one <base>` → `work/单章渲染_<base>/`;`main()` 里正文一律经 `_blank_plan` 楼间间距计划补空行(与旧内联实现等价)
- **章节数据懒加载(2026-10-06 二次优化, 章节多必用;仅 external 形态)**:`index.html` **不预载**任何章节数据(只有骨架+目录+JS),切章时才注入 `<script src="data/NNNNN.js">`;启动只载恢复章,之后预取下一章;DOM/解压结果只保留当前章 ±2(`KEEP`),越界 `drop()` 释放 `<pre>` + DATA/TXT/LOADED(回访从浏览器缓存秒回)。**inline 形态**:数据早在内存,`load()` 的 `dget(n)` 直接命中 → **一个 `<script src>` 都不注入**(实测 140 章单档:首屏 32.5KB 正文/401ms、切到第 100 章 60KB、回收后回访正常、全程 `document.querySelectorAll('script[src]').length === 0`)
  - **实测对照(この素晴らしいシンオウに祝福を, 810 章: 旧单文件 252MB vs 新交付文件夹)**:可交互 `showChapter` 就绪 **4.5s → 0.4s**;DOM 节点 **613,908 → 11,959**(51×);切章(缓存命中)**66~131ms → 12~32ms**,冷章(读盘+解压)**~300ms**;首屏只注入 1~2 个 data 脚本(全量预载要读 169MB 脚本)
  - **两个已踩的坑**:① `data/*.js` 是后注入的,它们会 `window.AA_DATA = window.AA_DATA || []` **新建数组** → 启动时 `var DATA = window.AA_DATA` 捕获引用永远是空数组(必须 `dget(n)` 现读 window.AA_DATA)② 骨架里第 0 章**默认可见**,首跳必须 `hideAll(n)` 显式关掉其余章,否则恢复章节时第 0 章(空)与恢复章同时显示
- **为什么分档**:`file://` 双击时 fetch/XHR 被 CORS 拦死、只有 `<script src>` 或**内嵌**能离线加载 → 小书内嵌最省事(实测 140 章单档 = index.html 6.12MB(v2 后;v1 时 8.82MB)、首屏 401ms;只带译文不做译/原切换还能再减半);大书内嵌会把单档推到百 MB 级(810 章作品 payload 实测 168MB)→ 才 `data/*.js` 外置。**判据是 payload 字节,不是章数**(同样章数厚薄差很多)
- **payload 两种格式(v1/v2,生成器逐章自动选)**:v1 = `{tr,src}` 两份各自 gzip+b64;v2 = `{src,p,v:2}` = src 全量 + tr 相对 src 的**差异行**(`行号+STX+内容` 每行一条,patch 文本再 gzip+b64)。依据:**AA 里艺术行/帖头/空行不翻译 → tr 与 src 约九成行逐字节相同**(抽 8 章实测 90.0% 行/90.8% 字节),v1 等于把同一内容存两遍。实测省 31~42%(fukou 24 章 3762→2284KB;キッコーマン 140 章 8874→6107KB;810 章那部 data 168MB→~97MB)。**选格式判据**:patch 比整份 tr 小才用 v2,行数不一致/不划算自动回退 v1。前端 `decode()` 按有没有 `p` 分派(`unpatch()` 按行号覆盖),**两种格式共用同一份页面 JS**;编辑服务写回时从现成 src b64 解出原文行重算 patch(不会把改过的章退回 v1)。**等价性实测**:同一作品 v1 vs v2,首屏/切章后正文长度逐一致(32525/60092 字符)、回收章回访正常、全程 0 个 `<script src>`
- **必须整目录拷贝**(只拿 index.html → 页面报「章节数据未加载」);`gzip.compress(..., mtime=0)` 保证字节可复现
- 源文本透明读 `.txt/.txt.gz`(`_read_lines`);`chapter_order` 同时认 `_汉化.txt.gz`(同名并存时取未压缩那份,不重复成两章)
- 少数作品 `html_out` 仍带 `.html`(白头 postprocess / 魔法戦姫 gen_yaruobook_html 生成器已进 legacy, 未迁移)→ `deliver_is_folder()` 为假, 按旧单文件语义处理;`gen_aa_html` 遇这种配置会写到去掉 `.html` 的文件夹, **不覆盖旧文件**

## 交付页编辑服务(2026-10-06：别的机器没工作台也能改)
- 形态: `AA编辑服务.exe`(PyInstaller 单文件, 10.5MB)＋`编辑服务.bat`(双击启动器)＋`edit_meta.json`(作品/章节/**相对仓库根**的源路径/`render_ver`)。双击 exe/bat → `http://127.0.0.1:8770`(占用自动 +1, 共试 10 个端口) → 页面右上**自动探测**并亮出「✎ 编辑」「⇄ 替换」
- 边界: **只有 PC 端能改**(`@media(max-width:640px)` 里 `.editbtn{display:none!important}`), 手机端纯浏览; 无服务 / 只读交付夹 → **两个按钮置灰不可点 + 鼠标悬浮给原因**(`editBtns(false, tip)` 把理由写进 `title`, 不弹红色提示条; 提示条只留给 stale 这类真警告)
- **编辑交互 = 工作台同款(2026-10-07 改版)**: 进编辑模式后正文左侧出现**行号列**(`.aa-gut`, 行号 = txt 1 基行号; 楼间距补出来的空行是 `.none` 占位不编号), **点行号**在原行位置插**行内编辑行**(`.aa-edit` = 译文 textarea + 原文行 + 按钮条, 原译文行加 `.hide` 让位; 行号列同步跟进等高占位)。textarea 与原文行都用 `font:inherit` + 左边界对齐 → **列位与正文逐行对齐**; `Ctrl+Enter` 保存 / `Esc` 取消 / `Shift+R` / **右键行号** = 还原原文并直接保存(不弹确认); 点正文不再触发编辑
  - 行/正文对齐靠三件事: ①`.aa-wrap` 是 flex, 必须 `.aa-wrap>pre.aa{margin:0}`(pre 自带 `1em` 上边距会把正文压低 15px, 行号列整体错位) ②`--aa-lh`(17px/手机 14px) 与 pre 的 line-height 一致, `.aa-gut` 的 padding-top = pre 上边框 + 上内边距 ③行号列必须与正文**同源生成**: 编辑模式里 `fill()` 把每行包成 `<span class="aa-line" data-i="txt行号">`(换行放在 span 内部 → 行间没有裸文本节点, 才能在流里插编辑行而不多空行), 行号列遍历 `pre.children` 生成, 编辑块按 `offsetHeight` 占位
- 验收行号列对齐: 在 CDP 里按 `pre 内容顶 + k*line-height` 逐行比 `cell.getBoundingClientRect().top`, **要求 0 错位**(脚本 `scripts/probe_edit.js` 的第一段断言)
- 写回**两个都写**: ①交付副本 `汉化/<base>_汉化.txt.gz`(主, 页面数据源) ②`edit_meta.json` 记的源 `汉化/<base>_汉化.txt`(本机存在才写, 拷到别处自动跳过; meta 里存**相对仓库根**的路径 → 服务按交付夹位置反推仓库根 `repo_root` 再拼回来, 交付物因此不含本机绝对路径)。行修改走 `POST /api/save`(宽度自动补位+重渲染该行+**重写该章章节数据**: external → `data/NNNNN.js`;inline → `index.html` 里 `window.AA_DATA[n]={…};` 那一段 `_rewrite_inline`,整档原子写、只动目标章), 替换走 `POST /api/replace`(preview/apply, `re:` 正则, `sfx` 字段, **整行跳过 AA 艺术行**)
- 批量替换后**不能靠 `?v=` 破缓存**(file:// 加 query 不可靠): 受影响章走服务端 `GET /api/chapter?ch=N` 直接回整章渲染 HTML

### 手机上读合集（--lan / 「手机阅读(局域网).bat」，2026-10-07）

手机直接点开交付夹的 index.html 会报「读不到 data/00000.js」——**不是文件夹没拷全**：文件管理器/QQ 把 `content://…` 交给浏览器时只授权了 index.html **一个文件**，页面里 `data/NNNNN.js` 这种相对路径读不了（页面老提示词「本文件夹必须整体拷贝」是错的，已改成按协议给准确指引）。

```
交付夹\AA编辑服务.exe <交付夹> --lan     # 或双击 手机阅读(局域网).bat
```
- 绑 `0.0.0.0`，控制台打印 `http://<局域网IP>:8770/index.html`（本机 IP 由 UDP connect 取默认出口网卡 + gethostbyname_ex 补全；多个时第一个优先，其余常是虚拟机网卡），并写 `手机打开地址.txt` 到交付夹（好发到手机点开）；首跑会弹 Windows 防火墙 → 选允许(专用网络)
- **远端(手机)一律只读**：`client_address` 不是回环也不是本机网卡地址 → POST /api/save、/api/replace 直接 403；`/api/ping` 回 `remote:true, can_edit:false`，页面按钮置灰并说「局域网只读…在电脑上用 127.0.0.1 打开」。PC 用自己局域网 IP 打开仍算本机（可改）
- 默认（不加 --lan）仍只绑 127.0.0.1——**绝不默认绑 0.0.0.0**（服务有写权限，等于全网段可改）

页面侧：读不到 data/ 时按 `location.protocol` 分三种给话（`loadErr()`）：content://（文件管理器/QQ→给 局域网bat/火狐/本地服务器 三条路）、file://（安卓 Chrome 不许本地页读同目录→换火狐/局域网）、http(s)（真缺 data/）。指引面板 `#edithint.loaderr` **手机上必须显示**：手机媒体查询里 `#edithint{display:none!important}` 要给它开例外（`display:block!important`），且 `top:44px` 让开顶部工具条（否则面板标题被「目录」按钮压住）。

验收：除 `scripts/probe_edit.js` 外另有 `probe_lan.js`（手机视口从局域网地址读正文/目录抽屉 100% 高/把 data 挪走后看指引面板截图）；逻辑回归 = `scripts/tests/test_edit_server.py`（`source_address=127.0.0.2` 伪造远端 → 真起服务验证写 403、本机可写、ping 标记；`TestInlineWriteBack` = 单档写回只动目标章/缺章报错，`TestInlineEndToEnd` = 生成单档夹后真改一行 → 校验 `index.html` 内嵌段更新）+ `test_gen_html.TestPayloadFormat`（v2 选用/生成器选格式的还原/patch 只列变化行）+ `test_gen_html.TestLayoutSwitch`（阈值两侧/`--external`/`--inline-max` + **两形态章节数据逐字节相同**）。改 edit_server.py / 改页面后：**重打包 exe（build_edit_server.py）→ 重生成交付夹**，顺序不能反。

### 打包/联调坑(全部实测踩过)
- exe 里打的是**数据**(整棵 `scripts/`＋`aa_web_app/logic.py`, 用 `importlib` 动态 import), 不是模块: PyInstaller 静态分析看不见它们 → **动态加载脚本 import 的标准库必须 `--hidden-import`**(漏 `urllib.request` → exe 启动时才 ImportError)
- `--add-data <repo>/scripts` 会连 `scripts/tools/dist` 里**上一次的 exe** 一起打包(13MB→26MB), 放 workpath 在 scripts/ 里更会报 `Trying to collect PKG file into itself!` → 先拷一份 staging(排除 `__pycache__`/`tools/dist`/`*.exe`), workpath/specpath/dist 放 `temp/`(已 gitignore)
- **改了 `gen_aa_html.py` 必须重打包 exe**(渲染逻辑在包里): `edit_meta.json` 带 `render_ver`(gen_aa_html+aa_common+logic 的 sha1 前 12 位), 服务启动自检不一致 → 控制台 + 页面提示「建议重新打包」
- **打包脚本里别给模块级常量赋局部值**:`build_edit_server.py` 的 `main()` 里写 `EXE = ...`(except 分支另存 `.new.exe`)→ `EXE` 变函数局部变量, 上面的 `copy2(built, EXE)` 抛 `UnboundLocalError`, **整脚本打不出包**且只在最后一步报错。用局部别名(`out = EXE`)再改 `out`。改完**先跑一次打包**验证得通, 别等重生成交付夹时才发现
- 页面 `.editbtn{display:none}` 靠 JS 亮出: 只能设**具体值** `style.display="inline-block"`, 设 `""` 会退回样式表 → 按钮永远不见(实测踩过)
- bat 里 `"%~dp0"` 结尾是反斜杠, Windows 把 `\"` 当转义引号 → 参数带脏字符、服务认不出交付夹; 用 `"%~dp0."` 且服务端再容错去引号/尾斜杠
- http.server 在刷新/关页面时往控制台甩 `ConnectionResetError` traceback(用户以为坏了) → 服务端 `handle_error` 对 Reset/Abort/BrokenPipe 静默; 启动横幅 `print(flush=True)`(非控制台时块缓冲看不到地址)
- **交付页 JS 写在 gen_aa_html 的 Python 三引号(非 raw)字符串里 → JS 里写 `\n` 会被 Python 先解码成真换行**, JS 字符串断行, 整页脚本 `SyntaxError`, 编辑/替换/行号列全挂, 而 index.html 还是「生成成功」的。要换行统一用页面里的 `var NL = String.fromCharCode(10)`。**验证必须验生成物**: 从生成的 index.html 抠出 `<script>` 段 `node --check`(`scripts/check_delivery_page.py`; 回归防线 = 测试 `test_gen_html.TestDeliveryFolder.test_page_js_syntax`)。验源码是验不出来的(源码里看着对)
- 行号列/正文错位排查顺序: 先量 `pre` 的 `margin`(UA 默认 `1em 0`)与 flex 对齐, 再量 line-height 是否等于 `--aa-lh`, 最后量行数是否与 `<b>` 数一致
- 验收: `node scripts/probe_edit.js <file://…/index.html> <http://127.0.0.1:8787/index.html> <outdir>`(CDP) 一次过: 只读态按钮置灰+悬浮理由/行号列逐行对齐(0 错位)/点行号开行内编辑行(左边界对齐)/保存进 DOM/右键行号还原/Shift+R/替换弹窗/手机抽屉 100% 高+关闭无阴影; 另有 `scripts/check_delivery_page.py <index.html>` 单验页面 JS 语法

## 响应式 HTML 移动适配(2026-08-09 落地)
- `gen_aa_html.py` 模板已内置: head 加 `<meta name="viewport">` + CSS_MODERN/CSS_LEGACY 末尾 `@media (max-width:640px)` 块(header/nav 去 margin-left、nav `position:static;max-height:34vh;overflow-y:auto`、main 全宽、pre 11px/14px)
- 已适配:さーきー/白头/yaruobook 等(模板改动后需重新生成生效);**手机看 HTML 非强需求**(无 MS Gothic 等宽 fallback 破版,用户接受,长图方案优先)
- 目录标题**去掉作品名**(2026-10-06): 帖头 `作品名　第N話/最終話` → 目录只留 `第N話`, 长作品名不再重复占宽
- 手机端(≤640px, 2026-10-06): 目录改**抽屉**(`nav` 移出文档流 + `transform:translateX(-100%)`, 点「目录」展开/遮罩点击收起/选完章自动收起), 阅读区**左右不留空**(main/pre padding 0, 正文贴左缘), 宽行仍靠 `pre` 自身横向滚动
  - **抽屉必须 `height:100%` + `max-height:none`**(上方还有一条旧的 `nav{max-height:34vh}` 规则会把它压成 34vh), 且 **`box-shadow` 只能挂在 `nav.open` 上**: 关闭时 nav 平移到 -100%, 阴影(2px/12px)会露在视口最左边(用户报的 bug); 关闭态再加 `visibility:hidden`(+ `transition:visibility 0s .18s`)连焦点/绘制一起去掉
- 桌面 >640px 完全不变; 适配后字节 diff 应恰为新增 meta+@media 行

## 手机布局硬数据(2026-08-09 实测,防误判)
- 无 viewport meta → 手机布局视口 980px 整体缩放 39.8%(15px 正文物理 ~6px 不可读);240px 侧栏占满首屏
- 行宽分布: 中位 67-83 格, p95 123-172 格, 极端横幅 339-891 格(白頭 891);>150 格仅 0.6-9.5%
- 手机 390px 屏 14px 字号每屏 ~56 格 → 宽行只能横滚或按块缩字号

## CDP 手机实测(验证方法论,必用; 工具 scripts/cdp_measure.js)
- **headless 截图/--window-size = 桌面 UA,不触发 980 行为** → 必须 `Emulation.setDeviceMetricsOverride {width:390, height:844, deviceScaleFactor:2.625, mobile:true}`
- 流程: Edge headless --remote-debugging-port=9222 后台 → node 连 CDP(cdp_measure.js)→ Runtime.evaluate 读 innerWidth/nav 宽/pre 字号/scrollWidth → Page.captureScreenshot
- 判定: 无 meta innerWidth=980、正文 ~6px; 适配后 innerWidth=390、main 全宽。page websocket 从 /json/list 取; 多页面 Page.navigate 复用

## 浏览器验证铁律(2026-08-10 七站实测)
- **视觉模型会幻觉——以 DOM 提取为准,不信视觉模型文字描述**(browser_vision 曾编造作品名/说"蓝底白字"/"全是日文",browser_console DOM 全对)
- **交付页别把整页截图喂视觉工具**:AA 单章动辄上万行(实测 617 行章 = 10,953 px 高,约 17.8 px/行;白头第1话 10,839 行 → 交付页高 ~276,413 px),而 Hermes 的 `browser_vision` **固定整页截图**(`--full`)→ 2.16 亿像素,超过 Pillow 解压炸弹硬上限(178,956,970 px)→ 视觉工具报 `source is not a recognized image` 或 provider `400 Invalid request parameters`,**不是工具坏了、也不是 `auxiliary.vision` 配错**(同尺寸图缩不动也认不出,`region=` 也救不了——裁切在 resolve 之后)。要看正文照旧走 DOM 提取(`browser_console`),要截图走 CDP 局部截图(`scripts/cdp_measure.js`)或先裁一块小图再喂;改字体列位后的对齐验证本来就是量 `getComputedStyle`/`measureText`,不用整页截图
- **本机 headless 实测走 Edge CDP**(browser 工具偶发把 evaluate 打到 about:blank):`"/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" --headless=new --disable-gpu --remote-debugging-port=9222 --user-data-dir=<tmp> about:blank` + `scripts/delivery_probe.js <file:///…/index.html> <mid>`(node≥22 原生 WebSocket,零依赖;测首屏/切章耗时、注入脚本数、切换只重填当前章、进度记忆、刷新恢复)
- `scripts/probe_switch.js` 是对照用的"切章耗时"探针(旧单文件 vs 新文件夹都能量;旧文件的 `<script>` 在文件末尾,`showChapter` 要等整个文件解析完才存在——这本身就是"加载卡")
- 验证链: navigate → browser_console 提取 `document.title`+h2+`pre.aa` 长度+中文正则+`span[style*="008800"]/ff0000` 计数+colormap 红字行 → showChapter 切换末章 → 截图仅辅助
- **交付文件夹页面要先等数据填好再验**(正文由 JS 解压后注入,`<pre>` 初始为空):轮询 `#loading` 隐藏 且 `#pre0` 有文本 → 再断言 ① `pre.aa` 数量 = 目录项数 ② 点 `.modebar button[data-mode=src]` 后 `#pre0` 变日文原文、`[data-mode=tr]` 切回 ③ `localStorage["aa:<作品名>"]` 存 `{ch,y}`、刷新后回到同一章同一位置 ④ 控制台无报错
- file:// 页面偶发重定向 about:blank → 重新 navigate 再验
- **改了存储/编码层后要拿「同一位置同一量」前后对照**:同一作品生成前后两版, 各取首屏/切章后 `pre` 的长度与行数**逐值比对**(数值一致 = 逐字节等价的强证据, 比截图目测可靠); 再抽 3~5 章(首/末/中段)**在 Python 里独立重渲染**, 与存档 payload 解码结果 `==` 比对。只验「能打开」会漏掉错行/漏行的静默损坏

## gen_aa_html.py 既有坑
- `chapter_order` 须按 name_style 过滤: eid_title 时排除 `entry_*` 前缀(さーきー 汉化/ 混 9 个 FC2 残留,不过滤 26 章假合集)
- eid_map 覆盖文件名推断(EID_RE 提 "1" → blog-entry-1.html;eid_map 12836 优先)
- **交付页正文基线 = 哈希清单（2026-10-07 重建，取代旧快照）**：`scripts/tests/fixtures/delivery_golden.json` 存每章 `<pre class="aa">` 正文（剥 sfx 后）的 sha1[:16] + 行数（现 4 部 46 章：fukou 24 / akari_ninshin 10 / jikan_teishi 10 / yaruohiroba 2）；测试 `test_gen_html.py::test_tr_matches_golden_hashes` 重建后逐章比。重建/扩展：`python scripts/tools/freeze_delivery_golden.py [--work id]…`（`--check` 只比不写）。
  - 为何不能用旧单文件：`交付HTML/<名>_汉化合集.html` 已被 ae6dc930 移出 git，且内容也落后于后续人工修订（「先生→桑」+宽度补位；jikan_teishi 甚至 1472→2441 行）→ 它只能当参照，正文字节基准必须现冻结（清单里 `vs_legacy` 记着差多少）
  - 基线红了 = 渲染口径或源文本真的变了：**先确认变更**，再跑 freeze 重建，**别改生成器去迁就旧样本**
  - **存放形态/数据格式多版本时,把差异吸收进测试侧的归一化 helper**（`_payloads()`: inline/external 两形态 + v1/v2 两格式都还原成 `{tr,src}` **字符串**再断言）→ 既有逐字节基线**自动覆盖新格式**, 不为每个变体另写一套。加格式时先扩这个 helper, 再跑基线; 断言写在 b64 层就等于什么都没验
- 生成器与历史交付结构差异(如 head/nav/样式) → 对比只比 `<pre>` 正文,别整文件 diff

## 交付目录约定
- 全部交付 HTML 统一 `<repo>\aaManga\交付HTML\`（2026-10-10 起）;图片漫画 = HTML + comic_seamless/ 同放 `<作品>/` 子目录、相对路径引用
- 改输出路径/形态必须同步: registry `html_out`(文件夹路径, 不带 `.html`)/ `gen_aa_html.deliver_dir|deliver_entry|deliver_is_folder` / 工作台 `main.py`(`_html_entry()` 判据与 mtime、`run_generator` 返回值、删除用 `_deliver_artifacts()`)/ `work_pack.html_files()` / 测试——漏改的典型症状是「判据盯着历史旧单文件、导出包漏掉交付物」

## 坑(实战)
- **字体栈现状 = `ＭＳ Ｐゴシック` 在前**（用户 2026-10-03 要求还原）：实测 PGothic 是比例字体（`|`=4px vs 半角假名 8px vs 汉字 15px，列位不齐）、MS Gothic 才是精确 1:2；但"改不改"由用户定，**不要擅自改回**。详见 `references/font-and-width.md`
- monkeypatch reg.get 闭包必须引用本次 cfg(默认参数绑原始引用 → 二次调用拿第一次配置)
- 无 git 基线验证: 从当前源码反构造改动前版, 断言"改后 = 改前 + 恰好 N 行"
- test_parallel_speedup 计时 flaky(10ms 级, 套件内偶挂单跑稳过), 勿误判回归

## 相关
- 图片渲染/CBZ/字体语义 → `aa-image-export`; 汉化管线 → `aa-manga-translation`; 矫正工作台 → `aa-web-review-app`
- references: font-and-width.md(字体实测)/mobile-verify.md(CDP 视口)/line-classify.md(行分类)/delivery-html-to-txt-reparse.md(HTML→txt 反解析)/r18-chinese-judge.md(中文 R18 判定)
- scripts/cdp_measure.js — CDP 手机模拟测量+截图; scripts/probe_edit.js — 交付页编辑(只读置灰/行号列对齐/行内编辑行/保存还原/手机抽屉)一次过验收; scripts/check_delivery_page.py — 校验生成好的交付页内联 JS 语法(防 Python 字符串吃转义); 正文基线维护 = 仓库内 `scripts/tools/freeze_delivery_golden.py` + `scripts/tests/fixtures/delivery_golden.json`