# -*- coding: utf-8 -*-
"""模型 API 层薄壳（2026-08-18 去重后）——唯一权威实现是 scripts/aa_translate.py。

背景：common/aa_api.py 与平铺版 aa_translate.py 曾是两套独立 call_api（后端分发/代理重试/
usage 落盘重复）。2026-08-18 代码改进 Phase 1 反向合并：以平铺版为唯一实现，
本文件仅 re-export 平铺版，保证 `import aa_api as A`（common/pass2、pass3、fix_width、
tools/translate_titles、tests/test_pipeline）与平铺版 `import aa_translate as T` 走同一份代码。

mock 语义（关键）：test_pipeline 等用 `A.call_api = fake` 覆盖。薄壳全量转发后
`A.call_api` 与 `A.translate_lines` 都指向 FLAT_MOD 对象；mock 请用
`A.FLAT_MOD.call_api = fake`（或直接 `import aa_translate as T; T.call_api = fake`）。
若确实需要 `A.call_api = fake` 语义，可对薄壳层赋值（会创建本地覆盖，不影响 FLAT_MOD
——此时 translate_lines 内部仍走 FLAT_MOD.call_api，需同步 mock A.FLAT_MOD.call_api）。

用法：
    import aa_api as A
    text = A.call_api(SYSTEM, body)
"""
import os, sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_SCRIPT_DIR)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from _flat_loader import FLAT_MOD

# 全量转发（含以下划线开头的内部符号，供旧脚本 import）
globals().update({k: v for k, v in vars(FLAT_MOD).items() if not k.startswith("__")})

# 显式暴露 FLAT_MOD，供 mock（A.FLAT_MOD.call_api = fake）与诊断
FLAT_MOD = FLAT_MOD


def record_usage(resp, elapsed):
    """兼容旧 common 版签名：从响应提取 usage 追加 JSONL。

    平铺版 call_api 已在内部调用 _log_usage 落盘（带 ts），此处保留同名函数供
    旧调用方不炸。resp 无 usage 字段时静默跳过。
    """
    u = resp.get("usage") or {}
    inp = u.get("input_tokens") or u.get("prompt_tokens") or 0
    out = u.get("output_tokens") or u.get("completion_tokens") or 0
    if not (inp or out):
        return
    FLAT_MOD._log_usage(inp, out)


def __getattr__(name):
    """PEP 562 兜底：平铺版新增的属性自动可访问。"""
    return getattr(FLAT_MOD, name)
