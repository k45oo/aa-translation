# -*- coding: utf-8 -*-
"""章节与行域：章节清单/区间、行读写与保存、单行渲染、行分类 override、台词 gold 标注、
阅读页骨架（work_page/reader_page 与 toc 标签）、查找与角色提示。"""
import importlib
import json
import os
import re
import time
from fastapi import HTTPException, Request
from pydantic import BaseModel
from aa_web_app import web_state
from aa_web_app import logic
from aa_web_app import reader_win
from common import gen_aa_html as _gen_aa_html
from aa_web_app.web_state import (_DATA, _LOCK, _art_checker, _art_sources, _load_progress, app, check_chapter, templates)
from aa_web_app.routers.site_url import _FULLW_DIGITS, _chapter_summary, _split_summary
from fastapi import APIRouter

router = APIRouter()


# ---------- 原站 URL 解析（2026-08-31：非 r401 作品也显示「原站」按钮）----------


@router.get("/work/{work}/read")
def reader_page(request: Request, work: str, f: str = ""):
    """阅读器：240px 深色侧边栏 + 单章渲染（帖头颜色/骰子红/1D10 加粗 + 原帖链接）。

    目录 = 合并总目录（原文 aa/*.txt 全集，标注未译章，优先显示汉化）；
    默认模式：有汉化的章显示汉化，无汉化的章显示原文；切换按钮本地双渲染。
    永远只渲染一章，38MB 级大作品也不卡；键盘 ← → 翻章。URL: ?f=<章节文件名>"""
    cfg = web_state.cfg_of(work)
    aa_dir, out_dir = cfg["aa_dir"], cfg["out_dir"]
    order_path = os.path.join(cfg["work_dir"], "chapter_order.json")
    mch = logic.merged_chapters(aa_dir, out_dir, cfg.get("name_style"), order_path)
    if not mch:
        raise HTTPException(404, f"作品 {work} 暂无章节（aa 原文目录为空）")
    resume_line = 0
    saved = _load_progress().get("aa", {}).get(work, {})

    def _same_chapter(ch):
        """请求的章 == 存档的章（原文名或汉化名任一命中）→ 才续读，避免跨章误跳"""
        sf = saved.get("fname", "")
        return bool(sf) and sf in (ch["tr_file"], ch["src_file"])

    # 当前章：优先显式 f（支持 原文名 或 汉化名）；否则续读；否则第一章
    if f:
        fname = None
        for ch in mch:
            if f in (ch["tr_file"], ch["src_file"]):
                fname = ch
                break
        if fname is None:
            fname = mch[0]
        # 2026-09-21：显式 f 也续读——URL 里的章就是存档章时（刷新 / 书签 / 返回），
        # 否则刷新页面跳回章首（用户实测「刷新后不在原位置」）
        if _same_chapter(fname) and saved.get("line"):
            resume_line = int(saved.get("line", 0) or 0)
    else:
        sf = saved.get("fname", "")
        fname = None
        for ch in mch:
            if sf in (ch["tr_file"], ch["src_file"]):
                fname = ch
                break
        if fname is None:
            fname = mch[0]
        if saved.get("line"):
            resume_line = int(saved.get("line", 0) or 0)
    cur_idx = mch.index(fname)
    # 默认显示汉化（无汉化 → 原文）
    use_tr = fname["has_tr"]
    base = logic.txt_key(fname["src_file"])
    # 章节上下文（2026-09-21 S1，reader_win）：行文本/colormap/meta/override/sfx/gold 一次装载并缓存，
    # 行 HTML 按需渲染（区间 API）。旧实现每次请求把整章 ×3 份渲染成 JSON 内联下发（大章 12–22 MB、
    # DOM 6–10 万节点）。本函数输出保持与旧实现**逐字节一致**（S1 有 curl A/B 锁定），S2 起才换轻量数据。
    ctx = reader_win.load_ctx(cfg, work, fname)
    gold = ctx["gold"]
    # 窗口化（S2/S3）：只下发几何表，行 HTML 由前端按窗口向 /range 区间接口取（页面体积与行数无关）
    win_data = reader_win.page_data(ctx, "tr" if use_tr else "src")
    # 逃生口（方案 §6）：?win=0 → 前端全量渲染（整章 DOM，排障/对照；见 static/reader.js 的 FULL）
    win_mode = request.query_params.get("win", "1") != "0"
    title, src_url = fname["title"], fname["src_url"]
    if not src_url:
        m = logic.EID_RE.search(fname["src_file"])
        if m:
            src_url = cfg["url_pattern"].format(eid=m.group(1))
    # 章节简介（2026-09-02）：作品知识库 章节简介 块，阅读页头部小字展示
    # 拆两段：剧情（模板 clamp 2 行）+ 末尾角色罗列换行展示
    sum_plot, sum_chars = _split_summary(_chapter_summary(work, cfg, base))
    # 侧边栏目录：完整标题（汉化优先，保留副标题），未译章标「未译」
    # 汉化标题翻译缓存：<work>/title_translations.json 键=日文标题 → 值=汉化标题（touhou 模式）
    tcache = {}
    tcache_path = os.path.join(cfg["work_dir"], "title_translations.json")
    if os.path.exists(tcache_path):
        try:
            tcache = json.load(open(tcache_path, encoding="utf-8"))
        except Exception:
            tcache = {}
    labels = {}
    # 2026-08-28: 缓存键可能是剥作品名前缀的标题(translate_titles.py 剥前缀),
    # ch["title"] 含全前缀 → 直查会 miss。同时尝试剥前缀键。
    _pref = (cfg.get("title") or "").strip()
    for ch in mch:
        raw = ch["title"] or logic.txt_key(ch["src_file"])
        # 汉化章：优先翻译缓存（日文标题→汉化），否则汉化文件首行本身
        _bare = raw
        if _pref and raw.startswith(_pref):
            _bare = raw[len(_pref):].lstrip("\u3000 ").strip()
        lab = tcache.get(_bare) or tcache.get(raw) or raw
        labels[ch["src_file"]] = toc_label(lab, cfg.get("title", ""))
    # 目录条目：src_file 作为 URL 锚点，前端根据模式切换正文（本地双渲染）
    toc = []
    for ch in mch:
        _r18j = logic.r18_chapter_judge(cfg.get("work_dir", ""),
                                        logic.txt_key(ch["src_file"])) or {}
        toc.append({
            "src_file": ch["src_file"], "tr_file": ch["tr_file"],
            "has_tr": ch["has_tr"],
            "label": labels[ch["src_file"]],
            "title": ch["title"],
            "active": ch is fname,
            # 2026-08-24 R18 章节级（读 prep）
            "r18_hit": _r18j.get("r18_hit", ""),
            "r18_hits": _r18j.get("r18_word_hits", 0),
        })
    # 2026-08-28 候选确认: ?cand=1 时注入候选列表 + 各候选来源章 term 命中行号
    cand_data = None
    if request.query_params.get("cand") == "1":
        cand_p = os.path.join(cfg["work_dir"], "glossary_candidates.json")
        if os.path.exists(cand_p):
            try:
                _cd = json.load(open(cand_p, encoding="utf-8"))
                _cands = [c for c in _cd.get("ai_候选", []) if not c.get("确认")]
                _t_order = {"术语": 0, "专有名词": 1, "拟声梗": 2, "角色": 3}
                def _ck(c):
                    srcs = str(c.get("来源") or "")
                    return (_t_order.get(c.get("type") or "", 9), -len([x for x in srcs.split(",") if x.strip()]))
                _cands.sort(key=_ck)
                # 命中行号: 对每个候选, 遍历其来源章原文, 找含 term 的行 idx
                for c in _cands:
                    term = (c.get("term") or "").strip()
                    c["命中"] = {}
                    if not term:
                        continue
                    for src in str(c.get("来源") or "").split(","):
                        src = src.strip()
                        if not src:
                            continue
                        # 来源是 _台词.txt 文件名 → 归一为原文 base(去 _台词 后缀), 供阅读器按 base 匹配
                        base = src[:-len("_台词.txt")] if src.endswith("_台词.txt") else logic.txt_key(src).replace("_台词", "")
                        sp = os.path.join(cfg["aa_dir"], base + ".txt")
                        if not os.path.exists(sp):
                            continue
                        try:
                            lines = logic.read_lines(sp)
                        except Exception:
                            continue
                        hits = [i for i, l in enumerate(lines) if term in l]
                        if hits:
                            c["命中"][base] = hits[:20]
                # ?ci=<n> 初始候选索引(候选确认跳章还原进度)
                _ci = request.query_params.get("ci", "")
                try:
                    _ci_i = int(_ci) if str(_ci).isdigit() else 0
                except Exception:
                    _ci_i = 0
                cand_data = {"candidates": _cands, "total": len(_cands), "init_idx": _ci_i}
            except Exception:
                cand_data = None
    return templates.TemplateResponse(request, "reader.html", {
        "work": work, "cfg": cfg, "toc": toc, "mch": mch,
        # 翻译替换弹窗的「章节多选过滤层」（2026-10-01）：只列已汉化章，按阅读器目录顺序
        "tr_chapters": [{"file": ch["tr_file"], "label": labels[ch["src_file"]],
                         "num": i + 1} for i, ch in enumerate(toc) if ch["has_tr"]],
        "cur": fname["src_file"], "cur_idx": cur_idx,
        "has_tr": fname["has_tr"], "use_tr": use_tr,
        "title": title, "src_url": src_url,
        "summary_plot": sum_plot, "summary_chars": sum_chars,
        "win_data": win_data, "win_mode": win_mode,
        "total": len(mch), "resume_line": resume_line, "active": "works",
        "cand_data": cand_data, "gold": gold,
    })


