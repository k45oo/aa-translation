---
name: aa-pipeline-optimization
description: Use when 优化/重构 AA 汉化管线（提速、省token、分类器改进、加质量保护）。
---

# AA 汉化管线优化与安全重构

本 skill 与 `aa-manga-translation`（运营 runbook）互补：后者讲"怎么跑管线"，本 skill 讲"怎么安全地改进管线"（不破坏生产、用数据证明收益再接入）。宝可梦链的「替换 → 判断还需不需翻译 → 直出」判据、固定行落点（整句→terms / 骨架→模板 / 说明→本作 wiki）、缺词导致的整行回落排查、假拒坑清单（art/宽度两处闸/`・`/引号外措辞/**译名自检拒→引号序号对齐**、**查表键归一**（假名↔汉字表记 `こうげき`↔`攻`、波ダッシュ↔全角チルダ `〜`↔`～`）、**短名子串≠整名**（自检只认引号内容整段相等））、按族验收流程（自带逐族计数探针 `scripts/verify_family_direct_output.py`、缺口归因 `scripts/attribute_gap_list.py`）、数据批次入场与生产开关落地见 `references/pokemon-fixed-content-direct-output.md`。送模型行集合审计（艺术行混入率基线 36% 行 / 38.5% 字符；探针 `scripts/audit_sent_lines.py`、方法与坑 `references/sent-line-audit.md`）与外部同类实现的**可抄清单**见 `references/external-aa-tools.md`。适用于 `<repo>` 项目。

- **cleanup/ 已删（2026-09-08, 344e56f6）**：pass2/pass3 薄转发层废弃——翻译链统一 refine_aa 后运行链零引用（touhou_runner `import pass2` 经 sys.path 运行时解析到 **common/**，与 cleanup 无关；历史 inventory 引用是别名归错的假象）。**权威实现 `common/pass2.py`/`pass3.py` 保留**（CLI 独立跑/历史）。gen_inventory CORE_FILES 已同步移除。教训：废弃目录前先验证「运行链 import」而非迷信 inventory 的 referenced_by——sys.path 注入下的 import 别名会归错。

## 给 runner 增删流程步骤：先查「隐式依赖该步骤」的上层入口

- **删 runner 内置步骤前，反向列出依赖它的入口**：`translate_runner` 曾内置 dl_runner 补缺；工作台「更新 → 下载并翻译」按钮（`/api/work/<id>/update` action=translate）只 enqueue 一个 `translate` 任务，靠这步取新章原文——删掉后新章**静默不译**：`aa_translate` 拿本地 `aa/*.txt` 清单与 `--pilot` eid 求交，缺原文的章不报错、直接从清单消失，任务照常显示「完成」。改动前后都 grep 调用点：`translate_runner|dl_runner` + `--no-dl` 类 flag 名，覆盖 `aa_web_app/`（API/enqueue 的 params）、`scripts/works/`、`templates/` 提示语。
- **task_manager 现已内置链式后续（`params["then"]`）**：`enqueue` 对同作品「排队/运行中」即拒绝，所以「下载完自动翻译」不能靠连发两次 enqueue——用 `params["then"]={"type":"translate","chapters":…}`，`_finalize` 末尾 `_chain_followup` 在 **rc==0 后**入队（停止/失败不入队；必须晚于 `_clear_legacy_lock` 否则旧锁挡住后续任务）。CLI 等价 `enqueue --then-translate`。实现细节与测试姿势见技能 aa-task-manager「链式后续任务」。
- **清步骤连带清死参数**：删被步骤带着走的 flag（变量、usage 串、docstring 一起删）。脚本不校验未知参数，旧 flag 留着也不报错——残留只能靠 grep 发现，别指望运行时暴露。
- **区分「清单拉取」与「下载调用」**：`load_entries` 里的 `import dl_runner` 只用于实时拉 yaruobook 分类页清单（非下载调用）。**但 2026-09-13 起只有 dl_runner 该实时拉**：translate_runner 的 `load_entries` 改为**只读本地 aa/**（顺序取 `chapter_order.json`，无则 eid 升序）——下载与翻译解耦后，`aa_translate` 只处理 aa/ 里存在的 txt，实时清单里「原站有而本地没下」的章根本译不了，白拉上千条。改回实时拉的代价：每次翻译多一轮网络请求且 `--chapters new` 选出一堆空转章。
- **文档同步范围**：`doc/接口文档.md` 由 `gen_interface_docs.py` 生成但只记 CLI 形态（`argv-manual`）不列具体 flag → 增删 flag 不必重跑；要手改的是 `AGENTS.md`、`scripts/README.md`、`aa_web_app/templates/*.html` 的提示语（`_inventory.json` 是 AST 静态分级，改函数/文件结构才重跑）。
- **测试结论用 grep 取，不用 tail**：`.venv/Scripts/python.exe -m unittest discover -s scripts/tests` 末尾还会打印质量闸门等其它 stdout 汇总，`tail -5` 看不到结论 → `| grep -E "^(OK|FAILED|Ran )"`。
- **发现连带影响先报告不动手**：删除步骤暴露的连带断点（如某入口依赖被删行为）属于新任务，按用户边界只报告+给方案与改动量估计，等确认再改第二个模块。

## line_meta（line_meta_v2）行口径：只有一种是对的（2026-09-13）

meta 的 key 是**落盘 txt 的 1-based 行号**，所以生成端必须用消费端同一口径：`open(aa/<base>.txt).read().split('\n')`（aa_translate / extract_dialogue 都这么读）。**绝不能用 `site.extract()` 重新提取当输入**——有的作品 txt 是旧版解析器/旧版原帖提取的，自解析虽自洽却与磁盘差几十行，生成出来整章行号错位（比缺 meta 更坏：错位的 kind 会喂给翻译）。旧 CLI（`tools/line_classify_v2.py --work`）历史上就这么生成的，存量里有一批 max key 比文件行数少 1 的错位 meta。

三个坑（同一类「解析端 ≠ 消费端」）：
1. HTML 里 `<br>` 之间夹的字面 `\n` 会被 `sites/_base.clean()` 保留，落盘时展开成多行 → 解析端必须用 `line_classify_v2._expand_row_lines/_expand_lines` 展开，否则解析行数 < txt 行数、整章被判「行数不齐」跳过（真・女神転生 243/1030 章无 meta、`extract_dialogue` 硬中断）。
2. 站点 `extract_article` 签名不统一（blog_2nt / aamatome_2nt / matomemo3 / mukankei_fc2 **需要 eid**）→ 一律走 `_extract_article_seg` 按签名传参；白头系 yaruonichijou 无该 API（非帖子型语料）→ 明确返回 None，别抛异常。
3. 多形态站点要钩子：yaruo_matome 旧站是 `.NNN ：` 的 `<p>` 流式（`parse_posts_old`）→ 站点提供 `parse_posts_auto(seg)`，classifier 优先用它。

**体检/修复**：`scripts/tools/gen_meta_batch.py --verify [--dry]` 校验「meta 覆盖 3..N 连续」，错位的默认删除（消费端回退 is_text_line，比错位安全）；`--missing` 只补缺。跨作品批量**默认 2 并发**——12789 章 × 6 进程曾把用户机器卡死，先 `--dry` 看量、小样本试跑再放大。

## 批量阶段的速度/成本取舍（简介生成这类上千次小调用）

**本机资源纪律（用户硬要求，违背后果严重）**：这台机器上用户经常**同时**跑着翻译/校对任务——跨作品批量脚本（meta 补刷、候选池、简介生成）必须**默认低并发（≤2 进程）**，先 `--dry` 看规模、小样本跑通再放大；上千章的批次开 6 进程会把用户机器卡死，任务会被当场叫停。自建批量工具时把并发数做成参数并将其默认值调低，不要在默认值里吃满 CPU。

- **批量低风险阶段走付费链，别开 `AA_FREE_MODE`**：实测同一批量任务免费链 0.85 项/分钟（免费链轮换 + 限流退避拖慢） vs 付费链 5.7 项/分钟（≈10-12s/项，链首 `zai-org/GLM-5.3-Flash`）。千项量级差 6 倍以上、成本只有几毛钱——免费额度该省的是正文翻译的钱，不是批处理。判读进度时也别拿免费链速度当基线，否则会把正常的付费批量误判成假死。
- **只跑单个阶段、不带动其他阶段**：想要简介就直调 `python scripts/common/gen_chapter_summaries.py --work <id> [--chapters entry_a,entry_b] [--force]`——走 translate_runner 会把 prep/翻译/清理/校对一并触发（runner 已不再自动补下载：缺原文章会静默不译，先跑 dl_runner）。CLI 直跑没有任务管理器的心跳与停止标志（要停只能 kill），所以该类阶段必须自带**逐项原子落盘**，保证重跑同一条命令即可续跑。

## 术语提取两段式（候选池 → 模型筛选，2026-09-07 benchmark 定稿）

`gen_glossary_ai --full` 的模型提取改成了**候选池两段式**，不是"整批台词丢模型"（旧法在长文本里大海捞针、空批率高）：
1. **本地候选池（零 API）** `build_term_pool.py`：Janome 分词 + CompoundNounFilter 复合名词合并 + 词频排序 + roles/known_names 差集排除 + FUNC_WORDS 过滤 → `term_pool.json`（默认 top200 候选）。旧 kana_runs 只提纯假名段，汉字专名全漏；Janome 对赛马娘式复合专名（オルフェーヴル/ダイヤモンド/グラスワンダー）切得准。
2. **模型分批筛选** `screen_term_pool.py`：每批喂 N 候选词，模型判"是否真专名 + 给译名" → 输出 `term_pool_screened_*.json`。
3. **参数 benchmark 定稿（不用每作重测）**：topN×每批词数 矩阵 → **top 200 × 每批 20 词**。趋势：每批 20 占比最高且稳（17→22%）；每批 80 模型明显退化（0%/5% 空响应）——淘汰；topN 增大有效专名近线性增长无饱和，200 是性价比甜点。译名质量抽查：官方中文译名命中率高（赛马娘等），误筛（普通名词当真专名）极低。
4. **接入**：`gen_glossary_ai --full` 主分支直接走两段式（extract_full_pool）；产物 glossary_candidates.json + 知识库待确认区；工作台「生成术语候选」按钮触发。

**长任务中断教训（实测多次）**：本地后台 subprocess 每 ~2 分钟可能被外部机制无 traceback 强杀（SIGKILL，日志停在某批、无 exit=、非内存/非任务自身错误）。对策：
- **分批持久化** pool_progress.json（done_batches + cands 增量，每批写完）→ 中断不丢批、重跑幂等续跑。
- **守护自动续跑** `run_full_pool_until_done.py`：循环 spawn gen_glossary_ai，共享 pool_progress 续跑，直到 candidates 生成（第一轮无条件跑，后续轮以 progress 是否清为完成判据——清=正常完成，留=续跑）。
- **严禁并发多实例**写同一 pool_progress（互相覆盖回退）——同作品只跑一个。
- **数据准备（补台词）必须拆独立阶段，勿与模型筛选同轮**（2026-09-07 class_teni 事故）：`--full` 候选流程只读 `aa/<章>_台词.txt`（build_term_pool 137 行 `endswith("_台词.txt")`），缺台词章直接漏掉候选。若补台词（`_ensure_dialogues`）和模型筛选在同一进程/任务里，后台 ~2min 回收中断时补台词也在中途被杀 → 候选基于残缺语料（class_teni 曾 142 原文只补出 26 台词）。**解法 = 拆独立幂等阶段**：`gen_glossary_ai --ensure-dialogues-only`（`_missing_dialogues` 幂等判定缺失并补齐，0=全齐/9=有缺），`run_full_pool_until_done` 进候选筛选前先循环补台词到全齐再筛选——补台词不受模型筛选中断影响（每轮幂等补缺，缺则续跑）。
- **registry work id ≠ 目录名**：id 是 `class_teni`（英文），aa 目录是日文 `aaManga/よくあるクラス転移物イージーモード`——用 id 查 cfg 拿 `work_dir`/`aa_dir`，勿拿日文目录名当 id（`registry.get` 会「未知作品」并列出全部可用 id）。
- 前端进度条消费新日志格式：`[pool] 批X/Y: 输入N → 专名M (累计 K, 已存progress)`（status API 需兼容新旧两种格式）。

## 项目真实结构（**已并入仓库文档，勿在此重复维护**）
- 目录/脚本分级权威：`scripts/README.md` §1（结构树）+ `scripts/_inventory.json`（分级）+ 根 `AGENTS.md`「目录结构」。
- 本技能只保留「流程/坑/实测」，结构性问题一律查上述三处（原 2026-08-10 勘误段已过时：download/extract/translate/generate/experiments 五目录早已删除、脚本全进 legacy）。
## 目录约定（用户明确指定）

- **临时文件/实验产物 → `<repo>\temp\`**（不入 git）
- **报告/文档 → `<repo>\doc\`**
- 实验记录统一写 `EXPERIMENTS.md`（目标/命令/实测数据/差异/是否合入 + feature flags 状态 + 里程碑勾选）

## 文档归档约定（2026-08-12 清理后定型）

用户要求**归档文件只读 + 建索引**便于后续寻找。当前 doc/ 结构（清理后权威版）：
- **根目录只留长期追踪文档**：`同步记录.md`（持续追加）+ `多智能体协同方案_2026-08-10.md` + `索引.md`（查找入口）
- **归档目录（全部只读）**：`翻译优化验收归档/`（阶段一，25 文件）、`阶段二翻译优化归档/`（阶段二，9 文件）——各含 README + 子目录（总结/PRD/实验报告/分类器优化/同步记录）
- **索引.md** = 活跃/归档分类表 + 查找指引 + 归档约定，后续找文档先看它

操作要点：
- 归档副本须先**内容核实一致**再删根文件：`diff <(tr -d '\r' < 根) <(tr -d '\r' < 归档)`——CRLF 差异会导致 md5 不同但内容实质一致；改名归档文件（审阅报告去日期/PRD加_v1.0）逐项 diff 确认无内容丢失
- `chmod -R a-w 归档目录` 设只读；需修改先 `chmod -R u+w` 解锁（索引.md 有此提示）
- **归档时保留长期追踪**（同步记录.md 归档版是旧备份，根目录活跃版必须留）
- Obsidian 本地工作区 `doc/.obsidian/workspace.json` 入库前 `git rm --cached` + gitignore（勿删实体文件）

## 结构维护约定（2026-08-10 scripts 分类整理后强制）

**`scripts/README.md` 是 scripts/ 结构的唯一权威说明**（含目录总览、核心规则、各目录脚本明细、管线命令、维护约定）。任何结构变更（新增/移动/删除/重命名脚本或目录）必须同步三处：
1. `scripts/README.md`（更新总览 + 明细表）
2. `AGENTS.md`（目录结构 + 管线命令章节）
3. 本 skill 的「项目真实结构」段落

## 免费模式（AA_FREE_MODE=1）全流程免费覆盖

**机制**（2026-08-26 落地；实现细节见 aa-free-model-rotation skill）：- `AA_FREE_MODE=1` 时所有 `call_with_fallback` / `call_api` 短路到 `_call_free_chain`——**翻译/校对/清理/漏译补译/术语提取全走免费链**（无 R18 区分，全免费）
- **视觉例外**：免费视觉模型均不可靠（ox-alpha 下架、laguna/minimax 视觉不可靠）→ **视觉一律 Qwen3-VL（付费）**，`call_qwen_vl` 无免费分支（2026-08-27 移除死代码 `_call_free_vision`）
- **⚠️ 超时也算可降级错误（2026-08-28 修复，yukika_robot 崩溃教训）**：`_degrade_reason` 原只认 429/截断/R18拒绝/空响应，**不认 timeout** → commandcode 网关超时（read operation timed out，重试3次全失败）后 `call_with_fallback` 直接 raise，不试链上 mimo 兜底 → 进程崩溃（30 章翻译完成但清理/校对/iter/合集全没跑）。修复：加 `timed out/timeout/超时` 分支 → 降级换下一候选。**教训：任何"某后端全链失败导致任务崩"事故，先查 `_degrade_reason` 是否漏了该失败类型**（AGENTS.md「非降级错误立即抛」已过时）
- **并发**：`AA_FREE_WORKERS`（默认3）多模型并发——`_call_free_chain` 用 `_free_round_robin` 轮询指针，不同 worker 从不同模型起步 → 天然分散到多平台并行（各平台限流独立）
- **免费模式忽略 AA_BACKEND 显式指定**：translate_runner 与 aa_translate 在 `AA_FREE_MODE=1` 时**不设 AA_BACKEND**（避免日志误导 + 防意外回退付费）；iter_glossary 的 `--backend` 参数同理会覆盖免费链 → **免费模式忽略 backend 参数**（2026-08-27 修复）
- **免费模型内容被拒 → 返回空串按漏译跳过**（不重试不崩章，后续补译环节处理）——`_call_free_chain` 检测中文拒绝词（无法/拒绝/不能协助）；**日文拒绝语（如「言えません」）检测不到会当正常翻译返回造成漏译**（GMI 坑，见下）

**免费链模型当前态（2026-09-05，15 条通道）→ 独立技能 `aa-free-model-rotation`（aa-translation 分类）**：链序/退役清单/目录轮换 SOP/假死排查/双 key 接入全部集中该技能，本节省略。历史沿革（08-27 三平台限流独立 → 08-29 三元组双 key → 08-31 扩 14 → 09-05 轮换 15）与新模型实测见 aa-free-model-rotation + doc/线上免费模型统计.md。旧链序（9/14 通道、hy3-free/m3-free/GLM-Z1）作废勿引用。


**GLM-Z1-9B-0414 假死元凶（2026-09-01 定案，已出免费链）**：`enable_thinking=false` 对 deepseek-r1 类推理模型**不生效** → thinking 混入 content + 超长生成（17246 token/20min）→ 单请求挂死、worker 占满、进程 CPU=0 假死。**判定法**：`usage.jsonl` 里 grep task_id 看每个任务**最后一条 usage 的 model**，>5min 无调用间隔若全在某个模型前后＝它挂的。**三层修复**：① 删 Z1 两行；② `_call_free_chain` 加 `FREE_MAX_ROUNDS`(默认 5，连续全败抛错释放 worker)；③ `socket.setdefaulttimeout(300)` 兜底 CLOSE_WAIT 半开连接。**通用教训：关不掉思考的推理模型是假死高危**，接入前先探单请求 content 是否干净。细节（时间线/证据/为何 B2 回退也救不了）见 `references/free-model-platforms-2026-08.md`

**第二根因：finish_reason=length 截断被当成功（2026-09-01 补记，GLM-4-9B 也中招）**：移除 Z1 后 bar_miku 仍假死——`usage.jsonl` 里 `completion>=16000`（=max_tokens=16384 打满截断）的调用被当正常返回：`call_siliconflow` 只取 content 没查 `finish_reason`，半截译文 → parse_result 解析失败 → 块失败重试退避 → 假死。**修复：call_siliconflow / call_openrouter / call_opencode_zen 三处加 `finish_reason=="length"` 检测抛异常**（走重试/换模型）。**改代码只对新启动任务生效**——正在跑的旧进程已加载旧 FREE_MODELS/旧函数，kill 重跑才吃新配置。**诊断口诀**：免费任务反复假死 → ①grep task_id 看最后一条 usage 的 model ②看 completion 是否 ≥16000（截断）③改完代码必须 kill 重跑验证（看 usage 里新任务无 Z1/无截断）。

**新后端接入模式（GMI 实战补充）**：新平台测试后**先确认内容过滤再入链**——测 R18 台词 + 粗口（「この野郎」），且**拒绝语可能无中文关键词**（GMI 的「言えません」检测不到 → 当正常翻译返回 = 静默漏译，比显式拒绝更糟）。内容过滤强的模型**宁可不入链**，保留 call_<名>() 供手动用。

**2026-09-02 补充（MiniMax 官网 M3 / runinfra Nemotron-3.5-Lightning-30B 实测定性，完整记录 doc/翻译模型对比.md）**：
- **粗口通过 ≠ R18 安全**：M3 粗口（ぶっ殺す）/童貞/ケツ穴级全过，但「ちんこ/中出し」级**性器官词必 HTTP 422**（内容过滤，3 次重试后抛错走降级）——探针必须含性器官词档，只测粗口会漏判
- **422 = 网关输入侧敏感度累计检测（2026-09-02 深挖定案，破限无效）**：`input new_sensitive (1026)` 发生在请求进模型**之前**——破甲/破限/DAN 类提示词（对付模型侧拒绝）对此**无效**（模型根本没被调用）。实测规则（temp/probe_mm3_jailbreak.py + precise + prod + final 四连探针）：①**按整块累计浓度触发**——单敏感词（ちんこ/中出し/挿れる/シャブ漬け）单独发全过，6 行全 R18→422，40 正常+2 R18→过，40 正常+6 R18→422；②**输出侧几乎不检**（通过后模型直出「舔屌/阴茎」照过）；③**system prompt 本身不检**（含「成人向/露骨」也 200，勿把整块 R18 输入的 422 误判为 system 触发）；④真实作品擦边词（童貞/処女/おっぱい）大量也不触发→**真实章节浓度（50行块 1-4 行 R18）原词直接可用，无需任何破解**；⑤罗马音变形输入（chinpo/nakadashi+暗语表）可行但属规避平台审核**有账号风险不推荐**；⑥**更现实的生产障碍=M3 对 mixed 行（图形+右侧台词）输出不稳**——输出「（省略装饰字符）」占位符或原样照抄→触发补译/review 浪费 token，接入前必须验证混合行表现；⑦国际端点（api.minimax.io 等）国内 key 401 需国际版账号
- **422 指纹定性法**：同批含 R18 词 422 + 纯日常同长度全过（M3 纯日常 42 行 3.4s 全对假名 0）= 内容过滤而非长批/格式问题
- **历史「顽疾」可能被官方修复**：M3 曾记录「长批角色名保留日文」（2026-08-28，40行假名68），2026-09-02 复测纯日常长批全对假名 0——接入前旧黑名单结论须复测，勿直接引用
- **长批漏行+照抄=硬伤不入链**：Nemotron 长批漏末尾行[37-42] + 8 行整行照抄（两轮一致）+ 短批角色名假名残留 10 + 同任务 2.6s/49.3s 速度不稳 + runinfra 端点偶发 10054 连接重置——费率再便宜（$0.05/$0.15）也不入，进链即大量补译/人工返工

## 翻译完成但清理/合集没跑的中断恢复（2026-08-28 akari_haisin 34话）

**症状**：翻译子进程(capture_output)全部完成后 translate_runner 静默退出——tee 缓冲丢失、日志只到 `=== 翻译 (aa_translate) ===`，runner_log 停在最后一章 M4b 补译，交付HTML 无本作合集。

**判别（别误判成 bug）**：汉化/*_汉化.txt 全在（34/34）+ ckpt 时间戳到最后一章 + 但无 scan_width/pass2 输出/无合集 = 中断。

**恢复标准路径**：`python scripts/runner/translate_runner.py --work <id>` → 自动进 only_cleanup 模式（日志 `无待翻译章节；对已译 N 话仅做清理`），补跑定向 scan_width/fix_width/角色名残留扫描（**合集一律手动**：`common/gen_aa_html.py --work <id>` 或工作台「重生成合集」按钮），**已译章不进模型重译**。这是中断续跑的标准路径，不是 bug。

**⚠️ only_cleanup 跳过校对该技能前文称会补跑**——错。`translate_runner.py` 的 `if not only_cleanup:` 同时门控 **iter_glossary 与校对两段**，only_cleanup 模式下**iter_glossary 不跑（校对跑——当前实现 `refine_aa` 不受 only_cleanup 门控，2026-09-07 起；仅处理本次章节 eid）**（合集一律手动，runner 无 `--gen`）。**校对默认启用（2026-09-07 起）**：翻译链清理 = scan_width → fix_width → `refine_aa`（`--no-proofread` 才禁）；旧 `proofread.py` 已废弃（词级替换顶歪 art 段事故）。单独补跑走任务管理器 proofread 类型或 `python scripts/fix/refine_aa.py --work <id> --write`（前置：确认 `_台词.txt` 在汉化目录、且 `aa/<章>_台词.txt` 已用 `export_dialogue --src` 生成——只有其一 `proofread_chapter` 会返回 None → JSON 空 list）。

**清理阶段慢≠卡死**：pass2 每章 still 行补译 1-2 分钟/章（34 章 pass2 约 40 分钟，pass3 更久），runner_log 停在某章十几分钟可能只是 still 补译慢。**判断活性**：看 `usage.jsonl` 尾部时间戳是否持续更新（模型还在调用）或 ckpt/汉化文件 mtime 是否推进，别只看 runner_log 停在哪章就杀进程。

**high 作品清理阶段实际后端**：high 清理设 `AA_R18=1` 但**不设 AA_BACKEND** → call_api work_r18=True → backend()=cloud → **走云端直接模式 mimo-v2.5**（r18=true 无审查，能用），不是配置的 r18 链 muse-spark 首选。usage.jsonl 里 `tag=translate, model=xiaomi/mimo-v2.5, backend=cloud` 是清理阶段正常现象。

## 免费翻译任务假死监控（2026-08-28 r401_ryouri 实战）

**症状**：后台 translate_runner 进程活着（tasklist 在）但 ckpt 2 小时+ 无更新、两次 wmic CPU 时间采样（KernelModeTime/UserModeTime）完全不变 = **假死**（某 API 调用无限挂起），不是正常翻译大话（大话 ckpt 会持续刷新）。

**多任务并发场景强化诊断（2026-08-31 r401_kizoku 实战）**：任务管理器下进程链 = task_manager → translate_runner → aa_translate(`--pilot`)。查假死按链逐层看：
- ① **心跳 `.hb_<tid>.json` 的 `hb_ts` age**（`aa_web_app/data/tasks/`）——心跳 1s 节流+phase 变化才写，**心跳停 ≠ 假死**（翻译中长 chunk 可长时间不写）；**心跳停 + ckpt 停 + 0 章完成 = 假死**。
- ② **最底层 aa_translate 子进程 CPU**：`powershell Get-Process -Id <pid>` 看 `CPU` 秒数——**从启动累计 CPU < 1s（如 0.015s）即彻底挂起**（阻塞 I/O 等待，非计算）；进程链用 `Get-CimInstance Win32_Process -Filter "ParentProcessId=<pid>"` 逐层找。
- ③ **`netstat -ano | grep <pid>` 看 socket 状态**：`CLOSE_WAIT`（连接已断未释放）＝卡死在已死连接上的确凿证据，请求永不返回 → 无限挂起。
- ④ **关键判别：看同模式并行任务**——若另一个 AA_FREE_MODE 任务 ckpt/心跳正常推进（如 idolmaster_yaruo 13:22 还在写 ckpt），**免费链本身没死**，卡死是单个任务的偶发挂起（R18 作品 + 某免费端点请求挂死）→ 直接 kill + 重入队续跑（ckpt 断点零损失），不用怀疑免费链配置。
- 处理：`task_manager.py kill <tid>` 杀链 → 重新 enqueue（同 chapters + 同 env），ckpt `__lines` 指纹自动续跑。

**判定三步**：① 最近 ckpt mtime 年龄（`max(glob('work/*.ckpt.json'), key=os.path.getmtime)`）≥2h；② `wmic process where "name=python.exe" get ProcessId,CommandLine` 确认 translate_runner 在跑；③ 10s 间隔两次 wmic CPU 时间对比不变。**处理**：`taskkill /F /PID` + 原命令重启（ckpt 断点续译零损失，启动日志"待处理 N 话"自动跳过已完成）。

**监控脚本（已部署，通用参数化版）**：`<hermes>\profiles\translation\scripts\monitor_aa_translate.py`——no_agent cron 每 2h 跑（`cronjob action=create no_agent=true script=monitor_aa_translate.py schedule="0 */2 * * *" deliver=all`）：
- **⭐ cron script 不能带参数（0.20.5 实测，2026-08-28 踩坑）**：`_validate_cron_script_path` 把整个 script 字符串当**单个文件名** resolve，`argv=[python_exe, str(path)]` 只传路径无参数拆分。传 `"monitor.py --work r401_ryouri"` 会报 `Script not found: <scripts>/monitor.py --work ...`（整个串当文件名）。**必须无参纯文件名**，参数由同目录 `aa_monitor_targets.json` 提供（`{"targets":[{"work":"r401_ryouri","cron_job":"baf5a42e7c7f","total":0}]}`）。验证：`hermes cron run <id>` 返回 `Ran now: succeeded`、executions.db 最新记录 status=completed 无 error
- **用法**：`python monitor_aa_translate.py --work <id> [--total N] [--cron-job <job_id>]`；`--total` 缺省从 chapter_order.json 读；`--cron-job` 传自己的任务 id
- **假死（ckpt≥2h）→ 杀 translate_runner + DETACHED 重启**（creationflags 0x8|0x200），重启命令固定 AA_FREE_MODE=1 AA_FREE_WORKERS=6
- 完成判定 = 汉化文件数 ≥ chapter_order 条数（**正式章节数口径，不是 aa/ 文件数**——aa/ 常有杂谈/支援图碎片不参与章节，用 `chapter_order.json` 的 order 长度）
- 正常 → 报进度 `✅ 正常: <eid> ckpt Nmin 前更新, 进度 6/39`
- **完成后自动 pause 自己（2026-08-28 用户问"翻译完成会自动停止吗"后实现）**：完成分支调 `hermes cron pause <cron-job>`（`HERMES = <hermes>\hermes-agent\venv\Scripts\hermes.exe`）——不会每 2h 反复报"完成"。**模拟验证法**：`--total <当前完成数>` 临时触发完成分支测自动 pause（实测输出"已自动暂停"），测完 `cronjob action=resume` 恢复
- **cron 鸡生蛋坑**：任务创建后 job_id 才生成，script 里先写占位 `--cron-job JOB_ID` 会留下假 id——创建后用 `cronjob action=update` 把 script 回写成真实 job_id
- 坑①：**wmic 输出是 GBK 编码**，`capture_output` 默认 utf-8 解码抛 UnicodeDecodeError 被 except 吞掉 → runner_up 误判 False 报"进程不在"。必须 `.decode('gbk', errors='replace')`
- 坑②：进程检测失败但 ckpt 新鲜（<30min）时**不要重启**（`elif not runner_up and age_min > 30` 才报"可能退出"）——避免把正常翻译误杀
- 坑③：**监控脚本不引 registry**（registry 依赖同目录 paths 模块，单独 exec 会 ModuleNotFoundError）——用硬编码 KNOWN 作品目录映射 `{work_id: 相对aaManga路径}`，新作品手动加一行
- 坑④：`cronjob list` 带 `gateway_running:false` 警告 = 任务已保存但**不会按时触发**（TUI 会话 cron 挂在 gateway 进程上）；gateway 起来后 deliver=all 才投递到已连接平台。**2026-08-28 确认**：`hermes gateway start` 启动后 cron 生效
- **新作品启用流程**：① KNOWN 表加一行目录映射 ② `cronjob action=create no_agent=true script="monitor_aa_translate.py --work <id> --cron-job JOB_ID占位" schedule="0 */2 * * *" deliver=all` ③ 创建后 `cronjob action=update` 回写真实 job_id ④ gateway 运行时自动生效

