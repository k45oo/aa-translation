# aa_search.py 端点结构实测（2026-09-01）

实现 `scripts/tools/aa_search.py` 时逐端点探明（v2rayN 代理 127.0.0.1:10808 + Chrome UA）。与 `r401_index.py`/`yaruobook_index.py`（读指定分类）互补：aa_search 是跨全集关键词发现。

## r401 搜索四线（rss.r401.net/yaruo/）

| 线 | URL | 返回 | 分页 |
|---|---|---|---|
| 作品名 | `/yaruo/categories?s=<kw>` | 作品头：catid 链接+作品名（无 site，date 在其后） | — |
| 章节名（题级） | `/yaruo/archives?s=<kw>` | item=作品(cat 链接)+章节(exlink)+站点+日期 | `&page=N` 40/页，`href="[^"]*page=(\d+)"` |
| 作者 | `/yaruo/authors?s=<kw>` | **全作者表子串模糊流**（搜 `ポン` 出 ショボン玉/バカポン/ボンクラ…；搜 trip `◆xxx` 也出一堆无关） | 须精确过滤后逐 aid 抓作者页 |
| tag | `/yaruo/tags/<tid>` | tag 下作品分类集合（同分类页结构） | `?page=N` |
| tags 清单 | `/yaruo/tags` | 32 个 `href="/yaruo/tags/(\d+)">名称` 一次抓全（オリジナル=6、R-18表現含む=26） | 无分页 |

**archives item 结构**（EXLINK_RE）：
```
<div class="item-name s" data-rid-catid-param="<cid>" data-rid-target="cat">
  <a href="/yaruo/categories/<cid>">作品名</a>          ← cat 链接文本=作品名（聚合键用 cid，不是行标题）
<div class="item-arc s" data-rid-arcid-param="<arcid>">
  <a class="exlink" href="<url>">章节名</a>
<span class="item-site"><a href="/yaruo/sites/<sid>">站点名</a>
<div class="item-date">12月19日 21時00分
```
无年份日期（月日 時分）= archives 当年；有年份（YYYY年M月D日）= 分类/tag 页。categories/tag 页 catid 指向作品分类但**行标题可能是单章**——聚合键必须用 cid。

**作者页** `authors/<aid>`：= 该作者出现过的全部分类（**含他人合集/转载**，ポン aid 8791 实测 86 条/页、37 页上千条，标题含 `◆toJd5AYQtwの雑談・投下所`、`胃薬の調剤所 ◆036aFhDFNU` 等）。本人作品过滤：抓前 3 页 + 标题含他人 trip（`◆xx` 非本人 trip）或 `氏の作品集|短編|の雑談|投下所|黒歴史|の製薬|隔離施設|証券取引所|調剤所|一発ネタ` 即跳过。作者页分页 `?page=N`（`rel=next` 亦有）。

**rank 页** `/yaruo/rank`：单页 4 块 `<h3 id=...>`：access1=アクセス多い連載 111 条 / access2=アクセス多い作者名連載・短編 66 / access3=アクセス多い作者 50 / new=新しいまとめ・新人 80。条目 `<li><a href="/yaruo/categories/<cid>">名字</a>`。week/all→access1，month→access2（无独立累计块）。

## yaruobook 三站（WordPress/Cocoon）

### `?s=<kw>` = 站内全文搜索（关键）
- **正文命中**：搜索「初音ミク」com 109 卡里 107 张标题不含、net 163 卡**全部**标题不含——标题只是章节名。
- 分页 `&paged=N`（300/页上限，实测 `?s=スライム` 单页 300 卡）；继续条件 `href="[^"]*?paged=(\d+)"` 含 page+1。
- 卡片完整结构（每卡自带作品分类）：
```
<a href="https://yaruobook.com/?p=<eid>" class="entry-card-wrap ..." title="章节标题">
  <span class="cat-label cat-label-<cat_id>">作品名</span>   ← 章节所属作品分类（聚合正道）
  <h2 class="entry-card-title">章节标题</h2>
  <span class="entry-date">2026年7月3日</span>
```
- 分类改名后旧章节标题作品名可能不一致（实测 `モルガンと往く人理修復` cat 下旧章还叫「トネリコの往く人理漂白」）——cat-label 是当前名，为准。
- 正文命中「含关键词但不主题相关」是本质（搜ポケットモンスター 命中 デキルオ…117 章=提及非主题），r401 搜题目（精确主题）+ yaruobook 搜正文（全量提及）互补。
- **第一个 `entry-card-wrap` 字符串命中在 CSS 里**——定位卡片要 `CARD_RE.search` 找 `class="entry-card-wrap` 的 a 标签，勿 `find('entry-card-wrap')`。

### searchandfilter（作品级过滤）
`<form method="post" class="searchandfilter">`：`input[name=ofsearch]` 关键词 + `select[name=ofcategory]`（option value=cat_id，几百作品）。POST 提交（页面自带 `?s=` GET 搜索框是另一个 form）。未接管线（aa_search 用 `?s=` + cat-label 聚合，不需要 ofcategory）。

### 性能
com 站 `?s=` 实测 141s+/卡住；net 39s、jp 中间。全源搜索 net/jp 通常已覆盖主要结果。

## 本地 registry 已收录标注
三重比对：r401_cid → work_id；work/site_url.json 的 url（yaruobook 归一 `yaruobook:cat:<id>`）→ work_id；规范化标题 → registry title / work_meta jp_title/cn_name。

## 其他结论
- tavily `?s=スライムやる夫` 只回角色 wiki/词条（atwiki/fandom 角色页），无「作品+站点+章节」——无结构化增益，--web 砍。
- 规范化标题顺序：**剥 R18 前缀（`【R-18】|【R18】|【18禁】|【R18G】`+全角变体）→ 全角转半角 → 去括号/空白 → 去 `のようです` 等后缀**——前缀剥离必须在去括号前（否则 `【R-18】ぼっち` 变 `R18ぼっち` 与 `ぼっち` 不归一）。
- config 文件：`scripts/config/search_authors.json`（作者正名→马甲/trip）、`search_duplicates.json`（重复作品折叠组），均带 candidates 区（自动收集→人工设 确认:true→`aliases --promote` 转正，glossary_candidates 同款流）；缓存 `data/search/`（r401_tags.json + rank_*.json，1 天 TTL）已 gitignore。
