# AA 汉化矫正工作台（aa_web_app）

本地单用户 FastAPI Web 应用：浏览/矫正已汉化 AA 漫画、管理术语表/R18素材、重生成合集、翻译控制台。运行于 http://127.0.0.1:8765（仅本机，**无鉴权**）。

## 启动（唯一允许方式）
- **只用仓库根 `一键重启工作台.bat`**：杀 8765 旧进程 + 启动 + 自动开浏览器。**端口可配**（2026-10-08）：`AA_PORT`（真实 env 或 `<仓库根>\.env`）> 8765，bat 与 `aa_web.py` 都读它（bat 用 `for /f` 解析 .env；改端口后 bat 才杀得对进程）。
- **运行日志落盘 `aa_web_app/workbench.log`**（aa_web.py 重定向 stdout/stderr，追加、>5MB 滚 .1）——崩溃/500 排查先看它（bat 窗口关闭后唯一证据；2026-09-08 起）。
  触发须**彻底 detach**（否则 terminal 卡死）：`subprocess.Popen(['cmd','/c','start','','一键重启工作台.bat'], cwd=仓库根, creationflags=0x08000000|0x200, stdout/stderr/stdin=DEVNULL, close_fds=True)`，触发后**绝不 sleep/验证**。**禁用 DETACHED_PROCESS(0x8)**——bat 无控制台时每条控制台子命令各开一个新窗口（闪烁）；详见根 AGENTS.md「工作台重启(最终规则)」
- **严禁** Hermes 内部终端杀 python 进程（杀 TUI 自身）、**严禁** .venv python 后台启动。
- bat 是 GBK 编码；反斜杠路径用 raw string（`\a`→BEL 坑，fact91）。
- 底层入口：`python scripts/tools/aa_web.py`（uvicorn，端口见上；.venv 依赖 fastapi/uvicorn/jinja2/pydantic）。

## 代码结构
- `main.py` FastAPI 路由；`logic.py` 纯逻辑（无 I/O 副作用，可单测）；`settings.py`（设置页纯逻辑，不 import fastapi，可单测）；`selection.py`（划词+朗读）；`task_manager.py`（任务管理器）；`work_pack.py`（导出/导入）；`add_work.py`（添加作品/更新检查）；`estimate.py`（消耗预估）；`reader_win.py`（窗口化渲染）
- `templates/` + `static/`（jinja2 模板 + 原生 JS：app.js / reader.js / glossary.js）
- `data/work_meta.json` — **用户手动编辑**的作品详情（tags/status/author/materials/r18_level），**绝不 git checkout 还原**（丢手写数据，fact86）
- 根 `glossary.json`（全局术语表：作品/角色/替换对/known_names）与 `roles.json`（全局角色表）—— logic 经**数据根** `_DATA` 读取
- **代码根 / 数据根分离（2026-10-09，方案 doc/方案归档/发行包打包方案_2026-10-09.md §5）**：可写数据（上面两个 JSON、`data/*`（work_meta / read_state / tasks / tts_cache / selection_log）、`揭示板/`）一律走 `_DATA = paths.BASE`（`AA_BASE` env > `<仓库根>/.env` 的 `AA_BASE` > 代码根）；代码 / 模板 / 静态资源走 `_PKG` / `_ROOT`。**冻结 exe 里代码在 PyInstaller 解包目录、数据根 = exe 所在目录**（入口 `scripts/tools/aa_web_exe.py`，打包 `scripts/tools/build_workbench_exe.py`）。`AA_BASE` 未设置时路径与从前**逐字节相同**（单测守护）

## 主要页面
- `/` 作品列表（素材列、R18 徽章、更多操作：重生成/统计素材/术语表）
- `/work/<id>` 章节目录 · `/work/<id>/read` 阅读器（逐行矫正弹窗、滚动保存进度）
  - **「可反哺全局的条目」区块（2026-09-20，紧随「作品术语表」下方）**：按钮「检查可反哺项」→ 点击才扫描（零 token）；表格列 `勾选｜日文｜本作品译名｜类型｜跨作品 N 部｜嵌入率`，按「可反哺 → 跨作品次数降序 → 长度」排；顶部三按钮 `全选跨作品项`（仅跨作品≥2 的可反哺项）/ `全不选` / `反哺选中项…`（confirm 里报将写入的三个目标与条数）。**默认勾选 = 跨作品≥2 且非短词**；短词(≤3 字)默认不勾并标「短词·需人工判断」+ 显示嵌入率（≥50% 标红）；`跳过` 行灰显禁用 checkbox 并给原因。逻辑全在 `scripts/common/glossary_repatriate.py`。
- `/work/<id>/translate` **翻译控制台**（POST 经统一任务管理器 enqueue，日志轮询 + 取消；勾选章节）
- `/tasks` **统一任务管理器**（2026-08-29）：翻译/下载任务集中看板——阶段/章节进度/日志尾/token 消耗/排队；两档优雅停止（译完本章后停=章节边界 / 尽快停=块边界，ckpt 可续跑）+ 强制终止兜底。启动仍在各作品控制台
- `/glossary` 术语表（全局+作品级）· `/board` 揭示板 · `/manga` 图片漫画
- `/settings` **设置页（2026-10-08，方案 doc/方案归档/工作台设置页与配置文件方案_2026-10-08.md）**：四区块各自保存，**只写 `<仓库根>\.env`**（唯一用户可改层；优先级 `真实环境变量 > .env > TOML > 代码默认`）——① **密钥**：清单自动读 TOML（`[backend.*].key_env` ∪ `[vision].models_*.key_env`，链上端点排前 + 用途/来源标注），**只回掩码**（尾 4 位）、留空 = 不改（另有显式「清除」）、每行「测试连接」（用该 key 发一次最小请求，回 可用 / 401 / 402 / 超时）；② **模型**：翻译链 + 校对链按**链条目 id**（`backend|model`）勾选（同端点不同模型可分别选；不勾 = 整条链；勾选集 == 默认集则不写 env，避免把 `fallback_only` 兜底端点摘掉）；③ **划词**：`AA_SEL_ENABLE` 开关（关 = 前端不挂监听 + 后端 403）+ `AA_SEL_CHAIN`；④ **代理**：`AA_PROXY`/`AA_USE_PROXY`（auto/0/1）+「测试代理」（端口可连？/ 直连与经代理能否到付费端点）。**保存即生效**：原子写 .env（行级替换、保留注释与未改动行）→ `load_dotenv(force=True)` → `aa_config.reload()`；**已在跑的任务不受影响**（子进程 env 已定）。后端逻辑全在 `settings.py`（不 import fastapi，可单测）。**改路由/模板要重启工作台**（Jinja 热加载模板，但 `main.py` 路由是 Python）
- `/` 顶部「⤓ 导入作品包」（.rar）、作品行操作「更多」菜单里的「导出」· `/work/<id>` 动作条「导出作品」（见「作品导出 / 导入」节）
- **章节分页（2026-09-13）**：作品页章节表 / 翻译控制台 / 校对控制台 / 阅读器目录统一走 `static/app.js` 的 `aaPager({rows, controls, key, activeOf})`——每页 10/50/100（localStorage 按 key 记，如 `aa-page-size:translate:<work>`），只改 `display` 不动 DOM，所以**勾选状态跨页保留、「全选/全不选」覆盖全部章节**；阅读器目录额外传 `activeOf: el => el.classList.contains('active')`，打开任意章自动跳到该章所在页；行数 ≤10 时不显示分页条。**阅读器目录分页条样式（2026-09-13）**：`aaPager` 支持 `symbols: true`（翻页按钮只显示 `‹` `›`，文字进 `title`）与 `sizeLast: true`（「每页 N 章」排到页数之后）；只有 `reader.html` 传这两个参数，配合 `style.css` 的 `.reader-nav .pager-row`（`flex-direction: column` → 页数行在上、每页选择在下；按钮 22×22 inline-flex 居中；深色 `#3f3f55/#55557a/#cdd6ff` 贴侧栏）→ 其余页仍是「‹ 上一页 / 下一页 ›」文字按钮 + 每页选择在前
**本页按钮 + 仅未译筛选（2026-09-13 / 2026-09-16）**：翻译控制台的「本页：未译全选 / 已译全选 / 全不选」与「仅未译」复选框**都挂在换页行里**（`pagerRowCtrls()` 在 `aaPager` 之后 append 到 `#chapters-pager`，顶部 actions 行只留跨页的 全选未译/全选/全不选）——`aaPager` 返回 `visible()`（当前页可见行），`pageChks()` 只取这些行，逐行状态读 `<tr data-tr="0|1">`（未译/已译），与跨页按钮互不影响。**校对控制台**仍只有「已译全选 / 全不选」（它只列已译章）。
- **「仅未译」筛选**：勾选后只把未译章纳入分页（`curRows`），**被筛掉的行必须自己显式 `display:none`**——aaPager 只管传给它的那批行（`buildPager()` 里先 `allRows.forEach(r => r.style.display = curRows.indexOf(r) >= 0 ? '' : 'none')` 再建 pager，否则已译行留在页面上、页码却是筛选后的行数）；筛选变化 → 重建 pager（同一个 `key` 保留每页大小），已勾选状态不受影响（勾选跟行绑定）。筛选状态只在本页内存（刷新回默认）。
- **翻译控制台章节勾选默认全不选**（2026-09-16 用户要求）：模板 checkbox 不带 `checked`，要选走「全选未译 / 本页未译全选 / 手动勾」。

