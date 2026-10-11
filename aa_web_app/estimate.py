# -*- coding: utf-8 -*-
"""翻译 / 校对任务的 token 与金额预估（2026-09-21）。

用途：翻译控制台、校对控制台在勾选章节后实时显示「这个任务大约烧多少 token、多少钱」。
零 API：只读本地原文/汉化文件 + 单价表。

口径
----
· 体量 = 所选章节**原文 txt 的全部字符数**（含帖头/`#` 行；与校准样本同口径，别改）。
· 系数 = 实测「每源字符 token 数」，由 usage.jsonl 的 `task` 字段按任务归因测得
  （**2026-10-11 重标定**，只取当前链族 opencode_go / deepseek / deepinfra 的已完成任务——
  mimo 的 tokenizer 对中日文切分不同，同任务报 0.58~0.86 in/字，跨厂商 token 数不可直接比，
  故不计入；样本 = 6 个 task，源字符 10~66 万/章）：

    | 任务（作品/章）         | 章数 | 源字符    | 翻译 in/字 | out/字 | 校对 in/字 | out/字 | 端点 |
    |-------------------------|------|-----------|-----------|--------|-----------|--------|------|
    | 供給源 3146-3155        | 7    | 4,439,707 | 0.298     | 0.109  | 0.015     | 0.002  | deepinfra |
    | 供給源 2995-3004        | 10   | 6,597,971 | 0.301     | 0.117  | 0.022     | 0.004  | deepseek |
    | 供給源 5090-5099/5781    | 10   | 6,006,190 | 0.358     | 0.125  | 0.015     | 0.002  | opencode_go |
    | 大破壊後 31768          | 1    |   452,682 | 0.370     | 0.116  | 0.005     | 0.000  | opencode_go |
    | 大破壊後 31597/8/31645  | 3    | 1,127,974 | 0.329     | 0.090  | 0.021     | 0.005  | deepseek |
    | 供給源 3153-3155        | 3    | 2,127,981 | 0.254     | 0.084  | 0.008     | 0.001  | deepseek |

  默认取**链首（opencode_go）那两个样本**的量级：翻译 (0.36, 0.12)，校对 (0.02, 0.003)。
  实测同链族跨度 0.34~0.49 tok/源字（翻译+校对合计）→ 对外给 **0.7~1.5 倍区间**。
  与 2026-09-21 的旧系数（翻译 0.80/0.19、校对 0.17/0.003）相比低 ≈2.5×——差额正是
  09-22「分块打包」（token −62~66%）与 10-03「校对批量」（refine 调用 −92%）两轮优化的落点，
  旧系数会把消耗/金额高估一倍以上。
· 已跳过章不计入：翻译跳过已有 `_汉化.txt` 的章（`--force` 时全算）；
  校对跳过 refine_ckpt 指纹未变的章（与 refine_aa._ckpt_ok 同口径：done + 原文/汉化 sha1 均未变）。
· 金额 = 单价表 `[pricing.rates]`（aa_config.pricing_for）。**取链首端点**（付费链章内 pin 到最优惠
  的可缓存端点，即 `[default].normal_backends[0]`；校对链 `[refine_aa].backends[0]`）。
  另给两个对照：`cost_peak`（官网高峰时段 ×2）、`cost_cached`（按实测前缀缓存命中 35% 计——
  2026-09-15 的微基准同前缀连发命中 79.6%，但**生产里每块 prompt 只有头部重叠**，实测
  translate 34~45% / 校对 45~53%；旧的 80% 假设会把「命中缓存」那栏算得过于乐观）。
  2026-09-29 定时任务：传 `at_ts`（定时启动时刻）→ 多给 `cost_at`（按该时刻倍率折算，含小米 token plan
  每日 00:00-08:00 的 0.8× 折扣，同源 `aa_cost.time_mult`）与 `off_at`/`off_now`/`off_windows`。
  免费模式（AA_FREE_MODE）金额记 0。
"""
import hashlib
import os
import re
import sys
import time

