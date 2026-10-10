# 作品表格前端 UI 偏好（2026-08-24）

工作台首页作品表格的 UI 交互偏好（用户明确要求）。主体技术（作者分组/元数据/素材统计）见
`references/works-table-author-grouping-20260824.md`；本文档只记 UI 布局与交互偏好。

## 操作列改「手风琴式」
- 默认只显示**单个「更多…」按钮**；点击后在其**正下方垂直展开**按钮组（打开/原站/重新生成/作品术语表/收起），再点「更多…」或「收起」收回。
- 实现：`.op-cell{display:flex;flex-direction:column;align-items:center}`，结构 = 更多…按钮 + `.more-menu`(垂直) + `.primary-row`(阅读/详情 并行一行)。
- `.more-menu{display:flex;flex-direction:column;gap:5px}` 垂直堆叠，`.btn{white-space:nowrap;text-align:center}`。
- **勿**用旧的横向 `.btn-cell`（grid 2 列）布局——那是上一版的多按钮平铺，已被弃。

## 列头名居中
- 子表 `<th>` 全部加 `class="center"`（text-align:center），含 作品/进度/标签/素材/操作。

## 固定列宽 + 可换行
- 列宽：作品 26% / 进度 9% / 标签 22% / 素材 28% / 操作 15%（`.col-*` class）。
- 作品名必须可换行（长日文标题）：`.w-title{white-space:normal}`（**覆盖旧的 `nowrap`**），`.jp{word-break:break-word}`。
- 标签/素材列 `max-width:none`（**覆盖旧 `.w-tags/.w-mats` 的 260px 限制**），否则固定列宽被旧的 max-width 压住不生效。

## 素材列不截断
- 显示**全部**素材人物（移除「+N」截断），靠固定列宽换行铺开——素材量小（3~6 人），用户要看到完整占比。
