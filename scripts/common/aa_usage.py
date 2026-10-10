# -*- coding: utf-8 -*-
"""usage.jsonl 大小限制 + 滚动（2026-08-31）。

背景: usage.jsonl 持续增长(实测 12MB+), 任务管理器的 _usage_index 全量扫会卡死系统。
除读取侧缓存外, 写入侧也限大小——超限滚动生成新文件, 旧文件归档 usage.jsonl.1/.2/.3。

写入点统一走本模块的 append_usage()（aa_backend._log_usage / vision_classify._log_usage_vision）:
    - 写入前检查主文件大小, 超 MAX_BYTES 则滚动（.3 删 → .2→.3 → .1→.2 → 主→.1）
    - 读侧 iter_usage_files() 返回主+全部滚动文件（统计工具合并用）

滚动后任务页实时聚合(读主文件)会自动重建缓存——旧文件属于已完成任务, 任务页约数可接受。
"""
import json
import os
import time

# 主文件大小阈值（默认 10MB）。可用 AA_USAGE_MAX_BYTES 覆盖。
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
# 保留滚动份数（.1 .2 .3）
KEEP = 3

# 进程内写锁（2026-09-15）：段级并发校对下多线程同时 append/rotate 需串行
import threading as _threading

_APPEND_LOCK = _threading.Lock()


def usage_file():
    """主 usage 文件路径（与 aa_config.usage_file 同口径，避免循环依赖）。"""
    try:
        import aa_config
        return aa_config.usage_file()
    except Exception:
        env = os.environ.get("AA_USAGE_FILE")
        if env:
            return env
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "usage.jsonl")


def max_bytes():
    try:
        return int(os.environ.get("AA_USAGE_MAX_BYTES") or DEFAULT_MAX_BYTES)
    except ValueError:
        return DEFAULT_MAX_BYTES


def rotate_if_needed():
    """主文件超限 → 滚动：.KEEP 删，其余后移，主文件改 .1，新主文件由下次写入创建。

    返回 True 表示发生了滚动。"""
    uf = usage_file()
    try:
        if not os.path.exists(uf) or os.path.getsize(uf) <= max_bytes():
            return False
    except OSError:
        return False
    try:
        # 删最旧
        oldest = f"{uf}.{KEEP}"
        if os.path.exists(oldest):
            os.remove(oldest)
        # 后移
        for i in range(KEEP - 1, 0, -1):
            src = f"{uf}.{i}"
            dst = f"{uf}.{i + 1}"
            if os.path.exists(src):
                if os.path.exists(dst):
                    os.remove(dst)
                os.rename(src, dst)
        # 主 → .1
        if os.path.exists(f"{uf}.1"):
            os.remove(f"{uf}.1")
        os.rename(uf, f"{uf}.1")
        return True
    except OSError:
        return False


def append_usage(rec):
    """写一条 usage 记录（写入前滚动检查）。rec 为 dict。失败静默。

    2026-09-15: 加进程内锁 —— refine_aa 校对已改多线程(段级并发 8), 并发 append 与
    rotate_if_needed 的「检查→滚动→写」必须串行, 否则可能双滚动/交错写坏行。"""
    try:
        with _APPEND_LOCK:
            rotate_if_needed()
            uf = usage_file()
            with open(uf, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def iter_usage_files():
    """主文件 + 滚动文件（.1 .2 .3），按新旧顺序（主最新）。不存在则空。"""
    uf = usage_file()
    files = [uf]
    for i in range(1, KEEP + 1):
        files.append(f"{uf}.{i}")
    return [p for p in files if os.path.exists(p)]
