# 作品列表页改版（2026-09-03 实施，doc/TODO/作品列表页改版设计_2026-09-03.md v2）

作品列表首页 `/` 大改：搜索框/7 列/评分/阅读状态/删除两步确认。设计 v2 已并入代码核对修正，本节记实现要点与坑。

## 新增纯函数（logic.py，全部压成纯函数可单测）
- `read_state_with_auto(cur, mode)`：进度 PUT 自动置「阅读中」状态机——`mode in ('tr','refine') and cur != done` → `reading`，否则保持 cur（done 不降级、src 不触发）。
- `delete_surface(cfg, all_work_dirs=None)`：删除面纯计算 → `{root, dirs, shared, items}`。**共享根退化**：`root=commonpath(cfg 四目录)`；`all_work_dirs` 里若有**其它作品**的目录落在 root 下 → `shared=True` → items=逐 cfg 目录；否则独占 → items=[root] 整删。**判单独占必须传全作品目录集合**（saki 删除后 saki_fc2 独占 `さーきー/`，不传会误 shared；反之 saki/saki_fc2 同时在 registry 时须 shared）。
- `delete_work(surface)`：rmtree surface['items']，返回删掉路径列表。`remove_paths(paths)`：删交付 HTML 等文件。
- `avg_rating(ratings)`：1-5 的算术平均一位小数，无评分/全非法 → None。`last_update_of(aa_dir, out_dir)`：两目录**顶层文件** max mtime（勿递归——递归 71 部 5.7s，顶层 4.5s 仍需缓存）。

## 后端路由新增（main.py，CRLF 用 python 脚本改）
- `GET/PUT /api/read-state`（全量/单作品 `{state}`，state='' 删键）；`aa_web_app/data/read_state.json` 运行时状态 **已入 .gitignore**（自动写入频繁，与手写 work_meta 分离）。
- `GET /api/work/{id}/delete-preview` + `POST /api/work/{id}/delete`——执行前查 `_latest_task_for_work` 非终态 → 409；执行=磁盘 rmtree → `_remove_registry_entry` → work_meta 删键 → `api_reload()`。
- `_remove_registry_entry(work_id)`：registry.py **只有 append 没有 remove**，自写行级：`marker=f'    "{id}": dict('` 找 `lstart=rfind('\n')+1`，向后找条目闭合 `    ),\r\n`（优先 CRLF 次 LF），切片删除；CRLF 用 `open(rp,'w',newline='')` 写。**registry key ≠ 目录名**（40+/71 案例）——删除基准全部用 cfg 路径。
- `GET /api/search-chapters?q=`（章节名后端）迭代全部 work_stats 的 chapters title 匹配（慢，~数秒——设计定调「章节名过滤降级走后端」，前端显示「搜索中…」）；`GET /api/search-local?scope=&lang=`（正文/全作品 search_work，更慢）。
- `PUT /api/work-meta/{id}` 扩展接受 `rating`/`review`（WorkMeta 模型加字段，白名单校验 1-5 或空）；`PUT /api/aa/{work}/progress` 加 `mode` 字段 → `read_state_with_auto`。
- index() 视图重构：每作品加 `rating/review/last_update/read_state`；**排序改真实 last_update 倒序**（弃原 `reversed(works)` 位置法）；作者组加 `tags_agg`（组内去重）+ `avg_rating`。`_LASTUPD_CACHE` TTL 缓存（`invalidate_stats`/`api_reload` 双清）。

