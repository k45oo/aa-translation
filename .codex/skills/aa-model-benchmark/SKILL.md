---
name: aa-model-benchmark
description: Use when 评估/选型AA翻译模型, 跑基准脚本打分.
---

# AA 翻译模型基准评估（benchmark）

## 何时用
- 新增/替换翻译模型（如 qwen3.5-27b 换掉）时自动打分选型
- 用户要"测试某模型能不能用/性价比"

## 入口
`python scripts/benchmark/eval_translation.py`（见 `doc/方案归档/模型评估基准测试方案_2026-09-06.md`）

## 关键命令
```bash
# 裸测全部配置模型(6个, 9项除pipeline)
python scripts/benchmark/eval_translation.py --outdir scripts/benchmark/report
# 只测指定后端/模型
python scripts/benchmark/eval_translation.py --backend ri
python scripts/benchmark/eval_translation.py --model runinfra/ornith-1-5-35b
# 本地不调API验证脚本
python scripts/benchmark/eval_translation.py --dry
# 管线真实层(重译章节看兜底)
python scripts/benchmark/eval_translation.py --pipeline --chapters entry_17808
# 扫预算/思考档（思考关不掉的模型调 max_tokens 用；报告名带 [mt32768] 后缀区分）
python scripts/benchmark/eval_translation.py --model commandcode/deepseek-v4.1-flash --max-tokens 32768 --reasoning low
```

## 10 项测试(裸测层)
1常规翻译 2粗口 3R18(r18=false后端标N/A) 4术语表遵循 5AA行识别保持 6mix列位保持 7管线真实(--pipeline) 8人工修改易错段 9极限测试(超长/半角假名密集) 10长困难块压力(30行art密集mixed整行, 21095语料)

## 评分
每项0~100扣分制: 缺行-10/行 假名残留-5/处 art列位-30/行 宽度-10/行。综合分=质量分(70%)+成本折算(30%)。缺跑项标N/A不进分母。

## 用例库（lib/cases.json）
9 项测试的语料/术语对/评分参数全部外置到 `scripts/benchmark/lib/cases.json`——**人工改 JSON 即可调用例/评分, 不碰代码**。`_scores` 存扣分标准(missing/kana/art/width)+综合权重; 每 case 存 `src`{行号:原文日文}; case4 额外 pairs/hint; case9 用 long/hankaku 双子块。刻: JSON 键是字符串, 脚本 `_src()` 自动转 int(否则块格式 `%05d` 报 "Unknown format code 'd' for str")。加测试项=JSON 加 case 块 + `CASES` 表加条目。

## 坑
- **长困难块(30行art密集)是独立短板, 短块测试测不出**: 21095事故——ornith 30行块只产548 tok漏13行, deepinfra补译380/376tok也漏, 用户手动修14行。case10 单测(2026-09-06): glm-5-3-flash/qwen3-8-27b/deepinfra-GLM 100分稳, ornith 70分(art坏1-3行), runinfra deepseek-v4-flash 10分(尾漏3行)。**修正**: ornith 非首选(短块好但长块art不稳), 长块稳的是 glm-5-3-flash/qwen3-8-27b。
- **宽度判定必须 pad 后再比**: 裸测模型输出译文比SRC短是合理的(管线落盘才pad U+3000)。用 `_width_ok(src,cand)`(=pad到SRC宽再比)而非裸 `_dw(t)!=_dw(s)`——否则全模型误报 width 差。
- **半角假名拟声(ｷﾞｭｯ/ｶﾞｸｶﾞｸ)是艺术装饰**: 模型不译是合理行为, case9 的 hk_kana 扣分只*3 不重罚。深度拟声行被当艺术跳过不应大幅扣分。
- **runinfra 网络不稳**: 偶发 WinError 10054/429, `direct_call` 已带3次重试+代理10808。仍可能整批超时(deepinfra GLM case1=0 曾全失败)——重试后仍失败会记0分(非模型能力,是网络)。跑全量用background+notify, 单模型限时。
- **glm-5-3-flash 双平台参数坑**: runinfra `reasoning_effort=none`→400须`low`; deepinfra `none`→泄漏推理当正文须去字段/`low`/thinking:disabled。
- **报表工具行为（2026-09-22 改）**: 汇总 md 含「长困难块(case10)」列（此前 case10 跑分不进表，只看 JSON）；
  **每跑完一个模型立即落盘报告**（原先整批结束才写——某模型卡到超时会让已完成模型的结果全部丢失）。
  想只补表不重跑：读 `report/<dir>/<model>_<date>.json` 用 `temp/regen_bench_md.py` 重生成 md。
