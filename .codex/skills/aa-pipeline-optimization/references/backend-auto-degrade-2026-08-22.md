# 2026-08-22：后端自动降级 + deepseek官网/deepinfra + 清理可续跑 + 术语白名单语法词污染

## 背景
`[default].r18_backends`/`normal_backends` 原 commandcode/muse-spark 首选。
2026-08-22 commandcode 周配额满(HTTP 429) → 临时改 **deepinfra 首选 + deepseek官网 兜底**。
`auto_backend()` 对 R18/非R18 均返回 `('deepinfra','deepseek-ai/DeepSeek-V4-Flash-0731')`。
恢复 commandcode：直接改回 `model_config.toml`（原值 = commandcode/muse-spark-1.2-contributor + mimo）。

## 新增两个后端（model_config.toml）
```toml
[backend.deepseek]
url = "https://api.deepseek.com/responses"       # Responses 端点(与 ds_websearch 同源)
model = "deepseek-v4-flash-vision-exp"
key_env = "DEEPSEEK_API_KEY"
format = "responses"                              # instructions/input/max_output_tokens; 须 thinking:disabled
r18 = true                                        # 先试官网; R18 被审核拒时换 deepinfra

[backend.deepinfra]
url = "https://api.deepinfra.com/v1/openai/chat/completions"
model = "deepseek-ai/DeepSeek-V4-Flash-0731"
key_env = "DEEPINFRA_API_KEY"
r18 = true                                        # OpenAI 兼容无额外审查; 有 reasoning 须 reasoning_effort=none
```
key 在 `translation/.env`：`DEEPSEEK_API_KEY` / `DEEPINFRA_API_KEY`（均 present）。

**deepseek官网坑**：Responses 解析用 `output[].content[].text` 拼接（非 choices[].message.content）。
`thinking:{type:disabled}` 必须；**长 fix_batch 返 `status:incomplete` 空响应**（不是 R18 拒绝，是输出截断）——
遇到用 `AA_BACKEND=deepinfra` 兜底。官网**本身不拒 R18**（露骨台词实测可过）。

## 后端自动降级（aa_backend.call_with_fallback）
- `aa_translate.call_api` 在 `backend() != "cloud"` 时转发 `aa_backend.call_with_fallback(work_r18, ...)`（返回 text）。
  cloud(未设 AA_BACKEND)才落云端直接模式(gateway/key/proxy 重试)。`work_r18 = AA_R18 in ("1","true","True")`。
- `call_with_fallback`：候选链 `aa_config.backend_chain(work_r18)`（=auto_backend 同口径：key_env 存在 + R18作品 r18_safe，按配置顺序）；显式 `AA_BACKEND` 置链头；`_PREFERRED_BACKEND` 记住当前可用后端避免重复撞已坏的。
- `_degrade_reason(e)` 判定可降级：429/rate/quota/limit→"限流配额"；incomplete/truncated→"响应截断"；
  R18+审核/reject/被拒→"R18被拒"；空响应/empty→"空响应"。**非降级错误(connection reset 等)立即抛不换后端**。
- `_model_for_backend(b, config_model)`：`AA_<BACKEND>_MODEL` env > 配置链 model > 后端默认。
- 全候选失败抛 `全后端失败: ...`。
- 新增依赖：`aa_config.backend_chain(work_r18)`（返回有序可用候选 [(backend, model), ...]）。

## translate_runner 自动选后端（修 429 根因）
**根因**：translate_runner 直接 `import pass2/pass3/fix_width` 调函数（不经 aa_translate --work），进程里无
`AA_BACKEND` → `call_api` 走 cloud 云端路径 → 读 `[default].backend`(=commandcode) → 429。
**修复**：runner 在 `cfg = registry.get(work)` 后，未显式设 `AA_BACKEND` 时按作品 r18 调 `auto_backend`
设 `os.environ["AA_BACKEND"]`（并 setdefault 对应 `AA_*_MODEL`）。清理步骤从此走 call_with_fallback。
同类：`aa_translate.main()` 的 --work 分支已有同样逻辑（第 1274-1290 行）。

## 清理可续跑（translate_runner only_cleanup）
原逻辑 `if not sel_eids: log("无待翻译章节") return` —— 中断在清理阶段的 run 无法续跑清理。
**修复**：`not sel_eids` 时，若 `_local_tr_eids(cfg)` 非空 → `sel_eids=done; only_cleanup=True`。
only_cleanup 跳过 dl_runner补缺/chapter_prep/aa_translate/iter_glossary，只跑定向
pass2→pass3→scan_width→fix_width→scan_width复验→生成合集。
实测（akari_ninshin 10话已译）`translate_runner --work akari_ninshin --no-dl` 走通，`DONE in 69s`。

## 清理轮次下调（省 token，清不掉的行更早落 review/manual 人工）
- pass3 外层 `for iteration in range(6)` → **range(4)**
- pass3 内层 fix_lines `for round_ in range(4)` → **range(2)**
- fix_width `for attempt in range(3)` → **range(2)**

## 术语白名单语法功能词污染（重要 — 根因级，直接导致测试回归）
**症状**：`test_copy_jp_detected` 失败——translate_lines 把模型照抄的 `おはようございます` 当"已译"接受。
**根因**：`glossary.json` 的 `known_names` 混入 46 个**日语语法功能词**（おはよ/おはよござます/みんなおはよ/です/
けど/でも/なるほど/確か/さんが... 等）。这些词被白名单豁免 → 照抄日文台词/问候被放行。
**诊断路径**：`residual_suspect("おはようございます")` 返回 `[]` 但 `kana_runs` 能提出片段 → 片段命中 KNOWN_NAMES。
`kana_runs("おはようございます")` = `['おはよござます']`（剔除う/い 后），该片段 in known_names 即污染。
**清理**：`known_names` 1141→1095（删44+2）；`角色` 571→565（删6）。备份 `glossary.json.bak_<ts>`。
**治本**：
- `gen_glossary_ai.FUNC_WORDS` 从 ~50 短助词扩到含长语法短语（おはよ/ですよ/ます/なるほど/確か/さんが...）。
- `iter_glossary.merge_confirmed` 加防线：`t in GA.FUNC_WORDS` 时 `skip`（不入 known_names，打印提示）。
  **之前 `--merge` 无条件写入——这就是污染来源**（iter 候选打 note 警示但 merge 无视）。
**边界词保留**（可能是艺术纹理/角色碎片，删了误报艺术行）：えば/さき/なお/てょ/ちゃ/ちゃん/はぁ。
**kana_runs 限制**：只捕获连续假名段 len≥2；单假名夹汉字间（如「確か千代の」的 か/の）不被捕获——既有设计非本次引入。

## 验证
- 全量 162 用例：HEAD 与改动后均为 1 fail（test_copy_jp_detected，白名单污染导致）；清污染后全绿（1 skip）。
- 残留检测恢复严格：`おはようございます`→残留、`それでは、また明日`→残留。
