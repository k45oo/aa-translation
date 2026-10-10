# -*- coding: utf-8 -*-
"""把交付页的「编辑服务」打包成**单文件 exe**（PyInstaller）→ `scripts/tools/dist/AA编辑服务.exe`。

用法：`python scripts/tools/build_edit_server.py`（`--clean` 先清构建缓存）

为什么这样打：exe 里带的是**数据**而不是模块 —— 整棵 `scripts/` 树 + `aa_web_app/logic.py`；
`edit_server.py` 用 `importlib` 动态加载它们（PyInstaller 静态分析看不见 → 不会被重复打进
PYZ）。于是「编辑服务用的渲染/宽度/替换逻辑」与仓库**同一份**，不复制、不会走样；
拷到没装 Python、没有仓库的机器上也能跑。

打完 `gen_aa_html.py main()` 会把该 exe 拷进每个交付文件夹（`install_edit_server`）。

**交付物不带生成者的本机信息**（exe 会随合集发出去）：打包前 staging 阶段
① 剔除与运行无关且夹带本机路径 / 作品清单 / 实测报告的目录（`legacy`/`logs`/`tests`/`benchmark`）；
② 对留下的源码做**本机路径脱敏**（`<repo>\\...` → `<repo>`），打包后还会复查残留，有残留就拒绝出包。
脱敏只改 exe 内那份拷贝，仓库文件不动；渲染指纹 `render_ver` 涉及的 3 个文件本就不含这类路径。
"""
import os
import re
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
# 脱敏 / 本机痕迹复查 / 密钥复查（2026-10-09 抽到 release_common.py，与发行包打包脚本共用）
from release_common import (DESENS as _DESENS, DESENS_EXT, LEFTOVER as _LEFTOVER,  # noqa: E402
                           PATTERN_HOLDERS, desensitize as _desensitize,
                           leftovers as _leftovers)
_SCRIPTS = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_SCRIPTS)
NAME = "AA编辑服务"
DIST = os.path.join(_HERE, "dist")                     # 打包产物只留这一份（生成合集时拷进交付夹）
EXE = os.path.join(DIST, NAME + ".exe")
# 构建中间目录必须放在 scripts/ **外面**：--add-data 会把整棵 scripts/ 当数据打包，
# 放在里面会被 PyInstaller 自己收进包（实测报 "Trying to collect PKG file into itself!",
# 且会把上一次的 exe 也打进去 → 体积翻倍）。temp/ 已在 .gitignore。
_BUILD = os.path.join(_ROOT, "temp", "pyinstaller_build")
# 打进去的源码要先 staging 一份：直接 --add-data <仓库>/scripts 会把 scripts/tools/dist 里
# **上一次的 exe** 也收进包（实测 13MB → 26MB），还会撞上 PyInstaller 自身构建目录。
_STAGE = os.path.join(_ROOT, "temp", "pyinstaller_src")   # 不能放 _BUILD 里：--clean 会清空 workpath

# 与编辑服务运行无关、却夹带本机路径 / 全部作品清单 / 模型实测报告的目录 → 不进交付物
EXCLUDE_DIRS = {"legacy", "logs", "tests", "benchmark"}
# 打包脚本自己（运行无关；且它自带上面的脱敏模式串，留在包里会被残留复查逮到）
EXCLUDE_FILES = {"build_edit_server.py", "dist"}

# 脱敏规则 / 痕迹复查 / 密钥复查已在 release_common.py（本文件只留打 exe 的部分）。
# 注意：本文件自带过这些模式串，PATTERN_HOLDERS 里列了它 → 复查时跳过自己。





def _stage_src():
    """把要随包走的源码拷到 staging（排除 __pycache__、tools/build、tools/dist 与 EXCLUDE_DIRS）。"""

    def ignore(dirpath, names):
        out = [n for n in names if n == "__pycache__" or n.endswith(".pyc")
               or n.endswith(".exe") or n in EXCLUDE_DIRS or n in EXCLUDE_FILES]
        if os.path.basename(dirpath) == "tools":
            out += [n for n in ("build", "dist") if n in names]
        return out

    if os.path.isdir(_STAGE):
        shutil.rmtree(_STAGE)
    os.makedirs(os.path.join(_STAGE, "aa_web_app"))
    shutil.copytree(_SCRIPTS, os.path.join(_STAGE, "scripts"), ignore=ignore)
    shutil.copy2(os.path.join(_ROOT, "aa_web_app", "logic.py"),
                 os.path.join(_STAGE, "aa_web_app", "logic.py"))
    return os.path.join(_STAGE, "scripts")
# 动态加载的脚本（scripts/ 与 aa_web_app/logic.py 是**数据**，PyInstaller 不做静态分析）
# 各自 import 的标准库要靠 --hidden-import 补齐，否则 exe 里 import 时才炸（实测漏了 urllib.request）。
HIDDEN = [
    "urllib.request", "urllib.error", "urllib.parse", "http.client", "hashlib", "gzip",
    "tomllib", "glob", "shutil", "html", "html.entities", "concurrent.futures",
    "collections.abc", "csv", "base64", "datetime", "unicodedata", "itertools", "functools",
    "ssl", "socket", "email", "email.parser", "email.utils",
]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if os.name != "nt":
        print("只支持在 Windows 上打包（交付页是双击运行的 Windows 交付物）")
        return 2
    sep = ";"
    stage = _stage_src()
    n_files, n_hits = _desensitize(stage)          # 交付物不带本机路径（只改 staging 拷贝）
    left = _leftovers(stage)
    print("staging: 剔除 %s；脱敏 %d 个文件 / %d 处本机路径" %
          ("、".join(sorted(EXCLUDE_DIRS)), n_files, n_hits))
    if left:
        print("拒绝出包：staging 里仍有本机痕迹（先修掉再打包）")
        for rel, what in left[:20]:
            print("   %s  ← %s" % (rel, what))
        return 3
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--console",
           "--name", NAME,
           "--distpath", os.path.join(_BUILD, "dist"),
           "--workpath", _BUILD,
           "--specpath", _BUILD,
           # 随包数据：仓库现成实现（编辑服务运行时按路径加载，见文件头说明）
           "--add-data", stage + sep + "scripts",
           "--add-data", os.path.join(_STAGE, "aa_web_app", "logic.py") + sep + "aa_web_app",
           os.path.join(_HERE, "edit_server.py")]
    for m in HIDDEN:
        cmd[3:3] = ["--hidden-import", m]
    if "--clean" in argv:
        cmd.insert(3, "--clean")
    print("$ " + " ".join(cmd))
    import subprocess
    rc = subprocess.call(cmd, cwd=_ROOT)
    if rc:
        print("打包失败：退出码 %d" % rc)
        return rc
    os.makedirs(DIST, exist_ok=True)
    built = os.path.join(_BUILD, "dist", NAME + ".exe")
    out = EXE                      # 注意：别在本函数里直接给 EXE 赋值（会变成局部变量，
    try:                           # 上面这行就读不到模块级常量了——曾踩：UnboundLocalError）
        shutil.copy2(built, out)
    except PermissionError:
        # 旧 exe 正被占用（编辑服务还开着/交付夹里的副本在跑）→ 另存，手动替换
        out = os.path.join(DIST, NAME + ".new.exe")
        shutil.copy2(built, out)
        print("！目标 exe 被占用 → 已另存为 %s，请手动替换" % out)
    print("=" * 62)
    print("  打包完成：%s（%.1f MB）" % (out, os.path.getsize(out) / 1048576.0))
    print("  生成合集时 gen_aa_html 会自动把它拷进交付文件夹（AA编辑服务.exe）")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
