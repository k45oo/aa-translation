# scripts/ — AA 翻译脚本说明

> **本文档是 scripts/ 结构的唯一权威说明**。任何结构变动（新增/移动/删除脚本、改目录）**必须同步更新本文档 + AGENTS.md + skill（aa-pipeline-optimization）**，否则视为不完整变更。
> 上次结构整理：2026-10-10（交付物统一 `aaManga/交付HTML/`；`cleanup/` 转发层删除——运行链早已无引用）。
> 更早：2026-08-30（P1-P6 代码优化：废弃 92 脚本进 legacy/、删除 5 空目录、接口文档自动生成）。

## 1. 目录结构总览

```
scripts/
├── aa_translate.py      ★ 核心共享模块（平铺版统一实现，所有管线脚本依赖，必须留在顶层）
├── config/              统一配置（model_config.toml 模型/网关；work_wikis.json 每部作品的官方设定 wiki）
├── common/              通用管线 --work 参数化（权威实现层）
├── sites/               站点解析器（_base + 各站）
├── works/               作品注册表 + 站点特例（registry / paths / dl_touhou / touhou_runner 等）
├── runner/              dl_runner / translate_runner / refine_runner（推荐入口层）
├── tools/               独立 CLI 工具（aa_web / line_classify* / vision_* / usage_* / gen_inventory / gen_interface_docs 等）+ **发行包打包**（build_release / build_workbench_exe / aa_web_exe / release_common）
├── fix/                 定向修复 + 校对链（refine_aa / refix_mixed_segs / fix_mixed_drift 等，5）
├── verify/              质量闸门 quality_gate / metrics_report / reader_win_ab（3）
├── benchmark/           模型评估基准（eval_translation.py；report/ 评测结果为生成物，不入线上）
├── tests/               单元测试（72 个 test_*.py / 1174 用例全绿）
├── legacy/              历史归档（108 个，自包含只读，勿再使用；**仅本地，线上仓库不含**）
└── logs/                批次日志（*.log 历史；**仅本地，线上仓库不含**）
```

**2026-08-30 结构重整要点**：
- `download/` `extract/` `translate/` `generate/` `experiments/` 五目录**已删除**（内容全部 git mv 进 legacy/，legacy/ 仅本地）
- `cleanup/` **已删除**（`pass2`/`pass3` 的薄转发层，2026-09-08 起运行链无引用；权威实现在 `common/pass2.py`、`common/pass3.py`）
- `fix/` `verify/` 保留权威工具（quality_gate / metrics_report / refine_aa / refix_mixed_segs / backfill_aa_comments）
- 废弃判定权威：`scripts/_inventory.json`（`scripts/tools/gen_inventory.py` 生成，AST 静态盘点）
- 接口文档：`doc/接口文档.md`（`scripts/tools/gen_interface_docs.py` 自动生成，勿手改）

## 1.5 发行包（开箱即用，2026-10-09）

