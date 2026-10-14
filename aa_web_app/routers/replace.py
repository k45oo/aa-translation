# -*- coding: utf-8 -*-
"""替换域：翻译替换（预览/应用/历史/备份恢复）、替换对预筛、预览扫描结果复用。"""
import os
import re
import threading
import time
from fastapi import HTTPException
from pydantic import BaseModel
from aa_web_app import web_state
from aa_web_app import logic
from aa_web_app.web_state import (_LOCK, _art_checker, _art_sources, app)
from aa_web_app.routers.chapters import _log_edits_bulk
from fastapi import APIRouter

router = APIRouter()


# ---------- 翻译替换（阅读器手动替换对 → 本作品全部 汉化.txt，2026-09-09） ----------


def _chapter_art_idxs(cfg, base, n, src_lines):
    """返回 0-based 行号集合：整章有效行分类为 art(aa) 的行。

    全量判定（逐行过分类链）——只在「单章重算」路径上用；预览/应用的常规扫描
    只判命中行（见 _chapter_replace_plan），避免整部作品逐行分类的开销。
    """
    meta, overrides = _art_sources(cfg, base)
    chk = _art_checker(meta, overrides, lambda: src_lines)
    return {i for i in range(n) if chk(i)}


class ReplacePairsBody(BaseModel):
    pairs: list = []     # [[查找, 替换为], ...]（手动输入）
    scope: str = "all"   # all = 全部已汉化章；from = 当前话及后续话（2026-09-16）
    start: str = ""      # scope="from" 的起始章（汉化文件名，前端传 CFG.fname）
    chapters: list = []  # 章节勾选过滤层（2026-10-01）：汉化文件名，空 = 不过滤（在 scope 之上取交集）


def _norm_manual_pairs(raw):
    """手动替换对归一：(查找, 替换为, sfx标记) 三元组；查找侧为空丢弃，同对去重（保序取首个）。

    2026-09-13: 前端会把「历史记录里勾选启用的对」拼在手工对后面，去重防同一对
    被应用两次（结果虽幂等，但预览计数会翻倍误导）。
    2026-09-21: 第 3 位 = 该对是否打 sfx 样式（历史里的对是二元组 → 默认 False）。
    去重键只用前两位——同一对既手工勾了 sfx 又从历史带进来时，手工那条优先（保序取首个）。
    """
    out, seen = [], set()
    for p in raw or []:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        f0, r0 = p[0], p[1]
        if not isinstance(f0, str) or not f0.strip():
            continue
        repl = r0 if isinstance(r0, str) else ""
        sfx = bool(p[2]) if len(p) > 2 else False
        if (f0, repl) in seen:
            continue
        seen.add((f0, repl))
        out.append((f0, repl, sfx))
    return out


def _sfx_pairs_of(pairs):
    """从归一后的三元组里取「要打 sfx 标记」的查找串集合。"""
    return tuple(jp for jp, _zh, s in pairs if s)


def _chapter_tr_files(cfg):
    """本作品全部已汉化章节文件（规范名，.gz 透明；磁盘缺失的清单项跳过）。"""
    out = []
    for fname in logic.chapter_order(cfg["out_dir"], cfg.get("name_style")):
        if os.path.exists(logic.txt_path(cfg["out_dir"], fname)):
            out.append(fname)
    return out


def _canon_tr(fname):
    """任意汉化文件名（含 .txt.gz / 已剥后缀）→ 规范名 entry_N_汉化.txt。"""
    k = logic.txt_key(fname or "")
    if k.endswith("_汉化"):
        k = k[:-len("_汉化")]
    return k + "_汉化.txt"


