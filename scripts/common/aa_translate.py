# -*- coding: utf-8 -*-
"""统一翻译入口（薄壳，2026-08-11 合并后；2026-08-18 去重统一走 _flat_loader）——实际实现全部在 scripts/aa_translate.py。

背景：平铺版(scripts/aa_translate.py) 与 common 版(scripts/common/aa_translate.py) 曾为两套
独立实现，M0-M5 优化只落其中一套导致「优化不生效」事故。现已合并为单一实现：
- 本文件仅 re-export 平铺版，保证 `from aa_translate import main`（works/translate_*）
  与 `python common/aa_translate.py --work <id>` 两条入口走同一份代码。
- 平铺版 main() 已支持 --work registry 模式 + 章节级并行 + meta 缺失自动生成。
- 注意：不能用 `from aa_translate import *` 转发（本文件注册为 'aa_translate' 会自我循环），
  必须用 _flat_loader 的单例（它就是 sys.modules['aa_translate']，与直接 import 同对象）。

用法：
    python common/aa_translate.py --work <id> [--pilot eid,eid...]   # registry 模式（推荐）
    python common/aa_translate.py <id>                               # 兼容：裸参数 = 作品 id
    python common/aa_translate.py                                    # 缺省 = baitou
"""
import os, sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, os.path.join(_SCRIPTS, "common"))   # aa_common/aa_api（供平铺版内部依赖）
sys.path.insert(0, os.path.join(_SCRIPTS, "works"))    # registry
sys.path.insert(0, _SCRIPTS)                           # scripts/（tools 等）

from _flat_loader import FLAT_MOD

# 全量转发（含以下划线开头的内部符号，供旧脚本 import）
globals().update({k: v for k, v in vars(FLAT_MOD).items() if not k.startswith("__")})


def main(work=None):
    """兼容旧签名：main('baitou') 或 main()；--work 由平铺版 main() 解析。"""
    if work is not None:
        keep = [a for a in sys.argv[1:]
                if a.startswith("--pilot") or (a not in ("--work", work) and not a.isdigit())]
        sys.argv = [sys.argv[0], "--work", work] + keep
    FLAT_MOD.main()


if __name__ == "__main__":
    main()
