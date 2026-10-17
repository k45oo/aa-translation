# -*- coding: utf-8 -*-
"""作品域：列表/搜索/详情、work_meta 与素材、添加作品与下载、阅读状态与进度。"""
import json
import os
import re
import time
import registry
from fastapi import HTTPException, Request
from pydantic import BaseModel
from aa_web_app import web_state
from aa_web_app import logic
from aa_web_app import task_manager as TM
from aa_web_app import add_work
from aa_web_app.web_state import (IMAGE_MANGA, _AUTHOR_OVERRIDE, _DATA, _INDEX_SNAP, _LASTUPD_CACHE, _LOCK, _READ_STATE_FILE, _SCRIPTS, _STATS_CACHE, _WORK_META_FILE, _deliver_artifacts, _load_progress, _load_read_state, _save_progress, app, generator_of, r18_summary_cached, templates, work_stats)
from aa_web_app.routers.admin import api_reload
from aa_web_app.routers.terms import _mat_scan_from_knowledge
from fastapi import APIRouter

router = APIRouter()


# ---------- 作品添加/下载（2026-08-30）----------


def _registry_path():
    r"""registry.py 的**真实可写位置** —— 一律取 `registry.__file__`（app 实际 import 的那份）。

    不能用 `_SCRIPTS/works/registry.py`：冻结 exe 里 `_SCRIPTS` 指向 PyInstaller **解包目录**，
    而启动引导会把那份 registry 删掉（防 shell 子任务误命中数据根之外的副本），于是写它 =
    FileNotFoundError → 「删除作品」在前面把文件删完之后报 500 → 用户看到
    **「文件已删除但记录还在」**（2026-10-09 实测，bar_miku / MM 两部都中招）。
    """
    p = getattr(registry, "__file__", None)
    if p and os.path.isfile(p):
        return p
    return os.path.join(_SCRIPTS, "works", "registry.py")


def _append_registry_entry(work_id, entry):
    """registry.py 末尾 WORKS dict 内追加条目（行级安全操作，禁 find+切片拼接）。

    WORKS 结构：`WORKS = {` —— 在最后一个条目后、闭合 `}` 前插入。
    """
    rp = _registry_path()
    with open(rp, "r", encoding="utf-8") as f:
        text = f.read()
    # 找 WORKS 里最后一个条目结尾 "    ),"（bar_miku_r 的），在其后插入新条目
    marker = "\n\ndef get(work)"
    idx = text.rindex(marker)
    tail_end = text.rindex("    ),", 0, idx) + len("    ),")
    # 构造新条目文本（与现有一致缩进）
    lines = []
    lines.append(f'    "{work_id}": dict(')
    for k, v in entry.items():
        if isinstance(v, str):
            # 路径含反斜杠 → repr 原样转义
            if "\\" in v:
                lines.append(f'        {k}={v!r},')
            elif "{" in v:
                lines.append(f'        {k}="{v}",')
            else:
                lines.append(f'        {k}="{v}",')
        elif isinstance(v, bool):
            lines.append(f'        {k}={str(v)},')
        else:
            lines.append(f'        {k}={v!r},')
    lines.append("    ),")
    new_block = "\n" + "\n".join(lines)
    text = text[:tail_end] + new_block + text[tail_end:]
    with open(rp, "w", encoding="utf-8") as f:
        f.write(text)


_SITE_PREFIX_RE = re.compile(r"^(?:やる夫スレ本棚[^ |]*?)\s*[|｜]\s*")


def _work_id_from_title(title, kind, r401_cid):
    """生成作品 id：剥站名前缀/特殊字符 → ASCII+日文混合（禁 ◆★|｜ 等文件系统/URL 特殊字符）。"""
    s = title.strip()
    s = _SITE_PREFIX_RE.sub("", s)   # 剥「やる夫スレ本棚2号館 | 」式站名前缀
    s = re.sub(r"[^\w\u3040-\u30ff\u4e00-\u9fff]+", "_", s)
    s = re.sub(r"[_]+", "_", s).strip("_")
    if not s:
        s = f"{kind}_{r401_cid or 'new'}"
    base, i = s[:40], 2
    while base in registry.WORKS:
        base = f"{s[:36]}_{i}"
        i += 1
    return base


