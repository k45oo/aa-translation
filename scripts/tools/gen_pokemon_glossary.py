# -*- coding: utf-8 -*-
"""宝可梦作品：本地 wiki ↔ 作品术语表 双向同步（方案 D7；两向都不自动跑，都过人工闸门）。

正向 `--from-wiki`：把本地宝可梦 wiki（`doc/aa作品相关/ポケモンAA术语wiki/`）里**本作品语料
  实际出现**的条目写成译名级 jp→zh 进作品术语表 `glossary_<work>.json`。只写译名——
  说明文（effect/desc）归固定内容表，不进提示词。`来源=wiki` 且 cat 不含「常用」→
  `aa_translate._load_glossary_pairs` 会按**本章语料**二次过滤，不会全量注入。
  人工条目（`来源=manual`）**永不覆盖**；zh==jp（未确认译名）不写（避免教模型保持日文）。

反向 `--repatriate-wiki`：把作品术语表里**人工确认过**的译名回写 wiki 源
  `_raw/terms_zh.json` → 重建 `术语表.json`/`index.html`。默认只列差集，`--apply` 才写
  （写前自动备份）。

用法：
  python scripts/tools/gen_pokemon_glossary.py --work <id> --list
  python scripts/tools/gen_pokemon_glossary.py --work <id> --from-wiki [--min-count 1] [--dry]
  python scripts/tools/gen_pokemon_glossary.py --work <id> --repatriate-wiki [--terms a,b] [--apply]
"""
import argparse
import collections
import glob
import json
import os
import re
import shutil
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "common"), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import aa_translate as A            # noqa: E402
import registry                     # noqa: E402

WIKI_DIR = os.path.join(_ROOT, "doc", "aa作品相关", "ポケモンAA术语wiki")
WIKI_JSON = os.path.join(WIKI_DIR, "术语表.json")
WIKI_RAW = os.path.join(WIKI_DIR, "_raw", "terms_zh.json")


def load_wiki():
    if not os.path.exists(WIKI_JSON):
        return None
    return json.load(open(WIKI_JSON, encoding="utf-8"))


def glossary_path(work):
    cfg = registry.get(work)
    return os.path.join(cfg["work_dir"], f"glossary_{work}.json")


def load_work_glossary(work):
    p = glossary_path(work)
    if not os.path.exists(p):
        return {"schema": "1.1", "更新日期": time.strftime("%Y-%m-%d"),
                "glossary": [], "known_names": []}, p
    return json.load(open(p, encoding="utf-8")), p


def save_json(path, data, backup=True):
    if backup and os.path.exists(path):
        bdir = os.path.join(os.path.dirname(path), "backup")
        os.makedirs(bdir, exist_ok=True)
        shutil.copy2(path, os.path.join(bdir, os.path.basename(path) + "." +
                                         time.strftime("%Y%m%d%H%M%S")))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


NAME_RE = re.compile(r"『([^』]{1,24})』")
# 帖头默认用户名等：wiki 里混入的非术语条目
BLOCK_JP = {"名無し", "名無しさん", "名無しのポケモン", "あの", "これ", "それ", "どれ"}


