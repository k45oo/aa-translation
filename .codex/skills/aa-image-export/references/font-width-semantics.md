# AA 图片渲染字体宽度语义实测数据(2026-08-09)

## 背景
AA(2ch 字符画)的对齐语义绑定"原帖字体"。2ch 默认字体 = MS PGothic(比例字体),交付 HTML 字体栈 `"ＭＳ Ｐゴシック","MS PGothic","Mona","MS Gothic","SimSun",monospace` 在 Windows 浏览器实际命中 MS PGothic。**用户习惯的"正常"= PGothic 比例渲染**,图片渲染必须复刻,不能想当然用等宽字体。

## 浏览器实测(canvas measureText,15px,Edge/Windows)
| 字符 | 宽度 | 格数 |
|---|---|---|
| \| | 4.0px | 0.53 |
| ｜ | 15.0 | 2.00 |
| / | 8.0 | 1.07 |
| あ | 14.0 | 1.87 |
| 漢 | 15.0 | 2.00 |
| A | 9.0 | 1.20 |
| 1 | 8.0 | 1.07 |
| ﾉ | 7.0 | 0.93 |
| ′ ¨ × ° … | 8.0~15.0 | 1.07~2.00 |
| — | 10.0 | 1.33 |
| 　(U+3000) | 10.0 | 1.33 |
| 简体中文(回退 SimSun) | 15.0 | 2.00 |

## fontTools hmtx 实测(msgothic.ttc, UPM=256)
- **MS Gothic(index 0)= 完美等宽**:全部字符恰 1 格(128)或 2 格(256);缺简体字形(JIS)。
- **MS PGothic(index 2)= 比例**:`|`=60(0.47格)、`あ`=241(1.88格)、`A`=162、`　`=170(1.33格)、`—`=171。
- **SimSun**:简体全覆盖,但**半角片假名 U+FF61-FF9F = 全宽 256**(disp_width 按 1 格算 → 渲染错位)。
- **Sarasa Mono SC**:全角 1000/半角 500 等宽,0 缺字、0 宽度不符 —— 但 ′ 等字符半宽,与原帖 PGothic 语义不同 → 用户仍觉"错位"。

## 结论
- 布局宽度 = 逐字符"PGothic cmap 有则 PGothic advance,否则 SimSun advance"(= 浏览器行为)。
- 字体选择:主 `msgothic.ttc index=2`(PGothic)+ 回退 `simsun.ttc`;cmap 判据 `fonts/msgothic_cmap.json`。
- 渲染验证:同一测试文本,PIL getlength 与浏览器 canvas measureText 逐字符对比(30px vs 15px×2,容差 2.5px),累计漂移 ≤3px。
