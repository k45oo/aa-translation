---
name: aa-model-chain-ops
description: Use when 在 AA 翻译链上增删/替换模型或后端（配置→默认值→验证→文档→测试）。
---

# AA 模型链变更落地（换模型/调链序/改兜底）

> 适用于 `<repo>`。**评估选型**看 `aa-model-benchmark`（跑分/校对对比）；**本技能只管落地**——
> 把评估选出的模型真的换上去：改哪些引用、怎么验证生效、哪些文档必须同步、提交怎么切。
> 校对链（refine_aa）的链与开关细节见 `aa-translation-proofreading`；run 后审计哪个模型真在干活见 `aa-task-manager`。

## 何时用
- 用户说「某模型接入 normal/r18」「把 X 换成 Y」「换链首/换兜底」
- 换后端（AA_BACKEND 新增/移除、思考参数适配）

## 五步流程（漏一步就出「配置改了没生效」/「文档与配置不符」）

1. **配置（唯一权威）**：`scripts/config/model_config.toml` 的 `normal_backends` / `r18_backends` 改条目。
   commandcode 系还有**独立概念** `[backend.commandcode].fallback_model`（网关内首选模型失败时的回退，与链是两回事）——
   用户只说「换 X」时默认是**全部引用一起换**；若想拆开（链换新/网关兜底留旧）要主动说明并在报告里写明。
   - **链上「兜底端点」= 条目加 `fallback_only = true`**（2026-09-21 落地，语义=默认不使用、只在其他端点均失效时启用）：
     `aa_config.fallback_only_names(work_r18)` 是唯一读取点，`call_with_fallback` 用它把该端点从**健康态端点轮询**与
     **pin 目标**里摘除（只留链尾按序降级），`aa_prompt_prefix` 同步跳过（否则白拼前缀）；**显式 `AA_BACKEND=<该端点>` 照旧放链首**（手动选择优先）。
     单测 `scripts/tests/test_endpoint_pin.py::TestFallbackOnlyEndpoint`。**只把它排到链尾是不够的**——关掉 pin
     （`AA_PIN_CACHE_ENDPOINT=0`）或链上没有 cache 端点时会走端点轮询，链尾条目照样占 1/N 流量。
2. **代码默认值**：模块级默认（如 `aa_backend.CC_FALLBACK_MODEL`、`DS_MODEL`）——TOML 优先，但默认值不跟会在 TOML 缺失时回旧模型。
   另查**靠模型名分支**的逻辑：`if "muse-spark" in model or "ox-alpha" in model: 加 reasoning_effort=low` 类型——版本号变了是否仍匹配。
   **引用别靠记忆列点**：`grep -rn "<旧模型 id>" --include=*.py --include=*.toml --include=*.md .`（含 docstring/注释里的模型名、`AGENTS.md`、`doc/翻译模型对比.md`）——改完只剩历史方案/legacy 里的引用才算完。
3. **验证生效**：`aa_config.backend_chain(False/True)` + `auto_backend(True/False)` 应指向新链首；再真发一发
   （`aa_backend.call_with_fallback(...)` / `aa_translate.call_api(...)`）确认 `usage.jsonl` 记的是新模型名，不是只看配置打印。
4. **文档同步**：`AGENTS.md` 模型链描述、`doc/翻译模型对比.md`（链表 + 实测段）。
   `scripts/_inventory.json` / `doc/接口文档.md` **只在新增/删除脚本或 env/CLI 变化时才重跑**（`gen_inventory.py` + `gen_interface_docs.py`）——否则会把无关变更混进提交。
