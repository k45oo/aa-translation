# -*- coding: utf-8 -*-
"""跑用例的**唯一入口**（2026-10-12）：全量并行 / 只跑与改动相关的模块。

为什么有它：全量 1174 例串行 ≈ 200s，改一行等三分钟太慢。两条加速：
  ① `-j N` 按**模块**并行（模块之间各用 mkdtemp、互不共享端口，可安全并行）；
  ② `--changed` 用「改动文件 → 哪些用例 import 了它」的映射只跑相关模块（+ 恒跑闸门用例）。
     —— 实测映射覆盖核心共享模块（aa_translate 19 / aa_config 11 / aa_backend 10 / refine_aa 9 /
        gen_aa_html 5…）；映射不出（新脚本、纯 CLI、站点解析器）就**退回全量**，绝不静默少跑。

用法（一律从仓库根、用 .venv/Scripts/python.exe；本脚本会自己切过去）：
  python scripts/tools/run_tests.py                    # 全量，并行（默认 -j = min(6, CPU)）
  python scripts/tools/run_tests.py -j 1               # 全量，串行（失败时看得清）
  python scripts/tools/run_tests.py --changed          # 只跑与 git 未提交/已暂存改动相关的模块
  python scripts/tools/run_tests.py --changed scripts/common/aa_font.py aa_web_app/main.py
  python scripts/tools/run_tests.py --modules test_aa_font test_gen_html
  python scripts/tools/run_tests.py --list ...         # 只打印会跑哪些模块 + 命中理由

退出码：0 全绿；1 有用例失败；2 用法/环境错（没 .venv、映射命令失败等）。
"""
import argparse
import ast
import concurrent.futures as fut
import glob
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))            # scripts/tools → scripts → 仓库根
TESTS = os.path.join(ROOT, "scripts", "tests")
VENV_PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")

# 恒跑：接口文档 / inventory 与文件树一致性（改任何脚本都可能让它过期，几毫秒~1s，值）
GATES = ("test_interface_docs",)

# 串行段：这几个模块跑**真调度器/真子进程**并按时间轮询中间状态，机器被 6 个进程占满时
# 会偶发假失败（实测 test_chain_end_to_end_with_fake_runner 在 -j6 下超时）。它们单独跑，
# 其余模块并行 —— 既快又不用把偶发当真实失败。
SERIAL = ("test_task_manager", "test_schedule_task")

# 调度提示（**只影响并行跑的顺序**，不影响跑哪些用例）：2026-10-14 重测（-j6 下各模块秒数，
# 相对量级为准）。并行时按它**从重到轻**发车（LPT）：最重的模块 t=0 就开跑，避免「最重的最后才轮到」。
# 新增模块没登记就用默认值（1.0s）；模块自身变重/变轻后记得回来更新，否则发车顺序会失真。
WEIGHT = {
    "test_mirror_incremental": 39, "test_gen_html_plain": 34, "test_gen_html": 29,
    "test_aa_font": 25, "test_refine_line_guard": 20, "test_refine_stream": 13,
    "test_edit_server": 9, "test_pre_terms": 8, "test_opencode_go": 7, "test_task_manager": 7,
    "test_schedule_task": 4, "test_work_pack": 4, "test_settings_page": 4, "test_edit_type": 3,
    "test_aaweb": 3, "test_repatriate": 3, "test_model_choice": 2, "test_work_glossary_merge": 2,
    "test_sfx_api": 2, "test_line_history": 2, "test_work_delete": 2, "test_update_chain": 2,
    "test_candidates": 2, "test_board_runner": 2, "test_fix_lead_kanji": 2,
}


def weight(m):
    return WEIGHT.get(m, 1.0)


def log(*a):
    print(*a, flush=True)


def ensure_venv():
    """仓库规矩：aa_web 系用例要 .venv（fastapi）。不是它就跑回去重来。"""
    if not os.path.exists(VENV_PY):
        log("[warn] 没找到 .venv/Scripts/python.exe —— 继续用当前解释器（aa_web 系用例可能报缺依赖）")
        return
    if os.path.normcase(os.path.abspath(sys.executable)) == os.path.normcase(VENV_PY):
        return
    log("[info] 切到 %s" % VENV_PY)
    os.execv(VENV_PY, [VENV_PY, os.path.abspath(__file__)] + sys.argv[1:])


