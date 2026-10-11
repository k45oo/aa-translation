---
name: aa-release-packaging
description: Use when 打AA翻译发行包/开箱即用包（脱敏、白名单、示例作品、冻结 exe、包内自检）。
---

# AA 翻译「开箱即用」发行包

一条命令（仓库根、用 `.venv/Scripts/python.exe`）：

```
python scripts/tools/build_release.py [--out DIR] [--no-exe] [--keep-stage] [--no-verify] [--smoke-api]
```

→ `导出/AA翻译_发行包_<YYYYMMDD>/` + 同名 zip（干净源码 + `AA工作台.exe` + 示例作品 3 话 +
宝可梦 wiki 翻译素材 + 独立配置）。方案 / 十项决策 / 实测 / 落地记录：`doc/方案归档/发行包打包方案_2026-10-09.md`。

**一键入口 = 仓库根 `一键打包发行包.bat`**（UTF-8 + CRLF + `chcp 65001`）：= `build_release.py --out 导出`，
参数原样透传；命中「活数据根」被拒（rc=6）时**自动改用 `导出_新` 重跑**，用户正在跑的实例不受影响。
改这个 bat 后真跑一次验：`subprocess.run(['cmd','/c',BAT], stdin=DEVNULL)`（不喂 stdin 会卡在 `pause`），
且**按关键行断言**而不是只看退出码：至少要看到「拒绝出包 → 自动改用 `导出_新` → 产物/压缩包路径 → 自检: 通过 → [ok]」。
命令被 shell 吃掉、根本没跑起来时同样以 0 退出，只对退出码会把「什么都没跑」当成验过。

> 适用于 `<repo>`。
> 同类机制：`.codex/skills/`（技能副本）与 `origin` 裸仓库的**代码镜像**（`scripts/tools/sync_codex_skills.py` / `scripts/tools/push_code_mirror.py`）都走「生成时脱敏 + 残留门禁 + `--check` 比对」，见下节与 `references/derived-artifact-desensitization.md`。
交付夹与外发单个作品走 `aa-delivery`；**打包整份工具链给别人用 = 本技能**。

## 何时用

- 用户说「出发行包」「打包给别人用」「开箱即用包」「打包脱敏」
- 改了模型链 / 示例作品 / 随包 wiki 素材后要重新出包
- 包内单测一片红、双击 exe 首页列出不该有的作品、外发物夹带本机路径

## 派生产物：技能副本 / 代码镜像（同一套脱敏 + 门禁）

| 产物 | 生成器 | 形态 |
|---|---|---|
| `.codex/skills/` | `scripts/tools/sync_codex_skills.py`（`--check` 验失同步） | 技能库 → agent CLI 兼容格式 |
| 代码镜像（`origin` 的 `main`） | `scripts/tools/push_code_mirror.py`（`--check` = 构建+门禁+与远端比 blob；`--dry-run` 提交不推） | 代码/文档子集，全新历史单提交 |

两者是**生成物，不手工维护**，共用同一张脱敏规则表与门禁：未脱敏路径/用户名、密钥形状、体积上限、
快照内 `.py` 全部 `compile()`，命中 rc=2 拒出包（先构造后门禁，拦住时远端零副作用）。

三条最容易白跑一轮的：
1. **分隔符要收 1~2 个**：源码里的路径是双反斜杠转义写法，只认单个分隔符的规则**既漏替换也漏门禁**
   → 镜像里躺着几百处本机路径、门禁还报「通过」。**替换与门禁必须同时放宽**，只放宽一边 = 假绿。
2. **规则里绝不能出现真实用户名/机器名**（含注释、门禁描述、示例文本）——规则本身就会成为下一个泄漏源。
   用户名从环境取（`USERNAME` / `AA_SCRUB_NAMES`），并给「描述门禁本身的句子」留白名单
   （文档里写「禁止某前缀」这类说明不该被自己拦下）。
