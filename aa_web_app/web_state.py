# -*- coding: utf-8 -*-
"""工作台共享底座（2026-10-14 自 main.py 拆出）：引导/路径常量/缓存与锁/通用工具
+ FastAPI app / 中间件 / 模板。**无依赖叶子**——routers/ 与 main.py 都 import 它。"""
# -*- coding: utf-8 -*-
"""AA 汉化矫正工作台：FastAPI 服务。

启动：双击仓库根 一键重启工作台.bat，或 python scripts/tools/aa_web.py
（自动打开浏览器 http://127.0.0.1:8765）
页面：/ 作品列表 · /work/<id> 章节 · /work/<id>/chapter/<文件> 行编辑器 · /glossary 术语表
"""


import importlib


import json


import os


import re


import subprocess


import sys


import threading


import time


import urllib.parse


_PKG = os.path.dirname(os.path.abspath(__file__))       # .../aa_web_app（与 scripts 同级）


_ROOT = os.path.dirname(_PKG)                            # 仓库根


_SCRIPTS = os.path.join(_ROOT, "scripts")


for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "works")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# 数据根（2026-10-09 代码根/数据根分离，方案 §5）：可写数据落数据根；AA_BASE 未设时 = 代码根。
import paths as _paths


_DATA = _paths.BASE


from aa_spawn import child_env, py_prefix   # 起子任务统一前缀（冻结 exe 走 --cli）


from aa_common import meta_load   # line_meta_v2 透明读（.json/.json.gz）


import registry


import work_pipeline as WP    # 2026-09-22 管线归属(方案 D11): 翻译入口按 pipeline 选链


from fastapi import FastAPI, HTTPException, Request


from fastapi.responses import FileResponse


from fastapi.staticfiles import StaticFiles


from fastapi.templating import Jinja2Templates


from pydantic import BaseModel


from aa_web_app import logic


from aa_web_app import reader_win            # 2026-09-21 阅读器窗口化：章节上下文 + 区间行渲染


from aa_web_app import task_manager as TM   # 2026-08-29 统一任务管理器


from aa_web_app import add_work             # 2026-08-30 作品添加/下载（URL 解析→注册→入队）


from aa_web_app import work_pack            # 2026-10-01 作品导入导出（rar 打包/解包）


from aa_web_app import index_cache          # 2026-10-13 首页快照（请求路径只读快照，见模块头）


from common import gen_aa_html as _gen_aa_html   # 渲染行（模块级 import，避免每行重复查找）


APP_DIR = _PKG


app = FastAPI(title="AA 汉化矫正工作台")


@app.middleware("http")
async def _no_store(request: Request, call_next):
    """本地单用户工具：所有响应禁用缓存，避免改模板/JS 后浏览器用旧快照。"""
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


app.mount("/static", StaticFiles(directory=os.path.join(APP_DIR, "static")), name="static")


templates = Jinja2Templates(directory=os.path.join(APP_DIR, "templates"))


templates.env.filters["timestamp"] = (
    lambda v: time.strftime("%Y-%m-%d %H:%M", time.localtime(v)) if v else "")


def _strip_r18_prefix(s):
    """剥掉作品标题开头的 R18 标记（【R-18】【R18】【R18G】等），R 以红标另示。"""
    import re as _re
    return _re.sub(r"^【\s*R[-－]?18G?\s*】", "", s or "").strip()


templates.env.filters["strip_r18"] = _strip_r18_prefix


def _sel_enabled():
    """划词翻译/朗读开关（设置页 `AA_SEL_ENABLE`，默认开）——模板/路由共用一处。"""
    try:
        from aa_web_app import selection as _SEL
        return bool(_SEL.sel_enabled())
    except Exception:
        return True


templates.env.globals["sel_enabled"] = _sel_enabled


@app.on_event("startup")
def _start_embedded_scheduler():
    """2026-08-29 统一任务管理器内嵌调度器线程(单例 pid 锁, 全局并行≤MAX)。daemon 不阻塞退出。"""
    threading.Thread(target=TM.scheduler_loop, kwargs={"embedded": True}, daemon=True).start()


