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
    """后台重算失配章节并写磁盘缓存（2026-08-30 非阻塞化）。"""
    if not _RECALC_LOCK.acquire(blocking=False):
        return  # 已有重算在跑，本次跳过（旧缓存已够用，下轮访问拿新数据）
    try:
        def _run():
            try:
                cfg = cfg_of(work)
                _known = logic.load_work_glossary(work)["known_names"]
                _changed = False
                for f in fnames:
                    if cache.get(f) and cache[f].get("key") == keys[f]:
                        continue
                    try:
                        r = _chapter_full(cfg, f, _known)
                    except Exception:
                        r = None
                    if r:
                        cache[f] = r
                        _changed = True
                if _changed:
                    try:
                        os.makedirs(work_dir, exist_ok=True)
                        with _LOCK:
                            with open(cache_path, "w", encoding="utf-8") as f:
                                json.dump(cache, f, ensure_ascii=False)
                    except Exception:
                        pass
            finally:
                _RECALC_LOCK.release()
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        _RECALC_LOCK.release()


# 启动**不**做任何全库预热：重建由首页请求限流触发（见 main.index 与 index_cache 模块头），
# 否则单元测试 import main 也会拉起全库扫描。


# ---------- 热加载 ----------

@app.post("/api/reload")
def api_reload(warm: bool = True, clear_cache: bool = True):
    """热加载 registry（新作品/代码调整无需重启服务）。

    - importlib.reload(registry)：新作品、改动的 registry 条目立即生效
      （main.py 全部经 `registry.WORKS` 模块属性访问，reload 后自动指向新 dict）
    - 清空内存统计缓存（_STATS_CACHE），下次访问按磁盘增量缓存重算
    - sites 解析器是运行时 importlib.import_module（见 check_chapter 调用点），天然读新代码
    - warm=False（删除作品路径）：不启动全量预热线程——删除后前端立即 reload，
      预热全量冷扫（75 部 × ~25s）会与首页请求抢磁盘 IO/GIL 导致首页卡刷新。
    - clear_cache=False（删除路径）：不清其它作品的统计缓存（删单部不影响其余），
      只由调用方 pop 被删作品，避免 reload 后首页逐部冷重算。
    """
    # reload registry 前先确保其依赖 paths 也新鲜（registry 里 `from paths import BASE`）
    import importlib as _il
    try:
        _paths = _il.import_module("paths")
        _il.reload(_paths)
    except ImportError:
        pass
    _il.reload(registry)
    # 清空统计缓存：作品可能增删，旧缓存条目失效；删除路径可保留其余缓存
    if clear_cache:
        _STATS_CACHE.clear()
        logic.invalidate_mch_cache()
        _R18_SUMMARY_CACHE.clear()
        _LASTUPD_CACHE.clear()
    # 后台重建首页快照（新作品/删除的作品不阻塞响应）；删除路径 warm=False 跳过防抢 IO
    _INDEX_SNAP.invalidate(None)          # 作品集合变了 → 整份快照重算
    if warm:
        _index_refresh_async(force=True)
    return {"ok": True, "works": len(registry.WORKS)}


# ---------- 作品添加/下载（2026-08-30）----------

def _registry_path():
    r"""registry.py 的**真实可写位置** —— 一律取 `registry.__file__`（app 实际 import 的那份）。

    不能用 `_SCRIPTS/works/registry.py`：冻结 exe 里 `_SCRIPTS` 指向 PyInstaller **解包目录**，
    而启动引导会把那份 registry 删掉（防 shell 子任务误命中数据根之外的副本），于是写它 =
    FileNotFoundError → 「删除作品」在前面把文件删完之后报 500 → 用户看到
    **「文件已删除但记录还在」**（2026-10-09 实测，bar_miku / MM 两部都中招）。
    """
    p = getattr(registry, "__file__", None)
    if p and os.path.isfile(p):
        return p
    return os.path.join(_SCRIPTS, "works", "registry.py")


def _append_registry_entry(work_id, entry):
    """registry.py 末尾 WORKS dict 内追加条目（行级安全操作，禁 find+切片拼接）。

    WORKS 结构：`WORKS = {` —— 在最后一个条目后、闭合 `}` 前插入。
    """
    rp = _registry_path()
    with open(rp, "r", encoding="utf-8") as f:
        text = f.read()
    # 找 WORKS 里最后一个条目结尾 "    ),"（bar_miku_r 的），在其后插入新条目
    marker = "\n\ndef get(work)"
    idx = text.rindex(marker)
    tail_end = text.rindex("    ),", 0, idx) + len("    ),")
    # 构造新条目文本（与现有一致缩进）
    lines = []
    lines.append(f'    "{work_id}": dict(')
    for k, v in entry.items():
        if isinstance(v, str):
            # 路径含反斜杠 → repr 原样转义
            if "\\" in v:
                lines.append(f'        {k}={v!r},')
            elif "{" in v:
                lines.append(f'        {k}="{v}",')
            else:
                lines.append(f'        {k}="{v}",')
        elif isinstance(v, bool):
            lines.append(f'        {k}={str(v)},')
        else:
            lines.append(f'        {k}={v!r},')
    lines.append("    ),")
    new_block = "\n" + "\n".join(lines)
    text = text[:tail_end] + new_block + text[tail_end:]
    with open(rp, "w", encoding="utf-8") as f:
        f.write(text)


_SITE_PREFIX_RE = re.compile(r"^(?:やる夫スレ本棚[^ |]*?)\s*[|｜]\s*")

def _work_id_from_title(title, kind, r401_cid):
    """生成作品 id：剥站名前缀/特殊字符 → ASCII+日文混合（禁 ◆★|｜ 等文件系统/URL 特殊字符）。"""
    s = title.strip()
    s = _SITE_PREFIX_RE.sub("", s)   # 剥「やる夫スレ本棚2号館 | 」式站名前缀
    s = re.sub(r"[^\w\u3040-\u30ff\u4e00-\u9fff]+", "_", s)
    s = re.sub(r"[_]+", "_", s).strip("_")
    if not s:
        s = f"{kind}_{r401_cid or 'new'}"
    base, i = s[:40], 2
    while base in registry.WORKS:
        base = f"{s[:36]}_{i}"
        i += 1
    return base


@app.post("/api/parse-url")
def api_parse_url(body: dict = None):
    """解析分类页 URL → 作品信息（标题/话数/简介/章节清单）。body: {url, translate_intro?}"""
    if not body or not body.get("url"):
        raise HTTPException(422, "缺少 url")
    info = add_work.parse_url(body["url"])
    if not info.get("ok"):
        raise HTTPException(422, info.get("error", "解析失败"))
    # 2026-09-20 用户要求：简介**默认不自动翻译**（手动翻译走 POST /api/translate-intro，
    # 添加作品弹窗的「翻译简介」按钮）。显式传 translate_intro: true 仍可解析时顺带翻译。
    intro_cn, translated = "", False
    if body.get("translate_intro") and info.get("description"):
        intro_cn, translated = add_work.translate_desc(info["description"])
    return {
        "ok": True,
        "kind": info["kind"],
        "title": info["title"],
        "chapters": info["chapters"],
        "chapter_count": len(info["chapters"]),
        "intro_jp": info["description"],
        "intro_cn": intro_cn or info["description"],
        "intro_translated": translated,
        "site": info["site"],
        "site_key": info.get("site_key"),
        "r401_cid": info["r401_cid"],
        "work_id": _work_id_from_title(info["title"], info["kind"], info.get("r401_cid")),
    }


class IntroReq(BaseModel):
    text: str = ""
    r18_level: str = ""      # high（整部走 r18 链）时翻译走 r18 安全端点


@app.post("/api/translate-intro")
def api_translate_intro(body: IntroReq):
    """手动翻译作品简介（添加作品弹窗「翻译简介」按钮；2026-09-20 起不再自动翻译）。

    走 add_work.translate_desc → aa_translate.call_api（AA_BACKEND 未设 = [default] commandcode 网关）。
    翻译失败（含内容审查拒绝）回退日文原文，返回 translated=False 供前端提示。
    """
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(422, "简介为空")
    zh, ok = add_work.translate_desc(text, work_r18=(body.r18_level == "high"))
    return {"ok": True, "text": zh, "translated": bool(ok)}


@app.post("/api/add-work")
def api_add_work(body: dict = None):
    """确认后注册作品 + 入队下载。body: {url, work_id?, cn_title, author, status, r18_level, intro}"""
    if not body or not body.get("url"):
        raise HTTPException(422, "缺少 url")
    info = add_work.parse_url(body["url"])
    if not info.get("ok"):
        raise HTTPException(422, info.get("error", "解析失败"))
    cn_title = (body.get("cn_title") or "").strip() or info["title"]
    # 作品 ID 一律自动生成（用户不再填写；body.work_id 忽略）
    work_id = _work_id_from_title(cn_title, info["kind"], info.get("r401_cid"))
    with _LOCK:   # 防连点：整个注册段串行化（单进程多线程，双击/多请求不重复注册）
        if work_id in registry.WORKS:
            raise HTTPException(409, f"作品 {work_id} 已存在（{registry.WORKS[work_id].get('title')}）")
        # 目录冲突防护：即使 registry key 不同，目标目录已有非空 aa/汉化/raw 也拒绝
        _d = os.path.join(registry.BASE, "aaManga", work_id)
        _existing_dirs = [x for x in ("aa", "汉化", "raw") if os.path.isdir(os.path.join(_d, x)) and os.listdir(os.path.join(_d, x))]
        if _existing_dirs:
            raise HTTPException(409, f"目录 aaManga/{work_id} 已存在非空子目录（{', '.join(_existing_dirs)}），请改作品 ID 或手动处理")
        work_dir = os.path.join(registry.BASE, "aaManga", work_id, "work")
        # 1. 章节清单 → work/chapters.json（yaruobook 系 cache 消费；r401 系也留一份兜底）
        add_work.save_chapters_json(work_dir, info["chapters"])
        # 2. registry 追加条目
        entry = add_work.build_registry_entry(work_id, info, cn_title,
                                              body.get("author", ""), body.get("status", ""),
                                              body.get("r18_level", ""), body.get("intro", ""))
        _append_registry_entry(work_id, entry)
        # 3. work_meta.json
        meta = logic.load_work_meta(_WORK_META_FILE)
        existing = meta.get(work_id, {})
        existing["author"] = logic.normalize_author(body.get("author"))
        existing["cn_name"] = cn_title
        existing["jp_title"] = info["title"]
        existing["status"] = (body.get("status") or "").strip()
        existing["intro"] = (body.get("intro") or "").strip() or info.get("description", "")
        existing["r18_level"] = body.get("r18_level", "") if body.get("r18_level") in ("high", "low", "medium", "") else ""
        existing.setdefault("tags", [])
        existing.setdefault("materials", [])
        existing.setdefault("关联度", "serial")
        meta[work_id] = existing
        ok = logic.save_work_meta(_WORK_META_FILE, meta)
        if not ok:
            raise HTTPException(500, "保存 work_meta.json 失败")
    # 4. 热加载 + 入队下载（锁外：网络/调度器操作不持锁）
    api_reload()
    r = TM.enqueue("download", work_id, {"chapters": "all"})
    if not r.get("ok"):
        return {"ok": False, "work": work_id, "error": r.get("error"), "task_id": None}
    return {"ok": True, "work": work_id, "task_id": r["task_id"]}


# ---------- 更新检查（2026-08-31）----------

@app.post("/api/work/{work}/check-update")
def api_check_update(work: str):
    """检查作品是否有新章节：实时拉原站清单 vs 本地已下载。

    `wiki` 字段（2026-10-06）= 本作品是否配了官方设定 wiki（前端据此显示「同步本作 wiki」勾选框）。"""
    cfg = registry.WORKS.get(work)
    if not cfg:
        raise HTTPException(404, f"未知作品: {work}")
    r = add_work.check_updates(work, cfg)
    if isinstance(r, dict):
        r["wiki"] = bool(TM.wiki_configured(work))
    return r


@app.post("/api/work/{work}/update")
def api_work_update(work: str, body: dict = None):
    """按需更新作品：body {action: "download"|"translate"}。仅处理 check_updates 报的新章。

    2026-08-31 修复：原 params.chapters="new" 是「本地缺的全部」（含历史缺口，如 baitou
    只下载了最新 42 章、原站 218 条 → 会误拉 176 条旧章）。改为用 check_updates 报的
    「比本地最新还新的连续新章」精确 eid 列表。"""
    body = body or {}
    action = body.get("action")
    if action not in ("download", "translate"):
        return {"ok": False, "error": "action 须为 download|translate"}
    cfg = registry.WORKS.get(work)
    if not cfg:
        raise HTTPException(404, f"未知作品: {work}")
    # 2026-10-06: 勾「同步本作 wiki」= 连载更新时把作品官方设定 wiki 一起对齐（零模型成本，
    # 只抓原站 + 增量翻译新增行）。未配置 wiki 的作品静默忽略（不强加失败）。
    sync_wiki = bool(body.get("sync_wiki")) and TM.wiki_configured(work)
    upd = add_work.check_updates(work, cfg)
    if not upd.get("ok"):
        return {"ok": False, "error": upd.get("error", "更新检查失败")}
    eids = [str(u["eid"]) for u in (upd.get("updates") or []) if u.get("eid")]
    if not eids:
        if sync_wiki:      # 章节没有新的，但原站 wiki 可能已经改了 → 仍然同步一次
            r = TM.enqueue("wiki", work, {})
            if not r.get("ok"):
                return r
            return {"ok": True, "task_id": r["task_id"],
                    "msg": f"章节已是最新；本作 wiki 同步已入队: {r['task_id']}"}
        return {"ok": True, "msg": "已是最新，无需更新", "task_id": None}
    params = {"chapters": ",".join(eids)}
    if action == "translate":
        # 2026-09-13 链式：下载新章 → 正常完成自动接翻译（task_manager params["then"]）。
        # translate_runner 已不再自动补下载，所以「下载并翻译」必须显式分两段。
        # 2026-10-06 三链：勾「同步本作 wiki」时 = 下载 → wiki 同步 → 翻译（wiki 项自身再带 then）。
        chain = {"type": "translate", "chapters": ",".join(eids)}
        if body.get("free_mode"):
            chain["env"] = {"AA_FREE_MODE": "1"}
        params["then"] = {"type": "wiki", "then": chain} if sync_wiki else chain
    r = TM.enqueue("download", work, params)
    if not r.get("ok"):
        return r
    if action == "translate":
        tail = "（下载完成后自动接 wiki 同步 → 翻译）" if sync_wiki else "（下载完成后自动接翻译）"
        msg = f"下载并翻译 {len(eids)} 个新章已入队{tail}: {r['task_id']}"
    else:
        msg = f"下载 {len(eids)} 个新章已入队: {r['task_id']}"
    return {"ok": True, "task_id": r["task_id"], "msg": msg}


@app.post("/api/work/{work}/sync-wiki")
def api_work_sync_wiki(work: str):
    """手动同步本作品官方设定 wiki（连载更新后 / 译名改动后）。

    只对 `scripts/config/work_wikis.json` 里配过的作品可用；`--update` = 重抓原站全部页
    （零模型成本）+ 行级缓存增量翻译（只译新行/改动行）+ 重建 index.html/术语表.json。"""
    if not registry.WORKS.get(work):
        raise HTTPException(404, f"未知作品: {work}")
    if not TM.wiki_configured(work):
        return {"ok": False, "error": f"作品 {work} 未配置官方设定 wiki（scripts/config/work_wikis.json）"}
    r = TM.enqueue("wiki", work, {"mode": "update"})
    if not r.get("ok"):
        return r
    return {"ok": True, "task_id": r["task_id"],
            "msg": f"{work} 的 wiki 同步已入队: {r['task_id']}"}


# ---------- 页面 ----------



# ---------- 原站 URL 解析（2026-08-31：非 r401 作品也显示「原站」按钮）----------
# ⚠️ 首页 index() 绝不发网络请求（曾因同步抓 yaruobook 页面卡死首页 3s+/作品）：
#    有缓存读缓存，无缓存立即回退首章 URL（秒回），分类解析交给 _site_url_backfill 后台线程惰性补全。
def _yaruobook_proxy():
    """原站解析用的代理（地址走 aa_config：AA_PROXY > TOML；2026-10-08 阶段 4）。"""
    return _aa_config().proxy_url()