_DIR = os.path.dirname(os.path.abspath(__file__))          # aa_web_app/
_ROOT = os.path.dirname(_DIR)
_SCRIPTS = os.path.join(_ROOT, "scripts")
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "works")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:                                        # 与 main.py 同口径的包内导入
    from aa_web_app import logic
except ImportError:                          # 直接以脚本路径运行时的兜底
    import logic                             # type: ignore

# 每源字符 token 系数（见模块 docstring 的实测表；2026-10-11 重标定）
COEF = {
    "translate": {"in": 0.36, "out": 0.12},
    "proofread": {"in": 0.02, "out": 0.003},
}
SPREAD_LO, SPREAD_HI = 0.7, 1.5         # 区间系数（实测同链族跨度 0.34~0.49 tok/源字）
CACHE_HIT_ASSUMED = 0.35             # 前缀缓存命中率（生产实测 34~45% / 校对 45~53%；微基准 79.6% 不适用）

# 进程内缓存（工作台长驻）：按章字符数 / 校对跳过判定。勾选变化时只算新增的章。
_SRC_CACHE = {}
_SKIP_CACHE = {}
SKIP_TTL = 120                       # 秒；校对跳过判定（读 ckpt + 算 sha1）的缓存时长


def _cfg_mod():
    import aa_config
    return aa_config


def _eid_of(name):
    m = re.search(r"\d+", name or "")
    return m.group(0) if m else ""


def _file_sha1(p):
    """文件 sha1（与 refine_aa._file_sha1 同口径；缺失返回 ""）。"""
    if not os.path.exists(p):
        return ""
    try:
        h = hashlib.sha1()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _refined_ok(cfg, base):
    """章是否已校对且原文/汉化未变（refine_aa 的增量 ckpt 判定，同口径 → 零调用跳过）。"""
    p = os.path.join(cfg["work_dir"], "refine_ckpt", base + ".json")
    if not os.path.exists(p):
        return False
    try:
        import json
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(d, dict) or not d.get("done"):
        return False
    # 与 refine_aa 一致：只看未压缩的 .txt（.gz 场景判不出 → 视为需要重校，安全方向）
    src_sha = _file_sha1(os.path.join(cfg["aa_dir"], base + ".txt"))
    tr_sha = _file_sha1(os.path.join(cfg["out_dir"], base + "_汉化.txt"))
    if not src_sha or not tr_sha:
        return False
    return d.get("src_sha1") == src_sha and d.get("tr_sha1") == tr_sha


def _src_chars(cfg, src_file):
    """原文行字符数（.txt/.txt.gz 透明读；读不到返回 0）。按 (路径,mtime,size) 缓存。"""
    p = logic.txt_path(cfg["aa_dir"], src_file)
    try:
        st = os.stat(p)
        key = (p, st.st_mtime_ns, st.st_size)
    except OSError:
        return 0
    if key in _SRC_CACHE:
        return _SRC_CACHE[key]
    try:
        lines = logic.read_lines(p)
    except Exception:
        lines = None
    if lines is None:
        try:
            import aa_translate
            lines = aa_translate.txt_read(p)
        except Exception:
            lines = []
    n = sum(len(x) for x in lines)
    _SRC_CACHE[key] = n
    return n


def _refined_ok_cached(cfg, base):
    """_refined_ok + 短 TTL 缓存（勾选变化时反复调用不重复算 sha1）。"""
    key = (cfg["work_dir"], base)
    now = time.time()
    hit = _SKIP_CACHE.get(key)
    if hit and now - hit[0] < SKIP_TTL:
        return hit[1]
    ok = _refined_ok(cfg, base)
    _SKIP_CACHE[key] = (now, ok)
    return ok


def _chain_items(kind, work_r18=False):
    """该任务候选链的端点元数据（含单价），顺序 = 链序。失败返回 []。

    翻译 → `[default].normal_backends`（R18 作品 → r18_backends）；校对 → `[refine_aa].backends`。
    只保留可用端点（缺 key 的排除，与 aa_config.backend_chain 同口径）。
    """
    C = _cfg_mod()
    try:
        if kind == "proofread":
            items, _d = C.refine_catalog(bool(work_r18))
        else:
            items, _d = C.chain_catalog(bool(work_r18))
        avail = [x for x in items if x.get("available")]
        return avail or items
    except Exception:
        return []


