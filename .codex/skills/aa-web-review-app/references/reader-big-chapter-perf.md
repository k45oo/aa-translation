# 阅读器大单章：内存/卡顿量测与窗口化（已实施）

适用：用户报「读大单章久了电脑卡死 / 阅读器很吃内存」；或要改阅读器渲染/区间接口。
**先把「泄漏」和「体积大」分开判定**，别一上来就找泄漏。
**2026-09-21 起阅读器已全面窗口化（S0–S4）**，下面的「量测手法 / 泄漏判定 / 陷阱」仍然有效，
「现状」一节改为记录窗口化后的架构与口径。

## 1. 架构（S0–S4 已实施，权威文档：`doc/方案归档/阅读器窗口化渲染方案_2026-09-21.md` §9 + `aa_web_app/AGENTS.md`）

所有章统一走窗口化（无「大章才窗口」分支）：
- 服务端 `aa_web_app/reader_win.py`：`load_ctx`（一次读原文/汉化/精译 + colormap/line_meta_v2/overrides/sfx/gold，按 (work, base) LRU，指纹 = 文件 mtime+size，`invalidate(work)` 清）→ 几何表；`page_data` 只发 `{n, skips, mt, hasTr, hasRefine, mode}`；`rows(ctx, mode, k0, k1)` 渲染行 HTML（单次 ≤ `BLOCK_MAX`=200）。
- 阅读页只下发几何表（`<script id="win-data">`）+ 空 `<pre id="chapter-pre">` / 空 `<div id="line-col">`，正文与行号列都由 `static/reader.js` 的 `WIN` 按窗口重建。
- `GET /api/work/{work}/chapter/{fname}/range?from_k&to_k&mode=tr|src|refine` → `{mode, from, to, n, hasTr, hasRefine, lines[]}`。
- 逃生口 `?win=0` / `localStorage['aa_reader_window']='0'` → 客户端分批取完整章（旧整章 DOM 行为）。

实测（`やる夫は供給源になるようです`，本机 Chrome）：

| 项 | 窗口化前 | 窗口化后 |
|---|---|---|
| 页面字节（25,014 行章） | 21.9 MB | **69 KB** |
| 页面字节（3,446 行章） | 3.2 MB | **64 KB** |
| DOM 节点 | ~105k | **2.1k**（滚动中 2.1k–3.7k） |
| 窗口行数 | 全章 | 400–800（视口 ± 1 块） |
| 首屏 | — | first-contentful-paint 1.34 s |

## 2. 几何口径（改渲染前必读）

- 行高 = CSS `--aa-lh`（`#chapter-pre` 的 line-height，默认 16px）；**行高锁死是刻意的**（曾修字面漂移），虚拟化依赖它。
- 楼距：服务端 `k_mt` 是 **px**，`k_mtk = k_mt // 16`（行高倍数）；旧模板用 `l.mt / 16`，两者等价——做等价性比对时必须折算。客户端 `margin-top: calc(var(--aa-lh) * mtk)`。
- 窗口用上下等高 `.win-sp` spacer 撑出整章高度，`scrollHeight` 与整章一致 → 滚动条比例、跳行百分比都不变。
- 行号列与正文共享同一窗口、同一 `k` 序：`k_idx`（跳过的 `#`/`=====` 行有 idx 但无 k）→ 行号 = `idx+1`。

## 3. 窗口化必须适配的交互点（漏一个就是 bug，实测踩过的）

- **进度保存/续读/`?line=N` 跳转**：一律走几何表（`WIN.lineAtScroll()` / `WIN.scrollToLine()`），**不要数 DOM 行**（窗口外不存在）。
- **行号闪烁**：状态驱动（`WIN.flashLine(k, ms)` 在 `paint()` 时套 class），DOM 版 `classList.add('flash')` 会在窗口重建时丢。
- **编辑面板**：打开期间**冻结窗口**（否则重建抹掉输入）；面板/`.src-row` 的插入高度用 `setEditDelta` 计入几何，关闭后总高回原值。
- **保存行 / 划词替换**：`window.__aaLineChanged(idx)` → 失效该块缓存 + 区内就地重建（划词脚本用行内 `data-idx` 定位，不依赖 DOM 序号 ✓）。
- **台词纠正标记 / 候选高亮**：挂 `WIN.onPaint`，每次重建后重放。
- **`markChanged` 加 `quiet`**：批量操作时别逐次重绘。
- **逃生口模式**：`?win=0` 下禁用块淘汰（LRU 上限失效），否则 `rowAt()` 取到被淘汰的块会出现空洞。
- **未译章**：模板必须把 `fname` 回落成原文名，否则区间接口 404、正文空白（旧模板给 `''`）。

### 3.1 上线后踩出来的三条铁律（2026-09-21，用户反馈驱动）

