---
name: aa-translation-proofreading
description: Use when 校对已汉化AA文本的质量(漏译/误译/宽度/行数错位/台词导出). 四件套+台词文件.
---

# AA 汉化校对工具链（漏译/误译/宽度 三件套）

> 适用于 `<repo>`。针对**已汉化文本**的三类质量问题——三件工具互不替代：
> - **漏译**（整行日文残留）→ `scripts/tools/fix_missed_lines.py`
> - **误译**（译了但错：鏡→镜子 应=眼镜；无残留/宽度对/模型自信，漏译和宽度都扫不到）→ `scripts/tools/proofread.py`
> - **宽度** → `scan_width.py`（区分行类型，见下）
> - **行数错位**（汉化比原文多/少整行 → 其后逐行全体错位，表现为「台词被截断到下一行、之后整体错位一行」）→ 第五节
>
> 方案文档：`doc/独立校对脚本方案_2026-08-25.md`。

## 触发
- 用户报告「某行没翻译/漏译率提高」→ fix_missed_lines
- 用户报告「某词译错」（镜子 vs 眼镜类多义词）→ proofread
- 用户报告「名字混了」——两个相似日文名互换中文名（ラ行 3 假名型，如 A 的日文行被译成 B 的中文名）→ 第三节之二（**零 API，别跑模型**）
- 生成/校对前的纯净台词（去 AA 艺术）→ extract_dialogue（统一入口，见下）
- 宽度校验报错太多、纯文本行被误报 LONG → scan_width 区分行类型
- 用户报「某行被截断到下一行／之后的行相对原文整体错位一行」→ 第五节（行数错位检修）

## 一、纯净台词导出（extract_dialogue.py 统一入口）——一切校对的前提

> **2026-08-30 统一入口**：`export_dialogue.py` 已**移除**，台词提取统一到 `scripts/common/extract_dialogue.py`（v3 meta / line_classify_v2 segments 单一口径）。gen_chapter_summaries / gen_glossary_ai / **refine_runner(精译)** 全是它的调用方；旧 export_dialogue 语义迁入。
>
> CLI：`python scripts/common/extract_dialogue.py --work <id> [--src] [--overwrite]`
> - **默认(智能)**：逐章自动判断 `汉化/<base>_汉化.txt` 是否存在——**有→读汉化行生成中文台词写 `汉化/<base>_台词.txt`**；**无→降级生成原文台词写 `aa/<base>_台词.txt`** 并打印提示（禁静默杜撰中文）。目标已存在且无 `--overwrite` → 跳过。
> - `--src`：强制只出原文(日文)台词写 `aa/`。
> - `--overwrite`：强制覆盖。
> - 楼主识别失败防护（`[?]` 占 >10% → 中断）已并入 CLI，承继旧 export_dialogue 的「不存在无楼主作品」硬约束。
> - **硬前置 = line_meta_v2 能生成**：缺 meta 时 `_ensure_meta` 自动生成，生成失败（日志 `警告: 行数不齐 N vs txt M,跳过`、或站点无 `extract_article`/`parse_posts`）会抛「meta 生成失败（行数不齐/站点不支持），中断」——这是设计好的硬约束，**勿在这条链上加静默降级**；批量补刷与对齐校验（删错位 meta、默认 ≤2 并发、先小样本）见 `aa-pipeline-optimization/references/line-meta-batch-repair.md`。
>
> **⚠️ 汉化(中文)台词提取两个关键**（2026-08-30 定稿）：
> 1. **读 `汉化/<base>_汉化.txt` 而非 `aa/<base>.txt`**。旧 bug：`ensure_dialogue_file`/CLI 无论 src 与否都读源文件 → 写进 `汉化/` 的台词其实是日文（refine_runner 用 `(line_no,seg_idx)` 配对「汉化台词」拿到日文=错）。修法：非 src 时 `txt_lines` 取 `out_dir/<base>_汉化.txt`，缺失按「禁静默降级」中断。
> 2. **mixed 行段按显示宽度定位，禁用字符偏移**。v3 meta 段偏移是**源行字符坐标**；汉化行因译文字符数不同会漂移（实测同段源`ソ`切出汉化`\u3000` 错位）。段边界以 `seg['src_width']`(显示宽度)为准——`_slice_line_by_widths(line,segs)` 逐段累积宽度到目标即截断。因 line_classify_v2 与 extract_dialogue 同用 `aa_common.disp_width` 算 `src_width`，宽度定位在源行上 == 旧字符切片（回归 r401_ryouri 39章/8082 段 mismatch=0），源/汉化行通用。

- 默认：`汉化/<章>_台词.txt`（中文台词，`L行号 [主/用] 内容`）
- `--src`：`aa/<章>_台词.txt`（日文原文，**L 行号与中文一一对应**供对照）
- 楼主判定：帖头作者含 ◆/★ 或 == work_meta.author（**多马甲**逗号分隔）→ [主]；名無し → [用]
- **用户楼台词默认也翻**（用户明确要求——占比 20-40%，含剧情/吐槽信息量；sujin 生娘シャブ漬け 40%）

**台词判定（演进定稿，防艺术行混入）**：
1. 连续假名≥2（**不排除 ART_KANA**——黑名单含常用假名 いうふしとこ，排除会漏「食い気が悪いよ」类送假名多的台词）
2. 汉字占比≥0.3（艺术行 ニニニニ/ズズ～ 几乎无汉字）
3. 或 假名占比≥0.5 兜底纯假名台词（乙です/ありがとう）
4. 艺术假名占比 <0.3（ニミ彡乂爻ﾆﾄﾘ 是 AA 绘画字符）
5. 重复字符占比 <0.5（三三三三/ニニニ 是背景艺术，且 三 是汉字会过汉字占比）

⚠️ **工作台 4 处把 `aa/*_台词.txt` 当原文章节**：`merged_chapters` / stats 章节数×2 / `check_chapter`——全要排除 `_台词.txt`。修复后验证阅读器目录无「台词」章。

## 二、漏译修复（fix_missed_lines.py）

`--work <id> [--fix] [--ctx-lines N 默认5]`

- 扫描：纯台词整行残留（判定同上）+ 译文==原文 → 漏译
- 重译上下文：**±N 楼**（帖头行切分楼边界）的台词.txt——「鏡→眼镜」线索在后 5 楼（用户吐槽 L447），±2 行/±2 楼都不够
- **中文译文直接给场景**（[用]镜子后的第一个画面是眼镜吗 → 模型判明鏡=眼镜），成本 ~32 token/次
- 超宽失败行：**从 SRC 重建**（保留艺术结构只换台词段 find→replace→pad），绝不整行替换（毁艺术符）

