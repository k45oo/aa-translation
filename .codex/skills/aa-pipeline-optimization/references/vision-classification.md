# 视觉辅助分类管线（2026-08-16/17 全量 73 章实战定型）

## 背景与动机

纯文本规则分类器（line_classify_v2 的 ART_KANJI 手工枚举）存在两类漏网：
- **误嵌**：画面纹理汉字（劜/芹/坏/汽 等）不在黑名单 → 被当台词嵌字放大
- **漏嵌**：真台词被误判 art（如"今天是我的生日！"行尾台词）

用户判定：**AA 行强上下行关联，只按行区分永远存在误判，必须按块（楼）视觉判定**。
AAhub 素材（2.3G/15023 个 .mlt）不能直接解决（纯字符频率分不清"魔字作画"vs"魔字作词"），
只能提供候选集缩小排查范围。最终裁决靠视觉模型按块看图。

## 管线（五步）

```
① AAhub 统计 → 542 艺术字候选（嫌疑字表，非直接替换）
② 规则粗筛存疑行（误嵌嫌疑:spans非空且art>35%; 漏嵌嫌疑:spans空且含中文art<25%）
③ 渲染存疑块红框+编号标注（vision_annotate.py）
④ 视觉模型按块判定 → v3 落盘 line_meta_v3/<章节>.json {行号: 'art'|'text'}
⑤ 嵌字消费 v3（v2_text_spans 加 v3_verdict 参数: art→停嵌; text→补嵌）
```

## 视觉模型实测对比（同图同口径）

| 模型 | 端点/key | 耗时 | 判定 | 结论 |
|---|---|---|---|---|
| **glm-4.6v** | open.bigmodel.cn / BIGMODEL_API_KEY（6000 万免费 token） | 2-30s/块 | ✅ 主力 | **首选**（免费、快、稳定） |
| **Qwen3-VL-8B-Instruct** | api.siliconflow.cn / SILICONFLOW_API_KEY | 11-33s | ✅ 最结构化 | 主力（**付费**：¥0.5/M in + ¥2/M out，勿当免费） |
| **mimo-v2.5 官方** | api.xiaomimimo.com / XIAOMI_MIMO_API_KEY | 9-15s | ⚠️ | **内容审核**：部分图 high risk 拒绝（0086 被拒） |
| **mimo-v2.5 网关** | opencode.ai/zen/go/v1/responses / **OPENCODE_GO_API_KEY** | 22-27s | ✅ | 无审核限制，官方被拒图正常 → fallback 用网关版 |
| **MiniMax-M3** | api.minimaxi.com / MINIMAX_CN_API_KEY | 4-5s | ❌ | 推理模型：思考进 content、不遵守输出格式、读图有误 → 不加入 |

**最终判定链**：glm-4.6v（免费优先）→ Qwen3-VL-8B（主力）→ mimo-v2.5 via 网关（备选）。
Qwen 批量 400 根因 = 图片最长边 >1024 + 上下文过长 → 渲染后缩放 ≤1024 + 只传红框行±2 行。

## opencode-go 网关视觉请求格式（关键）

```python
# POST https://opencode.ai/zen/go/v1/responses
# headers: Authorization: Bearer <OPENCODE_GO_API_KEY>（非 xiaomi key，否则 401）
#          User-Agent: 浏览器 UA（否则 403 Cloudflare error 1010）
body = {
    'model': 'mimo-v2.5',
    'input': [{'role': 'user', 'content': [
        {'type': 'input_image', 'image_url': {'url': f'data:image/png;base64,{b64}'}},
        {'type': 'input_text', 'text': f'{sys_prompt}\n{user_text}'},
    ]}],
}
# 响应: output[].content[].text（responses 格式，非 choices[].message.content）
```

## AAhub 统计要点（aahub_stats.py）

- .mlt = Shift-JIS 编码，`[SPLIT]` 分隔多帧，文件头有注释帧（"最終更新日"）
- 多进程：Windows ProcessPoolExecutor 需要**模块级函数**（pickle）+ `if __name__ == '__main__'` 保护
- 输出：542 艺术字候选（艺术行出现 ≥100 次）——比手工 77 字多 5 倍，但**混入台词字**（魔/龍/世界）
  → 纯字符频率无法区分"作画"与"作词"，必须视觉裁决

## v3 嵌字消费（aa_to_images.py）

`v2_text_spans(v2_line, txt, v3_verdict)`：
- `v3=='art'` → 返回空（停嵌，修误嵌）
- `v3=='text'` → 沿用规则 spans；规则空（漏嵌）时整行嵌
- 注意：`no_dialogue` 提前 return 分支也要加 v3=text 的整行兜底（曾漏改导致漏嵌模拟失败）

## 失败块补充判定（--retry-failed）

- 全量判定后解析日志失败块（`entry_(\d+) 块(\d+):? 解析率低`）→ 只重跑这些块
- **行号口径坑**：正则 group(1) 是 `8540`（无 entry_ 前缀），要 `'entry_'+group(1)==base` 才匹配
- retry 合并旧 v3（不丢已成功判定）；日志先备份（vision_classify_full.log）再重跑

## 长任务执行教训

- 全量 73 章视觉判定 ≈ 10h（串行 + 每块多模型尝试）；glm 成功率高时 2-15s/块
- 第一轮 109 失败块（59 章）多数是**临时失败**（glm 重试直接成功）——别急着并发/优化
- 后台任务用 notify_on_complete 等待，**别反复轮询**（本会话在轮询上浪费大量调用）
- 渲染与判定**不能并行**（v3 半写状态会被渲染读到 → 先停渲染等判定完成）

## 关键脚本

- ~~`scripts/tools/aahub_stats.py`~~ — AAhub 素材统计（542 艺术字候选）**已随 AAhub 素材库删除（2026-10-10）**
- `scripts/tools/vision_annotate.py` — 存疑块红框标注渲染
- `scripts/tools/vision_classify.py` — 批量视觉判定（--chapter/--limit/--retry-failed）
- 判定链配置在 vision_classify.py main()（models 列表，mimo 条目带 'responses' fmt 标记）
