# 校对「改回日文」防错 + 作品记忆（2026-08-25 实测, 8d8620b 等）

## 症状
校对自动矫正曾把 **20 行正确译文改坏**——正确中文被校对模型建议"改回日文原文"，
translate_runner 自动应用后变成日文残留（如「不得已＝超开心」→「仕方なく＝超开心」、
「是什么实况？」→「何かの実況？」）。

## 根因
校对 prompt 让模型自由判断「中文是否有误译」，但模型看到日文原文后倾向**把正确中文改回日文**。
自动矫正**无 new 过滤**直接应用 → 正确译文被破坏。且校对模型会输出大量**噪音 issue**。

## 双重过滤（铁律）
1. **生成时**（proofread.py）：prompt 明确「改译必须中文，绝对不要输出日文/假名」；
   生成 issue 后丢弃 `new` 含假名 `re.search(r'[ぁ-んァ-ヶ]', new)` 的条目。
2. **应用时**（translate_runner `_run_proofread`）：自动应用前 `if re.search(r'[ぁ-んァ-ヶ]', new): continue`。

## 清单噪音（清理时移除）
- `old == new`（模型输出"无问题"却解析成同值）——64 条
- `type == 无问题` / `new == 无问题`——3 条
- `new` 含日文假名（改回日文建议）——27 条
- 从 139 条清到 **45 条真 issue**。

## 已被改坏时的恢复
- 正确译文可从 `work/proofread_<work>.json` 的 **`old` 字段**找回（issue 保留了原译文）。
- 作品标题行（やる夫の生娘シャブ漬け大戦略N）**保留日文**不译，勿恢复成旧误译标题。
- 恢复只对**非艺术行**做（文本/mix），艺术行里的日文是装饰不改。

## issues 字段
`line/type/old/new/why/status/manual`——`status`: 0待核验 / 1自动校对已应用 / 2人工校对；
`manual`: 人工意见（空串）。旧记录（无 status/manual）需补字段。

## 作品记忆（work_memory）
- 自动矫正的 confirmed 问题 → `work_memory.add_from_issues` 积累到 `work/<work>_memory.json`
  （术语/语境规则/人物三类），注入翻译 `glossary_prompt_hint` + 校对 `_glossary_hint` 防误报。
- 只过滤掉 `new` 含假名的（不可靠建议）+ 超长整句；短中文词（亚鲁夫→亚鲁欧）才积累。
- 角色中文名（紲星あかり→绁星灯）用于素材显示：`logic.material_display_parts` 查 glossary
  角色表 中文/日文 → 作品 id → 作品表名称；作品 id 未登记（如 voiceroid/jinrou）会显示无作品名，
  须补作品表条目。
