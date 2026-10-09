# 进度未译差值与阅读器汉化/原文切换（2026-08-20 实测）

## 1. 首页进度列追加「差多少话」
- **需求**：作品未译完时在 `已读/已译` 后显示 `（差N）`，已译完不变；hover 显示原文总量。
- **实现**：`main.py#index` 对每作品实时 `os.listdir(aa_dir)` 数 `.txt` 得 `src_total`，`deficit = max(0, src_total - st.agg.chapters)`；仅 `deficit>0` 时渲染。
  ```jinja
  {{ w.read }}/{{ s.agg.chapters }}{% if w.deficit %}<span class="warn small">（差{{ w.deficit }}）</span>{% endif %}
  title="已读章节 / 总章节{% if w.deficit %}（原文 {{ w.src_total }} 话，差 {{ w.deficit }} 未译）{% endif %}"
  ```
- **要点**：不入 `work_stats` 缓存（`work_stats` 缓存的是 `汉化` 统计），`deficit` 在 `index()` 每次请求时算；`aa_dir` 不存在时 `src_total=0` 不显示；已译完 `src==tr` 时 deficit 0 保持 `N/N`。
- **验证**：`jikan_teishi` aa 270 / 汉化 10 → `1/10（差260）`；`touhou_teisou` 101/101、`saki` 17/17 无后缀；`curl http://127.0.0.1:8765/ | grep -A2 jikan_teishi`。

## 2. 阅读器目录旁汉化/原文一键切换（零请求/零模型）
- **需求**：阅读页目录旁加分段控件「汉化 | 原文」，本地切换，不跑模型。
- **后端**：`main.py#reader_page` 在原来 `rendered` 循环内同步生成 `rendered_src`（同一 `colormap` + `site.render_line`，`src_lines[i]` 为原文行；`mt` 与行号列一致），一并传模板：
  ```python
  rendered, rendered_src, line_nos = [], [], []
  # 循环内：
  rendered.append({"idx": i, "mt": mt, "html": gen_aa_html_render(i, l, cm, site)})
  rendered_src.append({"idx": i, "mt": mt, "html": gen_aa_html_render(i, src_l, cm, site)})
  return TemplateResponse(..., {"rendered": rendered, "rendered_src": rendered_src, ...})
  ```
- **模板**：`reader.html` 顶部 `toc-head` 内 `div.seg#src-toggle` 双按钮（样式 `.seg/.seg-btn` 复用 nav 深色，与 `toc-label` 同行 `display:flex`）；正文 `pre#chapter-pre` 加 `data-mode="tr"`；底部注入双 JSON：
  ```html
  <script type="application/json" id="src-data">{{ rendered_src | tojson }}</script>
  <script type="application/json" id="tr-data">{{ rendered | tojson }}</script>
  ```
- **前端**：`static/reader.js` 末尾 IIFE 读取双 JSON，`render(mode)` 替换 `pre` innerHTML（保留 `mt` 的 `calc(var(--aa-lh,16px) * ...)`），同步 `seg-btn.active`、`#mode-hint` 文案、`localStorage aa_reader_mode`；`closeEdit()` 防止切换时残留插入行；`line-col` 点击在 `data-mode=="src"` 时 `toast('原文模式不可编辑，请切回汉化', false)` 禁编辑。
- **坑**：`tr-data` 1.2MB（`jikan_teishi` 全章），但单章渲染所以可接受；`src-data` 与 `tr-data` 必须同 `cm/site` 渲染否则颜色不一致；静态资源改动后 bump `?v=`（reader.js）可不清缓存验证。
- **验证**：`curl /work/jikan_teishi/read | grep -c src-toggle` 1，`grep seg-btn` 含 `汉化`/`原文`，`grep src-data` 存在；154 单测 `OK`。

## 关联
- aa/*.txt 仅原文下载流程见 `aa-manga-translation` 「下载仅原文」段（270 话/43 话批量落盘案例）。
- 显示偏好 `html.escape` 与字体语义仍复刻交付 HTML。
