# -*- coding: utf-8 -*-
"""揭示板（2ch 系 zeroczhan 改変板）dat 下载/更新 runner（2026-10-10）。

用途：镜像站（r401 / yaruobook 等）只收录正篇时，把**原始揭示板**里作者的全スレ
（本スレ / 雑談所）抓下来落盘 `揭示板/<目录>/<N>スレ目_<eid>.dat` + `index.json`，
供工作台 `/board` 阅读、以及正篇章节→贴 的定位。

用法（仓库根）：
    python scripts/runner/board_runner.py --board FOX --match ポケの子 \
        [--dir ◆LXwkncCAp] [--author ◆LXwkncCAp.] [--work pokeko_fc2] \
        [--title ポケの子（？）] [--kako-from 1745] [--kako-to 1775] \
        [--no-kako] [--scan-kako] [--force] [--dry-run] [--json out.json]

发现顺序（省流）：
    1. `<base>/subject.txt` = 活跃贴清单（`<eid>.dat<>标题 (res)`）；
    2. 本地 `index.json` 里已知 eid —— 贴变归档后 eid 不变，直接改抓 kako 路径
       （`kako/<eid前4>/<eid前5>/<eid>.dat`，**直接构造，勿信跳转页**）；
    3. `--scan-kako` / 本地无 index.json 时扫 kako 索引发现历史贴
       （`/kako/` → dir1 → dir2 → 表格行 `<td>KEY</td><td><a href="<eid>.html">标题</a></td><td>时间戳</td>`）。

站点/协议要点（实测，改前先看）：
- 本站（`yarufox.sakura.ne.jp/FOX`）是 zeroczhan 系 `0ch+ BBS`：正文 dat 在 `/dat/<eid>.dat`
  （活跃贴 200 可达）；已归档贴 read.cgi 只回 ~1.3KB 跳转页 → 正文在 `kako/`。
- **dat/index 全是 CP932**（`decode("cp932")`；UTF-8 必失败）。
- **必须顺序抓 + 间隔 0.2~0.5s**：该站并发会回 404/空体（实测 3~8 并发 dir2 页 100% 失败）。
  所有 HTTP 走磁盘缓存（`揭示板/.cache/<board>/`，默认 24h TTL）→ 重跑/断点续跑几乎零请求。
- スレ编号在标题里用假名拼数字（`そのよんじゅうななっ` = 47）：按 せん/ひゃく/じゅう **求和**，
  不能「遇单位就累乘」（`ひゃくじゅう` = 110 会被算成 1000）。
- 文件名 `<N>スレ目_<eid>.dat` 与 `index.json` 的 `slaves[]`（label/eid/res/bytes/ts/file）
  是工作台 `logic.board_slaves` 的既有口径，**勿改名**。

零 token；支持统一任务管理器协议（心跳/两档停止，退出码 41 = 用户停止）。
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRIPTS = os.path.join(ROOT, "scripts")
_COMMON = os.path.join(_SCRIPTS, "common")
for p in (_COMMON, _SCRIPTS):
    if p not in sys.path:
        sys.path.insert(0, p)

import task_report as TR          # 心跳/停止（未设 AA_TASK_HB 时全 no-op）

DEFAULT_HOST = "http://yarufox.sakura.ne.jp"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
DELAY = float(os.environ.get("AA_BOARD_DELAY", "0.3"))     # 每请求间隔（该站禁并发）
CACHE_TTL = float(os.environ.get("AA_BOARD_CACHE_TTL", "24")) * 3600

# 假名数字（标题「そのよんじゅうななっ」= 47）
_KANA_NUM = {
    "ぜろ": 0, "れい": 0, "いち": 1, "に": 2, "さん": 3, "よん": 4, "し": 4, "ご": 5,
    "ろく": 6, "なな": 7, "しち": 7, "はち": 8, "きゅう": 9, "く": 9,
    "じゅう": 10, "ひゃく": 100, "せん": 1000,
}
_KANA_UNITS = (1000, 100, 10)
_KAKO_LINK_RE = re.compile(r'/kako/(\d{4})/(\d{5})/(\d+)\.(?:html|dat)')
_KAKO_ROW_RE = re.compile(r'<td>(\d+)</td>\s*<td><a href="(\d+)\.html">(.*?)</a></td>\s*<td>(\d+)</td>')
# dir1 页列出 dir2（5 位）、/kako/ 页列出 dir1（4 位）——用通用数字目录正则，两级共用
_KAKO_DIR_RE = re.compile(r'href="(\d+)/">\1</a>')
_SUBJECT_RE = re.compile(r'^(\d+)\.dat<>(.*?)(?:\s*\((\d+)\))?\s*$')


def log(msg):
    print(msg, flush=True)


def hb(phase, idx=None, total=None, force=False):
    TR.heartbeat(phase=phase, idx=idx, total=total, force=force)


def _stop_check():
    if TR.check_stop():
        log("[task] 收到停止请求，在当前贴边界安全退出")
        raise SystemExit(TR.RC_STOPPED)


# ---------------------------------------------------------------- HTTP（顺序 + 磁盘缓存）

class Fetcher:
    """顺序 HTTP + 磁盘缓存（该站禁并发；缓存键 = url 的 sha1）。"""

    def __init__(self, cache_dir=None, ttl=CACHE_TTL, delay=DELAY):
        self.cache_dir = cache_dir
        self.ttl = ttl
        self.delay = delay
        self.hits = 0
        self.misses = 0
        self._last = 0.0
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    def _cache_path(self, url):
        key = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
        return os.path.join(self.cache_dir, key + ".bin")

    def raw(self, url):
        """返回 (bytes, status)；status=0 表示网络异常，404 = 不存在。"""
        cp = self._cache_path(url) if self.cache_dir else None
        if cp and os.path.exists(cp) and (time.time() - os.path.getmtime(cp)) < self.ttl:
            self.hits += 1
            return open(cp, "rb").read(), 200
        # 间隔（禁并发）
        wait = self.delay - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            import aa_config
            op = urllib.request.build_opener(urllib.request.ProxyHandler(aa_config.proxy_dict()))
        except Exception:
            op = urllib.request.build_opener()
        try:
            with op.open(req, timeout=60) as r:
                body = r.read()
                st = r.status
        except urllib.error.HTTPError as e:
            body, st = b"", e.code
        except Exception as e:
            log(f"  [net] {url} → {e}")
            body, st = b"", 0
        self._last = time.time()
        self.misses += 1
        if cp and st == 200:
            with open(cp, "wb") as f:
                f.write(body)
        return body, st

    def text(self, url):
        b, st = self.raw(url)
        return b.decode("cp932", errors="replace") if b else "", st


# ---------------------------------------------------------------- 标题/路径工具

def kana_num(title):
    """标题里的和数字假名 → int（`そのよんじゅうななっ` → 47）；解析不出返回 None。"""
    m = re.search(r'その([ぁ-ん]+)っ', title)          # に=2 这样 1 个假名的编号也要认
    if not m:
        return None
    s = m.group(1)
    total, pending = 0, None
    i = 0
    while i < len(s):
        hit = None
        for w, v in _KANA_NUM.items():           # 长词优先（じゅう > し）
            if s.startswith(w, i) and (hit is None or len(w) > len(hit[0])):
                hit = (w, v)
        if hit is None:                          # 未知假名（ふぁいなる 等）→ 放弃
            return None
        w, v = hit
        i += len(w)
        if v in _KANA_UNITS:
            total += (pending or 1) * v
            pending = None
        else:
            pending = v
    return total + (pending or 0)


def kako_url(base, eid):
    """归档贴直构造地址（`kako/<前4>/<前5>/<eid>`）。"""
    e = str(eid)
    return f"{base}/kako/{e[:4]}/{e[:5]}/{e}"


def _first_date(dat_text):
    """dat 第一行的投稿时间（`名前<>mail<>日期<>本文<>` 的第 3 段）。"""
    first = dat_text.split("\n", 1)[0]
    parts = first.split("<>")
    return parts[2].strip() if len(parts) > 2 else ""


def dat_res_count(dat_text):
    return len([ln for ln in dat_text.split("\n") if ln.strip()])


# ---------------------------------------------------------------- 发现

def parse_subject(text):
    """subject.txt → [{eid, title, res}]（活跃贴）。"""
    out = []
    for ln in text.split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        m = _SUBJECT_RE.match(ln)
        if m:
            out.append({"eid": m.group(1), "title": m.group(2).strip(),
                        "res": int(m.group(3)) if m.group(3) else 0})
    return out


def scan_kako(f, base, from_d1=None, to_d1=None, on_dir=None):
    """扫 kako 索引，返回 [{eid, title, ts}]（顺序抓 + 缓存）。"""
    html, st = f.text(f"{base}/kako/")
    if st != 200:
        log(f"  [kako] /kako/ 打不开 (status={st})")
        return []
    d1s = sorted({int(m.group(1)) for m in _KAKO_DIR_RE.finditer(html)})
    if from_d1:
        d1s = [d for d in d1s if d >= from_d1]
    if to_d1:
        d1s = [d for d in d1s if d <= to_d1]
    rows = []
    log(f"  [kako] dir1 {len(d1s)} 个：" + (f"{d1s[0]}~{d1s[-1]}" if d1s else "-"))
    for n, d1 in enumerate(d1s):
        h1, st1 = f.text(f"{base}/kako/{d1}/")
        if st1 != 200:
            continue
        d2s = sorted({int(m.group(1)) for m in _KAKO_DIR_RE.finditer(h1)})
        for d2 in d2s:
            h2, st2 = f.text(f"{base}/kako/{d1}/{d2}/")
            if st2 != 200:
                continue
            for _key, eid, title, ts in _KAKO_ROW_RE.findall(h2):
                rows.append({"eid": eid, "title": re.sub(r"<[^>]+>", "", title).strip(),
                             "ts": ts})
        if on_dir:
            on_dir(n + 1, len(d1s))
        hb("扫kako", n + 1, len(d1s))
        _stop_check()
    return rows


# ---------------------------------------------------------------- 抓单个贴（dat）

def fetch_thread(f, base, board, eid):
    """返回 (dat_text, src, url)；失败 (None, err, url)。"""
    b, st = f.raw(f"{base}/dat/{eid}.dat")
    if st == 200 and len(b) > 500:
        return b.decode("cp932", errors="replace"), "dat", f"{base}/dat/{eid}.dat"
    # 归档：直接构造 kako 路径（不依赖跳转页）
    for ext in ("dat", "html"):
        u = f"{kako_url(base, eid)}.{ext}"
        b2, st2 = f.raw(u)
        if st2 == 200 and len(b2) > 500:
            t = b2.decode("cp932", errors="replace")
            return (t, "kako-" + ext, u) if ext == "dat" else (kako_html_to_dat(t), "kako-html", u)
    # 跳转页里可能有确切链接（构造法失败时兜底）
    page, st3 = f.text(f"{base}/test/read.cgi/{board}/{eid}/")
    m = _KAKO_LINK_RE.search(page or "")
    if m:
        u = f"{base}/kako/{m.group(1)}/{m.group(2)}/{m.group(3)}." + ("dat")
        b3, st4 = f.raw(u)
        if st4 == 200 and len(b3) > 500:
            return b3.decode("cp932", errors="replace"), "kako-dat", u
    return None, f"fail(st={st})", f"{base}/dat/{eid}.dat"


_DATE_RE = re.compile(r'(\d{4}/\d{2}/\d{2}\([^)]*\)\s*\d{2}:\d{2}:\d{2}\s*(?:ID:\S+)?)')


def kako_html_to_dat(html):
    """kako `.html`（zeroczhan：`<dt>帖头</dt><dd>AA<br></dd>`）→ dat 文本。

    只用于没有 `.dat` 的极少数贴：产出与 dat 同形状（`名前<>mail<>日期<>本文<>`），
    日期从帖头里抠出来单独放第 3 段（`_first_date` 依赖它）。
    """
    out = []
    for m in re.finditer(r'<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)(?=<dt|</dl>|$)', html, re.S | re.I):
        head = re.sub(r"<[^>]+>", "", m.group(1))
        head = re.sub(r"\s+", " ", head.replace("&nbsp;", " ")).strip()
        dm = _DATE_RE.search(head)
        date = dm.group(1) if dm else ""
        body = re.sub(r"<br\s*/?>", "\n", m.group(2), flags=re.I)
        body = re.sub(r"<[^>]+>", "", body).replace("&nbsp;", " ").rstrip()
        out.append(f"{head}<>{head}<>{date}<>{body}<>")
    return "\n".join(out)


# ---------------------------------------------------------------- index.json

def load_index(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d.get("slaves"), list):
            return d
    except Exception:
        pass
    return {}


def existing_labels(dest):
    """目录里已有 `*.dat` 的 (label→eid, eid→label)（从文件名读，权威=文件名）。

    用途：① 已知 eid **保留原标签**（重编号会把同作者另一部作品的贴串号——实测事故）；
    ② 写入前查标签冲突（同名不同 eid = 两套编号并行 → 拒绝）。
    """
    l2e, e2l = {}, {}
    if not os.path.isdir(dest):
        return l2e, e2l
    for fn in os.listdir(dest):
        m = re.match(r'^(.*)_(\d+)\.dat$', fn)
        if not m:
            continue
        label, eid = m.group(1), m.group(2)
        l2e[label] = eid
        e2l[eid] = label
    return l2e, e2l


def foreign_dats(dest, keep_eids):
    """目录里不属于本次范围的 `.dat`（eid 不在 keep_eids）→ 防「往别作品的目录里写」。"""
    out = []
    if not os.path.isdir(dest):
        return out
    for fn in os.listdir(dest):
        m = re.match(r'^.*_(\d+)\.dat$', fn)
        if m and m.group(1) not in keep_eids:
            out.append(fn)
    return out


def index_dir_of(path):
    return os.path.dirname(os.path.abspath(path))


def save_index(path, meta, slaves):
    """写 index.json（slaves 按 eid 升序；保留未在本次范围内的旧条目）。"""
    slaves = sorted(slaves, key=lambda s: (str(s.get("eid", ""))))
    obj = dict(meta)
    obj["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    obj["slaves"] = slaves
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return obj


def res_and_ts(dat_text):
    return dat_res_count(dat_text), _first_date(dat_text)


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="揭示板 dat 下载/更新 runner")
    ap.add_argument("--board", default="FOX", help="板 id（如 FOX）")
    ap.add_argument("--host", default=DEFAULT_HOST, help="板站点根（默认 yarufox）")
    ap.add_argument("--base", default="", help="完整板根 URL（给了则忽略 --host/--board 拼接）")
    ap.add_argument("--match", default="", help="标题关键词（如 ポケの子）；空 = 不限")
    ap.add_argument("--author", default="", help="作者 trip（如 ◆LXwkncCAp.）：校验 dat 内确实有该 trip")
    ap.add_argument("--dir", dest="outdir", default="", help="揭示板子目录名（默认=作者，退化=关键词）")
    ap.add_argument("--title", default="", help="作品名（写 index.json 的 work_title）")
    ap.add_argument("--work", default="", help="作品 id（写 index.json，仅记录用）")
    ap.add_argument("--label", default="", help="板/连载标签（写 index.json range.label）")
    ap.add_argument("--kako-from", type=int, default=None, help="kako dir1 下界（eid 前 4 位）")
    ap.add_argument("--kako-to", type=int, default=None, help="kako dir1 上界")
    ap.add_argument("--no-kako", action="store_true", help="只抓活跃贴（不扫归档）")
    ap.add_argument("--scan-kako", action="store_true", help="强制扫 kako 索引（本地已有 index.json 时默认不扫）")
    ap.add_argument("--force", action="store_true", help="已存在的 .dat 也重抓")
    ap.add_argument("--allow-mixed", action="store_true",
                    help="允许往已有其它贴的目录里写（默认拒绝：同作者另一部作品要用另一个目录）")
    ap.add_argument("--dry-run", action="store_true", help="只发现不下载")
    ap.add_argument("--json", default="", help="把发现/下载结果写 json（供工作台/脚本消费）")
    a = ap.parse_args()

    base = (a.base or f"{a.host.rstrip('/')}/{a.board}").rstrip("/")
    outdir = a.outdir or a.author or a.match or a.board
    dest = os.path.join(ROOT, "揭示板", outdir)
    cache = os.path.join(ROOT, "揭示板", ".cache", a.board)
    idx_path = os.path.join(dest, "index.json")
    prev = load_index(idx_path)
    prev_by_eid = {str(s.get("eid")): s for s in prev.get("slaves", [])}

    f = Fetcher(cache_dir=cache)
    log(f"=== board_runner [{a.board}] base={base} → 揭示板/{outdir} ===")
    hb("发现", 0, 1, force=True)

    # 1) 活跃贴
    subj, st = f.text(f"{base}/subject.txt")
    active = parse_subject(subj) if st == 200 else []
    log(f"subject.txt: {len(active)} 贴（活跃）")
    cand = {t["eid"]: {"eid": t["eid"], "title": t["title"], "res": t["res"], "src": "active"}
            for t in active}

    # 2) 已知归档贴（index.json 里的 eid）
    for eid, it in prev_by_eid.items():
        cand.setdefault(eid, {"eid": eid, "title": it.get("title", ""), "res": it.get("res", 0),
                              "src": "known"})

    # 3) kako 索引（首次/强制；--no-kako 跳过）
    need_scan = (not a.no_kako) and (a.scan_kako or not prev_by_eid)
    if need_scan:
        log("扫 kako 索引（发现历史贴）…")
        for r in scan_kako(f, base, a.kako_from, a.kako_to):
            cand.setdefault(r["eid"], {"eid": r["eid"], "title": r["title"], "res": 0, "src": "kako"})
    elif a.no_kako:
        log("--no-kako：跳过 kako 索引")

    # 4) 过滤
    sel = []
    for eid, it in cand.items():
        title = it["title"]
        if a.match and it["src"] != "known" and a.match not in title:
            continue
        sel.append(it)
    sel.sort(key=lambda x: int(x["eid"]))
    log(f"候选 {len(cand)} 贴 → 命中 {len(sel)} 贴")
    if not sel:
        log("无命中贴（检查 --match / --kako-from~to）")

    # 5) 编号（标题假名 → N；已知 eid **保留原标签**，否则按命中顺序兜底）
    l2e, e2l = existing_labels(dest)
    for i, it in enumerate(sel, 1):
        if it["eid"] in e2l:                       # 已有贴：沿用原标签（勿重编号）
            it["label"] = e2l[it["eid"]]
            it["n"] = kana_num(it["label"]) or 0
        else:
            it["n"] = kana_num(it["title"]) or i
            it["label"] = f"{it['n']}スレ目"
    sel.sort(key=lambda x: (x["n"], int(x["eid"])))

    # 5b) 安全闸：① 目录里有不属于本次范围的贴 → 拒绝（防把同作者**另一部作品**的贴串号/覆盖）；
    #     ② 新标签与已有贴重名（不同 eid）→ 拒绝（两套编号并行 = 阅读顺序错乱）；
    #     ③ index.json 记的作品/关键词与本次不同 → 拒绝（同一作者多作品必须各占一个目录）
    keep = {it["eid"] for it in sel}
    fgn = foreign_dats(dest, keep)
    collision = []
    for it in sel:
        if it["eid"] in e2l:
            continue
        if it["label"] in l2e and l2e[it["label"]] != it["eid"]:
            collision.append(it)
    prev_title = str(prev.get("work_title") or "")
    prev_match = str(prev.get("match") or "")
    mismatch = (prev_match and a.match and prev_match != a.match) or \
               (prev_title and a.title and prev_title != a.title)
    if mismatch and not a.allow_mixed:
        log(f"!! 目录 揭示板/{outdir} 属于「{prev_title or prev_match}」，本次要写「{a.title or a.match}」——"
            f"同一作者的**另一部作品请用另一个目录**（建议 --dir <作者>_<作品短名>）。")
        log("   拒绝写入（确要混放加 --allow-mixed）。")
        return 2
    if fgn and not a.allow_mixed:
        log(f"!! 目录 揭示板/{outdir} 已有 {len(fgn)} 个不属于本次范围的贴（例：{fgn[0]}）——"
            f"同一作者的另一部作品要放**另一个目录**（建议 --dir <作者>_<作品短名>）。")
        log("   拒绝写入（确要混放加 --allow-mixed）。")
        return 2
    if collision and not a.allow_mixed:
        log(f"!! 编号冲突 {len(collision)} 贴（标签已被别的 eid 占用，例：{collision[0]['label']}）"
            f"——拒绝写入（换目录或加 --allow-mixed）。")
        return 2

    # 6) 抓 + 落盘
    os.makedirs(dest, exist_ok=True)
    slaves = list(prev.get("slaves", []))
    by_eid = {str(s.get("eid")): s for s in slaves}
    added = updated = skipped = failed = 0
    for i, it in enumerate(sel, 1):
        _stop_check()
        hb("下载", i, len(sel))
        eid, n, label = it["eid"], it["n"], it["label"]
        path = os.path.join(dest, f"{label}_{eid}.dat")
        if os.path.exists(path) and not a.force:
            dat = open(path, encoding="utf-8", errors="replace").read()
            it["note"] = "已有"
            skipped += 1
        else:
            if a.dry_run:
                log(f"  [{i}/{len(sel)}] {n}スレ目 {eid} {it['title'][:30]} (dry-run)")
                continue
            dat, src, url = fetch_thread(f, base, a.board, eid)
            if not dat:
                log(f"  [{i}/{len(sel)}] {label} {eid} 抓取失败（{src}）")
                it["note"] = src
                failed += 1
                continue
            if a.author and a.author not in dat:
                log(f"  [{i}/{len(sel)}] {label} {eid} 跳过：dat 内无 {a.author}")
                it["note"] = "作者不匹配"
                continue
            with open(path, "w", encoding="utf-8") as fp:
                fp.write(dat)
            it["src"] = src
            it["note"] = "ok"
            if eid in by_eid:
                updated += 1
            else:
                added += 1
        res, ts = res_and_ts(dat)
        it["res"] = it["res"] or res
        entry = {"label": label, "eid": eid, "title": it["title"],
                 "res": res, "bytes": os.path.getsize(path) if os.path.exists(path) else 0,
                 "ts": ts, "file": path, "url": f"{base}/dat/{eid}.dat",
                 "note": it.get("note", "ok"), "src": it.get("src", "")}
        if eid in by_eid:
            by_eid[eid].update(entry)
        else:
            slaves.append(entry)
            by_eid[eid] = entry
        hb("下载", i, len(sel), force=True)
    if not a.dry_run:
        labels = [s.get("label", "") for s in slaves]
        meta = dict(prev)
        meta.update({"work_title": a.title or prev.get("work_title", ""),
                     "work": a.work or prev.get("work", ""),
                     "board": f"{a.host.replace('http://', '').replace('https://', '')}/{a.board}",
                     "host": a.host, "board_id": a.board,
                     "match": a.match or prev.get("match", ""),
                     "author": a.author or prev.get("author", ""),
                     "dir": outdir,
                     "range": {"label": a.label or (prev.get("range") or {}).get("label", ""),
                               "chapter_range": f"{labels[0]}~{labels[-1]}" if labels else ""}})
        meta = {k: v for k, v in meta.items() if k != "slaves" and v is not None}
        save_index(idx_path, meta, slaves)
    log(f"完成：命中 {len(sel)} / 新增 {added} / 刷新 {updated} / 跳过(已有) {skipped} / 失败 {failed}")
    log(f"index: {idx_path}（{len(slaves)} 贴）  HTTP 缓存命中 {f.hits} / 实抓 {f.misses}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fp:
            json.dump({"board": a.board, "base": base, "dir": outdir, "matched": len(sel),
                       "added": added, "updated": updated, "skipped": skipped,
                       "failed": failed, "index": idx_path,
                       "slaves": [{k: v for k, v in s.items() if k != "file"} for s in slaves]},
                      fp, ensure_ascii=False, indent=1)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    _rc = 1
    try:
        _rc = main() or 0
    except SystemExit as e:
        _rc = e.code if isinstance(e.code, int) else 1
        raise
    finally:
        try:
            TR.finalize(_rc)
        except Exception:
            pass