## 翻译前模型预判（**口径已并入仓库文档**）
- 现行链/端点/兜底语义 → 根 `AGENTS.md`「现行模型链」+「付费链端点策略」；模型实测/单价 → `doc/翻译模型对比.md`；免费链 → 技能 `aa-free-model-rotation`。
- 回答「这章会用哪些模型」时**读 TOML 现算**（`[default].normal_backends` / `r18_backends` + 章级 R18 判定），不要凭本文的历史快照（2026-09-02 的 sf/zhipu/muse-spark 版式早已作废）。
## 校对模型链（**2026-09-22 起跟随付费链**）
- 现行：`refine_aa` 链 = `[default].normal_backends`（作品 r18_level=high → 按 `r18_backends` 过滤），env `AA_REFINE_BACKEND` 覆盖；旧 `proofread.py` **已废弃**（词级替换顶歪 art 段事故），漏译清理统一走 `refine_aa`。详见根 `AGENTS.md`「refine_aa 模型链」。
- 历史「校对专用独立链（不走 normal 链）」的设计与实测留档：`references/refine-pass-design-2026-08-29.md`。
## 新平台模型探测的网关级内容过滤（2026-09-02 多平台实测，必读）

**⚠️ 「粗口通过 ≠ R18 安全」已升级为「网关输入侧预检」**：MiniMax 官网 M3(422 new_sensitive)、小米官方 mimo-v2.5/pro(high risk)、tokenrhythm 网关 mimo、glm-5.3-flash(显式拒译) 全部实测——**探测必须含性器官词档（ちんこ/中出し/挿れる/シャブ漬け），只测粗口/擦边（童貞/おっぱい）会漏判**。

- **内容过滤在模型被调用之前**（M3 422 / mimo high risk / tokenrhythm 同源）——破甲/破限/DAN 提示词（对付模型侧拒绝）**全部无效**（模型根本没收到请求，网关预检不看 system）。**勿把时间浪费在破限提示词上**（M3 曾花 4 组探针验证：罗马音变形可行但有账号风险不推荐）
- **触发阈值分级**（同一机制不同平台）：M3 = 整块敏感词累计浓度（单发硬词 200，42行块≥6 硬词 422）；miMo 官方/代理 = **硬词单发即拒**（擦边词混正常通过）——探测时用浓度梯度（纯正常/正常+2擦边/硬词单发/全 R18）定位真实生产可用性
- **代理网关≠解除审查**：tokenrhythm 转发 mimo 审查与官方端点一致（只是延迟高 10-20s）；但 tokenrhythm 长批是先输出正常行再中断（按块检测），官方是整批预检——对生产无意义（硬词章仍必断）
- **同模型不同入口审查可能不同**：commandcode 网关的 `xiaomi/mimo-v2.5`（r18 链在用）无审查，小米官方端点 `api.xiaomimimo.com` 的 mimo-v2.5 硬词单发即拒——**AGENTS.md/TOML 的 `r18=true` 字段只指「配置未标审查」，不代表官方端点实际行为，接入前必须实测**（mimo 的 r18=true 描述已过时）
- **R18 作品实际可用性以真实章节浓度为准**：M3 真实作品擦边词大量不触发，硬词密集章靠降级链兜底
- **文档**：付费模型测试记录归属 `doc/翻译模型对比.md`（2026-09-02 建），免费模型在 `doc/线上免费模型统计.md`；探针脚本 temp/probe_*.py 可复跑

