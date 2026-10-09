# -*- coding: utf-8 -*-
"""usage 记录 → 成本折算（2026-09-15 成本口径 P0）。

口径见 doc/方案归档/token成本优化方案_2026-09-15.md：
  · 单价来源 = `scripts/config/model_config.toml [pricing]`（aa_config.pricing_for 查表）
  · **缺失单价的 token 只计数不折算金额**（绝不猜价）；返回的 unpriced 让调用方标注覆盖率
  · 记录含 `cache_hit`（2026-09-15 起落盘）时按「命中价×命中 + 未命中价×其余」；
    无该字段的历史记录 → 全部按未命中价计，故其金额是**上界**
  · `peak` 倍率只对声明了 peak 的单价生效（DeepSeek 官网工作日高峰 ×2，周末不翻倍）
  · 2026-09-29 `off`/`off_windows` = **折扣窗口**（小米 token plan 每日 00:00-08:00 系数 0.8×，不看周末）；
    两者都按**记录时间戳**折算（`time_mult`），故历史报表与预估口径一致

本模块只做纯计算（无 IO，除读配置），便于单测与后续接入任务页/报表。
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aa_config as _CFG


def _parse_windows(raw):
    """["HH:MM-HH:MM", ...] → [(起始分钟, 结束分钟)]（解析失败即忽略）。"""
    out = []
    for w in raw or []:
        m = re.match(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$", str(w).strip())
        if m:
            h1, m1, h2, m2 = (int(x) for x in m.groups())
            out.append((h1 * 60 + m1, h2 * 60 + m2))
    return out


def peak_windows():
    """[pricing].windows ("HH:MM-HH:MM") → [(起始分钟, 结束分钟)]（解析失败即忽略）。"""
    return _parse_windows(_CFG.pricing().get("windows", []))


def off_windows(rate=None):
    """条目自带的折扣窗口（`rate["off_windows"]`，如小米 token plan 00:00-08:00）。

    2026-09-29：与 peak 相反方向——**不看周末**（官网 token plan 是「每日」），系数在 `rate["off"]`。
    """
    return _parse_windows((rate or {}).get("off_windows", []))


def is_peak(ts, windows=None):
    """ts 是否落在高峰时段（周末不算；windows 为空 → 永远 False）。"""
    wins = peak_windows() if windows is None else windows
    if not wins:
        return False
    lt = time.localtime(ts or 0)
    if lt.tm_wday >= 5:
        return False
    mins = lt.tm_hour * 60 + lt.tm_min
    return any(s <= mins < e for s, e in wins)


def is_off(ts, rate):
    """ts 是否落在该条目的折扣窗口内（每日，不看周末；未声明 off/off_windows → False）。"""
    wins = off_windows(rate)
    if not wins or rate.get("off") is None:
        return False
    lt = time.localtime(ts or 0)
    mins = lt.tm_hour * 60 + lt.tm_min
    return any(s <= mins < e for s, e in wins)


def time_mult(ts, rate):
    """时间倍率 = 高峰倍率 × 折扣系数（按**记录时间戳**折算，不是按"现在几点"）。

    未声明字段的一律 1.0。两个窗口不重叠时（如 deepseek 的 09-12/14-18 vs 小米的 00-08）只会生效一个。
    """
    if not rate:
        return 1.0
    mult = 1.0
    if rate.get("peak") is not None and is_peak(ts):
        mult *= float(rate.get("peak") or 1.0)
    if is_off(ts, rate):
        mult *= float(rate.get("off") or 1.0)
    return mult


def cost_of(rec):
    """单条 usage 记录 → (金额元, 未定价 token 数, 是否用了近似/未查证价)。

    未定价 token = 该记录中单价缺失的那部分 token（金额里没有它的贡献）。
    """
    backend, model = rec.get("backend"), rec.get("model")
    rate, _key = _CFG.pricing_for(backend, model)
    prompt = rec.get("prompt", 0) or 0
    completion = rec.get("completion", 0) or 0
    hit = rec.get("cache_hit")
    if not rate:
        return 0.0, prompt + completion, True
    mult = time_mult(rec.get("ts", 0), rate)
    cost = 0.0
    unpriced = 0
    miss_tok = (prompt - (hit or 0)) if hit is not None else prompt
    if "miss" in rate:
        cost += max(miss_tok, 0) * rate["miss"]
    else:
        unpriced += max(miss_tok, 0)
    if hit is not None:
        if "hit" in rate:
            cost += hit * rate["hit"]
        else:
            unpriced += hit
    if "out" in rate:
        cost += completion * rate["out"]
    else:
        unpriced += completion
    src = rate.get("src") or ""
    approx = ("近似" in src) or ("未查证" in src)
    return cost * mult / 1e6, unpriced, approx


def format_yuan(v):
    """金额显示（小于 1 分显示 4 位小数，便于看小批次的量级）。"""
    return f"{v:.4f}" if 0 < v < 0.01 else f"{v:.2f}"
