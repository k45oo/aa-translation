# 新站点接入速查（2026-08-10 第七站 touhouyaruosure 实战）

适用：rss.r401.net/yaruo/categories/<id> 分类页发现的任何新站点。完整 runbook 见
`aa-manga-translation` 技能各站点章节；本文档只记**跨站通用模式 + 本战特有坑**。

## 接入流程（从分类页到批量翻译）

1. **分类页抓取**：`curl -x http://127.0.0.1:10808 -A "<浏览器UA>" "https://rss.r401.net/yaruo/categories/<id>?page=N"`。
   - 新版 r401 是 Rails 应用，**每页 40 条**（旧版 20 条），条目外链 `class="exlink"` 指向**真实站点**（不再直接是 yaruonichijou.com）。
   - 章节清单用 `re.findall(r'class="exlink" href="([^"]+)">\s*([^<]+)</a>')` 解析，eid 从 URL 里 `/(?:chapters|blog-entry)/(\d+)` 提取。
   - 分页页数 = ceil(总数/40)；按 eid 去重排序，**eid 连续**（9553-9653）即完整。

2. **正文定位**：章节页可能不含正文！现代站（Rails/Honox island）正文走 API：
   - 页面里搜 `honox-island` / `<div id="c-chapter-content">` / `<div class="py-8 space-y-3 animate-pulse">`（skeleton 骨架屏）→ 正文在 `/api/chapters/<eid>/content`。
   - 老站特征：`<span class="aa">`（括号配对法）、`<dl><dt><dd>`（dt 切分）、`entry-content` 等 → 直接页内提取。

3. **dl 脚本适配**：正文走 API 的站点不能复用 `common/dl_chapters.py`（只抓单 URL）。
   写作品专用 dl（参照 `works/dl_touhou.py`）：
   - 抓章节页 + 抓 API content → **把 API 正文注入 `c-chapter-content` div 的 skeleton 位**，raw 存单文件，`extract_all.py` 无感复用。
   - 注入点：`s.find('id="c-chapter-content"')` → `tag_end = s.find('>', i)+1` → `sk_start = s.find('<div class="py-8', tag_end)` → `sk_end = s.find('</div></div>', sk_start)`；替换为 `s[:tag_end] + body + s[sk_end+13:]`。
   - **注入后闭合 div 数 = 1**（skeleton 的 `</div></div>` 被吞，只剩 c-chapter-content 自身闭合）——ad-hoc 验证断言用这个。

4. **sites/<站>.py 解析器**：复用 `_base.py` 的 `clean/build_txt/render_common_header`。
   - 帖头 `NNN ： ◆作者 ： 时间 ID:xxx`：作者绿粗 `#008800` / ID 红粗 `#ff0000`（bookshelf 系通用）；无 ◆ = 名無し（作者绿非粗、ID 黑）。
   - dd 开头 ` <br/>` 模板空行必须跳过（`row.strip()==''` continue）。
   - 红色 span 跨 `<br>` 断裂 = 骰子/红字行 → **区间重叠检测**（`row_start < re_ and row_end > rs`）。
   - 蓝色 span = `>>N` 引用，量少不还原。
   - 验证方法：`python scripts/sites/<站>.py raw/entry_<eid>.html aa/` 单文件提取，比对行数/颜色。

5. **registry 加条目** → `extract_all.py --work <id>` → `aa_translate.py --work <id> --pilot <eid>` 试译 1 话 → 浏览器实测 → 批量。

6. **批量 runner**：参照 `works/touhou_runner.py`（等批次 → pass2 → pass3 → scan_width → fix_width → gen_aa_html）。

## 本战坑（2026-08-10 touhou_teisou）

- **colormap 键是 0-based 行号**（`build_txt` 用 `len(txt_lines)-1`，`gen_aa_html.render_line(i,...)` 用 enumerate 索引直接查）——**验证时按 1-based 查会整体错位 1 行**，把帖头行误判成"被标红"（本战白查了 3 轮）。さーきー 旧版是 1-based 历史产物，勿当现行约定。
- **该站红色 = 原站红字非骰子**（用户确认不影响交付），不要自作主张改颜色规则。
- **批量后宽度修复链**：fix_width 后残余超宽行（横幅台词行）→ 本地删 U+3000 零 API 修复（见 SKILL.md「fix_width 失败行的零 API 本地修复」；脚本 `works/fix_touhou_width.py`）→ scan_width 复核 0 错位 → 重新 gen_aa_html。
- 高频假名片段（やる/パチュリ/ベアトリ 等角色名）由 pass3 全量白名单自动覆盖，不用手工预补。
- 浏览器实测渲染时，**视觉模型对 AA 长截图会幻觉**（两次给出互相矛盾且含页面不存在的作品名/配色的描述）——以 `browser_console` 的 DOM 断言为准（`document.title`、`pre.aa` 长度、`span[style*=008800/ff0000]` 计数、`hasChinese` 正则），截图只作参考。

## 已有作品换站 + 分类目录标题匹配选章（2026-08-28 akari_series 实战）

**场景**：作品原站地址变更（akari_series：r401 → `https://yaruobook.jp/?cat=446` 2号馆），且**该分类目录 244 条多部作品混在一起**——需标题匹配筛选本作章节再更新。

**完整流程**：
1. **拉分类全量**：`python scripts/common/yaruobook_index.py category 446 --site jp --all --json /tmp/cat.json`（三站 com/jp/net 同构，jp=2号馆）
2. **标题匹配筛章**：正则 `紲星\s*あかり`（**容忍空格变体**——`紲星 あかり` 与 `紲星あかり` 都是本作；核心匹配词=角色名/标题前缀）从 244 条筛出 43 条本作章节
3. **识别新增**：清单 eid − 已有 aa 目录 eid。**坑：`f.replace('.txt','')` 得 `entry_29662` 而 JSON eid 是 `29662`，直接 `not in` 会全判新增**——必须 `re.match(r'entry_(\d+)\.txt')` 剥离前缀
4. **重建权威清单 `work/chapters.json`**（`chapters_source=cache` 时 dl_runner 只读它）：带 `arc` 字段（从标题子串判：`狂った村`→村、`歪んだ街`→街）、date 转 ISO（`2026年8月28日`→`2026-08-28`）、url 用清单自带 `yaruobook.jp/?p=<eid>`
5. **下载提取**：`dl_runner.py --work <id> --chapters new`（读缓存清单，下载缺失章 → 提取 → 固序；原帖地址自动取页面 canonical/og:url 真实域名）

**坑**：
- **registry 的 `chapters_source=cache`** 决定 dl_runner 只读 work/chapters.json 缓存、不拉 r401——换站后**必须重建缓存**（否则新章永远拉不到）
- 标题匹配的**核心词**用「本作所有章节标题都含的最小公共子串」（akari_series = `紲星あかり` 角色名），不是作品名（`あかりちゃんシリーズ` 是合集名不在单章标题里）
- 视觉模型/推理模型（4.1v、M3）**长批角色名保留日文**（40 行假名 68=やる夫/紲星 全未译）——长批翻译主力别用它们，仅短文本/校对（详见 SKILL.md「glm-4.5-air 耗尽替代实测」）
