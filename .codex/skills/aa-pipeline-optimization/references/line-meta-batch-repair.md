# 跨作品批量补刷 / 校验 line_meta_v2（安全姿势）

零 API 作业，但**很容易把用户机器压死或产出静默错位的 meta**，照本文件走。

## 何时需要

- 日志出现 `[M4a] meta 缺失/损坏, fallback is_text_line`；
- 日志出现 `警告: 行数不齐 <N> vs txt <M>,跳过`（该章 meta 被整章拦掉）；
- `extract_dialogue` 抛「meta 生成失败（行数不齐/站点不支持），中断」（台词/简介/术语候选链的前置）。

工具：`scripts/tools/gen_meta_batch.py`（跨作品、增量补缺、可校验、零 API）。

## 三条不可违反的口径

1. **生成端行口径 = 落盘 txt 的 `read().split('\n')`**（aa_translate / extract_dialogue 的读法）。禁止拿 `site.extract()` 重新提取的结果当行序——同作品的 txt 若由旧解析器产出（空行保留策略/原帖版本不同），自解析虽自洽却与磁盘行号错位，生成出来的 meta 整章歪掉。
2. **站点解析入口走统一钩子**：正文段用 `_extract_article_seg`（按签名传 eid，勿无条件单参），帖子解析用 `_site_parse_posts`（优先站点可选钩子 `parse_posts_auto`）。有多形态的站点（如旧格式 `.NNN ：` <p> 流式 vs 新格式 `<dt>`）必须提供 `parse_posts_auto` 并与 `extract()` 同口径，否则整批章解析出 0 帖 → 行数必然不齐。
3. **错位 meta 比缺失 meta 危险得多**：错位会把错误 kind 喂进翻译。对齐判据 = `len(keys) == len(txt_lines) - 2` 且 `min(keys) == 3` 且 `max(keys) == len(txt_lines)`（前两行是 `# 标题`/`# 原帖地址`）。不满足就删文件，回退 `is_text_line` 比留错位安全。

## 步骤

1. `--dry` 看缺口总量与涉及作品（示例规模：54 作品 / 12789 章，属上万章级作业）。
2. `--work <id>` 小样本试跑 1~3 个作品，确认「生成 N | 不齐 0」。
3. 全量补缺：`--missing`（默认只补缺失、不动已有 json）+ `--workers 2`。
4. 体检：`--verify --dry` → `对齐 N | 错位 M`，明细形如 `[错位] <work> <base>: meta K 行/max X vs txt Y 行`。
5. `--verify`（去掉 --dry）删除错位 meta；需要的话再跑第 3 步补回能补的——对 parse≠disk 的章会如实报「不齐」，**保持缺失好过留错位**。
6. 验证：① 目标作品整跑一次 `--work <id>`，应 0 条「行数不齐」；② 抽查 30~60 章「meta max key == txt 行数」；③ 对比 flagged 数（meta 驱动 vs is_text_line 常降 60~75%，既省 token 也少误标）。

## 坑

- **分块并行按 key 汇总必须累加**（`agg[k] = agg.get(k, 0) + n`）：写赋值只留最后一块的计数——总数对、明细错，照明细定位问题会被带偏。
- `registry.all_works()` 里可能有 `raw_dir` 不存在的作品（只有 aa 的语料）→ 列清单与校验分支都要 isdir 守卫，否则 `os.listdir` 直接抛。
- **上万章批处理禁止高并发**：默认 2 并发，先 `--dry` + 小样本再全量；一次性开 6 进程会把用户机器压到卡死（用户明确叫停过）。跑之前先问用户要不要跑、跑多大。
- **补不回来的三类**（保持缺失 + fallback，别硬塞）：① raw 与 txt 内容就不一致（同一章帖头编号都不相同 = raw 换过源）；② 纯空行口径差（忽略空行后完全一致 = txt 是旧解析器产物）；③ 站点没有 `extract_article`/`parse_posts`（白头系 `===== AA块 N =====` 非帖子型语料，帖子级 meta 模型不适用）。
- 重刷是写操作：先确认目标作品没有正在跑的翻译/校对任务（meta 与 ckpt 的口径必须一致）。