- **模型接入清单在脚本 `MODELS` 列表**(手工维护), 价格表在 `PRICES`(2026-09-06快照+cc V4.1, 换价人工更新)。成本分按 off-peak 价算。
- **思考模型必须给 `max_tokens`**: MODELS 条目可加 `"max_tokens": 16384`(direct_call 优先取 spec 值, 默认 2048), 或 CLI `--max-tokens N` 全局覆盖。裸测 2048 下不可关思考的模型(cc/deepseek-v4.1-flash)会把预算全烧在 reasoning 上→content 空→整项记 0 分, 属假失败(生产 16384 才可比); **30 行困难块需要 ≥32768**(16384 实测截断→case10=0 分)。**别以为“预算越大越好”**: cc/dsv4.1-flash 实测预算翻倍→思考跟着翻倍(占 91-99%), 耗时 7s/47s/47s/13s 纯随机, 要拿分数只要跨过截断阈值即可。
- **commandcode 网关 reasoning_effort 枚举只有 low|medium|high|xhigh|max**（minimal/none 都 400）; 不传字段=默认思考。cc/deepseek-v4.1-flash 的思考**关不掉**（v2 复测全封）：`thinking:{type:disabled/enabled}` / `enable_thinking:false` / `chat_template_kwargs:{thinking:false}` / `reasoning:{effort:none}` 网关全部静默忽略（10 变体×多次, reasoning 占比恒 90-100%）; Anthropic 端点 `/provider/v1/messages` **直接 400 拒 OSS 模型**（“Use /provider/v1/chat/completions for OpenAI and OSS models”）, 路也不通。后果: 同一 6 行块 completion 559~8192 tok 大幅浮动, 撞上限就 `finish=length` content 全空（实测 +thinking disabled 39.2s/8191 全思考/正文 0 字）。此类模型裸测/生产必须给大 max_tokens, 且判定前先看 finish_reason。需要真能关思考的 DeepSeek → 用**官网端点**(chat `thinking:{type:disabled}` / responses `reasoning:{effort:none}`, 均实测生效)。

## 2026-09-13 cc/muse-spark 1.2 vs 1.3 contributor（1.3 译质量更高但慢 2.8×）
同价（$0.10/$0.20, 官方 Artificial Analysis: 1.2 智能 46.8/编码 72.2/262 tok/s；1.3 53/76.3/221 tok/s）。**均需 `reasoning_effort=low`**（无字段=默认高思考: 1.2 31.9s/5856tok, 1.3 87.2s/6721tok）→ 基准要配 big max_tokens。
| 模型 | 综合 | 常规 | mix列位 | 其余 | 9项耗时 |
|---|---|---|---|---|---|
| cc/muse-spark-1.2-contributor | 94.4 | 50 | 90 | 全100 | 94.5s |
| cc/muse-spark-1.3-contributor | **98.3** | 100 | 90 | 全100 | 268.0s |
校对对比（bar_miku entry_1641 同缺陷输入, 46 目标）: 1.2 97.8s/6.0s 每次 发现46 通过28 命中17; 1.3 216.1s/13.4s 每次 发现46 通过28 命中18（GLM 现役 82.6s/命中33 但它是标准答案作者）→ **校对无差别, 只慢**。
长块(case10 30行): 1.2 20.2s / 1.3 61.3s（两者都没漏行; 兜底 timeout 180s 余量足）。
**结论（2026-09-13 用户拍板换）**: **已把两链兜底位 + `[backend.commandcode].fallback_model` 从 1.2 换成 1.3**（质量优先, 兑底位 rarely used）; 校对链不用 muse（无收益只变慢）。