## 三、误译校对（proofread.py）— ⚠️ 2026-09-07 已废弃于翻译/校对管线

**管线内校对一律用 `refine_aa`（段级重建+术语注入），旧 `proofread.py` （词级 old→new 替换顶歪 mixed art 段列位，entry_17808 14 行事故）已从 translate_runner 移除、死代码删除。** 本文件仅保留作**手动 CLI 单章调查**用（对照工具，不自动铺到管线）。翻译任务**默认启用 refine_aa 校对**（`--no-proofread` 才禁用，2026-09-05 的"默认禁用"决定已反转）。run phrase：

`--work <id> [--chapter X] [--apply]`

1. **预筛**（零成本）→ 只对可疑楼送模型：同词多译 / 短台词≤4字 / 多义词表（鏡羽縁下駄鮎梯子腹手足顔）/ 风格生硬（的了/了一个…）
2. **复审** prompt：中文台词 ±3 楼 + 日文原文对照 + **作品术语表段 + 知识库 memory_hint**（全局前20对段已移除，2026-09-07）+ 输出 `L行号|类型|原译文|建议|原因`
3. 清单 `work/proofread_<work>.json`；人工确认后 `--apply`（宽度校验）

⚠️ **必须直接调 `aa_backend.call_with_fallback(False,...)` 而非 aa_translate.call_api**：
call_api 在 AA_BACKEND 未设时走**云端直接模式**（commandcode 网关），对长 body 返回**空串**（zhipu 直连短 body 正常——易误判为后端故障，实际是走了错误入口）。

⚠️ 多义词模型判断可能**自相矛盾**（不同楼上下文不同，楼14说镜子楼16说眼镜）——清单价值=标出争议点供人确认，非自动判定。

## 三之二、误译修复：符号互换 / 近名混淆（**零 API 定向替换**）

**适用**：不是「一个日文词被译成多种中文变体」（那走 `aa_web_app/gen_variant_extract.py` + `apply_variant_replace.py` 链，要调模型），而是 **A 的日文被译成了 B 的中文名**（典型：`ライザ→莱纳` / `ライナー→莱莎`——同为 ラ 行三假名，模型按形近挑中文名）。**不要为此调模型**：按行计数对齐就能精确定位，比模型准（模型会把整句当命中物摘出来）。

1. **逐章逐行对齐**：`aa/<base>.txt` 与 `汉化/<base>_汉化.txt` 都 `read_text().split("\n")`（同全仓行口径，勿去尾空行；行数不等先按第五节的错位流程处理）。
2. **每行双向计数**：源侧两个日文名各出现几次、产物侧两个中文名各出现几次。分流：
   - 源含 A 不含 B、产物含 B 的中文 → **错译**：该行产物里的 B 中文全替成 A 中文；
   - 源含 A 不含 B、产物无任何中文名 → **漏译残留**（另一类，见下）；
   - 源**同时**含 A 与 B 的行 → 跳过，列给人工（判不了）；
   - 产物有中文名而源两侧都没有 → 拖长音变体（`ライナァァ` 一类），多半是**正确**译名，别动。
3. **两个方向都要扫**：错译是双向的（实测一章整章 A→B，另有一处孤零零的 B→A）——只扫一个方向必漏，反向那几行最容易被漏掉。
4. **写盘前硬断言**（任一不成立就中止该行）：① 源侧锚点成立；② 替换前后 `disp_width` **相等**——同为 2 汉字的中文名天然同宽，宽度变了说明撞上了别的词；③ 行数不变。备份整文件到 `work/backup/<name>_<ts>/` 再 `"\n".join(lines)` 写回。
5. **验收**：重跑第 2 步扫描（待改应为 0）→ 跑 `scan_width --work <id>` → 再用 `aa_translate.line_kind(源行)` 证明改动行**全是 text/mixed**。最后这条是**判定既有告警归属**的关键：text/mixed 不参与 art 锚点检查，所以报告里剩下的 art 列位失配数才能断言「与本次改动无关」，否则会被当成自己改出来的。
6. **漏译 ≠ 错译，分两批做、分两批报**：产物里**仍是日文**的名字多落在艺术行/系统提示行（`◎` 开头的提示行、`【名前の红茶レベル】` 类），替换会改宽度（日文 3 假名 6 格 → 中文 2 字 4 格）→ 必须 pad 补全角空格 + 按 art 行等宽约束校验，风险完全不同，别混进同一批。

## 三之三、行首【角色名】标记丢失 / 未译 / 漂移（**根因侧护栏**，2026-10-02 建、2026-10-03 收紧）

⚠️ **批量修补工具 `scripts/tools/fix_speaker_tag.py` 已删除（2026-10-03）** —— 它的安価分支把内容重复追加（`new = 修好的标记 + 原文`），带「：」的安価/系统行成片损坏，用户回「全乱了」，需按 `tagfix_log.jsonl` 的 `old` 逐行回滚 10971 行。**不要再写这种批量文本修补脚本**；标记问题一律走根因侧（翻译/校对护栏）。

**为什么会丢**（两层根因）：① **主翻译**对「名字不在术语表」的【名前】行会整行失败（模型保留日文名 → 残留假名校验 3 轮拒收 → 落 `review_inline.txt`，本作 641 行 / 1.11%）；② **refine 的漏译补译链**原来只校验「无残留假名 + 宽度」，**对行首【…】没有任何约束**。

**根因侧护栏 = `refine_aa.guard_speaker_tag()`，三条硬规则**（方案 `doc/方案归档/说话人标记护栏收紧方案_2026-10-03.md`）：
1. **仅文本行**：只在 `_job_poly`（纯 text 行）生效；`_job_swallow`（段级补译 mixed/art 行的段）**一律不做标记约束** —— 段级强改 art/mixed 行会把艺术假名译坏（实测 `ｉヽVツﾉ`→`いヽVツノ`）。
2. **标记分类五类分派**（`tag_class`）：
   - `speaker`（含假名/汉字、无骰子安価特征、≤20字）→ 允许查表译名（`_pre_terms`）；缺标记时带 `TAG_RULE` **重试一次** → 仍缺则源标记确定性回填（`+tag_retry`/`+tag_reattach`）
   - `dice`（含 `\d+[dD]\d+` 或内层【…】）/ `anka`（安価/下\d/このレス/判定/交代先…）→ **不打重试**，只校验「数字序列+括号冒号骨架」（`_skeleton`），不符即源标记回填（`+tag_fix`）
   - `latin`（`【System】`/`【？？？】`）→ 括号内必须**逐字相同**
   - `art` 碎片（半角假名/框线/多空格/超 20 字）→ **完全不进护栏**
   - 整行只有一枚标记（安価/系统行，本作 2364 行）也要回填，别提前 return
