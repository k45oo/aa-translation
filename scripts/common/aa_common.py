# -*- coding: utf-8 -*-
"""共享基础层薄壳（2026-08-18 去重后）——唯一权威实现是 scripts/aa_translate.py。

背景：common/aa_common.py 与平铺版 aa_translate.py 曾是两套独立实现（KNOWN_NAMES/manual_fix/
disp_width 等重复）。2026-08-18 代码改进 Phase 1 反向合并：以平铺版为唯一实现，
本文件仅 re-export 平铺版，保证 `import aa_common as C`（common/pass2、pass3、fix_width、
verify/scan_width、tools/line_classify* 等）拿到的是同一份代码。

用法：
    import aa_common as C          # 全量符号 = 平铺版 aa_translate
    C.disp_width / C.kana_runs / C.residual_suspect / C.load_segments_meta ...
"""
import sys

_SCRIPT_DIR = __import__('os').path.dirname(__import__('os').path.abspath(__file__))
_SCRIPTS = __import__('os').path.dirname(_SCRIPT_DIR)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from _flat_loader import FLAT_MOD

# 全量转发（含以下划线开头的内部符号，供旧脚本 import）
globals().update({k: v for k, v in vars(FLAT_MOD).items() if not k.startswith("__")})
