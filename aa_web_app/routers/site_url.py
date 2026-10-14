# -*- coding: utf-8 -*-
"""站点域：首页(/) 与首页快照构建、原站 URL 解析、作品更新检查。"""
import json
import os
import re
import threading
import time
import registry
from fastapi import HTTPException, Request
from aa_web_app import logic
from aa_web_app import task_manager as TM
from aa_web_app import add_work
from aa_web_app import index_cache
from aa_web_app.web_state import (INDEX_SYNC_DIRTY_MAX, _AUTHOR_OVERRIDE, _BUILD_LOCK, _INDEX_SNAP, _LASTUPD_CACHE, _WORK_META_FILE, _load_progress, _load_read_state, app, r18_summary_cached, templates, work_stats)
from aa_web_app.routers.tasks import _aa_config
from fastapi import APIRouter

router = APIRouter()


# ---------- 更新检查（2026-08-31）----------


@router.post("/api/work/{work}/check-update")
def api_check_update(work: str):
    """检查作品是否有新章节：实时拉原站清单 vs 本地已下载。

    `wiki` 字段（2026-10-06）= 本作品是否配了官方设定 wiki（前端据此显示「同步本作 wiki」勾选框）。"""
    cfg = registry.WORKS.get(work)
    if not cfg:
        raise HTTPException(404, f"未知作品: {work}")
    r = add_work.check_updates(work, cfg)
    if isinstance(r, dict):
        r["wiki"] = bool(TM.wiki_configured(work))
    return r


@router.post("/api/work/{work}/update")
def api_work_update(work: str, body: dict = None):
    """按需更新作品：body {action: "download"|"translate"}。仅处理 check_updates 报的新章。

    2026-08-31 修复：原 params.chapters="new" 是「本地缺的全部」（含历史缺口，如 baitou
    只下载了最新 42 章、原站 218 条 → 会误拉 176 条旧章）。改为用 check_updates 报的
    「比本地最新还新的连续新章」精确 eid 列表。"""
    body = body or {}
    action = body.get("action")
    if action not in ("download", "translate"):
        return {"ok": False, "error": "action 须为 download|translate"}
    cfg = registry.WORKS.get(work)
    if not cfg:
        raise HTTPException(404, f"未知作品: {work}")
    # 2026-10-06: 勾「同步本作 wiki」= 连载更新时把作品官方设定 wiki 一起对齐（零模型成本，
    # 只抓原站 + 增量翻译新增行）。未配置 wiki 的作品静默忽略（不强加失败）。
    sync_wiki = bool(body.get("sync_wiki")) and TM.wiki_configured(work)
    upd = add_work.check_updates(work, cfg)
    if not upd.get("ok"):
        return {"ok": False, "error": upd.get("error", "更新检查失败")}
    eids = [str(u["eid"]) for u in (upd.get("updates") or []) if u.get("eid")]
    if not eids:
        if sync_wiki:      # 章节没有新的，但原站 wiki 可能已经改了 → 仍然同步一次
            r = TM.enqueue("wiki", work, {})
            if not r.get("ok"):
                return r
            return {"ok": True, "task_id": r["task_id"],
                    "msg": f"章节已是最新；本作 wiki 同步已入队: {r['task_id']}"}
        return {"ok": True, "msg": "已是最新，无需更新", "task_id": None}
    params = {"chapters": ",".join(eids)}
    if action == "translate":
        # 2026-09-13 链式：下载新章 → 正常完成自动接翻译（task_manager params["then"]）。
        # translate_runner 已不再自动补下载，所以「下载并翻译」必须显式分两段。
        # 2026-10-06 三链：勾「同步本作 wiki」时 = 下载 → wiki 同步 → 翻译（wiki 项自身再带 then）。
        chain = {"type": "translate", "chapters": ",".join(eids)}
        if body.get("free_mode"):
            chain["env"] = {"AA_FREE_MODE": "1"}
        params["then"] = {"type": "wiki", "then": chain} if sync_wiki else chain
    r = TM.enqueue("download", work, params)
    if not r.get("ok"):
        return r
    if action == "translate":
        tail = "（下载完成后自动接 wiki 同步 → 翻译）" if sync_wiki else "（下载完成后自动接翻译）"
        msg = f"下载并翻译 {len(eids)} 个新章已入队{tail}: {r['task_id']}"
    else:
        msg = f"下载 {len(eids)} 个新章已入队: {r['task_id']}"
    return {"ok": True, "task_id": r["task_id"], "msg": msg}


