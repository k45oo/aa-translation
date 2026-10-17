# -*- coding: utf-8 -*-
"""迭代术语更新：顺序翻译每章后，从本章（原文+译文）提取新候选术语，增量写入作品术语层。

输出: <work>/glossary_iter.json  （增量层，不直接改 glossary.json）
  {
    "作品": <id>, "更新": <date>,
    "候选": [ {"term","translation","type","来源eid","count","确认":false}, ... ]
  }

- 零 API 部分（默认）: 本章 kana_runs 高频片段（count>=2、len(set)>1、与 known_names 差集）
  → known_names 补录候选。
- --ai: 额外调模型从本章台词提取 term→translation（复用 chapter_prep 的台词过滤 + gen_glossary_ai 的 JSON 解析）。
- --merge: 把"确认":true 的候选合并进 glossary.json（写 known_names + 角色[name,作品本作]），审校后一键落地。
    合并后 --merge 会把已确认项从候选移除（标记 merged）。
- 2026-08-27 分流: 候选按「作品专属/跨作通用」分流——
    - 作品专属（疑似本作角色/专名, 默认）→ 合并进 作品知识库 work/<work>_knowledge.json 的 待确认.术语/人物,
      确认后由 work_memory 注入（不再直接进全局 glossary.json）
    - 跨作通用（--global 标记, 知名 IP/VOICEROID 等）→ 仍合入全局 glossary.json
  merge_confirmed 依据候选的 "global" 字段决定去向。

被 translate_runner 在每章翻译清理后调用（iter_work()）；也可独立跑看候选。
"""
import json, os, re, sys, glob, argparse, time
from collections import Counter

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _SCRIPTS)
sys.path.insert(0, os.path.join(_SCRIPTS, "common"))
sys.path.insert(0, os.path.join(_SCRIPTS, "works"))
sys.path.insert(0, os.path.join(_SCRIPTS, "tools"))

import aa_api as A
from aa_common import list_txt   # 压缩感知（.txt/.txt.gz）
import registry
from paths import BASE
import gen_glossary_ai as GA          # 复用 parse_terms / SYSTEM_PROMPT / HAS_KANA
import work_memory as WM              # 2026-08-27: 作品知识库

NUM_RE = re.compile(r"(\d+)")


def _eid_of_filename(fn):
    m = NUM_RE.search(os.path.basename(fn))
    return m.group(1) if m else os.path.basename(fn)


