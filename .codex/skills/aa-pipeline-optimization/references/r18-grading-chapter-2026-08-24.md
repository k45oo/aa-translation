# R18 分级与章节级判定 — 2026-08-24/25 落地

> 场景：R18 作品不能整部都走 r18 后端（浪费 zhipu 免费 normal 后端），需**两级制**——高含量作品级 / 低含量章节级。方案定稿见 `doc/R18分级与章节级判定方案_2026-08-24.md`。
> 前置：references/r18-auto-backend-selection-2026-08-19.md（单布尔 r18 时代）——本文件是其分级扩展。

## 一、R18 两级制（work_meta.json `r18_level` 三态）

```json
"r18_level": "high" | "low" | "",
```
- **high**（作品级，人工标 8 部）：akari_ninshin / akari_haisin / pon_nikutsubo / pon_slime / yaruohiroba / fukou / touhou_teisou / last_dungeon → **整部**走 r18_backends，占比显示 100%
- **low**（默认，registry r18=True 且非 high）：jikan_teishi / sujin_otona / sujin_dungeon / sujin_saigo / sujin_voiceroid / sujin_shabu / yaruobook 等 → **章节级判定**驱动后端
- **空**：registry r18=False（非 R18）→ 不 gate

**迁移脚本一次写齐**（勿手工编辑 30+ 部）。迁移时**补登记 meta 缺的作品**（pon_nikutsubo/pon_slime 曾不在 work_meta，r18_level=high 的白名单要有兜底登记）。

## 二、词表校准（数据驱动，绝非拍脑袋）

**方法**：阳性对照（含量≈100% 的 high 作品）+ 阴性对照（用户确认无 R18 的作品）跑一遍，词表按**区分度**重排。

| 对照 | 作品 | 结论 |
|---|---|---|
| 阳性 | akari_ninshin / akari_haisin | 词表应 100% yes |
| 阴性 | saki_fc2 / jinrou / sujin_otona / sujin_shabu | 应 ~0%（低含量） |

**关键修正**（实测发现三类问题）：
1. **子串误撞**：`性交` 撞「性交際/性交遊」=异性交往；`ソープ` 撞「ボディーソープ」=沐浴露 → 改正则 `性交(?!際|遊)`、`(?<!ボディー)ソープ`。**词表改成正则 RE 列表而非 `set`，支持排除模式**。
2. **STRONG 单次命中太敏感**：sujin_otona 第1话仅「えっち」×1+「過激」×1 被判 yes（日常调侃）→ **STRONG ≥2 次命中才 yes**（akari 系列 STRONG 大量命中 200+/140+ 不受影响）。
3. **低区分度词降 SOFT**：おっぱい/パンツ/下着/絶頂/巨乳/貧乳/童貞 等在阴性作品也大量出现（麻将选手身材调侃等）→ 从 STRONG/NORMAL 降到 `R18_SOFT`，**只作浓度统计/佐证，不 gate yes**。

判定规则（定稿）：STRONG ≥2 次 → yes；NORMAL ≥3 个不同词 → yes；单次 STRONG 或 1~2 NORMAL → suspect；SOFT 不 gate。
**NORMAL 只留中等区分度词**（処女/乳首/射精 等阳性有、阴性极少）。

校准结果（8 部对照实测）：akari 系列保持 100% yes；saki_fc2 30%→1%、jinrou 2%→0%、sujin_otona 20%→0%（误判消除）；jikan_teishi 18%（长篇大部分日常+少部分H章，可靠）；yaruobook 86%（章节含露骨词但每章浓度低——A/B 双指标分别体现章节数和浓度，都成立）。

## 三、章节级后端驱动

**判定入口** `_chapter_work_r18(base, cfg)`（aa_translate.py）返回 `(work_r18, low_r18)`：
- high → `(True, False)` 整部 r18 链
- low + yes 章 → `(True, True)` **zhipu 优先**（低含量省钱），1301 拦截自动降级
- low + no/suspect → `(False, False)` normal 免费
- 无 r18_level → `(None, False)` 旧行为读 env

**调用链透传**：`call_api(work_r18=None, low_r18=False)` → `translate_lines/translate_lines_mixed` → process_chapter。多章并行安全（每章独立判定，不依赖全局 env）。

