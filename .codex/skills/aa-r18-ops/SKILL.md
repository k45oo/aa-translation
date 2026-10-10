---
name: aa-r18-ops
description: Use when 管理AA汉化R18内容安全(分级/章节级后端选择/视觉辅助/工作台R18展示)。
---

# AA 汉化 R18 内容安全

> 适用于 `<repo>` 项目。R18 内容分级路由到对应后端链。
> 详细方案：`doc/R18分级与章节级判定方案_2026-08-24.md`。**zhipu 后端已于 2026-09-07 整体移除**（代码/工具/防护流程全清，历史方案 doc 仅存档）。

## 触发
- R18 作品的后端选择/降级
- 章节级 R18 判定、红 R 徽章、工作台 R18 展示

## 一、R18 四态分级（work_meta.json `r18_level`，2026-08-26 定稿）

```json
"r18_level": "high" | "medium" | "low" | "",
```
| 级别 | r标 | r18校验(prep阈值) | 后端路由 |
|---|---|---|---|
| **low** | 浅红 `.r-badge.low`(#f8b0b8) | **最严** STRONG≥1/NORMAL≥2→yes | **全部章先走 normal 链首**(low_r18 忽略显式后端), 限流自动降级 r18 链 |
| **medium**(默认档) | **普通红 #c22(默认色,无额外类)** | 正常 STRONG≥2/NORMAL≥3 | yes→r18；no/suspect→非r18 |
| **high** | 深红 `.r-badge.high`(#7a1020) | 最松 STRONG≥3/NORMAL≥4 | **除no章都走r18**(yes+suspect→r18, no→非r18) |
| **空(=no)** | 无R标 | 不判 | 非R18作品, 全走非r18, 不做章节级判定 |

- 用户强调：**medium 是普通红（当前色）**——`.r-badge` 默认类不带任何颜色类，只有 low/high 加类。
- **r18_level 可人工修改**：work 详情弹窗 R18 字段是下拉（no/medium/low/high + 摘要说明），PUT work-meta 校验非法值保留原值。
- 空 level 迁移语义 = **no**（14 个非 R18 作品），不做 prep 判定；R18 作品需人工标 high/medium/low。

## 二、词表校准（chapter_prep.py，零 API）

**方法**：阳性对照（akari_ninshin/haisin ≈100%）+ 阴性对照（saki_fc2/jinrou 无 R18）跑一遍，按区分度重排词表。**校准数据驱动，绝不拍脑袋**。

判定规则（定稿）：STRONG ≥2 次命中 → yes；NORMAL ≥3 个不同词 → yes；单次 STRONG 或 1~2 NORMAL → suspect；SOFT 不 gate（仅浓度统计）。

三类实测修正：
1. **子串误撞改正则**：`性交(?!際|遊)`（性交際/性交遊=异性交往）、`(?<!ボディー)ソープ`（沐浴露）。**词表是正则 RE 列表非 set**。
2. **STRONG 单次命中太敏感**（sujin_otona「えっち」×1 误判）→ ≥2 次才 yes。
3. **低区分度词降 SOFT**：おっぱい/パンツ/下着/絶頂/巨乳/貧乳 阴性也大量出现 → 不 gate。

实测结果：akari 100% 保持；saki_fc2 30%→1%、jinrou 2%→0%、sujin_otona 20%→0%。

## 三、章节级后端驱动（aa_translate.py `_chapter_work_r18`）

返回 `(work_r18, low_r18)`（2026-08-26 四态）：
- **high**：hit==no → (False,False)；yes/suspect → (True,False)；**无 prep 时保守走 r18**（`_os.path.exists(prep_p)` 判断）
- **medium**：hit==yes → (True,False)；no/suspect → (False,False)
- **low**：**全部章** (False,True)（low_r18=True → 忽略显式 AA_BACKEND，实际走 normal 链首；yes 章由 r18 链兜底）
- **空(=no)**：一律 (False,False)（非 R18，不读 prep）

调用链：`call_api(work_r18=None, low_r18=False)` 透传 → translate_lines/mixed → process_chapter。每章 `reset_preferred_backend()` 防降级污染。

⚠️ **low_r18 必须忽略显式 AA_BACKEND**（`if not low_r18:` 才做 explicit 提头）——否则 auto_backend 把 normal 链首提头覆盖章节级选择（实测踩坑）。

⚠️ **AA_BACKEND 只在空 level(=no)作品设**（normal 链首供无 prep 用）；high/medium/low 由每章判定驱动，**不设全局 AA_BACKEND**——否则显式后端提头覆盖章节级选择（high 的 no 章会被提头成 r18 链首，2026-08-26 修复）。translate_runner 清理环节同规则：high→AA_R18=1，medium/low/空→0。

**prep 阈值按 level 调**（chapter_prep.prep_chapter 的 level 参数）：low 最严（STRONG≥1/NORMAL≥2）→ medium 现规则（STRONG≥2/NORMAL≥3）→ high 最松（STRONG≥3/NORMAL≥4）；`prep_work(cfg, eids, work=id)` 从 work_meta 读 level 传入。

## 四（已移除）、zhipu 审查防护

**2026-09-07 随 zhipu 后端整体移除**：aa_translate 的 ZhipuGuardError/退出码30/`_mark_zhipu_guard`、aa_backend 的守卫计数与 low_r18 zhipu 优先分支、translate_runner 的 `_run_translate_guard` 重试轮次、guard_rejudge.py/r18_zhipu_probe.py 工具全部删除。历史方案 `doc/zhipu审查防护与智能重试方案_2026-08-25.md` 仅存档。

## 五、视觉辅助 R18 判定（试点结论）

`scripts/tools/r18_vision_pilot.py`：原文渲染块图（未汉化可用）+ Qwen3-VL 判定。成本 1 块 ~829 token（¥0.0016），抽样 8 块/章 ≈ ¥0.013。

结论：视觉**从不变 yes**（AA 抽象画面无法确认露骨）；**有降级价值**（2/6 suspect 章确认整章日常）；**排除第 1 块**（标题梗误判）。

## 六、工作台 UI

- 作品行红 R 徽章（high 恒显 / medium 有 yes 才显 / low 有 yes 才显，悬浮占比+命中数；**颜色分级**：low 浅红 `.r-badge.low`、high 深红 `.r-badge.high`、medium 默认红不带类）；详情弹窗 R18 字段 = **可编辑下拉**（no/medium/low/high + 摘要 `dm-r18-summary`）；阅读器 yes 红 R / suspect 黄 R
- **浓度弃百分比改「命中词次数 N 次/章」**（百分比被 AA 艺术行稀释到 1-2% 且 H 台词多隐晦，永远到不了 50%+）
- 移除 tags 的 `R18` 标签（红 R 徽章替代）
- stats_cache key 加版本强制重建；`yes_ch = sus_ch = []` 共用列表 bug 勿复犯

## 推坑汇总
- **siliconflow 余额耗尽（2026-09-13 实测）**：链上原第 4 项 `siliconflow/deepseek-ai/DeepSeek-V3` 返回 `HTTP Error 402: Payment Required`，重试耗尽后降级，白烧时间/日志噪声。**已从 normal/r18 两链摘除**（2026-09-13，充值后可手动加回）。重活前先确认链上各后端都有额度。
- **auto-backend 决策链演变（2026-08-28 三修）**：`scripts/runner/translate_runner.py` 与 `scripts/aa_translate.py` 的 auto-backend 块**必须两处对称**。
  1. 原始 bug：low→设 zhipu 写成无条件 else → high/medium 也被设 AA_BACKEND=zhipu → R18 翻译全失败。
  2. 修复①：high/medium「不设 AA_BACKEND」→ **副作用：backend()=cloud → call_api 走云端直接模式(mimo-v2.5)**，mimo 对 50 行大块输出不全 → 大量混合行漏译（akari_haisin 事故：含中文行仅 19.9%，mimo@cloud 1376 次占 59%，muse-spark 仅 11 次）。
  3. 修复②（定稿）：**high/medium 显式设 AA_BACKEND=commandcode（r18 链首）**，low/空设 normal 链首（2026-09-07 zhipu 移除后 low 不再设 zhipu）。设 commandcode → call_with_fallback 走 r18 链（muse-spark 主力+自动降级）。high/medium 的 no 章（极少）被提头成 commandcode，可接受。
  **铁律**：R18(high/medium) 作品翻译后查 usage.jsonl 的 model@backend——若出现大量 `@ cloud` 或单一后端霸占，就是 auto-backend 决策错了（cloud 直连 mimo 漏译）。验证：`python -c "import sys;sys.path[:0]=['scripts','scripts/common'];import aa_config as A;print(A.auto_backend(True))"` 应返回当前 r18 链首（**2026-09-13 起 = deepseek/deepseek-flash**；此前是 commandcode/muse-spark）。
- **2026-09-13 R18 链首换官网 deepseek-flash 验收（akari_haisin entry_23908 --force 重译）**：`[auto-backend] ... high/章节级判定 -> deepseek/deepseek-flash(r18链首)` ✓；官网**对 R18 章无拒绝**（usage 5 次调用：deepseek×2 / runinfra / deepinfra / commandcode——端点轮询+降级正常，无 `@ cloud`）。但发现三个链外问题：① **siliconflow 已欠费**（`HTTP 402 Payment Required` 重试耗尽），链第 4 项实际不可用（见下方推坑）；② 重译质量劣于旧版（词级短宽 10 行 + 术语/角色名不一致 `アカリ`/`小亚加里`/`绁星灯` 混用）；③ 重译后短宽行由 refine 引入且清理链 fix_width 在 refine **之前**跑，无人兜底（详见 aa-translation-proofreading）。
- **chunks=0 全部漏译事故（2026-08-27 r401_jyusatsu）**：楼主/用户分块作者归属未知(None)时行被静默丢弃 → `flagged=2270 但 0 chunks` → 全部不翻译原文落盘；**翻译异常快(15分钟21话)=没真翻译的信号**；汉化/ckpt 时间戳停滞但进程活着=进程卡死。详见 `references/2026-08-27-chunks-zero-missed-translation.md`
- **免费模式三环节全覆盖（2026-08-27 审计）**：翻译主链路/漏译补译/清理(pass2/3/fix_width) 走 call_api→call_with_fallback 免费链 ✓；校对 proofread 走 call_with_fallback ✓（实测 GLM）；术语表 iter_glossary AI 路径走 call_api ✓（**`--backend` 参数会设 AA_BACKEND 覆盖免费链——AA_FREE_MODE=1 时已忽略**）；作品记忆 work_memory 纯文件零 API ✓；视觉流程内无调用（独立工具走付费 Qwen3-VL，符合"除视觉外全免费"）
- zhipu 1301/contentFilter 已随后端移除（2026-09-07）——不再有 R18 内容审查后端需要规避
- 翻译子进程每作品一次（单进程=单作品），后端降级记忆章节级 reset_preferred_backend 隔离
