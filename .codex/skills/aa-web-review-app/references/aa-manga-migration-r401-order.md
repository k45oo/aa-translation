# aaManga 统一目录迁移 + r401 原站按钮 + 原站顺序排序（2026-08-20 晚）

## 1. aaManga/ 目录迁移（用户：「aa漫画统一转移到 <repo>/aaManga 目录，后续新漫画也放这里」）
- 18 部作品目录全部移入 `aaManga/`（2.0G）；`交付HTML/` 当时保持仓库根（**2026-10-10 起也统一到 `aaManga/交付HTML/`**）
- **registry.py**：所有 `BASE + r"\作品名\..."` → `BASE + r"\aaManga\作品名\..."`（批量替换脚本按作品名正则，注意跳过已带 aaManga 的）
- **82 个脚本**：`os.path.join(ROOT, "作品名"` → `os.path.join(ROOT, "aaManga", "作品名"`（saki 80 处、白头 68 处等）
- **git 迁移**：跟踪的作品用 `git mv`（保历史），未跟踪的（r401 8 部 + トレセン）用 `mv`
- **⚠️ git checkout 会丢未提交条目**：中途 `git checkout registry.py` 恢复误改时，**未提交的 r401/jinrou 条目全丢**——重建脚本 `temp/rebuild_registry.py`（普通字符串拼接 `chr(92)` 拼反斜杠，禁 f-string 里 `{BS}` + raw 前缀混用——`\a` 变 BEL 响铃符）
- **坑**：f-string `r"{BS}aaManga..."` 写入文件时 `\a` 变真实 BEL（cat -A 见 `^G`）——路径拼接一律用普通字符串 `+ chr(92) +`，写完 `python -m py_compile` + `od -c` 双验
- work_stats 对纯原文作品（汉化目录空）chapters = aa 原文数、error=None（防索引「✗ 目录错误」）

## 2. 原站小按钮（用户：「作品名右边加个原站小按钮，点击跳转 rss.r401.net」）
- registry 每作品加 `r401_cid` 字段（r401 分类页 id）→ index 路由透传 → index.html 作品名旁 `<a class="r401-link">原站</a>`（`https://rss.r401.net/yaruo/categories/<cid>`，target=_blank）
- cid 来源：8 部 r401 用下载时的分类 id（r401_works_index.json）；老作品用 r401 搜索按标题匹配（存 `temp/r401_cid_map.json`）；**多个同名分类时人工核对**（如時間停止有 19401/21114 多个）
- **样式（用户二次要求）**：与作品名不同（灰字 #888、font-weight:normal vs 作品名深色加粗）、不能太大（font-size:9px）、文字周围加方框（1px #bbb 边框 + 圆角 2px + padding 0 3px + 白底 + vertical-align:2px + margin-left:3px）
- 静态 CSS 改动无需重启（浏览器强刷），JS/模板改动要 bump `?v=`

## 3. 每站点一个解析器（用户：「每个站点对应1个文件。不用合并」）
- 废弃合并的 `yaruomatomex_any.py` → 拆成 4 个独立解析器 + 复用原版：
  - `yaruomatomex.py`（原版已有）→ yaruomatomex.com（r401_dungeon/metsubou/hyakka）
  - `yaruo_matome.py`（新建）→ yaruo-matome.com（r401_kizoku）：容器 `entry-content`
  - `himanatokiniyaruo.py`（新建）→ himanatokiniyaruo.com（r401_ryouri/kimyou）：容器 `entry-body`
  - `yaruocheck.py`（新建）→ yaruocheck.jp（r401_jyusatsu）：容器 `ently_body`，帖头 `N 名前：`（FC2 系）；**终点标记要从正文后找**（`カテゴリ` 在正文头部会截断到 2 行）
  - `yomuaa.py`（新建）→ yomuaa.r401.net（r401_rance）：容器 `<article class="aa">` 分镜漫画，dt 序号不输出
- 每个文件保留自己的 CONTAINER / 帖头正则 / entry_url / get_title（剥站点前缀+后缀）

## 4. Rance 原帖地址修复（用户：「Rance/Stay night原帖地址不对」）
- **根因**：YomuAA 站无 canonical/og:url（og:url 只有站首页 `yomuaa.r401.net/`），`_site_url` fallback 用了 `episode/<arcid>`，但 **arcid ≠ 真实文章号**
- **修复**：从 r401 分类页 exlink 拉 `arcid → https://yomuaa.r401.net/episode/<n>` 映射（205 条），批量重写全部 `# 原帖地址` 行
- 验证：`entry_1004162 → episode/2609`、`entry_1008981 → episode/2635`

## 5. 目录按原站顺序（用户：「目录章节顺序需要按照原站的顺序来，部分番外夹在2话正片中间」）
- 见 toc-label-title-cleaning.md「关联：目录排序按原站顺序」段——`chapter_order.json` + `merged_chapters(order_path)` 参数
- r401 分类页**最新在前**，反转才是原站顺序；番外/クリアリザルト位置自然正确
