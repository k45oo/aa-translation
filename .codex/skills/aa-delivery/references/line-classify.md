# 行分类 line_classify.py — 规则推理与踩坑记录(2026-08-09)

## 为什么需要(背景)
text-bonus 行级加大最初用 `is_big_text_line`(is_text_line + 行宽≤50格 + 艺术字符占比≤40%)做"事后猜测"。
用户反馈 AA 错位 → 根因:艺术行与台词行字符构成重叠,事后按行读必误判。
用户提出"最初下载时判断"→ 落地为 extract 阶段分类(信息最全:HTML 结构 + 行上下文)。

## 帖头(用户行)
- 各站点解析器(`sites/*.py`)统一 `<dt>`(帖头)/`<dd>`(内容)结构,`parse_posts` 返回
  `[(帖头行, [(内容行, color)...])...]`——帖头行 100% 准确,无需正则猜。
- 用户行与回复同 `<dt>` 的站点(未遇):整行标 header(不加大),待用户确认后处理。

## 内容行分类规则(字符段分析,空格不断段)
```
is_art_char(c):
  半角片假名 U+FF61-FF9F      → art   (AA 艺术主力:ﾉﾐﾊ 头发/身体)
  ASCII 标点 !@#$%^&*()-_=+…  → art   (AA 笔画:- . / = _ ~)
  全角假名(ぁ-んァ-ヿー)       → text  (ノ 在シノハユ是词;段级规则兜住纯假名画)
  ART_SYMS ∪ ART_KANJI       → art   (ART_SYMS 含:│／＼框线几何箭头 ＋ ´¨¯° ＝≠≡≒∞〓)
  其余(汉字/标点/字母数字)    → text

classify_content(row):
  空行 → blank
  text_total==0            → art
  text_total<5             → art   (短行"ノ ＼"表情不加大;短台词可接受不加大)
  art_total==0             → text
  art_max≥2 且 text_max≥3  → mixed (AA+台词同行,不加大)
  否则按占比归 text/art(单字符点缀)
```

## 调试中确认的关键案例
| 行 | 判定 | 为什么 |
|---|---|---|
| `・シノハユ成分はおそらくありません` | text | ノハ 是角色名一部分(全角假名=text) |
| `／´　…　ツモりますので！！` | mixed | 艺术段(／´)≥2 + 文本段≥3,同行混排不加大 |
| `　ノ　　＼`(表情行) | art | text_total=2 <5 |
| `＝ﾆ二二ﾆ…`(横幅线) | art | ＝ 在 ART_SYMS,二 在 ART_KANJI |
| `但想走靠实力夺回…` | text | 力 在 ART_KANJI 但占比低(比例/段级放行) |
| `-saki-`(规则文本内) | text | ASCII 字母段 ≥3 为主体,单个 - 是点缀 |

## 行号对齐(踩过的大坑)
- build_txt 行序:第1行 `# 标题`、第2行 `# 原帖地址`、第3行起逐帖(帖头 + 内容行)。
- meta 键 = 行号,从 3 开始;结束时校验 `n-1 == len(txt_lines)`(n 是"下一个行号",天然多 1)。
- 曾犯:键从 2 开始 + 校验用 `n == len` → 整体偏移 1 行 + 误报不齐,排查半天。
- 分类基于 raw 原文重建的 txt(校验行数与现有 txt 一致);渲染用**汉化 txt**——两者内容不同(原文 vs 译文)但行数一致即可按行号对齐。

## 渲染器消费
- `aa_to_images.py`:`load_line_meta(work_dir, base)` → `big_line(t, ln, meta, overrides=None)`(M14 更新):
  - 优先级 **override > meta > 规则**;meta/override 键是 **1-based**(文件第 N 行 → 'N'),rows 的 ln 是 0-based → 查 `str(ln+1)`(2026-08-13 修复 off-by-one:原 `meta[str(ln)]` 错位一行);colormap `cm` 键 0-based 与 ln 一致,绝不动
  - override 文件 = `work_dir/line_meta_overrides/<base>.json`,稀疏 `{1-based行号: kind}`,只喂图片导出、翻译 flag 不动;「还原为自动」=删键
  - `header/art/mixed/blank` 一律不加大,`text` 才加大(且 `disp_width≤OVERRIDE_WIDTH_GUARD=100`,超宽防线拦截);meta 缺失回退 `is_big_text_line`。
- 验收铁证:含文本页**图宽 == 无 text-bonus 原版**(AA 行零误判);文本行加大只增行高。

## 批量流程(73 话)
1. `python scripts/tools/line_classify.py --work saki_fc2`(全部,秒级)
2. `python scripts/tools/aa_to_images.py --work saki_fc2 --fixed-width --text-bonus 12 --out <临时>`
3. `aa_to_cbz.py --dir <临时> --tr-dir <汉化目录> --per-chapter --prefix saki_fc2_ --out <cbz目录>`
4. ftplib 批量上传 `/TachiyomiJ2K/local/さーきー/`(见 mt-ftp-transfer),逐个 ftp.size 验证
