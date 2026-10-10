# -*- coding: utf-8 -*-
r"""路径中心化（2026-08-18 代码改进 Phase 3）——仓库根 BASE 的唯一动态来源。

背景：全仓 100+ 脚本硬编码 r"<repo>"，换机器/换盘全盘失效。
本文件用 __file__ 相对计算仓库根（3 级 dirname: scripts/works/paths.py → 仓库根），
仓库搬迁自动跟随。registry.py 的 BASE 亦从本文件导入，保证全局唯一。

用法：
    from paths import BASE            # 数据根（默认 = 仓库根，如 <repo>）
    OUT_DIR = os.path.join(BASE, "aaManga", "白头与染灰魔女", "汉化")

2026-10-08（方案 doc/方案归档/工作台设置页与配置文件方案_2026-10-08.md 阶段 5）：支持 `AA_BASE` 覆盖
——数据根可与代码根分离（打包给别人 / 数据放移动盘）。取值顺序：
真实环境变量 > `<仓库根>/.env` 的 AA_BASE > `__file__` 上跳 3 级（默认，行为不变）。
"""
import os
import sys

# 代码根：scripts/works/paths.py 上跳 3 级（永远可用，不依赖任何配置）
CODE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# .env 读取复用 scripts/common/aa_env.py（零依赖，不反向依赖本项目任何模块）。
# 失败（目录被裁剪/权限）时退回只用真实环境变量，绝不因此让 import 崩。
_ENV_BASE = os.environ.get("AA_BASE")
try:
    _COMMON = os.path.join(CODE_ROOT, "scripts", "common")
    if os.path.isdir(_COMMON) and _COMMON not in sys.path:
        sys.path.insert(0, _COMMON)
    if os.path.isfile(os.path.join(_COMMON, "aa_env.py")):
        import aa_env as _aa_env
        _ENV_BASE = _aa_env.get("AA_BASE") or _ENV_BASE
except Exception:
    pass

# 数据根（默认 = 代码根；AA_BASE 可指向别处）
BASE = os.path.abspath(_ENV_BASE) if _ENV_BASE else CODE_ROOT
# 数据根别名（2026-10-09）：aa_web_app 用它区分「代码根」(CODE_ROOT) 与「数据根」(BASE)。
# 源码运行时两者相同；冻结 exe 时数据根 = exe 所在目录（见发行包打包方案 §5）。
DATA_ROOT = BASE
