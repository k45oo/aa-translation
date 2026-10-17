# -*- coding: utf-8 -*-
"""阅读器窗口化：章节渲染上下文 + 区间行渲染（无 FastAPI 依赖，可独立单测）。

背景（2026-09-21 实测）：阅读器旧实现把整章 ×3 份 HTML 内联成 JSON 下发
（大章 12–22 MB、DOM 6–10 万节点），长读大单章时标签页内存与布局成本过高。
本模块提供「按区间取行 HTML」的服务端能力，配合前端窗口化渲染（reader.js），
让初始页面与常驻 DOM 与章节行数无关。

关键口径（必须与旧的 reader_page 内联渲染**逐字节一致**，有 A/B 测试锁定）：
- 行循环跳过 `#` / `=====` 开头的行，但保留原始 txt 行号 idx（data-idx = idx，行号 = idx+1）
- 楼间距 mt：帖头（HEADER_RE）后补足 2 空行 → `mt = (2 - blank_run) * 16`
- tr 模式：有汉化 → `render(idx, tr, cm, site, src, sfx_marks)`；无汉化 → `render(idx, src, cm, site)`
- src 模式：`render(idx, src, cm, site)`；refine 模式：`render(idx, refine, cm, site, src)`
- 空间行渲染返回空 → 落成单个空格（`or " "`）

索引约定：**k = 可渲染行序号（0-based）**，`ctx["vis"][k]` = 原始 txt 行 idx。
区间 API 与前端几何表都按 k 表达；txt 行号 ↔ k 由 `ctx` 的 skips 表换算。
"""
import importlib
import json
import gzip
import os
import sys
import time

_PKG = os.path.dirname(os.path.abspath(__file__))       # .../aa_web_app
_ROOT = os.path.dirname(_PKG)                            # 仓库根
_SCRIPTS = os.path.join(_ROOT, "scripts")
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "works")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from aa_web_app import logic                              # noqa: E402
from common import gen_aa_html as _gen                    # noqa: E402

KIND2TYPE = {"art": "aa", "text": "text", "mixed": "mixed", "header": "header", "blank": "blank"}
SKIP_PREFIX = ("#", "=====")
MODES = ("tr", "src", "refine")
BLOCK_MAX = 500          # 单次区间请求最多行数（约 0.2–0.3 MB 响应）
_CTX_TTL = 300.0         # 兜底 TTL（秒）；正常靠 (mtime,size) 指纹失效
_CTX_MAX = 2             # 上下文 LRU 上限（每份含全章行文本 + 每行元数据，约 5–10 MB）
_CTX_CACHE = {}          # (work, base) -> (built_at, fp, ctx)


def render_line(idx, line, cm, site, src_line=None, sfx_marks=None):
    """与 main.gen_aa_html_render 同口径的单行渲染。"""
    return _gen.render_line(idx, line, cm, site, src_line, sfx_marks)


# ---------------------------------------------------------------- 上下文构建

def _aux_paths(cfg, base):
    wd, aa, out = cfg["work_dir"], cfg["aa_dir"], cfg["out_dir"]
    return {
        "cm": os.path.join(aa, base + "_colormap.json"),
        "meta": logic.meta_path(os.path.join(wd, "line_meta_v2"), base),
        "sfx": os.path.join(wd, "sfx_marks", base + ".json"),
        "gold": os.path.join(wd, "gold_dialogue", base + ".json"),
        "overrides": os.path.join(wd, "line_meta_overrides", base + ".json"),
    }


