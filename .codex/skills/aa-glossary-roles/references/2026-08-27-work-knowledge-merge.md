# 作品知识库（2026-08-27 grilling 定稿 → 当日实施完成，6 阶段 7 commit）

用户想法：翻译前统计作品素材生成作品术语表 → 人工确认 → 用于翻译；把作品术语表与作品记忆合并维护，作品记忆需含 **章节简介 / 人物简介**。

## 定稿决策（10 项，grilling 逐项确认 + 审批意见吸收）

1. **单文件四块** `work/<work>_knowledge.json`：`术语` / `语境规则` / `人物`(含简介) / `章节简介` + `待确认` 区；人物简介并入"人物"值
2. **双层结构**：全局 glossary.json 不动（跨作资产），作品层优先（同 key 作品层为准）
3. **章节简介开工前批量生成**（serial 可引用前章简介；anthology 逐章独立+提取本章角色译名），翻译+校对双注入
4. **人物简介 AI 初稿 + 人工 `确认:true` 才注入**（沿用 iter_glossary 审校模式，防幻觉）
5. **候选分流**：作品专属候选 → 知识库**正式块**；跨作通用（--to-global）→ 全局表（2026-08-27 审核修：原定"待确认区"是死数据——iter 层人工已 `确认:true`，写待确认区后无工具提升、永不注入；待确认区只收 AI 初稿）
6. **注入位置**：system 层每块带本章简介（1-3 行，**不跨章**；跨章连贯靠前 1 楼台词 context）
7. **迁移后删旧 `_memory.json`**（防双份漂移），备份 work/backup/ 后删
8. **`关联度` 字段（serial/anthology）唯一来源 = `aa_web_app/data/work_meta.json`**（审批意见 #1 必改：不存 knowledge 文件防双写漂移；与 r18_level 同源，aa_translate._load_work_meta_local 有先例可复用）
9. **anthology：章节简介含本章角色译名，人物详情不跨章注入**（防跨章污染）
10. **anthology 内部不细分章组**（小连载如肉壷短編セイバー系列靠前 1 楼 context 连贯）

## 实施记录（每阶段独立 commit）

| 阶段 | commit | 内容 |
|---|---|---|
| 1 | 987afed | work_memory.py 升级：`load_knowledge/save_knowledge`（兼容旧名 load_memory/save_memory）、`memory_hint(work, base, with_summary, max_people=20)`、`work_association`、`migrate_memory_to_knowledge`；sujin_shabu 迁移（术语10+人物2，备份 work/backup/ 后删旧） |
| 2 | 9460bc8 | aa_translate 注入：`glossary_prompt_hint(base=None)` 显式穿透、`_filter_glossary_pairs(aa_dir, base)` 按章过滤（缓存键加 base 维度）、`translate_lines/mixed` 加 base 参数、补译 hint_sys 也挂 glossary_prompt_hint；proofread._glossary_hint 改用 work_memory 统一构造（修掉"前20对不按语料过滤"旧问题） |
| 3 | 877bad8 | 新增 `gen_chapter_summaries.py`（台词提取输入、serial 前情/anthology 独立、增量 skip 已有键、R18 按 r18_level 选后端链） |
| 4 | d689167 | iter_glossary.merge_confirmed 分流（默认→知识库；--to-global→全局表）+ gen_glossary_ai AI 候选写待确认区 |
| 5 | 0c4d1d6 | work_meta.json 加 `关联度` 字段（_meta.说明更新；yaruohiroba 标 anthology，baitou/saki/sujin_shabu 标 serial） |
| 6 | 54eb20b | 新增 test_knowledge.py（15 用例全绿）；全量 190 用例 9 失败=历史遗留（git stash 对比确认，零新增失败） |

## 关键文件与接口

