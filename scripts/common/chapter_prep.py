# -*- coding: utf-8 -*-
"""章节预处理：翻译前统计本章术语 + 章节级 R18 文本启发式判定（零 API）。

用法:
    python scripts/common/chapter_prep.py --work <id> [--eids 1,3-5,9556] [--ordered] [--all]

对每章 aa txt 产出:
    <work>/prep/<base>.json   {eid, base, terms:[jp,zh], top_fragments:[...], r18:{hit,count,words,lines}}
    <work>/prep/summary.json  本批次汇总（含每章 R18 判定）

能力:
  1) 本章术语: 用 _raw_glossary_pairs() 全集与本章语料做子串匹配，列出"本章实际出现"的术语对；
     与注入侧的作品级过滤(_filter_glossary_pairs)互补——这里更细、供人/流程看。
  2) R18 判定(A1 文本启发式): R18 特征词命中台词行数; 0=无 / 1=疑似 / >=2 或含强词=含R18要素。
     保守策略: 判定仅作标注报告，不自动切后端(后端仍按作品级 registry.r18 决定)。
  3) 高频假名片段: 本章 kana_runs 频次>=2、与 known_names 差集 → known_names 补录候选。

被 translate_runner 调用（翻译前阶段 prep_work()），也可独立跑。
"""
import json, os, re, sys, glob, argparse
from collections import Counter

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))   # scripts/common
_SCRIPTS = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _SCRIPTS)
sys.path.insert(0, os.path.join(_SCRIPTS, "common"))
sys.path.insert(0, os.path.join(_SCRIPTS, "works"))

import aa_api as A            # flat 版 re-export: kana_runs / is_text_line / HEADER_RE / _raw_glossary_pairs
import registry
from paths import BASE

# ---------- R18 特征词（2026-08-24 校准版） ----------
# 校准方法: 阳性对照 akari_ninshin/akari_haisin(含量≈100%), 阴性对照 saki_fc2/jinrou(无R18)。
# 修正要点:
#  1. STRONG 词需 ≥2 次命中才 yes(单次可能是调侃/误撞, 如 sujin_otona「えっち」×1)
#  2. 子串误撞排除: 性交(?!際|遊)=异性交往, ソープ 排除 ボディーソープ=沐浴露
#  3. 低区分度词(阴性作品也大量出现: おっぱい/パンツ/下着/絶頂/巨乳/貧乳 等)降级 SOFT,
#     只作浓度统计/佐证, 不单独 gate yes
# 判定三态: no / suspect / yes

# 高区分度强词: 阳性(akari)大量出现, 阴性(saki_fc2/jinrou)为 0 或极少 —— ≥2 次命中 → yes
R18_STRONG = [
    r"中出し", r"輪姦", r"レイプ", r"強姦", r"凌辱", r"孕ませ", r"種付け",
    r"ちんちん", r"ちんこ", r"おちんちん", r"まんこ", r"おまんこ", r"クリトリス",
    r"ちくび", r"フェラ", r"パイズリ", r"セックス", r"性交(?!際|遊)", r"挿入", r"射精",
    r"膣", r"アナル", r"肉便器", r"発情", r"交尾", r"乱交", r"バイブ", r"淫獣",
    r"触手責め", r"精飲", r"ぶっかけ", r"巨根", r"姦淫", r"穢れ",
    r"エッチ", r"えちえち", r"脱がし", r"裸体", r"孕ん",
    r"(?<!ボディー)ソープ",   # 排除 ボディーソープ(沐浴露)
]
# 中等区分度普通词: 阳性有, 阴性极少 —— ≥3 个不同词 → yes
R18_NORMAL = [
    r"おっぱい", r"乳首", r"処女", r"精液", r"ムラムラ", r"舐め回し",
    r"じゅっぽ", r"ランジェリー", r"スカートめくり", r"淫魔",
]
# 低区分度软词(调侃/设定/身体词): 阴性作品也大量出现 —— 仅浓度统计/佐证, 不 gate yes
R18_SOFT = [
    r"絶頂", r"巨乳", r"貧乳", r"ノーブラ", r"官能", r"緊縛", r"童貞", r"妊娠",
    r"下着", r"パンツ", r"いやらしい", r"そそる", r"エロい", r"えろい", r"エロ本",
    r"色っぽい", r"挑発", r"誘惑", r"過激", r"際どい", r"えっち", r"むっちり",
    r"パンチラ", r"全裸", r"触手", r"調教",
]
# 编译
R18_STRONG_RE = [re.compile(p) for p in R18_STRONG]
R18_NORMAL_RE = [re.compile(p) for p in R18_NORMAL]
R18_SOFT_RE = [re.compile(p) for p in R18_SOFT]

NUM_RE = re.compile(r"(\d+)")


def _eid_of_filename(fn):
    m = NUM_RE.search(os.path.basename(fn))
    return m.group(1) if m else os.path.basename(fn)