def _ordered_tr_files(cfg):
    """已汉化章节文件，顺序 = 阅读器目录顺序（chapter_order.json 优先）。

    「当前话及后续话」按阅读器目录顺序切片——否则配了 chapter_order.json 的作品
    （r401 原站顺序）会切错范围。名称用规范名（.txt），与阅读器 ?f= 口径一致。
    """
    out = []
    try:
        order_path = os.path.join(cfg["work_dir"], "chapter_order.json")
        mch = logic.merged_chapters(cfg["aa_dir"], cfg["out_dir"],
                                    cfg.get("name_style"), order_path)
        for ch in mch:
            if not ch.get("has_tr"):
                continue
            if os.path.exists(logic.txt_path(cfg["out_dir"], ch["tr_file"])):
                out.append(ch["tr_file"])
    except Exception:
        out = []
    known = {_canon_tr(f) for f in out}
    for f in _chapter_tr_files(cfg):     # 兜底补齐（aa 原文缺失但汉化还在的章）
        cf = _canon_tr(f)
        if cf in known:
            continue
        known.add(cf)
        out.append(cf)
    return out


def _scope_tr_files(cfg, scope, start, chapters=()):
    """按范围取章节文件：all = 全部已汉化章；from = 当前话及后续话（含当前话）。

    2026-10-01: chapters（规范名集合，非空时）在范围之上**再收一层**——对范围切片取
    交集，保持阅读器目录顺序；空 = 不过滤（旧行为不变）。
    """
    files = _ordered_tr_files(cfg)
    if (scope or "all") == "from":
        key = _canon_tr(start)
        idx = next((i for i, f in enumerate(files) if _canon_tr(f) == key), None)
        if idx is None:
            raise HTTPException(422, "定位不到当前章节（%s），无法按「当前话和后续话」限定范围"
                                % (start or "空"))
        files = files[idx:]
    if chapters:
        want = set(chapters)
        files = [f for f in files if _canon_tr(f) in want]
    return files


def _norm_chapters(raw):
    """章节过滤层归一 → 规范名元组（排序去重；供 _scope_tr_files 取交集 + 计划指纹）。"""
    out = {_canon_tr(c.strip()) for c in (raw or []) if isinstance(c, str) and c.strip()}
    return tuple(sorted(out))


def _chapter_replace_ctx(cfg, fname):
    """单章替换上下文：(src_path, tr_path, art_idx)。"""
    tr_path = logic.txt_path(cfg["out_dir"], fname)
    src_name = logic.src_name_of(fname)
    src_path = logic.txt_path(cfg["aa_dir"], src_name)
    base = logic.txt_key(fname)
    src_lines = logic.read_lines(src_path) if os.path.exists(src_path) else []
    n = len(logic.read_lines(tr_path))
    art = _chapter_art_idxs(cfg, base, n, src_lines)
    return src_path, tr_path, art


# ---- 替换对预筛（2026-09-17 预览提速）--------------------------------------


# 整章「一对都不可能命中」时零成本跳过：不读原文、不做逐行 art 分类。
def _compile_pairs(pairs):
    """[(is_re, matcher)]：正则只编译一次（原文在逐行循环里 re.compile 是浪费）。

    pairs 兼容 (jp, zh) / (jp, zh, sfx) —— 只取查找侧。
    """
    out = []
    for p in pairs:
        jp = p[0]
        if jp.startswith("re:"):
            try:
                out.append((True, re.compile(jp[3:], re.M)))
            except re.error:
                continue
        else:
            out.append((False, jp))
    return out


def _pairs_hit_text(cpairs, text):
    r"""整篇预筛：任一对可能在 text 里命中。

    只允许假阳性、绝不允许假阴性——正则按 re.M 整篇 search 是逐行 search 的超集
    （`.`/`^`/`$` 都不跨行；`\A`/`\Z` 整篇语义不同 → 直接判命中，不预筛）。
    """
    for is_re, m in cpairs:
        if is_re:
            if "\\A" in m.pattern or "\\Z" in m.pattern:
                return True
            if m.search(text):
                return True
        elif m in text:
            return True
    return False


