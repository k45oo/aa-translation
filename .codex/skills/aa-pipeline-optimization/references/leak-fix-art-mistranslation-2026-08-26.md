# leak-fix 漏译补译误翻 art 行（2026-08-26 r401_metsubou 实证）

## 症状

用户报「高置信度(0.9) 的 art 行被误翻转了」。人工修改记录（edits.jsonl 的「误译」类）把若干行从中文拟声还原成 AA art：
- `υゝ ‐-‐'ノ` ← "嘿咻～嘿咻～"
- `f^Ｌ ｜ └ﾍ ヽ ￣ ノﾌﾊ トﾐ‐く ィ´/⌒ヽ` ← "嘎吱嘎吱…咯吱咯吱…"
- `│ ／ ｱ ぅさハ ｧ=≠ﾐh､Ⅵ/ │ ∨` ← "啊啦啦～好痛喔～"
- `Y ／─‐ｖ─‐ｖ‐ｧ|i ホ？` ← "咦？/ 怎么回事？"

## 排查链（排除法）

1. **line_meta 没丢**：`work/line_meta_v2/` 48 章全齐、生成时间早于翻译；`load_meta_0based` 返回 892 条映射。
2. **主翻译没误放行**：`is_translatable(idx, line, meta)` 对 entry_30613 判 114 行翻译 / 780 行不翻译，**分类器 art 行 0 条被放行**。
3. **pass3 残留检测没误报**：`residual_bad_segs`（空白名单最严）对 art 行只有 1 行（L879 真实台词「本日の投下は以上です」）。
4. **真凶 = `translate_runner.py _fix_chapter_leaks`（漏译补译）**：runner_log.txt 的 `[leak-fix]` 标签直接命中误翻行。

## 根因

`scripts/runner/translate_runner.py` `_fix_chapter_leaks`（~435-503 行）判定漏译的裸规则**完全不走 line_meta**：

```python
_KANA = re.compile(r"[ぁ-んァ-ヶ]")   # 全角假名
_ART = "／＼|()（）[]＿_⊂⊃ミ彡乂爻≧≦ｰ─━│├┤┬┴┼ﾆﾄﾘ:;,.。'\"`´~-=<>^vVヽﾉニ圭三"
...
art = sum(1 for c in nz if c in _ART)
if art / len(nz) >= 0.3: continue        # 艺术行跳过（阈值 30%）
if _KANA.search(s) and l.rstrip() == s.rstrip():   # 含假名 + 汉化==原文 → 判漏译
    leaks.append((i, s))                 # ← art 行被误判
```

实测占比（当前代码复算）：L63=29%、L254=20%、L321=29%、L472=21% —— **全部 <30% 逃过跳过**。

根因：`_ART` 字符集太小。AA 艺术常用假名/符号（ｱぅさハｧﾐ､Ⅵ∨υゝﾍﾌﾊ 等）不在集内，艺术占比被低估。而 art 行本来就「汉化==原文」，加上含全角假名（ホぅさ）命中 `_KANA` → 三条件齐备判漏译。

## 诊断法（下次遇到"art 行被翻译"先做）

```bash
grep -n "leak-fix" <work>/runner_log.txt        # 1. 找 leak-fix 补译记录
```
2. 对嫌疑行对照四口径：line_meta kind vs `is_text_line()` vs `line_kind()` vs `is_translatable(idx, line, meta_0based)`。
3. edits.jsonl 里 type=「误译」且 before 是中文拟声、after 是 art 原文的，就是 leak-fix 误翻（人工已还原）。

## 修复方向（未落地，需用户确认方案）

- **治本**：`_fix_chapter_leaks` 接入 line_meta —— kind=art 的行绝不进 leak 补译（与主翻译 `is_translatable` 口径一致）。漏译补译只对 text/mixed 行做。
- **次选**：改用 `_seg_has_real_text` 类真台词判别（区分「真漏译台词」vs「艺术纹理」）。
- **治标（不推荐）**：扩 `_ART` 字符集 —— 追不上 AA 艺术用字，还会误伤真漏译。

## 相关教训

- 「汉化行==原文行即判漏译」的模式在 art 行上必然误判（art 行本来就等于原文）——任何这类环节必须先按 line_meta kind=art 拦截。
- 用户指"误翻转"时，主翻译正确 ≠ 全链路正确：`translate_runner` 的 leak-fix / proofread / pass2/3 是独立判定路径，各自可能绕过 line_meta。