5. **测试 + 窄提交**：`.venv/Scripts/python.exe -m unittest discover -s scripts/tests`
   （`test_inventory_fresh` / `test_docs_up_to_date` 会在漏重跑生成器时直接红）；
   **只 `git add` 自己动过的文件**——并行会话常在同时改 `aa_web_app/*`，`git add -A` 会抖进去。
   **同一文件里也有别人的未提交改动时，按 hunk 暂存**（`git diff <files>` → 抽自己的 hunk →
   `git apply --cached`），别整文件 `git add` 把对方半成品提交进去——配方见 `git-selective-commit`。
   **并行会话在跑 git（大仓 `git add -A` 要数分钟）会留下 `.git/index.lock` → 你的 commit 直接 fatal**：
   先确认 `tasklist /fi "imagename eq git.exe"` 无进程、且锁文件比 `.git/index` 还旧 = 陈旧锁，`rm -f .git/index.lock` 后重试；有活进程就等，别删。

## 换上去 ≠ 基准分够了：必须过目标场景的真实语料 A/B

基准脚本是裸测（固定用例），**测不出目标功能自己的失败模式**。换成某个具体功能的主力模型（如划词、
单章整翻）后，用该功能的**真实输入**抽 3~5 条对比新旧模型，重点盯**老模型本来能处理、新模型会坏**的项：
- AA 艺术字符/列位在候选里是否原样保留（新模型可能整段丢掉或把 `: :` 改写成全角）
- 是否**照抄原文**、或拿注入的【章节简介】当译文复述（注入越长越容易）
- 长度/截断、以及每条降级路径都真的走通（顺手把一个后端打断，确认能落到下一个）
- 把实测结论写进被改函数的 docstring（时延/失败模式/为何加护栏），下次不用重测

**给新模型加 prompt 护栏会引入新的失败模式**：护栏（如「非日语部分原样保留」）修好 A 问题的同时，
可能在另一类输入上触发 B（实测：短纯文本句开始原样返回日语）——护栏必须配**输出侧检测 + 重试**
（检测特征 → 去掉护栏重试同一后端 → 仍失败才降级），否则等于把 bug 从左口袋挪到右口袋。

## 改动验收分两档（用户口径，先归好档再动手）

- **零风险档**（不改译文输出：量测/单价表/上报字段/端点策略/并发基础设施）：单测 + 真跑观察即可，做完就上。
- **影响输出档**（改 prompt 结构、合并调用、复用旧译文、换模型）：**基准分不降**（`scripts/benchmark/eval_translation.py`）
  + **3 章真实语料人工抽检**（重看 art 列位 / 角色名 / 残留假名）+ **env 开关可回退**（默认开）。
  逐条等价只适用于「重构」（同输入同模型），改 prompt/模型这类故意改变输出的项比不出对错，只比质量分。
- 复用历史译文（TM/缓存类）要额外加护栏（宽度、残留假名、术语一致性），因为错了不会自愈。
- **量测先于排序**：先按真实计费口径量测再排优先级；按 token 数拍的顺序会被推翻（同一批调用的 token 计数正确、成本结构却可能反）。

## 位子不同，验收标准不同

| 位子 | 看什么 |
|---|---|
| 链首/主位 | 质量 × 速度都要；换了会直接影响每章耗时与 token |
| 链上兜底位（rare path，前几个后端失败才轮到） | **质量优先，可接受变慢**（拿质量换延迟划算） |
| 校对链（refine_aa） | 2026-09-22 起**默认跟随付费链**（`[refine_aa] follow_default=true`）→ 改付费链首会连带改校对链首：**先测有无质量收益**——同缺陷输入下发现行集合/验证通过数没变就别换（白涨总耗时）；换链首后必须 dry-run 一章对照（见坑） |
| 网关内 fallback_model | 兜底位：注意超时余量（大块实测耗时 ≤ 超时值的 1/3 才稳） |

## 成本口径：链排序与「值不值」按**实际计费成本**，不按 token 数

同一个模型的 token 数与账单可以差一个数量级（缓存命中价 = 未命中价的 1/50），所以换链、排链序、
判断某优化值不值，一律先拿这三件量：

