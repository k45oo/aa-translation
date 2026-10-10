# -*- coding: utf-8 -*-
"""工作台「设置」页的纯逻辑层（方案 doc/方案归档/工作台设置页与配置文件方案_2026-10-08.md 阶段 6）。

四个区块：**密钥 / 模型（翻译·校对·划词）/ 划词开关 / 本地代理**。
* 读写目标只有一个：`<仓库根>\\.env`（唯一用户可改层，`aa_env.write_values` 行级替换 + 原子写，
  保留注释与未改动行）；优先级 `真实 env > .env > model_config.toml > 代码默认`，TOML 只读。
* **保存即生效**：写盘后 `aa_env.load_dotenv(force=True)` + `aa_config.reload()` → 本进程立即吃到，
  随后新启动的任务子进程（task_manager spawn 继承 env）也用新值；**已在跑的任务不受影响**。
* 密钥只回掩码（不回显明文），保存只提交**改动过的**字段（空输入 = 不改）。

本模块不 import fastapi，可单测。
"""
import os
import re
import socket
import sys
import urllib.request

# scripts/ 与 scripts/common 上 sys.path（与 selection.py 同款）——aa_env/aa_config/aa_backend 都在那儿
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_DIR)
_SCRIPTS = os.path.join(_ROOT, "scripts")
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "works")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import aa_env

_ENV_KEYS = ("AA_CHAIN", "AA_REFINE_BACKEND", "AA_SEL_CHAIN", "AA_SEL_ENABLE",
             "AA_PROXY", "AA_USE_PROXY", "AA_BASE", "AA_PORT")


# ---------------------------------------------------------------- 基础视图

def _ac():
    import aa_config
    return aa_config


def env_info():
    """配置文件位置与最近保存时间（页顶常驻显示）。"""
    p = aa_env.env_path()
    try:
        mt = os.path.getmtime(p)
    except OSError:
        mt = None
    return {"path": p, "exists": os.path.exists(p), "saved_at": mt}


def _key_source(name):
    """该 key 当前值来自哪一层：env（真实环境变量）/ .env（仓库根）/ 空。

    注意：aa_config import 时已把 .env 灌进 os.environ，所以「在 os.environ 里」不足以判定
    来源 —— 先比 .env 文件里的值：不同 → 真实 env 赢；相同/文件里有 → .env。
    （2026-10-10 起 hermes 兜底路径已废除，不再有第三层。）
    """
    try:
        file_val = (aa_env.load() or {}).get(name)
    except Exception:
        file_val = None
    env_val = os.environ.get(name)
    if env_val and env_val != file_val:
        return "env"
    if file_val:
        return ".env"
    if env_val:
        return "env"
    return ""


def _key_names_ordered():
    """key 清单（自动读 TOML）：付费链/划词链上用到的排最前，其余按名字排序。

    `[backend.*].key_env` ∪ `[vision].<group>.key_env`，去重；返回 `[(name, purpose)]`。
    """
    AC = _ac()
    try:
        cfg = AC.load()
    except Exception:
        return []
    labels = {}          # key_env -> [用途标签]

    def add(key_env, label):
        if not key_env:
            return
        labels.setdefault(key_env, [])
        if label and label not in labels[key_env]:
            labels[key_env].append(label)

    # ① 付费链 + 划词链用到的端点（最常用，排最前）
    used = []
    try:
        for work_r18 in (False, True):
            for it in AC.chain_catalog(work_r18)[0]:
                ke = (cfg.get("backend", {}).get(it["backend"], {}) or {}).get("key_env", "")
                if not ke:
                    continue
                tag = "付费链" if it["id"] in AC.chain_catalog(work_r18)[1] else "付费链(非默认)"
                if it.get("fallback_only"):
                    tag = "付费链(兜底)"
                add(ke, "%s %s" % (tag, it["label"]))
                if ke not in used:
                    used.append(ke)
        for b, m in AC.selection_defaults():
            ke = (cfg.get("backend", {}).get(b, {}) or {}).get("key_env", "")
            add(ke, "划词链 %s/%s" % (b, m or "?"))
            if ke and ke not in used:
                used.append(ke)
    except Exception:
        pass

    # ② 其余后端（可按需填）
    for name, c in sorted((cfg.get("backend", {}) or {}).items()):
        ke = (c or {}).get("key_env", "")
        if ke and ke not in used:
            add(ke, "后端 %s" % name)
            used.append(ke)
    # ③ 视觉模型用的 key（SiliconFlow 等）
    for grp, items in (cfg.get("vision", {}) or {}).items():
        if not isinstance(items, list):
            continue
        for it in items:
            ke = (it or {}).get("key_env", "")
            if ke:
                add(ke, "视觉 %s" % (it.get("name") or grp))
                if ke not in used:
                    used.append(ke)
    # ④ opencode zen 的两个 key（免费池 + zen 主 key）
    #    2026-10-10 用户要求：`OPENCODE_ZEN_API_KEY` **恒列出**（哪怕还没填，也要能在设置页填/测）；
    #    `OPENCODE_FREE_ZEN_API_KEY` 维持原样（已配置才列）。
    for ke, tag in (("OPENCODE_FREE_ZEN_API_KEY", "免费模型池"),):
        if aa_env.read_key(ke) and ke not in used:
            add(ke, tag)
            used.append(ke)
    for ke, tag in (("OPENCODE_ZEN_API_KEY", "opencode zen"),):
        if ke not in used:
            add(ke, tag)
            used.append(ke)

    return [(k, "、".join(labels[k][:3]) or "—") for k in sorted(
        labels, key=lambda x: (used.index(x) if x in used else 999, x))]


