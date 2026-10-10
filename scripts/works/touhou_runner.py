# -*- coding: utf-8 -*-
"""touhou_teisou（やる夫のねっとり貞操逆転世界）全自动编排：

等翻译批次 → pass2 → pass3（残留清理） → scan_width + fix_width。
（2026-10-06 起不再自动生成合集 HTML——需要时手动
 `python scripts/common/gen_aa_html.py --work touhou_teisou`。）

用法：python scripts/works/touhou_runner.py [--skip-wait]
"""
import os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根(2026-08-18 Phase 3 动态计算)

def log(msg):
    print(msg, flush=True)

def wait_batch(tr_dir, expect, timeout=36000):
    t0 = time.time()
    while time.time() - t0 < timeout:
        done = [f for f in os.listdir(tr_dir) if f.endswith('_汉化.txt')]
        if len(done) >= expect:
            return True
        time.sleep(60)
    return False

def main():
    _here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(_here, "..", "common"))
    TR_DIR = os.path.join(ROOT, "aaManga", "やる夫のねっとり貞操逆転世界", "汉化")
    AA_DIR = os.path.join(ROOT, "aaManga", "やる夫のねっとり貞操逆転世界", "aa")
    skip_wait = "--skip-wait" in sys.argv[1:]

    expect = len([f for f in os.listdir(AA_DIR) if f.endswith('.txt')])
    if not skip_wait:
        log(f"=== 等待翻译批次 ({expect} 话) ===")
        if not wait_batch(TR_DIR, expect):
            log("TIMEOUT waiting batch")
            return 1
    done = len([f for f in os.listdir(TR_DIR) if f.endswith('_汉化.txt')])
    log(f"翻译完成: {done} 话")

    import pass2 as P2
    import pass3 as P3
    import scan_width as SW
    import fix_width as FW

    log("=== pass2 漏译清理 ===")
    P2.main("touhou_teisou")
    log("=== pass3 残留清理 ===")
    P3.main("touhou_teisou")
    log("=== scan_width 宽度校验 ===")
    SW.main("touhou_teisou")
    log("=== fix_width 宽度修复 ===")
    FW.main("touhou_teisou")
    log("=== 重新校验宽度 ===")
    SW.main("touhou_teisou")

    log("=== ALL COMPLETE（合集手动生成: common/gen_aa_html.py --work touhou_teisou）===")
    return 0

if __name__ == "__main__":
    sys.exit(main())
