# -*- coding: utf-8 -*-
"""试点: 视觉模型辅助 R18 判定 (sujin_shabu 6 个 suspect 章)

流程: 渲染每章块图(temp/) → 每块调 Qwen3-VL 判定是否 R18 场景 → 汇总
输出: 每章判定 (yes/no/suspect) + 视觉理由; 与词表 suspect 对比, 验证视觉辅助价值。
用法: python scripts/tools/r18_vision_pilot.py
"""
import sys, os, json, subprocess, base64, glob, re

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(BASE, "scripts"))
sys.path.insert(0, os.path.join(BASE, "scripts", "common"))
sys.path.insert(0, os.path.join(BASE, "scripts", "works"))
import registry
import aa_backend as AB

WORK = "sujin_shabu"
OUT = os.path.join(BASE, "temp", "r18_vision_pilot")
SUSPECT = ["entry_10293", "entry_36133", "entry_36161", "entry_36176", "entry_8852", "entry_8920"]

SYSTEM = "你是 AA 漫画(2ch字符画)内容分析助手。判断画面/台词是否包含露骨的性行为或R18成人场景。"
PROMPT = ("这张AA漫画画面(可能含人物图+台词)是否包含露骨的性行为/R18成人场景？\n"
          "只回答一行 JSON: {\"r18\": \"yes\"|\"no\"|\"suspect\", \"reason\": \"简短中文理由\"}\n"
          "yes=明确性行为/R18场景; suspect=有暗示/擦边但不确定; no=无R18内容。")


def render_chapter(base):
    """渲染单章块图到 temp/（原文渲染——sujin_shabu 未汉化, 直接读 aa/ 原文）。
    复用 aa_to_images 的渲染函数链: split_blocks → merge_posts → render_block。"""
    import importlib
    sys.path.insert(0, os.path.join(BASE, "scripts", "tools"))
    import aa_to_images as A
    cfg = registry.get(WORK)
    site = importlib.import_module("sites." + cfg["site"])
    font_path = A.pick_font()
    # 读原文（aa/ 目录, 无汉化）
    src = os.path.join(cfg["aa_dir"], base + ".txt")
    if not os.path.exists(src):
        print(f"  原文不存在: {src}")
        return []
    lines = open(src, encoding="utf-8").read().split("\n")
    cm = {}
    cmf = os.path.join(cfg["aa_dir"], base + "_colormap.json")
    if os.path.exists(cmf):
        cm = json.load(open(cmf, encoding="utf-8"))
    rows = [(i, l) for i, l in enumerate(lines) if not l.startswith("#")]
    blocks = A.merge_posts(A.split_long(A.split_blocks(rows, site)), site=site)
    d = os.path.join(OUT, base)
    os.makedirs(d, exist_ok=True)
    for f in glob.glob(os.path.join(d, "*.png")):
        os.remove(f)
    imgs = []
    for bi, block in enumerate(blocks, 1):
        img = A.render_block(block, site, cm, A.MIN_FS, 720, font_path, min_width=0,
                             text_bonus=2)
        p = os.path.join(d, f"{bi:04d}.png")
        img.save(p)
        imgs.append(p)
    print(f"  渲染 {len(imgs)} 块 → {d}")
    return imgs


def judge_block(img_path):
    """单块图视觉判定。返回 (r18, reason)。"""
    b64 = base64.b64encode(open(img_path, "rb").read()).decode()
    try:
        txt = AB.call_qwen_vl(SYSTEM, PROMPT, image_b64=b64, max_tokens=200, timeout=120)
    except Exception as e:
        return "err", str(e)[:80]
    m = re.search(r'\{[^}]*"r18"\s*:\s*"(\w+)"[^}]*\}', txt)
    if m:
        reason = re.search(r'"reason"\s*:\s*"([^"]*)"', txt)
        return m.group(1), (reason.group(1) if reason else txt[:40])
    return "parse", txt[:60]


def main():
    os.makedirs(OUT, exist_ok=True)
    print(f"=== 视觉试点: {WORK} {len(SUSPECT)} 个 suspect 章 ===")
    for base in SUSPECT:
        imgs = render_chapter(base)
        if not imgs:
            print(f"{base}: 渲染 0 块, 跳过")
            continue
        print(f"\n{base}: {len(imgs)} 块")
        votes = {"yes": 0, "no": 0, "suspect": 0, "err": 0}
        reasons = []
        # 抽样: 每章最多 8 块(前4+后4), 控制成本
        sample = imgs[:4] + imgs[-4:] if len(imgs) > 8 else imgs
        for img in sample:
            v, reason = judge_block(img)
            votes[v] = votes.get(v, 0) + 1
            reasons.append(f"{os.path.basename(img)}: {v} — {reason}")
        total_voted = sum(v for k, v in votes.items() if k != "err")
        r18_ratio = (votes["yes"] + votes["suspect"]) / max(total_voted, 1)
        verdict = "yes" if r18_ratio >= 0.5 else ("no" if r18_ratio <= 0.25 else "suspect")
        print(f"  投票: {votes}  R18占比: {r18_ratio:.0%}  → {verdict.upper()}")
        for r_ in reasons[:6]:
            print(f"    {r_}")
        # 写结论
        with open(os.path.join(OUT, base + "_verdict.json"), "w", encoding="utf-8") as f:
            json.dump({"base": base, "votes": votes, "ratio": r18_ratio,
                       "verdict": verdict, "reasons": reasons}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
