# 术语表注入 A/B 实验（2026-08-21）

背景：评估 M4b 的 glossary 注入（`AA_USE_GLOSSARY`，默认已开）是否值得保留/改 work 过滤。
对比维度：token 消费 / 速度 / 质量 / 翻译模型支持。

## 现状（代码事实）

- `translate_lines()` 只做一次注入：`call_api(SYSTEM + glossary_prompt_hint(), body)`（round1）。
  **补译轮 differs：`hint_sys = SYSTEM + "【补译】..."` 不带 glossary hint**。
- `glossary_prompt_hint()` 注入**全部** glossary 对（glossary.json 的 `glossary` 数组，现 38 对），
  通过模块级 `_glossary_pairs_cache` 缓存。flag 由 `glossary_enabled()`（`AA_USE_GLOSSARY`，默认 open）控制。
- `translate_lines(flagged)` 把整个 flagged 列表拼进**一个 body**（`"\n".join(f"{i:05d}|{l}")`）——
  50 行分块发生在 process_chapter（FLAG_PER_CHUNK）层，**别直接对 >50 行调用 translate_lines**（会截断）。

## 已实测数据（muse-spark / 白头 35828_虚空のエントランス.txt，120 行整包）

| 条件 | prompt/call | completion/call | total | 备注 |
|---|---|--:|---:|---|
| off | 6,318 | 7,641 | 13,959 | round1 大块 |
| off | 10,940 | 5,370 | 16,310 | 补译/次轮 |
| full(38对) | 6,616 (+298) | 7,858 | 14,474 | **≈38对 × 8 tok/call（+4.7%）** |
| work(14对) | 6,439 (+121) | 6,974 | 13,413 | |

- **muse-spark 单 call 延迟 24~78s**（技能旧基准 4s/块是空闲时段）——速度对比必须当次实测。
- work 过滤法：`[p for p in ALL_PAIRS if p[0] in 作品语料 or p[1] in 作品语料]`，baitou 38→14：
  灰かぶりの魔女 極点の魔女 白頭 やる夫 乙でした フラグ 名無し 初見 まとめ 職人 ロン ポン チー 鳴き。

## A/B 实验模板（可复用于「N 后端 × flag 状态」）

要点（temp/exp_glossary_inject.py 已实现）：
1. **固定输入**：从某章取 is_text_line 台词行（跳 `#`/`=====`/帖头），cap 60 行；各条件同输入。
2. **控制注入集合**：off → `AA_USE_GLOSSARY=0`；full → `A.FLAT_MOD._glossary_pairs_cache = ALL_PAIRS`；
   work → `_glossary_pairs_cache = 过滤后对`。用后复位 `= None`。
3. **token**：每条件独立 `AA_USAGE_FILE` jsonl + `AA_USAGE_TAG`，聚合 prompt/completion。
4. **质量**：输出文本里 zh 术语出现计数（zh_count）+ 残留 jp 术语计数（jp_kept）+ missed；
   translate_lines 返回的 fixed 已过宽度/残留校验，宽度天然 0；「模型遵循度」= full 相对 off 的 zh 命中增幅。
5. **速度**：`time.time()` 包住 translate_lines。
6. **长任务**：结果逐条件增量 append `results.jsonl`（中断可续 + 后台 notify），别一把梭全内存。
7. **漂移检查**：对不含 jp 术语的行，比 off vs full 的译文相同/不同数量（注入不应扰动无关行）。

## 待续结论

会话结束时 mimo / runinfra-deepseek-v4-flash 两后端对比未跑完（muse 太慢占满前台超时）。
下一步：切小块(60行) + 后台跑完 3 模型 off/full（可加 work 只有 muse），出四维表后定：
① 是否保留注入（默认开）；② 是否加 AA_GLOSSARY_WORK_FILTER 按作品过滤注入集合。
