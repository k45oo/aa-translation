# 2026-08-18 代码改进去重重构（Phase 1-5）审核实测

> 本文件记录 2026-08-18 AA 翻译 scripts 代码改进（`scripts/代码改进8-18.md` 方案 + 执行）的**独立审核实测结论**。与 SKILL.md 主体互相补充：SKILL.md 的「去重定版」段记录了架构，本文件记录审核时发现的事实偏差与误判纠错。

## 执行结果（提交 f3b1372 → f5a2f0d，6 个提交）

| Phase | 内容 | 实测状态 |
|---|---|---|
| 1 | 去重（call_api/KNOWN_NAMES/manual_fix） | ✅ 真实生效：`aa_translate.py:584` 唯一 call_api；common/aa_common+aa_api 薄壳经 `_flat_loader` 单例转发；`A.call_api is T.call_api` 同对象 |
| 2 | pass/runner 统一 | ✅ cleanup/pass2+pass3、runner/saki+kaiki 均为薄封装转发 common/works 权威版；`P.OUT_DIR=` 注入废弃 |
| 3 | 路径 BASE 中心化 | ⚠️ **未达 DoD**：`works/paths.py` 建成（BASE = 3 级 dirname），registry 导入，仅 13 个脚本迁移；**实测仍有 71 个文件含 `r"<repo>` 字面量硬编码** |
| 4 | 稳定性 | ✅ proxy 单路径重试 + AA_USE_PROXY 强制；get_key 惰性缓存 + AA_KEY_RELOAD；ckpt 原子写 tmp+os.replace；subprocess.run |
| 5 | quality_gate --work registry id 映射 | ✅ 完成；9.2/9.3/9.4 评估不做 |

测试：103 个测试 102 绿 + 1 失败（test_gen_html saki_fc2 快照过期，已声明遗留）；**test_pipeline 2 个已知失败确实修复**（6 tests OK）——skill 记载的命名空间冲突 + mock 打不中已解决。

## ⚠️ 审核发现的事实偏差（勿重复踩）

1. **提交说明/追踪记录声称「生产脚本 0 残留硬编码」「实际只有 14 个」——实测 71 个**。含 `aa_translate.py` 自身（AA_DIR/OUT_DIR/WORK_DIR 22-24 行）、cleanup/ 18、works/ ~25、download/ 4、extract/ 6、sites/ 7、generate/ 4、fix/ 4、verify/ 3、translate/ 4、common/aa_hub 等。"仓库搬迁自动跟随"目标远未达成（只完成约 15%）。**审核不能采信提交自述统计，必须 grep 磁盘实测**。
   - grep 计数陷阱：bash 里 `grep -rl 'r"<repo>'` 单引号内 `\\` 处理不可靠，实测返回 0（假阴性）；**必须用 `grep -rlF 'r"<repo>'`（fgrep 固定字符串）** 才得到真实 71。
2. **`works/kaiki_runner.py` 的 `P.main("baitou")` 不是 bug**：该文件实为**白头回归篇后置流程**（KA_AA=白头与染灰魔女目录），registry 无 `kaiki` id，baitou 正确。审核前期把它当"复制粘贴遗留 bug"是误判——**文件名 kaiki ≠ 处理怪奇作品**，看文件内 KA_AA/KA_TR 常量才是真相。

## mock 语义（2026-08-18 起）

薄壳化后 `A.call_api = fake` 只改薄壳命名空间，translate_lines（转发 FLAT_MOD 函数）内部仍走 FLAT_MOD.call_api → **必须 `A.FLAT_MOD.call_api = fake`**。test_pipeline 已按此修复。

## 遗留（后续批次建议）

- Phase 3 剩余 71 文件硬编码路径迁移（按目录分批 + 每批独立提交 + py_compile + CR 双校验）
- test_gen_html saki_fc2 快照过期（更新期望或接受）
- cleanup/pass3_parallel.py 保留为实验基准（experiments/exp_pass3_compare.py 依赖）