def _find_yaruobook_cat(article_url):
    """从 yaruobook 章节页面包屑（itemListElement schema）解析作品专属分类 ?cat=N。
    走 v2rayN 代理（直连慢/403）。失败返回 None。"""
    import urllib.request
    try:
        op = urllib.request.build_opener(
            urllib.request.ProxyHandler(_aa_config().proxy_dict()))
        req = urllib.request.Request(article_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"})
        with op.open(req, timeout=8) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception:
        return None
    # 面包屑 itemListElement：兼容 <span itemprop="name"> 与 <span class="breadcrumb-caption"> 两种结构
    items = re.findall(
        r'itemListElement[^>]*>.*?<a href="[^"]*[?&]cat=(\d+)[^"]*"[^>]*>\s*<span[^>]*>(?:<span[^>]*>)?([^<]{1,60}?)(?:</span>)*</a>',
        html, re.S)
    if items:
        # 取最后一个分类（更具体：作品集 → 短編，避免误取父分类）
        return items[-1][0]
    m2 = re.search(r"category-(\d+)-post", html)
    return m2.group(1) if m2 else None


def _site_url_fallback(cfg):
    """无 r401_cid 作品的原站回退 URL（秒回，不网络）：chapters.json 首章 URL → url_pattern 首章。"""
    work_dir = cfg.get("work_dir", "")
    chapters_path = os.path.join(work_dir, "chapters.json") if work_dir else ""
    if chapters_path and os.path.exists(chapters_path):
        try:
            entries = json.load(open(chapters_path, encoding="utf-8")).get("entries", [])
            if entries:
                return (entries[0].get("url") or "").strip()
        except Exception:
            pass
    up = cfg.get("url_pattern", "")
    if up:
        return up.replace("{eid}", "1")
    return ""


def _site_url_for(wid, cfg):
    """作品原站 URL（首页同步路径，零网络）：r401 系 → r401 分类页；
    yaruobook 系 → work/site_url.json 缓存（无则回退首章 URL，后台补全分类）。"""
    cid = cfg.get("r401_cid")
    if cid:
        return f"https://rss.r401.net/yaruo/categories/{cid}"
    work_dir = cfg.get("work_dir", "")
    cache_path = os.path.join(work_dir, "site_url.json") if work_dir else ""
    if cache_path and os.path.exists(cache_path):
        try:
            v = json.load(open(cache_path, encoding="utf-8")).get("url") or ""
            if v:
                return v
        except Exception:
            pass
    return _site_url_fallback(cfg)


_SITE_URL_BF_LOCK = threading.Lock()
_SITE_URL_BF_FAILED = set()      # 解析过但拿不到 ?cat 的作品：本进程不再重试（防每次刷新都发超时请求）


def _site_url_backfill():
    """后台线程：为无 r401_cid 且未缓存分类页的作品解析 ?cat=N 并写 work/site_url.json（一次性）。

    2026-10-13：失败（站点不是 yaruobook / 解析不出 cat）记进 `_SITE_URL_BF_FAILED`——
    否则每个首页请求都会对同一部作品重发一次 8s 超时的网络请求（实测 商店街迎撃 每次都白等）。
    """
    import registry
    for wid, cfg in registry.WORKS.items():
        if cfg.get("r401_cid") or wid in _SITE_URL_BF_FAILED:
            continue
        work_dir = cfg.get("work_dir", "")
        cache_path = os.path.join(work_dir, "site_url.json") if work_dir else ""
        if cache_path and os.path.exists(cache_path):
            try:
                if json.load(open(cache_path, encoding="utf-8")).get("url"):
                    continue
            except Exception:
                pass
        url = _site_url_fallback(cfg)
        if not url or not url.startswith("http"):
            _SITE_URL_BF_FAILED.add(wid)
            continue
        cat = _find_yaruobook_cat(url)
        if not cat:
            _SITE_URL_BF_FAILED.add(wid)
            continue
        domain = re.sub(r"https?://([^/]+)/.*", r"\1", url)
        site_url = f"https://{domain}/?cat={cat}"
        try:
            os.makedirs(work_dir, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump({"url": site_url}, f, ensure_ascii=False)
        except Exception:
            _SITE_URL_BF_FAILED.add(wid)


def _site_url_backfill_async():
    """首页调用的非阻塞入口：单飞（同时只跑一个 backfill 线程）+ 失败记忆。"""
    if not _SITE_URL_BF_LOCK.acquire(blocking=False):
        return False

    def _run():
        try:
            _site_url_backfill()
        except Exception:
            pass
        finally:
            _SITE_URL_BF_LOCK.release()

    threading.Thread(target=_run, daemon=True).start()
    return True



def _build_index_data(only=None, base=None):
    """构建首页作品卡片**列表**（= 快照内容，见 index_cache 模块头）。

    only = 需要重算的作品 id 集合（其余条目从 base 快照原样复用）；None → 全量重算。
    read/read_state 在此算一次存进快照，渲染时再由 _render_index 按当前进度实时覆盖
    （阅读进度变化频繁且读取廉价，不该等后台重建）。
    """
    works = []
    prog = _load_progress().get("aa", {})
    meta = logic.load_work_meta(_WORK_META_FILE)
    read_states = _load_read_state()
    prev = {}
    for _w in ((base or {}).get("works") or []):
        if isinstance(_w, dict) and _w.get("id"):
            prev[_w["id"]] = _w
    for wid in registry.WORKS:
        cfg = registry.WORKS[wid]
        if only is not None and wid not in only and wid in prev:
            works.append(prev[wid])
            continue
        st = work_stats(wid)
        # 已读章节数 = 保存进度章节在列表中的序号（0 = 未读过）
        read = 0
        saved = prog.get(wid, {}).get("fname")
        if saved:
            for i, c in enumerate(st["chapters"]):
                if c["file"] == saved:
                    read = i + 1
                    break
        # 原文话数（aa/*.txt 计数）→ 未译差值（仅汉化<原文时显示）
        src_total = 0
        try:
            aa_dir = cfg.get("aa_dir")
            if aa_dir and os.path.isdir(aa_dir):
                src_total = sum(1 for f in os.listdir(aa_dir)
                                if logic._is_txt(f))
        except Exception:
            src_total = 0
        deficit = max(0, src_total - st["agg"]["chapters"]) if src_total else 0
        # 已译判定：汉化目录存在且确有 *_汉化.txt 产物（deficit==0 可能是纯原文误报——
        # 未汉化作品 out_dir 缺失时 chapters=aa 原文数 → deficit 恒 0）
        try:
            out_exists = bool(cfg.get("out_dir")) and os.path.isdir(cfg["out_dir"]) and any(
                f.endswith(("_汉化.txt", "_汉化.txt.gz")) for f in os.listdir(cfg["out_dir"]))
        except OSError:
            out_exists = False
        translated = out_exists and deficit <= 0
        # 差异：素材占比（汉化文本数人物名出现次数 → 占比降序）
        m = meta.get(wid, {})
        tag_list = list(m.get("tags", []) or [])
        mat_list = list(m.get("materials", []) or [])
        if m.get("ignore_materials"):
            mat_list = []   # 2026-08-26: jikan_yaluo 等特殊作品忽略素材统计
        mat_with_count = logic.material_frequency_cached(cfg.get("work_dir", ""), cfg.get("out_dir", ""), mat_list)
        # 素材显示名悬浮(2026-08-26): 名称单独显示, 作品出处/次数/章节数放 tooltip
        mats = []
        tot = sum(v["count"] for v in mat_with_count.values() if isinstance(v, dict))
        for name in mat_list:
            v = mat_with_count.get(name)
            if not isinstance(v, dict):
                continue
            c, ch = v.get("count", 0), v.get("chapters", 0)
            dn, wname, note = logic.material_display_parts(name)
            if dn is None:
                continue   # やる夫系原创/无对应作品 → 不显示
            pct = round(c * 100 / tot, 1) if tot else 0.0
            if pct < 5:
                continue   # 2026-08-26: 排除 5% 以下素材
            mats.append({
                "name": dn, "works": wname or "", "note": note or "",
                "count": c, "chapters": ch,
                "pct": pct})
        # 原站 URL（更多菜单「原站」按钮）：r401 系 → r401 分类页；
        # yaruobook 系 → 从首章页面包屑解析 ?cat=N（work/site_url.json 缓存，失败回退首章 URL）
        site_url = _site_url_for(wid, cfg)

        works.append({
            "id": wid,
            "title": cfg.get("title", wid),
            "cn_name": m.get("cn_name", ""),
            "jp_title": m.get("jp_title", "") or cfg.get("title", wid),
            "author": logic.author_display(wid, m, cfg.get("work_dir", ""), cfg.get("aa_dir", ""),
                                           _AUTHOR_OVERRIDE)[0],
            "r18": bool(cfg.get("r18")),
            "r18_level": m.get("r18_level", ""),
            "r18_summary": r18_summary_cached(cfg.get("work_dir", ""), cfg.get("out_dir", "")),
            "site": cfg.get("site_name") or cfg.get("site"),
            # 快照里只留用得到的统计字段（完整 st 含全部章节明细，落盘会白白翻几倍）
            "stats": {"work": st.get("work"), "error": st.get("error"),
                      "agg": st["agg"], "html_out": st.get("html_out"),
                      "html_exists": st.get("html_exists"),
                      "html_mtime": st.get("html_mtime")},
            # 章节文件名顺序：渲染时按当前阅读进度实时算 read（秒级反馈，不用等后台重建）
            "fnames": [c["file"] for c in st["chapters"]],
            "read": read, "total": st["agg"]["chapters"],
            "src_total": src_total, "deficit": deficit,
            "translated": translated,
            # 翻译状态三态（筛选用）：done=已翻译 / doing=翻译中(有汉化产物但差章未译) / none=未翻译
            "tstate": "done" if translated else ("doing" if out_exists else "none"),
            "status": m.get("status", ""),
            "r401_cid": cfg.get("r401_cid", ""),
            "site_url": site_url,
            "tags": tag_list,
            "tags_key": "\x01".join(tag_list),          # 行 data-tags（\x01 分隔防名字含|）
            "mats_key": "\x01".join(mt["name"] for mt in mats),
            "materials": mats,
            "mats_display": mats[:8],          # 2026-08-26: 表格最多显示8个
            "mats_more": len(mats) - 8 if len(mats) > 8 else 0,
            "unsynced": logic.count_unsynced_edits(cfg["work_dir"], st.get("html_mtime")),
            # 2026-09-03 作品列表改版：评分/评价/最后更新/阅读状态
            "rating": m.get("rating"),
            "review": m.get("review", ""),
            "last_update": _last_update_refresh(wid, cfg),
            "read_state": read_states.get(wid, {}).get("state", ""),
        })
    # 2026-09-03 排序改版：作品按最后更新倒序（分组在 _group_index_works 里做）
    works.sort(key=lambda w: w["last_update"], reverse=True)
    return works


def _group_index_works(works):
    """按作者分组（组内 last_update 倒序，组间按组内最新倒序）——构建与渲染共用同一口径。"""
    from collections import OrderedDict
    by_author = OrderedDict()
    for w in works:
        by_author.setdefault(w["author"] or "◆?", []).append(w)
    # 作者组排序：组内最新 last_update 倒序
    ordered = sorted(by_author.items(),
                     key=lambda kv: max(w["last_update"] for w in kv[1]), reverse=True)
    groups = []
    for author, wl in ordered:
        wl.sort(key=lambda w: w["last_update"], reverse=True)
        translated = sum(1 for w in wl if w["translated"])
        _tags = []
        for _w in wl:
            for _t in _w["tags"]:
                if _t not in _tags:
                    _tags.append(_t)
        _ratings = [w.get("rating") for w in wl]
        groups.append({
            "author": author,
            "total": len(wl),
            "translated": translated,
            "untranslated": len(wl) - translated,
            "all_translated": translated == len(wl),
            "works": wl,
            "tags_agg": _tags,
            "avg_rating": logic.avg_rating(_ratings),
        })
    return groups


def _index_filter_values(works):
    """筛选多选全集（2026-09-02）：标签/素材按出现次数降序。"""
    from collections import Counter
    _tc, _mc = Counter(), Counter()
    for _w in works:
        _tc.update(_w["tags"])
        _mc.update(_mt["name"] for _mt in _w["materials"])
    return [k for k, _ in _tc.most_common()], [k for k, _ in _mc.most_common()]


def _rebuild_index_snapshot(only=None):
    """重算首页快照（only=只重算这几部，其余复用当前快照条目）并落盘。"""
    with _BUILD_LOCK:
        base = _INDEX_SNAP.get()
        works = _build_index_data(only=only, base=base)
        # 先清脏再写盘：脏集合随快照一起落盘（顺序反了会把已重算的脏标又写回文件）
        _INDEX_SNAP.clear_dirty(only)
        return _INDEX_SNAP.set(index_cache.wrap({"works": works}))


def _index_refresh_async(force=False):
    """后台重建首页快照（单飞 + 限流）。返回 False = 已有在跑 / 距上次太近。"""
    if not _INDEX_SNAP.begin_refresh(force=force):
        return False

    def _run():
        err = None
        try:
            _rebuild_index_snapshot()
        except Exception as e:        # 重建失败保留旧快照，首页继续可用
            err = repr(e)
        finally:
            _INDEX_SNAP.end_refresh(err)

    threading.Thread(target=_run, daemon=True).start()
    return True


def _render_index(request, snap):
    """用快照渲染首页：作品列表来自快照，阅读进度/阅读状态按当前值实时覆盖。"""
    prog = _load_progress().get("aa", {})
    read_states = _load_read_state()
    works = []
    for _w in (snap.get("works") or []):
        if _w.get("id") not in registry.WORKS:
            continue            # 已删除的作品：快照可能还没重建，渲染时直接跳过
        w = dict(_w)
        wid = w.get("id")
        saved = (prog.get(wid) or {}).get("fname")
        if saved:
            try:
                w["read"] = list(w.get("fnames") or []).index(saved) + 1
            except ValueError:
                w["read"] = w.get("read", 0)
        w["read_state"] = (read_states.get(wid) or {}).get("state", "")
        works.append(w)
    groups = _group_index_works(works)
    all_tags, all_mats = _index_filter_values(works)
    return templates.TemplateResponse(request, "index.html",
                                      {"works": works, "groups": groups,
                                       "all_tags": all_tags, "all_mats": all_mats,
                                       "index_age": index_cache.age_text(_INDEX_SNAP.age()),
                                       "index_built_at": snap.get("built_at") or 0,
                                       "index_refreshing": _INDEX_SNAP.refreshing,
                                       "active": "works"})


@app.get("/")
def index(request: Request):
    """作品列表 —— 只读快照，不再每次扫全库（见 index_cache 模块头）。"""
    # 后台惰性补全 yaruobook 作品分类页 URL（不阻塞首页；单飞 + 失败记忆）
    _site_url_backfill_async()
    snap = _INDEX_SNAP.get()
    if not snap or not snap.get("works"):
        # 首次（无快照/快照损坏）：同步建一次并落盘；并发请求只建一次
        with _BUILD_LOCK:
            snap = _INDEX_SNAP.get()
            if not snap or not snap.get("works"):
                snap = _rebuild_index_snapshot()
        return _render_index(request, snap)
    dirty = _INDEX_SNAP.dirty()
    if dirty:
        if _INDEX_SNAP.needs_full_rebuild() or len(dirty) > INDEX_SYNC_DIRTY_MAX:
            _index_refresh_async(force=True)        # 大批脏：后台重建，本次先用旧值
        else:
            snap = _rebuild_index_snapshot(only=dirty)   # 少量脏：同步局部重算，改完立刻可见
    elif _INDEX_SNAP.should_refresh():
        _index_refresh_async()                      # 快照过期：后台刷新，本次先用旧值
    return _render_index(request, snap)


@app.get("/api/index/status")
def api_index_status():
    """首页快照状态（前端「刷新数据」按钮轮询）。"""
    return {"ok": True, "built_at": (_INDEX_SNAP.get() or {}).get("built_at") or 0,
            "age": _INDEX_SNAP.age(), "age_text": _INDEX_SNAP.age_text_now(),
            "refreshing": _INDEX_SNAP.refreshing, "dirty": sorted(_INDEX_SNAP.dirty()),
            "error": _INDEX_SNAP.last_error}


@app.post("/api/index/refresh")
def api_index_refresh():
    """手动触发后台重建首页快照（忽略限流）。"""
    started = _index_refresh_async(force=True)
    return {"ok": True, "started": started, "refreshing": _INDEX_SNAP.refreshing}


_LASTUPD_CACHE = {}
_LASTUPD_TTL = 300


def last_update_cached(work, cfg):
    """作品 last_update 内存 TTL 缓存（首页每作品调用；与 _STATS_CACHE 同 TTL，invalidate_stats 一并清）。"""
    hit = _LASTUPD_CACHE.get(work)
    now = time.time()
    if hit and now - hit[0] < _LASTUPD_TTL:
        return hit[1]
    v = logic.last_update_of(cfg.get("aa_dir", ""), cfg.get("out_dir", ""), cfg.get("work_dir", ""))
    _LASTUPD_CACHE[work] = (now, v)
    return v


def _last_update_refresh(work, cfg):
    """重算并写回 last_update 磁盘缓存（**快照重建路径**用）。

    快照重建时顺手 refresh：last_update 值 = aa/汉化 顶层文件 max mtime，
    `last_update_cached`（refresh=False）只读缓存，若不主动刷新，重启后首页会一直
    显示上次刷新时刻的旧时间。scandir 很快（全库 87 个 aa/ 实测 0.14s）。
    """
    v = logic.last_update_of(cfg.get("aa_dir", ""), cfg.get("out_dir", ""),
                             cfg.get("work_dir", ""), refresh=True)
    _LASTUPD_CACHE[work] = (time.time(), v)
    return v


@app.get("/work/{work}")
def work_page(request: Request, work: str):
    cfg = cfg_of(work)
    st = work_stats(work)
    gen_rel, gen_args = generator_of(work)
    resume = _load_progress().get("aa", {}).get(work, {})
    resume_title = ""
    if resume.get("fname"):
        for c in st["chapters"]:
            if c["file"] == resume["fname"]:
                resume_title = c["title"] or c["file"]
                break
    # 2026-08-28 候选确认: 未确认候选数
    cand_n = 0
    _cp = os.path.join(cfg["work_dir"], "glossary_candidates.json")
    if os.path.exists(_cp):
        try:
            _cd = json.load(open(_cp, encoding="utf-8"))
            cand_n = len([c for c in _cd.get("ai_候选", []) if not c.get("确认")])
        except Exception:
            pass
    return templates.TemplateResponse(request, "work.html", {
        "cand_n": cand_n,
        "work": work, "cfg": cfg, "stats": st, "resume": resume,
        "resume_title": resume_title,
        "unsynced": logic.count_unsynced_edits(cfg["work_dir"], st.get("html_mtime")),
        "generator": f"{gen_rel} {' '.join(gen_args)}".strip(),
        "wiki_ready": TM.wiki_configured(work),
        "active": "works",
    })


@app.get("/work/{work}/read")
def reader_page(request: Request, work: str, f: str = ""):
    """阅读器：240px 深色侧边栏 + 单章渲染（帖头颜色/骰子红/1D10 加粗 + 原帖链接）。

    目录 = 合并总目录（原文 aa/*.txt 全集，标注未译章，优先显示汉化）；
    默认模式：有汉化的章显示汉化，无汉化的章显示原文；切换按钮本地双渲染。
    永远只渲染一章，38MB 级大作品也不卡；键盘 ← → 翻章。URL: ?f=<章节文件名>"""
    cfg = cfg_of(work)
    aa_dir, out_dir = cfg["aa_dir"], cfg["out_dir"]
    order_path = os.path.join(cfg["work_dir"], "chapter_order.json")
    mch = logic.merged_chapters(aa_dir, out_dir, cfg.get("name_style"), order_path)
    if not mch:
        raise HTTPException(404, f"作品 {work} 暂无章节（aa 原文目录为空）")
    resume_line = 0
    saved = _load_progress().get("aa", {}).get(work, {})

    def _same_chapter(ch):
        """请求的章 == 存档的章（原文名或汉化名任一命中）→ 才续读，避免跨章误跳"""
        sf = saved.get("fname", "")
        return bool(sf) and sf in (ch["tr_file"], ch["src_file"])

    # 当前章：优先显式 f（支持 原文名 或 汉化名）；否则续读；否则第一章
    if f:
        fname = None
        for ch in mch:
            if f in (ch["tr_file"], ch["src_file"]):
                fname = ch
                break
        if fname is None:
            fname = mch[0]
        # 2026-09-21：显式 f 也续读——URL 里的章就是存档章时（刷新 / 书签 / 返回），
        # 否则刷新页面跳回章首（用户实测「刷新后不在原位置」）
        if _same_chapter(fname) and saved.get("line"):
            resume_line = int(saved.get("line", 0) or 0)
    else:
        sf = saved.get("fname", "")
        fname = None
        for ch in mch:
            if sf in (ch["tr_file"], ch["src_file"]):
                fname = ch
                break
        if fname is None:
            fname = mch[0]
        if saved.get("line"):
            resume_line = int(saved.get("line", 0) or 0)
    cur_idx = mch.index(fname)
    # 默认显示汉化（无汉化 → 原文）
    use_tr = fname["has_tr"]
    base = logic.txt_key(fname["src_file"])
    # 章节上下文（2026-09-21 S1，reader_win）：行文本/colormap/meta/override/sfx/gold 一次装载并缓存，
    # 行 HTML 按需渲染（区间 API）。旧实现每次请求把整章 ×3 份渲染成 JSON 内联下发（大章 12–22 MB、
    # DOM 6–10 万节点）。本函数输出保持与旧实现**逐字节一致**（S1 有 curl A/B 锁定），S2 起才换轻量数据。
    ctx = reader_win.load_ctx(cfg, work, fname)
    gold = ctx["gold"]
    # 窗口化（S2/S3）：只下发几何表，行 HTML 由前端按窗口向 /range 区间接口取（页面体积与行数无关）
    win_data = reader_win.page_data(ctx, "tr" if use_tr else "src")
    # 逃生口（方案 §6）：?win=0 → 前端全量渲染（整章 DOM，排障/对照；见 static/reader.js 的 FULL）
    win_mode = request.query_params.get("win", "1") != "0"
    title, src_url = fname["title"], fname["src_url"]
    if not src_url:
        m = logic.EID_RE.search(fname["src_file"])
        if m:
            src_url = cfg["url_pattern"].format(eid=m.group(1))
    # 章节简介（2026-09-02）：作品知识库 章节简介 块，阅读页头部小字展示
    # 拆两段：剧情（模板 clamp 2 行）+ 末尾角色罗列换行展示
    sum_plot, sum_chars = _split_summary(_chapter_summary(work, cfg, base))
    # 侧边栏目录：完整标题（汉化优先，保留副标题），未译章标「未译」
    # 汉化标题翻译缓存：<work>/title_translations.json 键=日文标题 → 值=汉化标题（touhou 模式）
    tcache = {}
    tcache_path = os.path.join(cfg["work_dir"], "title_translations.json")
    if os.path.exists(tcache_path):
        try:
            tcache = json.load(open(tcache_path, encoding="utf-8"))
        except Exception:
            tcache = {}
    labels = {}
    # 2026-08-28: 缓存键可能是剥作品名前缀的标题(translate_titles.py 剥前缀),
    # ch["title"] 含全前缀 → 直查会 miss。同时尝试剥前缀键。
    _pref = (cfg.get("title") or "").strip()
    for ch in mch:
        raw = ch["title"] or logic.txt_key(ch["src_file"])
        # 汉化章：优先翻译缓存（日文标题→汉化），否则汉化文件首行本身
        _bare = raw
        if _pref and raw.startswith(_pref):
            _bare = raw[len(_pref):].lstrip("\u3000 ").strip()
        lab = tcache.get(_bare) or tcache.get(raw) or raw
        labels[ch["src_file"]] = toc_label(lab, cfg.get("title", ""))
    # 目录条目：src_file 作为 URL 锚点，前端根据模式切换正文（本地双渲染）
    toc = []
    for ch in mch:
        _r18j = logic.r18_chapter_judge(cfg.get("work_dir", ""),
                                        logic.txt_key(ch["src_file"])) or {}
        toc.append({
            "src_file": ch["src_file"], "tr_file": ch["tr_file"],
            "has_tr": ch["has_tr"],
            "label": labels[ch["src_file"]],
            "title": ch["title"],
            "active": ch is fname,
            # 2026-08-24 R18 章节级（读 prep）
            "r18_hit": _r18j.get("r18_hit", ""),
            "r18_hits": _r18j.get("r18_word_hits", 0),
        })
    # 2026-08-28 候选确认: ?cand=1 时注入候选列表 + 各候选来源章 term 命中行号
    cand_data = None
    if request.query_params.get("cand") == "1":
        cand_p = os.path.join(cfg["work_dir"], "glossary_candidates.json")
        if os.path.exists(cand_p):
            try:
                _cd = json.load(open(cand_p, encoding="utf-8"))
                _cands = [c for c in _cd.get("ai_候选", []) if not c.get("确认")]
                _t_order = {"术语": 0, "专有名词": 1, "拟声梗": 2, "角色": 3}
                def _ck(c):
                    srcs = str(c.get("来源") or "")
                    return (_t_order.get(c.get("type") or "", 9), -len([x for x in srcs.split(",") if x.strip()]))
                _cands.sort(key=_ck)
                # 命中行号: 对每个候选, 遍历其来源章原文, 找含 term 的行 idx
                for c in _cands:
                    term = (c.get("term") or "").strip()
                    c["命中"] = {}
                    if not term:
                        continue
                    for src in str(c.get("来源") or "").split(","):
                        src = src.strip()
                        if not src:
                            continue
                        # 来源是 _台词.txt 文件名 → 归一为原文 base(去 _台词 后缀), 供阅读器按 base 匹配
                        base = src[:-len("_台词.txt")] if src.endswith("_台词.txt") else logic.txt_key(src).replace("_台词", "")
                        sp = os.path.join(cfg["aa_dir"], base + ".txt")
                        if not os.path.exists(sp):
                            continue
                        try:
                            lines = logic.read_lines(sp)
                        except Exception:
                            continue
                        hits = [i for i, l in enumerate(lines) if term in l]
                        if hits:
                            c["命中"][base] = hits[:20]
                # ?ci=<n> 初始候选索引(候选确认跳章还原进度)
                _ci = request.query_params.get("ci", "")
                try:
                    _ci_i = int(_ci) if str(_ci).isdigit() else 0
                except Exception:
                    _ci_i = 0
                cand_data = {"candidates": _cands, "total": len(_cands), "init_idx": _ci_i}
            except Exception:
                cand_data = None
    return templates.TemplateResponse(request, "reader.html", {
        "work": work, "cfg": cfg, "toc": toc, "mch": mch,
        # 翻译替换弹窗的「章节多选过滤层」（2026-10-01）：只列已汉化章，按阅读器目录顺序
        "tr_chapters": [{"file": ch["tr_file"], "label": labels[ch["src_file"]],
                         "num": i + 1} for i, ch in enumerate(toc) if ch["has_tr"]],
        "cur": fname["src_file"], "cur_idx": cur_idx,
        "has_tr": fname["has_tr"], "use_tr": use_tr,
        "title": title, "src_url": src_url,
        "summary_plot": sum_plot, "summary_chars": sum_chars,
        "win_data": win_data, "win_mode": win_mode,
        "total": len(mch), "resume_line": resume_line, "active": "works",
        "cand_data": cand_data, "gold": gold,
    })


_KN_SUM_CACHE = {}  # 章节简介缓存: path -> [mtime, size, {"base": 简介}]


def _chapter_summary(work, cfg, base):
    """读作品知识库章节简介（work/<work>_knowledge.json 的 章节简介 块，键=章节 base）。

    无知识库/无本章/失败哨兵 __summary_failed__ → 返回空串（模板不展示）。
    (mtime,size) 指纹缓存，避免每次翻章重读知识库 JSON。
    """
    kn_p = os.path.join(cfg.get("work_dir", ""), work + "_knowledge.json")
    if not os.path.exists(kn_p):
        return ""
    try:
        st = os.stat(kn_p)
        ent = _KN_SUM_CACHE.get(kn_p)
        if not ent or ent[0] != st.st_mtime or ent[1] != st.st_size:
            kn = json.load(open(kn_p, encoding="utf-8"))
            ent = [st.st_mtime, st.st_size, kn.get("章节简介", {}) or {}]
            _KN_SUM_CACHE[kn_p] = ent
        s = str(ent[2].get(base, "") or "").strip()
        if not s or s == "__summary_failed__":
            return ""
        return s
    except Exception:
        return ""


# 知识库章节简介结尾的角色罗列：先匹配带标签格式「主要角色：A、B、C。」，再匹配无标签格式
_SUM_LABEL_RE = re.compile(
    r"(主要角色|登场角色|出场角色|登場人物|主要人物|登场人物)\s*[:：]\s*"
    r"([一-鿿\w·・]+(?:、[一-鿿\w·・]+)+)[。．]?$"
)
_SUM_NAMES_RE = re.compile(r"([一-鿿\w·・]+(?:、[一-鿿\w·・]+)+)[。．]?$")


def _split_summary(s):
    """章节简介拆 (剧情, 角色罗列)。

    gen_chapter_summaries 生成的简介结尾是角色罗列句，两种格式：
    - 新格式: 「剧情。A、B、C。」
    - 旧格式: 「剧情。主要角色：A、B、C。」（带标签，存量 493 条）
    角色罗列独立成行展示，标签不进剧情行。
    """
    s = (s or "").strip()
    if not s:
        return "", ""
    m = _SUM_LABEL_RE.search(s)
    if m:
        plot = s[: m.start()].rstrip("。． \u3000")
        return plot, m.group(2)
    m = _SUM_NAMES_RE.search(s)
    if m:
        plot = s[: m.start()].rstrip("。． \u3000")
        if plot:
            return plot, m.group(1)
    return s, ""




def gen_aa_html_render(i, l, cm, site, src_line=None, sfx_marks=None):
    return _gen_aa_html.render_line(i, l, cm, site, src_line, sfx_marks)


def toc_label(lab, work_title):
    """阅读器目录标题清洗：去「（汉化）」字样 + 剥离作品名前缀。

    - 去「（汉化）」「(汉化)」尾巴（白头汉化标题自带）
    - 剥离作品名前缀：找「第N话/話/回」首次出现（N 支持阿拉伯/汉字数字）；
      若其前缀包含作品名 → 从话数处截断，保留副标题；
      前缀不含作品名（如白头『回归篇 第一话…』、touhou『第2話…』）
      或短篇集（标题无话数、是独立作品名）→ 保留完整。
    - 尾部站点名「 - xxxまとめ」/「| YomuAA」剥掉。
    """
    s = lab.strip()
    for suf in ("（汉化）", "(汉化)"):
        if s.endswith(suf):
            s = s[: -len(suf)].rstrip()
    # 尾部站点名
    for sep in (" - ", " | "):
        if sep in s:
            head, tail = s.split(sep, 1)
            if tail and re.search(r"まとめ|Yomu|チェック|r401|本棚", tail):
                s = head
    if work_title:
        m = re.search(r"第\s*[0-9０-９一二三四五六七八九十百千]+\s*[话話回]", s)
        if m:
            prefix = s[: m.start()]
            if work_title in prefix:
                s = s[m.start():]
        else:
            # 「そのN」「その(N)」格式（无第N话）
            m = re.search(r"その\s*[0-9０-９一二三四五六七八九十百千()（）]+", s)
            if m and work_title in s[: m.start()]:
                s = s[m.start():]
            else:
                # 作品名 + 末尾话数（如 kikkoman『…キッコーマン～　１０８』）
                i = s.find(work_title)
                if i != -1:
                    rest = s[i + len(work_title):].strip()
                    if rest and re.fullmatch(r"[0-9０-９　\s]+", rest):
                        s = rest.strip()
    return s or lab


_FULLW_DIGITS = str.maketrans('０１２３４５６７８９', '0123456789')


def short_chapter_label(lab, fname):
    """阅读器目录短标签：只留话数，不含章节标题。

    提取优先级：标题内「第N话/第N話」（取第一个）→ 标题末尾的全角/半角话数
    （如 kikkoman「…～　０５」）→ 文件名 eid 回退「番外#eid」。
    正文页顶 h2 仍显示完整标题，这里只压缩目录文字。"""
    s = lab.translate(_FULLW_DIGITS)          # 全角数字归一，便于匹配
    m = re.search(r'第\s*(\d+)\s*[话話]', s)
    if m:
        return f'第{int(m.group(1))}话'
    m = re.search(r'[　\s](?:第\s*)?(\d{1,4})\s*$', s)
    if m:
        return f'第{int(m.group(1))}话'
    m = logic.EID_RE.search(fname)
    if m:
        return f'番外#{m.group(1)}'
    return lab


@app.get("/glossary")
def glossary_page(request: Request):
    with open(logic._GLOSSARY_JSON, encoding="utf-8") as f:
        data = json.load(f)
    # 2026-09-01 三级重构: 角色块迁出 glossary.json → roles.json, 前端读 roles
    try:
        with open(os.path.join(_DATA, "roles.json"), encoding="utf-8") as f:
            roles_data = json.load(f)
    except Exception:
        roles_data = {"schema": "AA角色表 v1", "更新日期": "", "角色": []}
    works = [{"id": wid, "title": cfg.get("title", wid)}
             for wid, cfg in registry.WORKS.items()]
    return templates.TemplateResponse(request, "glossary.html",
                                      {"data": data, "roles_data": roles_data, "works": works,
                                       "active": "glossary"})


# ---------- 揭示板（本地浏览披露板 dat，2026-08-23） ----------

BOARD_PER_PAGE = 400      # 每页渲染的楼数（dat 大贴 5001 楼，整章渲染会卡）
BOARD_ROOT = logic.BOARD_ROOT


def _safe_board_author(author):
    """作者目录名校验：接受显示名（原名）或别名，解析为物理目录（防路径穿越）。"""
    d = logic.board_dir(author)
    if not d:
        return None
    full = os.path.join(BOARD_ROOT, d)
    return full if (os.path.isdir(full) and os.path.realpath(full).startswith(
        os.path.realpath(BOARD_ROOT) + os.sep)) else None


def board_author_entry(author, file=None):
    """返回渲染揭示板作者页所需上下文；贴不存在时 raise HTTPException。"""
    d = _safe_board_author(author)
    if not d:
        raise HTTPException(404, f"未知揭示板连载: {author}")
    display = logic.board_display_name(os.path.basename(d))
    slaves = logic.board_slaves(display)
    if not slaves:
        raise HTTPException(404, f"{author} 下没有贴")
    # 当前贴：显式 file，否则第一篇
    if file:
        # 允许传文件名或 eid
        cur = next((s for s in slaves if s["file"] == file or s["eid"] == file), None)
        if not cur:
            raise HTTPException(404, f"贴不存在: {file}")
        idx = slaves.index(cur)
    else:
        idx = 0
    # 解析当前贴
    parsed = logic.parse_board_dat(os.path.join(d, slaves[idx]["file"]))
    # index.json 权威标题优先（dat 内 1 楼 AA 可能复用开场横幅，猜不准每スレ标题）
    if slaves[idx].get("title"):
        parsed["title"] = slaves[idx]["title"]
    return {"author": display, "slaves": slaves, "cur": slaves[idx],
            "cur_idx": idx, "parsed": parsed}


@app.get("/board")
def board_index(request: Request):
    cols = logic.board_collections()
    totals = {"collections": len(cols), "slaves": sum(len(c["slaves"]) for c in cols)}
    return templates.TemplateResponse(request, "board.html", {
        "cols": cols, "totals": totals, "active": "board",
        "author": None, "cur": None, "cur_idx": 0, "parsed": None,
        "slaves": [], "page": 1, "total_pages": 0})


@app.get("/board/{author}")
def board_reader(request: Request, author: str, file: str = "", page: int = 1):
    ctx = board_author_entry(author, file)
    parsed = ctx["parsed"]
    total_res = len(parsed["res"])
    total_pages = max(1, (total_res + BOARD_PER_PAGE - 1) // BOARD_PER_PAGE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * BOARD_PER_PAGE
    chunk = parsed["res"][start:start + BOARD_PER_PAGE]
    return templates.TemplateResponse(request, "board.html", {
        "cols": logic.board_collections(), "totals": {}, "active": "board",
        "author": author, "slaves": ctx["slaves"],
        "cur": ctx["cur"], "cur_idx": ctx["cur_idx"],
        "parsed": parsed, "res": chunk, "total_res": total_res,
        "page": page, "total_pages": total_pages})


@app.get("/api/board/search")
def board_search_api(request: Request, author: str, q: str, limit: int = 200):
    """揭示板全局搜索：在 author 的全部 dat 里搜 q，返回结果（含跳转 page/no）。"""
    d = _safe_board_author(author)
    if not d:
        raise HTTPException(404, f"未知揭示板连载: {author}")
    results = logic.board_search(author, q, limit=min(limit, 500))
    # 每条补跳转页：按 400 楼/页 算出命中楼所在分页
    for r in results:
        r["page"] = (r["no"] - 1) // BOARD_PER_PAGE + 1
    return {"author": author, "q": q, "count": len(results), "results": results}


# ---------- 揭示板 下载/更新（2026-10-10；runner = scripts/runner/board_runner.py） ----------

BOARD_DEFAULT_HOST = "http://yarufox.sakura.ne.jp"


class BoardDownload(BaseModel):
    """揭示板下载/更新入队参数（工作台 /board 页表单 + 作者页「更新」按钮）。"""
    board: str = "FOX"                 # 板 id（如 FOX）
    host: str = BOARD_DEFAULT_HOST     # 板站点根
    match: str = ""                    # 标题关键词（如 ポケの子）
    author: str = ""                   # 作者 trip（校验用，如 ◆LXwkncCAp.）
    dir: str = ""                      # 揭示板目录（默认取 author/match）
    title: str = ""                    # 作品名（写 index.json）
    work_id: str = ""                  # 作品 id（写 index.json，可选）
    label: str = ""                    # 连载标签（写 index.json，可选）
    kako_from: int | None = None       # kako dir1 下界（eid 前 4 位）
    kako_to: int | None = None         # kako dir1 上界
    scan_kako: bool = False            # 强制扫 kako 索引（首次收录历史贴用）
    force: bool = False                # 已存在的 .dat 也重抓


def _board_index_meta(author):
    """读 揭示板/<dir>/index.json 的板信息（兼容既有两种 schema）。"""
    d = _safe_board_author(author)
    if not d:
        return {}
    p = os.path.join(d, "index.json")
    try:
        with open(p, encoding="utf-8") as f:
            idx = json.load(f)
    except Exception:
        return {}
    # board 字段两种写法：`yarufox.sakura.ne.jp/FOX` / `やる夫板のシェルター (yaruoshelter.com /yaruo001/)`
    raw = str(idx.get("board") or idx.get("掲示板") or "")
    m = re.search(r'([A-Za-z0-9.\-]+\.[A-Za-z]{2,})[^\w]*/([A-Za-z0-9_]+)', raw)
    host, board = ("", "")
    if m:
        host, board = m.group(1), m.group(2)
    slaves = idx.get("slaves") or idx.get("スレ一覧") or []
    eids = [str(s.get("eid") or "") for s in slaves if str(s.get("eid") or "").isdigit()]
    return {
        "dir": os.path.basename(d),
        "board": idx.get("board_id") or board,
        "host": idx.get("host") or (("http://" + host) if host else ""),
        "match": idx.get("match") or "",
        "author": idx.get("author") or "",
        "title": idx.get("work_title") or idx.get("作品") or "",
        "work_id": idx.get("work") or "",
        "label": (idx.get("range") or {}).get("label") or idx.get("備考") or "",
        "slaves": len(slaves),
        "eid_min": min(eids) if eids else "",
        "eid_max": max(eids) if eids else "",
        "updated": idx.get("updated") or "",
    }


def _board_enqueue(p: dict):
    """入队揭示板下载/更新任务；返回 {ok, task_id, ...}。"""
    board = (p.get("board") or "FOX").strip()
    host = (p.get("host") or BOARD_DEFAULT_HOST).strip().rstrip("/")
    dirname = (p.get("dir") or p.get("author") or p.get("match") or "").strip().replace("/", "_")
    if not dirname:
        raise HTTPException(422, "缺少揭示板目录（dir / author / match 至少给一个）")
    if not (p.get("match") or p.get("author")):
        raise HTTPException(422, "缺少标题关键词（match）或作者 trip（author）——否则会把整块板全抓下来")
    params = {k: p.get(k) for k in ("match", "author", "title", "work_id", "label",
                                    "kako_from", "kako_to", "scan_kako", "force")}
    params.update({"board": board, "host": host, "dir": dirname})
    params["kako_from"] = p.get("kako_from") if p.get("kako_from") not in ("", None) else None
    params["kako_to"] = p.get("kako_to") if p.get("kako_to") not in ("", None) else None
    params = {k: v for k, v in params.items() if v not in (None, "")}
    # work 用伪 id `board:<板id>`：非 registry 作品，同板串行（该站禁并发）
    return TM.enqueue("board", f"board:{board}", params)


@app.post("/api/board/download")
def api_board_download(payload: BoardDownload):
    """入队揭示板下载/更新（零 token）。"""
    return _board_enqueue(dict(payload))


@app.post("/api/board/{author}/update")
def api_board_update(author: str, payload: BoardDownload):
    """按 index.json 记的板信息更新该作者的全部贴（增量：活跃贴 + 已归档 eid 直接重抓）。"""
    meta = _board_index_meta(author)
    if not meta:
        raise HTTPException(404, f"揭示板 {author} 无 index.json，无法自动更新——请在 /board 页用表单指定板/关键词")
    p = {
        "board": payload.board if payload.board != "FOX" else (meta.get("board") or "FOX"),
        "host": (payload.host if payload.host != BOARD_DEFAULT_HOST else "") or meta.get("host") or BOARD_DEFAULT_HOST,
        "match": meta.get("match") or "",
        "author": meta.get("author") or "",
        "dir": meta.get("dir") or author,
        "title": meta.get("title") or "",
        "work_id": meta.get("work_id") or "",
        "label": meta.get("label") or "",
        "kako_from": payload.kako_from,
        "kako_to": payload.kako_to,
        "scan_kako": payload.scan_kako,
        "force": payload.force,
    }
    return _board_enqueue(p)


@app.get("/api/board/task")
def api_board_task(dir: str = ""):
    """该揭示板目录最近一次下载/更新任务（前端轮询进度用）。"""
    latest = None
    for t in TM.list_tasks():
        if t.get("type") != "board":
            continue
        if dir and (t.get("params") or {}).get("dir") != dir:
            continue
        if latest is None or (t.get("created_ts") or 0) > (latest.get("created_ts") or 0):
            latest = t
    return {"ok": True, "dir": dir, "task": _task_public(latest, log_tail=8) if latest else None}


@app.get("/api/board/{author}/info")
def api_board_info(author: str):
    """揭示板板信息（作者页「更新」按钮预填 + 显示来源）。"""
    meta = _board_index_meta(author)
    d = _safe_board_author(author)
    if not d:
        raise HTTPException(404, f"未知揭示板连载: {author}")
    meta["exists"] = True
    return {"ok": True, "meta": meta}


# ---------- 作品专属术语表（每作品独立文件 glossary_<work_id>.json） ----------

class WorkGlossary(BaseModel):
    glossary: list = []
    known_names: list = []
    # 2026-09-19 增量保存: 显式删除项 + 模式（merge=默认增量 / replace=整表覆盖）
    deleted: list = []
    deleted_names: list = []
    mode: str = "merge"
    force: bool = False


@app.get("/api/glossary/{work}")
def api_work_glossary(work: str):
    cfg_of(work)
    return logic.load_work_glossary(work)


@app.put("/api/glossary/{work}")
def api_work_glossary_put(work: str, body: WorkGlossary):
    """保存作品术语表。

    2026-09-19（用户要求）: **默认增量保存（mode=merge）**——只 upsert 提交的条目 +
    删除 deleted 里显式列出的 jp，文件里未提及的条目原样保留。旧「整表覆盖」语义
    下没加载就点保存（或旧标签页拿着空表保存）会把整部作品的术语写空
    （チンポ_メガテン 事故）；增量语义下同一次误点最多 no-op。
    mode=replace 保留为显式覆盖（清空整表需 force=true，防误清）。
    """
    cfg_of(work)
    with _LOCK:
        if body.mode == "replace":
            cur = logic.load_work_glossary(work)
            if (cur["glossary"] or cur["known_names"]) and not body.glossary \
                    and not body.known_names and not body.force:
                raise HTTPException(422, "整表覆盖会清空现有 %d 对 / %d 白名单；"
                                         "确认请带 force=true" % (len(cur["glossary"]),
                                                                len(cur["known_names"])))
            ok, err = logic.save_work_glossary(work, body.glossary, body.known_names)
            cur2 = logic.load_work_glossary(work)
            stat = {"added": len(body.glossary), "updated": 0, "kept": 0,
                    "removed": 0, "names_added": len(cur2["known_names"]),
                    "names_removed": 0, "total": len(cur2["glossary"]),
                    "names_total": len(cur2["known_names"])}
            mode = "replace"
        else:
            ok, err, stat = logic.merge_work_glossary(
                work, body.glossary, body.known_names, body.deleted, body.deleted_names)
            mode = "merge"
    if not ok:
        raise HTTPException(500, err)
    invalidate_stats(work)      # 残留白名单/术语变化 → 统计失效重算
    return {"ok": True, "mode": mode, "glossary": stat["total"],
            "known_names": stat["names_total"], **stat}


class GlossaryPairAdd(BaseModel):
    jp: str = ""
    zh: str = ""
    cat: str = "专名"


@app.post("/api/glossary/{work}/add-pair")
def api_work_glossary_add_pair(work: str, body: GlossaryPairAdd):
    """阅读页「翻译替换 → 历史替换记录 → ＋术语表」：单条历史记录手动加入作品术语表（2026-09-14）。

    服务端 load→append→save（走 _LOCK），**不复用整表 PUT**——前端「读→改→写」整表
    有竞态，会顺手覆盖别处刚存的条目。已存在同 jp 不覆盖，把现状回给前端提示。
    """
    cfg_of(work)
    with _LOCK:
        r = logic.add_work_glossary_pair(work, body.jp, body.zh, body.cat)
    if r.get("reason") == "error":
        raise HTTPException(500, r.get("error") or "保存失败")
    if r.get("added"):
        invalidate_stats(work)      # 术语表变化 → 残留白名单/统计失效
    return r


# ========== 作品级元数据（work_meta）：详情弹窗编辑 汉化名/标签/素材/作者 ==========

class WorkMeta(BaseModel):
    author: str = ""
    cn_name: str = ""
    status: str = ""
    intro: str = ""
    tags: list = []
    materials: list = []
    r18_level: str = ""   # 2026-08-26: high/low/medium/空(no) 人工可改
    rating: int | None = None   # 2026-09-03: 评分 1-5，缺省=未评分
    review: str = ""          # 2026-09-03: 评价文字
    pipeline: str = ""        # 2026-09-22: 管线归属 generic/pokemon(空=不改)


@app.get("/api/work/{work}/candidates")
def api_candidates(work: str):
    """候选确认列表（已排序：type 顺序 + 来源章节数降序；跳过条目不返回）。"""
    import json as _j, os as _os
    try:
        cfg = cfg_of(work)
    except HTTPException:
        return {"ok": False, "error": "未知作品"}
    _, _, wd = paths_of(cfg)
    cand_p = _os.path.join(wd, "glossary_candidates.json")
    if not _os.path.exists(cand_p):
        return {"ok": True, "candidates": [], "total": 0}
    with _LOCK:
        data = _j.load(open(cand_p, encoding="utf-8"))
    cands = [c for c in data.get("ai_候选", []) if not c.get("确认")]
    # 排序: type 顺序 + 来源章节数降序(≤3)
    _t_order = {"术语": 0, "专有名词": 1, "拟声梗": 2, "角色": 3}
    def _key(c):
        srcs = c.get("来源") or ""
        n = len([x for x in str(srcs).split(",") if x.strip()])
        return (_t_order.get(c.get("type") or "", 9), -n)
    cands.sort(key=_key)
    return {"ok": True, "candidates": cands, "total": len(cands)}


@app.post("/api/work/{work}/candidates/{term}/confirm")
def api_candidate_confirm(work: str, term: str, body: dict = None):
    """确认候选（设 确认:true + 更新译名/作品/别名_of）。"""
    import json as _j, os as _os
    from urllib.parse import unquote
    term = unquote(term)
    try:
        cfg = cfg_of(work)
    except HTTPException:
        return {"ok": False, "error": "未知作品"}
    _, _, wd = paths_of(cfg)
    cand_p = _os.path.join(wd, "glossary_candidates.json")
    if not _os.path.exists(cand_p):
        return {"ok": False, "error": "无候选文件"}
    with _LOCK:
        data = _j.load(open(cand_p, encoding="utf-8"))
        hit = None
        for c in data.get("ai_候选", []):
            if (c.get("term") or "") == term:
                hit = c
                break
        if hit is None:
            return {"ok": False, "error": f"候选不存在: {term}"}
        body = body or {}
        if "translation" in body and body["translation"] is not None:
            hit["translation"] = str(body["translation"]).strip()
        if "作品" in body and body["作品"] is not None:
            hit["作品"] = str(body["作品"]).strip()
        if "别名_of" in body and body["别名_of"] is not None:
            hit["别名_of"] = str(body["别名_of"]).strip()
        # 2026-09-07: type 支持人工改(角色/术语/专有名词 → promote cat 落表正确)
        if "type" in body and body["type"] is not None:
            _t = str(body["type"]).strip()
            _ok_t = {"角色": "角色", "术语": "术语", "专有名词": "专有名词", "专名": "专有名词", "人名": "角色", "人物": "角色"}
            if _t in _ok_t:
                hit["type"] = _ok_t[_t]
        hit["确认"] = True
        _j.dump(data, open(cand_p, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=2)
    return {"ok": True, "term": term}


@app.post("/api/work/{work}/candidates/{term}/delete")
def api_candidate_delete(work: str, term: str):
    """永久删除候选（从文件移除）。"""
    import json as _j, os as _os
    from urllib.parse import unquote
    term = unquote(term)
    try:
        cfg = cfg_of(work)
    except HTTPException:
        return {"ok": False, "error": "未知作品"}
    _, _, wd = paths_of(cfg)
    cand_p = _os.path.join(wd, "glossary_candidates.json")
    if not _os.path.exists(cand_p):
        return {"ok": False, "error": "无候选文件"}
    with _LOCK:
        data = _j.load(open(cand_p, encoding="utf-8"))
        before = len(data.get("ai_候选", []))
        data["ai_候选"] = [c for c in data.get("ai_候选", []) if (c.get("term") or "") != term]
        if len(data["ai_候选"]) == before:
            return {"ok": False, "error": f"候选不存在: {term}"}
        _j.dump(data, open(cand_p, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=2)
    return {"ok": True, "term": term}


@app.post("/api/work/{work}/candidates/{term}/whitelist")
def api_candidate_whitelist(work: str, term: str):
    """候选「白名单」（2026-09-20）：词记入**本作品**表 known_names + 移除本条候选。

    用户要求（2026-09-20）：只记作品白名单，**不动全局** glossary.json——要不要进全局由作品页
    「可反哺全局的条目」区块手动勾选（`POST .../repatriate-apply`，类型=白名单）。
    写作品表走 `logic.merge_work_glossary`（增量并集，表内其余条目原样保留）。
    """
    import json as _j, os as _os
    from urllib.parse import unquote
    term = unquote(term).strip()
    if not term:
        return {"ok": False, "error": "空词"}
    try:
        cfg = cfg_of(work)
    except HTTPException:
        return {"ok": False, "error": "未知作品"}
    with _LOCK:
        ok, err, stat = logic.merge_work_glossary(work, known_names=[term])
    if not ok:
        return {"ok": False, "error": err or "作品表写入失败"}
    # 本作品候选文件里移除该条（与 delete 同口径：已确认/未确认都移除）
    _, _, wd = paths_of(cfg)
    cand_p = _os.path.join(wd, "glossary_candidates.json")
    removed = 0
    if _os.path.exists(cand_p):
        with _LOCK:
            data = _j.load(open(cand_p, encoding="utf-8"))
            before = len(data.get("ai_候选", []))
            data["ai_候选"] = [c for c in data.get("ai_候选", []) if (c.get("term") or "") != term]
            removed = before - len(data["ai_候选"])
            if removed:
                _j.dump(data, open(cand_p, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=2)
    return {"ok": True, "term": term, "added": bool(stat.get("names_added")),
            "names_total": stat.get("names_total"), "removed": removed}


@app.post("/api/work/{work}/candidates/reset-skipped")
def api_candidates_reset_skipped(work: str):
    """重置本轮跳过（前端 sessionStorage 清空，服务端无状态）。"""
    return {"ok": True}


@app.post("/api/work/{work}/candidates/promote-work-glossary")
def api_candidates_promote_work_glossary(work: str):
    """批量写入：把 glossary_candidates.json 中 确认:true 的候选写进作品术语表
    (不覆盖 manual 同 jp 条目)，角色候选同时进知识库 人物 块。

    2026-09-06 grilling 定稿②: 分类三类——type 角色/人物/人名→cat 角色；
    专有名词/术语/拟声梗→cat 专名；jp 以 re: 开头→cat 正则。
    返回 {ok, written(条数), skipped(与manual同jp跳过), detail}。
    """
    import json as _j, os as _os
    try:
        cfg = cfg_of(work)
    except HTTPException:
        return {"ok": False, "error": "未知作品"}
    _, _, wd = paths_of(cfg)
    cand_p = _os.path.join(wd, "glossary_candidates.json")
    if not _os.path.exists(cand_p):
        return {"ok": False, "error": "无候选文件"}
    with _LOCK:
        data = _j.load(open(cand_p, encoding="utf-8"))
        confirmed = [c for c in data.get("ai_候选", []) if c.get("确认")]
        if not confirmed:
            return {"ok": True, "written": 0, "skipped": 0, "detail": [], "msg": "无已确认候选"}

        def _cat_of(c):
            jp = (c.get("term") or "").strip()
            typ = c.get("type") or ""
            if jp.startswith("re:"):
                return "正则"
            if any(k in typ for k in ("角色", "人物", "人名")):
                return "角色"
            return "专名"

        # 读现有作品表, 拿 manual 同 jp(不覆盖人工确认) 与 auto 集合
        gl_path = os.path.join(wd, f"glossary_{work}.json")
        gl, gl_names = [], []
        if _os.path.exists(gl_path):
            try:
                _ex = _j.load(open(gl_path, encoding="utf-8"))
                gl = _ex.get("glossary", [])
                # 2026-09-19: known_names 必须原样带回去——旧代码传 [] 会把整部作品的
                # 残留校验白名单清空(与「保存按钮整表覆盖」同类的静默数据丢失)
                gl_names = list(_ex.get("known_names", []) or [])
            except Exception:
                gl, gl_names = [], []
        manual_jp = set()
        for p in gl:
            if isinstance(p, dict):
                if p.get("来源") == "manual" and p.get("jp"):
                    manual_jp.add(p["jp"])
            elif isinstance(p, list) and len(p) == 2:
                manual_jp.add(p[0])

        written, skipped = [], []
        new_pairs = []
        for c in confirmed:
            jp = (c.get("term") or "").strip()
            tr = (c.get("translation") or "").strip()
            if not jp or not tr:
                skipped.append({"term": jp, "why": "缺译名"})
                continue
            if jp in manual_jp:
                skipped.append({"term": jp, "why": "作品表 manual 已有"})
                continue
            cat = _cat_of(c)
            new_pairs.append({"jp": jp, "zh": tr, "cat": cat, "来源": "manual"})
            written.append({"term": jp, "zh": tr, "cat": cat})

        # 角色候选 → 知识库人物(与 CLI _promote_confirmed 同结构: people[t]=tr 或 tr(作品))
        try:
            sys.path.insert(0, os.path.join(_ROOT, "scripts", "tools"))
            import work_memory as _WM
            mem = _WM.load_knowledge(work)
            people = mem.setdefault("人物", {})
            terms = mem.setdefault("术语", {})
            for c in confirmed:
                jp = (c.get("term") or "").strip()
                tr = (c.get("translation") or "").strip() or "(待定译名)"
                typ = c.get("type") or ""
                _w = (c.get("作品") or "").strip()
                if not jp:
                    continue
                if _cat_of(c) == "角色":
                    _alias_of = (c.get("别名_of") or "").strip()
                    if _alias_of:
                        continue  # 别名归并走全局角色表(CLI 专用), 此处跳过不落人物
                    _val = f"{tr}({_w})" if _w and _w not in tr else tr
                    if jp not in people:
                        people[jp] = _val
                elif jp not in terms and not jp.startswith("re:"):
                    terms[jp] = tr
            if people or terms:
                _WM.save_knowledge(work, mem)
        except Exception as _e:
            print(f"[promote-work-glossary] 知识库写入失败(不阻塞): {_e}")

        # 合并进作品表并保存(manual 前 auto 后; 新条目 manual)
        if new_pairs:
            merged = gl + new_pairs
            from aa_web_app.logic import save_work_glossary
            ok, err = save_work_glossary(work, merged, gl_names, path=gl_path)
            if not ok:
                return {"ok": False, "error": f"作品表写入失败: {err}"}
        # 从候选移除已写入项(防重复 promote)
        data["ai_候选"] = [c for c in data.get("ai_候选", []) if not c.get("确认")]
        _j.dump(data, open(cand_p, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=2)
    return {"ok": True, "written": len(written), "skipped": skipped,
            "detail": written, "msg": f"已写入作品表 {len(written)} 条"}


# ---------- 反哺全局（2026-09-20 方案 doc/方案归档/术语表反哺与候选池差集修复方案_2026-09-20.md） ----------

class RepatriateBody(BaseModel):
    selected: list = []      # 勾选的日文词（repatriate-list 的 jp）
    backup: bool = True      # 落盘前备份 roles.json/glossary.json 到 .glossary_backup/<ts>/


@app.get("/api/work/{work}/repatriate-list")
def api_repatriate_list(work: str, embed: int = 1):
    """可反哺全局的条目 = 本作品表 manual − 全局(glossary.json+roles.json) 差集。

    零 token、纯本地。embed=1 时按**本作品语料**算短词嵌入率（大作品略慢）。
    返回 {ok, work, items[], summary}；items 按「可反哺 → 跨作品次数降序 → 长度」排。
    """
    try:
        cfg_of(work)
    except HTTPException:
        return {"ok": False, "error": "未知作品"}
    import glossary_repatriate as _GR
    res = _GR.scan(work, with_embed=bool(int(embed)))
    return {"ok": True, **res}


@app.post("/api/work/{work}/repatriate-apply")
def api_repatriate_apply(work: str, body: RepatriateBody):
    """把勾选项写入全局：角色 → roles.json；专名 → glossary.json `特殊-专名`；两侧 known_names。

    服务端按勾选 jp **重新 scan 取项**（不信任前端送来的 zh/cat，防陈旧或被改坏）；
    短词专名自动包 `re:` 词边界由 core 负责（re: 对不注入 prompt，只做本地替换）。
    写前自动备份，写入幂等（已在全局的项跳过）。
    """
    try:
        cfg_of(work)
    except HTTPException:
        return {"ok": False, "error": "未知作品"}
    want = {str(x).strip() for x in (body.selected or []) if str(x).strip()}
    if not want:
        return {"ok": False, "error": "未勾选任何条目"}
    import glossary_repatriate as _GR
    res = _GR.scan(work, with_embed=False)
    items = [it for it in res["items"] if it["jp"] in want and not it["跳过"]]
    dropped = [{"jp": it["jp"], "why": it["跳过"]}
               for it in res["items"] if it["jp"] in want and it["跳过"]]
    if not items:
        return {"ok": False, "error": "勾选项无可写入内容（已跳过或缺译名）",
                "skipped_selection": dropped}
    with _LOCK:
        out = _GR.apply(items, work, backup=bool(body.backup))
    return {"ok": True, "skipped_selection": dropped, **out}


# ---------- 生成术语候选（2026-09-06 grilling ②, 后台 subprocess + 状态轮询） ----------

_GEN_CANDS = {}  # work -> {state, ts, pid, out_tail}


@app.post("/api/work/{work}/gen-candidates")
def api_gen_candidates(work: str):
    """后台跑 gen_glossary_ai --work <id> --full（全章遍历 AI 提取候选）。

    幂等: 同作品已有运行中的生成任务则拒绝(409)。
    """
    import subprocess
    try:
        cfg = cfg_of(work)
    except HTTPException:
        return {"ok": False, "error": "未知作品"}
    st = _GEN_CANDS.get(work)
    if st and st.get("state") in ("running", "starting"):
        return {"ok": False, "error": "该作品已有生成任务运行中"}
    aa_dir = cfg["aa_dir"]
    if not os.path.isdir(aa_dir):
        return {"ok": False, "error": f"aa 目录不存在: {aa_dir}"}
    # 2026-09-07: 两段式 --full 偶发被外部回收(~2min 无traceback强杀)——改用守护脚本
    #   run_full_pool_until_done.py 自动续跑(每轮共享 pool_progress 幂等续), 直到 candidates 生成
    logf = os.path.join(cfg["work_dir"], "gen_candidates.log")
    try:
        env = child_env()      # 子任务环境（冻结 exe 下带上 AA_BASE/AA_ENV）
        env.setdefault("AA_R18", "1" if cfg.get("r18") else "")
        proc = subprocess.Popen(
            py_prefix() + [os.path.join(_ROOT, "scripts", "common", "run_full_pool_until_done.py"),
             "--work", work, "--max-rounds", "30"],
            cwd=_ROOT, env=env,
            stdout=open(logf, "w", encoding="utf-8"), stderr=subprocess.STDOUT)
    except Exception as e:
        return {"ok": False, "error": f"启动失败: {e}"}
    _GEN_CANDS[work] = {"state": "running", "ts": time.time(), "pid": proc.pid}
    return {"ok": True, "msg": "已启动全章候选提取（后台运行，可轮询状态）"}


@app.get("/api/work/{work}/gen-candidates/status")
def api_gen_candidates_status(work: str):
    """生成候选任务状态: {state: running|done|error, progress: {total,cur,count}|None,
    tail: 日志尾部}。progress 解析自 gen_glossary_ai stdout(--full 逐批 flush)。"""
    st = _GEN_CANDS.get(work)
    if not st:
        return {"ok": True, "state": "idle"}
    cfg = registry.WORKS.get(work)
    tail = ""
    progress = None
    if cfg:
        logf = os.path.join(cfg["work_dir"], "gen_candidates.log")
        try:
            with open(logf, "r", encoding="utf-8", errors="replace") as f:
                logtxt = f.read()
            tail = logtxt[-2000:]
            # 解析进度: 兼容两段式新格式([pool] 批X/Y: 输入N → 专名M (累计 K))
            # 与旧整批格式(共 N 批 / 批Y: +N 条(累计 M))
            import re as _re
            m_total = _re.search(r"共 (\d+) 批", logtxt)
            pool_total = _re.search(r"批(\d+)/(\d+): 输入\d+ → 专名\d+", logtxt)
            if pool_total:
                total = int(pool_total.group(2))
                # 当前批: 取最后出现的 批X/Y (累计 K 后可能有 ", 已存progress" 后缀)
                all_pb = _re.findall(r"批(\d+)/(\d+): 输入\d+ → 专名(\d+) \(累计 (\d+)", logtxt)
                if all_pb:
                    cur = int(all_pb[-1][0])
                    total = int(all_pb[-1][1])
                    count = int(all_pb[-1][3])
                else:
                    cur, count = 0, 0
                progress = {"total": total, "cur": cur, "count": count}
            elif m_total:
                batches = _re.findall(r"批(\d+): \+(\d+) 条\(累计 (\d+)\)", logtxt)
                if m_total:
                    total = int(m_total.group(1))
                    cur = int(batches[-1][0]) if batches else 0
                    count = int(batches[-1][2]) if batches else 0
                    progress = {"total": total, "cur": cur, "count": count}
        except Exception:
            tail = ""
    if st["state"] == "running":
        # 进程结束检测(pid 存活检查)
        import ctypes
        _PH = ctypes.windll.kernel32.OpenProcess
        _hr = ctypes.windll.kernel32.OpenProcess(0x1000, False, st.get("pid") or 0)
        alive = bool(_hr)
        if _hr:
            ctypes.windll.kernel32.CloseHandle(_hr)
        if not alive:
            st["state"] = "done"
            st["ts"] = time.time()
    return {"ok": True, "state": st["state"], "ts": st.get("ts"),
            "progress": progress, "tail": tail[-1200:]}


@app.get("/api/work-meta/{work}")
def api_work_meta_get(work: str):
    """单作品元数据 + 素材出现话数（详情弹窗）。2026-08-26: materials 返回对象数组(含pct/chapters)。"""
    cfg = cfg_of(work)
    meta = logic.load_work_meta(_WORK_META_FILE)
    m = meta.get(work, {})
    mat_list = list(m.get("materials", []) or [])
    if m.get("ignore_materials"):
        mat_list = []   # 2026-08-26: jikan_yaluo 等特殊作品忽略素材统计
    # 一次 material_frequency_cached 得 全部素材 count+chapters(复用首页, 避免逐素材循环卡顿)
    mf = logic.material_frequency_cached(cfg.get("work_dir", ""), cfg.get("out_dir", ""), mat_list)
    tot = sum(v["count"] for v in mf.values() if isinstance(v, dict))
    mats = []
    for name in mat_list:
        v = mf.get(name)
        if not isinstance(v, dict):
            continue
        c, ch = v.get("count", 0), v.get("chapters", 0)
        dn, wname, note = logic.material_display_parts(name)
        if dn is None:
            continue
        pct = round(c * 100 / tot, 1) if tot else 0.0
        if pct < 5:
            continue   # 2026-08-26: 排除 5% 以下
        mats.append({"name": dn, "works": wname or "", "note": note or "",
                     "count": c, "chapters": ch,
                     "pct": pct})
    # 出现话数(逐素材懒算, 详情弹窗已改为异步加载)
    chapters = {}
    for name in mat_list:
        chapters[name] = logic.material_chapters_cached(cfg.get("work_dir", ""), cfg.get("out_dir", ""), name)
    # 作者：手动值优先 → 覆盖表 → 帖头◆提取（与列表页同口径，2026-09-13 修详情为空/保存丢作者）
    _a_manual = logic.normalize_author(m.get("author"))
    _a_auto = (_AUTHOR_OVERRIDE.get(work)
               or logic.extract_author_cached(cfg.get("work_dir", ""), cfg.get("aa_dir", "")) or "")
    return {"work": work,
            "author": _a_manual,
            "author_auto": "" if _a_manual else _a_auto,
            "author_is_auto": bool(not _a_manual and _a_auto),
            "cn_name": m.get("cn_name", ""),
            "status": m.get("status", ""),
            "intro": m.get("intro", ""),
            "jp_title": m.get("jp_title", "") or cfg.get("title", work),
            "site": cfg.get("site_name") or cfg.get("site"),
            "r18": bool(cfg.get("r18")),
            "r18_level": m.get("r18_level", ""),
            "r18_summary": r18_summary_cached(cfg.get("work_dir", ""), cfg.get("out_dir", "")),
            "tags": list(m.get("tags", []) or []),
            "materials": mats,
            "material_chapters": chapters,
            "rating": m.get("rating"),
            "pipeline": m.get("pipeline", ""),
            "review": m.get("review", "")}


@app.put("/api/work-meta/{work}")
def api_work_meta_put(work: str, body: WorkMeta):
    cfg_of(work)
    meta = logic.load_work_meta(_WORK_META_FILE)
    existing = meta.get(work, {})
    # 只更新允许编辑的字段；jp_title/site 等不动
    # 2026-09-13: author 走 normalize_author——'◆'(空输入被加前缀) 视为清空，
    # 否则会写成真值 '◆' 遮蔽列表页的帖头◆自动提取（用户报「保存导致作者丢失」）
    existing["author"] = logic.normalize_author(body.author)
    existing["cn_name"] = (body.cn_name or "").strip()
    existing["status"] = (body.status or "").strip()
    existing["intro"] = (body.intro or "").strip()
    existing["tags"] = [str(t).strip() for t in body.tags if str(t).strip()]
    # 2026-08-26: materials 存「角色名（作品名）」——同名跨作品唯一, 保留作品后缀
    # (只 trim 不去括号; 兼容旧纯名数据原样保留)
    existing["materials"] = [str(x).strip() for x in body.materials if str(x).strip()]
    # 2026-08-26: r18_level 人工可改(high/low/medium/空=no), 非法值丢弃保留原值
    if body.r18_level in ("high", "low", "medium", ""):
        existing["r18_level"] = body.r18_level
    # 2026-09-03: rating 1-5 或 None(未评分), 非法丢弃保留原值; review 自由文本
    if body.rating is None or (isinstance(body.rating, int) and 1 <= body.rating <= 5):
        existing["rating"] = body.rating
    existing["review"] = (body.review or "").strip()
    # 2026-09-22 方案 D11: pipeline 管线归属(generic/pokemon), 非法值丢弃保留原值;
    #   改动会切换翻译链(宝可梦专属链 = 固定内容表预填 + 本地 wiki 术语绑定)
    if body.pipeline in ("generic", "pokemon"):
        existing["pipeline"] = body.pipeline
    meta[work] = existing
    with _LOCK:
        ok = logic.save_work_meta(_WORK_META_FILE, meta)
    if not ok:
        raise HTTPException(500, "保存 work_meta.json 失败")
    return {"ok": True, "work": work}


@app.get("/api/materials/resolve")
def api_materials_resolve(name: str):
    """2026-08-26: 素材名 → 候选作品(出处)列表, 供手动添加时识别/选择。

    同名角色多作品时前端弹选择; 查不到返回空数组(可手动输入作品名)。
    """
    return {"ok": True, "candidates": logic.resolve_material(name)}


@app.post("/api/work-meta/{work}/mat-scan")
def api_work_meta_mat_scan(work: str):
    """2026-08-26: 扫描汉化文本自动统计素材并写回 work_meta materials。

    用全局术语表角色表匹配(中文名+日文原名), 排除 texture/yaruo/昵称梗,
    取出现≥3次且占比≥5% 的前8个, 写回 materials。返回统计结果。
    """
    import glob as _glob
    from collections import Counter
    cfg = cfg_of(work)
    meta = logic.load_work_meta(_WORK_META_FILE)
    m = meta.setdefault(work, {})
    if m.get("ignore_materials"):
        return {"ok": True, "skipped": True, "materials": []}
    out = cfg.get("out_dir")
    if not out or not os.path.isdir(out):
        raise HTTPException(400, "无汉化目录")
    files = _glob.glob(os.path.join(out, "*_汉化.txt"))
    if not files:
        return {"ok": True, "skipped": True, "materials": [], "msg": "无汉化文件"}
    # 2026-08-28: 知识库人物优先(作品专属素材, 精准); 空时回退全局角色表
    kn_mat = _mat_scan_from_knowledge(work, cfg, files)
    if kn_mat is not None:
        mats = [k for k, _ in kn_mat]
        m["materials"] = mats
        with _LOCK:
            _ok = logic.save_work_meta(_WORK_META_FILE, meta)
        return {"ok": True, "materials": mats, "counts": dict(kn_mat),
                "source": "knowledge", "msg": "作品知识库人物素材"}
    # 角色表(排除 texture/yaruo/昵称梗); 2026-08-26: 带作品名, mat key 用「名（作品名）」
    # 2026-09-01 三级重构: 角色块迁出 glossary.json → roles.json
    _gl = json.load(open(os.path.join(_DATA, "glossary.json"), encoding="utf-8"))
    _ws = {w["id"]: w["名称"] for w in _gl.get("作品", [])}
    _rl = json.load(open(os.path.join(_DATA, "roles.json"), encoding="utf-8")).get("角色", [])
    roles = []
    for c in _rl:
        if c.get("分类") == "texture":
            continue
        ws = c.get("作品") or []
        note = c.get("备注", "") or ""
        if "texture" in ws or "yaruo" in ws or "昵称" in note or "梗" in note:
            continue
        cn, jp = c.get("中文"), c.get("日文")
        if not cn and not jp:
            continue
        # 每个 (中文/日文名) → 作品名 (取第一个非 yaruo 作品)
        wname = next((_ws.get(w) for w in ws if w != "yaruo"), None)
        roles.append((cn, jp, wname))
    cnt = Counter()
    for f in files:
        t = open(f, encoding="utf-8", errors="replace").read()
        for cn, jp, wname in roles:
            # 2026-08-27: 匹配词集合(含短名/繁简变体) + 排除单字符(L 行号碰撞)
            words = [w for w in {cn, jp, cn, jp} if w and len(w) >= 2]
            n = sum(t.count(w) for w in words)
            if n:
                key = f"{cn or jp}（{wname}）" if wname else (cn or jp)
                cnt[key] += n
    tot = sum(cnt.values())
    mats = []
    for key, c in cnt.most_common():
        if c < 3:
            break
        pct = c * 100 / tot if tot else 0
        if pct < 5:
            break
        mats.append(key)
        if len(mats) >= 8:
            break
    m["materials"] = mats
    with _LOCK:
        ok = logic.save_work_meta(_WORK_META_FILE, meta)
    if not ok:
        raise HTTPException(500, "保存失败")
    return {"ok": True, "materials": mats, "counts": dict(cnt.most_common(10))}


def _mat_scan_from_knowledge(work, cfg, files):
    """从作品知识库 人物 生成素材(2026-08-28)。返回 [(mat_key, count), ...] 或 None(无人物)。

    - 读 work/<work>_knowledge.json 的 人物: key=日文短名, value=中文译名(作品)。
    - 匹配词展开: key(日文短名) + 中文译名(去括号) + 同名在全局角色表的别名。
    - 统计汉化文本出现次数, ≥3次 且 占比≥5% 取前8, mat key 用「中文（作品名）」或中文。
    返回 None = 知识库无人物(调用方回退全局角色表)。
    """
    from collections import Counter
    import shlex
    # 读知识库人物
    kn_p = os.path.join(cfg.get("work_dir", ""), work + "_knowledge.json")
    people = {}
    if os.path.exists(kn_p):
        try:
            _kn = json.load(open(kn_p, encoding="utf-8"))
            people = _kn.get("人物", {}) or {}
        except Exception:
            people = {}
    if not people:
        return None  # 无人物 → 回退全局角色表
    # 全局角色表(取同名角色的别名); 2026-09-01: 角色块迁出 glossary.json → roles.json
    _gl = json.load(open(os.path.join(_DATA, "roles.json"), encoding="utf-8"))
    _aliases = {}
    for c in _gl.get("角色", []):
        _aliases.setdefault(c.get("日文") or c.get("中文"), set()).update(c.get("别名") or [])
    # 每个知识库人物展开匹配词
    roles = []  # [(mat_key, [match_words])]
    for jp, val in people.items():
        # value 形如 "东北切蒲英(VOICEROID)" 或 "雷(舰队collection)" 或纯 "雷"
        val = str(val).strip()
        cn = val.split("(")[0].strip()
        wname = ""
        if "(" in val and val.endswith(")"):
            wname = val[val.index("(")+1:-1].strip()
        words = {jp, cn}
        # 同名全局别名并入(如 青蛙子/ケロちゃん)
        for _a in (_aliases.get(jp) or set()):
            if len(_a) >= 2:
                words.add(_a)
        words = {w for w in words if len(w) >= 2}  # 排除单字符(L行号碰撞)
        mat_key = f"{cn}（{wname}）" if wname else cn
        roles.append((mat_key, list(words)))
    # 统计
    tot_chars = 0
    cnt = Counter()
    for f in files:
        t = open(f, encoding="utf-8", errors="replace").read()
        tot_chars += len(t)
        for key, wl in roles:
            n = sum(t.count(w) for w in wl)
            if n:
                cnt[key] += n
    # 阈值: ≥3次 且 占比≥5% 取前8
    tot = sum(cnt.values())
    mats = []
    for key, c in cnt.most_common():
        if c < 3:
            break
        pct = c * 100 / tot if tot else 0
        if pct < 5:
            continue
        mats.append((key, c))
        if len(mats) >= 8:
            break
    return mats or ([], {})


# ---------- API ----------

class LineSave(BaseModel):
    text: str
    type: str = ""               # 编辑类型；**空串 = 自动判定**（2026-10-01），显式值 = 人工优先
    line_type: str = ""          # 人工修正的行类型（text/mixed/aa/header/blank），仅记录
    sfx: list = []               # 新增 sfx 标记 [[字符偏移, 文本], ...]（划词替换/「sfx 替换」按钮）
    sfx_clear: list = []         # 清与该区间重叠的 sfx 标记 [[偏移, 长度], ...]（普通替换时清掉旧标记）
    sfx_clear_all: bool = False  # 清该行全部 sfx 标记


def _log_edits_bulk(cfg, fname, rows, etype, line_type=""):
    """批量追加修改记录到单文件 work_dir/edits.jsonl（时间/章节/行/类型/行类型/改前/改后）。

    rows = [(idx0, before, after), ...]（idx0 为 0-based 行号）。
    2026-10-01: 翻译替换/术语替换一次改数千行 → 单次打开顺序写（逐条 _log_edit 会
    反复开关文件）。这条日志是这些改动**唯一**的前后数据留档（不再整文件备份）。
    """
    if not rows:
        return 0
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    log_path = os.path.join(cfg["work_dir"], "edits.jsonl")
    with open(log_path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"ts": ts, "work": cfg.get("title"), "file": fname,
                                "line": r[0] + 1, "type": etype,
                                "line_type": line_type,
                                "before": r[1], "after": r[2]},
                               ensure_ascii=False) + "\n")
    return len(rows)


def _log_edit(cfg, fname, idx, etype, before, after, line_type=""):
    """修改记录（单行）：追加到 work_dir/edits.jsonl（时间/章节/行/类型/行类型/改前/改后）。"""
    _log_edits_bulk(cfg, fname, [(idx, before, after)], etype, line_type)


def _line_is_art(cfg, fname, idx, src_lines):
    """该行是否 art（纯图形）：override > work/line_meta_v2 的 kind > line_type_of(src)。

    复用翻译替换那套 `_art_sources` + `_art_checker`（与阅读器行号列同一分类链，
    不另造一套）；src_lines 由调用方读一次传入。
    """
    base = logic.txt_key(logic.src_name_of(fname))
    meta, overrides = _art_sources(cfg, base)
    return _art_checker(meta, overrides, lambda: src_lines)(idx)


def _resolve_edit_type(cfg, fname, idx, src_lines, old, new_text, body_type):
    """行编辑类型：人工显式选择优先；空串 → 自动分类（2026-10-01）。

    返回 (type, auto)：auto=True 表示类型由代码判定（前端 radio 停在「自动」）。
    art 行（纯图形）不判 → 沿用旧默认「错位」；未知的非空值按旧行为落「错位」。
    比较用**提交值** new_text 与盘上旧值 old（pad 补位造成的差异不参与判定）。
    """
    t = (body_type or "").strip()
    if t:
        return (t if t in logic.MANUAL_EDIT_TYPES else logic.AUTO_EDIT_DEFAULT), False
    src_line = src_lines[idx] if 0 <= idx < len(src_lines) else ""
    kind = "aa" if _line_is_art(cfg, fname, idx, src_lines) else None
    return (logic.classify_edit(src_line, old, new_text, kind=kind)
            or logic.AUTO_EDIT_DEFAULT), True


@app.get("/api/work/{work}/chapter/{fname}/line/{idx}")
def api_line_get(work: str, fname: str, idx: int):
    """单行数据（弹窗编辑用）：src 原文 + tr 当前译文。

    未译章（只传原文名）无 tr → tr 返回 ''，编辑由前端拦截（hasTr=False）。"""
    cfg = cfg_of(work)
    check_chapter(cfg, fname)
    src_name = logic.src_name_of(fname) if (fname.endswith("_汉化.txt") or fname.endswith("_汉化.txt.gz")) else fname
    src = logic.read_lines(logic.txt_path(cfg["aa_dir"], src_name))
    if not 0 <= idx < len(src):
        raise HTTPException(404, f"行号越界: {idx + 1}")
    tr_path = logic.txt_path(cfg["out_dir"], fname) if (fname.endswith("_汉化.txt") or fname.endswith("_汉化.txt.gz")) else None
    tr_lines = logic.read_lines(tr_path) if tr_path and os.path.exists(tr_path) else []
    src_l = src[idx] if idx < len(src) else ""
    marks = logic.load_sfx_marks(cfg["work_dir"], logic.txt_key(src_name))
    return {"idx": idx, "src": src_l, "tr": tr_lines[idx] if idx < len(tr_lines) else "",
            "line_type": logic.line_type_of(src_l),
            "sfx": marks.get(str(idx + 1), [])}


@app.get("/api/work/{work}/edits")
def api_edits(work: str, limit: int = 100):
    """最近修改记录（edits.jsonl 尾部 limit 条）。"""
    cfg = cfg_of(work)
    log_path = os.path.join(cfg["work_dir"], "edits.jsonl")
    if not os.path.exists(log_path):
        return {"edits": []}
    with open(log_path, encoding="utf-8") as f:
        lines = [json.loads(l) for l in f if l.strip()]
    return {"edits": lines[-limit:]}


@app.get("/api/work/{work}/chapter/{fname}")
def api_chapter(work: str, fname: str):
    cfg = cfg_of(work)
    check_chapter(cfg, fname)
    aa_dir, out_dir = cfg["aa_dir"], cfg["out_dir"]
    src_name = logic.src_name_of(fname) if (fname.endswith("_汉化.txt") or fname.endswith("_汉化.txt.gz")) else fname
    tr_path = logic.txt_path(out_dir, fname) if (fname.endswith("_汉化.txt") or fname.endswith("_汉化.txt.gz")) else None
    tr = logic.read_lines(tr_path) if tr_path and os.path.exists(tr_path) else []
    return {"file": fname,
            "tr": tr,
            "src": logic.read_lines(logic.txt_path(aa_dir, src_name))}


@app.get("/api/work/{work}/chapter/{fname}/line/{idx}/html")
def api_line_html(work: str, fname: str, idx: int):
    cfg = cfg_of(work)
    check_chapter(cfg, fname)
    aa_dir, out_dir = cfg["aa_dir"], cfg["out_dir"]
    tr_path = logic.txt_path(out_dir, fname)
    tr_lines = logic.read_lines(tr_path)
    if not 0 <= idx < len(tr_lines):
        raise HTTPException(404, f"行号越界: {idx + 1}")
    base = logic.txt_key(logic.src_name_of(fname))    # 汉化名 → 原文基名（colormap/sfx_marks 键一致）
    # 原文行 + sfx 标记：与阅读页渲染同口径（2026-09-15 起补传 src_line——
    #   旧实现丢参，保存后重渲染的行会掉 sfx 特效；base 也按原文名归一，colormap 不再失配）
    src_lines = logic.read_lines(logic.txt_path(aa_dir, logic.src_name_of(fname)))
    src_l = src_lines[idx] if idx < len(src_lines) else ""
    marks = {str(idx + 1): logic.load_sfx_marks(cfg["work_dir"], base).get(str(idx + 1), [])}
    cm_path = os.path.join(aa_dir, base + "_colormap.json")
    cm = json.load(open(cm_path, encoding="utf-8")) if os.path.exists(cm_path) else {}
    site = importlib.import_module("sites." + cfg["site"])
    html = gen_aa_html_render(idx, tr_lines[idx], cm, site, src_l, marks)
    return {"idx": idx, "html": html or " "}


def chapter_entry(cfg, fname):
    """按章节名（原文名或汉化名）取 merged_chapters 条目；找不到 → 404（供区间 API 用）。"""
    order_path = os.path.join(cfg["work_dir"], "chapter_order.json")
    mch = logic.merged_chapters(cfg["aa_dir"], cfg["out_dir"], cfg.get("name_style"), order_path)
    for ch in mch:
        if fname in (ch["tr_file"], ch["src_file"]):
            return ch
    raise HTTPException(404, f"章节不存在: {fname}")


@app.get("/api/work/{work}/chapter/{fname}/range")
def api_chapter_range(work: str, fname: str, from_k: int = 0, to_k: int = -1, mode: str = "tr"):
    """阅读器窗口化：取区间行数据（k = 可渲染行序号，0-based；行 HTML 按需渲染）。

    单次最多 reader_win.BLOCK_MAX 行（防一次拉整章）；to_k < 0 → 从 from_k 起取一个块。
    mode: tr（无汉化退原文）/ src / refine（无精译退 tr/src），响应里给实际生效模式。
    """
    cfg = cfg_of(work)
    check_chapter(cfg, fname)
    if mode not in reader_win.MODES:
        raise HTTPException(422, f"未知模式: {mode}（可选 {'/'.join(reader_win.MODES)}）")
    ctx = reader_win.load_ctx(cfg, work, chapter_entry(cfg, fname))
    if to_k < 0:
        to_k = from_k + reader_win.BLOCK_MAX - 1
    m, rows = reader_win.rows(ctx, mode, from_k, to_k)
    return {"mode": m, "from": rows[0]["k"] if rows else 0,
            "to": rows[-1]["k"] if rows else -1, "n": ctx["n"],
            "hasTr": ctx["has_tr"], "hasRefine": ctx["has_refine"], "lines": rows}


@app.put("/api/work/{work}/chapter/{fname}/line/{idx}")
def api_save_line(work: str, fname: str, idx: int, body: LineSave):
    cfg = cfg_of(work)
    check_chapter(cfg, fname)
    if not (fname.endswith("_汉化.txt") or fname.endswith("_汉化.txt.gz")):
        raise HTTPException(422, "未汉化章节不可编辑（仅原文可读）")
    aa_dir, out_dir, work_dir = cfg["aa_dir"], cfg["out_dir"], cfg["work_dir"]
    tr_path = logic.txt_path(out_dir, fname)
    aa_path = logic.txt_path(aa_dir, logic.src_name_of(fname))
    cur_lines = logic.read_lines(tr_path)
    old = cur_lines[idx] if idx < len(cur_lines) else ""
    with _LOCK:
        r = logic.save_line(aa_path, tr_path, idx, body.text,
                            work_names=logic.load_work_glossary(work)["known_names"])
    invalidate_stats(work)
    logic.invalidate_mch_cache()   # 编辑可能改首行标题 → 章节列表失效（目录mtime对内容修改不可靠）
    if r["result"] == "error":
        raise HTTPException(422, r["message"])
    if r["result"] == "ok":
        # 2026-10-01: 类型自动分类（人工显式选择优先；radio 停在「自动」→ 空串 → 代码判定）
        src_lines = logic.read_lines(aa_path) if os.path.exists(aa_path) else []
        etype, auto = _resolve_edit_type(cfg, fname, idx, src_lines, old, body.text,
                                        body.type)
        _log_edit(cfg, fname, idx, etype, old, r["text"], body.line_type or "")
        r["type"], r["type_auto"] = etype, auto
    # sfx 标记（划词翻译替换）——与文本保存解耦：内容未变（unchanged）也要能加/清标记
    sbase, line_no = logic.txt_key(logic.src_name_of(fname)), idx + 1
    added = cleared = 0
    if body.sfx_clear_all:
        _ok, _err, cleared = logic.clear_sfx_marks(work_dir, sbase, line_no, None)
    for rng in (body.sfx_clear or []):
        try:
            a, ln = int(rng[0]), int(rng[1])
        except (TypeError, ValueError, IndexError):
            continue
        _ok, _err, n = logic.clear_sfx_marks(work_dir, sbase, line_no, [a, ln])
        cleared += n
    for pair in (body.sfx or []):
        try:
            off, txt = pair[0], pair[1]
        except (TypeError, ValueError, IndexError):
            continue
        _ok, _err, act = logic.add_sfx_mark(work_dir, sbase, line_no, off, txt)
        added += 1 if act == "add" else 0
    r["sfx_changed"] = bool(added or cleared)
    r["sfx_added"], r["sfx_cleared"] = added, cleared
    r["sfx_marks"] = logic.load_sfx_marks(work_dir, sbase).get(str(line_no), [])
    return r


# ---------- 人工行分类 override（只喂图片加大，翻译 flag 不动） ----------

class OverrideSave(BaseModel):
    kind: str


def _override_base(cfg, chapter):
    """路由参数 chapter（base 或完整文件名）→ base 章节名，并校验章节存在。"""
    fname = chapter if (chapter.endswith("_汉化.txt") or chapter.endswith("_汉化.txt.gz")) else chapter + "_汉化.txt"
    check_chapter(cfg, fname)
    return logic.txt_key(fname)


@app.get("/api/override/{work}/{chapter}")
def api_override_get(work: str, chapter: str):
    """读整章人工行分类 override：{1-based行号: kind}（稀疏：只存与自动不一致的行）。"""
    cfg = cfg_of(work)
    return logic.load_overrides(cfg["work_dir"], _override_base(cfg, chapter))


@app.put("/api/override/{work}/{chapter}/{line}")
def api_override_put(work: str, chapter: str, line: int, body: OverrideSave):
    """写单行 override；kind ∈ text/art/mixed/header/blank/auto（auto=删除=还原为自动）。"""
    cfg = cfg_of(work)
    base = _override_base(cfg, chapter)
    if line < 1:
        raise HTTPException(422, f"行号必须 ≥1: {line}")
    if body.kind == "auto":
        with _LOCK:
            ok, err = logic.delete_override(cfg["work_dir"], base, line)
    elif body.kind in logic.OVERRIDE_KINDS:
        with _LOCK:
            ok, err = logic.save_override(cfg["work_dir"], base, line, body.kind)
    else:
        raise HTTPException(422, f"非法 kind: {body.kind}（可选 {'/'.join(logic.OVERRIDE_KINDS)}/auto）")
    if not ok:
        raise HTTPException(500, err)
    return {"ok": True, "line": line, "kind": body.kind}


# ---------- 台词高亮 gold 标注（2026-08-29 第 2 步，精译 ground truth 回归集） ----------

class GoldSave(BaseModel):
    is_dialogue: bool


@app.get("/api/gold/{work}/{chapter}")
def api_gold_get(work: str, chapter: str):
    """读整章 golden 台词标注：{1-based行号: bool}（稀疏：只存人工纠正过的行）。"""
    cfg = cfg_of(work)
    return logic.load_gold(cfg["work_dir"], _override_base(cfg, chapter))


@app.put("/api/gold/{work}/{chapter}/{line}")
def api_gold_put(work: str, chapter: str, line: int, body: GoldSave):
    """写单行 golden 台词标注；is_dialogue 必填 bool（true=是台词/false=不是台词）。"""
    cfg = cfg_of(work)
    base = _override_base(cfg, chapter)
    if line < 1:
        raise HTTPException(422, f"行号必须 ≥1: {line}")
    with _LOCK:
        ok, err = logic.save_gold(cfg["work_dir"], base, line, body.is_dialogue)
    if not ok:
        raise HTTPException(500, err)
    return {"ok": True, "line": line, "is_dialogue": body.is_dialogue}


@app.delete("/api/gold/{work}/{chapter}/{line}")
def api_gold_delete(work: str, chapter: str, line: int):
    """删除单行 golden 标注（恢复为自动，幂等）。"""
    cfg = cfg_of(work)
    base = _override_base(cfg, chapter)
    if line < 1:
        raise HTTPException(422, f"行号必须 ≥1: {line}")
    with _LOCK:
        ok, err = logic.delete_gold(cfg["work_dir"], base, line)
    if not ok:
        raise HTTPException(500, err)
    return {"ok": True, "line": line}


@app.get("/api/work/{work}/search")
def api_search(work: str, q: str = ""):
    cfg = cfg_of(work)
    if not q.strip():
        return {"hits": []}
    hits = logic.search_work(cfg["out_dir"], cfg["aa_dir"], q.strip(),
                             name_style=cfg.get("name_style"))
    return {"hits": hits, "total": len(hits)}


@app.get("/api/work/{work}/find")
def api_find(work: str, q: str = "", scope: str = "both", limit: int = 300):
    """阅读器全库搜索（2026-10-07）：只搜 text/mixed 行，中日/单侧可选。

    与 /api/work/{work}/search（work 页术语小搜索）分开：这个走字节级扫描 + 行类型索引，
    810 章大作品也能 1~3 秒出结果（首次会建 work/line_kinds.json 索引，约 10 秒）。"""
    cfg = cfg_of(work)
    scope = scope if scope in ("both", "zh", "jp") else "both"
    r = logic.search_lines(cfg, work, q, scope=scope, limit=max(1, min(int(limit or 300), 2000)))
    return r


@app.post("/api/work/{work}/regenerate")
def api_regenerate(work: str):
    cfg = cfg_of(work)
    with _LOCK:
        code, tail, html_out = run_generator(work)
    invalidate_stats(work)
    mtime = os.path.getmtime(html_out) if html_out and os.path.exists(html_out) else None
    return {"ok": code == 0, "exit_code": code, "output": tail,
            "html_out": html_out, "html_mtime": mtime}


class MakeHtmlBody(BaseModel):
    """「仅 HTML 文件」简版的三个选项（作品页第二个生成按钮）。"""
    tr_only: bool = False       # 仅译文（页面不做译/原切换、不存原文数据）
    split: int = 0              # 每 N 章一个文件；0/负数 = 全部章放一个文件
    out_dir: str = ""           # 输出目录；留空 = 交付 HTML 目录（aaManga/交付HTML）


@app.post("/api/work/{work}/make-html")
def api_make_html(work: str, body: MakeHtmlBody = MakeHtmlBody()):
    """「仅 HTML 文件」：自包含 .html（可分页成多个），零 token。

    与 /regenerate（正式交付文件夹：编辑服务 exe + 源留档 + data/）互不影响：
    这里只写 `【机翻】<交付短名>_<起始>-<终止>.html` 到交付 HTML 目录（`--out-dir` 可改）。
    文件自带译/原切换（`tr_only` 关）、阅读进度、手机抽屉；重跑先删本作品上一次的简版文件。
    """
    cfg = cfg_of(work)
    split = int(body.split or 0)
    if split < 0:
        split = 0
    extra = ["--plain", "--split", str(split)]
    if body.tr_only:
        extra.append("--tr-only")
    out_dir = (body.out_dir or "").strip()
    if out_dir:
        extra += ["--out-dir", out_dir]
    with _LOCK:
        code, tail, _ = run_generator(work, extra=extra, plain=True)
    out_dir = out_dir or os.path.dirname(_gen_aa_html.deliver_dir(cfg))
    pre = _gen_aa_html.PLAIN_PREFIX + _gen_aa_html.plain_short_name(cfg) + "_"
    files = []
    try:
        files = sorted(f for f in os.listdir(out_dir)
                       if f.startswith(pre) and f.endswith(".html"))
    except OSError:
        pass
    note = next((l.strip() for l in (tail or "").splitlines() if l.startswith("[plain] ")), "")
    return {"ok": code == 0, "exit_code": code, "output": tail,
            "dir": out_dir, "files": files, "note": note}


@app.post("/api/regenerate-all")
def api_regenerate_all():
    results = {}
    for work in registry.WORKS:
        with _LOCK:
            code, tail, html_out = run_generator(work)
        invalidate_stats(work)
        results[work] = {"ok": code == 0, "output": tail}
    return {"results": results}


def _role_hint(jp):
    """查 roles.json 返回 jp 的疑似角色信息: (中文, [作品名...]) 或 None。

    2026-09-01: 作品术语表疑似角色展示——替换对 jp 命中角色(日文/别名)
    时标注角色中文+作品, 供人工确认(避免 阿熊→阿库娅 式错翻)。
    """
    try:
        with open(os.path.join(_DATA, "roles.json"), encoding="utf-8") as f:
            roles = json.load(f).get("角色", [])
    except Exception:
        return None
    ws = {}
    try:
        with open(logic._GLOSSARY_JSON, encoding="utf-8") as f:
            for w in json.load(f).get("作品", []):
                ws[w["id"]] = w["名称"]
    except Exception:
        pass
    for r in roles:
        # 2026-09-01: 艺术纹理/碎片 分类是 AA 绘画碎片(ツモ/ぃぃ 等), 非真角色, 不展示
        if (r.get("分类") or "") in ("艺术纹理/碎片", "texture"):
            continue
        if r.get("日文") == jp:
            wnames = [ws.get(w, w) for w in (r.get("作品") or []) if w != "yaruo"]
            return {"cn": r.get("中文") or jp, "works": wnames}
        for al in (r.get("别名") or []):
            if al == jp:
                wnames = [ws.get(w, w) for w in (r.get("作品") or []) if w != "yaruo"]
                return {"cn": r.get("中文") or jp, "works": wnames, "alias": True}
    return None


@app.get("/api/work/{work}/glossary-preview")
def api_glossary_preview(work: str, pairs: str = ""):
    cfg = cfg_of(work)
    aa_dir, out_dir = cfg["aa_dir"], cfg["out_dir"]
    # 分组: 作品专属替换对前置 + 全局替换对（作品对在前，同词以作品为准）
    # 2026-09-01: work 表 v2 对象 → 归一化二元组(修 ValueError); 附加角色提示
    global_pairs = logic._norm_pairs(logic.load_glossary_pairs())
    work_entries = logic.load_work_glossary(work)["glossary"]
    work_pairs = logic._norm_pairs(work_entries)
    work_hints = {}
    for e in work_entries:
        if isinstance(e, dict) and e.get("jp"):
            h = _role_hint(e["jp"])
            if h:
                work_hints[e["jp"]] = h
    groups = []
    if work_pairs:
        groups.append({"src": "work", "label": "作品专属术语表", "pairs": work_pairs,
                       "hints": work_hints})
    if global_pairs:
        groups.append({"src": "global", "label": "全局术语表", "pairs": global_pairs})
    all_pairs = global_pairs + work_pairs
    # pairs query: 逗号分隔 "日文|中文"，只算这些对（重新计算按钮用；空=全部）
    if pairs:
        sel = [p.split("|", 1) for p in pairs.split(",") if "|" in p]
        all_pairs = [p for p in all_pairs if p in sel or [str(p[0]), str(p[1])] in sel]
    files = []
    total = 0
    for fname in logic.chapter_order(out_dir, cfg.get("name_style")):
        changed, skipped = logic.apply_glossary_chapter(
            os.path.join(aa_dir, logic.src_name_of(fname)),
            os.path.join(out_dir, fname), all_pairs, dry_run=True)
        if changed or skipped:
            # 2026-09-01: 每行带 chapter(章节文件)+line(行号)+overwide(超宽警告), 前端可跳转阅读器预览
            files.append({"file": fname, "changed": len(changed), "skipped": skipped,
                          "lines": [[i + 1, b[:100], nb[:100], fname, i + 1, bool(ow)]
                                    for i, b, nb, *ow in changed[:20]]})
            total += len(changed)
    return {"groups": groups, "total": total, "files": files,
            "pairs": all_pairs}


class ApplyGlossaryBody(BaseModel):
    pairs: list = []   # 前端选中的 [日文, 中文] 列表


@app.post("/api/work/{work}/apply-glossary")
def api_apply_glossary(work: str, body: ApplyGlossaryBody):
    cfg = cfg_of(work)
    aa_dir, out_dir = cfg["aa_dir"], cfg["out_dir"]
    # 前端自选术语对（空 = 全部）
    # 2026-09-01: work 表 v2 对象 → 归一化(修 ValueError)
    pairs = body.pairs if body.pairs else (
        logic._norm_pairs(logic.load_glossary_pairs())
        + logic._norm_pairs(logic.load_work_glossary(work)["glossary"]))
    total = 0
    with _LOCK:
        for fname in logic.chapter_order(out_dir, cfg.get("name_style")):
            changed, _ = logic.apply_glossary_chapter(
                os.path.join(aa_dir, logic.src_name_of(fname)),
                os.path.join(out_dir, fname), pairs, dry_run=False)
            total += len(changed)
            # 逐行记改前/改后（替代原「整文件备份目录」，2026-10-01）
            _log_edits_bulk(cfg, fname, [(c[0], c[1], c[2]) for c in changed],
                            "术语替换")
    invalidate_stats(work)
    return {"ok": True, "applied": total, "pairs": len(pairs)}


# ---------- 翻译替换（阅读器手动替换对 → 本作品全部 汉化.txt，2026-09-09） ----------
# 与 apply-glossary 同机制（logic.apply_glossary_chapter：子串替换 + SRC 宽度自动
# 校正 + 先备份），区别：① 替换对来自手动输入 ② AA 艺术行保护——逐章最终行分类为
# art 的行跳过，防图形列位被改坏（分类链与 reader_page 行号列一致：
# override > line_meta_v2 > line_type_of 规则）。

_KIND2TYPE = {"art": "aa", "text": "text", "mixed": "mixed",
              "header": "header", "blank": "blank"}


def _art_sources(cfg, base):
    """art 判定所需的两份标注：line_meta_v2 与人工 override（读一次，全章共用）。"""
    meta, overrides = {}, {}
    meta_path = os.path.join(cfg["work_dir"], "line_meta_v2", base + ".json")
    if os.path.exists(meta_path):
        try:
            meta = json.load(open(meta_path, encoding="utf-8")) or {}
        except Exception:
            meta = {}
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


def _chapter_art_idxs(cfg, base, n, src_lines):
    """返回 0-based 行号集合：整章有效行分类为 art(aa) 的行。

    全量判定（逐行过分类链）——只在「单章重算」路径上用；预览/应用的常规扫描
    只判命中行（见 _chapter_replace_plan），避免整部作品逐行分类的开销。
    """
    meta, overrides = _art_sources(cfg, base)
    chk = _art_checker(meta, overrides, lambda: src_lines)
    return {i for i in range(n) if chk(i)}


class ReplacePairsBody(BaseModel):
    pairs: list = []     # [[查找, 替换为], ...]（手动输入）
    scope: str = "all"   # all = 全部已汉化章；from = 当前话及后续话（2026-09-16）
    start: str = ""      # scope="from" 的起始章（汉化文件名，前端传 CFG.fname）
    chapters: list = []  # 章节勾选过滤层（2026-10-01）：汉化文件名，空 = 不过滤（在 scope 之上取交集）


def _norm_manual_pairs(raw):
    """手动替换对归一：(查找, 替换为, sfx标记) 三元组；查找侧为空丢弃，同对去重（保序取首个）。

    2026-09-13: 前端会把「历史记录里勾选启用的对」拼在手工对后面，去重防同一对
    被应用两次（结果虽幂等，但预览计数会翻倍误导）。
    2026-09-21: 第 3 位 = 该对是否打 sfx 样式（历史里的对是二元组 → 默认 False）。
    去重键只用前两位——同一对既手工勾了 sfx 又从历史带进来时，手工那条优先（保序取首个）。
    """
    out, seen = [], set()
    for p in raw or []:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        f0, r0 = p[0], p[1]
        if not isinstance(f0, str) or not f0.strip():
            continue
        repl = r0 if isinstance(r0, str) else ""
        sfx = bool(p[2]) if len(p) > 2 else False
        if (f0, repl) in seen:
            continue
        seen.add((f0, repl))
        out.append((f0, repl, sfx))
    return out


def _sfx_pairs_of(pairs):
    """从归一后的三元组里取「要打 sfx 标记」的查找串集合。"""
    return tuple(jp for jp, _zh, s in pairs if s)


def _chapter_tr_files(cfg):
    """本作品全部已汉化章节文件（规范名，.gz 透明；磁盘缺失的清单项跳过）。"""
    out = []
    for fname in logic.chapter_order(cfg["out_dir"], cfg.get("name_style")):
        if os.path.exists(logic.txt_path(cfg["out_dir"], fname)):
            out.append(fname)
    return out


def _canon_tr(fname):
    """任意汉化文件名（含 .txt.gz / 已剥后缀）→ 规范名 entry_N_汉化.txt。"""
    k = logic.txt_key(fname or "")
    if k.endswith("_汉化"):
        k = k[:-len("_汉化")]
    return k + "_汉化.txt"


def _ordered_tr_files(cfg):
    """已汉化章节文件，顺序 = 阅读器目录顺序（chapter_order.json 优先）。

    「当前话及后续话」按阅读器目录顺序切片——否则配了 chapter_order.json 的作品
    （r401 原站顺序）会切错范围。名称用规范名（.txt），与阅读器 ?f= 口径一致。
    """
    out = []
    try:
        order_path = os.path.join(cfg["work_dir"], "chapter_order.json")
        mch = logic.merged_chapters(cfg["aa_dir"], cfg["out_dir"],
                                    cfg.get("name_style"), order_path)
        for ch in mch:
            if not ch.get("has_tr"):
                continue
            if os.path.exists(logic.txt_path(cfg["out_dir"], ch["tr_file"])):
                out.append(ch["tr_file"])
    except Exception:
        out = []
    known = {_canon_tr(f) for f in out}
    for f in _chapter_tr_files(cfg):     # 兜底补齐（aa 原文缺失但汉化还在的章）
        cf = _canon_tr(f)
        if cf in known:
            continue
        known.add(cf)
        out.append(cf)
    return out


def _scope_tr_files(cfg, scope, start, chapters=()):
    """按范围取章节文件：all = 全部已汉化章；from = 当前话及后续话（含当前话）。

    2026-10-01: chapters（规范名集合，非空时）在范围之上**再收一层**——对范围切片取
    交集，保持阅读器目录顺序；空 = 不过滤（旧行为不变）。
    """
    files = _ordered_tr_files(cfg)
    if (scope or "all") == "from":
        key = _canon_tr(start)
        idx = next((i for i, f in enumerate(files) if _canon_tr(f) == key), None)
        if idx is None:
            raise HTTPException(422, "定位不到当前章节（%s），无法按「当前话和后续话」限定范围"
                                % (start or "空"))
        files = files[idx:]
    if chapters:
        want = set(chapters)
        files = [f for f in files if _canon_tr(f) in want]
    return files


def _norm_chapters(raw):
    """章节过滤层归一 → 规范名元组（排序去重；供 _scope_tr_files 取交集 + 计划指纹）。"""
    out = {_canon_tr(c.strip()) for c in (raw or []) if isinstance(c, str) and c.strip()}
    return tuple(sorted(out))


def _chapter_replace_ctx(cfg, fname):
    """单章替换上下文：(src_path, tr_path, art_idx)。"""
    tr_path = logic.txt_path(cfg["out_dir"], fname)
    src_name = logic.src_name_of(fname)
    src_path = logic.txt_path(cfg["aa_dir"], src_name)
    base = logic.txt_key(fname)
    src_lines = logic.read_lines(src_path) if os.path.exists(src_path) else []
    n = len(logic.read_lines(tr_path))
    art = _chapter_art_idxs(cfg, base, n, src_lines)
    return src_path, tr_path, art


# ---- 替换对预筛（2026-09-17 预览提速）--------------------------------------
# 整章「一对都不可能命中」时零成本跳过：不读原文、不做逐行 art 分类。
def _compile_pairs(pairs):
    """[(is_re, matcher)]：正则只编译一次（原文在逐行循环里 re.compile 是浪费）。

    pairs 兼容 (jp, zh) / (jp, zh, sfx) —— 只取查找侧。
    """
    out = []
    for p in pairs:
        jp = p[0]
        if jp.startswith("re:"):
            try:
                out.append((True, re.compile(jp[3:], re.M)))
            except re.error:
                continue
        else:
            out.append((False, jp))
    return out


def _pairs_hit_text(cpairs, text):
    r"""整篇预筛：任一对可能在 text 里命中。

    只允许假阳性、绝不允许假阴性——正则按 re.M 整篇 search 是逐行 search 的超集
    （`.`/`^`/`$` 都不跨行；`\A`/`\Z` 整篇语义不同 → 直接判命中，不预筛）。
    """
    for is_re, m in cpairs:
        if is_re:
            if "\\A" in m.pattern or "\\Z" in m.pattern:
                return True
            if m.search(text):
                return True
        elif m in text:
            return True
    return False


def _pairs_hit_line(cpairs, line):
    """单行是否可能出现替换（真替换逻辑仍由 logic.apply_glossary_chapter 执行）。"""
    for is_re, m in cpairs:
        hit = m.search(line) if is_re else (m in line)
        if hit:
            return True
    return False


def _chapter_replace_plan(cfg, fname, pairs, sfx_pairs=()):
    """扫描单章 → (tr_path, changes, art_hit, sfx_marks)。

    changes = [(idx, before, after, overwide)]（0-based；AA 艺术行已跳过、宽度已校正）。
    sfx_marks = {1-based 行号: [[偏移, 插入文本], ...]}——只含勾了 sfx 的替换对命中的插入片段
    （偏移已换算到替换后的行上，跨多对替换也准；见 logic.apply_glossary_chapter 的 sfx_out）。
    两级筛选：① 整篇预筛不中 → 直接返回（零原文读取、零行分类）
    ② 命中行才做 art 判定（与行号列同口径；原文按需惰性读）→ 再交
    `logic.apply_glossary_chapter` 走同一套替换/宽度逻辑（唯一实现，不复制）。
    假阳性（预筛命中但替换无变化）由第二步自然消化。art_hit = 命中行里被 art 保护跳过的行数。
    """
    tr_path = logic.txt_path(cfg["out_dir"], fname)
    tr = logic.read_lines(tr_path)
    cpairs = _compile_pairs(pairs)
    if not _pairs_hit_text(cpairs, "\n".join(tr)):
        return tr_path, [], 0, {}
    cand = [i for i, b in enumerate(tr)
            if not logic.is_skip_line(b) and _pairs_hit_line(cpairs, b)]
    if not cand:
        return tr_path, [], 0, {}
    src_path = logic.txt_path(cfg["aa_dir"], logic.src_name_of(fname))
    meta, overrides = _art_sources(cfg, logic.txt_key(fname))
    _src = [None]

    def _lazy_src():
        if _src[0] is None:
            _src[0] = logic.read_lines(src_path) if os.path.exists(src_path) else []
        return _src[0]

    chk = _art_checker(meta, overrides, _lazy_src)
    art = {i for i in cand if chk(i)}
    sfx_out = {}
    changed, _skipped = logic.apply_glossary_chapter(
        src_path, tr_path, pairs, dry_run=True, skip_idx=art,
        sfx_pairs=sfx_pairs, sfx_out=sfx_out)
    return (tr_path,
            [(c[0], c[1], c[2], bool(c[3]) if len(c) > 3 else False) for c in changed],
            len(art),
            {i + 1: v for i, v in sfx_out.items()})


# ---- 预览扫描结果复用（2026-09-17）----------------------------------------
# 预览把逐章扫描结果（只含有变更的章）缓存下来；应用直接落这些行，不再全量重扫。
# 落地前逐文件校验（mtime+size 与逐行 before），过期只重算那一章——绝不误写。
_PLAN_CACHE = {}            # key -> {"ts": float, "files": [...]}
_PLAN_CACHE_MAX = 4         # 只留最近几次预览（本地单用户，够用不吃内存）
_PLAN_MAX_LINES = 120000    # 变更行超量时不缓存（应用退回现扫，防内存爆）
_PLAN_LOCK = threading.Lock()


def _plan_key(work, scope, start, pairs, chapters=()):
    """预览/应用共用指纹：作品 + 范围 + 章节过滤 + 替换对（归一后，含 sfx 勾选）。

    2026-10-01: chapters 进指纹——改章节勾选即视为另一个计划，预览缓存不会串。
    """
    return (work, scope, _canon_tr(start) if scope == "from" else "",
            tuple(chapters or ()), tuple(pairs))


def _plan_put(key, files):
    with _PLAN_LOCK:
        _PLAN_CACHE[key] = {"ts": time.time(), "files": files}
        while len(_PLAN_CACHE) > _PLAN_CACHE_MAX:
            _PLAN_CACHE.pop(min(_PLAN_CACHE, key=lambda k: _PLAN_CACHE[k]["ts"]))


def _plan_get(key):
    with _PLAN_LOCK:
        e = _PLAN_CACHE.get(key)
        return e["files"] if e else None


def _plan_drop(key):
    with _PLAN_LOCK:
        _PLAN_CACHE.pop(key, None)


def _scan_replace_plan(cfg, work, pairs, scope, start, chapters=()):
    """逐章扫描（预览与应用共用）→ (files, chapters, total, art_hit)。

    files = [{"file": 规范名, "tr_path", "changes": [(idx, before, after, overwide)], "sfx": {1-based行: [[off,text]]}}]
    只含真有变更的章；结果写进 _PLAN_CACHE 供 apply 直接复用。
    chapters（2026-10-01）= 勾选章节过滤层，与 scope 取交集后才是实扫章。
    """
    sfx_pairs = _sfx_pairs_of(pairs)
    scope_files = _scope_tr_files(cfg, scope, start, chapters)
    files, total, art_hit = [], 0, 0
    for fname in scope_files:
        tr_path, changed, hit, marks = _chapter_replace_plan(cfg, fname, pairs, sfx_pairs)
        art_hit += hit
        if not changed:
            continue
        files.append({"file": _canon_tr(fname), "tr_path": tr_path,
                      "art_hit": hit, "changes": changed, "sfx": marks})
        total += len(changed)
    if total <= _PLAN_MAX_LINES:
        _plan_put(_plan_key(work, scope, start, pairs, chapters), files)
    return files, len(scope_files), total, art_hit


def _plan_writes(cfg, plan, pairs):
    """应用前校验缓存计划 → [{"file","tr_path","idxs","lines","sfx"}]（只含真有变更的章）。

    直写条件：文件仍在 + 每个待改行的当前内容与预览时的 before 逐字相同；
    任一不符（预览后该章被改过/重译过）→ 只重算该章（单章扫描，不整库重扫）。
    2026-10-01: 每章带 changes = [(idx0, before, after), ...]——调用方逐行记
    work/edits.jsonl（改动前后数据的唯一留档，不再整文件备份）。
    """
    sfx_pairs = _sfx_pairs_of(pairs)
    out = []
    for f in plan:
        tr_path = f["tr_path"]
        if not os.path.exists(tr_path):
            continue
        lines = logic.read_lines(tr_path)
        marks = f.get("sfx") or {}
        fresh = all(0 <= c[0] < len(lines) and lines[c[0]] == c[1] for c in f["changes"])
        if fresh:
            idxs = []
            for c in f["changes"]:
                lines[c[0]] = c[2]
                idxs.append(c[0])
            changes = [(c[0], c[1], c[2]) for c in f["changes"]]
        else:
            src_path, tr_path2, art = _chapter_replace_ctx(cfg, f["file"])
            sfx_out = {}
            changed, _sk = logic.apply_glossary_chapter(
                src_path, tr_path2, pairs, dry_run=True, skip_idx=art,
                sfx_pairs=sfx_pairs, sfx_out=sfx_out)
            if not changed:
                continue
            lines = logic.read_lines(tr_path2)
            idxs = []
            for c in changed:
                lines[c[0]] = c[2]
                idxs.append(c[0])
            changes = [(c[0], c[1], c[2]) for c in changed]
            tr_path = tr_path2
            marks = {i + 1: v for i, v in sfx_out.items()}
        out.append({"file": f["file"], "tr_path": tr_path, "idxs": idxs,
                    "changes": changes, "lines": lines, "sfx": marks})
    return out


@app.post("/api/work/{work}/replace-preview")
def api_replace_preview(work: str, body: ReplacePairsBody):
    """翻译替换预览（按范围，dry-run 不落盘；扫描结果缓存给「应用」复用）。

    scope: all=全部已汉化章 / from=当前话及后续话（start=当前章汉化文件名）。
    返回 {scope, chapters, total, art_skipped, sfx_total, files:[{file, changed, art_skipped, sfx, lines}]}，
    lines = [1-based 行号, before[:160], after[:160], overwide]。
    art_skipped = 命中行里被 AA 艺术行保护跳过的行数（整章逐行 art 全量判定已不做）。
    sfx_total / files[].sfx = 勾了「sfx」的对命中的插入片段处数（应用后会写成
    <work_dir>/sfx_marks/<base>.json 标记，渲染成楷体斜体）。
    """
    cfg = cfg_of(work)
    pairs = _norm_manual_pairs(body.pairs)
    if not pairs:
        raise HTTPException(422, "请至少输入一对「查找 / 替换为」")
    scope = "from" if (body.scope or "all") == "from" else "all"
    chapters = _norm_chapters(body.chapters)
    plan, chapters_n, total, art_total = _scan_replace_plan(
        cfg, work, pairs, scope, body.start, chapters)
    files = [{"file": f["file"], "changed": len(f["changes"]),
              "art_skipped": f.get("art_hit", 0),
              "sfx": sum(len(v) for v in (f.get("sfx") or {}).values()),
              "lines": [[c[0] + 1, c[1][:160], c[2][:160], c[3]]
                        for c in f["changes"]]} for f in plan]
    return {"scope": scope, "chapters": chapters_n, "total": total,
            "picked": len(chapters),
            "art_skipped": art_total, "sfx_pairs": list(_sfx_pairs_of(pairs)),
            "sfx_total": sum(f["sfx"] for f in files), "files": files}


@app.post("/api/work/{work}/replace-apply")
def api_replace_apply(work: str, body: ReplacePairsBody):
    """应用翻译替换（按范围；逐行记改前/改后 + SRC 宽度校正）。

    优先复用「预览」的逐章扫描结果（不再全量重扫）；无预览缓存/缓存过期时才
    现扫或按章重算。**不再整文件备份到 work/backup/<ts>/**（2026-10-01 用户要求：
    备份目录/文件过多）——每个改动行写进单文件 work/edits.jsonl（含改前/改后，
    阅读器「修改记录」可查，可逐行还原）。
    """
    cfg = cfg_of(work)
    pairs = _norm_manual_pairs(body.pairs)
    if not pairs:
        raise HTTPException(422, "请至少输入一对「查找 / 替换为」")
    scope = "from" if (body.scope or "all") == "from" else "all"
    chapters = _norm_chapters(body.chapters)
    # 2026-10-01：章节条只影响「写哪些章」——先按**预览时的键**（不带章节过滤）找缓存，
    # 勾选集非空时再从缓存计划里筛掉没勾的章（无需重扫）；没有缓存才按章节过滤现扫。
    key_full = _plan_key(work, scope, body.start, pairs, ())
    plan = _plan_get(key_full)
    source = "preview"
    if plan is not None:
        if chapters:
            want = set(chapters)
            plan = [f for f in plan if _canon_tr(f["file"]) in want]
    else:
        source = "rescan"
        plan, _ch, _tt, _ah = _scan_replace_plan(cfg, work, pairs, scope, body.start, chapters)
    plan = _plan_writes(cfg, plan, pairs)
    # 计划已消费（落盘后文件内容已变，留着也是过期）；两个键都清
    _plan_drop(key_full)
    _plan_drop(_plan_key(work, scope, body.start, pairs, chapters))
    if not plan:
        return {"ok": True, "applied": 0, "scope": scope, "source": source,
                "picked": len(chapters), "files": []}
    with _LOCK:
        for p in plan:
            logic.write_lines(p["tr_path"], p["lines"])
        # 逐行记改前/改后（唯一留档，替代原「整文件备份目录」）
        logged = 0
        for p in plan:
            logged += _log_edits_bulk(cfg, p["file"], p.get("changes") or [], "翻译替换")
    invalidate_stats(work)
    applied = sum(len(p["idxs"]) for p in plan)
    # 2026-09-21: 勾了 sfx 的对 → 把命中的插入片段写成 sfx 标记（渲染成楷体斜体）
    sfx_added, sfx_failed = 0, []
    for p in plan:
        marks = p.get("sfx") or {}
        if not marks:
            continue
        # sfx 标记按**原文基名**落盘（与阅读器渲染/行保存同口径：src_name_of → txt_key）
        base = logic.txt_key(logic.src_name_of(p["file"]))
        ok, err, n = logic.merge_sfx_marks(cfg["work_dir"], base, marks)
        if ok:
            sfx_added += n
        else:
            sfx_failed.append({"file": p["file"], "error": err})
    # 2026-09-13: 记进作品替换历史（弹窗可直接勾选复用）；只记本次真正生效的调用
    try:
        logic.tr_hist_add(cfg["work_dir"], pairs, applied=applied, files=len(plan))
    except Exception as e:      # 历史写入失败不影响替换结果
        print(f"[replace-apply] 历史记录写入失败: {e}", flush=True)
    return {"ok": True, "applied": applied, "scope": scope, "source": source,
            "logged": logged, "picked": len(chapters),
            "sfx_marks": sfx_added, "sfx_failed": sfx_failed,
            "files": [{"file": p["file"], "applied": len(p["idxs"]),
                       "idxs": p["idxs"][:200]} for p in plan]}


@app.get("/api/work/{work}/replace-history")
def api_replace_history(work: str):
    """翻译替换历史记录（本作品，置顶在前、其余新在前）——弹窗「历史替换记录」用。

    只读；写入在 replace-apply 成功后自动发生（<work_dir>/tr_replace_history.json），
    置顶位由 POST .../replace-history/pin 改。
    """
    cfg = cfg_of(work)
    items = logic.tr_hist_load(cfg["work_dir"])
    return {"items": items, "max": logic.TR_HIST_MAX,
            "pinned": sum(1 for it in items if it.get("pin"))}


class TrHistPin(BaseModel):
    find: str = ""       # 查找串（与历史里存的完全一致，不做 strip）
    repl: str = ""       # 替换为（空 = 「删除该串」那条记录）
    pin: bool = True     # True = 置顶，False = 取消置顶


@app.post("/api/work/{work}/replace-history/pin")
def api_replace_history_pin(work: str, body: TrHistPin):
    """手动置顶 / 取消置顶一条历史替换记录（2026-10-13）。

    置顶的条目排在列表最前，且不占 TR_HIST_MAX 的淘汰名额（不会被旧记录挤掉）。
    """
    cfg = cfg_of(work)
    find = body.find or ""
    if not find:
        raise HTTPException(status_code=422, detail="find 不能为空")
    with _LOCK:
        found, items = logic.tr_hist_pin(cfg["work_dir"], find, body.repl or "", body.pin)
    if not found:
        raise HTTPException(status_code=404, detail="历史里找不到这一条（可能已被清除，刷新后重试）")
    return {"ok": True, "items": items, "max": logic.TR_HIST_MAX,
            "pinned": sum(1 for it in items if it.get("pin"))}


@app.get("/api/work/{work}/backups")
def api_backups(work: str):
    cfg = cfg_of(work)
    return {"backups": logic.list_backups(os.path.join(cfg["work_dir"], "backup"))}


@app.post("/api/work/{work}/backups/{ts}/restore")
def api_restore(work: str, ts: str):
    cfg = cfg_of(work)
    backup_root = os.path.join(cfg["work_dir"], "backup")
    with _LOCK:
        try:
            restored = logic.restore_backup(backup_root, ts, cfg["out_dir"])
        except ValueError as e:
            raise HTTPException(404, str(e))
    invalidate_stats(work)
    return {"ok": True, "restored": restored}


@app.get("/api/glossary")
def api_glossary_get():
    with open(logic._GLOSSARY_JSON, encoding="utf-8") as f:
        return json.load(f)


class GlossaryPayload(BaseModel):
    known_names: list = []
    glossary: list = []
    roles: list = []


@app.put("/api/glossary")
def api_glossary_put(payload: GlossaryPayload):
    data = {"known_names": payload.known_names,
            "glossary": payload.glossary,
            "角色": payload.roles}
    with _LOCK:
        r = logic.save_glossary(data)
        if r["ok"] and payload.roles:
            # 2026-09-01 三级重构: 角色保存到 roles.json
            rr = logic.save_roles(payload.roles)
            if not rr["ok"]:
                raise HTTPException(422, {"errors": rr["errors"]})
            r["角色"] = rr["角色"]
    if not r["ok"]:
        raise HTTPException(422, {"errors": r["errors"]})
    return r


@app.get("/api/glossary/backups")
def api_glossary_backups():
    root = os.path.join(os.path.dirname(logic._GLOSSARY_JSON), ".glossary_backup")
    return {"backups": logic.list_backups(root)}


@app.post("/api/glossary/backups/{ts}/restore")
def api_glossary_restore(ts: str):
    root = os.path.join(os.path.dirname(logic._GLOSSARY_JSON), ".glossary_backup")
    dest = os.path.dirname(logic._GLOSSARY_JSON)
    with _LOCK:
        try:
            restored = logic.restore_backup(root, ts, dest)
        except ValueError as e:
            raise HTTPException(404, str(e))
    return {"ok": True, "restored": restored}


# ---------- 阅读状态（read_state，作品列表改版 2026-09-03）----------
_READ_STATE_FILE = os.path.join(_DATA, "aa_web_app", "data", "read_state.json")
_READ_STATES = ("", "toread", "reading", "done")


def _load_read_state():
    try:
        with open(_READ_STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_read_state(data):
    try:
        with open(_READ_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


# ---------- 阅读进度 ----------

class ProgressAA(BaseModel):
    fname: str
    line: int = 1
    mode: str = ""   # 2026-09-03: tr/refine(汉化类) 自动置阅读中; src(原文) 不触发


class ProgressManga(BaseModel):
    chapter: str
    image: int = 0


@app.get("/api/aa/{work}/progress")
def api_aa_progress(work: str):
    cfg_of(work)
    return _load_progress().get("aa", {}).get(work, {})


@app.put("/api/aa/{work}/progress")
def api_aa_progress_put(work: str, body: ProgressAA):
    cfg_of(work)
    with _LOCK:
        data = _load_progress()
        data.setdefault("aa", {})[work] = {
            "fname": body.fname, "line": body.line,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        _save_progress(data)
        # 2026-09-03: mode=tr/refine(汉化类) 且 read_state != done → 自动置「阅读中」
        rs = _load_read_state()
        cur = rs.get(work, {}).get("state", "")
        new = logic.read_state_with_auto(cur, body.mode)
        if new != cur:
            rs.setdefault(work, {})["state"] = new
            rs[work]["updated"] = int(time.time())
            _save_read_state(rs)
    return {"ok": True}


@app.get("/api/manga/{name}/progress")
def api_manga_progress(name: str):
    if name not in IMAGE_MANGA:
        raise HTTPException(404, f"未知图片漫画: {name}")
    return _load_progress().get("manga", {}).get(name, {})


@app.put("/api/manga/{name}/progress")
def api_manga_progress_put(name: str, body: ProgressManga):
    if name not in IMAGE_MANGA:
        raise HTTPException(404, f"未知图片漫画: {name}")
    with _LOCK:
        data = _load_progress()
        data.setdefault("manga", {})[name] = {
            "chapter": body.chapter, "image": body.image,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        _save_progress(data)
    return {"ok": True}


# ---------- 图片漫画（仅阅览） ----------

def manga_chapters(root):
    return sorted(d for d in os.listdir(root)
                  if os.path.isdir(os.path.join(root, d)))


def manga_imgs(chapter_dir):
    return sorted(f for f in os.listdir(chapter_dir)
                  if os.path.splitext(f)[1].lower() in IMG_EXTS)


@app.get("/manga")
def manga_index(request: Request):
    items = []
    for name, root in IMAGE_MANGA.items():
        try:
            chs = manga_chapters(root)
            saved = _load_progress().get("manga", {}).get(name, {})
            items.append({"name": name, "chapters": len(chs),
                          "last": saved.get("chapter", ""),
                          "error": ""})
        except OSError as e:
            items.append({"name": name, "chapters": 0, "last": "", "error": str(e)})
    return templates.TemplateResponse(request, "manga.html", {
        "items": items, "active": "manga", "name": None,
        "chapters": [], "cur": "", "cur_idx": 0, "imgs": [], "total": 0})


@app.get("/manga/{name}")
def manga_reader(request: Request, name: str, ch: str = ""):
    root = IMAGE_MANGA.get(name)
    if not root:
        raise HTTPException(404, f"未知图片漫画: {name}（可用: {', '.join(IMAGE_MANGA)}）")
    chapters = manga_chapters(root)
    if not chapters:
        raise HTTPException(404, f"漫画 {name} 无章节目录")
    if ch not in chapters:
        ch = _load_progress().get("manga", {}).get(name, {}).get("chapter", "")
    if ch not in chapters:
        ch = chapters[0]
    imgs = manga_imgs(os.path.join(root, ch))
    return templates.TemplateResponse(request, "manga.html", {
        "items": [{"name": n, "chapters": len(manga_chapters(r)), "last": "", "error": ""}
                  for n, r in IMAGE_MANGA.items()],
        "active": "manga", "name": name, "chapters": chapters,
        "cur": ch, "cur_idx": chapters.index(ch), "imgs": imgs,
        "total": len(chapters)})


@app.get("/manga-img/{name}/{path:path}")
def manga_img(name: str, path: str):
    """图片直出（严格防目录穿越：解析后必须仍在漫画根目录内）。"""
    root = IMAGE_MANGA.get(name)
    if not root:
        raise HTTPException(404, "未知图片漫画")
    full = os.path.realpath(os.path.join(root, path))
    if not full.startswith(os.path.realpath(root) + os.sep) or not os.path.isfile(full):
        raise HTTPException(404, "图片不存在")
    return FileResponse(full)


# ---- 翻译任务（2026-08-21：勾章节 → 后台子进程跑 translate_runner → 轮询）----

def _task_file(cfg):
    return os.path.join(cfg["work_dir"], "runner_task.json")


def _lock_file(cfg):
    return os.path.join(cfg["work_dir"], ".runner.lock")


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _task_status(cfg):
    """读 runner_task.json；无任务/已结束返回 None。"""
    tf = _task_file(cfg)
    if not os.path.exists(tf):
        return None
    try:
        with open(tf, encoding="utf-8") as f:
            t = json.load(f)
    except Exception:
        return None
    # 校正 running 状态（进程退出但没更新文件）
    if t.get("status") == "running" and t.get("pid"):
        if not _pid_alive(t["pid"]):
            t["status"] = "crashed"
    return t


def _read_log_tail(cfg, n=60):
    lp = os.path.join(cfg["work_dir"], "runner_log.txt")
    if not os.path.exists(lp):
        return ""
    try:
        with open(lp, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:])
    except Exception:
        return ""


@app.get("/work/{work}/translate")
def translate_page(request: Request, work: str):
    cfg = cfg_of(work)
    aa_dir, tr_dir = cfg["aa_dir"], cfg["out_dir"]
    chapters = logic.merged_chapters(aa_dir, tr_dir, cfg.get("name_style"),
                                     os.path.join(cfg["work_dir"], "chapter_order.json"))
    chs = []
    for c in chapters or []:
        tr_path = os.path.join(tr_dir, c["tr_file"])
        translated = os.path.exists(tr_path)
        chs.append({
            "src_file": c["src_file"], "tr_file": c["tr_file"],
            "title": c.get("title") or c["src_file"],
            "translated": translated,
        })
    task = _task_public(_latest_task_for_work(work))   # 2026-08-29 读管理器
    return templates.TemplateResponse(request, "translate.html", {
        "work": work, "cfg": cfg, "chapters": chs,
        "task": task, "log_tail": _read_log_tail(cfg) if task else "",
        "active": "works"})


@app.get("/work/{work}/proofread")
def proofread_page(request: Request, work: str):
    """2026-09-07: 单独校对控制页(选已译章 → 启动 refine_aa 校对任务)。对称翻译控制台。"""
    cfg = cfg_of(work)
    aa_dir, tr_dir = cfg["aa_dir"], cfg["out_dir"]
    chapters = logic.merged_chapters(aa_dir, tr_dir, cfg.get("name_style"),
                                     os.path.join(cfg["work_dir"], "chapter_order.json"))
    chs = []
    for c in chapters or []:
        tr_path = os.path.join(tr_dir, c["tr_file"])
        translated = os.path.exists(tr_path)
        chs.append({
            "src_file": c["src_file"], "tr_file": c["tr_file"],
            "title": c.get("title") or c["src_file"],
            "translated": translated,
        })
    task = _task_public(_latest_task_for_work(work))
    return templates.TemplateResponse(request, "proofread.html", {
        "work": work, "cfg": cfg, "chapters": chs,
        "task": task, "log_tail": _read_log_tail(cfg) if task else "",
        "active": "works"})


def _aa_config():
    """懒导入 aa_config（scripts/common 已在 sys.path；工作台只在模型多选处用到）。"""
    import aa_config as AC
    return AC


def _chain_env(work, body, kind):
    """「付费链模型多选」→ 任务 env（2026-09-22）。

    勾选集 == 默认集 → 返回 {}（不写 env = 整条链，含 `fallback_only` 兜底端点，与旧行为一致）。
    勾选集不同 → 翻译写 `AA_CHAIN`；**校对链已跟随付费链**（2026-09-22 `[refine_aa].follow_default`），
    所以翻译任务**同时写 `AA_REFINE_BACKEND`**（翻译任务的校对阶段也用勾选模型），
    独立校对任务只写 `AA_REFINE_BACKEND`。
    """
    mods = [str(x).strip() for x in ((body or {}).get("models") or []) if str(x).strip()]
    if not mods:
        return {}
    AC = _aa_config()
    try:
        r18 = bool((cfg_of(work) or {}).get("r18"))
        _all, dflt = AC.chain_catalog(r18)          # 校对链跟随付费链 → 默认集同源
    except Exception:
        return {"AA_CHAIN": ",".join(mods), "AA_REFINE_BACKEND": ",".join(mods)}
    if set(mods) == set(dflt):
        return {}
    if kind == "proofread":
        return {"AA_REFINE_BACKEND": ",".join(mods)}
    return {"AA_CHAIN": ",".join(mods), "AA_REFINE_BACKEND": ",".join(mods)}


@app.get("/api/work/{work}/models")
def api_work_models(work: str, type: str = "translate"):
    """候选模型（端点）清单 —— 翻译/校对控制台的「付费链模型多选」（2026-09-22）。

    零 API：只读 `model_config.toml` 的链定义 + 单价表。
    query: type=translate（主翻译链 normal/r18，按作品 r18）| proofread（`[refine_aa].backends`）
    返回 {ok, type, r18, chain:[{backend,model,label,cache,r18,fallback_only,available,rate}],
          selected:[条目 id]（**当前生效勾选集** = 设置页保存的 AA_CHAIN / AA_REFINE_BACKEND
          与本作品链的交集；未设 = 整条链的默认集）, default:[条目 id], custom:bool,
          head:"主力端点（= 生效链第一个可缓存端点，与实际跑的头一个端点同口径）"}
    """
    try:
        AC = _aa_config()
        try:
            r18 = bool((cfg_of(work) or {}).get("r18"))
        except Exception:
            r18 = False
        if type == "proofread":
            chain, default = AC.refine_catalog(r18)
            _env_name = "AA_REFINE_BACKEND"
        else:
            chain, default = AC.chain_catalog(r18)
            _env_name = "AA_CHAIN"
        # 生效勾选集（2026-10-10）：设置页保存的选择 = 控制台多选的默认勾选，两页不再各说各话。
        # 用户选择的条目可能不在本作品的链上（如给 R18 作品选了不接 R18 的 mimo）→ 交集为空时回默认集。
        sel = AC.chain_selection(_env_name, chain, default)
        ids = {it["id"] for it in chain}
        picked = [x for x in sel["selected"] if x in ids] if sel["custom"] else []
        selected = picked or list(default)
        custom = bool(picked)
        # 生效链 = TOML 链序 ∩ 勾选集（与 aa_config.backend_chain 同序；未自定义 = 整条链）
        eff = [it for it in chain if it["id"] in selected] if custom else list(chain)
        if not eff:
            eff = list(chain)
        head = ""
        for it in eff:                        # 主力 = 生效链第一个可缓存端点（章内 pin 目标）
            if it["available"] and not it.get("fallback_only") and it.get("cache"):
                head = it["label"]
                break
        if not head:
            for it in eff:
                if it["available"] and not it.get("fallback_only"):
                    head = it["label"]
                    break
        if not head and eff:
            head = eff[0]["label"]
        return {"ok": True, "type": "proofread" if type == "proofread" else "translate",
                "r18": r18, "chain": chain, "selected": selected, "default": default,
                "custom": custom, "head": head}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (e.__class__.__name__, e)}


@app.get("/api/work/{work}/estimate")
def api_work_estimate(work: str, type: str = "translate", eids: str = "",
                      force: int = 0, free: int = 0, models: str = "", at: str = ""):
    """翻译/校对任务的 token + 金额预估（2026-09-21，零 API）。

    query: type=translate|proofread · eids=1,2,3（空=全部章节）· force=1（翻译重译已译章也计）
           · free=1（免费模型 → 金额记 0）· models=deepseek,deepinfra（模型多选，空=整条链）
           · at=2026-09-30 00:00（**定时启动时刻**，2026-09-29）→ 另给 `cost_at`（按该时刻倍率折算的金额）
    口径见 aa_web_app/estimate.py 模块 docstring（系数实测来源）。
    """
    from aa_web_app import estimate as EST
    ids = [x.strip() for x in (eids or "").split(",") if x.strip()]
    mods = [x.strip() for x in (models or "").split(",") if x.strip()]
    at_ts = TM.parse_schedule_at(at) if at else None
    try:
        r18 = bool((cfg_of(work) or {}).get("r18"))
    except Exception:
        r18 = False
    try:
        return EST.estimate(work, type, ids, force=bool(force), free=bool(free),
                            work_r18=r18, models=mods or None, at_ts=at_ts)
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (e.__class__.__name__, e)}


@app.get("/api/work/{work}/translate/status")
def api_translate_status(work: str):
    """2026-08-29 改读管理器: 该作品最近任务。兼容 translate.html 轮询。"""
    t = _latest_task_for_work(work)
    if not t:
        return {"status": "idle", "log": ""}
    return _task_public(t, log_tail=60)


@app.post("/api/work/{work}/translate")
def api_translate_start(work: str, body: dict = None):
    """启动翻译任务（2026-08-29 改走统一任务管理器 enqueue）。body: {eids: [...], force, gen}"""
    body = body or {}
    eids = body.get("eids") or []
    if not eids:
        return {"ok": False, "error": "未选择章节"}
    # 2026-09-06 用户规则: 翻译前必须已有作品术语表(只手动生成, 不自动灌 auto)。
    # 这里提前检查给友好提示(translate_runner 1.7 还有硬拦截兜底)。
    import json as _j
    _gl_p = os.path.join(cfg_of(work)["work_dir"], f"glossary_{work}.json")
    _has_gl = False
    if os.path.exists(_gl_p):
        try:
            _gd = _j.load(open(_gl_p, encoding="utf-8"))
            _has_gl = len(_gd.get("glossary", [])) > 0
        except Exception:
            _has_gl = False
    if not _has_gl:
        return {"ok": False, "error":
                "无作品术语表，翻译已中止。请先手动生成：\n"
                "① 快速派生(零 token): python scripts/common/gen_work_glossary.py --work "
                f"{work} --force\n"
                "② 完整流程: 作品页「生成术语候选…」→ 候选确认 →「写入作品表」(AI 提取+人工确认)。\n"
                "术语表是翻译硬前置。"}
    chapters = ",".join(str(e) for e in eids)
    params = {
        "chapters": chapters,
        "force": bool(body.get("force")),
        "no_proofread": bool(body.get("no_proofread")),   # 2026-09-07: 校对默认开, 可选禁用
    }
    # 2026-09-29 定时启动（工作台翻译控制台）：排到小米 token plan 折扣时段（每日 00:00-08:00，系数 0.8×）。
    # 约束（用户口径「仅 mimo-v2.6 模型」）：只能钉小米 mimo —— AA_CHAIN / AA_REFINE_BACKEND 都写 mimo，
    # 链内不降级到 deepseek/deepinfra（否则折扣时段的消耗跑到别的付费端点，白等）；
    # R18 作品直接拒绝：r18 链里没有 mimo（v2.6 对 R18 一律 content_filter），写了 AA_CHAIN 也会静默回退整链。
    _sched_ts = None
    if body.get("schedule_at"):
        if body.get("free_mode"):
            return {"ok": False, "error": "定时启动只能配付费链的小米 mimo-v2.6（免费链无 token plan 折扣）"}
        try:
            _is_r18 = bool((cfg_of(work) or {}).get("r18"))
        except Exception:
            _is_r18 = False
        if _is_r18:
            return {"ok": False, "error": "R18 作品不能用定时启动：小米 mimo 对 R18 一律 content_filter，"
                                          "r18 链里也没有它（请直接启动翻译）"}
        _sched_ts = TM.parse_schedule_at(body.get("schedule_at"))
        if _sched_ts is None:
            return {"ok": False, "error": "定时时间格式不对（用 YYYY-MM-DD HH:MM）"}
        if _sched_ts <= time.time():
            return {"ok": False, "error": "定时时间已过，请选一个将来的时刻（或直接启动）"}
        params["schedule_at"] = _sched_ts
    # 2026-08-29 免费模型选项: free_mode=true → env AA_FREE_MODE=1(免费链)
    # 2026-09-22 付费链模型多选: models=[...] → env AA_CHAIN（勾选集=默认集时不写）
    _env = {}
    if body.get("free_mode"):
        _env["AA_FREE_MODE"] = "1"
    elif _sched_ts is not None:
        _env["AA_CHAIN"] = "mimo"                 # 定时: 只走小米 mimo-v2.6（翻译+校对）
        _env["AA_REFINE_BACKEND"] = "mimo"
    else:
        _env.update(_chain_env(work, body, "translate"))
    if _env:
        params["env"] = _env
    # 2026-09-22 方案 D8/D11: pipeline=pokemon 的作品走专属链（固定内容表预填 + wiki 术语绑定）
    _ttype = "pokemon" if WP.pipeline_of(work) == WP.POKEMON else "translate"
    r = TM.enqueue(_ttype, work, params)
    if not r.get("ok"):
        return r
    _cn = "宝可梦专属链翻译" if _ttype == "pokemon" else "翻译"
    if r.get("scheduled"):
        return {"ok": True, "msg": r.get("msg") or f"定时{_cn}任务已排队: {r['task_id']}",
                "task_id": r["task_id"], "scheduled": True}
    return {"ok": True, "msg": f"{_cn}任务已入队: {r['task_id']}"}


@app.post("/api/work/{work}/proofread")
def api_proofread_start(work: str, body: dict = None):
    """2026-09-07: 单独校对任务——work 页「校对任务」按钮。body: {eids: [...]}(可选)。

    跑 refine_aa(段级重建+术语注入, 废旧 proofread.py) 对指定章/全部译章 --write。
    复用 task_manager proofread 类型 + 同作品串行(enqueue 自带)。
    """
    body = body or {}
    eids = body.get("eids") or []
    params = {}
    if eids:
        params["chapters"] = ",".join(str(e) for e in eids)
    # 2026-09-22 校对链模型多选: models=[...] → env AA_REFINE_BACKEND（勾选集=默认集时不写）
    _env = _chain_env(work, body, "proofread")
    if _env:
        params["env"] = _env
    r = TM.enqueue("proofread", work, params)
    if not r.get("ok"):
        return r
    return {"ok": True, "msg": f"校对任务已入队: {r['task_id']}", "task_id": r["task_id"]}


@app.post("/api/work/{work}/translate/cancel")
def api_translate_cancel(work: str):
    """取消/终止（2026-08-29 兼容旧语义=强制终止；建议新任务页用两档优雅停止）"""
    t = _latest_task_for_work(work)
    if not t or not t.get("pid"):
        return {"ok": False, "error": "无运行中任务"}
    return TM.force_kill(t["task_id"])


# ---- 统一任务管理器 web 接口(2026-08-29) ----

_FINAL_STATUSES = ("done", "failed", "crashed", "stopped", "cancelled", "stalled")


def _latest_task_for_work(work):
    """该作品最近一个非终态任务，无则最近一个终态任务。"""
    ts = [t for t in TM.list_tasks() if t.get("work") == work]
    live = [t for t in ts if t.get("status") not in
            ("done", "failed", "crashed", "stopped", "cancelled", "stalled")]
    return (live or ts)[0] if (live or ts) else None


def _task_public(t, log_tail=0):
    """把管理器任务映射成前端 json（status/chapters/log/phase 等）。"""
    if not t:
        return None
    d = {k: t.get(k) for k in ("task_id", "status", "pid", "rc", "started", "finished",
                               "type", "work", "stop_mode")}
    d["chapters"] = (t.get("params") or {}).get("chapters")
    # 2026-09-29 定时任务: 前端显示「定时 MM-DD HH:MM 启动」（status=scheduled = 未到点，还没启动）
    _sched = (t.get("params") or {}).get("schedule_at")
    if _sched:
        try:
            d["schedule_ts"] = float(_sched)
            d["scheduled_at"] = time.strftime("%m-%d %H:%M", time.localtime(float(_sched)))
        except (TypeError, ValueError):
            pass
    # 2026-09-13 链式后续（download → translate）：前端可显示"完成后自动接 <task_id>"
    if t.get("followup"):
        d["followup"] = t["followup"]
    # 2026-08-29 标题(registry 日文名)供前端同时显示
    # 2026-08-30: registry.get 抛 SystemExit（非 Exception）——作品被删/改名后列表 500 崩溃
    # 2026-10-10: 揭示板任务（伪 work id `board:<板id>`）标题 = 目录/关键词
    try:
        if t.get("type") == "board":
            _bp = t.get("params") or {}
            d["title"] = "揭示板：" + str(_bp.get("dir") or _bp.get("match") or _bp.get("board") or "")
            d["board_dir"] = _bp.get("dir") or ""
        else:
            d["title"] = registry.WORKS.get(t.get("work") or "", {}).get("title", "")
    except Exception:
        d["title"] = ""
    hb = t.get("hb") or {}
    d["phase"] = hb.get("phase")
    d["chapter_idx"] = hb.get("chapter_idx")
    d["chapter_total"] = hb.get("chapter_total")
    if log_tail:
        if t.get("log"):
            d["log"] = t["log"]   # 调用方已提供（api_tasks_get 预取）
        else:
            gt = TM.get_task(t["task_id"], log_tail=log_tail)
            d["log"] = (gt or {}).get("log", "")
    d["usage"] = t.get("usage")
    # 2026-08-29 免费链标记: task 参数 env 含 AA_FREE_MODE=1 即免费链
    d["free_mode"] = bool(((t.get("params") or {}).get("env") or {}).get("AA_FREE_MODE") == "1")
    # 运行时长/预估: running 用 started_ts~now, 终态用 started~finished
    # 2026-08-30 修复: finished_ts 缺失(reconcile/cancel 路径只写 finished 字符串)时
    # 解析 finished 字符串回退, 严禁 fallback time.time() —— 会让已结束任务 elapsed 无限累计
    t0 = t.get("started_ts")
    if t0:
        t1 = t.get("finished_ts")
        if t1 is None and t.get("status") in _FINAL_STATUSES:
            _fs = t.get("finished")
            try:
                t1 = time.mktime(time.strptime(_fs, "%Y-%m-%d %H:%M:%S")) if _fs else None
            except Exception:
                t1 = None
        if t1 is None:
            t1 = time.time()
        d["elapsed"] = max(0.0, t1 - t0)
        hb = t.get("hb") or {}
        idx, tot = hb.get("chapter_idx") or 0, hb.get("chapter_total") or 0
        if t.get("status") not in ("done", "failed", "crashed", "stopped", "cancelled", "stalled") and idx and tot and idx > 0:
            per = d["elapsed"] / idx
            d["eta"] = per * (tot - idx)
    return d


@app.get("/tasks")
def tasks_page(request: Request):
    return templates.TemplateResponse(request, "tasks.html", {
        "active": "tasks", "max_parallel": TM.MAX_PARALLEL,
        "max_parallel_dl": TM.MAX_PARALLEL_DL})


@app.get("/api/tasks")
def api_tasks_list(log: int = 0):
    """log=1 时附带日志尾。

    2026-09-13 用户要求: **任务页不再展示实时日志**(只显示状态/进度), 页面轮询不带 log;
    此参数保留给外部工具/CLI 查日志(单任务走 /api/tasks/<tid>?log=1)。"""
    out = []
    for t in TM.list_tasks():
        pub = _task_public(t)
        if pub:
            pub["usage"] = TM.aggregate_usage(t)
            if log:
                gt = TM.get_task(t["task_id"], log_tail=20)
                pub["log"] = (gt or {}).get("log", "")
            out.append(pub)
    return {"tasks": out}


@app.post("/api/tasks/prune-finished")
def api_tasks_prune_finished():
    """一键清理**全部已结束任务**（2026-09-19 用户要求）。

    只删终态（done/failed/stopped/crashed/cancelled/stalled）；运行中/停止中/排队中
    一律保留（删排队任务 = 静默丢待跑工作）。返回 {ok, removed, count, kept, msg}。"""
    return TM.prune_finished_tasks()


@app.post("/api/tasks")
def api_tasks_enqueue(body: dict = None):
    body = body or {}
    ttype = body.get("type") or "translate"
    work = body.get("work")
    if not work:
        return {"ok": False, "error": "缺 work"}
    params = {"chapters": body.get("chapters") or "new",
              "force": bool(body.get("force")),
              "env": body.get("env") or {}}
    return TM.enqueue(ttype, work, params)


@app.get("/api/tasks/{tid}")
def api_tasks_get(tid: str, log: int = 0):
    """单个任务详情（log=1 带日志尾；供 toggleLog 单任务拉取，避免全量 log=1）。"""
    t = TM.get_task(tid, log_tail=20 if log else 0)
    if not t:
        raise HTTPException(404, f"任务 {tid} 不存在")
    return _task_public(t, log_tail=20 if log else 0)


@app.post("/api/tasks/{tid}/stop")
def api_tasks_stop(tid: str, body: dict = None):
    return TM.request_stop(tid, (body or {}).get("mode") or "chapter")


@app.post("/api/tasks/{tid}/kill")
def api_tasks_kill(tid: str):
    return TM.force_kill(tid)


@app.post("/api/tasks/{tid}/cancel")
def api_tasks_cancel(tid: str):
    return TM.cancel_queued(tid)


@app.post("/api/tasks/{tid}/remove")
def api_tasks_remove(tid: str):
    return TM.remove_task(tid)

# ---------- 作品列表改版（2026-09-03）：阅读状态 / 删除 / 章节名+正文搜索 ----------

def _all_work_dirs():
    """全部作品用到的目录规范路径集合（判删除共享根）。"""
    s = set()
    for _cfg in registry.WORKS.values():
        for _k in ('aa_dir', 'out_dir', 'work_dir', 'raw_dir'):
            _p = _cfg.get(_k)
            if _p:
                s.add(os.path.normpath(_p))
    return s


def _delete_roots():
    """删除边界白名单根（realpath 后）：仓库根 / 数据根 **及其下的 aaManga 数据目录**。

    为什么必须带 aaManga（2026-10-12 实测，ポケの子（？）(FC2) 删除 400）：`aaManga/` 常被
    做成 junction/symlink 指到别的盘（本机 `<repo>\\aaManga` → `E:\\aaManga`），
    于是作品目录 realpath 后落在仓库根之外，而旧检查只许 realpath 在 `registry.BASE` 内
    → **每一次删除**都被判「删除路径越界」（107 部作品全中，不只这一部）。
    数据目录本就是允许删的地方，故把它（realpath）一并进白名单；其它任意路径仍然拒绝。
    """
    roots = []
    for r in (registry.BASE, _DATA):
        if not r:
            continue
        for c in (os.path.realpath(r), os.path.realpath(os.path.join(r, "aaManga"))):
            if c not in roots:
                roots.append(c)
    return roots


def _in_delete_roots(p, roots):
    """p 的 realpath 是否落在白名单根内（junction 已在 roots 侧解析过，两侧口径一致）。"""
    pr = os.path.realpath(p)
    for r in roots:
        if pr == r or pr.startswith(r + os.sep):
            return True
    return False


class ReadStateBody(BaseModel):
    state: str = ""


@app.get("/api/read-state")
def api_read_state_get():
    return _load_read_state()


@app.put("/api/read-state/{work}")
def api_read_state_put(work: str, body: ReadStateBody):
    cfg_of(work)
    if body.state not in ("", "toread", "reading", "done"):
        raise HTTPException(422, f"非法阅读状态: {body.state}")
    with _LOCK:
        data = _load_read_state()
        if body.state == "":
            data.pop(work, None)   # 清除回空
        else:
            data.setdefault(work, {})["state"] = body.state
            data[work]["updated"] = int(time.time())
        _save_read_state(data)
    return {"ok": True, "work": work, "state": body.state}


@app.get("/api/work/{work}/delete-preview")
def api_delete_preview(work: str):
    cfg = cfg_of(work)
    surf = logic.delete_surface(cfg, _all_work_dirs())
    dirs = []
    for kind, p in surf['items']:
        info = {"path": p, "exists": os.path.exists(p)}
        if os.path.isdir(p):
            try:
                info["size"] = sum(os.path.getsize(os.path.join(r, f))
                                   for r, _, fs in os.walk(p) for f in fs)
            except OSError:
                info["size"] = 0
            info["chapters"] = len([f for f in os.listdir(p)
                                    if f.endswith(("_汉化.txt", "_汉化.txt.gz"))]) if os.path.isdir(p) else 0
        dirs.append(info)
    # 交付物件（文件夹交付 → 整个文件夹；旧单文件 → 该 .html）
    htmls = [{"path": p, "exists": True} for p in _deliver_artifacts(cfg)]
    if not htmls:
        _dn = os.path.basename(os.path.dirname(cfg.get("aa_dir", "")))
        if _dn:
            import glob as _glob
            # 交付物固定到 aaManga/交付HTML/（2026-10-10 起）；根目录为历史位置，仍兼容
            for _ddir in (os.path.join(_DATA, "aaManga", "交付HTML"),
                          os.path.join(_DATA, "交付HTML")):
                if not os.path.isdir(_ddir):
                    continue
                for pat_suffix in ("_合集.html", "_汉化合集.html"):
                    for f in _glob.glob(os.path.join(_ddir, _dn + "*" + pat_suffix)):
                        htmls.append({"path": f, "exists": os.path.exists(f)})
                if htmls:
                    break
    # work 级知识资产（若不在删除面内则列出）
    extra = []
    _wd = cfg.get("work_dir", "")
    if _wd and os.path.isdir(_wd):
        for _f in os.listdir(_wd):
            if _f.endswith("_knowledge.json") or _f.startswith("glossary_"):
                extra.append({"path": os.path.join(_wd, _f), "exists": True})
    return {"work": work, "shared_root": surf['shared'], "dirs": dirs,
            "htmls": htmls, "extra": extra}


@app.post("/api/work/{work}/delete")
def api_delete(work: str, body: dict = None):
    body = body or {}
    if not body.get("confirm"):
        raise HTTPException(422, "需 confirm:true")
    cfg = cfg_of(work)
    # 有运行中任务 → 409
    for t in TM.list_tasks():
        if t.get("work") == work and t.get("status") not in (
                "done", "failed", "crashed", "stopped", "cancelled", "stalled"):
            raise HTTPException(409, f"作品 {work} 有运行中任务，请先停止/取消")
    # 1. 校验路径在允许的删除根内（防穿越；aaManga 常是 junction → 见 _delete_roots）
    roots = _delete_roots()
    surf = logic.delete_surface(cfg, _all_work_dirs())
    arts = _deliver_artifacts(cfg)
    for _p in [p for _k, p in surf['items']] + list(arts):
        if not _in_delete_roots(_p, roots):
            raise HTTPException(400, f"删除路径越界: {_p}")
    # 2. **先改记录、后删文件**（2026-10-09 改序）：旧实现先删盘再摘 registry 条目，
    #    而 registry 那步一旦失败（打包版曾经必失败）就抛出 —— 盘上文件已没了、记录还在，
    #    作品列表里留一个点不开又删不掉的僵尸条目（MM / bar_miku 实测）。现在记录失败 →
    #    直接 500 且**一个文件都不动**；文件删不动只会留下空目录，可重试。
    if not _remove_registry_entry(work):
        raise HTTPException(500, f"registry 移除失败: {work}（未删除任何文件）")
    # 3. work_meta 删键
    meta = logic.load_work_meta(_WORK_META_FILE)
    if work in meta:
        del meta[work]
        with _LOCK:
            logic.save_work_meta(_WORK_META_FILE, meta)
    # 4. read_state 删键
    rs = _load_read_state()
    if work in rs:
        rs.pop(work, None)
        with _LOCK:
            _save_read_state(rs)
    # 5. 删盘（目录 + 交付物件）
    removed = logic.delete_work(surf)
    removed += logic.remove_paths(arts)
    failed = [p for _k, p in surf['items'] if os.path.exists(p)]
    failed += [p for p in arts if os.path.exists(p)]
    if failed:
        print("[delete] %s 有 %d 个路径删不掉（多为文件被占用，可停掉实例后重试）: %s"
              % (work, len(failed), failed), flush=True)
    # 6. 热加载（必须 registry.py 改后）——warm=False + clear_cache=False：
    #    删后不启动全量预热、不清其它作品缓存（否则与前端 location.reload 抢磁盘
    #    IO/触发全量冷算，首页卡刷新——2026-09-03 修复）；只 pop 被删作品自身缓存
    api_reload(warm=False, clear_cache=False)
    _STATS_CACHE.pop(work, None)
    _LASTUPD_CACHE.pop(work, None)
    return {"ok": True, "work": work, "removed": removed, "failed": failed}


def _remove_registry_entry(work_id):
    """registry.py 里移除 `"<key>": dict(...)` 条目（行级，返回 bool）。
    从 `"<key>": dict(` 所在行找到其配对 `    ),` 结束。"""
    rp = _registry_path()
    try:
        text = open(rp, "rb").read().decode("utf-8")
    except OSError:
        return False
    marker = f'    "{work_id}": dict('
    i = text.find(marker)
    if i == -1:
        return False
    lstart = text.rfind("\n", 0, i) + 1
    # 找条目结束 `    ),`（4空格缩进）——优先 CRLF，次 LF
    pat_crlf, pat_lf = "\r\n    ),\r\n", "\n    ),\n"
    j = text.find(pat_crlf, i)
    if j == -1:
        j = text.find(pat_lf, i)
    if j == -1:
        return False
    jend = j + (len(pat_crlf) if text[j:j + len(pat_crlf)] == pat_crlf else len(pat_lf))
    new_text = text[:lstart] + text[jend:]
    # 简化相邻空行（防堆积）
    while "\r\n\r\n\r\n" in new_text:
        new_text = new_text.replace("\r\n\r\n\r\n", "\r\n\r\n")
    while "\n\n\n" in new_text:
        new_text = new_text.replace("\n\n\n", "\n\n")
    try:
        with open(rp, "w", encoding="utf-8", newline="") as f:
            f.write(new_text)
    except OSError:
        return False
    return True



# ---------- 作品导出 / 导入（2026-10-01）----------
# 打包实现见 aa_web_app/work_pack.py：rar 包内路径 = 相对仓库根，导入解到仓库根即归位；
# 只带资产（aa/汉化/aaManga/交付HTML/work 白名单），work 过程文件与备份不进包。

_EXPORT_JOBS = {}     # work -> {state, ts, log, path, name, size, error}
_IMPORT_JOBS = {}     # token -> {state, ts, rar, manifest, preview, result, error, log}


def _export_dir():
    return ((os.environ.get("AA_EXPORT_DIR") or "").strip()
            or os.path.join(_ROOT, "导出"))


def _work_busy(work):
    """该作品是否有未结束任务（导出/删除共用；打包中文件可能在写）。"""
    for t in TM.list_tasks():
        if t.get("work") == work and t.get("status") not in (
                "done", "failed", "crashed", "stopped", "cancelled", "stalled"):
            return True
    return False


@app.get("/api/work/{work}/export-preview")
def api_export_preview(work: str):
    """导出预览：包内文件数/体积（含 raw 与不含 raw 两种）、work 白名单资产、排除项计数。"""
    cfg = cfg_of(work)
    rar = work_pack.find_rar()
    try:
        no_raw = work_pack.plan_export(cfg, work, include_raw=False)
        with_raw = work_pack.plan_export(cfg, work, include_raw=True)
    except work_pack.PackError as e:
        raise HTTPException(400, str(e))
    out = {
        "ok": True, "rar": rar, "rar_ok": bool(rar), "export_dir": _export_dir(),
        "work": work, "title": cfg.get("title"), "root": no_raw["root"],
        "files": no_raw["files"], "bytes": no_raw["bytes"],
        "with_raw_files": with_raw["files"], "with_raw_bytes": with_raw["bytes"],
        "raw_files": no_raw["raw_files"], "raw_bytes": no_raw["raw_bytes"],
        "html": no_raw["html"],
        "assets": no_raw["work_asset_names"],
        "skipped": no_raw["work_skipped_names"],
        "busy": _work_busy(work),
    }
    if not rar:
        out["error"] = "未找到 Rar.exe：请安装 WinRAR，或用环境变量 AA_RAR 指定 Rar.exe 全路径"
    return out


@app.post("/api/work/{work}/export")
def api_export_start(work: str, body: dict = None):
    """后台打包（线程 + 轮询 /export/status），完成后 /export/download 下载。"""
    cfg = cfg_of(work)
    body = body or {}
    if not work_pack.find_rar():
        raise HTTPException(400, "未找到 Rar.exe（安装 WinRAR 或设 AA_RAR）")
    if _work_busy(work):
        raise HTTPException(409, f"作品 {work} 有运行中任务，等它结束再导出")
    st = _EXPORT_JOBS.get(work)
    if st and st.get("state") == "running":
        raise HTTPException(409, "该作品已有导出任务在跑")
    include_raw = bool(body.get("include_raw"))
    job = {"state": "running", "ts": time.time(), "log": [], "path": None,
           "error": None, "include_raw": include_raw}
    _EXPORT_JOBS[work] = job

    def _run():
        try:
            res = work_pack.run_export(
                cfg, work, include_raw=include_raw, out_dir=_export_dir(),
                progress=lambda d: job["log"].append(d.get("msg") or ""))
            job.update({"state": "done", "path": res["path"], "name": res["name"],
                        "size": res["size"], "files": res["files"], "secs": res["secs"]})
        except Exception as e:      # PackError 与意外异常都落到任务状态里
            job.update({"state": "error", "error": "%s" % e})
        finally:
            job["ts"] = time.time()

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "work": work, "include_raw": include_raw}


@app.get("/api/work/{work}/export/status")
def api_export_status(work: str):
    job = _EXPORT_JOBS.get(work) or {"state": "idle"}
    return {"ok": True, "state": job.get("state"), "error": job.get("error"),
            "name": job.get("name"), "size": job.get("size"), "files": job.get("files"),
            "secs": job.get("secs"), "include_raw": job.get("include_raw"),
            "log": (job.get("log") or [])[-8:], "export_dir": _export_dir()}


@app.get("/api/work/{work}/export/download")
def api_export_download(work: str, f: str = ""):
    """下载导出包（默认最近一次成功的；f= 指定导出目录里的文件名）。"""
    job = _EXPORT_JOBS.get(work) or {}
    path = job.get("path")
    if f:
        d = os.path.realpath(_export_dir())
        cand = os.path.realpath(os.path.join(d, os.path.basename(f)))
        if cand.startswith(d + os.sep) and os.path.exists(cand):
            path = cand
    if not path or not os.path.exists(path):
        raise HTTPException(404, "没有可下载的导出包")
    return FileResponse(path, filename=os.path.basename(path), media_type="application/vnd.rar")


def _prune_import_uploads(keep_days=2):
    """清掉过期上传包（temp/aa_pack/upload，导出/导入的中间目录不长期留）。"""
    d = os.path.join(_ROOT, "temp", "aa_pack", "upload")
    if not os.path.isdir(d):
        return
    cutoff = time.time() - keep_days * 86400
    for f in os.listdir(d):
        p = os.path.join(d, f)
        try:
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                os.remove(p)
        except OSError:
            pass


@app.post("/api/work-import")
async def api_work_import(request: Request):
    """上传作品包（原始 body + X-Filename，避开 python-multipart 依赖）→ 出导入预览。"""
    fn = urllib.parse.unquote(request.headers.get("x-filename") or "upload.rar")
    data = await request.body()
    if not data:
        raise HTTPException(422, "空请求体")
    if not fn.lower().endswith(".rar"):
        raise HTTPException(422, "只支持 .rar 作品包")
    _prune_import_uploads()
    updir = os.path.join(_ROOT, "temp", "aa_pack", "upload")
    os.makedirs(updir, exist_ok=True)
    token = time.strftime("%Y%m%d_%H%M%S") + "_" + str(os.getpid())
    path = os.path.join(updir, token + ".rar")
    with open(path, "wb") as f:
        f.write(data)
    try:
        m = work_pack.peek_manifest(path)
        prev = work_pack.inspect_import(m, path)
    except work_pack.PackError as e:
        try:
            os.remove(path)
        except OSError:
            pass
        raise HTTPException(400, str(e))
    _IMPORT_JOBS[token] = {"state": "ready", "ts": time.time(), "rar": path,
                           "manifest": m, "preview": prev, "log": []}
    return {"ok": True, "token": token, "preview": prev,
            "manifest": {k: m.get(k) for k in ("format", "exported_at", "work", "title",
                                               "root_rel", "dirs", "files", "bytes",
                                               "include_raw")}}


@app.post("/api/work-import/confirm")
def api_work_import_confirm(body: dict = None):
    """确认导入：解包 → 归位 → 注册 registry/work_meta → 热加载。"""
    token = (body or {}).get("token") or ""
    job = _IMPORT_JOBS.get(token)
    if not job:
        raise HTTPException(404, "导入会话不存在或已过期，请重新选择文件")
    if job.get("state") == "running":
        raise HTTPException(409, "导入进行中")
    w = job["manifest"].get("work")
    if w in registry.WORKS:
        raise HTTPException(409, f"作品 {w} 已注册，请先删除再导入")

    def _run():
        job["state"] = "running"
        try:
            res = work_pack.install(job["rar"],
                                    options={"registry": True, "work_meta": True},
                                    progress=lambda d: job["log"].append(d.get("msg") or ""))
            api_reload()                 # registry.py 已改 → 热加载
            _STATS_CACHE.pop(res["work"], None)
            job.update({"state": "done", "result": res})
        except Exception as e:
            job.update({"state": "error", "error": "%s" % e})

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "work": w}


@app.get("/api/work-import/status")
def api_work_import_status(token: str):
    job = _IMPORT_JOBS.get(token)
    if not job:
        raise HTTPException(404, "导入会话不存在或已过期")
    return {"ok": True, "state": job.get("state"), "error": job.get("error"),
            "log": (job.get("log") or [])[-8:], "result": job.get("result"),
            "preview": job.get("preview")}


@app.get("/api/search-local")
def api_search_local(q: str = "", scope: str = "title,chapter,content", lang: str = "all",
                     regex: int = 0):
    """本地正文/标题搜索（首页搜索框正文勾选时调）。跨作品逐部扫。

    2026-09-03: 每作品 try/except + isdir 防御（缺汉化目录不再 500）；
    regex=1 按正则匹配（非法正则早退）；scanned=已扫作品数（前端进度）。"""
    q = (q or "").strip()
    if not q:
        return {"hits": [], "total": 0, "scanned": 0}
    use_regex = int(regex or 0) == 1
    if use_regex:
        try:
            re.compile(q)
        except re.error as e:
            return {"hits": [], "total": 0, "scanned": 0, "error": f"正则错误: {e}"}
    scopes = set(s.strip() for s in scope.split(",") if s.strip())
    out = []
    _meta_cache = logic.load_work_meta(_WORK_META_FILE)
    scanned = 0
    for wid, cfg in registry.WORKS.items():
        out_dir = cfg.get("out_dir", "")
        aa_dir = cfg.get("aa_dir", "")
        hits = []
        try:
            if "content" in scopes:
                hits = logic.search_work(out_dir, aa_dir, q,
                                         name_style=cfg.get("name_style"), regex=use_regex)
        except Exception:
            hits = []
        # 标题命中
        title_hit = False
        jp = (cfg.get("title", wid) or "")
        cn = (_meta_cache.get(wid, {}).get("cn_name", "") or "")
        try:
            if "title" in scopes:
                if use_regex:
                    title_hit = bool(re.search(q, jp) or re.search(q, cn))
                else:
                    title_hit = (q in jp or q in cn)
        except re.error:
            title_hit = False
        if hits or title_hit:
            out.append({"work": wid, "title": jp,
                        "cn_name": cn,
                        "content_hits": hits[:20], "title_hit": title_hit,
                        "total": len(hits)})
        scanned += 1
    return {"hits": out, "total": len(out), "scanned": scanned}


@app.get("/api/search-chapters")
def api_search_chapters(q: str = "", regex: int = 0):
    """章节名搜索（首页搜索框章节名勾选时调）：返回含 q 的作品 id 列表。

    2026-09-03: regex=1 按正则匹配；scanned 供前端进度判断。"""
    q = (q or "").strip()
    if not q:
        return {"works": [], "scanned": 0}
    use_regex = int(regex or 0) == 1
    if use_regex:
        try:
            re.compile(q)
        except re.error as e:
            return {"works": [], "scanned": 0, "error": f"正则错误: {e}"}
    matched = []
    scanned = 0
    for wid, cfg in registry.WORKS.items():
        try:
            st = work_stats(wid)
        except HTTPException:
            continue
        for c in st["chapters"]:
            t = (c.get("title", "") or "")
            try:
                if (re.search(q, t) if use_regex else (q in t)):
                    matched.append(wid)
                    break
            except re.error:
                break
        scanned += 1
    return {"works": matched, "scanned": scanned}


# ---------- 划词翻译 + 朗读 TTS（2026-09-03，selection.py） ----------

class SelectionReq(BaseModel):
    text: str = ""
    work: str = ""      # 空 = 无作品上下文（board 页裸翻）
    base: str = ""      # 章节 base(entry_<eid>)，有则按章注入术语表/知识库
    force: bool = False  # true = 「重译」按钮：跳过照抄判定强制出结果 + 强化指令
    avoid: str = ""      # 上次通道名（force 重译时排到最后，避免同一模型给同样结果）


@app.post("/api/translate-selection")
def api_translate_selection(body: SelectionReq):
    """划词日→中：付费三通道(deepseek-flash → deepinfra → runinfra)；术语注入与整章翻译同口径。

    返回 {ok, text, via, elapsed, echo}——echo=true 表示模型把原文照抄回来了（前端显示黄色提示）。
    """
    from aa_web_app import selection as _SEL
    if not _SEL.sel_enabled():
        raise HTTPException(403, "划词翻译已在「设置」里关闭（AA_SEL_ENABLE=0）")
    try:
        res = _SEL.translate_text(body.text, work=body.work or None,
                                  base=body.base or None,
                                  force=bool(body.force), avoid=body.avoid or None)
        return {"ok": True, **res}
    except ValueError as e:
        raise HTTPException(422, str(e))
    except SystemExit as e:
        # 2026-09-08: 脚本库按设计 raise SystemExit(如术语表硬前置)逃逸会杀死 uvicorn 进程
        # (except Exception/Starlette 中间件都接不住 BaseException)——请求路径一律转 500 保命。
        raise HTTPException(500, f"内部终止: {e}")
    except Exception as e:
        raise HTTPException(502, f"翻译失败: {e}")


@app.post("/api/tts")
def api_tts(body: SelectionReq):
    """朗读原文（edge-tts ja-JP-NanamiNeural）。返回音频路径（FileResponse 由调用方决定）。"""
    from aa_web_app import selection as _SEL
    if not _SEL.sel_enabled():
        raise HTTPException(403, "朗读已在「设置」里关闭（AA_SEL_ENABLE=0）")
    try:
        path = _SEL.tts_path(body.text)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(502, str(e))
    return FileResponse(path, media_type="audio/mpeg",
                        filename=os.path.basename(path))


# ---------- 设置页（2026-10-08 阶段 6；方案 doc/方案归档/工作台设置页与配置文件方案_2026-10-08.md）----------
# 四区块（密钥/模型/划词/代理）各自保存，写 <仓库根>.env（唯一用户可改层），保存即生效。
# 纯逻辑在 aa_web_app/settings.py（不依赖 fastapi，可单测）。


class SettingsReq(BaseModel):
    section: str = ""                 # keys | models | selection | proxy
    set: dict = {}                    # 密钥：{KEY: 新值}（只提交改动项）
    clear: list = []                  # 密钥：显式清除的 key 名
    translate: list = []              # 模型：翻译线条目 id 列表（空 = 整条链）
    proofread: list = []              # 模型：校对线条目 id
    sel_chain: list = []              # 划词：划词链条目 id
    sel_enabled: bool = True          # 划词开关
    proxy_url: str = ""               # 代理地址（空 = 不改）
    proxy_mode: str = ""              # auto | 0 | 1（空 = 不改）
    clear_url: bool = False           # 删掉 .env 里的 AA_PROXY（回 TOML/默认）
    name: str = ""                    # test-key 用


def _settings_mod():
    from aa_web_app import settings as _S
    return _S


@app.get("/settings")
def settings_page(request: Request):
    return templates.TemplateResponse(request, "settings.html", {"active": "settings"})


@app.get("/api/settings")
def api_settings():
    """当前配置（密钥只回掩码 + 来源层，不回明文）。"""
    try:
        return _settings_mod().state()
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/settings")
def api_settings_save(body: SettingsReq):
    """按区块保存（只写改动项）。"""
    S = _settings_mod()
    try:
        if body.section == "keys":
            r = S.save_keys(body.set, body.clear)
        elif body.section == "models":
            r = S.save_models(translate=body.translate or [], proofread=body.proofread or [])
        elif body.section == "selection":
            S.save_selection(bool(body.sel_enabled))
            r = S.save_models(selection=body.sel_chain or [])
            r["sel_enabled"] = S.sel_enabled()
        elif body.section == "proxy":
            r = S.save_proxy(body.proxy_url, body.proxy_mode, clear_url=bool(body.clear_url))
        else:
            raise HTTPException(422, "未知区块: %s" % body.section)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))
    out = S.state()
    out["changed"] = r.get("changed", [])
    return out


@app.post("/api/settings/test-key")
def api_settings_test_key(body: SettingsReq):
    """「测试连接」：用该 key 发一次最小请求（零成本量级）。"""
    try:
        return _settings_mod().test_key(body.name)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/settings/test-proxy")
def api_settings_test_proxy():
    """「测试代理」：① 代理端口可连？② 不走代理/经代理能否到付费端点。"""
    try:
        return _settings_mod().test_proxy()
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------- 打开作品文件夹（2026-09-05） ----------

@app.post("/api/open-folder/{work}")
def api_open_folder(work: str):
    """在资源管理器中打开该作品根目录（aa/汉化/raw/work 的上级，含 override/glossary 等）。

    纯本地工具：服务端 os.startfile 弹出文件夹，前端无 file:// 限制问题。
    """
    cfg = cfg_of(work)
    root = os.path.dirname(cfg["aa_dir"])
    if not os.path.isdir(root):
        raise HTTPException(404, f"作品目录不存在: {root}")
    try:
        os.startfile(root)          # Windows 打开资源管理器
    except Exception as e:
        raise HTTPException(500, f"打开失败: {e}")
    return {"ok": True, "path": root}
