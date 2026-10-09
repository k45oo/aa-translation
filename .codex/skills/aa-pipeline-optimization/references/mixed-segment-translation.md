# mixed 段级翻译 1 话实验（B4，2026-08-11 达标）

实验对象：キッコーマン entry_14463（167 mixed 行，含文本在 AA 左侧 + 对话框符场景）
A/B：`temp/exp_14463`（段级新流程）vs `temp/exp_14463_old`（旧整行流程），同一章同一模型。

## 达标数据（审阅 v4-pro 独立复现确认）

| 指标 | 旧管线 | 段级路径 | 判定 |
|---|---|---|---|
| 帧宽度列偏移 | 5~6 行（最大 10 列） | **0** | ✅ |
| 文本在 AA 左侧错位 | 有 | 归零（74 个左侧文本行 art 全字节保留） | ✅ |
| art 段字节损坏 | — | 0（153/167 逐字节验证） | ✅ |
| 台词残留（conf≥0.65 段） | 3 行补译失败 | 0（1 行回退待人工） | ✅ |
| token | 62,778 | 52,252（**-16.8%**） | ✅ 额外收益 |

## 关键实现

- **B1 段边界**（line_classify_v2）：mixed 行输出 `segments:[{start,end,kind,src_width}]`，半开区间 `[start,end)`、覆盖整行无空隙、空白并入相邻 art 段、低置信段带 confidence。`FACE_KANA` 装饰假名集合 → 低置信，不送模型。
- **B2 段级翻译**（aa_translate）：只发 conf≥0.65 的 text 段（格式 `行号_段号|译文段`），本地按 segments 拼回，art 段原样保留。`AA_USE_MIXED_SEGMENTS` flag 默认 0。
- **B3 段级 pad**：`pad_segment(tr, seg_width)` U+3000 补**段尾**（下一个 art 段之前）→ art 段起始列不变。
- **回退安全网**：段级失败（宽度/残留/copy_fail）→ 回退整行走旧逻辑，11/11 恢复，无静默漏译。

## 迭代降回退率（47% → 6.6%）

- 迭代 1：装饰段也送模型 → 回退 78/167（47%）
- 迭代 2：**装饰段不送模型**（FACE_KANA 集合）+ **copy_fail 仅真台词段生效** → 回退 11/167（93.4% 段级直成）

## 测量陷阱（必须用宽度列，勿用字符索引）

帧偏移校验用 `wcol_of_frame()`（按 disp_width 语义累加：ASCII/半角假名 1 格、其他 2 格），**不能 `str.index`**——行首空白+台词混合段，模型丢空格后 pad 补段尾，字符数变了但宽度列不变，字符索引会虚报偏移。

```python
def wcol_of_frame(line):
    w = 0
    for c in line:
        if c in FRAME: return w
        w += 1 if ord(c) < 0x100 or 0xFF61 <= ord(c) <= 0xFF9F else 2
    return None
```

**帧字符集必须严格（2026-08-11 对照页实测误报）**：`FRAME_FULL = set('│｜┃┘└┐┌┓┏┚┙」』】|')` 会把日文引号 `『』`（以及 `」』】` 引号类字符）当帧线 → 正文中 `『敵国』` 这类**日文引号**位置移动（文本内容正常变化）被误报为"帧偏移"（L89/L92/L245/L777 全中招，NEW 虚报 5 处）。**严格帧线集只含真正的框线/管道字符**：

```python
FRAME_STRICT = set('│｜┃┘└┐┌┓┏┚┙|')
```

用严格集重测：NEW 偏移 = 0、OLD 偏移 = 7（与实验报告一致）。结论：引号位置变化是文本内容，不是框错位；验证帧线对齐必须用严格集。

## 单话对照渲染（temp/render_exp_compare.py，2026-08-11 建立）