def _load_iter(work_dir):
    p = os.path.join(work_dir, "glossary_iter.json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return {"作品": "", "更新": "", "候选": []}


def _save_iter(work_dir, data):
    p = os.path.join(work_dir, "glossary_iter.json")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return p


def _known_names():
    try:
        data = json.load(open(os.path.join(BASE, "glossary.json"), encoding="utf-8"))
        return set(data.get("known_names", []))
    except Exception:
        return set()


def _text_lines(path):
    try:
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return []
    return [ln.strip() for ln in lines
            if ln.strip() and not ln.startswith("#") and not ln.startswith("=====")
            and not A.HEADER_RE.match(ln.strip()) and A.is_text_line(ln)]


def iter_chapter_zero_api(aa_path, known):
    """零 API：本章高频假名片段 → 新增候选。返回 list[term]。"""
    cnt = Counter()
    for ln in _text_lines(aa_path):
        for frag in A.kana_runs(ln):
            cnt[frag] += 1
    out = []
    for f, n in cnt.most_common(30):
        if n >= 2 and len(set(f)) > 1 and f not in known:
            item = {"term": f, "translation": "", "type": "疑似角色/专名片段",
                    "来源eid": _eid_of_filename(aa_path), "count": n, "确认": False}
            if f in GA.FUNC_WORDS:
                item["note"] = "语法功能词？勿入 known_names（会掩盖漏译）"
            out.append(item)
    return out


def iter_chapter_ai(aa_path, backend=None):
    """--ai：取本章台词样本调模型提取 term→translation。返回 list[term]。"""
    lines = _text_lines(aa_path)
    sample = "\n".join(lines[:60])
    if not sample.strip():
        return []
    if backend:
        # 2026-08-27: 免费模式(AA_FREE_MODE=1)忽略 backend 显式指定——否则
        # AA_BACKEND 覆盖会绕过免费链(术语提取也要免费模型)
        if os.environ.get("AA_FREE_MODE") not in ("1", "true", "True"):
            os.environ["AA_BACKEND"] = backend
    os.environ.setdefault("AA_USAGE_TAG", "iter_glossary")
    raw = A.call_api(GA.SYSTEM_PROMPT, sample, max_tokens=3000)
    eid = _eid_of_filename(aa_path)
    out = []
    for c in GA.parse_terms(raw):
        t = {"term": c["term"], "translation": c.get("translation", ""),
             "type": c.get("type", "术语"), "来源eid": eid, "count": 1, "确认": False}
        n = GA.note_for(c["term"])
        if n:
            t["note"] = n
        out.append(t)
    return out


def iter_work(cfg, eids=None, use_ai=False, backend=None):
    """对指定章节跑迭代提取（零 API + 可选 AI），追加写 glossary_iter.json。返回候选列表。"""
    aa_dir = cfg["aa_dir"]
    work_dir = cfg["work_dir"]
    os.makedirs(work_dir, exist_ok=True)
    data = _load_iter(work_dir)
    data["作品"] = cfg.get("id") or os.path.basename(aa_dir)
    data["更新"] = time.strftime("%Y-%m-%d")
    known = _known_names()
    eid_set = set(map(str, eids)) if eids else None
    files = [os.path.join(aa_dir, n) for n in list_txt(aa_dir, exclude=("_台词.txt", "_汉化.txt"))]
    if eid_set:
        files = [f for f in files if _eid_of_filename(f) in eid_set]
    before = len(data["候选"])
    for f in files:
        cands = iter_chapter_zero_api(f, known)
        if use_ai:
            cands += iter_chapter_ai(f, backend)
        # 去重: 同 term 候补已存在则叠加来源
        for c in cands:
            dup = next((x for x in data["候选"] if x["term"] == c["term"]), None)
            if dup:
                dup["count"] = max(dup.get("count", 1), c.get("count", 1))
                if c.get("translation") and not dup.get("translation"):
                    dup["translation"] = c["translation"]
                if c["来源eid"] not in str(dup.get("来源eid", "")):
                    dup["来源eid"] = str(dup["来源eid"]) + "," + c["来源eid"]
            else:
                data["候选"].append(c)
    p = _save_iter(work_dir, data)
    print(f"[iter_glossary] {data['作品']}: 新增候选 {len(data['候选']) - before}（现有 {len(data['候选'])}） -> {p}", flush=True)
    return data["候选"]


def merge_confirmed(cfg, work_id=None):
    """把 glossary_iter.json 中"确认":true 的候选合并（2026-08-27 分流）。

    - global=True 候选: 合入全局 glossary.json（known_names + 角色）——跨作通用
    - 默认(作品专属): 合入 作品知识库 work/<work>_knowledge.json 正式块 术语/人物
      （人已在 iter 层标确认, 直接注入; 2026-08-27 审核修: 原写待确认区=死数据）
    """
    work_dir = cfg["work_dir"]
    data = _load_iter(work_dir)
    cands = [c for c in data.get("候选", []) if c.get("确认")]
    if not cands:
        print("[iter_glossary] 无已确认候选（编辑 glossary_iter.json 把要合并的项设 确认:true 后重跑 --merge）")
        return 0
    if not work_id:
        work_id = cfg.get("id") or os.path.basename(os.path.dirname(cfg["aa_dir"]))  # aa_dir 以 \aa 结尾
    to_global = [c for c in cands if c.get("global")]
    to_work = [c for c in cands if not c.get("global")]
    total = 0
    # --- 跨作通用 -> 全局 glossary.json (known_names) + roles.json (角色) ---
    # 2026-09-01 三级重构: 角色块迁出 glossary.json → roles.json
    if to_global:
        gp = os.path.join(BASE, "glossary.json")
        gl = json.load(open(gp, encoding="utf-8"))
        kn = set(gl.get("known_names", []))
        # roles.json 加载
        rp = os.path.join(BASE, "roles.json")
        try:
            roles = json.load(open(rp, encoding="utf-8")).get("角色", [])
        except Exception:
            roles = []
        skipped_syntax = []
        for c in to_global:
            t = c.get("term", "").strip()
            if t in GA.FUNC_WORDS:
                skipped_syntax.append(t)
                continue
            if t and t not in kn:
                kn.add(t)
                total += 1
            if not any(r.get("日文") == t for r in roles):
                roles.append({"日文": t, "中文": c.get("translation", ""),
                              "作品": [work_id], "分类": "角色", "备注": f"迭代候选 {c.get('来源eid','')}"})
        if skipped_syntax:
            print(f"[iter_glossary] 跳过 {len(skipped_syntax)} 个语法功能词(不入 known_names): {sorted(skipped_syntax)}", flush=True)
        gl["known_names"] = sorted(kn)
        gl["更新日期"] = time.strftime("%Y-%m-%d")
        with open(gp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(gl, f, ensure_ascii=False, indent=1)
        # roles.json 写回
        rdata = {"schema": "AA角色表 v1", "更新日期": time.strftime("%Y-%m-%d"), "角色": roles}
        with open(rp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(rdata, f, ensure_ascii=False, indent=2)
        print(f"[iter_glossary] 跨作通用: 合并 {len(to_global)} 条 -> 全局 glossary.json(known_names) + roles.json(角色)", flush=True)
    # --- 作品专属 -> 作品知识库 正式块 ---
    if to_work:
        mem = WM.load_knowledge(work_id)
        terms = mem.setdefault("术语", {})
        people = mem.setdefault("人物", {})
        for c in to_work:
            t = c.get("term", "").strip()
            if not t or t in GA.FUNC_WORDS:
                continue
            tr = c.get("translation", "") or ""
            typ = c.get("type", "")
            if "角色" in typ or "人物" in typ:
                if t not in people:
                    people[t] = tr or "(待定译名)"
                    total += 1
            else:
                if t not in terms:
                    terms[t] = tr or "(待定译名)"
                    total += 1
        WM.save_knowledge(work_id, mem)
        print(f"[iter_glossary] 作品专属: 合并 {len(to_work)} 条 -> {work_id}_knowledge.json 正式块", flush=True)
    # 从迭代层移除已确认项
    data["候选"] = [c for c in data.get("候选", []) if not c.get("确认")]
    _save_iter(work_dir, data)
    print(f"[iter_glossary] 合并完成 {total} 条；已从迭代层移除")
    return total


def main():
    ap = argparse.ArgumentParser(description="迭代术语更新：每章后提取候选写 glossary_iter.json")
    ap.add_argument("--work", required=True)
    ap.add_argument("--eids", help="逗号/范围列表（缺省=全部 aa 章）")
    ap.add_argument("--ai", action="store_true", help="额外调模型提取 term→translation")
    ap.add_argument("--backend", help="--ai 时后端覆盖")
    ap.add_argument("--to-global", action="store_true", help="合并时把候选标记为跨作通用(进全局 glossary.json); 默认作品专属进知识库")
    ap.add_argument("--merge", action="store_true", help="把已确认候选合并(global=进全局, 默认进作品知识库待确认区)")
    args = ap.parse_args()
    if args.work not in registry.WORKS:
        raise SystemExit(f"未知作品: {args.work!r}")
    cfg = registry.get(args.work)
    if args.merge:
        if args.to_global:
            # 标记候选为跨作通用(进全局表)
            d = _load_iter(cfg["work_dir"])
            for c in d.get("候选", []):
                if c.get("确认"):
                    c["global"] = True
            _save_iter(cfg["work_dir"], d)
        merge_confirmed(cfg, work_id=args.work)
        return
    eids = args.eids.split(",") if args.eids else None
    cands = iter_work(cfg, eids, use_ai=args.ai, backend=args.backend)
    for c in cands[:30]:
        print(f"  {c['term']:<18} {c['translation'] or '-':<16} [{c.get('type','')}] x{c['count']} eid={c['来源eid']}")


if __name__ == "__main__":
    main()
