# 付费链端点均匀轮询 + siliconflow 付费模型接入（2026-09-02）

## 背景

zhipu 全系免费额度耗尽（4.5-air → 4.6v-flashx → 4.6v → 4.7 依次 429），全部移出模型链。siliconflow 付费双模 Qwen/Qwen3.5-27B + deepseek-ai/DeepSeek-V3 探针通过接入 normal 链。用户同时要求付费模型并行分散，并对首版实现给出硬纠正。

## 用户纠正（最重要）

> 付费模型并行分散是默认分散到不同端点，假如 3 并发先用 sf，cc 各 1，每个端点均匀分配。你改错了

首版错误：按**链条目**轮询（`_chain_round_start(n)` 起点轮转 sf-Qwen→sf-DSV3→cc-muse→cc-mimo）——3 并发时 worker1/2 都落 sf，只有 worker3 到 cc。正确语义：**端点级均匀**，每端点先各占 1 个 worker，多出的再均匀轮流。

## 正确实现

```python
# aa_backend.py
_chain_round = {"i": 0}

def _endpoint_round_chain(chain):
    """i % 端点数 选端点, (i // 端点数) % 端点内模型数 选端点内模型。
    链 [sf-Qwen, sf-DSV3, cc-muse, cc-mimo]: i=0→sf-Qwen, 1→cc-muse,
    2→sf-DSV3, 3→cc-mimo, 4→sf-Qwen… (3并发=sf,cc,sf)"""
    if len(chain) <= 1:
        return chain
    eps = []
    for b, _ in chain:
        if b not in eps:
            eps.append(b)
    with _BACKEND_LOCK:
        i = _chain_round["i"]
        _chain_round["i"] = i + 1
    ep = eps[i % len(eps)]
    ep_items = [t for t in chain if t[0] == ep]
    pick = ep_items[(i // len(eps)) % len(ep_items)]
    return [pick] + [t for t in chain if t is not pick]
```

call_with_fallback 内三处配套改动：

1. **钉住仅限降级成功**：
```python
for _bi, (b, m) in enumerate(chain):
    txt = _call_named(b, system, body, model=(m if m else _model_for_backend(b, m)), ...)
    if _bi > 0:   # 仅降级后成功才记（健康直出不钉——否则并发全挤单端点）
        with _BACKEND_LOCK:
            if _PREFERRED_BACKEND["name"] is None:
                _PREFERRED_BACKEND["name"] = b
    return txt, b
```
2. **显式 AA_BACKEND 组前置**（保留同后端全部配置模型条目）：
```python
_grp = [t for t in chain if t[0].lower() == explicit.lower()]
_rest = [t for t in chain if t[0].lower() != explicit.lower()]
chain = (_grp + _rest) if _grp else [(explicit, None)] + _rest
# 同(端点,模型)去重（保序取首个）
```
3. **条目自带模型优先于 env**：`model=(m if m else _model_for_backend(b, m))`。
   坑：translate_runner/aa_translate 会 `os.environ.setdefault(AA_SF_MODEL, 链首模型)`——
   旧逻辑无条件 `_model_for_backend`（env 优先）会让链上两个 sf 条目都解析成同一模型，
   分散静默失效。条目带模型时直接用，条目无模型（显式插入头）才回落 env。

## zhipu 出链的连带修改

| 文件 | 改动 |
|---|---|
| model_config.toml | normal_backends 去 zhipu 链首；vision models_normal 移除 glm-4.7（剩 mimo 兜底） |
| tools/proofread.py | 校对链 [4.1v-thinking → mimo] → [sf Qwen3.5-27B → cc mimo] |
| tools/translate_yaruobook_desc.py | 硬编码 call_zhipu(4.5-air) → call_with_fallback 自动链 |
| aa_translate.py / runner/translate_runner.py | low 作品 AA_BACKEND=zhipu 分支 → normal 链首（sf 无审查可翻 yes 章） |

low_r18 分支保留：链无 zhipu → `zp=[]` 自然跳过不炸；zhipu_guard 防护流程保留。
`[backend.zhipu]` 定义 + call_zhipu 保留（同 deepseek 先例，补资源包可随时切回）。

## 验证（temp/test_chain_roundrobin.py，全过）

mock `_call_named` 记录 (backend, model) 序列，不发真实请求：
- 端点均匀：前4次 = sf-Qwen, cc-muse, sf-DSV3, cc-mimo；8次 = sf4+cc4；3并发窗口=[sf,cc,sf]
- 降级钉住：sf 全模型 429 → 降级 cc 成功 → 记钉住 → 后续直接走 cc
- 组前置：AA_BACKEND=siliconflow 时 4 次调用端点均匀且 sf 两模型都被轮到
- unittest 全量 250 用例全绿（skipped=2 无关）
- 端到端：AA_BACKEND=siliconflow 两次真调，usage.jsonl 尾部 model 分别 = DeepSeek-V3、Qwen3.5-27B

