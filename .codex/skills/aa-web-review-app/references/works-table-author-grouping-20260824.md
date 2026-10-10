# 作品表格作者分组 + 元数据（2026-08-24 实现）

工作台首页从「平铺作品表」改造成「按作者◆tripcode 分组 + 详情弹窗可编辑元数据」。
本文档记录实现中的关键技术与坑，供后续维护/扩展参考。方案文档：`doc/工作台作品表格改版方案_2026-08-24.md`。

## 数据结构
- **work_meta.json**（`aa_web_app/data/work_meta.json`，git 入库）：`{wid: {author, cn_name, jp_title, tags, materials}}`
  - `author` = 楼主◆tripcode（固化值；生成时自动识别 + 人工兜底）
  - `materials` = 素材人物名清单；**占比运行时**从汉化文本数出现次数，不落盘
  - 顶层 `_meta` 键放说明（非作品数据）
- **作者分组逻辑**（`main.py index()`）：遍历 registry，读 work_meta 的 author → 按作者聚合，多作品作者组排前，组内 registry 序；组级 translated/untranslated 统计。
- **API**：`GET/PUT /api/work-meta/{work}`——详情弹窗读单作品 meta + 素材出现话数；PUT 仅编辑 author/cn_name/tags/materials（防呆过滤空白）。

## 关键技术 1：作者◆tripcode 提取必须用「投票法」而非「第一个◆」
楼主判定 = 全作品 txt 中每个 ◆tripcode 出现次数取**众数**，而非扫到的第一个◆。
**原因**：汉化 txt 文件常从中间楼层开始（如 saki_fc2 首本从 3398 楼开始），第一个◆可能是**回帖者**而非楼主。
实测：saki_fc2 首◆=◆gy.Jr0fkrLU3（回帖者），投票法得真楼主 ◆i4P3ia.arsu1。
实现 `logic.extract_author_tripcode(aa_dir)`：扫前 12 本 × 前 600 行，跳过 `#`/`=====` 行，`Counter` 取众数。24/25 部自动识别，耗时约 2.3s/全量。

## 关键技术 2：帖头格式有 3 种变体，正则须全适配
```python
_HEADER_LINE_RE = re.compile(r"^\d{1,5}\s*(?:名前\s*)?[：:]")
_TRIP_RE = re.compile(r"◆([^\s：:　]+)")
```
- bookshelf/yaruobook：`437 ： ◆i4P3ia.arsu1 ： 日期 ID:...`（带名字 `ルピア ◆1v1ZPWQmKI`）
- 5ch/fukou：`1 名前： ◆pOgi2U5jM.[] 投稿日：...`——**数字与「名前」间是空格**，正则的 `(?:名前\s*)?[：:]` 才能吃下
- `#` 标题行 / `===== AA块 N =====` 分隔行必须跳过

**行数限制的坑**：`r401_rance` 的◆在第 **1507 行**才出现（前几百行是大 AA 图/无◆对话）。若扫描限 600 行/本会漏 → 该作品需人工 override 补。`fukou`/`r401_jyusatsu` 到第 3 行就有◆但整行是「1 名前：」无冒号紧跟 → 正则写错也会漏。

## 关键技术 3：作品「已译」判定不能用 deficit==0
`deficit = src_total - photons`，对**纯原文作品（out_dir 缺失）**，`work_stats` 里 chapters=aa 原文数 → deficit 恒 0 → **误判已译**。
正确判定：`out_exists = os.path.isdir(out_dir) and any(f.endswith("_汉化.txt") for f in os.listdir(out_dir))`，`translated = out_exists and deficit <= 0`。
实测修正前 akari 组（akari_ninshin 已译 + akari_haisin 纯原文）被误判「作品数 2」，修正后正确显示「1 已译 / 1 未译」。

## key 技术 4：素材占比「纯自动」在やる夫系原创会落空 + 普通词污染
glossary `角色` 表的 `作品` 字段指向 **IP 分支**（saki/madoka/touhou…），只对**有 IP 匹配**的作品可靠：
- saki → 咲-saki- 角色命中好（神代小蒔 20 次…）
- jinrou → 马娘角色命中好（黄金船 366 次…）
- **やる夫系原创**（baitou/fukou/last_dungeon 等）：`作品=yaruo` 分支角色几乎为空 → 纯自动落空
- 且 glossary 有**普通词污染**：`超越`（13 次）、`素质` 这类当角色名误报

结论：素材必须用「**每作品可编辑清单 + 占比自动从汉化文本数**」，初始清单 LLM 扫描提取供人类审校；不能纯自动。同人/多 IP 混合（touhou_teisou=马娘+原创）素材名来自澄清而非 glossary。

## work_meta.json 格式
`save_work_meta` 用 **indent=2 缩进**（元数据供人工审校，区别于 glossary 的紧凑字节稳定格式）。人工审校种子文件时保留缩进，机器读写为主的才用紧凑。

## 前端
- R18 红标：剥标题 `【R-18】/【R18】/【R18G】` 前缀用 Jinja filter `strip_r18`（正则 `^【\s*R[-－]?18G?\s*】`），R 以红底白字 badge 另示
- 作品名两行：日文名(jp_title)粗 + 汉化名(cn_name)灰小
- 外层作者行 + 内嵌 `<tr><td colspan>` 子表（非嵌套复杂表格），`.collapsed` 时 `display:none`
- 详情弹窗 = 模态 #detail-modal，fetch GET/PUT /api/work-meta，素材出现话数懒算（仅点开时）
- 验证套路：TestClient 缺 httpx2 依赖 → 用 `uvicorn background=true` + urllib 请求；浏览器改静态后必须 bump `?v=`；编辑保存后 `location.reload()`
