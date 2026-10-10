# -*- coding: utf-8 -*-
"""公共提示词前缀（2026-09-15 P2-b：让不同 system 变体互相吃前缀缓存）。

动机（实测）: refine_aa 的三种 system（MIXED/NORMAL/SWALLOW）与主翻译的
SYSTEM/SYSTEM_MIXED/FIX_MIXED_SYSTEM **从第 0 个 token 就分叉** → DeepSeek 前缀缓存的公共前缀≈0，
跨类型调用互相命中不了（生产实测 refine 命中率仅 36.9%，而单类型内可达 89.6%）。
把这段固定文本拼到**所有** system 最前面后，公共前缀 = 这段文本（≥64 token 起效、按 64-token 粒度对齐），
任意类型/任意调用之间都能吃到命中价（0.02 元/M vs 未命中 1.0 元/M）。

注意:
  · 必须拼在**最前**——前缀缓存自第 0 个 token 起逐字节匹配，拼在后面无效。
  · 改这段文字会让全库缓存失效一次（它是常量，改完只需重建一次）。
  · 内容只**重述各变体已有的共同要求**（专业汉化、保住绘画字符、不加戏、按格式逐行），
    不引入任何新策略，避免改变译文行为。
  · `AA_COMMON_PREFIX=0` 关闭（回到旧行为）；`apply(system, backend)` 只在会缓存的后端上拼。
"""
import os

COMMON_PREFIX = """【恒定前缀·任何任务都适用】你是やる夫 AA 漫画（2ch 字符画）的资深汉化翻译，任务是把给出的日语翻成简体中文。
恒定规则：
1. 只做日译简中，译文自然、贴切、符合漫画语气，不增删内容，不解释、不评论、不输出任何多余内容。
2. 绘画字符属于画面，必须原样保留、不可改动、不可翻译：全角空格 U+3000、边框与制表符（│｜／＼＿￣┌┐└┘ 等）、半角片假名（ﾉﾐﾑﾊﾞｯｽ 等）、ASCII 艺术字符。
3. 不要残留任何日文原文，也不要把译文追加在原文旁边——原文位置必须被译文完整替换。
4. 严格按要求的输出格式逐行输出：一行对应一条，不合并、不换序、不漏项、不加前后缀。
"""


def enabled():
    """是否启用公共前缀（env AA_COMMON_PREFIX=0 关闭）。"""
    return os.environ.get("AA_COMMON_PREFIX", "1") not in ("0", "false", "False")


def apply(system, backend=None):
    """把公共前缀拼到 system 最前。

    幂等（已拼过原样返回）；env `AA_COMMON_PREFIX=0` 全局关闭。
    backend 给出时**只有该后端会命中缓存才拼**（[backend.X].cache=true）——网关不缓存，
    拼了只是白加 ~200 token 的输入成本（实测 deepinfra/runinfra 同前缀命中 0/0/0）。
    """
    if not system or not enabled():
        return system
    if backend is not None:
        try:
            import aa_config
            if not aa_config.cache_safe(backend):
                return system
        except Exception:
            pass
    if system.startswith(COMMON_PREFIX[:20]):
        return system
    return COMMON_PREFIX + system


def current_cache_backend(work_r18=False):
    """本次调用大概率落在的「会缓存」的后端名；没有则 None（→ 不拼前缀）。

    规则与实际调度一致: 显式 AA_BACKEND 优先（非缓存后端则 None，尊重用户显式选择）；
    否则取配置链上第一个 [backend.X].cache=true 的端点（= pin 的目标）。
    """
    try:
        import aa_config as C
    except Exception:
        return None
    ex = os.environ.get("AA_BACKEND")
    if ex and ex.lower() not in ("cloud", "") and os.environ.get("AA_BACKEND_AUTO") not in ("1", "true", "True"):
        return ex if C.cache_safe(ex) else None
    try:
        _bottom = C.fallback_only_names(bool(work_r18))
    except Exception:
        _bottom = set()
    try:
        for b, _m in C.backend_chain(bool(work_r18)):
            if b in _bottom:
                continue          # 兜底端点(fallback_only)不作 pin 目标——与实际调度一致
            if C.cache_safe(b):
                return b
    except Exception:
        pass
    return None
