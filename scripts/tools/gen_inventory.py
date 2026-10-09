# -*- coding: utf-8 -*-
"""P1 脚本盘点生成器: 生成 scripts/_inventory.json（入库，可重复运行）。

每个脚本标注:
- grade: core（生产链路 + aa_web_app 依赖）/ tool（独立 CLI 工具，孤儿但保留）/
         deprecated（有权威替代的副本，候选废弃）/ test / legacy（已归档只读）
- cli: AST 提取的 argparse 参数（无则标 argv-manual / no-cli）
- refs: import + subprocess 字符串 + aa_web_app 引用 + 文档提及
- dup_of: 指向权威实现（人工判定后填写）

防误判铁律: 孤儿 != 废弃（CLI 入口 + 文档提及计入 refs；废弃清单逐条人工过目）。
纯标准库零 API。用法: python scripts/tools/gen_inventory.py
"""
import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS = os.path.join(ROOT, "scripts")
DOC = os.path.join(ROOT, "doc")
WEBAPP = os.path.join(ROOT, "aa_web_app")

EXCLUDE_DIRS = {"__pycache__"}
# 权威实现映射（dup_of 判定用，人工维护，按方案 P4 收敛目标）
AUTHORITATIVE = {
    "cleanup.pass4": "common.pass3",
    "cleanup.pass5": "common.pass3",
    "cleanup.pass5_fc2": "common.pass3",
    "cleanup.pass5_saki": "common.pass3",
    "cleanup.pass5_yaruobook": "common.pass3",
    "cleanup.pass6": "common.pass3",
    "cleanup.pass6_fc2": "common.pass3",
    "cleanup.pass6_kaiki": "common.pass3",
    "cleanup.pass6b_fc2": "common.pass3",
    "cleanup.pass7": "common.pass3",
    "cleanup.pass7_fc2": "common.pass3",
    "cleanup.pass7_kaiki": "common.pass3",
    "cleanup.pass8": "common.pass3",
    "cleanup.pass8_fc2": "common.pass3",
    "cleanup.pass9": "common.pass3",
    "cleanup.pass9_fc2": "common.pass3",
    "cleanup.pass10": "common.pass3",
    "cleanup.pass2_fc2": "common.pass2",
    "cleanup.pass3_fc2": "common.pass3",
    "cleanup.pass3_parallel": "common.pass3",
}

# 生产链路核心目录/文件（grade=core 基准；完整 scripts/ 前缀格式）
CORE_DIRS = {"common", "runner", "sites"}
CORE_FILES = {
    "scripts/aa_translate.py",
    "scripts/works/registry.py",
    "scripts/works/paths.py",
    "scripts/works/dl_touhou.py",
}
# works/ 下保留的站点特例/工具（方案 P2「保留（勿误伤）」；完整 scripts/ 前缀格式）
WORKS_KEEP_TOOL = {
    "scripts/works/fix_touhou_width.py",       # 站点特例宽度修复
    "scripts/works/touhou_runner.py",          # touhou 批量下载（AGENTS.md 文档提及）
    "scripts/works/fix_comments_yaruobook.py", # 评论区修复，无权威替代
    "scripts/works/translate_comments_yaruobook.py",
}
# verify/ 下现役工具（方案 P5 迁移进 tools/）
VERIFY_KEEP_TOOL = {"scripts/verify/quality_gate.py", "scripts/verify/metrics_report.py",
                    "scripts/verify/reader_win_ab.py"}
# fix/ 下无权威替代的保留工具
FIX_KEEP_TOOL = {"scripts/fix/backfill_aa_comments.py", "scripts/fix/refine_aa.py", "scripts/fix/fix_mixed_drift.py"}
# 保留的工具（独立 CLI/探针/统计/视觉，勿误伤）——tools/ 下默认 tool
TOOL_DIRS = {"tools"}

def rel(path):
    return os.path.relpath(path, ROOT).replace("\\", "/")

def mod_of(path):
    """脚本相对 scripts/ 的模块名: aa_translate / common.pass2 / works.registry"""
    r = os.path.relpath(path, SCRIPTS)
    if r.endswith(".py"):
        r = r[:-3]
    return r.replace(os.sep, ".")

def read_src(path):
    with open(path, "rb") as f:
        data = f.read()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gbk", errors="replace")

