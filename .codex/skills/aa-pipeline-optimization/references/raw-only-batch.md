# 仅下载原文零模型批量（raw-only）

来源：2026-08-20 実戦 — `rss.r401.net/yaruo/categories/23586` トレセン学園 人狼部（43话，`yaruobook.net` 别馆，`sites/198`）与 `21114` 時間停止探索者やる夫（270话，`yaruobook.com`，7页，含 `p=2012` 第4话乱序）

## 问题
`r401_index.py category <id>` 只取第1页（`EXLINK_RE` 40条），>40话作品会漏；用户要求“仅下载原文不汉化”必须零模型零 token，且别馆站 `yaruobook.net` 未入 `registry.py` 也要能落盘。

## 做法
1. **分页全取**：`fetch(BASE/yaruo/categories/<id>?page=N)` 循环，解析 `href="/yaruo/categories/<id>?page=N"` 求最大页或逐页至空，合并 `entries` 去重 `eid`。23586=2页43条、21114=7页270条。
2. **批量下载**（`temp/dl_raw_only.py` 模式）：5并发 `ThreadPoolExecutor`，直连 → `127.0.0.1:10808` 代理兜底，已有 `>50KB` 跳过，失败重试3-4次，`<10KB` 抛错。两部并行，時間停止既有10话 `skip`、新增260话 `ok`。
3. **本地提取零模型**：`sites.yaruobook.extract(raw, eid, fname)`（`entry-content cf` → `<dl>` → `<dt>/<dd>`，`yaruobook.net` 与 `yaruobook.com` 同结构复用），得 `aa/*.txt + *_comments.json`（标题 `#` + `# 原帖地址: https://yaruobook.{net,com}/?p=<eid>`）。全程不读 `model_config.toml`、不设 `AA_BACKEND`。
4. **产出核验**：`raw/*.html` 与 `aa/*.txt` 1:1（人狼部43/43、時間停止270/270），`extract_all --work jikan_teishi` 全量复核。

## 坑
- `r401` 偶发 `SSL UNEXPECTED_EOF / WinError 10054` 需重试3-5次+代理兜底；分页探测失败会误判为单页。
- 分类分页与话数乱序（`p=2012` 是第4话，按 eid 排序会错，需按标题「第N話」排序）。
- `SKILL.md` 与本 `references/` 互为索引：SKILL.md 仅保留一句话指针，细节在此文件。