## 核心 API（/api/...）
- `work-meta/<id>` GET/PUT + `POST .../mat-scan`（统计素材写回 materials；**2026-08-28 知识库优先**：读 `work/<work>_knowledge.json` 人物生成，无人物回退全局角色表）
- `work/<id>/chapter/<f>/line/<idx>` GET/PUT（行矫正，PUT 走 save_line：宽度==SRC 硬约束 + 逐行记 `work/edits.jsonl`（**2026-10-01 起不再整文件备份**）；GET 回带该行 `sfx` 标记，PUT 可带 `sfx`/`sfx_clear`/`sfx_clear_all` → 见「划词翻译」节 sfx 样式替换）
- `regenerate` POST（重生成合集）· `apply-glossary`/`glossary-preview`
- `GET/PUT /api/glossary/<work>` = 作品术语表（`work/glossary_<work>.json`）。**PUT 默认 `mode=merge` 增量保存（2026-09-19，用户要求）**：只 upsert 提交的条目 + 删除 `deleted`/`deleted_names` 里显式列出的项，**文件里未被提及的条目一律保留**；返回 `{added,updated,kept,removed,total,names_total}`。动机：旧 PUT 是整表覆盖，「没加载就点保存」（或旧标签页拿旧状态保存）会用空表把整部作品的术语写空（チンポ_メガテン 事故：29 条 → 0）。`mode=replace` 保留显式整表覆盖，且清空整表需 `force=true`（否则 422）。工作台作品页 JS 相应登记「删行/改名」为待删项，保存后自动重新加载表格。
  - 同类地雷：`candidates/promote-work-glossary` 曾传 `known_names=[]` 覆盖白名单 → 现在原样带回；整表覆盖语义只留给 `logic.save_work_glossary`（生成器/单条追加用）。
- **候选确认窗「记录白名单」（2026-09-20，按钮在「确认」右侧）**：`POST /api/work/<id>/candidates/<term>/whitelist` → `{ok, added, known_total, removed, backup}`。做两件事：① 词写进**全局** `glossary.json` 的 `known_names`（`logic.add_global_known_name`，写前备份 `.glossary_backup/<ts>/`）——此后不再进候选池（`build_term_pool.load_known()` 读该字段，**下次生成候选即生效**）、并作残留检测白名单；② 从本作品 `glossary_candidates.json` 移除该条（与「删除」同口径）。**注意**：残留检测白名单 `aa_translate.KNOWN_NAMES` 是**进程启动时**加载的常量 → 工作台/翻译进程重启后才吃到（候选池那边读盘即时生效）。
  - 与相邻按钮的区别：「确认」= 选为术语（勾选待 `写入作品表` 批量 promote）；「删除」= 只从本作品候选文件移除；「跳过」= 仅本会话前端跳过（sessionStorage）；「记录白名单」= 全局记住"这词不用管"+ 移除本条。
- `GET /api/work/<id>/repatriate-list`（可反哺全局的条目 = 本作品表 manual − 全局差集，零 token；`?embed=0` 跳过短词嵌入率）+ `POST /api/work/<id>/repatriate-apply`（body `{selected:[jp], backup:true}`）。
  **服务端按勾选 jp 重新 scan 取项**（不信任前端送来的 zh/cat）；路由层先剔除 `跳过` 项（`re:`/缺译名/`zh==jp`/FUNC_WORDS）并在 `skipped_selection` 回带原因。写入 roles.json（`cat=角色`）/ glossary.json `特殊-专名`（专名）+ known_names，落盘前备份到根 `.glossary_backup/<ts>/`（gitignore 已忽略）。核心逻辑在 `scripts/common/glossary_repatriate.py`，路由是薄封装。
  - **改完记得重启工作台**：模板走 Jinja 自动热加载（改 HTML/JS 立刻生效），但 **main.py 的 Python 路由要重启**——否则新按钮在、接口 404，前端只会显示「扫描失败：失败」（2026-09-20 实测踩到）。
- `work/<id>/replace-preview` / `replace-apply`（阅读器「⇄ 翻译替换」：手动替换对 → **按所选范围**（全部 / 当前话和后续话）× **章节勾选层**（默认全选，只列有命中的章，2026-10-01）的 汉化.txt，AA 艺术行跳过、宽度校正、**逐行记 `work/edits.jsonl`**（改前/改后，2026-10-01 起不再整文件备份）；body 带 `scope`/`start`/`chapters`，每对可带第 3 位 `sfx` 布尔 → 预览返 `sfx_total`、应用返 `sfx_marks`/`logged`）· `work/<id>/replace-history` GET（**历史替换记录**，2026-09-13）
- `work/<id>/translate`/`status`/`cancel`（翻译控制台，status 改读管理器最近任务）· `GET work/<id>/estimate`（**翻译/校对消耗预估**，零 API → 见「任务消耗预估」节）· `GET work/<id>/models`（**付费链模型多选清单**，零 API → 见「模型多选」节）
- **作品导出/导入**（2026-10-01）：`work/<id>/export-preview`·`export`·`export/status`·`export/download` + `work-import`·`work-import/confirm`·`work-import/status`（见「作品导出 / 导入」节）
- `tasks`（GET 页）/`api/tasks`（GET 列表·POST enqueue）· `api/tasks/<tid>/{stop,kill,cancel}` · `POST api/tasks/prune-finished`（**2026-09-19 一键清理结束任务**：删全部终态任务记录，运行中/启动中/停止中/排队中一律保留——删排队任务会静默丢待跑工作；返回 `{ok,removed,count,kept,msg}`，页面按钮 `#clean-finished` 会先按当前列表报数让用户确认）
- **设置页（2026-10-08）**：`GET /settings`（页面）· `GET /api/settings`（当前值：env 路径/保存时间 + key 掩码清单 + 三条链的候选与勾选（条目 id）+ 划词开关 + 代理地址/策略与各自来源层）· `POST /api/settings`（**区块级**：`section=keys|models|selection|proxy`，只写改动项；非法地址/策略/区块 → 422）· `POST /api/settings/test-key`（`{name}`）· `POST /api/settings/test-proxy`。密钥明文永不出接口（只有掩码）。

## 作品导出 / 导入（2026-10-01，grilling 确认后实施）

把一部作品打成一个 rar 包带走（换机 / 备份 / 分享），工作台里一键导入复位。

- **实现**：`aa_web_app/work_pack.py`（纯逻辑，可单测）+ `main.py` 路由；测试 `scripts/tests/test_work_pack.py`（含真实 rar 往返）。
- **前端一处实现两处入口**：弹窗逻辑全在 `static/work_pack.js`（`aaExportDialog(work, title)`，由 `base.html` 统一加载），作品页动作条与作品列表「更多 → 导出」共用；改导出交互只改这一个文件。
- **压缩依赖 WinRAR 的命令行**（GUI 版不算）：`Rar.exe` 按 env `AA_RAR` → `C:\Program Files\WinRAR\Rar.exe` → PATH 顺序探测；找不到则导出接口 400 并给提示。实测 RAR 5.60：日文/中文文件名、`-x` 排除掩码、`rar e` 只取 manifest 都正常。
- **包内路径 = 相对仓库根**（`aaManga/<作品目录>/…`、`aaManga/交付HTML/<…>_汉化合集/` 交付**文件夹**）：打包时 cwd=仓库根 + 清单文件（UTF-8 BOM）→ 导入端解到仓库根即归位；**不硬编码盘符**，导入时 registry 条目按 `BASE + r"\aaManga\…"` 重写。
- **打进去的（资产）**：`aa/`（原文+colormap）、`汉化/`（成品+台词文件）、`raw/`（**可选**，默认不带——体积最大且可从原站重下）、交付合集**文件夹**（2026-10-06 起：`index.html` + `data/` + 源 `txt.gz`；`html_files()` 取 `html_out` 指向的文件夹，**不做 glob 回退**，免得把历史遗留的旧单文件当交付物打进去），以及 `work/` 白名单：
  `chapter_order.json` / `chapters.json` / `site_url.json` / `title_translations.json` / `description_zh.txt` / `r18_summary.json` / `manual_fix.txt` / `glossary_candidates.json` / `glossary_iter.json` / `glossary_*.json`（作品术语表）/ `*_knowledge.json`（知识库）/ `pokemon_fixed_*.json`，目录 `gold_dialogue/`（人工台词标注）、`line_meta_overrides/`（人工行类型覆盖）。
- **不进包的（过程 / 记录 / 备份）**：`*.ckpt.json`、`tm_*`、`prep/`、`refine_ckpt/`、`line_meta*/`、`sfx_marks/`、`fixed_hits/`、`*_cache.json`、`last_update.json`；`edits.jsonl`、`tr_replace_history.json`、`runner_log*`、`*.log`、`review_inline.txt`、`width_report.txt`、`*_review.txt`、`*.tsv`；`*.bak*`、`backup_*/`（备份另加 rar `-x` 掩码兜底）。判定在 `work_pack.ASSET_FILES/ASSET_DIRS/ASSET_*` 白名单——**白名单之外一律不带**，新增资产类型要同步改白名单与测试。
- **API**：导出 `GET /api/work/<id>/export-preview`（文件数/体积、含与不含 raw 两种、资产清单、排除项计数、是否有运行中任务）· `POST /api/work/<id>/export`（body `{include_raw}`，后台线程）· `GET /api/work/<id>/export/status` · `GET /api/work/<id>/export/download?f=`；导入 `POST /api/work-import`（**原始 body + `X-Filename` 头**）→ `POST /api/work-import/confirm`（body `{token}`）→ `GET /api/work-import/status?token=`。
- **落盘位置**：导出包落 `BASE/导出/`（env `AA_EXPORT_DIR` 可改）；上传包与解包 staging 都走 `BASE/temp/aa_pack/`（同盘 → 归位是 move 不是拷贝；上传包 2 天后自动清）。
- **导入行为**：先 `inspect_import` 查冲突（registry 已注册同名 / 目标目录已存在非空）→ 有冲突拒绝（**不做覆盖**，要先删）；成功则解包归位 + 追加 registry 条目 + 合并 `work_meta.json`（跳过 `_meta` 说明键）+ `api_reload()` 热加载。
- **坑**：① 作品有未结束任务时禁止导出（防打包写了一半的文件）；② 打包/解包跑在线程里、前端轮询状态（大作品几分钟，别做成同步请求）；③ 上传不能用 FastAPI 的 `UploadFile`——venv 没有 `python-multipart`，故走原始 body；④ 测试里调 `install()` 必须同时把 `registry.BASE` / `work_pack.registry_path` / `work_pack.WORK_META_FILE` 指到临时仓库，否则测试作品会被写进真 `registry.py`（已踩过一次）。

