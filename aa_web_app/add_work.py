# -*- coding: utf-8 -*-
"""作品添加/下载功能：URL 解析 → 作品信息生成 → 注册。

2026-08-30 新增（方案经 grilling 确认）：
- 单输入框粘贴分类页 URL（yaruobook 三站 / rss.r401.net），按域名自动分派解析器
- yaruobook 系：yaruobook_index.fetch_all_pages → 章节清单 + 简介（category-page-content）
- r401 系：r401_index.fetch_all_pages → 章节清单（exlink 跨站）；简介通常没有
- r401 exlink 域名不在已支持解析器列表 → 中断流程提示手动处理
- 简介：**默认不翻译**（2026-09-20 用户要求，日文原样入库）；手动翻译走 POST /api/translate-intro
  → translate_desc() → call_api（失败回退原文 + 标注）

用法（作为库被 main.py 调用）：
    from aa_web_app import add_work
    info = add_work.parse_url("https://yaruobook.com/?cat=377")
    info = add_work.parse_url("https://rss.r401.net/yaruo/categories/15829")
"""
import os
import re
import sys
import json
import urllib.request

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
_COMMON = os.path.join(_SCRIPTS, "common")
_WORKS = os.path.join(_SCRIPTS, "works")
for _p in (_COMMON, _WORKS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---- 站点域名 → 解析器/注册 site 名映射（registry.py 的 site 字段）----
# yaruobook 三站同构，site 名统一用 "yaruobook"（registry 现有条目同）
YARUOBOOK_DOMAINS = {
    "yaruobook.com": "com",
    "yaruobook.jp": "jp",
    "yaruobook.net": "net",
    "yaruobookshelf.jp": "com",   # 本棚别名（saki 等）
}
# r401 exlink 可指向的已支持站点域名 → (site 名, url_pattern 域名片段)
SUPPORTED_EXLINK_DOMAINS = {
    "yaruobook.com": "yaruobook.com",
    "yaruobook.jp": "yaruobook.jp",
    "yaruobook.net": "yaruobook.net",
    "yaruonichijou.com": "yaruonichijou.com",
    "yaruomatomex.com": "yaruomatomex.com",
    "yaruohiroba.com": "yaruohiroba.com",
    "himanatokiniyaruo.com": "himanatokiniyaruo.com",
    "yaruo-matome.com": "yaruo-matome.com",
    "touhouyaruosure.com": "touhouyaruosure.com",
    "yaruobook.blog.fc2.com": "yaruobook.blog.fc2.com",
    "yaruok.blog.fc2.com": "yaruok.blog.fc2.com",
    "iitokolo.blog.fc2.com": "iitokolo.blog.fc2.com",
    "mukankei769.blog114.fc2.com": "mukankei769.blog114.fc2.com",
    "yarumo.blog83.fc2.com": "yarumo.blog83.fc2.com",
    "rusalka777.blog.fc2.com": "rusalka777.blog.fc2.com",
    "man001.blog.2nt.com": "man001.blog.2nt.com",
    "aamatome.blog.2nt.com": "aamatome.blog.2nt.com",
    "matomemo3.blog.2nt.com": "matomemo3.blog.2nt.com",
    "yaruocheck.jp": "yaruocheck.jp",
    "yaruomanyoshu.com": "yaruomanyoshu.com",
    "yaruo-manyoshu.com": "yaruo-manyoshu.com",
    "yomuaa.r401.net": "yomuaa.r401.net",
}
# exlink 域名 → 解析器 site 名（r401 系 registry 条目按此推断 site；默认 yaruobook 兜底）
EXLINK_TO_SITE = {
    "yaruobook.com": "yaruobook", "yaruobook.jp": "yaruobook", "yaruobook.net": "yaruobook",
    "yaruonichijou.com": "yaruonichijou",
    "yaruomatomex.com": "yaruomatomex",
    "yaruohiroba.com": "yaruohiroba",
    "himanatokiniyaruo.com": "himanatokiniyaruo",
    "yaruo-matome.com": "yaruo_matome",
    "touhouyaruosure.com": "touhouyaruosure",
    "yaruobook.blog.fc2.com": "yaruobook",
    "yaruok.blog.fc2.com": "fc2",
    "iitokolo.blog.fc2.com": "iitokolo_fc2",
    "mukankei769.blog114.fc2.com": "mukankei_fc2",
    "yarumo.blog83.fc2.com": "yarumo_fc2",
    "rusalka777.blog.fc2.com": "rusalka_fc2",
    "man001.blog.2nt.com": "blog_2nt",
    "aamatome.blog.2nt.com": "aamatome_2nt",
    "matomemo3.blog.2nt.com": "matomemo3",
    "yaruocheck.jp": "yaruocheck",
    "yaruomanyoshu.com": "manyoshu",
    "yaruo-manyoshu.com": "manyoshu",
    "yomuaa.r401.net": "yomuaa",
}

# 章节卡片 URL → eid 提取（兼容 ?p=123 / blog-entry-123.html / /chapters/123 / /archives/123）
EID_RE = re.compile(r"(?:[?&]p=|blog-entry-|/chapters/|/archives/)(\d+)")


def _noop(*a, **k):
    pass


def _domain_of(url):
    m = re.search(r"https?://([^/]+)", url)
    return m.group(1).lower() if m else ""


# 站名前缀：yaruobook 分类页 <title> 是「やる夫スレ本棚2号館 | 作品名」或「やる夫スレ本棚 | 作品名」——剥掉
_SITE_PREFIX_RE = re.compile(r"^(?:やる夫スレ本棚[^ |]*?|やる夫スレ本棚)\s*[|｜]\s*")


def _clean_title(t):
    """剥站名前缀 + 清理空白。"""
    t = (t or "").strip()
    t = _SITE_PREFIX_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _cat_id_from(url):
    """从分类页 URL 提取分类 id。支持 ?cat=377 / /categories/377 / /yaruo/categories/377"""
    m = re.search(r"(?:[?&]cat=|/categories/)(\d+)", url)
    if not m:
        m = re.search(r"/yaruo/categories/(\d+)", url)
    return m.group(1) if m else None


def parse_url(url):
    """解析分类页 URL → 作品信息 dict。

    返回: {ok, kind: "yaruobook"|"r401"|"error", title, description, chapters,
           site, r401_cid, entries, error}
    chapters = [{eid, title, url, date}]
    """
    url = url.strip()
    domain = _domain_of(url)
    cat_id = _cat_id_from(url)
    if not cat_id:
        return {"ok": False, "kind": "error",
                "error": "无法从网址识别分类 ID。请粘贴作品分类页网址（如 yaruobook.com/?cat=377 或 rss.r401.net/yaruo/categories/15829）"}

    # ---- yaruobook 三站 ----
    if domain in YARUOBOOK_DOMAINS:
        site_key = YARUOBOOK_DOMAINS[domain]
        import yaruobook_index as Y
        Y.BASE = Y.SITES[site_key]
        try:
            entries, desc = Y.fetch_all_pages(cat_id, log=_noop)
        except SystemExit as e:
            return {"ok": False, "kind": "error", "error": str(e)}
        # 标题：第一页 <title>（分类页含作品名）
        title = f"cat_{cat_id}"
        try:
            html = Y.fetch(Y.category_url(cat_id))
            title = Y.parse_category(html).get("title", title)
        except Exception:
            pass
        title = _clean_title(title)
        # 章节清单（原站顺序：分类页倒序 → 反转）
        entries = list(reversed(entries))
        chapters = [_entry_to_chapter(e) for e in entries]
        return {"ok": True, "kind": "yaruobook", "title": title,
                "description": desc, "entries": entries, "chapters": chapters,
                "site": "yaruobook", "r401_cid": None, "site_key": site_key}

    # ---- r401 ----
    if domain in ("rss.r401.net", "r401.net", "www.r401.net"):
        import r401_index as R
        try:
            entries = R.fetch_all_pages(cat_id, log=_noop)
        except SystemExit as e:
            return {"ok": False, "kind": "error", "error": str(e)}
        if not entries:
            return {"ok": False, "kind": "error", "error": "r401 分类页无章节（分类 ID 可能不对或站点未收录）"}
        # 标题：第一页 <h2 class=item-list-h2>
        title = f"cat_{cat_id}"
        try:
            html = R.fetch(f"{R.BASE}/yaruo/categories/{cat_id}")
            title = R.parse_category(html).get("title", title)
        except Exception:
            pass
        title = _clean_title(title)
        # exlink 域名校验：全部条目域名必须在已支持列表
        bad = set()
        for e in entries:
            u = e.get("url", "")
            d = _domain_of(u)
            if d and d not in SUPPORTED_EXLINK_DOMAINS:
                bad.add(d)
        if bad:
            return {"ok": False, "kind": "error",
                    "error": "该作品章节在新网站 %s，解析器未覆盖，请手动处理"
                             % "、".join(sorted(bad))}
        chapters = [_entry_to_chapter(e) for e in entries]
        # 2026-08-31 修复：r401 分类页倒序（最新在前），必须反转成正序（旧→新）——
        # 与 yaruobook 分支一致（list(reversed())）；否则 chapters.json 存倒序，
        # dl_runner 读缓存（chapters_source=cache）不再反转 → 目录/顺序全反
        entries = list(reversed(entries))
        chapters = [_entry_to_chapter(e) for e in entries]
        return {"ok": True, "kind": "r401", "title": title,
                "description": "", "entries": entries, "chapters": chapters,
                "site": None, "r401_cid": cat_id, "site_key": None}

    return {"ok": False, "kind": "error",
            "error": f"不支持的网站 {domain or '(空)'}。支持 yaruobook 三站（com/jp/net）与 rss.r401.net"}


def _entry_to_chapter(e):
    """entries 条目 → 统一章节 dict（含 url）。"""
    return {"eid": e.get("eid"), "title": e.get("title", ""),
            "url": e.get("url", ""), "date": e.get("date", "")}


def _clean_desc(html_text):
    """简介 HTML → 纯文本（去标签、压缩空白）。"""
    text = re.sub(r"<[^>]+>", "", html_text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:2000]


def translate_desc(desc, work_r18=False):
    """简介日文 → 中文。失败回退原文 + 标注。

    返回 (中文, 是否成功)。

    2026-09-20 实测（commandcode `xiaomi/mimo-v2.5`）：模型 3 次里 2 次把主角名保留成日文
    （「やる夫穿越到异世界…」），旧 `_looks_japanese` 见假名即判未翻译 → 整段回退日文原文
    （用户看到的"简介翻译失败"）。判据已放宽为「几乎还是日文才算失败」（见 _looks_japanese）。"""
    if not desc or not desc.strip():
        return "", True
    try:
        from aa_translate import call_api
        system = "你是日文作品简介翻译。把用户发来的日文简介翻译成简体中文：自然、通顺、准确，不要添加解释，不要输出日文假名。"
        out = call_api(system, desc.strip()[:1500], work_r18=work_r18)
        out = (out or "").strip()
        if out and not _looks_japanese(out, desc):
            return out, True
    except Exception:
        pass
    return _clean_desc(desc), False


def _looks_japanese(s, src=None):
    """粗略判断「译文基本还是日文」（翻译失败的兜底判断）。

    2026-09-20 修：旧判据是 `re.search(r"[\\u3040-\\u30ff]", s)`——只要译文里有**任意一个假名**
    就判失败，于是正常译文中**保留的专名**（やる夫/ドラえもん/ミク）会让整段简介被丢弃回退日文。
    现判据：① 与原文完全相同（未翻译）；② 假名 ≥15 个 或 假名占非空白字符 ≥30%（大段日文）。
    """
    t = (s or "").strip()
    if not t:
        return False
    if src is not None and t == (src or "").strip():
        return True
    n = len(re.sub(r"\s", "", t))
    if not n:
        return False
    kana = len(re.findall(r"[\u3040-\u30ff]", t))
    return kana >= 15 or (kana / n) >= 0.3


def build_registry_entry(work_id, info, cn_title, author, status, r18_level, intro_cn):
    """按解析结果构建 registry 条目 dict。

    yaruobook 系: chapters_source="cache"（dl_runner 读 work/chapters.json）
    r401 系: r401_cid（dl_runner 实时拉清单）
    """
    import registry
    base = registry.BASE
    d = os.path.join(base, "aaManga", work_id)
    # r401 系按 exlink 域名推断解析器 site（如 iitokolo.blog.fc2.com → iitokolo_fc2）；
    # 否则默认 yaruobook 兜底（历史行为）
    site = info.get("site")
    if not site and info.get("kind") == "r401":
        dom = _domain_of((info.get("entries") or [{}])[0].get("url", "")) if info.get("entries") else ""
        site = EXLINK_TO_SITE.get(dom, "yaruobook")
    entry = {
        "site": site or "yaruobook",
        "title": cn_title or info.get("title") or work_id,
        "aa_dir": os.path.join(d, "aa"),
        "out_dir": os.path.join(d, "汉化"),
        "work_dir": os.path.join(d, "work"),
        "raw_dir": os.path.join(d, "raw"),
        "name_style": "entry_eid",
        "r18": r18_level in ("high", "low"),
        "url_pattern": "",
        "min_size": 50000,
        "label_style": "plain",
        "html_css": "legacy",
        # 交付物是**文件夹**（2026-10-06 起）：gen_aa_html 写 <此路径>/index.html + data/ + 源 txt.gz
        "html_out": os.path.join(base, "aaManga", "交付HTML", f"{work_id}_汉化合集"),
        "html_title": f"{cn_title} 汉化合集",
        "source_note": f"来源：{info.get('site') or 'r401'} | 翻译：机翻 + 清理 | 帖头颜色保留原站",
    }
    if info.get("kind") == "r401":
        entry["r401_cid"] = info["r401_cid"]
        # 章节 URL 以 exlink 为准（跨站兼容），url_pattern 留空走条目自带 url
        entry["url_pattern"] = ""
        # FC2 R18 站（mukankei769 等）需先过年龄门（dl_chapters dl_age_gate 分支）
        if site == "mukankei_fc2":
            entry["dl_age_gate"] = True
        # touhouyaruosure：正文不在章节页 HTML 里，需二次请求 /api/chapters/<id>/content 注入
        # （用普通 dl_chapters 只拿到 JS skeleton → raw 全是一样大的空壳、aa 提取 0 章）
        if site == "touhouyaruosure":
            entry["dl_type"] = "api_inject"
            entry["min_size"] = 100000
    else:
        entry["chapters_source"] = "cache"
        # yaruobook 系 URL：按站点域名
        site_host = {"com": "yaruobook.com", "jp": "yaruobook.jp", "net": "yaruobook.net"}[info.get("site_key", "com")]
        entry["url_pattern"] = f"https://{site_host}/?p={{eid}}"
    return entry


def save_chapters_json(work_dir, chapters):
    """章节清单存 work/chapters.json（dl_runner chapters_source=cache 消费）。"""
    os.makedirs(work_dir, exist_ok=True)
    path = os.path.join(work_dir, "chapters.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"entries": chapters}, f, ensure_ascii=False, indent=1)
    return path


# ---------------------------------------------------------------- 更新检查（2026-08-31）

def _local_eids_of(cfg):
    """本地已下载章节 eid 集合：raw/ + aa/ 文件名里的数字（与 dl_runner._local_eids 同口径）。"""
    out = set()
    for d in (cfg.get("raw_dir"), cfg.get("aa_dir")):
        if not d or not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            m = re.search(r"(\d+)", fn)
            if m:
                out.add(m.group(1))
    return out


def fetch_remote_entries(cfg):
    """实时拉取原站章节清单（不读本地缓存）。

    返回 (entries, source_desc)；无可用源返回 (None, None)。
    entries = [{eid, title, url, date}]，原站顺序（正序）。

    2026-08-31 修正：site=yaruobook 的作品优先用 yaruobook 作品分类页
    （work/site_url.json 或从章节页解析），r401_cid 对 sujin_* 等是历史错误配置
    （指向别的作品/404）——r401 仅作兜底且需标题匹配校验。
    """
    work_dir = cfg.get("work_dir", "")

    def _from_site_url():
        """从 work/site_url.json（真实作品分类页）拉 yaruobook 清单。"""
        sp = os.path.join(work_dir, "site_url.json") if work_dir else ""
        if not sp or not os.path.exists(sp):
            return None
        try:
            site_url = json.load(open(sp, encoding="utf-8")).get("url") or ""
        except Exception:
            return None
        m = re.search(r"https://([^/]+)/\?cat=(\d+)", site_url)
        if not m:
            return None
        host, cat = m.group(1), m.group(2)
        site_key = YARUOBOOK_DOMAINS.get(host)
        if not site_key:
            return None
        import yaruobook_index as Y
        Y.BASE = Y.SITES[site_key]
        entries, _d = Y.fetch_all_pages(cat, log=_noop)
        return list(reversed(entries)), f"yaruobook {host} cat={cat}"

    # 1) yaruobook 系（含 sujin_* 等 site=yaruobook 但 r401_cid 错的）：作品分类页优先
    if cfg.get("site") == "yaruobook":
        r = _from_site_url()
        if r:
            return r
        # 无 site_url.json：解析作品分类页——从 chapters.json 首条 URL，或本地任一章节 eid 拼 url_pattern
        sp2 = os.path.join(work_dir, "site_url.json") if work_dir else ""
        if sp2:
            try:
                os.makedirs(work_dir, exist_ok=True)
                cp = os.path.join(work_dir, "chapters.json")
                url = ""
                if os.path.exists(cp):
                    try:
                        ent = json.load(open(cp, encoding="utf-8")).get("entries", [])
                        if ent:
                            url = (ent[0].get("url") or "").strip()
                    except Exception:
                        url = ""
                if not url:
                    # 本地任一章节 eid → url_pattern 拼原站 URL 解析面包屑
                    up = cfg.get("url_pattern") or ""
                    if up:
                        for fn in os.listdir(cfg.get("aa_dir") or ""):
                            m = re.search(r"(\d+)", fn)
                            if m:
                                url = up.replace("{eid}", m.group(1))
                                break
                if url:
                    cat = _find_yaruobook_cat_from_page(url)
                    if cat:
                        host = re.sub(r"https?://([^/]+)/.*", r"\1", url)
                        import yaruobook_index as Y
                        Y.BASE = Y.SITES[YARUOBOOK_DOMAINS.get(host, "com")]
                        entries, _d = Y.fetch_all_pages(cat, log=_noop)
                        try:
                            with open(sp2, "w", encoding="utf-8") as f:
                                json.dump({"url": f"https://{host}/?cat={cat}"}, f, ensure_ascii=False)
                        except Exception:
                            pass
                        return list(reversed(entries)), f"yaruobook {host} cat={cat}"
            except SystemExit:
                pass

    # 2) r401 兜底（非 yaruobook 系 / yaruobook 系解析失败）
    cid = cfg.get("r401_cid")
    if cid:
        import r401_index as R
        entries = R.fetch_all_pages(cid, log=_noop)
        if not entries:
            # r401 分类空（如 touhou 系 r401 未收录）——不算「已最新」，报无源
            return None, None
        return list(reversed(entries)), f"r401 分类 {cid}"
    return None, None


def _find_yaruobook_cat_from_page(article_url):
    """从 yaruobook 章节页面包屑解析作品专属分类 ?cat=N（走代理）。"""
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
        return None
    items = re.findall(
        r'itemListElement[^>]*>.*?<a href="[^"]*[?&]cat=(\d+)[^"]*"[^>]*>\s*<span[^>]*>(?:<span[^>]*>)?([^<]{1,60}?)(?:</span>)*</a>',
        html, re.S)
    if items:
        return items[-1][0]
    m2 = re.search(r"category-(\d+)-post", html)
    return m2.group(1) if m2 else None


def _fetch_page(cfg, page):
    """拉取单页分类页，返回 (entries最新在前, has_next, source_desc)。按作品源类型分发。"""
    # yaruobook 系
    if cfg.get("site") == "yaruobook":
        sp = os.path.join(cfg.get("work_dir", ""), "site_url.json")
        site_url = ""
        if sp and os.path.exists(sp):
            try:
                site_url = json.load(open(sp, encoding="utf-8")).get("url") or ""
            except Exception:
                site_url = ""
        m = re.search(r"https://([^/]+)/\?cat=(\d+)", site_url)
        if m:
            host, cat = m.group(1), m.group(2)
            site_key = YARUOBOOK_DOMAINS.get(host)
            if site_key:
                import yaruobook_index as Y
                Y.BASE = Y.SITES[site_key]
                html = Y.fetch(Y.category_url(cat, page))
                d = Y.parse_category(html)
                has_next = str(page + 1) in Y.PAGE_RE.findall(html)
                return d["entries"], has_next, f"yaruobook {host} cat={cat}"
    # r401 系
    cid = cfg.get("r401_cid")
    if cid:
        import r401_index as R
        url = f"{R.BASE}/yaruo/categories/{cid}" + (f"?page={page}" if page > 1 else "")
        html = R.fetch(url)
        d = R.parse_category(html)
        has_next = str(page + 1) in R.PAGE_RE.findall(html)
        return d["entries"], has_next, f"r401 分类 {cid}"
    return None, False, ""


def fetch_updates_paged(cfg, local_eids, max_pages=20):
    """逐页拉原站分类页，从最新往回扫，遇本地已有 eid 即停（不拉全部分页）。

    首页满页且全为新章时不提前停——继续拉后续页找本地 eid，
    拉完都没遇到 → 源变更（原站与本地 eid 完全不对应）。

    返回 (updates, meta)：
      updates = [{eid,title,url,date}] 原站顺序（旧→新）
      meta = {source, total_remote(已检查页数条目数), stopped_early, matched_any, error?}
    """
    updates = []            # 最新在前累积
    remote_seen = set()     # 已见 eid（跨页去重 + 完整性判断）
    stopped_early = False
    matched_any = False
    source = ""
    for page in range(1, max_pages + 1):
        entries, has_next, src = _fetch_page(cfg, page)
        if entries is None:
            return updates, {"source": source, "error": "无可用源"}
        source = src or source
        page_new = 0
        hit_local = False
        for e in entries:
            eid = str(e.get("eid") or "")
            if not eid or eid in remote_seen:
                continue
            remote_seen.add(eid)
            page_new += 1
            if eid in local_eids:
                # 遇到本地已有 → 该 eid 之后都是更旧章（本地下载过），整页停止
                hit_local = True
                stopped_early = True
                matched_any = True
                break
            updates.append(e)
        if hit_local or not has_next:
            break
        if page_new == 0:
            break   # 空页/重复页
    updates.reverse()       # 恢复原站顺序（旧→新）
    return updates, {"source": source, "total_remote": len(remote_seen),
                     "stopped_early": stopped_early, "matched_any": matched_any}


def check_updates(work, cfg=None):
    """对比原站清单与本地已下载，返回新章节列表（逐页拉取，遇已有即停）。

    返回 {ok, updates: [{eid,title,url,date}], total, local_count, already_latest, error?}
    """
    if cfg is None:
        import registry
        cfg = registry.WORKS.get(work, {})
    if not cfg:
        return {"ok": False, "error": f"未知作品 {work}"}
    local = _local_eids_of(cfg)
    try:
        updates, meta = fetch_updates_paged(cfg, local)
    except SystemExit as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"原站拉取失败: {e}"}
    if meta.get("error"):
        return {"ok": False, "error": meta["error"]}
    # 本地有章节但原站从未匹配到本地 eid（拉完全部页）→ 源可能已变更/站点迁移
    # （如 fukou 2nt→touhou 镜像、jikan_teishi r401 分类内容变化）。updates 即使非空
    # 也是「不同源」的章，不算真更新。
    if local and not meta.get("matched_any"):
        return {"ok": False, "error": "原站清单与本地章节不匹配（可能站点迁移/换源），请手动确认"}
    return {
        "ok": True,
        "updates": updates,
        "total": meta.get("total_remote", 0),
        "local_count": len(local),
        "already_latest": not updates,
        "source": meta.get("source", ""),
    }
