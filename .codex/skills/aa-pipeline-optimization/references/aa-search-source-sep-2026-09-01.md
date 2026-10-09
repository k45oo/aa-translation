# aa_search.py 源独立统计 + 排序 + 报告（2026-09-01 第二版修正）

第一版实现后用户两个硬纠正 + 数据缺陷修复。改搜索聚合/排序/输出前先读本文件 + `aa-search-2026-09-01.md`。

## 用户纠正 1：r401 与 yaruobook 独立统计，不要混在一起

- **聚合键按源分**（不再统一 cid/norm_title 混并）：r401 记录 `r401:{cid}`、yaruobook 记录 `yb:{site}:{cat_id}`、兜底 `misc:{norm_title}`。同名作品两源各保留一条独立记录（`source` 字段 r401/yaruobook）。**旧版把 r401 和 yb 同名作品合并，用户明确不要**。
- **展示/报告按源分组**：表格先打 `── r401（N 条，标题命中 M，本地已收 K）──` 再 `── yaruobook yaruobook.net（...）──`。r401 组站点可能为空（`-`）——只有作品头无 site。

## 用户纠正 2：--site 语义 = 只搜该源（曾混跑 r401）

旧分支 bug：`if args.site and args.site in sites: tasks = [r401_work, r401_chap, sites[args.site]]`——**`--site net` 时 r401 也跑了**（r401 版 ch=1 与 yb 版 167 章同存，排序挤压导致「ポケモン世界で、生き直すようです」看起来没搜到，实际本地已收被压到 20+ 名）。修正为互斥：
```python
tasks = []
if args.site == "r401": tasks = [r401_work, r401_chap]
elif args.site in sites: tasks = [sites[args.site]]   # 只该 yaruobook 站
else: tasks = [r401_work, r401_chap, yb_com, yb_jp, yb_net]  # 无 --site = 4 源全搜
```
`--tag` 任务在 --site 非 r401 时也要能追加（tag 只对 r401 有效，追加即可）。

## 标题命中靠前 + 排序

- `title_hit = kw in a["title"]`（关键词直接出现在作品标题）。排序键：`(not title_hit, x["local"], -_date_key(date), title)`——标题命中 → 未收录在前（找新作品）→ 日期倒序。**`x["local"]` 不用 `not x["local"]`**（后者配 sort 不带 reverse 会把已收排前；旧版 reverse=True 还把 title_hit 反转排最后——排序双坑）。
- `_date_key`：`2026年9月2日`/`2026-09-02` → `YYYYMMDD` 数值，空日期 0（排最末）。
- 用户主用例是找新作品，所以**未收录在前、已收录（✓）殿后**——别把已收顶前面。

## r401 同标题多 cid 归并（同作品多分类归档）

r401 里同一作品常有两个分类 id（镜像/换站重发，如 ポケットモンスター Yell cid 23702+21993 同标题同 40 章）。组内按 `norm_title` 归并折叠成一条：cid 逗号合并、chapter_count 取 max、date 取较新、sites 并集。

**坑**：归并字典键要用 `merged[nk] = x`（nk=norm_title）存第一条，后续同 nk 直接改这条——**不能 `merged[x["key"]] = x`**（key=`r401:23702`，`nk in merged` 永远查不到）。

## --report <path>.md（markdown 报告）

`--report out.md` 输出按源分组 markdown：标题/站点/章节数/日期/已收录(work_id)/标题命中(●) 表格 + 每作品前 3 章节明细。生成后 `print(f"报告: {path}")`。**新增 CLI 参数后必须重跑 `gen_inventory.py` + `gen_interface_docs.py`**（test_interface_docs 闸门）。

## 超时修复（2026-09-03，本节旧「全搜接受 com 缺失」已过时）

**根因**：yaruobook `?s=` 是 WordPress **全文搜索**（服务器扫正文+正则，非简单标题查询），响应天然慢。旧代码每页硬编码 `fetch(timeout=120)`——而 **com 站单页实测 ~150s**，120s 超时把 com **每次都杀**（所有搜索 com 组全空/缺失的直接原因，不是 com 没数据）。net ~40s、jp 中等，勉强没死。

**修复**：
```python
YB_TIMEOUT = {"com": 300, "jp": 180, "net": 180}   # 模块常量
# yaruobook_search / yaruobook_categories 内：timeout = YB_TIMEOUT.get(site, 180)
```
**实测**（`--site com` 搜 ポケットモンスター）：旧 120s → com 永远空；新 300s → **81 条**（含 373 章 `ブリジットのポケットモンスター(直球)` 标题命中）。耗时 5m9s（2 页，每页 ~150s）。

