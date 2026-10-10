# usage.jsonl 滚动归档（2026-08-31）

## 背景

usage.jsonl（token 用量统计日志）无限增长：翻译任务高峰期实测 12MB/68K+ 行。
任务管理器 `_usage_index` 曾因全量扫它卡死系统（见 SKILL.md 假死检测节）。
除读取侧指纹缓存外，写入侧也限大小：超限滚动归档。

## 机制（scripts/common/aa_usage.py，单一权威）

- 阈值：`AA_USAGE_MAX_BYTES`，默认 10MB（`max_bytes()`）。
- `append_usage(rec)`：写入前 `rotate_if_needed()` 检查超限 → 滚动 → append。
  - 滚动：`.3` 删 → `.2→.3` → `.1→.2` → 主→`.1`；新主文件由下次写入创建。
  - 保留最近 3 份归档 + 主文件 ≈ 40MB 上限。
- `iter_usage_files()`：主 + `.1/.2/.3` 统一遍历（存在才返回），统计工具合并用。
- **写入点统一走 `append_usage`**：`aa_backend._log_usage`（翻译）+ `vision_classify._log_usage_vision`（视觉）。
  禁其他 open() 直写（会绕过滚动）。
- **读取点**：任务管理器 `_usage_index` 只读主文件（滚动后主文件变小→(mtime,size) 指纹变→自动重建缓存，不崩）；
  `usage_total.py` / `usage_stats.py`（统计工具）用 `iter_usage_files` **合并读**，历史数据不丢。

## 坑

- `.gitignore` 要加 `usage.jsonl.[0-9]`（原只忽略 usage.jsonl，滚动文件会意外入库）。
- 手动滚动验证：`.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'scripts/common'); import aa_usage; aa_usage.rotate_if_needed()"`——
  注意这会**移动当前数据到 .1**，仅在无运行任务时手动做。
- 主文件 rename 走后主文件不存在 → `iter_usage_files` 只返回归档；下次写入重建。
- 滚动只发生在**写入时**（翻译/视觉调用），无任务时不动。

## 实测

- 真实滚动：12MB 超限 → 归档 `.1`（82256 行）→ 主文件重建空。
- `usage_stats.py` 合并读 `.1` 成功：translate 69076 调用、2.25 亿 token 全统计。
- `_usage_index` 滚动后（主文件不存在）返回空不崩。