三栏对照 HTML（SRC 原文 / NEW 段级 / OLD 旧管线，各一 `<pre class="aa">` 等宽块）用于浏览器实测帧线对齐。复用 `gen_aa_html.render_line` + `sites/<site>.render_header` 保证与交付渲染同语义。生成：`python temp/render_exp_compare.py` → `temp/exp_14463/entry_14463_对照.html`。

**坑 1：空行双倍渲染**。`parts.append('<br>')` 作空行占位 + 最后 `'<br>'.join(parts)` → 空行占位符再被 join 的 `<br>` 分隔，空行数 ×2（119→238，渲染行 955 vs 源 836）。**修复：空行占位 append('')**（join 自然产生分隔），空行数即一致。

**坑 2：浏览器侧行拆分**。页面用 `<br>` 换行时 `pre.textContent.split('\n')` 得到整块单行 → 必须先 `br.replaceWith('\n')` 再取 textContent 按 `\n` 拆。

**坑 3：帧列检测用浏览器 console 与 Python 双口径**。console 的 wcol 与 Python 版必须同语义（宽度列累加 + 严格帧集），否则视觉模型/console 的"偏移"报告与代码验证矛盾时无法归因。浏览器视觉模型对三栏对照的"偏移"判断不可靠（会误读引号、滚动位置、缩放差异）——**以代码级宽度列验证为准，视觉截图仅作人眼复核**。

**坑 4（2026-08-11 加测会话新增）：对照 HTML 数据源时间戳陷阱**。用户反馈"行首空格丢失"→ 排查发现对照 HTML（18:28 生成）用的是**修复前产物**（18:25 seg_before_fix），而修复后产物（19:24）行首空格已完好——渲染脚本 `render_exp_compare.py` 读**固定路径** `temp/exp_<eid>/汉化/entry_<eid>_汉化.txt`，重跑实验覆盖文件后 HTML **不会自动更新**。**排查顺序（血泪教训）**：先 `ls -la` 对比 HTML 生成时间 vs 各候选产物 mtime → 确定 HTML 实际用的数据源（逐行对比 HTML 列内容与磁盘文件）→ 再判断是代码问题还是数据过期。本会话浪费了大量时间先怀疑 rebuild 代码，实际是渲染脚本用了旧文件。重新生成后 836 行行首空格 0 丢失（仅末尾 3 行纯文本尾注 lead 差 1 属整行旧逻辑）。

**坑 5：行首空白保护（B3 补丁，commit fe30d19，2026-08-11）**。用户实测发现"文字在左侧的 mix 行文字左边的空格没了"（如 L178 行首宽 42→0）：模型吞掉 text 段行首 U+3000，`pad_segment` 只补段尾 → 文本整体左移。修复在 `rebuild_mixed_line()`：**拼回时先从 `orig_line` 对应位置截取该段行首空白（lead），再拼译文（模型译文先 `lstrip(' \\u3000')` 防双份），最后 pad 补段尾到 `src_width - lead宽 - 译文宽`**。验证：SRC/NEW 行首空格 0 丢失（'原来如此…' lead=23、'那是用于A级' lead=11 浏览器实测一致）。注意：**OLD 旧管线行首空格也可能与 SRC 差 1 格**（整行旧逻辑的 pad 行为），对比时以 SRC 为准。

## 台词切碎修复（_merge_narrow_kanji_art，2026-08-11 用户实测反馈）

**触发**：1 话实验"达标"后用户实测交付 HTML，发现段级路径出现错位空格：
- `确实频率相当　　　　上增加了`（旧管线正确：`确实频率上升了不少`）
- `什十么　　一只要对方想要` / `几十体里混一体对方想要的`（原文：何十体かに一体くらいは相手が望む）
- 纯文本行也被插 U+3000：`…也可能发生　　　　　　十二完全有可能`（原文：…十二分にあり得るのだ）

