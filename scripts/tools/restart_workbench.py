# -*- coding: utf-8 -*-
"""触发「一键重启工作台.bat」——唯一允许的重启方式（杀旧 + 重启 8765 + 自动开浏览器）。

为什么必须用 `cmd /c start` 而不是 DETACHED_PROCESS：
    bat 里有多条控制台子命令（for/f 里的 netstat/findstr、taskkill、wmic、ping，
    最后是 python 起服务）。若触发时用 DETACHED_PROCESS|CREATE_NO_WINDOW 启动 cmd，
    则 **bat 自身没有控制台**，每条子命令都会被系统各分配一个新控制台窗口 ——
    本机默认终端是 Windows Terminal，表现为「重启时窗口连续闪烁」。
    实测（枚举 ConsoleWindowClass/CASCADIA_HOSTING_WINDOW_CLASS）：
        DETACHED：16 秒内新开 7 个终端窗口、其中 4 个寿命 <1s（= 闪烁）
        start   ：只开 1 个常驻窗口（服务窗口，子命令全部继承）
    手动双击 bat 之所以正常，是因为双击本身给了 bat 一个控制台。
    `start` 同时保证 bat 与 Hermes 终端的管道无关（bat 的子进程不持有本终端的
    stdout 管道），所以本命令立即返回、不会把调用方卡住。

用法（仓库根运行）：
    python scripts/tools/restart_workbench.py
"""
import subprocess
import sys
from pathlib import Path

REPO = str(Path(__file__).resolve().parents[2])
BAT = "一键重启工作台.bat"

CREATE_NO_WINDOW = 0x08000000        # 外层 cmd 自身不显示窗口（真正显窗口的是 start 起的 bat）
CREATE_NEW_PROCESS_GROUP = 0x00000200


def main() -> int:
    if not (Path(REPO) / BAT).exists():
        print("找不到 %s（应在仓库根 %s）" % (BAT, REPO), file=sys.stderr)
        return 1
    p = subprocess.Popen(
        ["cmd", "/c", "start", "", "/D", REPO, BAT],
        cwd=REPO,
        creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    print("已触发 %s（pid=%s）→ http://127.0.0.1:8765/ 启动需数秒" % (BAT, p.pid))
    return 0


if __name__ == "__main__":
    sys.exit(main())
