# AA 文本压缩包（读 .txt.gz，2026-09-05 已实施）

把 AA 漫画文本（不转图片）压缩存为 `.txt.gz`，工作台阅读界面透明读它，省磁盘。

## 实测空间（venv python 测）
- `aa/` 原文 txt：**17,193 文件 / 3.86 GB**（大头，主战场）
- `汉化/` txt：**2,057 文件 / 537 MB**
- gzip 压缩率 ~78%（省约 3.4GB）

## 核心设计：规范名 vs 存储名
- **磁盘存 `.txt.gz`**（存储形态，省空间）
- **阅读器/目录/URL 一律报规范名 `.txt`**（如 `entry_1.txt`），读取时透明解析到 `.gz`
- 前端**零感知**——不要给 toc/cur 带 `.gz`，否则 URL/切换/保存全乱

## 权威 helper（scripts/aa_translate.py，经 aa_common 转发，管线/阅读器共用）
统一 import 勿复制（项目铁律）。helper 经 `aa_common` 的 `globals().update(vars(FLAT_MOD))` 自动转发。
- `txt_read(path)`：.txt 或 .txt.gz 透明读，剥 `\r`。**传 `.txt` 基路径时若磁盘是 `.gz` 也要尝试 `+'.gz'`**（否则只有 `.gz` 时读不到）。
- `txt_write(path, lines)`：path 以 `.gz` 结尾则 gzip 写，否则普通写。
- `txt_key(fname)`：剥 `.txt`/`.txt.gz` 得基名（`entry_1.txt.gz`→`entry_1`）。
- `txt_existing(path)`：给定 `.txt` 基路径返回实际存在者（path 或 path+'.gz'）；皆无返回原 path。
- `txt_path(base_dir, fname)`（logic.py）：base_dir/fname 解析到实际 .gz/.txt。

## 接入点
- **logic.py**：`read_lines`/`write_lines` 委托权威 helper；`merged_chapters`/`chapter_order`/`_chapter_full` 报规范名、`src_path`/`tr_path` 解析实际 `.gz`；`_txt_head(path,n)` 读 .gz 前 n 行（标题/原帖地址）；`_is_txt(f)` 判 .txt/.txt.gz 排除 _汉化/_台词。
- **main.py**：约 19 处 fname 后缀检查/路径构造/base 推导 `.gz` 化（api_line/api_line_html/api_chapter/api_save_line/check_chapter/reader_page/_chapter_full）。核心 = 后缀检查 `endswith("_汉化.txt")` → `or endswith("_汉化.txt.gz")`；path 构造用 `logic.txt_path`；base 推导用 `logic.txt_key`。

## 压缩工具
`scripts/tools/compress_text_pkg.py --work <id> [--keep] [--verify]`：aa+汉化 txt→.gz；默认删 `.txt` 省空间，`--keep` 保留，`--verify` 解压校验（防压坏）。
- 新增脚本会触发 `test_interface_docs` 的 `test_inventory_fresh`/`test_docs_up_to_date` 两个失败（inventory/接口文档未同步）——**预期内**，重跑 `gen_inventory.py` + `gen_interface_docs.py` 同步。

## 已验证（267 测试全绿）
- fukou 双存场景 + temp 副本"仅 .gz"场景端到端：merged_chapters 报规范名、读 2346 行、save_line 写回 `.gz` 保持 `.gz` 形态。
- 全库无 `.gz` 残留（compress 测试用 `--keep` 后手动清理）。

## ⚠️ 边界
管线侧（translate/pass2/pass3/fix/scan/gen_html/extract_dialogue）仍 `open(join(aa_dir, base+'.txt'))` 直接读——**未接 `.gz` 前勿对有翻译预期的作品删 `.txt`**。aa/（只读侧）分批接，汉化/（写侧）最后做、单独回归。

## 坑
- `txt_existing` 皆无时返回原路径，判断存在必须 `os.path.exists`（否则 `.endswith((".txt",".txt.gz"))` 恒真误判）。
- CRLF 文件用脚本改 + py_compile + 孤立 CR 校验（见主 SKILL.md「坑」）。
