# -*- coding: utf-8 -*-
r"""把工作台打包成 **单文件 exe**（PyInstaller）→ `scripts/tools/dist/AA工作台.exe`。

    python scripts/tools/build_workbench_exe.py [--clean] [--no-probe]

产出物由 `build_release.py` 拷进发行包（包根 `AA工作台.exe`）：不装 Python 也能双击用图形界面。

为什么这样打（与 `build_edit_server.py` 同一套路）
--------------------------------------------------
* 代码是**数据**：整棵 `scripts/` + `aa_web_app/`（含 templates/static）先 staging 一份，
  脱敏后才交给 PyInstaller → exe 里那份与仓库同源，但**不带生成者本机路径**；
* staging 里过四道硬门禁（本机痕迹 / 密钥 / 语法 / 必需文件），不过就拒绝出包；
* 运行期代码根 = `sys._MEIPASS`（只读解包目录），**数据根 = exe 所在目录**（见
  `aa_web_exe.py` 与方案 doc/方案归档/发行包打包方案_2026-10-09.md §5）——onefile 的临时目录每次启动都换，
  数据写进去等于丢。

打完默认会**起一次探测**：用临时数据根 + 空闲端口把 exe 跑起来，要求拿到 HTTP 200，
并确认它把 `workbench.log` 写在数据根（而不是解包目录）。
"""

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_SCRIPTS)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import release_common as rc                     # noqa: E402
from build_edit_server import HIDDEN as _BASE_HIDDEN  # 动态加载脚本的标准库依赖（同一份清单）  # noqa: E402

NAME = "AA工作台"
DIST = os.path.join(_HERE, "dist")
EXE = os.path.join(DIST, NAME + ".exe")
_BUILD = os.path.join(_ROOT, "temp", "pyinstaller_workbench")      # 构建中间目录（在 scripts/ 外）
_STAGE = os.path.join(_ROOT, "temp", "workbench_exe_src")          # staging 源码（脱敏对象）

SKIP_DIRS = {"legacy", "logs", "benchmark", "dist", "build", "__pycache__", "data"}
SKIP_GLOBS = ("*.pyc", "*.log", "*.log.[0-9]", "*.bak", "*.bak_*", "*_副本*", "*- 副本*",
              "AGENTS.md", "*.tmp")

# 冻结后动态加载的包（PyInstaller 静态分析看不见的部分）+ 它们的可选子模块
HIDDEN = sorted(set(_BASE_HIDDEN) | {
    "fastapi", "starlette", "pydantic", "jinja2", "uvicorn", "anyio", "h11",
    "edge_tts", "zhconv", "email.mime", "email.message", "encodings.idna",
})
# 动态 import 多的包（懒加载/插件式）→ 连子模块一起收；带数据文件的包 → 连数据一起收。
# 教训：`scripts/` 是**数据**，PyInstaller 看不见它们内部的 import，漏一个只在跑到那条路时才炸
# （实测打包版「生成术语候选」炸在 build_term_pool → import janome）。
# 兜底清单（扫描不到的场合）；正常的依赖由 `_scan_third_party()` 从源码里探出来，
# 且**每个探到的包都自动 collect-submodules + collect-data**（见下）。
COLLECT_SUBMODULES = ["uvicorn", "fontTools", "zhconv", "PIL", "janome"]
COLLECT_DATA = ["janome"]
# 官方 hook 已覆盖、且一收就胖的包不收（实测：collect-submodules PIL 多带 13MB 二进制插件
# `_avif.cp312-win_amd64.pyd` 等；collect-data fontTools 多带 6MB 包内 .c 源码，运行时用不上）。
# 排除是安全的：真漏了东西，构建探测里的 `check_imports`（含点分子模块）会直接让构建失败。
SUBMODULE_DENY = {"PIL"}
DATA_DENY = {"PIL", "fontTools"}
# 必须**整包以文件形式**落进 bundle 的第三方包：这类包运行期是"按路径读文件"的
# （janome 的词典 `sysdic/entries_*.py` 用 mmap 按文件名打开），PyInstaller 把它们当模块收进 PYZ
# 就等于没有 —— 实测 exe 里能 import、能 Tokenizer()，一读词典就
# FileNotFoundError: _MEI*/janome/sysdic/entries_compact0.py。
# 做法 = 整包当 data 复制（不进 PYZ），运行期 import 由 MEIPASS 的文件系统兜底。
DATA_PKGS = ["janome"]


