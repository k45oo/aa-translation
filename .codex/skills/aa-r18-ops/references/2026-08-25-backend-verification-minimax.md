# 2026-08-25: zhipu 优先后端验证 + MiniMax 实测

## auto-backend low 作品不设 AA_BACKEND（修复记录）

**现象**：用户要求 sujin_otona「用 zhipu 翻译」，但日志显示 `[auto-backend] sujin_otona -> commandcode / meta/muse-spark-1.2-contributor`。

**根因链**：
1. aa_translate main 的 auto_backend 按 **registry r18**（作品级，sujin_otona r18=True）选后端 → 设 `AA_BACKEND=commandcode`
2. 章节级 `_chapter_work_r18` 判 r18=no → `work_r18=False` → normal 链应为 `['zhipu', 'commandcode']`
3. 但 call_with_fallback 的 explicit 处理（`if explicit and not low_r18:`）把 commandcode 提到链首 → `['commandcode', 'zhipu']`
4. **实际走了 muse，非 zhipu**

**修复**（aa_translate.py main 的 auto_backend 块）：`r18_level=low + prep 目录存在 → 不设 AA_BACKEND`（由章节级 prep 驱动）；high 或无 prep → 照旧。验证：sujin_otona(low)→不设、akari_ninshin(high)→照旧。

**验证实际后端的办法**：usage.jsonl 记录不含后端名——用**时间窗**（基线行数后的新增行）判定；或看 runner 日志的 `[auto-backend]` 行 + 是否有 zhipu 调用特征。

## MiniMax M3/M2.7 实测（scripts/tools/test_minimax.py）

端点 `https://api.minimaxi.com/v1/chat/completions`（OpenAI 兼容），key=`MINIMAX_CN_API_KEY`。

| 测试 | MiniMax-M3 | MiniMax-M2.7 |
|---|---|---|
| 翻译 | ✅ 可用但格式噪音（`1|` 前缀残留 + 翻译说明） | ⚠️ 格式乱（`**翻译：**` 注释、`**镜子……**` 加粗） |
| 校对（鏡→眼镜） | ✅ 判断正确 | ❌ 剥 think 后空响应（M2.7 思考关不掉） |
| R18 | ❌ 审查拒绝（「我无法提供这类内容的翻译」/ 422） | ❌ 强审查拒绝（「抱歉，我无法协助」） |

**结论**：model_config.toml 的 minimax `r18` 已从 true 改 false（原标注错误）。M3 可作校对/审阅候选；翻译/批量仍用 muse/mimo/zhipu。文档：`doc/翻译模型对比_2026-08-21.md` 的「D. MiniMax 端点测试」节。

## 消耗统计口径（usage.jsonl）

- 格式 `{"tag","ts","prompt","completion","total"}`，不含后端名
- 统计某次任务消耗：**先记基线行数**（`wc -l usage.jsonl`）→ 任务完成后统计 > 基线的行
- sujin_otona 5 话实测：232 次调用 / 99.9 万 token（但那次实际走了 muse 非 zhipu——见上）
