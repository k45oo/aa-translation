# -*- coding: utf-8 -*-
"""任务域：翻译/校对控制台与页、统一任务管理器接口、消耗预估。"""
import json
import os
import time
import registry
from fastapi import HTTPException, Request
from aa_web_app import web_state
from aa_web_app import logic
from aa_web_app import task_manager as TM
from aa_web_app.web_state import (app, templates)
from fastapi import APIRouter

router = APIRouter()


# ---- 翻译任务（2026-08-21：勾章节 → 后台子进程跑 translate_runner → 轮询）----


def _task_file(cfg):
    return os.path.join(cfg["work_dir"], "runner_task.json")


def _lock_file(cfg):
    return os.path.join(cfg["work_dir"], ".runner.lock")


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _task_status(cfg):
    """读 runner_task.json；无任务/已结束返回 None。"""
    tf = _task_file(cfg)
    if not os.path.exists(tf):
        return None
    try:
        with open(tf, encoding="utf-8") as f:
            t = json.load(f)
    except Exception:
        return None
    # 校正 running 状态（进程退出但没更新文件）
    if t.get("status") == "running" and t.get("pid"):
        if not _pid_alive(t["pid"]):
            t["status"] = "crashed"
    return t


def _read_log_tail(cfg, n=60):
    lp = os.path.join(cfg["work_dir"], "runner_log.txt")
    if not os.path.exists(lp):
        return ""
    try:
        with open(lp, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:])
    except Exception:
        return ""


@router.get("/work/{work}/translate")
def translate_page(request: Request, work: str):
    cfg = web_state.cfg_of(work)
    aa_dir, tr_dir = cfg["aa_dir"], cfg["out_dir"]
    chapters = logic.merged_chapters(aa_dir, tr_dir, cfg.get("name_style"),
                                     os.path.join(cfg["work_dir"], "chapter_order.json"))
    chs = []
    for c in chapters or []:
        # 2026-10-17: 压缩感知——磁盘可能是 <tr_file>.gz，直接 join 判存在会把**每一章**都算成未译
        # （校对页因此「读不到章节」：列不出可校对章）。
        tr_path = logic.txt_path(tr_dir, c["tr_file"])
        translated = bool(c.get("has_tr")) or os.path.exists(tr_path)
        chs.append({
            "src_file": c["src_file"], "tr_file": c["tr_file"],
            "title": c.get("title") or c["src_file"],
            "translated": translated,
        })
    task = _task_public(_latest_task_for_work(work))   # 2026-08-29 读管理器
    return templates.TemplateResponse(request, "translate.html", {
        "work": work, "cfg": cfg, "chapters": chs,
        "task": task, "log_tail": _read_log_tail(cfg) if task else "",
        "active": "works"})


@router.get("/work/{work}/proofread")
def proofread_page(request: Request, work: str):
    """2026-09-07: 单独校对控制页(选已译章 → 启动 refine_aa 校对任务)。对称翻译控制台。"""
    cfg = web_state.cfg_of(work)
    aa_dir, tr_dir = cfg["aa_dir"], cfg["out_dir"]
    chapters = logic.merged_chapters(aa_dir, tr_dir, cfg.get("name_style"),
                                     os.path.join(cfg["work_dir"], "chapter_order.json"))
    chs = []
    for c in chapters or []:
        # 2026-10-17: 压缩感知——磁盘可能是 <tr_file>.gz，直接 join 判存在会把**每一章**都算成未译
        # （校对页因此「读不到章节」：列不出可校对章）。
        tr_path = logic.txt_path(tr_dir, c["tr_file"])
        translated = bool(c.get("has_tr")) or os.path.exists(tr_path)
        chs.append({
            "src_file": c["src_file"], "tr_file": c["tr_file"],
            "title": c.get("title") or c["src_file"],
            "translated": translated,
        })
    task = _task_public(_latest_task_for_work(work))
    return templates.TemplateResponse(request, "proofread.html", {
        "work": work, "cfg": cfg, "chapters": chs,
        "task": task, "log_tail": _read_log_tail(cfg) if task else "",
        "active": "works"})


def _aa_config():
    """懒导入 aa_config（scripts/common 已在 sys.path；工作台只在模型多选处用到）。"""
    import aa_config as AC
    return AC