**根因**：`ART_KANJI` 黑名单含台词高频汉字（上/十/一/十二 等——它们在 AA 艺术中也常见故入黑名单），`is_art_char` 把台词里的这些字判为 art 段 → 完整台词切成多段（如 `たしかに頻度はかなり` + [上]art + `がってます`）→ 每段独立翻译 + 段尾 pad → 语义断裂 + 段间 U+3000 堆积。**「宽对框歪」修复后暴露的新盲区：段边界本身切错了。**

**修复**（`line_classify_v2.py` 新增 `_merge_narrow_kanji_art`，segment_line 末尾调用）：
```python
def _merge_narrow_kanji_art(segs, row):
    # text-art-text 三合一：art ≤6格 且 内容全部是 ART_KANJI 汉字 且 两侧至少一个真实台词
    # 连续链（text-art-text-art-text…）全部并入前段，i += 2 跳过后 text 段
    # 仅纯汉字段合并；ASCII/边框/假名窄段（:、|、━、ﾆ）是真实装饰，不合并
```
条件细节：
- art 段 `src_width <= 6`（≤3 个全角字）
- 内容 `all(('\u4e00' <= c <= '\u9fff') and c in ART_KANJI for c in art_txt)`
- 两侧 text 段至少一个有**真实台词**（`has_real`：非艺术假名/非 FACE_KANA 的假名 或 含汉字）——防装饰假名段（ヽ/ｌ⌒ヽY）被误并
- 合并 = `out[-1]['end'] = segs[i+1]['end']`（前段 end 扩展到后段 end），src_width 重算

**验证**（ad-hoc，11/11 过）：
- L76/L164/L727 台词主体单段完整（`has_text_seg_containing` 断言——**勿断言"整行只有一个 text 段"**，装饰段独立是正确行为，会误报 FAIL）
- ASCII 夹缝保留为 art（` 　たしかに: まだ| 確認` 中 `:`/`|` 仍是 art 段）
- 全语料段覆盖 0 空隙；高置信侧窄汉字夹缝清零（剩余 57 处全是帖头星期 `(土)` 或艺术纹理，has_real 门禁正确挡住）
- 三行译文修复 + 帧偏移 NEW=0 + 宽度错位 0 回归
- 生产 meta 同步：`line_classify_v2.py --work kikkoman --chapter 14463` 重新生成（实验副本 cp 过去）→ 重跑 `temp/exp_mixed_segments.py` → 重新生成对照 HTML 给用户复核

**流程沉淀（用户反馈驱动修复的闭环）**：用户实测报问题 → grep 目标译文定位行 → 打印该行 segments 看切分 → 定位分类器误判 → 修 segment_line → 本地验证（不重跑全量实验，先 segment_line 单行断言）→ 重生成 meta → 重跑翻译实验 → 代码级指标回归 → 重渲染对照 HTML → commit（af1dfd1）。用户会用交付 HTML 复核每个修复——**达标报告 ≠ 用户验收**。

## 加测 2 话（2026-08-11 续跑，entry_17512/18015 全指标达标）

用户反馈"文字在左侧的 mix 行文字左边的空格没了"后，kanban 续跑卡（t_f028ba54）加测 mixed 行最多的 2 话验证修复普适性：

| 指标 | 14463 | 17512 (324 mixed) | 18015 (313 mixed) |
|---|---|---|---|
| 宽度错位 | 0 | 0 | 0 |
| mixed 行首空白丢失 | 0 | 0 | 0 |
| **段级路径**帧偏移 | 0 | 0 | 0 |
| art 段字节损坏 | 0 | 0 | 0 |
| 台词残留 | 0（人工修 3 行回退漏译 + 白名单补角色名シロエ） | 0 | 0 |