def gen_aa_html_render(i, l, cm, site, src_line=None, sfx_marks=None):
    return _gen_aa_html.render_line(i, l, cm, site, src_line, sfx_marks)


def toc_label(lab, work_title):
    """阅读器目录标题清洗：去「（汉化）」字样 + 剥离作品名前缀。

    - 去「（汉化）」「(汉化)」尾巴（白头汉化标题自带）
    - 剥离作品名前缀：找「第N话/話/回」首次出现（N 支持阿拉伯/汉字数字）；
      若其前缀包含作品名 → 从话数处截断，保留副标题；
      前缀不含作品名（如白头『回归篇 第一话…』、touhou『第2話…』）
      或短篇集（标题无话数、是独立作品名）→ 保留完整。
    - 尾部站点名「 - xxxまとめ」/「| YomuAA」剥掉。
    """
    s = lab.strip()
    for suf in ("（汉化）", "(汉化)"):
        if s.endswith(suf):
            s = s[: -len(suf)].rstrip()
    # 尾部站点名
    for sep in (" - ", " | "):
        if sep in s:
            head, tail = s.split(sep, 1)
            if tail and re.search(r"まとめ|Yomu|チェック|r401|本棚", tail):
                s = head
    if work_title:
        m = re.search(r"第\s*[0-9０-９一二三四五六七八九十百千]+\s*[话話回]", s)
        if m:
            prefix = s[: m.start()]
            if work_title in prefix:
                s = s[m.start():]
        else:
            # 「そのN」「その(N)」格式（无第N话）
            m = re.search(r"その\s*[0-9０-９一二三四五六七八九十百千()（）]+", s)
            if m and work_title in s[: m.start()]:
                s = s[m.start():]
            else:
                # 作品名 + 末尾话数（如 kikkoman『…キッコーマン～　１０８』）
                i = s.find(work_title)
                if i != -1:
                    rest = s[i + len(work_title):].strip()
                    if rest and re.fullmatch(r"[0-9０-９　\s]+", rest):
                        s = rest.strip()
    return s or lab