## 划词翻译 + 朗读（2026-09-03，reader/board 共用）
- 前端 `static/selection_translate.js`：mouseup 后选区静止 1.2s 弹悬浮框（日→中统一，限宽 280px）；原文/译文/行编辑弹窗内 textarea 均可划词；>500 字不弹；翻译完成 1s 后才允许点外部关（Esc/×/新划词不受限）。模板注入 `window.AA_SEL = {work, base, enabled}`。
  - **开关（2026-10-08）**：`enabled=false`（设置页 `AA_SEL_ENABLE=0`）时脚本**整段提前 return** —— 一个监听都不挂、不建弹窗；后端 `/api/translate-selection` 与 `/api/tts` 同步 403（双端硬关）。模板取值走 Jinja 全局 `sel_enabled()`（main.py 注册，避免每页各传一遍）。
- **「重译」+ 等待计时 + 取消（2026-09-20）**：弹窗新增「重译」按钮（`force=true` + `avoid=<上次通道>` → 跳过照抄判定、换模型再试）；`echo=true`（模型照抄原文）时显示黄色提示、**禁用「替换」**（防把日文替换进正文）、高亮重译；meta 行显示 `通道 · 耗时[ · 已自动换通道重试]`；翻译中显示已等待秒数（≥8s 提示可取消），×/Esc/点外部即 `AbortController.abort()` 取消在途请求，150s 前端硬超时（`ABORT_MS`）。
- **替换按钮（2026-09-06）**：译文出后可用（有选区上下文才启用），把选中日文片段替换为译文并按「漏译」保存——textarea 场景改 `#er-tr` 值+切漏译 radio+触发 #em-save（联动行编辑面板）；正文场景（汉化模式）用 Range 算该行 .aa-line 内偏移拼整行 → PUT `/line/{idx}` type=漏译 → 行重渲染。art 行/原文模式提示不可替换；board 页（无 work/章节）不显示。
- **选中项高亮 + 区间固化（2026-09-21）**：`trigger()` 弹出时把选中区间包 `<span class="sel-hl">`（`span.sel-hl` 只改背景/内阴影，**不动字号/内边距** → 不撑行宽、不影响 art 列位），并把 `[start,end)` 绝对偏移 + 原文记进 `selCtx`（`selOffsets()`/`hlRange()`）。**替换不再读活的选区**，改用记录的区间（`pickNow()` 定位，偏移漂移时按「整行/整框恰好命中一次」兜底）——因此**翻译过程中点了别处（原生选区消失）依然能替换**；旧实现替换时重取选区，点别处即报「选区已变化」。textarea 无法内联高亮 → 弹窗头部片段（`.sel-pop-src.pick`）标成「选中的那段」代替。关弹窗/关高亮时 `clearHl()` 还原 DOM（行重渲染后节点已脱离 → 判 `isConnected` 跳过）。
- **⚠ 高亮必须打在「量锚点」之后（2026-09-21 事故）**：`hlRange()` 用 `extractContents()` 重排选区内的文本节点 → **原生选区当场消失**。首版把高亮写在量锚点之前，于是 `getSelection().getRangeAt(0)` 取不到矩形 → 回落 `activeElement` 矩形 → 空选时 activeElement 是 `<body>`（rect = 整页盒子，bottom ≈ 文档高度 17 万 px）→ 弹窗 `top` 被设成 17 万 px，**超出视口看不见**，用户看到的就是「有高亮、没弹窗」。修法：① 锚点测量（选区 rect → 控件 rect）放高亮之前；② 回落时跳过 `BODY/HTML`；③ `showAt()` 末尾把 `top` 再夹进视口。**验收别只看 DOM 状态**（`classList.contains('open')` 为真 ≠ 看得见）：要断言 `popRect.top ≈ selRect.bottom + 14` 且在视口内 + `document.elementFromPoint(弹窗中心)` 返回弹窗自身。
- **按钮文案与图标（2026-09-21，用户要求）**：`sfx 替换` 按钮文字改成 **「替换」** 并用 sfx 字形（`.sel-sfxface` = 楷体+斜体+0.95em，与 `span.sfx` 同口径），语义不变（替换并恒加 sfx 标记）；朗读/复制/重译去文字 → 纯图标按钮 `.sel-icon`（`🔊`/`📋`/`🔁`，说明进 `title` + `aria-label`）。改这几个文件后记得升模板里的 `?v=` 版本号（`selection_translate.js?v=`、`app.js?v=`、`style.css?v=`）。
- **sfx 样式替换（2026-09-15）**：拟声词/效果音译文用 `span.sfx`（楷体+斜体+0.95em+#333）。三处：
  - **「sfx 替换」按钮**：popup 第二按钮（2026-09-21 起文字显示为「替换」+ sfx 字形），替换语义同「替换」但**恒加 sfx 标记**（原文不是半角假名也能强制样式）。
  - **纯半角假名自动**：选中片段去空白/省略号后全为 `\uFF61-\uFF9F`（≥2 字，`isHankakuSfx`）→ 普通「替换」自动加标记（用户不必按第二个按钮）。
  - **标记落盘** `<work_dir>/sfx_marks/<base>.json` = `{"1-based行号": [[字符偏移, 文本], ...]}`（`logic.sfx_marks_*`，原子写；偏移漂移时渲染按最近同文本兜底，文本消失即忽略）。PUT 行 body 加 `sfx`/`sfx_clear`(区间)/`sfx_clear_all`；两种替换模式都先清被替换区间上的旧标记（同区间只留一条）。行编辑面板：该行有标记才显示「清除 sfx」（`sfx_clear_all`，内容不变也能清——`sfx_changed` 使请求不被 `unchanged` 短路）。
  - **渲染**：`gen_aa_html.sfx_spans(l, src_line, marks)` = 原文半角假名宽度对齐的自动区间 ∪ 人工标记区间（`render_line(..., sfx_marks=dict|list)`）。**`/line/{idx}/html` 必须透传 src_line + 标记**（旧实现丢 src_line → 保存后重渲染的行掉 sfx 特效；base 也按原文名归一，顺带修了 colormap 失配）。已删 `render_sfx_line` 旧实现保留为兼容包装（逐字节等价，8.5 万次比对 0 diff）。
  - 交付合集生成器（`common/gen_aa_html.py main()`）**仍未传 src_line/marks** → sfx 样式目前只在工作台阅读器可见，交付 HTML 无 sfx（旧有限制，未动）。
- 后端 `selection.py` + 路由 `POST /api/translate-selection`（body `{text, work, base, force, avoid}` → 回 `{ok, text, via, elapsed, echo}`）与 `POST /api/tts`：
  - 翻译通道（**2026-10-08 起可配**）：`AA_SEL_CHAIN`（**条目 id**，逗号分隔；裸后端名 = 用该后端默认模型）> TOML `[selection].backends`（默认 = 原三通道：`deepseek/deepseek-flash`（官网 V4.1-Flash，思考已关 → 0.6~1.1s）→ `deepinfra/deepseek-ai/DeepSeek-V4-Flash-0731`（$0.09/M 最便宜，艺术字符最稳）→ `runinfra/glm-5-3-flash`（术语遵循强）；**不填行为不变**）。设置页「划词」区块可改；`sel_chain()` 解析、`_sel_candidates()` 装配候选、`_invoke()` 只给支持 `retries=` 的后端传该参数。划词是纯文本翻译(无 AA 图形列位要求)**不走付费 call_with_fallback**。usage 记 `selection_translate` tag。**`_SYSTEM` 含美术护栏**：「非日语部分原样保留」——deepseek-flash 实测会把 ASCII 艺术改写/丢掉、并在有章节简介注入时复述简介当译文（护栏后 2/2 修好）。
    - **超时收敛（2026-09-20）**：单候选 `timeout=120`→**`AA_SEL_TIMEOUT`(默认 30)**、候选内重试 3→**`AA_SEL_RETRY`(默认 1)**（`call_deepinfra`/`call_runinfra` 为此新增 `retries=` 参数，默认 3 不变——整章翻译链不受影响）。最坏等待 ≈ 3 候选 × 30s = 90s（原先 3×3×120+退避 ≈ 18 分钟）。
    - **照抄检测补齐（2026-09-20，用户实测「译文仍是日文原文」）**：旧 `_looks_echo` 只在源文假名 ≥8 时检测 → 短句（AA 台词多为 3~7 假名）照抄一律判为正常，弹窗直接显示日文。现判据 = 扣掉专名白名单（`_allow_kana` = `KNOWN_NAMES` + 本作品术语表 jp）后取**最长公共假名子串 L**：源文 ≥8 假名 → `L≥8`（等价旧行为）；源文 <8 → `L≥3 且 L≥源文假名×0.6`（2 假名的纯专名句不判，宁放过不误报）。
    - **强制翻译（2026-09-20）**：`_do_translate` 返回 `{"text","via","elapsed","echo"}`（不再 raise「三通道均失败」当全照抄时）；`translate_text(force=, avoid=)` — 非 force 遇到 `echo` **自动再跑一轮 force 语义**（换通道 + `_FORCE_HINT` 强化指令，仍照抄才带 `echo=true` 返回）；force=True 时 `avoid` 通道排到最后、有非空结果就接受。每次划词另记 **`aa_web_app/data/selection_log.jsonl`**（ts/通道/耗时/echo/auto_retry/force——「划词超时」靠它反查，usage.jsonl 只有 token 无耗时；>2MB 滚动 .1，已 gitignore）。
  - 术语注入：有 work/base 时调 `aa_translate.glossary_prompt_hint(base)`（按章过滤术语表+知识库+章节简介，与整章翻译同口径）；board 页无 work → 裸翻。
  - TTS：edge-tts `ja-JP-NanamiNeural`（.venv 已装），代理走 `_tts_proxy()`（`AA_TTS_PROXY` > `AA_PROXY` > TOML > 默认，调用时现取 → 设置页改完免重启），音频 hash 缓存 `aa_web_app/data/tts_cache/`（gitignore），同文本零请求。**开关与划词同源**（`AA_SEL_ENABLE=0` 时 `/api/tts` 也 403）。