3. **行内骰子**：`_job_poly` 产出后校验源行里每个 `【NdM:T(…)】` 是否**逐字出现**，否则放弃该次改写（保留原行）+ 记 `dice_err` → 落 review_inline。本作骰子片段 998 个/989 行，其中 458 行在 mixed 行（= 段级路径目标）—— 这是「不要改错」的重点。

**解析坑**：行首标记可能嵌套（`【Part（【1D912:674】）スレ主】`）。正则必须留一层嵌套 `^([ \u3000]*)【((?:[^【】\n]|【[^】\n]{0,40}】){1,40})】`；旧的 `[^】]{1,40}` 会截断成 `【Part（【1D912:674】`，回填就是写坏。

**存量丢失标记**（本作 667 行）处置口径：只动「说话人名 + 纯 text」的 615 行（dry-run 清单 `work/tagguard_dryrun_*.txt`）；含 art 段的 25 行、图形残片 27 行一律不碰（改了必错）。

**若还要写任何批量文本修补，四条铁律**：① 只能「就地替换一个 span」，**不得拼接/重排/合并**内容；② 落盘前 **dry-run 打印待改行**给用户过目；③ 每次运行建整章备份（按运行粒度，只留最近 2 次）+ **结构化 JSONL 记 `old`/`new`**（本次靠它才能精确回滚）；④ 回滚优先用日志 `old`（护栏：现文本 == 日志 `new` 才改），比备份目录可靠（备份会被 prune）。
**顺带修数据缺口**：名字在表里缺失就补 `doc/aa作品相关/ポケモンAA术语wiki/补充术语.json` 的 `works.<作品>.terms`（本作补了 メジロ→目白、メジロアサマ→目白浅间、カズミラクル→和真奇迹、カズミー→和真酱、**視聴者→观众**——最后这条一次修掉全库 `【視聴者A】` 类未译标记），然后 `python scripts/tools/gen_pokemon_aa_wiki.py --build`。

## 四、宽度校验区分行类型（scan_width.py，2026-08-25）

用户定稿：「纯文本和 mix 文本在右侧行允许超宽」。实现：
- 纯文本/mix 行 **wb>wa 不报 LONG**（译文自然扩展是正常的）
- 艺术行仍严格等宽（`_is_art_dense` 艺术符占比≥0.5 判定）
- 只报 SHORT（wb<wa）

## 五、行数错位检修（第四类：汉化比原文多/少整行）

> 症状：某行台词被截断到下一行，或从某行起整章相对原文错位一行。**根因=汉化文件里多出（偶尔少掉）整行**：多一行则其后所有行下移一位，逐行对齐全部失效。脚本骨架见 `references/line-count-drift.md`。

1. **先比行数**（任何逐行校验之前）：`aa/<base>.txt` vs `汉化/<base>_汉化.txt`。自己读字节判分隔符（`\r\n` 数 > `\n` 数/2 → CRLF）再 split，别用固定 `\n` 混两种口径。
2. **定位多出的行**：找**相邻两行逐字节相同、而原文该处不重复**的位置（= 重复插入的艺术行）。也可用打分定位：对每个候选下标 j 算「删掉该行后与原文同下标逐字节相同的行数」取最大；差 d>1 用前向 DP（恰好删 d 行最大化相同行，O(n·d)）。**修复前后该指标差别极大**（实测 262/806 → 735/806，约 30% → 91%）——这就是可靠的验收数字。
3. **EOF 空行是假警报**：汉化末尾多一个换行 → split 后多一个 `''` 元素 → 报「行数不一致」。判据：多出的元素**全是 `''` 且都在 `len(原文)` 之后** → 无害，别动。
4. **别整章重跑 refine 修行数**：会覆写用户手工还原过的行。漂移期间各行译文本来就与所在艺术行同源（只是物理多一行），**删掉多余行后配对自动恢复，不需要重译**。
5. **写回保格式**：先备份 `work/backup_linefix_<ts>/`；写回用与原文件相同的分隔符 join，并保留原末尾换行（否则又制造一次 EOF 空行差异）。
6. **验收**：① 行数 == 原文；② 重跑 `scan_width.collect_mismatch(..., check_art_align=True)`；③（可选）逐行同下标相似度——任何行都不该比相邻下标的更像原文。

### 根因之二：模型译文折行被写回拆行（2026-09-22，三层护栏已加）
症状与上面同款（汉化比原文多 1~4 行），但多出的**不是重复艺术行**，而是「重复的半句译文 / 半截译文 + 多余空行」：
`refine_aa` 段级重建 / swallow / poly 把模型回包里的换行当普通字符拼回「一行」，写回 `"\n".join(new_tr)` 时
这一行被拆成 2 行 → 静默多行（实测 entry_8332 +2、entry_7578 +4；工作台末几行报「行数据加载失败」= 末尾 idx 越界）。
- **护栏三层**：①`aa_translate.one_line()` + `txt_write()`：所有写盘行压平行内换行（压平即 stderr 告警；
  `aa_common.txt_write`/`logic.write_lines` 同源，全库写盘兜底）②refine_aa 的 swallow/poly 出口压平（根因处）
  ③refine_aa 写回前比对原文行数，不一致**拒写**并 `stats['write_skipped']` 跳过该章 ckpt（不落 ckpt 才会自愈重校）。
- **扫全库存量/事故后复查**：`python scripts/common/scan_line_drift.py [--work <id>] [--json]`，退出码 1=有中段错位。
- **修复口径（分流，不是一律删）**：多余行**只含中文** = 前一行译文的后半 → **并回前一行**（`rstrip()+strip()` 后 pad 到原文宽）；
  **空行 / 含假名的 art 碎片** → 删。删完以「行数相等 + 结构性行（空行/art/帖头）逐字节相同」为最硬验收判据 ——
  difflib 的长度不齐块判据会被**译文行**（内容不同但行数相同）干扰出假报警，别只看它。
  完整配方（DP 偏移惩罚项、art 截断还原的窄判据、五层验收、邻类勿误修）→ `references/line-count-drift.md` §5。
- **DP 必须带偏移惩罚**（`match 代价*1000+k`、`del 代价*1000+(k+1)`）：只比「逐字节相同行数」时 DP 会等价地选「在异常点之前删」，
  把后面整块 art 前移一行（实测第一版发生过）。