def short_chapter_label(lab, fname):
    """阅读器目录短标签：只留话数，不含章节标题。

    提取优先级：标题内「第N话/第N話」（取第一个）→ 标题末尾的全角/半角话数
    （如 kikkoman「…～　０５」）→ 文件名 eid 回退「番外#eid」。
    正文页顶 h2 仍显示完整标题，这里只压缩目录文字。"""
    s = lab.translate(_FULLW_DIGITS)          # 全角数字归一，便于匹配
    m = re.search(r'第\s*(\d+)\s*[话話]', s)
    if m:
        return f'第{int(m.group(1))}话'
    m = re.search(r'[　\s](?:第\s*)?(\d{1,4})\s*$', s)
    if m:
        return f'第{int(m.group(1))}话'
    m = logic.EID_RE.search(fname)
    if m:
        return f'番外#{m.group(1)}'
    return lab


# ---------- API ----------


class LineSave(BaseModel):
    text: str
    type: str = ""               # 编辑类型；**空串 = 自动判定**（2026-10-01），显式值 = 人工优先
    line_type: str = ""          # 人工修正的行类型（text/mixed/aa/header/blank），仅记录
    sfx: list = []               # 新增 sfx 标记 [[字符偏移, 文本], ...]（划词替换/「sfx 替换」按钮）
    sfx_clear: list = []         # 清与该区间重叠的 sfx 标记 [[偏移, 长度], ...]（普通替换时清掉旧标记）
    sfx_clear_all: bool = False  # 清该行全部 sfx 标记


