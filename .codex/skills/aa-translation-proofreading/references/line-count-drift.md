# 行数错位检修（脚本骨架）

> 适用：汉化文件与原文行数不等、或某行起整体错位一行。全部用 `<repo>\.venv\Scripts\python.exe` 跑（读注册表要 `sys.path` 加 `scripts/works`）。

## 0. 读文件（统一分隔符口径）
```python
def rdl(p):
    d = open(p, "rb").read()
    sep = "\r\n" if d.count(b"\r\n") > d.count(b"\n") / 2 else "\n"
    return d.decode("utf-8", "replace").split(sep), sep
A, _ = rdl(aa_dir + "/" + base + ".txt")
C, sep = rdl(out_dir + "/" + base + "_汉化.txt")
```
行数不等的三个类型（先分类，再动手）：
- `len(C) > len(A)` 且多出的元素**全是 `''` 且都在 `len(A)` 之后** → EOF 空行，无害。
- 多出的行与**相邻行逐字节相同**（原文该处不重复）→ 重复行漂移（本节主体）。
- 多出的行含 `最终结果：L<行号>` / `（注：…` / `…属于漏译` → 校对回包泄漏，属污染行，删除。

## 1. 定位删除位
打分法（单行差，O(n²) 可接受，n≈1000 时秒级）：
```python
scores = []
for j in range(len(C)):
    s = sum(1 for i in range(len(A)) if (C[i] if i < j else C[i + 1]) == A[i])
    scores.append((s, j))
scores.sort(reverse=True)   # (相同行数, 待删 0-based 下标)
```
多行差（d>1）用前向 DP：状态 `(i, k)` = 已对齐原文 i 行、已用掉 k 次删除；转移「保留 C[i+k] 对齐 A[i]」或「删 C[i+k]」；答案取 `g[n][d]`，回溯记下删除下标。注意：删任意一行前的基线相同行数也要打印出来，作对照（实测 262/806 → 735/806）。

## 2. 全库体检（只读）
```python
import sys, os, glob
sys.path.insert(0, "<repo>/scripts/works"); import registry
for name, cfg in registry.WORKS.items():
    for f in sorted(os.listdir(cfg["aa_dir"])):
        if not f.endswith(".txt") or f.endswith("_台词.txt"): continue
        base = f[:-4]
        cands = glob.glob(os.path.join(cfg["out_dir"], base + "_汉化.txt"))
        if not cands: continue
        # 比 len(rdl(aa/f)) vs len(rdl(cands[0]))
```
注意：`aa_dir`/`out_dir` 缺失或章节未译要跳过；`entry_*` 命名取自 registry，不要自己拼目录名。

## 3. 复验（用项目权威口径，别自写校验）
```python
sys.path.insert(0, "<repo>/scripts/common")
from scan_width import collect_mismatch
rows, rows_artalign, (n_files, skipped, count_mismatch) = collect_mismatch(
    cfg["aa_dir"], cfg["out_dir"], files=["entry_XXXX.txt"],
    check_art_align=True, WORK_DIR=cfg["work_dir"])
```
返回值是 3 元组（第 3 项是 `(文件数, 跳过, 行数不一致清单)`）——docstring 写的 5 元组是旧的，解包踩过坑。
- `count_mismatch` 非空 = 该文件**整章跳过**，宽度与 art 列位都没查。
- `rows_artalign` 里的行号是 0-based，展示时 +1。

## 4. 写回
```python
shutil.copy2(TR, backup_dir)          # work/backup_linefix_<ts>/entry_XXXX_汉化.txt
open(TR, "w", encoding="utf-8", newline="").write(sep.join(C2))   # sep 用上一步探测到的
```
`newline=""` + 显式 sep 是唯一能原样保留 CRLF 并保持末尾换行状态的写法；写回后再跑一次第 3 步，并断言 `len(C2) == len(A)`。

## 5. 折行型漂移：多出的行要「并回」而不是「删」（DP 两处改造 + 分流）

> 与重复行漂移同款症状（汉化比原文多 1~4 行），但多出的行是**半句译文**：模型回包里的换行被当普通字符拼回「一行」，
> 写回 `"\n".join()` 时这一行被拆成 2 行。**纯删除会丢内容**——多出的行按性质分流。

在 §1 的删除型前向 DP 上改两处：

- **代价加偏移惩罚**：`match 代价*1000 + k`、`del 代价*1000 + (k+1)`，k = 当前偏移（= 已多出的行数）。
  只比「逐字节相同行数」时 DP 会等价地选「在异常点**之前**删一行」，其后整块 art 跟着前移一行；加了 k 项才会把删除就地放在异常点。
  （实测第一版没这一项，214 行的 art 块被整体前移。）
- **多余行分流**（对 DP 判为删除的每行 j）：
  - 空行、或含假名/半角假名（`[\u3040-\u30ff\uff61-\uff9f]`）→ **删**（空行 / art 碎片 `ヾﾄ､ヽr‐､{ {　l`）；
  - 只含中文（汉字 + 中文标点 + 空格）→ **并入前一条保留行**：`line = 前行.rstrip() + t.strip()`，
    再 `pad_to_width(line, disp_width(SRC[i]))`（i = 前行对应的原文行）。实测两例合并后宽度恰等原文（45/45、103/103）。
- **判据别用 `line_kind()`**：对 art 行会撒谎——`以刈` 这类当画用的汉字 → `mixed`；短密 art `ヾﾄ､ヽr‐､{ {　l` → `text`。
- **art 被改写/截断的行按原文还原**（同一事故常见：art 行被模型「翻译」`､､`→`、、`、行尾丢一截）
  判据必须窄：**去尾空格后汉化是原文的严格前缀、且短 ≥4 格**（截断签名）。改用字符重合度（≥0.6）会把**几百行正常译文**
  判成 art 损伤——实测误报 900 行量级；宁可按此窄判据漏报，再手工补那两三行。

**验收五层，缺一层不算修完**：
1. 行数相等 + 「逐字节相同行数」跳变（实测 4089/14907 → 13975/14907）；
2. 结构性行（空行 / `#` / art）与原文逐字节 0 处不一致——用「原文该行无高置信 text 段」或前缀相似度补判，别只信 `line_kind`；
3. `scan_line_drift.py` 复扫该章消失；
4. `scan_width --work <id>` 复跑（行数修好后该章才第一次真正被查，会冒出被行数问题掩盖的既有 art 列位失配 —— 属另一类，别混进同一次改动）；
5. **工作台端到端**（原症状就是阅读器末行报「行数据加载失败」）：`GET /api/work/<work>/chapter/<fname>`，
   `fname` **必须带后缀**（`entry_XXXX_汉化.txt`，只给基名 404），再验 `.../line/<n-1..n-3>/html` 全 200
   + `.../range?from_k=<n-20>&to_k=<n-1>` 返回末页行。

**邻类勿误修**：修完仍能看到「短宽行」——落在 text/mixed 上的是**正常译文**（中文短于日文是常态，允许短宽）；
汉化比原文**少几百行**（相同行占比≈0）是旧译文对不上新原文，不是漂移，别用「删/并多余行」硬修。