- **`line_kind()` 不能当 art 判据**：`以刈` 类当画用的汉字 → `mixed`；短密 art `ヾﾄ､ヽr‐､{ {　l` → `text`。结构性行比对与 art 还原判据别只信它。
- **验收要到工作台**：`GET /api/work/<work>/chapter/<fname>` 的 `fname` **必须带 `_汉化.txt` 后缀**（只给基名 404），
  再验末 3 行 `line/<idx>/html` 200 + `range` 末页 —— 这才是「行数据加载失败」真消失的实证（行数/数值自检只算中间证据）。
- 回归测试：`scripts/tests/test_refine_line_guard.py`（one_line 压平 / txt_write 行数不增 / refine 三处护栏源码断言）。

### 坑
- **行数不等时 scan_width 整文件跳过**：`collect_mismatch` 一见 `len(src) != len(tr)` 就 `continue`，只报 count_mismatch——**该章的宽度/art 列位校验根本没跑**。所以行数不一致期间以前的「宽度通过」不算数；**修完行数必须重跑 scan_width**，常冒出被行数问题掩盖的既有 art 列位失配。
- **全库体检口径**：逐作品逐章比行数即可全库扫（53 作品/3027 章约 1 分钟）。行数不一致里**大多数是第 3 条的 EOF 空行假阳性**（实测 69 章中 58 章），别当错位量报给用户。
- **汉化正文里的校对/模型回包文本**：出现 `最终结果：L<行号>`、`（注：…`、`…属于漏译` 这类句子 = 校对产物被写进汉化文件，会直接渲染进阅读器，属要删的污染行——与「多一行」不同类，单独报。
- **行数大幅不符（少几百行）≠ 错位**：相同行占比接近 0 的多半是旧译文对不上新原文，别用「删多余行」的套路硬修。

## 坑汇总

## 独立 refine 流程（2026-09-05 设计定稿：refine_aa.py）
> 目标: 对**已译作品指定章节**做三类修复——错位修正(段级重建)、漏译补译(低置信段/未段级)、错译还原(图形行被当台词翻坏)。**独立 CLI+可被 task_manager 当 `proofread` 校对任务调（2026-09-07 task_report 适配: 章级 heartbeat/check_stop/finalize, env 无 AA_TASK_ID 自动 no-op）**。

**三态触发判定（逐行, 用 `segment_line` 现场重判, 不信任磁盘 meta.kind）**：
- **有 conf≥0.65 text 段** → 错位修正/漏译补译, 走段级重建（复用 `fix_mixed_drift.rebuild_row`）
- **全低置信 conf<0.65** → 纯 art 行（误判）, **整行还原 SRC**（防把图形字符当台词翻——这是错译根源）
- **汉化行台词留日文（连续假名串≥2, `restore_src_nozh`）** → 漏译, 还原 SRC 待补译

**关键复用（不重写）**：`fix_mixed_drift.rebuild_row`（段级重建+全低置信还原+漏译还原三路已覆盖）、`T._verify_art_alignment`、`T._confident_text_segs`、`L.segment_line`。refine_aa.py 是**指挥层**（判定+遍历+三类编排+备份），执行层复用已验证逻辑。

**关键诊断事实（17808 实测）**：
- **text-noseg 行**（meta 判 text 且无 segments, `classify_content` 误判, 落主块整行翻译→错位根源）：66 行中 **58 行补 segs 后可段级、8 行全低置信=纯 art 行**。补段判定可靠, 但全低置信行绝不能段级重译（会翻坏图形）。
- **review_inline.txt 记录=落盘的翻译失败混合行**（格式 `entry_X | line N | 原文 [mixed-fallback]`）：17808 有 258 行, 实测 55 行中 **54 行是纯 art 行（无台词 SRC==TR 正确）+ 仅 1 行 text 有差异**。→ 绝大多数是 art 行被误判 mixed 送段级失败落盘, 本就正确。**"用模型矫正 review_inline"的定位 = 对每行做三态裁决（有台词→补译/纯 art→还原/已正确→跳过）, 模型只在真有问题时介入, 裁掉 art 误报**。
- **无 seg 行为何错位**: `mixed_segs = {idx ... if _confident_text_segs(segs)}` 只收有 conf≥0.65 text 段的行; 无 seg text 行不在 mixed_segs → 落 normal_todo → **主块整行翻译** → 模型压 art（17808 事故同款）。
- **meta 误判来源**: `classify_content` 的 `lz_short_dialogue`/`lz_dotted` 会把含图形字符的 art 行判成 text（假名/图形字被当台词）——refine 判定必须现场 `segment_line` 重算而非信 meta.kind。

**测试/试用纪律（用户明确要求）**：让 refine 跑完覆写，产物交用户自己审。**勿在测试的同时另起补译/修复同一批文件**——会污染被测工具的归因（用户无法判断哪些改动是 refine 做的），用户原话「只做让你做的事」。干净复测法：先 md5 记基线；恢复原版用 refine 自带的 `work/backup_refine_aa_<ts>/`（ts=运行时刻，内含覆写前文件），md5 与基线一致再 `--write`，逐行报告留 /tmp 供用户 diff。

**refine 输出边界（2026-09-06 干净复测 17810/21101 实测）**：
- **restore 会吞可译短台词**：无 high-conf seg 行整行还原 SRC，行尾短台词（えっ？/えっと…？/…っと。）本可译却被还原成日文——refine 缺陷，该在 refine_aa.py 修（restore 前查行尾 conf=None 短段），勿手工事后补。
- **rebuild 偶发删 art 字符**（~3行/章，删字/重排型）：refine 内部 `_verify_art_alignment` 只查 art-kind 段、报 0 错位；`scan_width` 的 art-align 连 conf<0.65 text 碎段里的图形字（jf圷/卞ﾐ/ﾄ＼ 被误标 text）一起查——**复验用 scan_width，别信 refine 内部 0 错位**。
- **dry-run 也调模型**（rebuild 需重译台词段），非零 API；deepinfra GLM-5.3-Flash 间歇 SSL EOF（部分 poly 行 FAIL 属正常），项目主链 deepinfra zai-org/GLM-5.3-Flash（`aa_backend.call_with_fallback(False,...)`）实测可用作兜底。
- **refine 三类修复不覆盖语义误译**：已译但错词（ヒール→鞋跟 应=治疗/自警団→自卫团 应=自警团）全放过——那是 proofread.py 多义词表/术语核对的事，别指望 refine 抓。
- **段级回填/补译的宽度陷阱**：台词段边界用 `segment_line` 的 conf=None text 段时，碎段常含图形字（f'斧ミ/jf圷 被误标 text）——取「最后一个 conf=None 且宽≥20 的真台词段」为台词区，其前全部保持 SRC 字节不变，否则会吞掉人物线条。

