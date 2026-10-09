# 遗留 bug 清单（2026-08-27 全量测试基线盘点）

> 背景：2026-08-27 宽度校验统一（c999821）前后各跑了一次 `python -m unittest discover -s scripts/tests`，
> 用 git stash 对照确认以下失败**全部是遗留问题，与本次改动无关**。列出供后续批次修复。
>
> 复核（2026-08-27 作品知识库实施后，54eb20b）：同一批 9 个失败在实施前基线（29332af，171 用例）上
> 完全相同——知识库实施 0 回归，新增 19 用例全绿，全套 190 用例。复核手法改用
> `git worktree add <tmp> <pre-impl-commit>` 跑基线（不动工作区，比 stash 安全）。
> **注意：AGENTS.md 的「154 用例全绿」口径已过时**（基线就有 9 红，用例总数也已增长）。

## 1. fix_width_mixed_lines NameError（真 bug，最优先）

- **位置**：`scripts/aa_translate.py` `fix_width_mixed_lines()`（~L1204）
- **症状**：`text = call_api(..., work_r18=work_r18, low_r18=low_r18)` 引用了未定义的局部变量
  → 触发即 `NameError: name 'work_r18' is not defined`
- **存在版本**：51e29f0（2026-08-27 前最后一次提交）就已如此；该函数 2026-08-11 加入时大概率为
  call_api 加 work_r18 参数的批次漏改签名——**从未被调用过**（修宽度走 fix_batch 主路径）所以没炸
- **影响面**：一旦有人接入 mixed 段级宽度修复就会崩；kikkoman 路径（fix_width_kikkoman.py 改造版）
  是唯一预期调用方
- **修法**：给 `fix_width_mixed_lines(rows_by_file, work_r18=False, low_r18=False)` 加参数并在
  调用方传作品 r18 属性；或降级为 `call_api(FIX_MIXED_SYSTEM, body, max_tokens=16384)`
- **测试**：scripts/tests/test_mixed_segments.py TestFixWidthMixedLines 2 用例触发

## 2. test_aaweb.test_reject_wide_line 期望过期（测试期望未同步）

- **症状**：save_line 第 1 行 src=`あいう`(6格) tr 新值 `abcdefgh`(8格) 断言 error，实际 ok
- **根因**：2026-08-25 宽度校验行类型区分（line_kind/allow_overwidth）后 text 行允许超宽，
  测试样本「abc」源行判定为 text → 不再拒绝。工作台行为本身是**正确的**（用户决策）
- **修法**：改测试样本为 art 行（如 `＿＿＿＿＿＿＿＿＿＿` 纯图形）断言超宽仍 422 拒绝；
  再补一条 text 行超宽放行的正向断言

## 3. 其余（chapter_prep×3 / gen_html 快照 / line_meta / pipeline ckpt / fix_width mock ×2）

- 全部在 stash 基线（8f370ef 干净工作区）同样失败——多为黄金样本/交付物被后续翻译更新、
  或 mock 数据构造问题。修复前先判断「失败是特性」还是真回归：黄金样本改了就更新期望。
- 另：全量跑总出现若干 ERROR/FAIL 计数随 discover 顺序波动（同名模块 aa_translate 平铺/common
  两套的命名空间冲突，技能 aa-pipeline-optimization「通用坑」已有记录），单跑具体用例往往通过。

## 处理建议

按 #1 → #2 顺序单独开小批次修（各自 commit + 单独验证），不要混进功能性改动。