def _log_edits_bulk(cfg, fname, rows, etype, line_type=""):
    """批量追加修改记录到单文件 work_dir/edits.jsonl（时间/章节/行/类型/行类型/改前/改后）。

    rows = [(idx0, before, after), ...]（idx0 为 0-based 行号）。
    2026-10-01: 翻译替换/术语替换一次改数千行 → 单次打开顺序写（逐条 _log_edit 会
    反复开关文件）。这条日志是这些改动**唯一**的前后数据留档（不再整文件备份）。
    """
    if not rows:
        return 0
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    log_path = os.path.join(cfg["work_dir"], "edits.jsonl")
    with open(log_path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"ts": ts, "work": cfg.get("title"), "file": fname,
                                "line": r[0] + 1, "type": etype,
                                "line_type": line_type,
                                "before": r[1], "after": r[2]},
                               ensure_ascii=False) + "\n")
    return len(rows)


def _log_edit(cfg, fname, idx, etype, before, after, line_type=""):
    """修改记录（单行）：追加到 work_dir/edits.jsonl（时间/章节/行/类型/行类型/改前/改后）。"""
    _log_edits_bulk(cfg, fname, [(idx, before, after)], etype, line_type)


def _line_is_art(cfg, fname, idx, src_lines):
    """该行是否 art（纯图形）：override > work/line_meta_v2 的 kind > line_type_of(src)。

    复用翻译替换那套 `_art_sources` + `_art_checker`（与阅读器行号列同一分类链，
    不另造一套）；src_lines 由调用方读一次传入。
    """
    base = logic.txt_key(logic.src_name_of(fname))
    meta, overrides = _art_sources(cfg, base)
    return _art_checker(meta, overrides, lambda: src_lines)(idx)


def _resolve_edit_type(cfg, fname, idx, src_lines, old, new_text, body_type):
    """行编辑类型：人工显式选择优先；空串 → 自动分类（2026-10-01）。

    返回 (type, auto)：auto=True 表示类型由代码判定（前端 radio 停在「自动」）。
    art 行（纯图形）不判 → 沿用旧默认「错位」；未知的非空值按旧行为落「错位」。
    比较用**提交值** new_text 与盘上旧值 old（pad 补位造成的差异不参与判定）。
    """
    t = (body_type or "").strip()
    if t:
        return (t if t in logic.MANUAL_EDIT_TYPES else logic.AUTO_EDIT_DEFAULT), False
    src_line = src_lines[idx] if 0 <= idx < len(src_lines) else ""
    kind = "aa" if _line_is_art(cfg, fname, idx, src_lines) else None
    return (logic.classify_edit(src_line, old, new_text, kind=kind)
            or logic.AUTO_EDIT_DEFAULT), True