def _chain_head(kind, work_r18=False, models=None):
    """(展示名, backend, model, 选中端点列表) —— 主力端点。

    主力 = 选中集合里**第一个 `cache=true`** 的端点（= call_with_fallback 的章内 pin 目标），
    没有可缓存的就取第一个。`models` = 界面勾选的**链条目 id**（`backend|model`，2026-10-08 阶段 2）
    或旧式后端名（= 该后端全部条目）；None/空 = 整条链。
    勾选的全都不可用 → 回退整条链（与 aa_config.backend_chain 同语义）。
    """
    items = _chain_items(kind, work_r18)
    if models:
        sel = [x for x in items if x.get("id") in models or x["backend"] in models] or items
    else:
        # 默认（未勾选）= 可用且非「兜底端点」的那批 —— 与界面默认勾选集一致
        sel = [x for x in items if not x.get("fallback_only")] or items
    if not sel:
        return "?", "", "", []
    pick = next((x for x in sel if x.get("cache")), sel[0])
    return pick["label"], pick["backend"], pick["model"], sel


def _cost(prompt, completion, backend, model, free=False, at_ts=None):
    """(基础金额, 高峰金额, 缓存命中金额, 是否未定价, 定时时刻金额, 定时是否折扣时段) —— 元。

    2026-09-29: `at_ts`（定时启动时刻）→ 按该时刻的时间倍率折算（含小米 token plan 的 00:00-08:00 ×0.8；
    倍率口径与 usage 报表同源 `aa_cost.time_mult`）。单价缺失 → (0,0,0,True,…)。
    """
    if free:
        return 0.0, 0.0, 0.0, False, 0.0, False
    C = _cfg_mod()
    try:
        rate, _key = C.pricing_for(backend, model)
    except Exception:
        rate = None
    if not rate:
        return 0.0, 0.0, 0.0, True, 0.0, False
    miss = rate.get("miss")
    out = rate.get("out")
    hit = rate.get("hit")
    if miss is None or out is None:
        return 0.0, 0.0, 0.0, True, 0.0, False
    base = (prompt * miss + completion * out) / 1e6
    peak = base * float(rate.get("peak", 1.0) or 1.0)
    cached = base
    if hit is not None:
        cached = (prompt * (CACHE_HIT_ASSUMED * hit + (1 - CACHE_HIT_ASSUMED) * miss)
                  + completion * out) / 1e6
    at_cost, off_at = base, False
    if at_ts is not None:
        try:
            import aa_cost
            at_cost = base * aa_cost.time_mult(at_ts, rate)
            off_at = bool(aa_cost.is_off(at_ts, rate))
        except Exception:
            pass
    return base, peak, cached, False, at_cost, off_at


def pending(cfg, chapters, kind, force=False):
    """(将真正处理的章, 会被跳过的章) —— chapters: [{src_file,tr_file,...}]。"""
    will, skip = [], []
    for c in chapters or []:
        src_file = c.get("src_file") or ""
        tr_file = c.get("tr_file") or ""
        if kind == "proofread":
            base = logic.txt_key(src_file)
            if _refined_ok_cached(cfg, base):
                skip.append(c)
            else:
                will.append(c)
        else:
            tr_p = logic.txt_path(cfg["out_dir"], tr_file) if tr_file else ""
            exists = bool(tr_p) and os.path.exists(tr_p)
            if exists and not force:
                skip.append(c)
            else:
                will.append(c)
    return will, skip