## 汉化/原文行数不一致 = 重复行漂移（2026-09-20 全库实测）
`scan_width.collect_mismatch` 对**行数不一致的文件整篇跳过**（早退）——这类章的宽度/art 列位问题全部不可见，工具只报"行数不一致"一句。全库 3027 章体检：69 章不一致，其中 **58 章只是汉化 EOF 多一个空行**（无害，但会触发误报 + 跳过校验；批量归一化即可消音）。真问题=**汉化比原文多 N 行**：写回时多出/重复了一行（重复行多为紧邻艺术行，或校对模型输出泄漏行），其后所有行整体下移 → 表象是"之后的行相对原文都延后了一行"。**手工改文字救不回，必须删掉冗余行。**

- 定位法（temp/fix_redundant.py）：受限 DP —— 允许删除的候选 = 空行 / 泄漏特征行（`未翻译|漏译|最终结果|注：|中文译文|按规则输出|原样保留`）/ ±3 行内与某行逐字节相同的行；目标 = `3×逐字节相同行数 + 显示宽度相同行数`。
  - ⚠️ 纯"逐字节相同行数"目标在有长重复艺术行（〓〓/ニニニ）的章会挑错位置（实测 entry_30929 挑到 L2227，正解 L1487 → 残留 448 行短宽）。必须带宽度项，且优先选"块内行冗余"的等价最优位置。
- 验证（temp/final_verify.py）：修后**显示宽度相同行比例应达 ~99%**（30929: 1745→3677/3709；4690: 306→787/806；宝可梦 212→2297/2330），行数一致后再看 scan_width 残留。
- 泄漏行实例：`（注：部分表述可根据语境微调…按规则输出）`、`最终结果：L33`、`未将日文原文翻译为中文语句，属于漏译`、`（这句不是台词，而是AA字符画…故原样保留。）` —— 段落级模型/校对输出被写进汉化正文，会直接渲染进阅读器。
- 高仿真心智负担提醒：本类修复必须**逐文件备份**（`work/backup_linefix_<ts>/`）+ 只删行不改行；修完 `scan_width` 才会首次露出该章既有的短宽/art 列位缺陷（以前被行数不一致挡住），那是另一类问题（fix_width / refine_aa 段级重建），别混进同一次改动。
- 脚本：`temp/scan_align_all.py`（全库行数体检）、`temp/fix_redundant.py`（受限 DP 修复，MODE=DRY_RUN/APPLY）、`temp/final_verify.py`（修前后指标 + scan_width 残留）。

## 残留日文的「分层验收」口径

拿一章产物数「多少行还带日文」只是个粗数，**必须分层**，否则报给用户的漏译量虚高一个数量级：

| 分层 | 判法 | 性质 |
|---|---|---|
| 帖头 | 源行 `HEADER_RE` 命中 | 设计保留（不译），排除 |
| 画里的日文 | 高艺术符占比的 mixed 行 | 设计保留，排除 |
| 混合行右侧台词段 | 高置信 text 段仍是日文 | 真漏译（swallow 目标）|
| 纯文本行 | 其余 | 真漏译 |

- **判类型用源行，不要用产物行**：产物已被模型改写形状，`line_kind()`/`HEADER_RE` 在它上面会改判（实测帖头格式 B `NNN 名前：… 投稿日：…` 被翻掉一半后，按产物行分类落进「真漏译」，按源行分类是帖头）→ 分类口径漂移会把保留项报成缺陷。
- `kana_runs()` 只是**粗筛**：它会把艺术行（半角假名装饰）算成文本行，粗筛结果里往往大头是画（实测 77 行里 57 行是艺术碎片）→ 粗筛后必须再过 `line_kind`/高置信段。
- **残留 token 频率 = 补表清单，但要先分清来源**：产物里仍出现某个日文词 ≠ 替换没命中——模型会把词照抄回译文里。判「表里有没有」要查**替换表本身**（`pre_terms.pairs_for(base)` 的 jp 集合），不能只看产物：实测 `ガブリアス`（表内，配对「烈咬陆鲨」）在产物里仍出现 11 次 = 模型照抄；`技マシン` 表里没有 = 表洞。两者修法完全不同（前者靠校对/本地判据层，后者补表）。
- **高发形态**：固定模板行（场地效果 `場にいる全てのポケモンの「避」が低下（0.67倍）する。`、`X…　持ち物：Y`）模型常只删掉一个虚词、其余整行照抄（宽度合规、靠假名≥2 才能抓到）——这类行是「该由本地模板/替换产出」的候选，不是校对能修好的。
- 单章渲染验收：`python scripts/common/gen_aa_html.py --work <id> --one <base>` → `<work>/单章渲染_<base>.html`（单章产物，不覆盖正式合集），浏览器打开肉眼过一遍。

## 坑汇总2
- 全句未翻的 mixed 行（整行日文台词留行尾、TR==SRC）特征：尾段 conf=None 且宽≥8 且含句末标点（。？！…）——art 图形行不画标点，此判据防误收；"长台词被 conf 切碎只剩 …/。尾巴"时按整句翻，不翻碎片。

