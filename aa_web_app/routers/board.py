# -*- coding: utf-8 -*-
"""揭示板域：本地 dat 浏览、下载/更新任务。"""
import json
import os
from fastapi import HTTPException, Request
from pydantic import BaseModel
from aa_web_app import logic
from aa_web_app import task_manager as TM
from aa_web_app.web_state import (app, templates)
from aa_web_app.routers.tasks import _task_public
from fastapi import APIRouter

router = APIRouter()


# ---------- 揭示板（本地浏览披露板 dat，2026-08-23） ----------


BOARD_PER_PAGE = 400      # 每页渲染的楼数（dat 大贴 5001 楼，整章渲染会卡）


BOARD_ROOT = logic.BOARD_ROOT


def _safe_board_author(author):
    """作者目录名校验：接受显示名（原名）或别名，解析为物理目录（防路径穿越）。"""
    d = logic.board_dir(author)
    if not d:
        return None
    full = os.path.join(BOARD_ROOT, d)
    return full if (os.path.isdir(full) and os.path.realpath(full).startswith(
        os.path.realpath(BOARD_ROOT) + os.sep)) else None


def board_author_entry(author, file=None):
    """返回渲染揭示板作者页所需上下文；贴不存在时 raise HTTPException。"""
    d = _safe_board_author(author)
    if not d:
        raise HTTPException(404, f"未知揭示板连载: {author}")
    display = logic.board_display_name(os.path.basename(d))
    slaves = logic.board_slaves(display)
    if not slaves:
        raise HTTPException(404, f"{author} 下没有贴")
    # 当前贴：显式 file，否则第一篇
    if file:
        # 允许传文件名或 eid
        cur = next((s for s in slaves if s["file"] == file or s["eid"] == file), None)
        if not cur:
            raise HTTPException(404, f"贴不存在: {file}")
        idx = slaves.index(cur)
    else:
        idx = 0
    # 解析当前贴
    parsed = logic.parse_board_dat(os.path.join(d, slaves[idx]["file"]))
    # index.json 权威标题优先（dat 内 1 楼 AA 可能复用开场横幅，猜不准每スレ标题）
    if slaves[idx].get("title"):
        parsed["title"] = slaves[idx]["title"]
    return {"author": display, "slaves": slaves, "cur": slaves[idx],
            "cur_idx": idx, "parsed": parsed}


@router.get("/board")
def board_index(request: Request):
    cols = logic.board_collections()
    totals = {"collections": len(cols), "slaves": sum(len(c["slaves"]) for c in cols)}
    return templates.TemplateResponse(request, "board.html", {
        "cols": cols, "totals": totals, "active": "board",
        "author": None, "cur": None, "cur_idx": 0, "parsed": None,
        "slaves": [], "page": 1, "total_pages": 0})


@router.get("/board/{author}")
def board_reader(request: Request, author: str, file: str = "", page: int = 1, op: int = 0):
    """揭示板阅读页。

    `op=1` = **仅楼主**（2026-10-19 用户指定）：服务端**先过滤再分页**——每页 BOARD_PER_PAGE 个
    楼主楼，页码按楼主楼序号算；楼号仍显示原始楼号（搜索锚点照旧，搜索结果链接带 `op=0`）。
    去重（连续相同正文折叠）是显示层：`logic.board_mark_dedup` 只打标记，模板渲染折叠行 + 页面内 JS 展开。
    """
    ctx = board_author_entry(author, file)
    parsed = ctx["parsed"]
    trip = logic.board_author_trip(author)
    view = logic.board_page_view(parsed["res"], trip, op, BOARD_PER_PAGE, page)
    return templates.TemplateResponse(request, "board.html", {
        "cols": logic.board_collections(), "totals": {}, "active": "board",
        "author": author, "slaves": ctx["slaves"],
        "cur": ctx["cur"], "cur_idx": ctx["cur_idx"],
        "parsed": parsed, "res": view["rows"], "total_res": view["total_res"],
        "trip": trip, "op": 1 if view["op"] else 0, "op_total": view["op_total"],
        "page": view["page"], "total_pages": view["total_pages"]})


@router.get("/api/board/search")
def board_search_api(request: Request, author: str, q: str, limit: int = 200):
    """揭示板全局搜索：在 author 的全部 dat 里搜 q，返回结果（含跳转 page/no）。"""
    d = _safe_board_author(author)
    if not d:
        raise HTTPException(404, f"未知揭示板连载: {author}")
    results = logic.board_search(author, q, limit=min(limit, 500))
    # 每条补跳转页：按 400 楼/页 算出命中楼所在分页
    for r in results:
        r["page"] = (r["no"] - 1) // BOARD_PER_PAGE + 1
    return {"author": author, "q": q, "count": len(results), "results": results}


# ---------- 揭示板 下载/更新（2026-10-10；runner = scripts/runner/board_runner.py） ----------


BOARD_DEFAULT_HOST = "http://yarufox.sakura.ne.jp"