_LOCK = threading.Lock()          # 文件写串行化（单进程多线程安全）


_STATS_CACHE = {}                 # work_id -> (fetched_at, stats)


# 冷重算全作品约 25s（250+ 文件 × 残留扫描），TTL 放大到 5 分钟；
# 保存/重新生成/回滚都会 invalidate_stats 对应作品，不会看到旧数据
_CACHE_TTL = 300


# 特殊生成器（2026-08-21 起改读 registry 的 generator 字段，消除双份维护；
# 无 generator 字段的作品统一走 common/gen_aa_html.py --work <id>）
def generator_of(work):
    cfg = registry.WORKS.get(work, {})
    gen = cfg.get("generator")
    if gen:
        return gen
    return ("common/gen_aa_html.py", ["--work", work])


def _html_entry(cfg):
    """交付**入口 HTML**（2026-10-06 起交付物是文件夹 → `<folder>/index.html`）。

    存在性 / mtime / 「可打开」的判据一律用这个，别用交付文件夹本身的路径或 mtime
    （Windows 目录 mtime 只在增删文件时变，改内容不变）。
    """
    return _gen_aa_html.deliver_entry(cfg)


def _deliver_artifacts(cfg):
    """交付物件路径列表（用于「删除作品」预览/清理）：文件夹交付 → 整个文件夹；旧单文件 → 该 .html。"""
    ho = (cfg.get("html_out") or "").strip()
    cands = [ho] if ho.endswith(".html") else [_gen_aa_html.deliver_dir(cfg)]
    return [p for p in cands if p and os.path.exists(p)]


# 图片漫画（仅阅览）：名称 -> 章节根目录（子目录=章节，目录内图片按文件名排序）
# 东方心绮谭用交付的 seamless 裁边版（与交付 HTML 同源）
IMAGE_MANGA = {
    "东方心绮谭": os.path.join(_DATA, "aaManga", "交付HTML", "東方新異譚", "comic_seamless"),
    "白馒头做了人类灭绝的梦 001-210": os.path.join(_DATA, "manhuagui_", "comic_seamless"),
}


IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


# 阅读进度（AA 作品 + 图片漫画统一存根目录一个文件，不入 git）
_PROGRESS_FILE = os.path.join(_DATA, ".aaweb_progress.json")


# 作品级可编辑元数据（汉化名/标签/素材/作者兜底），入库
_WORK_META_FILE = os.path.join(_DATA, "aa_web_app", "data", "work_meta.json")


# 作者◆提取兜底：实时投票法扫不到（或需要人工修正）的作品 → authory_override
_AUTHOR_OVERRIDE = {
    "yaruohiroba": "◆?",     # 短篇祭合集无 ◆ 楼主帖头，待人工补真实作者
}