## 2026-09-13 deepseek 官网 deepseek-flash(V4.1-Flash)（翻译 95.1 / 校对可用）
官方 API 现有 `deepseek-flash`(V4.1-Flash) + `deepseek-v4-pro` 两名。**思考开关必需且有效**（详见下条坑），关掉后：
翻译裸测 综合 95.1（case1/2/3(R18无拒)/4/5/8/9/10=100, case6 mix列位=90）16.5s/9项, 10523 tok；
校对(refine_aa) 与现役 deepinfra GLM 同输入对比(bar_miku entry_1633/1641 缺陷版): 发现行**逐行完全一致**（46/46 无差异）, 动作分布相同, 产出多数逐字节相同(少行同义改写), 速度快 **5-7×**(11.7s vs 82.6s)。
定位: 翻译可入 normal 链(质量≈ornith/glm)；校对可替代 GLM 且更快, 但 refine_aa `_call_glm` 硬编码 deepinfra GLM, 换模型需改该函数或加开关。
价格 ¥1/M in(未命中)、¥4/M out(空闲；高峰翻倍) ≈ $0.14/$0.56，比 runinfra($0.13/$0.27)、deepinfra($0.09/$0.09) 贵。

## 坑：deepseek 官网思考开关字段分端点（写错=静默忽略）
官方 `guides/thinking_mode`: **chat 端点**用 `thinking:{type:disabled}`（实测生效 1.0s/78tok）；
**Responses 端点必须用 `reasoning:{effort:none}`**（实测 0.7s/80tok/0 思考）——发 `thinking:{type:disabled}` 会被静默忽略仍满思考(23.7s/5768 思考 tok)，思考模式还会忽略 temperature。
`aa_backend.call_deepseek` 主 url = `/responses` 但发的是 `thinking` 字段 → **现管线实际一直在满思考跑**（慢~30×、token ~70×）；锚点验证/质量不受影响, 只是慢+贵。裸测直连端点必须用 chat 或改发 reasoning 字段。

## 2026-09-22 小米 MiMo V2.6（mimo-v2.6-flash / -pro，官网直连）

接新官方端点只改 `MODELS` spec，不必碰函数：`"direct": True`（国内直连，绕 10808 代理）、
`"extra": {"thinking": {"type": "disabled"}}`（思考开关）、`"url"` 可覆盖端点。

| 模型 | 综合 | R18 | case8 | case10(长困难块) | 9项耗时 | 价格$/M |
|---|---|---|---|---|---|---|
| mimo-v2.6-flash（闭思考, mt16384） | 95.1 | **拒答** | 95 | **70**（三次 70/100/70 抖动） | 16.0s | 0.21 |
| mimo-v2.6-pro（闭思考, mt16384） | 90.7 | **拒答** | 95 | 100（两次均 100） | 33.5s | 0.6525 |
| deepseek/deepseek-flash（同日对照） | 95.9 | 100 | 100 | 100 | 16.4s | 0.35 |

- **R18 拒答形态**：`finish_reason=content_filter` + `content="The request was rejected because it was
  considered high risk"`，1~2s 极快拒。基准里表现为 case3 = 0 分（缺全部行）；确认拒答而非能力后，
  把该 spec 的 `r18` 改 `False` 记 N/A（别让它污染综合分）。**思考档也救不了**（v2.6 两档都拒；
  新奇点：v2.5-pro 开思考反而能过同一 R18 用例，闭思考才拒 → R18 定性必须逐模型/逐档实测，别外推）。
- **思考档（thinking=enabled）单测、别写进 MODELS**：六行块 23.6s(pro)/29.0s(flash)、思考 2108/5036 tok；
  **30 行困难块 302.6s(pro)/171.9s(flash)，思考吃满 32768 预算 → `finish=length`、content 空、0 行**。
  写进 MODELS 会让整批跑挂死到超时。量它用独立探针（`temp/probe_mimo_think.py`：同 SYSTEM + 同 case 语料，
  逐次报 耗时/finish/reasoning_tokens/输出行数）。
- 价格表口径：`PRICES` 用**海外价（$/M）**（官方定价页 hit/miss/out 三档，这里取 miss/out 均值口径）；
  国内 ¥ 价同页可查。**模型下线公告要看定价页**（本次：v2.5/v2.5-pro 2026-10-21 下线）。
- 详细记录：`doc/翻译模型对比.md`「2026-09-22 小米 MiMo V2.6 系列实测」；报告 `scripts/benchmark/report/mimo26/`。

## 2026-09-23 同模型换站点：MiMo V2.6-Flash × deepinfra/cc + di/DeepSeek-V4-Flash-0731 复测

报告 `scripts/benchmark/report/3models_20260923/`；探针 `temp/probe_3models_20260923.py`、`temp/probe_r18_repeat_20260923.py`、
`temp/probe_cc_mimo_case10_20260923.py`；校对对照 `temp/ab_refine_3models.py`（通用化版，`--models chain,di-mimo,di-dsv4,cc-mimo` + `--tag`）。

