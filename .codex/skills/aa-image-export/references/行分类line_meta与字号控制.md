# 行分类 line_meta 与字号控制(2026-08-10)

## 背景:为什么需要 extract 阶段分类
行级加大(文本行 +N px)要求精确区分 art/text/mixed。渲染时对纯文本做字符统计**必然误判**:
- AA 艺术行与台词行字符构成重叠(AA 人物右肩的台词行含假名,如 `／´…ヽ ツモりますので！！`,字符统计会当文本 → 加大 → AA 错位,用户实测确认)
- 用户定性:"不使用大模型只按行读太容易误判";主导设计为**在最初下载/extract 时就判定**(信息最全时)

## 分类器与数据
- `scripts/tools/line_classify.py`(v1.5):从 raw HTML 的 `<dt>/<dd>` 结构取帖头(100% 准确,不做正则猜)——**用户行(帖头)绝不加大**;内容行字符段分析
- v2(作者先验,`temp/layered/tools/line_classify_v2.py`,M3 达标):输出 `{行号: {kind, confidence, reason}}`,写入 `work_dir/line_meta_v2/<base>.json`(1-based 物理行号从 3 起,前两行为 # 标题/# 地址)
- **作者先验(用户提出,数据验证有效)**:楼主(◆ 开头,第一帖作者)86% 内容是 AA;其他用户(名無し)88% 纯文本。阈值加权:楼主帖 text 收紧(≥7 字),其他用户帖放宽(≥2 字、半角片假名算文本)——修复 41 处漏标,零退化
- 分类规则细节:
  - 字符分类:`ART_SYMS`(线条/框线/几何符号 + `＝≠≡≒∞〓´¨¯°`)+ ASCII 标点 + 半角片假名(U+FF61-FF9F)= 艺术;全角假名按文本段(ノ在"シノハユ"是词、在 AA 表情是画,单字符无法区分,靠段长);ART_KANJI 是艺术汉字("实力"的"力"在台词里是文本 → 用占比不用含集)
  - 段分析:空格不断段;text 需 ≥5 非空格字符(短台词"奈良!"漏标教训);art 段 ≥2 且 text 段 ≥3 → mixed
  - `_has_dialogue` 门禁(需真实日文对话字符,防装饰误标)

## 翻译管线接入(M4a,并行会话完成)
- `aa_translate.py`: `load_meta_0based`(int(k)-1 转 0-based)+ `is_translatable`——art 硬不进模型(防御:含连续 3+ 非艺术假名 → 回退 is_text_line 防漏译)、text 必进、mixed/header/blank 走旧逻辑
- `AA_USE_LINE_META` flag 默认开(设 0 关闭回滚);影子验证 token -40%、宽度错位 0
- 行号口径:meta 1-based 物理行号 ↔ 生产 enumerate 0-based,固定 +1 偏移

## 渲染消费
- `aa_to_images.py` `load_line_meta`:优先 `work_dir/line_meta_v2/`(dict 格式提取 `v['kind']`),兼容 v1 平铺(`{行号: 'kind'}`);均无 → None(fallback `is_big_text_line` 规则)
- `big_line(t, ln, meta, overrides=None)`(M14 更新):优先级 **override > meta > 规则**;meta/override 键是 **1-based**(文件第 N 行 → 'N'),rows 的 ln 是 0-based → 查 `str(ln+1)`。**off-by-one 教训(2026-08-13 修复)**:原代码 `meta[str(ln)]` 错位一行(一直用上一行分类决定本行);colormap `cm` 键 0-based 与 ln 一致,绝不动
- override 文件:`work_dir/line_meta_overrides/<base>.json`,稀疏 `{1-based行号字符串: kind}`,kind ∈ text/art/mixed/header/blank;只喂图片导出,翻译 flag 不动;「还原为自动」= 删键
- 宽度防线:`OVERRIDE_WIDTH_GUARD=100`(可配,CLI `--text-max-width`)——meta/override 判 text 但 `disp_width>100` → 不加大(2026-08-13 新增,原 meta==text 无条件加大无防线)
- `row_fs`:text 行 `fs + bonus`(TEXT_MAX_FS=48 上限,M14 从 64 下调);红行(colormap `is_red_style`)用 `--red-bonus`,其余 `--text-bonus`
- `block_layout` 宽度预估必须含加大后行宽(红行/文本行分别算),否则画布被裁

## 验证要点
- 行号对齐:meta 键 3..N == txt 行数;空行全 blank;非空行全有分类
- 渲染断言:0001 图宽 == 原版(AA 零误判的硬指标——规则版误判时宽会从 818 撑到 1003)、无裁边、文本行加大(图高增加)
- **宽断言修正(2026-08-10 实测)**:\"图宽==原版\"只适用于纯 AA 块——**含 text 行的块宽变大是正常**(文本行加大后成为最宽行,block_layout 预估含 bonus)。0011/0016/0031(纯文本或含 text 行块)宽 752→882/824/759 不是误判。正确断言:宽变化的块必须含 text 行,纯 AA 块(无 text 行)宽变化才算误判
- 分类质量:text 行全量抽查须全为台词/规则(漏标案例:`奈良!`/`ﾅｧｰﾙｧｰ!!`/带`（）`的台词)

## 单章宽度错位修复(2026-08-10,重译后 9 行错位处理)
1. **art 行错位 → 直接恢复原文行**(`tr[i] = aa[i]`)——艺术行本就不该被模型改动,恢复零风险
2. **text/mixed 行错位 → 先 `rstrip('　')` 再 `pad_to_width(base, target)`**——`pad_to_width` **只补不裁**:译文行尾 U+3000 过多时 `disp_width(line) >= target` 直接返回原行,必须剥掉行尾 padding 才能补/修。译文内容不足则补 U+3000
3. **内容真超宽**(译文比原文宽,如 `ﾕｳﾁｬｰと同期？`(半角片假名窄)→ `和ユウチャー同期？` 超 5 格)→ 保留超宽(渲染 rightmost 裁剪自动加宽不裁边),或**采用旧交付版对应行**(旧版已验证宽度匹配,如意译\"悠茶\"/保留原文)——保持交付一致性优先
4. 修后复查:全量 disp_width 对比 0 错位才算完成
