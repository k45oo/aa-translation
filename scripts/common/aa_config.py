# -*- coding: utf-8 -*-
"""统一模型配置加载器（2026-08-19 方案 A 落地）。

从 scripts/config/model_config.toml 读取模型/网关/key/参数配置。
优先级: 环境变量(AA_*) > TOML 配置文件 > 代码默认值。
无 TOML 时回落内置默认(≈旧硬编码值) + 警告，保证旧环境零破坏。
"""
import os
import tomllib  # py3.11 标准库

import aa_env  # 仓库根 .env（唯一用户可改层）—— 载入 os.environ，优先级 env > .env > TOML

# 配置路径: AA_CONFIG 环境变量可覆盖(测试隔离用)
CONFIG_PATH = os.environ.get("AA_CONFIG") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "model_config.toml",
)

# 导入即把 <仓库根>\.env 的 AA_*/key 灌进 os.environ（不覆盖已存在的真实 env）。
# 2026-10-08（方案 doc/方案归档/工作台设置页与配置文件方案_2026-10-08.md 阶段 1）：此前 .env 里的 AA_* 根本
# 没人载入 → 「从 .env 配模型」静默无效。失败不阻塞（缺文件/不可读都当没有）。
try:
    aa_env.load_dotenv()
except Exception:
    pass

# 内置默认值(TOML 缺失时回落；与旧硬编码一致)
_DEFAULTS = {
    "default": {
        "backend": "commandcode",
        "model": "xiaomi/mimo-v2.5",
        "gateway": "https://api.commandcode.ai/provider/v1/chat/completions",
        "max_workers": 8,
        "max_output_tokens": 16384,
        "temperature": 0.4,
        "use_proxy": "auto",
    },
    "usage": {"file": "usage.jsonl", "tag": "translate"},
}

# R18 兜底表(TOML 缺失时用; 依据 doc/模型比对_全后端翻译与视觉_2026-08-19.md 实测)
_R18_DEFAULT = {
    "commandcode": True,    # mimo-v2.5 / muse-spark 无审查
    "siliconflow": True,    # GLM-4-9B / Qwen3-VL 全系无审查
    "qwen-vl": True,        # Qwen3-VL 全系无审查
    "minimax": True,
    "mimo": True,
    "runinfra": True,
    "openrouter": True,
    "ollama": True,
}

_cache = None
_config_missing = False


def load():
    """读 TOML, 返回 dict。无 TOML/解析失败 → 回落内置默认 + 警告。"""
    global _cache, _config_missing
    if _cache is not None:
        return _cache
    if _config_missing:
        return dict(_DEFAULTS)
    try:
        with open(CONFIG_PATH, "rb") as f:
            _cache = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        _config_missing = True
        import warnings
        warnings.warn(f"[aa_config] 无法读取 {CONFIG_PATH} ({e})，回落内置默认", RuntimeWarning)
        _cache = dict(_DEFAULTS)
    return _cache


def get(section, key, default=None):
    """读配置, 支持 env 覆盖: AA_<SECTION>_<KEY> 优先(如 AA_DEFAULT_MODEL)。"""
    env_name = f"AA_{section.upper()}_{key.upper()}"
    env = os.environ.get(env_name)
    if env is not None:
        return env
    return load().get(section, {}).get(key, default)


def backend(name):
    """获取后端配置 dict(缺失返回空 dict, 调用方用默认值)。"""
    return load().get("backend", {}).get(name, {})


def backend_key_env(name):
    """后端 key 的 .env 变量名。"""
    return backend(name).get("key_env", "")


def r18_safe(name):
    """后端是否放行 R18 内容。TOML 缺字段时回落内置表(保守默认 False)。
    'cloud'(未设 AA_BACKEND 的云端直接模式) → 映射配置 [default].backend 的 r18。"""
    if name == "cloud":
        name = load().get("default", {}).get("backend", "commandcode")
    cfg = load().get("backend", {}).get(name, {})
    if "r18" in cfg:
        return bool(cfg["r18"])
    return _R18_DEFAULT.get(name, False)


def cache_safe(name):
    """后端是否**会命中前缀缓存**（2026-09-15 实测；TOML [backend.X].cache 缺省 False）。

    实测: deepseek 官网命中 1024/1286=79.6%、冷前缀 8 并发首波仍 79.0%；
          deepinfra/runinfra 网关同前缀连续调用 0/0/0 零命中 → 只有标 true 的端点值得 pin。
    见 doc/方案归档/token成本优化方案_2026-09-15.md §8。
    """
    return bool(load().get("backend", {}).get(name, {}).get("cache", False))