def estimate(work, kind, eids=None, force=False, free=False, work_r18=False, models=None, at_ts=None):
    """预估 {kind: translate|proofread} 任务消耗。eids=None → 全部章节。
    `models` = 界面勾选的后端名列表（None/空 = 整条链，见 aa_config.chain_catalog）。
    `at_ts` = 定时启动时刻（unix ts；2026-09-29 定时任务）→ 另给 `cost_at`（该时刻倍率折算的金额）。

    返回 dict（界面直接显示；见模块 docstring 口径）:
      chapters/skipped(章) chars(源字符) prompt/completion(+_lo/_hi tok)
      cost/cost_peak/cost_cached(元) priced/peak_now/rate/basis/note
      models(选中的端点标签) chain(整条链标签) cost_min/cost_max(链内区间, 元)
      cost_at/off_at/at_ts(定时时刻金额与其是否折扣时段) off_now/off_windows/off_factor(折扣窗口口径)
    """
    kind = "proofread" if kind == "proofread" else "translate"
    cfg = cfg_of(work)
    chapters = logic.merged_chapters(cfg["aa_dir"], cfg["out_dir"], cfg.get("name_style"),
                                    os.path.join(cfg["work_dir"], "chapter_order.json"))
    want = set(str(x) for x in (eids or []) if str(x) != "")
    matched = len(chapters or [])
    if want:
        chapters = [c for c in (chapters or []) if _eid_of(c.get("src_file")) in want]
        matched = len(chapters)
    will, skip = pending(cfg, chapters, kind, force=force)
    chars = 0
    for c in will:
        chars += _src_chars(cfg, c.get("src_file"))
    k = COEF[kind]
    prompt = int(round(chars * k["in"]))
    completion = int(round(chars * k["out"]))
    name, backend, model, sel = _chain_head(kind, work_r18=work_r18, models=models)
    base, peak, cached, unpriced, at_cost, off_at = _cost(
        prompt, completion, backend, model, free=free, at_ts=at_ts)
    # 链内区间（多选时给用户看勾选集合里最便宜/最贵的端点；单一端点时 min==max==base）
    cmin = cmax = None
    for it in sel:
        c, _pk, _ch, up, _atc, _off = _cost(prompt, completion, it["backend"], it["model"], free=free)
        if up:
            continue
        cmin = c if cmin is None else min(cmin, c)
        cmax = c if cmax is None else max(cmax, c)
    peak_now = off_now = False
    off_windows, off_factor = [], None
    try:
        import aa_cost
        peak_now = bool(aa_cost.is_peak(time.time()))
        _rate, _rk = _cfg_mod().pricing_for(backend, model)
        if _rate:
            off_now = bool(aa_cost.is_off(time.time(), _rate))
            off_windows = list(_rate.get("off_windows") or [])
            off_factor = _rate.get("off")
    except Exception:
        pass
    return {
        "ok": True, "type": kind, "work": work,
        "chapters": len(will), "skipped": len(skip), "matched": matched,
        "chars": chars,
        "prompt": prompt, "completion": completion,
        "prompt_lo": int(prompt * SPREAD_LO), "prompt_hi": int(prompt * SPREAD_HI),
        "completion_lo": int(completion * SPREAD_LO), "completion_hi": int(completion * SPREAD_HI),
        "cost": round(base, 4), "cost_peak": round(peak, 4), "cost_cached": round(cached, 4),
        "cost_min": round(cmin if cmin is not None else base, 4),
        "cost_max": round(cmax if cmax is not None else base, 4),
        "priced": not unpriced, "peak_now": peak_now,
        "cost_at": round(at_cost, 4), "off_at": bool(off_at), "at_ts": at_ts,
        "off_now": off_now, "off_windows": off_windows, "off_factor": off_factor,
        "rate": name, "free": bool(free),
        "models": [x["label"] for x in sel],
        "models_sel": [x.get("id") or x["backend"] for x in sel],
        "chain": [x["label"] for x in _chain_items(kind, work_r18)],
        "coef": k,
        "note": "粗估（实测跨度 0.7~1.5×）：翻译/校对均含重试；已跳过 {} 章不计".format(len(skip)),
    }


def cfg_of(work):
    """作品路径配置（registry.get）。"""
    import registry
    return registry.get(work)
