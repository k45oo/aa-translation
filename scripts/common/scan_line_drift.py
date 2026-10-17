# -*- coding: utf-8 -*-
"""汉化/原文行数漂移扫描（2026-09-22 entry_8332 行号错位事故；零 API，纯本地）

事故现象：汉化 txt 比原文多 N 行（中段多出重复译文/半截译文/多余空行）→ 全章行号右移，
工作台阅读/编辑末尾几行报「行数据加载失败」（末尾行号越界）。

根因：模型译文里带回车/换行 → refine_aa 段级重建把折行拼回「一行」，写回时按换行连接
把这一行拆成 2 行。护栏已加（aa_translate.one_line / txt_write + refine_aa 写回前比对行数并拒写），
本脚本用于**扫历史存量**与事故后复查。

分类：
  · 中段真错位 —— 行数不齐且差异不在结尾（阅读时会看到行号错位）→ 必须人工修（删多余行）
  · 仅行尾空行差 —— 只在文件末尾多/少空行，正文不受影响（低优先，可批量修）

用法：
  python scripts/common/scan_line_drift.py                 # 全库扫描（默认）
  python scripts/common/scan_line_drift.py --work <id>     # 单作品，写 <work_dir>/line_drift.txt
  python scripts/common/scan_line_drift.py --json          # 机读输出
退出码：0=无中段错位，1=有中段错位（供任务/CI 判断）
"""
import difflib, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "common"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "works"))

import aa_translate as T


def _lines(p):
    """读 .txt/.txt.gz → 行列表（缺文件返回 None）。"""
    real = T.txt_existing(p)
    if not os.path.exists(real):
        return None
    return T.txt_read(real)


def classify(src_lines, tr_lines):
    """返回 (kind, detail)：kind = 'mid' | 'tail'。"""
    s, t = list(src_lines), list(tr_lines)
    while s and s[-1] == "":
        s.pop()
    while t and t[-1] == "":
        t.pop()
    if len(s) == len(t):
        return "tail", {"tail_src": len(src_lines) - len(s), "tail_tr": len(tr_lines) - len(t)}
    sm = difflib.SequenceMatcher(None, s, t, autojunk=False)
    chunks = [[tag, i1 + 1, i2 - i1, j1 + 1, j2 - j1]
              for tag, i1, i2, j1, j2 in sm.get_opcodes()
              if tag != "equal" and (i2 - i1) != (j2 - j1)]
    return "mid", {"chunks": chunks[:8], "n_chunks": len(chunks)}


def scan_work(work_dir, work_id=None, verbose=True):
    """扫一个作品的 aa/ vs 汉化/，返回 [{'base','src','tr','kind','detail'}]。"""
    aa_dir = os.path.join(work_dir, "aa")
    tr_dir = os.path.join(work_dir, "汉化")
    rows = []
    if not (os.path.isdir(aa_dir) and os.path.isdir(tr_dir)):
        return rows
    for fname in T.list_txt(aa_dir, exclude=("_台词.txt", "_汉化.txt")):
        base = T.txt_key(fname)
        src = _lines(T.txt_existing(os.path.join(aa_dir, fname)))
        tr = _lines(T.txt_existing(os.path.join(tr_dir, base + "_汉化.txt")))
        if src is None or tr is None or len(src) == len(tr):
            continue
        kind, detail = classify(src, tr)
        rows.append({"base": base, "src": len(src), "tr": len(tr),
                     "diff": len(tr) - len(src), "kind": kind, "detail": detail})
    return rows


def main():
    args = sys.argv[1:]
    work = args[args.index("--work") + 1] if "--work" in args else None
    as_json = "--json" in args

    if work:
        import registry
        cfg = registry.get(work)
        # 作品根 = aa_dir 的父目录（work_dir 指 <根>/work，不能当作品根用）
        targets = [(work, os.path.dirname(cfg["aa_dir"]))]
    else:
        base = os.path.join(ROOT, "aaManga")
        targets = [(d, os.path.join(base, d)) for d in sorted(os.listdir(base))
                   if os.path.isdir(os.path.join(base, d))]

    all_rows, n_chap = [], 0
    for wid, wdir in targets:
        rows = scan_work(wdir, wid)
        n_chap += len(T.list_txt(os.path.join(wdir, "汉化"), "_汉化.txt")) if os.path.isdir(os.path.join(wdir, "汉化")) else 0
        for r in rows:
            r["work"] = wid
        all_rows += rows
        if work:
            mid = [r for r in rows if r["kind"] == "mid"]
            rep = os.path.join(wdir, "line_drift.txt")
            with open(rep, "w", encoding="utf-8") as f:
                f.write("# 汉化/原文行数漂移报告（%s）\n" % wid)
                f.write("# 中段真错位 %d 章 / 仅行尾空行差 %d 章 / 共 %d 章\n\n" % (
                    len(mid), len(rows) - len(mid), n_chap))
                for r in rows:
                    f.write("%-22s src %5d 汉化 %5d (%+d) %s %s\n" % (
                        r["base"], r["src"], r["tr"], r["diff"],
                        "【中段真错位】" if r["kind"] == "mid" else "（仅行尾空行差）",
                        r["detail"].get("chunks", "")))
            if not as_json:
                print("报告 → %s" % rep)

    mid = [r for r in all_rows if r["kind"] == "mid"]
    tail = [r for r in all_rows if r["kind"] == "tail"]
    if as_json:
        print(json.dumps({"mid": mid, "tail": tail}, ensure_ascii=False, indent=1))
    else:
        print("扫描 %d 部作品 / 约 %d 章：中段真错位 %d 章，仅行尾空行差 %d 章"
              % (len(targets), n_chap, len(mid), len(tail)))
        for r in mid:
            print("  [中段] %-34s %-16s src %5d 汉化 %5d (%+d) 位置 %s" % (
                r["work"][:34], r["base"], r["src"], r["tr"], r["diff"],
                ",".join(str(c[1]) for c in r["detail"]["chunks"][:6])))
        for r in tail[:8]:
            print("  [尾空] %-34s %-16s src %5d 汉化 %5d (%+d)" % (
                r["work"][:34], r["base"], r["src"], r["tr"], r["diff"]))
        if len(tail) > 8:
            print("  …另有 %d 章仅行尾空行差（未逐条列出）" % (len(tail) - 8))
    return 1 if mid else 0


if __name__ == "__main__":
    sys.exit(main())
