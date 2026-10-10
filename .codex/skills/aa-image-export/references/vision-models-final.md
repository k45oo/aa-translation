# 视觉模型选型定稿（2026-08-17，覆盖 vision-classification.md 中旧 mimo 行）

> 背景：AA 图片版嵌字"视觉辅助分类"（vision_classify.py）的模型选型实测。
> 本文档为**最终结论**；vision-classification.md 中"xiaomi 官方 mimo-v2.5 … 不可靠备选 / opencode-go 网关（历史）"两行已过时，以本文为准。

## 判定链（vision_classify.py 已按此配置）

```
glm-4.6v（免费主力）→ Qwen3-VL-8B（付费主力）→ mimo-v2.5 via opencode-go 网关（备选）
```

## 模型实测汇总

| 模型 | 端点 / key | 耗时 | 结论 |
|---|---|---|---|
| ~~zhipu glm-4.6v~~ | ~~open.bigmodel.cn / BIGMODEL_API_KEY~~ | — | **已失效**：zhipu 后端 2026-09-07 整体移除；视觉首选改 `qwen-vl`（SiliconFlow） |
| **Qwen3-VL-8B-Instruct** | `https://api.siliconflow.cn/v1/chat/completions` / `SILICONFLOW_API_KEY` | 5-33s | ✅ 主力（**不免费** ¥0.5/¥2 每 M；质量最好，实测 0 误判） |
| **mimo-v2.5 via opencode-go 网关** | `https://opencode.ai/zen/go/v1/responses` / `OPENCODE_GO_API_KEY` | 22-27s | ✅ 备选（**无内容审核**） |
| ~~xiaomi 官方 mimo-v2.5~~ | `https://api.xiaomimimo.com/v1/chat/completions` / `XIAOMI_MIMO_API_KEY` | 9-15s | ❌ **弃用**：官方 API 有内容审核，AA 画面图偶发被拒（`request rejected because it was considered high risk`，实测图 0086 被拒）——日志里 mimo "解析率低 0/N" 的真相是审核拒绝而非模型问题 |
| ~~MiniMax-M3~~ | `https://api.minimaxi.com/v1/chat/completions` / `MINIMAX_CN_API_KEY` | 4-5s | ❌ 不采用：推理模型，思考过程进 content（`` 标签包裹）、不输出明确的"台词/画面"格式答案、读图有误（"今天是我的生日"→"阿空的生日"） |

## mimo 走网关的关键格式（踩坑）

- **必须用 responses 端点**（`/zen/go/v1/responses`），不是 chat 端点（chat 端点直连被断/403）
- **图片字段**：`input[].content[]` 里用
  `{'type': 'input_image', 'image_url': {'url': 'data:image/png;base64,...'}}`
  ——`image_url` 必须是**嵌套 `{'url': ...}` 对象**，裸字符串会 400（`image_url is invalid`）
- **必须带浏览器 UA**：无 UA 时 403 Cloudflare `error code: 1010`
- **响应解析**：responses 格式 `output[].content[].text`（非 choices）；aa_api.py `_extract_text` 已兼容两种
- 模型名就是 `mimo-v2.5`（网关版与官方版同名，靠端点区分）

## 其他要点

- **Qwen3-VL 批量 400 两个根因**（都修过）：①图片最长边 >1024 需 resize；②上下文过长——只传"红框行 ±2 行"文本。单独调用成功 ≠ 批量成功，批量失败先查这两个。
- **read_env 要查双档案 env**：`<hermes>\profiles\translation\.env` 优先 → `<hermes>\.env` 兜底（XIAOMI_MIMO_API_KEY / MINIMAX_CN_API_KEY 在 translation 档案）。
- 判定输出容错：模型可能输出 "N: 画面(AA)" / "行号N：text" 等变体，正则须容忍括号/空格/大小写/絵/イラスト。
- 全量 73 章长任务（6-9 小时）：`terminal(background=true, notify_on_complete=true)` 后**停止轮询**等通知；断点续跑靠"已有 line_meta_v3 文件且 size>10 则跳过"。
