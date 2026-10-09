# -*- coding: utf-8 -*-
"""AA 汉化矫正工作台 启动器。

用法：python scripts/tools/aa_web.py（或双击仓库根 一键重启工作台.bat）
启动 FastAPI 于 http://127.0.0.1:8765 并自动打开浏览器（仅本机可访问，无鉴权）。
"""
import os
import sys
import threading
import webbrowser

_TOOLS = os.path.dirname(os.path.abspath(__file__))       # .../scripts/tools
_ROOT = os.path.dirname(os.path.dirname(_TOOLS))          # 仓库根
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 弹掉外部注入的 PYTHONPATH（如 Hermes 桌面终端环境），确保只用本 venv 的依赖
os.environ.pop("PYTHONPATH", None)

import uvicorn  # noqa: E402

from aa_web_app.main import app  # noqa: E402   (import 时已把 <仓库根>/.env 载入 os.environ)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根(2026-08-18 Phase 3 动态计算)

# 端口（2026-10-08 阶段 5）：AA_PORT（真实 env 或 <仓库根>/.env）> 8765。
# 必须在 import main 之后取 —— main 会 import aa_config，届时 .env 已灌进 os.environ。
def _port():
    try:
        import aa_env
        v = aa_env.get("AA_PORT")
    except Exception:
        v = os.environ.get("AA_PORT")
    try:
        return int(str(v).strip()) if v else 8765
    except (TypeError, ValueError):
        return 8765


PORT = _port()
URL = "http://127.0.0.1:%d/" % PORT


def _open_browser():
    webbrowser.open(URL)


def _setup_log_file():
    """运行日志落盘 aa_web_app/workbench.log(追加)——崩溃/异常 traceback 不再丢在 bat 窗口。

    2026-09-08: 划词翻译异常后工作台无痕退出, bat 控制台已关找不到原因; 此后 stdout/stderr
    全部重定向到文件(line-buffered, 强杀也保留已写行)。>5MB 滚动为 workbench.log.1。
    注意: 必须定义在调用(if __name__ 块)之前——曾定义在底部导致启动即 NameError。
    """
    log_path = os.path.join(_ROOT, "aa_web_app", "workbench.log")
    try:
        if os.path.exists(log_path) and os.path.getsize(log_path) > 5 * 1024 * 1024:
            os.replace(log_path, log_path + ".1")
        f = open(log_path, "a", encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        return          # 日志目录不可写 → 退回原控制台输出, 不阻塞启动
    sys.stdout = f
    sys.stderr = f
    import datetime
    print(f"[aa_web {datetime.datetime.now():%Y-%m-%d %H:%M:%S}] 运行日志落盘: {log_path}",
          file=f, flush=True)
    return f


if __name__ == "__main__":
    threading.Timer(1.5, _open_browser).start()
    _setup_log_file()
    print(f"AA 汉化矫正工作台: {URL}")
    try:
        uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
    except OSError as e:
        if getattr(e, "winerror", None) == 10048:
            print(f"端口 {PORT} 已被占用——工作台可能已在运行，直接打开浏览器访问 "
                  f"{URL} 即可（或先关闭旧实例再启动）。")
        raise
    except BaseException:
        # 兜底: SystemExit 等 BaseException 逃逸(曾致工作台无痕退出, traceback 只留在 bat 窗口)
        import traceback
        traceback.print_exc()
        raise