@router.post("/api/parse-url")
def api_parse_url(body: dict = None):
    """解析分类页 URL → 作品信息（标题/话数/简介/章节清单）。body: {url, translate_intro?}"""
    if not body or not body.get("url"):
        raise HTTPException(422, "缺少 url")
    info = add_work.parse_url(body["url"])
    if not info.get("ok"):
        raise HTTPException(422, info.get("error", "解析失败"))
    # 2026-09-20 用户要求：简介**默认不自动翻译**（手动翻译走 POST /api/translate-intro，
    # 添加作品弹窗的「翻译简介」按钮）。显式传 translate_intro: true 仍可解析时顺带翻译。
    intro_cn, translated = "", False
    if body.get("translate_intro") and info.get("description"):
        intro_cn, translated = add_work.translate_desc(info["description"])
    return {
        "ok": True,
        "kind": info["kind"],
        "title": info["title"],
        "chapters": info["chapters"],
        "chapter_count": len(info["chapters"]),
        "intro_jp": info["description"],
        "intro_cn": intro_cn or info["description"],
        "intro_translated": translated,
        "site": info["site"],
        "site_key": info.get("site_key"),
        "r401_cid": info["r401_cid"],
        "work_id": _work_id_from_title(info["title"], info["kind"], info.get("r401_cid")),
    }


class IntroReq(BaseModel):
    text: str = ""
    r18_level: str = ""      # high（整部走 r18 链）时翻译走 r18 安全端点


@router.post("/api/translate-intro")
def api_translate_intro(body: IntroReq):
    """手动翻译作品简介（添加作品弹窗「翻译简介」按钮；2026-09-20 起不再自动翻译）。

    走 add_work.translate_desc → aa_translate.call_api（AA_BACKEND 未设 = [default] commandcode 网关）。
    翻译失败（含内容审查拒绝）回退日文原文，返回 translated=False 供前端提示。
    """
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(422, "简介为空")
    zh, ok = add_work.translate_desc(text, work_r18=(body.r18_level == "high"))
    return {"ok": True, "text": zh, "translated": bool(ok)}


@router.post("/api/add-work")
def api_add_work(body: dict = None):
    """确认后注册作品 + 入队下载。body: {url, work_id?, cn_title, author, status, r18_level, intro}"""
    if not body or not body.get("url"):
        raise HTTPException(422, "缺少 url")
    info = add_work.parse_url(body["url"])
    if not info.get("ok"):
        raise HTTPException(422, info.get("error", "解析失败"))
    cn_title = (body.get("cn_title") or "").strip() or info["title"]
    # 作品 ID 一律自动生成（用户不再填写；body.work_id 忽略）
    work_id = _work_id_from_title(cn_title, info["kind"], info.get("r401_cid"))
    with _LOCK:   # 防连点：整个注册段串行化（单进程多线程，双击/多请求不重复注册）
        if work_id in registry.WORKS:
            raise HTTPException(409, f"作品 {work_id} 已存在（{registry.WORKS[work_id].get('title')}）")
        # 目录冲突防护：即使 registry key 不同，目标目录已有非空 aa/汉化/raw 也拒绝
        _d = os.path.join(registry.BASE, "aaManga", work_id)
        _existing_dirs = [x for x in ("aa", "汉化", "raw") if os.path.isdir(os.path.join(_d, x)) and os.listdir(os.path.join(_d, x))]
        if _existing_dirs:
            raise HTTPException(409, f"目录 aaManga/{work_id} 已存在非空子目录（{', '.join(_existing_dirs)}），请改作品 ID 或手动处理")
        work_dir = os.path.join(registry.BASE, "aaManga", work_id, "work")
        # 1. 章节清单 → work/chapters.json（yaruobook 系 cache 消费；r401 系也留一份兜底）
        add_work.save_chapters_json(work_dir, info["chapters"])
        # 2. registry 追加条目
        entry = add_work.build_registry_entry(work_id, info, cn_title,
                                              body.get("author", ""), body.get("status", ""),
                                              body.get("r18_level", ""), body.get("intro", ""))
        _append_registry_entry(work_id, entry)
        # 3. work_meta.json
        meta = logic.load_work_meta(_WORK_META_FILE)
        existing = meta.get(work_id, {})
        existing["author"] = logic.normalize_author(body.get("author"))
        existing["cn_name"] = cn_title
        existing["jp_title"] = info["title"]
        existing["status"] = (body.get("status") or "").strip()
        existing["intro"] = (body.get("intro") or "").strip() or info.get("description", "")
        existing["r18_level"] = body.get("r18_level", "") if body.get("r18_level") in ("high", "low", "medium", "") else ""
        existing.setdefault("tags", [])
        existing.setdefault("materials", [])
        existing.setdefault("关联度", "serial")
        meta[work_id] = existing
        ok = logic.save_work_meta(_WORK_META_FILE, meta)
        if not ok:
            raise HTTPException(500, "保存 work_meta.json 失败")
    # 4. 热加载 + 入队下载（锁外：网络/调度器操作不持锁）
    api_reload()
    r = TM.enqueue("download", work_id, {"chapters": "all"})
    if not r.get("ok"):
        return {"ok": False, "work": work_id, "error": r.get("error"), "task_id": None}
    return {"ok": True, "work": work_id, "task_id": r["task_id"]}


