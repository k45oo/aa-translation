# -*- coding: utf-8 -*-
"""P6 接口文档自动生成器: 扫描 inventory 中 grade=core/tool 的脚本 → doc/接口文档.md。

提取（纯标准库 AST，零 API，静态提取不运行 --help——脚本有副作用）:
- 模块 docstring 首段
- argparse add_argument 参数（AST 静态）
- sys.argv 手写解析标 argv-manual（待规范化）
- env 变量（AST 扫 os.environ.get 的 AA_* 等）

同步三道闸:
1. scripts/tests/test_interface_docs.py 重跑生成器与 doc/接口文档.md 比对
2. AGENTS.md + scripts/README.md 约定: 改 CLI/env → 跑生成器同 commit
3. 生成器校验 _inventory.json 与文件树一致（增删脚本未更新 inventory 即报错）

用法: python scripts/tools/gen_interface_docs.py [--check]
"""
import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS = os.path.join(ROOT, "scripts")
DOC = os.path.join(ROOT, "doc")
INVENTORY = os.path.join(SCRIPTS, "_inventory.json")
OUT = os.path.join(DOC, "接口文档.md")

HEADER = "<!-- AUTO-GENERATED, DO NOT EDIT -->\n"

# env 变量名白名单（非 AA_ 前缀但项目相关的）
ENV_EXTRA = {"OPENCODE_GO_API_KEY", "GLM_API_KEY", "COMMANDCODE_API_KEY",
             "SILICONFLOW_API_KEY", "MINIMAX_CN_API_KEY",
             "XIAOMI_MIMO_API_KEY", "OPENROUTER_API_KEY", "AA_BACKEND"}

def rel(path):
    return os.path.relpath(path, ROOT).replace("\\", "/")

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
        return v.value.strip().split("\n\n")[0].strip()
    return ""

def get_argparse_args(tree):
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
                v = arg.value
                if v.startswith("AA_") or v in ENV_EXTRA:
                    envs.append(v)
    return sorted(set(envs))

def uses_sys_argv(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute):
            if getattr(node.value, "attr", "") == "argv":
                return True
    return False

def has_argparse(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "ArgumentParser":
                return True
    return False

def cli_label(tree):
    if has_argparse(tree):
        return "argparse"
    if uses_sys_argv(tree):
        return "argv-manual（待规范化）"
    return "no-cli"

def verify_inventory_fresh():
    """校验 _inventory.json 与文件树一致（增删脚本未更新 inventory 即报错）"""
    if not os.path.exists(INVENTORY):
        print("错误: 缺 _inventory.json，先跑 gen_inventory.py", file=sys.stderr)
        sys.exit(1)
    inv = json.load(open(INVENTORY, encoding="utf-8"))
    inv_files = set(inv["files"].keys())
    disk = set()
    for dirpath, dirnames, filenames in os.walk(SCRIPTS):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__",)]
        for fn in filenames:
            if fn.endswith(".py"):
                disk.add(rel(os.path.join(dirpath, fn)).replace("\\", "/"))
    missing_in_inv = disk - inv_files
    stale_in_inv = inv_files - disk
    problems = []
    if missing_in_inv:
        problems.append("inventory 缺文件（先跑 gen_inventory.py）: " + ", ".join(sorted(missing_in_inv)))
    if stale_in_inv:
        problems.append("inventory 有过期文件（先跑 gen_inventory.py）: " + ", ".join(sorted(stale_in_inv)))
    return problems, inv

def gen_docs():
    problems, inv = verify_inventory_fresh()
    if problems:
        print("; ".join(problems), file=sys.stderr)
        sys.exit(1)

    by_dir = {}
    for rp, it in sorted(inv["files"].items()):
        if it["grade"] not in ("core", "tool"):
            continue
        top = rp.split("/")[1]
        by_dir.setdefault(top, []).append((rp, it))

    lines = [HEADER,
             "# scripts/ 接口文档（自动生成）",
             "",
             "> 生成命令: `python scripts/tools/gen_interface_docs.py`（纯标准库 AST 静态提取，零 API）。",
             "> 改 CLI 参数 / env 读取后必须重跑并同 commit；此文件勿手改（test_interface_docs 校验）。",
             "",
             "## 目录",
             ""]
    for top in sorted(by_dir):
        lines.append(f"- [{top}/](#{top})")
    lines.append("")

    order = ["aa_translate.py", "common", "runner", "sites", "works", "tools", "cleanup", "fix", "verify"]
    for top in sorted(by_dir, key=lambda t: order.index(t) if t in order else 99):
        lines.append(f"## {top}/\n")
        for rp, it in sorted(by_dir[top]):
            base = rp.split("/")[-1]
            lines.append(f"### {base}\n")
            ds = it.get("docstring") or "（无 docstring）"
            lines.append(f"{ds}\n")
            lines.append(f"- 路径: `{rp}`")
            lines.append(f"- 分级: `{it['grade']}`")
            cli = it.get("cli") or "no-cli"
            if isinstance(cli, list):
                cli = " ".join(cli)
            lines.append(f"- CLI: `{cli}`")
            args = it.get("argparse") or []
            if args:
                lines.append("- 参数: " + ", ".join(f"`{a}`" for a in args))
            envs = it.get("env_vars") or []
            if envs:
                lines.append("- env: " + ", ".join(f"`{e}`" for e in envs))
            lines.append("")
    return "\n".join(lines)

def main():
    check = "--check" in sys.argv
    text = gen_docs()
    if check:
        if os.path.exists(OUT):
            cur = open(OUT, encoding="utf-8").read()
            if cur == text:
                print("OK: doc/接口文档.md 与生成器一致")
                return
            print("DIFF: doc/接口文档.md 过期，重跑生成器", file=sys.stderr)
            sys.exit(1)
        print("MISSING: doc/接口文档.md 不存在", file=sys.stderr)
        sys.exit(1)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"wrote {rel(OUT)}")

if __name__ == "__main__":
    main()