@router.post("/api/work/{work}/sync-wiki")
def api_work_sync_wiki(work: str):
    """手动同步本作品官方设定 wiki（连载更新后 / 译名改动后）。

    只对 `scripts/config/work_wikis.json` 里配过的作品可用；`--update` = 重抓原站全部页
    （零模型成本）+ 行级缓存增量翻译（只译新行/改动行）+ 重建 index.html/术语表.json。"""
    if not registry.WORKS.get(work):
        raise HTTPException(404, f"未知作品: {work}")
    if not TM.wiki_configured(work):
        return {"ok": False, "error": f"作品 {work} 未配置官方设定 wiki（scripts/config/work_wikis.json）"}
    r = TM.enqueue("wiki", work, {"mode": "update"})
    if not r.get("ok"):
        return r
    return {"ok": True, "task_id": r["task_id"],
            "msg": f"{work} 的 wiki 同步已入队: {r['task_id']}"}


# ---------- 原站 URL 解析（2026-08-31：非 r401 作品也显示「原站」按钮）----------


# ⚠️ 首页 index() 绝不发网络请求（曾因同步抓 yaruobook 页面卡死首页 3s+/作品）：
#    有缓存读缓存，无缓存立即回退首章 URL（秒回），分类解析交给 _site_url_backfill 后台线程惰性补全。
def _yaruobook_proxy():
    """原站解析用的代理（地址走 aa_config：AA_PROXY > TOML；2026-10-08 阶段 4）。"""
    return _aa_config().proxy_url()


