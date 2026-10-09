# -*- coding: utf-8 -*-
"""测试用假 runner：心跳 → 循环检测停止标志 → 见停止即 finalize(41) 退出；超时 exit 0。
验证 task_manager 与 task_report 的端到端协议。被 test_task_manager.TestE2E 以子进程调用。"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))
import task_report as TR

TR.heartbeat(phase="测试", force=True)
t0 = time.time()
while time.time() - t0 < 3.0:
    mode = TR.check_stop()
    if mode:
        TR.finalize(TR.RC_STOPPED)
        sys.exit(TR.RC_STOPPED)
    time.sleep(0.05)
TR.finalize(0)
sys.exit(0)
