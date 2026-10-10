# 阅读器总目录 + 未译标记 + 汉化/原文切换（2026-08-20 最终版）

**需求演进（用户 3 轮纠正，勿回退到旧方案）**：
1. 第一版只切正文 → **原文按钮点击不生效**（旧实现 localStorage 记忆 + 服务端只列汉化章）
2. **纯原文作品（トレセン学園人狼部 43 话仅下载）也要能读** → 阅读页不得 404
3. 最终定案：**总目录 = 原文全集，未译章标「未译」；有汉化优先显示汉化，无汉化显示原文；模式不记忆（无 localStorage）**

## 1. 首页进度列「差多少话」（同轮早先需求）
- `main.py#index` 每请求现算：`src_total = len([f for f in os.listdir(aa_dir) if f.endswith('.txt')])`，`deficit = max(0, src_total - st.agg.chapters)`；仅 deficit>0 渲染 `<span class="warn small">（差N）</span>`，hover 显示「原文 N 话，差 N 未译」。不入 work_stats 缓存。
- 例：jikan_teishi aa 270/汉化 10 → `1/10（差260）`；已译完无后缀。

## 2. 后端（main.py#reader_page 重写 + logic.merged_chapters 新增）
- `logic.merged_chapters(aa_dir, out_dir, name_style)`：**以 `aa/*.txt` 为全集**（汉化目录可空/不全），每章对齐同名 `*_汉化.txt`（存在=has_tr；`name_style=="eid_title"` 时汉化目录 entry_* 残留不算）；title 取汉化首行否则原文首行；src_url 从原文 `# 原帖地址` 行取；排序同 chapter_order（标题「第N話」优先）。
- `reader_page`：当前章匹配 `f in (ch["tr_file"], ch["src_file"])`（目录链接统一 src_file）；`use_tr = ch["has_tr"]`（**默认汉化，无汉化章自动原文**）；`rendered` 用汉化行（无汉化章 body=原文行）、`rendered_src` 恒原文行，双 JSON 埋点；模板传 `toc`（含 has_tr/label/title/active）+ `has_tr/use_tr`。
- **API 未译章兼容**：`check_chapter` 接受汉化名或原文名（原文名判 `os.path.exists(aa_dir/fname)`）；`api_line_get`/`api_chapter` 原文名 → `tr:[]` 不 500；`api_save_line` 原文名 → 422「未汉化章节不可编辑（仅原文可读）」。
- **纯原文作品注册**：registry 加 `jinrou`（site=yaruobook，aa/raw/work/**汉化 四目录——汉化空目录也要建**，否则 work_stats error 分支）；索引 `0/0（差43）`。

## 3. 模板（reader.html）
- toc：`<a class="... {{ 'notr' if not c.has_tr }}">{{ loop.index }}. {{ c.label }}{% if not c.has_tr %}<span class="badge">未译</span>{% endif %}</a>`；CSS `.notr{opacity:.55;border-style:dashed}`、`.notr .badge{color:#b86a00;font-size:10.5px}`。
- seg：「汉化」按钮 `{% if not has_tr %}disabled{% endif %}`（`.seg-btn:disabled{opacity:.45;cursor:not-allowed}`），active 跟随 use_tr；正文 `data-mode="{{ 'tr' if use_tr else 'src' }}"`；未译章提示「本章未汉化，当前显示日文原文。」。
- 双 JSON 埋点：`<script id="src-data">{{ rendered_src | tojson }}</script>` + `tr-data`（同 cm/site 渲染，否则颜色不一致）。

## 4. 前端（reader.js v=20260820c）
- **无记忆**：初始 mode = `pre.dataset.mode`（服务端 use_tr），**不读写 localStorage**。
- 切换：`b.dataset.mode==='tr' && !hasTr` → toast 拒绝；原文模式行号点击 → toast「原文模式不可编辑」；`!CFG.hasTr` 整体禁编辑（行号/override 同）。
- `READER_CFG` 增 `hasTr`（模板 `{{ 1 if has_tr else 0 }}`）。

## 5. 验证
```bash
curl -s :8765/work/jinrou/read | grep -o 'data-mode="[a-z]*"\|disabled>汉化\|未译'  # src + disabled + 43 未译
curl -s :8765/work/jikan_teishi/read | grep -o 'data-mode="[a-z]*"'                 # tr（首章已译）
curl -s :8765/work/jikan_teishi/read?f=entry_87883.txt | grep -o 'data-mode="[a-z]*"' # src（未译章）
curl -s :8765/api/work/jinrou/chapter/entry_4531.txt/line/0                          # 200 {"tr":""}
curl -s -X PUT .../entry_4531.txt/line/0 -d '{"text":"x"}'                           # 422
```

## 坑
- **旧版被否**：localStorage 记忆 + 只列汉化章 → 已整体重写，勿回退。
- 静态文件改动必须 bump `?v=`；重启用 terminal(background=true)，杀旧按 8765 PID。
- `tr-data` 单章 1.2MB（jikan_teishi）可接受（单章渲染）。

## 关联
- aa/*.txt 仅原文下载流程见 aa-manga-translation「下载仅原文」段（270 话/43 话批量落盘）。
