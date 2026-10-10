# 术语表页按作品管理角色（2026-08-26 定型）

需求：术语表页隐藏全局「角色」表和「known_names 白名单」表，作品表格每行加「角色」按钮，点击弹窗显示该作品角色并支持增删改。

## 实现结构
- glossary.html：作品表加「角色」按钮列（`data-roles="{{ w.id }}"`）；全局角色/白名单表包进 `<div class="hidden">`（数据仍在 `GLOSSARY_DATA`，服务端校验/保存逻辑不变）；新增 `#roles-modal`（复用 `.modal-box` 样式）
- glossary.js：`rm = {work, rows}` 弹窗状态；`rmRender()` 渲染 `tbl-modal-roles`；打开时 `state.roles.filter(r => (r['作品']||[]).includes(work))` 收集该作品角色；增删改行内 input 事件直写 `rm.rows`；保存按作品 id 整体替换回 `state.roles` 后 PUT `/api/glossary`（带当前 glossary/known_names 整包）
- 新增角色行 `作品` 字段预填当前作品 id（`['saki']`）

## 保存合并逻辑（关键，别用 _idx）
```js
// ✓ 正确：按作品 id 整体替换（旧条目全移除，弹窗内容全量写回）
state.roles = state.roles.filter(r => !(r['作品'] || []).includes(rm.work));
const cleaned = rm.rows.map(r => ({日文,中文,作品,分类,备注}));
cleaned.forEach(c => { if (!c['日文'] && !c['中文']) return; state.roles.push(c); });
```
**✗ 错误做法（本次踩坑）**：按 `_idx` 收集原索引再 filter 移除。新增角色 `_idx:-1` 无索引标记——第一次保存入库后，第二次打开弹窗它带着真实索引（但 `_idx` 已丢弃），删除它时 `_idx>=0` 集合里根本没有它 → 删除不生效，测试角色残留在磁盘（表现为「保存成功 708 但数据没变」）。**根因：`_idx` 是会话内临时索引，跨保存不持久，不能用于跨次合并。**

## 整包 PUT 校验陷阱（服务端行为，前端要提前防）
`/api/glossary` PUT 走 `validate_glossary`：**roles/glossary/known_names 三块一起校验，任一块有重复就整包 422 拒绝**（`HTTPException(422, {errors})`）。因此：
- glossary.json 里**任何历史遗留重复项**（known_names 重复、角色日文重复）都会让所有保存失败——不只是编辑的那块。本次实测：known_names `マヤノトップガン`×2 + 角色 `ホシノ`×2（其中一条是 other 作品「多出处测试临时条目」残留）→ 弹窗保存报「保存失败: 已知重复项…」且按钮卡死
- 前端失败路径必须 `btn.disabled = false` 恢复按钮 + 显示 `j.detail.errors.join('；')`（不要只 JSON.stringify 整个响应）
- 上线前先全量扫重复：`Counter` 查 known_names 与角色日文，清了才能存

## 清理 glossary.json 必须保格式（本次血的教训）
- 原文件是 **pretty-printed 多行（2 空格缩进 + CRLF）**，7926 行。用 `json.dump(separators=(',',':'))` 紧凑写会压成 1 行 → git diff 显示 7927 行删除（内容没丢但 diff 爆炸，且可能被误判为破坏）
- 正确清理姿势：`git checkout -- glossary.json` 恢复原版 → 脚本 `json.loads` → 清理（去重/删条目）→ `json.dump(d, ensure_ascii=False, indent=2)` + `text.replace('\n','\r\n')` + `open(p,'w',newline='')` 写回 → diff 只剩真实内容变化（本次 3 行）
- 备份在 `/tmp/glossary_bak.json`（git-bash 的 /tmp 是临时目录，别依赖它跨会话）

## 前端静态资源缓存坑
- `glossary.js` 引用**没有 `?v=` 版本号** → 浏览器缓存旧版 JS，改完代码刷新页面不生效（症状：新功能 DOM 在但行为是旧的）。模板里 `<script src="/static/glossary.js?v=20260826c">` 必须带版本，改 JS 后 bump
- CSS 版本号在 base.html `<link ... style.css?v=...>`，bump 规则同

## 浏览器实测清单（全链路）
弹窗打开显示 209 角色 → 编辑字段（input 事件更新 state）→ 新增（作品预填）→ 删除 → 保存 → 磁盘验证（角色总数变化/测试角色存在与否）→ 再删除测试角色并保存恢复原状 → 确认无污染（真实数据如宮永咲完好）。测完必须清理测试数据（本次用「新增→保存→删除→保存」闭环，最后恢复 707 条）。
