# 国内线上免费模型平台速查（2026-08-14 调研）

> 完整实测记录、基准方法与追加约定 → 项目 `doc/线上免费模型统计.md`（唯一归属地）。
> 本文件是决策速查：评估/接入新免费后端时先看这里 + 主文档，再按同口径实测。

## 已接入 AA 管线（`AA_BACKEND` 分发器七态）

| 后端 | 免费/付费模型 | 关键坑 | 定位 |
|---|---|---|---|
| `siliconflow`（默认） | THUDM/GLM-4-9B-0414（9B 以下免费不限量）；**deepseek-ai/DeepSeek-V4-Flash（付费：入1/出2/缓存0.02 元每百万，2026-08-29 实测可用，R18 直翻无过滤、10.7s）** | `enable_thinking=false` 必须（**官方思考开关，2026-08-31 起统一**取代非官方 `reasoning_effort:none`；通用/更快更干净）；低温 0.1（0.4 偶发误译） | 顽固行/残留检测主力；V4-Flash 强但付费，作质量优先档（非免费链） |
| `zhipu` | **glm-4.5-air**（默认，**1.2 亿**免费token）/ glm-4.6v（6000万）/ glm-4.1v-thinking-flashx（1000万，**1个月有效优先消耗**）/ glm-4.7-flash（永久免费） | **推理模型须 `thinking:{"type":"disabled"}`**（4.5-air/4.6v/4.7-flash 裸调 content 全被 reasoning 吃光）；4.1v **强制思考 disabled 无效**（max_tokens≥8192 才正常）；1305 过载间歇 429 | 免费额度主力 + 语义复审 |
| `minimax` | **MiniMax-M3**（Token Plan 包月候选） | M3 须 thinking disabled（生效）；**M2.7 思考关不掉且混在 content**（须剥 `<think>`，9-12s 慢不可用）；key 在**翻译档案 env** MINIMAX_CN_API_KEY | 付费兜底/零星 |
| `mimo` | **mimo-v2.5-pro**（默认）/ mimo-v2.5（按量 ¥3/¥6 与 ¥1/¥2 每百万，**全场最便宜**） | 思考走 reasoning_content（content 干净，裸调也可用）；带 thinking disabled 提速（1.9/2.6s）；偶发请求挂起需重试；key 在**翻译档案 env** XIAOMI_MIMO_API_KEY；仅 api.xiaomimimo.com 端点可用 | AA 场景按量最省 |
| `openrouter` | google/gemma-4-26b-a4b-it:free | 连续请求必 429；必须 Referer+Title 头 | 兜底（限流太狠） |
| `ollama` | llama3.1:8b（本地） | 4GB 显存太慢 | 已停用，保留回退 |
| `cloud`（默认无 env） | deepseek-v4-flash（付费） | - | 主翻译 |

## 语义复审方法（2026-08-14 4.1v 实战）

用途：已交付汉化的漏译/误译抽查 + 消耗免费额度。**能抓到假名残留检测的盲区**：

- 方法：`原文行+译文行` 逐行对照分批（**串行 + BATCH 20**；实测并发 3 被限流排队无加速）给模型，严格输出 `行号|问题类型|说明`，无问题只回「无问题」；报告按行号回填原文/译文便于人工核对
- **全作品驱动（消耗 4.1v 额度）**：逐话分批 + **话级断点**（每话完成写 `<作品>__<话>_issues.txt`，已存在即跳过，中断可续）；模型输出的批次级"非标准行"（思考噪声/自我怀疑）直接丢弃；~200 批 ≈ 90 万 token
- **盲区案例**（全部逃过管线假名检测）：汉字混合残留（「お土産」→「お土会」无假名）、日文汉字残留（「事実」不触发 kana 检测）、半角假名残留（ﾎﾞｿ）
- **误报教训（报告只当线索，不可自动回填）**：正确翻译被误判（「乙でした=辛苦了」判错——乙=おつ=お疲れ）、项目术语被按通用译名判错（「亚鲁欧」是 glossary 标准译名）、拟声词主观——用正则按已知模式过滤（乙/亚鲁欧），报告分「真问题候选/待人工/疑似误报」三档
- 4.1v 每批 8-12s（含思考 token 大）→ 消耗额度快；想省额度用 4.5-air 同样 prompt 也行（误报率未对比）

## 未接入候选（2026-08 搜索核实，政策随时变）

### 2026-08-27 测试：GMI Cloud（未接入，内容过滤太强）
- **端点**：`https://api.gmi-serving.com/v1/chat/completions`（OpenAI 兼容；**不是** api.gmicloud.ai——错域名 SSL 失败）
- **Key**：`GMI_API_KEY`（<hermes>\profiles\translation\.env:543）
- **限时免费**（8.24-9.6 MiniMax Week）：`MiniMaxAI/MiniMax-M3`（2.3-8.8s 5/5 稳、质量好）、`MiniMaxAI/MiniMax-M2.7`（3.5-7s、偶发空响应）
- **❌ 内容安全过滤强**：性内容（「おっぱいが好き」）+ 粗口（「この野郎」）都被拒；**拒绝语是日文「言えません。お手伝いできることはありますか？」无中文关键词 → `_call_free_chain` 检测不到，当正常翻译返回 = 静默漏译**（比显式拒绝更糟）
- **结论**：保留 `call_gmi()` 供手动/非 R18 纯良作品用；**不入免费链**。教训：新免费平台测内容过滤必须含粗口 + 检查拒绝语语言