3. **`--check` 必须与远端比对**（远端 `ls-tree -r` 的 blob sha vs 快照 `git ls-files -s`），
   否则「构建通过」≠「镜像是新的」。真实收益：升级当天立刻抓到镜像落后了哪两个文件。

**验收**（`git clone` 到临时目录后三件事，缺一不算过）：① 逐模式复扫占位符/键形，应全 0（只允许通用前缀说明类的少量残留）；
② 冒烟导入 `works.registry` / `common.aa_config`，断言 `paths.BASE` **自动等于克隆目录**（这一项才证明可移植）；
③ 确认数据目录/`legacy/`/构建产物/字体子集都不在、提交数 = 1。

## 铁律（每条都踩过，改脚本前先读）

1. **exe 必须与包同源**：exe 只能由 `build_release.py` 那棵 staging 打（`build_workbench_exe.py --stage <staging>`）。
   若让 exe 生成器自己去仓库 staging，就会出现「包内 registry 裁成 1 部示例作品、exe 里仍是生成者的全部作品」
   —— 双击 exe 首页列出几十部作品。
2. **顺序固定**：staging → 变换 → 脱敏 → 硬门禁 → 打 exe → 压包 → 解包冒烟。
   打 exe 放到脱敏之后，exe 里才是脱敏过的那份源码；放到门禁之前，坏包会先进 exe。
3. **`scripts/tests/` 免脱敏**（`release_common.NO_DESENS_DIRS`）：测试夹具里的绝对路径是**故意伪造**的
   （`r"C:\tmp\f.ttf"`、`"<repo>/aaManga/某作品/aa"`），被替换成 `<repo>` 后断言即废。
   免脱敏目录仍过**放宽版**痕迹扫描（`LEFTOVER_RELAXED`：用户名/`C:\Users`/`AppData\Temp` 照拦，放行项目自身路径）。
4. **测试缺数据只能 `skipTest`，绝不 `SystemExit`**：`registry.get('未注册 id')` 抛的是 SystemExit
   （继承 BaseException，unittest 不接）→ 整个 `unittest discover` 当场终止，使用者看到「包里的测试跑不起来」。
   一律走 `scripts/tests/_work_guard.py`（`work_cfg` / `any_work_cfg` / `repo_only`）。
5. **可写数据必须走数据根**：`aa_web_app` 的 `glossary.json`/`roles.json`/`aa_web_app/data/*`/`揭示板`
   一律 `_DATA = paths.BASE`（`AA_BASE` 可覆盖）。冻结 exe 里代码在 PyInstaller 解包目录、数据根 = exe 所在目录；
   写进解包目录的数据每次启动就丢。`AA_BASE` 未设置时必须与仓库根逐字节同路径（单测守护）。

## 铁律 6–14：冻结 exe / 变换 / 依赖 / 数据落点 / 构建安全

6. **子任务命令一律走 `scripts/common/aa_spawn.py` 的 `py_prefix()`**：冻结后 `sys.executable` 是 exe 自己，
   `[exe, dl_runner.py, …]` 会被入口当成"再起一份工作台"（参数被无视、端口被占、父进程等一个永不退出的服务）
   → 用户看到的是**任务卡住 / 下载失败**。`py_prefix()` 冻结态返回 `[exe, "--cli"]`，配合
   `aa_web_exe.py` 的 `--cli` 模式（runpy 在本进程跑目标脚本）。子进程 env 用 `child_env()` 带上 `AA_BASE`。
   新增 spawn 点时 grep `\[sys\.executable` 收尾，别漏。
7. **变换必须换行无关**：读用 `_read_lines()`（剥尾随 `\r`、记下主导 eol）、写用 `_write_lines()`。
   工作区文件可能**整份 CRLF**（实测 `registry.py` 就是），直接 `read().split("\n")` 后
   `lines[i] == "}"` 会静默失配，报"找不到 WORKS 块"。