def _pairs_hit_line(cpairs, line):
    """单行是否可能出现替换（真替换逻辑仍由 logic.apply_glossary_chapter 执行）。"""
    for is_re, m in cpairs:
        hit = m.search(line) if is_re else (m in line)
        if hit:
            return True
    return False


def _chapter_replace_plan(cfg, fname, pairs, sfx_pairs=()):
    """扫描单章 → (tr_path, changes, art_hit, sfx_marks)。

    changes = [(idx, before, after, overwide)]（0-based；AA 艺术行已跳过、宽度已校正）。
    sfx_marks = {1-based 行号: [[偏移, 插入文本], ...]}——只含勾了 sfx 的替换对命中的插入片段
    （偏移已换算到替换后的行上，跨多对替换也准；见 logic.apply_glossary_chapter 的 sfx_out）。
    两级筛选：① 整篇预筛不中 → 直接返回（零原文读取、零行分类）
    ② 命中行才做 art 判定（与行号列同口径；原文按需惰性读）→ 再交
    `logic.apply_glossary_chapter` 走同一套替换/宽度逻辑（唯一实现，不复制）。
    假阳性（预筛命中但替换无变化）由第二步自然消化。art_hit = 命中行里被 art 保护跳过的行数。
    """
    tr_path = logic.txt_path(cfg["out_dir"], fname)
    tr = logic.read_lines(tr_path)
    cpairs = _compile_pairs(pairs)
    if not _pairs_hit_text(cpairs, "\n".join(tr)):
        return tr_path, [], 0, {}
    cand = [i for i, b in enumerate(tr)
            if not logic.is_skip_line(b) and _pairs_hit_line(cpairs, b)]
    if not cand:
        return tr_path, [], 0, {}
    src_path = logic.txt_path(cfg["aa_dir"], logic.src_name_of(fname))
    meta, overrides = _art_sources(cfg, logic.txt_key(fname))
    _src = [None]

    def _lazy_src():
        if _src[0] is None:
            _src[0] = logic.read_lines(src_path) if os.path.exists(src_path) else []
        return _src[0]

    chk = _art_checker(meta, overrides, _lazy_src)
    art = {i for i in cand if chk(i)}
    sfx_out = {}
    changed, _skipped = logic.apply_glossary_chapter(
        src_path, tr_path, pairs, dry_run=True, skip_idx=art,
        sfx_pairs=sfx_pairs, sfx_out=sfx_out)
    return (tr_path,
            [(c[0], c[1], c[2], bool(c[3]) if len(c) > 3 else False) for c in changed],
            len(art),
            {i + 1: v for i, v in sfx_out.items()})


# ---- 预览扫描结果复用（2026-09-17）----------------------------------------


# 预览把逐章扫描结果（只含有变更的章）缓存下来；应用直接落这些行，不再全量重扫。
# 落地前逐文件校验（mtime+size 与逐行 before），过期只重算那一章——绝不误写。
_PLAN_CACHE = {}            # key -> {"ts": float, "files": [...]}


_PLAN_CACHE_MAX = 4         # 只留最近几次预览（本地单用户，够用不吃内存）


_PLAN_MAX_LINES = 120000    # 变更行超量时不缓存（应用退回现扫，防内存爆）


_PLAN_LOCK = threading.Lock()


def _plan_key(work, scope, start, pairs, chapters=()):
    """预览/应用共用指纹：作品 + 范围 + 章节过滤 + 替换对（归一后，含 sfx 勾选）。

    2026-10-01: chapters 进指纹——改章节勾选即视为另一个计划，预览缓存不会串。
    """
    return (work, scope, _canon_tr(start) if scope == "from" else "",
            tuple(chapters or ()), tuple(pairs))


def _plan_put(key, files):
    with _PLAN_LOCK:
        _PLAN_CACHE[key] = {"ts": time.time(), "files": files}
        while len(_PLAN_CACHE) > _PLAN_CACHE_MAX:
            _PLAN_CACHE.pop(min(_PLAN_CACHE, key=lambda k: _PLAN_CACHE[k]["ts"]))