def _env_num(name, default):
    try:
        return float(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return float(default)


# 首页数据快照（2026-10-13，方案 A）：请求路径只读快照 → 秒回；
# 重建走后台线程（单飞 + 限流），显式改动走 invalidate 局部重算。见 index_cache 模块头。
_INDEX_SNAP = index_cache.IndexSnapshot(
    index_cache.snapshot_path(_DATA),
    max_age=_env_num("AA_INDEX_MAX_AGE", index_cache.DEFAULT_MAX_AGE),
    min_interval=_env_num("AA_INDEX_REFRESH_INTERVAL", index_cache.DEFAULT_MIN_INTERVAL),
)


# 脏作品数 ≤ 该值时在请求路径里同步重算（保证「保存/重生成完立刻回首页能看到」）；
# 超了就交给后台，本次先用旧值（大批脏=重新加载全部作品的场景，等后台就行）。
INDEX_SYNC_DIRTY_MAX = 3


_BUILD_LOCK = threading.RLock()       # 首次构建/重建串行（并发请求不重复扫全库）；RLock：index() 首建会重入


def _load_progress():
    try:
        with open(_PROGRESS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_progress(data):
    try:
        with open(_PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


# ---------- 工具 ----------


def cfg_of(work):
    if work not in registry.WORKS:
        raise HTTPException(404, f"未知作品: {work}（可用: {', '.join(registry.WORKS)}）")
    return registry.WORKS[work]


def paths_of(cfg):
    return cfg["aa_dir"], cfg["out_dir"], cfg["work_dir"]


def check_chapter(cfg, fname):
    """校验章节存在：接受 汉化名(entry_1_汉化.txt) 或 原文名(entry_1.txt)。"""
    if fname in logic.chapter_order(cfg["out_dir"], cfg.get("name_style")):
        return
    if os.path.exists(logic.txt_path(cfg["aa_dir"], fname)) and (fname.endswith(".txt") or fname.endswith(".txt.gz")) \
            and not fname.endswith(("_台词.txt", "_台词.txt.gz")):
        return
    raise HTTPException(404, f"章节不存在: {fname}")


def chapter_meta(cfg, fname, tr_lines=None):
    """章节标题（txt 第1行 # 后）+ 原帖地址（第2行 # 原帖地址: 后）。"""
    tr_lines = tr_lines if tr_lines is not None else logic.read_lines(
        logic.txt_path(cfg["out_dir"], fname))
    title, src_url = "", ""
    for l in tr_lines[:3]:
        if l.startswith("# ") and not title:
            title = l[2:].strip()
        if l.startswith("# 原帖地址: "):
            src_url = l.replace("# 原帖地址: ", "").strip()
    if not src_url:
        m = logic.EID_RE.search(fname)
        if m:
            src_url = cfg["url_pattern"].format(eid=m.group(1))
    return title, src_url


def run_generator(work, extra=None, plain=False):
    """子进程跑生成器（纯本地零 token）。返回 (exit_code, output_tail, html_out)。

    plain=True → 用**统一生成器** `common/gen_aa_html.py --plain`（「仅 HTML 文件」简版；
    别走作品自定义 generator，那种是 legacy 单文件路径、不认简版开关）。
    extra = 附加命令行参数（如 --split 100）。

    Windows 长驻进程偶发子进程 spawn 失败（exit 0xC0000142 DLL 初始化），
    重试 2 次自愈（实测重启服务即恢复，重试为兜底）。
    """
    cfg = cfg_of(work)
    if plain:
        rel, args = "common/gen_aa_html.py", ["--work", work]
    else:
        rel, args = generator_of(work)
    args = list(args) + list(extra or [])
    script = os.path.join(_SCRIPTS, rel)
    last = (-1, "生成失败: 子进程未能启动", _gen_aa_html.deliver_entry(cfg))
    for attempt in range(3):
        try:
            r = subprocess.run(py_prefix() + [script] + args, cwd=_SCRIPTS,
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=600)
        except subprocess.TimeoutExpired:
            return -1, "生成超时(600s)", _gen_aa_html.deliver_entry(cfg)
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0:
            return 0, "\n".join(out.strip().splitlines()[-30:]), _gen_aa_html.deliver_entry(cfg)
        if r.returncode != 0xC0000142:      # 非 DLL 初始化类错误不重试
            return r.returncode, "\n".join(out.strip().splitlines()[-30:]), _gen_aa_html.deliver_entry(cfg)
        time.sleep(1.0 * (attempt + 1))
        last = (r.returncode, "生成失败(0xC0000142,已重试 %d 次): %s" % (attempt + 1, out.strip().splitlines()[-1:] and out.strip().splitlines()[-1] or ""), _gen_aa_html.deliver_entry(cfg))
    return last


def _chapter_full(cfg, fname, work_names=None):
    """单章完整信息（统计+标题+原帖+备注）。标题只读前 3 行，不整文件重读。"""
    tr_path = logic.txt_path(cfg["out_dir"], fname)
    aa_path = logic.txt_path(cfg["aa_dir"], logic.src_name_of(fname))
    try:
        st_f = os.stat(tr_path)
    except OSError:
        return None
    title, src_url = "", ""
    try:
        for l in logic._txt_head(tr_path, 3):
            if l.startswith("# ") and not title:
                title = l[2:].strip()
            if l.startswith("# 原帖地址: "):
                src_url = l.replace("# 原帖地址: ", "").strip()
    except OSError:
        pass
    if not src_url:
        m = logic.EID_RE.search(fname)
        if m:
            src_url = cfg["url_pattern"].format(eid=m.group(1))
    if not os.path.exists(aa_path):
        stats = {"src_lines": 0, "tr_lines": 0, "line_count_ok": False,
                 "mismatches": 0, "short": 0, "long": 0, "residual": 0}
        note = "缺原文"
    else:
        # 2026-09-05: 移除逐章宽度/残留校验（用户确认工作台不展示，warm 不再逐章读正文+比对）
        stats = {"src_lines": 0, "tr_lines": 0, "line_count_ok": False,
                 "mismatches": 0, "short": 0, "long": 0, "residual": 0}
        note = ""
        # 2026-09-05: 保留原帖地址兜底（读前 3 行已含；此处仅当 _txt_head 未取到 URL 时兜底）
    # 2026-08-25: r18 判定并入缓存条目（避免 work_stats 每次重读 prep）；
    # backups 为作品级（work_dir/backup 时间戳数），由 work_stats 循环外算一次
    base = logic.txt_key(fname)
    _r18j = logic.r18_chapter_judge(cfg["work_dir"], base) or {}
    # prep json 的 mtime 也进 key——词表更新/重跑 prep 后缓存自动失效
    prep_m = 0
    prep_p = os.path.join(cfg["work_dir"], "prep", base + ".json")
    if os.path.exists(prep_p):
        try:
            prep_m = os.path.getmtime(prep_p)
        except OSError:
            prep_m = 0
    return {"key": ["v2", st_f.st_mtime, st_f.st_size, prep_m], "title": title,
            "src_url": src_url, "note": note, "stats": stats,
            "r18_hit": _r18j.get("r18_hit", ""),
            "r18_conc": _r18j.get("conc", 0),
            "r18_hits": _r18j.get("r18_word_hits", 0)}


def work_stats(work):
    """作品聚合统计。三层提速：
    1) 内存 TTL 缓存（300s，保存/生成后失效）
    2) 磁盘增量缓存 work_dir/stats_cache.json（按文件 mtime+size 命中，只重算变动的章节）
    3) 6 线程并行计算首次冷载。"""
    now = time.time()
    hit = _STATS_CACHE.get(work)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    cfg = cfg_of(work)
    aa_dir, out_dir, work_dir = paths_of(cfg)
    agg = {"chapters": 0, "mismatches": 0, "short": 0, "long": 0,
           "residual": 0, "line_mismatch": 0}
    error = None
    chapters = []
    cache = {}
    cache_path = os.path.join(work_dir, "stats_cache.json")
    if os.path.exists(cache_path):
        try:
            cache = json.load(open(cache_path, encoding="utf-8"))
        except Exception:
            cache = {}
    if not isinstance(cache, dict):
        cache = {}
    if not os.path.isdir(out_dir):
        # 纯原文作品（未译）：汉化目录不存在不报错，chapters = aa 原文数
        # 2026-08-31 修复：用 aa 目录真实文件名 + chapter_order.json 排序（原为 entry_{i} 假名，
        # 顺序错乱且不反映真实 eid，如 bar_miku 显示 entry_0~75 而非 entry_1630~1709）
        # 2026-09-14: 加磁盘缓存——此前该分支既不读也不写 stats_cache.json，每次冷调
        # （每次重启 + 每 300s TTL 到期）都 listdir + 逐章读前 3 行：1258 章的作品 ≈0.5s，
        # 43 部未译作品合计 ~4s，且因写回缺失永不自愈。指纹 = aa/ 顶层文件快照 + chapter_order.json。
        order_path = os.path.join(work_dir, "chapter_order.json")
        try:
            _ord_m = f"{os.path.getmtime(order_path):.0f}" if os.path.exists(order_path) else "0"
        except OSError:
            _ord_m = "0"
        _srcfp = logic.stable_fp(["srcv1", logic.dir_top_fp(aa_dir), _ord_m,
                                  cfg.get("name_style", "")])
        _sc = cache.get("__src")
        if (isinstance(_sc, dict) and _sc.get("fp") == _srcfp
                and isinstance(_sc.get("chapters"), list)):
            chapters = list(_sc["chapters"])
            agg["chapters"] = len(chapters)
            error = None
        else:
            try:
                src_files = sorted(f for f in os.listdir(aa_dir)
                                   if logic._is_txt(f))
            except OSError:
                src_files = []
            src_n = len(src_files)
            # chapter_order.json 排序（若存在）
            _order = {}
            if os.path.exists(order_path):
                try:
                    _d = json.load(open(order_path, encoding="utf-8"))
                    _order = {str(x): i for i, x in enumerate(_d.get("order", []))}
                except Exception:
                    _order = {}
            if _order:
                def _k(f):
                    m = re.search(r"(?:entry_)?(\d+)", f)
                    eid = m.group(1) if m else f
                    pos = _order.get(eid)
                    return (0, pos) if pos is not None else (1, 99999)
                src_files.sort(key=_k)
            error = None
            agg["chapters"] = src_n
            for f in src_files:
                # 2026-09-02 修复：读章节首行标题 + 原帖地址（未译作品目录此前 title 全空，只显示文件名）
                _t, _u = "", ""
                try:
                    with open(os.path.join(aa_dir, f), encoding="utf-8") as _fh:
                        for _ in range(3):
                            _l = _fh.readline().rstrip("\r\n")
                            if _l.startswith("# ") and not _t:
                                _t = _l[2:].strip()
                            if _l.startswith("# 原帖地址: "):
                                _u = _l.replace("# 原帖地址: ", "").strip()
                except OSError:
                    pass
                chapters.append({"file": f, "title": _t, "src_url": _u, "stats": {}, "note": "未译",
                                 "backups": 0})
            # 写回章节清单缓存（只读前 3 行得来，aa/ 未变即命中）
            try:
                os.makedirs(work_dir, exist_ok=True)
                with _LOCK:
                    cache["__src"] = {"fp": _srcfp, "chapters": chapters}
                    with open(cache_path, "w", encoding="utf-8") as f:
                        json.dump(cache, f, ensure_ascii=False)
            except Exception:
                pass
    else:
        fnames = logic.chapter_order(out_dir, cfg.get("name_style"))
        keys = {}
        for f in fnames:
            try:
                s = os.stat(os.path.join(out_dir, f))
                # v2: 2026-08-24 加 r18_hit/r18_conc 字段 → 版本号进 key 强制重建旧缓存
                # v3: 2026-08-25 prep mtime 也进 key（r18 判定变化时缓存失效）
                prep_m = 0
                prep_p = os.path.join(work_dir, "prep", logic.txt_key(f) + ".json")
                if os.path.exists(prep_p):
                    try:
                        prep_m = os.path.getmtime(prep_p)
                    except OSError:
                        prep_m = 0
                keys[f] = ["v2", s.st_mtime, s.st_size, prep_m]
            except OSError:
                keys[f] = None
        # 只重算缓存未命中/已变化的章节（串行：Python 计算受 GIL 限制，线程无益）
        todo = [f for f in fnames
                if not (cache.get(f) and cache[f].get("key") == keys[f])]
        if todo:
            # 2026-08-30 非阻塞: 缓存失配(挂机后任务完成写文件/大面积改动)时
            # 后台线程重算, 本次请求立即用旧缓存聚合返回 —— 避免作品页首次访问卡 5-12s
            _recalc_async(work, fnames, keys, cache, cache_path, work_dir)
        for fname in fnames:
            entry = cache.get(fname) or {}
            stats = entry.get("stats") or {
                "src_lines": 0, "tr_lines": 0, "line_count_ok": False,
                "mismatches": 0, "short": 0, "long": 0, "residual": 0}
            note = entry.get("note", "")
            if not stats["line_count_ok"]:
                agg["line_mismatch"] += 1
            agg["mismatches"] += stats["mismatches"]
            agg["short"] += stats["short"]
            agg["long"] += stats["long"]
            agg["residual"] += stats["residual"]
            chapters.append({
                "file": fname, "title": entry.get("title", ""),
                "src_url": entry.get("src_url", ""),
                "stats": stats, "note": note,
                "backups": entry.get("backups", 0),
                # 2026-08-24 R18 章节级判定（并入缓存条目，2026-08-25 起不再每次重读 prep）
                "r18_hit": entry.get("r18_hit", ""),
                "r18_conc": entry.get("r18_conc", 0),
                "r18_hits": entry.get("r18_hits", 0),
            })
        agg["chapters"] = len(chapters)
        # 作品级备份数（backup 目录时间戳数）——循环外算一次，避免每章重复 os.listdir
        backups_total = len(logic.list_backups(os.path.join(work_dir, "backup")))
        for _c in chapters:
            _c["backups"] = backups_total
        if not todo:
            # 仅无失配时主线程写缓存（有失配时由 _recalc_async 后台写，防双写/锁竞争）
            try:
                os.makedirs(work_dir, exist_ok=True)
                with _LOCK:
                    with open(cache_path, "w", encoding="utf-8") as f:
                        json.dump(cache, f, ensure_ascii=False)
            except Exception:
                pass
    stats = {"work": work, "error": error, "agg": agg, "chapters": chapters,
             "html_out": _gen_aa_html.deliver_entry(cfg),
             "html_exists": bool(_html_entry(cfg)) and os.path.exists(_html_entry(cfg)),
             "html_mtime": (os.path.getmtime(_html_entry(cfg)) if _html_entry(cfg)
                            and os.path.exists(_html_entry(cfg)) else None)}
    _STATS_CACHE[work] = (now, stats)
    return stats


def invalidate_stats(work):
    _STATS_CACHE.pop(work, None)
    _LASTUPD_CACHE.pop(work, None)
    # 首页快照：该作品标脏 → 下次首页请求同步只重算这一部（改完立刻可见，见 index_cache）
    try:
        _INDEX_SNAP.invalidate(work)
    except Exception:
        pass
    # 阅读器章节上下文（reader_win）同样按作品失效：保存行/术语替换/重生成/还原都会改行内容。
    # 注意 ctx 另有 (mtime,size) 指纹兜底（gold/标注这类不经过本函数的小写入也自动生效）。
    reader_win.invalidate(work)
    logic.clear_search_cache(work)      # 全库搜索结果同样作废（2026-10-07 阅读器搜索）


# r18_summary 首页缓存（每作品扫全部 prep JSON，TTL 与 stats 一致）
_R18_SUMMARY_CACHE = {}


_R18_TTL = 300


def r18_summary_cached(work_dir, out_dir):
    """r18_summary 的内存 TTL 缓存（首页每个作品调用，prep 文件多时全量扫慢）。"""
    _key = (work_dir, out_dir)
    now = time.time()
    hit = _R18_SUMMARY_CACHE.get(_key)
    if hit and now - hit[0] < _R18_TTL:
        try:
            out_m = os.path.getmtime(out_dir) if out_dir and os.path.isdir(out_dir) else 0
        except OSError:
            out_m = 0
        if out_m == hit[1]:
            return hit[2]
    r = logic.r18_summary(work_dir, out_dir)
    try:
        out_m = os.path.getmtime(out_dir) if out_dir and os.path.isdir(out_dir) else 0
    except OSError:
        out_m = 0
    _R18_SUMMARY_CACHE[_key] = (now, out_m, r)
    return r


_RECALC_LOCK = threading.Lock()   # 防止同作品并发后台重算（多请求同时失配）


def _recalc_async(work, fnames, keys, cache, cache_path, work_dir):
    """后台重算失配章节并写磁盘缓存（2026-08-30 非阻塞化）。

    2026-10-14: 原来两处 `except Exception: pass` 把失败吞成静默——写缓存失败时
    「失配章节」永远停在旧值且无任何痕迹。改为打一行 [recalc] 日志（控制台可见），
    行为不变（失败仍不阻断请求、旧缓存照用）。
    """
    if not _RECALC_LOCK.acquire(blocking=False):
        return  # 已有重算在跑，本次跳过（旧缓存已够用，下轮访问拿新数据）
    try:
        def _run():
            try:
                cfg = cfg_of(work)
                _known = logic.load_work_glossary(work)["known_names"]
                _changed = False
                _failed = 0
                for f in fnames:
                    if cache.get(f) and cache[f].get("key") == keys[f]:
                        continue
                    try:
                        r = _chapter_full(cfg, f, _known)
                    except Exception as e:
                        r = None
                        _failed += 1
                        print(f"[recalc] {work}/{f} 重算失败: {e!r}", flush=True)
                    if r:
                        cache[f] = r
                        _changed = True
                if _failed:
                    print(f"[recalc] {work}: {_failed} 章重算失败（沿用旧缓存）", flush=True)
                if _changed:
                    try:
                        os.makedirs(work_dir, exist_ok=True)
                        with _LOCK:
                            with open(cache_path, "w", encoding="utf-8") as f:
                                json.dump(cache, f, ensure_ascii=False)
                    except Exception as e:
                        # 静默改可见：盘上缓存没更新 → 下次访问还会走同一批失配章节
                        print(f"[recalc] {work}: 缓存落盘失败 {cache_path}: {e!r}", flush=True)
            finally:
                _RECALC_LOCK.release()
        threading.Thread(target=_run, daemon=True).start()
    except Exception as e:
        _RECALC_LOCK.release()
        print(f"[recalc] {work}: 后台重算线程启动失败: {e!r}", flush=True)


# ---------- 原站 URL 解析（2026-08-31：非 r401 作品也显示「原站」按钮）----------


_LASTUPD_CACHE = {}


# ---------- 翻译替换（阅读器手动替换对 → 本作品全部 汉化.txt，2026-09-09） ----------


_KIND2TYPE = {"art": "aa", "text": "text", "mixed": "mixed",
              "header": "header", "blank": "blank"}


def _art_sources(cfg, base):
    """art 判定所需的两份标注：line_meta_v2 与人工 override（读一次，全章共用）。"""
    meta, overrides = {}, {}
    meta = meta_load(os.path.join(cfg["work_dir"], "line_meta_v2"), base, None) or {}
    try:
        overrides = logic.load_overrides(cfg["work_dir"], base) or {}
    except Exception:
        overrides = {}
    return meta, overrides


def _art_checker(meta, overrides, src_getter):
    """返回 f(i)->bool（0-based, i 是否 art）：override > line_meta_v2 > 规则。

    规则路径（src 行判定）才调 src_getter()——传已读好的行（整章判定）或惰性
    读原文（只判命中行时，绝大多数行有 meta/override，省一次原文读取）。
    """
    def f(i):
        ov = overrides.get(str(i + 1))
        if ov:
            return _KIND2TYPE.get(ov) == "aa"
        mv = meta.get(str(i + 1)) if meta else None
        mk = mv.get("kind") if isinstance(mv, dict) else None
        if mk:
            return _KIND2TYPE.get(mk) == "aa"
        src = src_getter()
        return bool(src) and i < len(src) and logic.line_type_of(src[i]) == "aa"
    return f


# ---------- 阅读状态（read_state，作品列表改版 2026-09-03）----------


_READ_STATE_FILE = os.path.join(_DATA, "aa_web_app", "data", "read_state.json")


def _load_read_state():
    try:
        with open(_READ_STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
