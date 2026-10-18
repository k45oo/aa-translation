# -*- coding: utf-8 -*-
"""术语变体提取器：以原文为锚点，定位日文词在汉化里的所有中文译法。

用途：某日文词（如 きらきー）被模型翻成了多种中文（闪闪/琪拉琪/kiraki...），
当前术语表替换只能匹配残留日文，处理不了已被翻译成变体的情况。
本脚本对每个含该词的原文行 + 对应汉化行，喂模型归纳出所有译法变体，
写 json（变体 + 章节/行号映射）供人工确认，确认后批量替换。

用法：
  python aa_web_app/gen_variant_extract.py --work yukika_robot --word きらきー
  （--sample N 只取前 N 处，--k 每批条数）
"""
import json, os, re, sys

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "scripts", "works"))
sys.path.insert(0, os.path.join(_ROOT, "scripts", "common"))

from aa_common import list_txt, txt_read, txt_existing   # 压缩感知（.txt/.txt.gz）
import registry


def extract_lines(work, word):
    """返回 [(章节, 行号, src行, tr行)] —— 所有含 word 的原文行及其汉化对应行。"""
    cfg = registry.get(work)
    aa_dir, out_dir = cfg["aa_dir"], cfg["out_dir"]
    word_re = re.compile(re.escape(word))
    rows = []
    for fn in list_txt(aa_dir, exclude=("_台词.txt", "_汉化.txt")):
        # 2026-10-17: 压缩感知——规范名 → 实际 .gz（否则 exists 恒假、抽取恒空）
        dst = txt_existing(os.path.join(out_dir, fn.replace(".txt", "_汉化.txt")))
        if not os.path.exists(dst):
            continue
        src_lines = txt_read(txt_existing(os.path.join(aa_dir, fn)))
        tr_lines = txt_read(dst)
        for i, s in enumerate(src_lines):
            if word_re.search(s) and i < len(tr_lines):
                rows.append((fn.replace(".txt", ""), i + 1, s, tr_lines[i]))
    return rows


def batch_prompt(rows, word):
    """把一批 (章节, 行号, src, tr) 构造成模型 prompt。"""
    parts = []
    for idx, (ch, ln, s, t) in enumerate(rows):
        parts.append(f"[{idx}] {ch} L{ln}")
        parts.append(f"原文: {s}")
        parts.append(f"译文: {t}")
    body = "\n".join(parts)
    sys_msg = (
        f"你是AA漫画术语校对。日文词「{word}」在原文里出现多次。"
        f"下面是若干条「原文/译文」行对（已按行号对齐，同行对照）。"
        f"请逐条判断：译文中哪个词/短语对应原文里的「{word}」。"
        f"输出 JSON 数组，每项 {{\"idx\": 行对索引, \"变体\": \"译文中对应那段中文\"}}。"
        f"若该行译文没有对应片段（艺术画/未译/词被省略/译名含其他语言如kiraki）用原文的概括词，"
        f"但必须精确对应「{word}」那个位置，不要多带前后文。"
        f"只输出 JSON，不要其他文字。"
    )
    return sys_msg, body


def extract_variants(work, word, sample=0, k=40):
    """分批喂模型，归纳变体。返回 (rows, variants, hit_map)。
    variants: {变体: 计数}
    hit_map: {变体: [(章节, 行号, src, tr)]} —— 替换时精确定位。"""
    from aa_backend import call_with_fallback
    rows = extract_lines(work, word)
    if sample and sample < len(rows):
        rows = rows[:sample]
    print(f"共 {len(rows)} 处 {word}，分批处理（每批 {k}）...", flush=True)
    variants, hit_map = {}, {}
    for bi in range(0, len(rows), k):
        batch = rows[bi:bi + k]
        sys_msg, body = batch_prompt(batch, word)
        try:
            txt, fb = call_with_fallback(False, sys_msg, body,
                                         max_tokens=6000, timeout=300)
        except Exception as e:
            print(f"  批 {bi//k} 失败: {e}", flush=True)
            continue
        m = re.search(r"\[.*\]", txt, re.S)
        if not m:
            print(f"  批 {bi//k} 无 JSON: {txt[:200]!r}", flush=True)
            continue
        try:
            items = json.loads(m.group(0))
        except Exception as e:
            print(f"  批 {bi//k} JSON 解析失败: {e} | {txt[:200]!r}", flush=True)
            continue
        got = {it.get("idx"): (it.get("变体") or "").strip() for it in items}
        for j, (ch, ln, s, t) in enumerate(batch):
            v = got.get(j, "")
            if not v:
                continue
            variants[v] = variants.get(v, 0) + 1
            hit_map.setdefault(v, []).append({"chapter": ch, "line": ln, "src": s, "tr": t})
        print(f"  批 {bi//k}: 累计 {len(variants)} 变体", flush=True)
    return rows, variants, hit_map


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--word", required=True)
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--k", type=int, default=40)
    args = ap.parse_args()
    rows, variants, hit_map = extract_variants(args.work, args.word,
                                               sample=args.sample, k=args.k)
    out = os.path.join(registry.get(args.work)["work_dir"],
                       f"variant_{args.word}.json")
    # 只存变体+示例位置（不存大文本，人工确认用）
    compact_hits = {v: [(h["chapter"], h["line"]) for h in hs]
                    for v, hs in hit_map.items()}
    data = {"word": args.word, "count": len(rows),
            "variants": dict(sorted(variants.items(), key=lambda x: -x[1])),
            "hits": compact_hits}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"\n写出 {out}: {len(variants)} 个变体")
    for v, c in sorted(variants.items(), key=lambda x: -x[1])[:40]:
        print(f"  {c:4d}  {v!r}")