# ── 模块 ↔ 用例映射（AST，不执行被测代码）────────────────────────────────────
def _imported_names(path):
    """文件里 import 的模块名（取顶层 + 点号全名两套，供 aa_web_app.logic 这类命中）。"""
    try:
        tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
    except SyntaxError:
        return set()
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                out.add(a.name)
                out.add(a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            if n.module and not n.level:
                out.add(n.module)
                out.add(n.module.split(".")[0])
                for al in n.names:                       # `from aa_web_app import main` → aa_web_app.main
                    out.add(n.module + "." + al.name)
                    out.add(al.name)
    return out


def test_modules():
    return sorted(os.path.basename(p)[:-3] for p in glob.glob(os.path.join(TESTS, "test_*.py")))


def build_index():
    """{模块名 → {用例模块}}；同时返回每个用例 import 过的名字集合（改动的别名再用）。"""
    rev, names = {}, {}
    for m in test_modules():
        ns = _imported_names(os.path.join(TESTS, m + ".py"))
        names[m] = ns
        for x in ns:
            rev.setdefault(x, set()).add(m)
    return rev, names


def changed_files(explicit):
    """改动文件（相对仓库根）：显式参数 > git（未提交 + 已暂存 + 未跟踪）。"""
    if explicit:
        return [os.path.relpath(os.path.abspath(p), ROOT).replace("\\", "/") for p in explicit]
    r = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        log("[error] `git status` 失败（不在 git 仓库里？）—— 请显式给路径或用 --all")
        sys.exit(2)
    out = []
    for line in (r.stdout or "").splitlines():
        p = line[3:].strip()
        if " -> " in p:                                    # 重命名：取新名
            p = p.split(" -> ")[-1]
        if p:
            out.append(p.strip('"'))
    return out


def map_module(rel):
    """改动文件 → 它对应的模块名（用例按这个名 import）。"""
    if not rel.endswith(".py"):
        return []
    parts = rel.split("/")
    if "tests" in parts:                                   # 用例自己：直接跑那个模块
        base = os.path.basename(rel)
        return [base[:-3]] if base.startswith("test_") else []
    name = os.path.basename(rel)[:-3]
    cands = {name}
    if rel.startswith("aa_web_app/") and len(parts) >= 2:   # aa_web_app/logic.py → 也认 aa_web_app.logic
        cands.add("aa_web_app." + name)
    if rel.startswith("scripts/sites/"):                    # 站点解析器：用例 import 裸名
        cands.add(name)
    return sorted(cands)


def pick(changed, rev, names, all_=False):
    """→ (用例模块有序表, 命中理由 dict, 是否因为映射不出而回退全量)"""
    if all_ or not changed:
        return list(test_modules()), {}, bool(changed) and not all_
    hits, why, unmapped = {}, {}, []
    for rel in changed:
        ms = map_module(rel)
        got = set()
        for m in ms:
            for t in rev.get(m, ()):                       # 直接命中
                got.add(t)
            for t, ns in names.items():                    # 别名命中（例如 aa_web_app 包）
                if m in ns:
                    got.add(t)
        if not got:
            unmapped.append(rel)
        for t in got:
            hits[t] = 1
            why.setdefault(t, []).append(rel)
    if unmapped:
        log("[info] 这些改动映射不到用例（新脚本/纯 CLI/站点解析器？）→ 回退全量：")
        for u in unmapped:
            log("        - %s" % u)
        return list(test_modules()), {}, True
    sel = sorted(set(hits) | set(GATES))
    return sel, why, False


# ── 跑 ───────────────────────────────────────────────────────────────────────
def run_module(m):
    t0 = time.time()
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", TESTS,
                        "-p", m + ".py"], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    n = 0
    mm = re.search(r"^Ran (\d+) tests", out, re.M)
    if mm:
        n = int(mm.group(1))
    skip = len(re.findall(r"skipped\b", out))
    return {"module": m, "rc": r.returncode, "seconds": round(time.time() - t0, 1),
            "tests": n, "skipped": skip, "out": out}


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--all", action="store_true", help="显式全量（默认就是全量）")
    ap.add_argument("--changed", nargs="*", metavar="PATH",
                    help="只跑与这些文件相关的用例（不带路径 = 读 git status）")
    ap.add_argument("--modules", nargs="+", metavar="MOD", help="只跑这些用例模块（test_ 前缀可省）")
    ap.add_argument("-j", "--jobs", type=int, default=0, help="并行进程数（默认 min(6, CPU)；1 = 串行）")
    ap.add_argument("--list", action="store_true", help="只打印会跑哪些模块，不跑")
    a = ap.parse_args()

    if not os.path.isdir(TESTS):
        log("[error] 找不到 %s" % TESTS)
        return 2
    ensure_venv()

    rev, names = build_index()
    if a.modules:
        sel = [m if m.startswith("test_") else "test_" + m for m in a.modules]
        why, changed, fb = {m: ["--modules"] for m in sel}, [], False
    else:
        changed = changed_files(a.changed if a.changed is not None else None)
        sel, why, fb = pick(changed, rev, names, all_=a.all)
        if changed and not fb:
            log("[changed] %d 个改动文件 → %d 个用例模块（+%d 闸门）"
                % (len(changed), len(sel) - len(GATES), len(GATES)))
            for m in sel:
                mark = "（闸门）" if m in GATES and m not in why else ""
                log("  %-38s %s%s" % (m, ", ".join(why.get(m, [])[:3]), mark))
        elif fb:
            log("[changed] 映射不出 → 跑全量（不静默少跑）")

    if a.list:
        return 0
    if not sel:
        log("[warn] 没有要跑的模块")
        return 0

    # 实测（i7-6700HQ 4C8T / F 盘 SATA HDD，2026-10-14 重测；全量 74 模块 / 1221 例）：
    #   -j4（旧默认）68.9s  →  -j6 **53.1s**  →  -j8 57.0s（反升：8 路把 HDD 打满，
    #   各模块自身耗时被拖长，如 test_mirror_incremental 39.4s→48.1s）。
    #   这批用例是**子进程+文件 I/O 型**（镜像模块 93% 墙钟是 git spawn），不是 CPU 型
    #   ⇒ 4 物理核上给到 6 路是甜点，再多只抢盘。旧注释「-j4 ≈ -j6」是 mirror 还没提速时的实测，已过期。
    jobs = a.jobs or min(6, (os.cpu_count() or 2))
    jobs = max(1, min(jobs, len(sel)))
    first = sorted([m for m in sel if m in SERIAL], key=lambda m: -weight(m))
    rest = sorted([m for m in sel if m not in SERIAL], key=lambda m: -weight(m))   # LPT：重的先开跑
    log("\n=== 跑 %d 个模块（%s%s）===" % (len(sel), "串行" if jobs == 1 else "并行 -j %d" % jobs,
                                    "" if (jobs == 1 or not first) else "，其中 %d 个串行" % len(first)))
    t0 = time.time()
    res = []
    for m in (first if jobs > 1 else []):
        r = run_module(m)
        res.append(r)
        log("[串行] %-36s %6.1fs  %3d 用例%s" % (m, r["seconds"], r["tests"],
                                                "" if r["rc"] == 0 else "   ← 失败"))
    if jobs == 1:
        for i, m in enumerate(sel, 1):
            r = run_module(m)
            res.append(r)
            log("[%2d/%d] %-38s %6.1fs  %3d 用例%s" % (i, len(sel), m, r["seconds"], r["tests"],
                                                      "" if r["rc"] == 0 else "   ← 失败"))
    else:
        with fut.ThreadPoolExecutor(max_workers=jobs) as ex:
            futs = {ex.submit(run_module, m): m for m in rest}
            for i, f in enumerate(fut.as_completed(futs), 1):
                r = f.result()
                res.append(r)
                log("[%2d/%d] %-38s %6.1fs  %3d 用例%s" % (i, len(rest), r["module"], r["seconds"],
                                                          r["tests"], "" if r["rc"] == 0 else "   ← 失败"))
    bad = [r for r in res if r["rc"] != 0]
    wall = time.time() - t0
    log("\n=== %.1fs（%d 模块 / %d 用例，%d 失败）==="
        % (wall, len(res), sum(r["tests"] for r in res), len(bad)))
    if bad:
        for r in sorted(bad, key=lambda x: x["module"]):
            log("\n" + "-" * 72 + "\n[失败] %s（rc=%d）\n" % (r["module"], r["rc"]))
            lines = [l for l in r["out"].splitlines() if l.strip()]
            log("\n".join(lines[-40:]))
        log("\n重跑单个：%s -m unittest discover -s scripts/tests -p \"<模块>.py\" -v"
            % os.path.relpath(VENV_PY if os.path.exists(VENV_PY) else sys.executable, ROOT))
        return 1
    slow = sorted(res, key=lambda r: -r["seconds"])[:5]
    log("最慢：" + "、".join("%s %.1fs" % (r["module"], r["seconds"]) for r in slow))
    log("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
