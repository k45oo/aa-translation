---
name: aa-image-export
description: Use when 把AA汉化漫画导出为竖屏图片/CBZ阅读包. 渲染语义须复刻浏览器.
---

# AA 漫画图片版导出(竖屏长图 + CBZ)

## 触发
用户要把 AA 汉化作品做成手机看图/漫画阅读器可读的形式(竖屏长图 PNG、CBZ 漫画包)。

## 脚本
- `scripts/tools/aa_to_images.py --work <id> [--chapter 片段] [--width 600] [--fixed-width] [--font 覆盖] [--text-bonus N]` — 汉化 txt+colormap → 竖屏长图(aaManga/交付HTML/<work>_图片版/<章>/NNNN.png);--text-bonus 用户楼文本行加大(默认2,SimHei 统一;先跑 line_classify.py 生成 line_meta);纯文本块自动 580 标杆+折行(无需参数)
- `scripts/tools/aa_to_cbz.py --work <id> | --dir <图片目录> [--per-chapter] [--out]` — 章目录 → CBZ(章节子目录结构)

## 渲染语义(核心,踩坑血泪)
- **字体必须复刻交付 HTML 的浏览器行为**:主字体 MS PGothic(msgothic.ttc index=2),缺字形字符回退 SimSun。逐字符:ch 在 `fonts/msgothic_cmap.json`(PGothic cmap 码位集,fontTools 预生成)→ PGothic 字形+实测 advance;否则 SimSun。
- **MS PGothic 是比例字体**(| ≈ 0.53 格、あ ≈ 1.87 格、U+3000 ≈ 1.33 格)——绝不要按等宽假设!等宽字体(MS Gothic/Sarasa)渲染的 AA 与用户习惯的 HTML(PGothic 比例)不一致,用户会判定"错位"。
- **宽度一律用字体实测 advance(getlength)逐字符累加**,不要用 aa_common.disp_width:′ ″ Ⅳ ‘ ’ 等全角码位在等宽字体实为半宽、SimSun 半角片假名全宽,disp_width 会累积错位。
- 粗体用**位移双印**(x+1 先画再画 x);stroke_width 描边在小字号有荧光/模糊感。
- 像素烘焙无字体依赖:手机缺 MS Gothic 也不会破版(HTML 在手机上反而会因字体回退错位)。

## 切块与布局
- 块边界 = 帖头行(一帖一块图);空行原样保留渲染(占行高),楼(帖头)间不足 2 空行自动补足。
- 字号:100px 字号预测量行宽(advance 与字号近似线性)→ fs = clamp(100*width/w100, 16, 40);图宽 = 实测内容宽+padding(无右侧空白,手机上窄图自动放大)。
- 图宽两模式:默认自适应(窄块收窄,手机全宽显示自动放大);`--fixed-width` 固定=width(窄块右侧留白、内容不放大——用户曾把 saki_fc2 001 话换回此"放大字体前"旧版样式,批量前先问)。
- **fixed-width 裁边教训(用户反馈"右边被吞"后修复)**:固定画布 752 会被超宽横幅行(858px,fs 被 MIN_FS=16 卡住)冲破 → 修复为 `img_w = max(width+PAD*2, content_w)`(窄块统一、横幅按内容不裁)+ `render_block` 渲染到冗余画布后**按实际内容右缘像素裁剪**(防 100px 预估与 fs 字号实际渲染的 hinting 偏差);验证 = 逐像素"内容右缘 ≤ 图宽-16"。
- 基准宽默认 600(2026-08-14 从 720 下调:用户反馈"分辨率太高不需要";手机 2x 够看清,文件 ~51KB/页);超长帖(300+ 行)硬拆。

