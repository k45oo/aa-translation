# 翻译替换弹窗：左右双栏 + 章节勾选层（2026-10-01）

用户要求（原话拆解）：左右结构加宽定高；左 = 历史记录（加搜索）；右上 = 替换对列；右下 = 预览（可选章节、默认全选）；**左右各半**；历史时间显示简略信息；**章节选择在刷新预览之后才出现，只列有替换对的章，无则不显示**。

## 布局

- `.tr-grid` = `grid-template-columns: minmax(0,1fr) minmax(0,1fr)`（用户明确「左右对半开」，勿改成非对称比例）。
- `.modal-box`：`width: min(1400px, 96vw); max-width: none; height: 88vh; max-height: 88vh`，flex 列；`.tr-col`（两栏）与 `.tr-pane`（替换对 / 预览）都 `min-height:0` + 内部 `overflow-y:auto`，谁都不会把对方挤没（根治 2026-09-21 的「预览一多替换对没了」）。
- 窄屏 `@media (max-width:900px)`：叠成一列（历史在上），弹窗 92vh。
- 历史时间**简略**：`trShortTs()` 去掉年份 → `MM-DD HH:mm`（`2026-10-01 11:10` → `10-01 11:10`），次数显示 `×N`；完整时间/次数/上次影响行文件留在行 `title`。
- 历史搜索框 `#tr-hist-q`：匹配 find / repl / ts（子串，大小写不敏感）；**`全选`/`全不选` 只作用于过滤后的可见行**（信息行显示「搜索命中 N 条」）；无匹配时显示「没有匹配「…」的记录（共 N 条）」。搜索输入只重渲染列表，**不作废预览**。

## 章节勾选层（只影响「写哪些章」）

- 预览请求**不带** `chapters`（照顶部「范围」全扫）；响应 `files[]` 里命中的章 → 渲染成勾选条 `#tr-chapters`（**默认全选**，chips 显示 `序号. 章名`，title 带规范文件名）。**0 命中 → 整条 + 工具行一起隐藏**。
- 应用请求带 `chapters=[勾选章]`（规范名 `entry_N_汉化.txt`）。语义 = 范围 **∩** 勾选章。
- 章名显示数据来自服务端 `tr-chapters-data`（阅读器目录里 `has_tr` 的章，`{file,label,num}`，**仅用于显示**）；预览分组标题同样用该名（title 保留文件名）。
- 后端：`_scope_tr_files(cfg, scope, start, chapters)` 先按 scope 切片（from = 当前话及后续话）再对 `chapters` 取交集；`_norm_chapters(raw)` 归一（原文名 / `_汉化.txt.gz` → 规范名，去重排序，进 `_plan_key`）。
- **应用优先复用预览缓存**：`api_replace_apply` 先用 `_plan_key(..., chapters=())`（预览键）取计划，勾选集非空则从计划里筛掉没勾的章；只有没缓存才 `_scan_replace_plan(..., chapters)` 现扫（响应 `source=preview|rescan` 可断言）。否则「预览全库 + 应用带章列表」会白扫一遍全库。
- 前端「已预览」判据 = pairs + scope 与上次预览一致 **且** 勾选章 ⊆ 上次预览命中章 → **取消勾选不算改动作废**，直接应用不弹确认；改 pairs/scope 才 `trInvalidate()`（清预览 + 收起章节条）。
- 取消勾选的章：预览里该组加 `.off`（整体变淡 + 章名划掉 +「已取消勾选 → 不改」），`#tr-ch-sum` 与预览标题「要写入 N 章」同步。

## 坑：本工程没有通用 `.hidden` 规则

`static/style.css` 只有 `.modal.hidden` / `.edit-mask.hidden` / `.src-row.hidden` / `.cand-panel.hidden` 这类**专条**，没有 `.hidden{display:none}`。所以给自带 `display` 的元素（`#tr-chapters{display:flex}`、`.tr-ch-tools{display:inline-flex}`）挂 `class="hidden"` **不生效**，空条会一直占位。修法：显式 `#tr-chapters.hidden, .tr-ch-tools.hidden { display: none !important; }`。
**发现方式值得复用**：光看类名会以为已隐藏，靠 `getComputedStyle(el).display` / `el.offsetParent !== null` 量测才暴露。

## 验收（浏览器实测，2026-10-01 跑过）

1. 打开弹窗：`getComputedStyle('#tr-chapters').display === 'none'`、`.tr-ch-tools` 同为 none；`elementFromPoint` 命中替换对输入框 / 刷新预览 / 应用按钮（无遮挡）。
2. 布局数字：视口 1438×606 时 `.modal-box` 1380×533（=88vh，底 569 < 606 在视口内），两栏各 663px（左右各半），替换对区 125 / 预览区 204。
3. 填一对 → 刷新预览 → 章节条 `display:flex`，chips **只含有命中的章**（实测 3 章扫、2 章命中 → 2 个 chip，均默认勾选），标题「实扫 3 章，命中 2 章，共 8 行受影响（要写入 2 章）」。
4. 取消一个 chip → 该组 `.gfile.off`、sum「已勾 1 / 2 章」、标题「要写入 1 章」、预览没被清空；`全不选` → sum 变红并提示「0 章——应用不会改动任何章」；`全选` 复原。
5. 改「范围」→ 章节条 + 工具行回到 `display:none`、预览回到「尚未预览」。
6. 历史面板（拿 100 条记录的 «この素晴らしいシンオウに祝福を» 验）：meta 形如 `10-01 11:10 ×3`，title 有完整信息；搜 `ザ` → 1 行 + 「搜索命中 1 条」，此时 `全选` 只勾这 1 行；搜无匹配 → 提示文案；清空 → 100 行、勾选状态保留。
7. `browser_console` 查 `js_errors` 应为空（reader 主 IIFE 改动容易留引用错，如删 `void i` 之类的残留变量）。

## 后端 E2E（不碰真实作品）

临时作品结构（`aa/` + `汉化/` + `work/`）直接注入 `registry.WORKS[WID] = cfg`（`main.cfg_of` 读的就是它；`import registry` 是顶层模块，别写成 `from aa_web_app import registry`），再直接调路由函数 `main.api_replace_preview / api_replace_apply`（`ReplacePairsBody`）。断言：
- 预览（`chapters=[]`）→ `picked=0`、`chapters=已汉化章数`、`files` 列命中章；
- 应用 `chapters=[仅第 1 章]` → `source='preview'`（缓存快路径）、`applied=1`、**第 2 章字节完全不变**；
- 应用 `chapters=[不存在的章]` → `source='rescan'`、`applied=0`；
- `_norm_chapters(['entry_1.txt','entry_2_汉化.txt.gz','',None])` → 规范名元组。

**回滚/留档口径（2026-10-01 起）**：不再有 `work/backup/<ts>/` 整文件备份——改动行只进 `work/edits.jsonl`（改前/改后），回滚按记录逐行写回 `before`；测试用临时目录，绝不拿真实作品试写。