**并行等待重构**（否则放宽超时反而拖死全搜）：旧 `for f in futs: f.result()` 顺序等——慢源拖死全部（com 300s×2 页会让全搜等 10 分钟）。改：
```python
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout
ex = ThreadPoolExecutor(...)
try:
    if len(tasks) > 1:
        for f in as_completed(futs, timeout=360):   # 快源先出（r401/net/jp），com 慢不阻塞
            results.extend(f.result() or [])
    else:
        for f in futs: ...                            # 单源模式等满（用户主动选 com 可接受 5min）
finally:
    ex.shutdown(wait=False)   # ⚠️ 关键：不能用 with（__exit__ 隐式 wait=True 等慢源），shutdown(wait=False) 让其后台自然结束
```
**代价**：单站 com 搜索要等 ~5 分钟（`--site com` 主动选时接受）；全搜有 360s as_completed 上限保护，其他源先出。**改代码后必须 kill 重跑验证**（旧进程已加载旧超时常量）。

## 2026-09-03 增强方案定稿（doc/aa搜索增强方案_2026-09-03.md，未实施）

grilling 逐点确认 + 审阅修订后的设计决策（实施时按此，勿重开已决问题）：
- **yaruobook 章节总数 = 末页跳转 1 次计算**：分类页每页固定 300 条，分页链接取最大页号 N，末页计数 → **总数 = (N-1)×300 + 末页条数**（用户指正：不必逐章/逐页累计，一次末页跳转即可；同页取简介 + 最早章节日期=开坑日期，三个数据共用同一批请求）。
- **`--started-after` 仅标注不裁剪**（用户确认）：开坑日期依赖总数抓取（末页最早章节），无法预过滤；语义 = 报告/表格标出「开坑晚于该日期」供参考，不改变结果集。`--since/--until` 才是预过滤（作用于最新更新日期，聚合后、总数抓取前，零成本先砍）。
- **com 慢站预算（新增决策 7）**：任何涉及 yaruobook.com 的抓取（搜索 2 页 ≈ 5min、单请求 ~150s）都要独立预算——全搜 as_completed 兜底已够，但总数抓取阶段遇 com 结果会 15 分钟级（50 条 × 并发 8 ÷ 150s/请求），需跳过或 `--slow-count` 显式开启。原方案成本估算（50 条 10-20s）只对 r401 成立。
- **R18 判定**：r401 走 tag 26 权威集合（全量 ~72 页爬取缓存 1 天 TTL，r401 单页 2-5s → 20-40s 非方案写的 10-15s）；yaruobook 走标题+简介关键词表落 `scripts/config/search_categories.json`（人工可维护）。`--tag` 可重复多标签 AND；无关键词 = 纯标签浏览模式。
- **报告自动落盘**：`--report` 不带值 → `doc/aa搜索报告/<关键词>_<YYYYMMDD_HHMM>.md`（目录自动创建；无关键词浏览模式用 `tag-<标签组合>`）。表格新增列：タグ/分类、R18（●/推定）、章节总数、开坑日期。
- **commit 纪律教训**：doc/接口文档.md 与 scripts/_inventory.json 是 gen 工具**全量重生成**，常混入其他并行会话的后端 env 改动（如 AA_DS_MAX_INPUT/AA_QS_MODEL）——提交时只 add 明确属于本任务的文件（aa_search.py + 方案 doc），接口文档/inventory 混合改动跳过（避免把别人的后端改动卷进本任务 commit）。

## 其他实测

- **yaruobook.com `?s=` 慢但可用**（超时放宽后，见上节；net ~40s、jp 中等、com ~150s/页）。单站 com 查询耐心等 ~5min；全搜有 as_completed 360s 上限保护。
- r401 作者页合集过滤：`_is_collection_title(title, own_trips)` 判他人 trip（标题含 `◆xx` 且非本人 trip → 合集）+ 词表（氏の作品集/雑談/投下所/黒歴史/製薬/隔離施設/証券取引所/調剤所/一発ネタ）。本人 trip 从 aid 名提取。抓前 3 页 + 过滤后ポン 返回 68 条本人作品（vs 上千条全分类）。
- 全文搜索命中大量非主题作品是站端固有（搜「初音」能把无关作品 154 章全命中）——站端搜索行为无法控制，展示按 cat 聚合+标题命中排序已是最优解。