# ---------- 原站 URL 解析（2026-08-31：非 r401 作品也显示「原站」按钮）----------


@router.get("/work/{work}")
def work_page(request: Request, work: str):
    cfg = web_state.cfg_of(work)
    st = work_stats(work)
    gen_rel, gen_args = generator_of(work)
    resume = _load_progress().get("aa", {}).get(work, {})
    resume_title = ""
    if resume.get("fname"):
        for c in st["chapters"]:
            if c["file"] == resume["fname"]:
                resume_title = c["title"] or c["file"]
                break
    # 2026-08-28 候选确认: 未确认候选数
    cand_n = 0
    _cp = os.path.join(cfg["work_dir"], "glossary_candidates.json")
    if os.path.exists(_cp):
        try:
            _cd = json.load(open(_cp, encoding="utf-8"))
            cand_n = len([c for c in _cd.get("ai_候选", []) if not c.get("确认")])
        except Exception:
            pass
    return templates.TemplateResponse(request, "work.html", {
        "cand_n": cand_n,
        "work": work, "cfg": cfg, "stats": st, "resume": resume,
        "resume_title": resume_title,
        "unsynced": logic.count_unsynced_edits(cfg["work_dir"], st.get("html_mtime")),
        "generator": f"{gen_rel} {' '.join(gen_args)}".strip(),
        "wiki_ready": TM.wiki_configured(work),
        "active": "works",
    })


# ========== 作品级元数据（work_meta）：详情弹窗编辑 汉化名/标签/素材/作者 ==========


class WorkMeta(BaseModel):
    author: str = ""
    cn_name: str = ""
    status: str = ""
    intro: str = ""
    tags: list = []
    materials: list = []
    r18_level: str = ""   # 2026-08-26: high/low/medium/空(no) 人工可改
    rating: int | None = None   # 2026-09-03: 评分 1-5，缺省=未评分
    review: str = ""          # 2026-09-03: 评价文字
    pipeline: str = ""        # 2026-09-22: 管线归属 generic/pokemon(空=不改)


# ---------- 生成术语候选（2026-09-06 grilling ②, 后台 subprocess + 状态轮询） ----------


@router.get("/api/work-meta/{work}")
def api_work_meta_get(work: str):
    """单作品元数据 + 素材出现话数（详情弹窗）。2026-08-26: materials 返回对象数组(含pct/chapters)。"""
    cfg = web_state.cfg_of(work)
    meta = logic.load_work_meta(_WORK_META_FILE)
    m = meta.get(work, {})
    mat_list = list(m.get("materials", []) or [])
    if m.get("ignore_materials"):
        mat_list = []   # 2026-08-26: jikan_yaluo 等特殊作品忽略素材统计
    # 一次 material_frequency_cached 得 全部素材 count+chapters(复用首页, 避免逐素材循环卡顿)
    mf = logic.material_frequency_cached(cfg.get("work_dir", ""), cfg.get("out_dir", ""), mat_list)
    tot = sum(v["count"] for v in mf.values() if isinstance(v, dict))
    mats = []
    for name in mat_list:
        v = mf.get(name)
        if not isinstance(v, dict):
            continue
        c, ch = v.get("count", 0), v.get("chapters", 0)
        dn, wname, note = logic.material_display_parts(name)
        if dn is None:
            continue
        pct = round(c * 100 / tot, 1) if tot else 0.0
        if pct < 5:
            continue   # 2026-08-26: 排除 5% 以下
        mats.append({"name": dn, "works": wname or "", "note": note or "",
                     "count": c, "chapters": ch,
                     "pct": pct})
    # 出现话数(逐素材懒算, 详情弹窗已改为异步加载)
    chapters = {}
    for name in mat_list:
        chapters[name] = logic.material_chapters_cached(cfg.get("work_dir", ""), cfg.get("out_dir", ""), name)
    # 作者：手动值优先 → 覆盖表 → 帖头◆提取（与列表页同口径，2026-09-13 修详情为空/保存丢作者）
    _a_manual = logic.normalize_author(m.get("author"))
    _a_auto = (_AUTHOR_OVERRIDE.get(work)
               or logic.extract_author_cached(cfg.get("work_dir", ""), cfg.get("aa_dir", "")) or "")
    return {"work": work,
            "author": _a_manual,
            "author_auto": "" if _a_manual else _a_auto,
            "author_is_auto": bool(not _a_manual and _a_auto),
            "cn_name": m.get("cn_name", ""),
            "status": m.get("status", ""),
            "intro": m.get("intro", ""),
            "jp_title": m.get("jp_title", "") or cfg.get("title", work),
            "site": cfg.get("site_name") or cfg.get("site"),
            "r18": bool(cfg.get("r18")),
            "r18_level": m.get("r18_level", ""),
            "r18_summary": r18_summary_cached(cfg.get("work_dir", ""), cfg.get("out_dir", "")),
            "tags": list(m.get("tags", []) or []),
            "materials": mats,
            "material_chapters": chapters,
            "rating": m.get("rating"),
            "pipeline": m.get("pipeline", ""),
            "review": m.get("review", "")}