- 坑：`selection.py` 全程 `_LOCK` 串行（env AA_USAGE_TAG + aa_translate WORK_ID 是进程级全局，多标签同时划词互踩）；JS `busy` 标志必须在 early-return 前清（曾致卡「翻译中…」）；**`echo=true` 时必须禁用「替换」**（照抄结果是日文，替换进正文 = 把汉化行改回日文）；关弹窗 = abort 在途请求（`hide()` 里 `abortInflight()`，勿只清 `seq`）。

## 翻译替换 + 历史记录（2026-09-09 / 2026-09-13 / 2026-09-16）
- 阅读器右侧固定按钮「⇄ 翻译替换」（`#tr-replace-modal`）：手输「查找 → 替换为」对 → 预览（dry-run）/应用（`logic.apply_glossary_chapter` 逐章，**逐行记 `work/edits.jsonl`**——2026-10-01 起不再整文件备份）；**AA 艺术行整行跳过**（override > line_meta_v2 > line_type_of 分类链，与行号列一致）。
- **范围选择（2026-09-16）**：弹窗顶部「范围：全部 / 当前话和后续话（含当前话，副标题给当前章名）」——前端 POST body `{pairs, scope: 'all'|'from', start: CFG.fname}`；后端 `_scope_tr_files(cfg, scope, start)` 按 **阅读器目录顺序**（`logic.merged_chapters` 带 `chapter_order.json`，无则话数/eid）切出起始章及之后，`start` 支持汉化名或原文名（`_canon_tr` 归一）；**定位不到当前章 → 422**（绝不静默退化成全库）。响应带 `scope`，文件列表用**规范名 `.txt`**（.txt.gz 透明；apply 的 `files[].file` 也是规范名，否则前端按 `CFG.fname` 就地重渲染匹配不上）。
- **预览/应用指纹含范围**：`trPairsKey = JSON.stringify([pairs, trScope()])`——改范围即视为「未预览」，应用时弹确认（`window.confirm` 文案写明将改动哪个范围）→ 防「用 A 范围预览、按 B 范围应用」。改范围同时清空预览区。
- **sfx 逐对勾选（2026-09-21，用户要求）**：每个替换对右侧带 `sfx` 勾选框（`.tr-sfxbox`，勾上变砖红加粗）——勾了的对，命中的**每处插入片段**替换后一并写 sfx 标记（渲染成 `span.sfx` 楷体斜体），未勾的对不受影响；逐对人工选择，不搞"整批一律加样式"。链路：
  - 前端 `trCollect()` 发三元组 `[find, repl, sfx]`（历史里的对仍是二元组 → 后端按 `False`）；`trPairsKey` 含 sfx 位 → 勾选变化即视为「未预览」。
  - `_norm_manual_pairs` 归一成 `(jp, repl, sfx)` 三元组（去重键仍只用前两位，手工对优先——同一对既手工勾 sfx 又从历史带进来时取手工那条）；`_compile_pairs` / `logic.tr_hist_add` 都兼容 2/3 元组；`_sfx_pairs_of()` 抽出勾选集合，`_plan_key` 因含完整三元组而自动区分勾选状态（预览缓存不会串）。
  - `logic.apply_glossary_chapter(..., sfx_pairs=(...), sfx_out={})`：`_apply_pair()` 逐对重建（不再 `str.replace`/`re.sub`）以便拿到插入点的**新行偏移**，`edits`/`_shift_off()` 保证多对连续替换时已有标记跟着平移；**语义与旧实现逐字节等价**（11 万行真实汉化 × 11 对含正则/反向引用/删除 → 0 diff，改这段必须重跑该基准）。标记只经 `sfx_out`（dict 出参）返回，**不改返回元组形状**（`for i,b,nb,*ow in changed` + `bool(ow)` 这类解包多一个元素会把"是否超宽"静默变真）。
  - 计划里每章带 `sfx` 标记集合（`_scan_replace_plan`/`_plan_writes` 都算），apply 时 `logic.merge_sfx_marks(work_dir, base, {行号: [[off, text]]})` **一次读写整章文件**（逐条 `add_sfx_mark` 会重复读写；同位置同文本去重）；`base` 必须走 `src_name_of` → `txt_key`（标记按**原文基名**存，与渲染/行保存同口径）。响应带 `sfx_marks`/`sfx_failed`，预览带 `sfx_total` + 每文件 `sfx` 计数（UI 显示「其中 N 处将标 sfx 样式」）。
  - 边界：批量替换**不清**该行已有标记（只并入新标记；旧标记靠渲染端"最近同文本"兜底），要清用行编辑面板的「清除 sfx」。
- **弹窗改左右双栏 + 加宽定高（2026-10-01 用户要求，取代 2026-09-21 的单列地板方案）**：左栏历史（含搜索框 `#tr-hist-q`；`全选/全不选` 只作用于**当前搜索结果**）、右上替换对、右下预览；`.tr-grid` = `1fr 1fr`（左右各半），`.modal-box` `min(1400px,96vw)` + 定高 88vh，三块各自内部滚动；窄屏 <900px 叠成一列（92vh）。**踩坑：本工程没有通用 `.hidden` 规则**（style.css 只有 `.modal.hidden` 这类专条）→ `#tr-chapters`/`.tr-ch-tools` 自带 display，必须显式 `#id.hidden{display:none!important}` 才藏得住（浏览器 `offsetParent` 量测才发现空条一直占位）。历史时间改为**简略** `MM-DD HH:mm`（年份去掉，完整时间/次数/上次影响行数留在行 title）。
- **章节勾选层（2026-10-01）**：刷新预览**之后**才出现，只列**有命中的章**（0 命中 → 整条不显示），默认全选；语义 = 顶部「范围」**∩ 勾选章**。预览不带 `chapters`（照范围全扫），应用带 `chapters=[勾选章]`；后端 `_scope_tr_files(cfg, scope, start, chapters)` 先切范围再取交集，`_norm_chapters` 归一（原文名/.gz → 规范名）。章名来自服务端 `tr-chapters-data`（已汉化章，仅显示用）。**应用优先复用预览缓存**：先按不含 chapters 的 `_plan_key` 取计划再按勾选筛章，只有无缓存才带过滤现扫（`source=preview|rescan`）。前端「已预览」= pairs+scope 一致 **且** 勾选章 ⊆ 上次预览命中章（取消勾选不作废）。
- **历史替换记录（2026-09-13）**：每次 `replace-apply` **有实际改动**后自动记一条 → `<work_dir>/tr_replace_history.json`（`logic.tr_hist_load/save/add`，原子写；同 `(find, repl)` 去重置顶 + `n` 计数；上限 `TR_HIST_MAX=100`；损坏文件当空）。弹窗内「历史替换记录」区列出本作品记录（`find → repl` + 时间 + 次数 + 上次影响行/文件；空替换显示「（删除）」），**限高 96px 滚动**，逐条勾选「是否启用」（**默认全不勾**，与手工对合并后一起预览/应用，后端 `_norm_manual_pairs` 去重）。**历史不记 sfx 位**——从历史复用的对一律不加 sfx，要加就手工写一对并勾选。
- 注意：记的是**本次提交的对**（不是逐对命中统计）——同一批里有对没命中也会进历史；`applied == 0` 的调用不记。

