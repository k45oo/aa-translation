# -*- coding: utf-8 -*-
"""术语域：全局术语表 CRUD 与备份、作品术语表、术语候选（生成/确认/白名单/反哺全局）。"""
import json
import os
import sys
import time
import registry
from aa_spawn import child_env, py_prefix
from fastapi import HTTPException, Request
from pydantic import BaseModel
from aa_web_app import web_state
from aa_web_app import logic
from aa_web_app.web_state import (_DATA, _LOCK, _ROOT, app, paths_of, templates)
from aa_web_app.routers.chapters import ApplyGlossaryBody, _log_edits_bulk, _role_hint
from aa_web_app.routers.replace import GlossaryPayload
from fastapi import APIRouter

router = APIRouter()


# ---------- 原站 URL 解析（2026-08-31：非 r401 作品也显示「原站」按钮）----------


@router.get("/glossary")
def glossary_page(request: Request):
    with open(logic._GLOSSARY_JSON, encoding="utf-8") as f:
        data = json.load(f)
    # 2026-09-01 三级重构: 角色块迁出 glossary.json → roles.json, 前端读 roles
    try:
        with open(os.path.join(_DATA, "roles.json"), encoding="utf-8") as f:
            roles_data = json.load(f)
    except Exception:
        roles_data = {"schema": "AA角色表 v1", "更新日期": "", "角色": []}
    works = [{"id": wid, "title": cfg.get("title", wid)}
             for wid, cfg in registry.WORKS.items()]
    return templates.TemplateResponse(request, "glossary.html",
                                      {"data": data, "roles_data": roles_data, "works": works,
                                       "active": "glossary"})


# ---------- 作品专属术语表（每作品独立文件 glossary_<work_id>.json） ----------


class WorkGlossary(BaseModel):
    glossary: list = []
    known_names: list = []
    # 2026-09-19 增量保存: 显式删除项 + 模式（merge=默认增量 / replace=整表覆盖）
    deleted: list = []
    deleted_names: list = []
    mode: str = "merge"
    force: bool = False


@router.get("/api/glossary/{work}")
def api_work_glossary(work: str):
    web_state.cfg_of(work)
    return logic.load_work_glossary(work)


@router.put("/api/glossary/{work}")
def api_work_glossary_put(work: str, body: WorkGlossary):
    """保存作品术语表。

    2026-09-19（用户要求）: **默认增量保存（mode=merge）**——只 upsert 提交的条目 +
    删除 deleted 里显式列出的 jp，文件里未提及的条目原样保留。旧「整表覆盖」语义
    下没加载就点保存（或旧标签页拿着空表保存）会把整部作品的术语写空
    （チンポ_メガテン 事故）；增量语义下同一次误点最多 no-op。
    mode=replace 保留为显式覆盖（清空整表需 force=true，防误清）。
    """
    web_state.cfg_of(work)
    with _LOCK:
        if body.mode == "replace":
            cur = logic.load_work_glossary(work)
            if (cur["glossary"] or cur["known_names"]) and not body.glossary \
                    and not body.known_names and not body.force:
                raise HTTPException(422, "整表覆盖会清空现有 %d 对 / %d 白名单；"
                                         "确认请带 force=true" % (len(cur["glossary"]),
                                                                len(cur["known_names"])))
            ok, err = logic.save_work_glossary(work, body.glossary, body.known_names)
            cur2 = logic.load_work_glossary(work)
            stat = {"added": len(body.glossary), "updated": 0, "kept": 0,
                    "removed": 0, "names_added": len(cur2["known_names"]),
                    "names_removed": 0, "total": len(cur2["glossary"]),
                    "names_total": len(cur2["known_names"])}
            mode = "replace"
        else:
            ok, err, stat = logic.merge_work_glossary(
                work, body.glossary, body.known_names, body.deleted, body.deleted_names)
            mode = "merge"
    if not ok:
        raise HTTPException(500, err)
    web_state.invalidate_stats(work)      # 残留白名单/术语变化 → 统计失效重算
    return {"ok": True, "mode": mode, "glossary": stat["total"],
            "known_names": stat["names_total"], **stat}