@router.put("/api/work-meta/{work}")
def api_work_meta_put(work: str, body: WorkMeta):
    web_state.cfg_of(work)
    meta = logic.load_work_meta(_WORK_META_FILE)
    existing = meta.get(work, {})
    # 只更新允许编辑的字段；jp_title/site 等不动
    # 2026-09-13: author 走 normalize_author——'◆'(空输入被加前缀) 视为清空，
    # 否则会写成真值 '◆' 遮蔽列表页的帖头◆自动提取（用户报「保存导致作者丢失」）
    existing["author"] = logic.normalize_author(body.author)
    existing["cn_name"] = (body.cn_name or "").strip()
    existing["status"] = (body.status or "").strip()
    existing["intro"] = (body.intro or "").strip()
    existing["tags"] = [str(t).strip() for t in body.tags if str(t).strip()]
    # 2026-08-26: materials 存「角色名（作品名）」——同名跨作品唯一, 保留作品后缀
    # (只 trim 不去括号; 兼容旧纯名数据原样保留)
    existing["materials"] = [str(x).strip() for x in body.materials if str(x).strip()]
    # 2026-08-26: r18_level 人工可改(high/low/medium/空=no), 非法值丢弃保留原值
    if body.r18_level in ("high", "low", "medium", ""):
        existing["r18_level"] = body.r18_level
    # 2026-09-03: rating 1-5 或 None(未评分), 非法丢弃保留原值; review 自由文本
    if body.rating is None or (isinstance(body.rating, int) and 1 <= body.rating <= 5):
        existing["rating"] = body.rating
    existing["review"] = (body.review or "").strip()
    # 2026-09-22 方案 D11: pipeline 管线归属(generic/pokemon), 非法值丢弃保留原值;
    #   改动会切换翻译链(宝可梦专属链 = 固定内容表预填 + 本地 wiki 术语绑定)
    if body.pipeline in ("generic", "pokemon"):
        existing["pipeline"] = body.pipeline
    meta[work] = existing
    with _LOCK:
        ok = logic.save_work_meta(_WORK_META_FILE, meta)
    if not ok:
        raise HTTPException(500, "保存 work_meta.json 失败")
    # 2026-10-14: tags/materials/status 会被烘进首页快照（_build_index_data 读 work_meta）
    # → 必须标脏，否则首页最长 10 分钟（DEFAULT_MAX_AGE=600）显示旧值，用户以为没保存
    _INDEX_SNAP.invalidate(work)
    return {"ok": True, "work": work}


@router.get("/api/materials/resolve")
def api_materials_resolve(name: str):
    """2026-08-26: 素材名 → 候选作品(出处)列表, 供手动添加时识别/选择。

    同名角色多作品时前端弹选择; 查不到返回空数组(可手动输入作品名)。
    """
    return {"ok": True, "candidates": logic.resolve_material(name)}


