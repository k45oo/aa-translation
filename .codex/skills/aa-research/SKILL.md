---
name: aa-research
description: Use when 搜索日本AA/やる夫作品、排行、读者推荐、讨论论坛。
---

# AA 作品调研（搜索/排行/读者评价/论坛）

## When to Use
用户要搜索/调研日本 AA（やる夫系）作品、热门排行、读者推荐、讨论论坛，或发现新作品候选时使用。所有访问走 v2rayN 代理 `http://127.0.0.1:10808` + 浏览器 UA（直连 403）。

## 核心数据源总览

| 数据源 | 用途 | URL |
|---|---|---|
| **r401 やる夫RSS+インデックス** | 作品/站点/作者/排行/完结 权威索引 | rss.r401.net/yaruo/ |
| **fandom やる夫 Wiki** | 站点清单（194站）、作品条目、AA介绍 | yaruo.fandom.com（API 用 api.php） |
| **note 读者推荐** | 读者个人10选/感想（SPA，需解析内嵌JSON） | note.com |
| **5ch AA 板** | 日本最大匿名论坛的 AA 讨论板 | mevius.5ch.io/aasaloon 等 |
| **やる夫系专用掲示板** | 20+ 个专用论坛（r401 索引） | r401.net/yaruo/link_bbs |
| **AAHub** | AA 素材库（本地已有 2.9GB 镜像） | aahub.org |

## 一、r401 索引（首选，最权威）

已有工具 `<repo>\scripts\common\r401_index.py`：
```bash
cd <repo>
python scripts/common/r401_index.py search <关键词>   # 搜作品（注意：搜索接口返回的是"最近更新流"非精确匹配）
python scripts/common/r401_index.py category <id>     # 分类详情（章节/日期/站点）
python scripts/common/r401_index.py sites              # 全部站点
python scripts/common/r401_index.py site <id>          # 站点详情
```

### 关键 URL 模式（直接抓）
- 分类页：`/yaruo/categories/<cid>` — `<div class="item">` 结构，含 exlink 章节链接 + 日期
- 站点页：`/yaruo/sites/<sid>` — 该站全部作品
- **访问量排行**：`/yaruo/rank` — 最近1周访问量（#access1 連載 / #access2 作者 / #new 新人）
- **完結 tag**：`/yaruo/tags/24`（全部完結，141页，按时间倒序=最新完结）；`/yaruo/tags/2024`（最近人気の作品[完結] 排行）
- **读者票选**：`/yaruo/tags/1004` = #やる夫スレ十選 2026年2月（472部票数排行，13页）；tags/1000=非安価 / 1001=安価 / 2006=オリジナル / 2026=R18 / 2031=ゲーム
- tag 页两种结构：① `data-rid-catid-param="<cid>"` + `item-date`（带站点/日期，tags/24、tags/2024）② 票数版 `<p>N票</p>`（tags/1004 十選）

### 排行页解析要点
- rank 页按 `<h3 id="access1">` 分块，条目是 `<ol><li><a href="/yaruo/categories/<cid>">名字</a></li>`
- 完結 tag 页（tags/24）条目：`data-rid-catid-param="<cid>" ... <a href="/yaruo/categories/<cid>">名字</a> ... item-date">YYYY年M月D日`
- **票数版**（十選）：`<div class="item-name"> <a href="/yaruo/categories?s=...">名字</a> ... <p>N票</p>`

## 二、fandom やる夫 Wiki

直连 https 页面可能超时，**用 API**：
```bash
# 搜索页面
curl -x http://127.0.0.1:10808 "https://yaruo.fandom.com/api.php?action=query&list=search&srsearch=<关键词>&format=json"
# 读页面 wikitext
curl -x http://127.0.0.1:10808 "https://yaruo.fandom.com/api.php?action=parse&page=<URL编码页面名>&format=json&prop=wikitext"
# 列页面
curl -x http://127.0.0.1:10808 "https://yaruo.fandom.com/api.php?action=query&list=allpages&aplimit=200&format=json"
```
关键页面：
- **やる夫まとめサイト一覧** — 194 个站点的完整清单（wikitable：`!站点名` + `|URL` + `|備考`，分"総合/特化/更新停止"等区）
- **カテゴリ:ウェブサイト** — AAHub / AAMZ Viewer / Yarana.io / 新・マンガ総合AA保管庫 等
- 作品条目：`[[名字]]` 链接到具体作品页