def pin_cache_endpoint():
    """付费链是否章内 pin 会缓存的端点。

    env `AA_PIN_CACHE_ENDPOINT=0` 优先（也支持 `AA_DEFAULT_PIN_CACHE_ENDPOINT`）> TOML [default].pin_cache_endpoint > 默认 True。
    """
    env = os.environ.get("AA_PIN_CACHE_ENDPOINT")
    if env is not None:
        return str(env).strip().lower() not in ("0", "false", "no", "off")
    v = get("default", "pin_cache_endpoint", True)
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() not in ("0", "false", "no", "off")


# ---- 本地代理（2026-10-08 阶段 4，方案 doc/方案归档/工作台设置页与配置文件方案_2026-10-08.md §3 Q7）----
# 此前 `127.0.0.1:10808` 硬编码在 12 处、语义还各不相同；现统一从这里取：
#   proxy_url()    = AA_PROXY > TOML [default].proxy > 默认
#   use_proxy_mode() = AA_USE_PROXY > TOML [default].use_proxy > auto（'0' 一律直连 / '1' 一律代理）
#   proxy_opener() = 走代理的 urllib opener（模式为 '0' 返回 None = 直连）
_PROXY_DEFAULT = "http://127.0.0.1:10808"


def proxy_url():
    """本地代理 URL（AA_PROXY > TOML `[default].proxy` > 内置默认）。"""
    v = os.environ.get("AA_PROXY") or get("default", "proxy", "") or _PROXY_DEFAULT
    return str(v).strip().rstrip("/") or _PROXY_DEFAULT


def use_proxy_mode():
    """代理策略 `auto` / `0`（一律直连）/ `1`（一律代理）；env AA_USE_PROXY > TOML。"""
    v = os.environ.get("AA_USE_PROXY")
    if v is None or str(v).strip() == "":
        v = get("default", "use_proxy", "auto")
    s = str(v).strip().lower()
    if s in ("0", "false", "no", "off"):
        return "0"
    if s in ("1", "true", "yes", "on"):
        return "1"
    return "auto"


def proxy_dict():
    u = proxy_url()
    return {"http": u, "https": u}


def proxy_opener(force=None):
    """走代理的 urllib opener；返回 None = 直连（调用方 `proxy_opener() or build_opener()`）。

    force=True  = 无论模式如何都走代理（历史路径显式要代理时用，如 call_api 的代理轮次）；
    force=None  = 遵循 use_proxy_mode()：'0' → None（直连）。
    """
    if force is None and use_proxy_mode() == "0":
        return None
    import urllib.request
    return urllib.request.build_opener(urllib.request.ProxyHandler(proxy_dict()))


def vision_models():
    """视觉候选模型列表(配置缺失返回空列表)。兼容旧 models 字段。"""
    cfg = load()
    vis = cfg.get("vision", {})
    if vis.get("models_r18") or vis.get("models_normal"):
        # 2026-08-20: 按 R18 分组后, 旧调用方(无 r18 参数)返回 R18 组(默认)
        return vis.get("models_r18", [])
    return vis.get("models", [])


def vision_models_for(work_r18):
    """按作品 R18 属性返回视觉模型组(2026-08-20 分组配置):
    R18 → models_r18(Qwen3-VL-32B + 30B-A3B + mimo兜底)
    非R18 → models_normal(mimo兜底)
    兼容旧配置: 无分组字段回落旧 models 列表"""
    cfg = load()
    vis = cfg.get("vision", {})
    grp = "models_r18" if work_r18 else "models_normal"
    if vis.get(grp):
        return vis.get(grp)
    return vis.get("models", [])


def vision_r18_works():
    """R18 作品 id 名单(registry id; 这些作品只允许 r18=true 的视觉候选)。
    优先读 registry 的 r18 字段(统一数据源); TOML 的 r18_works 为兜底(兼容旧配置)。"""
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "works"))
        import registry as _R
        return [wid for wid, wc in _R.WORKS.items() if wc.get("r18")]
    except Exception:
        pass
    return load().get("vision", {}).get("r18_works", [])


