# -*- coding: utf-8 -*-
"""runner 任务心跳/停止协议（2026-08-29 统一任务管理器配套，方案见 doc/方案归档/翻译进程管理方案_2026-08-29.md）。

管理器（aa_web_app/task_manager.py）启动 runner 子进程时注入 env：
    AA_TASK_ID            任务 id
    AA_TASK_HB            心跳文件路径（runner 所有，sidecar 不与任务文件争写）
    AA_STOP_CHAPTER_FILE  停止标志：译完本章后停（章节/阶段边界检测）
    AA_STOP_ASAP_FILE     停止标志：尽快停（块边界检测，写 ckpt 后退出）

runner 侧用法：
    import task_report as TR
    TR.heartbeat(phase="翻译", idx=3, total=14)     # 周期心跳（1s 节流，阶段变化强制写）
    mode = TR.check_stop()                          # "asap"|"chapter"|None（见到即删标志）
    # 停止时：落盘当前进度 → sys.exit(TR.RC_STOPPED)
    TR.finalize(rc)                                 # 退出前写终态 rc（管理器接管判定用）

未设 AA_TASK_HB（直接跑 runner 不经管理器）时全部 no-op，零侵入。
"""


class TaskStoppedError(RuntimeError):
    """块边界停止：当前块已写 ckpt，章节不完整退出（下次 ckpt 续跑）。"""


RC_STOPPED = 41

import json
import os
import time

_HB = None              # 心跳文件路径
_STOP_FILES = None      # {"chapter": path, "asap": path}
_last_write = 0.0
_last_phase = None
_THROTTLE = 1.0


def _init():
    global _HB, _STOP_FILES
    if _HB is None:
        hb = os.environ.get("AA_TASK_HB")
        _HB = hb or ""
        _STOP_FILES = {
            "chapter": os.environ.get("AA_STOP_CHAPTER_FILE") or "",
            "asap": os.environ.get("AA_STOP_ASAP_FILE") or "",
        }


def enabled():
    _init()
    return bool(_HB)


def heartbeat(phase=None, idx=None, total=None, detail=None, force=False):
    """写心跳 sidecar（原子写）。1s 节流；phase 变化/force 立即写。"""
    global _last_write, _last_phase
    _init()
    if not _HB:
        return
    now = time.time()
    if not force and phase == _last_phase and now - _last_write < _THROTTLE:
        return
    rec = {"task": os.environ.get("AA_TASK_ID"), "hb_ts": now,
           "phase": phase, "chapter_idx": idx, "chapter_total": total, "detail": detail}
    try:
        tmp = _HB + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
        os.replace(tmp, _HB)
        _last_write = now
        if phase is not None:
            _last_phase = phase
    except Exception:
        pass


def check_stop(only=None):
    """检测停止标志（asap 优先），见到即删。only="asap" 时只检测/消费 asap
    （块边界循环用——chapter 标志留给章节边界检测，防误消费）。返回 "asap"|"chapter"|None。"""
    _init()
    if not _HB:
        return None
    modes = (only,) if only else ("asap", "chapter")
    for mode in modes:
        p = _STOP_FILES.get(mode)
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
            return mode
    return None


def peek_stop(only=None):
    """只读检测停止标志(不消费)。内层阶段(如简介生成循环)提前退出用它——标志必须
    留给外层章节/块边界消费, 否则外层收不到停止请求会继续往下跑。返回 "asap"|"chapter"|None。"""
    _init()
    if not _HB:
        return None
    modes = (only,) if only else ("asap", "chapter")
    for mode in modes:
        p = _STOP_FILES.get(mode)
        if p and os.path.exists(p):
            return mode
    return None


def finalize(rc):
    """退出前写终态 rc 进心跳（管理器 reconcile：pid 死 + 有 rc → 按 rc 定终态）。"""
    _init()
    if not _HB:
        return
    try:
        rec = {}
        if os.path.exists(_HB):
            try:
                rec = json.load(open(_HB, encoding="utf-8"))
            except Exception:
                rec = {}
        rec["rc"] = rc
        rec["hb_ts"] = time.time()
        tmp = _HB + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
        os.replace(tmp, _HB)
    except Exception:
        pass