- 17512/18015 v3 meta 用 `line_classify_v2` 生成，实验跑在 `temp/exp_<eid>/` 副本（**生产 `汉化/` 零触碰**，git diff 验证）
- 成本：126 calls / ~290K tokens，回退率 76.6-87.4% 直成（copy_fail 主导系模型照抄顽疾被正确拦截）
- **帧偏移口径修正（重要）**：回退行（段级失败 → 旧逻辑整行翻译）的"宽对框歪"是**旧逻辑已知问题**，不计入段级路径偏移——校验脚本按 `idx in fallback_lines`（mixed_fail_log.tsv）分流统计 `frame_shift`（段级）vs `frame_shift_legacy`（回退），段级路径必须 0、回退行另列。17512 首轮报告 14 行帧偏移全部是回退行（L50 例外深入调查后确认也是回退/脚本版本差异），口径修正后段级路径 0
- 报告：`doc/第二阶段_实验报告_3话mixed.md`；对比 HTML：`temp/exp_17512/entry_17512_对照.html`、`temp/exp_18015/entry_18015_对照.html`

## fix_width 段级同步（2026-08-11 commit 0335fb3，审阅 gap 闭环）

**问题**：fix_width 旧实现把整个 mixed 行（含 art 段）丢给模型整行重译 → 模型可能改动绘画字符。**修复**（`aa_translate.py` 新增 `fix_width_mixed_lines(rows_by_file)`，`fix_width_kikkoman.py` 改造）：

- **只重译 conf≥0.65 的 text 段**（格式 `行号_段号|片段`），专用 prompt `FIX_MIXED_SYSTEM` 要求「译文必须比原文片段更短或等长」；art 段/低置信段本地原样保留
- rebuild 用 `rebuild_mixed_line(src, segs, trans)`（复用 lead 保护+pad），**整行宽 == SRC 才接受**；3 轮重试，失败落 manual 清单
- collect()：`AA_USE_MIXED_SEGMENTS=1` 时才读 line_meta_v2 segments（flag off 行为完全不变）；`fix_batch` 拆分 mixed（段级）/普通行（旧整行重译）
- **影响面**：仅 kikkoman 有 v3 meta；fc2/白头 meta 无 segments → 自动走旧逻辑零影响（实测さーきー FC2 73 个 meta 全是 v2）
- 顺带修 bug：failed 列表曾重复 append（`for-else` 分支 + 循环后 `if` 双写）→ 统一循环后单一 `if (fname, idx) not in fixed`
- **验证模式（零 API）**：monkeypatch `T.call_api` 返回固定译文/原样照抄，断言 fixed/failed/宽度还原/art 段逐字符保留——段级逻辑的标准单测手法（12/12 ad-hoc 过）

## 遗留 / 翻 flag 状态（2026-08-11 flag 已翻）

1. ~~**fix_width.py 未同步 MIXED_SEGMENTS**~~ → **已修复（commit 0335fb3，见上节）**
2. ~~**flag 翻默认开**~~ → **已执行（commit 535b169，2026-08-11）**：`AA_USE_MIXED_SEGMENTS` default_on False→True，pass2/pass3 同步（三处一起改，缺省 True；显式 `=0` 关闭回滚）。验证 15/15 ad-hoc：三态（默认开/env=0 关/env=1 开）、pass2/pass3 `_flag_on` 默认开、meta 目录不存在/文件不存在/损坏 → `{}` 回退全行（白头/saki 无 v3 segments 零影响）、残留检测三态（text 干净 0 报 / text 假名报 / 无 segs 回退全行）、fix_width collect 默认开时带 segs。unittest 96=93+3 既有失败与基线一致（stash 对比）。
3. **さーきー FC2 73 话未生成 v3 meta**（推广前补跑，零 API）——FC2 meta 实测仍为 v2 无 segments，翻 flag 后走旧逻辑不受影响（自动回退），但享受段级收益需重生成
4. art 段差异 4 行 = 分类器段边界精度问题（非模型损坏），推广前用 quality_gate 多章验证
5. kikkoman 4 文件 21 段历史残留（M13 遗留）；review_inline L692 待人工回填
6. 渲染验收按宽度列口径（字符索引会误报）