def model_env(name):
    """后端名 → 模型覆盖环境变量名（单一来源, 2026-08-27 收敛）。

    之前 translate_runner 与 aa_backend._model_for_backend 各维护一份相同字典,
    现统一从此读取。未知后端返回 "AA_MODEL"。
    """
    return {
        "siliconflow": "AA_SF_MODEL", "qwen-vl": "AA_QWEN_VL_MODEL",
        "commandcode": "AA_CC_MODEL",
        "mimo": "AA_MIMO_MODEL", "minimax": "AA_MM_MODEL",
        "runinfra": "AA_RI_MODEL", "openrouter": "AA_OR_MODEL",
        "deepseek": "AA_DS_MODEL", "deepinfra": "AA_DI_MODEL",
        "dashscope": "AA_QS_MODEL",
    }.get(name, "AA_MODEL")


def auto_backend(work_r18):
    """按作品 R18 属性自动选后端: 返回 (backend_name, model_override)。
    优先级: [default].r18_backends / [default].normal_backends(TOML 配置, 从高到低)。
    逐项探测: 后端 key_env 存在才可用(没有 key 的后端跳过)。全部不可用回落配置 [default]。
    AA_BACKEND 显式设置时由调用方短路(本函数不读它)。"""
    chain = backend_chain(work_r18)
    if chain:
        return chain[0]
    d = load().get("default", {})
    return d.get("backend", "commandcode"), d.get("model", "")


def backend_chain(work_r18):
    """返回有序可用后端候选链 [(backend, model_override), ...]（供 call_api 自动降级）。
    与 auto_backend 同口径: 只含 key_env 存在 且(R18 作品) r18_safe 的后端，按配置顺序。

    `AA_CHAIN=名字1,名字2`（工作台模型多选）→ 只保留选中的端点（**保持 TOML 顺序**，
    链内降级语义不变）。选中的端点全都不可用（缺 key / R18 不安全）时不静默断链，
    回退为整条链并在 stderr 提示一次。
    """
    table_key = "r18_backends" if work_r18 else "normal_backends"
    table = load().get("default", {}).get(table_key, [])
    out = []
    for item in table:
        name = item.get("backend", "")
        cfg = load().get("backend", {}).get(name, {})
        if work_r18 and not r18_safe(name):
            continue
        key_env = cfg.get("key_env", "")
        if key_env and not _env_key_exists(key_env):
            continue
        out.append((name, item.get("model") or cfg.get("model", "")))
    sel = chain_names_override()
    if sel:
        picked = [x for x in out
                  if any(selector_match(s, x[0], entry_id(x[0], x[1])) for s in sel)]
        if picked:
            return picked
        import sys as _sys
        print("[aa_config] AA_CHAIN=%s 选中的端点都不可用 → 回退整条链" % ",".join(sel),
              file=_sys.stderr)
    return out


def chain_names_override():
    """`AA_CHAIN` 解析：返回选择项列表；未设/空 → None（用整条链）。

    2026-10-08 阶段 2 起，选择项可以是**链条目 id**（`backend|model`，界面按条目勾选）
    或**旧式后端名**（= 该后端全部条目，保持历史行为）。工作台翻译控制台/设置页写这个 env。
    """
    s = (os.environ.get("AA_CHAIN") or "").strip()
    if not s:
        return None
    return [x.strip() for x in s.split(",") if x.strip()]


def entry_id(name, model, item=None):
    """链条目的稳定 id（2026-10-08 阶段 2）：TOML 显式 `id=` 优先，否则 `<backend>|<model>`。

    分隔符用 `|` 而不是 `:`——模型名里会出现冒号（如 `meituan/longcat-2.0:free`）。
    同后端多条目（deepinfra 的 V4.1-Flash / 0731）靠它才能被分别勾选。
    """
    eid = (item or {}).get("id")
    if eid:
        return str(eid)
    return "%s|%s" % (name, model or "")


def selector_match(sel, name, eid):
    """`AA_CHAIN` / `AA_REFINE_BACKEND` 的单个选择项是否命中该链条目。

    兼容两种写法：条目 id（精确）或旧式后端名（= 该后端的全部条目，保持历史行为）。
    """
    s = (sel or "").strip()
    return s == eid or s == name


def _entry_meta(name, model, item=None, cfg=None):
    """链上单条端点的展示元数据（界面模型多选用；不含密钥）。"""
    cfg = cfg if cfg is not None else (load().get("backend", {}).get(name, {}) or {})
    mdl = model or cfg.get("model", "")
    rate, _key = pricing_for(name, mdl)          # 按**解析后**的模型名计价（传空串会查不到价）
    return {
        "backend": name,
        "model": mdl,
        "id": entry_id(name, mdl, item),
        "label": "%s / %s" % (name, mdl or "?"),
        "key_env": cfg.get("key_env", ""),
        "available": (not cfg.get("key_env")) or _env_key_exists(cfg.get("key_env", "")),
        "cache": bool(cfg.get("cache")),
        "r18": bool(r18_safe(name)),
        "fallback_only": bool((item or {}).get("fallback_only")),
        "rate": rate or {},
    }