@router.get("/api/work/{work}/chapter/{fname}/line/{idx}")
def api_line_get(work: str, fname: str, idx: int):
    """单行数据（弹窗编辑用）：src 原文 + tr 当前译文。

    未译章（只传原文名）无 tr → tr 返回 ''，编辑由前端拦截（hasTr=False）。"""
    cfg = web_state.cfg_of(work)
    check_chapter(cfg, fname)
    src_name = logic.src_name_of(fname) if (fname.endswith("_汉化.txt") or fname.endswith("_汉化.txt.gz")) else fname
    src = logic.read_lines(logic.txt_path(cfg["aa_dir"], src_name))
    if not 0 <= idx < len(src):
        raise HTTPException(404, f"行号越界: {idx + 1}")
    tr_path = logic.txt_path(cfg["out_dir"], fname) if (fname.endswith("_汉化.txt") or fname.endswith("_汉化.txt.gz")) else None
    tr_lines = logic.read_lines(tr_path) if tr_path and os.path.exists(tr_path) else []
    src_l = src[idx] if idx < len(src) else ""
    marks = logic.load_sfx_marks(cfg["work_dir"], logic.txt_key(src_name))
    return {"idx": idx, "src": src_l, "tr": tr_lines[idx] if idx < len(tr_lines) else "",
            "line_type": logic.line_type_of(src_l),
            "sfx": marks.get(str(idx + 1), [])}


@router.get("/api/work/{work}/edits")
def api_edits(work: str, limit: int = 100):
    """最近修改记录（edits.jsonl 尾部 limit 条）。"""
    cfg = web_state.cfg_of(work)
    log_path = os.path.join(cfg["work_dir"], "edits.jsonl")
    if not os.path.exists(log_path):
        return {"edits": []}
    with open(log_path, encoding="utf-8") as f:
        lines = [json.loads(l) for l in f if l.strip()]
    return {"edits": lines[-limit:]}


@router.get("/api/work/{work}/chapter/{fname}")
def api_chapter(work: str, fname: str):
    cfg = web_state.cfg_of(work)
    check_chapter(cfg, fname)
    aa_dir, out_dir = cfg["aa_dir"], cfg["out_dir"]
    src_name = logic.src_name_of(fname) if (fname.endswith("_汉化.txt") or fname.endswith("_汉化.txt.gz")) else fname
    tr_path = logic.txt_path(out_dir, fname) if (fname.endswith("_汉化.txt") or fname.endswith("_汉化.txt.gz")) else None
    tr = logic.read_lines(tr_path) if tr_path and os.path.exists(tr_path) else []
    return {"file": fname,
            "tr": tr,
            "src": logic.read_lines(logic.txt_path(aa_dir, src_name))}


@router.get("/api/work/{work}/chapter/{fname}/line/{idx}/html")
def api_line_html(work: str, fname: str, idx: int):
    cfg = web_state.cfg_of(work)
    check_chapter(cfg, fname)
    aa_dir, out_dir = cfg["aa_dir"], cfg["out_dir"]
    tr_path = logic.txt_path(out_dir, fname)
    tr_lines = logic.read_lines(tr_path)
    if not 0 <= idx < len(tr_lines):
        raise HTTPException(404, f"行号越界: {idx + 1}")
    base = logic.txt_key(logic.src_name_of(fname))    # 汉化名 → 原文基名（colormap/sfx_marks 键一致）
    # 原文行 + sfx 标记：与阅读页渲染同口径（2026-09-15 起补传 src_line——
    #   旧实现丢参，保存后重渲染的行会掉 sfx 特效；base 也按原文名归一，colormap 不再失配）
    src_lines = logic.read_lines(logic.txt_path(aa_dir, logic.src_name_of(fname)))
    src_l = src_lines[idx] if idx < len(src_lines) else ""
    marks = {str(idx + 1): logic.load_sfx_marks(cfg["work_dir"], base).get(str(idx + 1), [])}
    cm_path = os.path.join(aa_dir, base + "_colormap.json")
    cm = json.load(open(cm_path, encoding="utf-8")) if os.path.exists(cm_path) else {}
    site = importlib.import_module("sites." + cfg["site"])
    html = gen_aa_html_render(idx, tr_lines[idx], cm, site, src_l, marks)
    return {"idx": idx, "html": html or " "}