## refine 本地重建吞内容三连（2026-09-20 事故，代码已修）
**症状（用户报）**：「数字+クレジット 翻译后只剩『信用点』，数字丢了」「左右带 `|` 的框线行翻译丢了一部分」。
**根因（全在 `fix_mixed_drift.extract_translations` / `rebuild_row`，本地字符搬运、不走模型）**：
1. 译文候选字符集 `_CJK_CHAR` 缺 **全角数字 ０-９ / ー(U+30FC) / 全角括号（）** → `６６１信用点` 的 ６６１ 不入选 → 重建后被 U+3000 垫顶掉（**宽度校验查不出**，数字凭空消失）；`期待你今后的表现哦ーー！！` 被 ー 切成两候选 → 右起分配时行尾主台词段只拿到 `！！`（整句译文被吞成 `！！`）；`（另一个账户）` 括号被丢。
2. `restore_src_nozh` 只判「≥2 连续平假名」→ **已译好、仅保留日文专名(やる夫)的行被整行还原成日文**（`やる夫——快跑——！！`）。现改判「假名占优(假名数 ≥ 汉字数)」。
3. 候选多于真 text 段时新增「art 回显候选」剔除：整段只有 1 个非 art 字符的候选丢掉（`杀戮飞车党 赏 金 3000信用点` 曾被重建成 `赏 賞 金 …`）。
**兜底**：`rebuild_row` 外壳加数字保全校验（`_nums_kept`，全角/半角等价）——重建后 TR 原有数字消失即拒绝该行(`digit_loss` 落人工)，绝不写坏数据。
**修后数据回填法**（事故已污染的作品）：`work/backup_refine_aa_<ts>/<base>_汉化.txt` = 该章覆写前文件（refine 造成的损伤可用它逐行还原）。
- 取「refine 本地重建记录」（`work/runner_log.txt` 的 `[rebuild/OK/ok|ok_zone|ok_partial_cands|no_highconf_text|restore_src_nozh]` 行，含 原→新 文本）当候选集，再按**当前文件仍带伤**筛选：① SRC 里的数字在当前行没了；② 当前行 == 该次重建结果且丢了字符；③ 中文量掉了 ≥2 字（被吞/被还原成日文）。**别用「pre 行有的字现在没有」当判据**——重译/术语替换都会命中，实测误报 2000+ 行。
- 回填优先用**修好的** `rebuild_row(SRC, refine前行)`（art 列位摆正 + 内容零增减：数字/假名/≥2 字译文字段多重集必须一致），不满足才原样回填 refine 前行。
- 脚本：`temp/repair_refine_regress.py <work> [--write] [--fix-pairs "点数=信用点"]`（dry-run 默认；写盘前自动备份 `work/backup_refine_regress_<ts>/`）。**step1 回填会带回当时的专名写法**（如 やる夫），若之后跑过术语替换（tr_replace_history.json）要补应用同款替换，否则等于回退用户的术语决定（本会话实踩）。
- **验收**：`scan_width --work <id>`（width 0 且 art-align 不新增）+ 重跑脚本应报「仍带伤 0 行」。

## refine_aa 已知缺陷（2026-09-06 17810 用户14行黄金样本）
- **逐字空格版台词提取失败**：主流程整行翻译产物把台词写成 `这 次 听 说 国 王…`(字间空格) + U+3000 垫宽，`fix_mixed_drift.extract_translations` 的 `_CJK` 正则不含空格 → 只抓到单字(害/。)，rebuild 回填残缺。修复方向：正则允许字间空格 `[CJK]+(?:[ \u3000][CJK]+)*`，artset 过滤前剥空格。
- **kind=text 错位行被 refine 跳过**：SRC 行首 art(房子/立绘)被 meta 切成 conf<0.65 text 碎段或 kind=art，line_kind=text → refine 主循环 `kind!='mixed': continue` → 错位不修。修复方向：text 行含 art 段且 `_verify_art_alignment` False 也走 rebuild。
- **行尾短台词被误还原**：`えっ？`/`えっと…？`(conf=None 行尾短台词) 无 conf≥0.65 高置信段 → 判纯 art → 还原成日文。修复方向：还原守卫改"无真台词区"(宽≥6 含假名) 而非 `not high`。
- **`_looks_graphic_fragment` 漏单字残片**：`ノ`/`ー`/`ゝ` 宽≤2 无中文实词的单假名残片判 False(当台词)。修复：宽≤2 且非语气词(うん/え/あ) → 图形。
- 修复方案全文：`doc/方案归档/refine_aa短台词错位修复方案_2026-09-06.md`（含黄金样本行号与实证）。

## refine 漏译自动补译链（2026-09-06 落地: 17810 吞字修复 + 21101 漏译 74→4 行）
mixed/text 行 TR=SRC(整行日文残留) 的漏译行自动模型补译(swallow_translate)，落盘规则与坑：
- **尾台词段判定 `_tail_dial_seg`**：segs[-1] 是 art(气泡框 `│`/`::::|` 宽 26) 也要向左找最后 text；台词段宽≥6(うーん？宽8 是最短真台词)；假名≥2 或 1假名+≥2汉字(流石私の娘… 型) 才算台词；`_looks_graphic_fragment`(ノ/ー/ゝ 残片) 挡图形。
- **"已译"判据=无连续假名串且含长音ー(`[ぁ-んー]{2,}`)**：日文汉字(経費/必要/予算)落在 `\u4e00-\u9fff` 被当"含中文"是错判——漏译行会误判已译落 manual；ふーん/うーん 的 ー(U+30FC) 不在平假名区间也漏。两个都是本会话实踩。
- **段回填 `_rebuild_tail_seg` 必须保留段后内容** `s[:start]+zh_pad+s[end:]`——只拼到段尾会丢台词后的气泡边框 art(│)致整行错位。
- **宽度校验 `_ok_width(nl,ref)=ref-2 ≤ w ≤ ref+6`**：text/mixed 补译允许右超 ≤6 格(用户规则: 右侧允许超宽)——模型译文自然扩展即可，勿硬压段宽丢语义(圧不动返回 None 落 manual)。art 列位由 `_verify_art_alignment` 另把关。
- **GLM 对纯敬称句(後藤さん？)倾向原样返回** → nl==s 判"无变化"落 manual——此类应走全局正则术语 `re:([一-龠]{1,4})さん→\1桑`，不是翻译问题。
- 补译收尾判定：复跑 refine 剩的 manual 行多为纯 art 图形误报(ｨィイ筏竿ｱ/|fてﾊ/三アニニ，无台词)属正常，别当漏译量。
- **改 refine/aa_translate 源码后验证用新 terminal 进程**：execute_code 持久 kernel 缓存 import 过的旧模块，改文件后 kernel 测试仍跑旧代码(tail=None/有值反复矛盾根因)——勿信 kernel 结果。

## refine 写回定型与验收（短宽 / 半翻 / token 归属）

**写回定型** = `_pad_line_to_src(nl, s)` + 章节收尾统一定型：任何 ok 编辑写回前 pad 到 SRC 显示宽
（短于 SRC 就补、只补不裁、超宽不动、边框行补在边框前）。历史缺陷：poly(补译) 路径只把尾段 pad 到
**段宽**、不 pad 到整行 SRC 宽 → 产出 SHORT 行（实测 10 行，85→79 / 90→67 型）。
- **清理链顺序盲区**：链是 `scan_width → fix_width → refine_aa`，**fix_width 在 refine 之前** —— refine
  新引入的短宽行没人兜。⚠️**改 refine 后必须补跑 `scan_width --work <id>` 复验**（refine 内部只查 art 列位，不查行宽）。
- **半翻可见化**：写回后仍含连续假名(≥2) 的 ok 编辑，reason 追「+残留假名」并计入 `stats["partial_kana"]`，
  汇总行打 `⚠️其中 N 行…`。校验不能只看宽度/art 列位——`swallow_translate` 会产出「日文原文+中文尾巴」共存行
  （例 `自分の処女膜を…証\u3000无效`）。专名保留与作品标题保留日文也会命中该标记，属良性。