## 三、note 读者推荐（SPA 解析）

note 是 SPA，正文和搜索数据都内嵌在 HTML 的转义 JSON 里。

### 搜索文章
```
https://note.com/search?q=<关键词>&context=note&mode=search
```
HTML 里 JSON 是转义形式 `\"note\":{...}`，按 `\"note\":{` 切块提取：
- `\"key\":\"n[a-z0-9]+\"` — 文章 key
- `\"name\":\"...\"` — 标题
- `\"publishAt\":\"YYYY-MM-DD` — 发布时间
- **作者 urlname**：在 key 出现位置向后 3000 字符找 `\"urlname\":\"xxx\"`，完整 URL = `https://note.com/<urlname>/n/<key>`（**必须**，`/-/n/<key>` 会 404）

### 抓正文
页面内正文容器是 `<div data-name="body" class="note-common-styles__textnote-body">`（注意 `data-name="body"`，纯 class 匹配会撞上 CSS 定义）。正文是真实 HTML（`<p name="UUID">` 段落 + `<h3 name="UUID">` 标题 + `<a>` 嵌入卡片）。提取后：
- 段落：`<p>` → 文本
- 嵌入卡片：`<a href="外部URL"><strong class="external-article-widget-title">标题</strong>` — 推荐作品的原始链接
- 参考脚本：`<repo>\temp\note_parse3.py` / `note_search2.py`

### 已知读者推荐文（按时间）
- raishin「絶対に読んでほしい、おすすめやる夫スレ」(2025-12-08, 39部)
- 8月の蜃気楼「アタシのやる夫スレ10選」(2026-02-02)
- ヴィーナさん「やる夫スレ10選で挙げられた作品ランキング」(2026-02-14)

## 四、论坛（5ch + やる夫系専用板）

### 5ch AA 板（全部存活，2026-08 验证）
| 板 | URL | 用途 |
|---|---|---|
| AAサロン | https://mevius.5ch.io/aasaloon/ | AA 素材/讨论 |
| AA物語 | https://mevius.5ch.io/aastory/ | やる夫系故事连载 |
| 行間AAU | https://lavender.5ch.io/pav/ | AA 讨论 |
| エロAA | https://mercury.bbspink.com/eroaa/ | R18 AA |

5ch 板列表：https://menu.5ch.net/bbsmenu.html（**cp932 编码**，标签是大写 `<A HREF=...>`）

### やる夫系専用掲示板（r401 索引 /yaruo/link_bbs）
最活跃（2026-08 验证 780+ 线程）：やる夫スレヒロイン板(jbbs 15956)、やる夫系雑談・避難・投下板(jbbs 12368)、やる夫板@おーぷん2ch(open2ch)、SS速報VIP、Yaruyomi(bbs.yaruyomi.com)。
**编码**：jbbs 是 EUC-JP、5ch 是 cp932、open2ch 是 UTF-8——抓取时逐个尝试。

## 五、综合调研流程（推荐顺序）

1. **作品发现**：r401 rank（访问量）→ tags/24（最近完结）→ tags/1004（读者票选）
2. **交叉验证**：同一作品在多个榜单出现 = 优秀（如 時間停止探索者やる夫：12票+完结+访问TOP）
3. **站点归属**：r401 category 页确认站点，检查是否已有解析器（yaruomatomex/yaruobook/touhouyaruosure/fc2/2nt 已接）
4. **读者评价**：note 搜索「やる夫スレ おすすめ/10選/名作」→ 抓推荐文 → 提取嵌入链接
5. **论坛热点**：5ch AA物語 / やる夫スレヒロイン板 看最新连载讨论

## 六、已接入站点（汉化管线有解析器）
yaruomatomex.com（まとめくす）/ yaruobook.com（本棚）/ yaruonichijou.com（日常）/ touhouyaruosure.com（東方）/ yaruok.blog.fc2.com / man001.blog.2nt.com
**新站待接**：yaruohiroba.com（やる夫広場）、asitayaruo.com（明日やる夫）、yaruo-matome.com（まとめたお）、yaruobook.net（本棚別館新域名）、yaruobook.jp（本棚2号館，结构同 yaruobook.com 可复用解析器）

## 七、参考文档（doc\aa作品相关\，2026-08-19 调查结论）