def _chain_env(work, body, kind):
    """「付费链模型多选」→ 任务 env（2026-09-22）。

    勾选集 == 默认集 → 返回 {}（不写 env = 整条链，含 `fallback_only` 兜底端点，与旧行为一致）。
    勾选集不同 → 翻译写 `AA_CHAIN`；**校对链已跟随付费链**（2026-09-22 `[refine_aa].follow_default`），
    所以翻译任务**同时写 `AA_REFINE_BACKEND`**（翻译任务的校对阶段也用勾选模型），
    独立校对任务只写 `AA_REFINE_BACKEND`。
    """
    mods = [str(x).strip() for x in ((body or {}).get("models") or []) if str(x).strip()]
    if not mods:
        return {}
    AC = _aa_config()
    try:
        r18 = bool((web_state.cfg_of(work) or {}).get("r18"))
        _all, dflt = AC.chain_catalog(r18)          # 校对链跟随付费链 → 默认集同源
    except Exception:
        return {"AA_CHAIN": ",".join(mods), "AA_REFINE_BACKEND": ",".join(mods)}
    if set(mods) == set(dflt):
        return {}
    if kind == "proofread":
        return {"AA_REFINE_BACKEND": ",".join(mods)}
    return {"AA_CHAIN": ",".join(mods), "AA_REFINE_BACKEND": ",".join(mods)}


@router.get("/api/work/{work}/models")
def api_work_models(work: str, type: str = "translate"):
    """候选模型（端点）清单 —— 翻译/校对控制台的「付费链模型多选」（2026-09-22）。

    零 API：只读 `model_config.toml` 的链定义 + 单价表。
    query: type=translate（主翻译链 normal/r18，按作品 r18）| proofread（`[refine_aa].backends`）
    返回 {ok, type, r18, chain:[{backend,model,label,cache,r18,fallback_only,available,rate}],
          selected:[条目 id]（**当前生效勾选集** = 设置页保存的 AA_CHAIN / AA_REFINE_BACKEND
          与本作品链的交集；未设 = 整条链的默认集）, default:[条目 id], custom:bool,
          head:"主力端点（= 生效链第一个可缓存端点，与实际跑的头一个端点同口径）"}
    """
    try:
        AC = _aa_config()
        try:
            r18 = bool((web_state.cfg_of(work) or {}).get("r18"))
        except Exception:
            r18 = False
        if type == "proofread":
            chain, default = AC.refine_catalog(r18)
            _env_name = "AA_REFINE_BACKEND"
        else:
            chain, default = AC.chain_catalog(r18)
            _env_name = "AA_CHAIN"
        # 生效勾选集（2026-10-10）：设置页保存的选择 = 控制台多选的默认勾选，两页不再各说各话。
        # 用户选择的条目可能不在本作品的链上（如给 R18 作品选了不接 R18 的 mimo）→ 交集为空时回默认集。
        sel = AC.chain_selection(_env_name, chain, default)
        ids = {it["id"] for it in chain}
        picked = [x for x in sel["selected"] if x in ids] if sel["custom"] else []
        selected = picked or list(default)
        custom = bool(picked)
        # 生效链 = TOML 链序 ∩ 勾选集（与 aa_config.backend_chain 同序；未自定义 = 整条链）
        eff = [it for it in chain if it["id"] in selected] if custom else list(chain)
        if not eff:
            eff = list(chain)
        head = ""
        for it in eff:                        # 主力 = 生效链第一个可缓存端点（章内 pin 目标）
            if it["available"] and not it.get("fallback_only") and it.get("cache"):
                head = it["label"]
                break
        if not head:
            for it in eff:
                if it["available"] and not it.get("fallback_only"):
                    head = it["label"]
                    break
        if not head and eff:
            head = eff[0]["label"]
        return {"ok": True, "type": "proofread" if type == "proofread" else "translate",
                "r18": r18, "chain": chain, "selected": selected, "default": default,
                "custom": custom, "head": head}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (e.__class__.__name__, e)}


@router.get("/api/work/{work}/estimate")
def api_work_estimate(work: str, type: str = "translate", eids: str = "",
                      force: int = 0, free: int = 0, models: str = "", at: str = ""):
    """翻译/校对任务的 token + 金额预估（2026-09-21，零 API）。

    query: type=translate|proofread · eids=1,2,3（空=全部章节）· force=1（翻译重译已译章也计）
           · free=1（免费模型 → 金额记 0）· models=deepseek,deepinfra（模型多选，空=整条链）
           · at=2026-09-30 00:00（**定时启动时刻**，2026-09-29）→ 另给 `cost_at`（按该时刻倍率折算的金额）
    口径见 aa_web_app/estimate.py 模块 docstring（系数实测来源）。
    """
    from aa_web_app import estimate as EST
    ids = [x.strip() for x in (eids or "").split(",") if x.strip()]
    mods = [x.strip() for x in (models or "").split(",") if x.strip()]
    at_ts = TM.parse_schedule_at(at) if at else None
    try:
        r18 = bool((web_state.cfg_of(work) or {}).get("r18"))
    except Exception:
        r18 = False
    try:
        return EST.estimate(work, type, ids, force=bool(force), free=bool(free),
                            work_r18=r18, models=mods or None, at_ts=at_ts)
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (e.__class__.__name__, e)}


