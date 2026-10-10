# 台词提取基准测试（2026-08-29 r401_ryouri 实战，精译架构前置）

**触发**：设计精译（refine）环节时用户断言「台词提取可能存在漏识别和误识别，这个算法目前就需要优化」——精译骨架若建立在不可靠的台词表上，回填会错位、艺术行会被改坏。故**先做基准测试量化精度，再定精译架构**（用户明确要求 test-first 门控）。

## 基准测试结论（r401_ryouri 3 章抽样）

用 `line_kind`（平铺版 `scripts/aa_translate.py:542`，text/mixed/art/header/skip 严格三分类）作参考基准，对照 `export_dialogue.py` 现行启发式导出的台词表：

| 指标 | 数值 | 判定 |
|---|---|---|
| **误识别**（line_kind=mixed/art 却被台词表当台词导出） | **4 / 1077 = 0.4%** | ✅ 低，几乎不误伤 |
| **漏识别**（line_kind=text 纯台词但未导出） | **717 / 1077 = 66.6%** | ❌ **致命——2/3 台词行被漏** |
| 单章台词表行数 | ~123 行/章（vs line_kind 判定的 350 行/章） | 只抓到 1/3 |

### 根因：export_dialogue 启发式对 mixed 行失效

`export_dialogue.py` 的台词判定（`kanji_n/len>=0.3 and kana_n/len>=0.5` 之类字符占比启发式）在**「左侧 art + 右侧台词」的 mixed 行**上失灵：

- `L138 [主] __／　　　　┓┏＼_　　尾っぽやヒレにたっぷりと` —— 左侧是鱼 art，右侧是台词。左边大量 art 字符**稀释了台词浓度** → 字符占比不达标 → 整行判非台词 → **漏掉**
- 误识来源：纯文本行概率低是因为 `line_kind` 对 mixed 行有更细判定（`han_kana and art_chars/len>=0.2 → mixed`）

### 关键认知：两套标准不一致

`line_kind`（`aa_translate.py`）vs `export_dialogue` 启发式是**两套独立台词判定**——前者有成熟的 `text/mixed/art` 三分类，后者只有粗糙字符占比。基准测试的靶心就是让口径**统一**到 `line_kind`。

## 架构影响：精译不依赖脆弱台词表

除非先修好台词提取（把漏识从 66% 压到 <5%），**精译的纯台词表方案地基不牢**（漏译 2/3 台词 = 精译漏 2/3）。修正后精译输入建议：

- 读原始 `_汉化.txt`，用 `line_kind` 逐行门控：`text` 整行送模型 / `mixed` **剥右侧台词段才送**（`residual_text` 提取，art 不送）/ `art/header/skip` **不送模型**
- 精译输出按行号回填，art 字节保留

两条路径（未定）：
1. **先修台词提取**（用户原始诉求，全管线受益：漏译扫描/工作台高亮/精译/翻译质量）——把 `export_dialogue` 判定对齐 `line_kind`，压漏识率
2. **精译不依赖自动台词表**，V4-Flash 直接对 `_汉化.txt` 做 text/mixed 行级精译（`line_kind` 前置标定，不送 art）

## 附带事实（基准测试顺手摸清）

- `export_dialogue.py **已支持 `--src`**（只生成原文 `aa/<章>_台词.txt` 日文，不依赖汉化）——用户说「理论上翻译前就应生成原文台词」，此机制已在
- r401_ryouri：原文台词（`aa/`）39 个全部已生成；汉化台词（`汉化/`）只有 1 个（大多数章未导出）
- 台词文件混三类行：纯台词（`L34 [用] 投下か？！`）/ AA art+台词共行（`L138`）/ 纯 art 人物表情（`L98 /　⌒　⌒＼　『入速出やる夫』` 仅名字）
- 判定函数参考：`line_kind` 在平铺版 `scripts/aa_translate.py:542`（`is_text_line` @520）；工作台与生产需对齐（历史教训：`logic.py:line_type_of` 旧逻辑 vs `line_classify_v2` 曾 32% 标记不一致，见 SKILL.md「分类器误标审计」）

## 后续（用户拍板方向）

1. **台词提取算法优化** → 量化漏识/误识达标（漏识 <5%）→ 统一到 v3 meta（line_classify_v2）
2. **同时生成汉化台词 + 原文台词**（原文应在翻译前生成）
3. **工作台阅读界面加台词行高亮**，方便人工对比（`reader_page` 双渲染 `rendered`+`rendered_src`，见 `aa_web_app/main.py:505`；阅读器模板 `reader.html` + `reader.js`）

基准脚本：`<repo>\temp\benchmark_dialogue.py`（独立复制 line_kind 判定 + 统计，不 import aa_translate 避免拉起重度依赖）。

---

## ⚠️ 定稿后再修订：基准升级 v3 meta + conf<0.65 段数据纠正（2026-08-29 第二轮执行证实）

设计文档第二轮回合把基准从 `line_kind` 升级到 **v3 meta（line_classify_v2）**（kind/confidence/reason/segments 段边界），理由是 `line_kind` 无段边界/置信度、比 v3 meta 信息量低（无楼先验）。**line_kind 降为宽度校验口径，不再是台词提取依据。** 下列事实在实现 `extract_dialogue.py` 时实测确认，是第 1/2 步的硬依据。

### v3 meta 结构事实（读 meta 文件实测）

`scripts/tools/line_classify_v2.py` 生成的 `work/line_meta_v2/<base>.json`：
- **key = 1-based 物理行号，从 3 起**（行 0/1 是 `# 标题`/`# 原帖地址`，无 key）；`aa/<章>.txt` 是 0-based list → **meta_key-1 = txt_lines 索引**（meta 键 3~1394 覆盖 txt 全部 1394 行，精确对齐）
- value = `{kind, confidence, reason}`，仅 mixed 行有 `segments`
- `segments = [{start, end, kind, src_width, (confidence<0.65时)}]`，**半开区间 [start,end) 覆盖整行无空隙**，start/end 是 Python 字符索引
- 楼主帖(◆) `treat_hankaku_as_text`→半角片假名算 text；名無し帖同上
- 判定函数：`classify_raw_v2(site, raw, txt_lines)` 需 site 对象 + raw HTML；`_serialize` 输出；`segment_line`（140 行）做段边界，`_seg_confidence`（127 行）给段置信度