**每章重置降级记忆**：`aa_backend.reset_preferred_backend()`（process_chapter 开头调用）——防上章降级污染下章（尤其 low 作品 yes/no 章交替时）。

**call_with_fallback 加 `low_r18` 参数**：True 时 chain = `[zhipu] + r18链(去zhipu)`。⚠️ **必须同时**让 low_r18 时**忽略显式 AA_BACKEND**——否则 R18 作品 main 里 `auto_backend(cfg['r18'])` 会把 commandcode 提为显式后端覆盖 zhipu 优先（实测踩坑，已修：`if not low_r18:` 才做 explicit 提头）。

**清理 pass 按作品级 r18_level 设 AA_R18 env**（high→1, low→0）：清理是修已译文本，精确到章无必要；注意清理模块函数同进程 import 读 `os.environ`（不是子进程 tag_env）。

## 四、工作台 UI（R18 展示）

- **locic.R18 汇总**：`r18_chapter_judge(work_dir, base)`（读 prep 单章）+ `r18_summary(work_dir, out_dir)`（作品级，**只统计已汉化章**，口径=方案定稿）
- **浓度指标（2026-08-25 用户推翻百分比）**：百分比 = R18命中行/text_lines，但 text_lines(is_text_line) **混入大量 AA 艺术行**（768 行里真台词仅 ~200-300）→ 浓度被稀释到 1-2%，且大量 H 台词是隐晦/拟声/省略号不含显式词 → **永远到不了 50%+**。用户确认**弃用百分比浓度，改「命中词次数 N 次/章」**。UI 显示 `命中词 N 次`。
- 作品行红 R 徽章（high 恒显 / low 有 yes 章才显，悬浮占比+命中数）；详情弹窗 R18 区块（级别+占比+命中数+yes/suspect 章清单）；阅读器/章节列表 yes 红 R、suspect 黄 R（R?）
- **移除 tags 的 `R18` 标签**（红 R 徽章替代，25 部全清 + pon 的旧「成人向」也清）
- stats_cache key 加版本（`"v2"` 前缀）强制重建，否则旧缓存缺 r18 字段
- **bug 教训**：`yes_ch = sus_ch = []` 共用列表对象，append 互相污染——必须分开初始化

## 五、视觉辅助 R18 判定（试点结论，2026-08-25）

**基础设施现成**（2026-08-16 为"台词 vs 画面"建的）：`aa_to_images.py` 渲染块图 + `call_qwen_vl(image_b64=)` 视觉判定。试点脚本 `scripts/tools/r18_vision_pilot.py`（原文渲染，未汉化作品也可用）。

**实测成本**（Qwen3-VL-32B, SiliconFlow）：1 块图 ~829 prompt tokens（¥0.0016）；抽样 8 块/章 ≈ ¥0.013；单章全量 40-50 块 ≈ ¥0.08。

**试点结论（sujin_shabu 6 个 suspect 章）**：
1. 视觉**从不变 yes**——AA 抽象画面无法"确认"露骨 R18，只能抓文本暗示（台词擦边词イチャラブ/ベッドに押し倒して 等）
2. **有降级价值**：2/6 suspect 章视觉确认整章日常（51-53 块几乎全 no）→ 可降 no（走 zhipu 免费）
3. **系统性偏差**：每章第 1 块（含标题「生娘シャブ漬け」）必判 suspect——**标题梗误当正文，判定应排除第 1 块**

**推荐用法**：suspect 章视觉抽样（排除第1块）→ no 票 ≥75% 降 no；否则保持 suspect。

## 六、低含量作品 zhipu 探针（2026-08-25，降级依据）

**用户实测驱动**：sujin 作者作品 R18 含量极低（用户目检 suspect 章几乎无 R18）。用 `scripts/tools/r18_zhipu_probe.py` 把 yes/suspect 章的原文台词送 zhipu，看是否 1301 拦截：
- **sujin_shabu 全部 10 个 yes/suspect 章 zhipu 全过、无一拦截**（译文是擦边/讨论/设定，非露骨性行为）
- 证实：zhipu 不拦截 ≠ 无 R18，只是宽松（放行擦边，拦露骨）；真露骨会 1301 终止并提示换后端
- **方案 B（用户选）**：low 作品的 yes 章也**优先 zhipu + 1301 自动降级回 r18 链**（安全省钱两全）——即上面 low_r18 机制