class BoardDownload(BaseModel):
    """揭示板下载/更新入队参数（工作台 /board 页表单 + 作者页「更新」按钮）。"""
    board: str = "FOX"                 # 板 id（如 FOX）
    host: str = BOARD_DEFAULT_HOST     # 板站点根
    match: str = ""                    # 标题关键词（如 ポケの子）
    author: str = ""                   # 作者 trip（校验用，如 ◆LXwkncCAp.）
    dir: str = ""                      # 揭示板目录（默认取 author/match）
    title: str = ""                    # 作品名（写 index.json）
    work_id: str = ""                  # 作品 id（写 index.json，可选）
    label: str = ""                    # 连载标签（写 index.json，可选）
    kako_from: int | None = None       # kako dir1 下界（eid 前 4 位）
    kako_to: int | None = None         # kako dir1 上界
    scan_kako: bool = False            # 强制扫 kako 索引（首次收录历史贴用）
    force: bool = False                # 已存在的 .dat 也重抓


def _board_index_meta(author):
    """读 揭示板/<dir>/index.json 的板信息（兼容既有两种 schema）。"""
    d = _safe_board_author(author)
    if not d:
        return {}
    p = os.path.join(d, "index.json")
    try:
        with open(p, encoding="utf-8") as f:
            idx = json.load(f)
    except Exception:
        return {}
    # 板名/站点根：三种 schema 都认（统一口径在 logic.board_host_board）
    host, board = logic.board_host_board(idx)
    slaves = idx.get("slaves") or idx.get("スレ一覧") or []
    eids = [str(s.get("eid") or "") for s in slaves if str(s.get("eid") or "").isdigit()]
    return {
        "dir": os.path.basename(d),
        "board": board,
        "host": host,
        "match": idx.get("match") or "",
        "author": idx.get("author") or "",
        "title": idx.get("work_title") or idx.get("作品") or "",
        "work_id": idx.get("work") or "",
        "label": (idx.get("range") or {}).get("label") or idx.get("備考") or "",
        "slaves": len(slaves),
        "eid_min": min(eids) if eids else "",
        "eid_max": max(eids) if eids else "",
        "updated": idx.get("updated") or "",
    }


def _board_enqueue(p: dict):
    """入队揭示板下载/更新任务；返回 {ok, task_id, ...}。"""
    board = (p.get("board") or "FOX").strip()
    host = (p.get("host") or BOARD_DEFAULT_HOST).strip().rstrip("/")
    dirname = (p.get("dir") or p.get("author") or p.get("match") or "").strip().replace("/", "_")
    if not dirname:
        raise HTTPException(422, "缺少揭示板目录（dir / author / match 至少给一个）")
    if not (p.get("match") or p.get("author")):
        raise HTTPException(422, "缺少标题关键词（match）或作者 trip（author）——否则会把整块板全抓下来")
    params = {k: p.get(k) for k in ("match", "author", "title", "work_id", "label",
                                    "kako_from", "kako_to", "scan_kako", "force")}
    params.update({"board": board, "host": host, "dir": dirname})
    params["kako_from"] = p.get("kako_from") if p.get("kako_from") not in ("", None) else None
    params["kako_to"] = p.get("kako_to") if p.get("kako_to") not in ("", None) else None
    params = {k: v for k, v in params.items() if v not in (None, "")}
    # work 用伪 id `board:<板id>`：非 registry 作品，同板串行（该站禁并发）
    return TM.enqueue("board", f"board:{board}", params)


@router.post("/api/board/download")
def api_board_download(payload: BoardDownload):
    """入队揭示板下载/更新（零 token）。"""
    return _board_enqueue(dict(payload))


@router.post("/api/board/{author}/update")
def api_board_update(author: str, payload: BoardDownload):
    """按 index.json 记的板信息更新该作者的全部贴（增量：活跃贴 + 已归档 eid 直接重抓）。"""
    meta = _board_index_meta(author)
    if not meta:
        raise HTTPException(404, f"揭示板 {author} 无 index.json，无法自动更新——请在 /board 页用表单指定板/关键词")
    p = {
        "board": payload.board if payload.board != "FOX" else (meta.get("board") or "FOX"),
        "host": (payload.host if payload.host != BOARD_DEFAULT_HOST else "") or meta.get("host") or BOARD_DEFAULT_HOST,
        "match": meta.get("match") or "",
        "author": meta.get("author") or "",
        "dir": meta.get("dir") or author,
        "title": meta.get("title") or "",
        "work_id": meta.get("work_id") or "",
        "label": meta.get("label") or "",
        "kako_from": payload.kako_from,
        "kako_to": payload.kako_to,
        "scan_kako": payload.scan_kako,
        "force": payload.force,
    }
    return _board_enqueue(p)


@router.get("/api/board/task")
def api_board_task(dir: str = ""):
    """该揭示板目录最近一次下载/更新任务（前端轮询进度用）。"""
    latest = None
    for t in TM.list_tasks():
        if t.get("type") != "board":
            continue
        if dir and (t.get("params") or {}).get("dir") != dir:
            continue
        if latest is None or (t.get("created_ts") or 0) > (latest.get("created_ts") or 0):
            latest = t
    return {"ok": True, "dir": dir, "task": _task_public(latest, log_tail=8) if latest else None}


@router.get("/api/board/{author}/info")
def api_board_info(author: str):
    """揭示板板信息（作者页「更新」按钮预填 + 显示来源）。"""
    meta = _board_index_meta(author)
    d = _safe_board_author(author)
    if not d:
        raise HTTPException(404, f"未知揭示板连载: {author}")
    meta["exists"] = True
    return {"ok": True, "meta": meta}
