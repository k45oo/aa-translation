# -*- coding: utf-8 -*-
"""网站目录读取：rss.r401.net（やる夫RSS+インデックス）作品/章节发现工具。

用法：
    python common/r401_index.py search <关键词>            # 按作品名搜索 → 分类 id
    python common/r401_index.py category <id> [--json out.json]   # 分类章节清单（表格 / JSON）
    python common/r401_index.py sites                       # 全部站点列表
    python common/r401_index.py site <id>                   # 站点详情（该站全部作品）
    python common/r401_index.py html <本地分类页>            # 离线解析分类页（无需网络）

JSON 输出直接喂给 dl_chapters.py：
    python common/r401_index.py category 12068 --json 不幸の催眠/work/chapters.json
    python common/dl_chapters.py --json 不幸の催眠/work/chapters.json

注意：需要 v2rayN 代理 127.0.0.1:10808 运行中（直连 403/超时）。
"""
import json, os, re, sys, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aa_config   # 代理统一口径（2026-10-08 阶段 4）

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
PROXY = aa_config.proxy_url()
BASE = "https://rss.r401.net"

EXLINK_RE = re.compile(
    r'<div class="item-name.*?data-rid-arcid-param="(\d+)".*?>'
    r'.*?<a.*?class="exlink".*?href="([^"]+)"[^>]*>(.*?)</a>', re.S)
DATE_RE = re.compile(r'<div class="item-date">\s*(\d{4})年(\d{1,2})月(\d{1,2})日')
CAT_TITLE_RE = re.compile(r'<h2 class="item-list-h2">\s*([^<]+)')
SITE_LINK_RE = re.compile(r'href="(/yaruo/sites/\d+)"')
CAT_LINK_RE = re.compile(r'<a[^>]*href="(/yaruo/categories/(\d+))"[^>]*>\s*([^<]+?)\s*</a>')
ITEM_NAME_RE = re.compile(r'data-rid-catid-param="(\d+)"')


def fetch(url):
    op = urllib.request.build_opener(urllib.request.ProxyHandler(aa_config.proxy_dict()))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with op.open(req, timeout=90) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_category(html):
    """分类页 -> dict(title, site_link, entries[{eid,title,url,date,arcid}])"""
    m = CAT_TITLE_RE.search(html)
    title = m.group(1).strip() if m else ''
    sm = SITE_LINK_RE.search(html)
    site_link = sm.group(1) if sm else ''
    entries = []
    for m in EXLINK_RE.finditer(html):
        arcid, url, name = m.group(1), m.group(2).strip(), m.group(3).strip()
        eid = None
        mm = re.search(r'blog-entry-(\d+)', url) or re.search(r'[?&]p=(\d+)', url)
        if mm:
            eid = mm.group(1)
        elif url.rstrip('/').endswith('.html'):
            eid = os.path.basename(url).replace('.html', '')
        date = ''
        dm = DATE_RE.search(html, m.end())
        if dm:
            date = f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
        entries.append({"eid": eid or arcid, "title": name, "url": url,
                        "date": date, "arcid": arcid})
    return {"title": title, "site_link": site_link, "entries": entries}


def parse_site(html):
    """站点页 -> dict(name, works[{cid,title}])"""
    m = re.search(r'<h2 class="item-list-h2">\s*([^<]+)', html)
    name = m.group(1).strip() if m else ''
    works = []
    for m in ITEM_NAME_RE.finditer(html):
        cid = m.group(1)
        mm = re.search(r'href="/yaruo/categories/' + cid + r'"[^>]*>\s*([^<]+?)\s*</a>', html)
        title = mm.group(1).strip() if mm else ''
        works.append({"cid": cid, "title": title})
    return {"name": name, "works": works}


def parse_search(html):
    """搜索结果 -> [{cid,title}]（前几条最相关）"""
    out = []
    for m in CAT_LINK_RE.finditer(html):
        out.append({"cid": m.group(2), "title": m.group(3).strip()})
    return out