1. **落盘字段**：`usage.jsonl` 每条带 `cache_hit` / `reasoning`（由 `aa_backend._usage_extra()` 跨厂商取，
   `_log_usage(..., u=<响应 usage>)` 落盘；`AA_USAGE_COST=0` 回退旧格式）。
   **`0` 与「字段缺失」必须分开记**：前者=端点支持但没命中，后者=端点不提供，混了就算不出命中率。
2. **单价表** `model_config.toml [pricing]`：`rates` 三级回退 `<backend>|<model>` → `*|<model>` → `<backend>|*`，
   字段 `hit`/`miss`/`out`/`peak`/`src`，查表走 `aa_config.pricing_for()`。
   **查不到就留空，绝不猜价**——`common/aa_cost.py::cost_of()` 把无价 token 计入 `unpriced` 让报表标注覆盖率，
   而不是按 0 元顶替（把未查证网关算成免费会直接误导换链决策）。
3. **报表** `python scripts/tools/usage_stats.py --cost [--days N] [tag]`：按 tag / 端点 / 作品 / 天出 ¥、命中率、
   思考 token、未定价占比。**历史记录没有 `cache_hit` 字段 → 金额是上界**（全按未命中价），别当实际账单。

高峰倍率写在 `[pricing].windows`（官网 DeepSeek：工作日 09:00-12:00 / 14:00-18:00 双倍，周末半价），
`aa_cost.is_peak()` 已处理「周末不翻倍」——折算按记录时间戳套，不要按「现在几点」算。

**折扣窗口（反向，条目自带）**：`[pricing.rates]` 条目可带 `off`(系数) + `off_windows`(["HH:MM-HH:MM"])——
与 `peak` 语义相反：**每日**生效、**不看周末**（如小米 token plan 每日 00:00-08:00 系数 0.8×）。
两者都走 `aa_cost.time_mult(ts, rate)`（相乘，按记录时间戳）⇒ 历史报表不用重跑、预估与报表同源。
端到端自证法（零 API）：造两条**只差时刻**的同 token 记录 + 一份 `usage.file` 改指临时文件的 TOML，
`AA_CONFIG=<临时 toml> usage_stats.py --cost` —— 合计必须 = 窗口外原价 + 窗口内折后价（实测 3.00+2.40=5.40）。

**链排序按实际单位成本排，不按模型能力排**：输出价比命中输入价贵约 200 倍，所以「思考关不掉」的模型
（传 disabled 报错的那种）应当排在**链尾**，先看报表里 `reasoning` 占比再决定动不动链。

量测配方（跨厂商字段名、探针步骤、实测命中率与成本结构、pin 落地与验收）→ `references/token-cost-measurement.md`；
探针随本技能发货（不是仓库脚本）：`python <技能目录>/scripts/probe_llm_cache.py --backend deepseek --model deepseek-flash [--work <作品> --base <章>] [--conc 8]`。

## 换端点/换套餐（BASE_URL 变动、key 换代）

受理方给新链接时（如小米按量付费 → token plan 专用链接），**只有两处要动**：
`[backend.<x>].url` + `.key_env`（`aa_backend.<X>_URL`/`_model()` 自动跟随），外加一条 env 逃生口
（`AA_<X>_URL` / `AA_<X>_KEY_ENV`）→ 回滚不改代码。

1. **先做 key × 端点配对矩阵**（新套餐的 key 常与旧 key **互不通用**，混用是 401 而不是语义错误）：
   对 2 端点 × 2 key 各发一次 `max_tokens=8` 最小请求，只打印 HTTP 状态（key 只打前缀）——
   实测矩阵长这样：新链接×tp key=200、新链接×旧 sk key=401、旧链接×旧 key=200、旧链接×tp key=401。
   写进 `doc/翻译模型对比.md`：下次谁再看到 401 不用重跑一轮。
2. **key 解析按 `key_env` 取名 + 旧名兜底**：读不到指定名再试旧名，最后才抛错——tp key 缺失时
   会以旧 key 打新端点 = 401 = 链可见降级到下一端点，**不是静默错译**（选这种失败模式，别写死单一名）。