`python scripts/tools/build_release.py` 一键产出 `导出/AA翻译_发行包_<日期>.zip`
（**也可以用仓库根 `一键打包发行包.bat`**：等于 `--out 导出`；产物目录里若跑着工作台实例（活数据根）
会被拒绝覆盖 → bat 自动改用 `导出_新\` 重跑，参数原样透传）
（干净源码 + 冻结工作台 exe + 示例作品 3 话 + 宝可梦 wiki 翻译素材 + 独立配置）。
方案 / 十项决策 / 实测 / 踩坑：`doc/方案归档/发行包打包方案_2026-10-09.md`。

- `tools/release_common.py` —— 脱敏 / 痕迹复查 / 密钥复查 / `compile_all` / 白名单拷贝（与交付页编辑服务 exe 共用）
- `tools/build_workbench_exe.py` —— 冻结单文件 exe（`AA工作台.exe`）
- `tools/aa_web_exe.py` —— exe 入口：**代码根 = PyInstaller 解包目录，数据根 = exe 所在目录**
- `tests/_work_guard.py` —— 测试守卫：`work_cfg`（缺作品数据 → skip）、`repo_only`（发行包内跳过生成者专属用例）

**随包数据**：`glossary.json`（作品/角色/替换对/`known_names` 白名单）+ `roles.json` 给**真表**（使用者开箱即用）；`work_meta.json` 只留示例作品；`IMAGE_MANGA` 图片漫画注册表**清空**（表里是生成者本机图片目录），其余作品数据不进包。

**三条铁律（都踩过）**：

1. **exe 必须用发行包那棵 staging 打**（`build_workbench_exe.py --stage <staging>`），顺序 =
   变换 → 脱敏 → 硬门禁 → 打 exe → 压包 → 解包冒烟。否则包里裁过的 `registry.py`/`work_meta.json`
   与 exe 里冻结的那份不一致（曾出现"包内 1 部作品、exe 首页 45 部"）。
2. **`scripts/tests/` 不脱敏**（`release_common.NO_DESENS_DIRS`）：测试夹具里的绝对路径是**伪造**的
   （`r"C:\tmp\f.ttf"`），被替换成 `<repo>` 断言即废；但仍过"放宽版"痕迹扫描（个人信息照拦）。
3. **冻结 exe 起子任务走 `aa_spawn.py` 的 `py_prefix()`**（`[exe, "--cli", 脚本]` + `child_env()`）；
   变换读写用 `_read_lines`/`_write_lines`（工作区文件可能整份 CRLF，精确匹配 `"}"` 会静默失配）。
7. **包内自检要消毒环境**：跑解包单测时去掉 `AA_ENV`/`AA_ENV_PATH`/`*_API_KEY`（操作者 shell 里的
   这些会盖掉用例夹具：`AA_ENV` 优先级高于 `AA_ENV_PATH`，真环境变量优先于 `.env` → key 类用例假失败，
   实测 4 红 + 7 错）；同时用例本身要自洽（清 `AA_ENV` + 同名 key）。自检验的是**这个包**。
6. **产物目录只能挪开，绝不 `rmtree`**：用户会把 exe 放在 `导出/<包>/` 里跑（数据根 = 包目录），
   而"活数据根守卫"实测漏过一次（把整棵数据根换成干净包，丢了用户的翻译产物）→ 现在
   `_set_aside_out_dir()` 一律 `os.replace` 成 `<名>.old_<时间戳>`（保留最近 2 份），
   物理上不可能再删掉数据。用例 `tests/test_release_pack.py`。
4. **exe 依赖自动齐 + 数据可持久**：`build_workbench_exe` 问 staging 的 `check_imports.scan()` 要依赖清单；
   `aa_web_exe._bootstrap_registry()` 把 `registry.py` 落到数据根（否则「＋ 添加作品」写进临时解包目录，重启即丢）。
   **引导必须排在任何分支之前**（含 `--cli`），否则子任务只认包内示例作品 →「未知作品: X（可用: baitou）」；
   自检工具 `tools/check_registry.py`（打印实际命中的 registry 路径/作品清单），构建探测据此断言子进程用数据根那份。
   ⚠️ **子任务跑的是另一份脚本**：工作台用 `os.path.join(_ROOT, "scripts", ...)`（冻结态 = 解包目录）起子任务，
   那种脚本用 `__file__` 反推 `_BASE` 后会 `insert(0, <解包目录>/scripts/works)` **盖掉**数据根注册表 ——
   手工 `--cli scripts/...`（相对）命中的是数据根那份，所以"手工全绿、点按钮全红"。两条修法：
   `_resolve_cli_script()` 把解包目录的绝对路径换成数据根那份 + 引导后**删掉解包目录那份 registry**。
5. **依赖要收到"第三层"**：`--hidden-import`(顶层) → `--collect-submodules`+`--collect-data`(子模块/数据)
   → `DATA_PKGS`(**按路径读文件**的包整包当 data 复制、不进 PYZ，如 janome 的 `sysdic/entries_*.py` 靠 mmap 读)。
   漏哪层都在跑到那条路时才炸（实测三次：`janome` / `janome.tokenizer` / `_MEI*/janome/sysdic/entries_compact0.py`）。
   `check_imports` 连**点分子模块**一起验（只验顶层会放过第二层）；`SUBMODULE_DENY`/`DATA_DENY` 排除
   官方 hook 已覆盖的大块（PIL 二进制 13MB、fontTools 内嵌 .c 6MB）。真机验收走零 token 的
   `exe --cli scripts/common/build_term_pool.py --aa <目录>`（真跑分词器出候选）。

## 2. 核心规则（改结构前必读）

1. **`scripts/aa_translate.py` 必须留在 `scripts/` 顶层** —— 它现在是唯一实现（2026-08-18 Phase 1 反向合并），`scripts/common/aa_translate.py`、`aa_api.py`、`aa_common.py` 都是薄壳，经 `common/_flat_loader.py` 单例加载器转发到平铺版（保证 `A.call_api is T.call_api` 同一对象；mock 用 `A.FLAT_MOD.call_api = fake`）。两条入口（`python scripts/aa_translate.py` / `python scripts/common/aa_translate.py --work <id>`）必须始终走同一份代码。
2. **子目录脚本如何找到 aa_translate**：靠 `sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))`（向上两级到 scripts/ 根）。**新脚本必须保持此模式**。
   - 新版通用脚本（common/、works/）通常额外 insert `scripts/common` 与 `scripts/works`，通过 `registry.get(id)` 拿目录，而不是改脚本顶部常量。
   - ROOT 型脚本（`verify/quality_gate.py`、`verify/metrics_report.py`、`tools/vision_*.py` 等）：ROOT/BASE = 向上三级到仓库根（因为脚本在 scripts/子目录）。
   - **仓库根路径唯一来源 = `works/paths.py` 的 `BASE`**（动态 `__file__` 计算，2026-08-18 Phase 3）。新脚本不要硬编码 `<repo>`。
3. **推荐走 registry 通用管线**：新作品/新站点的下载、提取、翻译、清理、校验、生成都优先使用 `common/` 下 `--work <id>` 参数化脚本（或 runner 编排），旧平铺脚本只服务历史作品。
4. **运行方式**：从仓库根 `python scripts/<子目录>/<名>.py`（aa_translate.py 例外：`python scripts/aa_translate.py`）。
5. **模型配置统一化（2026-08-19）**：模型/网关/key/r18 全部在 `config/model_config.toml` 统一管理，加载器 `common/aa_config.py`（tomllib 标准库）。**改模型/网关/后端=改 TOML，不碰代码**。优先级：环境变量（AA_*）> TOML > 代码默认。
6. **接口文档同步（2026-08-30，P6）**：改 CLI 参数 / env 读取后必须重跑 `python scripts/tools/gen_interface_docs.py` 并同 commit（`tests/test_interface_docs.py` 会校验一致）。新增/删除脚本后必须重跑 `gen_inventory.py`（生成器自身也会校验 inventory 与文件树一致）。

## 3. 各目录脚本明细

### common/ — 通用管线（权威实现层，--work 参数化）

| 脚本 | 用途 |
|---|---|
| `aa_translate.py` | ★ 统一翻译入口薄壳：importlib 加载平铺版 `scripts/aa_translate.py` 并全量转发 |
| `aa_api.py` | ★ API 层薄壳：经 `_flat_loader` 转发平铺版 call_api/parse_result/SYSTEM/translate_lines |
| `aa_config.py` | ★ 统一模型配置加载器：tomllib 读 `config/model_config.toml`；env(AA_*)>TOML>默认 |
| `aa_backend.py` | ★ 多后端分发 + call_with_fallback 自动降级 |
| `aa_common.py` | ★ 共享基础薄壳：经 `_flat_loader` 转发平铺版（disp_width/kana_runs/residual_*/manual_fix 等） |
| `_flat_loader.py` | ★ 平铺版单例加载器：sys.modules['aa_translate'] 复用/防薄壳自我循环 |
| `r401_index.py` | rss.r401.net 作品/章节发现 |
| `yaruobook_index.py` | やる夫スレ本棚三站目录解析（--site com\|jp\|net） |
| `check_update.py` | 白头与染灰魔女 更新检测（供 cron） |
| `dl_chapters.py` | 批量下载章节页（`--work <id>` 或 `--json chapters.json`） |
| `extract_all.py` | 批量提取：raw/*.html → aa/*.txt + colormap |
| `extract_dialogue.py` | ★ 台词提取单一口径（v3 meta 段级，供 proofread/简介/术语/精译） |
| `gen_aa_html.py` | ★ 通用**交付文件夹**生成器（`index.html` + 源 `txt.gz` ＋ 章节数据**按体量自动选形态**：≤20MB 全内嵌 index.html（**单档**，双击/发出去/手机都能看）、超出才 `data/*.js` 外置懒加载；数据格式自动选 v1 两份 / **v2 = src 全量 + tr 相对 src 的差异行**（AA 里艺术行/帖头/空行不翻译 → 九成行两份一样，实测省 31~42%）＋**交付页编辑服务**四件（`AA编辑服务.exe`/`编辑服务.bat`/`手机阅读(局域网).bat`/`edit_meta.json`）；render_line 唯一实现、译/原切换与阅读进度、PC 端点行号改译文＋翻译替换（工作台式行内编辑行）、无服务时按钮置灰、手机端目录抽屉 100% 高、读不到 `data/` 时按打开方式给准确指引） |
| `gen_chapter_summaries.py` | 章节简介生成（translate_runner 翻译前自动跑，必须中文；注入作品术语表译名对照；每章增量落盘+心跳+可停） |
| `gen_glossary.py` | 生成项目总术语表 glossary.json |
| `gen_glossary_ai.py` | 术语表 AI 预提取（新作开工前） |
| `iter_glossary.py` | 迭代术语更新（--merge 合入 glossary.json） |
| `chapter_prep.py` | 章节预处理（零 API）：术语统计 + 章节级 R18 判定 |
| `scan_width.py` | ★ 宽度校验唯一口径（collect_mismatch） |
| `analyze_mismatch.py` | 错位行字符级 diff 分类 |
| `fix_width.py` | 宽度错位重译修复（--work） |
| `pass2.py` | 通用漏译清理（--work） |
| `pass3.py` | 白名单残留清零（--work） |
| `scan_role_residual.py` | 角色名残留扫描（零 token） |
| `task_report.py` | 任务管理器协议（心跳/停止标志/finalize） |

### runner/ — 推荐入口层

| 脚本 | 用途 |
|---|---|
| `dl_runner.py` | ★ 通用下载 runner：`--work <id> [--chapters eids\|1-20\|all\|new]` |
| `translate_runner.py` | ★ 通用翻译 runner：`--work <id> [--chapters ...] [--force]`（不再补下载，缺章先跑 dl_runner；**不生成合集**——合集一律手动 `gen_aa_html.py` 或工作台按钮） |
| `refine_runner.py` | ★ 精译环节（读 `_汉化.txt` + 台词表，输出 `精译/`） |
| `_log.py` | runner 日志工具 |

> 历史作品专用 runner（saki/kaiki）已归档 legacy/；touhou 批量用 `works/touhou_runner.py`。

### sites/ — 站点解析器（按站多态，勿整合）

| 脚本 | 用途 |
|---|---|
| `_base.py` | 公共底座：clean / build_txt / 帖头样式常量 |
| `yaruonichijou.py` / `bookshelf.py` / `fc2.py` / `blog_2nt.py` | 各站解析器 |
| `yaruobook.py` / `yaruomatomex.py` / `yaruocheck.py` / `yaruohiroba.py` | 各站解析器 |
| `manyoshu.py` / `matomemo3.py` / `yarumo_fc2.py` / `yaruo_matome.py` | 各站解析器 |
| `touhouyaruosure.py` | 第七站点（API 注入正文） |
| `aamatome_2nt.py` / `himanatokiniyaruo.py` / `yomuaa.py` | 各站解析器 |

> `parse_posts`×21 / `entry_url`×16 / `render_header`×16 是站点接口契约多态，**不整合**（方案 P4 明确）。

### works/ — 作品注册表 + 站点特例

| 脚本 | 用途 |
|---|---|
| `registry.py` | ★ 作品注册表（当前 8+ 作品条目：baitou / saki / saki_fc2 / fukou / yaruobook / touhou_teisou / kikkoman / akari_* 等） |
| `paths.py` | ★ 仓库根 BASE 动态计算（全仓唯一来源） |
| `dl_touhou.py` | touhou 特殊下载（二次请求 API 注入正文） |
| `touhou_runner.py` | touhou 全自动编排（等批次 → pass2/3 → scan/fix_width；**不再生成合集**） |
| `fix_touhou_width.py` | touhou 奇数格超宽修复（删 U+3000 + 标点半角化） |
| `fix_comments_yaruobook.py` / `translate_comments_yaruobook.py` | yaruobook 评论区工具 |

### tools/ — 独立 CLI 工具

| 脚本 | 用途 |
|---|---|
| `aa_web.py` | ★ 矫正工作台启动器（http://127.0.0.1:8765） |
| `edit_server.py` / `build_edit_server.py` | ★ 交付页**编辑服务**（PC 端点行号改行/翻译替换写回源 txt；无服务时页面按钮置灰；随交付夹分发，别的机器不用装 Python 也能改）/ 把它打包成单文件 exe。**`--lan` = 手机阅读**：绑 0.0.0.0，手机连同一 Wi-Fi 打开打印出来的 `http://<局域网IP>:8770/index.html`，远端(手机)只读 |
| `aa_to_images.py` / `aa_to_cbz.py` | 汉化 txt → 竖屏长图 / CBZ 漫画包 |
| `line_classify.py` / `line_classify_v2.py` | 行分类器 v1 / v2（meta 生成，零 API） |
| `vision_annotate.py` / `vision_classify.py` / `vision_review.py` | 视觉辅助分类 |
| `sync_codex_skills.py` | ★ `.codex/skills/` 技能副本重建：从技能库生成 Codex 兼容格式（frontmatter 只留 name/description）+ 本机信息占位符化 + 残留门禁（有残留拒出包）；`--check` 只校验。`.codex/skills/README.md` 说明生成规则 |
| `push_code_mirror.py` | ★ **代码同步推送**：按白名单构建（代码/文档，无数据/无 legacy/无构建产物）→ 本机信息占位符化 → 门禁（残留路径/密钥形状/体积上限/`.py` 语法，命中 rc=2 拒推）→ 推送远端仓库（默认 `origin`；GitHub 用 `--remote <url>`）。**默认增量**：只重放镜像还没有的提交（底座位 `temp/mirror_state.json`，丢了用说明+时间对号 + 树校验；认不出回退全量重建），门禁只对新提交跑、fast-forward 推送；`--full` 强制全量重建历史、`--history N/0` 定重建条数或回单提交快照；`--check` 只校验并与远端比对、`--dry-run` 提交不推 |
| ~~`proofread.py`~~ | 误译复审（**已废弃**，词级替换顶歪 art 段事故 → 已入 `legacy/`；漏译清理统一走 `fix/refine_aa.py`） |
| `gen_inventory.py` | ★ P1 盘点生成器（scripts/_inventory.json，AST 静态） |
| `gen_interface_docs.py` | ★ P6 接口文档生成器（doc/接口文档.md，AST 静态） |
| `freeze_delivery_golden.py` | 交付页正文**哈希基线**重建（`scripts/tests/fixtures/delivery_golden.json`；渲染口径变更时先确认再跑，`--check` 只比对） |
| `run_tests.py` | ★ **跑用例统一入口**（2026-10-12）：默认**全量并行**（`-j min(4,CPU)`，按模块分工；1174 例 200s → **80~110s**，视机器负载）；`--changed [路径…]` 用 AST 反查「改动的文件 → 哪些用例 import 了它」只跑相关模块（映射不出 = 新 CLI/站点解析器 → **回退全量**，不静默少跑；恒带闸门 `test_interface_docs`），`--list` 只看映射、`-j 1` 串行看 traceback。跑真调度器/按时间轮询中间状态的 `test_task_manager`/`test_schedule_task` 固定走**串行段**（并行占满时会假失败）；发车顺序按实测 `WEIGHT`（LPT） |
| `translate_titles.py` / `usage_stats.py` / `usage_total.py` | 标题翻译 / usage 聚合 / token 合并统计（`aahub_stats.py` 已随 AAhub 素材库删除，2026-10-10 入 legacy） |
| `r18_judge_ch.py` / `r18_vision_pilot.py` | R18 章节判定 / 视觉辅助试点（`r18_zhipu_probe.py`、`guard_rejudge.py` 随 zhipu 后端 2026-09-07 移除已删） |
| `fix_hankaku_fast.py` / `fix_hankaku_segs.py` / `fix_missed_lines.py` | 定点修复工具（`fix_san_sensei.py` 一次性修复已完成 → legacy） |
| `reparse_delivery_html.py` / `gen_standalone_reader.py` / `work_memory.py` | 交付反解析 / 独立阅读器 / 知识库工具 |
| `aa_to_*` 等 | 其他独立工具 |

### fix/ verify/ — 保留的权威工具

> `cleanup/` 转发层已于 2026-09-08 删除；`pass2`/`pass3` 的权威实现在 `common/`。
- `fix/backfill_aa_comments.py` — 评论区回填（无权威替代）
- `fix/refine_aa.py` — ★ 校对链（漏译清理唯一实现，详见 AGENTS）
- `fix/refix_mixed_segs.py` — ★ 段级存量修复（按 review_inline 重跑新段级协议，2026-10-10）
- `fix/fix_lead_kanji_miss.py` / `fix/fix_mixed_drift.py` — 定向修复工具（清单驱动）
- `verify/quality_gate.py` — ★ M5 质量闸门（五类检查）
- `verify/metrics_report.py` — ★ M5 指标聚合

### tests/ — 单元测试（72 个 test_*.py / 1174 用例全绿）

日常（推荐：改哪跑哪 + 并行；脚本会自己切到 `.venv` 的 python）：

```bash
python scripts/tools/run_tests.py                  # 全量并行（~80-110s）
python scripts/tools/run_tests.py --changed        # 只跑与 git 改动相关的模块（+闸门），数秒~几十秒
python scripts/tools/run_tests.py --list --changed scripts/common/gen_aa_html.py   # 先看会跑哪些（不跑）
python scripts/tools/run_tests.py -j 1             # 串行（失败时看完整 traceback）

python -m unittest discover -s scripts/tests        # 底层原样，1174 用例（含 test_interface_docs 同步闸门）
```

- 全量墙钟的**下限 ≈ `test_mirror_incremental`（~69s）**：它每例要跑 ~100 次 git 子进程，而本机 `git --version` 空跑就要 ~0.09s（进程启动开销，改测试改不掉）；再想快只能减 git 调用（生产代码）或用更小的语料。

- L0 纯函数：`test_aa_common.py` / `test_line_meta.py` / `test_manual_fix.py`
- L1 集成：`test_extract.py` / `test_aaweb.py`（工作台）
- L2 管线：`test_pipeline.py`（假模型校验循环）
- L3 交付：`test_gen_html.py`（交付文件夹结构/**单档 vs 外置自动分档**/**payload v1↔v2 格式与还原**/懒加载/源留档/页面 JS 语法 + 章节正文 vs `tests/fixtures/delivery_golden.json` 哈希基线，4 部 46 章；重建 `scripts/tools/freeze_delivery_golden.py`）+ `test_edit_server.py` 的 `TestInlineWriteBack`/`TestInlineEndToEnd`（单档形态改行写回 index.html 内嵌段）
- 段级/稳定性/薄壳：`test_mixed_segments.py` / `test_stability.py` / `test_thin_shell.py` / `test_quality_gate.py`
- **接口同步闸门（2026-08-30 新增）**：`test_interface_docs.py`（生成器 vs doc/接口文档.md 一致性 + inventory vs 文件树一致性）
- 任务管理器：`test_task_manager.py` / `test_candidates.py` / `_fake_runner.py`