**mock 断言陷阱**：组前置后链仍是 [sf,sf,cc,cc]，端点均匀轮询下第 2 次调用落 cc 是正确行为——断言分布必须「按端点过滤后比对 sf 内模型」，直接取全部调用的 model 列比对会假 FAIL（本会话踩过，浪费一轮排查）。

## 探针实测数据（2026-09-02）

翻译探针（短 6 行 + 长 42 行，enable_thinking=false）：
- Qwen/Qwen3.5-27B：短批 2.4-3.2s 格式 6/6；长批 12.1-18.1s 格式 42/42；假名 0
- deepseek-ai/DeepSeek-V3：短批 3.3s 格式 6/6；长批 15.2-15.3s 格式 42/42；假名 0

R18 三层探针（sujin_shabu 真实命中行 6 + 硬编码露骨 5 + 粗口 3）：两模型全过，无 HTTP 拦截、
无中日文拒绝语、无空响应；「中出しされて孕ませてほしいんだ」→「好想被内射怀孕啊」直译无回避。
两模型 siliconflow 后端 r18=true（无审查），可服务 R18 作品（但 normal 链仅 non-R18/low-yes 章消费）。

## r18 链同步接入 sf 双模（2026-09-02 晚，两链同构）

用户要求「siliconflow Qwen3.5-27B + DeepSeek-V3 同步接入 r18 链（付费链并行统一均匀分配）」。
改动仅 TOML：r18_backends = [sf Qwen3.5-27B → sf DSV3 → cc muse-spark → cc mimo]，
与 normal 链同构（2端点×2模型），sf 双模前移、commandcode 降兜底。

- **注意同模型不同入口**：r18 链上的 mimo 是 **commandcode 网关**的 `xiaomi/mimo-v2.5`（无审查，历史在用）；小米官方端点 `api.xiaomimimo.com` 的 mimo-v2.5/pro 实测硬词单发即拒（high risk，2026-09-02）不入链——TOML 注释必须区分两者，勿混淆
- 验证：work_r18=True + R18 台词（おっぱい/童貞）→ backend=siliconflow 正常翻译「胸口埋/破处」无拦截；mock 8 次调用端点序列 sf,cc 交替均匀；unittest 250 全绿
- AGENTS.md 模型链条目同步为「normal/r18 两链同构」

## 网关级内容过滤实测矩阵（2026-09-02，含破限验证）

**探测铁律：必须含性器官词档（ちんこ/中出し/挿れる/シャブ漬け），只测粗口/擦边（童貞/おっぱい）会漏判**。

| 平台/模型 | 粗口/擦边 | 硬词单发(中出し) | 全R18批 | 破限提示词 |
|---|---|---|---|---|
| MiniMax 官网 M3 | ✅ | ✅ 200 | ❌ 422(累计浓度) | 无效(网关预检) |
| miMo 官方 api.xiaomimimo.com v2.5 / v2.5-pro | ✅ | ❌ high risk | ❌ | — |
| tokenrhythm 转发 mimo-v2.5-pro | ✅ | ❌ high risk | ❌(长批先输出再断) | 无效 |
| tokenrhythm glm-5.3-flash | ✅ | — | ❌ 显式拒译(性暴力判定) | — |
| siliconflow Qwen3.5-27B / DSV3 | ✅ | ✅ | ✅ 全过 | 无需 |
| commandcode muse-spark / cc网关 mimo | ✅ | ✅ 无审查 | ✅ | 无需 |

- **内容过滤发生在模型被调用之前**（M3 422 input new_sensitive / mimo high risk）——破甲/破限/DAN 提示词对付模型侧拒绝，对网关预检全部无效（请求到不了模型）。勿浪费时间在破限提示词上；罗马音变形可行但有账号风险不推荐
- 触发阈值分级：M3 = 整块敏感词累计浓度（单发 200，42行块≥6 硬词 422）；miMo = 硬词单发即拒（擦边混正常通过）——用浓度梯度定位真实生产可用性
- 代理网关≠解除审查：tokenrhythm 转发 mimo 与官方审查一致（仅延迟高 10-20s，长批先输出正常行再中断 vs 官方整批预检拒）
- **TOML r18=true 字段≠官方端点实际无审查**：commandcode 网关 mimo 无审查但小米官方端点有——接入前必须实测（AGENTS.md「mimo r18=true 无审查」描述过时）

## tokenrhythm.studio 端点发现 + 三模型实测

