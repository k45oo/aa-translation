# 阅读器行级标注 / 台词纠正（gold_dialogue.json）— 2026-08-29

功能：阅读页右上角置顶悬浮按钮 `✦ 台词纠正`（fixed 定位、切章保持状态 via localStorage），
开启时**屏蔽行内编辑**，点行号列左侧小图标切换该行「是/否台词」（写 `work/gold_dialogue.json`），
台词行正文淡蓝底 + 行号列 ◆/◇ 标记。

## 数据流
- `logic.py`：`gold_path(work_dir, base)` / `load_gold` / `save_gold` / `delete_gold`。
  **文件是 per-chapter 目录** `work/gold_dialogue/<base>.json`（`<base>`=章节去 `.txt`，如 `entry_2641.json`），不是单文件 `gold_dialogue.json`（2026-08-29 实测：`find work -name "gold*"` 返回目录 `gold_dialogue/`，内含每章一个 json）。
  每文件 = `{行号(int→JSON→str): bool}`，稀疏（只存人工纠正过/需覆盖的行）。**gold 只存「与自动判定不同」的行**——全置 true/false 会污染，人工标注完后再回填 auto 判定行会冗余；保持根因逻辑：gold 里有 key 即覆盖自动，无 key 走自动。
- `main.py`：`GET /api/work/{work}/gold/{base}`（读全部）+ `POST`（`{line_no:int, is_dialogue:bool}`）+ `DELETE`。
  reader_page 里每行 `is_dlg = wtype in ('text','mixed') or gold.get(i+1)` —— **gold 人工标注覆盖自动判定**。
- 模板注入：`READER_CFG.gold`（`gold | tojson`），行号 `.dlg-mark` span + 正文 `dlg-line` class。
- reader.js：`goldData = window.READER_CFG.gold || {}`；`toggleGold(line,no)` 翻转 + PUT + 刷新标记/正文；
  `setDlgMode(on)` 切 `body.dlg-on` + localStorage `aa_dlg_mode`；`resetAllMarks()` 遍历重刷。

## 🔥 最大坑：`render()` 重建正文会抹掉服务端渲染的每行 class
reader.js 有一个 `render(mode)` 函数，**每次加载和每次切「汉化/原文」都会重建 `#chapter-pre` 的 innerHTML**：
```js
pre.innerHTML = arr.map(p => {
  const mt = p.mt ? ` style="..."` : '';
  return `<span class="aa-line" data-idx="${p.idx}"${mt}>${p.html}</span>`;
}).join('');
```
它只保留 `html`/`mt`/`idx` 三个字段，**丢弃服务端在 Jinja 里给 `.aa-line` span 加的任何 class（如 `dlg-line`）**。

**症状鉴别（这是本次踩最多的谜题）**：
- `curl` / python `fetch()` 服务器 HTML → 有 N 个 `aa-line dlg-line`（模板+后端正确）
- 浏览器 DOM `querySelectorAll('.aa-line.dlg-line').length` → **0**，但**行号列的 `dlg-mark` 有**（行号列不被 render() 重建，正文被重建）
- `location.reload(true)` 无效、加 no-store/Cache-Control 头无效、reader.js 换 `?v=` 版本号无效、换章节 URL 无效
- 🚫 **这不是浏览器缓存 / bfcache / Jinja2 模板缓存**。服务端模板、后端 `p.dlg` 判定、注入的 `tr-data` JSON（含 `dlg:true`）全都正确。
- ✅ **真正的修复 = 改 reader.js 的 `render()` 重建模板**，让它把 `p.dlg` 拼回 class：
  ```js
  const dlgCls = p.dlg ? ' dlg-line' : '';
  return `<span class="aa-line${dlgCls}" data-idx="${p.idx}"${mt}>${p.html}</span>`;
  ```
  `p.dlg` 来自 `tr-data`/`src-data` JSON（render 的 `arr`），所以后端渲染 rendered 时**必须把 `dlg` 字段写进 `rendered | tojson`**（`rendered[-1]["dlg"]=is_dlg` 回填），否则 `p.dlg` 为 undefined。

