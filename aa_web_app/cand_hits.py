# -*- coding: utf-8 -*-
"""候选出处命中索引（方案 doc/方案归档/候选联网核名与出处高亮方案_2026-10-15.md 决策 11/13）。

候选窗的"出处"= 含该候选词的行在哪些章。池式候选的 `来源` 字段只记**首次出现的样例章**
（`term_pool.json` 的「样例文件」），所以旧实现每个候选只有 1 章出处、读到别的章时恒空。

实测全库扫一遍的代价：1.5s（13MB/43 章/46 词）～ 22.3s（546MB/492 章/13 词）
→ 打开候选窗现扫不可接受，故落盘 `<work_dir>/cand_hits.json`：

    {"schema","生成时间","sig","词数","命中": {term: {base: [行idx, ...]}}}

失效键 `sig` = 候选文件 mtime + `aa/` 目录 mtime + 词集合 hash（任一变化即重建）。
缓存生成时机：`scripts/common/run_full_pool_until_done.py` 收尾（与联网核名同处，见方案 §3.1）；
路由层在**没有**缓存文件时兜底同步算一次并落盘（旧数据首次打开会慢一次）。
"""
import hashlib
import json
import os
import time

SCHEMA = "候选出处命中 v2"      # v2：加「计数」（真命中行数，不封顶）+ 逐章语料指纹
MAX_CH = 3          # 每候选最多显示 3 章（决策 11）
MAX_LINES = 20      # 每章最多记 20 个命中行（前端只用来滚动定位/计数）
CACHE_NAME = "cand_hits.json"


def _list_src_files(aa_dir):
    """原文 `.txt`（排除 `_台词.txt` / `_汉化.txt`），按名排序——与候选池的目录口径一致。"""
    out = []
    try:
        names = sorted(os.listdir(aa_dir))
    except OSError:
        return out
    for fn in names:
        if not fn.endswith(".txt"):
            continue
        if fn.endswith(("_台词.txt", "_汉化.txt")):
            continue
        out.append(fn)
    return out


def _corpus_digest(aa_dir):
    """原文语料指纹：逐章 `名|mtime_ns|size`（scandir 自带 stat，一次目录扫描即得）。

    比"只看目录 mtime"更准：单章内容改动也失效（目录 mtime 只在增删文件时变）。
    492 章级作品实测 <10ms（Windows DirEntry.stat 用 FindFirstFile 已有信息）。
    """
    h = hashlib.sha1()
    rows = []
    try:
        with os.scandir(aa_dir) as it:
            for e in it:
                if not e.name.endswith(".txt") or e.name.endswith(("_台词.txt", "_汉化.txt")):
                    continue
                try:
                    st = e.stat()
                except OSError:
                    continue
                rows.append((e.name, st.st_mtime_ns, st.st_size))
    except OSError:
        return "0"
    for name, mt, size in sorted(rows):
        h.update(f"{name}|{mt}|{size}\x00".encode("utf-8"))
    return h.hexdigest()[:16]


def compute_sig(cand_path, aa_dir, terms):
    """失效键：候选文件 mtime + 语料逐章指纹（名/mtime/size）+ 词集合 hash。"""
    h = hashlib.sha1()
    for t in sorted({(x or "").strip() for x in terms if (x or "").strip()}):
        h.update(t.encode("utf-8"))
        h.update(b"\x00")
    try:
        cand_mt = str(os.stat(cand_path).st_mtime_ns)
    except OSError:
        cand_mt = "0"
    return "|".join([h.hexdigest()[:16], cand_mt, _corpus_digest(aa_dir)])