def corpus_counts(work, terms, quiet=False):
    """全书语料里每个 wiki 词的次数（单次正则扫描；零 API）。

    同时统计**名字位证据**：该词在 `『名称』` 里出现的次数——本作把系统名（技/特性/
    ポテンシャル/持ち物）都写成 `『名』`，故「出现在 『』 内」是它确实是术语的硬证据；
    散文提及与帖头默认名（`名無し` 命中 113,806 次）都不算。
    """
    cfg = registry.get(work)
    pat = re.compile("|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True)))
    cnt = collections.Counter()
    name_cnt = collections.Counter()
    files = [os.path.join(cfg["aa_dir"], n) for n in A.list_txt(cfg["aa_dir"], exclude=("_台词.txt", "_汉化.txt"))]
    for i, f in enumerate(files):
        try:
            txt = "".join(A.txt_read(f))
        except Exception:
            continue
        cnt.update(pat.findall(txt))
        for m in NAME_RE.findall(txt):
            name_cnt[m.strip()] += 1
        if not quiet and (i + 1) % 200 == 0:
            print(f"    ...扫语料 {i + 1}/{len(files)}", flush=True)
    return cnt, name_cnt, len(files)


def from_wiki(work, min_count=1, min_name=2, dry=False, quiet=False):
    """正向：wiki → 作品术语表（只写译名级 jp→zh，manual 不覆盖）。

    入门条件（2026-09-22 加，首轮 701 条里混着 `名無し`/`あの`/单字 等垃圾）：
      ① 语料里以 `『名称』` 出现 ≥ min_name 次（名字位证据）**或** wiki 标了官方译名；
      ② jp 长度 ≥ 3（除非官方译名）；③ zh 长度 ≥ 2 且无残留假名；④ 不在 `名無し` 类黑名单。
    """
    wiki = load_wiki()
    if not wiki:
        return {"error": f"wiki 不存在：{WIKI_JSON}"}
    gl, gp = load_work_glossary(work)
    entries = gl.get("glossary") or []
    have = {e.get("jp") for e in entries if e.get("jp")}
    manual = {e.get("jp") for e in entries if e.get("来源") == "manual"}
    cand = {}
    for t in wiki.get("terms") or []:
        jp, zh = (t.get("jp") or "").strip(), (t.get("zh") or "").strip()
        if not jp or not zh or zh == jp or jp in BLOCK_JP:
            continue
        if A.kana_runs(zh) or len(zh) < 2:
            continue
        cand[jp] = t
    cnt, name_cnt, nfiles = corpus_counts(work, list(cand), quiet=quiet)
    add, drop = [], collections.Counter()
    for jp, t in sorted(cand.items(), key=lambda kv: -name_cnt.get(kv[0], 0)):
        official = bool(t.get("official"))
        if cnt.get(jp, 0) < min_count:
            drop["语料未命中"] += 1
            continue
        if name_cnt.get(jp, 0) < min_name and not official:
            drop["无『』名字位证据"] += 1
            continue
        if len(jp) < 3 and not official:
            drop["词长<3(非官方)"] += 1
            continue
        if jp in have:
            drop["已有条目"] += 1
            continue
        add.append({"jp": jp, "zh": t["zh"], "cat": f"wiki-{t.get('cat') or '其他'}",
                    "来源": "wiki", "role_cn": None, "role_works": None,
                    "命中": cnt.get(jp, 0), "名字位": name_cnt.get(jp, 0),
                    "wiki页": t.get("page") or ""})
    st = {"corpus_chapters": nfiles, "wiki_terms": len(cand), "add": len(add),
          "drop": dict(drop), "by_cat": dict(collections.Counter(e["cat"] for e in add)),
          "manual_protected": len(manual)}
    if not quiet or dry:
        print(f"[from-wiki] 语料 {nfiles} 章 | wiki 可用 {len(cand):,} | 入表 {len(add):,}"
              f" | 淘汰 {json.dumps(dict(drop), ensure_ascii=False)} | manual 保护 {len(manual)}")
        print(f"           按类：{json.dumps(st['by_cat'], ensure_ascii=False)}")
    if dry:
        for e in add[:25]:
            print(f"   + {e['jp']}→{e['zh']}  ({e['cat']}, 『』×{e['名字位']} / 全语料×{e['命中']})")
        return st
    if add:
        entries.extend(add)
        gl["glossary"] = entries
        gl["更新日期"] = time.strftime("%Y-%m-%d")
        save_json(gp, gl)
        print(f"           已写 {gp}（备份在 work/backup/）")
    return st


def repatriate(work, terms=None, apply_it=False, quiet=False):
    """反向：作品术语表里人工确认的译名 → wiki 源 `_raw/terms_zh.json` → 重建 wiki。"""
    wiki_raw = json.load(open(WIKI_RAW, encoding="utf-8")) if os.path.exists(WIKI_RAW) else {}
    gl, gp = load_work_glossary(work)
    diffs = []
    for e in gl.get("glossary") or []:
        jp, zh = (e.get("jp") or "").strip(), (e.get("zh") or "").strip()
        if not jp or not zh or zh == jp:
            continue
        if e.get("来源") == "wiki":
            continue                        # wiki 来的不回写（避免自转）
        if jp not in wiki_raw:
            continue
        cur = (wiki_raw.get(jp) or {}).get("zh") or ""
        if cur != zh:
            diffs.append({"jp": jp, "works": cur or "(空)", "glossary": zh,
                          "cat": e.get("cat", ""), "zh_hit": 0})
    st = {"diff": len(diffs), "applied": 0}
    print(f"[repatriate] 作品表→wiki 可回写 {len(diffs)} 条（wiki 现值 ≠ 作品表译名）")
    for d in diffs[:40]:
        print(f"   {d['jp']}: wiki「{d['works']}」→ 作品「{d['glossary']}」")
    if not apply_it:
        print("   （只列差集；--apply 才写。可用 --terms a,b 限定条目）")
        return st
    sel = set(x.strip() for x in (terms or "").split(",") if x.strip()) or None
    wrote = 0
    for d in diffs:
        if sel and d["jp"] not in sel:
            continue
        rec = wiki_raw.setdefault(d["jp"], {})
        rec["zh"] = d["glossary"]
        wrote += 1
    if wrote:
        save_json(WIKI_RAW, wiki_raw)
        import gen_pokemon_aa_wiki as W
        W.stage_build()
        st["applied"] = wrote
        print(f"   已回写 {wrote} 条并重建 wiki")
    return st


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", required=True)
    ap.add_argument("--from-wiki", action="store_true", help="正向：wiki → 作品术语表")
    ap.add_argument("--repatriate-wiki", action="store_true", help="反向：作品表 → wiki 源并重建")
    ap.add_argument("--list", action="store_true", help="只列两侧差集统计")
    ap.add_argument("--min-count", type=int, default=1, help="--from-wiki 语料最少命中次数（默认 1）")
    ap.add_argument("--min-name", type=int, default=2, help="--from-wiki 的『名称』名字位最少次数（默认 2）")
    ap.add_argument("--terms", default="", help="--repatriate-wiki 限定条目（逗号分隔）")
    ap.add_argument("--apply", action="store_true", help="--repatriate-wiki 真正写入")
    ap.add_argument("--dry", action="store_true", help="--from-wiki 只统计不落盘")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.from_wiki or a.list:
        st = from_wiki(a.work, min_count=a.min_count, min_name=a.min_name, dry=a.dry or a.list)
        if a.json:
            print(json.dumps(st, ensure_ascii=False))
    if a.repatriate_wiki:
        st = repatriate(a.work, terms=a.terms, apply_it=a.apply)
        if a.json:
            print(json.dumps(st, ensure_ascii=False))


if __name__ == "__main__":
    main()