### legacy/ — 历史归档（108 个，自包含只读；**仅本地，线上仓库不含**）

2026-08-10 前平铺旧版 + 2026-08-30 废弃 92 脚本（作品专用 pass/gen/translate、平铺旧版、一次性工具）。**只进不出，勿修改**。历史恢复：`git show <commit>:<path>`。

### logs/ — 批次日志

`aa_pass*.log` / `*_batch.log` / `*_runner.log` 等历史批次日志。

## 4. 管线对应命令（从仓库根运行）

```bash
# 0. 模型配置（2026-08-19 统一化，改 TOML 即可，不碰代码）
AA_BACKEND=zhipu python scripts/aa_translate.py --work <id>        # 临时切后端(env 最高优先)
AA_R18=1 python scripts/aa_translate.py --work <r18作品>           # R18 作品: 自动拦截 r18=false 后端

# 1. 通用 runner（推荐入口，2026-08-21 起）
python scripts/runner/dl_runner.py --work <id> [--chapters all|new|eids|1-20]
python scripts/runner/translate_runner.py --work <id> [--chapters new|eids] [--force]
python scripts/runner/refine_runner.py --work <id>                 # 精译环节

# 2. 通用管线（common/ 各环节单跑）
python scripts/common/r401_index.py search <关键词>
python scripts/common/r401_index.py category <cid> --json <作品>/work/chapters.json
python scripts/common/dl_chapters.py --json <作品>/work/chapters.json
python scripts/common/extract_all.py --work <id>
python scripts/common/aa_translate.py --work <id> [--pilot eid,eid...]
python scripts/common/pass2.py --work <id>
python scripts/common/pass3.py --work <id>
python scripts/common/scan_width.py --work <id>
python scripts/common/fix_width.py --work <id>
python scripts/common/gen_aa_html.py --work <id>            # 交付文件夹: index.html + aa|汉化 txt.gz ＋ 编辑服务；章节数据按体量自动选单档(内嵌)/外置(data/*.js)
python scripts/common/gen_aa_html.py --work <id> --no-source # 不带源 txt.gz（编辑服务变只读）
python scripts/common/gen_aa_html.py --work <id> --external  # 强制外置 data/*.js（默认 >20MB 才外置）
python scripts/common/gen_aa_html.py --work <id> --inline-max 40  # 改分档阈值（MB）；--inline 强制单档
python scripts/common/gen_aa_html.py --work <id> --one <base># 单章试读 → work/单章渲染_<base>/

# 3.1 交付页编辑服务（PC 端改行/翻译替换写回源 txt；交付夹里双击即可跑）
python scripts/tools/build_edit_server.py                   # 先打包 exe → scripts/tools/dist/AA编辑服务.exe（生成器会拷进交付夹）
python scripts/tools/edit_server.py "交付HTML/<标题>_汉化合集"  # 仓库内直接跑（调试用）→ http://127.0.0.1:8770

# 3. 校验/交付工具
python scripts/verify/quality_gate.py --work <作品目录名> [--chapter <eid>]
python scripts/verify/metrics_report.py --usage <作品>/work/usage.jsonl

# 4. 矫正工作台
python scripts/tools/aa_web.py                    # → http://127.0.0.1:8765（或双击 一键重启工作台.bat）

# 5. 结构维护工具（2026-08-30 新增）
python scripts/tools/gen_inventory.py             # 重生成 scripts/_inventory.json（增删脚本后必跑）
python scripts/tools/gen_interface_docs.py        # 重生成 doc/接口文档.md（改 CLI/env 后必跑）
python scripts/tools/freeze_delivery_golden.py   # 重生成交付页正文哈希基线（渲染口径变更时必跑）

# 6. 辅助工具
python scripts/tools/aa_to_images.py --work <id> [--chapter 子串] [--out <dir>]
python scripts/tools/aa_to_cbz.py --work <id> [--out xxx.cbz] [--per-chapter]
python scripts/tools/line_classify_v2.py --work <id> [--chapter 子串]
python scripts/tools/translate_titles.py --work <id> [--force]
python scripts/tools/usage_total.py --since "2026-08-10 21:55" --until "2026-08-11 09:00"

# 7. 手机上读交付合集（PC 与手机连同一个 Wi-Fi；手机浏览器读、PC 上改）
<交付文件夹>\手机阅读(局域网).bat        # = AA编辑服务.exe <夹> --lan；按窗口打印的 http://<局域网IP>:8770/index.html 在手机打开
```

## 5. 结构变动维护约定（强制）

**任何结构变更（新增/移动/删除/重命名脚本或目录）必须同步四处，缺一不可：**

1. **本文档**（scripts/README.md）— 更新目录总览 + 明细表
2. **AGENTS.md** — 更新「目录结构」「管线(按序)」章节的命令路径
3. **skill `aa-pipeline-optimization`**（<skills>\media\aa-pipeline-optimization\SKILL.md）— 更新「项目真实结构」段落
4. **`scripts/_inventory.json`** — 重跑 `python scripts/tools/gen_inventory.py`（生成器/接口文档测试会强制校验一致性）

**CLI 参数 / env 变量变更后**：重跑 `python scripts/tools/gen_interface_docs.py` 并同 commit（test_interface_docs 校验）。

**变更后必做验证**（零 API）：
- `python -m unittest discover -s scripts/tests` — 1034 用例（用 `.venv` 的 python 跑，工作台用例依赖 fastapi）
- `python scripts/verify/quality_gate.py --work キッコーマン` — 全 OK（import 链完整）
- 涉及 import 修改时：单独跑受影响脚本的 import 冒烟