def chain_catalog(work_r18):
    """链上**全部**端点（含 fallback_only 兜底、含缺 key 的）的展示元数据。

    与 backend_chain 的区别：不做可用性过滤（界面要把不可用的也列出来并标灰），
    顺序 = TOML 链序。`default` = 默认勾选集（可用且非 fallback_only 的那些）。
    """
    table_key = "r18_backends" if work_r18 else "normal_backends"
    table = load().get("default", {}).get(table_key, []) or []
    items = []
    for it in table:
        name = it.get("backend", "")
        items.append(_entry_meta(name, it.get("model") or "", item=it))
    # 默认勾选集 = 条目 **id**（2026-10-08 阶段 2：同后端多条目可分别勾选；界面按 id 回传）
    default = [x["id"] for x in items if x["available"] and not x["fallback_only"]]
    if not default:
        default = [x["id"] for x in items if x["available"]]
    return items, default


def chain_selection(env_name, entries, default_ids):
    """当前**生效的勾选集**（设置页 / 翻译·校对控制台 / 划词共用口径）。

    `env_name` = `AA_CHAIN` / `AA_REFINE_BACKEND` / `AA_SEL_CHAIN`；值为逗号分隔的
    **链条目 id**（`backend|model`）或旧式**后端名**（= 该后端全部条目，保持兼容）。
    未设/空/全部认不出 → `selected` = `default_ids`（= 整条链的默认勾选）。

    返回 `{"selected": [条目 id], "default": [...], "custom": bool, "raw": str}`；
    `custom` = env 里确有被认出的选择项（界面据此区分「用户自定义过」与「用默认」）。
    """
    raw = (os.environ.get(env_name) or "").strip()
    sels = [s.strip() for s in raw.split(",") if s.strip()]
    if not sels:
        return {"selected": list(default_ids), "default": list(default_ids),
                "custom": False, "raw": raw}
    valid = {it["id"] for it in entries}
    by_backend = {}
    for it in entries:
        by_backend.setdefault(it["backend"], []).append(it["id"])
    ids = []
    for s in sels:
        cand = by_backend.get(s) or ([s] if s in valid else [])
        for eid in cand:
            if eid not in ids:
                ids.append(eid)
    return {"selected": ids or list(default_ids), "default": list(default_ids),
            "custom": bool(ids), "raw": raw}


# 固定链（`follow_default = false`）里 deepinfra 路径若写 `glm` 别名 → 钉 GLM-5.3-Flash
# （refine_aa._chain_models 同口径；展示/计价都要按 GLM，不能按 [backend.deepinfra].model）
_REFINE_MODEL_FIXED = {"glm": "zai-org/GLM-5.3-Flash"}


# 划词默认链的内置兜底（TOML `[selection].backends` 缺失时用；与 selection.DEFAULT_SEL_ENTRIES 同口径）
_SEL_FALLBACK = (
    ("deepseek", "deepseek-flash"),
    ("deepinfra", "deepseek-ai/DeepSeek-V4-Flash-0731"),
    ("runinfra", "glm-5-3-flash"),
)


def _parse_entry_spec(s):
    """`后端|模型` / 裸后端名 → (backend, model_or_None)。"""
    s = str(s or "").strip()
    if not s:
        return None
    if "|" in s:
        b, m = s.split("|", 1)
        return (b.strip().lower() or None, m.strip() or None)
    return (s.lower(), None)


def selection_defaults():
    """划词链默认值 `[(backend, model)]`：TOML `[selection].backends` > 内置（三条）。"""
    raw = load().get("selection", {}).get("backends")
    if isinstance(raw, str):
        raw = [x for x in raw.split(",")]
    out = []
    for it in (raw or []):
        e = _parse_entry_spec(it if isinstance(it, str) else (it or {}).get("backend", ""))
        if e and e not in out:
            out.append(e)
    return tuple(out) or _SEL_FALLBACK