## 新平台端点发现法（tokenrhythm 实战，2026-09-02）**

- 官网主域（tokenrhythm.ai）只是 landing，真实 API 在控制台子域（tokenrhythm.studio）——**先 curl 首页 grep href 找 login/register/console 子域，再对子域试 /v1/models、/api/v1/models、/docs**
- SPA 站（React/SSR）内容在 HTML 里直接 grep `https://.../v1/chat/completions` 等完整 URL（`tokenrhythm.studio/docs/api-integration` 的 HTML 含端点）；JS bundle 里可能是相对路径难找
- .env 已有 `TOKENRHYTHM_API_KEY` 但无代码接入——**测试新平台先确认端点+模型列表（/v1/models），再手写 urllib 请求（带 key）而非改生产 aa_backend**；模型列表 20 个含 deepseek-v4-flash-0731/glm-5.3-flash/qwen3.7-flash/mimo-v2.5-pro 等
- **tokenrhythm 三模型实测全不可用**：deepseek-v4-flash-0731 长批漏末尾行[37-42]；glm-5.3-flash R18 显式拒译（比 zhipu 1301 更硬）+ 长批 504 + 16-48s/批慢；qwen3.7-flash 首请求后全空响应（reasoning_tokens 远超 text 疑似推理+网关超时）——端点稳定性问题，平台修复后可重测

## DashScope/阿里云千问系列实测要点（2026-09-02）

**端点**：`https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`（OpenAI 兼容）｜ **key**：`QWEN_API_KEY`（translation/.env，sk-ws- 开头，116 字符）｜ 完整记录 doc/翻译模型对比.md

- **必须带 `enable_thinking: false`**：qwen 系默认开思考（qwen3.5-plus 实测 80s/4638tok）→ 关后 4s/198tok（**23x 提速**）——探针不关思考会拖到天荒地老
- **接入后端（2026-09-02 已落地，qwen3.5-flash 入 normal 链）**：`[backend.dashscope]`（url=兼容端点, model=qwen3.5-flash, key_env=QWEN_API_KEY, **r18=false**）；`backend()` 别名 `dashscope/ds2/aliyun/qwen-cn/阿里`；`model_env("dashscope")→AA_QS_MODEL`；`call_dashscope()`（enable_thinking:false + **限输入 AA_DS_MAX_INPUT 默认 128000**——qwen3.5-flash 的 max_input_tokens=129024≈128k，超限抛错走降级链不硬发撞 429，token 估算 `(len(system)+len(body))//3`）；DashScope 必需 UA header 否则 403。**⚠️ 常量前缀坑（本会话踩过）**：`DS_URL`/`DS_MODEL` 已被 deepseek 后端占用（模块级同名，后定义覆盖前定义 → dashscope 的 call 会发到 deepseek.com 拿 QWEN key 返回 401）——**dashscope 常量必须用 `DSC_URL`/`DSC_MODEL`（DS_ 系列留给 deepseek）**。qwen3.5-flash 仅入 normal 链（r18=false 输出审核），r18 链不含 dashscope（aa_config.backend_chain 的 r18_safe 过滤自动处理）
- **R18 全 13 模型一致拒绝**：HTTP 400 `DataInspectionFailed: Output data may contain inappropriate content` = **输出侧审核**（模型译了但输出被拦）——比 zhipu 1301 更严，无法绕 → DashScope 只适非 R18
- **qwen-mt 系列（机器翻译专用）不支持 system 角色**：400 `Role must be in [user, assistant]`——带 system 必挂；无 system 时输出解释性内容（「このように…」）不守行号格式 → 不适 AA 管线。同批失败里「日常也 400」先查是否参数/角色问题，勿误判内容审核
- 最佳候选：qwen3.7-plus / qwen3.5-flash / qwen-plus（关思考后 1-11s/批、长批 45/45 不漏行——**DashScope 长批明显比 deepinfra/tokenrhythm 稳**）；qwen3.7-flash 长批偶发 0/45 空响应（0.7s tok703 无内容）；qwen-flash/qwen3.8-flash/qwen3.5-27b 角色名假名残留
- qwen2.5-omni-7b **不在模型列表**（旧名/下架），列表 250 个模型用 `/v1/models` 拉

## deepinfra 定价获取 + V4-Flash-0731 长批漏行定案（2026-09-02）

- **定价获取坑**：`api.deepinfra.com/v1/models` 带 key 的 `metadata.pricing` 对免费账户**全 0**（不暴露）；单模型页/Next data 路由（`/_next/data/<buildId>/...`）对非 featured 模型 fallback 同一通用目录页（245759B 恒等）——**SSR 只预埋 23 个 featured 模型的定价**（字段 `cents_per_input_token`/`cents_per_output_token`/`rate_per_input_token_cached`，换算 `$/M = cents × 1e6 / 100`）；非 featured 定价需逆 JS 运行时 API
- **DeepSeek-V4-Flash-0731 长批漏末尾行 [37-42] = 模型级通病**（deepinfra + tokenrhythm 双平台一致，12.9s/42行 与 6.9s 都漏同 6 行）——非端点问题 → **不适合 AA 长块（50行/块）翻译**（实证解释为何从未进 AA 链）
- **「更便宜且可用」判定看 output 价**：V4-Flash-0731 = $0.08/$0.18（out 全站文本模型最低）；比它 input 便宜的不成候选——GLM-4.7-Flash $0.06/$0.40（实测 26-68s/批极慢 + 成本 2.2x 基准）、gemma-4-26B-A4B $0.07/$0.34（快但校对检出 0/3、长批同漏）

## 付费链端点策略（**2026-09-15 起改为「章内 pin 缓存端点」**）
- 现行口径见根 `AGENTS.md`「付费链端点策略」：`call_with_fallback` 章首把 `_PREFERRED_BACKEND` 钉到链上第一个 `cache=true` 端点，本章内不轮询（`AA_PIN_CACHE_ENDPOINT=0` 回退轮询）。
- **均匀轮询是 2026-09-02 的旧实现**，其语义/实现细节与网关内容过滤矩阵见 `references/paid-chain-endpoint-roundrobin-2026-09-02.md`（历史，勿当现行）。
## 本地模型 / 免费模型（**已拆分为独立技能**）
- 免费链轮换、免费池现状与踩坑 → 技能 `aa-free-model-rotation`；模型横向实测与单价 → `doc/翻译模型对比.md`；免费平台清单 → `references/free-model-platforms-2026-08.md`。
- 本地 Ollama 已停用（根 `AGENTS.md` 后端列表标 `ollama(已停用)`）。
## 核心方法论：实验副本先行 + feature flag + 数据门控

任何改动不直接改生产脚本，遵循：
1. **实验副本**：`import pass3` 后 monkeypatch 模块级全局（`OUT_DIR`/`WORK_DIR`/`REVIEW_FILE`），在副本目录上跑，绝不污染生产 `汉化/`。
2. **feature flags 两段式语义**：
   - **开发期默认 0**（`AA_PASS3_PARALLEL` 等）：`env=="1"` 显式开启，跑通 + 回归通过后才上线。
   - **上线翻转后默认开**（`AA_USE_LINE_META`/`AA_USE_GLOSSARY`/`AA_PASS3_PARALLEL` 三 flag 均已翻，2026-08-10）：改为 `_flag_on(name, default_on=True)`，未设 env 即生效，显式 `=0` 关闭做回滚。统一 helper 避免多处理判断重复：
   ```python
   def _flag_on(name, default_on=True):
       v = os.environ.get(name)
       if v is None: return default_on
       return v not in ("0","false","False","")
   ```
3. **数据门控**：每项改进用对比实验量化（墙钟、token、diff 行数、漏译风险），达标才接入。未达标 → 诊断根因 → 迭代，不强行接入。

## 提速：pass3 文件级并行化（已验证 2.53x）

**瓶颈定位**：pass3 每话残留块仅 1-3 条，`ThreadPoolExecutor(6)` 块内几乎无并行；真实瓶颈在**跨文件串行**。五话脏基准 126s（≈25s/话）线性放大与全量一致。

**正确设计**：
- 只并行**文件间**（`ThreadPoolExecutor(workers=4~6)`），**单文件内部轮次保持串行**——收敛逻辑（连续2轮残留不降才 break）依赖轮间状态，并行会破坏正确性。
- 每文件独立处理 + 原子落盘；`--batch` 分片支持后台/notify。
- **429 自适应降并发**：记录 `_429_COUNT/_CALL_COUNT`，>10% 时 workers 4→2→1。

**对比实验**（`scripts/experiments/exp_pass3_compare.py` 同脏副本串并行）：串行 220.2s → 并行 86.9s，加速比 2.53x，逐话 diff 0 行（产物完全一致）。

**生产回归（翻默认开前必须做）**：并行 flag 上线前需跑 1 次全量生产回归确认 429 率。实测（kikkoman 60 文件）：总墙钟 449.4s、API 152 次、**429 率 0%**、review 33 行 → 无并发限流风险，才翻 `AA_PASS3_PARALLEL=1`。回归后立即重跑 `quality_gate.py` 验证 60 话全 OK/宽度0/残留0。

**验收口径**（重要）：不要用"逐行零差异"当唯一标准（模型非确定性会误伤）。分**硬性一致项**（行数/宽度错位0/非白名单残留0/人工行hash不变/元数据不变）必须全过 + **软性一致项**（普通译文允许差异，须出报告，禁止新增残留/艺术变化）。

## 质量保护：人工修复不可覆盖（P0-C）

**问题**：fix_width/pass3 把"模型输出原文（宽度恰好匹配）"当成功，覆盖已人工修好的行（历史多次事故）。
**方案**：`manual_fix.json` = `{filename: {line_no: {text, hash(sha256[:16]), ts, reason}}}`。
- 统一入口 `record_manual_fix(work_dir, filename, line_no, before, after, reason)`：`before.strip()==after.strip()` 跳过（无实际改动）。
- 所有模型修复 pass（pass2/3/fix_width）在 flagged 收集处加 `not is_manual_fix(...)` 跳过人工行。
- 各 pass 的 work 目录取 `os.path.dirname(OUT_DIR)`（作品根，人工文件惯例平铺于此）。

## 分类器改进：作者先验 + 对话门禁（v2.2 已验证达标）

**⚠️ 台词分类器调优的排错顺序（三层，2026-08-29 定稿，先读 `references/dialogue-extraction-impl-2026-08-29.md` 第 5-7 轮）**：台词提取/分类异常时**先按三层排除**，别直接跳进改规则：
1. **数据层（最常见）**：`line_meta_v2/*.json` 是否坏/过期（半成品中断任务残留）——抽一个纯文本长台词行，`classify_content` 单行 kind vs 磁盘 meta kind 比对，不一致=meta 坏。**修复 = `line_classify_v2 --work <id>` 重新生成（零 API）**。坏 meta 下 62% 误报、169 段艺术误报全消失。
2. **门禁层**：meta 正常但仍有误判（用户 gold 佐证）→ 用 **gold 做 A/B 回测**（对每条 gold 行跑现行 vs 候选分类器，量误标拦截/漏标保留/真台词误伤三轴）。
3. **语义层**（纯规则极限）：散落装饰 `ﾉンそしく` 既可能成词也可能装饰、纯边框包围的孤立拟声——**接受正确率与召回两难，按用户偏好选平衡点**。

**⚠️ 用户偏好（台词分类器调优，2026-08-29 拍板）**：「**短文本拟声词漏标难以避免，优先确保少误标**」= **宁漏勿伤**。任何候选方案 **真台词误伤 >0 即否决**（误伤真台词 → 漏译，比多提取装饰危害大），误标拦截率是次要目标。这在 A/B 回测里是**硬否决线**，不是权重。# 关于卷积/邻行上下文（用户提过）：**只在"补漏标"方向有益（邻行台词区救回散落短台词），对"少误标"有害（邻行装饰区借力放回装饰行）**——与宁漏勿伤偏好反向。半角修复后单行 run 已可识别真台词，卷积价值被吃掉，**实测证明纯负收益（误标 +5、漏标 +2），已移除借邻行**。详见 reference 第 7 轮。

**关键教训（v2 初版净退化）**：作者先验是**加权**，不是硬规则。"楼主 text 收紧 ≥7 字"会误杀楼主帖的**菜单/旁白**（`６．岩手`、`R-18はありません`）。正确做法：
- **楼主帖（◆，86% AA）保持 v1 逻辑不动**（`text_total≥5` 本就合理）
- **仅名無し帖（88% 纯文本）放宽**：text≥2 字、半角片假名按文本、混合阈值提高 → 修复 ~37 处短台词漏标（`奈良！`/`ﾕｳﾁｬｰと同期？`/`>>232`）

**对话门禁 `_has_dialogue(row)`**（必须）：text/mixed 必须含**真实日文对话字符**（假名/非艺术汉字，可选半角片假名）。否则纯装饰行（`. 。o ﾟ〇ﾟ o。`、`（●）（●）u.|`）会被 `is_art_char` 漏网误判 text，送进模型被改坏。与生产 `is_text_line` 语义对齐。

**art 防御（终审#5，接入 is_translatable）**：分类器仍可能误把含真实台词的 art 行判为纯艺术。在 `kind=='art'` 分支加兜底：`for run in kana_runs(line): if len(run)>=3: return is_text_line(line)`。`kana_runs` 已排除艺术假名，所以纯艺术纹理（`ﾉﾐﾑﾊﾞｯ`）不受影响，真台词（`キタキタ`/`吸っちゃダメ`）回退进模型避免漏译。验证要点：纯艺术行仍跳过、真台词行进模型、entry_3463 flagged 计数不变（227）。

**实测收益**：消费 meta 替换 `is_text_line` 后 flagged 行 -40.4%（154 行纯 art 移出模型，0 新增漏译）。远超 PRD 预期的 5~15% 下限。

**批量生成 line_meta（P5，零 token）**：`line_classify_v2.py --work <作品>` 不带 `--chapter` 会枚举 `raw_dir` 全部 html 批量生成 meta 到 `<work>/line_meta_v2/`。翻 `AA_USE_LINE_META` 前必须全量生成（否则只有已生成的话受益，其余 fallback）。生成后核对文件数与 `汉化/` 一致（如 60/60），并抽查 entry 行号口径仍对。

**meta 缺失自动生成（2026-08-11 落地，翻译入口内嵌）**：翻译入口（平铺 `scripts/aa_translate.py` 的 `_ensure_meta_flat(base, meta_dir, cfg)` 与 common 薄壳共用）在 meta 缺失时**自动调用 line_classify_v2 生成该章 meta 再译**（零 API 纯本地），不再静默 fallback——保证"后续翻译必用 meta"。要点：
- 定位站点/raw：优先 cfg 直传（registry 条目），否则从 `AA_DIR` 反查 registry `all_works()` 匹配 `aa_dir`；raw 文件名兜底搜目录含 eid 的 html
- 平铺版内嵌需临时 `sys.path.insert` tools/common/scripts 三处；`line_classify_v2` 已从 temp/layered 提升为生产工具 `scripts/tools/line_classify_v2.py`（sys.path 改指生产 common/works）
- 站点解析器需 `extract_article(raw)`（line_classify 依赖）——touhouyaruosure 等无此方法的新站要补别名
- 幂等：meta 已存在直接返回；关闭 flag 或生成失败返回 None → fallback is_text_line

## 分类器误标审计与双系统脱节（2026-08-12 entry_5866 实战）

**用户反馈**：「kikkoman 行标记大量误标，aa/mix 行被标文本（116话 57,61,62,64,65,204-206），aa 被标 mix（222,229,337-342）」。

**根因一：工作台 line_type_of 与生产分类器是两套独立逻辑（用户看到的误标主因）**：
- `aa_web_app/logic.py:line_type_of` 用**旧 `is_text_line`（简单 KANA_RE）+ ART_CHARS 计数**判定，生产 `line_classify_v2` 用 ART_* 黑名单+作者先验+对话门禁——entry_5866 全量对比 **303/936 行（32%）标记不一致**（text→aa 123、mixed→text 62、mixed→aa 60、text→mixed 58）。竖线画（`i:::::＼`、`≧:x`）被工作台判 mixed/text 而 meta 正确判 art。
- 排查陷阱：**用户说的行号口径不确定**（1-based/0-based/#头偏移±2 都试过才对齐）——直接全量对比两套系统量化差异，比逐行猜口径高效。
- 修复方向（已测试可行）：工作台 `line_type_of` 改读 `line_meta_v2`（KIND2TYPE 映射 art→aa/text/mixed/header/blank），meta 缺失回退旧逻辑（白头无 meta，需回退分支）。用户指出的行全部修正。

**根因二：KANA_RE 含 U+30FB（・中点）→ 分隔线被判 text（分类器自身 bug）**：
- `KANA_RE = re.compile(r'[ぁ-んァ-ヿー]')` **包含 U+30FB（・）**——纯装饰分隔线 `・・・━・・・` 的 `・` 被 `_has_dialogue` 误认假名 → 判 text（reason=lz_dotted）。
- 影响量化：**kikkoman 5,511 行 text 是纯 `・` 分隔线**（每话 ~40 条 `・・・━`），touhou 222/saki 22/fc2 44（合计 5,919 行，touhou 的 mixed 行含 `・` 且无真假名是 art 装饰，判 mixed 合理不算误标）。
- **修复（零 API，已测试可行）**：KANA_RE → `[ぁ-んァ-ヶー]`（排除 `・`U+30FB 与 `ヿ`U+30FF，**保留 `ー`U+30FC 长音**——台词常用）。三处同步：`line_classify_v2.py` + `aa_common.py` + `aa_translate.py`（每个文件独立定义）。验证：分隔线 `_has_dialogue` True→False 判 art；台词含其他假名（`そう・こう`）不受影响仍判 text。改后需重跑全部 meta（140+101+89 话，零 API ~5min）。
- **测量口径**：统计"仅 '・' 构成假名的 text/mixed 行"用 `KANA_NO_DOT = re.compile(r'[ぁ-んァ-ヶー]')` 复查行内无其他假名且无汉字。