## 2026-10-10 口径改版：段级校验从「整行否决」改「部分成功」（关键教训）

**症状**：某作品「大量漏译」，人工逐行修。**根因不在模型**：段级协议把**合法译文**判失败，
失败行 `missed` → 落 `review_inline.txt` → 成品里就是**原样日文**（静默）。实测该作段级候选
13,958 行里 **8,684 行(62%) 判失败**，其中 1,982 行成品至今是原样日文；同批整行协议只 2.2%
（差 28 倍）。全库 54 部作品 review_inline 共 255,222 行，**83% 来自这条路径**。

**三个误杀源（真机复现，entry_2996 送 50 行只产出 7~10 行）**：

| 原因 | 占比 | 机制 |
|---|---|---|
| `seg_shrink` | 72% | `原段宽 - 译文宽 > 4 列` 判吞字 → 整行失败。**中文天生比日文短 30~50%**（助词+2格假名），实测被拒的全是正确译文（18→8 / 42→34 / 28→18）；段内留白由 `pad_segment` 本地补位，art 列位不受影响 → 该判据在段级路径上「保位置」的价值为 0 |
| `copy_fail` | 26% | 模型原样照抄判漏译，但被照抄的「段」是**画面笔画**（`斧竿丐`/`汀うハ`/`ィ「ユユェェュ`）——分类器把这些字当台词（`ART_KANA_CHARS`/`FACE_KANA` 黑名单没收录该作用的 ユ/ェ/ュ 与生僻汉字）→ conf 0.92 送模型 → 照抄（正确）→ 整行失败，**同行的真台词一起陪葬** |
| `seg_wide` | 2% | 窄框放不下，重试却复用**同一份 SYSTEM_MIXED**（模型不会变短）→ 3 轮必败 |

**方法（可复用到任何「阈值型误杀」问题）**：

1. **拿人工修订当地面真值标定阈值**：`work/edits.jsonl` 里被人手改过的行（= 被协议拒掉、人译好的段）
   算中/日宽度比 → 中位 0.86、1% 位 0.56、**最低 0.33**；而**已知真吞字**样例 0.40
   ⇒ **合法压缩与真吞字重叠，任何单一宽度阈值都只能二选一**（保合法=放吞字）。结论：该判据只能
   降级为「极低比才否决 + 中间带进复核清单」，不能靠调参。
2. **消融实验**：`probe_variants.py` 逐项开关（放宽阈值 / 剔艺术碎片段 / 部分接受）分别量产出率，
   避免把「串行改进」误判成单点收益（实测放宽 shrink 单独就把 9/50 → 44/50）。

**改版口径（现行）**：

- 宽度只否决「比 < `MIXED_SHRINK_MIN_RATIO`(0.35, env `AA_MIXED_SHRINK_MIN_RATIO`)」，
  「比 < `MIXED_SHRINK_FLAG_RATIO`(0.6)」只记 `work/mixed_leftover.txt` 复核；
- 照抄分两态：**有决定性台词证据**（复用 `_rule_dialogue_run_ok`）→ 重试；**无**（画面纹理）→ 原样保留，
  **不判失败**；
- 重试**按失败原因换提示词**（`MIXED_COPY_HINT` / `MIXED_SHORT_HINT` / `MIXED_COMPLETE_HINT`）；
- **末轮接受部分成功**：不合格段退回原文、整行照常产出（`AA_MIXED_PARTIAL=0` 回滚），
  有真台词退原文的仍计 `missed` → 落 review_inline（**不再静默**）；
- **只重发失败段**（`trans`/`keep` 跨轮累积）——旧实现每轮重发整行全部段，确定性失败必然烧满 3 轮
  （段槽位调用 −57%）。

