# 2026-08-25 后端选择与术语表正则（实测）

## low 作品后端选择的坑（最严重）
- **症状**：sujin_shabu 翻译时用户后台看到 21:38 起大量 `xiaomi/mimo-v2.5` 调用——期望 zhipu 免费却全走 mimo。
- **根因链**：auto_backend 对 low r18_level 作品"不设 AA_BACKEND" → `aa_backend.backend()` 返回 `cloud` → `call_api` 走**云端直接模式**（`[default].model = xiaomi/mimo-v2.5`）→ 跳过 zhipu。
- **正确做法**（aa_translate.py 与 translate_runner.py 两处 auto_backend 同逻辑）：low + prep 存在 → **显式设 `AA_BACKEND=zhipu` + `AA_ZP_MODEL=glm-4.5-air`**。验证：no 章/yes 章(low_r18) 实际后端均 zhipu。
- **改后端选择逻辑后必须实测**：`AA_BACKEND` 未设时 `backend()=='cloud'`，云端直接模式用的是网关默认模型，与 call_with_fallback 链无关。

## 视觉兜底 mimo2.5-cmd
- models_r18 / models_normal 第 3 位兜底 = `mimo2.5-cmd`（commandcode 的 xiaomi/mimo-v2.5 视觉端点）。
- **mimo-v2.5 是全模态模型**（文本+视觉+音频+视频）——兜底可用，勿改成别的（曾误改 qwen2.5-vl 又被用户要求还原）。
- 改配置前先确认模型模态；视觉兜底不是"纯文本模型"，是全模态。

## 术语表正则（re: 前缀，2026-08-25）
- glossary.json 术语对支持 `re:` 前缀正则：`["re:([一-龠]{1,4})さん", "\\1桑"]` ——匹配「人名+さん」→「人名+桑」。
- 消费方 `aa_web_app/logic.apply_glossary_chapter`：`jp.startswith("re:")` → `re.compile(jp[3:])` + `rx.sub(zh, nb)`（zh 可含 `\1` 反向引用）。
- **级联生效**：普通术语「やる夫→亚鲁欧」+ 正则「さん→桑」→「やる夫さん→亚鲁欧桑」。
- **两个必须**：
  1. 正则项**不过滤**（`_filter_glossary_pairs` 对 `re:` 开头直接保留——正则串不在语料但语义始终适用）
  2. 正则项**不注入翻译 prompt**（`glossary_prompt_hint` 过滤 `re:` 项——模型看不懂正则）

## さん→桑 全库修复（c0b40fe）
- 用户纠正：「人名+さん」译为「人名+先生」是误译，通用译法是「人名+桑」；「先生」仅用于教师/医生。
- `scripts/tools/fix_san_sensei.py`：**原文行含 さん + 译文含 先生 → 先生是さん误译 → 改桑**；原文行不含さん → 真实先生保留。
- 全库修 1260 处（saki_fc2 450 / jinrou 498 / touhou_teisou 191 / saki 75...），保留 148 处真实先生（すこやん先生/ニィ大先生/老师）。
- 宽度：先生(4宽)→桑(2宽) 变短，pad_to_width 补偿；零新错位。
- 翻译 prompt 已加通用规则：`glossary_prompt_hint` 末尾「【通用译名规则】さん→桑，先生仅教师/医生」。