**排查铁律**：给正文加「每行 stuff」时（标注/高亮/新 class），一想到要改 Jinja 模板，就必须**同步检查 reader.js 的 `render()` 重建模板**。两者缺一 → 服务端有、浏览器没有。第 1 步先查 `pre.innerHTML = arr.map(...)`，别再浪费轮次在缓存上。

## 🔥 次大坑：注入 JS 的 IIFE 作用域
reader.js 是**单文件多个 IIFE**：主 IIFE（含 `CFG`/`openEdit`/`toggleGold`/`toast`/`closeEdit`/行号点击）/ 候选确认 IIFE（`?cand=1` 才启用）。
- 用 `s.find('})();')` 找「最后一个 IIFE 结尾」**会匹配到候选确认 IIFE 的 `})();`**（如果它排在最后）→ 块被注入候选 IIFE，主页面永不执行（症状：console 无 JS 错误、`window.__aaDlgMode` undefined、按钮绑定不上）。
- **可靠锚点**：主 IIFE 结束在 `/* ================= 候选确认悬浮窗` 注释之前的最后一个 `})();`。主 IIFE 结尾结构固定为：
  ```
      render(mode);
    })();      ← render 的 IIFE
  })();        ← 主 IIFE
  ```
  台词纠块要插在**主 IIFE 闭合 `})();` 之前**（即 `render(mode);\n  })();` 之后），不是之后 —— 否则 `CFG`（主 IIFE 局部变量）在顶层未定义 → `ReferenceError: CFG is not defined` 被吞，功能静默死亡。
- 判定注入块在哪个作用域：插进去后用 `node --check` 过语法不够，**必须确认块首尾的括号**（前面是否冒出一个非预期的 `})();`）。用 `grep -n "render(mode)\|台词纠正模式\|})();"` 看块前/块后各是什么。

## 🔥 第三坑：gold 键类型
服务端 `gold_dialogue.json` 经过 JSON 序列化，键是**字符串** `"38"`。前端 `+b.dataset.line` 得到**数字** `38`。
`goldData[38]`（数字键）永远命中不了 `goldData["38"]`（字符串键）→ toggle 无效、初始标记也可能错。
**统一用 `goldData[String(line)]`**（读和写都要）。

## 🔥 第三坑扩展：toggleGold 双向翻转 bug（自动台词行需点 2 次，2026-08-29 用户实测反馈）
**症状**：`✦台词纠正` 模式下，点自动台词行（`autoDlg=1`、gold 无记录、◆）1 次**不切换**，需点 2 次才变 ◇；再点回也要 2 次。用户明确报「点击是台词需要点 2 次才能变成非台词」。

**根因（即使已用 String(line) 仍会踩）**：`toggleGold` 旧逻辑 `const cur = goldData[line]; const next = cur===true ? false : true`。对**自动台词行**，gold 里**无记录** → `cur=undefined`（不是 `true`）→ `next = (undefined===true)?false:true = true`（永远是 true）→ 无论如何点都写成「是台词」，视觉上 1 次不变、2 次才因写入的 `goldData["38"]=true` 而能翻转。

**修复 = 读「当前实际状态」而非裸 gold 值，用自动判定兜底，再 `next=!cur`**：
```js
async function toggleGold(line, no) {
  const auto = no.dataset.autoDlg === '1';   // 自动判定是否台词（行号按钮 data-autoDlg，=text/mixed 即 1）
  const cur = goldData[String(line)] !== undefined ? goldData[String(line)] : auto;  // gold 优先，否则自动
  const next = !cur;   // 翻转：台词→非台词，非台词→台词
  ...PUT/goldData[String(line)]=next/refreshMark...
}
```
- `refreshMark` 已是「gold 字符串键优先、否则 autoDlg」的正确三分支，toggleGold 的 `cur` 必须与它同口径——**两者任一改，另一处同步**。
- **测试矩阵**：①自动台词行(autoDlg=1,gold无) 点1次→◇ ②再点1次→◆ ③自动非台词行(gold无) 点1次→◆ ④有 gold 记录行按记录翻转。`data-line`/`data-idx` 差 1（`data-line=N`→正文 `data-idx=N-1`），报文验证找行时用 `data-line=241` 对应正文 idx=240，别混。
- 发现类似「点 N 次才对」的 toggle bug，先查**初始状态是否从 auto 兜底**（不只字符串键）。

