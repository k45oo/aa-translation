# -*- coding: utf-8 -*-
"""交付域：重生成合集、简版 HTML、作品导出/导入、图片漫画阅览。"""
import os
import threading
import time
import urllib.parse
import registry
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from aa_web_app import web_state
from aa_web_app import task_manager as TM
from aa_web_app import work_pack
from common import gen_aa_html as _gen_aa_html
from aa_web_app.web_state import (IMAGE_MANGA, IMG_EXTS, _LOCK, _ROOT, _STATS_CACHE, _load_progress, app, run_generator, templates)
from aa_web_app.routers.admin import api_reload
from aa_web_app.routers.chapters import MakeHtmlBody
from fastapi import APIRouter

router = APIRouter()


# ---------- 台词高亮 gold 标注（2026-08-29 第 2 步，精译 ground truth 回归集） ----------


@router.post("/api/work/{work}/regenerate")
def api_regenerate(work: str):
    cfg = web_state.cfg_of(work)
    with _LOCK:
        code, tail, html_out = run_generator(work)
    web_state.invalidate_stats(work)
    mtime = os.path.getmtime(html_out) if html_out and os.path.exists(html_out) else None
    return {"ok": code == 0, "exit_code": code, "output": tail,
            "html_out": html_out, "html_mtime": mtime}


@router.post("/api/work/{work}/make-html")
def api_make_html(work: str, body: MakeHtmlBody = MakeHtmlBody()):
    """「仅 HTML 文件」：自包含 .html（可分页成多个），零 token。

    与 /regenerate（正式交付文件夹：编辑服务 exe + 源留档 + data/）互不影响：
    这里只写 `【机翻】<交付短名>_<起始>-<终止>.html` 到交付 HTML 目录（`--out-dir` 可改）。
    文件自带译/原切换（`tr_only` 关）、阅读进度、手机抽屉；重跑先删本作品上一次的简版文件。
    """
    cfg = web_state.cfg_of(work)
    split = int(body.split or 0)
    if split < 0:
        split = 0
    extra = ["--plain", "--split", str(split)]
    if body.tr_only:
        extra.append("--tr-only")
    if body.fav:
        extra.append("--fav")
    out_dir = (body.out_dir or "").strip()
    if out_dir:
        extra += ["--out-dir", out_dir]
    with _LOCK:
        code, tail, _ = run_generator(work, extra=extra, plain=True)
    out_dir = out_dir or os.path.dirname(_gen_aa_html.deliver_dir(cfg))
    pre = _gen_aa_html.PLAIN_PREFIX + _gen_aa_html.plain_short_name(cfg) + "_"
    files = []
    try:
        files = sorted(f for f in os.listdir(out_dir)
                       if f.startswith(pre) and f.endswith(".html"))
    except OSError:
        pass
    note = next((l.strip() for l in (tail or "").splitlines() if l.startswith("[plain] ")), "")
    return {"ok": code == 0, "exit_code": code, "output": tail,
            "dir": out_dir, "files": files, "note": note}


@router.post("/api/regenerate-all")
def api_regenerate_all():
    results = {}
    for work in registry.WORKS:
        with _LOCK:
            code, tail, html_out = run_generator(work)
        web_state.invalidate_stats(work)
        results[work] = {"ok": code == 0, "output": tail}
    return {"results": results}


# ---------- 图片漫画（仅阅览） ----------


def manga_chapters(root):
    return sorted(d for d in os.listdir(root)
                  if os.path.isdir(os.path.join(root, d)))


def manga_imgs(chapter_dir):
    return sorted(f for f in os.listdir(chapter_dir)
                  if os.path.splitext(f)[1].lower() in IMG_EXTS)


@router.get("/manga")
def manga_index(request: Request):
    items = []
    for name, root in IMAGE_MANGA.items():
        try:
            chs = manga_chapters(root)
            saved = _load_progress().get("manga", {}).get(name, {})
            items.append({"name": name, "chapters": len(chs),
                          "last": saved.get("chapter", ""),
                          "error": ""})
        except OSError as e:
            items.append({"name": name, "chapters": 0, "last": "", "error": str(e)})
    return templates.TemplateResponse(request, "manga.html", {
        "items": items, "active": "manga", "name": None,
        "chapters": [], "cur": "", "cur_idx": 0, "imgs": [], "total": 0})


