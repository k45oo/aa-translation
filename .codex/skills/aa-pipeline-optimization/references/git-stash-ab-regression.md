# 回归失败归因：git stash A/B 对比法（2026-08-27 实战）

## 场景

改造脚本后全量测试出现失败，需要判断「失败是本次改动引入」还是「历史遗留」——不能凭感觉，要用 A/B 实证。

## 方法（零成本，~1 分钟）

```bash
# 1. 记下改动前测试失败列表（baseline）
.venv/Scripts/python.exe -m unittest discover -s scripts/tests 2>&1 | grep -E "^(FAIL|ERROR):" | sort > /tmp/before.txt

# 2. 暂存工作区改动（回到 HEAD）
git stash

# 3. 在干净 HEAD 上跑同样测试
.venv/Scripts/python.exe -m unittest discover -s scripts/tests 2>&1 | grep -E "^(FAIL|ERROR):" | sort > /tmp/after.txt

# 4. 恢复改动
git stash pop

# 5. 对比：before == after → 失败全是历史遗留，与本次改动无关
diff /tmp/before.txt /tmp/after.txt
```

## 判定规则

- **diff 为空** = 零新增失败 → 可放心提交，并在 commit message / 汇报中注明「全量 N 用例，失败 M 个与改动前完全一致（历史遗留）」
- **diff 非空** = 本次改动引入新失败 → 逐个排查（通常是 mock 目标变了 / 签名改了 / 缓存键结构变了）

## 本项目实例（2026-08-27 作品知识库实施）

改造 aa_translate.py / proofread.py / work_memory.py 后全量 190 用例失败 9 个（test_mixed_segments 2 ERROR + test_aaweb/test_chapter_prep/test_gen_html/test_line_meta/test_pipeline 7 FAIL）。
stash A/B 对比确认这 9 个在改动前同样失败（历史遗留：test_aaweb 期望过期、test_chapter_prep R18 阈值变更后未更新期望、test_gen_html 快照过期等）→ 零新增失败 → 正常提交。

## 注意

- `git stash` 只暂存**已跟踪**文件的改动；新增未跟踪文件（如新测试文件）不受影响，跑测试时新文件仍会被 discover 到——若新文件本身引入失败，需手动排除或先移走
- 该方法对「验证自己的改动没破坏别人」的审阅场景同样适用（审阅卡独立复跑用同一技巧）
- 历史遗留失败的清单已沉淀在 `aa-pipeline-optimization/references/legacy-bugs-2026-08-27.md`

## 跨分支基线对比：git worktree 法（2026-08-30 P1-P6 重构实战）

stash A/B 法只能对比**当前分支 HEAD**。要对比「更早的基线 commit」（如方案文档 commit，证明偶发失败在改动前就存在），用 **git worktree 在独立目录检出基线**，绝不污染主工作区：

```bash
# 1. worktree 检出基线（用 Windows 真实路径！MSYS /tmp 会 add 失败）
git worktree add "<user>/AppData/Local/Temp/baseline-check" 5dc7dec4

# 2. 在基线目录跑测试（注意 venv 用主仓库的绝对路径）
cd "<user>/AppData/Local/Temp/baseline-check" && \
  <repo>/.venv/Scripts/python.exe -m unittest discover -s scripts/tests 2>&1 | grep -E "^(Ran|OK|FAILED)|FAIL:"

# 3. 清理
cd <repo> && git worktree remove "<user>/AppData/Local/Temp/baseline-check" --force && git worktree prune
```

**判定**：基线同样失败（同一批用例）→ 本次改动零新增失败，可提交。本实战：基线 3/3 次 test_task_manager 全失败（用例在 test_no_env_passthrough_default / test_env_passthrough_to_runner / test_tick_starts_head_and_finalizes 之间漂移=env 泄漏/时序竞态），与本分支的偶发失败完全同批 → 证明与 P1-P6 改动无关。

## ❌ 禁忌：`git checkout <旧commit> -- scripts/` 对比基线会污染主工作区

**绝对不要**用 `git checkout 5dc7dec4 -- scripts/` 把旧版检出进主工作区跑对比——旧文件以未提交状态覆盖工作区，且旧版缺新测试/新生成器（test_interface_docs 必挂），对比结果完全失真；更糟的是旧版文件滞留工作区、`git status` 一片混乱，最后只能 `git reset --hard HEAD` 恢复。基线对比**只用 worktree**。

## 偶发失败三连跑确认法

同一测试文件单独跑会 OK、全量跑偶发 1 个失败、失败用例还在漂移 → 是**测试间 env 泄漏/时序竞态**（既有缺陷），不是本次改动。确认三步：
1. 单独跑目标测试文件 3 次——若偶发失败，记下失败用例名
2. 该用例单跑 1 次——OK（证明是测试间干扰不是用例本身逻辑坏）
3. worktree 检出基线跑同样文件 3 次——同样失败 → 既有缺陷坐实，不阻塞交付，记录为遗留技术债即可（本次是 test_task_manager.py 的 TestUsage/TestScheduler env 泄漏，修它属另一任务，超出本方案范围）

**坑**：偶发测试失败不要在交付收尾时反复重跑全量刷"全绿"——先做基线对比定性，再决定是否阻塞。