def _plan_get(key):
    with _PLAN_LOCK:
        e = _PLAN_CACHE.get(key)
        return e["files"] if e else None


def _plan_drop(key):
    with _PLAN_LOCK:
        _PLAN_CACHE.pop(key, None)


def _scan_replace_plan(cfg, work, pairs, scope, start, chapters=()):
    """逐章扫描（预览与应用共用）→ (files, chapters, total, art_hit)。

    files = [{"file": 规范名, "tr_path", "changes": [(idx, before, after, overwide)], "sfx": {1-based行: [[off,text]]}}]
    只含真有变更的章；结果写进 _PLAN_CACHE 供 apply 直接复用。
    chapters（2026-10-01）= 勾选章节过滤层，与 scope 取交集后才是实扫章。
    """
    sfx_pairs = _sfx_pairs_of(pairs)
    scope_files = _scope_tr_files(cfg, scope, start, chapters)
    files, total, art_hit = [], 0, 0
    for fname in scope_files:
        tr_path, changed, hit, marks = _chapter_replace_plan(cfg, fname, pairs, sfx_pairs)
        art_hit += hit
        if not changed:
            continue
        files.append({"file": _canon_tr(fname), "tr_path": tr_path,
                      "art_hit": hit, "changes": changed, "sfx": marks})
        total += len(changed)
    if total <= _PLAN_MAX_LINES:
        _plan_put(_plan_key(work, scope, start, pairs, chapters), files)
    return files, len(scope_files), total, art_hit


def _plan_writes(cfg, plan, pairs):
    """应用前校验缓存计划 → [{"file","tr_path","idxs","lines","sfx"}]（只含真有变更的章）。

    直写条件：文件仍在 + 每个待改行的当前内容与预览时的 before 逐字相同；
    任一不符（预览后该章被改过/重译过）→ 只重算该章（单章扫描，不整库重扫）。
    2026-10-01: 每章带 changes = [(idx0, before, after), ...]——调用方逐行记
    work/edits.jsonl（改动前后数据的唯一留档，不再整文件备份）。
    """
    sfx_pairs = _sfx_pairs_of(pairs)
    out = []
    for f in plan:
        tr_path = f["tr_path"]
        if not os.path.exists(tr_path):
            continue
        lines = logic.read_lines(tr_path)
        marks = f.get("sfx") or {}
        fresh = all(0 <= c[0] < len(lines) and lines[c[0]] == c[1] for c in f["changes"])
        if fresh:
            idxs = []
            for c in f["changes"]:
                lines[c[0]] = c[2]
                idxs.append(c[0])
            changes = [(c[0], c[1], c[2]) for c in f["changes"]]
        else:
            src_path, tr_path2, art = _chapter_replace_ctx(cfg, f["file"])
            sfx_out = {}
            changed, _sk = logic.apply_glossary_chapter(
                src_path, tr_path2, pairs, dry_run=True, skip_idx=art,
                sfx_pairs=sfx_pairs, sfx_out=sfx_out)
            if not changed:
                continue
            lines = logic.read_lines(tr_path2)
            idxs = []
            for c in changed:
                lines[c[0]] = c[2]
                idxs.append(c[0])
            changes = [(c[0], c[1], c[2]) for c in changed]
            tr_path = tr_path2
            marks = {i + 1: v for i, v in sfx_out.items()}
        out.append({"file": f["file"], "tr_path": tr_path, "idxs": idxs,
                    "changes": changes, "lines": lines, "sfx": marks})
    return out