### ⚠️ 关键数据纠正：conf<0.65 段 93% 是艺术碎片，不是"翻译跳过的台词"

设计文档原文：「低置信 text 段（conf<0.65）全部提取，不按翻译阈值砍——翻译管线本就没送、汉化里仍是日文，恰是漏译扫描/精译最该看到的行」。**实测 entry_17568 证实大量反例**：

| 段类 | 数量 | 抽样 | 判定 |
|---|---|---|---|
| 低置信(conf<0.65) text 段 | **347** | `L51 ｀ヽメメ≧`、`L52 ミ`、`L54 ｝メ`、`L67 ノ` | **93% 是纯艺术假名碎片** |
| 高置信(≥0.65) text 段 | 104 | `L53 おっ、来たか来たか` | 真台词 |
| 低置信段**含真实假名**（可能真台词） | **仅 24/347（6.9%）** | — | 这些才是要补的真台词 |

**根因**：`_seg_confidence` 对"无真实假名/汉字=纯装饰"返回 0.55（`segment_line` 的 `treat_hankaku_as_text` 把楼主帖半角片假名 `ヽメメ≧` 全当 text 段）；翻译管线 B2 逻辑（`aa_translate.py:1003`）conf≥0.65 才送模型、跳过这些 0.55 段——**跳过的理由是它们是装饰假名，不是台词**。把它们全提取会让台词表 93% 掺艺术碎片。

**结论（待用户拍板）**：extract_dialogue 不该无条件"全提取 conf<0.65"，应加**真实假名门禁**（段内含非艺术假名/非艺术汉字才保留为台词，纯 `ミヽメメ` 装饰碎片丢弃）——保 24 个含真假名低置信段 + 104 高置信段，丢 323 个碎片。**真正"翻译跳过的台词"= conf≥0.65 段 + 那 24 个含真假名低置信段，不是全部 0.55 段。**

### 新模块 `scripts/common/extract_dialogue.py` 接口（已建，读 v3 meta）

- `extract_dialogue_from_meta(meta, txt_lines, op_names=None)` → `list[dict]`，每项 `{line_no, seg_idx, kind, text, seg_start, seg_end, conf, op}`
  - `text` 行 → 整行（seg_idx=0, seg_start=0, seg_end=len(row)）
  - `mixed` 行 → 按 segments 取**全部 text 段**（当前未过滤 conf，待用户拍板上面的门禁）
  - `art`/`header`/`blank` → 跳过
  - **段文本不 strip**（保留行首 U+3000，段级拼回需要）；start/end 供字节级回填
- `op`（主/用/None）：楼主识别复用 `export_dialogue.is_op_header` 口径（`parse_op_header` 支持 A/B/C 三种帖头格式）；多马甲走 work_meta.author
- `format_dialogue_entry(d)` → `L<行号>[_<段号>] [主/用] <段文本>`（mixed 行带段号）
- CLI 层对接 registry `cfg`（site/raw_dir/aa_dir/out_dir/work_dir），meta 缺失走 `_ensure_meta_flat` 自动生成，raw 缺失中断报错人工介入
- 依赖：`line_classify_v2`（meta 来源）、`registry`（cfg/news）、`extract_dialogue` 独立可测（参数传目录，不重依赖）

台词输出定位：**原文台词进 `aa/<章>_台词.txt`（`--src`，翻译前生成），汉化台词进 `汉化/<章>_台词.txt`**，两者用行号对齐（`.txt` 与 `_台词.txt` 同目录，行号即物理行号）。
