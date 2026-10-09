# -*- coding: utf-8 -*-
"""AA 漫画搜索工具（v1 纯 CLI）：以网上源（r401 全站 + yaruobook 3 站）为权威全集搜索新作品。

主用例：找新作品开工——搜索站端全集，本地 registry 仅做「已收录」标注，与「添加作品」流程衔接。

用法：
    python scripts/tools/aa_search.py <关键词> [--author 名] [--tag 标签]... [--site r401|com|jp|net] [--r18|--no-r18] [--since D] [--until D] [--started-after D] [--no-count] [--json out.json] [--report [path]]
    python scripts/tools/aa_search.py --tag R-18表現含む --tag 安価 [--site r401]      # 无关键词 = 纯标签浏览
    python scripts/tools/aa_search.py ranking --site com|jp|net --period week|month|all
    python scripts/tools/aa_search.py aliases            # 作者马甲表/候选 查看
    python scripts/tools/aa_search.py aliases --promote  # candidates 中 确认:true 转正（glossary 同款）

4 源并行抓取（concurrent.futures）。输出表格 + --json 机读（可直接喂 dl_runner / 添加作品流程）。
纯标准库（urllib/json/re/concurrent.futures）。需要 v2rayN 代理 127.0.0.1:10808（直连 403/超时）。
--web（tavily）已砍：探针实测公开 web 只返回角色 wiki 词条，无结构化作品增益（2026-09-01）。

数据面（2026-08-31 实测探明）：
- r401 搜索：/yaruo/categories?s= 作品名 / /yaruo/archives?s= 章节名（题级，item=作品+章节+站点+日期）/ /yaruo/authors?s= 作者（按名/trip）
- r401 tags：/yaruo/tags/<tid> 分类级（オリジナル=6、R-18表現含む=26）；/yaruo/tags 全量清单（32 个一次抓全）
- r401 rank：/yaruo/rank 单页四块（access1 連載 / access2 作者名連載短編 / access3 作者 / new 新人）
- yaruobook：?s= 章节级搜索（300/页 paged=N 分页）；searchandfilter=POST 表单（ofsearch 关键词 + ofcategory 作品分类）
- 作者马甲/重复作品：scripts/config/search_authors.json / search_duplicates.json（人工资产，candidates 自动收集→人工确认转正）
"""
import sys, os, json, re, time, argparse, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根
sys.path.insert(0, os.path.join(BASE, "scripts"))
sys.path.insert(0, os.path.join(BASE, "scripts", "works"))
sys.path.insert(0, os.path.join(BASE, "scripts", "common"))
import registry
import aa_config   # 代理统一口径（2026-10-08 阶段 4）

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
PROXY = aa_config.proxy_url()
R401 = "https://rss.r401.net"
YARUOBOOK = {"com": "https://yaruobook.com", "jp": "https://yaruobook.jp", "net": "https://yaruobook.net"}
# yaruobook ?s= 是 WordPress 全文搜索（扫正文），响应天然慢；按站放宽超时，com 实测单页 140s+ 再放宽
YB_TIMEOUT = {"com": 300, "jp": 180, "net": 180}
DATA_DIR = os.path.join(BASE, "data", "search")       # gitignore：tags 映射 + 榜单缓存（1 天 TTL）
CONFIG_DIR = os.path.join(BASE, "scripts", "config")
AUTHORS_JSON = os.path.join(CONFIG_DIR, "search_authors.json")
DUPS_JSON = os.path.join(CONFIG_DIR, "search_duplicates.json")
TTL = 86400  # 1 天

# ---------------- 网络 ----------------

def fetch(url, timeout=90):
    op = urllib.request.build_opener(urllib.request.ProxyHandler(aa_config.proxy_dict()))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with op.open(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


# ---------------- r401 解析 ----------------
# 分类页/搜索结果/archives 共用的 item 结构：
#   <div class="item-name s" data-rid-catid-param="<cid>" ...><a href="/yaruo/categories/<cid>">作品名</a>
#   <div class="item-arc s" data-rid-arcid-param="<arcid>"><a class="exlink" href="...">章节名</a>
#   <span class="item-site"><a href="/yaruo/sites/<sid>">站点名</a> + <div class="item-date">日期
EXLINK_RE = re.compile(
    r'data-rid-catid-param="(\d+)"[^>]*>\s*<a href="/yaruo/categories/\d+"[^>]*>([^<]+)</a>'
    r'.*?data-rid-arcid-param="(\d+)"[^>]*>\s*<a[^>]*class="exlink"[^>]*href="([^"]+)"[^>]*>([^<]+)</a>'
    r'.*?item-site[^>]*>\s*<a[^>]*href="/yaruo/sites/(\d+)"[^>]*>([^<]+)</a>'
    r'.*?item-date[^>]*>\s*([^<]+)', re.S)
# 作品头（categories 搜索 / tag 页）：data-rid-catid-param 指向作品分类，链接文本=作品名。
# date 取其后最近的 item-date（可选），site 不强制（archives 记录会补齐）。
CAT_HEAD_RE = re.compile(
    r'data-rid-catid-param="(\d+)"[^>]*>\s*<a href="/yaruo/categories/\d+"[^>]*>([^<]+)</a>'
    r'(?:(?!item-name).)*?item-date[^>]*>\s*([^<]+)', re.S)
PAGE_RE = re.compile(r'href="[^"]*?[?&amp;]page=(\d+)"')


def _parse_date_r401(s):
    """'2026年09月01日' / '05月10日 01時00分' -> 'YYYY-MM-DD'（无年取当年）。"""
    s = re.sub(r"\s+", " ", s).strip()
    m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r"(\d{1,2})月(\d{1,2})日", s)
    if m:
        y = time.localtime().tm_year
        return f"{y}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return ""


