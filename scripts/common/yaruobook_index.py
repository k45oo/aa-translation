# -*- coding: utf-8 -*-
"""网站目录读取：やる夫スレ本棚系列站 作品/章节发现工具（三站格式一致）。

支持的站（--site 指定，默认 com）：
    yaruobook.com   本棚（sujin 等）
    yaruobook.jp    2号馆（あかりちゃんシリーズ 等）
    yaruobook.net   别馆
三站同为 WordPress/Cocoon，分类页结构一致：entry-card 卡片 + category-page-content 简介。

用法：
    python common/yaruobook_index.py category <cat_id> [--site com|jp|net] [--json out.json]
    python common/yaruobook_index.py html <本地分类页> [--site com|jp|net] [--json out.json]

JSON 输出直接喂给 dl_chapters.py（与 r401_index.py 同口径）：
    python common/yaruobook_index.py category 377 --site com --json 大人のお店/work/chapters.json
    python common/yaruobook_index.py category 25548 --site jp --json あかりちゃん/work/chapters.json
    python common/dl_chapters.py --json 大人のお店/work/chapters.json

分类页结构（Cocoon）：
- 章节卡片: <a href="https://<site>/?p=<eid>" class="entry-card-wrap ..." title="<标题>">
            <article ...><h2 class="entry-card-title">标题</h2>...<span class="entry-date">日期</span></article>
- 作品简介: <div class="category-page-content entry-content">……（简介文本）……</div>（在 <article> 元素内）
- 分页: ?page=N（每页约 20 条，最新在前）

注意：需要 v2rayN 代理 127.0.0.1:10808 运行中（直连 403/超时）。
"""
import json, os, re, sys, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aa_config   # 代理统一口径（2026-10-08 阶段 4）

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
PROXY = aa_config.proxy_url()
SITES = {"com": "https://yaruobook.com", "jp": "https://yaruobook.jp", "net": "https://yaruobook.net"}
BASE = SITES["com"]

# 章节卡片: <a href="...?p=73916" class="entry-card-wrap a-wrap border-element cf" title="大人のお店　第5話">
# 域名不写死（三站 com/jp/net 同构），非贪婪匹配任意域名
CARD_RE = re.compile(
    r'<a href="(?:https://[^/"]+)?/\?p=(\d+)"[^>]*class="entry-card-wrap[^"]*"[^>]*title="([^"]+)"')
DATE_RE = re.compile(r'<span class="entry-date">([^<]+)</span>')
DESC_RE = re.compile(r'<div class="category-page-content entry-content">(.*?)</div>', re.S)
TAG_RE = re.compile(r'<[^>]+>')
WS_RE = re.compile(r'\s+')
# 分页链接：WordPress 标准 ?cat=N&paged=M（HTML 实体 &#038; = &）。支持 page= 兼容旧格式。
PAGE_RE = re.compile(r'href="[^"]*?(?:\?|&amp;|&#038;|&)cat=\d+(?:&amp;|&#038;|&)(?:paged|page)=(\d+)"')


def fetch(url):
    op = urllib.request.build_opener(urllib.request.ProxyHandler(aa_config.proxy_dict()))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with op.open(req, timeout=90) as r:
        return r.read().decode("utf-8", errors="replace")


def category_url(cat_id, page=1):
    u = f"{BASE}/?cat={cat_id}"
    if page > 1:
        # 2026-08-30: WordPress 标准是 paged=N；page= 被忽略(返回第一页)导致漏抓
        u += f"&paged={page}"
    return u


def parse_category(html):
    """分类页 -> dict(title, description, entries[{eid,title,date,url}])

    description = 作品简介（category-page-content entry-content 的纯文本）。
    entries 按页内顺序（最新在前，与 r401 同口径）。
    """
    title = ''
    mt = re.search(r'<title>([^<]+)</title>', html)
    if mt:
        title = mt.group(1).strip()
    desc = ''
    md = DESC_RE.search(html)
    if md:
        desc = WS_RE.sub(' ', TAG_RE.sub('', md.group(1))).strip()
    entries = []
    for m in CARD_RE.finditer(html):
        eid, t = m.group(1), m.group(2).strip()
        # 日期取该卡片之后最近的 entry-date
        date = ''
        dm = DATE_RE.search(html, m.end())
        if dm:
            date = dm.group(1).strip()
        entries.append({"eid": eid, "title": t, "date": date,
                        "url": f"{BASE}/?p={eid}"})
    return {"title": title, "description": desc, "entries": entries}


def fetch_all_pages(cat_id, log=print):
    """拉取分类页全部分页，合并去重（最新在前，调用方按需反转成原站顺序）。"""
    entries, desc = [], ''
    seen = set()
    page = 1
    while True:
        url = category_url(cat_id, page)
        try:
            html = fetch(url)
        except Exception as e:
            raise SystemExit(f"yaruobook 分页拉取失败 page={page}: {e}（检查 v2rayN 代理）")
        data = parse_category(html)
        if page == 1:
            desc = data["description"]
        new = 0
        for e in data["entries"]:
            if e["eid"] not in seen:
                seen.add(e["eid"])
                entries.append(e)
                new += 1
        log(f"page {page}: {len(data['entries'])} 条, 新增 {new}")
        # 2026-08-30: 不再用「条数<20」判断（每页 300 条的大分类会漏后续页）。
        # 分页链接含 上一页/下一页/跳页 混合——用 findall 收集全部页号，
        # 存在 page+1 才继续（search 只取第一个可能命中"上一页"导致提前停）。
        pages = {int(x) for x in PAGE_RE.findall(html)}
        if new == 0 or (page + 1) not in pages:
            break
        page += 1
        if page > 500:      # 安全阀
            break
    return entries, desc


def print_entries(entries):
    print(f"{'日期':<14}{'eid':<9}{'标题'}")
    print("-" * 70)
    for e in entries:
        print(f"{e['date']:<14}{e['eid']:<9}{e['title'][:50]}")
    print(f"\n共 {len(entries)} 条")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    global BASE
    args = sys.argv[2:]
    # 解析 --site（可从任意位置取）
    if "--site" in args:
        si = args[args.index("--site") + 1]
        if si not in SITES:
            raise SystemExit(f"未知站点 {si!r}（可选 com/jp/net）")
        BASE = SITES[si]
        args = [a for a in args if a != "--site" and a != si]
    cmd = sys.argv[1]
    out_path = None
    if "--json" in args:
        out_path = args[args.index("--json") + 1]
        args = [a for a in args if a != "--json" and a != out_path]
    if cmd == "category":
        cat_id = args[0]
        if "--all" in args:
            entries, desc = fetch_all_pages(cat_id)
            print(f"分类 {cat_id} ({BASE}): {len(entries)} 条（全分页，倒序）")
        else:
            html = fetch(category_url(cat_id))
            data = parse_category(html)
            entries, desc = data["entries"], data["description"]
            print(f"分类 {cat_id} ({BASE}): {data['title']}")
        if desc:
            print(f"\n【作品简介】{desc}\n")
        print_entries(entries)
        if out_path:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump({"title": f"cat_{cat_id}", "description": desc,
                           "entries": entries}, f, ensure_ascii=False, indent=1)
            print(f"\nJSON: {out_path}")
    elif cmd == "html":
        path = args[0]
        html = open(path, encoding="utf-8", errors="replace").read()
        data = parse_category(html)
        print(f"本地页 ({BASE}): {data['title']}")
        if data["description"]:
            print(f"\n【作品简介】{data['description']}\n")
        print_entries(data["entries"])
        if out_path:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            print(f"\nJSON: {out_path}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
