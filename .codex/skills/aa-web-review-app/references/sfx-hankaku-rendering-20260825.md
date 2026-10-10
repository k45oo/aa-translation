# 半角假名译文 sfx 渲染特效（2026-08-25 建立，2026-09-15 加人工标记）

## 需求
半角假名（ｦ-ﾟ）拟声词/效果音翻译后的中文，在阅读器中与普通台词**明显区分**（楷体+斜体+缩小+黑色，用户迭代：0.72em 太淡看不清 → 改 0.95em + 黑色 #333）。
2026-09-15 追加：**划词翻译替换纯半角假名时用该样式**（`class="sfx"`），并**加一个「sfx 替换」按钮**（原文不是半角假名也能强制）。

## 渲染（scripts/common/gen_aa_html.py，管线与工作台共用）
- `sfx_spans(l, src_line=None, marks=None)` → `[(start, end)]`（字符区间并集）：
  - **自动**：原文行半角假名段（`HANKAKU_RE = [\uff61-\uff9f]{2,}`）**起始显示宽度** = 汉化行同宽度位置（`_width_prefix` 按 `disp_width` 累计切分）→ 该区间；段内**含中文**才要（`HAN_ZH_RE`，纯图形跳过）。**已知局限**：区间宽度受原文段宽度限制 → 译文比假名宽时只覆盖前半（`ｷｭｯ` 3 格 vs `心头一紧` → 只有「心」被包）——这正是「sfx 替换」按钮的用武之地。
  - **人工**：`marks = [[字符偏移, 文本], ...]`；偏移命中优先，漂移（行内增删）时取**最近的同文本出现处**，找不到就忽略（不猜区间）。
- `render_line(i, l, cm, site, src_line=None, sfx_marks=None)`：`sfx_marks` 可传整章 dict（键=1-based 行号串，内部 `str(i+1)` 取）或行内 list；`_render_spans` 负责转义+包 span+整行颜色。`render_sfx_line(l, src_line, color)` 保留为兼容包装。
- **等价性一次性验证过**：旧实现 vs 新区间实现在 8 个作品 285,939 行上跑 857,817 次比对 —— 唯一差异是「相邻区间被合并成一个 span」（视觉等价），`_merge_spans` 改成只合并**重叠**（`a < prev_end`）后 diff = 0。改这段前先跑同类比对。
- CSS：`pre.aa span.sfx { font-size: 0.95em; font-style: italic; color: #333; font-family: "楷体","KaiTi","STKaiti",serif; }`（工作台 style.css）。

## 标记落盘 + API
- 文件 `<work_dir>/sfx_marks/<base>.json`，`{"1-based行号": [[字符偏移, 文本], ...]}`；`logic.sfx_marks_path/_clean_sfx_marks/load_sfx_marks/add_sfx_mark/clear_sfx_marks`（原子写 `.tmp` + `os.replace`；脏条目、空文本过滤）。
- PUT `/api/work/{w}/chapter/{f}/line/{idx}` body 增 `sfx` / `sfx_clear`（区间清）/ `sfx_clear_all`；响应增 `sfx_changed/sfx_added/sfx_cleared/sfx_marks`。GET 回带该行 `sfx`。
- 前端 `static/selection_translate.js`：`.sel-sfx` 按钮 + `isHankakuSfx()` + `sfxPayload(mode, off, segLen, seg, zh)`；正文场景直接进 PUT body，textarea 场景经 `#edit-panel.dataset.pendingSfx` 交给 reader.js `saveEdit`。

## 坑
- **base 归一**：`logic.txt_key("entry_1_汉化.txt")` = `entry_1_汉化`（**不剥 `_汉化`**）——sfx_marks / colormap 的键必须是原文 base，统一 `logic.txt_key(logic.src_name_of(fname))`（阅读页用的是 `txt_key(src_file)`）。曾经写成 `txt_key(fname)` → 标记写进 `sfx_marks/entry_1_汉化.json`，阅读页永远读不到。
- **`/line/{idx}/html` 必须传 `src_line`**（否则保存后重渲染的行掉 sfx 特效，整页刷新才恢复）；`gen_aa_html_render` 包装函数要透传全部参数（历史上漏 5 参 → 阅读页 500，见 restart-verify-pitfall）。
- **`save_line` 的 `unchanged` 短路**：sfx 标记写入必须放在 `if r["result"]=="ok"` 之外；前端判短路要带 `&& !j.sfx_changed`。
- **patch 工具 CRLF 归一化会把 main.py 写坏**（U+001B 混入 → SyntaxError）——改含 CRLF 的 .py 用 python 脚本 `open(p, encoding='utf-8', newline='\n')` 读写（现全仓已统一 LF，patch 可直接用）。
- 样式静态文件改动**浏览器刷新即生效**；`main.py`/`logic.py`/`gen_aa_html.py` 改动需**重启工作台（只能 bat）**。
- 交付合集生成器 `gen_aa_html.main()` 至今不传 `src_line`/标记 → **交付 HTML 里没有任何 sfx**（工作台阅读器专用）。要补需给 main() 加读取 `work/sfx_marks/` 并传参，且全库重新生成交付物。
- **浏览器验证套路**：`browser_console` 一次 eval 内用 TreeWalker 按 `textContent` 偏移建 Range → `dispatchEvent(new MouseEvent('mouseup',{bubbles:true}))` 触发热区；等翻译回来再点按钮；断言读 `getComputedStyle(span.sfx)`（fontStyle/fontSize/color/fontFamily）比截图可靠（`browser_vision` 可能拿不到分析）。测试会真写数据——按「污染还原纪律」还原并清空标记文件。