def chapter_entry(cfg, fname):
    """按章节名（原文名或汉化名）取 merged_chapters 条目；找不到 → 404（供区间 API 用）。"""
    order_path = os.path.join(cfg["work_dir"], "chapter_order.json")
    mch = logic.merged_chapters(cfg["aa_dir"], cfg["out_dir"], cfg.get("name_style"), order_path)
    for ch in mch:
        if fname in (ch["tr_file"], ch["src_file"]):
            return ch
    raise HTTPException(404, f"章节不存在: {fname}")


@router.get("/api/work/{work}/chapter/{fname}/range")
def api_chapter_range(work: str, fname: str, from_k: int = 0, to_k: int = -1, mode: str = "tr"):
    """阅读器窗口化：取区间行数据（k = 可渲染行序号，0-based；行 HTML 按需渲染）。

    单次最多 reader_win.BLOCK_MAX 行（防一次拉整章）；to_k < 0 → 从 from_k 起取一个块。
    mode: tr（无汉化退原文）/ src / refine（无精译退 tr/src），响应里给实际生效模式。
    """
    cfg = web_state.cfg_of(work)
    check_chapter(cfg, fname)
    if mode not in reader_win.MODES:
        raise HTTPException(422, f"未知模式: {mode}（可选 {'/'.join(reader_win.MODES)}）")
    ctx = reader_win.load_ctx(cfg, work, chapter_entry(cfg, fname))
    if to_k < 0:
        to_k = from_k + reader_win.BLOCK_MAX - 1
    m, rows = reader_win.rows(ctx, mode, from_k, to_k)
    return {"mode": m, "from": rows[0]["k"] if rows else 0,
            "to": rows[-1]["k"] if rows else -1, "n": ctx["n"],
            "hasTr": ctx["has_tr"], "hasRefine": ctx["has_refine"], "lines": rows}


@router.put("/api/work/{work}/chapter/{fname}/line/{idx}")
def api_save_line(work: str, fname: str, idx: int, body: LineSave):
    cfg = web_state.cfg_of(work)
    check_chapter(cfg, fname)
    if not (fname.endswith("_汉化.txt") or fname.endswith("_汉化.txt.gz")):
        raise HTTPException(422, "未汉化章节不可编辑（仅原文可读）")
    aa_dir, out_dir, work_dir = cfg["aa_dir"], cfg["out_dir"], cfg["work_dir"]
    tr_path = logic.txt_path(out_dir, fname)
    aa_path = logic.txt_path(aa_dir, logic.src_name_of(fname))
    cur_lines = logic.read_lines(tr_path)
    old = cur_lines[idx] if idx < len(cur_lines) else ""
    with _LOCK:
        r = logic.save_line(aa_path, tr_path, idx, body.text,
                            work_names=logic.load_work_glossary(work)["known_names"])
    web_state.invalidate_stats(work)
    logic.invalidate_mch_cache()   # 编辑可能改首行标题 → 章节列表失效（目录mtime对内容修改不可靠）
    if r["result"] == "error":
        raise HTTPException(422, r["message"])
    if r["result"] == "ok":
        # 2026-10-01: 类型自动分类（人工显式选择优先；radio 停在「自动」→ 空串 → 代码判定）
        src_lines = logic.read_lines(aa_path) if os.path.exists(aa_path) else []
        etype, auto = _resolve_edit_type(cfg, fname, idx, src_lines, old, body.text,
                                        body.type)
        _log_edit(cfg, fname, idx, etype, old, r["text"], body.line_type or "")
        r["type"], r["type_auto"] = etype, auto
    # sfx 标记（划词翻译替换）——与文本保存解耦：内容未变（unchanged）也要能加/清标记
    sbase, line_no = logic.txt_key(logic.src_name_of(fname)), idx + 1
    added = cleared = 0
    if body.sfx_clear_all:
        _ok, _err, cleared = logic.clear_sfx_marks(work_dir, sbase, line_no, None)
    for rng in (body.sfx_clear or []):
        try:
            a, ln = int(rng[0]), int(rng[1])
        except (TypeError, ValueError, IndexError):
            continue
        _ok, _err, n = logic.clear_sfx_marks(work_dir, sbase, line_no, [a, ln])
        cleared += n
    for pair in (body.sfx or []):
        try:
            off, txt = pair[0], pair[1]
        except (TypeError, ValueError, IndexError):
            continue
        _ok, _err, act = logic.add_sfx_mark(work_dir, sbase, line_no, off, txt)
        added += 1 if act == "add" else 0
    r["sfx_changed"] = bool(added or cleared)
    r["sfx_added"], r["sfx_cleared"] = added, cleared
    r["sfx_marks"] = logic.load_sfx_marks(work_dir, sbase).get(str(line_no), [])
    return r