## 前端（index.html LF + style.css + reader.js，patch 可改）
- 搜索框行：`#search-bar` + 范围复选（标题/章节名/正文）+ 语言 select；标题=前端 `data-title` 即时过滤；章节名/正文=防抖调后端。**列结构 colspan 5→7**（作品/进度/评分/标签/素材/最后更新/操作）。
- **`.flt` 多选筛选必须用 `none` 哨兵值匹配未标记行**：`rowOk` 多值分支 `(row.dataset[key]||'').split(SEP).filter(Boolean)`——空值被 `.filter(Boolean)` 吃掉，勾「未标记」`value=""` 恒不命中 → 未标记行 `data-rs="none"`、选项 `value="none"`（评分同理 `data-rating="none"`）。
- 进度第 2 行 = 阅读状态徽章 `.rs-badge`（toread/reading/done/—），点击弹 `.rs-menu` 小菜单（待读/读完/清除）→ `PUT /api/read-state`。
- 详情弹窗 `buildDetail` 加 `#dm-rating` 星级 + `#dm-review`；**dm-save 取评分须取「最后一个未 off」的星**（`_litStars[_litStars.length-1]`）——用 `querySelector('.star:not(.off)')` 会取**第一个**（永远 = 最低星，点击 4 星却存 rating=1，踩坑）。
- 删除两步确认 `#del-modal`：`POST /api/work/{id}/delete-preview` 列清单 → 第二步 `POST /api/work/{id}/delete`（confirm:true）。
- reader.js `saveAAResume` PUT 带 `mode`（`pre.dataset.mode` 实际渲染模式，非 has_tr 推断）。静态资源改动都要 bump `?v=`（`style.css` base.html / `reader.js` reader.html 两处）。

## 测试（test_aaweb.py LF，patch 可改，+10 用例）
`TestReadState`（tr→reading / done 不降级 / src 不触发）、`TestDeleteSurface`（独占根 / shared 根退化只删 *_fc2 / 全目录默认独占）、`TestRating`、`TestLastUpdate`。**注意 Fixture.setUp 已建 `self.tmp/aa` `self.tmp/tr`**——测试里的临时子目录勿重名 `aa`/`tr`（makedirs FileExistsError，踩过）。

## ⚠️ 数据安全坑（本会话踩爆）
**测试详情弹窗 save 在真实作品上会把 work_meta 字段清空**：dm-save PUT 整条 work_meta（author/cn_name/tags/materials 全部），fetch stub 装 `ok` 触发 `location.reload()` → 空表单覆盖真实作品（baitou 的 author/cn/tags/materials 全被清成空串/[]）。**教训**：① 测详情弹窗 save 用 fetch stub 拦截**不真正落盘**，返回 `{ok:false}` 不触发 reload，或指向假作品；② 若真弄脏 `work_meta.json`（用户手动数据，勿 git checkout 还原会丢手写），**从 git HEAD 取原值 json 合并恢复**：`git show HEAD:...work_meta.json > tmp` → json 读回 → 逐 key 核对差异只还原被清空字段，勿整文件覆盖（保留其它条目手写改动）。验证差异用 python json load 逐值比对（勿信 git diff 行数——json.dump 重排会让 997 行全变伪 diff）。

## 搜索二次迭代（2026-09-03 下午，用户四项反馈）

### ④ FileNotFoundError 崩溃（无汉化目录作品）
- 现象：`/api/search-local` 500 `FileNotFoundError: ...aaManga\时间停止探索者亚鲁欧\汉化`——jikan_yaluo 等**未翻译作品 out_dir 指向不存在路径**（字符串真值判断 `and out_dir` 过不了——路径存在与否要 `os.path.isdir`）。
- 根因链：`api_search_local` → `logic.search_work` → `chapter_order` **裸 `os.listdir(out_dir)` 无存在性检查** → FileNotFoundError 冒泡。
- 修：① `logic.search_work` 开头 `if not out_dir or not os.path.isdir(out_dir): return []`；② `api_search_local` 逐作品 try/except（单作品损坏不中断整体）；③ `api_search_chapters` 同样逐作品容错。修复后 content 搜索扫完 76 部返回（不再 500）。