@router.get("/api/work/{work}/translate/status")
def api_translate_status(work: str):
    """2026-08-29 改读管理器: 该作品最近任务。兼容 translate.html 轮询。"""
    t = _latest_task_for_work(work)
    if not t:
        return {"status": "idle", "log": ""}
    return _task_public(t, log_tail=60)


@router.post("/api/work/{work}/translate")
def api_translate_start(work: str, body: dict = None):
    """启动翻译任务（2026-08-29 改走统一任务管理器 enqueue）。body: {eids: [...], force, gen}"""
    body = body or {}
    eids = body.get("eids") or []
    if not eids:
        return {"ok": False, "error": "未选择章节"}
    # 2026-09-06 用户规则: 翻译前必须已有作品术语表(只手动生成, 不自动灌 auto)。
    # 这里提前检查给友好提示(translate_runner 1.7 还有硬拦截兜底)。
    import json as _j
    _gl_p = os.path.join(web_state.cfg_of(work)["work_dir"], f"glossary_{work}.json")
    _has_gl = False
    if os.path.exists(_gl_p):
        try:
            _gd = _j.load(open(_gl_p, encoding="utf-8"))
            _has_gl = len(_gd.get("glossary", [])) > 0
        except Exception:
            _has_gl = False
    if not _has_gl:
        return {"ok": False, "error":
                "无作品术语表，翻译已中止。请先手动生成：\n"
                "① 快速派生(零 token): python scripts/common/gen_work_glossary.py --work "
                f"{work} --force\n"
                "② 完整流程: 作品页「生成术语候选…」→ 候选确认 →「写入作品表」(AI 提取+人工确认)。\n"
                "术语表是翻译硬前置。"}
    chapters = ",".join(str(e) for e in eids)
    params = {
        "chapters": chapters,
        "force": bool(body.get("force")),
        "no_proofread": bool(body.get("no_proofread")),   # 2026-09-07: 校对默认开, 可选禁用
    }
    # 2026-09-29 定时启动（工作台翻译控制台）：排到小米 token plan 折扣时段（每日 00:00-08:00，系数 0.8×）。
    # 约束（用户口径「仅 mimo-v2.6 模型」）：只能钉小米 mimo —— AA_CHAIN / AA_REFINE_BACKEND 都写 mimo，
    # 链内不降级到 deepseek/deepinfra（否则折扣时段的消耗跑到别的付费端点，白等）；
    # R18 作品直接拒绝：r18 链里没有 mimo（v2.6 对 R18 一律 content_filter），写了 AA_CHAIN 也会静默回退整链。
    _sched_ts = None
    if body.get("schedule_at"):
        if body.get("free_mode"):
            return {"ok": False, "error": "定时启动只能配付费链的小米 mimo-v2.6（免费链无 token plan 折扣）"}
        try:
            _is_r18 = bool((web_state.cfg_of(work) or {}).get("r18"))
        except Exception:
            _is_r18 = False
        if _is_r18:
            return {"ok": False, "error": "R18 作品不能用定时启动：小米 mimo 对 R18 一律 content_filter，"
                                          "r18 链里也没有它（请直接启动翻译）"}
        _sched_ts = TM.parse_schedule_at(body.get("schedule_at"))
        if _sched_ts is None:
            return {"ok": False, "error": "定时时间格式不对（用 YYYY-MM-DD HH:MM）"}
        if _sched_ts <= time.time():
            return {"ok": False, "error": "定时时间已过，请选一个将来的时刻（或直接启动）"}
        params["schedule_at"] = _sched_ts
    # 2026-08-29 免费模型选项: free_mode=true → env AA_FREE_MODE=1(免费链)
    # 2026-09-22 付费链模型多选: models=[...] → env AA_CHAIN（勾选集=默认集时不写）
    _env = {}
    if body.get("free_mode"):
        _env["AA_FREE_MODE"] = "1"
    elif _sched_ts is not None:
        _env["AA_CHAIN"] = "mimo"                 # 定时: 只走小米 mimo-v2.6（翻译+校对）
        _env["AA_REFINE_BACKEND"] = "mimo"
    else:
        _env.update(_chain_env(work, body, "translate"))
    if _env:
        params["env"] = _env
    # 2026-09-22 方案 D8/D11: pipeline=pokemon 的作品走专属链（固定内容表预填 + wiki 术语绑定）
    # 2026-10-19: 判定统一走 TM.pipeline_type（enqueue 内也会归一，这里是给提示文案用）
    _ttype = TM.pipeline_type("translate", work)
    r = TM.enqueue(_ttype, work, params)
    if not r.get("ok"):
        return r
    _cn = "宝可梦专属链翻译" if _ttype == "pokemon" else "翻译"
    if r.get("scheduled"):
        return {"ok": True, "msg": r.get("msg") or f"定时{_cn}任务已排队: {r['task_id']}",
                "task_id": r["task_id"], "scheduled": True}
    return {"ok": True, "msg": f"{_cn}任务已入队: {r['task_id']}"}


