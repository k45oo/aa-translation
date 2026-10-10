# aa_search 增强实现笔记（2026-09-03，R18/分类/时间/章节总数/报告）

方案 `doc/aa搜索增强方案_2026-09-03.md` 落地（commit 13d8fc6a）。本文为 SKILL.md「作品发现搜索」节的 session 细节补充，按函数/端点记录实测结论。

## CLI 全参数（v2，2026-09-03）

```
keyword(可选) --author --tag(可重复 append) --site r401|com|jp|net
--r18|--no-r18(互斥) --since D --until D --started-after D
--no-count --no-cache --sort date|pop --json F --report [PATH|__auto__]
```
无 keyword 必须带 --tag/--author（浏览模式）。`--report` nargs='?' const='__auto__' → 自动落 `doc/aa搜索报告/<kw|tag-组合|author-名>_<YYYYMMDD_HHMM>.md`。

## tag26(R-18表現含む) 全量爬

- `r401_tag_all(tid)`: 首页拿末页号（nav `<a href="...?page=N">&gt;&gt;</a>` = » 末页链接），其余页并发 8 路。**每页失败必须重试**（首次无重试版静默丢 ~10 页 400 条：2854→2471）。
- tag 页 40/页；tag26 = 72 页 ≈ 2880 去重 2854 cid，并发 8 路实测 7.75s。
- 缓存 `data/search/r401_r18_cids.json`（1 天 TTL）——`_tag26_browse()` 读缓存转记录，免 72 页重拉丢页。
- **⚠️ tag26 语义是「作品含 R18 表现/擦边」，不是整部 R18 分级**：ポケットモンスターユカリ(24990)/ポケモン赤(1883)/ブリジット(21918) 全在集合内（正常向但有擦边章）。用户 2026-09-03 明确维持此语义（接受擦边算 R18），勿再改判定。

## 多标签 AND（keyword 模式 vs 浏览模式）

- keyword 模式：tag 是**过滤器**——只做关键词搜索，聚合后 r401 记录 cid 必须 ∈ 各 tag 全量 cid 交集；tag 全量**不进 results**（否则 1400+ 条淹没关键词结果）。
- 浏览模式（无 keyword）：tag 是**结果源**——tasks 拉各 tag 全量并集聚合；单 tag 天然全集跳过交集过滤；多 tag 聚合后交集过滤。
- 实测：R-18表現含む ∩ 安価 = 896 作；`ポケットモンスター + tag R-18表現含む` = 4 条（与 --r18 一致，语义等价）。

## 章节总数 / 开坑日期抓取（--no-count 关闭）

- `count_r401(cid)`：首页 `class="item-name s"` 数 items1 + 末页号 last（» 链接）；last>1 请求 `?page=last` 数 items_last；总数=(last-1)×40+items_last。开坑 = 末页**最后 item-date**。实测 25173 = 168 章、started 2026-01-02（误差 0）。
- `count_yb(site, cat_id)`：分类页 URL 是 `/?cat=<cat_id>`（**不是 `?s=`**，后者全文搜索超慢）；分页页号 PAGE_RE findall（`paged|page` 都收）；总数=(N-1)×300+末页条数。
- **⚠️ yaruobook 卡片日期必须取「卡后最近 entry-date」**（逐卡 search，`_count_yb_last_dates`）——全局 findall 混入侧栏 recent-posts 日期（137 vs 117 条）导致开坑日期取错（2026-09-02 → 2025-05-01）。
- 开坑日期 r401=`_parse_date_r401`（YYYY年M月D日 / M月D日 時分），yaruobook=`_parse_date_yb`（YYYY年M月D日）。
- 只对**过滤后最终结果**并发 8 路（`_fetch_counts`）；含 com 结果预算 240s 否则 120s；as_completed+shutdown(wait=False)。

## 分类关键词表（scripts/config/search_categories.json）

```json
{"R18":{"title":["R-18","R18","18禁","エロ","スケベ","成人"],"summary":["成人向","R-18","R18","18禁","エロ"]},
 "安価":{"title":["安価"],"summary":["安価"]},
 "完結":{"title":["完結"],"summary":[]},"短編":{...},"オリジナル":{...}}
```
`title_classify(title, summary)` → {分类: "标题"|"简介"}。标题词=聚合时零成本预判；简介词=总数抓取（同请求拿 description）后二次判定（报告标「推定」）。完結/短編/オリジナル 仅标题粗判。

## R18 过滤（组装时标注，避免重复查集合）

组装 out 时 r401 记录直接查 tag26 集合填 `r18` 字段（懒加载缓存），过滤段只读已填值——勿在过滤段重查（冗余且首次无缓存时多爬 72 页）。yaruobook 先标题词，简介二次判定补。

## yaruobook 全搜等待模式（重要模式）

多源并行收结果用：
```python
ex = ThreadPoolExecutor(max_workers=min(8, len(tasks) or 1))
try:
    futs = {ex.submit(fn): fn.__name__ for fn in tasks}
    if len(tasks) > 1:
        for f in as_completed(futs, timeout=360):  # 快源先出，com 超时放弃
            ...
    else:
        for f in futs: f.result()  # 单站要等满（用户主动选 com 等得起）
finally:
    ex.shutdown(wait=False)  # 关键：勿用 with 块（__exit__ 隐式 wait=True 等慢源）
```

## 附带修复：gen_interface_docs.py Windows 换行 bug

`open(OUT, "w", encoding="utf-8")` 在 Windows 默认 newline=None → 把 `\n` 转 `\r\n`。若仓库 HEAD 文件是 LF（doc/接口文档.md 是 LF），gen 重跑全文件 CRLF→1938 行假 diff。修：写文件加 `newline="\n"`（2026-09-03，diff 1938→4 行）。改 aa_search.py 后必须重跑 gen_interface_docs + gen_inventory 同 commit；两生成器写文件都要显式 newline。

## 验证记录（实测通过）

- r401 站关键词：总数抓取 19/19（7s）、报告 12.5s
- count_yb net 282（ポケの子）= 417 章、简介完整（含「安価」命中简介判定）、started 2025-05-01
- title_classify：R-18/18禁/スケベ/エロ 标题命中正确；普通作 {}
- yaruobook R18 过滤（monkeypatch 假数据）：【R-18】标题保 / 普通剔除
- 浏览 R18 单 tag 2471 条（缓存版 2854，重拉丢页已修重试）；多 tag R18∩安価 896
- 报告含新列：分类/R18●/总章数/开坑/开坑晚于★
- 250 测试全绿
