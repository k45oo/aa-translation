# 台词提取公共模块 extract_dialogue.py（2026-08-29 第1步落地）

收编 export_dialogue / gen_chapter_summaries / gen_glossary_ai 三套旧占比启发式为
`scripts/common/extract_dialogue.py`，统一到 **v3 meta（line_classify_v2）** 单一口径。
先读 `references/dialogue-extraction-benchmark-2026-08-29.md`（旧启发式漏识 66.6% 的根因），再读本文件。

## 权威判定源 = v3 meta（非 line_kind）

- `line_classify_v2.classify_raw_v2(site, raw, txt_lines)` → `{行号: {kind,confidence,reason,segments}}`
  - kind: `text`(整行台词) / `mixed`(左art右台词) / `art` / `header` / `blank`
  - mixed 行带 `segments` = `[{start,end,kind,src_width,(confidence)}]`（半开字符区间，覆盖整行无空隙）；text/art/header 行 `segments=None`
  - **非 mixed 行 segments=None**，mixed 才生成
- **行号口径**：meta key 是 **1-based 物理行号从 3 起**（0/1 是 `#` 标题/`#` 地址行）。`aa/<章>.txt` 是 0-based list → `raw = txt_lines[key-1]`。meta 键覆盖 txt 全行时行号精确对齐。
- meta 已存在（`<work>/line_meta_v2/<base>.json`）就不再生成；缺失→自动生成（零 API，`_ensure_meta` 内联 classify_raw_v2）；**raw HTML 缺失→抛 FileNotFoundError 中断**（禁静默降级，与「识别失败即中断」硬约束一致）。⚠️ `line_kind`（aa_translate.py:542）是**宽度校验口径**，信息量低（无段边界/置信度），**不作台词提取依据**。

## 关键修正：含真实假名门禁（用户拍板，推翻设计文档「全提取」）

设计文档第一轮写「conf<0.65 低置信段全部提取，补翻译管线跳过的段」——**实测数据推翻**：

- 抽样 entry_17568：mixed 低置信段(conf<0.65) 347 个，其中**仅 24 个(6.9%)含真实假名**（可能是真台词），其余 93% 是纯艺术碎片（`ミ`/`ヽ`/`メメメ`/`｝メ`/`ノ`——segment_line 的 `treat_hankaku_as_text` 把楼主帖半角片假名误判为 text 段，`_seg_confidence` 因此给 0.55）
- **切勿把全部低置信段当台词提取**——会让台词表 93% 是艺术碎片
- **门禁 `_has_real_dialogue(text)`**：段/行内至少 1 个**真实假名或非艺术汉字**才算台词（`KANA_RE` 匹配且不在 `ART_HIRA`/`ART_KATA`/`FACE_KANA`；或 `\u4e00-\u9fff` 且不在 `ART_KANJI`）。`text` 行整行判，`mixed` 段逐段判。复用 `_seg_confidence` 的 real 假名语义，勿自造判定漂移。

## 提取逻辑

