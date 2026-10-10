# 矫正工作台消失事故 — 恢复手册（2026-08-10）

## 事故机制

- 并行会话把重构（含矫正工作台）提交到 `feat/2nt-pipeline`（commit `379968c`），随后工作区 `git checkout main`。
- **`scripts/common|sites|works|tools|tests` 只在 feat 分支跟踪，main 不跟踪** —— git 切换分支时把这些目录从工作区整体删除（症状：`scripts/tools/aa_web_app/` 只剩 `__pycache__`，服务 curl 000）。
- main 分支自己的 M5 测试（`test_line_meta.py`/`test_manual_fix.py`）反而在切换后才出现在磁盘上。

## 排查路径（按序）

1. **症状确认**：`ls scripts/tools/aa_web_app/` 只剩 `__pycache__`；服务无响应（curl 000）。
2. **排除移动**：`find <repo-parent> -maxdepth 3 -name "aa_web_app"` 全盘无副本；回收站无（git-bash rm 不走回收站）。
3. **版本取证（pyc 反编译探查）**：`python -c` 读 `main.cpython-311.pyc` 头部字节 8-12 得内嵌源 mtime（`struct.unpack('<I', data[8:12])`），再 `marshal.loads(data[16:])` 递归收集 `co_names` 检查关键特性（`work_glossary`/`count_unsynced` 存在）——确认丢失的是最新版。
4. **定位 git 备份**：`session_search`（session 库 FTS5）查并行会话记录 → 发现 commit `379968c refactor: ... 新增矫正工作台`；`git cat-file -e 379968c:scripts/tools/aa_web_app/main.py` 确认文件在 git 里。
5. **确认 HEAD 不跟踪**：`git ls-tree HEAD --name-only scripts/tools/` 为空 → git status 不显示删除（所以普通 `git status` 检查看不出问题）。

## 恢复命令

```bash
git checkout 379968c -- scripts/common scripts/sites scripts/works scripts/tools scripts/tests scripts/legacy
```

- 只恢复 379968c 里存在的文件，不会碰 main 更新的文件（test_line_meta/test_manual_fix 等）。
- 恢复后验证：18 个关键文件（aa_web.py、aa_web_app/{main,logic,__init__}.py、templates×6、static×4、common/aa_common.py、common/gen_aa_html.py、sites/bookshelf.py、tests/test_aaweb.py）存在 → 重启 uvicorn → 6 端点 200 → 测试子集。
- **恢复后立即提交到 main 保护**，否则再切分支再丢。

## 测试污染（混合工作区状态）

- main 的 `test_manual_fix.py`/`test_line_meta.py`：`sys.path.insert(0, scripts/)` 后 `import aa_translate` → 加载**旧平铺版** `scripts/aa_translate.py`（无 CHAPTER_WORKERS）→ sys.modules['aa_translate'] 被污染。
- 全量 `unittest discover` 时 test_pipeline 的 `test_parallel_speedup`/`test_fingerprint_discard` 因此失败；**单独跑 test_pipeline 即 6/6 OK**——非 app 问题。
- 验证用子集：`python -m unittest scripts.tests.test_aaweb scripts.tests.test_aa_common scripts.tests.test_gen_html scripts.tests.test_extract`（61 项 OK）。

## 通用教训

- **session 数据库是文件恢复源**：任何 write_file 的完整内容都在会话 DB 里，`session_search` 可检索回放（FTS5 搜文件名/独特字符串）。
- **pyc 是源码版本的旁证**：内嵌源 mtime + co_names 特征可判断丢失版本新旧。
- **分支切换删除只删"对方分支不跟踪的文件"**：多分支并行工作区，关键目录要么提交到所有活跃分支，要么切分支前 `git stash`/备份。
- 验证脚本自身断言错误 ≠ 应用 bug：先核对脚本预期（本事故中 unittest 汇总在 stderr、urllib CJK URL 需 quote、PNG 响应不可 utf-8 decode）。