def _data_pkg_dirs(names):
    """第三方包 → 它在当前环境里的包目录（用于 --add-data 整包复制）。找不到就跳过。"""
    out = {}
    for m in names:
        try:
            spec = importlib.util.find_spec(m)
        except Exception:                          # noqa: BLE001
            continue
        root = os.path.dirname(spec.origin) if spec and spec.origin else None
        if root and os.path.isdir(root):
            out[m] = root
    return out


def _scan_third_party(stage):
    """问**staging 里的** check_imports 要一份第三方依赖清单（脚本依赖变了就自动跟上）。

    这样 exe 打包不再依赖手维护的 hidden-import 列表——新增脚本用到新库时，只要
    `check_imports.py` 能扫到（它扫的是同一棵树），就会被收进 exe。
    """
    tools = os.path.join(stage, "scripts", "tools")
    if not os.path.isfile(os.path.join(tools, "check_imports.py")):
        return []
    sys.path.insert(0, tools)
    try:
        import check_imports as CI          # noqa: PLC0415
        return sorted(CI.scan())
    except Exception as e:                   # noqa: BLE001
        print("  ! 第三方依赖扫描失败（回退手维护清单）: %s" % e)
        return []
    finally:
        if tools in sys.path:
            sys.path.remove(tools)


def stage_src():
    """staging 一份 codes（scripts/ + aa_web_app/），剔除运行无关与运行时数据。"""
    if os.path.isdir(_STAGE):
        shutil.rmtree(_STAGE)
    os.makedirs(_STAGE)
    n1 = rc.copy_filtered(os.path.join(_ROOT, "scripts"), os.path.join(_STAGE, "scripts"),
                          ignore_dirs=SKIP_DIRS, ignore_globs=SKIP_GLOBS)
    n2 = rc.copy_filtered(os.path.join(_ROOT, "aa_web_app"), os.path.join(_STAGE, "aa_web_app"),
                          ignore_dirs=SKIP_DIRS, ignore_globs=SKIP_GLOBS)
    print("staging: scripts %d 个文件 / aa_web_app %d 个文件" % (n1, n2))
    return _STAGE


def gate(stage):
    """四道硬门禁：本机痕迹 / 密钥 / 语法 / 必需文件（命中即拒绝出包）。"""
    bad = []
    left = rc.leftovers(stage)
    if left:
        bad.append("仍有本机痕迹 %d 处，例如 %s" % (len(left), left[:5]))
    sec = rc.secret_hits(stage)
    if sec:
        bad.append("发现疑似真 key 明文: %s" % sec[:5])
    syn = rc.compile_all(stage)
    if syn:
        bad.append("语法检查失败: %s" % syn[:5])
    must = [os.path.join("aa_web_app", "main.py"),
            os.path.join("aa_web_app", "templates"),
            os.path.join("aa_web_app", "static"),
            os.path.join("scripts", "tools", "aa_web_exe.py"),
            os.path.join("scripts", "common", "aa_env.py"),
            os.path.join("scripts", "works", "paths.py")]
    missing = [m for m in must if not os.path.exists(os.path.join(stage, m))]
    if missing:
        bad.append("缺少必需文件: %s" % missing)
    return bad


def probe(exe, port=8799, timeout=120):
    """起一次真 exe（临时数据根 + 空闲端口）→ (ok, 说明)。同时验证数据根落点。"""
    tmp = tempfile.mkdtemp(prefix="aa_exe_probe_")
    env = dict(os.environ)
    env.update({"AA_BASE": tmp, "AA_PORT": str(port)})
    env.pop("AA_ENV", None)
    env.pop("AA_ENV_PATH", None)
    env.pop("PYTHONPATH", None)
    p = subprocess.Popen([exe], env=env, cwd=tmp, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    ok, note = False, ""
    t0 = time.time()
    try:
        while time.time() - t0 < timeout:
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=2) as r:
                    body = r.read(4096).lower()
                if r.status == 200 and b"<html" in body:
                    ok, note = True, "HTTP 200（%.1fs）" % (time.time() - t0)
                    break
            except Exception:
                time.sleep(1)
        if not ok:
            note = "%ds 内没起来" % timeout
        log = os.path.join(tmp, "aa_web_app", "workbench.log")
        if os.path.isfile(log):
            note += "；日志落数据根 ✓"
        else:
            ok = False
            note += "；[×] 数据根下没有 workbench.log"
    finally:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(tmp, ignore_errors=True)
    return ok, note