- 端点发现法：官网主域（tokenrhythm.ai）是 landing，真实 API 在控制台子域（tokenrhythm.studio）——curl 首页 grep href 找 login/register 子域，再试 /v1/models、/api/v1/models、/docs/api-integration；SPA 页 HTML 里直接 grep `https://.../v1/chat/completions` 完整 URL
- 端点：tokenrhythm.studio/v1/chat/completions；key TOKENRHYTHM_API_KEY（translation/.env sk_tr_ 49字符）；无代码接入，测试用 urllib 手写请求；api.tokenrhythm.ai 等域名 SSL EOF 不可用，直连免代理
- 模型列表 20 个：deepseek-v4-flash-0731/pro-0813、glm-5/5.1/5.2/5.3/5.3-flash、qwen3.7-flash/max、qwen3.8-27b/max、mimo-v2.5-pro、kimi-k2.5/2.6/2.7-code、minimax-m2.5/m2.7、seed-2.1-pro/turbo、longcat-2.0
- 三模型全不可用：deepseek-v4-flash-0731 快准但**长批漏末尾[37-42]**（Nemotron 同模式，输出分配问题）；glm-5.3-flash R18 显式拒译（「抱歉，我无法完成…毒品协助性侵害/性暴力」比 zhipu 1301 更硬）+ 长批 504 + 16-48s/批慢；qwen3.7-flash **首请求后全空响应**（usage reasoning_tokens 259-282 远超 text，疑似推理模型+网关首字节超时；重测 67.8s 截断 4/6 确认不稳定）
- 探针：temp/probe_tokenrhythm.py（短批6行分组+长批42+校对）+ probe_tr_mimo.py（含破限对照）+ probe_mimo_pro.py（官方 mimo 对照）

## DashScope 千问系列实测（2026-09-02，13 模型）

**端点**：dashscope.aliyuncs.com/compatible-mode/v1/chat/completions（OpenAI 兼容）｜ key QWEN_API_KEY（translation/.env sk-ws- 116字符）｜ 完整表 doc/翻译模型对比.md

- **⚠️ 必须 `enable_thinking: false`**：qwen 系默认开思考——qwen3.5-plus 80s/4638tok → 关后 4s/198tok（23x）。探针不带会拖到天荒地老（本会话曾 5 分钟只跑完 1 模型）
- **R18 全 13 模型一致：HTTP 400 `DataInspectionFailed: Output data may contain inappropriate content`**（**输出侧**审核——模型译了但输出被拦）→ DashScope 平台级审查，R18 作品全不可用（比 zhipu 1301 严，无法绕）
- **qwen-mt-lite/flash/plus/turbo（机器翻译专用）不支持 system 角色**：400 `Role must be in [user, assistant]`——带 system 必挂；无 system 时输出解释性内容不守行号格式 → 不适 AA 管线。**同一批 400 里「日常也拒」先查是否参数/角色问题（错误体 message 区分），勿一刀切判内容审核**（本会话曾误把 qwen-mt 全 400 当审核，实测是 system 角色问题）
- 翻译质量档：qwen3.7-plus / qwen3.5-flash / qwen-plus 最佳（关思考后 1-11s/批、长批 45/45 不漏行、角色名全译）——**DashScope 长批明显比 deepinfra/tokenrhythm 稳（42行 45/45 无漏）**；qwen3.7-flash 长批偶发 0/45 空响应（0.7s tok703 无内容）；qwen-flash/qwen3.8-flash/qwen3.5-27b 角色名假名残留
- qwen2.5-omni-7b 不在模型列表（旧名/下架）；列表 250 个，模型名先 GET /v1/models 确认再测

## deepinfra 定价获取 + V4-Flash-0731 长批漏行定案（2026-09-02）

- **定价获取**：api.deepinfra.com/v1/models 带 key 的 metadata.pricing 对免费账户**全 0**；单模型页/Next data 路由（/_next/data/<buildId>/models.json）对非 featured 模型 fallback 同一目录页（245759B 恒等）——**SSR 只预埋 23 个 featured 模型定价**（字段 cents_per_input_token/cents_per_output_token/rate_per_input_token_cached，$/M = cents×1e6/100）。非 featured 定价需逆 JS 运行时 API
- **DeepSeek-V4-Flash-0731 长批漏末尾 [37-42] = 模型级通病**（deepinfra + tokenrhythm 双平台一致）→ 不适 AA 50行/块长批（实证为何从未进 AA 链）
- **比价看 output 价**（翻译 out≈in）：V4-Flash-0731 $0.08/$0.18 的 out 是全站文本模型最低；input 更便宜的 GLM-4.7-Flash $0.06/$0.40（26-68s/批极慢+成本 2.2x）与 gemma-4-26B-A4B $0.07/$0.34（快但校对 0/3+长批同漏）都不成候选

## 维护提醒

- 模型链变化必须同步四处：TOML + AGENTS.md 模型链条目 + 本 skill「翻译前模型预判」+ 校对链（proofread.py 内注释）
- 探针脚本在 temp/（不入 git），会话后可删；探针方法本节已完整记录可复现