def build(aa_dir, terms, max_ch=MAX_CH, max_lines=MAX_LINES):
    """扫全库原文算命中。

    返回 `(hits, counts)`：
      hits   = {term: {base: [行idx,…]}}（每章最多 max_lines 个——只用于滚动定位/高亮）
      counts = {term: {base: 真命中行数}}（不封顶——展示「命中 N 处」与按命中降序排序用它，
               否则 20 封顶会让「マヤ 747 次」这类高频词的排序退化）
    每词最多 max_ch 章（按真命中数降序）。零 API。
    """
    terms = [(t or "").strip() for t in terms]
    terms = [t for t in terms if t and not t.startswith("re:")]
    if not terms:
        return {}, {}
    # 按首字分桶：只对行内出现过的首字取词，词多时省一大截
    by_head = {}
    for t in terms:
        by_head.setdefault(t[0], []).append(t)
    raw = {t: {} for t in terms}
    for fn in _list_src_files(aa_dir):
        base = fn[:-4]
        path = os.path.join(aa_dir, fn)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.read().split("\n")
        except OSError:
            continue
        for i, line in enumerate(lines):
            if not line:
                continue
            cand = set()
            for ch in set(line):
                for t in by_head.get(ch, ()):
                    cand.add(t)
            for t in cand:
                if t in line:
                    raw[t].setdefault(base, []).append(i)
    hits, counts = {}, {}
    for t, chs in raw.items():
        if not chs:
            continue
        # 排序看真命中数（不封顶），同数按章名升序（稳定）
        ordered = sorted(chs.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:max_ch]
        hits[t] = {b: idxs[:max_lines] for b, idxs in ordered}
        counts[t] = {b: len(idxs) for b, idxs in ordered}
    return hits, counts


def hits_counts(hits):
    """（兼容用）{term: {base: n}} —— 以**已封顶**的行列表长估算；正式口径请用 build 的 counts。"""
    return {t: {b: len(v) for b, v in chs.items()} for t, chs in (hits or {}).items()}


def sort_pairs(hits, counts, term):
    """(base, 行列表, 真命中数) 列表，按真命中数降序（同数按章名升序）——展示顺序的唯一口径。"""
    h = (hits or {}).get(term) or {}
    c = (counts or {}).get(term) or {}
    rows = [(b, list(v), int(c.get(b, len(v)))) for b, v in h.items()]
    rows.sort(key=lambda r: (-r[2], r[0]))
    return rows


def load_cache(work_dir):
    """读缓存（sig 校验交给调用方）；失败/不存在返回 None。"""
    p = os.path.join(work_dir, CACHE_NAME)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("schema") != SCHEMA or not isinstance(d.get("命中"), dict):
            return None
        return d
    except (OSError, ValueError):
        return None


def save_cache(work_dir, sig, hits, counts=None):
    """落盘（原子替换，失败不抛）。"""
    p = os.path.join(work_dir, CACHE_NAME)
    tmp = p + ".tmp"
    d = {"schema": SCHEMA, "生成时间": time.strftime("%Y-%m-%d %H:%M:%S"),
         "sig": sig, "词数": len(hits), "命中": hits, "计数": counts or {}}
    try:
        os.makedirs(work_dir, exist_ok=True)
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, p)
        return p
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return ""


def load_or_build(work_dir, cand_path, aa_dir, terms, allow_build=True):
    """取命中表：缓存 sig 命中即用；否则（allow_build）重算并落盘。

    返回 (hits, counts, info)；
      info = {"from": "cache"|"built"|"empty", "sig":…, "词数":…}
    `allow_build=False`（只读场景）时缺缓存返回空表。
    """
    sig = compute_sig(cand_path, aa_dir, terms)
    d = load_cache(work_dir)
    if d and d.get("sig") == sig:
        return (d.get("命中") or {}, d.get("计数") or {},
                {"from": "cache", "sig": sig, "词数": d.get("词数")})
    if not allow_build:
        return {}, {}, {"from": "empty", "sig": sig, "词数": 0}
    hits, counts = build(aa_dir, terms)
    save_cache(work_dir, sig, hits, counts)
    return hits, counts, {"from": "built", "sig": sig, "词数": len(hits)}