| 模型 | 综合 | R18 | case10 长困难块 | 9项耗时 | 校对（entry_1641/1633） | 价格$/M |
|---|---|---|---|---|---|---|
| deepinfra/deepseek-ai/DeepSeek-V4-Flash-0731 | **99.5**（9/9 满分） | 100 无拒 | 100 | 125.7s | 4.6s/21.7s，修复 20/12 行 | **0.06/0.18** |
| deepinfra/XiaomiMiMo/MiMo-V2.6-Flash | 98.1（9/9 满分） | 100 无拒 | 100（86.0s 慢） | 156.8s | 57.2s/324.0s（18~60× 慢），误改多 | 0.14/0.28 |
| commandcode/xiaomi/mimo-v2.6-flash | 89.9 | **默认 3/3 拒 / low 2/3 拒** | **0（HTTP 524）** | 226.8s | 242.1s/288.5s | 0.14/0.28 |

**新坑（必看）**：
- **R18 定性按「站点」不按模型名**：同一个 MiMo V2.6-Flash —— 官网 `content_filter` 拒、**deepinfra 托管版 0/3 拒（放行）**、
  cc 托管版默认 3/3 拒、`reasoning_effort=low` 仍 2/3 拒。换站点必须重测 R18，别外推（`[backend.mimo] r18=false` 只对官网成立）。
- **deepinfra 的思考开关 = `reasoning_effort:none`**（MiMo V2.6 实测生效 reason_tok=0/11.5s；无字段=默认思考 671tok；`low` 反而 1961tok/210s）；
  `thinking:{type:disabled}` 在 deepinfra **被静默忽略**（796tok）。与官网 `thinking` 字段口径不同。
- **cc 网关托管 mimo 思考关不掉**（`reasoning_effort:none`→400 枚举只到 low|medium|high|xhigh|max；`low` 未减思考 2724tok；`thinking:disabled` 忽略）
  → **30 行困难块必挂 `HTTP 524`**（复测 2/2，~127s 网关切断；`max_tokens` 16384/32768 一样）→ 该站点不可入链。
- 0731 复测 9/9 满分（2026-09-06 曾 91.2 / case2 粗口 0）→ 同模型跨日会变，报告里要写清日期；其 **deepinfra 实时价 2026-09-23 已变 $0.06/$0.18**（原快照 $0.09/$0.09），换价须回 `/v1/openai/models` 查 `metadata.pricing`。
- 校对对照脚本已通用化：`temp/ab_refine_3models.py`（callers 表 + `--tag` 防覆盖旧结果；输出 `temp/ab_refine_<work>_<base>_<tag>.json`）。

## 校对（refine_aa）跨模型对照方法

翻译裸测只答「能不能翻」，**校对能力必须另测**——用现成缺陷输入跑真 refine 流程：

1. 输入 = 作品 `work/backup_refine_aa_*` 里的「校对前」汉化（refine 每次 `--write` 前自动备份），
   标准答案 = 现版 `汉化/<base>_汉化.txt`；
2. 把 `refine_aa._call_glm` 换成目标模型的直连调用器（签名 `(system, body, max_tokens, timeout,
   temperature, gloss_hint)`；**必须自己拼 gloss_hint**：`if hint and "本作标准译名" not in system: system += hint`，
   否则术语注入丢失、对照不公平），跑 `refine_chapter(cfg, base, do_write=False)`，workers=8；
3. 指标四件套：**写回行数**（`edits` 里 ok=True）/ **修复中标准答案**（new == 标准答案行，真修复）/ 
   **误改已定稿行**（校对前 == 标准答案却仍被改 = 误改，最要命的指标）/ 耗时。
   参考实现：`temp/ab_refine_mimo.py`（+ `temp/ab_refine_metrics.py` 出细分指标）。

**实测（bar_miku entry_1641/1633，2026-09-22）**：mimo-v2.6-flash 校对 ≈ 现役链（2.3s/7.2s vs 2.7s/6.4s，
多抓 1~2 行真漏译，但误改多 1~4 行）；pro 同章同输入 11.2s→117.7s 抖动且无质量收益。
**坑（新模型判读必看）**：误改多是「**把 art 拟声图形翻成中文**」（`ノ＾ヽ.`→`喂，你这家伙`、`二ﾆﾆう`→`二呵呵`）——
这类行 `art_stroke_preserved` 拦不住（短图形锚点 <3 不触发），进生产就会吃掉 AA 图形。
**`partial_kana` 计数不是质量指标**：含「ひらがな→カタカナ」转写（`きーりーふーきー！`→`キーリーフキー！`）与专名保留日文。