3. **实机发一发**（`call_<x>` 走 TOML 解析出的 URL/key），确认：非空输出、行号格式保持、0 残留假名、
   耗时量级、`usage.jsonl` 里 `backend=` 正确、以及**缓存命中的端点换链接后是否仍命中**（`cache_hit>0`）
   —— 这条决定链内 pin 目标还有效与否。
4. 换端点往往连带**计费变化**（套餐/折扣窗口/高峰）→ 同一次改动里把 `[pricing]` 补上（见上「折扣窗口」），
   否则报表继续按旧单价算，换端点的收益看不见。

## 坑
## 链条目 id：选择的单位是「条目」不是「后端名」

比后端名更细的一级选择单位：`aa_config.entry_id(name, model, item)` = TOML 显式 `id=` 或自动 `<backend>|<model>`
（分隔符用 `|`：模型名里会出现冒号，如 `meituan/longcat-2.0:free`）。规则：
- `AA_CHAIN` / `AA_REFINE_BACKEND` 接受**条目 id**，并**兼容**旧式后端名（= 该后端全部条目），匹配走 `aa_config.selector_match()`；
  全部选不中就回退整条链（并在 stderr 提示一次），不要静默断链。新增/改动链选择 env 时照此二者兼容。
- `chain_catalog()` 的 `default` 集 = 条目 id；界面勾选（`app.js` 模型多选）、`estimate._chain_head(models=)`、
  `estimate` 的 `models_sel`、工作台 `_chain_env` 写进 env 的值全部按 id 对齐。
- **「当前生效勾选集」只有一份实现**：`aa_config.chain_selection(env_name, entries, default_ids)`
  → 设置页 `models_view`、控制台 `GET /api/work/<id>/models` 的 `selected`/`custom` 都调它；
  控制台多选**默认勾选 = 设置页保存的链**（别再拿 `chain_catalog` 的 `default` 当勾选态：
  那 = 整条链，设置页改过的选择在控制台看不见，用户得每次手点取消勾选）。选择项不在本作品链上
  （给 R18 作品选了不接 R18 的端点）→ 交集为空回 `default`，与 `backend_chain` 的回退同语义。
  `head`（主力端点）也要按**生效链**（链序 ∩ 勾选集）取第一个可缓存端点，摘掉链首时显示得跟着变。
- 校对链的存储形态 = `_REFINE_CHAIN`（后端名序列，旧接口）+ `_REFINE_MDLSEQ`（**按位**模型覆盖，与链等长）
  + `_REFINE_CHAIN_MODELS`（折叠表，兼容旧调用方）；`_call_glm` 按位取模型，只在外部**只改** `_REFINE_CHAIN`
  （长度不等）时回退折叠表。
- **要换模型就改/加链条目，别指望 `AA_<BK>_MODEL`**：`aa_backend` 的 `DS_MODEL`/`DI_MODEL`/... 是 **import 时期定型的模块常量**，
  而链条目自带模型且优先级更高（`model=(m if m else _model_for_backend(b, m))`）⇒ 在链上设 `AA_DI_MODEL` 是**静默无效**的空操作。
- **改链的存储形态/选择口径前先扫测试树**：手写 mock 会照旧形态重实现（直接改 `_REFINE_CHAIN`、用 `items[-1]["backend"]` 断言），
  改完它们要么失效要么静默指向真链——`search_files` 搜旧全局名与旧断言逐处更新，别等全套件里冒红。

- **新增后端/换链首要扫「手写后端分发映射」**：链以外的调用点常按后端名 `dict` 分发（`aa_web_app/selection.py` 的
  `_call_fn`、`refine_aa._call_glm`、`aa_backend._call_named`）——漏登记=该候选被**静默跳过**（无报错、无日志）。
  症状是「链首配了却从不生效、每次都走第二候选」（2026-10-10 opencode_go 进划词链首：`via` 永远不是它）。
  换链首后至少验证：① 每个能进链的后端都有分发（加个「遍历 catalog 断言全有映射」的用例）；
  ② 真跑一次看 `via`/日志里的实际后端名（别只看配置）；③ 同名模型挂多个后端时，显示/日志要带**后端名**
  （只显示模型名分不出走了哪条）。
