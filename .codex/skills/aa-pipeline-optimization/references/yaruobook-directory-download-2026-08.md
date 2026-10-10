# sujin 作者 5 部作品批量下载 + yaruobook 目录解析（2026-08-24）

## 背景
用户要求下载 yaruobook.com（やる夫スレ本棚）作者 sujin（すじん ★，ID:sujin）5 部作品原文 + 翻译简介。作者批量下载、作品简介提取、作者分类登记三条流程在此固化。

## yaruobook 目录解析脚本：`scripts/common/yaruobook_index.py`（与 r401_index.py 同级）
分类页 `https://yaruobook.com/?cat=<cid>` 是 Cocoon 结构，两类关键数据：
- **章节卡片**：`<a href="https://yaruobook.com/?p=<eid>" class="entry-card-wrap ..." title="<标题>">`（`?p=` 拿 eid）
- **作品简介**：`<div class="category-page-content entry-content">……</div>`（**在 article 内**，用户明确指出的位置）——纯文本剥标签即得
- 分页 `?page=N`（约 20 条/页，最新在前，与 r401 同口径倒序）

用法：
```bash
python common/yaruobook_index.py category 377 --all --json <作品>/raw/chapters.json
python common/yaruobook_index.py html <本地分类页>       # 离线解析(无需网络)
```
JSON 结构 `{title, description, entries[{eid,title,date,url}]}`，可直接喂 dl_chapters。`category --all` 拉全分页并保存 description。

## ⚠️ dl_chapters 无 --work 时的 work 反推误匹配（本次 134 章误下载事故）
`dl_chapters.py --json xxx.json` 无 `--work` 时，从章节 URL 域名反推 work：
```python
for wid, wc in registry.WORKS.items():
    if domain in wc["url_pattern"]: work = wid; break
```
**同站点多个作品时，反推命中第一个该站点作品**（registry 顺序=魔法戦姫 `yaruobook`）。5 个 sujin 作品的 JSON 全部下载到了魔法戦姫 的 raw/（134 文件混入），而非各自的作品 raw/。

**修复**：写脚本按各自 chapters.json 的 eid 把误文件 move 归位（`shutil.move`）。**教训**：批量下载多同站作品，要么显式 `--work <id>`（registry 有该作品条目），要么先确认 JSON 里条目 url 反推不会撞错作品。下载后核对各 raw/ 文件数与章节数一致（本次 5/36/10/51/32 + 魔法戦姫恢复原 59）。

## chapter_order.json 原站顺序（分类页倒序需反转）
r401/yaruobook 分类页条目**最新在前**，反转 = 原站发布顺序（第 1 话在前）。生成 `work/chapter_order.json = {"order":[eid,...]}` 供 gen_aa_html 排序。

## gen_aa_html 按 chapter_order.json 排序（2026-08-24 新增 work_dir 参数）
`common/gen_aa_html.py` 的 `chapter_order(TR_DIR, name_style, work_dir)`：
- **优先 `work/chapter_order.json` 的 order 排序**（eid 匹配，未收录的按 eid 数字补尾）
- 缺失回退文件名 eid 排序
- **为什么必须**：部分作品 eid 与话数序不一致（akari_koikokoro: 20400=第1話 36400=第2話 20822=第3話；sujin_* 同理），文件名排序会乱。23 部有 chapter_order.json 的作品统一受益。
- 改后调用处传 `cfg.get("work_dir")`。**akari_koikokoro 曾因 chapter_order 里第5/6话 eid 反序需手动对调 fix**。

## 作品简介翻译 + 加入详情
- 简介日文原文存 `raw/chapters.json` 的 description；翻译脚本 `scripts/tools/translate_yaruobook_desc.py`（zhipu glm-4.5-air，简中译文写 `work/description_zh.txt`）
- gen_aa_html 读 `work/description_zh.txt` 渲染到合集 header（`<p class="work-desc">`，CSS_MODERN/LEGACY 各加一段）
- 工作台详情弹窗（index.html）字段顺序：汉化名/作者/状态/日文原题/站点（grid）→ 标签 → **简介**（dm-sec，用户要求介于标签和素材之间）→ 素材人物。后端 WorkMeta model 已含 status/intro（main.py PUT/GET 已读写）。

## 工作台作者登记铁律（用户 2026-08-24 明确）
- **作者以手动输入和下载时告知的为准，不做自动提取**。sujin 帖头是 `すじん ★` **无 ◆**（`extract_author_tripcode` 投票法扫 ◆tripcode 扫不到）→ 自动提取返回空/乱分。
- 优先级（main.py:383）：`work_meta.author`（手动）> `_AUTHOR_OVERRIDE` > `extract_author_tripcode`（自动兜底）。
- ui 新作品登记：写 `aa_web_app/data/work_meta.json` 条目，author 用告知的作者（如 `◆sujin`）。akari_koikokoro + 5 部新作共 6 部归 sujin 组。
- 用户偏好：**成人向标签统一用 `R18`**（不做 成人向），index.html `tag-r18` 以 `t == 'R18'` 匹配。

## 注意
- registry 新增作品条目含反斜杠路径用 write_file/Python 脚本写（patch 的 `\raw` → CR+aw 转义坑重演，见 patch-escaping skill），改完 py_compile + `grep -nP '\r[^\n]'` 双校验。