8. **改包内内容只能改变换 —— 不手改产物、不动本地库**：用户说「移除/清空某物」而目标在打包产物上时，
   正确动作是给 `build_release.py` 加/改一个变换（如 `transform_image_manga()`），而不是去编辑
   `导出/…` 里的文件（下次出包就被覆盖）也不是去改本地开发库（会要求回滚，已发生过一次）。
   改动前先问自己/确认一句：目标到底是「发行包」还是「本地开发库」。

9. **依赖必须"自动齐全"，且要收到第三层**：exe 把 `scripts/` 当数据带，PyInstaller 看不见它们内部的 import ——
   漏一个依赖只在跑到那条路时才炸。分三层，每层都真炸过一次：
   ① 顶层模块（`ModuleNotFoundError: janome`）→ 问 staging 的 `check_imports.scan()` 要清单 + `--hidden-import`；
   ② 子模块（`No module named 'janome.tokenizer'`）→ 每个探到的包都 `--collect-submodules`（`import X` 过 ≠ `X.sub` 在包里）；
   ③ **按路径读文件的包**（`FileNotFoundError: _MEI*/janome/sysdic/entries_compact0.py`）→ 这类包运行期 mmap/按名
   打开自己的 `.py` 词典，收进 PYZ 等于没有 ⇒ `DATA_PKGS` 把它**整包当 data 复制**、不进 PYZ
   （`--add-data <pkg目录>;<包名>`），运行期 import 由 MEIPASS 文件系统兜底。
   配套：`check_imports` 连**点分子模块**一起验（只验顶层会放过 ②）；
   `SUBMODULE_DENY`/`DATA_DENY` 排除官方 hook 已覆盖的大块（PIL 二进制 13MB、fontTools 内嵌 `.c` 6MB），
   排除安全——真漏了自检直接让构建失败。**验收必须真跑一次**：`exe --cli scripts/common/build_term_pool.py --aa <目录>`
   （零 token，真跑分词器出候选；只验 import 会漏掉 ③）。
   两个配套口径：① `--cli` 的**相对**脚本路径优先按数据根解析（否则手敲 `--cli scripts/...` 跑到解包目录那份副本上，
   扫不到 `aa_web_app/*.py` 会把 Web 子模块误报成"缺依赖"）；② `check_imports` 判本地名时除目录扫描外
   再试 `find_spec('aa_web_app.' + name)`——冻结态 `aa_web_app/*.py` 在 PYZ 里、磁盘上没有。
10. **冻结态必须有"可写的数据根"落点**：exe 里的 `registry.py` 属于**临时解包目录**，而「＋ 添加作品」写
   `registry.__file__` → 等于写进临时目录，**重启后作品消失**（表现为作品页看不到章节）。
   `aa_web_exe._bootstrap_registry()` 启动时把 registry 复制到 `<数据根>/scripts/works/` 并把它插到
   sys.path 最前（只复制 registry 本身，`paths.py` 仍用解包目录那份，保住 `CODE_ROOT` 语义）。
   构建探测断言数据根里出现该文件。
   ⚠️ **引导必须排在任何分支之前**（先 `_ensure_dirs` + `_bootstrap_registry`，再判 `--cli`）——
   否则工作台起的子任务（下载/翻译/候选提取）只认包内示例作品，表现为使用者一按按钮就
   「未知作品: X（可用: baitou）」。自检 `tools/check_registry.py`（打印实际命中的 registry 路径与作品清单），
   构建探测断言它落在**数据根**（不是 `_MEI*`）——旧探测只用示例作品，读错注册表也照样通过。

11. **包装脚本不得把子进程的崩溃报成成功**：`run_full_pool_until_done` 曾用「无 `pool_progress.json` = 已完成」
    判完成 → 子进程崩（rc≠0、没写 progress）时打印「已完成」并 `return 0`：候选一条没生成、界面报成功，
    用户只能反复点「还是不行」。判定必须**同时看退出码与进度文件**：exit=0 且无 progress = 真完成；
    exit≠0 且无 progress = 失败（透传退出码）；跑满轮数也是失败。同类检查：任何"续跑/重试"包装都要区分
    「无进展」与「跑完了」。用例 `scripts/tests/test_run_pool_exit.py`。