class GlossaryPairAdd(BaseModel):
    jp: str = ""
    zh: str = ""
    cat: str = "专名"


@router.post("/api/glossary/{work}/add-pair")
def api_work_glossary_add_pair(work: str, body: GlossaryPairAdd):
    """阅读页「翻译替换 → 历史替换记录 → ＋术语表」：单条历史记录手动加入作品术语表（2026-09-14）。

    服务端 load→append→save（走 _LOCK），**不复用整表 PUT**——前端「读→改→写」整表
    有竞态，会顺手覆盖别处刚存的条目。已存在同 jp 不覆盖，把现状回给前端提示。
    """
    web_state.cfg_of(work)
    with _LOCK:
        r = logic.add_work_glossary_pair(work, body.jp, body.zh, body.cat)
    if r.get("reason") == "error":
        raise HTTPException(500, r.get("error") or "保存失败")
    if r.get("added"):
        web_state.invalidate_stats(work)      # 术语表变化 → 残留白名单/统计失效
    return r


# ========== 作品级元数据（work_meta）：详情弹窗编辑 汉化名/标签/素材/作者 ==========


@router.get("/api/work/{work}/candidates")
def api_candidates(work: str):
    """候选确认列表（已排序：type 顺序 + 来源章节数降序；跳过条目不返回）。"""
    import json as _j, os as _os
    try:
        cfg = web_state.cfg_of(work)
    except HTTPException:
        return {"ok": False, "error": "未知作品"}
    _, _, wd = paths_of(cfg)
    cand_p = _os.path.join(wd, "glossary_candidates.json")
    if not _os.path.exists(cand_p):
        return {"ok": True, "candidates": [], "total": 0}
    with _LOCK:
        data = _j.load(open(cand_p, encoding="utf-8"))
    cands = [c for c in data.get("ai_候选", []) if not c.get("确认")]
    # 排序: type 顺序 + 来源章节数降序(≤3)
    _t_order = {"术语": 0, "专有名词": 1, "拟声梗": 2, "角色": 3}
    def _key(c):
        srcs = c.get("来源") or ""
        n = len([x for x in str(srcs).split(",") if x.strip()])
        return (_t_order.get(c.get("type") or "", 9), -n)
    cands.sort(key=_key)
    return {"ok": True, "candidates": cands, "total": len(cands)}


