# 代码改进方案审核方法论（2026-08-18 实战）

用户给出一份改进计划文档（如 `scripts/代码改进8-18.md`）要求「只审核/只负责审核」时：**先逐项与磁盘对照核验计划的事实主张，再给结论**，别照单接受计划里的现状描述（本会话计划文档 5 项核心主张有 3 处与代码现状不符）。

## 核验手段（全部零 API）

- **重复实现定位**：`grep -n "def call_api" aa_translate.py common/*.py`、`grep -n "def load_manual_fixes\|def record_manual_fix"` 找双份，`sed -n` 区间导出函数体后 `diff` 对比
- **白名单/集合差异**：`python -c` 导入两模块打印 `len()` 与集合差集——**差集 0 才是真一致**（2026-08-18 计划声称 KNOWN_NAMES「内容不一致」，实测 653=653 差集 0，主张不实）
- **硬编码路径统计**：`grep -rlF "<repo>" scripts --include="*.py" | grep -v __pycache__ | grep -v legacy | wc -l`（必须 `-F` 精确匹配，普通 grep 会因反斜杠正则语义漏报；实测 109 文件 vs 计划声称 150+）
- **入口/参数行为**：**读 main() 源码而非跑 `--help`**——`common/pass2.py`/`pass3.py` 的 main 把非 `-` 开头参数当作品 id 传 `registry.get()`，`--help` 会直接 KeyError 或误跑（plan 文档里的 `--help` 验证命令全部不可用，验证改 `python -c "import common.pass2; common.pass2.main('saki')"`）

## 审核发现的固定模式（写进每份改进计划的风险清单）

1. **合并方向必须符合 2026-08-11 既定架构：平铺版 `aa_translate.py` = 唯一实现，`common/aa_translate.py` = 薄壳 re-export**。任何「让平铺版 `from common import ...`」的方向都是反的（破坏薄壳 re-export 链 + 依赖环风险）。
2. **pass 脚本改造前先查 runner 注入**：`grep -n "P.OUT_DIR\|import pass" works/*_runner.py runner/*.py`——6 个 runner 都 `import pass3 as P` + 注入 `P.OUT_DIR/WORK_DIR/REVIEW_FILE`，而 common 版（registry 取参）**无这些模块属性**，不先同步改 runner 会 AttributeError。
3. **cleanup/pass3.py 内部委托 `pass3_parallel.py`**（白头硬编码、无 `--work`）——改薄封装前先确认委托目标归属，别只改壳不换靶。
4. **默认参数别在统一时静默改值**：平铺 call_api 默认 `timeout=400`，aa_backend 各后端是 `timeout=120`——合并进 dispatch 必须保留生产默认值，否则大批量翻译提前超时。
5. **usage 落盘格式两份不同**：平铺 `{tag,ts,prompt,completion,total}` vs common `{tag,prompt,completion,total,ms}`（无 ts）——去重时顺手统一（metrics_report 虽兼容两种，`--meta` 时序分析靠 ts）。
6. **works/kaiki_runner.py:31 `P.main("baitou")` 疑似复制粘贴 bug**（怪奇作品 id 应为 `kaiki`，同文件 saki_runner 传的是 `"saki"`）——改 runner 时核实。
7. **双真相源字段（2026-08-27 知识库方案审核发现）**：方案里同一字段出现在两处存储（如 `关联度` 既在 work_meta.json 又在 _knowledge.json 示例里）= 必漂移，审批时必须砍到单一来源（本项目惯例：用户手编元数据归 `aa_web_app/data/work_meta.json`，与 r18_level 同源）。
8. **方案审核的核实锚点**：方案里引用的函数签名/缓存键/接口名要逐个 `grep` 核对（本次核对 `_filter_glossary_pairs` 缓存键、work_memory 接口、现存 _memory.json 实例内容均与方案一致才放行）；方案说「唯一实例」时用 `search_files *_memory.json` 实测验证。

## 审核交付物规范（用户说「只负责审核」时的固定动作：审核 + 文档 + git 提交，绝不动代码）

- 新建 `doc/代码改进记录_<日期>.md`：审核结论表（已核实事实 / 事实性错误 / 被低估风险 / 建议）+ 执行进度表 + 阶段记录模板（每阶段完成后追加，方便追踪）
- 追加 `doc/同步记录.md`（沿用既有 `## 日期 主题` 格式）
- 更新 `doc/索引.md` 活跃文档表
- `git commit` **只提交文档**——工作区其他未提交代码改动（常与本任务无关）一律不碰，用 `git add <具体文件>` 精确暂存而非 `git add -A`；commit message 注明「只审核/文档」