- **runner 自动设的 `AA_BACKEND` 会盖过 `AA_CHAIN` 勾选集**（2026-10-11 修）。`call_with_fallback` 对「显式
  `AA_BACKEND`」做**组前置 + 缺则插头**，而 `translate_runner` / `aa_translate --work` / `refix_mixed_segs`
  在用户没设时会把 `AA_BACKEND` 设成**自动选出的链首**——那不是用户选择，却因此把链首强行插到最前：
  ① 定时任务（`AA_CHAIN=mimo` + 00:00-08:00 折扣）实测从 opencode_go 起步，折扣白等；② 控制台取消勾选链首
  （`AA_CHAIN=deepinfra+deepseek`）实测 `[deepseek, opencode_go, deepinfra]`，被取消的端点照样被用。
  **修法**：`AA_BACKEND_AUTO=1`（三个自动选点都要打这个标记）⇒ 不组前置、也不把该端点从 `fallback_only`
  兜底位提出来；`aa_prompt_prefix` 早就是同口径。**加新「自动选后端」的地方必须补 `AA_BACKEND_AUTO=1`**，
  否则又会盖过勾选集（用例 `test_endpoint_pin.TestAutoBackendDoesNotOverrideChain`，含「三个自动选点都打了标记」的静态断言）。
  注意：健康态端点轮询（`_chain_round` 全局指针）本身就会旋转链序，所以「组前置」只在无轮询（有 pin/降级记忆）时可见效果；
  端点在 `AA_CHAIN` 过滤后**不在链上**时，旧逻辑是真把它**插入**链里——这类用例断言「用了哪些端点」而不是「谁是第一个」。
- **别名表漏登记比漏分发更狠：静默回落「云端直连」**（2026-10-11 opencode_go 事故）。`aa_backend.backend()` 是
  **手写 alias 表**，认不出 `AA_BACKEND` 就返回 `"cloud"` → `aa_translate.call_api` 改走云端直接模式
  （`gateway=[default].gateway`、`model=AA_MODEL`），把**端点专属裸模型名**（如 opencode 的 `deepseek-v4.1-flash`）
  发给别家网关 → `400 unsupported_model`、每块重试 5 次后失败 → **整章静默留日文**（实测 3 作品 254 块全灭，
  93% 台词行没译）。触发条件很隐蔽：只在你**没显式**设 `AA_BACKEND`、由 runner `auto_backend()` 自动选链首时发生——
  换链首（或任何认证新端点进链）后必查这四处：`backend()` 别名表 / `aa_config.model_env()`（漏回 `AA_MODEL`，
  把端点专属模型名漏给云端直连）/ `_call_named()` 分发 / `selection._call_fn`。守卫用例
  `scripts/tests/test_opencode_go.py::TestChainBackendRegistrations`（遍历 normal/r18/划词链 catalog 断言四处齐备）。
  诊断口子：`call_api` 云端直连现在会在 stderr 喊一次「AA_BACKEND=X 未被识别 → 已回落云端直连」，
  且失败消息带网关正文与 `gateway=/model=`。

**旧坑（已修，别退回去）**：同一后端在链上出现两条（不同模型）时，**按后端名索引**的代码路径会重复或折叠**：
  预估/链上选择集（`estimate._chain_head` 的 `sel`）会出现同名两项、校对链模型表 `{后端:模型}` 折叠取后者，
  `test_model_choice::test_selection_changes_priced_endpoint` 直接用 `items[-1]["backend"]` 断言 ⇒ 直接红。
  改链前先 `grep -rn '"backend"\] in models\|models_sel\|{.*backend.*:.*model'` 找这类索引点；
  看到别人链上已有同后端两条时，别以为是自己改坏的，先 A/B 归因（`git show :<toml>` 暂存版跑该测试）。