def _find_yaruobook_cat(article_url):
    """从 yaruobook 章节页面包屑（itemListElement schema）解析作品专属分类 ?cat=N。
    走 v2rayN 代理（直连慢/403）。失败返回 None。"""
    import urllib.request
    try:
        op = urllib.request.build_opener(
            urllib.request.ProxyHandler(_aa_config().proxy_dict()))
        req = urllib.request.Request(article_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"})
        with op.open(req, timeout=8) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception:
        return None
    # 面包屑 itemListElement：兼容 <span itemprop="name"> 与 <span class="breadcrumb-caption"> 两种结构
    items = re.findall(
        r'itemListElement[^>]*>.*?<a href="[^"]*[?&]cat=(\d+)[^"]*"[^>]*>\s*<span[^>]*>(?:<span[^>]*>)?([^<]{1,60}?)(?:</span>)*</a>',
        html, re.S)
    if items:
        # 取最后一个分类（更具体：作品集 → 短編，避免误取父分类）
        return items[-1][0]
    m2 = re.search(r"category-(\d+)-post", html)
    return m2.group(1) if m2 else None


def _site_url_fallback(cfg):
    """无 r401_cid 作品的原站回退 URL（秒回，不网络）：chapters.json 首章 URL → url_pattern 首章。"""
    work_dir = cfg.get("work_dir", "")
    chapters_path = os.path.join(work_dir, "chapters.json") if work_dir else ""
    if chapters_path and os.path.exists(chapters_path):
        try:
            entries = json.load(open(chapters_path, encoding="utf-8")).get("entries", [])
            if entries:
                return (entries[0].get("url") or "").strip()
        except Exception:
            pass
    up = cfg.get("url_pattern", "")
    if up:
        return up.replace("{eid}", "1")
    return ""


def _site_url_for(wid, cfg):
    """作品原站 URL（首页同步路径，零网络）：r401 系 → r401 分类页；
    yaruobook 系 → work/site_url.json 缓存（无则回退首章 URL，后台补全分类）。"""
    cid = cfg.get("r401_cid")
    if cid:
        return f"https://rss.r401.net/yaruo/categories/{cid}"
    work_dir = cfg.get("work_dir", "")
    cache_path = os.path.join(work_dir, "site_url.json") if work_dir else ""
    if cache_path and os.path.exists(cache_path):
        try:
            v = json.load(open(cache_path, encoding="utf-8")).get("url") or ""
            if v:
                return v
        except Exception:
            pass
    return _site_url_fallback(cfg)


_SITE_URL_BF_LOCK = threading.Lock()


_SITE_URL_BF_FAILED = set()      # 解析过但拿不到 ?cat 的作品：本进程不再重试（防每次刷新都发超时请求）


def _site_url_backfill():
    """后台线程：为无 r401_cid 且未缓存分类页的作品解析 ?cat=N 并写 work/site_url.json（一次性）。

    2026-10-13：失败（站点不是 yaruobook / 解析不出 cat）记进 `_SITE_URL_BF_FAILED`——
    否则每个首页请求都会对同一部作品重发一次 8s 超时的网络请求（实测 商店街迎撃 每次都白等）。
    """
    import registry
    for wid, cfg in registry.WORKS.items():
        if cfg.get("r401_cid") or wid in _SITE_URL_BF_FAILED:
            continue
        work_dir = cfg.get("work_dir", "")
        cache_path = os.path.join(work_dir, "site_url.json") if work_dir else ""
        if cache_path and os.path.exists(cache_path):
            try:
                if json.load(open(cache_path, encoding="utf-8")).get("url"):
                    continue
            except Exception:
                pass
        url = _site_url_fallback(cfg)
        if not url or not url.startswith("http"):
            _SITE_URL_BF_FAILED.add(wid)
            continue
        cat = _find_yaruobook_cat(url)
        if not cat:
            _SITE_URL_BF_FAILED.add(wid)
            continue
        domain = re.sub(r"https?://([^/]+)/.*", r"\1", url)
        site_url = f"https://{domain}/?cat={cat}"
        try:
            os.makedirs(work_dir, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump({"url": site_url}, f, ensure_ascii=False)
        except Exception:
            _SITE_URL_BF_FAILED.add(wid)


def _site_url_backfill_async():
    """首页调用的非阻塞入口：单飞（同时只跑一个 backfill 线程）+ 失败记忆。"""
    if not _SITE_URL_BF_LOCK.acquire(blocking=False):
        return False

    def _run():
        try:
            _site_url_backfill()
        except Exception:
            pass
        finally:
            _SITE_URL_BF_LOCK.release()

    threading.Thread(target=_run, daemon=True).start()
    return True


def _build_index_data(only=None, base=None):
    """构建首页作品卡片**列表**（= 快照内容，见 index_cache 模块头）。

    only = 需要重算的作品 id 集合（其余条目从 base 快照原样复用）；None → 全量重算。
    read/read_state 在此算一次存进快照，渲染时再由 _render_index 按当前进度实时覆盖
    （阅读进度变化频繁且读取廉价，不该等后台重建）。
    """
    works = []
    prog = _load_progress().get("aa", {})
    meta = logic.load_work_meta(_WORK_META_FILE)
    read_states = _load_read_state()
    prev = {}
    for _w in ((base or {}).get("works") or []):
        if isinstance(_w, dict) and _w.get("id"):
            prev[_w["id"]] = _w
    for wid in registry.WORKS:
        cfg = registry.WORKS[wid]
        if only is not None and wid not in only and wid in prev:
            works.append(prev[wid])
            continue
        st = work_stats(wid)
        # 已读章节数 = 保存进度章节在列表中的序号（0 = 未读过）
        read = 0
        saved = prog.get(wid, {}).get("fname")
        if saved:
            for i, c in enumerate(st["chapters"]):
                if c["file"] == saved:
                    read = i + 1
                    break
        # 原文话数（aa/*.txt 计数）→ 未译差值（仅汉化<原文时显示）
        src_total = 0
        try:
            aa_dir = cfg.get("aa_dir")
            if aa_dir and os.path.isdir(aa_dir):
                src_total = sum(1 for f in os.listdir(aa_dir)
                                if logic._is_txt(f))
        except Exception:
            src_total = 0
        deficit = max(0, src_total - st["agg"]["chapters"]) if src_total else 0
        # 已译判定：汉化目录存在且确有 *_汉化.txt 产物（deficit==0 可能是纯原文误报——
        # 未汉化作品 out_dir 缺失时 chapters=aa 原文数 → deficit 恒 0）
        try:
            out_exists = bool(cfg.get("out_dir")) and os.path.isdir(cfg["out_dir"]) and any(
                f.endswith(("_汉化.txt", "_汉化.txt.gz")) for f in os.listdir(cfg["out_dir"]))
        except OSError:
            out_exists = False
        translated = out_exists and deficit <= 0
        # 差异：素材占比（汉化文本数人物名出现次数 → 占比降序）
        m = meta.get(wid, {})
        tag_list = list(m.get("tags", []) or [])
        mat_list = list(m.get("materials", []) or [])
        if m.get("ignore_materials"):
            mat_list = []   # 2026-08-26: jikan_yaluo 等特殊作品忽略素材统计
        mat_with_count = logic.material_frequency_cached(cfg.get("work_dir", ""), cfg.get("out_dir", ""), mat_list)
        # 素材显示名悬浮(2026-08-26): 名称单独显示, 作品出处/次数/章节数放 tooltip
        mats = []
        tot = sum(v["count"] for v in mat_with_count.values() if isinstance(v, dict))
        for name in mat_list:
            v = mat_with_count.get(name)
            if not isinstance(v, dict):
                continue
            c, ch = v.get("count", 0), v.get("chapters", 0)
            dn, wname, note = logic.material_display_parts(name)
            if dn is None:
                continue   # やる夫系原创/无对应作品 → 不显示
            pct = round(c * 100 / tot, 1) if tot else 0.0
            if pct < 5:
                continue   # 2026-08-26: 排除 5% 以下素材
            mats.append({
                "name": dn, "works": wname or "", "note": note or "",
                "count": c, "chapters": ch,
                "pct": pct})
        # 原站 URL（更多菜单「原站」按钮）：r401 系 → r401 分类页；
        # yaruobook 系 → 从首章页面包屑解析 ?cat=N（work/site_url.json 缓存，失败回退首章 URL）
        site_url = _site_url_for(wid, cfg)

        works.append({
            "id": wid,
            "title": cfg.get("title", wid),
            "cn_name": m.get("cn_name", ""),
            "jp_title": m.get("jp_title", "") or cfg.get("title", wid),
            "author": logic.author_display(wid, m, cfg.get("work_dir", ""), cfg.get("aa_dir", ""),
                                           _AUTHOR_OVERRIDE)[0],
            "r18": bool(cfg.get("r18")),
            "r18_level": m.get("r18_level", ""),
            "r18_summary": r18_summary_cached(cfg.get("work_dir", ""), cfg.get("out_dir", "")),
            "site": cfg.get("site_name") or cfg.get("site"),
            # 快照里只留用得到的统计字段（完整 st 含全部章节明细，落盘会白白翻几倍）
            "stats": {"work": st.get("work"), "error": st.get("error"),
                      "agg": st["agg"], "html_out": st.get("html_out"),
                      "html_exists": st.get("html_exists"),
                      "html_mtime": st.get("html_mtime")},
            # 章节文件名顺序：渲染时按当前阅读进度实时算 read（秒级反馈，不用等后台重建）
            "fnames": [c["file"] for c in st["chapters"]],
            "read": read, "total": st["agg"]["chapters"],
            "src_total": src_total, "deficit": deficit,
            "translated": translated,
            # 翻译状态三态（筛选用）：done=已翻译 / doing=翻译中(有汉化产物但差章未译) / none=未翻译
            "tstate": "done" if translated else ("doing" if out_exists else "none"),
            "status": m.get("status", ""),
            "r401_cid": cfg.get("r401_cid", ""),
            "site_url": site_url,
            "tags": tag_list,
            "tags_key": "\x01".join(tag_list),          # 行 data-tags（\x01 分隔防名字含|）
            "mats_key": "\x01".join(mt["name"] for mt in mats),
            "materials": mats,
            "mats_display": mats[:8],          # 2026-08-26: 表格最多显示8个
            "mats_more": len(mats) - 8 if len(mats) > 8 else 0,
            "unsynced": logic.count_unsynced_edits(cfg["work_dir"], st.get("html_mtime")),
            # 2026-09-03 作品列表改版：评分/评价/最后更新/阅读状态
            "rating": m.get("rating"),
            "review": m.get("review", ""),
            "last_update": _last_update_refresh(wid, cfg),
            "read_state": read_states.get(wid, {}).get("state", ""),
        })
    # 2026-09-03 排序改版：作品按最后更新倒序（分组在 _group_index_works 里做）
    works.sort(key=lambda w: w["last_update"], reverse=True)
    return works


def _group_index_works(works):
    """按作者分组（组内 last_update 倒序，组间按组内最新倒序）——构建与渲染共用同一口径。"""
    from collections import OrderedDict
    by_author = OrderedDict()
    for w in works:
        by_author.setdefault(w["author"] or "◆?", []).append(w)
    # 作者组排序：组内最新 last_update 倒序
    ordered = sorted(by_author.items(),
                     key=lambda kv: max(w["last_update"] for w in kv[1]), reverse=True)
    groups = []
    for author, wl in ordered:
        wl.sort(key=lambda w: w["last_update"], reverse=True)
        translated = sum(1 for w in wl if w["translated"])
        _tags = []
        for _w in wl:
            for _t in _w["tags"]:
                if _t not in _tags:
                    _tags.append(_t)
        _ratings = [w.get("rating") for w in wl]
        groups.append({
            "author": author,
            "total": len(wl),
            "translated": translated,
            "untranslated": len(wl) - translated,
            "all_translated": translated == len(wl),
            "works": wl,
            "tags_agg": _tags,
            "avg_rating": logic.avg_rating(_ratings),
        })
    return groups


def _index_filter_values(works):
    """筛选多选全集（2026-09-02）：标签/素材按出现次数降序。"""
    from collections import Counter
    _tc, _mc = Counter(), Counter()
    for _w in works:
        _tc.update(_w["tags"])
        _mc.update(_mt["name"] for _mt in _w["materials"])
    return [k for k, _ in _tc.most_common()], [k for k, _ in _mc.most_common()]


def _rebuild_index_snapshot(only=None):
    """重算首页快照（only=只重算这几部，其余复用当前快照条目）并落盘。"""
    with _BUILD_LOCK:
        base = _INDEX_SNAP.get()
        works = _build_index_data(only=only, base=base)
        # 先清脏再写盘：脏集合随快照一起落盘（顺序反了会把已重算的脏标又写回文件）
        _INDEX_SNAP.clear_dirty(only)
        return _INDEX_SNAP.set(index_cache.wrap({"works": works}))


def _index_refresh_async(force=False):
    """后台重建首页快照（单飞 + 限流）。返回 False = 已有在跑 / 距上次太近。"""
    if not _INDEX_SNAP.begin_refresh(force=force):
        return False

    def _run():
        err = None
        try:
            _rebuild_index_snapshot()
        except Exception as e:        # 重建失败保留旧快照，首页继续可用
            err = repr(e)
        finally:
            _INDEX_SNAP.end_refresh(err)

    threading.Thread(target=_run, daemon=True).start()
    return True


def _render_index(request, snap):
    """用快照渲染首页：作品列表来自快照，阅读进度/阅读状态按当前值实时覆盖。"""
    prog = _load_progress().get("aa", {})
    read_states = _load_read_state()
    works = []
    for _w in (snap.get("works") or []):
        if _w.get("id") not in registry.WORKS:
            continue            # 已删除的作品：快照可能还没重建，渲染时直接跳过
        w = dict(_w)
        wid = w.get("id")
        saved = (prog.get(wid) or {}).get("fname")
        if saved:
            try:
                w["read"] = list(w.get("fnames") or []).index(saved) + 1
            except ValueError:
                w["read"] = w.get("read", 0)
        w["read_state"] = (read_states.get(wid) or {}).get("state", "")
        works.append(w)
    groups = _group_index_works(works)
    all_tags, all_mats = _index_filter_values(works)
    return templates.TemplateResponse(request, "index.html",
                                      {"works": works, "groups": groups,
                                       "all_tags": all_tags, "all_mats": all_mats,
                                       "index_age": index_cache.age_text(_INDEX_SNAP.age()),
                                       "index_built_at": snap.get("built_at") or 0,
                                       "index_refreshing": _INDEX_SNAP.refreshing,
                                       "active": "works"})


@router.get("/")
def index(request: Request):
    """作品列表 —— 只读快照，不再每次扫全库（见 index_cache 模块头）。"""
    # 后台惰性补全 yaruobook 作品分类页 URL（不阻塞首页；单飞 + 失败记忆）
    _site_url_backfill_async()
    snap = _INDEX_SNAP.get()
    if not snap or not snap.get("works"):
        # 首次（无快照/快照损坏）：同步建一次并落盘；并发请求只建一次
        with _BUILD_LOCK:
            snap = _INDEX_SNAP.get()
            if not snap or not snap.get("works"):
                snap = _rebuild_index_snapshot()
        return _render_index(request, snap)
    dirty = _INDEX_SNAP.dirty()
    if dirty:
        if _INDEX_SNAP.needs_full_rebuild() or len(dirty) > INDEX_SYNC_DIRTY_MAX:
            _index_refresh_async(force=True)        # 大批脏：后台重建，本次先用旧值
        else:
            snap = _rebuild_index_snapshot(only=dirty)   # 少量脏：同步局部重算，改完立刻可见
    elif _INDEX_SNAP.should_refresh():
        _index_refresh_async()                      # 快照过期：后台刷新，本次先用旧值
    return _render_index(request, snap)


@router.get("/api/index/status")
def api_index_status():
    """首页快照状态（前端「刷新数据」按钮轮询）。"""
    return {"ok": True, "built_at": (_INDEX_SNAP.get() or {}).get("built_at") or 0,
            "age": _INDEX_SNAP.age(), "age_text": _INDEX_SNAP.age_text_now(),
            "refreshing": _INDEX_SNAP.refreshing, "dirty": sorted(_INDEX_SNAP.dirty()),
            "error": _INDEX_SNAP.last_error}


@router.post("/api/index/refresh")
def api_index_refresh():
    """手动触发后台重建首页快照（忽略限流）。"""
    started = _index_refresh_async(force=True)
    return {"ok": True, "started": started, "refreshing": _INDEX_SNAP.refreshing}


_LASTUPD_TTL = 300


def last_update_cached(work, cfg):
    """作品 last_update 内存 TTL 缓存（首页每作品调用；与 _STATS_CACHE 同 TTL，invalidate_stats 一并清）。"""
    hit = _LASTUPD_CACHE.get(work)
    now = time.time()
    if hit and now - hit[0] < _LASTUPD_TTL:
        return hit[1]
    v = logic.last_update_of(cfg.get("aa_dir", ""), cfg.get("out_dir", ""), cfg.get("work_dir", ""))
    _LASTUPD_CACHE[work] = (now, v)
    return v


def _last_update_refresh(work, cfg):
    """重算并写回 last_update 磁盘缓存（**快照重建路径**用）。

    快照重建时顺手 refresh：last_update 值 = aa/汉化 顶层文件 max mtime，
    `last_update_cached`（refresh=False）只读缓存，若不主动刷新，重启后首页会一直
    显示上次刷新时刻的旧时间。scandir 很快（全库 87 个 aa/ 实测 0.14s）。
    """
    v = logic.last_update_of(cfg.get("aa_dir", ""), cfg.get("out_dir", ""),
                             cfg.get("work_dir", ""), refresh=True)
    _LASTUPD_CACHE[work] = (time.time(), v)
    return v


_KN_SUM_CACHE = {}  # 章节简介缓存: path -> [mtime, size, {"base": 简介}]


def _chapter_summary(work, cfg, base):
    """读作品知识库章节简介（work/<work>_knowledge.json 的 章节简介 块，键=章节 base）。

    无知识库/无本章/失败哨兵 __summary_failed__ → 返回空串（模板不展示）。
    (mtime,size) 指纹缓存，避免每次翻章重读知识库 JSON。
    """
    kn_p = os.path.join(cfg.get("work_dir", ""), work + "_knowledge.json")
    if not os.path.exists(kn_p):
        return ""
    try:
        st = os.stat(kn_p)
        ent = _KN_SUM_CACHE.get(kn_p)
        if not ent or ent[0] != st.st_mtime or ent[1] != st.st_size:
            kn = json.load(open(kn_p, encoding="utf-8"))
            ent = [st.st_mtime, st.st_size, kn.get("章节简介", {}) or {}]
            _KN_SUM_CACHE[kn_p] = ent
        s = str(ent[2].get(base, "") or "").strip()
        if not s or s == "__summary_failed__":
            return ""
        return s
    except Exception:
        return ""


# 知识库章节简介结尾的角色罗列：先匹配带标签格式「主要角色：A、B、C。」，再匹配无标签格式
_SUM_LABEL_RE = re.compile(
    r"(主要角色|登场角色|出场角色|登場人物|主要人物|登场人物)\s*[:：]\s*"
    r"([一-鿿\w·・]+(?:、[一-鿿\w·・]+)+)[。．]?$"
)


_SUM_NAMES_RE = re.compile(r"([一-鿿\w·・]+(?:、[一-鿿\w·・]+)+)[。．]?$")


def _split_summary(s):
    """章节简介拆 (剧情, 角色罗列)。

    gen_chapter_summaries 生成的简介结尾是角色罗列句，两种格式：
    - 新格式: 「剧情。A、B、C。」
    - 旧格式: 「剧情。主要角色：A、B、C。」（带标签，存量 493 条）
    角色罗列独立成行展示，标签不进剧情行。
    """
    s = (s or "").strip()
    if not s:
        return "", ""
    m = _SUM_LABEL_RE.search(s)
    if m:
        plot = s[: m.start()].rstrip("。． \u3000")
        return plot, m.group(2)
    m = _SUM_NAMES_RE.search(s)
    if m:
        plot = s[: m.start()].rstrip("。． \u3000")
        if plot:
            return plot, m.group(1)
    return s, ""


_FULLW_DIGITS = str.maketrans('０１２３４５６７８９', '0123456789')