- `kind=text` → 整行一段台词（经 `_has_real_dialogue` 门禁）
- `kind=mixed` → 按 `segments` 取**每个 text 段**（经门禁），段号 `_0/_1/...`
- `kind=art/header/blank` → 跳过
- 输出 `L<行号>[_<段号>] [主/用] <段文本>`；段文本**不 strip**（保留行首 U+3000，段级拼回需）；记录 `seg_start/seg_end` 偏移供按行号/段字节级回填
- `[主]=楼主`(#/◆ 帖头)，`[用]=用户楼`，识别失败 `[?]`；楼主作者名从 `work_meta.json` 的 `author`（多马甲逗号分隔）读，`op_names` 传 `parse_op_header`

## CLI

```
python scripts/common/extract_dialogue.py --work <id> [--src] [--overwrite]
```
`--src` 输出到 `aa_dir/`（原文台词），否则 `out_dir/`（汉化台词，基于译文文件）。meta 缺失自动补，raw 缺失中断。

## 实测数据（r401_ryouri 39 章，换用新模块后）

- 全 39 章 **8082 台词段**，章均 ~207 段（旧 export_dialogue 台词表 ~123 行/章 → **新量约 1.7 倍**，正是补上的漏词台词）
- 抽检 entry_17568 新表：`L111 なんだこの鮎は！？` / `L126 値段相応の...` / `L138 尾っぽやヒレにたっぷりと` / `L207_1 へぇ～立派な子持ち鮎...` —— **全是旧启发式漏掉的 mixed 行右侧台词**，现全补上
- **残留可接受**：全站极短(≤3字符)且无假名段 415 个(5.1%)，如 `回`/`卞`/`森森森`/`汢汢`/`橆橆`（非 ART_KANJI 的艺术汉字/效果字/背景纹理）。单汉字(`中`/`大`)可能是短台词可能是画面字，**无法用门禁完全区分**——5.1% 残留可接受（精译/漏译扫描看到 `森森森` 会视为效果字跳过，过度过滤会误伤真单字台词）

## 踩坑

- **import 顺序 bug**：`from line_classify_v2 import classify_raw_v2, _serialize` 若放在 `os.path.exists(path)` **之前**，即使 meta 已存在也会先 import → ModuleNotFoundError（meta 全在却报错）。**必须** `if os.path.exists(path): return path` 在前，需要生成时才 `import`（及 `sys.path` 注入 tools/）。
- `line_classify_v2` 在 `scripts/tools/`，CLI 运行时 default sys.path 不含 tools → 需注入 `_this_dir/tools`、`_this_dir/common`、`_this_dir/..`。
- `_iter_bases` 排除 `_台词.txt` 后缀（aa_dir 里已有旧台词文件），否则 base 里带 `_台词` → meta 路径不存在 → 误触发生成。

## 2026-08-29 第四轮：帖头行未跳过 bug 修复 + 门禁过松诊断（yukika_robot entry_2641）

### 帖头行无条件跳过（bug 已修）
- **症状**：FC2 系（`yarumo_fc2` 等）帖头行 `N 名前： ◆/H7wY7ybL2[] 投稿日：2011/02/25(金)...` 被 `line_classify_v2` 误分类为 `text`/`mixed`（conf 0.92/lz_pure_text），导致 `extract_dialogue_from_meta` 把它们当台词提取（entry_2641 实测 31 处）。帖头行的 `名前`/`投稿日` 汉字触发了 `_has_real_dialogue` 的汉字分支。
- **根因**：原实现 `if is_hdr: cur_op=…; if kind=='header': continue` —— **只在 `kind=='header'` 时跳过**。分类器把这些帖头行标成了 text/mixed，`kind` 不是 `'header'` → 不跳过 → 帖头当台词。
- **修复**：`is_hdr` 为真**无条件 `continue`**（帖头行物理上不是台词，只更新 `cur_op` 楼主归属，不依赖 `kind`）。修复后 entry_2641 294→272 段。
- **教训**：识别"这行该不该跳过"用**语义判定结果**（`is_hdr`），别额外依赖分类器的 `kind`——分类器对楼头/帖头这类行容易错标，`kind` 不可靠，应直接以行内容判定为准。

### ⚠️ 第五轮（2026-08-29 续）：62% 误报的**真正根因 = 坏 meta 数据**，门禁并非过松

**上文第四轮把 yukika_robot 的 62% 误报归因于 `_has_real_dialogue` 门禁过松——这个诊断是错的，本会话纠偏**。实测重新诊断后确认根因是 **`line_meta_v2/entry_2641.json` 是 2026-08-28 生成的坏/过期数据**，而非门禁缺陷。

**证据链（诊断方法）**：
1. 直接调 `line_classify_v2.classify_content(L36_raw)` → `('text', 0.92, ['lz_pure_text'])`，L238/L42 同理判 `text`、L241 判 `mixed`。**分类器逻辑本身是对的**。
2. 但磁盘 `line_meta_v2/entry_2641.json` 里 L36 却是 `art`（`reason:['lz_text_lt5']`）——L36 明明是 22 字纯文本却标 `lt5`（<5 字），**行号与内容错位**（分类器当时看到的是别的内容）。
3. 重新跑 `line_classify_v2 --work yukika_robot --chapter entry_2641`（**零 API**）→ 新 meta L36/L42/L238=text、L241/L102=mixed、L3 帖头=header，全对。行数 6179（旧 5346，旧版差 833 行 = 漏了 blank 行）。

**修复后效果（重新生成 meta + 重跑 extract_dialogue）**：

| 指标 | 坏 meta（修复前） | 重新生成 meta（修复后） |
|---|---|---|
| 台词段总数 | 272 | **771**（text 309 + mixed 462） |
| 艺术误报 | 169（62%） | **0** |
| 帖头误报 | 有 | **0** |
| L36/L238/L102/L241 真台词 | 全漏 | 全部正确提取 |

**工作台高亮也连带修正**：工作台 reader_page 的 `is_dlg`（`KIND2TYPE.get(mk)` + gold）同样读这份 meta——坏 meta 下 L36 被判 art → 工作台不显示台词高亮；regenerate 后 L36 变 text → 工作台 `✦台词纠正` 正确高亮（且后台 gold_dialogue.json 同源，两者一起变对）。**同源判定 = 修一处，台词表 + 工作台高亮 + gold 一起修好。**

**方法论铁律（本会话最大教训）**：
- 台词提取结果异常（大量艺术误报 / 大量漏词）时，**第 1 步永远先怀疑 `line_meta_v2` 数据是否坏/过期**，用 `classify_content`（单行）或直接 `--work <id> --chapter <base>` 重新生成对比。**别急着改门禁/改 extractor**——门禁调优 3 候选那个分析是建立坏数据上的无效功。
- meta 是 2026-08-28 生成的（那段时间 yukika_robot 恰好有过 commandcode 崩溃事故）——**半成品/异常中断的任务残留的 meta 可能是坏的**，重跑前先 regenerate。
- **验证 meta 是否权威**：任抽一个纯文本长台词行，`classify_content` 单行 kind 与磁盘 meta kind 比对，不一致 = meta 坏。
- 参考 `references/classifier-mislabel-audit-2026-08-12.md`？不——那在 aa-web-review-app 的 SKILL.md 正文。本条与第四轮「帖头行 62% 误报」一起构成 complete 链：**先 regenerate meta 排数据问题，再谈门禁**。

### 第六轮（2026-08-29 续）：meta 修好后，用 gold 做 A/B 回测调门禁（含用户偏好）

**前提**：坏 meta 已 regenerate，误报 169→0。但用户手动纠正发现了**更深的两类残余误判**（yukika_robot entry_2641 人工 gold 106 条：误标 false 94 + 漏标 true 12）——**meta 即使"正确"（classify_content 正常判），仍有大量分类偏差**。此时才真正进入**门禁调优**。

**用 gold 当 ground truth 做 A/B 回测（方法论，高复用）**：
- gold 106 条 = 用户已人工确认的真相。回测 = 对每条 gold 行，跑「现行分类器」vs「候选改进分类器」，量 **误标拦截率**（该变 art 的变没变）和 **漏标保留率**（该不变的变没变）。
- **输出三档对比表**（误标拦截 / 漏标保留 / 真台词误伤）——真台词误伤是**否决线**（宁漏勿伤，见下）。
- 回测脚本用 `sys.path` 注入 tools/common 后 `from line_classify_v2 import classify_content, _max_real_dialogue_run, ART_KANJI`，对 gold 行号 `row=src[ln-1]` 重新判定，对比磁盘 meta。

**误判两类相反方向（同一根因的两面，无法靠一个阈值统一修）**：
| 类型 | 数量 | 表现 | 根因 |
|---|---|---|---|
| 误标（false） | 94 | 纯艺术装饰被当台词 | `is_art_char` 对假名一律放行（`ミミ/ヘメ/ソ`）、`ART_KANJI` 只 77 字漏装饰汉字（`穰鬱懶/三ニニ/て芯ミ/ァトミ`） |
| 漏标（true） | 12 | 真台词短句/拟声被当非台词 | `lz_text_lt5`（txt<5 整行降 art）、`no_dialogue`（run<2）、**`_max_real_dialogue_run` 不认半角片假名**（`ｷｭﾑｷｭﾑ/ｹﾞｾﾇ/ﾌﾟｼｭｰ` run=0） |

**候选规则 A/B/C 对比（entry_2641 gold 实测，这是淘汰的关键证据）**：
| 方案 | 误标拦截 | 漏标保留 | 真台词误伤 |
|---|---|---|---|
| 现状（磁盘 meta） | 11/94 | 12/12 | 0 |
| **装饰字符黑名单 + run≥2 + 支持半角**（胜出） | **69/94** | **9/12** | **0** ✅ |
| text_ratio ≥0.35 | 90/94 | 7/12 | **高（误伤 `おとうさま`/`むかしむかし`/`同じ過ち`）** ❌ |
| sem_word（常见词判定） | 65/94 | 4/12 | 高（漏标严重） ❌ |

**「text_ratio」方案被否决的教训**：台词行前半常是密集 art 边框（`:::::`/`|`），把 text 占比拉低到 <0.35 → 真台词 `おとうさま！`（run=5, ratio=0.14）被降 art = 音像级误伤。**占比/阈值类启发式在该站不可靠**——画幅密度不均导致 ratio 分不开装饰与真台词。

**胜出规则（装饰字符黑名单 + `_max_real_dialogue_run` run≥2 + 半角片假名支持）**：
- `DECOR` 黑名单收**明确无意义装饰字**（`穰鬱懶彡乂弋辷州屯鬯阝` 装饰汉字 + `ミヘメソ三ニヨソミヘハイケ` 装饰假名 + 既有 `FACE_KANA`）——只收**几乎不可能是台词词**的，不收可能成词的（`そ`/`し`/`く` 等单字假名**不收**，否则过拟合单作品误伤 `そう――`）。
- `_max_real_dialogue_run` 支持半角片假名（非纯装饰半角 `ｦｧｨｩｪｫｬｭｮｯｰ､ﾞﾟ` 才算），修复 `ｷｭﾑｷｭﾑ`/`ｹﾞｾﾇ` 漏标。
- 效果：误标 69/94、漏标 9/12、**真台词 0 误伤**（`おとうさま`/`むかしむかし` 等 run 全≥2 保留）。

**⚠️ 用户偏好（必须遵守，且明确拍板）**：「**短文本拟声词漏标难以避免，优先确保少误标**」——即**宁漏勿伤**：允许漏标（`ひ、あ、あ`/`（あ……）`/`ﾌﾟｼｭｰ` 这类散落/半角拟声可丢），但**绝不允许把真台词误伤成非台词**（后者会漏译）。所以任何候选方案，**真台词误伤 >0 即否决**，误标拦截率是次要目标。

**方法论铁律（第六轮补充）**：① 调门禁前必须**先 regenerate meta 排除数据问题**（第五轮）——否则在坏数据上做 A/B 是无效功；② meta 修好后用**用户 gold 当 ground truth** 做 A/B，别凭空设计规则；③ 候选方案必须同时报**误标拦截 + 漏标保留 + 真台词误伤**三轴，其中**真台词误伤是硬否决线**；④ 纯规则有极限（误标拦不到 100%、漏标短拟声必漏），**接受正确率与召回的两难，按用户偏好（宁漏勿伤）选最优平衡点**，别追求理想值而过拟合单作品。

---

### 第七轮（2026-08-29 终盘）：门禁方案 A 落地 + 卷积借邻行尝试与放弃（本会话核心）

第六轮选定的「装饰黑名单 + run≥2 + 半角支持」正式落地到 `line_classify_v2.py`（commit `c484e090`）。**落地两处关键**：

**① 装饰黑名单纳入 `is_art_char`（治本，不止改 run 函数）**：只改 `_max_real_dialogue_run` 的 DECOR 排除**不够**——`classify_content` 的 `txt` 计数用的是 `is_art_char`（装饰假名 `ミヘメ` 仍被判 text → `txt` 虚高 → 不触发 `txt<5` → 仍判 text/mixed）。**要把 DECOR 集合同时纳入 `is_art_char`**（装饰字符判 art），才能让 `txt` 计数不虚高、触发降 art。这是"两处联动"的坑——改 run 函数只拦 `no_dialogue` 分支，拦不住走 `lz_mixed`/`lz_dotted` 的装饰行。

**② 短台词保留（`lz_text_lt5` 不再整行降 art）**：`txt<5` 时若 `_max_real_dialogue_run>=2`（已排除装饰后仍有连续真对话）→ 保留 text（`lz_short_dialogue`），否则才降 art。救回 `そう――`/`でも……`/`蒼い、空`/`命令認証`/`ｷｭﾑｷｭﾑ` 等短台词。

**落地后实测（entry_2641 106 gold）**：误标 52/94（基线 11，提升 4.7 倍）、漏标 8/12、真台词 0 误伤（`おとうさま`/`むかしむかし`/`同じ過ち` 全保留）。全站 30 章重生成 meta，工作台 `entry_2687` dlg-line 2051、`entry_2912` 短台词（`そうだ、あの子と遊んであげよう`/`それは「母」の声`）正确提取。

#### 卷积借邻行（用户提出，±2 窗口 + 长度判断 + 降边缘）——尝试后**放弃**

用户要求用卷积思路（借邻行上下文，仅楼主楼内不跨楼）：单行判 art 时，若邻行构成"台词区"则提升为 text。我实现了 `_classify_lz_conv` + `_conv_row_is_dialogue`（窗口 ±2、±1 权重 2 / ±2 权重 1、邻行 `true_dialogue_run>=2` 加权 strength≥3 才借力）。**实测证明卷积对"少误标"适得其反**：

| 场景 | 误标 | 漏标 | 说明 |
|---|---|---|---|
| 纯黑名单（已落地） | 44 | 9 | 基线 |
| + 卷积借邻行 | **47-52** | 10 | 卷积救回漏标 **+2，但误标反弹 +5**（借邻行放回装饰行）|

**根因（数据驱动，`ab_conv_why` 脚本按"自身run vs 借邻行"分类）**：误标 47 条里 **42 条是自身 `run>=2`**（`ミミ`/`ヘメ` 装饰本身 run 就≥2，单行/卷积都拦不住，**跟卷积无关**），**仅 5 条是借邻行引入**（`て芯ミ`/`アミゝく` 等 kana≥2 的装饰行邻接台词区被救回）。而漏标大多 `run>=2`（`ｷｭﾑｷｭﾑ` 修半角后 run=2、`ｶﾞﾋﾞｰﾝ` run=3）——**半角修复后真台词单行 run 已≥2 可识别，卷积的"补漏标"价值被半角修复**（第三处改动）**冗余掉了**。卷积只留下加误标的副作用。

**结论：移除借邻行**（`_classify_lz_conv` 保持单行 `classify_content`，不借邻行）。卷积框架（函数）保留备用，但**借邻行逻辑删除**。移除后：entry_2687 误标 640→**449**（-191，直接解决用户"mix 台词行附近误标变多"的反馈），漏标不变。

**方法论教训（卷积类方案泛化）**：① 卷积/邻行上下文对**补漏标**有益（邻行台词区能救回散落短台词），但对**少误标有害**（邻行装饰区也借力放回装饰行）——**与用户"宁漏勿伤"偏好相反的操作方向**；② 卷积救不了"自身 run 就达标的装饰行"（`ミミ`/`ヘメ`），这是黑名单边界不是卷积能解决的；③ **先修好单行能修的（半角/黑名单/短台词），再看还需要不需要卷积**——本会话半角修复把卷积价值吃掉了，卷积纯负收益；④ 用户接受"短拟声漏标难避免"（`（あ……）`/`ﾌﾟｼｭｰ`/`ｷｭｯ` 这些纯艺术边框包围的孤立拟声，邻行也无台词区，卷积也救不回——本质边界）。

#### 半角拟声 run 深度修复（本会话独立 bug，量大收益）

`_max_real_dialogue_run` 对**半角片假名拟声词的 run 计数**系统性偏低，导致 `ｷｭﾑｷｭﾑ`/`ｶﾞﾋﾞｰﾝ` 漏标。根因是**半角浊音/拗音/促音被当断点**：

- 修复前：`ｷｭｯｷｭｯ` run=1、`ｶﾞﾋﾞｰﾝ` run=1、`ﾌﾟｼｭｰ` run=1、`ｷｭﾑｷｭﾑ` run=2
- 修复后：`ｷｭｯｷｭｯ` run=2、`ｶﾞﾋﾞｰﾝ` run=3、`ﾌﾟｼｭｰ` run=2、`ｼｬﾊﾞｼｬﾊﾞ` run=4、`ｷｭﾑｷｭﾑ` run=4

**根因（`KANA_RE = re.compile(r'[ぁ-んァ-ヺー]')` 只匹配全角假名）**：半角片假名（`ｷ`=FF77）不在 `ァ-ヺ` 范围，走独立半角分支。但半角浊音 `ﾞ`(FF9E)/半浊音 `ﾟ`(FF9F)/长音 `ｰ`(FF70)/拗音小假名 `ｧｨｩｪｫｬｭｮ`/促音 `ｯ`(FF6F) 这些**独立码位**被当"纯装饰"排除或当断点 → 拟声词的 run 被切成 1-2。

**修复（关键：紧跟前假名时继承 run，不断不自己起 run）**：
```python
if c in 'ﾞﾟｰ':            # 浊音/半浊音/长音：词的延续，继承
    continue
if c in 'ｧｨｩｪｫｬｭｮｯ':      # 拗音小假名/促音：紧跟前假名(cur>0)时继承，独立才是装饰断点
    if cur > 0:
        continue
    cur = 0
    continue
```
要点：① `ﾞﾟｰ` 一律继承（`ｶﾞ`=ｶ+ﾞ、`ﾋﾞｰﾝ` 的 `ｰ` 长音是词尾）；② 拗音/促音**只有 current run>0 时才继承**（`ｷｭ`=ｷ+ｭ 拗音、`ｷｭｯ` 促音），独立 `ｯｯｯ` 横排才是装饰断点——**progressive 特征，不能用黑名单一刀切**（`ｯ`/`ｭ` 既是拟声词成分又是装饰字符）；③ 全角 `ー`(30FC) 仍是断点（艺术面板横线），半角 `ｰ`(FF70) 继承——**两者 U+ 不同可区分**，勿混淆。

**方法论**：半角假名（半角片假名是 AA 拟声词的载体）的 run 计数，**浊音/拗音/促音/长音必须按"词内延续"处理**，绝不能当独立断点——否则 `ｶﾞﾋﾞｰﾝ`/`ｺﾚｶﾞｹﾞｲｼﾞ` 这类标准拟声词全部 run≤1 必漏。

### 第八轮（2026-08-30）：三套口径统一收编完成（原流程反哺第②节落地, commit ecf1ab74）

第七轮"遗留"的收编已**完成**。统一接口设计（`extract_dialogue.py` 新增）：

- **`parse_dialogue_file(path)`** → `[{line_no, seg_idx, op, text, raw}]`，用 `_DLGLINE = re.compile(r'^L(\d+)(?:_(\d+))?\s+\[(主|用|\?)\]\s?(.*)$')` 解析 `L<行号>[_<段号>]` ——**这就是解决"下游正则不兼容"的正解**，不是去改各家正则，而是让所有消费方走同一 parse 函数（兼容段号）。`text` 不 strip（保留行首 U+3000 供段级拼回），`op` 的 `?` 转 None，`raw` 保留原行。
- **`ensure_dialogue_file(cfg, base, out_dir=None, src_only=False, overwrite=False)`** → 确保目标 `_台词.txt` 是 v3 meta 口径（已存在且未 overwrite 则复用；缺失自动 `_ensure_meta` 生成；raw 缺失抛错中断）。`src_only=True` → `aa/`（原文台词，gen_glossary/gen_summaries 用）；`src_only=False`+out_dir → `out/`（汉化台词，export_dialogue 用）。
- **① `export_dialogue.export_work` 收编**：删 128 行自身占比启发式，循环调 `ED.ensure_dialogue_file`（透传 overwrite）；保留 `[?]` tag 占 >10% 楼主识别失败中断检查（`parse_dialogue_file` 后 `op is None` 计数）。`export_dialogue.is_op_header` 保留（公共函数别处还用）。
- **② `gen_chapter_summaries._chapter_dialogue` 收编**：删占比启发式循环，读 `aa/<base>_台词.txt`（`ED.ensure_dialogue_file(src_only=True)` 兜底 + `ED.parse_dialogue_file` 解析）。保留 `[主/用]` tag 与 `L<行号>`（简介列出场角色用，段号丢弃无妨——简介是概括性任务）。
- **③ `gen_glossary_ai` 的 `collect_sample`/`collect_all_batches`/`freq_fragments` 三处收编**：删各自占比启发式，改读 `aa/<base>_台词.txt`（`parse_dialogue_file` 后 `e["text"]`）。**`A.kana_runs(e["text"])` 对带 L 前缀的行也能提干净**——`L`/`主`/`]`/空格不进 `[ぁ-んァ-ヶ]+` 匹配，无需先剥前缀。
- 修改前先 grep 全部 `_台词.txt` 读取方（已确认 6 处：aa_translate/fix_width/scan_width/translate_runner/fix_missed_lines/proofread——后两者用自身正则 `L(\d+) \[(主|用|\?)\]` 仍不兼容段号，但它们主要读 text 行台词，mixed 段号行的影响在后续接入漏译扫描时再处理）。

**三套口径存量（已统一为第1套）**：v3 meta（extract_dialogue 正确，唯一）/ export_dialogue 占比启发式（第2套，已删）/ gen_glossary_ai+gen_chapter_summaries 占比启发式（第3套，已删）。

**验证（零 API 回归）**：全量 195 单测绿（skipped=1 质量闸门）；`export_dialogue --src r401_ryouri` 39 章 **8082 段**与 extract_dialogue 一致；`freq_fragments` 高频片段干净（`から`/`です`/`さん`/`その` 等真词，无艺术碎片——旧启发式混入装饰）；`gen_summaries entry_17568` 133 段正常。**收编的意义**：mixed 行右侧台词在简介/术语/频次统计全都能抓到（旧占比启发式 per-line 漏掉），且消灭"改一处台词判定要动三处"的冲突。

**方法论（统一接口收编模式）**：① 看到多套"同功能启发式"散落各消费方 = 该抽公共模块收编，用**单一认证入口 + parse 函数**让所有消费方过同一解析；② 消费方改读公共产物时**先 grep 产物读取方**（尤其正则不兼容的风险点），统一用公共 parse 函数而非各自改正则；③ 收编后跑**零 API 回归**（单测全量 + 真数据产物数量/内容比对）确认无回归。

### 第九轮（2026-08-30）：收编后原流程端到端实测（r401_kimyou 1-3 话 zhipu 试译）

**为什么要真跑一遍**：单测全绿 + 单模块产物比对 ≠ 收编后的模块真的接进了**生产主流程**。`translate_runner` 是生产入口，它会自动调用 gen_chapter_summaries（收编后）产 meta/台词表/简介，再进 aa_translate 翻译——**这是唯一能证明"收编没把原流程跑挂"的验证**。所以收编后用 `scripts/runner/translate_runner.py --work <id> --chapters 1-3` 做一次小规模真实翻译（r401_kimyou，未译新作，zhipu normal 链首）。

**实测结论（改动后原流程可用，关键观察点）**：
- **自动选后端**：`[auto-backend] r401_kimyou -> zhipu / glm-4.6v-flashx`（normal 链首，r18=False 作品）——收编不影响后端选择（那是另一条链，见「翻译前模型预判」）。
- **收编模块在生产流里活起来了**：translate_runner 跑 chapter_prep → gen_chapter_summaries 时，自动触发 extract_dialogue 的 `_ensure_meta`（meta 自动生成）+ `ensure_dialogue_file`（**原文台词表 `aa/<章>_台词.txt` 自动产出**，350/288/352 段）→ 简介正常生成（中文 1-3 行/章）。**这正是收编后 extract_dialogue/gen_summaries 在真实翻译流里工作生效的直接证据**（收编前是各自占比启发式）。
- **翻译质量验证无回归**：行数对齐（1583=1583）、art 行保留（`!:::::{}`/`∨::∨:从=` 原样）、台词译对（`ワーイジョジョ→哇—JOJO`、`ゾンビ→僵尸`、`クリティカル→大成功`）、长句译全。
- **假死判断（区分真卡死 vs 正常等网络）**：进程 CPU 0.03（几乎空闲）+ 无 zhipu 域名连接 + **ckpt 检查点在持续产出译文** = 正常等 API 响应，不是卡死。**判断活性看 ckpt 是否推进 / usage.jsonl 尾部时间戳，别只看 CPU/网络**（zhipu 走代理域名时 netstat 看不到 bigmodel.cn，误判"没连上"）。
- **前台跑会超时**（180s/600s 上限），翻译长任务必须 `background=true + notify_on_complete=true` 后台跑，别前台等。

**方法论（收编后必做端到端验证）**：收编/重构公共模块后，除单测 + 单模块比对，**一定要用生产入口（translate_runner）跑 1-3 章真实翻译**，确认"消费方真的调用了新模块"而非还在用旧启发式——单测能绿但生产可能仍走旧路径（双版本漂移事故的教训，见 SKILL.md「双版本漂移」）。验证产物：meta 自动生成日志 + `aa/<章>_台词.txt` 自动产出 + 简介正常 + 行数对齐 + art 保护。