### ② 正则搜索选项
- 搜索框加「正则」checkbox（`#sbox-regex`）→ 后端 `search-local`/`search-chapters` 加 `regex=1` 参数（`re.search(re.compile(q))`）；标题/章节名/正文都支持；**非法正则 try/except → 返回 `{error: "正则错误: ..."}`**（前端 ✗ 红显）而非 500。
- **正则模式标题也走后端**：前端 `data-title` `.includes()` 不支持正则 → `regexOn && scopes.has('title')` 时并入 search-local 的 title scope（`needBackend` 判定要含它），非正则才 `filterRows` 前端过滤。

### ③ 搜索进度三态显示（能看出是否完成）
- `#sbox-status` span 三态 class：**⏳ 搜索中 Ns**（`sbox-run` 蓝，`setInterval` 500ms 刷秒数——content 搜索 8-33s，静态「搜索中…」像卡死）→ **✓ 命中 N 部（M 部 · Ns）**（`sbox-ok` 绿，M=scanned 或 `{{ works|length }}` 兜底）→ **✗ 错误**（`sbox-err` 红）。
- **`backendTok` 作废机制**：每次 input/change `backendTok++`，doBackend 取 `token=++backendTok`，`end()` 里 `token !== backendTok` 即丢弃（防旧慢响应覆盖新输入状态，曾卡「搜索中」不消失）。

### 🚫 `.sbox-scope` selector 吃进正则 checkbox（bug）
正则 checkbox 若也标 `class="sbox-scope"`，`scopeSet()`（`querySelectorAll('#search-bar .sbox-scope input')`）会把它当 scope 读 `value="on"` → 污染 scopes 集合 + 测试里 `c.value!=='title' && c.checked` 误开关。**正则 checkbox 用独立 class `sbox-opt`**（CSS 同款即可），scope 复选只认 `sbox-scope`。

### 正文搜索慢（8-33s 实测）是设计内预期
content scope = 全作品 search_work 读文件，76 部 8-33s（进程重启后磁盘缓存热则快）。§6 明确不做缓存/并发，前端靠秒数跳动表活。勿试图加并发（GIL 下磁盘 IO 串行最快）。

### 🐛 命中数与显示数不符（用户报「命中16部只显示1部」）——两层 bug
- **层 1（显示只 1 部）**：doBackend 先循环强制 `display=''` 后端命中行，随后 `filterRows(q)` 又用前端标题 `.includes(q)` 重滤——把「章节/正文命中但标题不含 q」的行全藏回，只剩标题含 q 的。修：`filterRows(titleQ, extraHide, keepWork)` 加第三参——`keepWork.has(row.dataset.work)` 时强制 show（**后端命中集豁免标题过滤**），doBackend 调 `filterRows(q, null, hitWorks)`；语言过滤对命中行同样豁免（`!hitWorks.has(wk) && !applyLang(row)` 才隐藏）。
- **层 2（报 16 实际 15）**：计数 `contentData.length + chapterWorks.size` 把**正文+章节双命中**的重叠作品重复计（「彼は_響_になってしまったようです」两路都中）。修：`const n = hitWorks.size`（并集）。
- 教训：正文/章节两路后端命中的并集是唯一权威——显示、计数、过滤豁免三处必须共用同一个 `hitWorks` Set，任何一处单用一路都会不一致。

