# -*- coding: utf-8 -*-
"""runner 共享日志模块（2026-08-21）：双写 stdout + 文件，按时间戳归档旧日志。

用法：
    import _log
    _log.init(work_dir, log_file=None)      # 默认 <work_dir>/runner_log.txt
    log = _log.make_log()                    # 返回 log(msg)=print+写文件
    # 或直接 _log.write(msg)

归档规则：每次 init 时把已存在的活跃日志重命名为
  runner_log_YYYYmmdd_HHMMSS.txt（保留历史，不覆盖）。
子进程（dl_runner / aa_translate / pass 等）由最外层 runner 的 log 承载。
"""
import os
import sys
import time

# 2026-08-30: Windows 控制台 stdout 默认 GBK，日志含 \ufffd（UTF-8 解码失败的替换字符）
# 时 print 崩 UnicodeEncodeError——强制 stdout/stderr 用 UTF-8 + errors=replace
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_ACTIVE = None      # io handle


def init(work_dir, log_file=None):
    """初始化日志：归档旧活跃日志，打开新日志。返回日志路径。
    log_file="none" = 不写文件（只 stdout），供嵌套调用（translate_runner 内部
    调 dl_runner 时避免覆盖外层日志）。"""
    global _ACTIVE
    if _ACTIVE:
        close()
    if log_file == "none":
        _ACTIVE = None
        return None
    os.makedirs(work_dir, exist_ok=True)
    path = log_file or os.path.join(work_dir, "runner_log.txt")
    if os.path.exists(path):
        try:
            ts = time.strftime("%Y%m%d_%H%M%S")
            archive = os.path.join(os.path.dirname(path) or ".",
                                   "runner_log_%s.txt" % ts)
            if path != archive:
                os.rename(path, archive)
        except OSError:
            pass  # 归档失败不阻塞（文件可能正被占用）
    fh = open(path, "a", encoding="utf-8", errors="replace")
    _ACTIVE = fh
    return path


def close():
    global _ACTIVE
    if _ACTIVE:
        try:
            _ACTIVE.close()
        except OSError:
            pass
        _ACTIVE = None


def write(msg=""):
    """写一行到日志文件（stdout 由调用方 log 打）。"""
    if not _ACTIVE:
        return
    try:
        _ACTIVE.write(msg + "\n")
        _ACTIVE.flush()
    except OSError:
        pass


def make_log():
    """返回 log(msg) —— print 到 stdout + 写日志文件。"""
    def log(msg, _write=write):
        print(msg, flush=True)
        _write(str(msg))
    return log