- `scripts/tools/work_memory.py`：`MEM_FNAME="_knowledge.json"`，`OLD_MEM_FNAME="_memory.json"`；`_work_dir` 用 registry.get(work).work_dir；`_mem_path = <work_dir>/<work>_knowledge.json`
- `scripts/common/gen_chapter_summaries.py`：`--work <id> [--force] [--chapters eid1,eid2]`
- `scripts/common/iter_glossary.py`：`merge_confirmed(cfg, work_id)`；main `--merge [--to-global]`
- `scripts/common/gen_glossary_ai.py`：AI 候选同时写知识库待确认区（角色/人物类型→待确认.人物，其余→待确认.术语）

## 使用流程

- 翻译/校对自动读 knowledge.json（memory_hint 经 glossary_prompt_hint(base) 注入），无需手动配置
- 新作开工：`gen_glossary_ai --work <id>`（提术语/人物候选到待确认区）→ 人工审 → `gen_chapter_summaries --work <id>`（章节简介）→ translate_runner
- 短篇集作品：work_meta.json 标 `关联度: "anthology"`
- 知识库文件在 aaManga/ 下（已被 .gitignore 排除）→ **git add -f 强制入库**（知识资产与脚本同等重要）

## 踩坑（实测，防复犯）

1. **registry 配置没有 "id" 键**（id=dict key）→ `cfg.get("id")` 是 None；`os.path.basename(cfg["aa_dir"])` 得 **'aa'**（aa_dir 以 `\aa` 结尾）→ `registry.get('aa')` 报"未知作品: 'aa'"。修复：work_id 从 main(args.work) 显式传入 merge_confirmed。
2. **`--global` 是 Python 保留字** → argparse 变量名 SyntaxError；改 `--to-global` / `args.to_global`。
3. **json.dump 重写 glossary.json 产生万行格式 diff**（indent 1 vs 2）→ 测试回滚脚本动全局 glossary.json 后必须 `git checkout -- glossary.json` 回滚（纯格式、数据无损）。教训：动全局表的脚本保持原 indent 或改完回滚。
4. **CRLF 文件 patch 工具失败** → iter_glossary.py 是 CRLF（read_file 显示正常但实际 \r\n）→ 用 execute_code python 字节级 replace 改写，禁 patch。
5. **iter_glossary import work_memory 缺 sys.path** → work_memory 在 scripts/tools/，iter_glossary 在 scripts/common/，原 sys.path 无 tools → ModuleNotFoundError；补 `sys.path.insert(0, os.path.join(_SCRIPTS, "tools"))`。
6. **work_meta.json json.dump indent=1 造成 543 行 diff**（原 2 空格缩进）——数据无损可接受，但注意 git diff 噪声；尽量保持原格式写。
7. **全量回归前用 `git stash` 对比确认失败是否历史遗留**——本次 9 个失败（test_mixed_segments/test_aaweb/test_chapter_prep 等）stash 后同样失败=与改动无关，零新增失败才算通过。

## 状态验证（实施后实测）

- `sujin_shabu_knowledge.json`：术语 10 / 人物 2 / 章节简介 1 / 待确认(空)
- `work_association`：yaruohiroba→anthology，缺省/非法值→serial
- `gen_chapter_summaries` 试跑 sujin_shabu entry_10073 成功（含本章角色キャル/七実）；增量再跑 skip
- `iter_glossary --merge`：作品专属 2 条→知识库正式块（2026-08-27 审核修后）；`--to-global` 1 条→全局表（测试后已回滚）

## 实施审核待修改项（09238d4，2026-08-27 落地后逐提交核对）

**1.【工作流缺口-大】确认候选落"死区"**：原 merge_confirmed 把人工已 `确认:true` 的作品专属候选写入知识库**待确认**区，但 memory_hint 只读正式块、无工具提升 → 确认过的术语永不注入（死数据）。**修：merge 直接写正式块**（人已在 iter 层确认过；待确认区只收 AI 初稿）。验证：确认词进正式块、待确认区空。

**2.【Bug-中】知识库注入被空术语表吞掉**：`aa_translate.glossary_prompt_hint` 原 `if not pairs: return ""` 在知识库注入**之前**——全局术语按章过滤为空时，作品术语/人物/章节简介整体丢失（翻译+补译都中招；按章过滤后空结果概率上升）。**修：pairs 空时跳过术语段、继续走 memory_hint，不 return**。验证：mock 空 pairs 时知识库+章节简介仍注入。

