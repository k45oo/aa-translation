# -*- coding: utf-8 -*-
"""作品管线归属（方案 D10/D11）：决定该作品走通用链还是专属链。

权威源 = `aa_web_app/data/work_meta.json` 的 `pipeline` 字段（工作台作品详情页可改，
改动时红字提示"会切换翻译链"）；registry 条目可放默认值兜底；都没有 = `generic`。

外部覆盖：env `AA_PIPELINE=<值>`（跳过翻译链做单点调试用）。

用法：
    from work_pipeline import pipeline_of, is_pokemon
    pipeline_of("この素晴らしいシンオウに祝福を")   # -> "pokemon"
    is_pokemon(work)                                # -> True/False
"""
import json
import os

GENERIC = "generic"
POKEMON = "pokemon"

_META_PATHS = (
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "aa_web_app", "data", "work_meta.json"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "aa_web_app", "data", "work_meta.json"),
)


def _work_meta(work):
    for p in _META_PATHS:
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            e = d.get(work)
            if isinstance(e, dict):
                return e
        except (OSError, ValueError):
            continue
    return {}


def pipeline_of(work):
    """work_meta.pipeline > registry.pipeline > generic。"""
    env = os.environ.get("AA_PIPELINE", "").strip()
    if env:
        return env
    if not work:
        return GENERIC
    v = (_work_meta(work).get("pipeline") or "").strip()
    if v:
        return v
    try:
        import sys
        _w = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "works")
        if _w not in sys.path:
            sys.path.insert(0, _w)
        import registry
        v = (registry.get(work) or {}).get("pipeline") or ""
    except (Exception, SystemExit):
        v = ""      # registry.get 对未知作品 raise SystemExit（BaseException），必须一起吞
    return (v or "").strip() or GENERIC


def is_pokemon(work):
    return pipeline_of(work) == POKEMON


def owner_process():
    """当前进程是否是专属 runner 交给下级通用脚本的"内部交接"（见 D10 硬拦豁免）。"""
    return os.environ.get("AA_PIPELINE_OWNER", "").strip()