5 份调查文档已归档 `<repo>\doc\aa作品相关\`：
- `r401热门作品调查_2026-08-19.md` — 访问量排行 TOP（正常/R18 分开）
- `AA站点全览_2026-08-19.md` — 154 站活跃度 + 194 站域名 + 新站发现
- `读者推荐作品调查_2026-08-19.md` — #やる夫スレ十選 2026年2月 472 部票选
- `日本论坛AA板块调查_2026-08-19.md` — 5ch 4 板 + やる夫系 20+ 论坛验证
- `最近完结优秀作品_2026-08-19.md` — 240 部最近完结 + 交叉验证候选

**2026-08 关键结论速查**：
- 读者票选 TOP：サバイバルヤルオ(21票)、やる夫の魔王の道(16票)、時間停止探索者やる夫(12票,R18)
- 最近完结+读者认可：悪役令嬢と石田三成(9票,2026-08,まとめくす已接)、バカエロファンタジー無双英雄レイヴン(R18,访问TOP)、時間停止探索者やる夫(R18,12票)、やる夫と壬生のゆかいな仲間たち(8票)
- 汉化候选推荐：悪役令嬢と石田三成 → バカエロファンタジー無双英雄レイヴン → 時間停止探索者やる夫
- 新站：yaruobook.net（本棚別館）、YomuAA（yomuaa.r401.net 投稿站，有 R18G 新作）

## 八、关键词分类搜索（主搜索 + 二次搜索）

用 `scripts/tools/aa_search.py` 搜题材关键词，再用二次搜索给结果分类（如「ポケモン」+ 四大資質词 育成/指示/統率/能力 → 判定是否战斗系统系）。

- **refine 只覆盖 yaruobook 三站**（`parse_report_works` 只认 `yaruobook.*/?cat=N` 链接），r401 作品被静默跳过；r401 只能做**章节题级**检索（`archives?s=`），不能扫正文——报告里必须写明这个限制。
- 多词判定不要用现成 CLI（`refine` 单次只收一个词）：复用 `aa_search._refine_work_search(work, kw)` 自己并发循环（temp/pk_refine.py 模板：175 作品 × 4 词 ≈ 9 分钟，8 并发）。
- **高频普通词的 AND 判定判别力很差**：`育成/指示/能力` 是日语高频词，四词全中能覆盖近半数作品（含ガンダム/女神転生/ダンジョン系无关作）。真正有判别力的是 `統率`。
- **系统系（ポケマス四大資質/四式六令/ポテンシャル）作品识别要用密度而非出现**：单词命中不可信（`かわせ` 89/175 部、`全開` 124/175 部、`資質` 102/175 部均为普通词误命中）。做法：每词算「命中章节数 ≥ 5 才成立」，再按成立词数分级（≥7=A 确定 / 3-6=B 疑似 / 1-2=C 弱 / 0=D），实测干净分出 8 部确定作品。
- 高特异字符串（命中≈确定使用该系统）：`四大資質` `四式六令` `エキスパンション` `金冠`（4-10 部/175）；中特异：`熟達` `よけろ` `妙手` `あたれ` `ヌシ`。
- 系统关键词全表与实测结果 → `doc/aa作品相关/ポケモン关键词作品调查_2026-09-23.md`；词源 → `doc/aa作品相关/ポケモンAA四大資質四式六令设定速查_2026-09-02.md` + 本地 `ポケモンAA术语wiki`（seesaawiki ポケマス2 快照）。
- 主搜索 com 站全文搜索单请求 150s+，4 源全搜约 10 分钟；二次搜索 175 作品 × 16 词（14 并发）约 38 分钟。用后台任务跑，别在前台等。
- 产出归档：分类调查文档 → `doc/aa作品相关/<题材>关键词作品调查_<日期>.md`（引用主报告 + 机读 JSON）。

## 坑
- **r401 搜索接口（?s=）返回的是"最近更新流"不是精确匹配**——搜索"人気"等泛词会返回最新分类而非相关结果；精确找作品用 category 页或全列表
- fandom 直连页面可能超时，必须用 api.php；URL 里的日文必须 percent-encode
- note 文章 URL 必须带作者 urlname（`/-/n/<key>` 404）；正文容器带 `data-name="body"`
- 5ch 菜单是 cp932 编码 + 大写标签；日文板名双重编码会乱码，看 URL 推断
- tag 页解析前先看结构（带票数的十選 vs 带站点/日期的完結，正则不同）
- temp/ 下脚本可复用：note_search2.py（搜note）、note_parse3.py（抓note正文）、r401_10sen.py（十選票数）、r401_kanketsu3.py（最近完结）
