# mixed 行错位根因修复全记录（2026-09-01，commit 622ceb9a）

ドスケベナイトあかりちゃん 84 条人工修正驱动。目标：mix 台词错位 + AA 行误翻的**根因级**修复，不是"丢人工"兜底。

## 症状与数据

- 84 条 edits.jsonl = 63「错译」+ 11「错位」。
- **11 条错位全部"宽度==SRC 但中文列位置错"**：
  - 29785:257 SRC `ぱんっ！` 列 95 → 译文 `啪！` 列 60
  - 29785:59 SRC 台词列 136 → 译文列 101
  - 29858:522 SRC `トエエエエエイ` 列 76 → 译文 `托欸欸欸` 列 38
  - 即「宽对框歪」：`disp_width 相等 ≠ 视觉不错位`。段级 pad 补段尾、总宽对，但 art 段被推右。
- 63 条错译 = AA 艺术行被模型当台词翻译（`Blue Mountain`→蓝山咖喱、`ハハハ`→哈哈哈、`ｨぅうみ､`→唔嗯、`｀ー-`→装饰被译），人工还原日文原文。

## 根因（字符级）

**`is_art_char` 把装饰字符判 text → 送进翻译模型**。实测 meta：
- `｀`（U+FF40）、`ー`（U+30FC）、`⌒`、`l`、`U`、`（`、`ノ`、`：` 全判 text
- 29785:257 的 `｀`/`ー`/`｀⌒⌒` 被标 text 段 conf 0.55-1.0 → 送模型
- 全 34 章：320 段高置信装饰段（conf≥0.65）误送模型 → 模型改字符/落错列

## 判定：B2 回退 vs 分类器（哪个是主因？）

初判 B2 整行回退是主因（mixed 失败→整行重译→art 进模型）。**数据推翻**：11 条错位 before 宽度全 ==SRC，说明**段级路径本身产出就错位**——不是回退产物。真正主因 = **分类器把装饰字符划进 text 段**。B2 回退是次要路径（同源防护）。

## 修复（三层）

### 1. 分类器 `line_classify_v2.py`

```python
DECOR_SYMS = set('｀´⌒⌢⌣〜¨˙lIiUu')   # 弧线/线条装饰 → is_art_char 判 art
DECOR_HANZI |= set('衛滯灘鑾眦奝奲娚姶嫐嬢宀丿乀乁彡癶麤龘龖爨鬱麐麕')  # 纹理汉字
CJK_COMPAT_KANA = set(chr(c) for c in range(0x31F0, 0x3200))  # ㇰ-ㇿ
CJK_SYMBOLS     = set(chr(c) for c in range(0x3300, 0x3400))  # ㌀-㋿
CJK_EXT_A       = set(chr(c) for c in range(0x3400, 0x4DC0))  # 扩展A生僻字
# is_art_char: 以上三组 + DECOR_SYMS 判 art
```

`_seg_confidence`：
- `kanji` 排除 DECOR_HANZI（纹理汉字不算台词）
- `real`（真实假名）排除：`ー`(U+30FC 长音，孤立是装饰)、拗音小假名 `ぁぃぅぇぉゎゃゅょっ`（后缀，独立出现=装饰）
- 无 kanji 且无 real → conf 0.4（不送模型）
- **小假名开头段**（`ぅて`/`ぅてぅ` 装饰嘴型）→ conf 0.4；**`real` 必须先算再引用 `len(real)`**（曾 NameError）
- `_has_dialogue` 排除 DECOR_HANZI（防艺术行判 text）

### 2. 段级兜底 `aa_translate.py`

```python
def _compress_seg_to_width(t, target_w):
    # 段译文超宽本地压缩（零 API）：删段尾/首 U+3000 → 删段内连续 U+3000 → 全角标点半角化
    # 必须精确 == target_w，否则 None（调用方重试/落盘）

def _verify_art_alignment(orig_line, cand, segments):
    # 按 segments 宽度列定位 art 段，cand 同列区间必须仍是原 art 字符
    # 防宽对框歪（disp_width 相等但 art 被推右）
```