- **校对 token 是黑账**：refine 的 deepinfra GLM 路径自建 request、**不写 usage.jsonl**（只有降级到 deepseek 时
  经 `call_deepseek` 记录）→ 任务页 token 在校对阶段几乎不动。别据此判断"校对没在跑"（看章节进度/runner_log）；
  要记校对 token 需在 `_call_glm_deepinfra` 里补 `_log_usage(tag="refine")`。
- **术语一致性验收（跨章比对）**：`--force` 重译单章后，把新译文的关键角色译名与**本作其余章**比对——
  同作内译名漂移（其余章「绁星灯」、重译章「明里/アカリ」）即不可接受，**回滚保持全作一致**。
  根因多在作品术语表：自动派生的角色条目 `zh` 字段是**日文原名**（未确认译名不猜的设计），规范中文名只在
  `role_cn` 里、**不参与注入** → 模型自由发挥。排查：读 `work/glossary_<work>.json`，看角色条目 `zh` 是否等于 `jp`。
  修法：手工把该条 `zh` 填规范译名（作品表可手改；勿用 `gen_work_glossary --force` 重派生——force 会清 auto 条目）；
  改派生规则属全局行为，**先给方案对比再动手**。
- **测试纪律**：重译/重跑 refine 前先把产物拷到临时目录（`$LOCALAPPDATA/Temp/<work>_baseline/`）+ 记 md5，
  发现问题一键回滚。实测校准（单章 `--force` 重译）：修前 SHORT 10 / 假名残留行 22 → 修后 SHORT 0 / 19（旧基线 39）。

## 校对也记账（2026-09-13）
- refine_aa 原来**两条路径都不写 usage**（GLM 路径自建 request；deepseek 路径虽走 call_deepseek 但无 tag）——校对阶段 token 在任务页是黑账。现：`_log_refine_usage()` 统一记 `tag="refine"`；deepseek 分支临时置 `env AA_USAGE_TAG="refine"` 包住 `call_deepseek`（其内部接 env 记账）后还原。
- `aa_backend._log_usage(..., tag=None)` 新增可选 tag 参数（显式 > env AA_USAGE_TAG > 配置默认）；`call_deepseek` 记 model 改 `model or DS_MODEL`（否则链上不传 model 时 usage 里 model=None）。
- 查校对成本：`usage.jsonl` 里 `tag=="refine"` 行，按 backend/model 聚合。

## 校对进度 off-by-one（2026-09-13 修）
`process_work` 原来只在**循环头**报心跳 `TR.heartbeat("校对", fi, n_files)` → 最后一章永不报"完成"，任务卡永远显示”待校对 1”（如 14/15）。修：本章 `refine_chapter` 返回**后**补一条 `TR.heartbeat("校对", fi + 1, n_files, force=True)`。已跑过的任务不会回填，重启新任务后生效。

## 校对也记账（2026-09-13）
- refine_aa 原来**两条路径都不写 usage**（GLM 路径自建 request；deepseek 路径虽走 call_deepseek 但无 tag）——校对阶段 token 在任务页是黑账。现：`_log_refine_usage()` 统一记 `tag="refine"`；deepseek 分支临时置 `env AA_USAGE_TAG="refine"` 包住 `call_deepseek`（其内部接 env 记账）后还原。
- `aa_backend._log_usage(..., tag=None)` 新增可选 tag 参数（显式 > env AA_USAGE_TAG > 配置默认）；`call_deepseek` 记 model 改 `model or DS_MODEL`（否则链上不传 model 时 usage 里 model=None）。
- 查校对成本：`usage.jsonl` 里 `tag=="refine"` 行，按 backend/model 聚合。

## 校对进度 off-by-one（2026-09-13 修）
`process_work` 原来只在**循环头**报心跳 `TR.heartbeat("校对", fi, n_files)` → 最后一章永不报"完成"，任务卡永远显示”待校对 1”（如 14/15）。修：本章 `refine_chapter` 返回**后**补一条 `TR.heartbeat("校对", fi + 1, n_files, force=True)`。已跑过的任务不会回填，重启新任务后生效。

## 校对模型链（2026-09-13）
refine_aa 只有一个模型入口 `_call_glm`，按 `_REFINE_CHAIN` 依次尝试、**前一个自身重试耗尽即降级下一个**：
- **默认链 `deepinfra,deepseek`** = deepinfra `zai-org/GLM-5.3-Flash`(主，走代理 10808、不带 reasoning_effort)
  → 官网 `deepseek-flash`(兜底，`aa_backend.call_deepseek`，已关思考、直连)。全败才抛 `refine 后端链全部失败`。
- 配置 `AA_REFINE_BACKEND`（逗号分隔；别名 glm/di/deepinfra、deepseek/ds/官网；**写单个=不降级**）；
  `AA_REFINE_MODEL` 只作用于 deepseek 项（deepinfra 路径固定 GLM，换模型要改代码）。降级时日志打
  `[refine] deepinfra 失败(...) → 降级 deepseek`。
- 单元测试 `scripts/tests/test_refine_fallback.py`：链解析 + 降级/不降级/全败抛错。
- 历史：模块 docstring/AGENTS 曾写「兜底 DSV4-Flash-0731」但代码里从来没有——2026-09-13 才第一次真正实现兜底（官网 deepseek-flash）。

## 校对模型可替换性实测（2026-09-13）
refine_aa 的模型调用只有一个函数 `_call_glm`（硬编码 deepinfra zai-org/GLM-5.3-Flash）——换模型改它即可；
**探针对比零侵入**：`import refine_aa; refine_aa._call_glm = 自己的函数`, 再 `refine_chapter(cfg, base, do_write=False)`（`cfg={aa_dir,out_dir,work_dir}` 可指临时目录）。注意 refine_chapter 需 `work/line_meta_v2/<base>.json`（只有跑过 meta 的章能测）。
**比法**（复用本文档 backup 法）：`work/backup_refine_aa_<ts>/<base>_汉化.txt`=修复前缺陷输入，`汉化/<base>_汉化.txt`=修复后标准答案；两模型跑同一输入, 比“发现行集合/动作分布/与标准答案的逐字节一致率”。
**实测（bar_miku entry_1633 差85行、entry_1641 差32行）**：deepseek 官网 `deepseek-flash`（chat 端点 + `thinking:{type:disabled}`）vs 现役 GLM——
发现行**完全相同**（1641: 46/46, 差集为空）, 动作分布相同(restore4/rebuild24), 多数行逐字节相同, 少数同义改写（「做饭用的或者烧酒用的」vs「用于烹饪或烧酒之类的」）；速度 11.7s vs 82.6s（5-7×）。→ 校对链换 deepseek-flash 可行且更快。
（入口口径同翻译：官网 /responses 端点必须发 `reasoning:{effort:none}` 才能关思考，发 `thinking` 被静默忽略。）
**假名残留不是校对质量指标**：生产版自身有 127/2516 行含假名串（SYSTEM 本来就要求专名保留日文）, 别拿它评模型好坏。

