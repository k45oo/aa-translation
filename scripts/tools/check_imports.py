# -*- coding: utf-8 -*-
r"""依赖自检：把 `scripts/` 与 `aa_web_app/` 用到的**第三方模块**逐个 import 一遍。

为什么需要：发行包的 exe 把 `scripts/` 当**数据**带（PyInstaller 看不见它们内部的 `import`），
漏一个依赖只有跑到那条具体路径时才炸——实测打包版「生成术语候选」就是炸在
`build_term_pool → import janome`（`ModuleNotFoundError`），而库内一切正常。
本脚本把"依赖齐不齐"变成一条**可自动断言的自检**（发行包构建的 exe 探测会跑它）。

用法：
    python scripts/tools/check_imports.py [--list]

退出码：0 = 全部可导入；1 = 有缺失（打印缺失清单）。`--list` 只列出扫描到的第三方模块。
"""

import argparse
import ast
import importlib
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_SCRIPTS)
_SKIP_DIRS = {"legacy", "benchmark", "logs", "__pycache__", "dist", "build"}


def _local_module_names():
    """项目自身的模块名（含包名与子目录名）——扫描结果的排除项，别把内部 import 当第三方。

    判据：`scripts/` 与 `aa_web_app/` 下的**任何 .py 名或目录名**都算本地——因为这些脚本靠
    `sys.path.insert` 注入（`common/`、`works/`、`sites/` 等都能直接 `import`），不是第三方。
    """
    local = set()
    for base in (_SCRIPTS, os.path.join(_ROOT, "aa_web_app")):
        local.add(os.path.basename(base))              # 包名本身（aa_web_app）
        for dp, dns, fns in os.walk(base):
            dns[:] = [d for d in dns if d not in _SKIP_DIRS]
            local.update(dns)                          # 注入式可导入的子目录
            for fn in fns:
                if fn.endswith(".py"):
                    local.add(fn[:-3])
    return local


def scan(mods_only=False):
    """扫描 staging/仓库 → (第三方模块名 → 使用它的文件集合)。"""
    local = _local_module_names()
    std = set(sys.stdlib_module_names)
    found = {}
    for base in (_SCRIPTS, os.path.join(_ROOT, "aa_web_app")):
        for dp, dns, fns in os.walk(base):
            dns[:] = [d for d in dns if d not in _SKIP_DIRS]
            for fn in fns:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(dp, fn)
                try:
                    tree = ast.parse(open(p, encoding="utf-8").read())
                except (OSError, SyntaxError):
                    continue
                for node in ast.walk(tree):
                    names = []
                    if isinstance(node, ast.Import):
                        names = [a.name for a in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                        names = [node.module]
                    for m in names:
                        top = m.split(".")[0]
                        if top in std or top in local or top.startswith("_"):
                            continue
                        found.setdefault(top, set()).add(fn)
    return found


def scan_dotted():
    r"""扫描 `from a.b import c` / `import a.b` 的**完整点分路径**（记 a.b）。

    只验顶层会漏掉「顶层能导入、子模块缺失」的场合 —— 实测冻结 exe 里 `import janome` 通过、
    `janome.tokenizer` 却 ModuleNotFoundError（打包版「生成术语候选」就是这么炸的，
    而旧自检只 import 顶层，没能拦住）。
    """
    local = _local_module_names()
    std = set(sys.stdlib_module_names)
    found = {}
    for base in (_SCRIPTS, os.path.join(_ROOT, "aa_web_app")):
        for dp, dns, fns in os.walk(base):
            dns[:] = [d for d in dns if d not in _SKIP_DIRS]
            for fn in fns:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(dp, fn)
                try:
                    tree = ast.parse(open(p, encoding="utf-8").read())
                except (OSError, SyntaxError):
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                        names = [node.module]
                    elif isinstance(node, ast.Import):
                        names = [a.name for a in node.names]
                    else:
                        continue
                    for m in names:
                        top = m.split(".")[0]
                        if "." not in m or top in std or top in local or top.startswith("_"):
                            continue
                        found.setdefault(m, set()).add(fn)
    return found


def _is_local_name(name, local_names):
    """该顶层名是不是项目自己的模块/包。

    除目录扫描结果外，再试一次 `aa_web_app.<name>` / `scripts` 下的子模块探测 —— 冻结 exe 里
    `aa_web_app/` 只有模板与静态资源（.py 在 PYZ 里，磁盘上没有），只靠扫描会把 `selection`
    这类 Web 应用子模块误判成第三方。
    """
    if name in local_names:
        return True
    for pkg in ("aa_web_app",):
        try:
            if importlib.util.find_spec(pkg + "." + name) is not None:
                return True
        except Exception:                      # noqa: BLE001
            continue
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description="第三方依赖自检")
    ap.add_argument("--list", action="store_true", help="只列出扫描结果，不做导入")
    a = ap.parse_args(argv)
    found = scan()
    _local = _local_module_names()
    found = {k: v for k, v in found.items() if not _is_local_name(k, _local)}
    if a.list:
        for k in sorted(found):
            print("%-16s <- %s" % (k, ", ".join(sorted(found[k])[:4])))
        for k in sorted(dotted):
            print("%-28s <- %s" % (k, ", ".join(sorted(dotted[k])[:4])))
        return 0
    dotted = {k: v for k, v in scan_dotted().items()
              if not _is_local_name(k.split(".")[0], _local)}
    missing = []
    for name in sorted(set(found) | set(dotted)):
        try:
            importlib.import_module(name)
        except Exception as e:                     # noqa: BLE001 —— 只关心"能不能导入"
            missing.append((name, "%s: %s" % (type(e).__name__, e)))
    print("扫描到第三方模块 %d 个（含点分子模块 %d 个）；缺失 %d 个"
          % (len(found), len(dotted), len(missing)))
    for name, err in missing:
        print("  [×] %-16s %s" % (name, err[:120]))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
