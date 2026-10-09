# -*- coding: utf-8 -*-
"""测试的作品数据守卫（2026-10-09 加，发行包需要）。

有些用例跑的是**生成者本机的真实作品数据**（fukou 的 24 章交付、kikkoman 的作品术语表…）。
这些数据不随发行包走（包里只有 1 部示例作品），因此：

* 数据缺失时必须 `unittest.SkipTest`（跳过是预期行为，不是失败）；
* **绝不能让它抛 `SystemExit`** —— `registry.get('未注册的 id')` 就是 SystemExit，
  而 SystemExit 继承 BaseException，unittest 不接：它会把整个测试 run 当场终止，
  使用者看到的是"包里的测试跑不起来"，正是发行包第一次实测踩到的坑。

用法：`cfg = work_cfg("fukou", ("out_dir", "work_dir"))`（缺数据自动跳过本用例）。
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))


def _ensure_paths():
    root = os.path.dirname(os.path.dirname(_HERE))
    for p in (os.path.join(root, "scripts", "works"), os.path.join(root, "scripts"),
              os.path.join(root, "scripts", "common")):
        if p not in sys.path:
            sys.path.insert(0, p)


def work_cfg(work, need_dirs=("aa_dir",), why="需要该作品的本地数据"):
    """取作品配置；未注册或所需目录不存在 → `SkipTest`。返回配置 dict（可安全临时改 html_out）。"""
    _ensure_paths()
    import registry
    try:
        cfg = registry.get(work)
    except (SystemExit, KeyError):
        raise unittest.SkipTest("%s：registry 里没有 %s（发行包只带示例作品）" % (why, work))
    for key in need_dirs:
        d = cfg.get(key)
        if d and not os.path.isdir(d):
            raise unittest.SkipTest("%s：%s 的 %s 不存在（%s）" % (why, work, key, d))
    return cfg


def any_work_cfg(why="需要任意一个已注册作品作模板"):
    """取任意一个已注册作品的配置（用例只把它当**模板**，随后会覆盖其目录）；一个都没有 → SkipTest。"""
    _ensure_paths()
    import registry
    for wid in registry.WORKS:
        return registry.WORKS[wid]
    raise unittest.SkipTest("%s：registry 里没有任何作品" % why)


# ---------------------------------------------------------------- 发行包跳过

MARKER_NAME = ".release_package"


def is_release_package():
    """当前是不是**发行包**（包根有 `.release_package` 标记；由 build_release.py 写入）。"""
    root = os.path.dirname(os.path.dirname(_HERE))          # tests/ → scripts/ → 仓库根
    return os.path.isfile(os.path.join(root, MARKER_NAME))


def repo_only(why):
    """装饰器：**发行包内跳过**该用例（仓库内照常运行）。

    用于断言"生成者完整模型链 / 全部作品已注册 / 完整术语表 / 本机兜底路径"的用例——
    发行包按方案裁过配置与数据（默认链换成公共端点、registry 只留示例作品、术语表为空），
    这些断言在包里必然不成立。跳过信息里写明原因，使用者一眼能看懂**为什么跳过**。

    可装饰类或方法：`@repo_only("断言生成者的 4 条付费链")`
    """
    return unittest.skipIf(is_release_package(), "发行包跳过：" + why)
