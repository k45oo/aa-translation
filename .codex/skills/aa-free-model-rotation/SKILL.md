---
name: aa-free-model-rotation
description: Use when 开免费模式翻译(AA_FREE_MODE=1)或轮换/排查FREE_MODELS链.
---

# AA 免费模型链轮换与使用

> 权威事实源：`scripts/common/aa_backend.py` 的 `FREE_MODELS` 注释块 + 代码本身；本技能是操作手册，**动手前先读 aa_backend.py 最新注释**（2026-09-05 已 15 条，历史技能里的旧链序作废）。

## 快速使用（免费翻译怎么开）

- **工作台翻译控制台**「模型」下拉 → 免费模型 = `free_mode=true` → env `AA_FREE_MODE=1`（aa_web_app/main.py:673/2391）。
- **CLI 任务管理器**：`python aa_web_app/task_manager.py enqueue --type translate --work <id> --chapters <eids> --free-mode`（= `--env AA_FREE_MODE=1`）。
- **直接跑 runner**：`AA_FREE_MODE=1 python scripts/runner/translate_runner.py --work <id> --chapters new`。
- **免费任务并行上限**：任务级 `free_translate`=1（data/tasks_parallel.json 可改）；章节级强制串行=1（translate_runner）；章内块级 `AA_FREE_WORKERS`（默认 3，任务管理器对免费 translate 注入 10）。
- **例外（用户 2026-09-03 指定，勿改）**：划词翻译 `aa_web_app/selection.py` **不用 FREE_MODELS 全链**——只走 siliconflow 双模：`THUDM/GLM-4-9B-0414`(SILICONFLOW_FREE_API_KEY 免费通道) → `Qwen/Qwen3.5-27B`(主 key)。

## 调用路径与机制

`AA_FREE_MODE=1` 时：`call_with_fallback`(aa_backend:623) 与 `aa_translate.call_api`(:851) 短路到 `_call_free_chain`（aa_backend:565）——**翻译/校对/清理/漏译补译/iter_glossary 全走免费链**（iter_glossary `--backend` 参数免费模式下被忽略, aa_translate:851 同）。

- **FREE_MODELS 结构** = `(模型名, 后端, key)` 三元组；`key=None`=平台主 key，`key="FREE"`=`*_FREE_API_KEY`（独立配额，只用于免费模型）。同模型拆 FREE+主 key 双通道 = 并发/配额翻倍。
- **轮换**：`_free_round_robin_start()` 线程安全轮询指针——多 worker 各从不同模型起步，天然分散多平台并行（各平台限流独立），避免全抢链首。
- **无限重试**：单模型失败换下一个（间隔 2s）；整轮全败 → 指数退避 `AA_FREE_BASE_BACKOFF`(10s) × 2^n 封顶 `AA_FREE_MAX_BACKOFF`(300s) + jitter；连续 `AA_FREE_MAX_ROUNDS`(5) 轮全败抛 RuntimeError（块落 review 人工，不假死）。
- **内容被拒**：返回含 `无法协助/不能协助/无法翻译/不能翻译/无法帮你/不能帮你` → 该块按漏译跳过（返回空串不重试不崩章）；异常形式含 `拒绝/无法` 同处理。

## 当前 FREE_MODELS 链（2026-09-15，14 条）

| 模型 | 后端 | key | 备注 |
|---|---|---|---|
| THUDM/GLM-4-9B-0414 ×2 | siliconflow | FREE + 主 | 链首最快无过滤 |
| mimo-v2.5-free ×2 | opencode_zen | 主 + FREE | 快+稳 |
| big-pickle ×2 | opencode_zen | 主 + FREE | 偶发连接失败重试即过 |
| ling-3.0-flash-fin-free ×2 | opencode_zen | 主 + FREE | 1.6s 快+稳 art 好 |
| nemotron-3.5-lightning-free ×2 | opencode_zen | 主 + FREE | 3.0s 全对 art 好 |
| nemotron-3-ultra-free | opencode_zen | FREE | 50s 慢，兜底位 |
| meituan/longcat-2.0:free | commandcode | 主 | 9-11s 全对 art 好；**日配额 100 次** |
| poolside/laguna-s-2.1-free | commandcode | 主 | 快；间歇 429（上游过载）自动轮换 |
| inclusionai/ling-3.0-flash-sante:free | commandcode | 主 | **链尾兜底**；免费池质量最高（基准 9 项全 100）但慢（4~60s/块） |

**条目结构 = `(模型, 后端, key[, max_tokens])`**：第 4 元素可选 = 该模型专用输出预算（2026-09-15 加，为推理型免费模型准备）。**推理型免费模型务必带它**——链默认 `max_tokens=16384` 会被思考烧光 → 空 content 被当失败轮换。sante 带 `32768`（16384 下 30 行困难块 3 次重试全空，65536/32768 满分）。

**2026-09-15 变更**：**commandcode free key 通道整体移除**（`COMMANDCODE_FREE_API_KEY` 与主 key 在免费模型上共用同一日配额——LongCat 两侧同报「used all 100 free requests」；且 free key 无 credits 调不了付费模型）→ FREE_MODELS 删 2 条、`_cc_free_key()` 删、两处 .env 删行。commandcode 免费模型只剩主 key 通道。
**commandcode 免费池第三个模型（同日入链）**：`inclusionai/ling-3.0-flash-sante:free`——基准 9 项全 100（含 R18/art 列位/30 行困难块），但 `reasoning_effort=none` 被网关 400 拒（枚举只到 low|medium|high|xhigh|max）、思考关不掉 → 必须给大预算。详见 `doc/线上免费模型统计.md` 2026-09-15 节。

