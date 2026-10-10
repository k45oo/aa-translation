# -*- coding: utf-8 -*-
r"""`AA工作台.exe`（PyInstaller 冻结）的启动入口 —— 不装 Python 也能用的图形界面。

由 `scripts/tools/build_workbench_exe.py` 打包产出（发行包随包附带）。

**代码根 / 数据根分离**（方案 doc/方案归档/发行包打包方案_2026-10-09.md §5）：

* 代码（`aa_web_app/` 与 `scripts/`）→ PyInstaller 的 `_MEIPASS` 解包目录（只读，一次性）；
* **数据根 = exe 所在目录**：`.env`、`aaManga/`、`交付HTML/`、`glossary.json`、`roles.json`、
  工作台的 `aa_web_app/data/`（作品元数据/任务队列/朗读缓存）全部落在这里——
  onefile 的 `_MEIPASS` 每次启动都是新的临时目录，写进去等于丢。
  可用 `AA_BASE` 覆盖（数据放移动盘/别的目录），`AA_ENV` 覆盖 `.env` 位置。

与 `scripts/tools/aa_web.py`（源码路线）的区别：

* 数据根默认取 exe 所在目录（`AA_BASE` 覆盖）、日志落数据根；
* **默认端口 8766**（源码路线是 8765，两者可同时在跑；`AA_PORT` 覆盖，被占自动顺延）；
* 支持 **`--cli <脚本> [参数…]`**：以命令行模式在本进程里执行一个项目脚本——工作台的任务
  管理器靠它起子任务（下载/翻译/校对/生成合集），见 `scripts/common/aa_spawn.py`。
"""

import os
import shutil
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

PORT_MAX_TRIES = 10                      # 端口被占时顺延次数（同 AA编辑服务.exe 口径）


def _code_root():
    """代码根：onefile 为 `sys._MEIPASS`；源码运行 = 本文件所在目录。"""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def _data_root():
    """数据根：exe 所在目录（`AA_BASE` 可覆盖；开发态直接跑本文件时 = 仓库根）。"""
    v = (os.environ.get("AA_BASE") or "").strip()
    if v:
        return os.path.abspath(v)
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    # 源码态：scripts/tools/aa_web_exe.py → 上跳 3 级 = 仓库根（与 paths.CODE_ROOT 一致）
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