def _load_raw_pairs():
    """glossary.json 全替换对（复用 aa_translate._raw_glossary_pairs 的缓存）。"""
    try:
        return A.FLAT_MOD._raw_glossary_pairs()
    except Exception:
        try:
            data = json.load(open(os.path.join(BASE, "glossary.json"), encoding="utf-8"))
            return [tuple(p) for p in data.get("glossary", []) if isinstance(p, list) and len(p) == 2]
        except Exception:
            return []


def _known_names():
    try:
        data = json.load(open(os.path.join(BASE, "glossary.json"), encoding="utf-8"))
        return set(data.get("known_names", []))
    except Exception:
        return set()


def _split_lines(path):
    try:
        return open(path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return []


def prep_chapter(aa_path, work_dir, raw_pairs=None, is_r18_work=None, level=None):
    """单章预处理，返回 dict 并写 <work>/prep/<base>.json。零 API。

    level: work_meta.r18_level (high/low/medium/空)。按 level 调 R18 判定阈值:
      low(最严):    STRONG≥1 → yes;  NORMAL≥2 个不同词 → yes
      medium(正常): STRONG≥2 → yes;  NORMAL≥3 个不同词 → yes
      high(最松):   STRONG≥3 → yes;  NORMAL≥4 个不同词 → yes
      空(no):       不参与章节级判定(阈值用 medium, 结果不被 _chapter_work_r18 消费)
    """
    base = os.path.basename(aa_path).replace(".txt", "")
    eid = _eid_of_filename(aa_path)
    lines = _split_lines(aa_path)
    text_lines = [ln.strip() for ln in lines
                  if ln.strip() and not ln.startswith("#") and not ln.startswith("=====")
                  and not A.HEADER_RE.match(ln.strip()) and A.is_text_line(ln)]

    # 1) 本章术语（全集子串匹配）
    pairs = raw_pairs if raw_pairs is not None else _load_raw_pairs()
    text_all = "\n".join(text_lines)
    terms = [[jp, zh] for jp, zh in pairs if jp and jp in text_all]
    terms += [[jp, zh] for jp, zh in pairs if zh and zh in text_all and jp not in text_all]
    # 去重（按 jp）
    seen, terms_u = set(), []
    for p in terms:
        if p[0] not in seen:
            seen.add(p[0])
            terms_u.append(p)

    # 2) 高频假名片段（与 known_names 差集 → 补录候选）
    cnt = Counter()
    for ln in text_lines:
        for frag in A.kana_runs(ln):
            cnt[frag] += 1
    known = _known_names()
    top_fragments = [{"fragment": f, "count": n}
                     for f, n in cnt.most_common(15) if n >= 2 and f not in known
                     and len(set(f)) > 1]

    # 3) R18 文本启发式（2026-08-24 校准版）：
    #    STRONG ≥2 次命中 → yes（单次可能是调侃/误撞）
    #    NORMAL ≥3 个不同词 → yes
    #    SOFT 不 gate（仅浓度统计/佐证）
    #    suspect = 单次 STRONG 或 1~2 个不同 NORMAL
    r18 = {"hit": "no", "count": 0, "words": [], "lines": [],
           "r18_lines_count": 0, "r18_word_hits": 0}
    strong_hits = []       # (line, [words])  STRONG 命中行
    normal_hits = []       # (line, [words])  NORMAL 命中行
    soft_lines = set()     # 仅 SOFT 命中行（浓度统计用）
    for ln in text_lines:
        sw = [p for p, r in zip(R18_STRONG, R18_STRONG_RE) if r.search(ln)]
        nw = [p for p, r in zip(R18_NORMAL, R18_NORMAL_RE) if r.search(ln)]
        fw = [p for p, r in zip(R18_SOFT, R18_SOFT_RE) if r.search(ln)]
        if sw:
            strong_hits.append((ln, sw))
        if nw:
            normal_hits.append((ln, nw))
        if fw:
            soft_lines.add(ln)
        if sw or nw or fw:
            r18["lines"].append({"line": ln[:60], "words": sw + nw + fw})
    # 统计
    strong_count = sum(len(ws) for _, ws in strong_hits)          # STRONG 命中次数
    normal_words = {w for _, ws in normal_hits for w in ws}       # 不同 NORMAL 词
    r18_lines = len({ln for ln, _ in strong_hits} | {ln for ln, _ in normal_hits} | soft_lines)
    r18_word_hits = strong_count + sum(len(ws) for _, ws in normal_hits) \
        + sum(len([p for p, r in zip(R18_SOFT, R18_SOFT_RE) if r.search(ln)]) for ln in soft_lines)
    # 判定（2026-08-26 按 r18_level 调阈值: low最严/high最松）
    if level == "low":
        _s_yes, _n_yes = 1, 2      # 最严: 单次 STRONG 或 2 个不同 NORMAL 即 yes
    elif level == "high":
        _s_yes, _n_yes = 3, 4      # 最松: 需更多命中才 yes
    else:
        _s_yes, _n_yes = 2, 3      # medium/空: 现规则
    if strong_count >= _s_yes:
        r18["hit"] = "yes"
    elif len(normal_words) >= _n_yes:
        r18["hit"] = "yes"
    elif strong_count == 1 or len(normal_words) >= 1:
        r18["hit"] = "suspect"
    r18["words"] = [w for _, ws in strong_hits for w in ws] + sorted(normal_words) \
        + [p for ln in sorted(soft_lines) for p, r in zip(R18_SOFT, R18_SOFT_RE) if r.search(ln)]
    r18["count"] = r18_word_hits          # 修正: count = 总命中次数
    r18["r18_lines_count"] = r18_lines   # 命中 R18 词(STRONG/NORMAL/SOFT)的行数
    r18["r18_word_hits"] = r18_word_hits  # 总命中次数

    out = {
        "eid": eid, "base": base, "text_lines": len(text_lines),
        "work_r18": bool(is_r18_work),
        "terms": terms_u, "term_count": len(terms_u),
        "top_fragments": top_fragments,
        "r18": {k: v for k, v in r18.items()},
    }
    prep_dir = os.path.join(work_dir, "prep")
    os.makedirs(prep_dir, exist_ok=True)
    with open(os.path.join(prep_dir, base + ".json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def prep_work(cfg, eids=None, ordered=False, work=None):
    """对作品跑本章 prep。eids=None = aa/ 全部。返回结果列表 + 写 summary.json。"""
    aa_dir = cfg["aa_dir"]
    work_dir = cfg["work_dir"]
    os.makedirs(os.path.join(work_dir, "prep"), exist_ok=True)
    raw_pairs = _load_raw_pairs()
    files = sorted(f for f in glob.glob(os.path.join(aa_dir, "*.txt")))
    if eids:
        files = [f for f in files if _eid_of_filename(f) in set(map(str, eids))]
    # 2026-08-26: 从 work_meta 读 r18_level 调判定阈值
    _lvl = None
    try:
        _mp = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           "aa_web_app", "data", "work_meta.json")
        if os.path.exists(_mp):
            import json as _j
            _meta = _j.load(open(_mp, encoding="utf-8"))
            if not work:
                # 从 aa_dir 反查 registry id
                try:
                    for _wid, _wc in registry.WORKS.items():
                        if os.path.abspath(_wc.get("aa_dir", "")) == os.path.abspath(aa_dir):
                            work = _wid
                            break
                except Exception:
                    pass
            _lvl = ((_meta.get(work, {}) or {}).get("r18_level", "")) or None if work else None
    except Exception:
        _lvl = None

    results = [prep_chapter(f, work_dir, raw_pairs, bool(cfg.get("r18")), level=_lvl) for f in files]

    summary = {
        "作品": cfg.get("title", os.path.basename(aa_dir)),
        "生成": "2026-08-21",
        "章数": len(results),
        "r18_yes": [r["eid"] for r in results if r["r18"]["hit"] == "yes"],
        "r18_suspect": [r["eid"] for r in results if r["r18"]["hit"] == "suspect"],
        "r18_no": [r["eid"] for r in results if r["r18"]["hit"] == "no"],
        "章节": results,
    }
    with open(os.path.join(work_dir, "prep", "summary.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    return results, summary


def main():
    ap = argparse.ArgumentParser(description="章节预处理：本章术语 + R18 文本启发式判定（零 API）")
    ap.add_argument("--work", required=True)
    ap.add_argument("--eids", help="逗号/范围列表，缺省=全部")
    ap.add_argument("--ordered", action="store_true", help="按 chapter_order.json 原站顺序输出报告")
    args = ap.parse_args()
    if args.work not in registry.WORKS:
        raise SystemExit(f"未知作品: {args.work!r}")
    cfg = registry.get(args.work)
    eids = args.eids.split(",") if args.eids else None
    results, summary = prep_work(cfg, eids, work=args.work)
    print(f"[chapter_prep] {cfg.get('title','')}: {len(results)} 章")
    for r in results:
        t = r["r18"]["hit"]
        r18_txt = {"yes": "R18", "suspect": "R18?", "no": " - "}[t]
        print(f"  {r['eid']:<7} R18[{r18_txt}] 术语{r['term_count']:>2}条 "
              f"([{', '.join(j for j, _ in r['terms'][:6]) or '-'}]) 新片段top "
              f"{[f['fragment'] for f in r['top_fragments'][:3]]}")
    print(f"[chapter_prep] 汇总: R18 {len(summary['r18_yes'])} / 疑似 {len(summary['r18_suspect'])} / 无 {len(summary['r18_no'])}"
          f" -> {os.path.join(cfg['work_dir'], 'prep', 'summary.json')}")


if __name__ == "__main__":
    main()