def _load_json(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        if str(path).endswith(".gz"):
            with gzip.open(path, "rt", encoding="utf-8") as f:
                return json.load(f) or {}
        with open(path, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _load_gold(work_dir, base):
    """台词标注（gold）按章存：work/gold_dialogue.json 或 work/gold/<base>.json（与 logic.load_gold 同口径）。"""
    return logic.load_gold(work_dir, base)


def chapter_fp(cfg, base):
    """章节上下文指纹：正文与所有辅助文件的 (mtime, size)。

    任一文件被改写（保存行/替换/重生成/标注）→ 指纹变 → 缓存自动重建，
    不依赖「调用方记得清缓存」（Windows 目录 mtime 对内容修改不更新，不能用目录 mtime）。
    """
    paths = [os.path.join(cfg["aa_dir"], base + ".txt"),
             os.path.join(cfg["aa_dir"], base + ".txt.gz"),
             os.path.join(cfg["out_dir"], base + "_汉化.txt"),
             os.path.join(cfg["out_dir"], base + "_汉化.txt.gz"),
             os.path.join(os.path.dirname(cfg["out_dir"]), "精译", base + "_精译.txt")]
    paths += list(_aux_paths(cfg, base).values())
    parts = []
    for p in paths:
        try:
            st = os.stat(p)
            parts.append("%s:%d:%d" % (os.path.basename(p), int(st.st_mtime), st.st_size))
        except OSError:
            parts.append(os.path.basename(p) + ":-")
    return logic.stable_fp(parts)


def _build_ctx(cfg, work, fname):
    """构造章节渲染上下文（一次读盘 + 每行元数据；HTML 按需渲染）。"""
    aa_dir, out_dir, work_dir = cfg["aa_dir"], cfg["out_dir"], cfg["work_dir"]
    base = logic.txt_key(fname["src_file"])
    src_path = fname.get("src_path") or logic.txt_path(aa_dir, fname["src_file"])
    src_lines = logic.read_lines(src_path)
    has_tr = bool(fname.get("has_tr"))
    tr_path = fname.get("tr_path")
    tr_lines = logic.read_lines(tr_path) if (has_tr and tr_path) else []
    refine_path = os.path.join(os.path.dirname(out_dir), "精译", base + "_精译.txt")
    refine_lines = logic.read_lines(refine_path) if os.path.exists(refine_path) else []
    has_refine = bool(refine_lines)
    body = tr_lines if tr_lines else src_lines
    cm = _load_json(_aux_paths(cfg, base)["cm"])
    meta = _load_json(_aux_paths(cfg, base)["meta"])
    overrides = logic.load_overrides(work_dir, base)
    sfx_marks = logic.load_sfx_marks(work_dir, base)
    gold = _load_gold(work_dir, base)
    site = importlib.import_module("sites." + cfg["site"])

    vis, skips = [], []
    k_idx, k_mt, k_mtk, k_dlg, k_type, k_auto, k_ov, k_tip, k_gold = [], [], [], [], [], [], [], [], []
    blank_run, seen_header = 0, False
    for i, l in enumerate(body):
        if l.startswith(SKIP_PREFIX):
            skips.append(i)
            continue
        is_head = logic.HEADER_RE.match(l) is not None
        mt = 0
        if is_head:
            if seen_header and blank_run < 2:
                mt = (2 - blank_run) * 16
            seen_header = True
            blank_run = 0
        elif l.strip():
            blank_run = 0
        else:
            blank_run += 1
        src_l = src_lines[i] if i < len(src_lines) else ""
        ov_kind = overrides.get(str(i + 1))
        mv = meta.get(str(i + 1), {}) if meta else {}
        mk = mv.get("kind") if isinstance(mv, dict) else None
        atype = KIND2TYPE.get(mk) if mk else logic.line_type_of(src_l)
        wtype = KIND2TYPE.get(ov_kind) if ov_kind else atype
        tip = "第 %d 行 · 自动: %s" % (i + 1, mk or atype)
        if isinstance(mv, dict):
            if mv.get("confidence") is not None:
                tip += " · 置信 %s" % (mv["confidence"],)
            if mv.get("reason"):
                tip += " · %s" % (mv["reason"],)
        g = gold.get(str(i + 1)) if gold else None
        vis.append(i)
        k_idx.append(i)
        k_mt.append(mt)
        k_mtk.append(mt // 16)
        k_dlg.append(bool(g) if g is not None else (wtype in ("text", "mixed")))
        k_type.append(wtype)
        k_auto.append(atype)
        k_ov.append(ov_kind)
        k_tip.append(tip)
        k_gold.append(g)
    return {
        "work": work, "base": base, "cfg": cfg, "fname": fname,
        "src_file": fname["src_file"], "tr_file": fname.get("tr_file") or "",
        "src_lines": src_lines, "tr_lines": tr_lines, "refine_lines": refine_lines,
        "cm": cm, "meta": meta, "overrides": overrides, "sfx_marks": sfx_marks,
        "gold": gold, "site": site,
        "has_tr": bool(tr_lines), "has_refine": has_refine,
        "vis": vis, "skips": skips, "n": len(vis),
        "k_idx": k_idx, "k_mt": k_mt, "k_mtk": k_mtk, "k_dlg": k_dlg, "k_type": k_type,
        "k_auto": k_auto, "k_ov": k_ov, "k_tip": k_tip, "k_gold": k_gold,
        "fp": chapter_fp(cfg, base),
    }


def invalidate(work=None):
    """清上下文缓存（work=None 清全部）。写盘路径（保存行/标注/替换/重生成）后调用。"""
    if work is None:
        _CTX_CACHE.clear()
        return
    for key in [k for k in _CTX_CACHE if k[0] == work]:
        _CTX_CACHE.pop(key, None)


def load_ctx(cfg, work, fname):
    """取章节上下文（LRU + 指纹失效）。fname = merged_chapters 的章节条目。"""
    base = logic.txt_key(fname["src_file"])
    key = (work, base)
    ent = _CTX_CACHE.get(key)
    now = time.time()
    if ent is not None:
        built, fp, ctx = ent
        if now - built < _CTX_TTL and fp == chapter_fp(cfg, base):
            return ctx
    ctx = _build_ctx(cfg, work, fname)
    _CTX_CACHE[key] = (now, ctx["fp"], ctx)
    if len(_CTX_CACHE) > _CTX_MAX:
        oldest = min(_CTX_CACHE, key=lambda k: _CTX_CACHE[k][0])
        _CTX_CACHE.pop(oldest, None)
    return ctx


# ---------------------------------------------------------------- 输出

def effective_mode(ctx, mode):
    """模式兜底：无汉化 → tr 退化为 src；无精译 → refine 退化为 tr/src（与旧双渲染一致）。"""
    if mode == "refine":
        if ctx["has_refine"]:
            return "refine"
        mode = "tr" if ctx["has_tr"] else "src"
    if mode == "tr" and not ctx["has_tr"]:
        return "src"
    return mode


def line_html(ctx, k, mode):
    """第 k 个可渲染行的 HTML（口径与旧 reader_page 内联渲染逐字节一致）。"""
    idx = ctx["k_idx"][k]
    src = ctx["src_lines"][idx] if idx < len(ctx["src_lines"]) else ""
    if mode == "tr" and ctx["has_tr"]:
        tr = ctx["tr_lines"][idx] if idx < len(ctx["tr_lines"]) else ""
        return render_line(idx, tr, ctx["cm"], ctx["site"], src, ctx["sfx_marks"]) or " "
    if mode == "refine" and ctx["has_refine"]:
        ref = ctx["refine_lines"][idx] if idx < len(ctx["refine_lines"]) else ""
        return render_line(idx, ref, ctx["cm"], ctx["site"], src) or " "
    return render_line(idx, src, ctx["cm"], ctx["site"]) or " "


def _tip(ctx, k):
    """行号列 tooltip（默认行类型；人工 override 时附带）。"""
    tip = ctx["k_tip"][k]
    ov = ctx["k_ov"][k]
    if ov:
        tip += " · 人工: %s" % (ov,)
    return tip


def rows(ctx, mode, k0, k1):
    """区间行数据（k0/k1 含端点，自动夹到 [0, n-1]；返回 (实际模式, 行列表)）。"""
    m = effective_mode(ctx, mode)
    n = ctx["n"]
    if n <= 0:
        return m, []
    k0 = max(0, min(int(k0), n - 1))
    k1 = max(k0, min(int(k1), n - 1))
    if k1 - k0 + 1 > BLOCK_MAX:
        k1 = k0 + BLOCK_MAX - 1
    out = []
    for k in range(k0, k1 + 1):
        out.append({
            "k": k, "idx": ctx["k_idx"][k], "n": ctx["k_idx"][k] + 1,
            "mtk": ctx["k_mtk"][k], "html": line_html(ctx, k, m),
            "dlg": ctx["k_dlg"][k], "type": ctx["k_type"][k], "auto": ctx["k_auto"][k],
            "ov": ctx["k_ov"][k], "tip": _tip(ctx, k), "gold": ctx["k_gold"][k],
        })
    return m, out


def page_data(ctx, mode):
    """阅读页轻量数据（模板 win-data）：几何表 + 跳行映射，不含任何行 HTML。

    只发「与行数同阶但极窄」的数据：可渲染行数 n、跳过行表 skips、非零楼距 [[k, mtk]]。
    行 HTML 一律走区间 API（rows），所以页面体积与章节行数基本无关。
    """
    m = effective_mode(ctx, mode)
    mt = [[k, v] for k, v in enumerate(ctx["k_mtk"]) if v]
    return {"n": ctx["n"], "skips": ctx["skips"], "mt": mt,
            "hasTr": ctx["has_tr"], "hasRefine": ctx["has_refine"], "mode": m}


def k_of_line(ctx, line_no):
    """txt 行号（1-based）→ k；不可渲染（# / ===== 行）→ 其后第一个可渲染行的 k。"""
    idx = max(0, int(line_no) - 1)
    lo, hi = 0, ctx["n"]
    while lo < hi:
        mid = (lo + hi) // 2
        if ctx["k_idx"][mid] < idx:
            lo = mid + 1
        else:
            hi = mid
    return min(lo, max(0, ctx["n"] - 1))


def line_of_k(ctx, k):
    """k → txt 行号（1-based）。"""
    k = max(0, min(int(k), ctx["n"] - 1))
    return ctx["k_idx"][k] + 1 if ctx["n"] else 0