# ---------- 人工行分类 override（只喂图片加大，翻译 flag 不动） ----------


class OverrideSave(BaseModel):
    kind: str


def _override_base(cfg, chapter):
    """路由参数 chapter（base 或完整文件名）→ base 章节名，并校验章节存在。"""
    fname = chapter if (chapter.endswith("_汉化.txt") or chapter.endswith("_汉化.txt.gz")) else chapter + "_汉化.txt"
    check_chapter(cfg, fname)
    return logic.txt_key(fname)


@router.get("/api/override/{work}/{chapter}")
def api_override_get(work: str, chapter: str):
    """读整章人工行分类 override：{1-based行号: kind}（稀疏：只存与自动不一致的行）。"""
    cfg = web_state.cfg_of(work)
    return logic.load_overrides(cfg["work_dir"], _override_base(cfg, chapter))


@router.put("/api/override/{work}/{chapter}/{line}")
def api_override_put(work: str, chapter: str, line: int, body: OverrideSave):
    """写单行 override；kind ∈ text/art/mixed/header/blank/auto（auto=删除=还原为自动）。"""
    cfg = web_state.cfg_of(work)
    base = _override_base(cfg, chapter)
    if line < 1:
        raise HTTPException(422, f"行号必须 ≥1: {line}")
    if body.kind == "auto":
        with _LOCK:
            ok, err = logic.delete_override(cfg["work_dir"], base, line)
    elif body.kind in logic.OVERRIDE_KINDS:
        with _LOCK:
            ok, err = logic.save_override(cfg["work_dir"], base, line, body.kind)
    else:
        raise HTTPException(422, f"非法 kind: {body.kind}（可选 {'/'.join(logic.OVERRIDE_KINDS)}/auto）")
    if not ok:
        raise HTTPException(500, err)
    return {"ok": True, "line": line, "kind": body.kind}


# ---------- 台词高亮 gold 标注（2026-08-29 第 2 步，精译 ground truth 回归集） ----------


class GoldSave(BaseModel):
    is_dialogue: bool


@router.get("/api/gold/{work}/{chapter}")
def api_gold_get(work: str, chapter: str):
    """读整章 golden 台词标注：{1-based行号: bool}（稀疏：只存人工纠正过的行）。"""
    cfg = web_state.cfg_of(work)
    return logic.load_gold(cfg["work_dir"], _override_base(cfg, chapter))


@router.put("/api/gold/{work}/{chapter}/{line}")
def api_gold_put(work: str, chapter: str, line: int, body: GoldSave):
    """写单行 golden 台词标注；is_dialogue 必填 bool（true=是台词/false=不是台词）。"""
    cfg = web_state.cfg_of(work)
    base = _override_base(cfg, chapter)
    if line < 1:
        raise HTTPException(422, f"行号必须 ≥1: {line}")
    with _LOCK:
        ok, err = logic.save_gold(cfg["work_dir"], base, line, body.is_dialogue)
    if not ok:
        raise HTTPException(500, err)
    return {"ok": True, "line": line, "is_dialogue": body.is_dialogue}