@router.post("/api/work/{work}/proofread")
def api_proofread_start(work: str, body: dict = None):
    """2026-09-07: 单独校对任务——work 页「校对任务」按钮。body: {eids: [...]}(可选)。

    跑 refine_aa(段级重建+术语注入, 废旧 proofread.py) 对指定章/全部译章 --write。
    复用 task_manager proofread 类型 + 同作品串行(enqueue 自带)。
    """
    body = body or {}
    eids = body.get("eids") or []
    params = {}
    if eids:
        params["chapters"] = ",".join(str(e) for e in eids)
    # 2026-09-22 校对链模型多选: models=[...] → env AA_REFINE_BACKEND（勾选集=默认集时不写）
    _env = _chain_env(work, body, "proofread")
    if _env:
        params["env"] = _env
    r = TM.enqueue("proofread", work, params)
    if not r.get("ok"):
        return r
    return {"ok": True, "msg": f"校对任务已入队: {r['task_id']}", "task_id": r["task_id"]}


@router.post("/api/work/{work}/translate/cancel")
def api_translate_cancel(work: str):
    """取消/终止（2026-08-29 兼容旧语义=强制终止；建议新任务页用两档优雅停止）"""
    t = _latest_task_for_work(work)
    if not t or not t.get("pid"):
        return {"ok": False, "error": "无运行中任务"}
    return TM.force_kill(t["task_id"])


# ---- 统一任务管理器 web 接口(2026-08-29) ----


_FINAL_STATUSES = ("done", "failed", "crashed", "stopped", "cancelled", "stalled")


def _latest_task_for_work(work):
    """该作品最近一个非终态任务，无则最近一个终态任务。"""
    ts = [t for t in TM.list_tasks() if t.get("work") == work]
    live = [t for t in ts if t.get("status") not in
            ("done", "failed", "crashed", "stopped", "cancelled", "stalled")]
    return (live or ts)[0] if (live or ts) else None


def _task_public(t, log_tail=0):
    """把管理器任务映射成前端 json（status/chapters/log/phase 等）。"""
    if not t:
        return None
    d = {k: t.get(k) for k in ("task_id", "status", "pid", "rc", "started", "finished",
                               "type", "work", "stop_mode")}
    d["chapters"] = (t.get("params") or {}).get("chapters")
    # 2026-09-29 定时任务: 前端显示「定时 MM-DD HH:MM 启动」（status=scheduled = 未到点，还没启动）
    _sched = (t.get("params") or {}).get("schedule_at")
    if _sched:
        try:
            d["schedule_ts"] = float(_sched)
            d["scheduled_at"] = time.strftime("%m-%d %H:%M", time.localtime(float(_sched)))
        except (TypeError, ValueError):
            pass
    # 2026-09-13 链式后续（download → translate）：前端可显示"完成后自动接 <task_id>"
    if t.get("followup"):
        d["followup"] = t["followup"]
    # 2026-08-29 标题(registry 日文名)供前端同时显示
    # 2026-08-30: registry.get 抛 SystemExit（非 Exception）——作品被删/改名后列表 500 崩溃
    # 2026-10-10: 揭示板任务（伪 work id `board:<板id>`）标题 = 目录/关键词
    try:
        if t.get("type") == "board":
            _bp = t.get("params") or {}
            d["title"] = "揭示板：" + str(_bp.get("dir") or _bp.get("match") or _bp.get("board") or "")
            d["board_dir"] = _bp.get("dir") or ""
        else:
            d["title"] = registry.WORKS.get(t.get("work") or "", {}).get("title", "")
    except Exception:
        d["title"] = ""
    hb = t.get("hb") or {}
    d["phase"] = hb.get("phase")
    d["chapter_idx"] = hb.get("chapter_idx")
    d["chapter_total"] = hb.get("chapter_total")
    if log_tail:
        if t.get("log"):
            d["log"] = t["log"]   # 调用方已提供（api_tasks_get 预取）
        else:
            gt = TM.get_task(t["task_id"], log_tail=log_tail)
            d["log"] = (gt or {}).get("log", "")
    d["usage"] = t.get("usage")
    # 2026-08-29 免费链标记: task 参数 env 含 AA_FREE_MODE=1 即免费链
    d["free_mode"] = bool(((t.get("params") or {}).get("env") or {}).get("AA_FREE_MODE") == "1")
    # 运行时长/预估: running 用 started_ts~now, 终态用 started~finished
    # 2026-08-30 修复: finished_ts 缺失(reconcile/cancel 路径只写 finished 字符串)时
    # 解析 finished 字符串回退, 严禁 fallback time.time() —— 会让已结束任务 elapsed 无限累计
    t0 = t.get("started_ts")
    if t0:
        t1 = t.get("finished_ts")
        if t1 is None and t.get("status") in _FINAL_STATUSES:
            _fs = t.get("finished")
            try:
                t1 = time.mktime(time.strptime(_fs, "%Y-%m-%d %H:%M:%S")) if _fs else None
            except Exception:
                t1 = None
        if t1 is None:
            t1 = time.time()
        d["elapsed"] = max(0.0, t1 - t0)
        hb = t.get("hb") or {}
        idx, tot = hb.get("chapter_idx") or 0, hb.get("chapter_total") or 0
        if t.get("status") not in ("done", "failed", "crashed", "stopped", "cancelled", "stalled") and idx and tot and idx > 0:
            per = d["elapsed"] / idx
            d["eta"] = per * (tot - idx)
    return d


