# -*- coding: utf-8 -*-
"""通用下载 runner（2026-08-21）：拉 r401 清单 → 下载 → 提取 → 固化原站顺序。零 token。

用法：
    python scripts/runner/dl_runner.py --work <id> [--chapters <eids|1-20|all|new>] [--json <file>] [--skip-order]

--chapters 默认 all（下载幂等）：
    all        = 清单全部章节
    new        = raw/ 和 aa/ 都缺的章节（本地缺失才算新）
    eids       = 逗号分隔 eid 列表（如 74899,65495）
    1-20       = 按原站顺序取前 20 话

--json <file> : 显式章节清单（r401 未收录的站用；格式同 r401_index category --all --json 输出）
--skip-order  : 不生成/更新 chapter_order.json

依赖：common/r401_index.py（--all 分页）、common/dl_chapters.py、common/extract_all.py；
      touhou 类（registry dl_type=api_inject）用 works/dl_touhou.py 特殊下载。
"""
import json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRIPTS = os.path.join(ROOT, "scripts")
_COMMON = os.path.join(_SCRIPTS, "common")
_WORKS = os.path.join(_SCRIPTS, "works")
sys.path.insert(0, _COMMON)
sys.path.insert(0, _WORKS)

import registry
from aa_spawn import py_prefix              # 起子任务统一前缀（冻结 exe 走 --cli）
import task_report as TR   # 2026-08-29 任务管理器: 心跳/停止(common 已 sys.path, 直跑 no-op)


def log(msg):
    print(msg, flush=True)


def _hb(phase, idx=None, total=None):
    TR.heartbeat(phase=phase, idx=idx, total=total, force=True)


def _stop_check():
    if TR.check_stop():
        log("[task] 收到停止请求, 在阶段边界安全退出")
        raise SystemExit(TR.RC_STOPPED)


def parse_chapters_spec(spec, all_eids):
    """把 --chapters 解析成 eid 列表。
    all_eids: 清单全部 eid（按原站顺序）。"""
    if spec in (None, "", "all"):
        return list(all_eids)
    if spec == "new":
        return None  # 特殊值，由调用方按"缺"判定
    if re.fullmatch(r"\d+-\d+", spec):
        a, b = map(int, spec.split("-"))
        if b < a:
            a, b = b, a
        return list(all_eids[a - 1:b])  # 1-based 前闭后开
    return [e.strip() for e in spec.split(",") if e.strip()]


def _normalize_eid(e):
    s = str(e)
    m = re.search(r"(\d+)", s)
    return m.group(1) if m else s


def _local_eids(cfg):
    """本地已有的 eid 集合：raw/ 文件名 + aa/ 文件名里的数字。"""
    out = set()
    for d in (cfg.get("raw_dir"), cfg.get("aa_dir")):
        if not d or not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            m = re.search(r"(\d+)", fn)
            if m:
                out.add(m.group(1))
    return out


def load_entries(cfg, json_file=None):
    """加载章节清单（entries 列表）。返回 (title, entries)。
    优先级：--json > cache 系作品实时拉 yaruobook 分类页刷新（site_url.json，2026-08-31：
    旧逻辑只读 work/chapters.json 缓存 → 新章永远发现不了，如ドスケベナイト 29990 下载失败）
    > work/chapters.json 缓存 > r401 分类页实时拉取。"""
    if json_file:
        data = json.load(open(json_file, encoding="utf-8"))
        entries = data.get("entries") or data
        return data.get("title", ""), entries
    # yaruobook 系（chapters_source=cache 或 site=yaruobook）：先实时拉 yaruobook 分类页刷新
    # （site_url.json，2026-08-31：旧逻辑只读 work/chapters.json 缓存 → 新章永远发现不了，
    # 如ドスケベナイト 29990 下载失败；sujin_* 等 site=yaruobook 但 chapters_source 未配
    # 也走 yaruobook 分类页，否则 r401_cid 是历史错配会拉错作品），失败才回退缓存。
    if cfg.get("chapters_source") == "cache" or cfg.get("site") == "yaruobook":
        cache = os.path.join(cfg.get("work_dir", ""), "chapters.json") if cfg.get("work_dir") else None
        try:
            fresh = _fetch_yaruobook_entries(cfg)
            if fresh:
                log(f"实时拉取 yaruobook 分类页: {len(fresh)} 条（含新章）")
                return "", fresh
        except Exception as e:
            log(f"实时拉取失败，回退缓存: {e}")
        if cache and os.path.exists(cache):
            data = json.load(open(cache, encoding="utf-8"))
            entries = data.get("entries") or data
            log(f"用缓存章节清单: {cache} ({len(entries)} 条)")
            return data.get("title", ""), entries
    cid = cfg.get("r401_cid")
    if not cid:
        raise SystemExit(f"作品 {cfg.get('title')} 无 r401_cid 且未给 --json/无缓存；无法拉清单")
    import r401_index as R
    entries = R.fetch_all_pages(cid)
    entries = list(reversed(entries))  # 分类页倒序 → 反转成原站顺序
    return "", entries


