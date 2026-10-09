# 术语表三级重构（2026-09-01 落地）

> 方案 `doc/TODO/术语表三级重构方案_2026-09-01.md`（grill 定稿）。目标：全局术语表瘦身分类化；作品术语表翻译前自动生成并固化进 runner。
> 归档：commit 3d5c0cdf（数据迁移）/ b01965b9（生成器+注入）/ f344aac7（消费方+UI+测试）/** 9cada802（昵称/校名修正）** / **e9088010（疑似角色+人工确认闭环）** / **67e72cd0（超宽不跳过+全局组折叠，用户并行提交）**。

## 文件格局（重构后）

| 文件 | 内容 | 角色 |
|---|---|---|
| `glossary.json` | 作品(28，2026-09-01 加 konosuba) / 角色块(901，**保留磁盘值但不再维护**，读侧已切 roles.json) / glossary 19 对 `[{jp,zh,cat}]`（**2026-09-01 用户手动删到 19，勿恢复**） / known_names(1311) | 全局替换对分类化 |
| `roles.json`（新） | 角色 905 条（901 + 灰かぶりの魔女/極点の魔女/梦见りあむ + アクア→阿库娅） | 全局角色表唯一权威 |
| `glossary_<work>.json` | v2 `[{jp,zh,cat,来源,role_cn,role_works}]`，来源=auto/manual | 作品术语表，**参与翻译注入** |
| `_knowledge.json` | 不动 | 人工知识库 |

**57 对全局归类**（逐对核对，方案原「25 角色+12 梗」是错的）：
- 角色 23 对→roles.json：VOICEROID 15 + キャル/梦见りあむ/りあむ + やる夫/戒能プロ/小鍛治/灰かぶりの魔女/極点の魔女
- 麻将 17 →特殊-麻将；作品名 1（白頭）→特殊-作品名；梗 13→常用-梗
- **校名 2（白糸台/阿知賀）→ 2026-09-01 已从全局移除**（saki 麻将世界观校名，属作品级，全局早该没有）——移入 saki 作品术语表 `来源:manual`（防 auto 重生成丢失）
- 正则 1（`re:...さん→\1桑`）→**保留 glossary.json 本地替换**（不进 prompt/roles）

## 注入语义（aa_translate `_load_glossary_pairs`）

- 数据源 = `_work_glossary_entries()`：WORK_ID 有值时读 `glossary_<work>.json`（缺文件调 `gen_work_glossary.generate` 现场生成），WORK_ID 空回退全局 34 对
- **manual 对 + 常用对（cat 含「常用-」）全量注入不过滤**（方案决策 3：梗量少通用，过滤会漏变体）
- **auto 的特殊/角色对按 base 章节语料二次过滤**（沿用旧 `_filter_glossary_pairs` 机制，命中才注入）
- **zh==jp 的 auto 角色对不注入**（e9088010：未人工确认译名不教模型保持日文；确认后来源改 manual 才全量注入）
- `base=None` 时全部注入；`re:` 正则对只本地替换不进 prompt
- 措辞保持「角色名也要用标准译名」（2026-08-21 实测结论）

## 生成器 `scripts/common/gen_work_glossary.py`

- 输入 roles.json + glossary.json + 作品 `aa/` 全语料；输出 auto 部分（常用全量 + 特殊按语料命中 + 角色按日文/别名命中）
- manual 部分保留不动；**指纹 = roles+glossary mtime + 章节数**，失效自动重生成（`.fp` 文件存指纹）
- **角色对 zh 一律=日文原文**（e9088010 定稿：アクア→アクア 不猜译名），真实译名放 `role_cn`、作品放 `role_works` 字段供 UI 展示
- **备注含「昵称/简称」的角色条目一律不生成替换对**（2026-09-01 用户双轮修正定稿）——昵称/简称只作素材统计定位角色（`_material_match_names` 别名展开），**不是术语替换对**；术语替换会把昵称误换全称（憧→新子憧），且单字（憧/玄/衣）误匹配风险高。用户原话："昵称/简称是用来找角色的，不是直接拿来术语表替换的"，二次强调"全部昵称简称都不注入术语表，即使你找对了术语表会把昵称误换成全称"。重生成后：宝可梦大师 57→53、kikkoman 38→33、saki 85→61（25 个昵称清除；白糸台/阿知賀 保留为 manual 校名）
- **艺术纹理/碎片 分类（ツモ/ぃぃ 等 AA 绘画碎片）不进术语表**（e9088010；宝可梦大师 auto 173→57 就是滤掉 116 个纹理占位对）
- translate_runner 1.7 在 chapter_prep/章节简介之后、翻译前调用（失败不阻塞）
- CLI：`python scripts/common/gen_work_glossary.py --work <id> [--force]`

## 工作台人工确认闭环（e9088010）

- work 页作品术语表：auto 角色对 zh==jp 且 role_cn≠jp 时显示「疑似角色: role_cn（role_works）+ 确认译名」按钮 → 点击 zh=role_cn、来源改 manual、保存落盘 → 翻译注入生效（验证：アクア→阿库娅 确认后注入 True，やる夫→やる夫 未确认不注入）
- 预览弹窗（应用术语表替换）：作品组显示疑似角色提示（`_role_hint` 查 roles.json 日文/别名命中，过滤艺术纹理分类）；受影响行带章节+行号跳转阅读器（`/work/<id>/read?f=..&line=..`，lines 数组第 4/5 元素是 fname/行号）
- **roles.json 加角色**：`{日文:アクア, 中文:阿库娅, 作品:[konosuba]}` + glossary.json 作品表加 konosuba（为美好的世界献上祝福）

## 踩坑（按踩序）

1. **`work_glossary_path` 用 work_dir 末段会生成 `glossary_work.json`**——registry 的 `work_dir` 是 `<作品>/work`，末段恒为 `work` 不是作品 id。修正：`work_id` 显式传参或取 work_dir **上一级**目录名。误生成的 `glossary_work.json` + `.fp` 要删。
2. **小鍛治/小鍛冶 字形陷阱**：glossary 替换对是 `小鍛冶`（冶），角色表里 `小鍛冶健夜`（治，全名）+ `小鍛冶`（冶，简称昵称）两条。ROLE_PAIRS 写错字形会新建重复条目。迁移前先查角色表实际日文。
3. **`validate_glossary` 校验的是二元组**——对象格式 `[{jp,zh,cat}]` 保存会 422。改 validate 接受对象或旧二元组。
4. **`load_work_glossary`/`save_work_glossary` 只认二元组**——生成器写 v2 对象后 UI 加载/保存会坏。load 兼容两种、save 对象缺省 `来源:manual`。
5. **消费方不止 logic.py**：`_load_roles_works`（素材统计）、main.py mat-scan 两处、gen_chapter_summaries（anthology 角色译名对照）、scan_role_residual（角色名残留扫描）都直读 glossary.json 角色块——全部切 roles.json。grep `"角色"` 全仓核对。
6. **iter_glossary --merge / gen_glossary_ai _add_global_alias 写角色**——角色写入目标从 glossary.json 角色数组改 roles.json（known_names 仍写 glossary.json）。
7. **`save_glossary` 角色键保留磁盘旧值**（不再 `cleaned["角色"]`）——角色已迁出，防旧前端把空角色写回。
8. **新增脚本后必须重跑 `gen_inventory.py` + `gen_interface_docs.py`**（test_interface_docs 闸门：inventory 与文件树一致 + 接口文档新鲜）——本会话新增 3 脚本（migrate/gen_work_glossary/test_work_glossary）触发 2 个闸门失败，重跑即绿。
9. **工作台旧代码进程**：改 UI（模板/JS）后浏览器实测前需重启工作台（bat 方式），否则拿到旧缓存页面（cat input 缺失症状）。
10. **roles.json 是全局角色表唯一权威**，但 `aaManga/` 已 gitignore——作品术语表 `glossary_<work>.json` 是生成物不入库（与 `_knowledge.json` 人工资产不同），符合仓库约定。
11. **改 glossary.json 等数据文件必须保持原格式（indent=2 + CRLF 结尾换行）**——json.dumps(indent=1) 会让 git diff 整文件重写（9844 行 diff 爆炸）。HEAD 是 `git show HEAD:glossary.json` 确认格式（CRLF + 2 空格缩进）后，用 `json.dumps(indent=2).replace('\n','\r\n')` 写回；改完 `git diff --stat` 应只显示目标行（移除 2 对 = 1 insert/11 delete）。**教训：迁移/手工改数据文件后先 `git diff --stat` 核对行数，别让格式重排污染提交**。
12. **⚠️ glossary.json/roles.json 是用户手动编辑的**（e9088010 事故）：agent 加 konosuba 作品时差点覆盖用户手动删除的术语对。**改前读磁盘现状、改后 `git diff --stat` 确认只含自己的改动**；用户说"这是我手动删的"=恢复用户版本+更新过时测试期望（不要坚持测试里的旧条目如 白頭）。
13. **`load_work_glossary` 返回 v2 对象后 preview/apply 直接 `list(p)` 解包炸 ValueError too many values**（e9088010）——必须 `logic._norm_pairs()` 归一化成 (jp,zh) 二元组再进 `apply_glossary_chapter`；main.py `api_glossary_preview`/`api_apply_glossary` 两处都要改。
14. **UI 改了但浏览器/服务端缓存旧版**：work.html 加载后 `p.role_cn` 未透传 → 确认按钮不显示。JS load 的 map 必须保留 role_cn/role_works 字段（`{jp,zh,cat,来源,role_cn,role_works}`）。
15. **超宽不跳过（67e72cd0，用户要求）**：`apply_glossary_chapter` 超宽行**照样替换**（旧版 skipped 跳过会漏替换），changed 元素带第4字段 `overwide=True`；preview 受影响行显示 ⚠超宽 徽标；测试 `test_overflow_not_skipped_but_flagged`。**调用方解包 changed 3 元组的地方要兼容**（main.py preview `for i,b,nb,*ow`；写回 `for i,_,nb,*_`）。
16. **预览弹窗全局组默认折叠**：`.gl-group.folded .gl-pairs{display:none}` + 组头 `▸/▾` 箭头（style.css），全局组渲染加 `folded` class，组头点击 `classList.toggle('folded')`（work.html）。
17. **用户并行提交会覆盖 agent 工作区**：agent 改完超宽逻辑后 `git status` 显示 clean——用户已并行 commit（67e72cd0 含同样的超宽改动）。**提交前先 `git log --oneline -3` 核对，内容一致则勿重复提交**（工作台重启后浏览器实测仍是唯一验证）。

## 验证

- 迁移 dry-run 对账：57 → 34 对（角色 23 迁出）、roles 901→904（小鍛冶并入 + 3 新增）
- 生成器：kikkoman auto 37+manual 1、saki 85（55 角色命中）；二次跑 regenerated=False（指纹生效）
- 注入：WORK_ID 模式 manual+常用全量、auto 角色按章过滤（448→283 字符）；`?` 占位不注入
- e9088010：宝可梦大师 auto 57（43 角色对，texture 滤除后）、アクア `{jp:アクア, zh:アクア, role_cn:阿库娅, role_works:[konosuba]}`、注入验证（确认后 アクア→阿库娅 True / 未确认 やる夫→やる夫 False）
- 测试：`test_work_glossary.py` 新增 7 用例 + `test_line_meta` WorkMode 4 用例；全量 250 绿（含修复后的 test_mixed_segments）
- 浏览器实测：/glossary 34 对带 cat 列、角色弹窗 roles.json 加载（saki 209 角色）、work 页作品术语表 58 对来源/cat 正常、34 个疑似角色确认按钮、确认后 zh 变阿库娅+来源 manual、console 零错误
