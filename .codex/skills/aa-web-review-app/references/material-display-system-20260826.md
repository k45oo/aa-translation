# 素材统计与显示系统（2026-08-26 定型）

工作台首页/详情弹窗的「素材」= 作品出场角色，显示名统一「中文名（作品）」，作品出处放悬浮 tooltip。

## 数据流
1. work_meta.json 的 `materials` = 素材名单（**存角色名，不带「（作品）」后缀**——带括号会导致 material_display_parts 查不到、material_frequency count=0，曾致 akari_ninshin 显示异常）
2. `logic.material_frequency_cached(work_dir, out_dir, names)` → `{name: {count, chapters}}`（count=中文名+日文原名双计，`_material_names_for` 从角色表反查日文名；缓存指纹含 v2 版本号，改结构须 bump）
3. `logic.material_display_parts(name)` → `(显示名, 作品名, 出处备注)`——精确匹配角色表「中文==name 或 日文==name」；**作品=yaruo（やる夫系）返回 (None,None,None) 不显示**；显示名取角色中文字段（mats 存日文原名如紲星あかり → 显示绁星灯）
4. main.py 首页 + api_work_meta_get 组装 mats 数组：**pct<5% 剔除**、`mats_display[:8]` + `mats_more`（表格最多 8 个，超出 +N 徽章）
5. 前端 index.html：表格 `.mat` 徽章 + `.mat-tip` 悬浮（作品/备注/出场章节·次数）；详情弹窗横向 `.mats-wrap` 徽章 + `＋添加`/`×移除`

## 角色表（glossary.json）
- 「作品」表 id→名称（21 个：saki/yaruo/madoka/danganronpa/haruhi/touhou/other/texture/princess/imas_cg/katanagatari/voiceroid/jinrou/deathnote/rezero/rozen/gs_meishen/is/codegeass/bluearchive/azurlane）
- 「角色」表 {日文, 中文, 作品:[id], 分类, 备注}——**作品字段必须是「作品」表已登记的 id**，否则 material_display_parts 匹配到角色但作品名 None
- 分类=texture 的是艺术假名碎片（さん/か/ムイ 等），统计时必须排除（`分类=='texture'`）；备注含「昵称/梗」的也不是真角色（クロ/クリ 剔除）

## 关键坑
- **前端手动添加素材**：输入「贝尔法斯特（碧蓝航线）」会被原样存入 → 需**去括号**（`replace(/[（(].*?[）)]$/g,'')`），作品靠角色表映射。三重防护：前端添加时去括号 + 前端保存收集时兜底 + 服务端 PUT `re.sub(r"[（(].*?[）)]$","",x)`。
- 前端添加的徽章必须带 `<b>` 标签，否则保存收集 `el.querySelector('b')?.textContent || el.textContent` 走 textContent 拿全文本。
- **碎片误配**：キング 会同时匹配凉宫角色和赛马娘「キングヘイロー」碎片——统计用角色表精确名（2+字中文）可缓解，但单字/碎片仍有风险。
- **jikan_yaluo 类特殊作品**：work_meta 加 `ignore_materials: true`，首页和 api 都跳过素材统计。
- 统计排除名单（用户指定不统计）：saki_fc2/saki/sujin_shabu/jikan_yaluo。
- 中文字段为空的角色（クロ/キング/クリ）显示日文原名——这些是 AA 昵称/梗词，不应作素材。

## 已登记角色-作品映射（用户确认，2026-08-26）
艾米莉亚→Re:0 / 水银灯·苍星石·蔷薇水晶→蔷薇少女 / 横岛忠夫→GS美神 / 织斑一夏→IS / 鲁路修→Code Geass / 星野→碧蓝档案 / 贝尔法斯特→碧蓝航线 / 芙兰→东方 / キャル=凯露→公主连结 / りあむ=璃亚梦→偶像大师灰姑娘 / 七実=七实→刀语 / 绁星灯→VOICEROID。**父様非素材**；露皮亚/克丽丝等归 other 待确认。
