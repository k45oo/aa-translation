# 工作台候选确认功能实施记录（2026-08-28）

方案文档：`doc/工作台候选确认功能方案_2026-08-28.md`（grilling 6 决策 + 审批 4 条，已实施）。
候选数据：`work/glossary_candidates.json`（schema 术语表候选 v1：ai_候选 数组，每项 term/translation/type/作品/确认/来源/别名_of/note）。
提交：25df76c（功能）、bbfa8a3（DOM顺序+原文高亮实测修复）、5a17de7（三问题修复：多出处/高亮持久/索引还原）。

## 数据流（不变）
`gen_glossary_ai --work <id> --full` 生成候选 → **人工确认**（原手编 JSON，现在工作台悬浮窗）→ `gen_glossary_ai --work <id> --promote` 落地知识库/全局 → 从 ai_候选 移除已确认项。

## API（main.py，CRLF 用 python 脚本改）
- `GET /api/work/{work}/candidates` → 未确认候选，排序 type（术语0/专名1/拟声梗2/角色3）+ 来源章节数（≤3）降序；已确认项排除
- `POST /api/work/{work}/candidates/{term}/confirm` body `{translation?, 作品?, 别名_of?}` → 设 确认:true + 更新字段（term 用 URL unquote）
- `POST /api/work/{work}/candidates/{term}/delete` → 永久移除
- `POST /api/work/{work}/candidates/reset-skipped` → 无状态（前端 sessionStorage 管跳过）
- reader_page `?cand=1`：注入 `cand_data`（候选列表 + 各候选 `命中` = {来源章: [命中行号前20]}，后端遍历来源章原文找 term）+ **`init_idx`（从 `?ci=` 解析，跳章还原候选进度）**
- work 页：注入 `cand_n`（未确认数），模板 `{% if cand_n > 0 %}` 显示「候选确认 (N)」按钮
- term 定位：API 用 `unquote(term)` 比较（不用数组索引，删除会重排）

## 前端
- work.html：`<a class="btn highlight" href="/work/{{ work }}/read?cand=1">候选确认 ({{ cand_n }})</a>`
- reader.html：`<script>window.CAND_DATA = {{ cand_data | tojson }}</script>` + `<div id="cand-panel">`（悬浮窗容器：head 可拖动/body/actions 按钮 上一跳过删除确认下一）+ `<script src="/static/reader.js?v=...">`
  - **顺序铁律**：cand-panel div 必须在 reader.js `<script src>` **之前**（reader.js 同步无 defer，执行时 getElementById null → IIFE 静默 return 不显示）
- reader.js：候选 IIFE（独立于主 IIFE，文件尾）——可拖动、show() 渲染当前候选、确认/跳过/删除/下一条（fetch API）、跳过存 sessionStorage `cand-skip-<work>`；**不自动 location 跳转**（见下方三问题修复）
- style.css：`.cand-panel`（fixed right 24 top 80 w340 z-index 800）、`.cand-highlight`（#fff176 + dashed outline）、`.cand-src-link`（出处链接）、`.btn.highlight/.danger/.primary`、`.toast.bad`

## 三问题修复（5a17de7，2026-08-28 用户回测提出）
1. **多出处只显示 1 个**：候选 `来源` 多章只跳第一个。修复＝悬浮窗 `出处(N)` 列出**全部来源章为可点击链接**，当前章无词标"（当前章无此词）"提示去点别的出处。
2. **切换汉化/原文后高亮丢失**：主 IIFE `render()` 重建 innerHTML（重建会清 `.cand-highlight`）。修复＝主 IIFE render 末尾调 `window.__candReapplyHighlight()`（候选 IIFE 注册，按当前候选 term 命中行 idx 重应用高亮）。term 只在原文（汉化已译成中文）时高亮查 `src-data` 原文 JSON，命中后自动切「原文」模式（`seg-btn[data-mode="src"]` click）再按 `data-idx` 高亮。
3. **点下一/跳章重置候选（最严重，表现为"先跳最新章节后跳回候选1"）**：根因是 `show()` 里 `jumpTo` 用 `location.href` 全页跳转，重载后 `list/idx` 全重置（idx=0）。修复＝**show() 不再自动跳转**；跳章由用户点出处链接触发，跳转 URL 带 `&ci=<候选idx>`，经服务端 `cand_data.init_idx` 还原（reader.js `let idx = Math.min(CAND.init_idx||0, list.length-1)`）。

## 审批 4 条落实
1. **--promote 清理待确认区**：`_promote_confirmed` 落地后按 term+type 分流从 `待确认.术语/人物` 弹出对应项，空则删整个 `待确认` 键（gen_glossary_ai.py）
2. **/glossary 入口砍掉**：全局页无作品上下文，只留作品页
3. **排序如实**：来源章节数 ≤3 三档降序（files[:3] 截断）
4. **别名_of 展示**：悬浮窗有 `别名_of` 字段时显示可编辑输入框，确认即触发全局别名归并

## 测试
`scripts/tests/test_candidates.py` 5 用例：列表排序排除已确认 / confirm 设标志+更新 / delete 移除 / 未知 term 报错 / promote 清理待确认区（mock `work_memory.load_knowledge/save_knowledge`，gen_glossary_ai 内部用 `WM.load_knowledge` 非模块属性）。全量 195 全绿（190 基线 + 5）。

## 浏览器实测套路（每改必做）
- 入口按钮 → 点开 `?cand=1` → 悬浮窗显示 → 候选1 出处跳章 → 高亮 → 确认/跳过 → 下一条进度不重置
- 用 `browser_console` 查：`panel.classList.contains('hidden')`（悬浮窗是否显示）、`document.getElementById('cand-progress').textContent`（进度）、`document.querySelectorAll('.cand-highlight').length`（高亮数）、`location.href`（是否带 `&ci=` 还原）
- **测试完必须重置候选数据**（`确认:false` 全置回），避免污染真实待确认清单