def keys_view():
    """密钥区块：`[{name, purpose, configured, masked, source}]`（**不含明文**）。"""
    out = []
    for name, purpose in _key_names_ordered():
        v = aa_env.read_key(name)
        out.append({"name": name, "purpose": purpose, "configured": bool(v),
                    "masked": aa_env.mask(v), "source": _key_source(name)})
    return out


def _chain_sel(env_name, entries, default_ids):
    """env 里当前生效的勾选集（口径见 `aa_config.chain_selection`，两处共用一份实现）。"""
    return _ac().chain_selection(env_name, entries, default_ids)


def models_view():
    """模型区块：翻译链 / 校对链 / 划词链 的候选 + 当前勾选。"""
    AC = _ac()
    tr_items, tr_default = AC.chain_catalog(False)
    rf_items, rf_default = AC.refine_catalog(False)
    sel_items, sel_default = AC.selection_catalog()
    return {
        "translate": {"entries": tr_items, **_chain_sel("AA_CHAIN", tr_items, tr_default)},
        "proofread": {"entries": rf_items, **_chain_sel("AA_REFINE_BACKEND", rf_items, rf_default)},
        "selection": {"entries": sel_items, **_chain_sel("AA_SEL_CHAIN", sel_items, sel_default)},
    }


def proxy_view():
    """代理区块：地址 + 策略 + 各自来源（便于排查"界面显示 A、实际用 B"）。"""
    AC = _ac()
    url = AC.proxy_url()
    tom = (AC.load().get("default", {}) or {})
    src = "env" if os.environ.get("AA_PROXY") else (
        ".env" if aa_env.load().get("AA_PROXY") else (
            "TOML" if tom.get("proxy") else "默认"))
    mode = AC.use_proxy_mode()
    msrc = "env" if os.environ.get("AA_USE_PROXY") else (
        ".env" if aa_env.load().get("AA_USE_PROXY") else (
            "TOML" if tom.get("use_proxy") else "默认"))
    return {"url": url, "url_source": src, "mode": mode, "mode_source": msrc,
            "toml_url": tom.get("proxy", ""), "toml_mode": tom.get("use_proxy", "auto")}


def sel_enabled():
    try:
        from aa_web_app import selection as SEL
        return bool(SEL.sel_enabled())
    except Exception:
        return True


def state():
    """设置页要的全部数据（一次取齐，密钥只回掩码）。"""
    info = env_info()
    return {"ok": True, "env": info, "keys": keys_view(), "models": models_view(),
            "proxy": proxy_view(), "sel_enabled": sel_enabled()}


# ---------------------------------------------------------------- 保存

def _commit(updates, deletes=()):
    """写 .env（行级替换 + 原子写）→ 立即生效（本进程 + 之后 spawn 的子进程）。"""
    changed = aa_env.write_values(updates or {}, deletes or ())
    try:
        aa_env.load_dotenv(force=True)
    except Exception:
        pass
    try:
        _ac().reload()
    except Exception:
        pass
    return changed


def _sel_value(ids, entries, default_ids):
    """勾选集 → env 值（None = 不写/删掉 = 默认或整条链）。

    语义（与工作台模型多选/`_chain_env` 一致）：**空 = 整条链**；**等于默认集 = 不写**
    （否则会把 `fallback_only` 兜底端点摘掉）。
    """
    ids = [i for i in (ids or []) if i]
    if not ids or set(ids) == set(default_ids):
        return None
    valid = {it["id"] for it in entries}
    keep = [i for i in ids if i in valid]
    return ",".join(keep) if keep else None


def save_keys(set_map=None, clear=()):
    """密钥区块：只写改动项；`clear` 里的显式删除。返回 {"changed": [键名]}。"""
    return {"changed": _commit(set_map or {}, [c for c in (clear or ()) if c])}