### 搜索行布局（2026-09-03 用户三轮迭代定稿，以末轮为准）
顺序=**状态（最左）→ 输入框 → 正则 → 范围(标题/章节名/正文) → 语言 → 「搜索」按钮（最右）**；`#sbox-status` 放 `.search-bar` 第一个子元素；正则 checkbox 在「范围」标签**前**。轮次演进：① 初版自动防抖 → ② 状态左移/正则前置/行宽减半 `min(50%,900px)` → ③ **末轮加宽 `min(90%,1400px)` + 加「搜索」按钮手动触发**（用户嫌自动全扫卡顿；input/change 只留标题前端即时过滤，`runSearch()` 由按钮 click / Enter 触发 doBackend）+ **状态区固定 `min-width:210px`**（初始「就绪」，搜索中/完成文字变化不跳动行宽）+ **`.btn` padding 7px 14px**（原 16px）。验证：输入后等 1s 状态仍 idle 不自动搜 → 点按钮立即 `sbox-run`；搜索全程行宽不变（210px 占位）。
- **🚫 完全手动定稿（用户尾随纠正）**：初版只对正则禁自动、非正则保留标题即时过滤 → 用户反馈「不选正则时还是实时搜索，只有选了正则才不实时」。终版 = **任何 input/change 都零过滤零搜索**（清 `.sr-hit` + `filterRows('')` 恢复全量 + 状态「就绪」），只有按钮/Enter 的 `runSearch()` 才过滤+后端。验证：不勾正则输入「響」等 1.5s 列表全量 76 不动。

## 删除后首页卡刷新（2026-09-03 用户报，复现确认）
**症状**：删除作品后首页 reload 卡数秒~数十秒，/glossary /tasks 其它页正常（不调 work_stats）。
**根因**：删除 API 末尾 `api_reload()` 无条件 `_STATS_CACHE.clear()`（全作品缓存失效）+ 启动 `_warm_all_stats` 后台全量冷扫 ~25s——删除响应后前端 `location.reload()` 立即打首页，与预热线程抢磁盘 IO/GIL → index() 逐部重算被拖垮。
**修复**：`api_reload(warm=True, clear_cache=True)` 加两参数，删除路径 `api_reload(warm=False, clear_cache=False)`（只 pop 被删作品缓存不预热不清全局）；`_warm_all_stats` 加 `_WARM_LOCK` 单飞锁。实测删除后立即首页 reload 1.02s。
**⚠️ 验证删除全链路勿删真作品**：registry.py 先复制备份（**当前状态**，勿 git checkout 恢复——平行会话/用户可能已删真条目如恋愛騒動七番勝負 19 行 diff，checkout 会还原它）→ 文本追加假条目 zzz → 建小目录 → 删除 API → 计时首页 → 备份拷回还原。测试前的全量 `/api/reload` 会启动预热线程干扰，须先等预热完成（轮询首页 <1.5s）再测删除，勿把预热残留误判成删除卡顿。

## 页面统一左右留空（2026-09-03 用户两次微调：20px→30px）
- **用户要求是「作品列表页整个内容区」（h1/操作按钮/搜索框/筛选条/表格）统一左右留空，不是只表格留空**——逐元素加 margin 会不齐。
- 方案：`.container.wide.idx-wide { padding: 0 30px 80px; }`（新专属类，index.html `container_class` 改 `wide idx-wide`）；**表格自身 margin 归零**（`margin:0; width:100%`）——容器 padding + 表格 margin 会叠加成 60px。
- **🚫 勿改 `.container.wide` 全局**：reader/board/manga 共用它且用户硬要求全宽贴边（阅读页 left=0 实测）——只加 `idx-wide` 组合类，index.html 专属。
- 改后浏览器实测：h1/搜索框/筛选条/表格 `getBoundingClientRect().left` 全等于 30；reader 页 `padLeft=0px` 不受影响。数值调整（20↔30）只改这一处 padding + bump `?v=`。

## 实测验证套路
- 直调 API：`PUT /api/read-state/<id>`、`PUT /api/aa/<id>/progress {mode:'tr'}` 验状态机；删除 preview 是 **GET**（POST 会 405）；search-chapters 慢（数秒）给足超时。
- 首页冷启动 ~25s（`_warm_all_stats` + last_update 冷扫首部），热 0.59s——验证首屏勿当卡死。
- 浏览器 console：详情弹窗星级点击后 dm-save stub 抓 PUT body 看 `rating` 是否取最高星；`.rs-cell` dispatchEvent click 验菜单；搜索框 `box.value=...; dispatchEvent(new Event('input'))` 验过滤/后端。