`rebuild_mixed_line` 分流：**不足宽 → `pad_segment`（补段尾）；超宽 → `_compress_seg_to_width`（压缩）**。
- **坑**：`pad_segment` 超宽时直接返回超宽译文（`if w >= seg_width: return translated`），会把后续 art 推右——必须超宽走压缩。
- 曾实现「只压缩不 pad」导致不足宽丢 6 格，5 个测试失败（`test_art_segments_byte_preserved` 等）——不足宽仍要 pad。

`translate_lines_mixed` 整行校验：`disp_width(cand) != disp_width(line) or not _verify_art_alignment(...)` → 失败重试。

B2 回退：mixed 失败行不再整行重译（防 art 进模型）→ 落 review_inline 带 `[mixed-fallback]` 标记。纯 text 行维持现状（无 art 可破坏）。

### 3. 测试 +10（test_mixed_segments 37 全过）

`TestCompressSegToWidth`（5：fits/删尾U+3000/删段内/标点半角/不可压None/精确）、`TestRebuildMixedLineOverwide`（2：超宽压缩/不可压None）、`TestVerifyArtAlignment`（2：对齐过/推右失败）。

## 实测结果

- 全 34 章高置信 text 段：**6195 → 5751（Δ-444 装饰段不再送模型）**
- 真台词段全保留：`ぱんっ！`/`やる夫くんとのセックスです♥`/`必死で今も抑えている…` conf 1.0
- entry_28594（错位最多，45 装饰段）真实重译（`translate_runner --chapters 28594 --force --no-dl`，408s）：宽度错位 0、art 行 0 改动、仅 2 个 mixed 行微调（1 处 `ぅて` 装饰误翻已拦、1 处 pad）
- 全量 250 测试：test_mixed_segments 37 全过；test_line_meta 2 失败为**用户 glossary.json 移出白頭**（git stash A/B 实证与本次无关）

## 踩坑清单

1. **方案方向被用户否决**：首版「mixed 失败行原样保留」→ "这根本不算什么方案纯粹是垃圾"。教训：用户要「自动翻译且不错位」，任何丢人工的兜底都不是方案。先诊断根因（宽度==SRC 但列错 = 分类器问题），别急着设计兜底。
2. `_compress_seg_to_width` 只压缩不 pad → 丢 6 格测试失败；`rebuild_mixed_line` 必须不足 pad/超宽 compress 分流。
3. `_seg_confidence` 小假名开头判定引用未定义的 `real`（顺序错）→ NameError。
4. `CJK_EXT_A` 是大 set（~6592 字符）模块级常量，可接受；`街` 等单字常用字夹纹理仍 conf 1.0（宁漏勿伤，不枚举）。
5. 验证「被移除装饰段」勿用 `(idx, si, text)` 三元组集合差——装饰段移除后段下标重排，同一台词段下标变化会**误报真台词被删**（entry_28594 曾误报 129 段）。按行号+内容文本对比。
6. 重译验证前必须 `cp -r 汉化 备份`；`--chapters 28594` 只译该章，但 extract_dialogue 台词表是全量刷新（DONE: 34 文件更新 是台词表不是翻译）。
7. `git stash` A/B 回归归因：stash 全量（含用户 glossary 改动）后测试过 ≠ 我的改动无责；按 `git diff --stat` 分开归因（我的 3 文件 vs 用户 glossary/roles/接口文档）。

## 后续

- `圷` 等低频生僻字纹理仍可能送模型（宁漏勿伤取舍）
- test_line_meta 2 断言过期（白頭移出全局）需用户确认后更新
- 方案文档 `doc/mixed左侧台词错位优化方案_2026-09-01.md`（已 commit）