实测（同批同输入）：60 行产出 **10 → 60**；存量修复跑完全作 30 章：**送协议 3,161 行 → 写回 1,565 行，
成品残留日文行 1,229 → 194（−84%）**，30 章读回复核 0 不符；抽查三章「新增宽度违规 0 / 新增 art 列位异常 0」。
分类器侧 `line_classify_v2._demote_non_dialogue_text_segs`
把 `_max_real_dialogue_run==0` 的 text 段降为 art（本作 82,462 个 text 段里 63.7% 是纯装饰
`ヽ/‐/∨/＞/ｊ/Ｖ`），char 级实测误杀仅 1 例（`ミニ`，本就是画）。

**踏坑（血泪）**：

- **`review_inline.txt` 的 `line N` 是 0-based 索引**（= `translate_lines_mixed` 的 `num`；实测 415/415
  行与 `src[N]` 逐字节相同），而 refine_aa 写的那批可能是 1-based → 工具必须按「**记录原文 == src[N]**」
  判定并**只在一处**做 ±1。曾两边各减一次 → 取到隔壁行，而「宽度恰好相等」时覆盖率判据不会报警。
- **末轮部分成功必须无条件尝试**：`cand is None`（段没译出来 → 拼不回去）时也要走「全部退原文」的兜底，
  否则 line 永远拿不到。（首版写 `if cand is not None and round>=2` → 一行都救不回来。）
- **压缩失败的降级顺序**：只有「所有段都已有译文仍装不回去」才逐段退原文（先退最宽的）；
  还在重试的段不能当作「放不下」被退掉（否则把已译好的别的段一起退掉）。
- **人工复核清单要分级**：「真台词退原文」(revert/fit) 才算漏译报 `missed`；「译了但偏短」(shrink_flag)
  只进清单不报 missed，否则 review_inline 会被灌满（实测比 <0.6 的段占三成）。
- 改判据会连带影响**依赖旧失败表现的工具**：`fix_lead_kanji_miss.py` 原先靠「不在 fixed」判 model_fail，
  现要改成「目标段没变 = model_fail」；它的 `--shrink-ratio` 也要映射到新 env。

**改版后的回归审计（配方，改「会不会把画译成中文」类判据时必跑）**：

新口径把「原样照抄」从失败改成「重试并要求译出中文」，代价是**画面笔画段可能被写成假中文**——
校验只看宽度/列位与 art 段字节，而误判为台词段的笔画**不属于 art 段**，列位检查保护不到它。
审计用**逐段按显示列切片**（宽度守恒 ⇒ 列可直接对齐，字符索引不可用）比「改动前产物 / 改动后候选」，
再判该段**源文**：

```python
# 高置信 text 段：改动前(产物) != 改动后(cand) 且源段无实质平假名 且改动后含汉字 → 可疑
jp = src_line[sg['start']:sg['end']]
zh_a = col_slice(tr_before, sg['start'], sg['end'])
zh_b = col_slice(cand,      sg['start'], sg['end'])
# 实质平假名 = 非 ART_HIRA、非小写促音拗音
susp = (zh_a != zh_b) and not has_real_hira(jp) and re.search(r'[\u4e00-\u9fff]', zh_b)
```

实测本次改动 26~百余个段 **0 例可疑**（含片假名台词如 `セックスだ…` 被正确排除在"可疑"外）；
另注意「源段无平假名」会把卡壳音节/拟声（`乙ぅぅ乙`）也报出来，抽样人工看一眼即可，不要为它加白名单。

## 相关文档

- 报告：`doc/第二阶段_实验报告_1话mixed.md`
- PRD：`doc/AA翻译管线优化_PRD_第二阶段.md`（G6/G8 目标、M8-M13 里程碑）
- 同步记录：`doc/同步记录.md` 2026-08-11 条目（M8 + B1-B4）
- 实现：`scripts/tools/line_classify_v2.py`（B1）、`scripts/aa_translate.py`（B2/B3）、`scripts/common/aa_common.py`（residual_*_segs/load_segments_meta）