@router.get("/manga/{name}")
def manga_reader(request: Request, name: str, ch: str = ""):
    root = IMAGE_MANGA.get(name)
    if not root:
        raise HTTPException(404, f"未知图片漫画: {name}（可用: {', '.join(IMAGE_MANGA)}）")
    chapters = manga_chapters(root)
    if not chapters:
        raise HTTPException(404, f"漫画 {name} 无章节目录")
    if ch not in chapters:
        ch = _load_progress().get("manga", {}).get(name, {}).get("chapter", "")
    if ch not in chapters:
        ch = chapters[0]
    imgs = manga_imgs(os.path.join(root, ch))
    return templates.TemplateResponse(request, "manga.html", {
        "items": [{"name": n, "chapters": len(manga_chapters(r)), "last": "", "error": ""}
                  for n, r in IMAGE_MANGA.items()],
        "active": "manga", "name": name, "chapters": chapters,
        "cur": ch, "cur_idx": chapters.index(ch), "imgs": imgs,
        "total": len(chapters)})


@router.get("/manga-img/{name}/{path:path}")
def manga_img(name: str, path: str):
    """图片直出（严格防目录穿越：解析后必须仍在漫画根目录内）。"""
    root = IMAGE_MANGA.get(name)
    if not root:
        raise HTTPException(404, "未知图片漫画")
    full = os.path.realpath(os.path.join(root, path))
    if not full.startswith(os.path.realpath(root) + os.sep) or not os.path.isfile(full):
        raise HTTPException(404, "图片不存在")
    return FileResponse(full)


# ---------- 作品导出 / 导入（2026-10-01）----------


_EXPORT_JOBS = {}     # work -> {state, ts, log, path, name, size, error}


_IMPORT_JOBS = {}     # token -> {state, ts, rar, manifest, preview, result, error, log}


def _export_dir():
    return ((os.environ.get("AA_EXPORT_DIR") or "").strip()
            or os.path.join(_ROOT, "导出"))


def _work_busy(work):
    """该作品是否有未结束任务（导出/删除共用；打包中文件可能在写）。"""
    for t in TM.list_tasks():
        if t.get("work") == work and t.get("status") not in (
                "done", "failed", "crashed", "stopped", "cancelled", "stalled"):
            return True
    return False


@router.get("/api/work/{work}/export-preview")
def api_export_preview(work: str):
    """导出预览：包内文件数/体积（含 raw 与不含 raw 两种）、work 白名单资产、排除项计数。"""
    cfg = web_state.cfg_of(work)
    rar = work_pack.find_rar()
    try:
        no_raw = work_pack.plan_export(cfg, work, include_raw=False)
        with_raw = work_pack.plan_export(cfg, work, include_raw=True)
    except work_pack.PackError as e:
        raise HTTPException(400, str(e))
    out = {
        "ok": True, "rar": rar, "rar_ok": bool(rar), "export_dir": _export_dir(),
        "work": work, "title": cfg.get("title"), "root": no_raw["root"],
        "files": no_raw["files"], "bytes": no_raw["bytes"],
        "with_raw_files": with_raw["files"], "with_raw_bytes": with_raw["bytes"],
        "raw_files": no_raw["raw_files"], "raw_bytes": no_raw["raw_bytes"],
        "html": no_raw["html"],
        "assets": no_raw["work_asset_names"],
        "skipped": no_raw["work_skipped_names"],
        "busy": _work_busy(work),
    }
    if not rar:
        out["error"] = "未找到 Rar.exe：请安装 WinRAR，或用环境变量 AA_RAR 指定 Rar.exe 全路径"
    return out


@router.post("/api/work/{work}/export")
def api_export_start(work: str, body: dict = None):
    """后台打包（线程 + 轮询 /export/status），完成后 /export/download 下载。"""
    cfg = web_state.cfg_of(work)
    body = body or {}
    if not work_pack.find_rar():
        raise HTTPException(400, "未找到 Rar.exe（安装 WinRAR 或设 AA_RAR）")
    if _work_busy(work):
        raise HTTPException(409, f"作品 {work} 有运行中任务，等它结束再导出")
    st = _EXPORT_JOBS.get(work)
    if st and st.get("state") == "running":
        raise HTTPException(409, "该作品已有导出任务在跑")
    include_raw = bool(body.get("include_raw"))
    job = {"state": "running", "ts": time.time(), "log": [], "path": None,
           "error": None, "include_raw": include_raw}
    _EXPORT_JOBS[work] = job

    def _run():
        try:
            res = work_pack.run_export(
                cfg, work, include_raw=include_raw, out_dir=_export_dir(),
                progress=lambda d: job["log"].append(d.get("msg") or ""))
            job.update({"state": "done", "path": res["path"], "name": res["name"],
                        "size": res["size"], "files": res["files"], "secs": res["secs"]})
        except Exception as e:      # PackError 与意外异常都落到任务状态里
            job.update({"state": "error", "error": "%s" % e})
        finally:
            job["ts"] = time.time()

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "work": work, "include_raw": include_raw}


