# token 成本量测与提示缓存（AA 管线）

配套：`aa-model-chain-ops` 的「成本口径」节；脚本 `scripts/probe_llm_cache.py`（本技能自带）。

## 1. 跨厂商字段名（落盘 cache_hit / reasoning 用）

| 端点类型 | 缓存命中字段 | 思考 token |
|---|---|---|
| DeepSeek chat/completions | `prompt_cache_hit_tokens`（另有 `prompt_cache_miss_tokens`） | `completion_tokens_details.reasoning_tokens` |
| OpenAI 兼容（deepinfra / runinfra / commandcode / 硅基流动） | `usage.prompt_tokens_details.cached_tokens` | 同上 |
| Responses（官网 DeepSeek、OpenAI） | `input_tokens_details.cached_tokens` | `output_tokens_details.reasoning_tokens` |

**规则：取不到就不写字段（None），绝不写 0。** `0` = 端点支持缓存但本次没命中，缺失 = 端点不提供——混了就永远算不出命中率。

## 2. 探针配方（真机，几分钟）

1. **前缀必须用生产形态**：`aa_translate.SYSTEM + aa_translate.glossary_prompt_hint(base)`（≈1.3~1.7K token）。拿 `hello` 当前缀测出来的命中行为不代表管线。
2. **冷缓存要人为保证**：前缀尾部塞一个随机 nonce；否则测到的是上一轮的暖缓存，会把「其实没缓存」的端点看成命中。
3. 同一前缀连发几次（第 1 次写缓存、第 2 次起看是否抬升），看 `prompt` / `cache_hit` / `backend` / `model` / 单次秒数——**backend 要读 usage 落盘的实际值**，配置里写了不等于真的打到那儿。
4. **思考模型的探针要留足 `max_tokens`**：给 200 会被思考吃光 → 返回空响应，容易误判成「端点挂了」。
5. 报表：`python scripts/tools/usage_stats.py --cost [--days N] [tag]`；**历史记录没有 `cache_hit` 字段 → 金额是上界**（全按未命中价），别当账单。

## 3. 实测结论（别重新猜）

| 端点 | 同前缀连续调用 | 冷前缀 8 并发首波 |
|---|---|---|
| **官网 deepseek-flash** | 384 → 1024/1024/1024（prompt 1286）＝**79.6%** | `[1024]×8` ＝ **79.0%**；暖后再 8 并发 `[1152]×8` ＝ 88.9% |
| deepinfra `zai-org/GLM-5.3-Flash` | **0 / 0 / 0** | — |
| runinfra `glm-5-3-flash` | **0 / 0 / 0** | — |
| commandcode `meta/muse-spark-1.3` | 探针空响应（思考吃光 max_tokens） | — |

1. **只有官网 deepseek 命中**——缓存以端点为界（换端点 = 换机器 = 没缓存），所以 pin 目标必须挑会缓存的端点。
2. **并发不会击穿 DeepSeek 的缓存**：冷前缀 8 并发首波就 79% 命中 → **不要照搬 Anthropic 式「先发一个预热请求」**（那是 Anthropic 的语义；第三方「冷前缀 10 并发命中率 4%」的案例同理不适用）。
3. 命中粒度 64 token；每次调用的**前缀尾巴 + 正文不可缓存**（1296 中约 144）。

## 4. 成本结构（缓存生效后，单次调用）

1296 token 前缀 + 1 行正文 + 11 token 输出，官网 off-peak 价（hit 0.02 / miss 1.0 / out 4.0 元每百万）：

| 组成 | token | 金额 | 占比 |
|---|---|---|---|
| 缓存命中部分 | 1152 | 0.000023 元 | 11% |
| **未命中部分（前缀尾 + 正文）** | **144** | **0.000144 元** | **68%** |
| 输出 | 11 | 0.000044 元 | 21% |

**结论：缓存命中后，钱在「每次调用的不可缓存尾巴 + 输出」，不在静态前缀长度**。
优化排序 = 减少调用次数（TM 复用 / 合并碎调用 / 不整批重发）> 瘦身 SYSTEM 提示词。
反例警示：若按「前缀仅 280 tok 且未命中」做算术，会得出「输出占 66%、该砍提示词」的错误结论。

## 5. 端点 pin 落地与验收

- **配置**：`[backend.deepseek].cache = true`（标记会缓存的端点，注释里写实测依据）+ `[default].pin_cache_endpoint = true`；`AA_PIN_CACHE_ENDPOINT=0` 优先回退。
- **代码**：`call_with_fallback` 在章首（`_PREFERRED_BACKEND` 为空）钉到链上第一个 `aa_config.cache_safe()` 端点，本章内不轮询；失败仍按原链序降级并钉住新的可用端点。
- **验收**：同章前缀 A/B；单测要覆盖「缓存端点不在链首也必须被选中 / 章内保持 / 章边界重钉 / env 回退轮询 / 降级后钉可用端点 / 免费模式不受影响 / 无缓存端点回落轮询」。
- **A/B 用 env 开关模拟旧行为**，不要 revert 代码再改回来：改造时留一个语义等价的反向开关，A/B 就是同一实验跑两遍 env。

## 6. 前缀稳定性审计（命中的前置条件）

同一章前缀在**不同 `PYTHONHASHSEED`** 的独立进程里算 sha1 比对：集合/字典迭代序一旦泄漏进前缀，不同进程会给出不同字节序列，而**同进程内是稳定的——单进程自测发现不了**。

**术语/设定注入只按章锁定**：按调用或按块筛（「只注本块命中的词」）会让前缀每次唯一 → 缓存全灭，算术上成本反而涨（约 1.9×）。