- **校对链跟随付费链后，链首模型会决定「艺术假名被译坏」是否落地**：refine_aa 的 swallow 补译路径
  （`_tail_dial_seg` → `_call_glm`）会把**纯艺术假名段**当台词段送模型，`_looks_graphic_fragment` 拦不住
  （实测 `ミニニニニ`/`ヘニニニニニ`/`ﾆﾆﾆ` 这类）。同章 dry-run 对照（entry_10437）：mimo 33 编辑含 2 处艺术损伤，
  deepseek 32/3 无，deepinfra(V4.1-Flash) 12 处写入含 3 处损伤 + 1 处把 `与` 改回日文 `と`。
  → **换链首/换校对链后，拿一章艺术行多的章跑 `refine_aa --eids <eid>`（不带 `--write`）对比新旧链的编辑集**
  （`comm -23 <(sort new) <(sort old)`），别只看单测。回退：`[refine_aa] follow_default=false` +
  `backends="deepseek,deepinfra"`，或单次 `AA_REFINE_BACKEND=deepseek`。
- **思考参数适配先测再改**：不同端点/网关的关思考字段名不同，写错是**静默忽略**（表现为慢、token 高、偶发 content 空），不会报错。
  改完拿真函数计时验证（秒级 = 生效），并确认 `finish_reason` 不是 `length`。
- **摘除后端（余额耗尽/不可用）先看链上实际行为**：失效后端会白烧重试并污染日志——摘除后留注释写清原因，充值后可手动加回。
- **模型候选 `MODELS`/`PRICES` 是手工维护**：评估完把条目留在基准脚本里（下次对比直接复用），但报告文件在 gitignore 的 `report/` 下，不入库。
- **同模型多端点/多版本**：改链时把同一模型的不同端点相邻排列（评估/降级时好读）。但注意
  **端点轮询与缓存命中互相冲突**：`call_with_fallback` 健康态**每次调用**都轮询端点（注释「健直出不钉住」），
  而前缀缓存**以端点为界**（换端点=换机器=没缓存），且实测**只有官网 deepseek 会命中**，GLM 网关连续同前缀 0/0/0
  ——同一章的请求被打散到多家端点＝缓存收益归零（每章开头轮询一次没问题，每次调用轮询才是问题）。
  **已落地策略**：付费链 pin 目标 = 链上第一个 `[backend.*].cache = true` 的端点（**章内不轮询、跨章也不再分散付费负载**），
  网关只作降级/溢出；`AA_PIN_CACHE_ENDPOINT=0`（或 `[default].pin_cache_endpoint=false`）回退旧轮询；免费池走 `_call_free_chain` 不受影响。
  实测同章前缀 A/B：旧轮询 61.8% 命中（混入两家 0 命中网关，单次 4.7s/2.1s）→ pin 后 **8/8 全走官网、89.6% 命中、0.5~0.8s**。
  - **`reset_preferred_backend()` 要当「章边界清记忆 + 章首重钉」用，并把触发条件放宽到 legacy 作品**（`work_r18 is None` 时也重置）——
    否则上一章降级到网关的记忆会让整轮都钉在零缓存端点上。
  - **pin 会盖过显式 `AA_BACKEND` 的端点偏好**（它在整条链上找第一个 cache 端点）：临时要钉某网关时用 `AA_PIN_CACHE_ENDPOINT=0`，
    或把那家也标 `cache = true`。
  - **「只有官网会命中缓存」这条要定期复测**：2026-09-21 探针里 deepinfra 的 `DeepSeek-V4-Flash-0731`/`V4.1-Flash`
    在 usage 报了 `cache_hit=1536`（同批 GLM 恒 0），与当初 0/0/0 的结论不符——若属实，pin 目标可放宽到 deepinfra
    （cache_read 单价便宜约 30×）。**别凭单次探针改策略**，用本技能的 `scripts/probe_llm_cache.py` 做受控复测。