## 楼级加大/冻结(2026-08-14 M15 用户方案)
- 用户方案: **楼主楼(帖头作者含 ◆,fc2 系)整楼冻结不动**——字号/字体与交付版像素级一致(验证=base vs --text-bonus 渲染 0 差异);用户楼内容文本行加大 + **SimHei 统一**(缺字回退 PGothic,实测仅 ´・､ｧﾟ 5 缺字不乱码)
- 实现: `post_is_op(block)` 判 ◆;楼主标记在 **split_blocks 阶段锁定**为 `(is_op, rows)` 元组贯穿 split_long/merge_posts——防超长帖拆段丢帖头误判、楼主块独立不混入用户合并块(freeze 不误伤用户楼)
- 用户楼加大行判定: `line_meta=='text'`(无 meta 回退 is_big_text_line 规则);加大行 SimHei 统一
- ⚠️ **line_classify 短台词下限 5→2(2026-08-14 用户反馈 6 楼未加大)**: `classify_content` 原 `text_total<5→art` 把 `奈良！`(4字)/`太好了`(3字)/`やったぜ` 等**短台词全判 art 不加大**——用户明确"用户楼加大优先级最高"。已改 `<2`(单字装饰仍 art)。改后须**重新生成 line_meta**(`line_classify.py --work <id>` 全量)
- ⚠️ **line_meta 基于原文 raw 分类,汉化后文本可能已变**: 漏译残留行 `ﾅｧｰﾙｧｰ!!` 原文是半角片假名 → meta 判 art;人工补译成 `奈良—！！` 后 meta 仍 art → 不加大。修复: **big_line 在 meta 判 art/mixed 时用汉化后文本规则兜底**(`is_big_text_line(t)` 交叉验证,真台词仍加大);`block_is_pure_text` 同步(art 行汉化后是台词 → 归纯文本块)
- ⚠️ **用户报"52/59/62"是楼层号不是图片块编号(2026-08-14 排查陷阱)**: 用户报未加大的楼是**帖头里的楼层号**(如 `52 ： 名無し`)——排查时必须按帖头行号定位该楼所在渲染块,不能直接按块索引 merged[idx-1] 查(块编号≠楼层号;曾把 202/242 楼的 ◆ 楼主楼误当"62/73 用户楼"差点误判)
- ⚠️ **line_meta 键是 1-based 完整行号**(文件第N行→'N')→ big_line 查 `meta.get(str(ln+1))`(rows 的 ln 是 0-based enumerate 完整行号);交付版曾用 str(ln) 错位 1 行,M14 实验版修过、回退时又丢
- ⚠️ **渲染必须用 `.venv/Scripts/python.exe`**:SimHei cmap 靠 fontTools 读;全局 python 无 fontTools → get_simhei_cmap() 静默回退 {} → SimHei 统一**静默失效**(全部走 PGothic 无任何报错),对比两版像素差 3% 才暴露——用 venv 后 SimHei 才真正生效
- 验证要点: 楼主块 base vs bonus 像素 0 差异;用户块 ink 条带高度双档(fs / fs+text_bonus,如 +2 时 17/19)
- **楼主楼文字加大 → 嵌字方案(两轮已实现,2026-08-14)**: 见 references/embedding-plan.md —— 9 项决策(渲染期坐标记录/A1 嵌字重绘/SimHei 统一/矩形+2px/行高+2/预算同步放大/只对楼主楼)+ `v2_text_spans` 识别(3 轮收敛: v2 segments conf≥0.5 且段含中文 主源 + 纯文本整行 + reason 含 `lt` 且汉化含语义中文兜底 + mixed 中文定位兜底2;`no_dialogue` 纹理行永不嵌)+ art 占比>25% 只嵌台词子串;⚠️ 特殊色锚点会被 PNG-8 量化吃掉,嵌字必须在 quantize 前做;⚠️ **嵌字执行必须逐字符 pick()**(SimHei 缺 U+30FB・→ 整段 SimHei 画出口字)+逐字符 x 定位(空格跳过防右偏);字号已定 = 图宽≥800 时 +1(用户实测 0001 宽834 字小,0002/0009 合适——\"按宽度判断字体大小\")

## ink 边界统一裁剪(2026-08-14,替代 rightmost 裁剪)
- 老法缺陷: 按 rightmost(advance 累加)+PAD+8 裁剪——**行尾 U+3000 pad 空格(透明)计入宽度** → 右空白 23-159px 漂移;块首帖头补 2 空行+块尾空行 → 上下空白 58-97px
- 新法: `ImageChops.difference(img, 纯BG图).getbbox()` 得 ink 边界 → 四边各留 PAD 裁剪 → **上下左右全部恒定 16px**;块首/尾空行与行尾 pad 空格自动裁掉;fixed 模式保底 min_width(不裁横幅)
- ⚠️ **fixed 模式左缘必须强制 0**(`l, r = 0, max(r, min_width)`):SimHei 字形 1px hinting 使 ink 从 x=17 起 → 左裁剪 l=max(0,17-16)=1 → 图宽 571≠572 漂移,固定后恒 612/572
- ⚠️ **AA 块(自适应)右缘额外 +AA_RIGHT_PAD**:`else: r = min(W, bbox[2]+PAD+AA_RIGHT_PAD)` —— 只有自适应分支加,纯文本 fixed 分支不加(用户要求 AA 块右侧更宽)
- 验证: 每图四边空白==PAD、无 ink 触边(<8px 距边)、双档行高

## 纯文本块宽度标杆与智能折行(2026-08-14 用户方案,第三次迭代定稿)
- 背景: 用户反馈纯文本图(无 AA)宽度不统一 → 方案 = **用户行宽度标杆 `TEXT_W=580` 内容区**(fixed 语义,图宽恒 612=580+PAD*2),text 行超 **WRAP_W=500px 自动折行**;text-bonus 最终定 **2**(迭代: 8→4→2,用户逐次下调)
- **AA 块右侧额外留白 `AA_RIGHT_PAD=16`**(2026-08-14 末次迭代):自适应(非 fixed)块右缘 = ink+16+16=32px,纯文本块不受影响;实测 90 个 AA 块右留白全部 32px
- 纯文本块判定 `block_is_pure_text`: 块内所有非空行(除帖头/空行)都是 line_meta 'text'(无 meta 回退规则)→ 走 580 标杆;含 AA/mixed 的块保持内容宽自适应
- **merge_posts 三态分类合并**(2026-08-14): 楼主楼独立 / **用户纯文本帖相互合并成幕** / **用户含AA帖独立** —— 纯文本帖不能与 AA 帖混块(否则块级纯文本判定失败、折行错乱;实测 79块→154块,612 宽占 42%)
- **智能折行 `wrap_line`**: 断点优先级 空格 > 句逗顿叹问(`，。、！？…．,.;;!?`) > 闭括号(`）」』】〉》〕》`)——断在其**后**;**开括号后不断**(避免"（SS井感"式截断);折余纯空格(U+3000 pad)丢弃不占行
- 折行宽度测量用 **SimHei 语义 `line_width100_sh`**(SimHei cmap 优先,缺字回退 PGothic,与渲染 pick 一致)——不能用 PGothic/SimSun 口径,否则折行阈值错
- `block_layout(fs_fixed=fs0)` 固定字号二次预估: 先算 fs0 → 折行展开 → 固定 fs0 重算 img_w——**防折行后字号被二次放大溢出**
- 实测: 超 600px 的 text 行仅 3.6%(910/25460),折行只影响少数长行;折后段可略超 500 触发阈值(断点在语义位置)但必须 <580 内容区不裁边
- **调参工作流(用户偏好)**: 每次调参(标杆宽/折行阈值/text-bonus)后**单章渲染 → 打包 CBZ → FTP 传手机 → 用户手机确认 → 确认后才全量 73 章**;用户会给出明确数字定值(如"纯文本 580 统一宽,文字宽度大于500换行,字号+2,aa块右侧再加宽16"),照单改勿自行发挥

## PNG-8 固定调色板输出(2026-08-14 末轮,用户"AA不需要这么高画质")
- 触发: 用户反馈图片文件太大/画质过剩 → render_block 裁剪后 `img.quantize(palette=固定PALETTE8, dither=Image.Dither.NONE)` 转 P 模式 PNG
- **⚠️ 必须固定调色板,禁用自适应 `quantize(colors=8)`**: 自适应调色板逐图漂移主色——实测 77/90 张红缺失、44/90 张 ID 蓝偏移、21/90 张帖头绿偏移(违反"帖头作者绿粗/ID红粗"交付约定);固定调色板则关键色 0 缺失
- `PALETTE8 = [背景(255,253,247), 正文(34,34,34), 帖头绿(0,136,0), ID蓝(0,0,255), 红(255,0,0), 深红(128,0,0), 灰1(162,161,157), 灰2(67,66,65)]`;用法 `_pal_img.putpalette([v for c in PALETTE8 for v in c])` + `quantize(palette=_pal_img, dither=NONE)`
- 效果: 全章 3375KB→987KB(-71%,CBZ 3.3MB→1.0MB);90 张关键色全保留;尺寸/布局与 RGB 版完全一致;代价仅抗锯齿过渡色近似(边缘略硬,内容无感)
- 验证: 遍历每张图 `set(img.getpalette())` 断言 5 关键色都在;体积节省 ≥50%;尺寸与旧版一致

## CBZ 打包
- 内部结构:`NNN_章节名/NNNN.ext` 子目录分章,页序定宽,zip STORED(PNG 已压缩无需再压)。
- **⚠️ `--work` 打包读 `aaManga/交付HTML/<work>_图片版`,`--dir` 打包读指定目录(2026-08-13 事故)**：重导时若渲染到 temp/ 临时目录,打包**必须用 `--dir temp/<渲染目录>`**,用 `--work` 会把旧图打包传手机(症状=本地看新图、手机老样子,用户实测踩坑)。重导流程: `aa_to_images.py --work X --out temp/m14_X` → `aa_to_cbz.py --dir temp/m14_X --per-chapter --out ...` → ftplib 上传。整部一包 `--dir` 模式输出 `NNN_章节名/` 子目录;per-chapter 文件名 `{prefix}{序号}_{话数}.cbz`。
- **图片格式 png/jpg/jpeg/webp/gif/bmp 全收**——只认 png 会让 jpg 章节整章缺失(用户视角 = 跳章/空目录);zip 内扩展名按源保留(jpg 内容标 .png 会让严格阅读器解码失败)。
- chapter_label:优先读汉化 txt 第一行(FC2 行尾全角数字→第N话、【完】保留、番外用原标题、`/`→`／` 清洗);无 txt 回退目录名 `第N話`。
- 章节排序:正则数字提取(00_預告→0 排首)。
- `--dir` 直接打包任意图片漫画目录(如 東方新異譚/comic_seamless),无需 registry 条目。

## 验证(每次导出必做)
- 渲染后:fontTools hmtx/cmap 断言(全语料字符 advance 与实测格数一致)、像素颜色抽查(帖头作者绿/ID 红)。
- CBZ:章节数==本地、无空目录、每章页数==本地、全量解码、扩展名分布。
- 浏览器语义对比:CDP canvas measureText vs PIL getlength,见 references/browser-semantics.md。

## 坑
- 交付 HTML 的 L3 字节快照测试在改 gen_aa_html 模板后预期挂,黄金样本待全量重生成后更新。
- Edge headless 模拟手机必须 `Emulation.setDeviceMetricsOverride{mobile:true}`(见 references)。
- 大作品批量渲染(73 话≈12 分钟):background + notify_on_complete。
- **验证渲染/回归必须 `--out temp/...`(2026-08-13 教训)**:无 --out 默认写 `aaManga/交付HTML/<work>_图片版/`(被 git 跟踪)→ 81 张验证 PNG 误入 commit(A 卡事故),靠 `git reset --soft HEAD~1 && git reset HEAD . && git add <自己的文件> && git commit` 剥离。
- **所有验证/渲染一律用 `.venv/Scripts/python.exe`**(非 `python`):全局 hermes venv 缺 fontTools(SimHei cmap 静默失效)也缺 numpy;项目 .venv 有 fontTools+PIL。ad-hoc 验证脚本(hermes-verify-*.py 放 Temp)也必须用 venv python 跑,否则误报。
- **⚠️ ad-hoc 验证脚本运行后不要立即删除(2026-08-14 循环教训)**:验证脚本跑完 ALL PASS 后若马上 `rm` 掉临时脚本+输出目录,系统 hook 会把删除视为"变更"→ 下轮又要求"fresh evidence"→ 反复重跑同一验证(浪费多轮)。改为: 脚本+输出目录**保留在 Temp 直到用户确认观感/提交时再统一清理**,并让脚本 print 出自己的路径与输出目录(见 aa-image-export 验证脚本模式);真正干净的做法是脚本 print 保留路径。视觉类工作(嵌字/调参)本身按 Coding 指引就是"等用户确认后再提交"。
- **批量交付全流程(2026-08-13 saki_fc2 73 章实测成功)**:渲染 `--out temp/<批次> --text-bonus 6` → `aa_to_cbz.py --work <id> --per-chapter --out temp/<批次>_cbz`(命名 `{work}_{NNN}_{第N话}.cbz` 与手机旧版一致可同名覆盖) → ftplib storbinary 批量传 `/TachiyomiJ2K/local/<漫画名>/` + **逐文件 ftp.size 字节数验证** → **MLSD 复核文件名**。
- **FTP 文件名核对用 MLSD 勿用 LIST split(2026-08-13)**:`saki_fc2_073_2／18　は　...` 含 U+3000 全角空格,`LIST` 行 `split()[-1]` 只取最后一段 → 误判"手机有旧残留"→ delete 报 450(该名不存在)。mlsd() 返回精确文件名;同目录同名 CBZ 直接覆盖无重复。

## 相关
- 管线本体 `aa-manga-translation`、矫正工作台 `aa-web-review-app`(均 user-owned,内容同步需 adopt)
- 传手机:`mt-ftp-transfer`(MT 远程 FTP;漫画 → TachiyomiJ2K/local/<漫画名>/ 每话一个 CBZ,普通文件 → /Download)
- 脚本文件可能被外部会话改动:patch 失败先 read_file 看当前内容再改
- **图片渲染现行权威(2026-09-07 合并)**: 本技能 = AA 汉化→竖屏图片/CBZ 的唯一权威,已吸收 `aa-image-rendering`(字体语义/line_meta 字号控制/被否决实验史)与 `aa-manga-image-export`(简版)。references/ 含三技能全部数据(字体实测/行分类/override 试点/嵌字演进)。
- 交付多端适配/响应式 HTML/CDP 手机实测 → `aa-delivery`; 图片漫画下载(acgn/manhuagui)→ `web-manga-download`