@router.post("/api/work/{work}/replace-preview")
def api_replace_preview(work: str, body: ReplacePairsBody):
    """翻译替换预览（按范围，dry-run 不落盘；扫描结果缓存给「应用」复用）。

    scope: all=全部已汉化章 / from=当前话及后续话（start=当前章汉化文件名）。
    返回 {scope, chapters, total, art_skipped, sfx_total, files:[{file, changed, art_skipped, sfx, lines}]}，
    lines = [1-based 行号, before[:160], after[:160], overwide]。
    art_skipped = 命中行里被 AA 艺术行保护跳过的行数（整章逐行 art 全量判定已不做）。
    sfx_total / files[].sfx = 勾了「sfx」的对命中的插入片段处数（应用后会写成
    <work_dir>/sfx_marks/<base>.json 标记，渲染成楷体斜体）。
    """
    cfg = web_state.cfg_of(work)
    pairs = _norm_manual_pairs(body.pairs)
    if not pairs:
        raise HTTPException(422, "请至少输入一对「查找 / 替换为」")
    scope = "from" if (body.scope or "all") == "from" else "all"
    chapters = _norm_chapters(body.chapters)
    plan, chapters_n, total, art_total = _scan_replace_plan(
        cfg, work, pairs, scope, body.start, chapters)
    files = [{"file": f["file"], "changed": len(f["changes"]),
              "art_skipped": f.get("art_hit", 0),
              "sfx": sum(len(v) for v in (f.get("sfx") or {}).values()),
              "lines": [[c[0] + 1, c[1][:160], c[2][:160], c[3]]
                        for c in f["changes"]]} for f in plan]
    return {"scope": scope, "chapters": chapters_n, "total": total,
            "picked": len(chapters),
            "art_skipped": art_total, "sfx_pairs": list(_sfx_pairs_of(pairs)),
            "sfx_total": sum(f["sfx"] for f in files), "files": files}


@router.post("/api/work/{work}/replace-apply")
def api_replace_apply(work: str, body: ReplacePairsBody):
    """应用翻译替换（按范围；逐行记改前/改后 + SRC 宽度校正）。

    优先复用「预览」的逐章扫描结果（不再全量重扫）；无预览缓存/缓存过期时才
    现扫或按章重算。**不再整文件备份到 work/backup/<ts>/**（2026-10-01 用户要求：
    备份目录/文件过多）——每个改动行写进单文件 work/edits.jsonl（含改前/改后，
    阅读器「修改记录」可查，可逐行还原）。
    """
    cfg = web_state.cfg_of(work)
    pairs = _norm_manual_pairs(body.pairs)
    if not pairs:
        raise HTTPException(422, "请至少输入一对「查找 / 替换为」")
    scope = "from" if (body.scope or "all") == "from" else "all"
    chapters = _norm_chapters(body.chapters)
    # 2026-10-01：章节条只影响「写哪些章」——先按**预览时的键**（不带章节过滤）找缓存，
    # 勾选集非空时再从缓存计划里筛掉没勾的章（无需重扫）；没有缓存才按章节过滤现扫。
    key_full = _plan_key(work, scope, body.start, pairs, ())
    plan = _plan_get(key_full)
    source = "preview"
    if plan is not None:
        if chapters:
            want = set(chapters)
            plan = [f for f in plan if _canon_tr(f["file"]) in want]
    else:
        source = "rescan"
        plan, _ch, _tt, _ah = _scan_replace_plan(cfg, work, pairs, scope, body.start, chapters)
    plan = _plan_writes(cfg, plan, pairs)
    # 计划已消费（落盘后文件内容已变，留着也是过期）；两个键都清
    _plan_drop(key_full)
    _plan_drop(_plan_key(work, scope, body.start, pairs, chapters))
    if not plan:
        return {"ok": True, "applied": 0, "scope": scope, "source": source,
                "picked": len(chapters), "files": []}
    with _LOCK:
        for p in plan:
            logic.write_lines(p["tr_path"], p["lines"])
        # 逐行记改前/改后（唯一留档，替代原「整文件备份目录」）
        logged = 0
        for p in plan:
            logged += _log_edits_bulk(cfg, p["file"], p.get("changes") or [], "翻译替换")
    web_state.invalidate_stats(work)
    applied = sum(len(p["idxs"]) for p in plan)
    # 2026-09-21: 勾了 sfx 的对 → 把命中的插入片段写成 sfx 标记（渲染成楷体斜体）
    sfx_added, sfx_failed = 0, []
    for p in plan:
        marks = p.get("sfx") or {}
        if not marks:
            continue
        # sfx 标记按**原文基名**落盘（与阅读器渲染/行保存同口径：src_name_of → txt_key）
        base = logic.txt_key(logic.src_name_of(p["file"]))
        ok, err, n = logic.merge_sfx_marks(cfg["work_dir"], base, marks)
        if ok:
            sfx_added += n
        else:
            sfx_failed.append({"file": p["file"], "error": err})
    # 2026-09-13: 记进作品替换历史（弹窗可直接勾选复用）；只记本次真正生效的调用
    try:
        logic.tr_hist_add(cfg["work_dir"], pairs, applied=applied, files=len(plan))
    except Exception as e:      # 历史写入失败不影响替换结果
        print(f"[replace-apply] 历史记录写入失败: {e}", flush=True)
    return {"ok": True, "applied": applied, "scope": scope, "source": source,
            "logged": logged, "picked": len(chapters),
            "sfx_marks": sfx_added, "sfx_failed": sfx_failed,
            "files": [{"file": p["file"], "applied": len(p["idxs"]),
                       "idxs": p["idxs"][:200]} for p in plan]}