## 校对链术语注入（2026-09-07 用户规则）

**翻译/校对术语注入只读作品术语表 `glossary_<work>.json`，不读全局 glossary.json**：
- proofread._glossary_hint = 作品术语表(manual 对, 按 base 语料过滤) + 作品知识库 memory_hint（全局前20对段已移除）。
- proofread main() 无表/空表 → `_check_work_glossary` raise SystemExit 中止（文案带生成指引：① gen_work_glossary --force 零token ② gen_glossary_ai --full 完整流程）。
- refine_aa 补译/精译：`_load_gloss_hint(cfg, base)` 读作品表 manual 对按本章原文语料过滤 → 模块级 `_GLOSS_HINT`，`_call_glm` 统一拼 system——**覆盖 SYSTEM_SWALLOW/MIXED/NORMAL 里"专名一律保留日文原文"要求**（术语表词必须用标准译名，未列出专名仍保留日文）。
- **坑**：① work_dir 下 `glossary_*.json` glob 首匹配常是 `glossary_candidates.json`/`glossary_iter.json`/「副本」，必须排除再取；② 删代码段时留意**段内 import 被连带删除**（删全局段把 `import json as _json` 删了致作品表段 NameError 静默吞入 except）。

## refine vs pass2/3/fix_width 重叠量化（2026-09-07 grilling 离线重放结论）
- **互补非重叠**：refine 修的 mixed 行右侧台词残留（21105 章实测 79 行中 67 行），pass2 判据能命中但当时没清掉、fix_width 0 命中（宽度对）——不是重复劳动，是 pass2/3 对这些行的修复失效、refine 兜底。
- **失效机制（为什么 refine 才清得掉）**：pass2/3 判据是段级的（residual_text_segs 只查 text 段，抓得到），但**修复=整行送模型**（prompt 让模型保 art 整行重写）→ art 密集 mixed 行模型原样照抄/翻坏 → 判据命中但清不掉。refine 程序化 `segment_line` 切段 → 只翻台词段、art 字节零改动 → 成功。
- **量化方法（零 token 离线重放，勿真跑全链）**：refine 实际改行 = `diff work/backup_refine_aa_<ts>/entry_X_汉化.txt 汉化/entry_X_汉化.txt` 展开 a/c/d 区间（backup=覆写前，汉化目录=refine 后）；pass2 判据重放 = 对 backup 文件逐行 `aa_translate.residual_text_segs(line, aa_common.load_segments_meta(WORK/line_meta_v2, base).get(i))`（排 #/= 与 HEADER_RE）；fix_width = `FW.collect(aa_dir, 临时out_dir, files=...)`（backup 拷进临时目录当"当前汉化"）。**交集>0 ≠ 重叠**——先查"当时为何没清"（判据命中但修复失效=refine 独有价值），再谈砍层。
- **line_meta_v2 文件结构坑**：`<base>.json` 的 key 是**字符串行号**、值是单 dict（kind/confidence/reason），**不是 segments 数组**——直接 `json.load` 喂 residual_text_segs 报 `string indices`。必须经 `aa_common.load_segments_meta`（转 0-based {idx: segs}，仅含 segments 的 mixed 行）。
- **runner_log.txt 每次运行被覆盖**：只看得到最后那次任务的逐行记录——离线 diff 用 backup 目录，勿依赖 log。backup_refine_aa_<ts>/ 每目录只含该次运行触达的章。

**⚠ 2026-10-01 起历史 `backup_refine_aa_*` 已全部清理**（2542 个 backup_* 条目 / 872 MB；名单落盘 `work/tm_proofread_legacy.txt`，`aa_tm.build` 已改读它，TM 语义不变）。后果：**旧章没有"覆写前状态"可 diff**，上述离线 diff / 量化重放法只能对新跑的 refine 用；查旧章改动改用 `work/edits.jsonl`（单行 before/after）或自行先记 md5 基线。新跑的 refine/fix 脚本仍会照建 backup 目录（**粒度=每次运行一个目录**），但已自动只保留**最近 2 次运行**（`aa_translate.prune_backup_dirs`，2026-10-01 用户指定；refine/fix_mixed_drift/fix_lead_kanji/pokemon normalize 四处写入方各自调用）。
- **grilling 终版已落地（2026-09-07 用户拍板：去掉 pass2/pass3，日后维护 refine）**：translate_runner 清理链 = scan_width → fix_width → refine_aa（refine 覆盖 text 漏译+mixed 残留+art 还原）；**only_cleanup 也跑 refine**（`if do_proofread` 不再限 `not only_cleanup`）。translate_runner 的 `_dir_pass2`/`_dir_pass3` 函数与 `_PASS2_CHUNK`/`_PASS2_BLOCK_WORKERS` 常量已删。pass2.py/pass3.py 模块保留（CLI 独立跑/历史），cleanup/pass2 薄转发保留，touhou_runner 遗留仍用旧链。
- **`--no-proofread` 禁用 refine 时**：漏译残留落 review_inline/manual 人工（pass2/3 已不在链上无兜底）——禁 refine 前确认接受此后果。
- **量化方法仍有效**（diff backup vs 汉化目录求 refine 实际改行 + 判据重放）——新链回归/新模型对比 refine 效果时复用。

## 坑汇总
- **奇宽差**（差 1/3/5 格）不能纯补 U+3000（全角=2 格）——需半角空格（1 格）混合；先 rstrip 再按 `full=need//2, half=need%2` 补
- 补宽过量：`sw - tw` 是格数差，补 `(sw-tw)` 个全角空格 = 2 倍宽（错）——补 `(sw-tw)//2` 个
- ART_HIRA 黑名单（つだりへぢづゑゐいうしふとこっッ）含常用假名——**漏译检测勿复用**，残留检测才用（两者判定目标不同）
- 艺术行内嵌台词（`（__人__）ずんだもんが…`）整行替换必毁结构 → 段级替换（定位日文段 → 中文 → 保留艺术前后缀）
- export_dialogue 输出需人工抽查：L490/L543 类 mix 行可能残留少量艺术符（`丶 ) / l`），可接受（台词部分有效）