### 永久免费不限量档
- **智谱 BigModel**：GLM-4-Flash / GLM-4.7-Flash 永久免费不限 token，只限并发（错误码 1302 用户限流 / 1305 平台过载）——**已接入**
- 百度千帆：ERNIE-Speed-8K/128K、Lite、Tiny 永久免费不限量（RPM 60-300；2026-04 起新用户须实名）
- 讯飞星火：Spark Lite 永久免费不限量，但 **QPS=2 太严**、无 system 角色 → 不适合 AA 批量

### 可持续领取档
- **火山方舟**：新用户每模型 50 万 token；协作奖励计划二期「今日用量=次日返还」（每模型日最高 50万-500万，30 天有效）；需实名 + 建推理接入点（Endpoint ID）；doubao-seed-2.0-mini 单价近免费
- **魔搭 ModelScope**：每日 2000 次免费调用（全模型共享，单模型 500），每日重置，OpenAI 兼容（2026 起也支持 Anthropic 协议）；需绑阿里云实名 → 零星任务兜底候选

### 一次性额度档（用完即止）
- 阿里云百炼：新用户每模型 100 万 token（70+ 模型，总计 7000 万），90 天有效；**快照版本各算独立额度可拆**；免费档 QPS 低（3-5 QPM）；开「用完即停」防扣费
- 腾讯云混元：首开 100 万 token 资源包（10 模型共享，1 年有效）；正迁移 TokenHub
- DeepSeek 官方：新用户约 500 万 token（30 天）
- Kimi/Moonshot：实名送 ¥15 代金券（3 个月）；无永久免费层，~3 RPM
- 阶跃星辰 StepFun：注册送 ¥10（~5 RPM）；Model Lab 实验模型免费（并发仅 1）
- MiniMax：注册送积分，限速

## 新后端接入模式（2026-08-14 zhipu 实战定型）

1. `scripts/common/aa_backend.py`：加 URL/MODEL 常量（env 可覆盖）+ `_<名>_key()` + `call_<名>()`（3 次重试）+ `backend()` 别名分支。**key 位置先确认**：多数平台在 <hermes>\.env，但 **minimax/mimo 在翻译档案 env（<hermes>\profiles\translation\.env）**——`_<名>_key()` 按「翻译档案 → 默认」顺序兜底读取
2. **两处对称**：`scripts/aa_translate.py` 与 `scripts/common/aa_api.py` 的 `call_api` 各加分支（AGENTS.md 铁律，只改一处静默失效）
3. **mock 层验证请求形状**：断言 URL/Authorization/模型名/温度/**关推理参数**（siliconflow=`enable_thinking=false`（2026-08-31 官方参数）、zhipu=thinking disabled——漏带会 content 空）——不发真实 API 即可抓住形状回归
4. 单请求真实探测：先验证 content 非空 + 计时，再跑管线基准（3 批 AA 台词：边框/拟声/口语，宽度+残留+照抄校验）
5. 429 类限流：先抓响应体确认错误码（区分用户限流 vs 平台过载），再定重试退避；平台过载类（如 zhipu 1305）用 5/10/15s
6. 结论按文档格式追加进 `doc/线上免费模型统计.md` + 同步本 skill + fact_store


---

## 附：2026-09-15 由 SKILL.md 迁入（压 SKILL.md 体积，原文逐字保留）

**GLM-Z1-9B-0414 假死元凶（2026-09-01 定案，已从免费链移除）**：bar_miku/akari_series 反复"跑几小时假死"的根因。**判定法（usage.jsonl 金标准）**：grep task_id 看每个任务**最后一条 usage 的 model**——两任务都死在 `THUDM/GLM-Z1-9B-0414` 上（b2d8 最后 Z1 completion=**17246** 超长 17 倍；4997 最后 Z1）。**>5min 无调用间隔全在 Z1 前后**（11:40 Z1→12:00 Z1 20min、23:18→23:37 18min、02:24→02:52 28min）——一个 Z1 请求卡 15-30 分钟。**根因**：Z1 是 deepseek-r1 类推理模型，`enable_thinking=false` 对它**不生效**——thinking 混入 content + 超长生成（17246 token 生成 20 分钟）→ 单请求挂死 → 10 个 worker 全被占 → B2 回退 `ex.submit` 排队拿不到线程 → 整个进程 CPU=0 假死。**修复三层**：① `FREE_MODELS` 删 GLM-Z1 两行（14→12，全为非推理模型）；② `_call_free_chain` 加 `FREE_MAX_ROUNDS`（默认 5）——连续 N 轮全失败抛 RuntimeError 释放 worker（块失败落 review 人工兜底，任务继续推进不假死）；③ `socket.setdefaulttimeout(300)` 兜底 CLOSE_WAIT 半开连接（urllib urlopen timeout 对半开连接读可能不生效）。**通用教训**：免费链里**推理模型（thinking 关不掉的）是假死高危**——接入前单请求探测 content 是否干净（同 zhipu 4.1v/GMI 模式）；任务"假死"先看最后一条 usage 的 model 归属，别只盯着并发/退避。
