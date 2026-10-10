# override 试点验收：四类正反例 + 逐像素自检（2026-08-13 M14 D 卡实战）

## 试点选章
挑设计文档记载过 Bug 样本的章节（如 entry_9557 L35 超宽 mixed），天然覆盖负例场景。

## 四类正反例（必测，少一类算漏）
| 类 | 操作 | 期望 |
|---|---|---|
| 正例1 | meta=art 且实为台词的行标 text | 字号加大（fs+bonus > 基线） |
| 正例2 | meta=text 且实为装饰/艺术的行标 art | 字号 == 基线（16） |
| 负例1 | 超宽行 disp_width>100 标 text | 被 OVERRIDE_WIDTH_GUARD 拦截不加大 |
| 负例2 | 删除条目（还原为自动）重渲染 | 与删除前像素级一致（或与自动分类基线一致） |

## 逐像素自检坑（踩过）
- 渲染器背景 `BG=(255,253,247)` **不是纯白**（同交付 HTML pre 背景，`aa_to_images.py` 顶部常量）。扫描「内容右缘 ≤ 图宽-16」时判定非内容像素必须按 `!= (255,253,247)`，写 `!= (255,255,255)` 会把背景全当内容 → 全部越界误报。
- 自检脚本放渲染输出目录旁（如 temp/m14_试点/selfcheck.py），修完即跑。

## 自检项（全过才算 done）
1. `big_line` 逻辑断言：四类行各抽一条直接调 `big_line(t, ln0, meta, overrides)` 核对返回值。
2. 渲染无报错：`.venv/Scripts/python.exe scripts/tools/aa_to_images.py --work <id> --chapter <base> --out temp/... --text-bonus 4`。
3. CBZ 打包 + 全量解码：`aa_to_cbz.py --dir <渲染目录> --out temp/<x>.cbz`。
4. 环境障碍：.venv 可能缺 PIL，`uv pip install --python .venv/Scripts/python.exe pillow` 解决（系统 python 装了但 .venv 没装，别让 worker 来回查）。

## 交付纪律
- 试点 override 标注 + CBZ 是给用户手机实测的材料，**用户实测通过前不 commit**；实测通过后由主会话统一 commit（连同正式交付HTML图片版）。
- 渲染命令一律 `--out temp/`，禁写 aaManga/交付HTML/（批次重导才写）。
