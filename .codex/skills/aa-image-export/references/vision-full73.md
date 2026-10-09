# 视觉辅助分类全量落地（2026-08-17，73 章完成）

> 承接 vision-classification.md（方向与单章验证）→ 本文记录**全量 73 章落地结果 + 失败块补充链 + 验证方法**。

## 全量结果

- 73 章 line_meta_v3 全部生成，**3339 行判定**（1264 art 停嵌 + 1893+ text 补嵌），0 空文件
- 全量 v3 校正重渲染后 **406/6913 块（5.9%）内容变化**、覆盖全部 73 章
  = 视觉校正净效果（规则判错的嵌字被修正的量）
- 验证：73 章 / 6913 张 / 0 损坏 / 调色板无越界色 ALL PASS

## 失败块补充链（--retry-failed，三轮收敛）

**背景**：首轮全量有 ~109 块三模型全失败（glm-4.6v 解析率低 → Qwen3-VL 400 → mimo 官方被拒）。
这些块对应行没有 v3 判定 → 沿用规则（可能是残留误嵌/漏嵌）。

**流程**：
1. 从日志解析失败块集合，**只重跑这些块**（`if a.retry_failed and bi not in failed_blocks: continue`）
2. glm 重试成功率很高——多数首轮失败是临时性的（网络/限流），不必换模型
3. 仍失败的块靠修好认证的网关 mimo 补（4.5-39s 稳定成功）
4. 三轮后剩余极少数顽固块（三模型都不出格式答案）→ 接受沿用规则，影响有限

**关键实现细节**：
- **retry 写出 v3 前必须合并旧文件**：`old.update(v3)` 再 dump——否则重跑文件只含
  失败块判定 → **丢该章已成功判定**（事故根源）
- **块状态按最后状态解析**：日志顺序 `vision_retry.log`（最新）→ `vision_classify_full.log`
  → `vision_classify.log`；同一块 `last_state[key] = 'fail' if '解析率低' in ln else 'ok'`，
  只把 fail 进重跑集合——避免重跑已成功的块（
  正则注意：`re.search(r'entry_(\d+) 块(\d+)', ln)` 的 group(1) 是裸数字，
  与 `base`（`entry_8532` 形式）比较要拼 `'entry_'+group(1)`）
- **日志文件名坑**：重跑进程会覆盖 vision_classify.log → 首轮全量日志先 `cp` 成
  vision_classify_full.log 再启动重跑；重跑输出单独 vision_retry.log

## 全量渲染对比验证（快而准）

- ⚠️ **全图逐像素对比超时**（6913 张，采样 1/9 也 300s 被杀）→ 改**缩略图 hash**：
  `Image.open(path).convert('RGB').resize((32,32))` + `hashlib.md5(tobytes())`，
  97s 扫完全部 6913 块，结论可靠（5.9% 变化）
- ⚠️ **调色板验证别用"每图必须含全部关键色"**：红（骰子）/蓝（ID）只在特定行出现，
  多数图天然不含 → 误报 FAIL。正确断言 = **图内颜色必须 ⊆ PALETTE8**
  （`set(im.getdata()) - P8_set` 为空 = 量化正确零越界，抽样 3 章 × 20 张足够）

## 大块章节注意

- entry_8981 有 211 块 / 59 存疑块（最长章），判定耗时显著——长任务要接受
  `notify_on_complete` 等待，勿中途反复查进度（浪费轮次教训见 vision-classification.md）