12. **"手工验证"和"用户点按钮"走的不是同一条路**（打包版最阴的坑）：工作台起子任务用
   `os.path.join(_ROOT, "scripts", ...)`，冻结态 `_ROOT` = **解包目录**；那种脚本用 `__file__`
   反推 `_BASE` 后 `insert(0, <解包目录>/scripts/works)` **盖掉**启动引导放进来的数据根注册表 →
   子任务报「未知作品: X（可用: baitou）」。手工 `--cli scripts/...`（相对）命中的是数据根那份，
   所以**手工怎么验都是绿的**、用户点按钮全红（为此白转三轮）。
   修法：`_resolve_cli_script()` 把落在解包目录的绝对路径映射到数据根那份；`_bootstrap_registry()`
   落好数据根后**删掉解包目录那份 registry**（物理杜绝）。
   **验证方法（唯一可信）**：往数据根注册表临时加一个**空作品**（aa/ 0 章 → 零 token），
   `POST /api/work/<探针>/gen-candidates` 让**服务端**起真子任务，看它认不认得这个作品 ✔；
   一键跑：`python scripts/probe_exe_spawn.py <包目录> [端口]`（本技能自带，自动加探针→起实例→查日志→摘干净）；
   用例 `tests/test_aa_web_exe.py`（解析 2 例 + "解包目录那份必须删掉"断言）。

13. **包内自检/测试必须与"操作者环境"隔离**：`aa_env` 取值口径 = **真环境变量 > .env > 兜底**，
   且 `AA_ENV` 的优先级**高于** `AA_ENV_PATH` —— 操作者 shell 里带 `AA_ENV` 或同名 `*_API_KEY`，
   用例夹具（临时 `.env` + `AA_ENV_PATH`）就被静默盖掉：实测 key 类 4 例假失败、settings 页 7 例假错
   （用户报「打包脚本报错、包内工作台可用」的真身）。修两处：① 用例自洽（setUp 清 `AA_ENV`、同名 key、
   `aa_env._CACHE["sig"]`，tearDown 还原；凡用 `AA_ENV_PATH` 的测试都要把 `AA_ENV` 纳入"要清清单"）；
   ② `verify_unpacked` 跑包内单测时**消毒环境**（去 `AA_ENV`/`AA_ENV_PATH`/`*_API_KEY`）——
   自检要验的是**这个包**。验证法：带敌意环境跑一次打包
   （`AA_ENV=不存在 OPENCODE_GO_API_KEY=zzz build_release.py --out X --no-exe`）必须"自检: 通过"。

15. **产物目录只能"挪开"、绝不 `rmtree`**（第二次数据丢失换来的教训）：用户把 exe 放在
   `导出/<包>/` 里跑（数据根 = 包目录）→ 重建时"活数据根守卫"**漏过一次**（守卫函数在沙箱里对同样
   结构返回 True，原因未定论），`rmtree`+`copytree` 把整棵数据根换成干净包：丢用户的翻译产物。
   判「是谁动了数据根」**先看证据再下结论**：产物/exe/zip 的 mtime、有没有 `.old_*`、有没有比你部署时间更晚的
   构建痕迹 —— **使用者自己也会跑出包脚本**（它会重建/替换实例目录、清掉 `.env` 与作品数据），
   别默认是自己刚干的就急着道歉/复原；先问一句，再按 `references/safe-rebuild-and-recovery.md` 恢复。
   根治 = 不再删：`_set_aside_out_dir()` 把旧目录 `os.replace` 成 `<名>.old_<时间戳>`（保留最近 2 份），
   守卫只是"能早拒绝就早拒绝"，**不能当唯一防线**。产物目录检查还要**前置**到参数解析之后（别等 7 分钟）。
   验证要点：沙箱里造"有数据"和"空但有旧产物"两种目录各跑一次（前者必须拒绝、后者必须挪开且内容不丢）；
   用例 `tests/test_release_pack.py`。一键入口 = 仓库根 `一键打包发行包.bat`（拒绝后自动改 `导出_新`）。