def _fetch_yaruobook_entries(cfg):
    """实时拉 yaruobook 作品分类页清单（从 work/site_url.json 或 chapters.json 首条 URL 解析）。"""
    work_dir = cfg.get("work_dir", "")
    site_url = ""
    sp = os.path.join(work_dir, "site_url.json") if work_dir else ""
    if sp and os.path.exists(sp):
        try:
            site_url = json.load(open(sp, encoding="utf-8")).get("url") or ""
        except Exception:
            site_url = ""
    if not site_url:
        # 从 chapters.json 首条 URL 现场解析分类
        cp = os.path.join(work_dir, "chapters.json") if work_dir else ""
        url = ""
        if os.path.exists(cp):
            try:
                ent = json.load(open(cp, encoding="utf-8")).get("entries", [])
                if ent:
                    url = (ent[0].get("url") or "").strip()
            except Exception:
                url = ""
        if not url:
            return None
        import re as _re
        # 复用 add_work 的面包屑解析（内联轻量版）
        site_url = _resolve_site_url(url)
    m = re.search(r"https://([^/]+)/\?cat=(\d+)", site_url)
    if not m:
        return None
    host, cat = m.group(1), m.group(2)
    site_key = {"com": "com", "jp": "jp", "net": "net"}.get(_host_key(host), "com")
    import yaruobook_index as Y
    Y.BASE = Y.SITES.get(site_key, Y.BASE)
    entries, _d = Y.fetch_all_pages(cat, log=log)
    return list(reversed(entries))


def _host_key(host):
    for k, v in {"com": "com", "jp": "jp", "net": "net"}.items():
        if host.endswith(k):
            return k
    return "com"


