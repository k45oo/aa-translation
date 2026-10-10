# 原文锚定变体替换（2026-08-30，yukika_robot きらきー 试点）

## 痛点（为何普通术语替换不够）

`apply_glossary_chapter` 只匹配**残留日文**（`jp in nb`）。但同一日文词可能被模型译成**好几种中文变体**——yukika_robot 的 きらきー（雪华绮晶/Kaiki 的简称）被译成 闪闪/kiraki/琪拉琪/绮丽绮/亮亮/绮罗 等 100+ 种。汉化里已无日文，普通替换够不着。

核心诉求（用户）：**查原文替换对应汉化** —— 以原文做锚点，定位该词已译成的多种错误中文变体，统一为正确译名。

## 方案（三段式）

1. **变体提取（模型）**：`python aa_web_app/gen_variant_extract.py --work <id> --word <日文词>`
   - 扫全部原文行定位该词 → 每批 30-40 条「原文行+汉化行」喂模型归纳每处变体 → 写 `work/variant_<词>.json`
   - 输出结构：`{word, count, variants:{变体:次数}, hits:{变体:[(章节,行号)]}}`
2. **人工确认**：读 variant json，勾选要替换的变体；歧义候选（胡/吃/金丝雀——可能是其他角色或同音误译）留给用户
3. **批量替换**：`python aa_web_app/apply_variant_replace.py --work <id> --word <词> --target <译名> [--auto] [--dry-run]`
   - 用 `logic.apply_glossary_chapter`（宽度自动校正 + 备份）或 `apply_by_hitmap`

## 已实测确认的设计要点（勿凭直觉改）

- **变体提取用 `aa_backend.call_with_fallback`，勿用 `aa_translate.call_api`**。`AA_BACKEND` 未设时 `call_api` 走 cloud 直连，长 body（>40 条）返回空串；`call_with_fallback` 走 zhipu 免费链稳定（30-40 条/批）。
- **hits 的 (章节,行号) 与变体不对应**（实测：kiraki 的 46 命中行里 0 行字面真含 `kiraki`）——模型给的行号索引粗。**勿按行号定位**，改**整章无边界长词优先替换**（只处理原文确含该词的章节，防误伤别处）。
- **词边界正则会漏中文人名**：昵称常带汉字后缀（桑/酱/亮亮），`(?<!汉字)闪闪(?![汉字])` 匹配不到 `闪闪桑`/`闪闪亮亮`。**改用无边界整串 + 长词优先**（长变体先吃掉粘连，短词根如 `闪闪` 最后替换时粘连已不存在）。
- **模型会把きらきー(人名)误听成きらきら(闪闪发光)** —— 高频变体 綺晶(73)/kiraki(46)/闪闪(42)/闪闪发光(24)/琪拉琪(20)/kirakira(14)。`闪闪发光`/`闪闪亮亮` 可能是形容词误判而非人名，需人工区分。
- **`is_name_like` 要允许带后缀变体**(kiraki桑/綺晶酱)，长度 2-6，全中文或拉丁。

## 用户偏好（分开处理，勿一刀切）

- 单纯きらきー场景 → 短译名（绮晶）；指全名「雪华绮晶」的保留（不做全名/简称强行统一）。
- 先试点一个词，模板化后再推广全部术语表原词。
- 已验证：雪华绮晶 = 简体标准译名（きらきしょう，Kirakishou，蔷薇少女系列），简称きらきー = 绮晶。

## 脚本位置

- `aa_web_app/gen_variant_extract.py` — 变体提取
- `aa_web_app/apply_variant_replace.py` — 批量替换（含 auto_select 自动勾选 / 词根分组 / 长词优先）

## 状态

提取可靠（162 变体 / 484 处）；替换需 dry-run 复核粘连误伤后实写。auto_select 仍在迭代（hits 行号不可靠→整章替换），实写前务必 dry-run + scan_width 验证 0 错位。