1. **块缓存「只失效不补齐」= 正文空洞**（症状：翻译替换后页面位置整个跳）
   `paint()` 用 `rowAt(k)` 取行、取不到就 `continue` → 整块 200 行消失、`scrollHeight` 骤降（实测 58798→55470，−3328px）→ 后面的内容整体上移。
   规矩：失效块必须立刻重取（单行 `markChanged` 内 `await load(b,b)`，批量用 `markBatch`）；`sync()` 判「窗口内块齐不齐」（`cacheFull`），不齐就补，不能因「窗口号没变」早退；`repaint()` 先补窗口内块。
2. **编辑期间禁止重建正文**（症状：点行号后没有原文行、无法编辑）
   `.edit-row`/`.src-row`/`.lc-ph` 是 JS 插进正文流的，`paint()` 重建 `innerHTML` 会抹掉。
   规矩：`paint()` 在「`window.__aaEditing` 且 editK 在窗口内」时直接 return；`sync()` 编辑期间只 return（**不要 closeEdit**——会丢用户输入）；`openEdit` 必须在 `ensureVisible` 之后**先** `setEditDelta(k, 0)` 把 editK 钉住，否则冻结判据 `editK >= k0` 不成立，一次滚动就删掉编辑区。
3. **进度保存要尾沿 + keepalive**（症状：刷新后续读不在原位置）
   领先沿节流（`if (now - last > 800) save()`）在滚动停下那一刻常不触发 → 最后位置丢失。
   规矩：350ms 尾沿防抖 + `fetch(..., {keepalive:true})` + `pagehide` 落盘；任何 `location.reload()`（如翻译替换整章路径）之前显式 `saveAAResume()`。
   另：服务端续读原本只在**不带 `?f=`** 时下发 `resume_line` → 带章节参数的 URL 刷新必回章首；修法=`?f=` 时也续读，但仅当请求章 == 存档章（原文名/汉化名任一命中）才设，避免跨章误跳。跳转参数 `?line=` 用完 `history.replaceState` 抹掉，否则刷新再跳一次旧位置。
4. **保存后要刷新「哪一行」，别用会被重置的全局**（症状：保存后正文还是旧的、再次编辑却是新内容、刷新后才变）
   `closeEdit()` 会把 `curIdx = -1`；而行编辑保存路径却在 `closeEdit()` **之后**才 `markChanged(curIdx)` → 传进去是 -1 → `Math.max(0,-1)=0` → **永远只重取块 0**，被编辑行所在块从不刷新（行号闪烁也会落在第 0 行）。
   规矩：保存前 `const saveIdx = curIdx` 快照，之后一切刷新/闪烁用 `saveIdx`。右键还原原文用的是局部 `idx`，所以它当时是正常的——别被「同类功能有的好有的坏」带偏，先比对两条路径用的是不是同一个变量。

## 4. 量测手法（零前端工具链）

```bash
# 页面字节 + 服务端耗时（f= 必须非空，空 f 会回落到续读章 → 看错章）
curl -s "http://127.0.0.1:8765/work/<urlencoded work>/read?f=entry_XXXX.txt" \
     -o "$LOCALAPPDATA/Temp/p.html" -w "%{size_download} %{time_total}\n"
```

```js
// 浏览器 console（Hermes browser）
document.getElementsByTagName('*').length;                       // 节点数（窗口化后 2k 上下）
document.querySelectorAll('#chapter-pre span.aa-line').length;    // 窗口行数
document.querySelectorAll('#line-col button.lc-no').length;      // 行号数（应与行数一致）
window.__aaWin.lineAtScroll();                                    // 几何推算的当前行
performance.getEntriesByType('paint');                            // first-contentful-paint
```

**泄漏判定协议**（节点计数比堆稳定，GC 会让堆回落造成假阴性）：记录节点数 → 重复操作 N 次 → 再记录，**必须回到基线**：模式切换 40 次、编辑面板开/关 20 次、滚动到底再回顶。窗口化后回顶应精确回到 400 行 / 2131 节点。
**结论模板**：不是泄漏而是单页固定占用 → 谈窗口化/分片；窗口化后仍涨才是真泄漏。

## 5. 等价性 / 回归验证

- **工具**：`python scripts/verify/reader_win_ab.py <窗口化之前的整页基线> [--base-url ...]`——从旧整页 HTML 里解析 `src-data/tr-data/refine-data`，按 200 行块拉区间接口逐行逐字段比对（自动折算 mt→mtk）。2026-09-21 实测 17 组（3 万+行、tr/src/refine）0 不一致。
- **基线怎么来**：窗口化上线前 `curl -s ".../read?f=<src>" -o 基线/<base>.html`；或从 git 历史 checkout 旧 `main.py`/`reader.html` 起临时实例抓。**必须同一批数据**，否则比对必然报差异。
- **单测**：`scripts/tests/test_aaweb.py::TestReaderWin`（区间行 HTML == 单行渲染逐行、行元数据 == ctx 几何表逐项、page_data 无 HTML、BLOCK_MAX 截断、缓存指纹）。
- **旧页面 refine 行没有 `dlg` 字段**（S1 遗漏，窗口化一并修正）→ 比对 refine 模式时 dlg 只做提示，别当回归。