def get_docstring(tree):
    if not tree.body or not isinstance(tree.body[0], ast.Expr):
        return ""
    v = tree.body[0].value
    if isinstance(v, ast.Constant) and isinstance(v.value, str):
        # 取首段（第一个空行前）
        return v.value.strip().split("\n\n")[0].strip()
    return ""

def get_argparse_args(tree):
    """AST 提取 argparse add_argument 的 dest/option 字符串（静态，不运行 --help）"""
    args = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "add_argument":
                for a in node.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        args.append(a.value)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "ArgumentParser":
                for a in node.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        args.append(a.value)
    return sorted(set(args))

def get_env_vars(tree):
    envs = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args):
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if arg.value.startswith("AA_") or arg.value.startswith("OPENCODE_") or arg.value in ("GLM_API_KEY", "COMMANDCODE_API_KEY"):
                    envs.append(arg.value)
    return sorted(set(envs))

def get_imports(tree):
    """本文件 import 的模块名（标准库/三方/项目模块全列）"""
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.add(node.module.split(".")[0])
    return sorted(mods)

def get_subprocess_refs(src):
    """subprocess/os.system/importlib 字符串里的 python 脚本路径引用"""
    refs = set()
    for m in re.finditer(r"['\"](?:python\s+)?(?:scripts/)?([A-Za-z0-9_./-]+\.py)['\"]", src):
        refs.add(m.group(1))
    for m in re.finditer(r"['\"]([A-Za-z0-9_./-]+\.py)['\"]", src):
        if "scripts" in m.group(1) or m.group(1).endswith(".py"):
            refs.add(m.group(1))
    return sorted(refs)

def get_top_consts(tree):
    """模块级赋值的 AA_DIR/OUT_DIR/WORK_DIR/CHAPTERS/TITLES 常量"""
    consts = []
    names = {"AA_DIR", "OUT_DIR", "WORK_DIR", "CHAPTERS", "TITLES"}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id in names:
                    consts.append(t.id)
    return sorted(set(consts))