## 🔥 第四坑：dlg-mark 按钮位置（改成「行号右边 + 绝对定位悬浮」，2026-08-29 用户指正）
初版把 `.dlg-mark`（◆/◇）放在行号**左边**、`body.dlg-on` 时 `display:inline-block` —— 但 `.lc-no` 是 `width:46px; text-align:right` 的窄块，◆ + 行号数字 + `ov-ic`(⚙) 三样挤一行**超宽换行 → 行号数字错位**（用户明确报「是非台词按钮和行号放一起超宽换行导致行号错误」）。
**用户要求的正确形态：按钮放行号右边 + 悬浮显示不改变原布局**。实现二连招：
1. **HTML 顺序**（reader.html 行号按钮模板）：`<span dlg-mark>◆</span>` 从行号前移到行号**右边、`ov-ic` 前**：`{{ l.n }}<span class="dlg-mark...">◆</span><span class="ov-ic">⚙</span>`。
2. **CSS 改绝对定位悬浮**（reader.html 内联 `<style>`）：`.dlg-mark { display:none; position:absolute; left:calc(100% + 2px); top:0; width:14px; ... }`；`body.dlg-on .dlg-mark { display:block; }`。**absolute 脱离文档流 → 不占 `.lc-no` 流式空间**，行号数字永远单行右对齐、不换行错位。
3. **清残留**：删掉旧的 `body.dlg-on .lc-no { padding-left:4px }`（那是给「◆ 在行号左」留空间用的，◆ 移右成 absolute 后此 padding 会把行号整体右移）。
**前提**：`.lc-no` 本身要有 `position:relative`（reader.html 里已有），否则 absolute 子元素相对更外层定位、跑飞。
**验证硬指标**：开模式前后对同一行号 `.lc-no` 比对 `getBoundingClientRect()`（`x/width/right` 应完全一致——证明 ◆ 悬浮不挤布局）；`btnH` 应保持单行高（如 16px）不乱跳。
**初版其它坑**：`.dlg-mark` 一度误设 `display:inline-block` 且留着旧 padding-left → 既是换行源又是行号偏移源，改前先 grep `body.dlg-on .lc-no` 清残留。

## 其它
- 悬浮按钮用 `if (goldBtn) goldBtn.addEventListener(...)` 防 null；`setDlgMode` 里 `closeEdit()`（切模式时关编辑框）。
- 行号点击 handler 是**委托到 `#line-col` 容器**的 `e.target.closest('.lc-no')`；台词模式下 `window.__aaDlgMode` 为真时先 `toggleGold` 并 `return`（屏蔽 `openEdit`），否则走原 `openEdit`。
- 开启时 `closeEdit()` 会关掉编辑框（`edit-row`/`src-row`），这就是「屏蔽编辑」的观感来源——验收看 `#edit-row` 是否存在，**不是 `.ov-menu`**（`.ov-menu` 是候选确认悬浮窗，不是编辑框）。
- 关闭模式后点行号恢复 `openEdit`（`#edit-row` + `src-row` 出现）。
- 台词模式开关用 `localStorage` 持久化 → 重导航/切章自动恢复，满足「切章保持状态」。
- 改 reader.js 后必须 bump reader.html 里的 `reader.js?v=YYYYMMDDx`（本次 20260826f→20260829d→e→…→20260830c）。

## 浏览器实测套路
- browser 工具 `browser_console` 的 evaluate **跨导航不稳定**：有时返回 `chrome-error://chromewebdata/`、有时 `getElementById(...)` 返回 null（实际元素存在）、有时 `document.title` 为空。**每轮验证 = 重新 `browser_navigate` 到同 URL，然后立刻 evaluate**，别跨轮复用 DOM 引用。
- 触发按钮 handler 用 **`el.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window}))`** —— 纯 `.click()` 或 ref-click 对委托到容器的事件可能不命中（target 落在子元素）。dispatch 到 `.lc-no` 冒泡到 `#line-col` 能触发。
- 快照极大（1767 元素）时只读前 200 行看悬浮按钮/行号标记住；正文行属性用 console evaluate 查。

