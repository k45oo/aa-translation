# 字体与宽度实测数据(2026-08-09,さーきー 全语料)

## 三种字体对比(24px 下,em=24.0)

| 字体 | 简体字形 | 半角片假名 U+FF61-FF9F | 结论 |
|---|---|---|---|
| **Sarasa Mono SC**(更纱黑体,下载) | ✅ cmap 0 缺字 | ✅ 半宽(12px=0.5em) | **唯一正解** |
| MS Gothic(msgothic.ttc) | ❌ JIS 缺简体("们说这会的红饭钱车东长门问…"→"口") | ✅ 半宽 | 缺字不能用 |
| SimSun(simsun.ttc) | ✅ GB2312 全覆盖 | ❌ 全宽(24px=1em),AA 人物错位 | 错位不能用 |

## 全角码位实为半宽(等宽字体 advance=0.5em,disp_width 会算错)
- 语料中约 99 个:′ ″ Ⅳ ⅵ ‘ ’ 及 U+2002-200A 系列空格等
- MS Gothic 的同类不符字符(11 个):— ´ ¨ × ° U+2002/2005/2006/2007/2009/200A
- 结论:渲染 x 坐标必须按**字体实测 advance** 累加(cells = round(getlength(ch)/half),half = getlength('a')),连续同格数段合并绘制;不能用 aa_common.disp_width(它按码位猜格数)

## HTML/浏览器侧的字体栈：实测事实与**现状（2026-10-03 已按用户要求还原）**

> ⚠️ **当前代码 = `ＭＳ Ｐゴシック` 在前**（用户 2026-10-03 明确要求还原）。下面是实测记录，"该不该改"由用户决定；**不要自作主张改回去**。

2026-10-02 曾把三处字体栈改成 MS Gothic 在前，理由是实测（本机 Chromium，15px）：

| 字符 | PGothic（现行） | MS Gothic |
|---|---|---|
| ASCII `\|` | 4px | 8px |
| 半角假名 `ﾆ` | 8px | 8px |
| 汉字 `漢` | 15px | 16px |
| 全角 `ヽ` | 11px | 16px |

即：PGothic 是比例字体，**AA 列位在浏览器里不对齐**（MS Gothic 才是精确 1:2）；但用户看惯现有效果并在 2026-10-03 要求还原 —— 改字体栈属于**需先确认**的改动。

- 验证法（真要改时必须做）：浏览器打开单章 HTML → `getComputedStyle(pre).fontFamily` 取实际解析栈 → 注入 span 量 `|`/`ﾆ`/`ヽ`/`漢`/`　` 宽度 → 要求 `全角/半角 == 2.00`
- 位置（共 9 处）：`scripts/common/gen_aa_html.py`(2) / `aa_web_app/static/style.css`(5) / `aa_web_app/templates/board.html`(2)
- ⚠️ 图片版 `scripts/tools/aa_to_images.py` 注释写「与交付 HTML 同款字体语义: 主=MS PGothic」✓ 与现状一致；若日后改 HTML 字体栈，图片侧须同步重测（图片侧另有「MS Gothic 缺简体字」问题，见上文）

## 缺字检测方法

交付 HTML 与工作台 CSS 曾把 **「ＭＳ Ｐゴシック」/MS PGothic 排在第一位**（沿袭 2ch 习惯）——**PGothic 是比例字体**，实测（本机 Chromium，15px）：

| 字符 | PGothic（旧栈） | MS Gothic（改后） |
|---|---|---|
| ASCII `|` | **4px** ✗ | 8px ✅ |
| ASCII `A` | 11px ✗ | 8px ✅ |
| 半角假名 `ﾆ` | 8px | 8px ✅ |
| 全角 `ヽ` | 11px ✗ | 16px ✅ |
| 汉字 `漢` | 15px ✗ | 16px ✅ |
| 全角空格 U+3000 | 10px ✗ | 16px ✅ |

即：旧栈下 AA 的竖线/斜线（AA 里大量使用）只有 4px、汉字 15px、半角假名 8px → **列位全乱**（渲染不出 disp_width 的 1:2 网格）；改 MS Gothic 后 ASCII/半角=8px、全角=16px，**比例精确 2.00** ✅。
（2ch 的经典 AA 字体确实是 Ｐゴシック，但那是**等宽克隆 Mona/Saitamaar** 的语义；Windows 上的 MS PGothic 是真比例字体，不可直接当等宽用。）