- `save_line` 行矫正：pad 到 SRC 宽（text/mixed 允许超宽，art 行 422 拒绝）、edits.jsonl 追加
  - **编辑类型自动分类（2026-10-01，纯代码）**：`logic.classify_edit(src, old, new, kind)`（纯函数、表驱动单测在 `scripts/tests/test_edit_type.py`）。面板 radio 五选一：**自动（默认）**/错译/漏译/错位/**校对**（新类型，中文换中文）；停「自动」→ body `type=""` → 后端判定 → 响应回带 `type`/`type_auto`，toast 显示「类型：校对（自动）」；用户手动选则人工优先（`LineSave.type` 默认由 `"错位"` 改为 `""` = 自动，未知非空值仍落「错位」）。
    - 判据（顺序即优先级；用**提交值**比盘上旧值，**pad 补位不参与**——否则每次自动补 U+3000 都会被误判成错位）：① 去空白后完全相同 → **错位**；② 两行都无假名且**与原文重合数不变** → **校对**；③ 新日文度 < 旧 → **漏译**；④ 其余 → **错译**。日文度 = `logic.jp_degree` = (假名数 `KANA_RE`, 与原文去空白后的字符多重集交集数)：第二个分量专治**纯汉字日文盲区**（照抄原文的 `白頭` 假名 0，改 `白头` 后重合 2→1 下降 → 判漏译而非校对）。
    - **art 行（纯图形）不判** → 沿用旧默认「错位」（`_line_is_art` 复用 `_art_sources`+`_art_checker` 分类链：override > line_meta_v2 > line_type_of）；`#` 标题行、帖头行照常判。其它写行路径**不动**：划词「替换」= 漏译、还原原文/Shift+R/右键 = 错译、翻译替换 = 翻译替换、术语替换 = 术语替换。
    - **真实数据校准**（`edits.jsonl` 全库 12340 条，取标注可信的 漏译/错译 且去空白后确有差异的 9159 条）：漏译 6052/6060、错译 3016/3099（**99% / 97%**）。历史里的 错位 大量是 radio 默认值（存在整句重写却标错位的行）、`误译/生硬/用词不当` 等质量类标注是**无内容空记录**（before/after 皆空）→ 二者都不参与校准；故「校对」类无历史标注可校准，判据由用户口径确定。历史 type 不回填。
  - **唯一留档 = 单文件 `work/edits.jsonl`（2026-10-01 用户要求，原 `work/backup/<ts>/` 整文件备份已废）**：行保存（`main._log_edit`）/ 翻译替换（`_log_edits_bulk(..., "翻译替换")`）/ 术语替换（`"术语替换"`）每个改动行一条 `{ts, work, file, line(1-based), type, line_type, before, after}`，阅读器「修改记录」弹窗可见（`GET /api/work/<id>/edits`）；批量替换一次改数千行 → 单次打开顺序写。`logic.backup_files/rotate_backups/list_backups/restore_backup` 保留但只剩术语表 JSON 的 `.glossary_backup/` 在用（`/api/work/<id>/backups` 与 `/restore` 仅为遗留目录保留）
- `material_frequency` 素材统计（中+日文双计）→ `material_display_parts` 查角色表 → 显示名+作品+备注；**pct<5% 过滤**；`ignore_materials` 跳过。**2026-08-28 知识库优先**：work 有 `_knowledge.json` 人物 → mat-scan 用其生成（日文短名+中文译名+同名全局别名展开，带作品出处）；否则全局角色表。
  - **`_material_match_names` 别名字段**（2026-08-28）：角色表 `别名` 数组并入匹配词——同角色多译名（青蛙子/ケロちゃん 并入 泄矢诹访子）素材统计归并，译文保留两者；`len>=2` 防单字符/行号碰撞
  - **`material_display_parts` 作品名大小写不敏感+子串匹配**（2026-08-28）：mat-scan 知识库 key 是小写 `fgo/舰队collection`，全局作品名 `Fate/Grand Order/舰队Collection` + 斜杠别名串——精确 == 失配曾致悬浮显示原创，改 `(名称+别名).lower() 包含 wn_part.lower()`
- `chapter_stats`/`stats_cache.json` 章节统计增量缓存（指纹 [v2,mtime,size,prep_m]）——**改缓存 key 结构必须两处同步**（a48fdc7 教训）。**2026-09-05 起工作台不再调用**：用户确认作品页不展示宽度/残留统计，`_chapter_full` 返回空 stats（warm 不再逐章读正文+校验，提速）；`chapter_stats` 函数保留（测试用 + 管线工具独立），但 `/work/<id>` 不再逐章算
- `apply_glossary_chapter` 术语替换（re: 正则 + 宽度自动校正；**不再备份**——改动行由路由记 `work/edits.jsonl`）
- `r18_chapter_judge`/`r18_summary` 章节级 R18 判定（读 work/prep/*.json）
- **目录章节名用正文已译（c07f20f）**: 阅读目录走 `title_translations.json` 机制(键=含作品名标题→值=正文已译章节名), 目录显示「第N话:正文已译标题」不含作品名——作品级配置, 不改通用代码

## 测试
`python -m unittest discover -s scripts/tests -v`（**全量 1034 例全绿**；含 test_aaweb/test_scanner 等，零依赖 mock FastAPI 客户端 + 2 例环境性 skip）。**必须用 `.venv/Scripts/python.exe`**（aa_web_app 系用例依赖 fastapi，全局 python 会误报）
测试被改坏的黄金样本/交付物 → 失败是特性，改前先更新期望（如 test_gen_html 的正文基线 = `scripts/tests/fixtures/delivery_golden.json` 哈希清单，4 部 46 章；重建 `scripts/tools/freeze_delivery_golden.py`。旧的「已交付 fukou 单文件 HTML」基准已退役：文件被移出 git，内容也落后于后续人工修订）。
**save_line 超宽新规则**：text/mixed 行允许超宽（`test_aaweb` 的 `test_reject_wide_line` 已改 `test_text_line_allow_overwide`, src 纯假名行 text 类 → 超宽 ok）；art 行仍严格拒绝。

## 性能优化（2026-08-30，64399218/f90fe255，实测首页-68% 阅读页-58~85%）
- **阅读器正文改 JS 填充**：reader.html 的 `<pre>` 不再由 Jinja 渲染 1.8 万行 span（服务端双份序列化=HTML+tojson 是纯浪费），留空注释，reader.js 加载后立即 `render(mode)` 从 `src-data`/`tr-data` JSON 重建。行号列 line-col 仍服务端渲染（JS 不重建），正文/行号靠同一 mt 数据对齐。**风险**：JS 报错正文全无（已验证无错）；改 reader.js 渲染逻辑时必须保持 pre 初始填充。
- **merged_chapters 内存 TTL 缓存**（logic.py `_MCH_CACHE`，key=aa_dir/out_dir/name_style/order_path，指纹=目录 mtime，300s）：翻章/重复访问不再全量读章首行+排序。`invalidate_mch_cache()` 在保存行/api_reload 后显式调用——**Windows 目录 mtime 对文件内容修改不更新，mtime 指纹不可靠，必须显式清空**。
- **rendered_refine 仅 has_refine 才渲染**（无精译文件不逐行调 render）。
- **r18_summary 内存 TTL 缓存**（main.py `_R18_SUMMARY_CACHE`，out 目录 mtime 失效）：首页每作品扫全部 prep JSON 消除。
- **素材指纹内存缓存**（logic.py `_MAT_FP_CACHE`）+ `gen_aa_html_render` 的 `from common import gen_aa_html` 移模块级（5 万次重复 import 查找）。
- **基准方法**：直调视图函数计时（绕开 HTTP）；profile 用 cProfile 看 cumulative。**陷阱**：导入 main 时 `_warm_all_stats` 后台线程启动遍历全部作品调 work_stats——profile 里出现别的作品的 work_stats 是预热线程干扰，用 spy 打点 `main.work_stats = lambda w: calls.append(w)` 区分。
- 剩余可优化（收益有限未做）：search_work ~790ms（跨章搜索本质要扫全量）；line_nos 行号列 Jinja 渲染（改 JS 填充需动点击事件绑定，风险大）。

## 阅读器窗口化渲染（2026-09-21，S0–S4；方案 doc/方案归档/阅读器窗口化渲染方案_2026-09-21.md）
- **背景**：单章一次性渲染（12–22 MB HTML / 6–10 万 DOM 节点）导致大章卡死。**已确认不是内存泄漏**（40 次模式切换节点零增长），根因是体量。
- **结构**（所有章统一走窗口化，无「大章才窗口」分支）：
  - 服务端 `aa_web_app/reader_win.py`：`load_ctx`（一次读原文/汉化/精译 + colormap/line_meta_v2/overrides/sfx/gold，LRU 按 (work, base) 缓存，指纹 = 文件 mtime+size，`invalidate(work)` 清）→ 几何表 `k_idx/k_mt/k_mtk/k_dlg/k_type/k_auto/k_ov/k_gold`；`page_data` 只发 `{n, skips, mt, hasTr, hasRefine, mode}`；`rows(ctx, mode, k0, k1)` 按区间渲染行 HTML（单次 ≤ `BLOCK_MAX`=200 行）。
  - 阅读页只下发几何表（`<script id="win-data">`）+ 空 `<pre id="chapter-pre">` / 空 `<div id="line-col">`；正文与行号列**都由 reader.js 按窗口重建**（旧版的 `src-data/tr-data/refine-data` 内联 JSON 与 `line_nos` Jinja 渲染已删）。
  - `GET /api/work/{work}/chapter/{fname}/range?from_k&to_k&mode=tr|src|refine` → `{mode, from, to, n, hasTr, hasRefine, lines[]}`；`fname` 传原文名或汉化名都行（**未译章必须传原文名**——旧模板给 `''`，会让区间接口 404 空正文）。
- **几何口径**：行高 = CSS `--aa-lh`（`#chapter-pre` 的 line-height，默认 16px）；楼距 `mtk` = 行数倍数，`margin-top:calc(var(--aa-lh) * mtk)`。服务端 `k_mt` 是 **px**（`k_mtk = k_mt // 16`，与旧模板 `l.mt / 16` 等价）。窗口用上下等高 `.win-sp` spacer 撑出整章高度 → `scrollHeight` 与整章一致。
- **客户端常量**（reader.js `WIN`）：块 200 行（与接口单次上限一致）、缓存 LRU 24 块、窗口 = 视口 ± 1 块（实测常驻 400–800 行 / 2.1k–3.7k 节点）。滚动/字号/粗细变更 → `sync()`/`repaint()` 按视口重算窗口。
- **逐交互适配**：进度保存 `WIN.lineAtScroll()`、续读/`?line=N` 跳转 `WIN.scrollToLine()`（都由几何表算，不依赖窗口外 DOM）；保存行/划词替换 → `window.__aaLineChanged(idx)`（失效该块缓存 + 区内就地重建）；行号闪烁 `flashNo` → `WIN.flashLine`（按 k 记状态，窗口重建不丢）；编辑面板打开期间 `ensureVisible` + 窗口冻结（防重建抹掉编辑行），`.edit-row/.src-row` 的插入高度用 `setEditDelta` 计入几何；台词纠正标记 / 候选高亮挂 `WIN.onPaint`。
- **逃生口**：`?win=0` 或 `localStorage['aa_reader_window']='0'` → 全量渲染（分批取完所有块，行为退回旧整章 DOM）；初始化失败在正文里显示原因 + 「改用整页渲染」链接（**绝不留下空白正文**）。
- **三条必须在改这块代码时守住的铁律**（2026-09-21 上线后实测踩出来的）：
  1. **块缓存「只失效不补齐」= 正文空洞**：`paint()` 靠 `cache` 取行，`rowAt(k)` 取不到就 `continue` → 整块 200 行消失、文档高度骤降（实测掉 3328px）、正文位置整体跳。所以失效块必须立刻重取（`markChanged` 单行 / `markBatch` 批量），`sync()` 用 `cacheFull()` 判「窗口内块齐不齐」，不齐就补。
  2. **编辑期间不许重建正文**：编辑行/原文行（`.edit-row`/`.src-row`）是 JS 插进正文流的，`paint()` 重建内部 HTML 会把它们抹掉。故 `paint()` 在「正在编辑且 editK 在窗口内」时直接 return，`sync()` 编辑期间只 return（**不 closeEdit**，否则丢用户输入）；另外 `openEdit` 必须在 `ensureVisible` 之后**先 `setEditDelta(k, 0)` 钉住 editK**，否则刷新前的判定 `editK >= k0` 不成立 → 一次滚动就把编辑区删掉（「没有原文行、没法编辑」的真因）。
  3. **进度保存要「收尾」**：旧的领先沿节流在滚动停下那一瞬常不触发 → 最后位置丢失。现为防抖（350ms）+ `fetch(..., {keepalive:true})` + `pagehide` 落盘；整数页重载前（如翻译替换走 `location.reload()`）显式 `saveAAResume()`。
  4. **保存后要刷新「哪一行」，别用会被重置的全局**：`closeEdit()` 会把 `curIdx = -1`（`isEditing()` 也靠它），所以行编辑保存路径里 `closeEdit()` **之后**再 `markChanged(curIdx)` 传的是 -1 → `Math.max(0,-1)=0` → 永远只重取**块 0**，被编辑行所在块从不刷新（症状：保存后正文仍是旧内容、再次编辑却显示新内容、刷新后才变；行号闪烁也不出现）。修法=保存前先 `const saveIdx = curIdx` 快照，之后一律用 `saveIdx`（2026-09-22）。右键还原原文用的是局部 `idx`，所以它当时是正常的——**改这块时凡「closeEdit 之后还要用 curIdx/__restoreSave」都要先快照**。
- **续读口径**（服务端 `main.py reader_page`）：`?f=` 显式指定章时**也**续读——仅当请求的章 == 存档章（原文名/汉化名任一命中）才设 `resume_line`，避免跨章误跳；`?line=` 跳转参数在客户端用完后 `history.replaceState` 抹掉，否则刷新会再跳回旧位置。
- **限制**：章节内 Ctrl+F 只能命中已渲染的窗口（整章虚拟化的固有代价）；编辑面板打开期间窗口冻结（滚出窗口外会看到留白，编辑行不会消失）。
- **验证**：`python scripts/verify/reader_win_ab.py <基线目录>` 用窗口化之前的整页基线逐行比对区间 API（17 组 tr/src/refine 全一致，含 entry_28602 两万五千行）；单测 `scripts/tests/test_aaweb.py::TestReaderWin`（区间行 HTML == 单行渲染、行元数据 == ctx 几何表逐项、page_data 无 HTML）。
- **被取代**：本节之前的 §性能优化（2026-08-30）里「正文改 JS 填充 / line_nos 仍服务端渲染」的描述对正文与行号列**已失效**（现两者都按窗口重建）；`rendered_refine 仅 has_refine 才渲染` 也随之作废（按需取块）。

## 作品添加/下载功能（2026-08-30，grilling 确认后实施）
- **入口**：首页「＋ 添加作品」按钮 → 弹窗（`addwork-modal`）→ 粘贴分类页 URL → 解析 → 可编辑信息 → 确认下载。
- **简介默认不翻译（2026-09-20 用户要求）**：`POST /api/parse-url` 默认**不调模型**，简介框填**日文原文**；需要中文时点弹窗里的「翻译简介」按钮 → `POST /api/translate-intro` body `{text, r18_level}` → `{ok, text, translated}`（`add_work.translate_desc`，失败/内容审查拒绝时回退原文 + `translated:false`，翻译后可再手动改）。解析时一次性翻译仍可用：`POST /api/parse-url` 显式传 `translate_intro: true`。**模型口径**：走 `aa_translate.call_api`，`AA_BACKEND` 未设时 = `[default]`（`scripts/config/model_config.toml`）即 commandcode 网关 `xiaomi/mimo-v2.5`（key `COMMANDCODE_API_KEY`）；`r18_level=high` 传 `work_r18=True` 走 R18 安全检查。
- **后端**：`aa_web_app/add_work.py`（URL 分派/章节清单/简介翻译/registry 条目构建）+ main.py 三个路由：
  - `POST /api/parse-url` body `{url, translate_intro?}` → `{title, chapter_count, intro_jp, intro_cn, intro_translated, chapters[], kind, r401_cid, site_key}`（不翻译时 `intro_cn` = 日文原文，即前端默认值）
  - `POST /api/add-work` body `{url, work_id?, cn_title, author, status, r18_level, intro}` → 写 work/chapters.json + registry 追加 + work_meta + api_reload + TM.enqueue(download) → `{ok, work, task_id}`
- **URL 分派**：`yaruobook.com/jp/net` → yaruobook_index（返回 `(entries, desc)` 元组！）；`rss.r401.net` → r401_index（entries 含 exlink 跨站 URL）。
- **r401 新网站拦截**：exlink 域名不在 `add_work.SUPPORTED_EXLINK_DOMAINS` → 422「该作品章节在新网站 XXX，解析器未覆盖，请手动处理」。
- **registry 追加**：`main._append_registry_entry`（行级安全插入 WORKS dict 末尾，禁 find+切片拼接）。yaruobook 系带 `chapters_source="cache"`（dl_runner 读 work/chapters.json）；r401 系带 `r401_cid`。
- **⚠️ 写 registry 一律走 `main._registry_path()`（2026-10-09）**：= `registry.__file__`（app 实际 import 的那份；冻结 exe 里 = **数据根**那份），别硬编码 `_SCRIPTS/works/registry.py` —— 冻结态 `_SCRIPTS` = PyInstaller **解包目录**，那份 registry 启动引导时会被删掉（防子任务误命中），写它 = 静默丢写入或直接 FileNotFoundError。实测后果：「删除作品」在**盘上文件已删完之后**报 500 → 用户看到「文件已删除但记录还在」（僵尸条目）。
- **⚠️ `POST /api/work/<id>/delete` 顺序 = 先改记录、后删盘**（2026-10-09 改序）：旧序先删盘再摘 registry 条目，中间任何失败都会把「文件没了、记录还在」留在盘上（列表里点不开又删不掉）。现在 registry → work_meta → read_state 都成功后才删目录/交付物件；记录失败直接 500 且**一个文件都不动**。响应带 `failed`（删不掉的路径，多为文件被占用，可停实例后重试）并打日志。用例 `scripts/tests/test_work_delete.py`。
- **⚠️ 目录冲突防护**（sujin_otona 事故教训）：work_id 即使不在 registry.WORKS，目标目录 `aaManga/<work_id>/` 已有非空 aa/汉化/raw 子目录 → 409 拒绝（registry key 可能与目录名不一致，如 sujin_otona 的目录=大人のお店）。
- **work_id 生成**：`_work_id_from_title`（标题 ASCII 化，可中文）；测试确认可用。

## 压缩文本包（2026-09-05，读 .txt.gz 省磁盘）
- **目标**：工作台阅读界面读压缩文本包（文本不转图片），直接渲染章节，省磁盘。实测 `aa/` 原文 3.86GB + `汉化/` 537MB，gzip ~78%（省 ~3.4GB）。
- **存储形态 vs 规范名**：磁盘存 `.txt.gz`，但阅读器/目录/URL 一律用**规范名 `.txt`**（`entry_1.txt`），读取时经 `logic.txt_path`/`txt_read` 透明解析到 `.gz`。前端零感知。
- **权威 helper（scripts/aa_translate.py，经 aa_common 转发，管线/阅读器共用）**：`txt_read`（.txt 或 .txt.gz 透明读，剥 CR）、`txt_write`（.gz 走 gzip）、`txt_key`（剥 `.txt`/`.txt.gz` 得基名）、`txt_existing`（解析存在路径）、`txt_path`（base_dir/fname 解析到实际 .gz/.txt）。**统一 import 勿复制**。
- **逻辑层（logic.py）**：`read_lines`/`write_lines` 委托权威 helper；`merged_chapters`/`chapter_order`/`_chapter_full` 报告规范 `.txt` 名、`src_path`/`tr_path` 解析到实际 `.gz`；`_txt_head` 读 .gz 前 n 行（标题/原帖地址）。
- **压缩工具**：`scripts/tools/compress_text_pkg.py --work <id> [--keep] [--verify]`——把 `aa/*.txt` 与 `汉化/*_汉化.txt` 转成 `.txt.gz`；默认删 `.txt`（省空间），`--keep` 保留，`--verify` 解压校验（防压坏）。压缩后阅读器读 `.gz` 零代码改动（透明）。
- **已验证**（2026-09-05）：fukou 双存场景 + temp 副本"仅 .gz"场景端到端——merged_chapters 报告规范名、读 2346 行、save_line 写 `.gz` 并保持 `.gz` 形态；全量测试全绿（新增脚本会触发 test_interface_docs 的 inventory/接口文档一致性闸门 → 重跑 gen_inventory/gen_interface_docs 即绿）。
- **坑**：① 压缩后其他管线模块（translate/pass/fix/scan/gen_html/extract_dialogue）仍在 `open(join(aa_dir, base+'.txt'))` 直接读——**未接 `.gz` 前勿对有小改动预期作品删 `.txt`**（aa/ 只读侧将分批接上，汉化/ 写侧最后做、单独回归）；② `txt_existing` 皆无时返回原路径，判断存在必须用 `os.path.exists`；③ 压缩后新文件全 LF（2026-09-07 全仓统一，见根 .gitattributes）；改文件用 patch 工具安全，改完 `python -m py_compile` 校验 + 孤立 CR 检查（`grep -nP '\r[^\n]'`）。

## 坑
- **全仓统一 LF（2026-09-07）**：本目录 main.py/logic.py/task_manager.py 等全部 `.py/.md/.json/.html/.js/.css` 已 LF 化（`.gitattributes` eol=lf，add 时自动规范化）——**patch 工具可直接用**；`.bat` 例外仍 CRLF（cmd 兼容，`-text` 不转换）。改完 `python -m py_compile` 校验 + 孤立 CR 检查
- Jinja2 自动转义会吃 render_line 的 HTML → 模板必须 `|safe`
- 路由参数用 `{fname}` 单段（`{fname:path}` 贪婪匹配会吞 `/line/{idx}` 子路由 404）
- 素材手动添加输入「名（作品）」→ 前端/服务端都去括号（只存角色名，作品靠角色表映射）
- work_meta.json 是用户手写数据，误还原会永久丢失
- **设置页/`.env`（2026-10-08）**：`AA_USE_PROXY=0`（一律直连）会让**抓日文站**也直连而失败（抓站本来就依赖代理）——它只该用来给"没有 v2rayN 的机器"关掉 API 侧的强制代理；`AA_BASE` 只改**数据根**，`.env` 恒在**仓库根**；改端口 `AA_PORT` 后 `一键重启工作台.bat` 会跟着读 `.env`（bat 用 `for /f` 解析，别把端口写成带引号/带空格的值）。
- bat 写文件要 **GBK + CRLF + 全 ASCII 注释**：`rem`/`echo` 行里出现 `>` 会被 cmd 当重定向（旧 bat 的 `echo ... ->` 每次运行都多打一行 "The filename...incorrect"，2026-10-08 顺手去掉）。

## usage 统计（翻译/校对/视觉）
`usage.jsonl` 每行 `{tag,ts,prompt,completion,total,model,backend}`——翻译(11个 call_*)/校对(call_with_fallback)/视觉(vision_classify)每次调用记 model+backend（a01d0b5）。**ts 是 unix 时间戳非日期串**（按日期过滤应比较数值范围）。
- **大小限制+滚动（2026-08-31，`scripts/common/aa_usage.py`）**：主文件超 **10MB**（`AA_USAGE_MAX_BYTES` 覆盖）→ 滚动归档 `usage.jsonl.1/.2/.3`（`.3` 最旧淘汰），新主文件由下次写入创建。写入点 `aa_backend._log_usage` / `vision_classify._log_usage_vision` 统一走 `aa_usage.append_usage`；统计工具 `usage_total.py`/`usage_stats.py` 用 `iter_usage_files()` **合并读主+滚动文件**（历史数据不丢）。**读取侧性能**：`_usage_index` 用 (mtime,size) 指纹增量缓存——滚动后主文件变小 → 自动重建缓存不崩；任务页实时聚合只读主文件（旧文件=已完成任务 usage，约数可接受）。`.gitignore` 已忽略 `usage.jsonl.[0-9]`。


## 任务消耗预估（2026-09-21，翻译/校对控制台）
- **入口**：`GET /api/work/{id}/estimate?type=translate|proofread&eids=1,2,3&force=0|1&free=0|1&models=mimo,deepseek`（eids 空=全部章节；`models` 空/缺=整条链）→ `{ok,chapters,skipped,matched,chars,prompt,completion,cost,cost_peak,cost_cached,cost_min,cost_max,priced,peak_now,rate,models,models_sel,chain,coef,note}`。零 API、无副作用。
- **模块** `aa_web_app/estimate.py`（纯逻辑、可单测）：体量 = 所选章**原文全字符数**（`logic.txt_path`+`read_lines`，.gz 透明；按 (路径,mtime,size) 进程内缓存）；系数 `COEF` = **实测每源字符 token**（2026-09-21 按 usage.jsonl 的 `task` 字段任务归因，3 个已完成 translate 任务：翻译 in 0.587~1.100 / out 0.181~0.192；校对 in 0.126~0.213 / out 0.0025~0.0037）→ 默认 翻译 (0.80, 0.19)、校对 (0.17, 0.003)，界面标「粗估 ±40%」（实测比 0.73~1.38×，与区间一致）。
- **已处理章不计入**：翻译跳过已有 `_汉化.txt` 的章（`force=1` 全算）；校对跳过 `refine_ckpt` 指纹未变的章（`_refined_ok` 与 `refine_aa._ckpt_ok` 同口径：done + 原文/汉化 sha1 均未变；`.gz` 场景判不出 → 视为需重校，安全方向）；判定带 120s TTL 缓存。
- **金额** = `[pricing.rates]`（`aa_config.pricing_for`）按**勾选集合的主力端点**算（2026-09-22 模型多选后：主力 = 勾选里**第一个 `cache=true`** 的端点 = `call_with_fallback` 的章内 pin 目标；未勾选 = 默认集 = 付费链里可用且非兜底的第一项——翻译与校对同源（校对链跟随付费链），故两页同勾选金额一致）。三个对照：`cost`（全按未命中）、`cost_peak`（高峰 ×2，`peak_now` 标当前是否高峰）、`cost_cached`（按实测前缀缓存命中 80%）；多选时另给 `cost_min`/`cost_max`（勾选集合内最便宜/最贵的端点，界面显示「链内 ¥a–¥b」）。**单价缺失 → `priced:false`**，界面只显示 token 不显示钱（不猜价）；`free=1`（免费模型）→ 全部 0。
- **前端** `static/app.js` 的 `aaEstimateBox()`（去抖 400ms + 丢弃过期响应 + `.est-box` 显示）与 `aaModelPicker()`（模型多选，见下节）；`translate.html`（勾选/`--force`/模型下拉/模型勾选变化 → 刷新）与 `proofread.html`（勾选/模型勾选变化）各挂一处。冷算 42 章 ≈1.2s（逐章算 sha1），热算 <50ms。
- **坑**：路由参数名 `type` 会遮蔽内置 `type()`——异常分支里用 `e.__class__.__name__`，别写 `type(e)`。

## 付费链模型多选（2026-09-22，翻译/校对控制台）
- **入口**：`GET /api/work/{id}/models?type=translate|proofread` → `{ok,type,r18,chain:[{backend,model,id,label,cache,r18,fallback_only,available,rate}],default:[**条目 id**],head:"主力端点"}`（零 API，只读 `model_config.toml` 链定义 + `[pricing.rates]`）。**2026-10-08 起勾选单位 = 链条目 `id`（`backend|model`）**，不再是后端名——同后端多条目（deepinfra 的 V4.1-Flash / 0731）因此可分别勾选（旧后端名仍被 `aa_config.selector_match` 接受 = 该后端全部条目）。
- **清单来源**：翻译 = `[default].normal_backends`（`work_meta.r18` 为真 → `r18_backends`），**校对 = 同一条付费链**（2026-09-22 `[refine_aa].follow_default=true` → `aa_config.refine_catalog(r18)` 直接返回 `chain_catalog(r18)`；两页勾选清单因此完全一致）。`follow_default=false` 时才退回 `[refine_aa].backends` 固定链。**不做可用性过滤**——缺 key 的端点也列出（前端标灰 + disabled）；`default` = 可用且非 `fallback_only` 的那批（兜底端点默认不勾）。
- **语义（勾选集 = 本任务候选链）**：勾选集 **== 默认集 → 后端不写 env**（= 整条链，含兜底端点，与旧行为完全一致）；勾选集不同 → 翻译写 `AA_CHAIN=id1,id2`（`aa_config.backend_chain` 按条目 id 过滤，**保持 TOML 链序**，链内降级语义不变；全不可用则回退整链并 stderr 提示），校对写 `AA_REFINE_BACKEND=...`（refine_aa 既有 env，写单个即不降级；链项同样支持条目 id，按位模型覆盖 `_REFINE_MDLSEQ`）。全不勾 = 不写 env = 整条链（前端会提示）。
- **翻译任务的校对阶段也用勾选模型（2026-09-22）**：校对链已跟随付费链 → 翻译入队时 `_chain_env` 同时写 `AA_CHAIN` 与 `AA_REFINE_BACKEND`（同一个勾选集），translate_runner 内置的 refine_aa 子进程即按勾选集校对；独立校对任务只写 `AA_REFINE_BACKEND`。前端提示语按 type 区分（「翻译+校对阶段都用它」/「校对阶段」）。
- **前端**：`static/app.js` 的 `aaModelPicker({box,work,type,onChange})` → `mp.names()` 给 `aaEstimateBox({models:...})` 和提交 body（`models:[...]`）；翻译页仅在模型下拉 = 「默认（付费链）」时显示（选免费链 → `mp.show(false)`，免费池不挑模型），校对页常显。样式 `.model-box/.mchk-list/.mchk-row`（style.css）。
- **测试** `scripts/tests/test_model_choice.py`（27 例）：catalog 形状（含 `id`）/默认集排除兜底与缺 key/r18 链过滤、**校对清单 == 付费链清单**、`AA_CHAIN` 按 id 过滤与回退（含同后端两条目单选、旧后端名等价、未识别回退整链）、`_chain_env` 映射（翻译同时写两键）、`estimate(models=...)` 换端点换金额 + 链内区间 + 免费 0 元；`scripts/tests/test_refine_fallback.py` 覆盖 `refine_chain()` 跟随付费链/r18 过滤/env 覆盖（含条目 id 写法、同后端两条目按位模型）/逐项降级。

## 定时任务 + 小米 token plan 折扣时段（2026-09-29，方案 doc/方案归档/定时任务与tokenplan折扣方案_2026-09-29.md）
- **入口**：翻译控制台（`/work/<id>/translate`）模型下拉新增「小米 mimo-v2.6（token 套餐 · 可定时）」→ 旁边「定时启动」复选框 + `datetime-local` + 「下一个 00:00」快捷（默认填下一个 00:00 = 折扣窗口开始）。
  勾定时**自动切到 mimo（小米）**；`POST /api/work/<id>/translate` body 加 `schedule_at`（`YYYY-MM-DDTHH:MM`）。
- **后端强制**（不信前端）：`main.api_translate_start` 解析 `schedule_at` → `params["schedule_at"]`（unix 秒），env 写 `AA_CHAIN=mimo` + `AA_REFINE_BACKEND=mimo`（翻译+校对都只用小米、链内不降级）。
  **拒绝**：免费链（无 token plan 折扣）、**R18 作品**（r18 链没有 mimo；v2.6 对 R18 一律 content_filter）、时刻已过/格式不对（422 文案）。
- **`scheduled` 非终态**（`task_manager`）：未来时刻入队即 `scheduled`（仍在 `queue.json`）——**不占同作品串行**（否则一个 00:00 的任务锁该作品一整天）、不参与并行计数与假死检测；`scheduler_tick` 每轮把**到点**的转 `queued`，未到点的**跳过但不阻塞后面的任务**（候选 = 队列里第一个 queued，其余保持 FIFO）。
  到点但同作品在跑 → 留在队列等下一轮；`cancel_queued` 对 `queued`/`scheduled` 同权（任务页「取消排队」两态都出）。CLI：`task_manager.py enqueue --type translate --work <id> --chapters <eids> --schedule-at "2026-09-30 00:00" [--env AA_CHAIN=mimo]`。
- **折扣口径**：`[pricing.rates]` 条目可带 `off = 0.8` + `off_windows = ["00:00-08:00"]`（**每日**，不看周末）；`aa_cost.time_mult(ts, rate)` = 高峰倍率 `peak` × 折扣 `off`，按**记录时间戳**折算 → `usage_stats --cost` 历史报表自动按 0.8× 计。
  预估：`GET /api/work/<id>/estimate?...&at=<定时时刻>` → 多返回 `cost_at`（该时刻金额）/`off_at`/`off_now`/`off_windows`/`off_factor`，预估条显示「定时落在折扣时段（00:00-08:00，×0.8）→ ≈¥X」。
- **测试** `scripts/tests/test_schedule_task.py`（25 例：时间解析/到点判定/不占串行/不阻塞后续/到点转排队启动/同作品忙等待/取消/路由与 env/R18 与免费链拒绝/`_task_public`/预估折扣金额）+ `test_usage_cost.py::TestOffWindow`（5 例：窗口边界与周末、peak×off 相乘、cost_of 折算、无 off 字段不误折）。

## 统一任务管理器（2026-08-29，方案 doc/方案归档/翻译进程管理方案_2026-08-29.md）
- 模块 `aa_web_app/task_manager.py`：注册表= `aa_web_app/data/tasks/*.json`（单一事实源）+ FIFO 队列 `queue.json` + 调度器 pid 锁 `.scheduler.lock` + 两档停止标志 `.stop_<tid>.{chapter,asap}`。**CLI/HTTP 双入口**，工作台不开也能提交（CLI enqueue 自动拉起独立调度器进程排空队列）。
- 全局并行 ≤ `MAX_PARALLEL`(2)，同作品串行；落盘持久化：重启接管 running（pid 检测）+ 队列自动接续。同作品串行在 enqueue 拒绝（排队/运行中即报错）。
- runner 协议 `scripts/common/task_report.py`：env `AA_TASK_ID/AA_TASK_HB/AA_STOP_CHAPTER_FILE/AA_STOP_ASAP_FILE`；心跳写 `data/tasks/.hb_<tid>.json`（与任务文件分开防争写），退出前 `finalize(rc)`；退出码 **41=用户停止**；块边界尽快停用 `TaskStoppedError` 取消未开始块后弃本章（ckpt 续跑）；阶段/章边界 `check_stop` 见到即删标志。
- token 统计：usage.jsonl 带 `task` 字段（aa_translate/aa_backend/vision_classify 的 `_log_usage` 已加），任务页按 task 聚合，旧记录按时间窗兜底（approx）。
- 入口：CLI `python aa_web_app/task_manager.py enqueue|list|status|stop|kill|cancel|remove --help`；web 内嵌调度器线程随工作台启动（`@app.on_event("startup")`）。
- 任务卡字段（`_task_public`）：status/task_id/pid/rc/type/work/chapters/phase/chapter_idx/chapter_total/usage/free_mode(env 含 AA_FREE_MODE=1)/elapsed/eta(按章节均速估算)/title(registry 日文名)。**phase 等在顶层不在 t.hb**（前端 taskCard 读顶层）。
- `/api/tasks?log=1` 才带 log（日志尾 20 行，`_log_tail` n=20）；任务页分区刷新（只重建 `.st` 状态区，日志 `.log-box` 保留 DOM 不重建）→ 展开日志不被轮询折叠。
  - **日志展示范围（2026-09-13 用户要求）**：**保留**在 `/tasks`（翻译任务菜单页）——「打开日志」按钮 + 单任务 `?log=1` 拉取；**去掉**作品下的 `/work/<id>/translate` 与 `/work/<id>/proofread` 两页的日志块（那两页只留「任务状态」：状态/阶段/章节进度/rc，实时日志去菜单页看）。原始日志文件 `<work_dir>/runner_log.txt` **按作品覆盖写**，同作品多任务会互相顶掉（历史任务卡看到的是最新一次的输出）。
- 翻译控制台「模型」下拉：默认付费链 / 免费模型（body `free_mode=true` → env AA_FREE_MODE=1）；**选付费链时下方多出「候选模型」多选**（`#model-box`，2026-09-22 → 见「付费链模型多选」节），勾选集随 body `models=[...]` 入队。
- **任务页卡死修复（2026-08-31）**：① `_stall_check` 原 `_usage_index(force=True)` 每次全量重读 usage.jsonl（12MB+ 时 381ms/次），调度器每 2s tick × 每 running 任务 → 持续 ~20% CPU 卡死系统——改非 force 用 (mtime,size) 指纹增量缓存（0.09ms/次）；② `_log_tail` 原 `readlines()[-n:]` 读全文件（翻译日志几 MB），改尾部 seek 只读最后 64KB；③ bat 增强：杀 8765 外再杀所有命令行含 aa_web.py 的残留进程（防旧实例不监听 8765 漏杀）。
- **作品更新检查（2026-08-31）**：作品列表「更多」菜单「更新」按钮 → `POST /api/work/{work}/check-update`（实时拉原站清单 vs 本地已下载，逐页拉取遇已有即停）→ 弹窗显示新章 + 三按钮；`POST /api/work/{work}/update` body `{action: download|translate}` **用 check_updates 精确 eid 入队**（非 "new" 全量，防 baitou 类只下载部分章节的作品误拉历史旧章）。逻辑在 `add_work.py`（check_updates/fetch_updates_paged/fetch_remote_entries），详见主 AGENTS.md「作品更新检查」。
  - **链式（2026-09-13）**：`action=translate` 入队的是 **download** 任务 + `params["then"]={"type":"translate","chapters":<eids>[,"env"]}`；task_manager `_chain_followup` 在前置任务 **rc=0** 时自动入队后续（停止/失败/崩溃不入队，避免对半成品烧 token），结果落 `task["followup"]`（`/api/tasks` 的 `_task_public` 已透出）。CLI 侧等价 `task_manager.py enqueue --type download --work <id> --then-translate`
- 坑：全仓已统一 LF（2026-09-07，.bat 例外仍 CRLF）；停止标志 asap 优先且块循环用 `check_stop(only="asap")` 只消费 asap，chapter 标志留给章节边界检测；Windows `os.kill(pid,0)` 全抛 WinError 87 → `_pid_alive` 用 OpenProcess 判活（勿改回 os.kill）；`sys.exit(41)` 在 ThreadPoolExecutor with 块内会被 __exit__ 吞掉（aa_translate 已改 raise TaskStoppedError，勿改回）。
