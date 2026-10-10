# -*- coding: utf-8 -*-
r"""自检：当前进程实际命中的**作品注册表**是哪一份、里面有哪几部作品。

为什么需要：冻结 exe 里 `registry.py` 有两份——解包目录里的（随包那部示例作品）与
**数据根**里的（使用者自己加的作品）。工作台起的子任务（下载/翻译/候选提取）必须命中**数据根那份**，
否则使用者一按按钮就报「未知作品: X（可用: baitou）」。实测踩过：`--cli` 分支跑在 registry 引导之前。

用法：
    python scripts/tools/check_registry.py [期望的作品 id]

退出码：0 = 正常（给了 id 则要求它在册）；1 = 期望的 id 不在册；2 = 导入失败。
"""

import os
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(os.path.dirname(here), "works"),
              os.path.join(os.path.dirname(here))):
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        import registry
    except Exception as e:                       # noqa: BLE001
        print("[check_registry] 导入 registry 失败: %s" % e)
        return 2
    works = list(registry.WORKS)
    print("[check_registry] registry.__file__ = %s" % getattr(registry, "__file__", "?"))
    print("[check_registry] BASE             = %s" % getattr(registry, "BASE", "?"))
    print("[check_registry] 作品 %d 部: %s" % (len(works), ", ".join(works[:8])))
    if argv:
        want = argv[0]
        if want in registry.WORKS:
            print("[check_registry] OK 找到期望作品: %s" % want)
            return 0
        print("[check_registry] 缺期望作品: %s" % want)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