CODE = _code_root()
DATA = _data_root()
for _p in (CODE, os.path.join(CODE, "scripts"), os.path.join(CODE, "scripts", "common"),
           os.path.join(CODE, "scripts", "works")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 数据根 / .env 位置必须在 import 业务代码**之前**定死（aa_env 与 paths 都在 import 期读它们）
os.environ.setdefault("AA_BASE", DATA)
os.environ.setdefault("AA_ENV", os.path.join(DATA, ".env"))
os.environ.pop("PYTHONPATH", None)


def _ensure_dirs():
    """第一次运行就把可写目录建好（工作台各处会直接往里写）。"""
    for rel in (os.path.join("aa_web_app", "data"),
                os.path.join("aa_web_app", "data", "tasks"),
                os.path.join("aa_web_app", "data", "tts_cache"),
                "aaManga", "交付HTML", "temp", "导出"):
        try:
            os.makedirs(os.path.join(DATA, rel), exist_ok=True)
        except OSError:
            pass


def _bootstrap_registry():
    r"""冻结态：把 registry 种子放到**数据根**，并让 import 优先命中它。

    为什么必须这么做：exe 里的 `registry.py` 属于 PyInstaller **解包目录**（每次启动重新解包、
    退出即删）。「＋ 添加作品」会写 `registry.__file__` → 等于写进临时目录：本次会话里看得见，
    **重启后作品就"消失"**（作品页看不到章节、翻译也找不到配置）。

    做法：启动时把解包目录里的 registry 复制成 `<数据根>/scripts/works/registry.py`（只复制一次），
    并把该目录插到 sys.path 最前 —— 之后 import 命中的是数据根那份，增删改都**持久**。
    只复制 registry 本身：`paths.py` 等仍用解包目录那份，`paths.CODE_ROOT` 的语义不变。
    """
    if not getattr(sys, "frozen", False):
        return                                    # 源码态：仓库里那份本来就是可写的
    src = os.path.join(CODE, "scripts", "works", "registry.py")
    if not os.path.isfile(src):
        return
    dst_dir = os.path.join(DATA, "scripts", "works")
    dst = os.path.join(dst_dir, "registry.py")
    try:
        if not os.path.isfile(dst):
            os.makedirs(dst_dir, exist_ok=True)
            shutil.copy2(src, dst)
        if dst_dir not in sys.path:
            sys.path.insert(0, dst_dir)           # 必须排在 <解包目录>/scripts/works 之前
        # 数据根那份已就位 → 把**解包目录**里那份删掉。只靠 sys.path 顺序不够：任何脚本
        # 用 `__file__` 反推仓库根后 insert(0, <解包目录>/scripts/works) 就会把它翻出来
        # （实测：工作台起的子任务读到的仍是"只有示例作品"的那份）。删掉即物理上不可能误命中。
        if os.path.isfile(dst) and os.path.isfile(src):
            try:
                os.remove(src)
            except OSError:
                pass
    except OSError as e:
        print("[aa_web_exe] registry 持久化初始化失败（本次会话仍可用，重启后会丢新增作品）: %s" % e)


def _setup_log():
    """运行日志落数据根（`<数据根>/aa_web_app/workbench.log`，>5MB 滚 .1）。"""
    p = os.path.join(DATA, "aa_web_app", "workbench.log")
    try:
        if os.path.exists(p) and os.path.getsize(p) > 5 * 1024 * 1024:
            os.replace(p, p + ".1")
        f = open(p, "a", encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        return
    sys.stdout = f
    sys.stderr = f
    print("[aa_web_exe %s] 数据根: %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), DATA), flush=True)


def _port_free(port):
    with socket.socket() as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _looks_like_workbench(port):
    """该端口上是不是已经有一个工作台（是则直接开浏览器，不再起第二个）。"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=1.5) as r:
            return r.status == 200 and b"<html" in r.read(2048).lower()
    except Exception:
        return False


def _pick_port(default):
    if _port_free(default):
        return default, False
    if _looks_like_workbench(default):
        return default, True
    for i in range(1, PORT_MAX_TRIES + 1):
        if _port_free(default + i):
            return default + i, False
    return default, False


def _resolve_cli_script(script):
    r"""`--cli` 的脚本路径解析（抽出来便于单测）。

    * 相对路径 → **数据根**优先（用户自己的树），再退解包目录；
    * 绝对路径但落在**解包目录** → 换成数据根里的同一份（存在时）。

    第二条是"工作台起子任务报「未知作品: X（可用: baitou）」"的根治：脚本用 `__file__` 反推
    仓库根（`_BASE`），跑解包目录那份时 `_BASE` = 解包目录，它会把 `<解包目录>/scripts/works`
    插到 sys.path 最前，**盖掉**启动引导放进来的数据根注册表。手工 `--cli scripts/...`（相对）
    走的正是数据根那份，所以这条只在"工作台起子任务"时发作。
    """
    if not os.path.isabs(script):
        cand = os.path.join(DATA, script)
        return cand if os.path.isfile(cand) else os.path.join(CODE, script)
    if os.path.abspath(script).startswith(CODE + os.sep):
        rel = os.path.relpath(os.path.abspath(script), CODE)
        cand = os.path.join(DATA, rel)
        if os.path.isfile(cand):
            return cand
    return script


def _run_cli(argv):
    r"""`--cli <脚本> [参数…]`：把目标脚本当 `__main__` 在本进程里跑，返回它的退出码。

    冻结成 exe 后 `sys.executable` 就是 exe 自己，子任务若照旧写 `[exe, 脚本, …]`，会被入口
    当成"再起一份工作台"（参数被无视、端口被占、父进程等一个永不退出的服务 → 任务卡住，
    表现就是"下载失败"）。所以子任务命令统一成 `[exe, "--cli", 脚本, …]`。

    行为尽量贴近"源码态从仓库根跑脚本"：cwd = 数据根、`sys.argv` = 脚本+参数、开行缓冲（源码态靠 `-u`）。
    """
    if not argv:
        print("用法: AA工作台.exe --cli <脚本> [参数...]", file=sys.stderr)
        return 2
    script = _resolve_cli_script(argv[0])
    if not os.path.isfile(script):
        print("[--cli] 找不到脚本: %s" % script, file=sys.stderr)
        return 2
    os.makedirs(DATA, exist_ok=True)
    os.chdir(DATA)                                   # 与源码态一致：从（数据）根跑
    sys.argv = [script] + list(argv[1:])
    _d = os.path.dirname(os.path.abspath(script))
    if _d not in sys.path:
        sys.path.insert(0, _d)
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(line_buffering=True)      # 等价源码态的 `python -u`
        except Exception:
            pass
    import runpy
    try:
        runpy.run_path(script, run_name="__main__")
    except SystemExit as e:
        code = e.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return 1
    return 0


def main():
    _argv = sys.argv[1:]
    # 数据根准备必须在**任何分支**之前：`--cli` 子任务（下载/翻译/候选提取）同样要用数据根里的
    # 作品注册表——它排在解包目录之前才能命中（否则子进程只认包里那份示例作品，
    # 报「未知作品: X（可用: baitou）」）。
    _ensure_dirs()
    _bootstrap_registry()
    if _argv and _argv[0] == "--cli":
        return _run_cli(_argv[1:])
    try:
        import aa_env
        v = aa_env.get("AA_PORT")
    except Exception:
        v = os.environ.get("AA_PORT")
    try:
        base_port = int(str(v).strip()) if v else (8766 if getattr(sys, "frozen", False) else 8765)
    except (TypeError, ValueError):
        base_port = 8766 if getattr(sys, "frozen", False) else 8765

    port, already = _pick_port(base_port)
    url = "http://127.0.0.1:%d/" % port
    if already:
        print("工作台已在运行 → 直接打开浏览器: %s" % url)
        webbrowser.open(url)
        return 0

    _setup_log()
    print("AA 汉化矫正工作台: %s" % url)
    print("数据根（作品/配置/日志都在这里）: %s" % DATA)
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    import uvicorn
    from aa_web_app.main import app          # 动态 import：代码在 _MEIPASS 里的 data 拷贝上
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
    except OSError as e:
        if getattr(e, "winerror", None) == 10048:
            print("端口 %d 已被占用——工作台可能已在运行，请直接访问 %s" % (port, url))
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
