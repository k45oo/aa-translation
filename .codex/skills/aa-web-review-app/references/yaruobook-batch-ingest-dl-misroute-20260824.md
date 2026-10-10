# yaruobook 作品批量入库 + dl_chapters 误路由坑（2026-08-24）

新增站点作品(如作者 sujin 5 部)批量下载入库的完整流程与踩坑。

## yaruobook 目录解析脚本（与 r401_index.py 同级）
`scripts/common/yaruobook_index.py`：解析 yaruobook.com 分类页。
- `category <cid> [--all] [--json out.json]`：章节清单(entry-card) + **作品简介**
- `html <本地页>`：离线解析
- 分类页结构(Cocoon)：
  - 章节卡片 `<a href=".../?p=<eid>" class="entry-card-wrap ..." title="标题">`，h2 内标题，`entry-date` 日期
  - **作品简介在 `<div class="category-page-content entry-content">…</div>`**（article 元素内）——用户明示简介位置
  - 分页 `?page=N`（约 20 条/页，最新在前）→ `--all` 合并去重；**顺序是倒序，原站顺序需反转**
- JSON 输出含 `task description`（简介原文）+ entries，喂给 dl_chapters / 提取 / chapter_order 生成

## ⚠️ dl_chapters --json 无 --work 时按 url_pattern 反推 work → 会**误路由到第一个同域作品**
`dl_chapters.py` 无 `--work` 时，用条目 url 的 domain 匹配 `registry.WORKS[].url_pattern`，**取第一个匹配的作品**。
同域多作品时（yaruobook.com 下有魔法戦姫/jikan_teishi/sujin 系列…），新作品的章节会被下载到**第一个 yaruobook 作品的 raw/**。
**实测**：5 部 sujin 作品 134 章全下载进「魔法戦姫」的 raw/（该目录 59→193 文件）。
**修复**：dl 完成后写脚本按各作品 chapters.json 的 eid 把 entry_<eid>.html 归位（`shutil.move(SRC → 各作品 raw/)`）。
**预防**：给新作品在 registry 建好条目、dl 时显式 `--work <id>`（registry 的 raw_dir 才是正确目标）——无 --work 的 json 模式只适合单域单作品。

## 批量入库流程（sujin 5 部成品）
1. `yaruobook_index.py category <cid> --all --json <作品>/raw/chapters.json`（5 个分类 377/850/853/851/860）
2. `dl_chapters.py --work <id>` 下载（或 json 模式 + 归位）
3. `extract_all.py --work <id>` 提取 raw→aa+colormap+评论区；验证无小文件(<10KB)
4. 生成 `work/chapter_order.json`：分类清单倒序反转（第1话在前）
5. `translate_yaruobook_desc.py`（zhipu glm-4.5-air）翻译简介 → `work/description_zh.txt`
6. registry 注册 + work_meta 登记（author 以告知为准，sujin=◆sujin）+ gen_aa_html 渲染简介

## gen_aa_html 简介渲染
从 `cfg.work_dir/description_zh.txt` 读取，渲染到 header（`<p class="work-desc">`，CSS 在 CSS_MODERN/CSS_LEGACY 都加了）。简介用 AI 总结到 ≤150 字存 work_meta.intro。

## 其他坑
- **patch 写含 `\raw` 的路径会崩**：`\r` 被 JSON 解码成真实换行 → `\raw` 变成 `\naw`，registry 语法崩。含反斜杠路径的编辑用 `write_file`/`python - <<'PYEOF'` 脚本（替换 `\naw`→`\raw`，二次修复可能残留 `\r\raw` 再替换），禁 `patch` 直接写 `\raw`。改完 `python -m py_compile` + `od -c` 验 CR。
