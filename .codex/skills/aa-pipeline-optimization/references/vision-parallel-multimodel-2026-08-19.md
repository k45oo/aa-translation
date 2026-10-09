# vision_classify 并行 + 多模型 + 反哺（2026-08-19 实战）

> 归属：aa-pipeline-optimization「视觉辅助分类」章节的展开明细。
> 模型选型权威版在 aa-image-export/references/vision-models-final.md（2026-08-19 更新）。

## 章级并行（ProcessPool）

- `vision_classify.py --workers N`（默认 3）：每章独立 v3 文件（`line_meta_v3/<base>.json`）无写竞争
- 断点续跑：已有 v3 且 `size>10` 则跳过；日志按章收集回主进程统一打印（防交错）
- 加速实测：3 章 3 进程 0.5-1.9 分钟（vs 串行 3 倍）；touhou 52 章预估 4-6h → 1.5-2h
- ⚠️ 旧串行进程要 kill 掉再起并行版（否则两进程争同一批 API 配额 + 重复写 v3）

## 多模型 round-robin 主力分配

- 配置元组统一 **5 元素** `(name, url, key, model_id, fmt)`：
  - `('glm-4.6v', zhipu_url, BIGMODEL_API_KEY, 'glm-4.6v', 'chat')`
  - `('Qwen3-VL-32B', sf_url, SILICONFLOW_API_KEY, 'Qwen/Qwen3-VL-32B-Instruct', 'chat')`
  - `('mimo2.5-cmd', cmd_url, COMMANDCODE_API_KEY, 'xiaomi/mimo-v2.5', 'chat')`
- 请求体用 **model_id**（Qwen 带 `Qwen/` 前缀、mimo 带 `xiaomi/` 前缀），name 仅日志/记录用
- 每章按 `i % len(models)` 选主力；主力失败（超时/解析率低/异常）→ 依次 fallback 其他模型
- 结束写 `work/vision_model_chapters.txt`：模型 → 章节列表（用户要求记录每个模型识别了哪些章节）

## spawn 子进程坑（Windows ProcessPool 关键）

- **spawn 子进程不继承模块级全局变量**（重新 import 模块 → 全局 dict 是空的）
- 主力模型映射**必须作为函数参数传入** worker（`process_chapter(..., primary_model=...)`）
- 症状：子进程日志显示"主力模型=glm-4.6v"（all_models[0]）而非分配给他的模型
- 修复：`primary_map = {b: models[i % len(models)] for i, b in enumerate(bases)}` → `ex.submit(process_chapter, ..., primary_map[b])`

## 模型端点实测（2026-08-19）

| 模型 | 端点 | 耗时 | 备注 |
|---|---|---|---|
| glm-4.6v | 智谱 chat | 2-25s/块 | 免费额度仅剩 303 万 token |
| glm-4.6v-flashx | 智谱 chat | 2.1s | 8元/10M，无审查，免费耗尽后接替 |
| Qwen3-VL-32B | siliconflow chat | 1.5-21s/块 | ¥0.0022/图，**最快且准**，主力首选 |
| mimo2.5-cmd | commandcode chat | 4.5-25s | **无审查无限额**（替代 opencode-go 网关 429） |
| ~~opencode-go 网关 mimo~~ | zen/go/v1/responses | 22-27s | 2026-08-19 报 GoUsageLimitError 月度限额，10 天重置 |

## commandcode 端点格式（2026-08-19）

- 端点：`https://api.commandcode.ai/provider/v1/chat/completions`（OpenAI 兼容 chat）
- **模型名必须带 provider 前缀**：`xiaomi/mimo-v2.5`（裸 `mimo-v2.5` 或 `-pro` 都报 ModelError not supported）
- 图片：标准 `{'type':'image_url','image_url':{'url':'data:...'}}` + 浏览器 UA
- 响应：content 正常；reasoning 字段另带思考（可忽略）
- key：`COMMANDCODE_API_KEY`（translation 档案 env）

## 安全审查测试方法论

- **测试图 = 曾被 xiaomi 官方拒的敏感图**（entry_8532 的 0086）——能判定 = 无审查
- 新端点接入前先测，避免批量跑一半发现被拒
- 2026-08-19 结论：glm-4.6v-flashx + commandcode mimo2.5 均无安全审查

## 反哺翻译管线（2026-08-18 grilling 定稿 + 实现）

图片版嵌字经验反哺 aa_translate.py（详见 doc/图片嵌字反哺翻译流程方案_2026-08-18.md + doc/反哺实验记录_2026-08-18.md）：

1. **⑥ SYSTEM_MIXED 提示词优化**：语义完整约束（"不得省略任何实词"）+ 禁空格补宽 + few-shot → 稳定防"日和"吞字（用户案例 `いいお出かけ日和だお` 从偶发吞字→稳定完整）
2. **② 段级宽度校验**（translate_lines_mixed）：每段校验 `disp_width(t) > disp_width(orig_seg)`（超宽）或 `原段宽-译文宽 > 4`（吞字嫌疑）→ 判失败重译。**"吞字填空格"漏译根因 = 整行宽校验被画面部分掩盖，段内宽不符不查**
3. **⑤ v3 回写 v2**（`scripts/tools/v3_backport_v2.py`）：v3=art 的行其 segments 里 text 段 kind→art + conf→0（不进 _confident_text_segs）→ 翻译不送模型/嵌字不放大。saki 255 行/367 段
4. **⑦ 段合并评估后放弃**：回写后仍 2607 行"相邻 text 段"，但抽样显示绝大多数是**并排独立台词**（人物名+台词），合并误伤 > 收益；真切碎极少
5. **③④ 视觉复核降级为规则方案**（`vision_review.py`）：日文残留检测本质是文本任务（视觉误报高 + 红框行号偏移 + 专名误判）→ 纯规则扫描（台词行 text 段区域查非艺术假名连续段≥2 + PROPER_NAMES 白名单 + ART_KANA 占比过滤 + art 主导行跳过）。**抓到真漏译**"麻雀でテスカトリポカ…？"（整句日文没翻）
6. **清单处理**：PROPER_NAMES +45 角色术语 + ART_KANA 补画面纹理 → 误报 4292→1037 行；真漏译 44 处补译（さすやる×8→"不愧是你"、メガテン梗×3、整句漏译 ~20 处）全部整行宽匹配（scan_width 验证 0 新 mismatch）

## 教训

- **新作品接入 vision_classify**：`--work` 决定 site（site_map: saki_fc2→fc2, touhou_teisou→touhouyaruosure）；line_meta/ 缺失容错（空 dict）；line_classify.py 也支持 `--chapter` 多值
- **line_meta 生成极快**（101 章 45 秒，纯本地零 API）——比预估 1-2h 快得多
- render_block 的 `freeze_post` 是位置参数（第 10 位）+ 关键字重复传 → `multiple values` 崩溃