def selection_catalog():
    """划词链可选项 = 两条付费链条目 ∪ 划词默认链条目（按 id 去重）。

    返回 `(items, default_ids)`；items 与 `chain_catalog` 同结构（多 `id` 字段，供界面按条目勾选）。
    划词默认链里可能有**不在付费链上**的条目（如 runinfra/glm-5-3-flash），故要并进来。
    """
    items, seen = [], set()
    for work_r18 in (False, True):
        try:
            for it in chain_catalog(work_r18)[0]:
                if it["id"] not in seen:
                    seen.add(it["id"])
                    items.append(it)
        except Exception:
            continue
    default = []
    for b, m in selection_defaults():
        eid = entry_id(b, m or "")
        default.append(eid)
        if eid not in seen:
            seen.add(eid)
            items.append(_entry_meta(b, m or ""))
    return items, default


def refine_catalog(work_r18=False):
    """校对链（refine_aa）的展示元数据 + 默认勾选集。

    2026-09-22「与付费链一致」：默认**跟随付费链**（`[refine_aa].follow_default`，默认 true）
    → 直接返回 `chain_catalog(work_r18)`（端点、模型、价格都与主翻译完全相同）。
    `follow_default = false` 时退回 `[refine_aa].backends` 固定链（模型取 `[backend.x].model`）。
    """
    try:
        _ra = load().get("refine_aa", {}) or {}
    except Exception:
        _ra = {}
    fd = _ra.get("follow_default", None)
    if fd is None or bool(fd):
        return chain_catalog(work_r18)
    names = [x.strip() for x in str(_ra.get("backends") or "").split(",") if x.strip()] or ["deepseek"]
    items = []
    for n in names:
        b, mdl = (n.split("|", 1) + [""])[:2] if "|" in n else (n, _REFINE_MODEL_FIXED.get(n, ""))
        items.append(_entry_meta(b.strip(), mdl.strip()))
    return items, [x["backend"] for x in items if x["available"]] or names


def fallback_only_names(work_r18):
    """链上标 `fallback_only = true` 的端点名集合（2026-09-21「兜底端点」）。

    语义（用户口径）: **默认不使用，只在其他端点均失效时启用**——条目仍留在链尾按序降级，
    但不参与健康态端点轮询，也不作章内 pin 的目标；显式 `AA_BACKEND=<该端点>` 时照旧放链首。
    """
    table_key = "r18_backends" if work_r18 else "normal_backends"
    table = load().get("default", {}).get(table_key, []) or []
    return {it.get("backend", "") for it in table if it.get("fallback_only")}


def _env_key_exists(key_env):
    """该 key 是否有值（真实 env → 仓库根 .env；唯一文件来源）。"""
    if not key_env:
        return False
    try:
        return aa_env.has_key(key_env)
    except Exception:
        return False


def usage_file():
    """usage 落盘路径(相对仓库根解析为绝对路径)。"""
    p = get("usage", "file", "usage.jsonl")
    if os.path.isabs(p):
        return p
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), p)


def usage_tag():
    """默认 usage 环节标签。"""
    return get("usage", "tag", "translate")


def pricing():
    """成本单价表 [pricing]（2026-09-15 成本口径 P0）。

    返回 {"windows": ["09:00-12:00", ...], "rates": {"<backend>|<model>": {...}}}。
    rate 字段: hit(命中输入)/miss(未命中输入)/out(输出) 元/百万 token（缺失=未查证）,
    peak(高峰倍率, 默认 1.0), src(出处)。见 doc/方案归档/token成本优化方案_2026-09-15.md。
    """
    p = load().get("pricing", {}) or {}
    return {"windows": p.get("windows", []) or [], "rates": p.get("rates", {}) or {}}


def pricing_for(backend_name, model):
    """查单价 → (rate dict, key)。未查证返回 (None, None)；模型名含 free 视为免费 0 元。

    查表顺序: "<backend>|<model>" → "*|<model>" → "<backend>|*"。
    """
    rates = pricing()["rates"]
    for key in (f"{backend_name}|{model}", f"*|{model}", f"{backend_name}|*"):
        if key in rates and isinstance(rates[key], dict):
            r = rates[key]
            if any(k in r for k in ("hit", "miss", "out")):
                return r, key
    if model and "free" in str(model).lower():
        return {"hit": 0, "miss": 0, "out": 0, "src": "免费模型(名含 free)"}, "free"
    return None, None


def reload():
    """强制重读(测试/运行时切换用)。TOML + 仓库根 .env 都重新解析。"""
    global _cache, _config_missing
    _cache = None
    _config_missing = False
    try:
        aa_env.load(force=True)
    except Exception:
        pass