@router.get("/tasks")
def tasks_page(request: Request):
    return templates.TemplateResponse(request, "tasks.html", {
        "active": "tasks", "max_parallel": TM.MAX_PARALLEL,
        "max_parallel_dl": TM.MAX_PARALLEL_DL})


@router.get("/api/tasks")
def api_tasks_list(log: int = 0):
    """log=1 时附带日志尾。

    2026-09-13 用户要求: **任务页不再展示实时日志**(只显示状态/进度), 页面轮询不带 log;
    此参数保留给外部工具/CLI 查日志(单任务走 /api/tasks/<tid>?log=1)。"""
    out = []
    for t in TM.list_tasks():
        pub = _task_public(t)
        if pub:
            pub["usage"] = TM.aggregate_usage(t)
            if log:
                gt = TM.get_task(t["task_id"], log_tail=20)
                pub["log"] = (gt or {}).get("log", "")
            out.append(pub)
    return {"tasks": out}


@router.post("/api/tasks/prune-finished")
def api_tasks_prune_finished():
    """一键清理**全部已结束任务**（2026-09-19 用户要求）。

    只删终态（done/failed/stopped/crashed/cancelled/stalled）；运行中/停止中/排队中
    一律保留（删排队任务 = 静默丢待跑工作）。返回 {ok, removed, count, kept, msg}。"""
    return TM.prune_finished_tasks()


@router.post("/api/tasks")
def api_tasks_enqueue(body: dict = None):
    body = body or {}
    ttype = body.get("type") or "translate"
    work = body.get("work")
    if not work:
        return {"ok": False, "error": "缺 work"}
    params = {"chapters": body.get("chapters") or "new",
              "force": bool(body.get("force")),
              "env": body.get("env") or {}}
    return TM.enqueue(ttype, work, params)


@router.get("/api/tasks/{tid}")
def api_tasks_get(tid: str, log: int = 0):
    """单个任务详情（log=1 带日志尾；供 toggleLog 单任务拉取，避免全量 log=1）。"""
    t = TM.get_task(tid, log_tail=20 if log else 0)
    if not t:
        raise HTTPException(404, f"任务 {tid} 不存在")
    return _task_public(t, log_tail=20 if log else 0)


@router.post("/api/tasks/{tid}/stop")
def api_tasks_stop(tid: str, body: dict = None):
    return TM.request_stop(tid, (body or {}).get("mode") or "chapter")


@router.post("/api/tasks/{tid}/kill")
def api_tasks_kill(tid: str):
    return TM.force_kill(tid)


@router.post("/api/tasks/{tid}/cancel")
def api_tasks_cancel(tid: str):
    return TM.cancel_queued(tid)


@router.post("/api/tasks/{tid}/remove")
def api_tasks_remove(tid: str):
    return TM.remove_task(tid)