14. **冻结态写项目文件，先问"这是代码根还是数据根"**：`_SCRIPTS = _ROOT/scripts`（`_ROOT` 由
   `aa_web_app/main.py` 的 `__file__` 推出）在冻结 exe 里 = **解包目录** —— 写它会静默丢（下次启动没了），
   或在引导已删掉那份文件时直接失败。实测：`_remove_registry_entry`/`_append_registry_entry` 写
   `_SCRIPTS/works/registry.py` → 「删除作品」在**盘上文件已删完之后**报 500 → 用户看到
   「文件已删除但记录还在」。修法：`main._registry_path()`（一律 `registry.__file__` = 数据根）统一两处写入口。
   同类排查：凡冻结态要写项目文件/配置/注册表，先确认它落在数据根。
   配套铁律：**接口顺序** = 先改记录、后删文件（记录失败 500 且不动任何文件）；响应带 `failed` 列表。
   用例 `scripts/tests/test_work_delete.py`。

   - **早拒绝判据**：`_looks_like_live_data_root()` 命中（带 `.env` / 非空 `aaManga/` / `work_meta.json`）
     → rc=6 拒绝并提示换 `--out`；这道检查要**前置**到参数解析之后，别等 staging/测试/exe 跑完 7 分钟才报。
   - **升级正在跑的实例**：走「另建一份包 + 只同步代码、保留数据」（本技能 `scripts/sync_pkg_code.py <新包目录> <实例目录> <新zip>`），
     先停实例（它占着日志文件）。判据、步骤与「数据已被删」的恢复（`.env` 从备份、注册表条目重建、
     原文重下、`chapters.json` 从别处实例借副本）见 `references/safe-rebuild-and-recovery.md`。

## 变换点（要改默认链 / 示例作品 / wiki 就改这些）