@router.post("/api/work-meta/{work}/mat-scan")
def api_work_meta_mat_scan(work: str):
    """2026-08-26: 扫描汉化文本自动统计素材并写回 work_meta materials。

    用全局术语表角色表匹配(中文名+日文原名), 排除 texture/yaruo/昵称梗,
    取出现≥3次且占比≥5% 的前8个, 写回 materials。返回统计结果。
    """
    import glob as _glob
    from collections import Counter
    cfg = web_state.cfg_of(work)
    meta = logic.load_work_meta(_WORK_META_FILE)
    m = meta.setdefault(work, {})
    if m.get("ignore_materials"):
        return {"ok": True, "skipped": True, "materials": []}
    out = cfg.get("out_dir")
    if not out or not os.path.isdir(out):
        raise HTTPException(400, "无汉化目录")
    files = [os.path.join(out, n) for n in logic.list_txt(out, "_汉化.txt")]
    if not files:
        return {"ok": True, "skipped": True, "materials": [], "msg": "无汉化文件"}
    # 2026-08-28: 知识库人物优先(作品专属素材, 精准); 空时回退全局角色表
    kn_mat = _mat_scan_from_knowledge(work, cfg, files)
    if kn_mat is not None:
        mats = [k for k, _ in kn_mat]
        m["materials"] = mats
        with _LOCK:
            _ok = logic.save_work_meta(_WORK_META_FILE, meta)
        if not _ok:      # 2026-10-14: 原来忽略保存结果 → 保存失败也报成功（静默）
            raise HTTPException(500, "保存 work_meta.json 失败")
        _INDEX_SNAP.invalidate(work)      # materials 进首页快照 → 标脏
        return {"ok": True, "materials": mats, "counts": dict(kn_mat),
                "source": "knowledge", "msg": "作品知识库人物素材"}
    # 角色表(排除 texture/yaruo/昵称梗); 2026-08-26: 带作品名, mat key 用「名（作品名）」
    # 2026-09-01 三级重构: 角色块迁出 glossary.json → roles.json
    _gl = json.load(open(os.path.join(_DATA, "glossary.json"), encoding="utf-8"))
    _ws = {w["id"]: w["名称"] for w in _gl.get("作品", [])}
    _rl = json.load(open(os.path.join(_DATA, "roles.json"), encoding="utf-8")).get("角色", [])
    roles = []
    for c in _rl:
        if c.get("分类") == "texture":
            continue
        ws = c.get("作品") or []
        note = c.get("备注", "") or ""
        if "texture" in ws or "yaruo" in ws or "昵称" in note or "梗" in note:
            continue
        cn, jp = c.get("中文"), c.get("日文")
        if not cn and not jp:
            continue
        # 每个 (中文/日文名) → 作品名 (取第一个非 yaruo 作品)
        wname = next((_ws.get(w) for w in ws if w != "yaruo"), None)
        roles.append((cn, jp, wname))
    cnt = Counter()
    for f in files:
        t = open(f, encoding="utf-8", errors="replace").read()
        for cn, jp, wname in roles:
            # 2026-08-27: 匹配词集合(含短名/繁简变体) + 排除单字符(L 行号碰撞)
            words = [w for w in {cn, jp, cn, jp} if w and len(w) >= 2]
            n = sum(t.count(w) for w in words)
            if n:
                key = f"{cn or jp}（{wname}）" if wname else (cn or jp)
                cnt[key] += n
    tot = sum(cnt.values())
    mats = []
    for key, c in cnt.most_common():
        if c < 3:
            break
        pct = c * 100 / tot if tot else 0
        if pct < 5:
            break
        mats.append(key)
        if len(mats) >= 8:
            break
    m["materials"] = mats
    with _LOCK:
        ok = logic.save_work_meta(_WORK_META_FILE, meta)
    if not ok:
        raise HTTPException(500, "保存失败")
    _INDEX_SNAP.invalidate(work)          # materials 进首页快照 → 标脏
    return {"ok": True, "materials": mats, "counts": dict(cnt.most_common(10))}


# ---------- 阅读状态（read_state，作品列表改版 2026-09-03）----------


_READ_STATES = ("", "toread", "reading", "done")