### 5.1 交互级回归探针（改 `reader.js` 后必跑，console 单表达式、结果只回小 JSON）

等价性脚本（上节）只比字节，抓不到「位置跳 / 编辑被抹 / 续读丢」——这三类必须用下面的数值判据。每条都先 `if (!window.__aaWin) return {err:'no page', href:location.href}` 自检。

1. **正文不出现空洞**（改缓存失效/重绘路径后）：取 `rows0 = #chapter-pre span.aa-line` 数、`sh0 = document.scrollingElement.scrollHeight`、`y0 = scrollY`、当前行 `line0 = lineAtScroll()` 与目标行的 `getBoundingClientRect().top` → 触发替换/保存（`markBatch(idxs,true)` + `repaint()` 等价于 UI 的批量路径）→ 四项必须**逐项不变**（行数与 scrollHeight 同时掉 = 块被渲染成空洞，不是内容变短）；批量闪烁要能一次盖上多行（`flashMany`）。
2. **编辑面板存活**：点一个**需要滚动才可见**的行号（模拟真实场景，别只点视口内首行）→ 断言 `.edit-row` / `.src-row`（原文行）/ `#er-tr`（textarea）/ `#edit-panel` 存在，改 value 后宽度读数跟着变 → 再 `scrollBy(0,500)` 后四者**仍在且输入未丢** → 关闭后 `#edit-row` 消失且 `scrollHeight` 回到原值。
3. **续读落点 = 存档行**：`scrollTo(0,Y)` → 等 ≥1s（尾沿防抖 350ms）→ 读 `.aaweb_progress.json` 或 `GET /api/aa/<work>/progress` 得存档行 → `location.reload()` → 断言 `lineAtScroll() == 存档行`、该行号按钮 `getBoundingClientRect().top ≈ 0`，且 `location.search` 里**已无 `line=`**（有 = 下次刷新会再跳旧位置）。另单独验：`curl ".../read?f=<别的章>"` 抓 `resumeLine:` 必须为 **0**（防跨章误跳）。
4. **服务端续读下发**：`curl -s ".../read?f=<本章原文名或汉化名>" | grep -o 'resumeLine: [0-9]*'`——这是不依赖浏览器就能验的服务端侧判据，先跑它再接浏览器。
5. **保存后正文就地更新**（行编辑/还原/替换共通）：挂 `window.fetch` 钩子记录请求 → 用 UI 真保存一行（`#line-col button.lt-text` 取一个**文本行**——art 行会被后端 422 拒绝，拿它测会误判）→ 断言：① 请求里出现**被编辑行所在块**的 `from_k`（例：idx 614 → `from_k=600`；假设拿到 `from_k=0` = 传错了 idx）；② `#chapter-pre span.aa-line[data-idx=<idx>]` 的文本含新内容；③ `__aaWin.cache.get('<mode>:<blk>')` 里该行也含新内容（缓存新鲜→只是没重绘 / 缓存也旧→取数就错了）。测完用同一个 `PUT /line/<idx>` 把原文本写回去。
6. **探针自身的坑**：窗口重建后先前抓到的节点已 detached（click 不触发委托事件、断言恒 false）→ 每次滚动/重建后重新 `querySelectorAll`。多次 console 调用之间页面上下文可能漂到 `about:blank`，所以每条探针都带自检字段，异常值不当结论。

## 6. Hermes 环境下的实测陷阱

- **改代码后要分清热更**：`static/*.js` 立刻生效但要**在模板里 bump `?v=`**（否则浏览器用缓存旧文件）；`templates/*.html` 由 Jinja 热加载；**`main.py` 必须重启工作台**（否则新加的模板变量是 undefined，会静默变成 `1 if x else 0` 的 0 分支）。
- **Hermes browser 的 console eval 有时跑在 `about:blank` 上**：相对 URL 会报 invalid URL、导航无效。做法：eval 里先返回 `location.href` 自检，导航一律用**绝对 URL**，测完再看 `location.search` 确认到底测的是哪一页。
- 远程 chromium 会拒绝超大页（22 MB 章导航后落回 `about:blank`）——窗口化后不再有这个问题，但**历史基线只能用小/中章抓**。
- 服务端行渲染/区间字段口径（`k_mt` px、`k_idx`、`k_dlg`、`_tip`）改动时，AGENTS.md 与方案 §9 要同步。