def save_models(translate=None, proofread=None, selection=None):
    """模型区块：三链各自独立；None = 不改该链。"""
    AC = _ac()
    upd, dels, changed = {}, [], []
    if translate is not None:
        items, dflt = AC.chain_catalog(False)
        v = _sel_value(translate, items, dflt)
        (upd.update({"AA_CHAIN": v}) if v else dels.append("AA_CHAIN"))
        changed.append("AA_CHAIN")
    if proofread is not None:
        items, dflt = AC.refine_catalog(False)
        v = _sel_value(proofread, items, dflt)
        (upd.update({"AA_REFINE_BACKEND": v}) if v else dels.append("AA_REFINE_BACKEND"))
        changed.append("AA_REFINE_BACKEND")
    if selection is not None:
        items, dflt = AC.selection_catalog()
        v = _sel_value(selection, items, dflt)
        (upd.update({"AA_SEL_CHAIN": v}) if v else dels.append("AA_SEL_CHAIN"))
        changed.append("AA_SEL_CHAIN")
    done = _commit(upd, dels)
    return {"changed": done or changed}


def save_selection(enabled):
    """划词开关（AA_SEL_ENABLE）：开=删掉 env（回默认 1），关=写 0。"""
    if enabled:
        done = _commit({}, ["AA_SEL_ENABLE"])
    else:
        done = _commit({"AA_SEL_ENABLE": "0"}, [])
    return {"changed": done or ["AA_SEL_ENABLE"], "sel_enabled": sel_enabled()}


def save_proxy(url="", mode="", clear_url=False):
    """代理区块：空 url/mode = 不改；`clear_url` = 删掉 AA_PROXY（回 TOML/默认）。"""
    upd, dels = {}, []
    if clear_url:
        dels.append("AA_PROXY")
    elif str(url or "").strip():
        u = str(url).strip().rstrip("/")
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", u):
            raise ValueError("代理地址要带协议头，如 http://127.0.0.1:10808")
        upd["AA_PROXY"] = u
    if str(mode or "").strip():
        m = str(mode).strip().lower()
        if m not in ("auto", "0", "1"):
            raise ValueError("代理策略只能是 auto / 0 / 1")
        if m == "auto":
            dels.append("AA_USE_PROXY")     # auto = 默认 → 不写 env
        else:
            upd["AA_USE_PROXY"] = m
    return {"changed": _commit(upd, dels)}


# ---------------------------------------------------------------- 自检

def _classify_err(e):
    s = str(e)
    low = s.lower()
    if "401" in s or "invalid_api_key" in low or "unauthorized" in low or "authentication" in low:
        return "key 无效或无权限（401）"
    if "402" in s or "insufficient" in low or "balance" in low or "quota" in low or "欠费" in s:
        return "余额不足/欠费（402）"
    if "403" in s or "forbidden" in low:
        return "被拒绝（403；可能是地区/内容策略）"
    if "429" in s or "rate limit" in low:
        return "限流（429），key 本身可用"
    if "timed out" in low or "timeout" in low or "超时" in s:
        return "连接超时（端点不通 → 检查代理）"
    if "getaddrinfo" in low or "name or service not known" in low or "ssl" in low:
        return "网络/SSL 失败（多为没走代理）"
    return s[:200]


def _test_targets(key_env):
    """用该 key 的后端候选 `[(backend, model)]`（付费链优先，其次任意带该 key 的后端）。"""
    AC = _ac()
    out = []
    try:
        chain_ids = [it["id"] for it in AC.chain_catalog(False)[0]]
        for it in AC.chain_catalog(False)[0]:
            cfg = (AC.load().get("backend", {}).get(it["backend"], {}) or {})
            if cfg.get("key_env") == key_env and it["id"] in chain_ids:
                out.append((it["backend"], it["model"]))
        for b, m in AC.selection_defaults():
            cfg = (AC.load().get("backend", {}).get(b, {}) or {})
            if cfg.get("key_env") == key_env:
                out.append((b, m))
        for name, cfg in sorted((AC.load().get("backend", {}) or {}).items()):
            if (cfg or {}).get("key_env") == key_env:
                out.append((name, cfg.get("model") or ""))
    except Exception:
        pass
    seen, uniq = set(), []
    for t in out:
        if t[0] not in seen:
            seen.add(t[0])
            uniq.append(t)
    return uniq