**已退役/排除**（勿加回）：minimax/minimax-m3-free（403 "free MiniMax M3 and M2.7 models have been retired"）；hy3-free（目录消失）；GLM-Z1-9B（推理模型假死元凶）；stealth/ox-alpha（下架）；muse-spark-1.3-contributor-free（官网列出但 500 全挂）；GMI MiniMax-M3/M2.7（内容过滤强 + 拒绝语是日文「言えません」检测不到 → 静默漏译，保留 call_gmi() 手动用）。

## key 与环境变量

- 免费 key（translation/.env，`_read_env_key(name, _env_file_paths())` 读，未设回退主 key）：`SILICONFLOW_FREE_API_KEY` / `OPENCODE_FREE_ZEN_API_KEY`。**`COMMANDCODE_FREE_API_KEY` 已于 2026-09-15 移除**（与主 key 共用免费配额，双通道无增益）。
- 平台：siliconflow=api.siliconflow.cn；commandcode=api.commandcode.ai/provider；opencode_zen=**opencode.ai/zen/v1/chat/completions**（free 模型仅 zen chat 端点可用，go 网关 401；必须 `reasoning_effort:none` + 浏览器 UA）。
- 端点接入新后端 → 先看 `_call_free_model`(aa_backend:537) 分发 + 各 `call_*` 的 key 参数。

## 目录轮换 SOP（免费模型会轮换/下架，入链前必探针）

1. **拉平台目录核对可用名单**（聊天粘贴可能被折叠截断）：zen `https://opencode.ai/zen/v1/models` 带 key；commandcode/siliconflow 用探针脚本试。以**官网免费文档**为准（zen /models 里有但不在官网文档的不入链）。
2. **探针用管线真实口径**：6 行 AA 短批块（2 纯台词 + 2 art前缀 mixed + 1 拟声 + 1 台词）+ art 列位检查（art_stroke_preserved 逻辑内联）——现有脚本 `temp/probe_free_models_20260905.py`（cc/zen 两组，改顶部模型名单即用）。判可用：行数够/假名 0/art 字符在/格式全对。
3. **三态区分再动 FREE_MODELS**：**403 或目录消失 = 下架移除**；**503 = 间歇过载保留**（免费链自动轮换兜底）；**429 = 限流保留**（FreeUsageLimitError=配额限流非失效，退避轮换处理）。
4. **入链**：改 FREE_MODELS 加行（同模型拆双通道：FREE key + 主 key 两行相邻）；注释块同步日期+实测依据。
5. **验证**：`AA_FREE_MODE=1` 走一次真实块看轮询/返回；全量 unittest。
6. **改代码只对新启动任务生效**——正在跑的旧进程已加载旧 FREE_MODELS，kill 重跑才吃新配置。

## 假死/卡死排查（usage.jsonl 金标准）

- **usage.jsonl 诊断**：`grep <task_id> usage.jsonl` 逐行解析——①每个任务**最后一条 usage 的 model**=死前最后一次调用，多任务同死一模型=元凶；②`completion>=16000`=max_tokens 截断；③>5min 间隔=卡死点（看两端模型）。
- **推理模型是假死高危**：GLM-Z1-9B 案例——`enable_thinking=false` 对 r1 类推理模型不生效，thinking 混入 content + 超长生成（一次 17246 token 占死 worker 20min）→ 10 worker 全占 → CPU=0 假死。**免费链只用非推理模型**；接入前单请求探测 content 是否干净。
- **finish_reason=length 截断被当成功**（GLM-4-9B 中招）：call_siliconflow/call_openrouter/call_opencode_zen 已加 length 检测抛异常走重试。
- **usage 停滞 ≠ 假死**：免费链退避（10→300s）或内容被拒/格式不达标 fallback 期间可 5-10min 零 usage。kill 前看 runner_log 块日志：`+0/N missed=[...]` = 模型在返回但不达标（如 GLM-4-9B 对 mixed 段格式输出差），重跑同样失败，别 kill。真假死 = CPU 不动 + usage/ckpt 停 ≥1.5h。
- **真死处理**：kill translate_runner + DETACHED 重启（creationflags 0x8|0x200），重启固定 `AA_FREE_MODE=1 AA_FREE_WORKERS=6`，ckpt 断点续跑零损失。

## 免费用例注意（R18）

- 免费链无 R18 区分（全免费）；R18 作品约 70% 台词能过，被拒的留漏译后续补（免费链拒翻按漏译跳过不崩章）。GLM-4-9B/laguna/mimo-v2.5-free/big-pickle 实测 R18 无过滤。
- 真免费模型质量波动大：weak 模型对 mixed 段格式/art 空格保护差（qwen3.5-flash 事故同理）——免费翻译后必须跑 scan_width + 残留扫描，别跳过质量闸门。
- 免费翻译结果 token 成本≈0 但**耗时长**（免费模型慢 + 单任务并行受限），适合非 R18 批量/不赶时间；急活/精译走付费链（refine 不走免费链）。

## 相关技能

- 免费链实现细节/历史：aa-pipeline-optimization「免费模式」节（含过时链序，以 aa_backend.py 为准）；opencode-go-gateway（zen 端点/限速细节）。
- 任务编排：aa-task-manager；R18 安全：aa-r18-ops。