**方案对比结论（用户要求先测试可行性）**：①工作台消费 meta（改 1 处+白头回退，修复用户可见误标）；②修 KANA_RE+重跑 meta（零 API，修复 5,511 行系统性误标+翻译管线受益）；③P4 模型反哺段边界——**不建议**（touhou 实战已证 v4-pro 输出格式不可靠，1108 calls 全失败；且本次误标是规则可修的确定性 bug）。推荐 ②先做 ①随后。

**⚠️ 排查前先确认「第 N 话」对应哪个文件（本会话最大浪费）**：用户报"116话误标"，我先后查了 entry_5866（第83话）和 entry_16489（1094行，L1094 恰好匹配用户"纯文本标mixed"）两个**错误文件**才找到真相——用户"116话"实际是 **entry_15923**（标题含 `...１１６`，636 行）。判定依据：①章节标题数字（`grep 标题` 找含 116 的文件）；②用户报的 L1094 越界则必非 636 行的 116 话，是**另一个文件**的末行。**别用文件名 eid 猜话数，先用标题定位**。L1094 纯文本（`長々と補足...ほなﾉｼ`）实际在 entry_16489，是独立于 116 话的另一处误标。

**第三轮：纯 art 面板被标 mixed（2026-08-12 entry_15923 实战，commit af42aa4）**：
- **根因**：楼主帖 art 面板用**散落艺术假名/汉字作装饰**（`ィハノイく赱勹` 等嘴/卷曲/花纹），每个都触发 `_has_dialogue` → `txt_max>=3` 过松 → `lz_mixed`。纯文本尾 `ﾉｼ`（2 半角片假名装饰）也触发 `art_max>=2` → mixed。
- **修复核心 = `_max_real_dialogue_run(row)` 连续段门禁**：真实对话需**连续段 ≥2**（`しーん`/`そうだね` 等真台词 maxrun≥2），单个散落艺术假名 maxrun=1 判 art。**边界字符 `ー`(长音) / `々ヽヾ`(迭字符) 作断点不延长 run**（艺术面板常用 `ーー` 横线装饰，但真台词长音 `ー` 是尾缀不构成 run≥2）。
- **第一个坑（初版回归）**：用 ART_KANA_CHARS/FACE_KANA 黑名单排除艺术假名会误伤**合法台词假名**（`う/っ/し/つ/と` 也在黑名单里）→ 真台词 `ぐっ…うぅ…！！`（entry_5866 L64）maxrun=1 被误判 art。**正确判别 = 连续假名段**（任意相邻假名即算，艺术面板假名是散落的所以不相邻），不是黑名单过滤。
- **mixed 门槛 `art_max>=2` → `art_max>=3`**（治纯文本尾 `ﾉｼ` 触发 mixed）。
- **全量回归抽样验证法**：改后重跑 meta 前后做 kind 变化对比（`git show HEAD:meta` vs 新 meta），对 mixed→art 的大批变化**抽样**核实。实测 6,528 行 mixed→art 中 **99.97% 是纯 art 面板正确修正**（仅 0.03% 是 `やる夫ｷﾀ━━(ﾟ∀ﾟ)━━!!` 类艺术化大喊，可接受）。**先抽样确认净收益再落地/commit，别贸然批量重跑 meta**。

## M4a 接入：行号口径 + 影子验证（关键）

**行号口径（致命风险，先查再接入）**：line_meta 是 **1-based 物理行号从 3 起**（前 2 行 `# 标题`/`# 原帖地址` 无 key），而生产 `process_chapter` 用 **0-based `enumerate(lines)`** → 固定 +1 偏移。接入必须**加载时统一转 0-based**：
```python
def load_meta_0based(meta_dir, base):
    raw = json.load(open(f"{meta_dir}/{base}.json", encoding="utf-8"))
    return {int(k) - 1: (v['kind'] if isinstance(v, dict) else v) for k, v in raw.items()}
```
`#` 行（idx 0/1）不在映射自然 fallback 到 `is_text_line`（本就 False）。用 `meta.get(idx)` 直接对齐，避免散落 +1。抽查：meta[key] 对应物理第 key 行（帖头），首/中/末全对齐 + 全文件 0 缺失才算过。

**接入函数**：`is_translatable(idx, line, meta)`：`art`→硬不进模型 / `text`→必进 / `mixed`→保守走 `is_text_line` / header/blank/idx 不在映射→fallback。flag `AA_USE_LINE_META=1` 启用，meta 缺失/损坏返回 None 自动 fallback。

**meta 目录查找（2026-08-10 实测踩坑）**：原实现 `os.path.join(os.path.dirname(path), "..", "work", "line_meta_v2")` 硬编码目录名 `work`——さーきー FC2 的 work 目录是 `work_fc2`（registry 配置），**meta 找不到但只打一行警告继续 fallback，翻译静默走旧逻辑**（saki_fc2 全量 73 话可能都在旧逻辑下重译过而无人察觉）。正确实现：**主用 `WORK_DIR`**（各 translate_*.py 入口都会 `T.WORK_DIR = ...` 正确设置，与 ckpt 同源），不存在时再扫描 `dirname(dirname(path))` 父级下所有 `work*` 子目录找含 `line_meta_v2` 的兜底。验证：`python -c` 设 `T.WORK_DIR` 后 `load_meta_0based` 应返回全键数 + `flagged(meta)` < `flagged(is_text_line)`（saki_fc2 实测 719 vs 1067）。

**对已交付作品做单章重译/清理（2026-08-10 事故）**：`pass2_fc2.py`/`pass3_fc2.py` 等 pass 脚本**全量遍历 OUT_DIR 且无单文件参数**——对已完成交付的作品跑，会把所有已交付 `*_汉化.txt` 一并"修复"（改动内容），且耗时长（>600s 超时）。正确做法：
- 绝不直接跑全量 pass；**import 后只调其函数**处理目标文件（如 `P.fix_lines(flagged)`，`residual_text()` 收集），写回目标文件即可
- 已误改时恢复：`git checkout -- <作品>/汉化_fc2/`（全量恢复），再 cp 回保留的新翻译文件
- pass3 残留判定用 `build_whitelist()`（全量高频假名串 + KNOWN_NAMES）——角色名（`やる夫` 58 次）自动入白名单，单章验证 `residual_bad` 应 0
- **2026-08-11 续译批次（キッコーマン 61-70 话）新增三个坑**：
  - **pass 脚本无 --help/参数校验**：`python scripts/cleanup/pass2.py --help` 会把 `--help` 当普通参数忽略、直接执行默认配置——**cleanup/ 下是白头硬编码版（OUT_DIR 写死白头），参数化版在 common/（`--work <id>`）**。实测误改 3 个已交付文件（git checkout 恢复）。探测用法用 read_file 看头部，禁 `--help`。
  - **pass2 的 residual_text 无白名单**（只数假名）会把角色名当残留"修复"已交付文件（31 文件实测，`git checkout -- <作品>/汉化/` 一键还原，untracked 新章不受影响）；**pass3 白名单制安全**（对已交付文件无实际落盘差异）。续译批次只处理新章：import 后只调函数，或跑完立即 `git diff --name-only -- <作品>/汉化/` 验证已交付文件 0 改动。
  - **验证已交付文件是否被改勿用 `git hash-object` 对比**（CRLF/LF 规范化让 hash 不同但 diff 为空，会误判"被改了"）——用 `git diff`/`git status`。
- **全数据 git 入库是 pass 事故的最终恢复网**：`git checkout -- <作品>/汉化/` 一键还原已交付章节（untracked 新章保留）。

**影子验证（真 token A/B，审阅报告强制）**：flagged 行数 -40.4% ≠ 实测 token。必须跑真实翻译对比：
- `AA_USAGE_FILE=xxx.jsonl` + `AA_USAGE_TAG=tag` 环境变量注入 `call_api` → 逐调用落盘 `{tag,ts,prompt,completion,total}`。
- ch3463 实测：32 calls / prompt 40097 / completion 18394 / total 58491，行数 669=669=669，宽度错位 0，token -40%。
- **残留对比口径陷阱**：old 交付产物是走完 pass2/3/fix_width 全清理的，new 只是 pass1 直出——残留差异不可直接比。逐条核对来源（`#` 标题行误报/角色名白名单缺口/与交付已知残留同源），别误判成 M4a 引入漏译。

## M4b：模型原样照抄检测 + 补译落盘 + 术语前移

- **`is_copy_failure(src, tr)`**（覆盖事故根因）：`src 含假名 且 tr.rstrip()==src.rstrip()` → 模型假装完成，判失败并进 retry（原因标签「原样照抄」）。角色名保留不误伤（只整行 TR==SRC 才判）。
- **补译失败不静默**：`translate_lines` 返回的 `still_missed` 由 `process_chapter` 落盘到 `work/review_inline.txt`（`filename | line N | 原文`），供人工/pass3 追踪，不静默保留原文。
- **glossary 术语前移进 pass1 提示词**（`AA_USE_GLOSSARY=1`）：加载 `glossary.json` 的 `glossary` 替换对，注入 SYSTEM 提示词「若该词是角色名则保留日文原文」。目的：模型 pass1 直接输出标准译名，避免后处理子串替换 re-pad 改宽度引入覆盖事故。flag 关返回空串，不改变现有行为。缓存用模块级变量（`_glossary_pairs_cache`），测试后须重置。

## M5：统一质量闸门 + 指标采集（G4/G5 验证）

**目标**：取代「每 pass 后人工跑 scan_width + 残留扫描」的零散验证，用可参数化的脚本一键产出 G4/G5 依据。`scripts/verify/quality_gate.py` + `scripts/verify/metrics_report.py`（2026-08-10 分类整理后路径）。

**quality_gate.py**（五类检查，参数化作品目录 `--work キッコーマン --chapter 3463`）：
- 行数一致 / 逐行宽度错位（`#` 行除外）/ 残留假名（白名单制）/ **人工行保护(G5)** / `#` 行保留（标题/地址必须原样）
- 白名单从**全量汉化产物跨章统计**（`kana_runs` 片段 ≥3 次 + KNOWN_NAMES 并集），避免单章频率低误报
- G5 检查：`manual_fix.json` 记录的行在产物中 `text`/`hash` 必须不变，否则报 `MANUAL OVERWRITE`
- 报告写 `--report`（默认 `<work>/quality_report.txt`），`--strict` 时任一失败退出码非 0

**metrics_report.py**（聚合 usage.jsonl）：
- 兼容旧格式 `{tag,prompt,completion,total,ms}` 与新格式 `{tag,ts,prompt,completion,total}`（ts 由 `_log_usage` 写，ms 是历史 `aa_api.py` 写的）
- 按 tag 输出 calls/prompt/completion/total 与占比；`--meta` 加分类 kind 分布
- **交叉验证法**：用已知的《统计对比报告》数字（total 3,252,352 / calls 1,854 / translate 1,482）断言脚本输出吻合，证明聚合正确

**token 用量统计已默认开启（2026-08-10 第七站收尾）**：`scripts/common/aa_api.py` 与平铺 `scripts/aa_translate.py` 的 `_usage_file()` 未设 `AA_USAGE_FILE` 时自动落盘 `<项目根>\usage.jsonl`（已 gitignore）。**开销≈零**——usage 字段本就随响应返回，仅本地追加一行 JSON（<1ms，2 万 calls ≈ 3MB）。`AA_USAGE_TAG` 标环节（translate/pass2/pass3/fix_width），env 调用时读（导入后设置也生效）。聚合：`python scripts/verify/metrics_report.py --usage usage.jsonl`。无 usage 字段/写盘失败均静默。

**AA + Hermes 合并统计（2026-08-11 新增工具）**：任务总消耗 = AA 管线(usage.jsonl) + **Hermes 对话**（state.db `session_model_usage` 表：api_call_count/input_tokens/output_tokens/cache_read_tokens/reasoning_tokens）。合并工具 `python scripts/tools/usage_total.py --since "YYYY-MM-DD HH:MM" --until "YYYY-MM-DD HH:MM"`（sqlite3 只读，时间窗按 first_seen 过滤；AA 侧缺失文件容错返回 0）。**每次任务完成后**把合并数字（两段分列 + 合计 calls/net tokens）追加进 `doc/同步记录.md`——单文档持续追加、勿覆盖历史，条目固定格式：任务摘要/变更点/token 消耗合并表/校验结果/遗留事项。实测窗口（101 话任务）：Hermes 侧 553 calls / 1.26M net（含 cache_read 70M 另列），AA 侧估算 ~36M。