**3.【偏差-文档追认】按章过滤未按关联度门控**：方案写 serial=全作语料/anthology=按本章，实现为传 base 一律按章（serial 也按章）。语义更优（只注入本章出现的词，省 token 无漏项），**文档追认此实现**：4.1 第 1 条改为"全局术语表按**本章**语料过滤（serial/anthology 一致；base 缺省回退全作语料）"。

**4.【Bug-轻】简介只取第一行**：`gen_chapter_summaries.py` 原 `txt.split("\n")[0]` 把 prompt 要求的 1-3 行简介砍成一行。**修：保留全部行 `[:300]`**。

**5.【小】anthology 简介角色译名无对照**：生成 prompt 让模型自拟角色名，简介内译名可能与标准不一致。**修：生成 anthology 简介时把本章原文命中的人物译名对（知识库 人物 + 全局角色表，`[:20]`）附进 prompt**（`【本章角色译名对照(简介中请使用这些中文译名)】`）。

**6.【记录】commit 失实**：9460bc8 称"修掉只取前20对不按语料过滤旧问题"，实际 proofread 的 `pairs[:20]` 未过滤仍保留（仅追加知识库段）。行为可接受，不改代码。

**7.【另案】AGENTS.md "154 用例全绿"不属实**：基线即 9 个失败（test_aaweb/test_chapter_prep/test_gen_html/test_line_meta/test_pipeline/test_mixed_segments），与知识库无关，需另案清理。

## 知识库设计教训（防复犯）

- **"确认过的数据写待确认区"= 死数据**：任何"人工已确认"的产物必须直接进消费方读取的正式块；待确认区只收未经确认的 AI 初稿。审核员视角：检查数据写入位置是否被读取方覆盖。
- **注入函数的空值分支要谨慎**：提前 `return ""` 会吞掉后续拼接段（术语空 → 知识库全丢）。段式拼接（`out=""` + 各段 `if` 追加）更安全。
- **按章过滤是全局优化**：serial/anthology 一致按本章语料过滤（比按全作更精确、省 token），不要按关联度门控过滤粒度——关联度只门控人物简介注入。

## 后续接入与验证（2daea3c / 1f710ce，2026-08-27 当日）

**translate_runner 1.6 翻译前自动生成章节简介**：chapter_prep 后插入 `gen_chapter_summaries(work, force=False, chapters=本次eids)`（增量 skip 已有键、失败不阻塞、only_cleanup 跳过）。日志验证：`=== 章节预处理 (chapter_prep) ===` → `=== 章节简介 (gen_chapter_summaries) ===` → `=== 翻译 (aa_translate) ===`。**已译章只走 only_cleanup（不触发 1.6），未译章才自动生成**——存量作品不重译，靠清理/校对吃到知识库。

**zhipu 全流程验证（sujin_saigo，r18_level=low）**：`[auto-backend] low/章节级判定 -> zhipu/glm-4.5-air(免费, prep驱动)` → prep → 简介生成 → 翻译 → 清理(0错位) → iter(6候选) → 校对。108s 全链路跑通，产出汉化 txt + 知识库 2 简介。**r18_level=low 的 yes 章 zhipu 优先免费，1301 自动降级 r18 链**——章节简介生成也走同链（`gen_summaries` 按 work_meta r18_level 选 work_r18，low 走 normal 免费）。

**usage 统计盲区（opencode_zen）**：`aa_backend.call_*` 全部有 `_log_usage`，**唯独 `call_opencode_zen` 没有**（直接 urllib 返回 msg）——FREE_MODELS 里 mimo-v2.5-free/hy3-free/big-pickle 走 opencode_zen，消耗完全漏统计（usage.jsonl 该后端 0 条）。**已修**：响应里读 `resp.usage`（OpenAI 兼容）补记 `_log_usage(model=..., backend="opencode_zen")`。排查法：`grep -o '"backend": "[^"]*"' usage.jsonl | sort | uniq -c` 看各后端记录，0 条的后端=漏记。

