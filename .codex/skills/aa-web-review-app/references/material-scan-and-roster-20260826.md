# 素材统计系统演进补充（2026-08-26 后半）

本文件是 material-display-system-20260826.md 的补充（原文件在压缩后无法追加，独立成篇避免丢失本会话后半学习）。

## 「统计素材」按钮（更多操作）
- 作品行「更多…」菜单 → **统计素材** 按钮（`.mat-scan`）→ POST `/api/work-meta/{work}/mat-scan`
- 后端：扫描汉化文本 → 角色表匹配（中文+日文名）→ 排除 texture/yaruo/昵称梗 → 取 **≥3 次且占比 ≥5%** 前 8 个 → 写回 work_meta materials → 返回统计
- 前端：globalMask 遮罩 + spinner「统计素材中...」→ toast 结果 → reload
- **globalMask 定义必须在脚本顶部**（mat-scan 事件绑定在 detail 弹窗 JS 之前，定义在后面会 ReferenceError——已把 globalMask/showMask/hideMask 提前到 `<script>` 开头）

## 导入作品规范（2026-08-26 定型）
新增作品时**同步查萌娘百科导入主要角色统一译名**——作品表加 id + 角色表加 {日文, 中文, 作品:[id], 备注}。
已补全：弹丸论破16(苗木诚/雾切响子/黑白熊/江之岛盾子)、魔法少女小圆12(鹿目圆/晓美焰/佐仓杏子/巴麻美/美树沙耶香/丘比)、东方12(博丽灵梦/雾雨魔理沙/琪露诺/蕾米莉亚/芙兰朵露)、凉宫7(凉宫春日/阿虚/长门有希/古泉一树/朝比奈实玖瑠)、死亡笔记6(夜神月/L/弥海砂)、Re:0 6(艾米莉亚/蕾姆/拉姆/帕克/碧翠丝/昴)、蔷薇少女6(真红/翠星石/苍星石/水银灯/金丝雀/蔷薇水晶)、碧蓝航线6(贝尔法斯特/企业/提尔比茨/赤城/加贺/拉菲)、IS 6、碧蓝档案5(星野/白子/芹香/野乃美/阿罗娜)、碧蓝幻想5(莫妮卡/兰斯洛特/吉塔/维拉/卡塔琳娜)、公主连结5(佩可莉姆/可可萝/佑树/爱梅斯)、刀语5、GS美神4、鲁路修5。角色表 604→689。
**莫妮卡 = 碧蓝幻想**（granblue，用户确认；曾误归 other）。

## 前端显示演进
- 表格素材：9 个 → 8 个（mats_display[:8] + mats_more）
- 3 行内限制（.w-mats max-height:57px）已移除（用户要求完整显示）
- 详情弹窗 R18 移到站点右边只读输入 + 不再显示具体 R18 章/疑似章清单
- 详情加载：api_work_meta_get 曾逐素材循环卡顿 → 一次 material_frequency_cached 得全部；globalMask 异步加载防多次点击
- 详情素材人物：横向 .mats-wrap 徽章 + ＋添加/×移除；出现话数 .dm-scroll 滚动容器

## 素材统计与显示数据流（速查）
1. work_meta.materials = 素材名单（存角色名，**不带「（作品）」后缀**——带括号致 material_display_parts 查不到/count=0）
2. material_frequency_cached → {name: {count, chapters}}（中文+日文双计，指纹 v2）
3. material_display_parts → (显示名, 作品名, 出处)；yaruo 系返回 None 不显示；显示名取角色中文字段
4. mats 组装：pct<5% 剔除、mats_display[:8] + mats_more
5. 前端 .mat 徽章 + .mat-tip 悬浮（作品/备注/章节·次数）

## 关键坑（补充）
- 手动添加「贝尔法斯特（碧蓝航线）」带括号 → 三重去括号防护（前端添加/保存收集/服务端 PUT）
- 添加徽章必须带 `<b>` 标签（否则保存收集走 textContent 拿全文本）
- 碎片误配：キング 同时匹配凉宫角色和赛马娘碎片；单字假名碎片（さん/か/ムイ）是 texture 分类必须排除
- jikan_yaluo 类特殊作品：work_meta `ignore_materials: true`
- 统计排除名单（用户指定）：saki_fc2/saki/sujin_shabu/jikan_yaluo