def _save_read_state(data):
    try:
        with open(_READ_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


# ---------- 阅读进度 ----------


class ProgressAA(BaseModel):
    fname: str
    line: int = 1
    mode: str = ""   # 2026-09-03: tr/refine(汉化类) 自动置阅读中; src(原文) 不触发


class ProgressManga(BaseModel):
    chapter: str
    image: int = 0


@router.get("/api/aa/{work}/progress")
def api_aa_progress(work: str):
    web_state.cfg_of(work)
    return _load_progress().get("aa", {}).get(work, {})


@router.put("/api/aa/{work}/progress")
def api_aa_progress_put(work: str, body: ProgressAA):
    web_state.cfg_of(work)
    with _LOCK:
        data = _load_progress()
        data.setdefault("aa", {})[work] = {
            "fname": body.fname, "line": body.line,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        _save_progress(data)
        # 2026-09-03: mode=tr/refine(汉化类) 且 read_state != done → 自动置「阅读中」
        rs = _load_read_state()
        cur = rs.get(work, {}).get("state", "")
        new = logic.read_state_with_auto(cur, body.mode)
        if new != cur:
            rs.setdefault(work, {})["state"] = new
            rs[work]["updated"] = int(time.time())
            _save_read_state(rs)
    return {"ok": True}


@router.get("/api/manga/{name}/progress")
def api_manga_progress(name: str):
    if name not in IMAGE_MANGA:
        raise HTTPException(404, f"未知图片漫画: {name}")
    return _load_progress().get("manga", {}).get(name, {})


@router.put("/api/manga/{name}/progress")
def api_manga_progress_put(name: str, body: ProgressManga):
    if name not in IMAGE_MANGA:
        raise HTTPException(404, f"未知图片漫画: {name}")
    with _LOCK:
        data = _load_progress()
        data.setdefault("manga", {})[name] = {
            "chapter": body.chapter, "image": body.image,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        _save_progress(data)
    return {"ok": True}


# ---------- 作品列表改版（2026-09-03）：阅读状态 / 删除 / 章节名+正文搜索 ----------


def _all_work_dirs():
    """全部作品用到的目录规范路径集合（判删除共享根）。"""
    s = set()
    for _cfg in registry.WORKS.values():
        for _k in ('aa_dir', 'out_dir', 'work_dir', 'raw_dir'):
            _p = _cfg.get(_k)
            if _p:
                s.add(os.path.normpath(_p))
    return s


def _delete_roots():
    """删除边界白名单根（realpath 后）：仓库根 / 数据根 **及其下的 aaManga 数据目录**。

    为什么必须带 aaManga（2026-10-12 实测，ポケの子（？）(FC2) 删除 400）：`aaManga/` 常被
    做成 junction/symlink 指到别的盘（本机 `<repo>\\aaManga` → `E:\\aaManga`），
    于是作品目录 realpath 后落在仓库根之外，而旧检查只许 realpath 在 `registry.BASE` 内
    → **每一次删除**都被判「删除路径越界」（107 部作品全中，不只这一部）。
    数据目录本就是允许删的地方，故把它（realpath）一并进白名单；其它任意路径仍然拒绝。
    """
    roots = []
    for r in (registry.BASE, _DATA):
        if not r:
            continue
        for c in (os.path.realpath(r), os.path.realpath(os.path.join(r, "aaManga"))):
            if c not in roots:
                roots.append(c)
    return roots


def _in_delete_roots(p, roots):
    """p 的 realpath 是否落在白名单根内（junction 已在 roots 侧解析过，两侧口径一致）。"""
    pr = os.path.realpath(p)
    for r in roots:
        if pr == r or pr.startswith(r + os.sep):
            return True
    return False


class ReadStateBody(BaseModel):
    state: str = ""


@router.get("/api/read-state")
def api_read_state_get():
    return _load_read_state()


@router.put("/api/read-state/{work}")
def api_read_state_put(work: str, body: ReadStateBody):
    web_state.cfg_of(work)
    if body.state not in ("", "toread", "reading", "done"):
        raise HTTPException(422, f"非法阅读状态: {body.state}")
    with _LOCK:
        data = _load_read_state()
        if body.state == "":
            data.pop(work, None)   # 清除回空
        else:
            data.setdefault(work, {})["state"] = body.state
            data[work]["updated"] = int(time.time())
        _save_read_state(data)
    return {"ok": True, "work": work, "state": body.state}


@router.get("/api/work/{work}/delete-preview")
def api_delete_preview(work: str):
    cfg = web_state.cfg_of(work)
    surf = logic.delete_surface(cfg, _all_work_dirs())
    dirs = []
    for kind, p in surf['items']:
        info = {"path": p, "exists": os.path.exists(p)}
        if os.path.isdir(p):
            try:
                info["size"] = sum(os.path.getsize(os.path.join(r, f))
                                   for r, _, fs in os.walk(p) for f in fs)
            except OSError:
                info["size"] = 0
            info["chapters"] = len([f for f in os.listdir(p)
                                    if f.endswith(("_汉化.txt", "_汉化.txt.gz"))]) if os.path.isdir(p) else 0
        dirs.append(info)
    # 交付物件（文件夹交付 → 整个文件夹；旧单文件 → 该 .html）
    htmls = [{"path": p, "exists": True} for p in _deliver_artifacts(cfg)]
    if not htmls:
        _dn = os.path.basename(os.path.dirname(cfg.get("aa_dir", "")))
        if _dn:
            import glob as _glob
            # 交付物固定到 aaManga/交付HTML/（2026-10-10 起）；根目录为历史位置，仍兼容
            for _ddir in (os.path.join(_DATA, "aaManga", "交付HTML"),
                          os.path.join(_DATA, "交付HTML")):
                if not os.path.isdir(_ddir):
                    continue
                for pat_suffix in ("_合集.html", "_汉化合集.html"):
                    for f in _glob.glob(os.path.join(_ddir, _dn + "*" + pat_suffix)):
                        htmls.append({"path": f, "exists": os.path.exists(f)})
                if htmls:
                    break
    # work 级知识资产（若不在删除面内则列出）
    extra = []
    _wd = cfg.get("work_dir", "")
    if _wd and os.path.isdir(_wd):
        for _f in os.listdir(_wd):
            if _f.endswith("_knowledge.json") or _f.startswith("glossary_"):
                extra.append({"path": os.path.join(_wd, _f), "exists": True})
    return {"work": work, "shared_root": surf['shared'], "dirs": dirs,
            "htmls": htmls, "extra": extra}


@router.post("/api/work/{work}/delete")
def api_delete(work: str, body: dict = None):
    body = body or {}
    if not body.get("confirm"):
        raise HTTPException(422, "需 confirm:true")
    cfg = web_state.cfg_of(work)
    # 有运行中任务 → 409
    for t in TM.list_tasks():
        if t.get("work") == work and t.get("status") not in (
                "done", "failed", "crashed", "stopped", "cancelled", "stalled"):
            raise HTTPException(409, f"作品 {work} 有运行中任务，请先停止/取消")
    # 1. 校验路径在允许的删除根内（防穿越；aaManga 常是 junction → 见 _delete_roots）
    roots = _delete_roots()
    surf = logic.delete_surface(cfg, _all_work_dirs())
    arts = _deliver_artifacts(cfg)
    for _p in [p for _k, p in surf['items']] + list(arts):
        if not _in_delete_roots(_p, roots):
            raise HTTPException(400, f"删除路径越界: {_p}")
    # 2. **先改记录、后删文件**（2026-10-09 改序）：旧实现先删盘再摘 registry 条目，
    #    而 registry 那步一旦失败（打包版曾经必失败）就抛出 —— 盘上文件已没了、记录还在，
    #    作品列表里留一个点不开又删不掉的僵尸条目（MM / bar_miku 实测）。现在记录失败 →
    #    直接 500 且**一个文件都不动**；文件删不动只会留下空目录，可重试。
    if not _remove_registry_entry(work):
        raise HTTPException(500, f"registry 移除失败: {work}（未删除任何文件）")
    # 3. work_meta 删键
    meta = logic.load_work_meta(_WORK_META_FILE)
    if work in meta:
        del meta[work]
        with _LOCK:
            logic.save_work_meta(_WORK_META_FILE, meta)
    # 4. read_state 删键
    rs = _load_read_state()
    if work in rs:
        rs.pop(work, None)
        with _LOCK:
            _save_read_state(rs)
    # 5. 删盘（目录 + 交付物件）
    removed = logic.delete_work(surf)
    removed += logic.remove_paths(arts)
    failed = [p for _k, p in surf['items'] if os.path.exists(p)]
    failed += [p for p in arts if os.path.exists(p)]
    if failed:
        print("[delete] %s 有 %d 个路径删不掉（多为文件被占用，可停掉实例后重试）: %s"
              % (work, len(failed), failed), flush=True)
    # 6. 热加载（必须 registry.py 改后）——warm=False + clear_cache=False：
    #    删后不启动全量预热、不清其它作品缓存（否则与前端 location.reload 抢磁盘
    #    IO/触发全量冷算，首页卡刷新——2026-09-03 修复）；只 pop 被删作品自身缓存
    api_reload(warm=False, clear_cache=False)
    _STATS_CACHE.pop(work, None)
    _LASTUPD_CACHE.pop(work, None)
    return {"ok": True, "work": work, "removed": removed, "failed": failed}


def _remove_registry_entry(work_id):
    """registry.py 里移除 `"<key>": dict(...)` 条目（行级，返回 bool）。
    从 `"<key>": dict(` 所在行找到其配对 `    ),` 结束。"""
    rp = _registry_path()
    try:
        text = open(rp, "rb").read().decode("utf-8")
    except OSError:
        return False
    marker = f'    "{work_id}": dict('
    i = text.find(marker)
    if i == -1:
        return False
    lstart = text.rfind("\n", 0, i) + 1
    # 找条目结束 `    ),`（4空格缩进）——优先 CRLF，次 LF
    pat_crlf, pat_lf = "\r\n    ),\r\n", "\n    ),\n"
    j = text.find(pat_crlf, i)
    if j == -1:
        j = text.find(pat_lf, i)
    if j == -1:
        return False
    jend = j + (len(pat_crlf) if text[j:j + len(pat_crlf)] == pat_crlf else len(pat_lf))
    new_text = text[:lstart] + text[jend:]
    # 简化相邻空行（防堆积）
    while "\r\n\r\n\r\n" in new_text:
        new_text = new_text.replace("\r\n\r\n\r\n", "\r\n\r\n")
    while "\n\n\n" in new_text:
        new_text = new_text.replace("\n\n\n", "\n\n")
    try:
        with open(rp, "w", encoding="utf-8", newline="") as f:
            f.write(new_text)
    except OSError:
        return False
    return True


# ---------- 作品导出 / 导入（2026-10-01）----------


@router.get("/api/search-local")
def api_search_local(q: str = "", scope: str = "title,chapter,content", lang: str = "all",
                     regex: int = 0):
    """本地正文/标题搜索（首页搜索框正文勾选时调）。跨作品逐部扫。

    2026-09-03: 每作品 try/except + isdir 防御（缺汉化目录不再 500）；
    regex=1 按正则匹配（非法正则早退）；scanned=已扫作品数（前端进度）。"""
    q = (q or "").strip()
    if not q:
        return {"hits": [], "total": 0, "scanned": 0}
    use_regex = int(regex or 0) == 1
    if use_regex:
        try:
            re.compile(q)
        except re.error as e:
            return {"hits": [], "total": 0, "scanned": 0, "error": f"正则错误: {e}"}
    scopes = set(s.strip() for s in scope.split(",") if s.strip())
    out = []
    _meta_cache = logic.load_work_meta(_WORK_META_FILE)
    scanned = 0
    for wid, cfg in registry.WORKS.items():
        out_dir = cfg.get("out_dir", "")
        aa_dir = cfg.get("aa_dir", "")
        hits = []
        try:
            if "content" in scopes:
                hits = logic.search_work(out_dir, aa_dir, q,
                                         name_style=cfg.get("name_style"), regex=use_regex)
        except Exception:
            hits = []
        # 标题命中
        title_hit = False
        jp = (cfg.get("title", wid) or "")
        cn = (_meta_cache.get(wid, {}).get("cn_name", "") or "")
        try:
            if "title" in scopes:
                if use_regex:
                    title_hit = bool(re.search(q, jp) or re.search(q, cn))
                else:
                    title_hit = (q in jp or q in cn)
        except re.error:
            title_hit = False
        if hits or title_hit:
            out.append({"work": wid, "title": jp,
                        "cn_name": cn,
                        "content_hits": hits[:20], "title_hit": title_hit,
                        "total": len(hits)})
        scanned += 1
    return {"hits": out, "total": len(out), "scanned": scanned}


@router.get("/api/search-chapters")
def api_search_chapters(q: str = "", regex: int = 0):
    """章节名搜索（首页搜索框章节名勾选时调）：返回含 q 的作品 id 列表。

    2026-09-03: regex=1 按正则匹配；scanned 供前端进度判断。"""
    q = (q or "").strip()
    if not q:
        return {"works": [], "scanned": 0}
    use_regex = int(regex or 0) == 1
    if use_regex:
        try:
            re.compile(q)
        except re.error as e:
            return {"works": [], "scanned": 0, "error": f"正则错误: {e}"}
    matched = []
    scanned = 0
    for wid, cfg in registry.WORKS.items():
        try:
            st = work_stats(wid)
        except HTTPException:
            continue
        for c in st["chapters"]:
            t = (c.get("title", "") or "")
            try:
                if (re.search(q, t) if use_regex else (q in t)):
                    matched.append(wid)
                    break
            except re.error:
                break
        scanned += 1
    return {"works": matched, "scanned": scanned}
