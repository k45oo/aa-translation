# chunks=0 全部漏译事故（2026-08-27，r401_jyusatsu）

## 现象
- r401_jyusatsu（入速出邸）免费流程翻译：21 话在 **15 分钟内"完成"**（异常快），但抽查汉化文件发现**大量日文台词残留未翻译**（511 行含假名台词行原文未动）
- 第一话也大量漏翻（但比其他话好，因 meta 生成成功）
- 进程随后卡死 10+ 小时（汉化/ckpt 时间戳停滞，但进程还活着）

## 根因（aa_translate.py `process_chapter` 楼主/用户分块逻辑）
```python
# 按楼主/用户分块(保持原顺序, 只分组不重排)
cur_list = None
cur_kind = None
for idx, l in normal_todo_orig:
    k = _op_of.get(idx, None)          # 作者归属: True=楼主/False=用户/None=未知
    if k != cur_kind:
        cur_kind = k
        cur_list = op_chunks if k else (usr_chunks if k is False else None)  # ← k=None → cur_list=None
        if cur_list is not None:
            cur_list.append([])
    if cur_list is not None:           # ← None 时行被静默丢弃
        cur_list[-1].append((idx, l))
```
- 该作品帖头格式（`4 名前： ◆iCktY1rcsc` 等）未被 `export_dialogue.is_op_header` 识别 → `_cur_op` 全程 None → 所有行 `k=None` → `cur_list=None` → **所有行被静默丢弃**
- 结果：`[entry_X] 3282 lines, 2270 flagged` 但 **`0 chunks of 50`** → 全部不翻译，原文直接落盘成"汉化"
- 附带：第一话 5162 有部分翻译是因为其 line_meta 生成成功（行数对齐），走 is_translatable 且... 实际是同一 bug，只是 5162 的部分 chunk 幸运走通

## 修复（commit c007df8）
```python
cur_list = None
cur_kind = None
first = True
for idx, l in normal_todo_orig:
    k = _op_of.get(idx, None)
    if first or k != cur_kind:          # first 处理首行 k==cur_kind==None 边界
        first = False
        cur_kind = k
        cur_list = op_chunks if k is not False else usr_chunks   # None 归入楼主块, 绝不丢行
        cur_list.append([])
    cur_list[-1].append((idx, l))       # 无 None 保护, 行必进某块
```
验证：5163 单话重跑 `0 chunks` → `46 chunks of 50`，翻译正常（`@3-79: +50/50` per chunk）。

## 两个高危信号（排查同类问题的第一线索）
1. **翻译异常快 = 没真翻译**：15 分钟"完成"21 话（每话几千行、几十 chunk，正常不可能）。看到完成速度离谱立即抽查汉化内容（源文件台词 vs 汉化是否相同）
2. **汉化/ckpt 时间戳停滞但进程活着 = 进程卡死**：可能卡在某 chunk 网络调用（超时/无限重试）。kill 重启（ckpt 断点续译无损）

## 排查路径
- 汉化文件 vs 源文件逐行对比（`TR==SRC 且含假名` = 未翻译台词）
- runner_log 看 `[entry_X] N lines, M flagged` + `N chunks of 50`——`0 chunks` 即此 bug
- line_meta 生成失败日志：`[M4a] meta 缺失, 自动生成` + `警告: 行数不齐 X vs txt Y,跳过` → fallback is_text_line（此时 flagged 仍正常，问题在分块）

## 相关：免费模式多模型并发（2026-08-27）
- `AA_FREE_WORKERS`（默认 3，可调 6）控制并发；`_call_free_chain` 轮询指针（`_free_round_robin_start`）让 worker 从不同免费模型起步，多模型（GLM/mimo/laguna/hy3/big-pickle/minimax，三平台限流独立）并行
- free 模式**不设 AA_BACKEND**（translate_runner/aa_translate 的 auto-backend 跳过，避免误导日志/意外回退付费）