**work_meta 字段检查教训（用户纠正）**：`json.dumps(meta[work], ensure_ascii=False)[:200]` 截断 200 字符导致误判"未设 r18_level"（实际 low 在截断后）——**查 work_meta 关键字段必须完整 dump 或按 key 读**（`meta[work].get("r18_level")`），勿截断下结论。

**免费模式慢的坑**：AA_FREE_MODE=1 翻译 1 章 100s+ 无产出（免费链轮询+退避慢）——**测试新流程用 zhipu（normal 链首选）而非免费链**，快且免费（sujin_saigo 108s 全链路 vs 免费模式 100s 还在翻译阶段）。

## 章节简介中文约束（fc412c1，2026-08-27）

**事故**：SUMMARY_SYSTEM 要求"角色名保留日文原名（如 やる夫/キャル）"——zhipu/glm-4.5-air 误解成**整段输出日文**，sujin_saigo/otona 的章节简介全变日文（污染翻译注入）。

**修复**：
- prompt 改"角色名用**简体中文译名**（やる夫→亚鲁欧、キャル→凯露），不知道译名才保留日文；整段必须简体中文"——zhipu 实测完美输出中文
- 生成后**中文校验**：`re.search(r'[ぁ-んァ-ヶ]', s)` 含假名 → 重试 1 次（追加"必须输出简体中文"强调）→ 仍日文**丢弃不注入**（fail_n++ 不落库）
- 存量重生成：sujin_saigo 2 章 + sujin_otona 5 章 force 重生成中文，0 日文残留

**教训**：给中文任务模型写 prompt，"X 保留日文"这类豁免会被放大成"整段日文"。要么角色名也要求中文译名（推荐），要么明确"除角色名外全部中文"并加输出校验兜底。**任何注入翻译的 AI 生成内容都要有语言校验**。

## 开工前人物（素材）向导（e10ea0c，2026-08-27）

用户需求：开工前遍历全部章节原文 → 提取人物（素材）清单 → 人工确认 → 作为翻译初始向导（"人物应该在开始时遍历全部后再人工确认然后执行之后的流程"）。

**实现**（改 gen_glossary_ai，复用现有，未新建脚本）：
1. `--full`：`collect_all_batches(aa_dir, batch=4000)` 遍历全部章节原文，**艺术行过滤（台词口径）**：非空白字符里 汉字+假名占比<0.4 / 艺术符占比≥0.3 / 重复字符占比≥0.5 → 跳过（参考 export_dialogue src 过滤；otona 5章 44批→3批，省 token）→ 分批 AI 提取 → 聚合去重（同 term 保留首个，累计来源文件）
2. 候选写 `work/glossary_candidates.json`（默认 `确认:false` + 来源）
3. 人工编辑设 `确认:true` → `--promote`：角色/人名类型→知识库 `人物`，其余→`术语` 正式块（跳过 FUNC_WORDS），从候选移除已提升项
4. 翻译时 memory_hint 读正式块注入——**确认前不注入**（待确认区只收 AI 初稿）

实测 sujin_saigo：8 批提取 **97 候选**（角色32/专名14/术语44/拟声7），角色带中文译名建议（鳳翔先生/雷/洩矢諏訪子/きりたん）；确认 3 项→知识库（雷/きりたん/婚姻義務），翻译注入闭环验证通过。

**与工作台素材统计的关系**：`logic.material_frequency` 是**基于已知素材名**（names 参数，来自 work_meta.materials/角色表）在**已汉化文本**统计出现次数——用于展示；`--full` 是**从原文提取未知人物**——用于开工前建初始清单。互补：开工前 `--full` 建人物 → 翻译中/后工作台统计展示。可后续把 `--full` 确认的人物同步到 work_meta.materials（工作台素材列）。

**promote 计数 bug**：初版打印 `术语+{0}` 写死——修复为 n_t/n_p 分别计数。

## 角色候选/人物带作品字段（85d137b，2026-08-27）