def _resolve_site_url(article_url):
    """从 yaruobook 章节页面包屑解析作品分类 URL（走代理）。失败返回空。"""
    import urllib.request
    try:
        import aa_config
        op = urllib.request.build_opener(
            urllib.request.ProxyHandler(aa_config.proxy_dict()))   # 地址可配（2026-10-08 阶段 4）
        req = urllib.request.Request(article_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"})
        with op.open(req, timeout=8) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception:
        return ""
    items = re.findall(
        r'itemListElement[^>]*>.*?<a href="[^"]*[?&]cat=(\d+)[^"]*"[^>]*>\s*<span[^>]*>(?:<span[^>]*>)?([^<]{1,60}?)(?:</span>)*</a>',
        html, re.S)
    if items:
        host = re.sub(r"https?://([^/]+)/.*", r"\1", article_url)
        return f"https://{host}/?cat={items[-1][0]}"
    m2 = re.search(r"category-(\d+)-post", html)
    if m2:
        host = re.sub(r"https?://([^/]+)/.*", r"\1", article_url)
        return f"https://{host}/?cat={m2.group(1)}"
    return ""


def ensure_chapters_json(cfg, entries):
    """缓存章节清单到 work_dir/chapters.json（供 translate_runner 复用/离线兜底）。"""
    work_dir = cfg.get("work_dir")
    if not work_dir:
        return None
    os.makedirs(work_dir, exist_ok=True)
    path = os.path.join(work_dir, "chapters.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"entries": entries}, f, ensure_ascii=False, indent=1)
    log(f"章节清单已缓存: {path} ({len(entries)} 条)")
    return path


def ensure_order(cfg, entries):
    """生成 chapter_order.json（原站顺序）——只在缺失时生成，不覆盖手工调整过的。"""
    work_dir = cfg.get("work_dir")
    if not work_dir:
        return
    order_path = os.path.join(work_dir, "chapter_order.json")
    if os.path.exists(order_path):
        log(f"chapter_order.json 已存在，跳过：{order_path}")
        return
    os.makedirs(work_dir, exist_ok=True)
    with open(order_path, "w", encoding="utf-8") as f:
        json.dump({"order": [e["eid"] for e in entries]}, f, ensure_ascii=False, indent=1)
    log(f"chapter_order.json 已生成: {order_path} ({len(entries)} 话)")


def run(cmd, cwd=None):
    import subprocess
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.stdout:
        for line in r.stdout.strip().splitlines():
            log("  " + line)
    if r.stderr:
        for line in r.stderr.strip().splitlines()[-5:]:
            log("  ERR " + line)
    if r.returncode != 0:
        raise SystemExit(f"命令失败 [{r.returncode}]: {' '.join(cmd)}")
    return r


def main():
    args = sys.argv[1:]
    work, json_file = None, None
    chapters = "all"
    skip_order = "--skip-order" in args
    if "--work" in args:
        i = args.index("--work")
        work = args[i + 1] if i + 1 < len(args) else None
    if "--chapters" in args:
        i = args.index("--chapters")
        chapters = args[i + 1] if i + 1 < len(args) else "all"
    if "--json" in args:
        i = args.index("--json")
        json_file = args[i + 1] if i + 1 < len(args) else None
    if not work:
        raise SystemExit("usage: dl_runner.py --work <id> [--chapters all|new|eids|1-20] [--json file]")

    cfg = registry.get(work)
    import _log
    # --log none = 禁用文件日志（translate_runner 嵌套调用时用，避免覆盖外层日志）
    _log.init(cfg.get("work_dir", os.path.join(ROOT, "temp")),
              None if "--log" not in args else args[args.index("--log") + 1])
    global log  # rebind 模块级 log：print + 写日志文件
    log = _log.make_log()
    log(f"=== dl_runner [{work}] {cfg.get('title')} ===")
    log("=== dl_runner 日志初始化完成 ===")

    # 1. 清单
    title, entries = load_entries(cfg, json_file)
    all_eids = [_normalize_eid(e["eid"]) for e in entries]
    log(f"清单: {len(entries)} 话 (来源={'--json' if json_file else 'r401'})")
    _hb("清单", 0, len(entries))

    if chapters == "new":
        local = _local_eids(cfg)
        target = [e for e in entries if _normalize_eid(e["eid"]) not in local]
        log(f"new: 本地已有 {len(local)} eid，待处理 {len(target)} 话")
        sel = target
    else:
        sel_eids = parse_chapters_spec(chapters, all_eids)
        sel = [e for e in entries if _normalize_eid(e["eid"]) in set(sel_eids)]
        log(f"chapters={chapters}: 处理 {len(sel)} 话")
    if not sel:
        log("无待处理章节（全部已有/清单为空）")
        return 0

    # 缓存放 work_dir（写全部清单，供 translate_runner 顺序基准复用）
    ensure_chapters_json(cfg, entries)

    _stop_check()
    _hb("下载", 0, len(sel))
    # 2. 下载
    dl_type = cfg.get("dl_type", "standard")
    if dl_type == "api_inject":
        log("=== 下载 (api_inject: touhou 式 API 注入) ===")
        # 复用 works/dl_touhou.py 核心逻辑：构造临时 json 喂给它
        import importlib.util
        spec_path = os.path.join(_WORKS, "dl_touhou.py")
        spec = importlib.util.spec_from_file_location("dl_touhou_impl", spec_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        tmp_json = os.path.join(cfg["work_dir"], "dl_tmp_selected.json")
        os.makedirs(cfg["work_dir"], exist_ok=True)
        with open(tmp_json, "w", encoding="utf-8") as f:
            json.dump({"entries": sel}, f, ensure_ascii=False)
        import subprocess
        r = subprocess.run(py_prefix(["-u"]) + [spec_path, "--work", work, "--json", tmp_json],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        for line in (r.stdout or "").strip().splitlines():
            log("  " + line)
        if r.returncode != 0:
            raise SystemExit(f"dl_touhou FAILED rc={r.returncode}")
        os.remove(tmp_json)
    else:
        log("=== 下载 (standard: common/dl_chapters.py) ===")
        tmp_json = os.path.join(cfg["work_dir"], "dl_tmp_selected.json")
        os.makedirs(cfg["work_dir"], exist_ok=True)
        with open(tmp_json, "w", encoding="utf-8") as f:
            json.dump({"entries": sel}, f, ensure_ascii=False)
        run(py_prefix(["-u"]) + [os.path.join(_COMMON, "dl_chapters.py"), "--work", work, "--json", tmp_json],
            cwd=_SCRIPTS)
        os.remove(tmp_json)

    _stop_check()
    _hb("提取", 0, len(sel))
    # 3. 提取
    log("=== 提取 (common/extract_all.py) ===")
    eids_arg = ",".join(_normalize_eid(e["eid"]) for e in sel)
    run(py_prefix(["-u"]) + [os.path.join(_COMMON, "extract_all.py"),
         "--work", work, "--eids", eids_arg], cwd=_SCRIPTS)

    _stop_check()
    _hb("固化")
    # 4. 顺序固化
    if not skip_order:
        ensure_order(cfg, entries)  # entries 用全部（原站顺序），不用 sel

    log("=== dl_runner DONE ===")
    return 0


if __name__ == "__main__":
    _rc = 1
    try:
        _rc = main() or 0
    except SystemExit as _e:
        _rc = _e.code if isinstance(_e.code, int) else 1
        raise
    finally:
        try:
            TR.finalize(_rc)
        except Exception:
            pass