@router.get("/api/work/{work}/replace-history")
def api_replace_history(work: str):
    """翻译替换历史记录（本作品，置顶在前、其余新在前）——弹窗「历史替换记录」用。

    只读；写入在 replace-apply 成功后自动发生（<work_dir>/tr_replace_history.json），
    置顶位由 POST .../replace-history/pin 改。
    """
    cfg = web_state.cfg_of(work)
    items = logic.tr_hist_load(cfg["work_dir"])
    return {"items": items, "max": logic.TR_HIST_MAX,
            "pinned": sum(1 for it in items if it.get("pin"))}


class TrHistPin(BaseModel):
    find: str = ""       # 查找串（与历史里存的完全一致，不做 strip）
    repl: str = ""       # 替换为（空 = 「删除该串」那条记录）
    pin: bool = True     # True = 置顶，False = 取消置顶


@router.post("/api/work/{work}/replace-history/pin")
def api_replace_history_pin(work: str, body: TrHistPin):
    """手动置顶 / 取消置顶一条历史替换记录（2026-10-13）。

    置顶的条目排在列表最前，且不占 TR_HIST_MAX 的淘汰名额（不会被旧记录挤掉）。
    """
    cfg = web_state.cfg_of(work)
    find = body.find or ""
    if not find:
        raise HTTPException(status_code=422, detail="find 不能为空")
    with _LOCK:
        found, items = logic.tr_hist_pin(cfg["work_dir"], find, body.repl or "", body.pin)
    if not found:
        raise HTTPException(status_code=404, detail="历史里找不到这一条（可能已被清除，刷新后重试）")
    return {"ok": True, "items": items, "max": logic.TR_HIST_MAX,
            "pinned": sum(1 for it in items if it.get("pin"))}


@router.get("/api/work/{work}/backups")
def api_backups(work: str):
    cfg = web_state.cfg_of(work)
    return {"backups": logic.list_backups(os.path.join(cfg["work_dir"], "backup"))}


@router.post("/api/work/{work}/backups/{ts}/restore")
def api_restore(work: str, ts: str):
    cfg = web_state.cfg_of(work)
    backup_root = os.path.join(cfg["work_dir"], "backup")
    with _LOCK:
        try:
            restored = logic.restore_backup(backup_root, ts, cfg["out_dir"])
        except ValueError as e:
            raise HTTPException(404, str(e))
    web_state.invalidate_stats(work)
    return {"ok": True, "restored": restored}


class GlossaryPayload(BaseModel):
    known_names: list = []
    glossary: list = []
    roles: list = []
