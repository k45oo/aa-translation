# -*- coding: utf-8 -*-
"""章节标题翻译器：扫描作品全部章节唯一标题 → 批量翻译 → <work_dir>/title_translations.json 缓存。

用法: python scripts/tools/translate_titles.py --work <id> [--force]
- 去重翻译（同一标题只译一次），已有缓存的标题跳过（幂等）
- 检测无假名的标题（已是中文/纯数字）直接原样入缓存，不调 API
- 输出 JSON: {"原文标题": "中文翻译", ...}
- 环境: AA_MODEL/AA_GATEWAY 可选切换免费版（见 aa_api.py）
"""
import sys, os, json, re, glob, argparse

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根(2026-08-18 Phase 3 动态计算)
sys.path.insert(0, os.path.join(BASE, "scripts"))
sys.path.insert(0, os.path.join(BASE, "scripts", "common"))
sys.path.insert(0, os.path.join(BASE, "scripts", "works"))
import registry
import aa_api
from aa_common import list_txt, txt_read   # 压缩感知（.txt/.txt.gz）

# 跳过翻译的标题：无假名且无汉字（纯 ASCII/数字/标点）——这类不是日文标题，原样保留
PURE_NON_CJK = re.compile(r"^[\x20-\x7e0-9（）、．.・\s\u3000]*$")


def needs_translation(t):
    """日文标题需翻译：含假名，或含日文汉字（話/編/前/後/最/終 等）。纯 ASCII/数字无需。"""
    if not t:
        return False
    if PURE_NON_CJK.match(t):
        return False
    # 含假名 → 需翻译
    if re.search(r"[ぁ-んァ-ヶ]", t):
        return True
    # 含 CJK 汉字 → 需翻译（统一 話→话、編→篇 等）
    if re.search(r"[\u4e00-\u9fff]", t):
        return True
    return False


def collect_titles(cfg):
    """收集作品全部章节的标题（txt 首行 # 后），并剥离作品名前缀。返回 {标题: 示例文件名}。"""
    out_dir = cfg["out_dir"]
    pref = (cfg.get("title") or "").strip()
    out = {}
    for f in [os.path.join(out_dir, n) for n in list_txt(out_dir, "_汉化.txt")]:
        try:
            title = (txt_read(f) or [""])[0].strip()
        except Exception:
            continue
        if title.startswith("#"):
            title = title[1:].strip()
        # 剥离作品名前缀（可含全角空格分隔）
        if pref:
            t2 = title
            if t2.startswith(pref):
                t2 = t2[len(pref):].lstrip("\u3000 ")
            title = t2.strip()
        if title:
            out[title] = out.get(title, "") or f  # 标题 → 示例文件名
    return out


SYSTEM = """你是一位日文→简体中文的章节标题翻译。把给定的日文章节标题翻译成中文，保留格式结构。

规则：
- 第N話/第N话 → 第N话
- 「」书名号保留
- そのN → 第N部分（前編/後編 若紧跟则合并：第N部分前篇/后篇）
- プロローグ → 序章，エピローグ → 尾声，前編 → 前篇，後編 → 后篇
- 外伝 → 外传，小ネタ → 小剧场，埋めネタ → 番外，短篇 → 短篇，雑談所ネタ → 闲谈所小品
- 幕間 → 幕间
- 角色名（如 やる夫/黒子 等）保留原文
- 标题中的英文/数字（High School Lovers、Legend of the YARUO 等）保留原文
- 只在末尾输出 序号|中文翻译，不要任何解释

输出格式（每行一条）：
序号|中文翻译"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--force", action="store_true", help="忽略已有缓存全部重译")
    ap.add_argument("--dry", action="store_true", help="只打印待翻译标题，不调 API")
    args = ap.parse_args()
    cfg = registry.WORKS.get(args.work)
    if not cfg:
        print(f"未知 work: {args.work}"); sys.exit(1)

    cache_path = os.path.join(cfg["work_dir"], "title_translations.json")
    cache = {}
    if os.path.exists(cache_path) and not args.force:
        try:
            cache = json.load(open(cache_path, encoding="utf-8"))
        except Exception:
            cache = {}

    titles = collect_titles(cfg)
    # 待翻译 = 未在缓存，且确需翻译（含假名/日文汉字）；纯 ASCII/数字原样入缓存
    todo = {}
    for t in titles:
        if args.force or t not in cache:
            if needs_translation(t):
                todo[t] = titles[t]
            else:
                cache[t] = t  # 纯 ASCII/数字 → 原样
    print(f"唯一标题 {len(titles)} | 已在缓存 {len([t for t in titles if t in cache])} | 待翻译 {len(todo)}")

    if args.dry:
        for t in todo:
            print("  ", t)
        return

    # 分块翻译（每块 ≤ 40 条）
    items = list(todo.items())
    for i in range(0, len(items), 40):
        chunk = items[i:i + 40]
        body = "\n".join(f"{j}|{t}" for j, (t, _) in enumerate(chunk))
        try:
            text = aa_api.call_api(SYSTEM, body, max_tokens=8192)
        except Exception as e:
            print(f"  块 {i//40+1} API 失败: {e}")
            continue
        res = aa_api.parse_result(text)
        for j, (t, fname) in enumerate(chunk):
            zh = res.get(j, "").strip()
            if zh:
                cache[t] = zh
                print(f"  {t[:30]} → {zh[:40]}")
            else:
                print(f"  ⚠ 未返回: {t[:30]}")
        # 每块后存一次（断点续跑）
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

    print(f"完成，缓存 {len(cache)} 条 → {cache_path}")


if __name__ == "__main__":
    main()