@router.delete("/api/gold/{work}/{chapter}/{line}")
def api_gold_delete(work: str, chapter: str, line: int):
    """删除单行 golden 标注（恢复为自动，幂等）。"""
    cfg = web_state.cfg_of(work)
    base = _override_base(cfg, chapter)
    if line < 1:
        raise HTTPException(422, f"行号必须 ≥1: {line}")
    with _LOCK:
        ok, err = logic.delete_gold(cfg["work_dir"], base, line)
    if not ok:
        raise HTTPException(500, err)
    return {"ok": True, "line": line}


@router.get("/api/work/{work}/search")
def api_search(work: str, q: str = ""):
    cfg = web_state.cfg_of(work)
    if not q.strip():
        return {"hits": []}
    hits = logic.search_work(cfg["out_dir"], cfg["aa_dir"], q.strip(),
                             name_style=cfg.get("name_style"))
    return {"hits": hits, "total": len(hits)}


@router.get("/api/work/{work}/find")
def api_find(work: str, q: str = "", scope: str = "both", limit: int = 300):
    """阅读器全库搜索（2026-10-07）：只搜 text/mixed 行，中日/单侧可选。

    与 /api/work/{work}/search（work 页术语小搜索）分开：这个走字节级扫描 + 行类型索引，
    810 章大作品也能 1~3 秒出结果（首次会建 work/line_kinds.json 索引，约 10 秒）。"""
    cfg = web_state.cfg_of(work)
    scope = scope if scope in ("both", "zh", "jp") else "both"
    r = logic.search_lines(cfg, work, q, scope=scope, limit=max(1, min(int(limit or 300), 2000)))
    return r


class MakeHtmlBody(BaseModel):
    """「仅 HTML 文件」简版的三个选项（作品页第二个生成按钮）。"""
    tr_only: bool = False       # 仅译文（页面不做译/原切换、不存原文数据）
    split: int = 0              # 每 N 章一个文件；0/负数 = 全部章放一个文件
    out_dir: str = ""           # 输出目录；留空 = 交付 HTML 目录（aaManga/交付HTML）


def _role_hint(jp):
    """查 roles.json 返回 jp 的疑似角色信息: (中文, [作品名...]) 或 None。

    2026-09-01: 作品术语表疑似角色展示——替换对 jp 命中角色(日文/别名)
    时标注角色中文+作品, 供人工确认(避免 阿熊→阿库娅 式错翻)。
    """
    try:
        with open(os.path.join(_DATA, "roles.json"), encoding="utf-8") as f:
            roles = json.load(f).get("角色", [])
    except Exception:
        return None
    ws = {}
    try:
        with open(logic._GLOSSARY_JSON, encoding="utf-8") as f:
            for w in json.load(f).get("作品", []):
                ws[w["id"]] = w["名称"]
    except Exception:
        pass
    for r in roles:
        # 2026-09-01: 艺术纹理/碎片 分类是 AA 绘画碎片(ツモ/ぃぃ 等), 非真角色, 不展示
        if (r.get("分类") or "") in ("艺术纹理/碎片", "texture"):
            continue
        if r.get("日文") == jp:
            wnames = [ws.get(w, w) for w in (r.get("作品") or []) if w != "yaruo"]
            return {"cn": r.get("中文") or jp, "works": wnames}
        for al in (r.get("别名") or []):
            if al == jp:
                wnames = [ws.get(w, w) for w in (r.get("作品") or []) if w != "yaruo"]
                return {"cn": r.get("中文") or jp, "works": wnames, "alias": True}
    return None


class ApplyGlossaryBody(BaseModel):
    pairs: list = []   # 前端选中的 [日文, 中文] 列表