def get_dup_functions(tree):
    """重复函数定义检测（方案实测的 6 个）"""
    dups = {}
    targets = {"disp_width", "pad_to_width", "kana_runs", "is_text_line",
               "line_kind", "allow_overwidth", "residual_text", "residual_bad",
               "build_whitelist", "fix_batch", "render_line", "render_header", "entry_url"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in targets:
            dups[node.name] = True
    return sorted(dups.keys())

def has_main_guard(tree):
    for node in tree.body:
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__"):
            return True
    return False

def uses_sys_argv(tree, src):
    return "sys.argv" in src

def has_hardcoded_path(src):
    return ("<repo-parent>" in src) or ("<repo-parent>" in src) or ("F:\\\\ai" in src)

def collect_py_files():
    files = []
    for dirpath, dirnames, filenames in os.walk(SCRIPTS):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            if fn.endswith(".py"):
                files.append(os.path.join(dirpath, fn))
    return sorted(files)

def load_doc_refs():
    """doc/ 下 md + AGENTS.md + scripts/README.md 提及的 scripts 路径"""
    refs = {}
    doc_files = []
    if os.path.isdir(DOC):
        for dirpath, _, fns in os.walk(DOC):
            for fn in fns:
                if fn.endswith(".md"):
                    doc_files.append(os.path.join(dirpath, fn))
    for extra in (os.path.join(ROOT, "AGENTS.md"), os.path.join(SCRIPTS, "README.md")):
        if os.path.exists(extra):
            doc_files.append(extra)
    pat = re.compile(r"(?:scripts/)?([A-Za-z0-9_]+/[A-Za-z0-9_./-]+\.py|aa_translate\.py)")
    for df in doc_files:
        try:
            with open(df, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        for m in pat.finditer(text):
            p = m.group(1)
            if not p.startswith("scripts/"):
                p = "scripts/" + p
            refs.setdefault(p, []).append(rel(df))
    return refs

def load_webapp_refs():
    """aa_web_app/ 引用 common/works/runner 脚本（import + subprocess 字符串）"""
    refs = {}
    if not os.path.isdir(WEBAPP):
        return refs
    for dirpath, _, fns in os.walk(WEBAPP):
        if "__pycache__" in dirpath:
            continue
        for fn in fns:
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(dirpath, fn)
            src = read_src(fp)
            for m in re.finditer(r"(?:from|import)\s+(common|works|runner|scripts)\.([A-Za-z0-9_]+)", src):
                refs.setdefault(f"scripts/{m.group(1)}/{m.group(2)}.py", []).append(rel(fp))
            for m in re.finditer(r"['\"](?:python\s+)?scripts/([A-Za-z0-9_./-]+\.py)['\"]", src):
                refs.setdefault("scripts/" + m.group(1), []).append(rel(fp))
    return refs

def main():
    files = collect_py_files()
    doc_refs = load_doc_refs()
    webapp_refs = load_webapp_refs()

    # 第一遍: 每个文件的事实
    facts = {}
    for fp in files:
        src = read_src(fp)
        try:
            tree = ast.parse(src)
        except SyntaxError:
            tree = None
        rp = rel(fp)
        mod = mod_of(fp)
        top = os.path.relpath(fp, SCRIPTS).split(os.sep)[0]
        facts[fp] = {
            "path": rp, "module": mod, "top_dir": top,
            "docstring": get_docstring(tree) if tree else "(parse error)",
            "cli": get_argparse_args(tree) if tree else [],
            "env_vars": get_env_vars(tree) if tree else [],
            "imports": get_imports(tree) if tree else [],
            "subprocess_refs": get_subprocess_refs(src),
            "top_consts": get_top_consts(tree) if tree else [],
            "dup_functions": get_dup_functions(tree) if tree else [],
            "has_main_guard": has_main_guard(tree) if tree else False,
            "uses_sys_argv": uses_sys_argv(tree, src),
            "hardcoded_path": has_hardcoded_path(src),
        }

    # 第二遍: 交叉引用（谁 import / subprocess 引用谁）
    mod_to_file = {facts[fp]["module"]: fp for fp in facts}
    # 别名: 去掉目录前缀的模块名也可能被 import（如 import pass2 指 cleanup/pass2）
    alias_to_file = {}
    for fp in facts:
        m = facts[fp]["module"]
        alias_to_file[m] = fp
        if "." in m:
            alias_to_file[m.split(".")[-1]] = fp

    referenced_by = {}
    for fp in facts:
        src = read_src(fp)
        mod = facts[fp]["module"]
        refs = set()
        # 本文件 import 的模块（方向: fp -> 被 import 者）
        # 优先级: 精确模块名 > common/(权威层) 同 basename > 其他目录同 basename
        for imp in facts[fp]["imports"]:
            if imp in alias_to_file and alias_to_file[imp] != fp:
                refs.add(mod_of(alias_to_file[imp]))
            matched = False
            for cand, cfile in mod_to_file.items():
                if cand != mod and (cand == imp or cand.split(".")[-1] == imp):
                    refs.add(cand)
                    matched = True
                    break
            if not matched:
                # 同 basename 匹配: common/ 优先（权威层），再任意目录
                base_match = None
                for cand, cfile in sorted(mod_to_file.items()):
                    if cand != mod and cand.split(".")[-1] == imp:
                        base_match = cand
                        if cand.startswith("common."):
                            break
                if base_match:
                    refs.add(base_match)
        # subprocess 引用（同 basename 时 common/ 优先）
        for s in facts[fp]["subprocess_refs"]:
            norm = s
            if norm.startswith("scripts/"):
                norm = norm[len("scripts/"):]
            if norm.endswith(".py"):
                key = norm[:-3].replace("/", ".")
                if key in mod_to_file:
                    refs.add(key)
                elif norm.startswith(("common/", "runner/", "sites/", "works/", "tools/", "cleanup/", "fix/")):
                    base_name = norm.split("/")[-1][:-3]
                    found = None
                    for m2, cfile in sorted(mod_to_file.items()):
                        if m2.split(".")[-1] == base_name:
                            found = m2
                            if m2.startswith("common."):
                                break
                    if found:
                        refs.add(found)
        referenced_by[fp] = sorted(refs)

    # 反转: referenced_by[fp] = 谁 import / subprocess 引用 fp
    imported_by = {fp: set() for fp in facts}
    for fp, refs in referenced_by.items():
        for m2 in refs:
            other = mod_to_file.get(m2)
            if other and other != fp:
                imported_by[other].add(facts[fp]["module"])
    referenced_by = {fp: sorted(imported_by[fp]) for fp in facts}

    # 文档/工作台引用
    doc_mentions = {}
    for fp in facts:
        rp = rel(fp)
        ments = []
        # 文档提及（精确路径或 basename）
        for dp, srcs in doc_refs.items():
            if dp == rp or os.path.basename(dp) == os.path.basename(rp):
                ments.extend(srcs)
        doc_mentions[fp] = sorted(set(ments))
    web_mentions = {}
    for fp in facts:
        rp = rel(fp)
        ments = []
        for dp, srcs in webapp_refs.items():
            if dp == rp or os.path.basename(dp) == os.path.basename(rp):
                ments.extend(srcs)
        web_mentions[fp] = sorted(set(ments))

    # 第三遍: 分级（建议值，废弃清单人工过目后调整）
    inventory = {}
    stats = {"total": len(facts), "legacy": 0, "core": 0, "tool": 0,
             "deprecated_candidate": 0, "test": 0, "hardcoded": 0, "top_consts": 0}
    for fp in facts:
        f = facts[fp]
        rp = rel(fp)
        top = f["top_dir"]
        mod = f["module"]
        refs_by = referenced_by[fp]
        web = web_mentions[fp]
        docs = doc_mentions[fp]

        if top == "legacy":
            grade = "legacy"
        elif top == "tests":
            grade = "test"
        elif top in CORE_DIRS or rp in CORE_FILES:
            grade = "core"
        elif top == "tools":
            grade = "tool"
        elif top == "works":
            if rp in WORKS_KEEP_TOOL:
                grade = "tool"
            elif rp in CORE_FILES:
                grade = "core"
            else:
                grade = "deprecated_candidate"
        elif top == "verify":
            if rp in VERIFY_KEEP_TOOL:
                grade = "tool"
            else:
                grade = "deprecated_candidate"
        elif top == "fix":
            if rp in FIX_KEEP_TOOL:
                grade = "tool"
            else:
                grade = "deprecated_candidate"
        elif top in ("cleanup", "download", "extract", "translate", "generate", "experiments"):
            # cleanup/pass2.py, pass3.py 在 CORE_FILES 已标 core；其余按方案 P2
            grade = "deprecated_candidate"
        else:
            grade = "tool"

        # 引用修正: 被 core 或 aa_web_app 引用 → 升级为 core（依赖倒置保护）
        if grade in ("deprecated_candidate", "tool"):
            core_referenced = False
            for m2 in refs_by:
                other = mod_to_file.get(m2)
                if other and facts[other]["top_dir"] in CORE_DIRS:
                    core_referenced = True
                    break
            if core_referenced or web:
                grade = "core"

        dup_of = None
        if grade == "deprecated_candidate":
            dup_of = AUTHORITATIVE.get(mod)

        item = {
            "path": rp,
            "grade": grade,
            "cli": f["cli"] if f["cli"] else ("argv-manual" if f["uses_sys_argv"] else "no-cli"),
            "argparse": f["cli"],
            "env_vars": f["env_vars"],
            "refs": {
                "imports": f["imports"],
                "subprocess": f["subprocess_refs"],
                "referenced_by": refs_by,
                "webapp": web,
                "docs": docs,
            },
            "dup_of": dup_of,
            "hardcoded_path": f["hardcoded_path"],
            "top_consts": f["top_consts"],
            "dup_functions": f["dup_functions"],
            "has_main_guard": f["has_main_guard"],
            "docstring": f["docstring"][:200],
        }
        inventory[rp] = item
        stats[grade] = stats.get(grade, 0) + 1
        if f["hardcoded_path"]:
            stats["hardcoded"] += 1
        if f["top_consts"]:
            stats["top_consts"] += 1

    # 重复函数统计
    dup_counts = {}
    for fp in facts:
        for fn in facts[fp]["dup_functions"]:
            dup_counts[fn] = dup_counts.get(fn, 0) + 1

    out = {
        "generated_at": None,  # 稳定性: 不写时间戳，避免 P6 比对误报
        "stats": {
            **stats,
            "dup_function_counts": dup_counts,
            "hardcoded_files": sorted(rp for rp, it in inventory.items() if it["hardcoded_path"]),
            "top_consts_files": sorted(rp for rp, it in inventory.items() if it["top_consts"]),
        },
        "files": inventory,
    }
    out_path = os.path.join(SCRIPTS, "_inventory.json")
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:  # 全仓 LF(2026-09-07), newline 防 CRLF
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"wrote {rel(out_path)}")
    print(json.dumps({k: v for k, v in stats.items() if k != "hardcoded_files"}, ensure_ascii=False, indent=1))
    print("dup counts:", json.dumps(dup_counts, ensure_ascii=False))

if __name__ == "__main__":
    main()