def parse_sites(html):
    """站点列表页 -> [{sid,name}]"""
    out = []
    for m in re.finditer(r'href="(/yaruo/sites/(\d+))"[^>]*>\s*([^<]+?)\s*</a>', html):
        out.append({"sid": m.group(2), "name": m.group(3).strip()})
    return out


def print_entries(entries):
    print(f"{'日期':<12}{'eid':<9}{'标题':<40}URL")
    print("-" * 100)
    for e in entries:
        print(f"{e['date']:<12}{e['eid']:<9}{e['title'][:38]:<40}{e['url']}")
    print(f"\n共 {len(entries)} 条")


PAGE_RE = re.compile(r'href="[^"]*?page=(\d+)"')


def fetch_all_pages(cid, log=print):
    """拉取分类页全部分页，合并去重。

    分类页每页 40 条（倒序，最新在前）；分页链接只含相邻页（如 page=2），
    无法直接求最大页——逐页探测直到空页/重复页。返回 entries（保持倒序，
    调用方按需反转成原站顺序）。
    """
    entries = []
    seen = set()
    page = 1
    while True:
        url = f"{BASE}/yaruo/categories/{cid}" + (f"?page={page}" if page > 1 else "")
        try:
            html = fetch(url)
        except Exception as e:
            raise SystemExit(f"r401 分页拉取失败 page={page}: {e}（检查 v2rayN 代理）")
        data = parse_category(html)
        new = 0
        for e in data["entries"]:
            key = (e["eid"], e["url"])
            if key not in seen:
                seen.add(key)
                entries.append(e)
                new += 1
        log(f"page {page}: {len(data['entries'])} 条, 新增 {new}")
        if new == 0 or len(data["entries"]) < 40:
            break
        # 探测下一页：当前页有 page=N+1 链接才继续（否则已到最后一页）
        m = PAGE_RE.search(html)
        if not m:
            break
        page += 1
        if page > 200:      # 安全阀
            break
    return entries


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    args = sys.argv[2:]
    if cmd == "search":
        kw = urllib.parse.quote(args[0]) if args else ''
        html = fetch(f"{BASE}/yaruo/categories?s={kw}")
        results = parse_search(html)
        print(f"搜索「{args[0] if args else ''}」→ {len(results)} 个分类（前 20）:")
        for r in results[:20]:
            print(f"  {r['cid']:<8}{r['title']}")
    elif cmd == "category":
        cid = args[0]
        if "--all" in args:
            entries = fetch_all_pages(cid)
            data = {"title": "", "site_link": "", "entries": entries}
            # 第一页的标题/站点
            try:
                html0 = fetch(f"{BASE}/yaruo/categories/{cid}")
                d0 = parse_category(html0)
                data["title"], data["site_link"] = d0["title"], d0["site_link"]
            except Exception:
                pass
            print(f"分类 {cid}: {data['title']}  站点: {data['site_link']}（全分页 {len(entries)} 条，倒序）")
        else:
            html = fetch(f"{BASE}/yaruo/categories/{cid}")
            data = parse_category(html)
            print(f"分类 {cid}: {data['title']}  站点: {data['site_link']}")
        print_entries(data["entries"])
        if "--json" in args:
            out = args[args.index("--json") + 1]
            with open(out, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            print(f"\nJSON: {out}")
    elif cmd == "sites":
        html = fetch(f"{BASE}/yaruo/sites")
        for s in parse_sites(html):
            print(f"  {s['sid']:<8}{s['name']}")
    elif cmd == "site":
        sid = args[0]
        html = fetch(f"{BASE}/yaruo/sites/{sid}")
        data = parse_site(html)
        print(f"站点 {sid}: {data['name']}（{len(data['works'])} 个作品）")
        for w in data["works"]:
            print(f"  {w['cid']:<8}{w['title']}")
    elif cmd == "html":
        path = args[0]
        html = open(path, encoding="utf-8", errors="replace").read()
        data = parse_category(html)
        print(f"本地页: {data['title']}  站点: {data['site_link']}")
        print_entries(data["entries"])
        if "--json" in args:
            out = args[args.index("--json") + 1]
            with open(out, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            print(f"\nJSON: {out}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
