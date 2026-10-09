#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""候选池两段式 --full 自动续跑守护 (2026-09-07)。

gen_glossary_ai --full 的模型筛选任务在后台可能被外部机制每~2分钟回收(无 traceback
强杀, 已在 jinrou 多轮实测)。extract_full_pool 已做分批持久化(pool_progress.json,
续跑跳过完批)——本脚本循环调用 gen_glossary_ai --full, 直至 pool_progress 被清(*完成
标志)为止, 从而把偶发中断自动续完。

用法:
  python scripts/common/run_full_pool_until_done.py --work jinrou
完成后: glossary_candidates.json 生成(含筛出的专名候选 + 知识库待确认)。
"""
import os
import sys
import time
import subprocess

# Windows 下 stdout 被 main.py 重定向到 UTF-8 文件后, 默认仍按 GBK(locale) 编码——
# 打印 ✓ 等非 GBK 字符即崩 UnicodeEncodeError(bar_miku 候选任务曾在此收尾崩溃, 已产文件但 exit=1)。
# 显式 reconfigure, 与 main.py 的 open(encoding='utf-8') 一致。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_THIS = os.path.dirname(os.path.abspath(__file__))   # scripts/common
_BASE = os.path.dirname(os.path.dirname(_THIS))      # 仓库根
sys.path.insert(0, os.path.join(_BASE, "scripts"))
sys.path.insert(0, os.path.join(_BASE, "scripts", "works"))
sys.path.insert(0, os.path.join(_BASE, "scripts", "common"))
import registry
from aa_spawn import py_prefix              # 起子任务统一前缀（冻结 exe 走 --cli）

DONE = False


def _is_done(work):
    """progress 文件不存在 = 上一轮全跑完(essays末尾清理) 或失败; 存在=还在积累中。
    用存在性判断"是否还要跑"不可靠(失败也无 progress)→ 改为: 轮询返回码 + progress。"""
    # 简单: 本轮 gen_glossary_ai 退出码 0 且 无 pool_progress → 完成
    # 该判断由调用方做, 这里只给 work work_dir
    cfg = registry.get(work)
    return os.path.join(cfg["work_dir"], "pool_progress.json")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="自动续跑候选池全量筛选直至完成")
    ap.add_argument("--work", required=True)
    ap.add_argument("--max-rounds", type=int, default=15, help="最大续跑轮数(防死循环)")
    args = ap.parse_args()

    cfg = registry.get(args.work)
    prog_p = os.path.join(cfg["work_dir"], "pool_progress.json")
    script = os.path.join(_BASE, "scripts", "common", "gen_glossary_ai.py")

    # ---- 阶段 0: 补台词(独立, 不受模型筛选中断影响) ----
    # 进候选筛选前先确保全部原文《_台词.txt》齐全。补台词本身也可能被后台回收
    # (~2min 无 traceback 强杀), 故也做续跑: 每轮 `--ensure-dialogues-only` 幂等补齐缺失章,
    # 返回 0=全齐进入筛选, 9=仍有缺则 sleep 续跑, 直到全齐。
    print("===== 阶段0: 确保全部原文台词=《_台词.txt》齐全 =====", flush=True)
    for r0 in range(1, args.max_rounds + 1):
        r = subprocess.run(py_prefix() + [script, "--work", args.work, "--ensure-dialogues-only"],
                           cwd=_BASE)
        if r.returncode == 0:
            print("[run_pool] 补台词阶段完成 ✓ 进入候选筛选", flush=True)
            break
        print(f"[run_pool] 补台词轮{r0} exit={r.returncode}(仍有缺), 续跑", flush=True)
        time.sleep(1)
    else:
        print("[run_pool] 补台词达最大轮数, 仍继续候选筛选(以已有台词为准)", flush=True)

    first = True
    last_rc = None
    for rnd in range(1, args.max_rounds + 1):
        print(f"\n===== 续跑轮 {rnd}/{args.max_rounds} =====", flush=True)
        # 首次无条件跑; 后续: progress 还在 → 续跑; 不在了则要**分清**「跑完」与「崩了」
        if not first and not os.path.exists(prog_p):
            if last_rc not in (0, None):
                # 上一轮崩了(非 0 退出)且没留下进度文件 = 失败，不是「已完成」。
                # 旧实现在这里打印「已完成」并 return 0，把子进程的崩溃吞掉：候选一条没生成，
                # 任务页/按钮却报成功（实测打包版缺 janome.tokenizer 时正是如此）。
                print(f"[run_pool] 上一轮 exit={last_rc} 且无进度文件 → 判定失败, 停(轮{rnd})", flush=True)
                return last_rc
            print(f"[run_pool] progress 不存在且上一轮 exit=0 → 已完成, 停(轮{rnd})", flush=True)
            return 0
        first = False
        r = subprocess.run(py_prefix() + [script, "--work", args.work, "--full"],
                           cwd=_BASE)
        last_rc = r.returncode
        # exit0 且 progress 清 → 完成
        if r.returncode == 0 and not os.path.exists(prog_p):
            print(f"[run_pool] --full 全量完成 ✓ (轮{rnd})", flush=True)
            return 0
        why = "progress 仍在" if os.path.exists(prog_p) else "无 progress 文件"
        print(f"[run_pool] 轮{rnd} exit={r.returncode}, {why} → 续跑", flush=True)
        time.sleep(1)
    print("[run_pool] 达最大轮数")
    return 1                       # 跑满轮数仍未完成 = 失败（旧实现 return 0 会把失败报成成功）


if __name__ == "__main__":
    sys.exit(main())