def parse_r401_items(html, chapters=False):
    """r401 搜索/archives/tag 页 → 作品列表。

    chapters=True（archives）：每条 item 带作品名(cat 链接)+章节(exlink)+站点+日期；
    chapters=False（categories 搜索 / tag 页）：只抓作品头（cid+作品名+日期，site 留空）。
    返回 [{cid,title,site,site_id,date,chapter?(eid,url,ctitle)}]
    """
    out, seen = [], set()
    for m in (EXLINK_RE if chapters else CAT_HEAD_RE).finditer(html):
        if chapters:
            cid, title, arcid, url, ctitle, sid, site, date = m.groups()
            rec = {"cid": cid, "title": title.strip(), "site": site.strip(), "site_id": sid,
                   "date": _parse_date_r401(date),
                   "chapter": {"eid": arcid, "url": url.strip(), "ctitle": ctitle.strip()}}
        else:
            cid, title, date = m.groups()
            rec = {"cid": cid, "title": title.strip(), "site": "", "site_id": "",
                   "date": _parse_date_r401(date or "")}
        key = (rec["cid"], rec["title"], rec.get("site", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return out


def r401_search(kw, chapters=False):
    """r401 搜索：作品名（categories?s=）或章节名（archives?s=，题级）。拉前 5 页。"""
    path = "archives" if chapters else "categories"
    out, seen = [], set()
    for page in range(1, 6):
        url = f"{R401}/yaruo/{path}?s={urllib.parse.quote(kw)}" + (f"&page={page}" if page > 1 else "")
        try:
            html = fetch(url)
        except Exception as e:
            break
        items = parse_r401_items(html, chapters=chapters)
        new = 0
        for it in items:
            k = (it["cid"], it["title"])
            if k not in seen:
                seen.add(k)
                out.append(it)
                new += 1
        if new == 0:
            break
    return out


def r401_tag_list():
    """/yaruo/tags 全量清单 -> {名称: tid}（32 个一次抓全）。1 天 TTL 缓存。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    cache = os.path.join(DATA_DIR, "r401_tags.json")
    if os.path.exists(cache) and time.time() - os.path.getmtime(cache) < TTL:
        try:
            return json.load(open(cache, encoding="utf-8"))
        except Exception:
            pass
    html = fetch(f"{R401}/yaruo/tags")
    m = {}
    for tid, name in re.findall(r'href="/yaruo/tags/(\d+)"[^>]*>\s*([^<]+?)\s*</a>', html):
        m[name.strip()] = tid
    json.dump(m, open(cache, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return m


def r401_tag_works(tid, kw=None):
    """r401 tag 分类页 → 该标签下作品（catid 集合，前 5 页；kw 为 None 时是 tag 浏览）。"""
    out, seen = [], set()
    for page in range(1, 6):
        url = f"{R401}/yaruo/tags/{tid}" + (f"?page={page}" if page > 1 else "")
        try:
            html = fetch(url)
        except Exception:
            break
        items = parse_r401_items(html, chapters=False)
        new = 0
        for it in items:
            k = (it["cid"], it["title"])
            if k not in seen:
                seen.add(k)
                out.append(it)
                new += 1
        if new == 0:
            break
    return out


def r401_tag_all(tid, max_workers=8):
    """r401 tag 分类页**全量**抓取（tag26 有 72 页，前 5 页会漏）→ [{cid,title,site,date}]。

    先抓第 1 页拿分页总数（nav 含最大页号），其余页并发 8 路（失败重试 2 次）。
    与 r401_tag_works 的区别：无 5 页上限，用于 R18 权威集合等需要全量的场景。
    """
    out, seen = [], set()

    def grab(page, tries=3):
        url = f"{R401}/yaruo/tags/{tid}" + (f"?page={page}" if page > 1 else "")
        for i in range(tries):
            try:
                html = fetch(url)
                break
            except Exception:
                if i == tries - 1:
                    return [], None
        items = parse_r401_items(html, chapters=False)
        # 分页 nav：<a href="...?page=N">&gt;&gt;</a> = 末页
        last = None
        m = re.search(r'href="[^"]*?page=(\d+)"[^>]*>&gt;&gt;', html)
        if m:
            last = int(m.group(1))
        return items, last

    first, last = grab(1)
    if not first and last is None:
        return []
    for it in first:
        k = (it["cid"], it["title"])
        if k not in seen:
            seen.add(k)
            out.append(it)
    if not last or last <= 1:
        return out
    pages = range(2, last + 1)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(grab, p): p for p in pages}
        for f in futs:
            try:
                items, _ = f.result()
            except Exception:
                continue
            for it in items:
                k = (it["cid"], it["title"])
                if k not in seen:
                    seen.add(k)
                    out.append(it)
    return out


def r401_r18_cid_set(force=False):
    """r401 R18 权威集合 = tag 26（R-18表現含む）全量 cid。1 天 TTL 缓存 data/search/r401_r18_cids.json。

    force=True 强刷（--no-cache）。返回 {cid: {title, date}}（保留元数据供展示）。
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    cache = os.path.join(DATA_DIR, "r401_r18_cids.json")
    if not force and os.path.exists(cache) and time.time() - os.path.getmtime(cache) < TTL:
        try:
            return json.load(open(cache, encoding="utf-8"))
        except Exception:
            pass
    works = r401_tag_all(26)
    data = {w["cid"]: {"title": w["title"], "date": w.get("date", "")} for w in works}
    json.dump(data, open(cache, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return data


def _tag26_browse():
    """--tag R-18表現含む 浏览：优先读 r18 缓存（全量且免 72 页重拉）。"""
    d = r401_r18_cid_set()
    return [{"cid": cid, "title": v.get("title", ""), "site": "", "date": v.get("date", "")}
            for cid, v in d.items()]


def r401_author_search(name, max_pages=3):
    """r401 作者搜索：?s= 返回全作者表子串模糊流，必须精确过滤（trip/正名完全匹配）。

    作者页 = 该作者出现过的全部分类（含转载/他人合集，上千条）——抓前 max_pages 页
    并过滤他人合集标题（◆XX氏の… 等），返回本人作品 [{cid,title,author}]。
    """
    html = fetch(f"{R401}/yaruo/authors?s={urllib.parse.quote(name)}")
    aids = []
    for aid, aname in re.findall(r'href="/yaruo/authors/(\d+)"[^>]*>\s*([^<]+?)\s*</a>', html):
        aname = aname.strip()
        # 精确匹配：搜索词是 trip 前缀（含 ◆）→ 作者名含该 trip；否则正名完全相等
        if name.startswith("◆"):
            if name in aname:
                aids.append((aid, aname))
        elif aname.split("◆")[0].strip().replace("★", "").rstrip("＠") == name:
            aids.append((aid, aname))
    works = []
    seen = set()
    own_trips = set()
    for aid, aname in aids:
        mt = re.search(r"◆([^★\s]+)", aname)
        if mt:
            own_trips.add(mt.group(1))
    for aid, aname in aids:
        for page in range(1, max_pages + 1):
            url = f"{R401}/yaruo/authors/{aid}" + (f"?page={page}" if page > 1 else "")
            try:
                h2 = fetch(url)
            except Exception:
                break
            for cid, title in re.findall(r'href="/yaruo/categories/(\d+)"[^>]*>\s*([^<]+?)\s*</a>', h2):
                title = title.strip()
                if _is_collection_title(title, own_trips):
                    continue  # 他人合集/杂谈分类，非本人作品
                if (cid, title) not in seen:
                    seen.add((cid, title))
                    works.append({"cid": cid, "title": title, "site": "", "site_id": "", "date": "",
                                  "author": aname})
            pages = {int(x) for x in re.findall(rf'authors/{aid}\?page=(\d+)', h2)}
            if (page + 1) not in pages:
                break
    return works


_COLLECTION_TITLE_RE = re.compile(r"氏の(?:作品集|短編)|の雑談|投下所|黒歴史|の製薬|一発ネタ|の隔離施設|の証券取引所|の調剤所")


def _is_collection_title(t, own_trips):
    """他人合集/杂谈分类标题（作者页里非本人作品的分类）。

    规则：标题含他人 trip（◆xx 且非本人 trip）→ 合集；标题含 氏の/雑談/投下所 等词 → 合集。
    """
    if _COLLECTION_TITLE_RE.search(t):
        return True
    for mt in re.finditer(r"◆([^★\s]+)", t):
        if mt.group(1) not in own_trips:
            return True
    return False


# ---------------- yaruobook 解析 ----------------
# 卡片: <a href="...?p=<eid>" class="entry-card-wrap ..." title="章节标题">
#   <span class="cat-label cat-label-<cat_id>">作品名</span>  ← 该章节所属作品分类（按作品聚合的正道）
YB_CARD_RE = re.compile(
    r'<a href="(?:https://[^/\"]+)?/?\?p=(\d+)"[^>]*class="entry-card-wrap[^"]*"[^>]*title="([^"]+)"'
    r'.*?cat-label cat-label-(\d+)">([^<]+)</span>', re.S)
YB_DATE_RE = re.compile(r'<span class="entry-date">([^<]+)</span>')


def yaruobook_search(site, kw, tag=None, max_pages=3):
    """yaruobook ?s= 章节级全文搜索（300/页，paged=N；WordPress 站内全文搜索，
    命中章节标题可能不含关键词=正文命中）。

    每张卡自带 cat-label（所属作品分类 id+作品名）→ 按 cat id 聚合到作品。
    返回 [{cat_id, cat_name, title(作品名), site, chapters:[{eid,ctitle,date}]}]
    """
    base = YARUOBOOK[site]
    timeout = YB_TIMEOUT.get(site, 180)
    works = {}
    for page in range(1, max_pages + 1):
        url = f"{base}/?s={urllib.parse.quote(kw)}" + (f"&paged={page}" if page > 1 else "")
        try:
            html = fetch(url, timeout=timeout)
        except Exception:
            break
        cards = YB_CARD_RE.findall(html)
        if not cards:
            break
        for eid, title, cat_id, cat_name in cards:
            date = ""
            dm = YB_DATE_RE.search(html, html.find(f'?p={eid}'))
            if dm:
                date = dm.group(1).strip()
            wk = f"cat:{cat_id}"
            w = works.setdefault(wk, {"cat_id": cat_id, "cat_name": cat_name.strip(),
                                      "title": cat_name.strip(), "site": f"yaruobook.{site}", "chapters": []})
            w["chapters"].append({"eid": eid, "ctitle": title.strip(), "date": date})
        # 分页链接含 paged=page+1 才继续
        pages = {int(x) for x in re.findall(r'href="[^"]*?paged=(\d+)"', html)}
        if (page + 1) not in pages:
            break
    # 汇总（作品行 + 章节聚合）
    out = []
    for wk, w in works.items():
        chapters = sorted(w["chapters"], key=lambda c: c.get("date") or "", reverse=True)
        rec = {"cat_id": w["cat_id"], "title": w["title"], "site": w["site"],
               "date": max((c["date"] for c in chapters if c["date"]), default=""),
               "chapter_count": len(chapters), "chapters": chapters}
        out.append(rec)
    return out


def yaruobook_categories(site):
    """searchandfilter 的 ofcategory 选项 -> {作品名: cat_id}（GET ?s= 页内嵌 POST 表单）。"""
    base = YARUOBOOK[site]
    timeout = YB_TIMEOUT.get(site, 180)
    try:
        html = fetch(f"{base}/?s=test", timeout=timeout)
    except Exception:
        return {}
    j = html.find('class="searchandfilter"')
    if j < 0:
        return {}
    return {name.strip(): val for val, name in re.findall(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)</option>', html[j:j + 60000]) if val}


# ---------------- 规范化/本地已收录 ----------------

def norm_title(s):
    """规范化标题：去全半角/空白/系列后缀 → 重复作品聚类键。"""
    s = s.strip()
    s = s.replace("\u3000", " ").replace("　", " ").replace(" ", "")
    # 前缀 R18/18禁 剥离必须在括号删除之前（【R-18】→R-18 后再剥前缀会失败）
    s = re.sub(r"^(?:\[R-18\]|【R-18】|【R18】|【18禁】|【R18G】|\[18禁\]|【Ｒ-１８】|【１８禁】)", "", s)
    s = re.sub(r"[\uFF01-\uFF5E]", lambda m: chr(ord(m.group(0)) - 0xFEE0), s)  # 全角→半角
    s = re.sub(r"[【】\[\]\(\)（）\-\s]", "", s)
    s = re.sub(r"(?:のようです|のようです。|のようだ|なようです)$", "", s)
    s = re.sub(r"(?:第?\s*[0-9０-９]+\s*[話话]|[0-9０-９]+\s*[話话]|番外).*$", "", s)
    return s


def local_registry_index():
    """本地 registry → 已收录标注键。

    返回 (by_cid, by_site_url, by_title)：
      by_cid: r401_cid -> work_id
      by_site_url: site_url.json 分类页 URL -> work_id（域名含 yaruobook 时按 ?cat= 归一）
      by_title: 规范化标题 -> [work_id]（本地 title / work_meta jp_title / cn_name）
    """
    by_cid, by_url, by_title = {}, {}, {}
    works = registry.all_works()
    meta = {}
    mp = os.path.join(BASE, "aa_web_app", "data", "work_meta.json")
    if os.path.exists(mp):
        try:
            meta = json.load(open(mp, encoding="utf-8"))
        except Exception:
            meta = {}
    for wid, cfg in works.items():
        cid = cfg.get("r401_cid")
        if cid:
            by_cid[str(cid)] = wid
        wd = cfg.get("work_dir", "")
        sp = os.path.join(wd, "site_url.json") if wd else ""
        if os.path.exists(sp):
            try:
                u = json.load(open(sp, encoding="utf-8")).get("url", "")
            except Exception:
                u = ""
            if u:
                by_url[norm_site_url(u)] = wid
        # 标题
        titles = [cfg.get("title", "")]
        m = meta.get(wid, {})
        if isinstance(m, dict):
            titles += [m.get("jp_title", ""), m.get("cn_name", "")]
        for t in titles:
            if t:
                by_title.setdefault(norm_title(t), []).append(wid)
    return by_cid, by_url, by_title


def norm_site_url(u):
    """站点分类页 URL 归一（yaruobook ?cat=N 去域名差异；r401 保留路径）。"""
    u = u.split("?")[0].rstrip("/") if "?" not in u or "cat=" in u else u
    m = re.search(r"cat=(\d+)", u)
    if m:
        return f"yaruobook:cat:{m.group(1)}"
    return u


def is_local(rec, by_cid, by_url, by_title):
    """三重比对：r401_cid / site_url / 规范化标题。返回 (bool, 匹配方式, work_id)"""
    cid = rec.get("cid", "")
    if cid and str(cid) in by_cid:
        return True, "r401_cid", by_cid[str(cid)]
    url = rec.get("site_url", "") or ""
    if url:
        k = norm_site_url(url)
        if k in by_url:
            return True, "site_url", by_url[k]
    if "yaruobook" in rec.get("site", ""):
        # yaruobook 搜索按章节聚合，无 cid——用规范化标题
        t = norm_title(rec.get("title", ""))
        if t in by_title:
            return True, "title", by_title[t][0]
    return False, "", ""


# ---------------- 作者马甲 / 重复作品（candidates→确认，glossary 同款） ----------------

SEARCH_CATS_JSON = os.path.join(CONFIG_DIR, "search_categories.json")


def search_categories():
    """分类关键词表 scripts/config/search_categories.json -> {分类: {title:[...], summary:[...]}}。"""
    return load_json(SEARCH_CATS_JSON, {})


def title_classify(title, summary=""):
    """按关键词表对标题+简介分类 -> {分类: "标题"|"简介"|None 判定来源}。

    标题命中 = 零成本预判；简介命中需在总数抓取阶段二次判定。
    """
    cats = search_categories()
    res = {}
    for name, kw in cats.items():
        src = None
        for w in kw.get("title", []):
            if w and w.lower() in (title or "").lower():
                src = "标题"
                break
        if not src and summary:
            for w in kw.get("summary", []):
                if w and w.lower() in (summary or "").lower():
                    src = "简介"
                    break
        if src:
            res[name] = src
    return res


def load_json(p, default):
    if not os.path.exists(p):
        return default
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def save_json(p, data):
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def author_aliases():
    """作者马甲表 -> {正名: {"马甲": [...], "trip": [...]}}"""
    data = load_json(AUTHORS_JSON, {"作者": {}})
    return data.get("作者", {})


def collect_alias_candidates(works):
    """自动收集作者马甲候选：同规范名不同 trip / 同站同作者名多个 aid。写 candidates 区（不自动转正）。"""
    data = load_json(AUTHORS_JSON, {"作者": {}})
    known = data.get("作者", {})
    cands = data.get("candidates", [])
    seen = {(c.get("正名"), c.get("马甲")) for c in cands}
    changed = False
    for w in works:
        an = w.get("author") or ""
        if not an:
            continue
        an = re.sub(r"\s*◆.*$", "", an).strip()  # 去 trip 后缀留正名
        if not an:
            continue
        for kname, kv in known.items():
            if an == kname or an in kv.get("马甲", []):
                break
        else:
            cand = {"正名": an, "马甲": an, "trip": "", "说明": "自动收集：同规范名不同 trip / 同站同作者"}
            if (an, an) not in seen:
                cands.append(cand)
                seen.add((an, an))
                changed = True
    if changed:
        data["candidates"] = cands
        save_json(AUTHORS_JSON, data)
    return cands


def promote_aliases():
    """candidates 中 确认:true 转正进 作者 区。返回转正数。"""
    data = load_json(AUTHORS_JSON, {"作者": {}})
    cands = data.get("candidates", [])
    n = 0
    for c in cands:
        if c.get("确认"):
            name = (c.get("正名") or "").strip()
            if not name:
                continue
            data.setdefault("作者", {})[name] = {"马甲": [name], "trip": [c.get("trip", "")] if c.get("trip") else []}
            n += 1
    data["candidates"] = [c for c in cands if not c.get("确认")]
    save_json(AUTHORS_JSON, data)
    return n


def dup_groups():
    data = load_json(DUPS_JSON, {"重复作品": {}})
    return data.get("重复作品", {})


def site_key(site):
    """站点归一：r401 站名「やる夫スレ本棚/やる夫スレ本棚2号館/別館」与 yaruobook.com/jp/net 映射同键。"""
    if not site:
        return site
    m = {"やる夫スレ本棚": "com", "やる夫スレ本棚2号館": "jp", "やる夫スレ本棚別館": "net"}
    if site in m:
        return m[site]
    return site


def collect_dup_candidates(works):
    """自动收集重复作品候选：规范化标题相同且站点（归一后）不同的作品 → candidates 区。"""
    data = load_json(DUPS_JSON, {"重复作品": {}})
    known = data.get("重复作品", {})
    cands = data.get("candidates", [])
    by_key = {}
    for w in works:
        t = norm_title(w.get("title", ""))
        if not t or len(t) < 3:
            continue
        by_key.setdefault(t, []).append(w)
    changed = False
    for t, group in by_key.items():
        sites = {site_key(w.get("site", "")) for w in group if w.get("site")}
        if len(sites) > 1:
            # 已确认组跳过
            already = False
            for k_titles in known.values():
                if any(t == norm_title(x) for x in k_titles):
                    already = True
                    break
            if already:
                continue
            cand = {"key": t, "作品": [{"title": w.get("title"), "site": w.get("site")} for w in group],
                    "说明": "自动收集：同规范名多站点"}
            if not any(c.get("key") == t for c in cands):
                cands.append(cand)
                changed = True
    if changed:
        data["candidates"] = cands
        save_json(DUPS_JSON, data)
    return cands


def promote_dups():
    """candidates 确认:true → 重复作品 组（key -> [标题]）。"""
    data = load_json(DUPS_JSON, {"重复作品": {}})
    cands = data.get("candidates", [])
    n = 0
    for c in cands:
        if c.get("确认"):
            key = (c.get("key") or "").strip()
            if not key:
                continue
            data.setdefault("重复作品", {})[key] = [w.get("title") for w in c.get("作品", [])]
            n += 1
    data["candidates"] = [c for c in cands if not c.get("确认")]
    save_json(DUPS_JSON, data)
    return n


# ---------------- 榜单（r401 rank，1 天 TTL） ----------------

def fetch_rank(period="week"):
    """r401 /yaruo/rank 单页四块。period: week(access1)/month(access2)/all(access1 累计)——rank 页无累计块，all 复用 access1。"""
    cache = os.path.join(DATA_DIR, f"rank_{period}.json")
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(cache) and time.time() - os.path.getmtime(cache) < TTL:
        try:
            return json.load(open(cache, encoding="utf-8"))
        except Exception:
            pass
    html = fetch(f"{R401}/yaruo/rank")
    block = "access1" if period in ("week", "all") else "access2"
    i = html.find(f'id="{block}"')
    seg = html[i:i + 9000]
    out = []
    for cid, title in re.findall(r'<li>\s*<a href="/yaruo/categories/(\d+)"[^>]*>([^<]+)</a>', seg):
        out.append({"cid": cid, "title": title.strip()})
    json.dump(out, open(cache, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return out


# ---------------- 章节总数 / 开坑日期 / 简介抓取 ----------------

def _last_page_from_nav(html):
    """r401 分页 nav：<a href="...?page=N">&gt;&gt;</a> = 末页号。无则 None。"""
    m = re.search(r'href="[^"]*?page=(\d+)"[^>]*>&gt;&gt;', html)
    return int(m.group(1)) if m else None


def count_r401(cid):
    """r401 作品分类页（40/页）→ {chapter_total, started, items_last}。

    首页拿末页号（&gt;&gt;）+ item 数；多页则请求末页数 items。
    总数 = (last-1)×40 + 末页数；开坑 = 末页最后 item-date（最早章节）。
    """
    try:
        h1 = fetch(f"{R401}/yaruo/categories/{cid}")
    except Exception:
        return {"chapter_total": 0, "started": ""}
    items1 = len(re.findall(r'class="item-name s"', h1))
    last = _last_page_from_nav(h1)
    started = ""
    if not last or last <= 1:
        # 单页：最早 = 本页最后日期
        dt = re.findall(r'item-date[^>]*>\s*([^<]+)', h1)
        if dt:
            started = _parse_date_r401(dt[-1].strip())
        return {"chapter_total": items1, "started": started}
    try:
        hl = fetch(f"{R401}/yaruo/categories/{cid}?page={last}")
    except Exception:
        return {"chapter_total": (last - 1) * 40 + items1, "started": ""}
    items_last = len(re.findall(r'class="item-name s"', hl))
    dt = re.findall(r'item-date[^>]*>\s*([^<]+)', hl)
    if dt:
        started = _parse_date_r401(dt[-1].strip())
    return {"chapter_total": (last - 1) * 40 + items_last, "started": started}


def _count_yb_last_dates(html):
    """分类页 → (条目数, 最早卡日期)。卡片日期 = 该卡后最近的 entry-date（防侧栏日期混入）。"""
    cards = re.findall(r'class="entry-card-wrap', html)
    n = len(cards)
    # 每卡取其后最近 entry-date，最后一张卡的日期 = 最早章节（开坑）
    last_date = ""
    pos = 0
    for i in range(n):
        j = html.find('class="entry-card-wrap', pos)
        if j < 0:
            break
        dm = re.search(r'<span class="entry-date">([^<]+)</span>', html[j:j + 2000])
        if dm:
            last_date = dm.group(1).strip()
        pos = j + 30
    return n, last_date


def count_yb(site, cat_id):
    """yaruobook 分类页（300/页）→ {chapter_total, started, description}。

    首页拿分页最大页号 N（PAGE_RE）+ 简介；多页则请求末页 ?cat=X&paged=N 计数。
    总数 = (N-1)×300 + 末页条数（2026-09-03 用户指正：一次末页跳转即可）。
    开坑 = 末页最后一张卡的 entry-date；简介 = 首页 category-page-content（R18/安価 二次判定用）。
    """
    base = YARUOBOOK[site]
    timeout = YB_TIMEOUT.get(site, 180)
    try:
        h1 = fetch(f"{base}/?cat={cat_id}", timeout=timeout)
    except Exception:
        return {"chapter_total": 0, "started": "", "description": ""}
    # 简介（首页才有 category-page-content）
    desc = ""
    md = re.search(r'<div class="category-page-content entry-content">(.*?)</div>', h1, re.S)
    if md:
        desc = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", md.group(1))).strip()
    # 分页页号（findall 收集全部）
    pages = {int(x) for x in re.findall(r'href="[^"]*?(?:\?|&amp;|&#038;|&)cat=\d+(?:&amp;|&#038;|&)(?:paged|page)=(\d+)"', h1)}
    max_page = max(pages) if pages else 1
    items1, d1 = _count_yb_last_dates(h1)
    started = _parse_date_yb(d1)
    if max_page <= 1:
        return {"chapter_total": items1, "started": started, "description": desc}
    try:
        hl = fetch(f"{base}/?cat={cat_id}&paged={max_page}", timeout=timeout)
    except Exception:
        return {"chapter_total": (max_page - 1) * 300 + items1, "started": "", "description": desc}
    items_last, dlast = _count_yb_last_dates(hl)
    started = _parse_date_yb(dlast)
    return {"chapter_total": (max_page - 1) * 300 + items_last, "started": started, "description": desc}


def _parse_date_yb(s):
    """yaruobook entry-date '2026年9月2日' → 'YYYY-MM-DD'。"""
    m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""


# ---------------- 主搜索 ----------------

def search(args):
    kw = args.keyword or ""
    by_cid, by_url, by_title = local_registry_index()
    tags = args.tag or []           # 可重复多标签（r401 标签名 AND 交集）
    results = []                    # 主记录（作品级）
    r18_set = {}                    # r401 R18 cid 集合（懒加载）

    def load_r18():
        nonlocal r18_set
        if not r18_set:
            r18_set = r401_r18_cid_set(force=bool(getattr(args, "no_cache", False)))
        return r18_set

    # ---- 无关键词浏览模式：--tag / --author 直接作为结果源 ----
    browse_mode = not kw

    # 各源并行（关键词搜索）
    def r401_work():
        return r401_search(kw, chapters=False)
    def r401_chap():
        return r401_search(kw, chapters=True)
    def yb_com():
        return yaruobook_search("com", kw, max_pages=2)
    def yb_jp():
        return yaruobook_search("jp", kw, max_pages=2)
    def yb_net():
        return yaruobook_search("net", kw, max_pages=2)
    sites = {"com": yb_com, "jp": yb_jp, "net": yb_net}

    tasks = []
    if args.site == "r401":
        tasks = [r401_work, r401_chap]
    elif args.site in sites:
        tasks = [sites[args.site]]
    elif not browse_mode:
        tasks = [r401_work, r401_chap, yb_com, yb_jp, yb_net]

    # --tag：r401 标签（可重复，多标签 AND 交集；无关键词 = 纯标签浏览）
    #   keyword 模式：tag 是过滤器——关键词结果 ∩ tag cid 集（tag 全量不进 results）
    #   browse 模式：tag 是结果源——各 tag 全量直接作为结果
    tag_tids = []
    if tags:
        tmap = r401_tag_list()
        for t in tags:
            tid = tmap.get(t, "")
            if not tid:
                print(f"[warn] r401 无标签 {t!r}（可用: {', '.join(list(tmap)[:12])}…）")
            else:
                tag_tids.append(tid)
        if browse_mode and tag_tids:
            tasks = []  # 浏览模式只从 tag 拉
            for tid in tag_tids:
                # R-18表現含む(tid 26) 优先读 r18 缓存（2854 全量，避免 72 页重拉丢页）
                if tid == "26":
                    tasks.append(_tag26_browse)
                else:
                    tasks.append((lambda tid: lambda: r401_tag_all(tid))(tid))
        elif args.site in sites and tag_tids:
            print("[warn] --tag 仅对 r401 有效，单站 yaruobook 搜索忽略 tag")

    # --author：作者线（只搜作者，不混关键词）
    author_mode = bool(args.author) and not browse_mode
    if args.author:
        tasks = []
        aliases = author_aliases()
        names = [args.author]
        for k, v in aliases.items():
            if args.author in (k, *v.get("马甲", [])):
                names = [k] + list(v.get("马甲", []))
                break
        for nm in names:
            tasks.append((lambda n: lambda: r401_author_search(n))(nm))
        if tag_tids:
            tasks.append((lambda tid: lambda: r401_tag_all(tid))(tag_tids[0]))
        author_mode = True

    from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout
    ex = ThreadPoolExecutor(max_workers=min(8, len(tasks) or 1))
    try:
        futs = {ex.submit(fn): fn.__name__ for fn in tasks}
        if len(tasks) > 1:
            try:
                for f in as_completed(futs, timeout=360):
                    try:
                        results.extend(f.result() or [])
                    except Exception:
                        pass
            except FutureTimeout:
                pass
        else:
            for f in futs:
                try:
                    results.extend(f.result() or [])
                except Exception:
                    pass
    finally:
        ex.shutdown(wait=False)

    # 按源独立聚合（r401 / yaruobook 独立 key）
    agg = {}
    for r in results:
        if r.get("cid"):
            key = f"r401:{r['cid']}"
        elif r.get("cat_id"):
            key = f"yb:{r['site']}:{r['cat_id']}"
        else:
            key = f"misc:{norm_title(r.get('title', ''))}"
        a = agg.setdefault(key, {"title": r.get("title", ""), "site": r.get("site", ""),
                                 "cid": r.get("cid", ""), "cat_id": r.get("cat_id", ""),
                                 "source": "r401" if r.get("cid") else "yaruobook",
                                 "chapters": [], "date": r.get("date", ""),
                                 "chapter_count": 0, "sites": set()})
        if r.get("title") and not a["title"]:
            a["title"] = r["title"]
        if r.get("site"):
            a["sites"].add(r["site"])
        if r.get("site") and not a["site"]:
            a["site"] = r["site"]
        if r.get("chapter"):
            c = r["chapter"]
            if (c.get("eid"), c.get("url")) not in {(x.get("eid"), x.get("url")) for x in a["chapters"]}:
                a["chapters"].append(c)
        for c in r.get("chapters", []) or []:
            cc = {"eid": c.get("eid", ""), "url": "", "ctitle": c.get("ctitle", "")}
            if (cc["eid"], cc["ctitle"]) not in {(x.get("eid"), x.get("ctitle")) for x in a["chapters"]}:
                a["chapters"].append(cc)
        if r.get("chapter_count"):
            a["chapter_count"] = max(a["chapter_count"], r["chapter_count"])
        if r.get("date") and r["date"] > a["date"]:
            a["date"] = r["date"]
        if r.get("author"):
            a["author"] = r["author"]

    # --tag 过滤（r401 按 cid；keyword 模式：结果 ∩ tag cid 集；browse 单 tag 已全集无需过滤）
    #   浏览模式多 tag：并集已聚合 → 交集过滤；keyword 模式任意 tag 数：结果必须 ∈ 交集
    if tag_tids and not (browse_mode and len(tag_tids) == 1):
        _tag_works_cache = {}
        tag_sets = []
        for tid in tag_tids:
            if tid not in _tag_works_cache:
                _tag_works_cache[tid] = _tag26_browse() if tid == "26" else r401_tag_all(tid)
            tag_sets.append({w["cid"] for w in _tag_works_cache[tid]})
        inter = set.intersection(*tag_sets) if len(tag_sets) > 1 else (tag_sets[0] if tag_sets else set())
        # 结果裁剪：r401 记录 cid ∈ inter（yaruobook 不受 tag 影响）
        agg = {k: a for k, a in agg.items()
               if not (a.get("cid") and a["cid"].split(",")[0] not in inter)}
        if not inter:
            print(f"[warn] 多标签交集为空（各 tag 独立命中: {[len(s) for s in tag_sets]}）")

    # 组装 out（含标题命中/本地标注/R18 预判）
    out = []
    rank_cids = {}
    if args.sort == "pop":
        try:
            for i, r in enumerate(fetch_rank("week"), 1):
                rank_cids[r["cid"]] = i
        except Exception:
            rank_cids = {}
    for key, a in agg.items():
        loc, how, wid = is_local(a, by_cid, by_url, by_title)
        title_hit = bool(kw) and kw in a["title"]
        # 标题关键词表分类（yaruobook 零成本预判；r401 的 R18 用 tag26 权威）
        cls = title_classify(a["title"]) if a["source"] == "yaruobook" else {}
        # r401 R18 标注：懒加载 tag26 集合（缓存命中快；仅当搜索含 r401 记录时）
        r18v = None
        if a["source"] == "r401" and a.get("cid"):
            try:
                r18v = a["cid"].split(",")[0] in load_r18()
            except Exception:
                r18v = None
        # 作品页 URL（标题链接用）：r401=categories/<cid>；yaruobook=分类页 ?cat=<cat_id>
        if a["source"] == "r401" and a.get("cid"):
            url = f"{R401}/yaruo/categories/{a['cid'].split(',')[0]}"
        elif a["source"] == "yaruobook" and a.get("cat_id"):
            site_k = (a.get("site") or "yaruobook.net").replace("yaruobook.", "")
            base_k = YARUOBOOK.get(site_k, YARUOBOOK["net"])
            url = f"{base_k}/?cat={a['cat_id']}"
        else:
            url = ""
        out.append({"key": key, "title": a["title"], "site": a["site"],
                    "sites": sorted(a["sites"]), "cid": a["cid"], "cat_id": a["cat_id"],
                    "source": a["source"], "url": url,
                    "chapters": a["chapters"][:5], "chapter_count": len(a["chapters"]) or a.get("chapter_count", 0),
                    "date": a["date"], "author": a.get("author", ""), "local": loc, "local_how": how,
                    "work_id": wid, "rank": rank_cids.get(a.get("cid", ""), 9999),
                    "title_hit": title_hit, "classify": cls, "r18": r18v,
                    "chapter_total": 0, "started": "", "started_after_hit": False,
                    "tags": []})

    # --since/--until：最新更新日期预过滤（聚合后、总数抓取前，零成本）
    if getattr(args, "since", None) or getattr(args, "until", None):
        lo = _date_int(getattr(args, "since", None) or "")
        hi = _date_int(getattr(args, "until", None) or "")
        keep = []
        for x in out:
            d = _date_int(x["date"])
            if lo and d < lo:
                continue
            if hi and d > hi:
                continue
            keep.append(x)
        out = keep

    # r18 / no-r18 过滤（r401 走 tag26 权威集合已标注 x[r18]；yaruobook 走标题词，简介二次在后面）
    r18_opt = getattr(args, "r18", False) or (getattr(args, "no_r18", False))
    if r18_opt:
        want_r18 = bool(getattr(args, "r18", False)) and not getattr(args, "no_r18", False)
        keep = []
        for x in out:
            if x["source"] == "r401":
                isr = x.get("r18") is True
                if isr == want_r18:
                    keep.append(x)
            else:
                # yaruobook 标题词判定（简介二次在总数抓取后）
                cls = x.get("classify") or {}
                isr = "R18" in cls and cls["R18"] == "标题"
                x["r18"] = isr
                if isr == want_r18:
                    keep.append(x)
        out = keep

    # 章节总数 + 开坑日期 + 简介（并发 8 路；--no-count 关闭；com 站跳过预算内超时）
    if not getattr(args, "no_count", False):
        out = _fetch_counts(out, args)

    # 简介二次判定（yaruobook R18/安価：总数抓取后才获得简介）
    if not getattr(args, "no_count", False):
        removed = 0
        keep = []
        for x in out:
            if x["source"] == "yaruobook":
                cls = dict(x.get("classify") or {})
                desc = x.get("description", "")
                sub = title_classify(x["title"], desc)
                for k, src in sub.items():
                    if src == "简介":
                        cls[k] = src
                x["classify"] = cls
                x["tags"] = sorted(cls.keys())
                # 二次过滤：r18/no-r18 遇简介判定 R18
                if getattr(args, "r18", False) and not getattr(args, "no_r18", False):
                    isr = "R18" in cls
                    if not isr:
                        removed += 1
                        continue
                if getattr(args, "no_r18", False):
                    if "R18" in cls and x["r18"] is None:  # 标题没判出，简介判出
                        removed += 1
                        continue
                x["r18"] = "R18" in cls
            else:
                x["tags"] = sorted(cls.keys())
            keep.append(x)
        out = keep
        if removed:
            print(f"[info] 简介判定后剔除 {removed} 条（R18/安価 二次过滤）")

    # --started-after：仅标注（开坑晚于该日期 → started_after_hit=True），不裁剪
    if getattr(args, "started_after", None):
        sa = _date_int(args.started_after)
        if getattr(args, "no_count", False):
            print("[warn] --started-after 需 --no-count 关闭才有开坑日期，标注忽略")
        else:
            for x in out:
                d = _date_int(x.get("started", ""))
                if d and sa and d >= sa:
                    x["started_after_hit"] = True

    # r401 组内按规范化标题归并（同标题多 cid）
    merged = {}
    for x in out:
        if x["source"] == "r401":
            nk = norm_title(x["title"])
            if nk in merged:
                m = merged[nk]
                m["cid"] = (m["cid"] + "," + x["cid"]) if m["cid"] else x["cid"]
                m["chapter_count"] = max(m["chapter_count"], x["chapter_count"])
                m["chapter_total"] = max(m["chapter_total"], x["chapter_total"])
                m["date"] = max(m["date"], x["date"], key=lambda d: _date_key(d))
                m["sites"] = sorted(set(m["sites"]) | set(x["sites"]))
                if not m["started"] and x.get("started"):
                    m["started"] = x["started"]
                continue
            merged[nk] = x
        else:
            merged[x["key"]] = x
    out = list(merged.values())

    # 排序：标题命中靠前 → 未收录在前(找新作品) → 日期倒序/标题
    if args.sort == "pop":
        out.sort(key=lambda x: (not x["title_hit"], x["local"], x["rank"]))
    else:
        out.sort(key=lambda x: (not x["title_hit"], x["local"], -_date_key(x["date"]), x["title"]))
    return out


def _fetch_counts(results, args):
    """对最终结果并发抓 章节总数/开坑/简介（8 路）。com 站结果独立预算：超时整体放弃该站。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout

    def job(x):
        if x["source"] == "r401":
            cid = x["cid"].split(",")[0]
            try:
                c = count_r401(cid)
                return x, c
            except Exception:
                return x, {}
        else:
            site = (x["site"] or "").replace("yaruobook.", "")
            try:
                c = count_yb(site, x["cat_id"])
                return x, c
            except Exception:
                return x, {}

    n = len(results)
    if n == 0:
        return results
    # com 站结果慢预算：com 单请求 150s，8 并发下整体上限放宽到 240s
    has_com = any(x["source"] == "yaruobook" and "com" in x.get("site", "") for x in results)
    budget = 240 if has_com else 120
    ex = ThreadPoolExecutor(max_workers=8)
    try:
        futs = {ex.submit(job, x): x for x in results}
        done = {}
        try:
            for f in as_completed(futs, timeout=budget):
                try:
                    x, c = f.result()
                    done[id(x)] = (x, c)
                except Exception:
                    pass
        except FutureTimeout:
            pass
    finally:
        ex.shutdown(wait=False)
    out = []
    n_ok = 0
    for x in results:
        if id(x) in done:
            _x, c = done[id(x)]
            if c.get("chapter_total"):
                x["chapter_total"] = c["chapter_total"]
                n_ok += 1
            if c.get("started"):
                x["started"] = c["started"]
            if c.get("description"):
                x["description"] = c["description"]
        out.append(x)
    print(f"[info] 章节总数抓取完成 {n_ok}/{n} 条")
    return out


def _date_int(s):
    """'YYYY-MM-DD'/'2026年9月2日' → 整数 20260902；空/无法解析 → 0。"""
    s = (s or "").strip()
    if not s:
        return 0
    m = re.match(r"(\d{4})[年-](\d{1,2})[月-](\d{1,2})", s)
    if m:
        return int(m.group(1)) * 10000 + int(m.group(2)) * 100 + int(m.group(3))
    return 0


def _date_key(d):
    """日期排序键（兼容 _date_int）。"""
    return _date_int(d)


def print_results(results, args):
    """表格输出：按源分组（r401 / yaruobook.com/jp/net），组内标题命中靠前。"""
    # 分组
    groups = {}
    for r in results:
        src = r["source"]
        if src == "yaruobook":
            site = r["site"] or "-"
            key = f"yaruobook {site}"
        else:
            key = "r401"
        groups.setdefault(key, []).append(r)
    total_loc = sum(1 for r in results if r["local"])
    print(f"共 {len(results)} 条（本地已收录 {total_loc}）— r401 与 yaruobook 独立统计")
    for gname in sorted(groups, key=lambda g: (g != "r401", g)):
        grp = groups[gname]
        n_loc = sum(1 for r in grp if r["local"])
        n_hit = sum(1 for r in grp if r["title_hit"])
        print(f"\n── {gname}（{len(grp)} 条，标题命中 {n_hit}，本地已收 {n_loc}）──")
        print(f"{'状态':<6}{'标题命中':<8}{'日期':<12}{'总章数':<8}{'标题'}")
        for r in grp:
            st = "✓已收" if r["local"] else "   "
            hit = "●标题" if r["title_hit"] else "    "
            ct = r.get("chapter_total") or r.get("chapter_count") or 0
            line = f"{st:<6}{hit:<8}{r['date']:<12}{ct:<8}{r['title']}"
            print(line)
            if r["chapters"]:
                shown = r["chapters"][:3]
                for c in shown:
                    print(f"        └ {c.get('ctitle', '')}")


# ---------------- 二次搜索（refine）：对报告内 yaruobook 作品逐个搜指定关键词 ----------------

def parse_report_works(path):
    """解析搜索报告 md → [{site, cat_id, title}]（仅 yaruobook 作品）。

    从表格行提取链接 `[标题](https://yaruobook.<站>/?cat=<cat_id>)`。
    """
    works = []
    try:
        html = open(path, encoding="utf-8").read()
    except OSError as e:
        raise SystemExit(f"无法读报告 {path}: {e}")
    for m in re.finditer(r'\[([^\]]+)\]\((https://yaruobook\.(com|jp|net)/\?cat=(\d+))\)', html):
        title, url, _site, cat = m.group(1), m.group(2), m.group(3), m.group(4)
        site = f"yaruobook.{_site}"
        if not any(w["cat_id"] == cat and w["site"] == site for w in works):
            works.append({"site": site, "cat_id": cat, "title": title, "url": url})
    return works


def _refine_work_search(work, kw):
    """对单个作品做分类限定全文搜索：?s=<kw>&cat=<cat_id> → 命中章节。"""
    site = work["site"].replace("yaruobook.", "")
    base = YARUOBOOK[site]
    timeout = YB_TIMEOUT.get(site, 180)
    url = f"{base}/?s={urllib.parse.quote(kw)}&cat={work['cat_id']}"
    hits = []
    for page in range(1, 3):  # 300/页，作品内命中通常 1 页够；翻到 2 页防超大量
        u = url + (f"&paged={page}" if page > 1 else "")
        try:
            html = fetch(u, timeout=timeout)
        except Exception:
            break
        cards = YB_CARD_RE.findall(html)
        if not cards:
            break
        for _eid, ctitle, c_cat, _cn in cards:
            if c_cat == work["cat_id"]:
                hits.append(ctitle.strip())
        pages_in = {int(x) for x in re.findall(r'href="[^"]*?paged=(\d+)"', html)}
        if (page + 1) not in pages_in:
            break
    return hits


def cmd_refine(argv):
    """二次搜索：对报告内 yaruobook 作品逐个搜关键词。

    用法: python scripts/tools/aa_search.py refine <报告.md> <关键词> [--json out.json]
    产出: <报告>_二次<关键词>_<时间>.md（每作品标注命中章节数+前 N 章名单）
    """
    import argparse, datetime
    ap = argparse.ArgumentParser(prog="aa_search.py refine", description="对搜索报告内 yaruobook 作品逐个二次搜索关键词")
    ap.add_argument("report", help="已生成的搜索报告 md 路径")
    ap.add_argument("kw", help="二次搜索关键词（如 響）")
    ap.add_argument("--json", help="机读输出到文件")
    args = ap.parse_args(argv)

    works = parse_report_works(args.report)
    if not works:
        print("[warn] 报告内未解析到 yaruobook 作品（refine 仅支持 yaruobook 作品二次搜索）")
        return
    print(f"[info] 报告 {args.report} → {len(works)} 个 yaruobook 作品，逐个搜「{args.kw}」")

    # 并发逐作品搜索（按站分类，最多 8 并发）
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_refine_work_search, w, args.kw): w for w in works}
        for f in as_completed(futs):
            w = futs[f]
            try:
                hits = f.result()
            except Exception:
                hits = []
            results.append({**w, "hits": hits, "hit_count": len(hits)})

    hits_total = sum(r["hit_count"] for r in results)
    print(f"[info] 完成：{sum(1 for r in results if r['hit_count'])}/{len(results)} 作品命中「{args.kw}」，共 {hits_total} 章")
    results.sort(key=lambda r: (-r["hit_count"], r["title"]))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"kw": args.kw, "results": results}, f, ensure_ascii=False, indent=1)
        print(f"JSON: {args.json}")

    # 生成细化报告
    base_name = os.path.splitext(os.path.basename(args.report))[0]
    safe = re.sub(r'[\\/:*?"<>|\s]', "_", args.kw)[:30]
    out = os.path.join(os.path.dirname(os.path.abspath(args.report)),
                       f"{base_name}_二次{safe}_{datetime.datetime.now():%Y%m%d_%H%M}.md")
    L = []
    L.append(f"# 二次搜索报告：{base_name} → 「{args.kw}」\n")
    L.append(f"- 时间：{datetime.datetime.now():%Y-%m-%d %H:%M}")
    L.append(f"- 二次关键词：「{args.kw}」")
    L.append(f"- 源报告：{os.path.basename(args.report)}")
    L.append(f"- 范围：{len(results)} 个 yaruobook 作品，{sum(1 for r in results if r['hit_count'])} 个命中，共 {hits_total} 章\n")
    L.append("## 命中的作品（按命中章节数倒序）\n")
    L.append("| # | 作品 | 站 | %s 命中 | 命中章节(前5) |" % args.kw)
    L.append("|---|------|----|--------|--------------|")
    n = 0
    for r in results:
        if not r["hit_count"]:
            continue
        n += 1
        site = r["site"].replace("yaruobook.", "")
        cts = "、".join(r["hits"][:5]) + ("…" if r["hit_count"] > 5 else "")
        L.append(f"| {n} | [{r['title']}]({r['url']}) | {site} | {r['hit_count']} | {cts} |")
    L.append(f"\n共 {n} 个作品命中「{args.kw}」")
    # 未命中的作品简述（可选，避免信息丢失）
    miss = [r["title"] for r in results if not r["hit_count"]]
    if miss:
        L.append(f"\n未命中 {len(miss)} 个：{'、'.join(miss)}")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))
    print(f"细化报告: {out}")


# ---------------- CLI ----------------

def main():
    # 子命令先行分派（ranking / aliases / refine 不带 keyword）
    if len(sys.argv) > 1 and sys.argv[1] == "refine":
        cmd_refine(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "ranking":
        site = "com"
        period = "week"
        if "--site" in sys.argv:
            site = sys.argv[sys.argv.index("--site") + 1]
        if "--period" in sys.argv:
            period = sys.argv[sys.argv.index("--period") + 1]
        rank = fetch_rank(period)
        print(f"r401 人气榜（{period}，{len(rank)} 条）:")
        for i, r in enumerate(rank[:30], 1):
            print(f"  {i:>3}. [{r['cid']}] {r['title']}")
        return
    if len(sys.argv) > 1 and sys.argv[1] == "aliases":
        data = load_json(AUTHORS_JSON, {"作者": {}})
        if "--promote" in sys.argv:
            n = promote_aliases()
            print(f"aliases: 转正 {n} 条候选")
            return
        print("作者马甲表:")
        for k, v in data.get("作者", {}).items():
            print(f"  {k}: 马甲={v.get('马甲', [])} trip={v.get('trip', [])}")
        cands = data.get("candidates", [])
        if cands:
            print(f"\n候选 {len(cands)} 条（编辑 search_authors.json 设 确认:true 后 --promote 转正）:")
            for c in cands:
                print(f"  ? {c.get('正名')} ({c.get('说明', '')})")
        return

    ap = argparse.ArgumentParser(prog="aa_search.py", description="AA 漫画搜索（r401 + yaruobook 3 站全集）")
    ap.add_argument("keyword", nargs="?", help="搜索关键词（作品名/章节名）；省略且带 --tag/--author = 纯标签浏览")
    ap.add_argument("--author", help="作者线：aliases 展开全部马甲多路查询")
    ap.add_argument("--tag", action="append", default=[], help="标签过滤（r401 标签名，可重复多标签 AND，如 オリジナル/R-18表現含む）")
    ap.add_argument("--site", choices=["r401", "com", "jp", "net"], help="限定站点（默认 4 源全搜）")
    ap.add_argument("--r18", action="store_true", help="仅 R18（r401 tag R-18表現含む 权威集合 / yaruobook 标题+简介关键词）")
    ap.add_argument("--no-r18", action="store_true", help="排除 R18（与 --r18 互斥）")
    ap.add_argument("--since", help="最新更新日期下限 YYYY-MM-DD（含）")
    ap.add_argument("--until", help="最新更新日期上限 YYYY-MM-DD（含）")
    ap.add_argument("--started-after", dest="started_after", help="开坑日期下限 YYYY-MM-DD（仅标注，不裁剪）")
    ap.add_argument("--no-count", action="store_true", help="不抓章节总数/开坑日期/简介（快模式）")
    ap.add_argument("--no-cache", action="store_true", help="强刷 1 天 TTL 缓存（tag26 R18 集合等）")
    ap.add_argument("--sort", choices=["date", "pop"], default="date", help="排序（pop=按榜单人气加权）")
    ap.add_argument("--web", action="store_true",
                    help="（v1 不做：tavily 探针无增益——公开 web 返回角色 wiki 词条而非结构化作品数据，已砍）")
    ap.add_argument("--json", help="机读输出到文件（可直接喂 dl_runner/添加作品）")
    ap.add_argument("--report", nargs="?", const="__auto__",
                    help="输出 markdown 报告；不带值 → doc/aa搜索报告/<关键词>_<YYYYMMDD_HHMM>.md 自动落盘")
    args = ap.parse_args()

    # 无关键词须有 --tag/--author（浏览模式）
    if not args.keyword and not args.tag and not args.author:
        ap.print_help()
        return
    if args.r18 and args.no_r18:
        ap.error("--r18 与 --no-r18 互斥")

    results = search(args)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"query": args.keyword or "", "results": results}, f, ensure_ascii=False, indent=1)
        print(f"JSON: {args.json}")
    if args.report:
        if args.report == "__auto__":
            import datetime
            os.makedirs(os.path.join(BASE, "doc", "aa搜索报告"), exist_ok=True)
            seg = args.keyword or ("tag-" + "-".join(args.tag or [])) or ("author-" + (args.author or ""))
            safe = re.sub(r'[\\/:*?"<>|\s]', "_", seg)[:60]
            args.report = os.path.join(BASE, "doc", "aa搜索报告", f"{safe}_{datetime.datetime.now():%Y%m%d_%H%M}.md")
        write_report(results, args, args.report)
    if not args.json and not args.report:
        print_results(results, args)


def write_report(results, args, path):
    """markdown 报告：按源分组（r401 / yaruobook 三站），组内标题命中靠前，列出全部作品。

    表格列：标题/站点/分类/章节总数/最新日期/开坑/已收录/R18/标题命中/开坑晚于。
    """
    import datetime
    groups = {}
    for r in results:
        src = r["source"]
        key = "r401" if src == "r401" else f"yaruobook {r['site']}"
        groups.setdefault(key, []).append(r)
    total_loc = sum(1 for r in results if r["local"])
    # 头部搜索条件
    conds = []
    if args.keyword:
        conds.append(f"关键词「{args.keyword}」")
    if getattr(args, "tag", None):
        conds.append("标签: " + "+".join(args.tag))
    if getattr(args, "since", None):
        conds.append(f"更新≥{args.since}")
    if getattr(args, "until", None):
        conds.append(f"更新≤{args.until}")
    if getattr(args, "started_after", None):
        conds.append(f"开坑≥{args.started_after}（仅标注）")
    if getattr(args, "r18", False):
        conds.append("仅 R18")
    if getattr(args, "no_r18", False):
        conds.append("排除 R18")
    if args.site:
        conds.append(f"站点 {args.site}")
    L = []
    L.append(f"# AA 搜索报告：{args.keyword or '(浏览)'}\n")
    L.append(f"- 时间：{datetime.datetime.now():%Y-%m-%d %H:%M}")
    L.append(f"- 条件：{('，'.join(conds)) if conds else '无'}")
    L.append(f"- 总条数：{len(results)}（本地已收录 {total_loc}）")
    L.append("- 说明：r401 与 yaruobook 独立统计不合并；「标题命中」= 关键词在作品标题（排前）；"
             "分类/R18：r401 走 tag 权威集合，yaruobook 走标题+简介关键词（简介级标「推定」）；"
             "开坑晚于 = 仅标注不裁剪\n")
    for gname in sorted(groups, key=lambda g: (g != "r401", g)):
        grp = groups[gname]
        n_loc = sum(1 for r in grp if r["local"])
        n_hit = sum(1 for r in grp if r["title_hit"])
        L.append(f"\n## {gname}（{len(grp)} 条，标题命中 {n_hit}，本地已收 {n_loc}）\n")
        L.append("| # | 标题 | 分类/R18 | 命中/总章数 | 最新日期 | 开坑 | 已收录 | 标题命中 | 开坑晚于 |")
        L.append("|---|------|---------|-----------|---------|------|--------|---------|---------|")
        for i, r in enumerate(grp, 1):
            site = ",".join(r["sites"]) if r.get("sites") else (r["site"] or "-")
            loc = f"✓ {r['work_id']}" if r["local"] else "—"
            hit = "●" if r["title_hit"] else ""
            sa = "★" if r.get("started_after_hit") else ""
            # 标题（作品页链接）
            t = r["title"]
            if r.get("url"):
                t = f"[{t}]({r['url']})"
            # 分类/R18 列
            cats = []
            if r["source"] == "yaruobook":
                for k, src in (r.get("classify") or {}).items():
                    cats.append(k if src == "标题" else f"{k}(推定)")
            if r.get("r18") is True:
                cats.append("R18●")
            cats = ", ".join(cats) or "—"
            # 命中/总章数列：chapter_count=搜索命中章节数（正文/章节级），chapter_total=作品总章数
            # 标题命中但无章节命中（r401 categories 搜索）→ 显示「标题/N」
            hitn = r.get("chapter_count") or 0
            tot = r.get("chapter_total") or 0
            if hitn:
                ct = f"{hitn}/{tot}" if tot else str(hitn)
            elif r.get("title_hit"):
                ct = f"标题/{tot}" if tot else "标题"
            else:
                ct = str(tot) if tot else "—"
            started = r.get("started") or "—"
            L.append(f"| {i} | {t} | {cats} | {ct} | {r['date']} | {started} | {loc} | {hit} | {sa} |")
        # 章节明细（前 3 章 + 省略号）
        L.append("")
        for r in grp:
            if r["chapters"]:
                chs = "、".join(c.get("ctitle", "") for c in r["chapters"][:3])
                more = "…" if r["chapter_count"] > 3 else ""
                L.append(f"- {r['title']}（{r['chapter_count']}章）：{chs}{more}")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))
    print(f"报告: {path}")


if __name__ == "__main__":
    main()