- **`[backend.mimo]` 已更新（2026-09-22）**: `model="mimo-v2.6-flash"` + `r18=false`（v2.5-pro 官网 2026-10-21 下线；V2.6 实测拒 R18），`aa_backend.MIMO_MODEL` 代码默认同步。
- **批量推理（Batch API）**: 实时价 50%、只支持 v2.6 两模型（名须小写）、只扣现金余额（Token Plan 不抵扣）、Base = `https://batch-api-cn.xiaomimimo.com/v1`（OpenAI Batch 协议: files→batches→轮询→files/<id>/content）。
  **2026-09-22 实测: 不可用**——上传/查询/鉴权全正常，但 `POST /v1/batches` 恒 500（参数完全不校验 → 改请求绕不过；疑似账号前置: 余额 0 / 未实名，或服务刚上线故障）。复测脚本 `temp/probe_mimo_batch.py`。

## 2026-09-06 实测结果(替换qwen3.5-27b候选)
| 模型 | 综合 | 速度 | 价格$/M | 特点 |
|---|---|---|---|---|
| runinfra/ornith-1-5-35b | 98.3 | 12.0s | 0.2 | 快+满分, 推荐 |
| runinfra/glm-5-3-flash | 98.3 | 27.9s | 0.2 | 慢但全项满分 |
| runinfra/qwen3-8-27b | 97.8 | 16.3s | 0.2 | 人工易错段95 |
| runinfra/deepseek-v4-flash | 93.9 | 13.1s | 0.2 | case9半角拟声扣重 |
| deepinfra/DeepSeek-V4-Flash-0731 | 91.2 | 34.6s | 0.09 | 最便宜, case2粗口0 |
| deepinfra/GLM-5.3-Flash | 87.2 | 20.4s | 0.35 | case1网络失败, 最贵 |

**结论**: runinfra 四模型(尤其 ornith/glm)质量最高且便宜, 是替换 qwen3.5-27b 首选; deepinfra 胜在便宜但质量略逊/粗口弱。

## 2026-09-10 cc/deepseek-v4.1-flash（不推荐入链）
综合 87.4（9 项 253.5s, 62803 tok, $0.15/$0.60 off-peak）：短块全满分（case1/2/3/4/5/6=100, case9=100, case8=95）, R18 无审查, 但: ① reasoning 不可关且占输出 95-100%（case1 六行块烧 7233 completion tok 才吐 469）→ 慢到 10-36s/块, case10 单块 68s; ② case10(30行困难块) 3 次测 2 次失败（一次思考吃光 16384 上限 content 空, 一次漏 9 行+art 坏 1 行）; ③ 同块跑 3 次质量抖动（case1 100/60/100）。定位: 只适合短块兜底, 不如 runinfra ornith/glm。

**2026-09-14 复测（low + 抬 max_tokens，用户要求）**：失败本质是**预算**不是能力——16384 → 综合 84.2（30 行困难块 `finish=length` 截断，只出 14-25 行 = 0 分）；**32768 / 65536 → 95.1（case10=100，30/30 行 art 全过）**。tokens 39,994 / 58,022 / 47,250，9 项耗时 156.7 / 232.8 / 187.3s。
规律（关键）：**预算越大思考越多**（同 6 行块 completion 1224→9950→9937，思考恒占 91-99%），耗时抖动极大（131072 反而比 32768 快 7 倍）——**不是“给得越多越快/越稳”，而是随机**；65536 与 32768 同分同质量，没必要给更高。
**仍不入链**：官网 `deepseek-flash` 同样 95.1 分但 9 项 **16.5s / 10.5K tok**（约 1/14 时间、1/5 token）+ 思考可关 + 更便宜；链上 cc 兑底位已有 muse-spark-1.3（98.3）。
生产注意：`[default].max_output_tokens=16384` 正好是截断阈值，若临时用此模型必须调到 ≥32768。

## 未来扩展(仅记录)
benchmark/ 目录可加 `eval_vision_model.py`(视觉模型评估)等; 脚本模块化, 加测试项只需往 `CASES` 加条目。