## 第五轮（2026-08-30）：台词纠正改「悬浮正文行加深高亮 + 点击切换」，不点行号

用户反馈需求变化：台词纠正模式下不再以「点行号」为切换手段，改为**鼠标悬浮正文行 → 加深高亮 → 点击该行切换**。这是交互重构，不是修 bug。

**实现（reader.js 事件委托 + reader.html CSS）**：
- **事件绑 `#chapter-pre`（正文容器）而非 `#line-col`**——因为 `render()` 每次重建 innerHTML，绑在正文行的单个节点会被销毁；委托到容器（`preEl.addEventListener('mousemove'/'click'/'mouseleave')`）render 重建后仍有效（同 `#line-col` 委托理由）。
- **mousemove**：`e.target.closest('.aa-line')` → `parseInt(b.dataset.idx)` → 加 `.dlg-hl`；`mouseleave` 清。用 `dlgHoverLine` 变量记录当前 hover 的 idx 去重 + `clearDlgHl()` 只清旧的。
- **click**（`if (!window.__aaDlgMode) return` 守护）：`line = parseInt(b.dataset.idx) + 1`（**data-idx 是 0-based，行号是 1-based**）→ `document.querySelector('.lc-no[data-line=...]')` + `#chapter-pre span.aa-line[data-idx=...]` → 翻转。**cur 判定与 toggleGold 同口径**（gold 字符串键优先、否则 autoDlg 兜底），`next=!cur`，PUT 后端，然后手动 `span.classList.toggle('dlg-line')` + 行号 `.dlg-mark` textContent/className 同步（不依赖 refreshMark，因为这里直接拿到 span/no）。
- **CSS（reader.html 内联 `<style>`）**：`body.dlg-on span.aa-line.dlg-hl { background: rgba(188,214,255,.6); }`（比 `.dlg-line` 的 `.25` 深 2.4 倍）+ `body.dlg-on span.aa-line:not(.dlg-line).dlg-hl { outline: 1px dashed #8a5a3a; }`（非台词行 hover 给虚线提示）+ `body.dlg-on span.aa-line { cursor: pointer; }`。
- **保留两者兼容**：行号点击仍走 toggleGold（两套习惯都能用）；正文点击仅在 `dlg-on` 下拦截（`e.preventDefault(); e.stopPropagation()` 防冒泡到其它）。

**验证（browser_console computed style，比截图可靠）**：
- `span.dispatchEvent(new MouseEvent('mousemove',{bubbles:true}))` → `span.classList.contains('dlg-hl')` 为真。
- 模拟 click → `span.className` 含 `dlg-line`、行号 `.dlg-mark` 文本 `◆/◇` 同步翻转、后端 GET 确认 gold 已写。
- computed style：hover 行 `backgroundColor` = `rgba(188,214,255,0.6)`（加深生效）、`cursor` = `pointer`。**注意**：这些 `dlg-*` 样式在 `body.dlg-on` 下才生效，验证前先把 `body.classList.add('dlg-on')`（或点悬浮按钮），否则 computed style 全是透明/auto。
- ⚠️ **测试污染清理**：先 GET gold 看该行是否本来就在（用户在 gold 里已有记录则保留，blank 行误标要 DELETE 恢复）。行号差 1 极易误用——**测试前先读该行物理内容，确认是台词还是空行/装饰**，别用命令行背一个"行号"就写。
- 截图/browser_vision 常因图片格式 400（模型不支持该截图 MIME）失败——**改用 console evaluate 读 computed style 验证视觉**（背景色/光标/高度），比截图稳。

**坑**：
- ❌ 版本号曾从 `20260830d` **降级**成 `20260830c`（写脚本时字符串拼接错）——浏览器会缓存旧版，**只许递增**。改完必须 `git diff` 或 grep 确认版本号方向是「加大」。
- ❌ `browser_vision` 触发页面跳 `about:blank` 导致 `#chapter-pre` 找不到——报 `no pre` 先看 `location.href` 是否还是目标页，是就重新 navigate。
- reader.html 的 `dlg-*` CSS 在**模板内联 `<style>`** 块里，**不在 style.css**——改样式要 grep reader.html 的 `<style>`，别只搜 style.css。