def test_key(name):
    """「测试连接」：用该 key 发一次最小请求（成本≈0），回可用/失败原因。"""
    if not name:
        return {"ok": False, "error": "未指定 key 名"}
    val = aa_env.read_key(name)
    if not val:
        return {"ok": False, "error": "未配置（先填一个值再测）", "kind": "empty"}
    if name in _ZEN_KEYS:                      # opencode zen 系：TOML 里没有端点，直连 zen 探针
        return _test_zen_key(name, val)
    targets = _test_targets(name)
    if not targets:
        return {"ok": False, "error": "该 key 未绑定任何可测后端（TOML 里没有 key_env=%s 的端点）" % name,
                "kind": "no_backend"}
    import aa_backend as AB
    last = None
    for b, mdl in targets[:3]:
        try:
            txt = AB._call_named(b, "你是测试探针。", "只回复 OK 两个字母。",
                                 model=mdl or None, max_tokens=8, timeout=30)
            return {"ok": True, "backend": b, "model": mdl, "reply": (txt or "").strip()[:40]}
        except Exception as e:
            last = (b, e)
    return {"ok": False, "backend": last[0] if last else "",
            "error": _classify_err(last[1]) if last else "无候选",
            "kind": "call_failed"}


# opencode zen 系 key（TOML 里没有 key_env 指向它们的端点，单独走 zen 探针）
_ZEN_KEYS = {"OPENCODE_ZEN_API_KEY", "OPENCODE_FREE_ZEN_API_KEY", "OPENCODE_GO_API_KEY"}


def _test_zen_key(name, val):
    """用给定 key 打一次「它现实里真能用的门」。

    2026-10-10 实测：opencode **免费档已对非 OpenCode 客户端关闭**（zen 门对 9 个免费模型一律
    403 `FreeTierError: …free tier can only be used from within OpenCode`；老的 `mimo-v2.5-free`
    410 `ModelDeprecated`）→ 拿免费模型当探针会把一把好 key 测成红的。
    现在这把 key 现实用途 = **Go 门订阅**（`/zen/go/v1` + `x-opencode-session`）→ 先探 Go 门
    （`deepseek-v4.1-flash`）；Go 门不认（按量 Zen key）再退回 Zen 门同模型。

    走的都是 `call_opencode_*`，并**显式传 key**——测的就是「这个变量里的值」，不走优先级回退。
    """
    import aa_backend as AB
    errs = []
    for gate, fn in (("Go 门", lambda: AB.call_opencode_go(
                        "你是测试探针。", "只回复 OK 两个字母。",
                        max_tokens=8, timeout=30, model="deepseek-v4.1-flash", key=val)),
                     ("Zen 门", lambda: AB.call_opencode_zen(
                        "你是测试探针。", "只回复 OK 两个字母。",
                        max_tokens=8, timeout=30, model="deepseek-v4.1-flash", key=val))):
        try:
            txt = fn()
            return {"ok": True, "backend": "opencode_go" if gate == "Go 门" else "opencode_zen",
                    "model": "deepseek-v4.1-flash", "gate": gate, "reply": (txt or "").strip()[:40]}
        except Exception as e:
            errs.append("%s: %s" % (gate, _classify_err(e)))
    return {"ok": False, "backend": "opencode_go", "model": "deepseek-v4.1-flash",
            "error": " / ".join(errs), "kind": "call_failed"}


def _probe_http(url, proxy=None, timeout=8):
    """对端点发一个无害的请求：能拿到 HTTP 响应（哪怕是 401/404）就算"可达"。"""
    try:
        handler = urllib.request.ProxyHandler(
            {"http": proxy, "https": proxy} if proxy else {})
        op = urllib.request.build_opener(handler)
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "AA-workbench-probe"})
        with op.open(req, timeout=timeout) as r:
            return True, "HTTP %s" % r.status
    except urllib.error.HTTPError as e:
        return True, "HTTP %s" % e.code          # 401/404 也是"通了"
    except Exception as e:
        return False, _classify_err(e)


def test_proxy():
    """「测试代理」：① 代理端口能否连上；② 不走代理能否到付费端点。"""
    AC = _ac()
    url = AC.proxy_url()
    m = re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://([^/:]+):(\d+)", url)
    host, port = (m.group(1), int(m.group(2))) if m else ("", 0)
    res = {"url": url, "mode": AC.use_proxy_mode(), "host": host, "port": port}
    # ① 代理端口
    try:
        with socket.create_connection((host, port), timeout=2):
            res["local"] = True
            res["local_msg"] = "代理端口可连（%s:%s）" % (host, port)
    except Exception as e:
        res["local"] = False
        res["local_msg"] = "代理端口连不上（%s:%s）：%s —— v2rayN 没开？" % (host, port, e)
    # ② 直连能否到付费端点 / 经代理能否到
    try:
        ep = (AC.backend("deepseek") or {}).get("url") or "https://api.deepseek.com"
    except Exception:
        ep = "https://api.deepseek.com"
    origin = re.match(r"^(https?://[^/]+)", ep)
    target = (origin.group(1) if origin else ep) + "/"
    res["direct_ok"], res["direct_msg"] = _probe_http(target, None)
    if res["local"]:
        res["proxy_ok"], res["proxy_msg"] = _probe_http(target, url)
    else:
        res["proxy_ok"], res["proxy_msg"] = None, "跳过（代理端口不通）"
    return res
