# -*- coding: utf-8 -*-
"""平铺版 aa_translate 单例加载器（2026-08-18 去重后内部模块，勿直接 import）。

用途：common/aa_common.py 与 common/aa_api.py 两个薄壳必须转发到【同一份】平铺版实现，
否则会产生两份独立函数对象（is 判断不相等、mock 打不中）。本模块缓存单例。

关键设计：FLAT_MOD 必须是【标准名 'aa_translate'】的模块对象——即生产脚本
`import aa_translate as T` 拿到的那个。加载策略：
1. sys.modules['aa_translate'] 已有真平铺版（有 process_chapter/main 特征）→ 复用。
2. 已有但特征不符（薄壳 self-register 场景，scripts/common 在 path 前）→ 忽略它，
   按标准名重新从磁盘加载平铺版并【注册进 sys.modules】，确保后续 import 同对象。
3. 未加载 → 按标准名加载并注册。

⚠️ 薄壳自我循环防护：common/aa_translate.py 作为 'aa_translate' 被加载时（最坏场景），
本 loader 检测其无 process_chapter → 强制从磁盘重载真身并覆盖 sys.modules['aa_translate']。

用法（薄壳内）：
    from _flat_loader import FLAT_MOD
    globals().update({k: v for k, v in vars(FLAT_MOD).items() if not k.startswith('__')})
"""
import importlib.util, os, sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_SCRIPT_DIR)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

_FLAT_PATH = os.path.join(_SCRIPTS, "aa_translate.py")


def _is_real_flat(mod):
    """判断模块是否为平铺版真身（有 process_chapter/main 特征），而非薄壳。"""
    return mod is not None and hasattr(mod, "process_chapter") and hasattr(mod, "main")


_existing = sys.modules.get("aa_translate")
if _is_real_flat(_existing):
    FLAT_MOD = _existing
else:
    _SPEC = importlib.util.spec_from_file_location("aa_translate", _FLAT_PATH)
    FLAT_MOD = importlib.util.module_from_spec(_SPEC)
    sys.modules["aa_translate"] = FLAT_MOD
    _SPEC.loader.exec_module(FLAT_MOD)
