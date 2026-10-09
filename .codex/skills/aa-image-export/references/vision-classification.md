# 视觉辅助分类管线（2026-08-16 落地，vision_classify.py）

> 承接 aa-classification-aahub.md（方向探索）→ 本文件记录**已验证的落地实现**。
> 解决嵌字 6 轮迭代后仍存的①误放大 AA ②漏放大文本——根因 = 纯规则分类（ART_KANJI 手工枚举不完）。

## 核心设计：按块（楼）区分，视觉模型参与

**用户判断（正确）**：AA 行强上下行关联（角色=连续 10-20 行，轮廓/阴影跨行延续），
**单行判定永远有歧义**——同一行字符单独看可以是台词也可以是画面一部分，放进整块上下文
人一眼看出。视觉模型看像素级上下文 = 规则做不到的。

**分层策略（用户确认）**：规则先粗筛 → 仅存疑行送视觉（免费额度内）→ 结果缓存复用。

## 数据流

```
规则粗筛存疑行(误嵌嫌疑: spans非空且art>35%; 漏嵌嫌疑: spans空且含中文art<25%)
  → 渲染块图(存疑行红框+编号标注, vision_annotate.py)
  → 视觉模型判定(glm-4.6v优先→Qwen3-VL→mimo) → line_meta_v3/<eid>.json
  → 嵌字消费: v3=art 停嵌(修误嵌) / v3=text 补嵌(修漏嵌)
```

## 视觉模型对比结论（2026-08-16/17 实测）

| 模型 | 端点/key | 速度 | 结论 |
|---|---|---|---|
| ~~zhipu glm-4.6v~~ | ~~open.bigmodel.cn / BIGMODEL_API_KEY~~ | — | **已失效**：zhipu 后端 2026-09-07 整体移除（额度耗尽 + 审查风险）；视觉首选改 `qwen-vl`（SiliconFlow） |
| **Qwen3-VL-8B-Instruct** | api.siliconflow.cn / SILICONFLOW_API_KEY | 5-33s | 主力（¥0.5/2.0 每M，不免费；质量最好 0 误判） |
| xiaomi 官方 mimo-v2.5 | api.xiaomimimo.com / XIAOMI_MIMO_API_KEY | 9-15s | ⚠️ **内容审核**：部分 AA 图返回 "high risk" 拒绝 → 不可靠备选 |
| MiniMax-M3 | api.minimaxi.com / MINIMAX_CN_API_KEY | 4-5s | ❌ 推理模型，思考进 content、不输出格式答案、读图有误 → 不采用 |
| opencode-go 网关 mimo-v2.5 | （历史） | - | 之前探测用过，现用官方端点 |

**⚠️ Qwen3-VL 400 Bad Request 两个根因（都修过）**：
1. **图片尺寸超限** → 渲染图最长边 >1024 时 400；必须 `resize` 到最长边 ≤1024
2. **上下文过长** → 块内全行文本清单超长；只传"红框行 ±2 行"上下文
单独调用成功 ≠ 批量成功：批量失败先查图片尺寸 + 文本长度。

## 嵌字消费逻辑（aa_to_images.py v2_text_spans 加 v3_verdict 参数）

```
v3_verdict == 'art'  → 返回空(该行不嵌字, 修误嵌)
v3_verdict == 'text' → 沿用规则 spans(规则已判对); 仅当规则 spans 空(漏嵌)才整行兜底
                       ——no_dialogue 提前 return 分支也要考虑 v3=text 放行
```

**验证**：entry_8532 27 行判定（9 art + 18 text）中 26 行规则已判对（无差异），
视觉精准命中唯一判错行 3107（画面"汽"字规则误嵌 → 视觉判 art → 停嵌，块0086 差异 442px）。

## AAhub 统计（aahub_stats.py，已验证）

- 15023 .mlt 解析（Shift-JIS→UTF-8，[SPLIT] 分帧，跳过头部注释帧）
- 产出 542 艺术用字候选（艺术行出现≥100次），新增 469 字覆盖漏网纹理字（匕/圦/芹/艾/斗/心）
- **⚠️ 关键限制：不能直接替换 ART_KANJI**——统计混入语义常用字（世/中/魔/龍/世界），
  因为 AA 素材含角色台词帧 + 角色身体用汉字拼。字符频率无法区分"魔字作画"与"魔字作词"，
  **最终裁决必须靠视觉模型看上下文**（"魔法少女"vs 作画"魔"字脸，视觉一目了然）
- ⚠️ **Windows ProcessPoolExecutor 不能 pickle 嵌套函数**：worker 必须是模块级函数，
  且 `if __name__ == '__main__'` 保护；`ex.map(_process_file, all_mlt, chunksize=32)`
- ⚠️ 单进程解析 2.4GB 卡死（CPU 0.06s/20min——stdout 管道阻塞）；多进程 + `-u` +
  输出重定向到日志文件（非管道 tail）才能跑

## 全量 73 章判定（长任务经验）

- 每章 0-30 存疑块 × 2-48s/块 = 全量约 6-9 小时（glm-4.6v 免费）
- **断点续跑必须**：已有 v3 文件章节自动跳过（`os.path.getsize > 10` 判已判定）
- **⚠️ 长任务等待教训（2026-08-17 实测教训）**：全量判定期间反复 poll 进度浪费大量调用；
  正确姿势 = `terminal(background=true, notify_on_complete=true)` 后**停止轮询**，
  等待完成通知。中途修复代码（如 Qwen3-VL 400）需要 kill 重启时，skip 逻辑保证不重跑已完成章节。

## 相关文件
- `scripts/tools/vision_classify.py` — 批量判定（存疑粗筛→渲染→视觉→v3落盘）
- `scripts/tools/vision_annotate.py` — 红框标注渲染
- ~~`scripts/tools/aahub_stats.py`~~ — AAhub 素材统计（**2026-10-10 已随 AAhub 素材库删除**，脚本不存在）
- `scripts/tools/aa_to_images.py` — v2_text_spans 加 v3_verdict 消费
- `さーきー/work_fc2/line_meta_v3/*.json` — 视觉判定结果（git 跟踪）
- 实验记录：`doc/图片版楼主楼嵌字实验记录_2026-08-14.md` + `doc/视觉模型对比记录_2026-08-16.md`
