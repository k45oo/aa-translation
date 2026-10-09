#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""本地术语候选池构建 (2026-09-07, ④ 候选池预筛方案 零 API)。

用 Janome 分词 + 复合名词合并(CompoundNounFilter) 对作品全台词做词频统计,
按 pos 过滤(名詞) + 接尾剥离(さん/くん/ちゃん/様/殿 等) + 助词/功能词过滤,
再与 已有角色(roles.json)/known_names/作品术语表(manual+auto) 差集,
输出按频次降序的候选池 term_pool.json —— 供后续「模型对短候选列表筛选+给译名」的输入。

前置:
  pip install janome (纯 Python, Windows 直装, 无 VC++ 依赖; 备选 SudachiPy/MeCab)
  台词文件 aa/<章>_台词.txt —— 缺失自动用 extract_dialogue ensure_dialogue_file 补生成
  (与 gen_chapter_summaries 同机制, src_only=True)。

用法:
  python scripts/common/build_term_pool.py --work <id> [--top 500] [--min-freq 3]
  python scripts/common/build_term_pool.py --work <id> --full    # 全台词(默认采样? 默认全)
  --aa <目录> 直连 aa 目录(无 registry, 差集仅 known_names/glossary)
输出: <work_dir>/term_pool.json
  {"schema":"术语候选池 v1","work":..,"工具":"janome+compound","总台词段":N,
   "候选":[{term,count,pos,样例文件}..]  // 按频次降序
  }
