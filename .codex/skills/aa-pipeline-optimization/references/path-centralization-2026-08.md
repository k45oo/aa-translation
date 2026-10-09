# 路径 BASE 中心化批量改造配方（2026-08-18 Phase 3 补完实战）

67 个生产脚本、188 行硬编码 `r"<repo>\..."` 字面量全量动态化为 `os.path.join(ROOT, ...)`。
提交 29989e2。本文件是完整配方，SKILL.md 有要点摘要。

## 审计口径（先做对，否则白干）

找硬编码路径**必须匹配前缀**，不能只搜独立字面量：

```python
# ❌ 只匹配独立字面量 → 14 个文件（严重漏报）
pattern = re.compile(r'r"<repo>"')

# ✅ 匹配前缀（含完整路径拼接 + 单引号形式）→ 71 个文件（真实数量）
pattern = re.compile(r'r"<repo>')
# 再补单引号: r'<repo> 前缀
```

漏掉的原因：`r"<repo>\白头与染灰魔女\aa"` 这类带路径后缀的字面量不匹配独立字面量正则。
教训：一次"0 残留"误报 → 审核复核打回 → 补完。统计时要排除 legacy/ tests/（基准保留）
和 paths.py 的文档注释。

## 替换规则

```
r"<repo>\A\B\C"   →  os.path.join(ROOT, "A", "B", "C")
r"<repo>\glossary.json"  →  os.path.join(ROOT, "glossary.json")
r'<repo>\...'     →  同上（单引号，sites/extract 的 RAW/OUT 默认参数）
```

注意 sites/extract 的典型形态：`RAW = sys.argv[1] if len(sys.argv) > 1 else r'F:\...'`
（三元表达式内嵌字面量）——逐行正则替换即可，os.path.join 在表达式里合法。

## ROOT 定义注入

- 层级公式：`n = len(rel.replace('/', os.sep).split(os.sep)) + 1`
  - scripts/xx.py（顶层）→ 2 级 dirname
  - scripts/dir/xx.py（子目录）→ 3 级 dirname
  - **坑**：`os.path.relpath()` 在 Windows 返回**反斜杠**路径，`rel.split('/')` 只得到 1 项
    → n 少 1 → ROOT 算到 scripts/。必须先把 `/` 换 `os.sep` 再 split。
- 表达式：`ROOT = os.path.dirname( × n + os.path.abspath(__file__) + ) × n`
- **插入位置**：模块**顶层**最后一个 import 语句之后（且必须已 `import os`）。
  - 坑 A：库模块（sites/ 等被 import 而非直接运行）也要补 ROOT，否则 NameError。
  - 坑 B："找最后一个 import"只能扫**顶层语句**——函数内 `from collections import Counter`
    会被误当插入点 → ROOT 嵌进函数体 → 整文件 SyntaxError，AST 都解析不了，
    只能文本级正则删除重插。
  - 坑 C：插到 docstring 前（`import os` 之前）→ NameError: os。检查 ROOT 行之前
    是否已有 `import .*\bos\b`，没有则移到 import 区之后。

## 验证四连

1. 全量 py_compile（`find scripts -name '*.py' | grep -v legacy ...`）
2. AST 验证 ROOT：解析每个文件，确认 ROOT 在模块顶层 Assign，且
   `eval(expr, {'os': os, '__file__': 假路径}) == 仓库根`（能暴露历史 bug——
   实测 metrics_report.py 原 ROOT 只有 2 级 dirname，verify/ 需 3 级，一直算到 scripts/）
3. 1:1 平衡：`git diff` 中删除的硬编码路径行数 == 新增 os.path.join(ROOT) 行数
   （本实战 188 == 188）
4. 残留前缀 grep：生产脚本 0 残留（排除 legacy/tests/paths.py 注释/行尾注释）

## 换行保持（CRLF 陷阱）

批量重写脚本 `open(p, encoding='utf-8').read()` 用 universal newlines 把 CRLF 读成
`\n`，写回 `\n` → **CRLF 文件变 LF** → git diff 显示全文件行尾变化（521 行噪声，
实际内容只改 5 行）。修复：

```python
# 找出原本 CRLF 现在 LF 的文件，转回
raw_head = subprocess.run(['git', 'show', f'HEAD:{rel}'], capture_output=True).stdout
if b'\r\n' in raw_head and b'\r\n' not in open(p,'rb').read():
    raw = open(p,'rb').read().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
    open(p,'wb').write(raw)
```

仓库 core.autocrlf=true：git 对象存 LF、工作区检出 CRLF。提交前先消除行尾噪声，
用 `git diff --ignore-space-at-eol` 确认真实改动量。

## 迭代修复的教训（本次踩坑顺序）

1. 第一版 root_calc 用 `rel.split('/')`（Windows relpath 反斜杠）→ 子目录 ROOT 少一层
2. 修正公式后**全量重跑**把已正确的文件也改了一遍 → 引入 docstring 空行 + ROOT 位置错乱
3. 文本级修复（正则删除所有 ROOT 行 + 顶层 import 后重插）最稳——不要用 AST 去
   "规范化"已语法损坏的文件（AST 解析不了就跳过，结果文件继续坏）
4. 每步之间 git diff --stat 看总量变化，发现异常大的文件（521 行）立刻查行尾

## 工具脚本落盘位置

转换/修复脚本写 `<repo>\temp\`（不入 git），跑完即删。