@router.post("/api/work/{work}/candidates/{term}/confirm")
def api_candidate_confirm(work: str, term: str, body: dict = None):
    """确认候选（设 确认:true + 更新译名/作品/别名_of）。"""
    import json as _j, os as _os
    from urllib.parse import unquote
    term = unquote(term)
    try:
        cfg = web_state.cfg_of(work)
    except HTTPException:
        return {"ok": False, "error": "未知作品"}
    _, _, wd = paths_of(cfg)
    cand_p = _os.path.join(wd, "glossary_candidates.json")
    if not _os.path.exists(cand_p):
        return {"ok": False, "error": "无候选文件"}
    with _LOCK:
        data = _j.load(open(cand_p, encoding="utf-8"))
        hit = None
        for c in data.get("ai_候选", []):
            if (c.get("term") or "") == term:
                hit = c
                break
        if hit is None:
            return {"ok": False, "error": f"候选不存在: {term}"}
        body = body or {}
        if "translation" in body and body["translation"] is not None:
            hit["translation"] = str(body["translation"]).strip()
        if "作品" in body and body["作品"] is not None:
            hit["作品"] = str(body["作品"]).strip()
        if "别名_of" in body and body["别名_of"] is not None:
            hit["别名_of"] = str(body["别名_of"]).strip()
        # 2026-09-07: type 支持人工改(角色/术语/专有名词 → promote cat 落表正确)
        if "type" in body and body["type"] is not None:
            _t = str(body["type"]).strip()
            _ok_t = {"角色": "角色", "术语": "术语", "专有名词": "专有名词", "专名": "专有名词", "人名": "角色", "人物": "角色"}
            if _t in _ok_t:
                hit["type"] = _ok_t[_t]
        hit["确认"] = True
        _j.dump(data, open(cand_p, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=2)
    return {"ok": True, "term": term}


@router.post("/api/work/{work}/candidates/{term}/delete")
def api_candidate_delete(work: str, term: str):
    """永久删除候选（从文件移除）。"""
    import json as _j, os as _os
    from urllib.parse import unquote
    term = unquote(term)
    try:
        cfg = web_state.cfg_of(work)
    except HTTPException:
        return {"ok": False, "error": "未知作品"}
    _, _, wd = paths_of(cfg)
    cand_p = _os.path.join(wd, "glossary_candidates.json")
    if not _os.path.exists(cand_p):
        return {"ok": False, "error": "无候选文件"}
    with _LOCK:
        data = _j.load(open(cand_p, encoding="utf-8"))
        before = len(data.get("ai_候选", []))
        data["ai_候选"] = [c for c in data.get("ai_候选", []) if (c.get("term") or "") != term]
        if len(data["ai_候选"]) == before:
            return {"ok": False, "error": f"候选不存在: {term}"}
        _j.dump(data, open(cand_p, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=2)
    return {"ok": True, "term": term}


@router.post("/api/work/{work}/candidates/{term}/whitelist")
def api_candidate_whitelist(work: str, term: str):
    """候选「白名单」（2026-09-20）：词记入**本作品**表 known_names + 移除本条候选。

    用户要求（2026-09-20）：只记作品白名单，**不动全局** glossary.json——要不要进全局由作品页
    「可反哺全局的条目」区块手动勾选（`POST .../repatriate-apply`，类型=白名单）。
    写作品表走 `logic.merge_work_glossary`（增量并集，表内其余条目原样保留）。
    """
    import json as _j, os as _os
    from urllib.parse import unquote
    term = unquote(term).strip()
    if not term:
        return {"ok": False, "error": "空词"}
    try:
        cfg = web_state.cfg_of(work)
    except HTTPException:
        return {"ok": False, "error": "未知作品"}
    with _LOCK:
        ok, err, stat = logic.merge_work_glossary(work, known_names=[term])
    if not ok:
        return {"ok": False, "error": err or "作品表写入失败"}
    # 本作品候选文件里移除该条（与 delete 同口径：已确认/未确认都移除）
    _, _, wd = paths_of(cfg)
    cand_p = _os.path.join(wd, "glossary_candidates.json")
    removed = 0
    if _os.path.exists(cand_p):
        with _LOCK:
            data = _j.load(open(cand_p, encoding="utf-8"))
            before = len(data.get("ai_候选", []))
            data["ai_候选"] = [c for c in data.get("ai_候选", []) if (c.get("term") or "") != term]
            removed = before - len(data["ai_候选"])
            if removed:
                _j.dump(data, open(cand_p, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=2)
    return {"ok": True, "term": term, "added": bool(stat.get("names_added")),
            "names_total": stat.get("names_total"), "removed": removed}


@router.post("/api/work/{work}/candidates/reset-skipped")
def api_candidates_reset_skipped(work: str):
    """重置本轮跳过（前端 sessionStorage 清空，服务端无状态）。"""
    return {"ok": True}


@router.post("/api/work/{work}/candidates/promote-work-glossary")
def api_candidates_promote_work_glossary(work: str):
    """批量写入：把 glossary_candidates.json 中 确认:true 的候选写进作品术语表
    (不覆盖 manual 同 jp 条目)，角色候选同时进知识库 人物 块。

    2026-09-06 grilling 定稿②: 分类三类——type 角色/人物/人名→cat 角色；
    专有名词/术语/拟声梗→cat 专名；jp 以 re: 开头→cat 正则。
    返回 {ok, written(条数), skipped(与manual同jp跳过), detail}。
    """
    import json as _j, os as _os
    try:
        cfg = web_state.cfg_of(work)
    except HTTPException:
        return {"ok": False, "error": "未知作品"}
    _, _, wd = paths_of(cfg)
    cand_p = _os.path.join(wd, "glossary_candidates.json")
    if not _os.path.exists(cand_p):
        return {"ok": False, "error": "无候选文件"}
    with _LOCK:
        data = _j.load(open(cand_p, encoding="utf-8"))
        confirmed = [c for c in data.get("ai_候选", []) if c.get("确认")]
        if not confirmed:
            return {"ok": True, "written": 0, "skipped": 0, "detail": [], "msg": "无已确认候选"}

        def _cat_of(c):
            jp = (c.get("term") or "").strip()
            typ = c.get("type") or ""
            if jp.startswith("re:"):
                return "正则"
            if any(k in typ for k in ("角色", "人物", "人名")):
                return "角色"
            return "专名"

        # 读现有作品表, 拿 manual 同 jp(不覆盖人工确认) 与 auto 集合
        gl_path = os.path.join(wd, f"glossary_{work}.json")
        gl, gl_names = [], []
        if _os.path.exists(gl_path):
            try:
                _ex = _j.load(open(gl_path, encoding="utf-8"))
                gl = _ex.get("glossary", [])
                # 2026-09-19: known_names 必须原样带回去——旧代码传 [] 会把整部作品的
                # 残留校验白名单清空(与「保存按钮整表覆盖」同类的静默数据丢失)
                gl_names = list(_ex.get("known_names", []) or [])
            except Exception:
                gl, gl_names = [], []
        manual_jp = set()
        for p in gl:
            if isinstance(p, dict):
                if p.get("来源") == "manual" and p.get("jp"):
                    manual_jp.add(p["jp"])
            elif isinstance(p, list) and len(p) == 2:
                manual_jp.add(p[0])

        written, skipped = [], []
        new_pairs = []
        for c in confirmed:
            jp = (c.get("term") or "").strip()
            tr = (c.get("translation") or "").strip()
            if not jp or not tr:
                skipped.append({"term": jp, "why": "缺译名"})
                continue
            if jp in manual_jp:
                skipped.append({"term": jp, "why": "作品表 manual 已有"})
                continue
            cat = _cat_of(c)
            new_pairs.append({"jp": jp, "zh": tr, "cat": cat, "来源": "manual"})
            written.append({"term": jp, "zh": tr, "cat": cat})

        # 角色候选 → 知识库人物(与 CLI _promote_confirmed 同结构: people[t]=tr 或 tr(作品))
        try:
            sys.path.insert(0, os.path.join(_ROOT, "scripts", "tools"))
            import work_memory as _WM
            mem = _WM.load_knowledge(work)
            people = mem.setdefault("人物", {})
            terms = mem.setdefault("术语", {})
            for c in confirmed:
                jp = (c.get("term") or "").strip()
                tr = (c.get("translation") or "").strip() or "(待定译名)"
                typ = c.get("type") or ""
                _w = (c.get("作品") or "").strip()
                if not jp:
                    continue
                if _cat_of(c) == "角色":
                    _alias_of = (c.get("别名_of") or "").strip()
                    if _alias_of:
                        continue  # 别名归并走全局角色表(CLI 专用), 此处跳过不落人物
                    _val = f"{tr}({_w})" if _w and _w not in tr else tr
                    if jp not in people:
                        people[jp] = _val
                elif jp not in terms and not jp.startswith("re:"):
                    terms[jp] = tr
            if people or terms:
                _WM.save_knowledge(work, mem)
        except Exception as _e:
            print(f"[promote-work-glossary] 知识库写入失败(不阻塞): {_e}")

        # 合并进作品表并保存(manual 前 auto 后; 新条目 manual)
        if new_pairs:
            merged = gl + new_pairs
            from aa_web_app.logic import save_work_glossary
            ok, err = save_work_glossary(work, merged, gl_names, path=gl_path)
            if not ok:
                return {"ok": False, "error": f"作品表写入失败: {err}"}
        # 从候选移除已写入项(防重复 promote)
        data["ai_候选"] = [c for c in data.get("ai_候选", []) if not c.get("确认")]
        _j.dump(data, open(cand_p, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=2)
    return {"ok": True, "written": len(written), "skipped": skipped,
            "detail": written, "msg": f"已写入作品表 {len(written)} 条"}


# ---------- 反哺全局（2026-09-20 方案 doc/方案归档/术语表反哺与候选池差集修复方案_2026-09-20.md） ----------


class RepatriateBody(BaseModel):
    selected: list = []      # 勾选的日文词（repatriate-list 的 jp）
    backup: bool = True      # 落盘前备份 roles.json/glossary.json 到 .glossary_backup/<ts>/


@router.get("/api/work/{work}/repatriate-list")
def api_repatriate_list(work: str, embed: int = 1):
    """可反哺全局的条目 = 本作品表 manual − 全局(glossary.json+roles.json) 差集。

    零 token、纯本地。embed=1 时按**本作品语料**算短词嵌入率（大作品略慢）。
    返回 {ok, work, items[], summary}；items 按「可反哺 → 跨作品次数降序 → 长度」排。
    """
    try:
        web_state.cfg_of(work)
    except HTTPException:
        return {"ok": False, "error": "未知作品"}
    import glossary_repatriate as _GR
    res = _GR.scan(work, with_embed=bool(int(embed)))
    return {"ok": True, **res}


@router.post("/api/work/{work}/repatriate-apply")
def api_repatriate_apply(work: str, body: RepatriateBody):
    """把勾选项写入全局：角色 → roles.json；专名 → glossary.json `特殊-专名`；两侧 known_names。

    服务端按勾选 jp **重新 scan 取项**（不信任前端送来的 zh/cat，防陈旧或被改坏）；
    短词专名自动包 `re:` 词边界由 core 负责（re: 对不注入 prompt，只做本地替换）。
    写前自动备份，写入幂等（已在全局的项跳过）。
    """
    try:
        web_state.cfg_of(work)
    except HTTPException:
        return {"ok": False, "error": "未知作品"}
    want = {str(x).strip() for x in (body.selected or []) if str(x).strip()}
    if not want:
        return {"ok": False, "error": "未勾选任何条目"}
    import glossary_repatriate as _GR
    res = _GR.scan(work, with_embed=False)
    items = [it for it in res["items"] if it["jp"] in want and not it["跳过"]]
    dropped = [{"jp": it["jp"], "why": it["跳过"]}
               for it in res["items"] if it["jp"] in want and it["跳过"]]
    if not items:
        return {"ok": False, "error": "勾选项无可写入内容（已跳过或缺译名）",
                "skipped_selection": dropped}
    with _LOCK:
        out = _GR.apply(items, work, backup=bool(body.backup))
    return {"ok": True, "skipped_selection": dropped, **out}


# ---------- 生成术语候选（2026-09-06 grilling ②, 后台 subprocess + 状态轮询） ----------


_GEN_CANDS = {}  # work -> {state, ts, pid, out_tail}


@router.post("/api/work/{work}/gen-candidates")
def api_gen_candidates(work: str):
    """后台跑 gen_glossary_ai --work <id> --full（全章遍历 AI 提取候选）。

    幂等: 同作品已有运行中的生成任务则拒绝(409)。
    """
    import subprocess
    try:
        cfg = web_state.cfg_of(work)
    except HTTPException:
        return {"ok": False, "error": "未知作品"}
    st = _GEN_CANDS.get(work)
    if st and st.get("state") in ("running", "starting"):
        return {"ok": False, "error": "该作品已有生成任务运行中"}
    aa_dir = cfg["aa_dir"]
    if not os.path.isdir(aa_dir):
        return {"ok": False, "error": f"aa 目录不存在: {aa_dir}"}
    # 2026-09-07: 两段式 --full 偶发被外部回收(~2min 无traceback强杀)——改用守护脚本
    #   run_full_pool_until_done.py 自动续跑(每轮共享 pool_progress 幂等续), 直到 candidates 生成
    logf = os.path.join(cfg["work_dir"], "gen_candidates.log")
    try:
        env = child_env()      # 子任务环境（冻结 exe 下带上 AA_BASE/AA_ENV）
        env.setdefault("AA_R18", "1" if cfg.get("r18") else "")
        proc = subprocess.Popen(
            py_prefix() + [os.path.join(_ROOT, "scripts", "common", "run_full_pool_until_done.py"),
             "--work", work, "--max-rounds", "30"],
            cwd=_ROOT, env=env,
            stdout=open(logf, "w", encoding="utf-8"), stderr=subprocess.STDOUT)
    except Exception as e:
        return {"ok": False, "error": f"启动失败: {e}"}
    _GEN_CANDS[work] = {"state": "running", "ts": time.time(), "pid": proc.pid}
    return {"ok": True, "msg": "已启动全章候选提取（后台运行，可轮询状态）"}


@router.get("/api/work/{work}/gen-candidates/status")
def api_gen_candidates_status(work: str):
    """生成候选任务状态: {state: running|done|error, progress: {total,cur,count}|None,
    tail: 日志尾部}。progress 解析自 gen_glossary_ai stdout(--full 逐批 flush)。"""
    st = _GEN_CANDS.get(work)
    if not st:
        return {"ok": True, "state": "idle"}
    cfg = registry.WORKS.get(work)
    tail = ""
    progress = None
    if cfg:
        logf = os.path.join(cfg["work_dir"], "gen_candidates.log")
        try:
            with open(logf, "r", encoding="utf-8", errors="replace") as f:
                logtxt = f.read()
            tail = logtxt[-2000:]
            # 解析进度: 兼容两段式新格式([pool] 批X/Y: 输入N → 专名M (累计 K))
            # 与旧整批格式(共 N 批 / 批Y: +N 条(累计 M))
            import re as _re
            m_total = _re.search(r"共 (\d+) 批", logtxt)
            pool_total = _re.search(r"批(\d+)/(\d+): 输入\d+ → 专名\d+", logtxt)
            if pool_total:
                total = int(pool_total.group(2))
                # 当前批: 取最后出现的 批X/Y (累计 K 后可能有 ", 已存progress" 后缀)
                all_pb = _re.findall(r"批(\d+)/(\d+): 输入\d+ → 专名(\d+) \(累计 (\d+)", logtxt)
                if all_pb:
                    cur = int(all_pb[-1][0])
                    total = int(all_pb[-1][1])
                    count = int(all_pb[-1][3])
                else:
                    cur, count = 0, 0
                progress = {"total": total, "cur": cur, "count": count}
            elif m_total:
                batches = _re.findall(r"批(\d+): \+(\d+) 条\(累计 (\d+)\)", logtxt)
                if m_total:
                    total = int(m_total.group(1))
                    cur = int(batches[-1][0]) if batches else 0
                    count = int(batches[-1][2]) if batches else 0
                    progress = {"total": total, "cur": cur, "count": count}
        except Exception:
            tail = ""
    if st["state"] == "running":
        # 进程结束检测(pid 存活检查)
        import ctypes
        _PH = ctypes.windll.kernel32.OpenProcess
        _hr = ctypes.windll.kernel32.OpenProcess(0x1000, False, st.get("pid") or 0)
        alive = bool(_hr)
        if _hr:
            ctypes.windll.kernel32.CloseHandle(_hr)
        if not alive:
            st["state"] = "done"
            st["ts"] = time.time()
    return {"ok": True, "state": st["state"], "ts": st.get("ts"),
            "progress": progress, "tail": tail[-1200:]}


def _mat_scan_from_knowledge(work, cfg, files):
    """从作品知识库 人物 生成素材(2026-08-28)。返回 [(mat_key, count), ...] 或 None(无人物)。

    - 读 work/<work>_knowledge.json 的 人物: key=日文短名, value=中文译名(作品)。
    - 匹配词展开: key(日文短名) + 中文译名(去括号) + 同名在全局角色表的别名。
    - 统计汉化文本出现次数, ≥3次 且 占比≥5% 取前8, mat key 用「中文（作品名）」或中文。
    返回 None = 知识库无人物(调用方回退全局角色表)。
    """
    from collections import Counter
    import shlex
    # 读知识库人物
    kn_p = os.path.join(cfg.get("work_dir", ""), work + "_knowledge.json")
    people = {}
    if os.path.exists(kn_p):
        try:
            _kn = json.load(open(kn_p, encoding="utf-8"))
            people = _kn.get("人物", {}) or {}
        except Exception:
            people = {}
    if not people:
        return None  # 无人物 → 回退全局角色表
    # 全局角色表(取同名角色的别名); 2026-09-01: 角色块迁出 glossary.json → roles.json
    _gl = json.load(open(os.path.join(_DATA, "roles.json"), encoding="utf-8"))
    _aliases = {}
    for c in _gl.get("角色", []):
        _aliases.setdefault(c.get("日文") or c.get("中文"), set()).update(c.get("别名") or [])
    # 每个知识库人物展开匹配词
    roles = []  # [(mat_key, [match_words])]
    for jp, val in people.items():
        # value 形如 "东北切蒲英(VOICEROID)" 或 "雷(舰队collection)" 或纯 "雷"
        val = str(val).strip()
        cn = val.split("(")[0].strip()
        wname = ""
        if "(" in val and val.endswith(")"):
            wname = val[val.index("(")+1:-1].strip()
        words = {jp, cn}
        # 同名全局别名并入(如 青蛙子/ケロちゃん)
        for _a in (_aliases.get(jp) or set()):
            if len(_a) >= 2:
                words.add(_a)
        words = {w for w in words if len(w) >= 2}  # 排除单字符(L行号碰撞)
        mat_key = f"{cn}（{wname}）" if wname else cn
        roles.append((mat_key, list(words)))
    # 统计
    tot_chars = 0
    cnt = Counter()
    for f in files:
        t = open(f, encoding="utf-8", errors="replace").read()
        tot_chars += len(t)
        for key, wl in roles:
            n = sum(t.count(w) for w in wl)
            if n:
                cnt[key] += n
    # 阈值: ≥3次 且 占比≥5% 取前8
    tot = sum(cnt.values())
    mats = []
    for key, c in cnt.most_common():
        if c < 3:
            break
        pct = c * 100 / tot if tot else 0
        if pct < 5:
            continue
        mats.append((key, c))
        if len(mats) >= 8:
            break
    return mats or ([], {})


# ---------- 台词高亮 gold 标注（2026-08-29 第 2 步，精译 ground truth 回归集） ----------


@router.get("/api/work/{work}/glossary-preview")
def api_glossary_preview(work: str, pairs: str = ""):
    cfg = web_state.cfg_of(work)
    aa_dir, out_dir = cfg["aa_dir"], cfg["out_dir"]
    # 分组: 作品专属替换对前置 + 全局替换对（作品对在前，同词以作品为准）
    # 2026-09-01: work 表 v2 对象 → 归一化二元组(修 ValueError); 附加角色提示
    global_pairs = logic._norm_pairs(logic.load_glossary_pairs())
    work_entries = logic.load_work_glossary(work)["glossary"]
    work_pairs = logic._norm_pairs(work_entries)
    work_hints = {}
    for e in work_entries:
        if isinstance(e, dict) and e.get("jp"):
            h = _role_hint(e["jp"])
            if h:
                work_hints[e["jp"]] = h
    groups = []
    if work_pairs:
        groups.append({"src": "work", "label": "作品专属术语表", "pairs": work_pairs,
                       "hints": work_hints})
    if global_pairs:
        groups.append({"src": "global", "label": "全局术语表", "pairs": global_pairs})
    all_pairs = global_pairs + work_pairs
    # pairs query: 逗号分隔 "日文|中文"，只算这些对（重新计算按钮用；空=全部）
    if pairs:
        sel = [p.split("|", 1) for p in pairs.split(",") if "|" in p]
        all_pairs = [p for p in all_pairs if p in sel or [str(p[0]), str(p[1])] in sel]
    files = []
    total = 0
    for fname in logic.chapter_order(out_dir, cfg.get("name_style")):
        changed, skipped = logic.apply_glossary_chapter(
            os.path.join(aa_dir, logic.src_name_of(fname)),
            os.path.join(out_dir, fname), all_pairs, dry_run=True)
        if changed or skipped:
            # 2026-09-01: 每行带 chapter(章节文件)+line(行号)+overwide(超宽警告), 前端可跳转阅读器预览
            files.append({"file": fname, "changed": len(changed), "skipped": skipped,
                          "lines": [[i + 1, b[:100], nb[:100], fname, i + 1, bool(ow)]
                                    for i, b, nb, *ow in changed[:20]]})
            total += len(changed)
    return {"groups": groups, "total": total, "files": files,
            "pairs": all_pairs}


@router.post("/api/work/{work}/apply-glossary")
def api_apply_glossary(work: str, body: ApplyGlossaryBody):
    cfg = web_state.cfg_of(work)
    aa_dir, out_dir = cfg["aa_dir"], cfg["out_dir"]
    # 前端自选术语对（空 = 全部）
    # 2026-09-01: work 表 v2 对象 → 归一化(修 ValueError)
    pairs = body.pairs if body.pairs else (
        logic._norm_pairs(logic.load_glossary_pairs())
        + logic._norm_pairs(logic.load_work_glossary(work)["glossary"]))
    total = 0
    with _LOCK:
        for fname in logic.chapter_order(out_dir, cfg.get("name_style")):
            changed, _ = logic.apply_glossary_chapter(
                os.path.join(aa_dir, logic.src_name_of(fname)),
                os.path.join(out_dir, fname), pairs, dry_run=False)
            total += len(changed)
            # 逐行记改前/改后（替代原「整文件备份目录」，2026-10-01）
            _log_edits_bulk(cfg, fname, [(c[0], c[1], c[2]) for c in changed],
                            "术语替换")
    web_state.invalidate_stats(work)
    return {"ok": True, "applied": total, "pairs": len(pairs)}


# ---- 预览扫描结果复用（2026-09-17）----------------------------------------


@router.get("/api/glossary")
def api_glossary_get():
    with open(logic._GLOSSARY_JSON, encoding="utf-8") as f:
        return json.load(f)


@router.put("/api/glossary")
def api_glossary_put(payload: GlossaryPayload):
    data = {"known_names": payload.known_names,
            "glossary": payload.glossary,
            "角色": payload.roles}
    with _LOCK:
        r = logic.save_glossary(data)
        if r["ok"] and payload.roles:
            # 2026-09-01 三级重构: 角色保存到 roles.json
            rr = logic.save_roles(payload.roles)
            if not rr["ok"]:
                raise HTTPException(422, {"errors": rr["errors"]})
            r["角色"] = rr["角色"]
    if not r["ok"]:
        raise HTTPException(422, {"errors": r["errors"]})
    return r


@router.get("/api/glossary/backups")
def api_glossary_backups():
    root = os.path.join(os.path.dirname(logic._GLOSSARY_JSON), ".glossary_backup")
    return {"backups": logic.list_backups(root)}


@router.post("/api/glossary/backups/{ts}/restore")
def api_glossary_restore(ts: str):
    root = os.path.join(os.path.dirname(logic._GLOSSARY_JSON), ".glossary_backup")
    dest = os.path.dirname(logic._GLOSSARY_JSON)
    with _LOCK:
        try:
            restored = logic.restore_backup(root, ts, dest)
        except ValueError as e:
            raise HTTPException(404, str(e))
    return {"ok": True, "restored": restored}