"""
import json
import os
import re
import sys
import argparse
from collections import Counter

# ---- path bootstrap (脚本可独立跑, 也可被 gen_glossary_ai import) ----
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))       # .../scripts/common
_BASE = os.path.dirname(os.path.dirname(_THIS_DIR))          # .../scripts/common/../.. -> 仓库根
for _sp in ("scripts", "scripts/common", "scripts/works", "scripts/tools"):
    _p = os.path.join(_BASE, _sp)
    if _p not in sys.path:
        sys.path.insert(0, _p)
# site-packages (janome 装在这里)
_sp = os.path.join(_BASE, ".venv", "Lib", "site-packages")
if os.path.isdir(_sp) and _sp not in sys.path:
    sys.path.insert(0, _sp)

from janome.tokenizer import Tokenizer                       # noqa: E402
from janome.analyzer import Analyzer                         # noqa: E402
from janome.tokenfilter import CompoundNounFilter, POSKeepFilter  # noqa: E402

# ---- 接尾词 / 功能词(复用 gen_glossary_ai.FUNC_WORDS + 补充代词/助词) ----
SUFFIX = ("さん", "くん", "ちゃん", "様", "殿", "氏", "君", "ちん", "たん", "先輩", "先生", "ちゃんち")
# 普通名詞但非专名的常见词(位于角色名候选的低优先)→ 不进候选? 交给模型筛, 这里只挡裸助词/纯代词
FUNC_NOUN = {
    "こと", "もの", "それ", "これ", "あれ", "ここ", "そこ", "あそこ", "どれ", "どこ",
    "よう", "わけ", "はず", "つもり", "ところ", "人", "物", "一", "何", "何か", "誰",
    "ぐらい", "くらい", "だけ", "まで", "ほう", "方", "中", "上", "下", "前", "後ろ",
    "場合", "時", "時間", "日", "月", "年", "顔", "目", "声", "手", "心",
}

# Janome 分词器(懒加载, 全词+复合合并)
_tokenizer = None
_analyzer = None


def _get_analyzer():
    global _tokenizer, _analyzer
    if _analyzer is None:
        _tokenizer = Tokenizer()
        _analyzer = Analyzer(token_filters=[
            CompoundNounFilter(),          # 相邻名詞合并(蒼星石/霧雨魔理沙/コッコロ)
            POSKeepFilter(["名詞"]),       # 只留名词
        ])
    return _analyzer


def clean_surface(srf):
    """剥离接尾词(さん/くん/様 等), 还原纯专名。"""
    if len(srf) < 2:
        return srf
    for su in SUFFIX:
        if srf.endswith(su) and len(srf) > len(su):
            return srf[: -len(su)]
    return srf


_TERM_RE = re.compile(r"^[\u4e00-\u9fffぁ-んァ-ヶー・々]{2,}$")


def is_qualified(term):
    """过滤: 长度>=2, 纯日文(CJK/假名/长音), 非接尾残留/非裸助词。"""
    if not _TERM_RE.match(term):
        return False
    if term in FUNC_NOUN:
        return False
    # 纯重复单字符(メメメ…)艺术
    if len(set(term)) == 1:
        return False
    return True


def load_known(work_id=None, aa_dir=None):
    """差集: 全局 glossary known_names/glossary对 + roles.json 日/中文 + 各作品表 manual/auto。
    返回 set(规范化 term)。

    2026-09-20 缺口修复（doc/方案归档/术语表反哺与候选池差集修复方案_2026-09-20.md 决策 6）:
    原实现只读全局、`work_id/aa_dir` 收了参数却没用，与 docstring 不符 → 作品表里人工
    确认过的条目会重新冒出来当候选。现补读全部作品表（manual+auto；要重新看到某词，
    删掉作品表那行即可）。
    """
    known = set()
    norm = lambda s: re.sub(r"\s+", "", s or "").lower()
    # 全局 glossary.json
    gp = os.path.join(_BASE, "glossary.json")
    if os.path.exists(gp):
        try:
            d = json.load(open(gp, encoding="utf-8"))
            for n in d.get("known_names", []):
                known.add(norm(n))
            for r in d.get("角色", []):
                known.add(norm(r.get("日文"))); known.add(norm(r.get("中文")))
            for p in d.get("glossary", []):
                if isinstance(p, list) and p:
                    known.add(norm(p[0])); known.add(norm(p[1]) if len(p) > 1 else "")
                elif isinstance(p, dict):
                    known.add(norm(p.get("jp"))); known.add(norm(p.get("zh")))
        except Exception:
            pass
    # roles.json (三级重构后角色主体在这)
    rp = os.path.join(_BASE, "roles.json")
    if os.path.exists(rp):
        try:
            rd = json.load(open(rp, encoding="utf-8"))
            for r in rd.get("角色", []):
                known.add(norm(r.get("日文"))); known.add(norm(r.get("中文")))
                for al in (r.get("别名") or []):
                    known.add(norm(al))
        except Exception:
            pass
    # 各作品表 manual+auto（2026-09-20 缺口修复）
    try:
        import glossary_repatriate as _GR
        known |= _GR.work_table_terms()
    except Exception:
        pass
    return known


def build_pool(work_id, aa_dir, top=500, min_freq=3):
    """构建候选池。返回 (cands, stats)。"""
    import extract_dialogue as ED
    an = _get_analyzer()
    files = sorted(f for f in os.listdir(aa_dir) if f.endswith("_台词.txt"))
    cnt, ex = Counter(), {}
    nseg = 0
    for fn in files:
        try:
            entries = ED.parse_dialogue_file(os.path.join(aa_dir, fn))
        except Exception:
            continue
        for e in entries:
            txt = e["text"].strip()
            if not txt:
                continue
            nseg += 1
            for tok in an.analyze(txt):
                term = clean_surface(tok.surface)
                if not is_qualified(term):
                    continue
                cnt[term] += 1
                if term not in ex:
                    ex[term] = fn
    known = load_known(work_id, aa_dir)
    cands = []
    for term, n in cnt.items():
        if n < min_freq:
            continue
        if _norm2(term) in known:
            continue
        cands.append({"term": term, "count": n, "pos": "名詞(複合)", "样例文件": ex[term]})
    cands.sort(key=lambda x: (-x["count"], x["term"]))
    cands = cands[:top]
    return cands, {"总台词段": nseg, "不同词": len(cnt), "原文件名数": len(files)}


def _norm2(s):
    return re.sub(r"\s+", "", s or "").lower()


def main():
    ap = argparse.ArgumentParser(description="本地术语候选池(Janome 分词+复合合并, 零 API)")
    ap.add_argument("--work", help="registry 作品 id")
    ap.add_argument("--aa", help="直接指定 aa 目录(无 registry)")
    ap.add_argument("--top", type=int, default=500, help="候选池上限(默认500)")
    ap.add_argument("--min-freq", type=int, default=3, help="频次阈值(默认3)")
    ap.add_argument("--no-ensure", action="store_true", help="不自动补生成缺失台词文件")
    args = ap.parse_args()

    cfg = None
    if args.work:
        import registry
        if args.work not in registry.WORKS:
            raise SystemExit(f"未知作品: {args.work}")
        cfg = registry.get(args.work)
        aa_dir = cfg["aa_dir"]
        work_dir = cfg["work_dir"]
        work_id = args.work
        os.makedirs(work_dir, exist_ok=True)
        if cfg.get("r18"):
            os.environ.setdefault("AA_R18", "1")
    elif args.aa:
        aa_dir = args.aa
        work_id = "direct:" + os.path.basename(os.path.normpath(args.aa))
        work_dir = os.path.join(os.path.dirname(aa_dir), "work")
        os.makedirs(work_dir, exist_ok=True)
    else:
        raise SystemExit("必须提供 --work <id> 或 --aa <目录>")
    if not os.path.isdir(aa_dir):
        raise SystemExit(f"aa 目录不存在: {aa_dir}")

    # 确保台词文件存在(缺的章节自动补生成, 与 gen_chapter_summaries 同机制)
    if cfg is not None and not args.no_ensure:
        import gen_glossary_ai as GA
        made, failed = GA._ensure_dialogues(cfg, aa_dir)
        if made:
            print(f"[build_term_pool] 台词文件补生成 {made} 章", flush=True)
        if failed:
            print(f"[build_term_pool] [warn] {len(failed)} 章失败: "
                  + "; ".join(f"{b}({e})" for b, e in failed[:5]))

    cands, stats = build_pool(work_id, aa_dir, top=args.top, min_freq=args.min_freq)
    out = {
        "schema": "术语候选池 v1",
        "work": work_id,
        "工具": "janome+compoundnoun",
        "生成日期": __import__("time").strftime("%Y-%m-%d"),
        "top": args.top, "min_freq": args.min_freq,
        "统计": stats,
        "候选": cands,
    }
    out_path = os.path.join(work_dir, "term_pool.json")
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[build_term_pool] {work_id}: 候选 {len(cands)} 个 -> {out_path}")
    for c in cands[:15]:
        print(f"  {c['count']:3}  {c['term']}")


if __name__ == "__main__":
    main()