用户需求：角色类型添加「作品」项——AI 提取时识别角色出身的外部 IP，promote 进知识库时带上。

**改动**（gen_glossary_ai.py 三处）：
1. **SYSTEM_PROMPT**：角色类型必须输出 `作品` 字段，格式示例 `{"term":"キャル","translation":"凯露","type":"角色","作品":"公主连结"}`；**原创角色/やる夫系原创 作品留空 `""`**——AI 会把「やる夫系AA原创」当作品填（噪音），prompt 必须明确排除类目名
2. **parse_terms**：解析 `作品` 字段（`str(it.get("作品") or "").strip()`）
3. **promote / 待确认区写入**：角色进知识库人物值带作品 `f"{tr}({_w})" if _w and _w not in tr else tr`（如 `灵梦(东方Project)`）；**已存在人物且新候选带作品时补作品**：`elif _w and "(" not in people.get(t, "")` → `people[t] = f"{people[t]}({_w})"`

**实测**：洩矢諏訪子→东方Project、霊夢→灵梦(东方Project) 正确；原创角色（鳳翔先生等）作品留空。

**坑**：
- `--full` 重跑时已在知识库的词被 `load_existing` 去重跳过 → **不会再次进候选**；已存在人物想补作品，靠新候选 promote 或**手动编辑知识库**（`"泄矢诹访子"` → `"泄矢诹访子(东方Project)"`）
- 角色作品字段口径与全局 glossary.json 角色表 `作品` 一致 → promote 后可打通工作台 `material_display_parts`（已支持「名（作品）」解析）

## 外部 IP 角色补全局 + VOICEROID 短名规范化（68e0fb7 / 42018c0，2026-08-27）

用户确认的 3 部新作品：**FGO / 舰队collection / 火影忍者**（加已确认的东方/舰C角色）；VOICEROID 短名映射萌娘百科规范全名。两条流程合起来是「开工前人物向导」的收尾：**确认的角色既进作品知识库（作品层），也补全局 glossary.json（跨作复用）**。

**规范译名（萌娘百科实查）**：結月ゆかり→**结月缘**、東北ずん子→**东北俊子**、東北きりたん→**东北切蒲英**、東北イタコ→**东北伊达子**（全局已入，用全名；作品知识库用短名对照）。

**作品层（sujin_saigo_knowledge.json）VOICEROID 短名校正**（promote 后手动规范化）：
- `ゆかり → 结月缘(VOICEROID)`（用户纠正：ゆかり=缘，且规范为结月缘；AI 原标 VOCALOID 错）
- `ずんこ → 东北俊子(VOICEROID)`、`きりたん → 东北切蒲英(VOICEROID)`（修正 AI 错误「基里坦」、短名「切蒲英」→全名）、`イタコ → 东北伊达子(VOICEROID)`（从误识别「巫女」术语纠正为人物）

**全局 glossary.json 补 9 角色 + 3 作品**（脚本 `json.dump(..., ensure_ascii=False, indent=1)`）：
- 新增作品：`fgo`(Fate/Grand Order)、`kancolle`(舰队Collection)、`naruto`(火影忍者)；东方用已有 `touhou` id
- 新增角色（带作品 id）：舰C凤翔/雷/雷酱、FGO贞德/安心院、火影大蛇丸、东方灵梦/泄矢诹访子/青蛙子
- VOICEROID 4 个全局已有（全名），**不重复**——查重逻辑 `any(r.get('日文')==jp for r in roles)`

**git 提交前零删改校验**：`git show HEAD~1:glossary.json` vs `git show HEAD:glossary.json` 用 python 对比 `{r.get('日文') for r in 角色}` 集合——**必须确认纯新增（删除=无✓、glossary 对不变=57）**。json.dump 重写产生万行 diff 是缩进噪音，靠**集合对比**（而非 diff 行数）确认数据层干净。

**外部 IP 角色补全局的价值**：后续任何作品出现这些角色（舰C舰娘、东方角色、火影、FGO），全局表自动匹配 + 翻译过滤注入，跨作复用，不用每作品重复建。