@router.get("/api/work/{work}/export/status")
def api_export_status(work: str):
    job = _EXPORT_JOBS.get(work) or {"state": "idle"}
    return {"ok": True, "state": job.get("state"), "error": job.get("error"),
            "name": job.get("name"), "size": job.get("size"), "files": job.get("files"),
            "secs": job.get("secs"), "include_raw": job.get("include_raw"),
            "log": (job.get("log") or [])[-8:], "export_dir": _export_dir()}


@router.get("/api/work/{work}/export/download")
def api_export_download(work: str, f: str = ""):
    """下载导出包（默认最近一次成功的；f= 指定导出目录里的文件名）。"""
    job = _EXPORT_JOBS.get(work) or {}
    path = job.get("path")
    if f:
        d = os.path.realpath(_export_dir())
        cand = os.path.realpath(os.path.join(d, os.path.basename(f)))
        if cand.startswith(d + os.sep) and os.path.exists(cand):
            path = cand
    if not path or not os.path.exists(path):
        raise HTTPException(404, "没有可下载的导出包")
    return FileResponse(path, filename=os.path.basename(path), media_type="application/vnd.rar")


def _prune_import_uploads(keep_days=2):
    """清掉过期上传包（temp/aa_pack/upload，导出/导入的中间目录不长期留）。"""
    d = os.path.join(_ROOT, "temp", "aa_pack", "upload")
    if not os.path.isdir(d):
        return
    cutoff = time.time() - keep_days * 86400
    for f in os.listdir(d):
        p = os.path.join(d, f)
        try:
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                os.remove(p)
        except OSError:
            pass


@router.post("/api/work-import")
async def api_work_import(request: Request):
    """上传作品包（原始 body + X-Filename，避开 python-multipart 依赖）→ 出导入预览。"""
    fn = urllib.parse.unquote(request.headers.get("x-filename") or "upload.rar")
    data = await request.body()
    if not data:
        raise HTTPException(422, "空请求体")
    if not fn.lower().endswith(".rar"):
        raise HTTPException(422, "只支持 .rar 作品包")
    _prune_import_uploads()
    updir = os.path.join(_ROOT, "temp", "aa_pack", "upload")
    os.makedirs(updir, exist_ok=True)
    token = time.strftime("%Y%m%d_%H%M%S") + "_" + str(os.getpid())
    path = os.path.join(updir, token + ".rar")
    with open(path, "wb") as f:
        f.write(data)
    try:
        m = work_pack.peek_manifest(path)
        prev = work_pack.inspect_import(m, path)
    except work_pack.PackError as e:
        try:
            os.remove(path)
        except OSError:
            pass
        raise HTTPException(400, str(e))
    _IMPORT_JOBS[token] = {"state": "ready", "ts": time.time(), "rar": path,
                           "manifest": m, "preview": prev, "log": []}
    return {"ok": True, "token": token, "preview": prev,
            "manifest": {k: m.get(k) for k in ("format", "exported_at", "work", "title",
                                               "root_rel", "dirs", "files", "bytes",
                                               "include_raw")}}


@router.post("/api/work-import/confirm")
def api_work_import_confirm(body: dict = None):
    """确认导入：解包 → 归位 → 注册 registry/work_meta → 热加载。"""
    token = (body or {}).get("token") or ""
    job = _IMPORT_JOBS.get(token)
    if not job:
        raise HTTPException(404, "导入会话不存在或已过期，请重新选择文件")
    if job.get("state") == "running":
        raise HTTPException(409, "导入进行中")
    w = job["manifest"].get("work")
    if w in registry.WORKS:
        raise HTTPException(409, f"作品 {w} 已注册，请先删除再导入")

    def _run():
        job["state"] = "running"
        try:
            res = work_pack.install(job["rar"],
                                    options={"registry": True, "work_meta": True},
                                    progress=lambda d: job["log"].append(d.get("msg") or ""))
            api_reload()                 # registry.py 已改 → 热加载
            _STATS_CACHE.pop(res["work"], None)
            job.update({"state": "done", "result": res})
        except Exception as e:
            job.update({"state": "error", "error": "%s" % e})

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "work": w}


@router.get("/api/work-import/status")
def api_work_import_status(token: str):
    job = _IMPORT_JOBS.get(token)
    if not job:
        raise HTTPException(404, "导入会话不存在或已过期")
    return {"ok": True, "state": job.get("state"), "error": job.get("error"),
            "log": (job.get("log") or [])[-8:], "result": job.get("result"),
            "preview": job.get("preview")}
