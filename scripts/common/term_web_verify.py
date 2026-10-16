# -*- coding: utf-8 -*-
"""候选联网核名（方案 doc/方案归档/候选联网核名与出处高亮方案_2026-10-15.md A 轴）。

做什么：`gen_glossary_ai --full` 出的候选只有「模型凭自身知识给的译名」，池式候选连**出处 IP**
都没有（实测 24 部作品 219 条候选：译名空 37%、缺作品 98%）→ 错译样本
`トップガン→壮志凌云`（其实是 `マヤノトップガン` 的片段）、`マヤ→玛雅`、`アーモンドアイ→空`。

本脚本对每个待确认候选跑**两轮检索**（决策 3）：
  第1轮 日文查询词 → 定**出处 IP**（Tavily 搜索摘要里 pixiv 百科/ニコニコ大百科/やる夫 Wiki 天然带出处）
  第2轮 中文查询词 → 取**通行中文译名**与**简称/愛称**（官方简中 / 中文维基 / 萌娘百科）
再让模型在**已抽出的候选里挑一个**（不让它自由生成——避免幻觉，见模块 doc `_adjudicate`）。

译名优先级（决策 4）：本仓库已有（roles.json / 其它作品表）> 官方简体中文 > 中文维基角色列表 >
萌娘百科；同一词多源不一致 → **不落定名**，状态记 `多源冲突`（候选窗并列各源，人工一眼选）。

落地（决策 5/6/9）：
  - 状态 `一致` → 写 `translation`/`作品` + `检索证据` + `确认: true`（自动确认；落库仍需点一次「写入作品表」）
  - `多源冲突` / `未命中` → 只写 `作品` + `检索证据`（译名为空时也预填第一优先级建议），**不自动确认**
  - 简称只写 `简称_of` **提示**，不自动落 `别名`（别名不注入翻译，见决策 5）

缓存（决策 7）：`doc/术语检索缓存.json`（仓库内全局单文件，跨作品复用；人工手改优先、脚本不覆盖）。
只搜缓存里缺的 term → 重跑/换部作品零成本。`TAVILY_API_KEY` 未配置时整体跳过（候选生成不受影响）。

用法：
  python scripts/common/term_web_verify.py --work <id> [--limit N] [--rounds 1|2] [--dry-run]
        [--no-model] [--force] [--terms a,b]
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_SCRIPTS)
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "works"),
           os.path.join(_SCRIPTS, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import aa_config                      # noqa: E402  （import 时载入 <仓库根>/.env）
import aa_backend                     # noqa: E402
import registry                       # noqa: E402

SCHEMA = "术语检索缓存 v1"
CACHE_PATH = os.path.join(_ROOT, "doc", "术语检索缓存.json")
TAVILY_URL = "https://api.tavily.com/search"
KANA_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff\uff61-\uff9f]")
HAN_RE = re.compile(r"[\u4e00-\u9fff]")
# 中文源分层：官方简中（B 站代理的官方 wiki / 官网）> 中文维基 > 萌娘百科
OFFICIAL_CN_DOMAINS = ("biligame.com", "gamekee.com", "umamusume.jp", "princessconnect",
                       "gamewith.jp", "gamerch.com")
WIKI_CN_DOMAINS = ("zh.wikipedia.org", "zh.m.wikipedia.org")
MOEGIRL_DOMAINS = ("zh.moegirl.org.cn", "mzh.moegirl.org.cn")
# 检索轮次查询词（决策 3 + 方案自定细节）
QUERIES_R1 = ("{term} 出典 キャラクター", "{term} ピクシブ百科事典")
QUERIES_R2 = ("{term} 中文名 译名", "{term} 愛称 通称")
STATES = ("一致", "多源冲突", "未命中", "人工定名")


# ---------------- 检索通道 ----------------

def tavily_key():
    """TAVILY_API_KEY（真实 env > 仓库根 .env，由 aa_config import 时载入）。"""
    return (os.environ.get("TAVILY_API_KEY") or "").strip()


def tavily_search(query, key, max_results=5, timeout=40, retries=2):
    """一次 Tavily 搜索。

    返回 `[results]`；**失败返回 None**（与"搜到 0 条"区分！）——调用方据此决定**不写缓存**：
    限流/网络错误被记成「未命中」会让该词永远不再重查（实测 432 限流把末尾 3 个词静默降级）。
    429/432（限流）与超时按 3s/9s 退避重试。
    """
    body = json.dumps({"api_key": key, "query": query, "max_results": int(max_results),
                       "search_depth": "basic", "include_answer": False}, ensure_ascii=False)
    req = urllib.request.Request(TAVILY_URL, data=body.encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    opener = aa_config.proxy_opener() or urllib.request.build_opener()
    last = None
    for attempt in range(int(retries) + 1):
        try:
            with opener.open(req, timeout=timeout) as f:
                data = json.loads(f.read().decode("utf-8", "replace"))
            break
        except urllib.error.HTTPError as e:                    # noqa: PERF203
            last = f"HTTP Error {e.code}"
            if e.code in (429, 432) and attempt < int(retries):
                time.sleep(3 * (3 ** attempt))                 # 3s → 9s
                continue
            print(f"[web-verify] 检索失败 {query!r}: {last}")
            return None
        except Exception as e:                                 # noqa: BLE001
            last = str(e)
            print(f"[web-verify] 检索失败 {query!r}: {last}")
            return None
    else:                                                      # pragma: no cover - 退避后仍失败
        print(f"[web-verify] 检索失败 {query!r}: {last}")
        return None
    out = []
    for r in (data.get("results") or []):
        out.append({"title": r.get("title") or "", "url": r.get("url") or "",
                    "content": (r.get("content") or "")[:1200], "query": query})
    return out


def collect_evidence(term, key, rounds=2, max_results=5, work_ip="", work_title=""):
    """跑两轮查询词，返回 `(证据列表, 失败查询数)`。

    `work_ip`（作品 IP，如「ウマ娘」）已知时**锚定查询词**——这一步是必要的：单词检索对
    片段名会给出**另一个 IP 的正确答案**（实测 `トップガン` 无锚定 → 搜到《壮志凌云》电影、
    出处=映画），锚定后才会指向本作真正用的那个角色。
    `rounds=1` 只跑第 1 轮（省 credit）。**失败查询数 > 0 且证据为空时调用方不得写缓存。**
    """
    t = term
    q1 = ([f"{t} 出典 キャラクター {work_ip}".strip()] if work_ip else []) + \
         [q.format(term=t) for q in QUERIES_R1]
    qs = [(q, 1) for q in q1]
    if int(rounds) >= 2:
        q2 = ([f"{t} 中文名 译名 {work_ip}".strip()] if work_ip else []) + \
             [q.format(term=t) for q in QUERIES_R2]
        qs += [(q, 2) for q in q2]
    ev, failed = [], 0
    for i, (q, rd) in enumerate(qs):
        if i:
            time.sleep(float(os.environ.get("AA_WEB_VERIFY_DELAY") or 0.4))   # 限流护栏
        got = tavily_search(q, key, max_results=max_results)
        if got is None:
            failed += 1
            continue
        for r in got:
            r["轮次"] = rd
            ev.append(r)
    return ev, failed


# ---------------- 作品 IP 锚定（先定本作用的那个 IP，再核名单词） ----------------

_WORKIP_SYSTEM = (
    "下面是一部日文 AA 漫画作品的标题与该作品的检索证据。请判断这部作品**取材自哪个外部作品/IP**"
    "（例如「ウマ娘 プリティーダービー」「ポケモン」「東方Project」）；若是原创无外部 IP，回答 null。\n"
    "同时给出该 IP 的**通行简体中文名**（例如 ウマ娘 → 闪耀！优俊少女/赛马娘），没有就给 null。\n"
    "**约束**：两个名字都必须**逐字出现在证据里**（引用原文写法，别翻译、别补全）。\n"
    "只输出一行 JSON：{\"ip\": \"…\"|null, \"ip_zh\": \"…\"|null, \"备注\": \"…\"}"
)


def detect_work_ip(work, title, key, max_results=5):
    """作品级 IP 探测：搜作品标题 → 模型从证据里挑 IP（必须逐字在证据中）。

    返回 (ip, ip_zh, evidence)。`ip_zh` 用于**排除**「把 IP 自己的中文名当成词条译名」
    （实测 ダイヤモンド 曾被核成「闪耀！优俊少女」——那是 IP 的中文名，不是这个词的译名）。
    """
    qs = [f"{title} 登場人物", f"{title} AA 原作 元ネタ"]
    ev = []
    for q in qs:
        got = tavily_search(q, key, max_results=max_results)
        if got:
            ev += got
    if not ev:
        return "", "", []
    body = json.dumps({"作品标题": title, "检索证据": [
        {"i": i, "t": e.get("title", ""), "u": e.get("url", ""), "c": e.get("content", "")[:600]}
        for i, e in enumerate(ev[:8])]}, ensure_ascii=False)
    ip, ip_zh = "", ""
    try:
        text, _ = aa_backend.call_with_fallback(False, _WORKIP_SYSTEM, body, max_tokens=300, timeout=60)
        m = re.search(r"\{.*\}", text or "", re.S)
        if m:
            j = json.loads(m.group(0))
            ip = (j.get("ip") or "").strip()
            ip_zh = (j.get("ip_zh") or "").strip()
    except Exception as e:                                     # noqa: BLE001
        print(f"[web-verify] 作品 IP 探测失败: {e}")
    # 逃生口：模型给的必须逐字出现在证据里（防幻觉）
    def _in_ev(v):
        return bool(v) and any(v in f"{e.get('title','')}\n{e.get('content','')}" for e in ev)
    if ip and not _in_ev(ip):
        print(f"[web-verify] 作品 IP {ip!r} 不在证据里 → 丢弃")
        ip = ""
    if ip_zh and not _in_ev(ip_zh):
        ip_zh = ""
    if ip:
        print(f"[web-verify] 作品 IP 锚定 = {ip!r}（中文名 {ip_zh or '-'}）（{work}）")
    return ip, ip_zh, ev


# ---------------- 纯函数（可单测） ----------------

def tier_of(url):
    """来源分层：0=日文源（定出处用）/1=官方简中/2=中文维基/3=萌娘/9=其它中文源。"""
    u = (url or "").lower()
    if any(d in u for d in OFFICIAL_CN_DOMAINS):
        return 1
    if any(d in u for d in WIKI_CN_DOMAINS):
        return 2
    if any(d in u for d in MOEGIRL_DOMAINS):
        return 3
    return 0


def _clean_name(s):
    """页面名 → 可能的译名：去括号补充、去站点后缀、去空白。"""
    s = (s or "").strip()
    s = re.split(r"\s*[-|｜–—]\s*", s)[0]                  # 「杏目 - 萌娘百科」
    s = re.sub(r"[（(][^）)]*[）)]$", "", s).strip()        # 「アーモンドアイ(ウマ娘)」
    return s.strip(" 　·、,，.。:：\"'「」『』")


def is_cn_name(s):
    """像中文译名：有汉字、无假名、长度 1~20。"""
    return bool(s) and 1 <= len(s) <= 20 and bool(HAN_RE.search(s)) and not KANA_RE.search(s)


JUNK_TITLE = ("一覧", "リスト", "カテゴリ", "索引", "まとめ", "目次", "検索", "結果")


def extract_candidates(term, evidence, ip_zh=""):
    """从证据里抽 **候选**（不让模型自由生成，模型只在候选里挑）。

    `ip_zh` = 本作 IP 的中文名（如「闪耀！优俊少女」）——**排除掉**，否则锚定检索会把
    IP 自己的中文名当成词条译名（实测 `ダイヤモンド` → 「闪耀！优俊少女」）。

    返回 {"出处":[{v,url,tier,ev_idx}], "译名":[{v,url,tier,ev_idx}], "简称":[{v,ev_idx}]}
    """
    t = (term or "").strip()
    出处, 译名, 简称 = [], [], []
    pat_paren = re.compile(re.escape(t) + r"\s*[（(]([^（()）\s]{2,20})[)）]")
    pat_ip = re.compile(r"[『「]([^』」\s]{2,20})[』」][のに](?:登場|キャラクター|登場人物)")
    pat_alias = re.compile(r"(?:愛称|通称|別名|呼び方|通称名)[は:：]?\s*[「『]?([^」』、。,\s]{1,12})")
    for i, e in enumerate(evidence):
        title = e.get("title", "")
        text = f"{title}\n{e.get('content','')}"
        url = e.get("url", "")
        tier = tier_of(url)
        # 话题守卫：这条证据得**跟这个词有关**（标题/正文/URL 里出现该词），否则它讲的是别的主题
        topic_ok = (t in title) or (t in text) or (t in urllib.parse.unquote(url))
        for m in pat_paren.finditer(title):
            v = _clean_name(m.group(1))
            if v and v != t and v != ip_zh:
                出处.append({"v": v, "url": url, "tier": tier, "ev": i})
        for m in pat_ip.finditer(text):
            v = _clean_name(m.group(1))
            if v and v != t and v != ip_zh:
                出处.append({"v": v, "url": url, "tier": tier, "ev": i})
        if tier >= 1 and topic_ok:
            v = _clean_name(title)
            if is_cn_name(v) and v != t and v != ip_zh and not any(k in v for k in JUNK_TITLE):
                译名.append({"v": v, "url": url, "tier": tier, "ev": i})
        for m in pat_alias.finditer(text):
            v = _clean_name(m.group(1))
            if is_alias_like(v) and v != t and v != ip_zh:
                简称.append({"v": v, "ev": i})
    return {"出处": _uniq(出处), "译名": _uniq(译名), "简称": _uniq(简称)}


def is_alias_like(s):
    """像「名字」的简称：片假名/汉字/字母数字（**不含平假名**）、1~12 字。

    实测放开平假名会把 `をしましたが`/`である`/`が存在します` 这类句子片段当简称（噪音）。
    """
    s = s or ""
    if not (1 <= len(s) <= 12):
        return False
    return bool(re.fullmatch(r"[\u30a0-\u30ff\u4e00-\u9fffA-Za-z0-9ー・]{1,12}", s))


def _uniq(rows, key="v"):
    seen, out = set(), []
    for r in rows:
        k = (r.get(key), r.get("url"))
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


# ---------------- 译名裁决（模型只在候选里挑） ----------------

_ADJ_SYSTEM = (
    "你在为日文 AA 漫画汉化做**术语核名**。给你：一个日文词、**本作锚定的外部 IP**、若干检索证据"
    "（标题/网址/摘要）。请只依据证据回答：\n"
    "1) **出处**：这个词在本作用的那个外部 IP（通常就是锚定 IP；若证据显示是别的作品/角色名，照证据写）。\n"
    "2) **译名**：该词对应的**通行简体中文名**——必须**逐字出现在你引用的那条证据**里（标题或摘要），"
    "照抄原文写法，不许自己翻译/补全/美化；证据里没有就给 null。\n"
    "3) **简称**：证据里给出的愛称/通称/简称（可多个；没有给空数组）。\n"
    "4) **依据**：你引用的证据序号（没有给 null）。\n"
    "只输出一行 JSON：{\"出处\":\"…\"|null,\"译名\":\"…\"|null,\"简称\":[],\"依据\":N|null,\"备注\":\"…\"}\n"
    "不要解释、不要多余文本。**宁缺勿错**：把握不足一律给 null。"
)


def _adjudicate(term, cands, evidence=None, work_ip="", timeout=90):
    """让链上模型**依据证据**作答（出处/译名/简称），返回 (pick, backend)。

    设计要点：模型可以自由引用证据里的名字（规则抽取太脆），但返回值会被 `validate_pick`
    **逐字回证据里核对**——不在证据里的一律丢弃（防幻觉），所以"宁可 null"。
    """
    if not (evidence or cands.get("出处") or cands.get("译名") or cands.get("简称")):
        return None, None
    payload = {
        "日文词": term,
        "本作锚定IP": work_ip or None,
        "检索证据": [{"i": i, "t": e.get("title", ""), "u": e.get("url", ""),
                      "c": (e.get("content") or "")[:400]} for i, e in enumerate(evidence or [])][:8],
        "已抽出的出处候选": [c["v"] for c in cands.get("出处", [])][:12],
    }
    try:
        text, backend = aa_backend.call_with_fallback(False, _ADJ_SYSTEM,
                                                      json.dumps(payload, ensure_ascii=False),
                                                      max_tokens=800, timeout=timeout)
    except Exception as e:                                     # noqa: BLE001
        print(f"[web-verify] 模型裁决失败 {term!r}: {e}")
        return None, None
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None, backend
    try:
        pick = json.loads(m.group(0))
    except ValueError:
        return None, backend
    return (pick if isinstance(pick, dict) else None), backend


def _ev_text(evidence, idx=None):
    """证据文本（idx=None → 全部拼接）。"""
    if idx is None:
        return "\n".join(f"{e.get('title','')}\n{e.get('content','')}" for e in (evidence or []))
    try:
        e = (evidence or [])[int(idx)]
    except (IndexError, TypeError, ValueError):
        return ""
    return f"{e.get('title','')}\n{e.get('content','')}"


def validate_pick(pick, cands, evidence=None, ip_zh="", work_ip=""):
    """把模型选择收敛到**证据字面**上（防幻觉）。

    - 出处：必须在证据或出处候选里逐字出现；若等于锚定 IP/其别名则直接采纳
    - 译名：必须逐字出现在**引用的那条证据**里（没给依据就全证据里找），且像中文名
      （有汉字、无假名、≤20 字）、不等于 IP 中文名
    - 简称：必须逐字出现在证据里且像名字（片假名/汉字/字母数字）
    """
    out = {"出处": None, "译名": None, "简称": [], "备注": ""}
    if not pick:
        return out
    ev_all = _ev_text(evidence)
    ip = (pick.get("出处") or "").strip()
    ip_cands = [c["v"] for c in (cands.get("出处") or [])]
    if ip and (ip in ip_cands or ip in ev_all or (work_ip and ip in work_ip)):
        out["出处"] = ip
    nm = (pick.get("译名") or "").strip()
    quoted = _ev_text(evidence, pick.get("依据"))
    if nm and is_cn_name(nm) and nm != ip_zh and (nm in quoted or nm in ev_all):
        out["译名"] = nm
    for s in (pick.get("简称") or []):
        s = str(s).strip()
        if is_alias_like(s) and s != ip_zh and s in ev_all:
            out["简称"].append(s)
    out["备注"] = str(pick.get("备注") or "")[:80]
    return out


def ip_matches(work_ip, cands, pick):
    """出处是否与本作 IP 一致（决策 4 的「单源一致」前提之一）。

    无锚定 IP → 视为一致（无法判断）；有锚定 → 出处候选/模型选的出处里必须能对上
    （相等、包含、或被包含：锚定「ウマ娘 プリティーダービー」对上出处「ウマ娘」）。
    """
    if not work_ip:
        return True
    vals = [c["v"] for c in (cands.get("出处") or [])]
    if pick.get("出处"):
        vals.append(pick["出处"])
    if not vals:
        return False
    for v in vals:
        if v and (v in work_ip or work_ip in v or v == work_ip):
            return True
    return False


def decide_status(term, pick, cands, work_ip=""):
    """状态与最终译名（决策 4/6）。

    - 有译名 + 出处与本作 IP 一致（或无锚定）+ 没有**不同字面**的其他译名 → `一致`
    - 有多个不同字面的译名，**或出处与本作 IP 不符** → `多源冲突`（取最高优先级者当「建议」，
      绝不自动确认——实测 `トップガン` 无锚定时会得到「壮志凌云/出处=映画」这种"另一个 IP 的
      正确答案"，正是要挡住的情形）
    - 什么都没有 → `未命中`
    """
    names = list(cands.get("译名") or [])
    uniq = []
    for n in names:
        if n["v"] not in [u["v"] for u in uniq]:
            uniq.append(n)
    uniq.sort(key=lambda x: x["tier"])                       # 分层优先
    ok_ip = ip_matches(work_ip, cands, pick)
    if pick.get("译名"):
        chosen = pick["译名"]
        others = [u["v"] for u in uniq if u["v"] != chosen]
        return ("一致" if (not others and ok_ip) else "多源冲突"), chosen
    if uniq:
        return ("一致" if (len(uniq) == 1 and ok_ip) else "多源冲突"), uniq[0]["v"]
    return "未命中", ""


# ---------------- 缓存（doc/术语检索缓存.json） ----------------

def load_cache(path=None):
    p = path or CACHE_PATH
    if not os.path.exists(p):
        return {"schema": SCHEMA, "更新": "", "词": {}}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("schema") != SCHEMA or not isinstance(d.get("词"), dict):
            return {"schema": SCHEMA, "更新": "", "词": {}}
        return d
    except (OSError, ValueError):
        return {"schema": SCHEMA, "更新": "", "词": {}}


def save_cache(cache, path=None):
    p = path or CACHE_PATH
    cache["schema"] = SCHEMA
    cache["更新"] = time.strftime("%Y-%m-%d %H:%M:%S")
    tmp = p + ".tmp"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, p)
    return p


def cache_skip(entry):
    """该词是否已有人工定名（脚本绝不覆盖，决策 7）。"""
    return bool(entry) and (entry.get("状态") == "人工定名" or entry.get("人工定名"))


# ---------------- 应用回候选文件 ----------------

def apply_entry(cand, entry, overwrite_trans=True):
    """把缓存条目写回候选（返回改动说明列表）。**不覆盖已有人工填写的作品字段**。"""
    notes = []
    st = entry.get("状态")
    if entry.get("出处") and not (cand.get("作品") or "").strip():
        cand["作品"] = entry["出处"]
        notes.append("作品")
    ev = entry.get("证据") or []
    if ev:
        cand["检索证据"] = {"状态": st, "来源": [{"t": e.get("title", ""), "u": e.get("url", "")}
                                              for e in ev[:5]], "时间": entry.get("时间", "")}
        if entry.get("建议") and entry.get("建议") != st:
            cand["检索证据"]["建议"] = entry["建议"]
        notes.append("检索证据")
    nm = (entry.get("译名") or "").strip()
    if nm:
        cur = (cand.get("translation") or "").strip()
        if not cur or (st == "一致" and overwrite_trans):
            if cur and cur != nm:
                cand.setdefault("检索证据", {})["原译名"] = cur
            cand["translation"] = nm
            notes.append("译名")
    if st == "一致" and (cand.get("translation") or "").strip() == nm and nm:
        if not cand.get("确认"):
            cand["确认"] = True
            notes.append("确认")
    if entry.get("简称"):
        cand["简称"] = list(entry["简称"])
        notes.append("简称")
    return notes


def mark_aliases(cands, cache):
    """简称 → `简称_of` 提示（决策 5/9：只当提示，不落别名、不注入翻译）。"""
    by_term = {c.get("term"): c for c in cands}
    n = 0
    for term, entry in (cache.get("词") or {}).items():
        for s in entry.get("简称") or []:
            tgt = by_term.get(s)
            if tgt is not None and not (tgt.get("简称_of") or "").strip():
                tgt["简称_of"] = term
                n += 1
    return n


# ---------------- 主流程 ----------------

def verify_work(work, limit=0, rounds=2, dry_run=False, use_model=True, force=False,
                only_terms=None, cache_path=None, max_results=5, work_ip=""):
    cfg = registry.get(work)
    wd = cfg["work_dir"]
    cand_p = os.path.join(wd, "glossary_candidates.json")
    if not os.path.exists(cand_p):
        print(f"[web-verify] 无候选文件: {cand_p}")
        return {"ok": False, "error": "无候选文件"}
    key = tavily_key()
    if not key:
        print("[web-verify] 未配置 TAVILY_API_KEY（真实 env 或 <仓库根>/.env）→ 跳过联网核名")
        return {"ok": False, "error": "无 TAVILY_API_KEY"}
    data = json.load(open(cand_p, encoding="utf-8"))
    cands = data.get("ai_候选") or []
    todo = [c for c in cands if not c.get("确认")]
    if only_terms:
        want = {t.strip() for t in only_terms if t.strip()}
        todo = [c for c in todo if (c.get("term") or "").strip() in want]
    cache = load_cache(cache_path)
    words = cache.setdefault("词", {})
    os.environ.setdefault("AA_USAGE_TAG", "term_web_verify")
    # 作品 IP 锚定（决策 4 的"单源一致"前提）：没有它，片段名会核到**另一个 IP 的正确名字**
    # （实测 トップガン → 壮志凌云/出处=映画）。--work-ip 可覆盖；探测结果进缓存复用。
    if not work_ip:
        work_ip = (cache.get("作品IP") or {}).get("值", "")
    ip_zh = (cache.get("作品IP") or {}).get("中文名", "") if not work_ip else ""
    if not work_ip and not dry_run:
        work_ip, ip_zh, wev = detect_work_ip(work, cfg.get("title") or work, key, max_results=max_results)
        if work_ip:
            cache["作品IP"] = {"值": work_ip, "中文名": ip_zh, "作品": work,
                               "证据": [{"title": e.get("title", ""), "url": e.get("url", "")}
                                        for e in wev[:5]],
                               "时间": time.strftime("%Y-%m-%d %H:%M:%S")}
            save_cache(cache, cache_path)
    if not work_ip:
        print("[web-verify] 无作品 IP 锚定（原创或无证据）——出处一致性无法校验，"
              "冲突判定会偏保守")
    picked, skipped, nfail = 0, 0, 0
    stat = {"一致": 0, "多源冲突": 0, "未命中": 0}
    for i, c in enumerate(todo, 1):
        term = (c.get("term") or "").strip()
        if not term:
            continue
        if limit and picked >= int(limit):
            break
        old = words.get(term)
        if cache_skip(old):
            skipped += 1
            continue
        if old and not force:
            entry = old
        else:
            ev, failed = collect_evidence(term, key, rounds=rounds, max_results=max_results,
                                          work_ip=work_ip)
            if failed and not ev:
                # 限流/网络错误**不得**记成「未命中」——那会让该词永远不再重查（实测 432 限流）
                print(f"[web-verify] {term}: 检索全失败 → 不写缓存，下次重跑会重试")
                nfail += 1
                continue
            cds = extract_candidates(term, ev, ip_zh=ip_zh)
            pick, backend = (None, None)
            if use_model:
                pick, backend = _adjudicate(term, cds, evidence=ev, work_ip=work_ip)
            pick = validate_pick(pick, cds, evidence=ev, ip_zh=ip_zh, work_ip=work_ip)
            st, nm = decide_status(term, pick, cds, work_ip=work_ip)
            entry = {"状态": st, "出处": pick.get("出处") or "",
                     "译名": nm, "简称": pick.get("简称") or [],
                     "作品IP": work_ip, "IP中文名": ip_zh,
                     "证据": [{"title": e.get("title", ""), "url": e.get("url", ""),
                               "tier": tier_of(e.get("url"))} for e in ev[:5]],
                     "后端": backend or "", "时间": time.strftime("%Y-%m-%d %H:%M:%S")}
            if st == "多源冲突":
                alts = sorted({x["v"] for x in (cds.get("译名") or [])} | ({nm} if nm else set()))
                entry["候选译名"] = alts[:6]
                if work_ip and not ip_matches(work_ip, cds, pick):
                    entry["建议"] = f"出处与本作 IP（{work_ip}）不符，须人工确认"
        words[term] = entry
        if not dry_run:
            save_cache(cache, cache_path)          # 逐词落盘（中断可续）
            if picked % 10 == 0:                   # 每 10 词也落一次候选文件（中断不白跑）
                with open(cand_p, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
        stat[entry.get("状态") or "未命中"] = stat.get(entry.get("状态") or "未命中", 0) + 1
        picked += 1
        if not dry_run:
            notes = apply_entry(c, entry)
        else:
            notes = ["dry-run"]
        print(f"[web-verify] {i}/{len(todo)} {term} → {entry.get('状态')} "
              f"出处={entry.get('出处') or '-'} 译名={entry.get('译名') or '-'} "
              f"简称={entry.get('简称') or '-'} {notes}")
    aliases = mark_aliases(cands, cache) if not dry_run else 0
    if not dry_run:
        with open(cand_p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        save_cache(cache, cache_path)
    print(f"[web-verify] {work}: 处理 {picked} 词（跳过人工定名 {skipped}，检索失败未缓存 {nfail}）"
          f" 一致 {stat.get('一致', 0)} / 冲突 {stat.get('多源冲突', 0)} / 未命中 {stat.get('未命中', 0)}"
          f" 简称提示 {aliases}  →  {cache_path or CACHE_PATH}")
    return {"ok": True, "处理": picked, "跳过": skipped, "检索失败": nfail,
            "统计": stat, "简称提示": aliases}


def main():
    ap = argparse.ArgumentParser(description="候选联网核名（Tavily 定出处 + 中文源取译名）")
    ap.add_argument("--work", required=True, help="作品 id（registry）")
    ap.add_argument("--limit", type=int, default=0, help="最多处理多少个词（0=全部待确认候选）")
    ap.add_argument("--rounds", type=int, default=2, choices=(1, 2), help="检索轮数（1=只定出处，省 credit）")
    ap.add_argument("--terms", default="", help="只处理这些词（逗号分隔）")
    ap.add_argument("--force", action="store_true", help="忽略缓存重新检索（人工定名仍跳过）")
    ap.add_argument("--no-model", action="store_true", help="不做模型裁决（纯规则抽取）")
    ap.add_argument("--work-ip", default="", help="作品 IP 锚定（如「ウマ娘」；不给则自动探测一次）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写缓存/候选文件")
    ap.add_argument("--cache", default="", help="缓存文件路径（默认 doc/术语检索缓存.json）")
    args = ap.parse_args()
    r = verify_work(args.work, limit=args.limit, rounds=args.rounds, dry_run=args.dry_run,
                    use_model=not args.no_model, force=args.force,
                    only_terms=(args.terms.split(",") if args.terms else None),
                    cache_path=(args.cache or None), work_ip=args.work_ip)
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
