# 实施审核方法论（方案落地后核对实现，2026-08-27 作品知识库实战）

与 `plan-review-methodology.md`（方案落**地前**审核）互补：本文是方案实施完成、用户说「方案已全部落地，审核实施情况」时的核对法。**核心立场：commit message 是声明不是证据——逐条对着 diff 和磁盘核实。**

## 固定步骤

1. **圈定实施范围**：`git log --oneline` 找实施提交串，逐个 `git show --stat` + 读关键 diff。
2. **审批意见核销**：把方案/审批意见列成表，逐项标 ✅/❌（本次 10 条意见逐条对到代码行号）。
3. **测试基线对比（判回归的唯一可靠法）**：
   ```bash
   git worktree add "$LOCALAPPDATA/Temp/aat_pre" <实施前commit>
   cd "$LOCALAPPDATA/Temp/aat_pre" && <venv python> -m unittest discover -s scripts/tests
   # 与当前工作区失败集合逐一比对；完事 git worktree remove --force
   ```
   worktree 优于 stash（不动工作区/不怕 stash 冲突）。失败集合完全相同 = 0 回归（本次：基线 171 用例 9 红，实施後 190 用例同 9 红 +19 新绿）。
4. **用户手编数据文件过格式化提交的存活验证**：大 diff（如 work_meta.json 1082 行变更）可能只是重排版。解析对比而非行对比：
   ```python
   old=json.loads(subprocess.run(['git','show','<c>^:<path>'],capture_output=True).stdout.decode('utf-8'))
   new=json.load(open(path,encoding='utf-8'))
   # 逐 key 逐 field 比对，忽略有意新增字段
   ```
5. **迁移类提交核实**：备份文件确实在 backup/（`ls`）、旧文件确实删除、新文件内容与原文件逐块一致。

## 审核高频 bug 模式（本次 6 个发现全是这几类，审核时按此清单扫）

1. **死区数据（写入无读取）**：候选合并进 knowledge `待确认` 区，但 hint 构造函数不读待确认、且无 promote 工具 → 人工确认过的术语永不注入。**审计手法：对每个新写入点，grep 出所有读取方，确认链路闭合**（写→读→生效）。
2. **early-return 吞掉新追加的逻辑**：在旧函数尾部追加注入时，上方已有 `if not pairs: return ""` → 过滤为空时新功能（章节简介/知识库）整体静默丢失。**给函数加新阶段时，先检查它上面所有提前 return**。
3. **门控缺失/过度**：方案说 anthology 才按章过滤，实现是「传 base 就按章」（serial 也被按章）。偏离批准方案必须二选一：改代码加门控，或**更新方案文档追认**（本次按章过滤对 serial 语义上更优，走文档追认）。
4. **多行输出截首行**：prompt 要求 1-3 行，代码 `txt.split("\n")[0]` 只留第一行——试跑样本恰好单行没暴露。
5. **AI 生成内容无既有资产对照**：章节简介让模型自拟角色名，不注入 glossary/知识库已有译名 → 简介译名与标准译名漂移风险。
6. **commit message 与代码不符**：message 称「修掉只取前20对不按语料过滤」，diff 里 `pairs[:20]` 原样保留。**message 里的「修复」声明必须逐条在 diff 里找到对应删除/改动行**。

## 审核报告形态（用户接受度已验证）

- ✅吻合表（意见→代码行号证据）| ❌问题按严重度排序（工作流缺口 > 功能 bug > 偏差 > 信号问题）| 建议分「立即修 / 改文档追认 / 顺手修 / 另案」| 另案条目单独列出（如 AGENTS.md 过时口径）
- 结论先行（批准/有条件批准），问题给修法建议但不代劳（用户会决定哪些进下一批）
