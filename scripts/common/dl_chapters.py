# -*- coding: utf-8 -*-
"""批量下载章节页（5 并发，已存在且大于 min_size 跳过）。

章节清单来源（二选一）：
1. --json <file>：r401_index.py category 输出的章节 JSON
   `python common/r401_index.py category 12068 --json 不幸の催眠/work/chapters.json`
   `python common/dl_chapters.py --json 不幸の催眠/work/chapters.json`
2. --work <id>：works/registry.py 里该作品的 chapters 字段（现有作品留空）

文件命名按作品 name_style：entry_<eid>.html 或 <eid>_<标题>.html。
"""
import os, re, sys, json, time, urllib.request, urllib.parse, http.cookiejar, concurrent.futures

# 2026-08-30: Windows stdout 默认 GBK，print 日文路径/含 \ufffd 日志崩——强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(_SCRIPT_DIR), "works"))

import registry
import aa_config   # 代理统一口径（AA_PROXY > TOML [default].proxy > 默认；2026-10-08 阶段 4）

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
PROXY = aa_config.proxy_url()
ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n]')

# 2026-08-31 FC2 R18 年龄门：registry `dl_age_gate=true` 时，会话内首次 GET 若撞
# age_verify 页 → POST age_verification=1 确认 → 种 cookie（全站通用），后续带 cookie 直取正文。
# 每个下载进程一个 jar（5 并发共享）。
_cj = http.cookiejar.CookieJar()
_age_gate_done = False


def _opener():
    op = urllib.request.build_opener(
        urllib.request.ProxyHandler(aa_config.proxy_dict()),   # 抓日文站必走代理（地址可配）
        urllib.request.HTTPCookieProcessor(_cj))
    return op


def _confirm_age(url):
    """POST age_verification=1 确认年龄门（FC2 表单：age_verification + entry_number）。

    只种 cookie（age_check/blog_view 全站通用），entry_number 实际值不影响。
    每下载进程只需一次；失败静默（后续 fetch 撞门会重试确认）。
    """
    global _age_gate_done
    if _age_gate_done:
        return
    try:
        # entry_number 用 URL 里的 eid（FC2 校验，错值 404）；没有则兜底 1
        m = re.search(r"blog-entry-(\d+)", url) or re.search(r"[?&]p=(\d+)", url)
        entry_no = m.group(1) if m else "1"
        post = urllib.parse.urlencode({"age_verification": "1", "entry_number": entry_no}).encode()
        req = urllib.request.Request(url, data=post, headers={"User-Agent": UA,
                                    "Content-Type": "application/x-www-form-urlencoded"})
        with _opener().open(req, timeout=60) as r:
            r.read()
        _age_gate_done = True
        print(f"[age-gate] 已确认成年，cookie 种入 {[c.name for c in _cj]}", flush=True)
    except Exception as e:
        print(f"[age-gate] 确认失败: {e}", flush=True)


def fetch(url, timeout=120, age_gate=False):
    """直连优先，代理兜底（2026-08-19：Windows schannel 直连需 --ssl-no-revoke 等价物，
    urllib/OpenSSL 无此问题；代理 502 时直连可用）。

    age_gate=True（FC2 R18 站）：GET 撞 age_verify 页 → POST 确认种 cookie → 带 cookie 重取。
    """
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    # age_gate 站：直接用带 cookie jar 的 opener（直连无 cookie，撞门确认后重试也拿不到正文）
    if age_gate:
        for attempt in range(3):
            try:
                with _opener().open(req, timeout=timeout) as r:
                    data = r.read()
            except Exception:
                time.sleep(2)
                continue
            if b"age_verify" in data[:5000]:
                _confirm_age(url)
                continue
            return data
        return data
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
        except Exception:
            with _opener().open(req, timeout=timeout) as r:
                data = r.read()
        if b"age_verify" in data[:5000]:
            _confirm_age(url)
            continue  # 重试（cookie 已种）
        return data
    return data


def dl_one(item, cfg):
    eid = str(item["eid"])
    # 优先用清单条目自带 URL（r401 分类页 exlink 是权威源，可能跨站/域名不同）；
    # 缺失时 fallback registry url_pattern
    url = item.get("url") or cfg["url_pattern"].format(eid=eid)
    title = ILLEGAL.sub('_', str(item.get("title", "")).strip()).strip('_')
    if cfg["name_style"] == "eid_title":
        name = f"{eid}_{title}.html" if title else f"{eid}.html"
    else:
        name = f"entry_{eid}.html"
    path = os.path.join(cfg["raw_dir"], name)
    if os.path.exists(path) and os.path.getsize(path) > cfg.get("min_size", 50000):
        return f"skip {eid} ({os.path.getsize(path)}B)"
    for attempt in range(4):
        try:
            data = fetch(url, age_gate=cfg.get("dl_age_gate", False))
            with open(path, "wb") as f:
                f.write(data)
            return f"ok {eid} {len(data)//1024}KB -> {name}"
        except Exception as e:
            time.sleep(3 * (attempt + 1))
    return f"FAIL {eid} {url}"


def main():
    args = sys.argv[1:]
    json_file = None
    work = None
    if "--json" in args:
        json_file = args[args.index("--json") + 1]
    for i, a in enumerate(args):
        if a.startswith("--work"):
            work = (a.split("=", 1)[1] if "=" in a else args[i + 1]) if i + 1 < len(args) or "=" in a else None
            if not work:
                raise SystemExit("--work 需要值：--work <id> 或 --work=<id>")
    if json_file:
        data = json.load(open(json_file, encoding="utf-8"))
        entries = data.get("entries") or data
        # 从条目 URL 反推站点配置（无 --work 时）
        if not work:
            m = re.search(r'https?://([^/]+)/', entries[0]["url"])
            domain = m.group(1) if m else ""
            for wid, wc in registry.WORKS.items():
                if domain in wc["url_pattern"]:
                    work = wid
                    break
        cfg = registry.get(work) if work else {
            "raw_dir": os.path.dirname(os.path.abspath(json_file)),
            "url_pattern": "{url}", "name_style": "entry_eid",
        }
        if work:
            # 用条目自带 URL（跨站兼容）
            items = [dict(e, url=e.get("url", cfg["url_pattern"].format(eid=e["eid"]))) for e in entries]
        else:
            items = entries
    else:
        cfg = registry.get(work)
        items = [{"eid": str(e[0]), "title": e[1]} for e in cfg.get("chapters", [])]
        if not items:
            raise SystemExit("registry 无 chapters；请先 `python common/r401_index.py category <id> --json xxx.json` 生成章节清单")
    os.makedirs(cfg["raw_dir"], exist_ok=True)
    print(f"download {len(items)} chapters -> {cfg['raw_dir']}", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        for msg in ex.map(lambda it: dl_one(it, cfg), items):
            print(msg, flush=True)


if __name__ == "__main__":
    main()