| 想改什么 | 改哪 |
|---|---|
| 默认模型链、`[default]` 四键 | `build_release.transform_toml()`（读 TOML → 改键写回，**不写死整段文本**，否则仓库 TOML 一改就漂移） |
| 示例作品、留几话 | `build_release.EXAMPLE` + `stage_example()`（registry 条目路径用 `os.path.join`，别写反斜杠字面量） |
| 随包 wiki 素材 | `build_release.WIKI_*`（只带翻译必需的 JSON，**不带 `_raw/` 抓取缓存与 `index.html`**） |
| 作品痕迹清空范围 | `write_skeletons()`：`glossary.json`（作品/角色/替换对/**`known_names` 残留白名单**）与 `roles.json` **随包给真表**（用户指定，开箱即用）；`work_meta.json` 只留示例作品 |
| 顶层白名单 | `build_release.TOP_ENTRIES`（多一个条目就拒出包） |
| 随包图文手册 | `build_release.stage_manual()`（复制到包根 + 图片链接前缀改写为 `doc/使用说明图/`）；`TOP_ENTRIES` 与 `gates()` 的 `must` **两处都要加** |
| 起子任务前缀 | `scripts/common/aa_spawn.py`（`py_prefix`/`child_env`）；新增 spawn 点别漏 |
| exe 依赖清单 | `build_workbench_exe._scan_third_party()`（问 staging 的 `check_imports.scan()`）+ `COLLECT_SUBMODULES`/`COLLECT_DATA` |
| exe 数据根持久化 | `aa_web_exe._bootstrap_registry()`（registry 落数据根 + sys.path 优先；**须在任何分支前**，`--cli` 也要） |
| registry 归属自检 | `scripts/tools/check_registry.py`（`--cli` 子进程实际命中哪份 registry） |
| 图片漫画注册表 | `transform_image_manga()`（清空 `IMAGE_MANGA`——表里是生成者本机图片目录；硬门禁断言空表，exe 探测断言 `/manga` 0 行） |
| 脱敏规则 / 豁免目录 | `release_common.DESENS` / `NO_DESENS_DIRS` / `LEFTOVER` |

## 随包图文用户手册

- **源文件放仓库 `doc/<手册>.md`，出包时复制到包根**（与 `README.md` 并排，使用者解包就看得到），配图留在包内 `doc/使用说明图/`。
  手册里的图片链接按**自身相对路径**写（`使用说明图/xxx.png`），复制到包根时改写前缀为 `doc/使用说明图/xxx.png`——
  同一个 md 在仓库与包内两个基准目录下都能解析。
- **`TOP_ENTRIES` 与 `gates()` 的 `must` 都要加**：只加白名单 → 门禁拦不住漏带；只加 `must` → 直接拒出包。
  配图目录也进 `must`（目录存在性判据），拦住「干净但东西没进去」。包内 README 里加一行指路。
- **图必须来自干净实例、且与本次要出的代码同源**（做法见 `aa-web-review-app`「给手册/交付说明截图」节）。
  **二进制图不过脱敏/残留门禁**（`release_common.DESENS_EXT` 只收文本后缀，`.png` 直接跳过）——图里的 key 掩码、本机路径、
  作品清单只能靠**人眼看图**拦，别指望门禁。
- **手册里的按钮名/下拉项/站点清单必须从代码校对**：可见文案用 `search_files`（内容搜索，底层 rg）从 `aa_web_app/templates/*.html` 取，
  支持站点表用 `add_work.YARUOBOOK_DOMAINS` / `SUPPORTED_EXLINK_DOMAINS`——别凭记忆写（界面文案一改，手册就成假说明书）。
  按钮/链接分布别猜：先拿 `search_files` 把 `<(button|a|label|summary)[^>]*>[^<]{1,60}` 的实际文案倒出来。
- **出包后必查三件**：① 包内手册与仓库源文件除链接前缀外**逐字节一致**（`diff` 一次）；② 手册里每个 `](相对路径)` 在包内都能解析
  （一条 python 循环，漏改前缀就是断链）；③ zip 里手册 + 全部配图都在。


## 验收标准（自检不过就别外发）

`build_release.py` 默认跑完就自检，全过才是完成：

1. 硬门禁：白名单 / 本机痕迹 / 真 key 明文 / 所有 `.py` 过 `compile()` / 必需文件齐全；
2. zip 清单 == 目录清单；
3. 解包后跑单测：**仓库基线之外零失败**（基线由脚本自己跑一次仓库套件生成、缓存于 `temp/release_baseline_tests.json`）。
   **判定口径**：抓失败用例用**非锚定** `(?:FAIL|ERROR): (\S+)` —— `ResourceWarning` 会插在同一行前，锚定 `^FAIL:` 会全漏；
   且**必须看到 `Ran N tests` + `OK|FAILED` 汇总行**。缺汇总行 = 导入层直接崩了（典型：某用例抛 `SystemExit` 会当场终止整个 run），
   此时的"零失败"是假绿，绝不能当成通过 —— 这个坑曾让门禁放行了一个包里 27 例失败、外加整跑被 SystemExit 打断的包。
4. 解包后零 token 链路 `rc=0`（`chapter_prep` + `extract_dialogue --src`）；
5. exe 在解包目录内真起：HTTP 200 + 首页**只列出示例作品** + `/manga` 表格 0 行 + `workbench.log` 落在数据根；
6. **冻结 exe 起子任务**：`AA工作台.exe --cli scripts/common/chapter_prep.py --work baitou` 必须 rc=0；`--cli scripts/tools/check_imports.py` 也必须 rc=0（依赖齐全，含**点分子模块**）；`--cli scripts/common/build_term_pool.py --aa <含 *_台词.txt 的目录>` 必须真出候选（零 token，验"按路径读词典"那一层）；`--cli scripts/tools/check_registry.py <示例作品>` 的输出里 `registry.__file__` 必须落在**数据根**而非 `_MEI*`（旧探测只用示例作品，子进程读错注册表也照样通过）；启动后数据根应出现 `scripts/works/registry.py`（持久化）
   ⚠️ **验前先清环境**：`AA_BASE`/`AA_ENV` 在终端会话里会残留并**静默改掉数据根** —— 实测拿探针副本当数据根起实例，表现为「首页只 1 部作品、作品页 404」，白查一轮当成产品 bug。起实例用 `env -u AA_BASE -u AA_ENV`，`--cli` 自检则显式传 `AA_BASE=<包目录>`（**Windows 风格路径**：原生 exe 不做 MSYS 转换，传 `<repo-parent>/...` 会被拼成 `F:\f\ai\...`，报「aa 目录不存在」——那是调用问题，不是包坏了）。
   （这是"工作台能不能真的下载/翻译"的直接证据，探测里已内置）。
   exe 默认端口 **8766**（源码路线 8765；`AA_PORT` 覆盖，被占自动顺延）。
   ⚠️ 探测里的路径断言**两侧都要 `os.path.normcase`**：Windows 大小写不敏感，只归一化一侧
   （`normcase(期望) in 实际输出`）会因盘符/目录大小写不一致而假失败，把好包判成坏包。

**声称「下载/翻译在包里能跑」必须用真命令验一次**（零 token、真联网）：在包目录里跑
`AA工作台.exe --cli scripts/runner/dl_runner.py --work baitou --chapters 1-1`，
看 rc=0 + 日志里「清单 N 话 → ok <eid> <KB> → 提取 N lines / M aa 行 → DONE」+ `work/` 下
`chapters.json`/`chapter_order.json` 真出现。只凭「--cli 能跑 chapter_prep」不足以说明下载链通。

`--smoke-api` 目前只是开关占位（未接真实调用）——别以为它验了端到端翻译。

**自检"未通过"先别急着重跑**：判据是"仓库基线之外"，而基线是**出包那一刻**算的——并行会话此刻
正在改的代码会让仓库自身也失败（基线过时 → 包内那条"新失败"其实与打包无关）。
先在仓库里跑同一条用例确认，再决定是刷新基线复验既有产物（快）还是真需要重建（慢）——
复验用本技能的 `scripts/reverify_release.py <仓库根> <包目录> [<zip>]`（刷基线 → 解包单测 → exe 探测，
**不重建 exe**）。参见 `references/safe-rebuild-and-recovery.md`。

**先冻结代码，再出最后一次包**：`build_release.py` / `build_workbench_exe.py` / 自检工具本身**也在包里**，
出包之后对它们的任何改动都不进包（zip 里是旧那份，甚至与包内 exe 的行为不自洽）。要么把所有编辑做完再出包，
要么承认这轮产物滞后、用同步脚本补进实例并明确告诉使用者 zip 是旧的。
**staging 在构建一开始就整体快照**：出包途中改 `doc/` 里的手册/文档同样要重跑一轮（先出包再改手册 = 白跑一次 6 分钟）。
**迭代要挑便宜的档**：试机制/守卫/变换 → `--no-exe --no-verify`（一两分钟）或直接在沙箱目录上跑；
只动 exe → `build_workbench_exe.py --stage <现成 staging> --out <路径>`（~3 分钟，本技能「单独用 exe 打包器」节）；
完整出包 ~3~4 分钟（包内千例单测 + 7 项 exe 探测）—— 别每轮都跑满，一轮一轮攒起来就是十几分钟。

## 排查「打包版某功能失败」的顺序

使用者报「还是失败」时按这个顺序走，别一上来就重出包：

1. **先读他实例里最新的日志，并核对时间戳**。把日志 mtime 与「你上次部署新包的时间」比一比——
   **早于部署 = 他测的还是上一版**，不是你的修复失败（同一功能连点几次会留下多份日志，最新那份才算）。
   一次看全：`find <数据根> -name '*.log' -newermt '-40 minutes' -printf '%TH:%TM %8s %p\n' | sort`。
   日志落在 `<数据根>/aa_web_app/workbench.log`（服务端）与 `<数据根>/aaManga/<作品>/work/*.log`（按功能分文件）。
2. **同一报错会一层一层往下走**：修掉顶层错误再点，暴露的是**下一层**（依赖尤其如此：能 import →
   子模块在不在 → 运行期按路径读的文件在不在）。每次都以**新日志**为准，别拿旧报错反复推理。
   - **读日志里的路径**：出现 `_MEI*`（如 `_MEI000008a02\scripts\common\gen_glossary_ai.py`）= 子进程跑的是
     **解包目录**那份脚本，它的 `__file__` / `_BASE` / `sys.path` 插入都指向解包目录（铁律 12 的根因，
     与你手工 `--cli` 验的那条路不同）；路径指着数据根那份才说明走的是同一条路。
   - **先分清「崩溃」与「跑完但 0 结果」**：`--full 全量完成 ✓` / `AI 筛选 0 条专名候选` 是正常结局
     （池子空、语料缺），不是失败；失败必然带 traceback 或非 0 退出码。别把 0 结果当成"还是失败"去修。
   - 阶段 0 反复 `补台词轮 N exit=9(仍有缺), 续跑` = 有章缺 `raw/`（同日志里 `[warn] N 章台词提取失败 … raw HTML 缺失`），
     `run_pool` 会空转到 max-rounds 再带缺继续——这是**语料/作品数据**问题，不是打包问题。
3. **服务进程与子进程视图不同，要分开问**：首页/作品页是服务进程，按钮触发的任务是 `--cli` 子进程。
   用 `check_registry.py`（谁在册）与 `check_imports.py`（依赖）各问一次——只看界面会漏掉子进程那一半
   （实测：界面正常列出章节，子进程却报「未知作品」）。
4. **复现要落在「真跑」，且优先挑零 token 的阶段**：同一条链上不花钱的那段（`gen_glossary_ai
   --ensure-dialogues-only`、`build_term_pool --aa <含 *_台词.txt 的目录>`）能复现同一代码路径，
   别为了验证去跑花钱的 AI 阶段。这类「真跑」断言随后加进构建探测，下次出包自动拦。
5. **修完在他实际运行的那份产物上复验**：实例目录（数据根 = 包目录，含他自己的作品与 `.env`）才算数；
   只在自己 `temp/` 里的干净副本上验过就交付，等于把差异留给使用者。升级在跑的实例见铁律 12 的同步脚本。
6. 起实例前清环境：`env -u AA_BASE -u AA_ENV`（残留的 `AA_BASE` 会把数据根静默指到别处 →
   首页少作品、作品页 404，看着像产品 bug）。`--cli` 自检则显式传 `AA_BASE=<包目录>`。

## 单独用 exe 打包器

```
python scripts/tools/build_workbench_exe.py [--stage <源码树>] [--out <exe路径>] [--clean] [--no-probe]
```

不带 `--stage` 时它自己 staging 仓库源码（**仅供本机开发自用**，不可用于发行包）。
打完默认起一次探测（临时数据根 + 端口 8799，绝不碰 8765）。

## 交付页编辑服务（另一条 exe 线）

`scripts/tools/build_edit_server.py` 与发行包共用 `release_common` 的脱敏/复查；它随交付夹外发，
改渲染逻辑要重打。两者都不要把生成者本机路径带出去。
