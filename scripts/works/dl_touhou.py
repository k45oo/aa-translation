# -*- coding: utf-8 -*-
"""touhouyaruosure 批量下载：章节页 + /api/chapters/<eid>/content 正文注入。

- 正文不在章节页 HTML 里，需二次请求 API；把 API 返回注入
  `<div id="c-chapter-content">` 的 skeleton 位，raw 存单文件供 extract_all 使用。
- 用法：python scripts/works/dl_touhou.py [--json temp/touhou_chapters.json]
"""
import json, os, re, sys, time, urllib.request, concurrent.futures

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
sys.path.insert(0, os.path.dirname(_SCRIPT_DIR))
sys.path.insert(0, os.path.join(os.path.dirname(_SCRIPT_DIR), "common"))

import registry
import aa_config   # 代理统一口径（2026-10-08 阶段 4）

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
PROXY = aa_config.proxy_url()


def fetch(url, timeout=120):
    op = urllib.request.build_opener(urllib.request.ProxyHandler(aa_config.proxy_dict()))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with op.open(req, timeout=timeout) as r:
        return r.read()


def inject_content(page: bytes, content: bytes) -> bytes:
    """把 API 正文注入章节页 c-chapter-content div（替换 skeleton）"""
    s = page.decode('utf-8', errors='replace')
    i = s.find('id="c-chapter-content"')
    if i == -1:
        return page
    tag_end = s.find('>', i) + 1
    sk_start = s.find('<div class="py-8', tag_end)
    sk_end = s.find('</div></div>', sk_start)
    if sk_start == -1 or sk_end == -1:
        return page
    body = content.decode('utf-8', errors='replace')
    return (s[:tag_end] + body + s[sk_end + len('</div></div>'):]).encode('utf-8')


def dl_one(item, cfg):
    eid = str(item["eid"])
    path = os.path.join(cfg["raw_dir"], f"entry_{eid}.html")
    if os.path.exists(path) and os.path.getsize(path) > cfg.get("min_size", 100000):
        return f"skip {eid} ({os.path.getsize(path)}B)"
    # 章节真实 id：优先从 item["url"] 提取（chapters/<id>），否则用 eid
    url = item.get("url", "")
    m = re.search(r'/chapters/(\d+)', url)
    ch_id = m.group(1) if m else eid
    for attempt in range(4):
        try:
            page = fetch(f"https://touhouyaruosure.com/chapters/{ch_id}")
            content = fetch(f"https://touhouyaruosure.com/api/chapters/{ch_id}/content")
            merged = inject_content(page, content)
            with open(path, "wb") as f:
                f.write(merged)
            return f"ok {eid} {len(merged)//1024}KB -> entry_{eid}.html"
        except Exception as e:
            time.sleep(3 * (attempt + 1))
    return f"FAIL {eid} {e}"


def main():
    args = sys.argv[1:]
    json_file = "temp/touhou_chapters.json"
    work = "touhou_teisou"
    if "--json" in args:
        json_file = args[args.index("--json") + 1]
    for i, a in enumerate(args):
        if a.startswith("--work"):
            work = (a.split("=", 1)[1] if "=" in a else args[i + 1]) if i + 1 < len(args) or "=" in a else work
    cfg = registry.get(work)
    data = json.load(open(json_file, encoding="utf-8"))
    entries = data.get("entries") if isinstance(data, dict) else data
    os.makedirs(cfg["raw_dir"], exist_ok=True)
    print(f"download {len(entries)} chapters -> {cfg['raw_dir']}", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        for msg in ex.map(lambda it: dl_one(it, cfg), entries):
            print(msg, flush=True)
    print("ALL DONE")


if __name__ == "__main__":
    main()