- 改法（一次改全，共 9 处）：`"MS Gothic", "ＭＳ ゴシック", "Mona", "Saitamaar", "SimSun", monospace`
- 位置：`scripts/common/gen_aa_html.py`(2) / `aa_web_app/static/style.css`(5) / `aa_web_app/templates/board.html`(2)
- **验证**（改前必做，勿只看代码）：浏览器打开单章 HTML → `getComputedStyle(pre).fontFamily` 取**实际解析**的栈 → 注入 span 量 `|`/`ﾆ`/`ヽ`/`漢`/`　` 的宽度 → 要求 `全角/半角 == 2.00`
- ⚠️ 图片版 `scripts/tools/aa_to_images.py` 的注释写「与交付 HTML 同款字体语义: 主=MS PGothic」——HTML 改 MS Gothic 后**该注释与选择逻辑已不一致**，改图片版前先重测（图片侧另有「MS Gothic 缺简体字」问题，见上文）。

## 缺字检测方法
- ❌ `ImageFont.getmask(c).getbbox()` / `font.getlength(c)` 均不可靠:FreeType 对缺字形返回 .notdef,advance=1em 与正常全角无异
- ✅ fontTools:`cmap = font.getBestCmap()` 查覆盖;`hmtx[cmap[o]][0]` 查 advance(与 cells×half 对比,容差 2 font units)
- 实测 Sarasa 对さーきー 全语料 2989 字符:0 缺字,advance 全部 == disp_width×500(UPM=1000)

## 交付页内嵌字体（2026-10-08 起默认开，`common/aa_font.py`）

上面那个「PGothic 比例字体→AA 列位不齐 / 手机没字体」的问题，解法是**把等宽字体内嵌进交付页**
（借鉴 zseineo/infrared-singularity 内嵌 Monapo/Saitamaar 的做法，这里改用 OFL 可外发的 Sarasa Mono SC）。

- 字符集 = 固定块（ASCII/箭头/带圈数字/制表符/方块/几何/CJK 标点/平片假名/全角 ASCII/半角假名）
  ∪ 本次交付各章语料实际字符；fontTools 子集 + woff2，缓存 `fonts/subset/aa_<sha1>.woff2`（.gitignore）
- 形态：**单档**（数据内嵌 index.html）→ `data:font/woff2;base64,` 内嵌（单文件带走）；
  **外置** → 交付夹 `aa_font.woff2` + 相对 URL（Chromium `file://` 实测相对路径字体可加载，不必 base64）
- 实测（baitou 5540 字符 → 941 KB；语料真缺字仅 1 个 ৲）。**但——「网格正确」≠「画得对」（2026-10-08 用户 A/B 定案）**：
  同章 200 采样字符（15px）内嵌 Sarasa **0 个**偏离 1:2 网格，现行 PGothic 栈 **37 个**
  （`=`8、`/`8、`,`3、`レ`13、`マ`14、`ニ`13、`Ｎ`12、`ぃ`12）——**这批作品的 AA 是按 MS PGothic 的不规则字宽画的**
  （作者对着 PGothic 摆笔画）；换成严格网格后整体列位被推离作者摆的位置，用户看到的就是「错位」
- 故默认栈序 = **last**（`AA_AA_FONT_STACK` 缺省 last）：内嵌字体排在 PGothic/MS PGothic/Mona/MS Gothic **之后**、
  SimSun/monospace **之前** → PC 观感逐字符不变（实测默认版与 `--font none` 版宽度直方图逐项相同），
  手机/非 Windows 设备才落到它。要严格 1:2 网格才 `--font-stack first` / `AA_AA_FONT_STACK=first`（改观感，需先看）
- 开关：`AA_AA_FONT=0` 关；`AA_AA_FONT_FILE=<ttf>` 换源；CLI `--font none|<路径>`、`--font-stack first|last`
- 编辑服务静态 MIME 需含 `.woff2 → font/woff2`（外置形态经 127.0.0.1:8770 访问）
- 验证法（改字体相关必做）：浏览器打开交付页 → `getComputedStyle(pre.aa).fontFamily` +
  `document.fonts.check('15px AA-SarasaMono')`，再拿 canvas `measureText` 批量量语料字符，
  要求每个字宽 ∈ {半宽, 全宽} 且 全角/半角 == 2.00（单看代码不算数）

## 下载与安装
- 源:GitHub `be5invis/Sarasa-Gothic` releases(latest v1.0.40)
- asset:`SarasaMonoSC-TTF-<ver>.7z`(62.7MB;SC=简体中文;SuperTTC 包 135MB 不必下)
- 解压:MSYS bsdtar 不支持 7z → `uv pip install --python <repo>/.venv/Scripts/python.exe py7zr` 后用 py7zr 解压
- 只保留 `SarasaMonoSC-Regular.ttf` 到 `<repo>\fonts\`,删其余变体省空间
- 代理下载:`curl -x http://127.0.0.1:10808 -A "Mozilla/5.0..." -L -o`
