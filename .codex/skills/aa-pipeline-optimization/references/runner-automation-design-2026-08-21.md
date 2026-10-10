# Runner 自动化方案（2026-08-21 设计定稿，待实施）

grilling 会话产出：两个通用 runner（dl_runner 下载原文 / translate_runner 翻译流程）+ 工作台薄入口。
全部 17 项决策经用户逐项确认。**状态：设计定稿、阶段 0（文档先行）尚未开工**——本文件是实施时的唯一权威 spec。

## 现状地图（实测审计）

**已参数化（2026-08-18 Phase 3，无需重做）**：
- `works/paths.py` BASE = 动态仓库根；全仓 `<repo>` 硬编码 **0 处**
- `common/dl_chapters.py` 支持 `--json <chapters.json>` + `--work <id>`（无 --work 时从条目 URL 域名反推站点）
- `common/extract_all.py` 支持 `--work <id> [--file entry_x.html] [--limit N]`
- `aa_translate`（平铺 `scripts/aa_translate.py` + common 薄壳）支持 `--work <id> [--pilot eid1,eid2]`；**--pilot 已按 eid 过滤 + 自动跳过已有 `_汉化.txt`**（runner 翻译入口零改动）；registry 模式还有按作品 r18 自动选后端
- `common/pass2/pass3/scan_width/fix_width` 均 `--work <id>`；`cleanup/` 与 `runner/` 下是薄封装转发 common/works

**真硬编码残留（用户点名要改的）**：
- `scripts/download/dl_chapters.py` / `dl_kaiki.py` / `dl_saki.py` / `dl_fc2.py`：CHAPTERS/EIDS 清单 + url_pattern 写死（早期遗留，与 common/dl_chapters.py 功能重叠）
- `scripts/works/dl_touhou.py`：touhou 特殊下载（正文经 `/api/chapters/<eid>/content` 注入章节页），URL 写死不走 registry url_pattern → registry 需 `dl_type=api_inject`
- `works/postprocess.py`：白头 TITLES 42 条（人工汉化标题）+ ORDER 硬编码 → TITLES 迁 `work/baitou_titles.json`，ORDER 改用 chapter_order.json
- `scripts/aa_translate.py` 顶部 AA_DIR/OUT_DIR/WORK_DIR 白头常量（旧常量模式保留，registry 模式是新分支）
- 56 个脚本含"白头与染灰魔女"（含 legacy/ 归档 + 作品专属 pass5_* 等）——**C 类不改**（作品绑定是其设计）

**关键事实（实施依赖）**：
- `r401_index.py category <cid>` **只取第 1 页（40 条）**——>40 话作品必须加 `--all` 自动分页（解析 `?page=N` 分页链接→逐页 fetch→去重→反转=原站发布顺序）
- `chapter_order.json` 格式 `{"order": ["eid",...]}`（eid 字符串数组），9 部作品已有；`chapters.json` 仅 3 部有
- `aa_web_app/main.py` 有硬编码 `GENERATORS` dict（生成器分派）→ 迁 registry `generator` 字段，消除双份维护
- `batch_cleanup_driver.py`（本 skill templates/）定向清理模板**未部署到 scripts/** → 逻辑并入 translate_runner
- works/translate_*.py 是薄壳 → common/aa_translate.py；quality_gate 的 `--work` 是作品**目录名**（キッコーマン）≠ registry id（kikkoman）

## 设计定稿（17 决策）

**CLI**：
```
python scripts/runner/dl_runner.py --work <id> [--chapters <eids|1-20|all|new>] [--json <file>] [--skip-order]
python scripts/runner/translate_runner.py --work <id> [--chapters <eids|1-20|all|new>] [--no-dl] [--force] [--no-gen] [--workers N]
```
- dl_runner 默认 `all`（下载幂等，多下无害）；translate_runner 默认 `new`（只译缺的，最安全）
- `--chapters 1-20` 按**原站顺序**取前 N 话（chapter_order.json 优先，无则实时拉 r401 反转），不是 eid 数字大小
- `--chapters new` 分层判定：dl_runner new=raw/aa 缺，translate_runner new=汉化缺（各 runner 语义自洽，中断恢复自然衔接）

**决策表**：
1. 两独立 runner，translate_runner 可自动调 dl_runner（也能独立跑）
2. 章节 eid 形态（eid 是管线唯一稳定标识；话数跨站会错位——FC2 与 bookshelf 不同步）
3. CLI 为主 + 工作台薄入口（后台子进程+轮询进度），终端 runner 可被 cron 复用
4. 硬编码范围：A（dl_* 清单）+B（URL 模板）全改 + D（白头 TITLES 迁 JSON）；C（作品专属脚本）不动
5. 清单来源：r401 分类页实时拉取全分页（权威源）+ `--json` 覆盖 + 缓存 work_dir/chapters.json
6. r401_index.py 加 `--all` 自动分页（对现有工具的增量改进，不新造轮子）
7. 顺序基准 chapter_order.json 优先，无则 r401 实时反转
8. 定向清理并入 translate_runner（--chapters 贯穿翻译+清理全链路，吸收 batch_cleanup_driver 模板逻辑）
9. 新站：registry 加 `dl_type`（standard/api_inject）+ `--json` 覆盖；sites/ 解析器注册流程不变，runner 只做收敛
10. 断点续传依赖现有幂等（_汉化.txt/raw 跳过），失败章记 `<work_dir>/runner_fail.txt`，`--force` 才重译
11. new 判定分层（见上）
12. 部署 scripts/runner/ 放通用 runner，作品专用 runner（touhou/saki/kaiki_runner）移 works/
13. registry 加 `generator` 字段（默认 common/gen_aa_html.py），工作台 GENERATORS 改读 registry
14. CLI 形态如上确认
15. 任务状态 `<work_dir>/runner_task.json`（pid/status/started/eids/log_tail）+ 文件锁防重（多开 TUI/工作台双入口安全）
16. r401 拉取失败**报错终止不静默降级**（静默降级会空清单误判"无新章"）；`--json` 显式兜底；new 时可用本地缓存（标注"用缓存"）
17. 落地顺序：文档先行→基础设施→runner→工作台→验证，每阶段独立 commit

## 落地阶段（待执行）

- **0 文档先行**：AGENTS.md + scripts/README.md + skill 同步，方案落 `doc/runner自动化方案_2026-08-21.md`
- **1 基础设施**：r401_index `--all` 分页 → registry 加 `dl_type`/`generator` 字段 → 白头 TITLES 迁 `work/baitou_titles.json` → 工作台 GENERATORS 改读 registry
- **2 runner**：`runner/dl_runner.py` + `runner/translate_runner.py`（定向清理并入）→ 作品专用 runner 移 works/
- **3 工作台**：翻译任务页（勾章节→提交→后台子进程→轮询进度→文件锁）
- **4 验证**：unittest + 真实作品冒烟（如 jinrou 43 话 `--chapters new` 应全 skip / 补 1 话端到端）

## 实施时的坑（从设计推导，落地注意）

- 白名单 build_whitelist 仍全量统计（零 API 口径不变），只有**处理**范围定向到 eid——勿把白名单也定向（口径漂移）
- pass2 的 residual_text 无白名单会误改已交付文件——定向清理的 eid 过滤是安全边界，不能丢
- touhou 类 api_inject 站下载=章节页 + API 二次请求注入，dl_type 分发不能走普通 fetch
- 工作台子进程跑 translate_runner 会烧 token——UI 上必须显式展示"本次将翻译 N 话/预计 token"再确认
