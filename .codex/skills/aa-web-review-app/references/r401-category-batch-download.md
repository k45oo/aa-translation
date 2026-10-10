# r401 分类页批量下载 + 完结标记（2026-08-20 实测，8 分类 636 话）

## 触发
用户给一批 rss.r401.net/yaruo/categories/<cid> 链接要求「仅下载原文不汉化 + 标记是否完结 + 最新话时间」。

## 完成标记（用户指定判定依据）
- 分类页 `<dt>タグ</dt>\s*<dd>(.*?)</dd>` 内的 `<span class="tag"><a href="/yaruo/tags/24">完結</a>`
- 提取：`re.findall(r'<a href="/yaruo/tags/\d+">([^<]+)</a>', tag_dd)` → `"完結" in tags` 即完结

## 最新话时间
分类页按时间倒序，第一条 `item-date`（`YYYY年M月D日` → `YYYY-MM-DD`）即最新话。

## 分页
- `?page=2` 起，`<link rel="next" href="...?page=N">` 存在才继续；每页 40 条
- 例：25286（Rance/Stay night）205 话翻 6 页；22525（夜神月）114 话

## 原帖地址
- 从每条 `exlink` href 取真实 URL，**勿写死域名**（24032 实际在 yaruomatomex.com/blog-entry-43580.html）
- 正则：`blog-entry-(\d+)` 或 `[?&]p=(\d+)` 取 eid

## 正文提取（同 yaruobook dt/dd）
- 容器：`<div class="entry-content">`（回退 `c-chapter-content`，再回退整页）
- 结构：`<dl><dt>` 帖头 + `<dd>` 正文块；按 `<dt>` split，dd 内 `<br>` → `\n`
- **帖头去重**（关键）：页面 `<dt>` 与 `<dd>` 首行常重复同一帖头 → `if dd_lines and dd_lines[0]==dt: pass`；重复 `<dt>` 用 seen 集合跳过
- 输出格式：`# 标题` / `# 原帖地址: <url>` + 帖头行 + 正文行（与 yaruobook/汉化管线一致）

## SSL 偶发 EOF / WinError 10054（代理链路抖动）
- 分类页抓取必须：**每次新建 opener + 重试 3 次**（`build_opener(ProxyHandler)` 每次新建；`time.sleep(1.5*(i+1))`）
- 单章下载（5 并发 ThreadPool + try/except）天然健壮，一次过了
- 同一批里 24032/25538/23281/24467/22525 成功、21162/20386/25286 失败 → 补跑脚本 `temp/dl_r401_retry.py` 单独重跑失败分类即可（断点续传：已存在 >50KB 跳过）

## 参考脚本
- `temp/dl_r401_works.py`：批量下载+提取+索引
- `temp/dl_r401_retry.py`：失败分类补跑 + 缺失单章重试
- 索引：`temp/r401_works_index.json`（cid/title/dir/work_dir/site/author/tags/finished/chapters/latest_date）
- 结果 2026-08-20：8 分类 636 话全落盘，5 完結 3 連載

## 坑
- 分类页首抓 SSL 失败是偶发（重试可过），勿判「站点不可达」
- 已下载 >50KB 的 html 跳过，补跑不会重复下载
- 提取失败多因下载失败（no-html），先补下载再提取