def main(argv=None):
    ap = argparse.ArgumentParser(description="打包 AA工作台.exe")
    ap.add_argument("--clean", action="store_true", help="先清构建缓存")
    ap.add_argument("--no-probe", action="store_true", help="跳过起 exe 探测")
    ap.add_argument("--stage", default="", help="用现成的源码树（含 scripts/ 与 aa_web_app/）；"
                                               "发行包用它保证 exe 与包同源")
    ap.add_argument("--out", default="", help="输出 exe 路径（默认 scripts/tools/dist/AA工作台.exe）")
    a = ap.parse_args(argv)
    if os.name != "nt":
        print("只支持 Windows（交付物是双击运行的 Windows exe）")
        return 2

    if a.stage:
        stage = os.path.abspath(a.stage)
        if not (os.path.isdir(os.path.join(stage, "scripts"))
                and os.path.isdir(os.path.join(stage, "aa_web_app"))):
            print("--stage 目录不是源码树（缺 scripts/ 或 aa_web_app/）: %s" % stage)
            return 2
        print("用现成源码树: %s（不再脱敏/再 staging —— 调用方已处理）" % stage)
    else:
        stage = stage_src()
        n_files, n_hits = rc.desensitize(stage)
        print("脱敏: %d 个文件 / %d 处本机路径" % (n_files, n_hits))
        bad = gate(stage)
        if bad:
            print("拒绝出包：")
            for b in bad:
                print("   ✗ %s" % b)
            return 3

    out = os.path.abspath(a.out) if a.out else EXE
    sep = ";"
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--console",
           "--name", NAME,
           "--distpath", os.path.join(_BUILD, "dist"),
           "--workpath", _BUILD,
           "--specpath", _BUILD,
           # 从 staging 解析代码（保证打进 exe 的是脱敏后那份）
           "--paths", stage,
           # 运行期动态加载的数据：整棵 scripts/ + 模板/静态资源
           "--add-data", os.path.join(stage, "scripts") + sep + "scripts",
           "--add-data", os.path.join(stage, "aa_web_app", "templates") + sep + "aa_web_app/templates",
           "--add-data", os.path.join(stage, "aa_web_app", "static") + sep + "aa_web_app/static",
           # uvicorn 的 loop/protocol 实现是运行期字符串导入的，静态分析看不见
           "--collect-submodules", "uvicorn",
           os.path.join(_HERE, "aa_web_exe.py")]
    deps = _scan_third_party(stage)
    if deps:
        print("  自动收集第三方依赖: %s" % ", ".join(deps))
    data_pkgs = _data_pkg_dirs(DATA_PKGS)
    if data_pkgs:
        print("  整包按数据复制（运行期按路径读文件）: %s" % ", ".join(sorted(data_pkgs)))
    for name, root in sorted(data_pkgs.items()):
        cmd[3:3] = ["--add-data", root + sep + name]
    for m in [x for x in sorted(set(HIDDEN) | set(deps)) if x not in data_pkgs]:
        cmd[3:3] = ["--hidden-import", m]
    # 每个探到的第三方包都要连**子模块与数据**一起收。`import X` 过得了不代表 `X.sub` 在包里
    # （实测打包版「生成术语候选」炸在 janome：顶层能导入，`janome.tokenizer` 却是
    # ModuleNotFoundError）；janome 还要它自带的词典数据。
    for m in sorted(((set(COLLECT_SUBMODULES) | set(deps)) - SUBMODULE_DENY) - set(data_pkgs)):
        cmd[3:3] = ["--collect-submodules", m]
    for m in sorted(((set(COLLECT_DATA) | set(deps)) - DATA_DENY) - set(data_pkgs)):
        cmd[3:3] = ["--collect-data", m]
    if a.clean:
        cmd.insert(3, "--clean")
    print("$ " + " ".join(cmd))
    rc_code = subprocess.call(cmd, cwd=stage)
    if rc_code:
        print("打包失败：退出码 %d" % rc_code)
        return rc_code

    os.makedirs(os.path.dirname(out), exist_ok=True)
    built = os.path.join(_BUILD, "dist", NAME + ".exe")
    try:
        shutil.copy2(built, out)
    except PermissionError:
        out = out + ".new.exe"
        shutil.copy2(built, out)
        print("！目标 exe 被占用 → 已另存为 %s，请手动替换" % out)
    print("=" * 62)
    print("  打包完成：%s（%.1f MB）" % (out, os.path.getsize(out) / 1048576.0))
    print("=" * 62)

    if a.no_probe:
        return 0
    print("=== 探测：起一次真 exe（临时数据根 + 端口 8799）")
    ok, note = probe(out)
    print("  %s %s" % ("通过:" if ok else "失败:", note))
    return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(main())