**无记录批次的事后估算法（2026-08-10 第七站 101 话实战，仅当 usage.jsonl 缺失时用）**：用 flagged 行比例外推：
- `估算 token = 基准全流程 token × (本作 flagged 行 / 基准 flagged 行)`；基准 = キッコーマン 40 话 ≈ 20,000 flagged 行 / 3.25M tokens（技能 aa-manga-translation 有实测）
- 本战：touhou 101 话 380,891 行 / 221,995 flagged（58.3%）→ 估算 36M tokens ≈ 20.6k calls
- **换算套餐金额**：opencode-go 订阅 $10/月，deepseek-v4-flash 月配额 158k 次；`占比 = calls/158k`（本战 13%）→ 三口径交叉：$60 额度 ×13% ≈ $7.8 / $10 订阅×13% ≈ $1.3 / 按 mimo 单价 $0.14+$0.28 每 M ≈ $6.6。误差 ±20%，精确值要 opencode.ai 后台或下次先设 AA_USAGE_FILE
- flagged 行数从 `aa_common.is_text_line` 对全部 aa/*.txt 统计可得，零 API

**G4 验证法（残留分类，勿当黑盒）**：闸门报的残留段要**逐条核对源**，分三类再定论：
- 真漏译（TR==SRC 整行照抄）→ 真缺陷，正是 `is_copy_failure` 该在 pass1 拦截的（闸门独立复现了 M4b 目标，二者互证）
- 角色名合理保留 → 白名单缺口（`できる夫`→`kana_runs` 提取的是片段 `できる`，白名单须含**片段**而非完整名）
- `#` 标题行 → 误报（永不翻译）
60 话实测：5 真漏译 + 3 白名单缺口 ≪ 32 目标（基线 65）→ G4 达标。

**真实交付缺陷暴露**：闸门首次系统暴露已交付产物的宽度错位（60 话 111 行，如 `棍棒が折れたお→棍、棍棒断了` 宽 102→100）。这类是历史遗留待修，不是闸门误报——抽检 25 条与 `disp_width` 实际值全一致即证明闸门无误报。

- **批量修宽度错位（fix_width 参数化副本）**：已有 `scripts/fix/fix_width.py` 指向某作品，用参数化副本 `scripts/fix/fix_width_kikkoman.py` 指向目标（复用 `collect()`/`fix_batch()` 逻辑 + 文件级并行）。要点：
- **修前备份**：`cp -r 汉化 汉化_backup_<tag>`（PRD 4.9 回滚依据，不覆盖原始交付）
- `collect()` 里跳过 `#` 行（标题/地址永不参与宽度）
- 修后立即重跑 `quality_gate.py` 验证——**fix_width 可能引入残留副作用**（为凑宽度把整行台词保留日文），此时跑 pass3 清理收尾。
- 修前 `python -c "import fix_width_kikkoman; print(len(F.collect()))"` 与闸门报告行数核对（应为 111），确认收集口径一致。

**fix_width 失败行的零 API 本地修复（2026-08-10 第七站 27 行实战）**：fix_width 对宽横幅行中间嵌台词（100-170 列）常重译 3 轮仍超宽（模型为凑宽度乱改空格），失败行落 `work/manual_fix.txt` 后**先用纯本地删空格修复，零 token**：
- **偶数格差（2-8 格，最常见）**：只删 U+3000（每格 2 格宽），顺序：行尾 → 行首 → 文本前空白段（`re.finditer(r'\u3000+')` 倒序遍历），删完必须 `disp_width` 复核 == SRC。
- **奇数格差**：组合「删 U+3000 + 全角标点转半角」——`！？、。：；` → `!?,.`:;` 每个 -1 格；仍差 1 格可删行首半角空格（ASCII 空格 1 格，须在行首 20 字符内且属艺术空白区）。**顺序铁律（2026-08-11 实测）**：delta=5 时**先只转 1 个标点**使 delta 变 4（偶数），再删 2 个 U+3000——一次 `str.translate` 转多个标点会把 delta 从 5 变 3（仍是奇数），fix_line 依然拒绝。实现：`for i,ch in enumerate(line): if ch in HALF1: cand = line[:i]+HALF1[ch]+line[i+1:]; if disp_width(cand)==cur_w-1: return fix_line(cand, src_w)`。
- **仍修不了**（奇数差且无 1 格可删）→ 人工精简译文（换更短中文词），禁硬删艺术字符。
- 参考实现 `scripts/works/fix_touhou_width.py`（`fix_line(line, target_w)` 纯函数，27/27 修复后 scan_width 0 错位）；已用 ad-hoc 脚本覆盖 7 项边界（行尾/行首/中段/奇数不可修/已匹配/短行/真实样本回归）。
- 顺序铁律：**先 fix_width（API）→ 再本地删空格 → 最后才人工**；每个 pass 后立即 scan_width 确认落盘。

## 阶段2：mixed 段级翻译全作品推广（2026-08-11 kikkoman 140 话实战）

**增量推广驱动（核心设计）**：`scripts/works/promote_mixed_kikkoman.py`（复制改 registry id 可复用于其他作品）——**只重译 mixed 行的 conf≥0.65 text 段，非 mixed 行（普通 text/art/header/blank）保留生产已有译文绝不触碰**。绝不整话重译（会浪费 token + 改动已交付质量）。要点：
- 逐话读取 `aa/entry_X.txt` + `汉化/entry_X_汉化.txt` + `line_meta_v2/entry_X.json`，收集有 conf 段的行 → 按 ≤50 行/批调 `T.translate_lines_mixed(chunk)`（复用 3 轮语义）→ 只替换 fixed 的行
- **失败行保留旧译文**（不整行回退旧逻辑——那是旧管线行为，推广阶段宁缺毋滥），记 `<work>/promote_mixed_fail.tsv`（base\tL行号\tSRC\tTR）
- **断点续跑**：`<work>/promote_mixed_progress.json`（{eid: ok/partial/skip}），中断后重跑自动跳过
- **token 打标**：`AA_USAGE_TAG=promote_mixed_<作品>`，聚合用 `scripts/tools/usage_stats.py [tag过滤]`（按 tag 的 calls/prompt/completion/total + 分钟级时间序列）
- **参数化 + 并行（2026-08-11 三作品实战定型）**：`--work <registry id>` 选作品（默认 kikkoman 兼容旧用法）、`--workers N` 线程池按话并行（默认 4）。`call_api` 是纯函数式（每次新建 opener 无共享状态）→ **线程安全**；`_log_usage` 追加写 GIL 安全；progress 保存用 `threading.Lock` 保护。验证并发：mock process_one 测峰值 == N。实测 3 路并行 touhou 101 话 ~2.5h（vs 串行预估 4h+）。**注意配额墙**：并发越多越早触顶 opencode-go 5h$12 周限，推荐 workers=3 稳妥

**token 校准教训（重要）**：实验口径（整话重译，536 tokens/conf 行）**不适用于增量推广**——增量驱动只发 conf text 段、非 mixed 行零成本，实测 **~68 tokens/conf 行、8.0K/话**（kikkoman）。**估算推广成本用「conf 行数 × ~70」而非实验单话成本**。台词密度是主要波动源：

| 作品 | 话数 | conf 行 | 实测 tokens | /conf |
|---|---:|---:|---:|---:|
| kikkoman | 140 | 16,389 | 1.12M | 68 |
| touhou | 101 | 85,798 | 7.52M | 88 |
| saki 主站 | 16 | 2,921 | 0.20M | 69 |
| saki_fc2 | 73 | 15,793 | 1.13M | 71 |
| **合计** | **330** | **120,901** | **≈9.97M** | ~82 |

touhou 每话成本（~75K）是 kikkoman（8K）的 ~9 倍——**conf/mixed 比例 0.98 的高台词密度作品，直接按 70/conf 预估会低估 ~30%**（touhou 预估 5.84M 实测 7.52M）。预估公式：conf 行数 × 70~90（台词密集取高值）。

**实测数据（kikkoman 140 话）**：mixed 16,389 行（v3 meta 全量补齐后统计），段级直成 13,849（84.5%），保留旧译文 2,540；copy_fail 仍是失败主因（台词密集章模型照抄）。**宽度错位 78 行全部为 promote 前存量**——用 `git show HEAD:汉化/xxx.txt` 对比旧译文确认 0 新引入（推广后必做此验证，避免误判推广引入回归）。直成率随台词密度下降：touhou 78.5%、saki_fc2 73.6%（kikkoman 84.5%）。

**推广后质量验证（非 mixed 行 0 误动，2026-08-11 三作品实测）**：
- 宽度错位归属三分类（promote 后必做）：① art 行存量错位（kind=art，promote 不碰 art 行，从 meta 确认）② fail 列表中的行（保留旧译文=旧译文本身错位）③ 非 fail 且非 art → 才可能是 promote 引入。实测 touhou 抽检 5 话 13 行错位全部归①②，0 新引入
- **非 mixed 行未动验证的 git 陷阱**：`git show HEAD:汉化/x.txt` 对比会因 HEAD 过旧（工作区有大量历史未提交改动）**误报 1889 行"被改"**——对比基准必须是 promote 前的工作区版本（提前备份或 git stash），不能用 HEAD。代码级确认更可靠：写回循环只处理 `fixed_all`（宽度校验通过的行），非 mixed 行物理上不可达
- **usage 汇总 tag 一致性**（bug 修复教训）：`AA_USAGE_TAG` 命令行可覆盖，但汇总若按 `promote_mixed_<work>` 硬匹配会 total=0/错配（touhou 批次曾打印 kikkoman 数字，数据本身正确落盘）。修复：模块级 `USAGE_TAG = os.environ.get("AA_USAGE_TAG") or "promote_mixed_"+_WORK`，汇总与落盘共用同一常量。另修 `tot_mixed=0` 除零（空目录/全 skip 场景）→ `max(tot_mixed,1)`

**v3 meta 补跑**：只有 3 话实验时有 segments，其余 137 话 v2→v3 需 `line_classify_v2.py --work kikkoman` 全量重生成（零 API，140 话 ~75s；saki_fc2 73 话同样补跑）。touhou/yaruobook 已是 v3。**先补 v3 meta 再统计 conf 行数**（v2 meta 无 segments 统计为 0，会严重低估）。さーきー/aa 里混有 FC2 章节副本（entry_*.txt，aa_fc2 已有同名）——统计/推广前先核对目录归属，避免重复处理。

**git 分组提交模式（推广批次收尾，2026-08-11 实测）**：大批次会留下大量混杂改动（作品数据/驱动/temp 实验/文档），一次 `git add -A` 会误纳入 backup 目录（`*_backup_blank` 41MB+17MB 历史快照、work/backup 时间戳备份）。分组提交：
1. `.gitignore` 加 `*_backup_blank/`（git rm -r --cached 移除已误提交的）
2. 分组：推广产物（驱动+日志+PRD+glossary）→ 各作品数据（汉化/aa/meta/work）→ 交付 HTML → scripts/aa_web_app/doc 历史遗留 → temp 实验归档
3. 提交后 `git status --short | wc -l` 应 0；`git ls-files | grep -c backup_blank` 应 0

**审阅卡编排模式（批次类任务）**：批次后台跑时先建审阅卡再 `kanban block --kind transient`（理由=等待批次完成），批次完成后 `kanban unblock --reason "..."` 放行独立验收——避免审阅卡被 dispatcher 提前拉起空跑。审阅卡 body 要写死验收方法（抽 5 话宽度/行首空格/art 段/失败行/非 mixed 行 0 误动/usage 核对/unittest）。

## mixed 段级翻译（B1-B4，2026-08-11 1 话实验达标）

**「宽对框歪」现象（核心问题，宽度校验盲区）**：disp_width 相等 ≠ 视觉不错位。当前整行 `pad_to_width` 在**行尾**补 U+3000，当文本在 AA 左侧或文本被对话框符（`|`、`】`）包围时，pad 改变字符列分布 → art 段/框被推歪但宽度校验全过。实测 entry_14463：167 mixed 行中宽度错位 0，但框架字符列偏移 3 行（1.8%）。规律：文本在 AA 右侧无框基本不出错；右侧有框 → 框大概率微偏（视觉影响小）；**文本在 AA 左侧 → 大概率整体错位（视觉影响大）**，除非被两侧符号约束。

**解决方案（段级翻译）**：
1. **B1 段边界（line_classify_v2 扩展）**：mixed 行输出 `segments: [{start,end,kind,src_width}]`——Python 字符索引**半开区间 [start,end)**、**覆盖整行无空隙**、空白并入相邻 art 段、低置信段带 `confidence` 标记。合并相邻同 kind 段。置信度 <0.65 的 text 段不送模型（装饰假名如 FACE_KANA 集合）。
2. **B2 段级翻译（aa_translate）**：只发 conf≥0.65 的 text 段进模型（格式 `行号_段号|译文段`），本地按 segments 拼回——art 段**原样字节保留**（模型物理上接触不到）。
3. **B3 段级 pad**：`pad_segment(tr, seg_width)` U+3000 补**段尾**（即下一个 art 段之前），不是行尾 → art 段起始列不变。
4. **回退安全网（必须）**：段级失败（宽度/残留/超宽）→ 回退整行走旧逻辑，11/11 全数恢复。**回退率迭代**：装饰段不送模型 + copy_fail 仅真台词段生效 → 回退 78/167 → 11/167（93.4% 段级直成）。
5. **flag**：`AA_USE_MIXED_SEGMENTS` **默认开（2026-08-11 翻 flag，commit 535b169；显式 =0 关闭回滚）**；pass2/pass3 同步 segments 感知残留（`aa_common.residual_bad_segs`，按 segments 跳过 art 段）——翻 flag 三处默认值要一起改（aa_translate.mixed_segments_enabled + pass2/pass3 的 `_flag_on("AA_USE_MIXED_SEGMENTS", False)`→缺省 True），只改一处会导致残留检测口径不一致。

**实测（entry_14463 A/B，167 mixed 行）**：帧宽度列偏移 **0**（旧 5~6 行、最大 10 列）；段级路径 art 段字节 0 损坏（153/167 逐字节验证）；台词残留 0；token 52,252 vs 62,778（**-16.8%**）；补译失败 1 vs 3。报告模板 `doc/第二阶段_实验报告_1话mixed.md`。

**测量陷阱（重要）**：帧偏移必须按**宽度列**算（disp_width 语义：ASCII/半角假名 1 格、其他 2 格），**不能用字符索引**——行首空白+台词混合段模型丢空格后 pad 补段尾，字符数变了但宽度列不变，字符索引会**虚报偏移**。校验脚本先 `wcol_of_frame()` 再比列。

**翻 flag 前置清单（审阅发现的 gap，2026-08-11 已全部闭环 + flag 已翻）**：① ~~`fix_width.py` 未同步 MIXED_SEGMENTS~~ → 已修复（commit 0335fb3，见下节「fix_width 段级同步」）；② 全作品 v3 meta 重生成（kikkoman 140 已有、touhou 101 + yaruobook 59 已于 D4 生成，さーきー FC2 需补跑——FC2 meta 实测仍为 v2 无 segments，翻 flag 后走旧逻辑不受影响，但要享受段级收益需重生成）；③ art 段差异 4 行属分类器边界精度（非模型损坏），翻前用 quality_gate 在更多章节验证；④ 渲染验收按宽度列口径。**翻 flag 验证模式（commit 535b169）**：三态断言（默认开 / `env=0` 关 / `env=1` 开）+ 旧作品 meta 缺失/损坏/目录不存在 → `load_segments_meta` 返回 `{}` 自动回退全行逻辑（白头/saki 无 v3 segments 零影响）+ 残留检测三态（text 段干净 0 报 / text 段假名报 / 无 segs 回退全行）+ unittest 与基线一致（stash 对比）。

**台词切碎修复（_merge_narrow_kanji_art，2026-08-11 用户反馈驱动）**：1 话实验"达标"后用户实测 HTML 发现段级路径**错位空格**——`确实频率相当　　　　上增加了`（旧：`确实频率上升了不少`）、`什十么　　一只要对方想要`、纯文本行也被插 U+3000。根因：**台词中的常用汉字（上/十/一/十二 等）在 `ART_KANJI` 黑名单里被 `is_art_char` 误判为 art 段** → 完整台词切成多段独立翻译 + 每段独立 pad → 语义断裂 + 段间 U+3000 堆积。修复（line_classify_v2.segment_line 末尾）：
- 规则：`text-art-text` **三合一**（art 段宽度 ≤6 格、内容**全部是 ART_KANJI 汉字**、两侧 text 至少一个有真实台词——非艺术假名/含汉字）；连续链（text-art-text-art-text…）全部并入一个 text 段，`i += 2` 跳过后段
- **只合并纯汉字段**：ASCII/边框/假名窄段（`:、|、━、ﾆ` 等）是真实装饰，**不合并**（实测夹缝 art 段 TOP 是 `:` 136 次、`|` 33 次——那些是画面）
- 剩余不合并的汉字夹缝（帖头星期 `(土)`、艺术纹理 `込/云/辷/寸/乂`）两侧是装饰假名非真实台词，has_real 门禁正确挡住
- 修复后三行译文恢复正常：`确实频率上升了不少` / `几十体里有一体是对方想要的` / `最坏的情况也完全有可能发生`；帧偏移 NEW=0 保持、宽度错位 0
- 验证断言陷阱：**装饰段独立是正确行为**（L76 的 `ｌ⌒ヽY` 等 conf 0.55 装饰段单独存在），断言应检查「台词主体在单个 text 段内」（`has_text_seg_containing(idx, needle)`）而非「该行只有一个 text 段」——后者会误报 FAIL

**fix_width 段级同步（2026-08-11 commit 0335fb3，审阅 gap 闭环）**：翻 flag 前审阅发现 fix_width 旧实现把**整个 mixed 行（含 art 段）丢给模型整行重译**——模型可能改动绘画字符。修复方案（aa_translate.py 新增 `fix_width_mixed_lines(rows_by_file)` + fix_width_kikkoman.py 改造）：
- **只重译 conf≥0.65 的 text 段**：格式 `行号_段号|片段`，专用 prompt `FIX_MIXED_SYSTEM` 要求「译文必须比原文片段更短或等长」（宽度错位通常是译文超宽）；art 段/低置信段本地原样保留
- rebuild 用 `rebuild_mixed_line(src, segs, trans)`（复用段级翻译的 lead 保护+pad），**整行宽 == SRC 才接受**；3 轮重试（失败重发未译段），仍失败落 manual 清单
- collect() 侧：`AA_USE_MIXED_SEGMENTS=1` 时才读 line_meta_v2 segments（`T.load_meta_segments_0based`），flag off 行为完全不变（不读 meta）；`fix_batch` 拆分 mixed（段级）/普通行（旧整行重译）
- **影响面**：仅 kikkoman 有 v3 meta；fc2/白头 meta 无 segments → 自动走旧逻辑零影响（实测さーきー FC2 73 个 meta 全是 v2）
- **验证模式（零 API）**：monkeypatch `T.call_api` 返回固定译文/原样照抄，跑 `fix_width_mixed_lines` 断言 fixed/failed/宽度/art 段逐字符保留——这是段级逻辑的标准单测手法，无需真调模型
- **mock 数据必须用真实 meta（2026-08-11 推广驱动验证踩坑）**：手写 segments 的 `src_width` 若与真实行字符不符，rebuild 后整行宽 != SRC → width_fail 假 FAIL（v1 验证脚本 6 个 FAIL 全是测试数据构造错误，非代码 bug）。正确做法：从真实 meta 文件取 segments（如 `temp/exp_14463/work/line_meta_v2/entry_14463.json` 的 L57），断言才可信。另两个断言陷阱：①失败行「保留旧译文」应对比**当前文件值**（前一轮成功替换后的值），不是初始 tr；②usage 落盘是 `call_api` 内部调 `_log_usage`，mock 掉 call_api 后 usage 不写——验证 usage 直接调 `T._log_usage(p, c)`。
- 顺带修的 bug：failed 列表曾**重复 append**（`for-else` 分支 + 循环后 `if` 双写），统一为循环后单一 `if (fname, idx) not in fixed`

## B2 回退整行重译 = mix 错位/AA 误翻的生成路径（2026-09-01 定案+实施，commit 622ceb9a）

**现象**：ドスケベナイトあかりちゃん 84 条人工修正 = 63「错译」(AA 艺术行被模型误翻：`Blue Mountain`→蓝山咖喱、`ハハハ`→哈哈哈、`ｨぅうみ､`→唔嗯，人工还原日文原文) + 11「错位」(mix 台词位置)。全作品扫描 art 行被翻译 = 0（段级保护本身生效）。

**⚠️ 用户硬否决（方案迭代教训，必须遵守）**：首版方案「mixed 失败行原样保留原文落人工」被用户当场否决（"这根本不算什么方案纯粹是垃圾"）——**「译好但错位的行至少只需调整位置；你不翻译则人工既要调位置还要手动翻译」**。mixed 失败行的修复必须**自动翻译且不错位**，任何"丢给人工"的兜底都不是方案。最终 v4 走字符级根因修复，B2 落 review_inline 只是失败兜底，不是主方案。

**根因（字符级，数据实证）**：`is_art_char` 把装饰字符判 text → 送进翻译模型。实测 `｀´⌒lIiUu`（弧线/线条）、CJK 兼容假名（㍉㌘㏍）、CJK 扩展区汉字（滯灘鑾）、`衛衛衛` 式纹理汉字全被标 text 且 conf 0.92 → 模型译成中文落错列/改坏 art。**全 34 章 320 段高置信装饰段误送模型**；11 条「错位」记录**全部"宽度==SRC 但中文列位置错"**（如 29785:257 `ぱんっ！` 列 95 → 译文 `啪！` 列 60；29858:522 `トエエ` 列 76 → 译文列 38）——即「宽对框歪」的系统性来源，不是 pad 逻辑 bug。

**v4 修复（三层，全自动，零"原样保留"）**：
1. 分类器 `line_classify_v2.py`：`DECOR_SYMS`（｀´⌒⌢⌣〜¨˙lIiUu）→ art；`DECOR_HANZI` 扩充（衛滯灘鑾眦奝奲等纹理字）+ CJK 兼容假名/符号/扩展A（U+31F0-33FF / U+3400-4DBF）→ art；`_seg_confidence` 无真实假名/汉字段 → conf 0.4（不送模型）；`ー`长音与拗音小假名（ぁぃぅ…）排除出「真实假名」；小假名开头段（ぅて/ぅてぅ 装饰嘴型）降级；`_has_dialogue` 排除 DECOR_HANZI
2. 段级兜底 `aa_translate.py`：`_compress_seg_to_width`（段超宽本地压缩：删 U+3000→删段内连续 U+3000→全角标点半角化，必须精确==target 否则 None）；`rebuild_mixed_line` 超宽→压缩（**`pad_segment` 不压缩、超宽直接返回，会把后续 art 推右——必须分流：不足宽 pad、超宽 compress**）；`_verify_art_alignment`（art 段列位置校验，宽对框歪防御）；**B2 对 mixed 失败行不再整行重译**（防 art 进模型）→ 落 review_inline 带 `[mixed-fallback]` 标记
3. 测试 +10（test_mixed_segments 37 全过）：压缩边界/超宽 rebuild/art 对齐

**实测**：全 34 章高置信 text 段 6195→5751（Δ-444 装饰段不再送模型）；真台词段全保留（`ぱんっ！`/`やる夫くんとのセックスです♥` conf 1.0）；entry_28594（错位最多）真实重译 408s：宽度错位 0、art 行 0 改动、仅 2 个 mixed 行微调。全量 250 测试，test_mixed_segments 37 全过。

**坑**：① `_seg_confidence` 的 `real` 列表必须先算再用（小假名开头判定引用 `len(real)` 曾引用未定义变量，NameError）；② `_compress_seg_to_width` 只压缩不 pad——rebuild 里不足宽仍走 `pad_segment`（曾只压缩不 pad 丢 6 格测试失败）；③ test_line_meta 2 失败与本次无关（用户 glossary.json 移出白頭，git stash A/B 实证）；④ `街` 等单字常用字夹纹理中仍 conf 1.0（宁漏勿伤取舍，不枚举）。详见 `references/mixed-align-root-cause-2026-09-01.md`

## AAHub 行数据库辅助分类/分段（2026-09-04 定稿，**2026-10-10 判废弃**）
- **已废弃**：该方案 2026-09-07 判不可行搁置，**AAhub 素材库 2.3G 与检索工具 `common/aa_hub.py` 已于 2026-10-10 删除/归档 legacy**（重下 `https://dataset.aahub.org/datasets/mlt_v32_1.zip`）。
- 历史实现/决策留档：`references/aahub-db-classification-2026-09-04.md`、`references/aahub-block-fingerprint-2026-09-05.md`（其中 `doc/TODO/*` 链接已随该目录删除而失效）。
## 人工修正记录检查法（edits.jsonl，2026-09-01）

- **`work/edits.jsonl` = 人工修改权威记录**（用户纠正：不是 review_inline.txt——后者是 M4b 补译失败行落盘，`entry_X.txt | line N | 原文` 格式，行号可能漂移）。
- **双格式**：人工工作台记录 `{file,line,type,before,after,ts}`（无 src 字段，type=错译/错位）；校对自动 `{file,line,old,new,why,type,src:"proofread"}`。
- **验证已生效**：before/after 记录 → 磁盘行 == after 精确比对；proofread 记录 → 磁盘行 == new **宽松比对**（`new 核心文本 in 磁盘行`——宽度补 U+3000 前后缀致精确字符串不等，勿误判未应用）。
- type 语义：人工「错译」= AA 艺术行被误翻后人工还原日文原文；「错位」= mix 台词位置调整（补 U+3000 对齐）。

## M12：promote 失败行回填（2026-08-12 三作品实战，25,887 行）

**背景**：阶段2 推广后 mixed 段级失败行保留旧译文，记录在 `<work>/promote_mixed_fail.tsv`（base\tL行号\tSRC\tTR）。失败主因 copy_fail（模型照抄原文被拦截）。回填 = 对这些行**重试段级翻译**，成功写回汉化文件，失败导 manual 清单供人工。

**回填驱动（temp/refill_mixed.py，--work 参数化复用 promote 驱动结构）**：
- 读 fail.tsv → 取 line_meta_v2 segments → `translate_lines_mixed`/`fix_width_mixed_lines` 重试
- **段宽约束提示词（降 copy_fail 关键，回填率 78%→95%）**：告知模型「片段区域宽度 N 格，译文必须比原文更短或等长」——promote 阶段 78% 直成 vs 回填 95% 成功率
- 成功（宽度==SRC + 残留检测通过）→ 写回 `汉化/` 对应行；失败 → `refill_manual.tsv`（人工清单）
- **断点续跑双保险**：`refill_progress.json`（{base: {L行号: ok/fail}}）+ **汉化行 != 记录TR 即跳过**（崩溃恢复后不重复已写回的行）
- manual.tsv 是追加式，含旧判定误写的行 → 去重 + **与 progress fail 交叉过滤**（只保留最终 fail 的行）

**艺术纹理跳过判定（数据驱动，关键省 token）**：回填前必须能区分「真漏译台词」vs「艺术纹理被误判为台词」。`_seg_has_real_text(seg)` 数据驱动调优：
- 真台词（`レベルアップ`/`瑕疵ある子になったら`）→ True
- 纹理/同形字（`テ弍ヽ`/`仟テ`/`巛巛`/`x癶x`/`rくニニニニ`/`物理防御：6`）→ False（**面板标签类不算台词**）
- 角色名（`やる夫`/`ルイズ`/`キュルケ`/`できない夫`）→ False（豁免）
- touhou 实测 **11,865 行被正确识别为艺术纹理跳过**（非真漏译），未浪费 token 重译

**实测数据（三作品，回填环节 ~2.24M tokens）**：

| 作品 | 回填成功 | manual | 成功率 | tokens |
|---|---:|---:|---:|---:|
| kikkoman | 2,390 | 150 | 94.1% | 891K |
| touhou | 5,740 | 290 | 95.2% | 703K（888 calls，17min，3 路并行） |
| saki 主站+FC2 | ~4,500 | ~320 | ~90%+ | 644K |

**质量验证（审阅卡独立复跑）**：回填行宽度全量 ==SRC（kikkoman 2,390 行全量抽查非抽样）；残留 <0.1%；usage 精确匹配（888 calls/702,792 tokens）；非回填行 0 误动（16,858 行 0 错位）；unittest 96 无新增失败。

**已知缺陷（记录不阻塞）**：
- refill_art_texture.tsv 并行写竞态缺 44 条记录（progress.json 12,272 vs 文件 12,228）——不影响译文质量
- 父卡 summary manual 数字宣 308 实 320（文件本身正确）

**教训**：
- **回填是增量工作的最后一段**：推广（M11）→ 回填（M12）→ 人工（manual 清单）三层递进，每层只处理上一层失败的行
- 回填驱动复用 promote 驱动的 `--work/--workers` 参数化 + 线程池模式，不需要新框架
- 人工回填量：~760 行（kikkoman 150 + touhou 290 + saki 320）——导出清单后人工译或后续批次处理

## M13：顽固行 manual 攻坚（2026-08-12 三作品实战，~915 行）

**背景**：M12 回填后剩余的 `refill_manual.tsv` 行 = 弱模型反复失败的顽固行。M13 用 v4-pro 攻坚（每实施卡配独立或聚合审阅）。

**核心教训（按处理顺序，可省大量 token）**：
1. **先查 TSV 是否已过期（最重要，省 95% 工作量）**：touhou 311 行中 **295 行已被宽度修复/后续批次自然解决**（TSV 是追加式快照，行内容可能已变）——处理前先逐行对比 `汉化/` 当前文件，`disp_width(当前) == disp_width(SRC)` 即已 OK 直接跳过。saki 454 行中 186 行同理。
2. **再分类艺术误报（零 API）**：saki 454 行中 **266 行是纯艺术假阳性**——`is_text_line` 被 `ん/ミ/冬` 等艺术字符触发（含假名但全是艺术用途、在白名单），`residual_suspect` 认为无需翻译。判别：抽查确认 SRC 行无真实日文台词段。
3. **v4-pro 整行重译不适合顽固行（touhou 1108 calls 全失败）**：v4-pro 对「整行重译+art 保护」prompt **不遵循输出格式**（1108 次调用全因格式不符判失败，243K tokens 白耗）。顽固行正确策略：本地修复（宽度已 OK 则跳过）→ 手工翻译（真问题行直接人工译，saki 2 行、kikkoman 13 行）→ 白名单补录。
4. **段宽约束提示词是段级路径的灵药，但救不了「模型不遵循格式」类失败**——format 失败要换路径（本地/人工），不是换模型或加轮次。

**零 API 收尾（M13 最终形态）**：saki 主站 17 话 + FC2 73 话 scan_width 0 错位，全部人工翻译/本地处理，零 API 消耗。宽度清理卡同理（width_clean 仅 774 tokens——本地优先 strip 扩展到尾空白）。

**审阅发现的文档级偏差（不影响质量，记录即可）**：usage tag 欠缺（18 calls 混入常规 tag）、HTML 快照预期不一致（manual fix 导致，测试需更新快照）、汇总数字宣 308 实 320（文件本身正确）。

## 视觉辅助分类（2026-08-16/17 全量 73 章实战定型）

**触发**：纯文本规则分类器（ART_KANJI 手工枚举）仍误嵌/漏嵌时——AA 行强上下行关联，**必须按块（楼）视觉判定**，单行规则永远有歧义。AAhub 素材统计只能给候选集（纯字符频率分不清"魔字作画/魔字作词"），最终裁决靠视觉模型。

**管线**：规则粗筛存疑行（误嵌:spans非空且art>35%；漏嵌:spans空且含中文art<25%）→ 渲染红框图（vision_annotate.py）→ 视觉判定（vision_classify.py）→ v3 落盘 `line_meta_v3/` → 嵌字消费（v2_text_spans 加 v3_verdict：art 停嵌/text 补嵌）。

**判定链（实测定稿）**：glm-4.6v（免费优先，2-30s/块）→ Qwen3-VL-8B（主力，**付费** ¥0.5/M in+¥2/M out）→ mimo-v2.5 via opencode-go 网关（备选；**官方端点有内容审核**，部分图 high risk 拒绝）。MiniMax-M3 是推理模型（思考进 content、不遵守格式）**不加入**。Qwen 批量 400 根因=图>1024px 或上下文过长 → 缩放≤1024+红框行±2行。网关视觉请求格式（input_image+image_url 嵌套+浏览器 UA+OPENCODE_GO_API_KEY）详见 `references/vision-classification.md`。

**坑**：Windows ProcessPoolExecutor 要模块级函数+`__main__` 保护；--retry-failed 行号口径（`'entry_'+group(1)==base`）；retry 合并旧 v3 不丢已成功判定；渲染与判定不能并行（v3 半写状态）。

## 批次续译标准流程（2026-08-11 キッコーマン 61-80 话两批实战）

**续译 10 话一批的标准流水线**（承接既有作品，含所有优化项；61-70 批清理全量扫描 vs 71-80 批定向清理的对比见下）：
1. **抓取**：`curl` 分类页 `?page=N`（40 条/页，page=2 即第 61-100 话）→ `r401_index.py html <本地文件>`（**--json 子命令必须跟输出文件名，只传 --json 会 IndexError**；用 html 子命令解析本地页拿表格）→ 构造 `{"entries":[{eid,title,url}]}` JSON（**条目必须含 url 字段，否则 dl_chapters.py KeyError 'url'；漏 entries 包装会 AttributeError 'list' has no 'get'**）→ `dl_chapters.py --json xxx.json`
2. **提取**：`extract_all.py --work <id>`（新章 txt + meta 缺失自动生成）
3. **翻译**：`AA_USAGE_TAG=translate python scripts/aa_translate.py --work <id>`（后台 + notify_on_complete；自动跳过已有 _汉化.txt；实测 10 话 ~6-7 分钟）
4. **清理三连（只新章，勿全量！）**：用 `templates/batch_cleanup_driver.py`（本 skill 模板目录）——`AA_USAGE_TAG=pass2|pass3|fix_width python batch_cleanup_driver.py <work> <stage> <eid1,eid2,...>`。**全量扫描旧章的代价（61-70 批实测）**：① pass2 的 residual_text 无白名单，角色名/艺术假名被判残留重译 → 误改已交付文件（31 个，git checkout 恢复）；② pass3 对旧章 iter 轮次照发 API，白耗 ~179K tokens——清理环节 token 占比 18.9% → **定向后 3.8%**，单话成本 94.6K → **66.5K（-30%）**
5. **本地修宽 + 人工回填**：fix_width 失败行按「偶数差删 U+3000 / 奇数差先转 1 个标点再删空格」本地修（详见 fix_width 章节）；真漏译（TR==SRC 整行照抄）用「子串替换 + pad_to_width」，**译文宽度必须 ≤ SRC 片段宽**（超宽 FAIL 后换更短译文，如「是走投无路的客人吗？」21 格→「是走投无路客？」15 格）；**替换源串必须是 TR 里实际存在的原文**（TR 可能是日文原文或已被前轮改过，先打印 TR 再写 replace 源）
6. **校验**：`scan_width.py --work` + `quality_gate.py --work <作品目录名> --chapter <eid>`——**注意 quality_gate 的 --work 是作品目录名（如 キッコーマン）不是 registry id（kikkoman）**，传错报 FileNotFoundError；已交付旧章 `git status --short <作品>/汉化/ | grep -c "^ M"` 应为 0
7. **交付（需渲染时才生成）**：合集**绝不**自动生成（2026-10-06 起 runner/task_manager 无 `--gen`，全仓库无自动路径）——交付时 `gen_aa_html.py --work` 写出**交付文件夹**（`index.html` + `data/*.js` + 源 `txt.gz`）→ 浏览器实测（**等 JS 填完数据**再验：侧边栏章数、原帖链接、等宽对齐、帖头绿粗、译文嵌气泡、右上译/原切换、刷新回到上次章节）
8. **统计记录**：`usage_total.py --since "YYYY-MM-DD HH:MM"` 合并 AA+Hermes → `doc/同步记录.md` 追加条目（任务摘要/变更点/token 表/校验/遗留）→ `doc/统计对比报告.md` 追加对比章节（含口径说明）

**token 打标要点**：`AA_USAGE_TAG` 是**调用时读 env**（导入后设置也生效，勿在模块级缓存）；aa_translate.py 默认 tag=translate，pass2/pass3/fix_width 不设 tag 时落 unknown——所以清理环节必须显式设 tag。usage.jsonl **无 ts 字段**（旧格式 {tag,prompt,completion,total,ms}）时用「上次任务结束时行数切片」区分批次（本次任务 = lines[628:]）；或跑前 `wc -l usage.jsonl` 记基线。

**quality_gate 残留误报的两类判别**：① 真漏译（TR==SRC 整行照抄且含可译台词）→ 人工回填；② **艺术行误报**（TR==SRC、宽度匹配、kana_runs 命中的是嘴部/装饰假名纹理如 `ャかト`）→ 补录 `glossary.json` 的 known_names（**补的是 kana_runs 提取的完整片段**，不是单字符），补后 quality_gate 立即清零。

**实测批次数据（供对比）**：
- 61-70（清理全量扫描）：946.4K tokens，单话 94.6K，清理占比 18.9%（unknown 合并）
- 71-80（AA_USAGE_TAG + 定向清理）：665.1K tokens（AA 646.7K + Hermes 18.4K），单话 66.5K，清理占比 3.8%（pass2 0.9% / pass3 2.5% / fix_width 0.4%）
- 81-140（60 话大批次完结）：4.77M tokens（AA 4.66M + Hermes 110.5K），单话 77.7K，清理占比 5.9%（translate 94.1%）；全作品 140 话累计 AA 管线 ~6.38M / 3061 calls
- 21-60 旧逻辑基线：单话 81.3K；单话成本波动主因是语料结构（横幅宽行多则高）+ 清理是否定向

**顽固残留分类与处理（大批次实测，52 行 review 收敛法）**：pass3 修不动的 review 行按 TR vs SRC 分类：① 真漏译（TR==SRC 整行照抄）→ 人工回填（子串替换+pad，译文宽度须 ≤ SRC 片段宽，超宽换更短译文）；② 已译残留（TR≠SRC 但日文片段残留）→ 逐段判别：艺术假名纹理/角色名保留（补白名单），真残留子串替换；③ 纯艺术行误报（TR==SRC、宽度匹配、kana_runs 命中的是嘴部/装饰纹理）→ 不动 + 白名单补录。**面板/状态标签要译**（【スキル】→【技能】），面板内角色名（セッシー/ミヨシ/レヴィさん）保留日文。**装饰字「そて」类散落假名 = 作者艺术装饰大字**（大字横幅放大写法，隔 U+3000 散布）——翻译会破坏空格结构导致宽度 +1~2 修不动，**直接恢复 SRC 原文**（与艺术行同待遇，宽度天然一致）；判别：行内仅 1-2 个散落假名、无完整台词段。

## 仅下载原文零模型批量（2026-08-20 実戦：23586 トレセン学園人狼部 43话 / 21114 時間停止探索者やる夫 270话）

`r401_index.py category <id>` 只取第1页（40条）；>40话必须分页 `?page=N` 循环——解析 `href="/yaruo/categories/<id>?page=N"` 求最大页或逐页至空合并 entries（23586=43/2页、21114=270/7页，含 `p=2012` 第4话乱序按标题排序）。批量下载按 `temp/dl_raw_only.py` 模式：5并发、直连→`127.0.0.1:10808` 代理兜底、已有>50KB跳过、失败重试3-4次、长度<10KB抛错；提取 `sites.yaruobook.extract`（`entry-content cf`→`<dl>`，`yaruobook.net` 别馆与 `yaruobook.com` 同解析器，无 registry 亦可手工提取），得 `aa/*.txt + *_comments.json`，全程不读 `model_config.toml`/不设 `AA_BACKEND` 零 token。`r401` 偶发 `SSL UNEXPECTED_EOF / WinError 10054` 需重试3-5次+代理兜底。详见 `references/raw-only-batch.md`。

## 解析器空行修复 + 已交付零 API 迁移（2026-08-11 キッコーマン 80 章实战）

**用户偏好（必须遵守）**：**楼主正文中的空行是漫画的一部分**（分镜/段落间隔、场景停顿），交付 HTML 必须保留——"空行被过度压缩"是交付缺陷。

**问题**：`sites/yaruomatomex.py` 的 `parse_posts` 曾 `if row.strip()=='' : continue` 一刀切跳过所有 `<br>` 空片段，把作者在正文中留的分镜空行（分隔线前后、段落间、长场景停顿）也删了，画面过度压缩。其他解析器（bookshelf/fc2/2nt/yaruobook/touhou）同样有 `row.strip()==''` 跳过逻辑，**新站接入时必须做空行语义审计**。

**修复**：`parse_posts` 只跳 dd **开头/结尾**的连续模板空行（站点 `<br>  <br>` 装饰），**正文中间空行保留**：
```python
rows = [clean(rh) for rh in re.split(r'<br\s*/?>', body_html)]
s = 0;  while s < len(rows) and rows[s].strip() == '': s += 1
e = len(rows); while e > s and rows[e-1].strip() == '': e -= 1
rows = [(r, None) for r in rows[s:e]]   # 中间空行保留
```

**已交付章节零 API 迁移**（改解析器后行号全变，勿重译）：
1. **前置校验（关键安全门）**：新旧提取的「非空行序列」必须完全一致——`[l for l in aa_lines if l.strip() != '']` 逐章比对，80/80 一致才继续（不一致说明解析器还改了别的东西，先查）
2. 重新提取全部章节（`extract_all.py --work`，+10,280 空行）
3. 旧汉化非空行按序回填到新 aa 非空行位置，空行位置补 `''`（旧汉化本就无空行，非空行序列=全行序列）
4. **后置校验**：`scan_width` 错位数必须**不变**（=历史债，证明零新增）；`quality_gate` 残留 0；空行位置对齐（aa 空行索引 == 汉化空行索引）；# 头保留；浏览器实测帖间空行出现

**坑**：
- **作者留白可为连续 8 空行**（raw 原始 9 个 `<br>`，长场景停顿/翻页效果）——验证脚本勿假设"作者空行 ≤2 行"，应以「空行组与 raw 原始 br 序列一致」为准
- 迁移后 **line_meta_v2 行号过时**（提取加空行），下次重译/pass 前须 `line_classify_v2 --work` 重新生成（ckpt 有 `__lines` 指纹自动丢弃，安全）
- 其他站审计结论：2nt 空行是真模板（每行后 `<br>\n<br>`，跳过正确）；touhou/yaruobook 中间空片段是**连续 5 行组**（模板特征，未动）——只有 kikkoman（yaruomatomex）作者单行/长空行分镜需保留
- 迁移前备份：`cp -r 汉化 汉化_backup_before_blank`（git 已跟踪的可不备份）

## 通用坑

- **patch 工具会破坏 CRLF 脚本中的字符串字面量**（本会话两次踩坑）：`scripts/*.py` 多为 CRLF，`patch` 的模糊匹配有时会把字符串里的 `\r\n` 转义写成**字面换行**，产生 `SyntaxError: unterminated string literal`（如 `strip('"\r\n')` 被破坏成 `strip('"<LF>\n')`）。这类错误 patch 常报"已应用"但没真正修复。**处理**：①先用 `sed -n 'N,Np' file | cat -A` 看清实际字节；②patch 失败两次后改用 `execute_code` 里的 `io.open(p, encoding='utf-8', newline='')` 做**字节级精确替换**（构造含 `\n` 字面换行的 bad 子串 replace）；③改完 `python -m py_compile` 验证。教训：对 CRLF 脚本，`read_file` 会判二进制（须用 terminal 读），patch 也易出错——优先 execute_code 字节操作。
- **patch 的 JSON 参数层会把 `\r`/`\t`/`\uXXXX` 解码成真实控制字符**（2026-08-10 第三次踩坑）：写含反斜杠 Windows 路径的代码时，`new_string` 里 `r"\やる夫のねっとり貞操逆転世界\raw"` 的 `\r` 在 JSON 解码层变成真实 CR → registry.py 字符串内嵌 CR，`SyntaxError: unterminated string literal`，且 patch 报"已应用"但文件仍坏、read_file 显示正常（被行渲染掩盖）。**处理**：含反斜杠路径的代码改动一律用 heredoc `python - <<'PYEOF'` 写修复脚本（禁 `python -c` 多层嵌套引号）；构造路径用 `chr(92)` 拼反斜杠或 `repr` 验证；改完 `ast.parse`/`py_compile` + `grep -nP '\r[^\n]'` 双校验。
- **patch 另一种拒绝模式："Escape-drift detected"（2026-08-31 本会话踩坑）**：当 `old_string`/`new_string` 含反斜杠（如 `paths=("<hermes>\\.env",)`）且工具-call 参数被 JSON 额外转义一次时，patch 报 `Escape-drift detected: every backslash run in old_string is exactly twice as long as in the matched region`——工具拒绝（比静默损坏更安全，但含反斜杠行改不了）。**处理：统一用 `execute_code` + `io.open(path, encoding='utf-8', newline='')` 做字节级 replace 最稳**（`newline=''` 保留 CRLF；匹配含换行 old 时用 `NL = '\r\n' if '\r\n' in src else '\n'` 拼接；逐段断言 `src.count(old)==1`；全部替换后一次性写盘 `io.open(path,'w',encoding='utf-8',newline='').write(src)`）。这是改 CRLF+反斜杠文件的当前最佳路径，比挨个试 patch 高效。

- **usage 采集必须调用时读 env，不能导入时缓存**：`_USAGE_FILE = os.environ.get(...)` 在模块导入时一次性读取，若之后才设 env 会永远缓存 None。改 `_usage_file()` 函数每次调用读 `os.environ.get`。

- **disp_width 旧副本是"0 错位假象"的头号来源（2026-08-11 キッコーマン 61-70 话实战）**：`scripts/aa_translate.py` 自身都曾带着旧版 `sum(1 if ord(c) < 0x100 else 2 for c in s)`（半角片假名虚高 6 格/行），症状是**两工具口径互相矛盾**——quality_gate（`T.disp_width` 指向 aa_translate 旧版）报 14 行错位，scan_width（aa_common 正确版）报 0。审计：`grep -rn "sum(1 if ord(c) < 0x100" scripts/ --include="*.py"`（2026-08-11 实测仍有 5 处副本：verify/scan_width.py、verify/scan_width_fc2.py、verify/analyze_mismatch.py、generate/postprocess.py、works/postprocess.py——后两个是已交付作品生成器，改它会改变交付物字节，须单独批次处理）。快速判据：`python -c` 对同一行打 `T.disp_width` 与 `C.disp_width`，不一致即有人在用旧版。修正 aa_translate.py 后**旧 60 话 84 行历史错位债暴露**（旧口径掩盖）——这是口径修正的正面价值，不是回归。**修复后必须立即 git commit**（2026-08-11 教训：quality_gate.py 的 disp_width 修复因未提交，后续 `git checkout --` 回退误操作时被一并回退丢失，二次重修；全数据 git 仓库中"工作区修复"不可靠，只有 commit 才防回退）。
- **bash 重定向日志被删**：实验脚本若 `shutil.rmtree(EXP_DIR)` 会连带删掉 bash 重定向进该目录的日志文件——只清子目录（如 `exp_汉化/`），不删根目录。
- **argparse 在 main() 内会误解析 sys.argv**：被 `exp_*.py` 程序化调用时（`main()` 嵌套）会吃外层参数报 `unrecognized arguments`。把参数解析移出 main，改成 `main(batch=0, workers=4)` 签名。
- **meta 行号 vs list 索引**：line_meta 行号是完整 txt 行号（1-based，含 `#` 头行），`txt_lines` 是 0-based list。取内容用 `txt_lines[ln-1]`，否则越界/错位导致混淆矩阵失真。
- **交付文件也有真实残留**：干净副本跑 pass3 仍可能改行（如角色名 `ポッポ` 白名单外被误译「波波肉」）——测基准别假设残留为 0。
- **反编译库对 pyc 3.11 常不可用**（decompyle3/xdis/uncompyle6 探测为 False）——优先从 git 恢复源码，比反编译准得多。
- **平铺版与三层版 `aa_translate.py` 同名模块冲突**（全量测试必踩）：`scripts/aa_translate.py`（生产平铺版）与 `scripts/common/aa_translate.py`（三层版，含 `CHAPTER_WORKERS`）同名。`python -m unittest discover` 从 `scripts/tests` 全量跑时，`sys.modules` 先被平铺版占据 → `test_pipeline.py`（import common 版）报 `AttributeError: module 'aa_translate' has no attribute 'CHAPTER_WORKERS'`，mock 也打到平铺版上（`0 not greater than 0`）。**单独跑 `python -m unittest test_pipeline` 全过**（其 `sys.path.insert(0, scripts/common)` 生效）——失败是命名空间冲突不是数据丢失。修复：test_pipeline.py 顶部 `sys.modules.pop('aa_translate', None)` 或 discover 前清缓存。
- **薄壳 re-export 三个坑（2026-08-11 合并实战；2026-08-18 已由 _flat_loader 根治大部分）**：
  1. **不能用 `from aa_translate import *` 转发**——薄壳自身注册为 `aa_translate`（sys.modules 已被占用），`import` 会命中自己形成循环（症状：`dir(COMMON)` 只有 main/os/sys，process_chapter 缺失）。现在统一走 `common/_flat_loader.py`：`_is_real_flat` 检测 sys.modules['aa_translate'] 是否为真平铺版（process_chapter/main 特征），是则复用，否则按独立名 `aa_translate_flat_shared` 加载。**薄壳被当作 aa_translate 加载时（common 在 path 前）也能正确转发**。
  2. **`sys.path.insert(0, ...)` 是压栈**：最后插入的排最前。要让 scripts/ 优先（`import aa_translate` 命中平铺版而非薄壳自己），scripts/ 必须**最后** insert（先插 common/works 再插 scripts/）。⚠️ 2026-08-18 实测：即使 common 在 path 前命中薄壳，薄壳经 _flat_loader 仍转发真平铺版，功能正确。
  3. **平铺版加 `--work` 模式需补 sys.path**：`import registry`（在 works/）会 `ModuleNotFoundError`——平铺版顶部原本只 import 标准库，加 --work 后须 `sys.path.insert(0, os.path.join(_THIS_DIR, "works"))` 等。
  4. **mock 必须打 FLAT_MOD（2026-08-18 新增）**：薄壳 `A.call_api = fake` 只改薄壳命名空间，`A.translate_lines`（转发 FLAT_MOD 函数）内部仍走 FLAT_MOD.call_api → mock 失效打真 API。正确：`A.FLAT_MOD.call_api = fake`（或 `import aa_translate as T; T.call_api = fake`）。test_pipeline 已修复并全绿。

## 分支切换事故后核查方法（2026-08-10 分支事故复查沉淀）

切换分支导致工作区文件被删后，核查恢复完整性 + 本地仓库同步状态：

1. **分支文件完整性**：`git ls-tree -r --name-only <feat> -- scripts/ | grep '\.py$' | sort > /tmp/a.txt`，同样对 main，`comm -23 /tmp/a.txt /tmp/b.txt` 为空 = feat 的 py 全在 main（0 缺失）。
2. **三层结构跟踪确认**：`git ls-files scripts/common/ scripts/sites/ scripts/works/ scripts/tools/ scripts/tests/ scripts/legacy/ | wc -l` 非 0 即已入库。
3. **平铺 vs 工作区一致性**：`comm -23 <(ls scripts/*.py | xargs -n1 basename | sort) <(git ls-files scripts/ | grep '^scripts/[^/]*\.py$' | xargs -n1 basename | sort)` 空 = 无未跟踪脚本。
4. **本地裸仓库同步状态**：`git --git-dir=../aa-translation count-objects -v` 显示 count=0 → **裸仓库从未 push**（数据只在工作区 + .git）。`git ls-remote origin` 空 = 未同步。M 里程碑稳定后补 `git push origin main`。
5. **作品数据完整性**：逐作品对比 `ls <作品>/aa/*.txt | wc -l` vs `ls <作品>/汉化/*_汉化.txt | wc -l`（应相等）；さーきー 有两套（bookshelf: aa/汉化 + FC2: aa_fc2/汉化_fc2 + work_fc2/line_meta）。
6. **manual_fix.json 缺失 ≠ 数据丢失**：全盘 find 无结果 = 当前无人工修复记录（0 记录 = 0 覆盖风险），符合预期。
7. **恢复验证收尾**：重跑 `quality_gate.py --work キッコーマン` 全量（零 API）确认 60 话 0 失败/0 错位/0 残留，与终审结论一致即无回归。

## 吸收内容已完全在 main 树的分支 → `git merge -s ours`（2026-08-11 f147a18 实战）

合并本地分支前先做**树包含关系分析**，main ⊇ feat 时 `-s ours` 零风险（合并记录历史、树不变）：

1. **分析序列**：`git merge-base main feat` → `git log --oneline main ^feat`（main 独有）/ `git log --oneline feat ^main`（feat 独有）→ `git diff --name-status feat main | awk '{print $1}' | sort | uniq -c` 统计 A/M/D。实测 feat→main 是 4688 A + 3-4 M + 0 D = **main 树已完整包含 feat 全部内容**。
2. **人工核对仅有的几个 M 内容文件是否有单边数据**（`git diff feat main -- <file>`）：AGENTS.md/.gitignore 以 main 版为准（live 配置）；**glossary.json 类数据文件必须逐块比对**——`git show feat:glossary.json` 与磁盘版用 python 按 key 求差集。
3. **白名单/glossary 分叉决策（重要项目事实）**：feat 版多 63 条 texture 角色 + 196 条 known_names，但 main 版是**生产验证过**的（40 话回归、M 系列打磨）——合并**不带走**白名单增量（盲目合入有残留漏报回归风险），以 main 为准；确实需要时再选择性合入。白名单权威版 = main。
4. **执行**：`git merge -s ours feat/xxx -m "merge: 吸收 ..."` → `git log --oneline -1`（合并提交、双亲含 feat）→ `git diff --quiet <合并前main的HEAD> HEAD` 应为 0（树零变化；若中间有他人提交如「无用数据删除」，以那个提交为基准比）。
5. 合并后旧恢复命令（`git checkout 379968c -- scripts/...`）仅适用于历史分支，日常不再需要。

## 验证

- 零 API 的验证优先：比较 `is_text_line` vs 新 meta 的 flagged 行集（量化 token 节省）、混淆矩阵 + diff 明细、`is_text_line()` 抽检差异行确认无漏译风险。
- 缺 canonical 测试命令时，用 `tempfile` 路径下的 `hermes-verify-` 前缀脚本做 ad-hoc 冒烟验证（编译 + 函数存在 + 行为断言），跑完即删。注：这是冒烟，不是测试套件绿灯。
- 单元测试放 `scripts/tests/`（本会话项目已有 `test_manual_fix.py`，零依赖 unittest）。


> **本目录共 48 份 reference**；下表只列常用/权威的 26 份，其余（`ls references/` 可见）为各轮实测留档，按需读取；`doc/TODO/*` 链接均已随该目录 2026-09-07 删除而失效。
## 支持文件

- `templates/batch_cleanup_driver.py` — **批次续译清理驱动**（pass2/pass3/fix_width 只处理指定 eid 新章，AA_USAGE_TAG 打标；替代全量扫描，清理 token 占比 18.9%→3.8%）。用法见「批次续译标准流程」
- `scripts/promote_mixed_kikkoman.py` — **阶段2 增量推广驱动**（只重译 mixed 行 conf text 段；非 mixed 行保留；断点续跑 progress.json + fail.tsv + AA_USAGE_TAG 打标）。用法见「阶段2：mixed 段级翻译全作品推广」（同目录下已部署为 scripts/works/promote_mixed_kikkoman.py，本文件为 skill 副本）
- `references/prompt-cache-and-cost-2026-09-15.md` — **前缀缓存命中 + 计费成本口径**（先立口径再谈折扣：记录 cache_hit、0 与缺失分开、缺失单价不猜钱；实测只有官网 deepseek 命中 79.6%、网关 0/0/0、并发不击穿缓存；章内 pin 61.8%→89.6%；公共常量头 0%→55.8%；按章锁定否则反升 1.9×；坑：自动 AA_BACKEND 让 pin 静默失效、按 kind 统计≠真实送模型行集、meta loader 要 entry_ 前缀）
- `scripts/usage_stats.py` — usage.jsonl 按 tag 聚合（calls/prompt/completion/total + 分钟序列），推广批次监控用
- `references/refine-parallel-ab-verify-2026-09-15.md` — **「逐段模型调用 → 段级并发」改法与等价性验证**（量化：usage 单次规模/调用间隔/零 API 探针 CPU 分解；两段式 job + ThreadPoolExecutor + 按行号合并；并发隔离：模块级全局 hint、env tag hack、usage 落盘锁；**等价性 A/B**：git show 旧版 + 确定性 mock + edits/stats/调用数逐条比对 + 夹具须用**纯假名**防日文汉字误判；实测 refine_aa 314.4s→6.0s（52×）、8 作品 16 章逐条相同）。配套工具 `scripts/tools/ab_refine_verify.py`。改管线任意环节的串行性能先读它
- `scripts/migrate_blanks.py` — **空行迁移工具**（解析器改保留作者分镜空行后，重新提取 + 旧汉化非空行序列回填，零 API；含前置/后置校验）。用法见「解析器空行修复 + 已交付零 API 迁移」
- 恢复的源码与 v2 分类器、eval/验证脚本均在 `<repo>\temp\layered\`（不入 git，跨会话可复用基线）。
- **EXPERIMENTS.md 已移至 `doc/EXPERIMENTS.md`**（2026-08-10 起，根目录不再有）；完整分步记录见 `doc/EXPERIMENTS.md`、`doc/M0-M5_完整总结报告.md`（M0-M4a 旧版已标记过期）、`doc/现状评估_2026-08-10_分支事故复查.md`。
- `references/paid-chain-endpoint-roundrobin-2026-09-02.md` — **付费链端点均匀轮询（用户硬纠正语义+实现）+ r18 链同步接入 sf 双模（两链同构）+ 网关级内容过滤矩阵（M3/miMo/tokenrhythm 多平台浓度梯度）+ tokenrhythm 端点发现法**。付费链改动/新平台内容过滤探测先读它
- `references/legacy-bugs-2026-08-27.md` — 遗留 bug 清单（fix_width_mixed_lines NameError 最优先 / test_aaweb 期望过期等，stash 基线实证与 2026-08-27 改动无关）
- `references/git-stash-ab-regression.md` — **回归失败归因 A/B 法**：改脚本后测试失败，`git stash` + 干净 HEAD 重跑对比，diff 空=历史遗留零新增，diff 非空=本次引入（2026-08-27 作品知识库实施实证）；**2026-08-30 增补 worktree 跨分支基线对比法**（对比旧 commit 用 worktree 独立目录，禁 `git checkout <旧> -- path` 污染主工作区）+ 偶发失败三连跑确认法（env 泄漏/时序竞态定性）
- `references/implementation-audit-2026-08-27.md` — **实施审核方法论**（方案落地后核对实现）：commit message 逐条对 diff 核销、worktree 基线判回归、手编数据文件过格式化提交的存活验证、6 类高频 bug 模式（死区数据/early-return 吞新逻辑/门控缺失/多行截首行/AI 生成无既有资产对照/message 与代码不符）
- `references/review-protocol.md` — 审阅报告规范、flag 滞留检测、交叉验证证据、闸门曝光 vs 引入判定、审阅检查清单
- `references/free-model-platforms-2026-08.md` — 国内线上免费模型平台速查（已接入六态表 + 未接入候选 11 平台 + 新后端接入模式），评估/接入新免费后端先看它，实测数据以 doc/线上免费模型统计.md 为准
- `references/mixed-segment-translation.md` — mixed 段级翻译 1 话实验明细（A/B 数据、实现要点、迭代降回退率、宽度列测量陷阱、**严格帧线字符集（勿含日文引号『』）**、单话三栏对照渲染 temp/render_exp_compare.py 及其 5 坑（含**对照 HTML 数据源时间戳陷阱**：渲染脚本读固定路径，重跑产物后 HTML 不自动更新，排查先比 mtime）、**行首空白保护 B3 补丁（fe30d19：lead 截取+strip 双份+pad 到 src_width-lead-译文）**、**加测 2 话（17512/18015）全指标达标 + 帧偏移口径修正（回退行另列）**、**fix_width 段级同步（0335fb3：只重译 text 段+整行宽==SRC 才接受+mock call_api 零 API 单测）**、翻 flag 前置清单（fix_width 已闭环，FC2 meta 仍 v2 待补））
- `references/usage-call-star-miss-2026-08-20.md` — **usage call_* 漏记录坑**：2026-08-20 修复 9 个老后端；**2026-08-28 复发——call_opencode_zen 漏 `_log_usage`（3 个免费模型走它 usage 全 0）**，排查法 = grep backend 计数。教训：**新增任一 call_<后端> 函数验收必含"usage 该后端记录>0"**
- `references/new-site-onboarding.md` — 新站点接入速查（分类页→正文定位→dl 注入→解析器→registry→试译→批量；含 JS 壳站 API 注入模式、colormap 0-based 坑、视觉模型幻觉坑；**含 2026-08-28「已有作品换站 + 分类目录标题匹配选章」**：yaruobook.jp cat=446 多作品混排 + `紲星\s*あかり` 标题匹配 + chapters.json 重建 + eid 前缀剥离坑）
- `references/refine-pass-design-2026-08-29.md` — **精译独立环节设计**（grill 拍板：独立 pass 不动 proofread / 输出 `精译/` 目录不覆盖汉化 / B 分块+长前缀续接 / 读 `_汉化.txt` 精译右侧台词保护 art / 不加视觉模型 / V4-Flash 1M 上下文+16K 输出上限是「分块+前缀」硬依据 / 免费链当校验助理）。新环节实现前先读它，勿重开 Q1-Q4
- `references/refine-impl-2026-08-30.md` — **精译实现落地 + 关键坑**（refine_runner 用**中日文台词表配对**而非设计文档的原始 _汉化.txt+line_kind；通道因 SiliconFlow V4-Flash 挂起改为 **commandcode deepseek-v4-flash 推理模型**——必须 `reasoning_effort=low` + `thinking:{type:enabled}` + `max_tokens=32768` 三件套，`call_commandcode` 不处理 deepseek 需自建 `_call_commandcode_ds`；**COMMANDCODE_API_KEY 新 key 在 translation/.env 不在 <hermes>/.env 否则 HTTP 400**；台词净化 `_clean_dialogue_text` 剥 art 前缀+弃纯 art 段；`_has_kana` 新旧对比只拒新增假名；批量重试不逐段；**工作台精译档=合并「汉/精」单按钮+快捷键 h+紫色区分**（非独立第三档，`reader.js?v=20260830h`）+ stopPropagation 防双触发；用户硬约束=勿随意改 extract_dialogue/勿用个例判对错/1步步来/测试作品以当前指令为准）。**继续精译先读它**
- `references/original-anchored-variant-replace-2026-08-30.md` — **原文锚定变体替换**（术语表只匹配残留日文，但同一日文词被模型译成多种中文变体——以原文为锚点定位已译变体并统一）。含 gen_variant_extract.py（模型逐行归纳变体 → variant_<词>.json）+ apply_variant_replace.py（auto_select/词根分组/长词优先）；**坑**：变体提取用 `call_with_fallback` 勿用 `call_api`(cloud 直连长 body 空串)、hits 行号不可靠→整章无边界长词优先替换、词边界会漏中文人名(带桑/酱/亮亮后缀)、模型把きらきー(人名)误听成きらきら(闪闪发光)、用户偏好分开处理(简称→绮晶/全名雪华绮晶保留)。术语相关技能 aa-glossary-roles(用户拥有) 也覆盖此域，新词推广前先读本 reference
- `references/dialogue-extraction-benchmark-2026-08-29.md` — **台词提取基准测试 + v3 meta 实现事实**（2026-08-29 r401_ryouri 实测：export_dialogue 启发式漏识 66.6%/误识 0.4%，根因=对「左 art+右台词」mixed 行失效）。**第二轮修订：基准从 `line_kind` 升级到 v3 meta（line_classify_v2, kind/confidence/segments 段边界），line_kind 降为宽度校验口径**。含 **conf<0.65 段数据纠正**（347 段 93% 是纯艺术假名碎片，仅 24 含真实假名——「全部提取 conf<0.65」假设被实测推翻，实现应加真实假名门禁）、v3 meta 结构事实（key=1-based 行号从 3 起、segments 半开区间、segment_line/_seg_confidence）、新模块 `scripts/common/extract_dialogue.py` 接口（extract_dialogue_from_meta 返回 line_no/seg_idx/kind/text/seg_start/seg_end/conf/op）。**精译架构前置**——用户要求先量化台词提取精度再定精译输入。所有台词提取/精译/工作台高亮相关改动先读它（三套口径已统一到 v3 meta）
- `references/dialogue-extraction-impl-2026-08-29.md` — **extract_dialogue.py 实现细节 + 实测数据**（2026-08-29 第1步落地）：统一到 v3 meta 单一口径；**含真实假名门禁 `_has_real_dialogue`**（鉴台词必须含真实假名/非艺术汉字，弃纯装饰 `ミヽメメ` 碎片；text 整行判、mixed 逐段判）；行号口径 `raw=txt_lines[key-1]`；meta 缺失自动生成（`_ensure_meta`，raw 缺失抛错中断）、import 顺序坑（`classify_raw_v2` import 必须在 `os.path.exists` 之后）、`line_classify_v2` 在 tools/ 需注入 sys.path；r401_ryouri 39 章 8082 段（约旧 1.7 倍，补上漏词台词）。**2026-08-29 四/五/六轮补全**：⑤ 帖头行未跳过 bug（须 `is_hdr` 无条件跳过）+ **62% 误报真根因=坏 meta 数据**（先 regenerate 排数据再谈门禁）；⑥ **meta 修好后用 gold 做 A/B 回测调门禁**——误标 94/漏标 12 两类，候选规则对比（装饰黑名单+run≥2+支持半角片假名胜出：误标 69/94、漏标 9/12、真台词 0 误伤；text_ratio 误伤 `おとうさま` 被否），**+用户偏好「宁漏勿伤，优先少误标」**；⑦ 半角拟声 run 修复（`ﾞﾟｰ`/`ｧｨｩｪｫｬｭｮｯ` 紧跟前假名继承 run 不断——`ｶﾞﾋﾞｰﾝ` 1→3、`ｷｭﾑｷｭﾑ` 2→4）+ 卷积借邻行尝试**放弃**（卷积救漏标但放回装饰、且半角修复后价值被吃——纯负收益）。**2026-08-30 第八轮**：**三套口径统一收编完成**（`parse_dialogue_file`/`ensure_dialogue_file` 统一接口，export_dialogue/gen_chapter_summaries/gen_glossary_ai 全部改走 v3 meta，删各自占比启发式）+ **第九轮：收编后用 translate_runner 真跑 1-3 章验证原流程端到端可用**（zhipu normal 链首 glm-4.6v-flashx，meta 自动生成 + 原文台词表自动产出 + 简介正常 + 行数对齐 + art 保护）。实现/验证前先读它
- **token 合并统计工具 `scripts/tools/usage_total.py`**（2026-08-11）：AA 管线(usage.jsonl) + Hermes 对话(state.db session_model_usage) 双源合并，`--since/--until` 时间窗；每次任务完成后把合并数字追加进 `doc/同步记录.md`（单文档持续追加、勿覆盖历史，条目格式：任务摘要/变更点/token 消耗合并表/校验结果/遗留事项）——项目同步记录的唯一权威文档

## usage.jsonl 模型↔翻译行 lines 字段（2026-09-04 落地 + 三坑）

**目的**：每条翻译调用记录本次翻的行号列表，后期按模型比对各行质量。**实现**：aa_backend `_USAGE_CTX`(threading.local) + `set_usage_lines()`/`clear_usage_lines()`；aa_translate 在 **`translate_lines`(1051) 与 `translate_lines_mixed`(1309) 两处**每次 call_api 前 set `sorted(keys)`、finally clear。聚合工具 `scripts/tools/usage_by_model.py`（--task/--tag，backend/model/calls/prompt/行总/行去重/行范围）；测试 `scripts/tests/test_usage_lines.py`（3 用例）。

- **坑①：只给 translate_lines 加 = mixed 路径漏记（本会话实测）**——force 重译 20992 时日志 `0 chunks of 50 + 1 mixed-seg chunks`（295 flagged 主块行全在 ckpt done 里，实际只重翻 mixed 段），usage 翻译调用 0 条带 lines。诊断口诀：**先看 runner_log 的 chunk 结构判断本次翻译走哪条函数**（`0 chunks of 50` = 只走 mixed），新加翻译路径的埋点必须双函数都覆盖。**已修（2026-09-05）**：`translate_lines_mixed`(aa_translate.py:1309) 也 set lines（按行号去重 `sorted({num+1 ...})`）。
- **坑②：多模型同 lines ≠ 降级链，是 3 轮重试循环**——同 23 行 3 条记录（Qwen/flash/muse 各一、ts 隔 8-10s、prompt≈同）= `translate_lines_mixed` 的 `for round_ in range(3)` 每轮独立 call_api + 健康态端点轮询换到不同模型（降级链会钉 `_PREFERRED_BACKEND`，round2 不会换模型）。前两轮输出全校验被拒（`@ 106-1690: +2/23` 只成 2 行）→ pending 未减 → 每轮 jobs/lines 相同。
- **坑③（行号口径缺陷，2026-09-05 已修）**：usage `lines` 曾为 **0-based**（process_chapter `enumerate(lines)` 的 idx 直传，aa_translate.py:1620/1641），而工作台阅读器行号是 **1-based**（aa_web_app/main.py:1067 `i+1`，reader.html data-line）——差 1，用户对照必错位。已修：translate_lines(1051)/translate_lines_mixed(1309) 的 set 改 `sorted(x+1 for x in keys)`，usage 行号现在 = 1-based 文件行号 = 工作台阅读界面行号（test_usage_lines 只测 set/get 往返、不涉及 +1 转换，无需改）。
- **坑④（归属语义）**：`lines` = 本次调用**尝试**翻的行，非**最终采纳**的行——校验失败行保留旧译文/旧模型产物；按 usage lines 归属模型会虚高，看日志 `@ N-N: +X/Y missed=[...]` 的 X 才是实际采纳数。
- **多路径验证法（本会话探针序列）**：cloud 单进程 / call_with_fallback 单进程 / Popen 子进程 / ThreadPoolExecutor worker 全落盘而 production 任务不落 → 差异不在环境而在**走的函数分支**（mixed vs 主块）——验证新埋点先列全调用路径再逐个 probe，勿只测一条就假设全链路生效。

## 审阅协议（2026-08-10 确立）

每次里程碑完成或进度审阅后，**必须产出审阅报告**：
- **路径**：`<repo>\doc\审阅报告_<日期>_<主题>.md`（不进 temp/，git-tracked）
- **固定章节顺序**：
  1. 总体评估（1-2 句结论）
  2. 前置风险核销表 — 逐项核对上次审阅的「下次审阅确认点」
  3. 交付物核实 — 行数/功能/测试通过率/交叉验证证据
  4. 数据可信度证据 — 交叉验证、抽检结果、独立工具互证
  5. 风险清单 — 新发现 + 遗留未解，按优先级
  6. 下一步行动 — 按优先级 + 预估工时
  7. **下次审阅确认点清单**（强制章节，bullet checklist；下次审阅时必须逐项核对）
- 审阅报告不可只读 EXISTING 报告做摘要 — 必须独立核实代码、测试、数据文件

## Flag 滞留检测（最高杠杆行动模式）

里程碑完成后，已验证通过但 feature flag 仍未翻转 → **这是当前最高杠杆行动**：
- 审计方法：`grep -rn "os.environ.get" scripts/` 找所有 flag 读取点
- 已验证但未翻的 flag 按收益排序作为最高优先级建议
- 强调措辞：**「翻 flag 不需要新代码，只需要决策」**
- 典型场景：M2 验证 2.53x 加速、M4a 验证 -40% token，但三个 flag 全部滞留 0，收益无法兑现

## 交叉验证作为可信度证据

独立开发的工具/验证对同一问题收敛于相同结论 → 比单一测试通过更有说服力：
- 例：quality_gate G4 发现 5 处真漏译（模型整行照抄日语），精确匹配 M4b `is_copy_failure` 的拦截目标
- 审阅报告应主动寻找并突出这类交叉印证
- **数据级吻合**（如 metrics_report total 3,252,352 与《统计对比报告》完全一致）是最硬的证据

## 闸门曝光历史债 vs 闸门引入

质量闸门首次上线时，常会系统暴露之前完全不可见的历史缺陷：
- 例：60 话 111 行宽度错位（`棍棒が折れたお→棍、棍棒断了` 宽 102→100）
- **不是闸门引入的 bug** — 抽检 25/25 与底层 `disp_width` 全一致证明闸门无误报
- 审阅报告应明确区分「闸门引入的新问题」vs「闸门曝光的旧债」— 后者是工具上线的正面附加价值
