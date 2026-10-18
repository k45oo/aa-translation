# -*- coding: utf-8 -*-
"""AA 汉化矫正工作台：纯逻辑层（无 FastAPI 依赖，可独立单测）。

职责：读/写汉化 txt、宽度/残留校验、备份轮转、术语表应用、glossary.json 读写。
路径全部由调用方传入（main.py 从 registry 构造），便于单测。
"""
import datetime
import gzip
import hashlib
import html
import json
import os
import re
import shutil
import sys
import time
from collections import Counter

_PKG = os.path.dirname(os.path.abspath(__file__))       # .../aa_web_app（与 scripts 同级）
_ROOT = os.path.dirname(_PKG)                            # 代码根（源码运行 = 仓库根；冻结 exe = 解包目录）
_SCRIPTS = os.path.join(_ROOT, "scripts")
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "works")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 数据根（2026-10-09 代码根/数据根分离，方案 doc/方案归档/发行包打包方案_2026-10-09.md §5）：
# 术语表/角色表/揭示板这类**可写数据**一律落数据根；`AA_BASE` 未设时 = 代码根（与从前逐字节相同）。
# 冻结 exe 里代码在被解包的临时目录，只有数据根是 exe 所在目录 → 两者必须分开。
try:
    import paths as _paths
    _DATA = _paths.BASE
except Exception:                        # 包不在 sys.path 上（极端情况）→ 退回代码根
    _DATA = _ROOT

from aa_common import disp_width, pad_to_width, residual_suspect, HEADER_RE, is_text_line, allow_overwidth, KANA_RE, txt_read, txt_write, txt_key, txt_existing, meta_path, meta_load, meta_list, list_txt, txt_bytes

_GLOSSARY_JSON = os.path.join(_DATA, "glossary.json")
BACKUP_KEEP = 10          # 每作品滚动保留的备份份数
EID_RE = re.compile(r"(?:entry_)?(\d+)")


def stable_fp(parts):
    """跨进程稳定的指纹（磁盘缓存用）。

    内置 `hash()` 受 PYTHONHASHSEED 影响——每个进程算出的值都不同，
    存进磁盘的指纹永远匹配不上新进程算的 → 磁盘缓存 100% 失效、每次冷启动全量重扫。
    一律用 md5（2026-09-14 统一：extract_author_cached / material_frequency_cached /
    material_chapters_cached 三处曾都用 hash()，缓存等于没有）。
    """
    return hashlib.md5("\x1f".join(str(p) for p in parts).encode("utf-8", "replace")).hexdigest()


_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
           "十": 10, "百": 100, "千": 1000, "〇": 0, "零": 0}


def _cn_to_int(s):
    """汉字数字 → int（第一→1、十五→15、百十→110 等；支持 0-9999 常见写法）。"""
    if not s:
        return None
    if s.isdigit():
        return int(s)
    total, cur = 0, 0
    for ch in s:
        if ch in "〇零":
            cur += 0
        elif ch in "一二三四五六七八九":
            cur += _CN_NUM[ch]
        elif ch in "十百千":
            if cur == 0:
                cur = 1
            total += cur * _CN_NUM[ch]
            cur = 0
        else:
            return None
    return total + cur


def chapter_no_from_title(first):
    """从章节标题首行提取话数：第N話（N 支持阿拉伯/全角/汉字数字）。"""
    m = re.search(r"第\s*([0-9０-９一二三四五六七八九十百千〇零]+)\s*[话話回]", first)
    if m:
        return _cn_to_int(m.group(1))
    return None
_SKIP_RE = re.compile(r"^(#|=====|\d{1,4}\s*：)")   # #/分隔线/帖头(NNN ： 名前 ： 日期 ID:) 均跳过

# AA 常用绘图字符（用于「文本 / 混合」判定：文本行内混入 ≥2 个即算混合）
# 注意：只收绘图字符，勿加 …※〓 等台词常用标点（「……」两句就是 2 个 → 误判混合）
ART_CHARS = set('│｜┃┌┐└┘├┤┬┴┼─━┏┓┗┛┣┫┳┻╋╂═╔╗╚╝║＼／〇◎●○◆◇■□▲△▼▽☆★∴∵')


def line_type_of(l):
    """弹窗行类型：blank 空行 / header 帖头 / text 纯文本 / mixed 文本+艺术混合 / aa 纯艺术。

    判定基于原文 SRC 行：帖头/空行优先；is_text_line 为真且混入 ≥2 个绘图字符 → mixed；
    含假名或非艺术汉字 → text；其余（纯艺术/标点）→ aa。"""
    if not l.strip():
        return "blank"
    if HEADER_RE.match(l):
        return "header"
    if is_text_line(l):
        art = sum(1 for c in l if c in ART_CHARS)
        return "mixed" if art >= 2 else "text"
    return "aa"


# 人工行分类 override：<work_dir>/line_meta_overrides/<base>.json。
# 稀疏 {1-based行号字符串: kind}，只存人工与自动不一致的行；只喂图片加大，翻译 flag 不动。
OVERRIDE_KINDS = ("text", "art", "mixed", "header", "blank")


def overrides_path(work_dir, base):
    """override 文件路径：<work_dir>/line_meta_overrides/<base>.json（base 与 line_meta_v2 同名）。"""
    return os.path.join(work_dir, "line_meta_overrides", base + ".json")


def load_overrides(work_dir, base):
    """读整章人工行分类 override：{1-based行号字符串: kind}。

    文件缺失/损坏 → {}；非五类 kind 的脏值过滤（防下游误判）。"""
    try:
        with open(overrides_path(work_dir, base), encoding="utf-8") as f:
            d = json.load(f)
        return {str(k): v for k, v in d.items() if v in OVERRIDE_KINDS}
    except (OSError, ValueError):
        return {}


def _write_overrides(work_dir, base, ov):
    p = overrides_path(work_dir, base)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(ov, f, ensure_ascii=False, indent=1)
        return True, ""
    except OSError as e:
        return False, str(e)


def save_override(work_dir, base, line_no, kind):
    """写/改单行 override（line_no 1-based）。kind 必须五类之一，非法拒绝。返回 (ok, err)。"""
    if kind not in OVERRIDE_KINDS:
        return False, f"非法 kind: {kind}（可选 {'/'.join(OVERRIDE_KINDS)}）"
    ov = load_overrides(work_dir, base)
    ov[str(line_no)] = kind
    return _write_overrides(work_dir, base, ov)


def delete_override(work_dir, base, line_no):
    """「还原为自动」：删除该行 override 记录（幂等：无记录也返回 ok）。"""
    ov = load_overrides(work_dir, base)
    ov.pop(str(line_no), None)
    return _write_overrides(work_dir, base, ov)

# ---------------------------------------------------------------------------
# 台词高亮 gold 标注（2026-08-29 第 2 步）：<work_dir>/gold_dialogue/<base>.json
# 行级二值 {1-based行号字符串: true/false}，true=是台词，false=不是台词（人工纠正覆盖自动）。
# 用途：精译/漏译扫描的 ground truth 回归集；文件缺失 → 视为无人工标注。
# ---------------------------------------------------------------------------
def gold_path(work_dir, base):
    return os.path.join(work_dir, "gold_dialogue", base + ".json")


def load_gold(work_dir, base):
    """读整章 golden 台词标注：{1-based行号字符串: bool}。文件缺失/损坏 → {}。"""
    try:
        with open(gold_path(work_dir, base), encoding="utf-8") as f:
            d = json.load(f)
        return {str(k): bool(v) for k, v in d.items() if isinstance(v, bool)}
    except (OSError, ValueError):
        return {}


def _write_gold(work_dir, base, gold):
    p = gold_path(work_dir, base)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(gold, f, ensure_ascii=False, indent=1)
        return True, ""
    except OSError as e:
        return False, str(e)


def save_gold(work_dir, base, line_no, is_dialogue):
    """写/改单行 golden 台词标注（line_no 1-based）。is_dialogue 必须 bool。返回 (ok, err)。"""
    if not isinstance(is_dialogue, bool):
        return False, f"is_dialogue 必须 bool，收到 {type(is_dialogue).__name__}"
    gold = load_gold(work_dir, base)
    gold[str(line_no)] = is_dialogue
    return _write_gold(work_dir, base, gold)


def delete_gold(work_dir, base, line_no):
    """删除该行 golden 标注（恢复为自动，幂等）。"""
    gold = load_gold(work_dir, base)
    gold.pop(str(line_no), None)
    return _write_gold(work_dir, base, gold)


# ---------------------------------------------------------------------------
# 人工 sfx 标记（2026-09-15）：<work_dir>/sfx_marks/<base>.json
# {1-based行号字符串: [[字符偏移, 文本], ...]} —— 阅读器划词翻译「替换」/「sfx 替换」写入，
# 渲染时该区间包 <span class="sfx">（与原文半角假名的自动 sfx 渲染叠加，见 gen_aa_html.sfx_spans）。
# 偏移是「写入时该行文本的字符下标」；行内后续增删导致偏移漂移时，渲染按最近的同文本出现处兜底。
# ---------------------------------------------------------------------------
def sfx_marks_path(work_dir, base):
    return os.path.join(work_dir, "sfx_marks", base + ".json")


def _clean_sfx_marks(d):
    """清洗标记 dict：值必须是 [[int, str], ...]；非法行/条目丢弃。"""
    out = {}
    if not isinstance(d, dict):
        return out
    for k, v in d.items():
        if not isinstance(v, list):
            continue
        rows = []
        for it in v:
            if isinstance(it, (list, tuple)) and len(it) >= 2 and str(it[1]):
                try:
                    rows.append([max(0, int(it[0])), str(it[1])])
                except (TypeError, ValueError):
                    continue
        if rows:
            out[str(k)] = rows
    return out


def load_sfx_marks(work_dir, base):
    """读整章 sfx 人工标记：{1-based行号字符串: [[偏移, 文本], ...]}。文件缺失/损坏 → {}。"""
    try:
        with open(sfx_marks_path(work_dir, base), encoding="utf-8") as f:
            return _clean_sfx_marks(json.load(f))
    except (OSError, ValueError):
        return {}


def _write_sfx_marks(work_dir, base, marks):
    p = sfx_marks_path(work_dir, base)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(marks, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
        return True, ""
    except OSError as e:
        return False, str(e)


def add_sfx_mark(work_dir, base, line_no, off, text):
    """给某行加 sfx 标记（line_no 1-based）。返回 (ok, err, action)。

    与既有标记**重叠**的区间先清掉（同一区间只留一条，防同处叠标记）；无文本直接忽略。
    """
    txt = str(text or "")
    if not txt:
        return True, "", "skip"
    try:
        off = max(0, int(off))
    except (TypeError, ValueError):
        off = 0
    marks = load_sfx_marks(work_dir, base)
    rows = [r for r in marks.get(str(line_no), [])
            if not (r[0] < off + len(txt) and off < r[0] + len(r[1]))]
    rows.append([off, txt])
    marks[str(line_no)] = rows
    ok, err = _write_sfx_marks(work_dir, base, marks)
    return ok, err, ("add" if ok else "fail")


def merge_sfx_marks(work_dir, base, marks_by_line):
    """批量并入 sfx 标记（line_no → [[off, text], ...]）→ (ok, err, added)。

    批量场景（⇄ 翻译替换里勾了 sfx 的替换对）逐条调 add_sfx_mark 会反复读写整章文件，
    这里合并成一次读写；去重规则与 add_sfx_mark 一致（同位置同文本不重复计入 added，
    重叠区间只留新的一条）。line_no 用 1-based（int/str 均可）。
    """
    if not marks_by_line:
        return True, "", 0
    marks = load_sfx_marks(work_dir, base)
    added = 0
    for k, rows in marks_by_line.items():
        try:
            ln = str(int(k))
        except (TypeError, ValueError):
            continue
        cur = list(marks.get(ln, []))
        for it in rows or []:
            if not isinstance(it, (list, tuple)) or len(it) < 2:
                continue
            try:
                off = max(0, int(it[0]))
            except (TypeError, ValueError):
                continue
            txt = str(it[1] or "")
            if not txt:
                continue
            if any(r[0] == off and r[1] == txt for r in cur):
                continue                      # 同位置同文本：已标过
            cur = [r for r in cur if not (r[0] < off + len(txt) and off < r[0] + len(r[1]))]
            cur.append([off, txt])
            added += 1
        if cur:
            marks[ln] = cur
    if not added:
        return True, "", 0
    ok, err = _write_sfx_marks(work_dir, base, marks)
    return ok, err, (added if ok else 0)


def clear_sfx_marks(work_dir, base, line_no, rng=None):
    """清 sfx 标记：line_no 1-based；rng=[off, length] 时只清与之重叠的（None=清该行全部）。

    返回 (ok, err, removed_count)。该行无标记 → 不写文件（removed 0）。
    """
    marks = load_sfx_marks(work_dir, base)
    rows = marks.get(str(line_no), [])
    if not rows:
        return True, "", 0
    if rng is None:
        kept, removed = [], len(rows)
    else:
        try:
            a, ln = int(rng[0]), int(rng[1])
        except (TypeError, ValueError, IndexError):
            return False, f"非法范围: {rng}", 0
        b = a + max(0, ln)
        kept = [r for r in rows if not (r[0] < b and a < r[0] + len(r[1]))]
        removed = len(rows) - len(kept)
    if not removed:
        return True, "", 0
    if kept:
        marks[str(line_no)] = kept
    else:
        marks.pop(str(line_no), None)
    ok, err = _write_sfx_marks(work_dir, base, marks)
    return ok, err, (removed if ok else 0)



def read_lines(path):
    """读 txt，按 \n 切分并剥掉行尾 \r（管线产物为 CRLF）。

    2026-09-05: 委托 aa_common.txt_read（透明支持 .txt.gz 压缩文本包）。
    """
    return txt_read(path)


def write_lines(path, lines):
    """写 txt。Windows 文本模式把 \n 转 CRLF，与管线产物字节风格一致。

    2026-09-05: 委托 aa_common.txt_write（path 以 .gz 结尾则 gzip 写）。
    """
    return txt_write(path, lines)


def src_name_of(tr_fname):
    """汉化文件名 → aa 原文文件名（entry_1_汉化.txt → entry_1.txt；.txt.gz 后缀保留）。"""
    return tr_fname.replace("_汉化.txt", ".txt")


def _txt_head(path, n=3):
    """读 .txt 或 .txt.gz 断言前 n 行（标题/原帖地址用）。空文件返回 []。"""
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return [l.rstrip("\r\n") for l in (x for _, x in zip(range(n), f))]
    with open(path, encoding="utf-8") as f:
        return [l.rstrip("\r\n") for l in (x for _, x in zip(range(n), f))]


def _is_txt(f):
    """文件名是 .txt 或 .txt.gz（正文文本，非 _汉化/_台词 后缀）。"""
    return (f.endswith(".txt") or f.endswith(".txt.gz")) and not f.endswith(("_汉化.txt", "_汉化.txt.gz", "_台词.txt", "_台词.txt.gz"))


def txt_path(base_dir, fname):
    """base_dir/fname 的实际存在路径：直接存在则返回；fname 为 .txt 基名且磁盘是 .txt.gz 则返回 .gz。皆无返回 base_dir/fname。"""
    p = os.path.join(base_dir, fname)
    if os.path.exists(p):
        return p
    if not fname.endswith(".gz"):
        gz = p + ".gz"
        if os.path.exists(gz):
            return gz
    return p


def is_skip_line(l):
    return bool(_SKIP_RE.match(l))


def chapter_order(out_dir, name_style=None):
    """章节文件列表，按 eid 数字排序（同 gen_aa_html.chapter_order）。

    name_style="eid_title"（さーきー bookshelf）时过滤 entry_* 残留
    （早期 FC2 文件混入汉化/ 不得当本作章节）。

    2026-08-20: 支持标题「第N話」优先排序——文件名 eid 数字与话数不一致的作品
    （jikan_teishi 第4话 eid=2012 < 第1话 eid=42170，按 eid 排会乱）。"""
    if not out_dir or not os.path.isdir(out_dir):
        return []                     # 未翻译作品没有 汉化/ 目录 → 空清单（曾致阅读页 500）
    files = [f for f in os.listdir(out_dir)
             if f.endswith("_汉化.txt") or f.endswith("_汉化.txt.gz")]
    if name_style == "eid_title":
        files = [f for f in files if not f.startswith("entry_")]

    # 标题话数缓存（读首行 # 标题；失败回落 eid 排序）
    _title_no_cache = {}

    def title_no(f):
        if f in _title_no_cache:
            return _title_no_cache[f]
        try:
            first = _txt_head(os.path.join(out_dir, f), 1)[0]
            _title_no_cache[f] = chapter_no_from_title(first)
        except Exception:
            _title_no_cache[f] = None
        return _title_no_cache[f]

    def key(f):
        tn = title_no(f)
        if tn is not None:
            return (0, tn, f)   # 有「第N話」标题 → 按话数排（优先）
        m = EID_RE.search(f)
        return (1, int(m.group(1)) if m else 99999, f)

    return sorted(files, key=key)


_MCH_CACHE = {}          # (aa_dir, out_dir, name_style, order_path) -> (fetched_at, mch)
_MCH_TTL = 300           # 与 work_stats 缓存一致
_MAT_FP_CACHE = {}       # (out_dir, tuple(names)) -> (fetched_at, fp)  素材指纹内存缓存


def invalidate_mch_cache():
    """清空 merged_chapters 内存缓存（保存行/重新生成后调用）。"""
    _MCH_CACHE.clear()


def merged_chapters(aa_dir, out_dir, name_style=None, order_path=None):
    """合并目录：以原文 aa/*.txt 为全集，标注每章是否有汉化。

    返回按话数/eid 排序的 [{eid, src_file, tr_file, has_src, has_tr,
    title, src_url}]。title 取汉化首行（已译）否则原文首行；src_url 从
    原文 # 原帖地址 行取，缺失时由调用方按 url_pattern 兜底。

    - 原文目录为全集（含未译章），汉化目录按同名 *_汉化.txt 对齐
    - name_style="eid_title" 时汉化里的 entry_* 残留不参与对齐
    - order_path: 可选 <work>/chapter_order.json（r401 原站顺序 arcid 列表），
      存在则按原站顺序排（番外/クリアリザルト位置不乱），否则按话数/eid。

    2026-08-30 优化: 内存 TTL 缓存（阅读页翻章/重复访问不再全量读章首行+排序）。
    key 含 aa/out 目录 mtime（文件增删/修改自动失效）；invalidate_mch_cache()
    在保存行/重新生成后显式清空。
    """
    _key = (aa_dir, out_dir, name_style, order_path)
    now = time.time()
    hit = _MCH_CACHE.get(_key)
    if hit and now - hit[0] < _MCH_TTL:
        # 目录 mtime 变了则失效（新增/删除章节文件）
        try:
            aa_m = os.path.getmtime(aa_dir)
            out_m = os.path.getmtime(out_dir) if out_dir and os.path.isdir(out_dir) else 0
        except OSError:
            aa_m = out_m = 0
        if (aa_m, out_m) == hit[1]:
            return hit[2]
    _order = None
    if order_path and os.path.exists(order_path):
        try:
            d = json.load(open(order_path, encoding="utf-8"))
            _order = {str(x): i for i, x in enumerate(d.get("order", []))}
        except Exception:
            _order = None
    _title_no_cache = {}

    def title_no(f):
        if f in _title_no_cache:
            return _title_no_cache[f]
        try:
            with open(f, encoding="utf-8") as fh:
                first = fh.readline()
            _title_no_cache[f] = chapter_no_from_title(first)
        except Exception:
            _title_no_cache[f] = None
        return _title_no_cache[f]

    out = []
    for f in sorted(os.listdir(aa_dir)):
        # 2026-08-25: 排除 _台词.txt(纯净台词) 和 _汉化.txt——只留正文 txt/.txt.gz
        if not _is_txt(f):
            continue
        # 规范名 = .txt（前端/URL/读者引用的名字）；存储名 = .txt.gz（磁盘形态）
        src_canon = txt_key(f) + ".txt"
        src_file = src_canon
        src_path = txt_path(aa_dir, src_canon)   # 解析到实际 .gz 或 .txt
        tr_canon = txt_key(f) + "_汉化.txt"
        tr_file = tr_canon
        tr_path = txt_existing(os.path.join(out_dir, tr_canon))
        has_tr = os.path.exists(tr_path)
        if name_style == "eid_title" and tr_file.startswith("entry_"):
            has_tr = False
        title = ""
        src_url = ""
        for p in (tr_path if has_tr else src_path,):
            try:
                for l in _txt_head(p, 3):
                    if l.startswith("# ") and not title:
                        title = l[2:].strip()
                    if l.startswith("# 原帖地址: "):
                        src_url = l.replace("# 原帖地址: ", "").strip()
            except OSError:
                pass
        m = EID_RE.search(f)
        out.append({"eid": m.group(1) if m else "",
                    "src_file": src_file, "tr_file": tr_file,
                    "has_src": True, "has_tr": has_tr,
                    "title": title, "src_url": src_url,
                    "src_path": src_path, "tr_path": tr_path})

    if _order is not None:
        # 原站顺序（r401 分类页 arcid 顺序）：未在 order 里的章排最后（按 eid）
        def key(ch):
            pos = _order.get(ch["eid"])
            if pos is not None:
                return (0, pos)
            m = EID_RE.search(ch["src_file"])
            return (1, int(m.group(1)) if m else 99999)
    else:
        def key(ch):
            tn = title_no(ch["src_path"])
            if tn is not None:
                return (0, tn, ch["src_file"])
            m = EID_RE.search(ch["src_file"])
            return (1, int(m.group(1)) if m else 99999, ch["src_file"])

    result = sorted(out, key=key)
    # 写缓存（目录 mtime 作失效指纹）
    try:
        aa_m = os.path.getmtime(aa_dir)
        out_m = os.path.getmtime(out_dir) if out_dir and os.path.isdir(out_dir) else 0
    except OSError:
        aa_m = out_m = 0
    _MCH_CACHE[_key] = (now, (aa_m, out_m), result)
    return result


def chapter_stats(aa_path, tr_path, with_details=False, work_names=None):
    """仿 scan_width.py 的逐行宽度校验 + 行数 + 疑似残留统计。

    宽度：汉化 vs 原文逐行 disp_width 比对（跳过 # / ===== 行）。
    work_names：作品专属白名单（并入残留校验）。
    残留：residual_suspect（白名单制），排除帖头行（HEADER_RE）。
    """
    src = read_lines(aa_path)
    tr = read_lines(tr_path)
    st = {"src_lines": len(src), "tr_lines": len(tr),
          "line_count_ok": len(src) == len(tr),
          "mismatches": 0, "short": 0, "long": 0, "residual": 0,
          "detail": []}
    for i, (a, b) in enumerate(zip(src, tr)):
        if a.startswith(("#", "=====")):   # 标记行不参与（tuple startswith 比正则快）
            continue
        if a != b:                          # TR==SRC 的艺术行宽度必然一致，跳过 findall
            wa, wb = disp_width(a), disp_width(b)
            if wa != wb:
                st["mismatches"] += 1
                if wb < wa:
                    st["short"] += 1
                else:
                    st["long"] += 1
                if with_details and len(st["detail"]) < 50:
                    st["detail"].append({"idx": i, "src_w": wa, "tr_w": wb,
                                         "src": a[:100], "tr": b[:100]})
        if residual_suspect(b, work_names) and not HEADER_RE.match(a):
            st["residual"] += 1
    return st


def backup_files(paths, backup_root):
    """把文件复制到 backup_root/<ts>/ 并轮转（保留 BACKUP_KEEP 份）。返回 ts。"""
    ts = time.strftime("%Y%m%d%H%M%S")
    while os.path.exists(os.path.join(backup_root, ts)):
        time.sleep(1.0)
        ts = time.strftime("%Y%m%d%H%M%S")
    dest_dir = os.path.join(backup_root, ts)
    os.makedirs(dest_dir, exist_ok=True)
    for p in paths:
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(dest_dir, os.path.basename(p)))
    rotate_backups(backup_root)
    return ts


def rotate_backups(backup_root):
    """只保留最近 BACKUP_KEEP 份备份目录。"""
    if not os.path.isdir(backup_root):
        return
    dirs = sorted(d for d in os.listdir(backup_root)
                  if os.path.isdir(os.path.join(backup_root, d)))
    for d in dirs[:-BACKUP_KEEP]:
        shutil.rmtree(os.path.join(backup_root, d), ignore_errors=True)


def count_unsynced_edits(work_dir, html_mtime):
    """未同步到交付 HTML 的人工修改条数。

    edits.jsonl 每行含 ts（%Y-%m-%d %H:%M:%S）；条数 = ts 不早于交付 HTML
    生成秒的条目（>= 保守方向：同一秒内修改一律视为未同步，宁多报不漏报，
    重新生成后自然归零）。HTML 未生成时全部计为未同步。
    """
    log_path = os.path.join(work_dir, "edits.jsonl")
    if not os.path.exists(log_path):
        return 0
    if not html_mtime:
        with open(log_path, encoding="utf-8") as f:
            return sum(1 for l in f if l.strip())
    thresh = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(html_mtime))
    cnt = 0
    with open(log_path, encoding="utf-8") as f:
        for l in f:
            l = l.strip()
            if not l:
                continue
            try:
                if json.loads(l).get("ts", "") >= thresh:
                    cnt += 1
            except Exception:
                pass
    return cnt


def list_backups(backup_root):
    if not os.path.isdir(backup_root):
        return []
    out = []
    for d in sorted(os.listdir(backup_root)):
        dp = os.path.join(backup_root, d)
        if not os.path.isdir(dp):
            continue
        files = sorted(f for f in os.listdir(dp)
                       if os.path.isfile(os.path.join(dp, f)))
        size = sum(os.path.getsize(os.path.join(dp, f)) for f in files)
        out.append({"ts": d, "files": files, "size": size})
    return out


def restore_backup(backup_root, ts, dest_dir):
    """把备份 ts 下的全部文件复制回 dest_dir。返回恢复的文件名列表。"""
    dp = os.path.join(backup_root, ts)
    if not os.path.isdir(dp):
        raise ValueError(f"备份不存在: {ts}")
    os.makedirs(dest_dir, exist_ok=True)
    restored = []
    for f in os.listdir(dp):
        p = os.path.join(dp, f)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(dest_dir, f))
            restored.append(f)
    return restored


# ---------- 行编辑类型自动分类（2026-10-01，口径由用户逐条确认） ----------
# 类型取值：错译 / 漏译 / 错位 / 校对（校对 = 中文换中文，译文本身有错）。
# 人工显式选择永远优先；未指定（空串）时由 classify_edit 判定（工作台 radio 的「自动」项）。
MANUAL_EDIT_TYPES = ("错译", "漏译", "错位", "校对")
AUTO_EDIT_DEFAULT = "错位"                     # 无法判定（art 行）时沿用旧默认值
_WS_RE = re.compile("[\u3000 \t\u00a0]+")      # 全角空格/半角空格/制表/不换行空格


def _strip_ws(s):
    """去掉全部空白字符（错位判据与日文度的比较基准都基于它）。"""
    return _WS_RE.sub("", s or "")


def jp_degree(line, src_line):
    """行的「日文度」= (假名数, 与原文去空白后的字符多重集交集数)。

    假名数是主信号（KANA_RE 与残留检测同口径：平假名/片假名/半角片假名）；
    与原文重合度是补充信号，专治「纯汉字日文」盲区——照抄原文的行（如 `白頭`）
    假名数为 0，但汉字重合度高；改写成中文（`白头`）后重合度下降 → 判成漏译而非校对。
    """
    t = _strip_ws(line)
    a, b = Counter(t), Counter(_strip_ws(src_line))
    return (len(KANA_RE.findall(t)), sum(min(a[c], b[c]) for c in a))


def classify_edit(src_line, old_line, new_line, kind=None):
    """行编辑类型自动分类（纯函数、零 I/O）。

    返回 "错位" / "校对" / "漏译" / "错译"；kind == "aa"（纯图形行）→ None
    （art 行没有中日文语义，调用方沿用 AUTO_EDIT_DEFAULT）。

    判据（顺序即优先级；一律用**提交值**对比盘上旧值，pad 补位不参与）：
      ① 两行去掉全部空白后完全相同 → 错位（只可能是空白的位置/数量变了）
      ② 两行都无假名 且 与原文的重合数**不变** → 校对（中文换中文）
      ③ 新日文度 < 旧日文度（日文/原文残留被中文吃掉）→ 漏译（补译）
      ④ 其余（日文度上升，或不变但文本确实变了）→ 错译（中文还原成日文/混合行重写）
    走到 ④ 必是文本改动（纯空白差异已被 ① 吃掉）。
    ② 的"重合数不变"是必需的护栏：只看"两行都无假名"会把「白頭→白头」（照抄的纯汉字
    日文被翻成中文，重合数 2→1 下降）误判成校对——那是漏译；而重合数上升
    （如「武士→勇者」把原文汉字搬回来）按 ④ 落错译。
    真实数据校准（12340 条 edits.jsonl，只取标注可信的 漏译/错译 且去空白后确有差异的
    9159 条）：漏译 6052/6060、错译 3016/3099（99% / 97%）。历史里的 错位 大量是前端
    radio 默认值（存在整句重写却标错位的行），质量类标注（误译/生硬…）是无内容的空记录，
    故二者都不参与校准。
    """
    if kind == "aa":
        return None
    if _strip_ws(old_line) == _strip_ws(new_line):
        return "错位"
    d_old = jp_degree(old_line, src_line)
    d_new = jp_degree(new_line, src_line)
    if d_old[0] == 0 and d_new[0] == 0 and d_old[1] == d_new[1]:
        return "校对"
    if d_new < d_old:
        return "漏译"
    return "错译"


def save_line(aa_path, tr_path, idx, text, work_names=None):
    """保存单行（idx 为 0-based，对应 txt 行号-1）。

    宽度约束（非跳过行）：短则自动 pad_to_width 到 SRC 宽度；超宽拒绝。
    行数不变（只改内容）。返回 dict(result=ok|unchanged|error, ...)。
    work_names：作品专属白名单（残留校验并入）。

    2026-10-01: 不再整文件备份到 work/backup/<ts>/（备份目录/文件过多）——
    改前/改后由调用方逐行记进单文件 work/edits.jsonl（main._log_edit）。
    """
    if "\n" in text or "\r" in text:
        return {"result": "error", "message": "一行只能保存单行内容"}
    src = read_lines(aa_path) if os.path.exists(aa_path) else None
    tr = read_lines(tr_path)
    if not 0 <= idx < len(tr):
        return {"result": "error", "message": f"行号越界: {idx + 1}（文件共 {len(tr)} 行）"}
    old = tr[idx]
    if text == old:
        return {"result": "unchanged", "idx": idx}
    src_line = src[idx] if src and idx < len(src) else ""
    skip = is_skip_line(src_line) if src else True
    padded = False
    if not skip:
        sw = disp_width(src_line)
        tw = disp_width(text)
        if tw > sw and not allow_overwidth(src_line):
            # 超宽: 仅 art 行(纯图形)拒绝——超宽会错位破坏图形;
            # text/mixed/header 行允许(2026-08-25 用户决策: 译文比日文长正常)
            return {"result": "error",
                    "message": f"超宽无法自动修正：TR {tw} 格 > SRC {sw} 格（第 {idx + 1} 行，图形行不可超宽）"}
        if tw < sw:
            text = pad_to_width(text, sw)
            padded = True
    tr[idx] = text
    write_lines(tr_path, tr)
    residual = (residual_suspect(text, work_names)
                if not skip and not HEADER_RE.match(src_line) else [])
    return {"result": "ok", "idx": idx, "padded": padded, "text": text,
            "src_width": disp_width(src_line) if not skip else None,
            "tr_width": disp_width(text), "residual": residual}


def _work_dir_of(work):
    """从 registry 解析作品 work_dir；未注册/异常返回 None（回退根目录）。"""
    try:
        for _p in (os.path.join(_SCRIPTS, "works"),):
            if _p not in sys.path:
                sys.path.insert(0, _p)
        import registry as _R
        return (_R.get(work).get("work_dir") or "").strip() or None
    except BaseException:
        return None


def work_glossary_path(work):
    """作品专属术语表文件：<work_dir>/glossary_<work>.json（随作品入库，与 glossary.json 同约定）。

    未注册/无法解析的作品回退项目根 glossary_<work>.json（兼容单测）。"""
    wd = _work_dir_of(work)
    if wd:
        return os.path.join(wd, f"glossary_{work}.json")
    return os.path.join(os.path.dirname(_GLOSSARY_JSON), f"glossary_{work}.json")


def load_work_glossary(work, path=None):
    """作品专属术语表（替换对 + 白名单）。文件缺失/损坏时返回空结构。

    2026-09-01: 兼容 v1 二元组与 v2 对象({jp,zh,cat,来源})。
    """
    path = path or work_glossary_path(work)
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        gl = []
        for p in d.get("glossary", []):
            if isinstance(p, list) and len(p) == 2:
                gl.append({"jp": p[0], "zh": p[1], "cat": "专名", "来源": "manual"})
            elif isinstance(p, dict) and p.get("jp") and p.get("zh"):
                cat = p.get("cat", "")
                if cat not in _CAT3:  # 2026-09-06 旧分类读侧归一
                    cat = "角色" if "角色" in cat else ("正则" if "正则" in cat else "专名")
                gl.append({"jp": p["jp"], "zh": p["zh"], "cat": cat,
                           "来源": p.get("来源", "manual"),
                           "role_cn": p.get("role_cn"), "role_works": p.get("role_works")})
        return {"glossary": gl, "known_names": list(d.get("known_names", []))}
    except (OSError, ValueError):
        return {"glossary": [], "known_names": []}


_CAT3 = {"角色", "专名", "正则"}


def _norm_pair_obj(p):
    """入参一条术语对 → v2 对象（{jp,zh,cat,来源,role_cn,role_works}）。

    兼容旧二元组；jp/zh 任一为空视为非法返回 None。
    2026-09-06 分类重构: 作品表 cat 三类(角色/专名/正则); 旧自由分类/空值归"专名"。
    """
    if isinstance(p, dict) and p.get("jp") and p.get("zh"):
        cat = p.get("cat", "")
        if cat not in _CAT3:
            if "角色" in cat:
                cat = "角色"
            elif "正则" in cat:
                cat = "正则"
            else:
                cat = "专名"
        return {"jp": p["jp"], "zh": p["zh"], "cat": cat,
                "来源": p.get("来源", "manual"),
                "role_cn": p.get("role_cn"), "role_works": p.get("role_works")}
    if isinstance(p, (list, tuple)) and len(p) == 2 and all(isinstance(x, str) for x in p):
        return {"jp": p[0], "zh": p[1], "cat": "专名", "来源": "manual"}
    return None


def save_work_glossary(work, pairs, known_names, path=None):
    """保存作品专属术语表：紧凑 JSON、UTF-8 无 BOM、自动更新日期。

    2026-09-01: 支持对象格式 {jp,zh,cat,来源} 与旧二元组。
    来源缺省 manual(人工编辑)。防呆：非法条目跳过。返回 (ok, err)。
    2026-09-19: 本函数是**整表覆盖**语义（唯一合法调用方=merge_work_glossary 与
    生成器/单条追加）——网页端「保存」按钮不再直接调它（见 merge_work_glossary）。
    """
    path = path or work_glossary_path(work)
    clean_pairs, clean_names = [], []
    for p in pairs:
        o = _norm_pair_obj(p)
        if o:
            clean_pairs.append(o)
    for n in known_names:
        if isinstance(n, str) and n.strip():
            clean_names.append(n.strip())
    data = {"schema": "AA翻译作品术语表 v2",
            "更新日期": time.strftime("%Y-%m-%d"),
            "glossary": clean_pairs,
            "known_names": clean_names}
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            f.write("\n")
        return True, ""
    except OSError as e:
        return False, str(e)


def merge_work_glossary(work, pairs=None, known_names=None, deleted_pairs=None,
                        deleted_names=None, path=None):
    """增量保存作品术语表（2026-09-19）：传入条目 upsert + 显式删除，其余既有条目保留。

    语义:
      - pairs 里的 jp 已存在 → 更新 zh/cat（来源/role_cn/role_works 缺省保留旧值），
        不存在 → 追加；
      - deleted_pairs 里的 jp 才删除（前端点「删」的行）；
      - known_names 取并集，deleted_names 里的删除；
      - **文件里未被提及的条目原样保留**（含自动派生的 auto 条目）。

    动机（チンポ_メガテン 事故）: 旧「保存」= 整表覆盖，工作台页面没加载就点保存
    （或旧标签页拿着旧状态保存）会用空表把文件写空 → 整部作品的人工术语瞬间清零。
    增量语义下同一次误点最多是 no-op，删条目必须显式点「删」。

    返回 (ok, err, stat)；stat = {added, updated, kept, removed, names_added,
    names_removed, total, names_total}。
    """
    path = path or work_glossary_path(work)
    cur = load_work_glossary(work, path)
    entries = list(cur["glossary"])
    by_jp = {}
    order = []
    for e in entries:
        if e.get("jp") and e["jp"] not in by_jp:
            by_jp[e["jp"]] = e
            order.append(e["jp"])
    added = updated = kept = 0
    for p in (pairs or []):
        o = _norm_pair_obj(p)
        if not o:
            continue
        old = by_jp.get(o["jp"])
        if old is None:
            by_jp[o["jp"]] = o
            order.append(o["jp"])
            added += 1
            continue
        if o["zh"] != old.get("zh") or o["cat"] != old.get("cat"):
            updated += 1
        else:
            kept += 1
        # 表格不编辑 role_cn/role_works：传空视为"未提供"，沿用旧值
        o["role_cn"] = o.get("role_cn") or old.get("role_cn")
        o["role_works"] = o.get("role_works") or old.get("role_works")
        by_jp[o["jp"]] = o
    removed = 0
    for jp in (deleted_pairs or []):
        if isinstance(jp, str) and jp in by_jp:
            del by_jp[jp]
            order.remove(jp)
            removed += 1
    out = [by_jp[jp] for jp in order]
    del_names = {x for x in (deleted_names or []) if isinstance(x, str) and x}
    names = [n for n in cur["known_names"] if n not in del_names]
    names_removed = len(cur["known_names"]) - len(names)
    names_added = 0
    for n in (known_names or []):
        if isinstance(n, str) and n.strip() and n.strip() not in names:
            names.append(n.strip())
            names_added += 1
    ok, err = save_work_glossary(work, out, names, path=path)
    stat = {"added": added, "updated": updated, "kept": kept, "removed": removed,
            "names_added": names_added, "names_removed": names_removed,
            "total": len(out), "names_total": len(names)}
    return ok, err, stat


def add_work_glossary_pair(work, jp, zh, cat="专名", path=None):
    """把**一条** (jp, zh) 追加进作品术语表——阅读页「翻译替换 → 历史替换记录 → ＋术语表」用。

    2026-09-14：历史替换记录里的 find→repl 本质就是译名对，手动一条条转成术语表条目，
    比事后去术语表页重新输一遍省事。**只手动触发**，不自动同步。
    - 不覆盖已有同 jp 条目（术语表是翻译硬资产，静默改写会污染后续 prompt）：
      同 jp 同 zh → reason="exists"；同 jp 不同 zh → reason="conflict"（把现有 zh 回给前端提示）。
    - 空 jp/zh 不写（repl 为空表示「删除该串」，不是译名）→ reason="invalid"。
    - cat 归一三类（角色/专名/正则），非法值落「专名」；来源记 manual。

    返回 {"ok", "added", "reason", "current_zh", "error", "total"}。
    """
    jp = jp if isinstance(jp, str) else ""
    zh = zh if isinstance(zh, str) else ""
    if not jp.strip() or not zh.strip():
        return {"ok": False, "added": False, "reason": "invalid", "current_zh": "",
                "error": "", "total": 0}
    cat = cat if cat in _CAT3 else "专名"
    d = load_work_glossary(work, path)
    gl = d["glossary"]
    for e in gl:
        if e.get("jp") == jp:
            same = (e.get("zh") or "") == zh
            return {"ok": True, "added": False, "reason": "exists" if same else "conflict",
                    "current_zh": e.get("zh") or "", "error": "", "total": len(gl)}
    gl.append({"jp": jp, "zh": zh, "cat": cat, "来源": "manual"})
    ok, err = save_work_glossary(work, gl, d.get("known_names") or [], path=path)
    return {"ok": bool(ok), "added": bool(ok), "reason": "added" if ok else "error",
            "current_zh": "", "error": err or "", "total": len(gl)}


def _norm_pairs(entries):
    """术语条目归一化 → [(jp, zh)]。兼容 v2 对象({jp,zh,...}) 与 v1 二元组。

    2026-09-01: 作品术语表升级 v2 对象后, load_work_glossary 返回对象,
    preview/apply 需归一化成二元组(apply_glossary_chapter 内部 for jp, zh in pairs)。
    """
    out = []
    for e in entries:
        if isinstance(e, dict) and e.get("jp") and e.get("zh"):
            out.append((e["jp"], e["zh"]))
        elif isinstance(e, (list, tuple)) and len(e) == 2:
            out.append((e[0], e[1]))
    return out


def _shift_off(off, edits):
    """旧行字符偏移 → 新行偏移（edits = [(pos, old_len, new_len)] 升序，一次替换的全部改动）。"""
    d = 0
    for pos, olen, nlen in edits:
        if pos + olen <= off:
            d += nlen - olen
        elif pos >= off:
            break
        else:
            return pos + d          # 偏移落进被替换段内 → 夹到替换起点
    return off + d


def _apply_pair(line, jp, zh, want_marks):
    """在一行上应用一对替换 → (新行, marks, edits)。语义与旧实现逐字等价。

    marks = [(新行里的字符偏移, 插入文本), ...]（want_marks=False → 空列表）；
    edits = [(旧行里的位置, 被替换长度, 新文本长度), ...] 升序（供已有标记平移）。
    `re:` 前缀走正则（zh 可含 \\1 反向引用）、否则整串替换——两条路径都全部命中。
    """
    if jp.startswith("re:"):
        try:
            rx = re.compile(jp[3:])
        except re.error:
            return line, [], []
        out, marks, edits, last, pos = [], [], [], 0, 0
        try:
            for m in rx.finditer(line):
                seg = line[last:m.start()]
                out.append(seg)
                pos += len(seg)
                rep = m.expand(zh)
                out.append(rep)
                if rep:
                    marks.append((pos, rep))
                edits.append((m.start(), m.end() - m.start(), len(rep)))
                pos += len(rep)
                last = m.end()
        except re.error as e:
            # 替换串反向引用写错（例：只有一个括号却写 \2）→ 该对按「不命中」处理。
            # 旧行为是 m.expand 抛 re.error 一路冒到路由 → 预览/应用整个 500
            # （弹窗里现在推荐用户写 \1，写错时不该炸整批）。提示留 workbench.log + 预览 0 命中。
            print("[logic] 替换对正则错误（%s）：%s → %s（该对按不命中处理）" % (e, jp, zh), flush=True)
            return line, [], []
        if not edits:
            return line, [], []
        out.append(line[last:])
        return "".join(out), (marks if want_marks else []), edits
    if jp not in line:
        return line, [], []
    out, marks, edits, i, pos = [], [], [], 0, 0
    while True:
        k = line.find(jp, i)
        if k < 0:
            out.append(line[i:])
            break
        seg = line[i:k]
        out.append(seg)
        pos += len(seg)
        out.append(zh)
        if zh:
            marks.append((pos, zh))
        edits.append((k, len(jp), len(zh)))
        pos += len(zh)
        i = k + len(jp)
    return "".join(out), (marks if want_marks else []), edits


def apply_glossary_chapter(aa_path, tr_path, pairs, dry_run=True,
                           skip_idx=(), sfx_pairs=(), sfx_out=None):
    """对一章应用术语替换（跳过 # / ===== 行），宽度按 SRC 校正。

    返回 (changed, skipped)；changed = [(idx, before, after)]，
    skipped = [(idx, before, reason)]（替换后超宽等，需人工处理）。

    2026-09-01: 超宽不跳过——替换照样生效, 超宽行进 changed 且元素带第4字段
    (overwide=True) 供前端显示警告; skipped 仅保留无法处理的行。
    2026-09-09: skip_idx = 0-based 行号集合，整行跳过（翻译替换的 AA 艺术行
    保护——不参与替换、不计入 changed/skipped）。默认空元组，行为不变。
    2026-10-01: 不再整文件备份到 work/backup/<ts>/（备份目录/文件过多）——
    改前/改后由调用方逐行记进单文件 work/edits.jsonl（main._log_edits_bulk）。
    2026-09-21: sfx_pairs = 「查找」串集合，命中的对替换后**返回值里带 sfx 标记**——
    标记写进调用方给的 sfx_out 字典 {0-based行号: [[偏移, 插入文本], ...]}
    （偏移 = 替换后该行字符下标；顺序扫描 + 逐步平移，跨多对替换也准）。不改返回形状。
    pairs 兼容 (jp, zh) 与 (jp, zh, sfx标记) 两种写法（三元组的第 3 位由调用方转成 sfx_pairs）。
    """
    pairs = [(p[0], p[1]) for p in pairs]
    src = read_lines(aa_path) if os.path.exists(aa_path) else None
    tr = read_lines(tr_path)
    changed, skipped = [], []
    for i, b in enumerate(tr):
        if is_skip_line(b):
            continue
        if skip_idx and i in skip_idx:
            continue
        nb, marks = b, []
        for jp, zh in pairs:
            nb2, m2, edits = _apply_pair(nb, jp, zh, jp in sfx_pairs)
            if nb2 == nb:
                continue
            if marks and edits:
                marks = [(_shift_off(o, edits), t) for o, t in marks]
            if m2:
                marks.extend(m2)
            nb = nb2
        if nb == b:
            continue
        sw = disp_width(src[i]) if src and i < len(src) else disp_width(b)
        nw = disp_width(nb)
        overwide = nw > sw
        if marks and sfx_out is not None:
            sfx_out[i] = [[o, t] for o, t in marks]
        if overwide:
            # 2026-09-01: 超宽不跳过——替换生效, 带 overwide 标记(前端显示警告)
            changed.append((i, b, nb, True))
            continue
        if nw < sw:
            nb = pad_to_width(nb, sw)
        changed.append((i, b, nb))
    if changed and not dry_run:
        for i, _, nb, *_ in changed:
            tr[i] = nb
        write_lines(tr_path, tr)
    return changed, skipped


# ---------- 翻译替换历史记录（2026-09-13）：<work_dir>/tr_replace_history.json ----------
# 记「查找 → 替换为」成功应用过的对，供阅读器翻译替换弹窗复用（可手动勾选启用）。
# 结构：[{find, repl, ts, n, applied, files, pin}, ...] 新在前、**置顶段在最前**；同对再去重（n 计数、ts 更新）。
# 2026-10-13: pin = 手动置顶（True/False），置顶条目排最前；TR_HIST_MAX 只约束未置顶部分。
TR_HIST_NAME = "tr_replace_history.json"
TR_HIST_MAX = 100


def tr_hist_path(work_dir):
    return os.path.join(work_dir, TR_HIST_NAME)


def tr_hist_pinned_first(rows):
    """置顶条目排最前（稳定排序 → 两组内部仍保持「新在前」的相对次序）。"""
    return sorted(rows, key=lambda it: 0 if it.get("pin") else 1)


def tr_hist_trim(rows):
    """按上限截断：TR_HIST_MAX 只约束**未置顶**部分，置顶条目额外保留、不被挤掉。

    置顶是手动钉的（数量由用户自己控制），不占「最多 N 条」的名额——
    否则整表都是置顶时新记录会在写入瞬间被丢掉。
    """
    out, kept = [], 0
    for it in rows:
        if it.get("pin"):
            out.append(it)
        elif kept < TR_HIST_MAX:
            out.append(it)
            kept += 1
    return out


def tr_hist_load(work_dir):
    """读历史记录（文件缺失/损坏 → []；只保留结构合法的条目）。

    2026-10-13: 输出已按「置顶在前」排好（pin 缺失的旧文件当未置顶，行为与从前一致）。
    """
    p = tr_hist_path(work_dir)
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for it in data:
        if not isinstance(it, dict):
            continue
        find = it.get("find")
        if not isinstance(find, str) or not find:
            continue
        repl = it.get("repl")
        out.append({"find": find, "repl": repl if isinstance(repl, str) else "",
                    "ts": it.get("ts") or "", "n": int(it.get("n") or 1),
                    "applied": int(it.get("applied") or 0), "files": int(it.get("files") or 0),
                    "pin": bool(it.get("pin"))})
    return tr_hist_pinned_first(out)


def tr_hist_save(work_dir, rows):
    """原子写历史记录（临时文件 + os.replace，防半写损坏）。"""
    p = tr_hist_path(work_dir)
    if work_dir:
        os.makedirs(work_dir, exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return p


def tr_hist_add(work_dir, pairs, applied=0, files=0, ts=None):
    """把一次成功应用记进历史：同 (find, repl) 去重并置顶、n+1；上限 TR_HIST_MAX 条。

    返回更新后的列表。pairs 里 find 为空的跳过；applied/files 表示本次的行数/文件数。
    2026-10-13: 置顶位（pin）跟着条目走；未置顶的新条目插在**置顶段之后**——
    手动钉住的对不会被每次「应用」不断顶下去；上限只淘汰未置顶的旧条目。
    """
    rows = tr_hist_load(work_dir)              # 已按「置顶在前」排好
    now = ts or time.strftime("%Y-%m-%d %H:%M")
    for p in pairs or []:
        fp = p[0] if isinstance(p, (list, tuple)) and len(p) > 0 else ""
        rp = p[1] if isinstance(p, (list, tuple)) and len(p) > 1 else ""
        find = fp if isinstance(fp, str) else ""
        repl = rp if isinstance(rp, str) else ""
        if not find:
            continue
        old = None
        for it in rows:
            if it["find"] == find and it["repl"] == repl:
                old = it
                break
        if old is not None:
            rows.remove(old)
            n = old["n"] + 1
            pin = bool(old.get("pin"))
        else:
            n = 1
            pin = False
        pos = sum(1 for it in rows if it.get("pin"))    # 置顶段之后（未置顶的新条目不进置顶区）
        rows.insert(pos, {"find": find, "repl": repl, "ts": now, "n": n,
                          "applied": int(applied or 0), "files": int(files or 0), "pin": pin})
    rows = tr_hist_trim(tr_hist_pinned_first(rows))
    tr_hist_save(work_dir, rows)
    return rows


def tr_hist_pin(work_dir, find, repl, pin=True):
    """手动置顶 / 取消置顶一条历史记录；返回 (是否找到, 更新后的列表)。

    置顶的条目排在列表最前（最近置顶的在最前），且不占 TR_HIST_MAX 的淘汰名额。
    找不到（find+repl 都不匹配）→ 不写盘，返回 (False, 原列表)。
    """
    rows = tr_hist_load(work_dir)
    hit = None
    for it in rows:
        if it["find"] == find and it["repl"] == repl:
            hit = it
            break
    if hit is None:
        return False, rows
    hit["pin"] = bool(pin)
    if hit["pin"]:
        rows.remove(hit)
        rows.insert(0, hit)                    # 新置顶的直接排到置顶段最前
    rows = tr_hist_trim(tr_hist_pinned_first(rows))
    tr_hist_save(work_dir, rows)
    return True, rows


def load_glossary_pairs(path=None):
    """glossary.json 的替换对 [日文, 中文]。

    2026-09-01: 兼容对象格式 [{jp,zh,cat}]（新 schema v2）与旧二元组 [jp,zh]。
    """
    path = path or _GLOSSARY_JSON
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    pairs = []
    for x in data.get("glossary", []):
        if isinstance(x, list) and len(x) == 2:
            pairs.append(tuple(x))
        elif isinstance(x, dict) and x.get("jp") and x.get("zh"):
            pairs.append((x["jp"], x["zh"]))
    return pairs


def validate_glossary(payload):
    """校验前端提交的术语表数据（作品块只读，服务端保留磁盘值）。
    返回 (errors, cleaned)。"""
    errors, cleaned = [], {}
    kn = payload.get("known_names")
    if not isinstance(kn, list) or not all(isinstance(x, str) for x in kn):
        errors.append("known_names 必须是字符串列表")
        cleaned["known_names"] = []
    else:
        dup = {x for x in kn if kn.count(x) > 1}
        if dup:
            errors.append(f"known_names 有重复项: {sorted(dup)[:5]}…")
        cleaned["known_names"] = kn
    gl = payload.get("glossary")
    if not isinstance(gl, list) or not all(
            (isinstance(x, list) and len(x) == 2 and all(isinstance(s, str) for s in x))
            or (isinstance(x, dict) and isinstance(x.get("jp"), str) and isinstance(x.get("zh"), str))
            for x in gl):
        errors.append("glossary 必须是 [日文, 中文] 二元组 或 {jp,zh,cat} 对象列表")
        cleaned["glossary"] = []
    else:
        jps = [x[0] if isinstance(x, list) else x["jp"] for x in gl]
        dup = {x for x in jps if jps.count(x) > 1}
        if dup:
            errors.append(f"glossary 日文侧有重复: {sorted(dup)[:5]}…")
        cleaned["glossary"] = gl
    ch = payload.get("角色")
    if not isinstance(ch, list) or not all(
            isinstance(x, dict) and isinstance(x.get("日文"), str)
            and isinstance(x.get("中文"), str) for x in ch):
        errors.append("角色 必须是包含 日文/中文 字段的对象列表")
        cleaned["角色"] = []
    else:
        # 2026-08-27: 艺术纹理白名单(texture 分类, 中文空)可与真实角色日文同名
        # (如 texture アリス 与 多娜多娜爱丽丝 アリス)——重复检查排除 texture 分类
        jps = [x["日文"] for x in ch if x.get("分类") != "艺术纹理/碎片"]
        dup = {x for x in jps if jps.count(x) > 1}
        if dup:
            errors.append(f"角色 日文侧有重复: {sorted(dup)[:5]}…")
        cleaned["角色"] = ch
    return errors, cleaned


def save_glossary(payload, path=None, backup_root=None):
    """合并保存：schema/项目/作品/角色 保留磁盘值（作品/角色只读），更新日期刷新。

    2026-09-01 三级重构: 角色块迁出 glossary.json → roles.json。
    角色键保留磁盘旧值(兼容旧前端不传 roles 时); glossary 支持新对象格式。
    紧凑格式（separators=(',',':')）+ 结尾换行，与现有文件字节风格一致。
    """
    path = path or _GLOSSARY_JSON
    backup_root = backup_root or os.path.join(os.path.dirname(path), ".glossary_backup")
    errors, cleaned = validate_glossary(payload)
    if errors:
        return {"ok": False, "errors": errors}
    with open(path, encoding="utf-8") as f:
        old = json.load(f)
    new = {
        "schema": old.get("schema", "AA翻译术语表 v1"),
        "更新日期": datetime.date.today().isoformat(),
        "项目": old.get("项目", ""),
        "作品": old.get("作品", []),
        "角色": old.get("角色", []),          # 角色块已迁出 roles.json, 保留磁盘旧值
        "glossary": cleaned["glossary"],
        "known_names": cleaned["known_names"],
    }
    ts = backup_files([path], backup_root)
    text = json.dumps(new, ensure_ascii=False, separators=(",", ":")) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return {"ok": True, "backup": ts, "更新日期": new["更新日期"],
            "角色": len(new["角色"]), "glossary": len(new["glossary"]),
            "known_names": len(new["known_names"])}


def save_roles(roles, path=None, backup_root=None):
    """保存全局角色表 roles.json。返回 {"ok", "角色"}。"""
    path = path or os.path.join(_DATA, "roles.json")
    backup_root = backup_root or os.path.join(os.path.dirname(path), ".glossary_backup")
    # 校验: 角色必须是含 日文/中文 的对象列表
    if not isinstance(roles, list) or not all(
            isinstance(x, dict) and isinstance(x.get("日文"), str) and isinstance(x.get("中文"), str)
            for x in roles):
        return {"ok": False, "errors": ["角色 必须是包含 日文/中文 字段的对象列表"]}
    with open(path, encoding="utf-8") as f:
        old = json.load(f)
    new = {
        "schema": old.get("schema", "AA角色表 v1"),
        "更新日期": datetime.date.today().isoformat(),
        "角色": roles,
    }
    ts = backup_files([path], backup_root)
    text = json.dumps(new, ensure_ascii=False, separators=(",", ":")) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return {"ok": True, "backup": ts, "角色": len(roles)}


def search_work(out_dir, aa_dir, q, limit=300, name_style=None, regex=False):
    """跨章节搜索汉化内容，返回 [{file, line(1-based), idx, tr, src}]。

    2026-09-03: out_dir 不存在/不可读 → 空结果（防御未翻译作品的目录缺失）；
    regex=True 时 q 按正则处理（非法正则 → 空结果）。"""
    if not out_dir or not os.path.isdir(out_dir):
        return []
    try:
        pat = re.compile(q) if regex else None
    except re.error:
        return []
    hits = []
    try:
        fnames = chapter_order(out_dir, name_style)
    except OSError:
        return []
    for fname in fnames:
        tr = read_lines(os.path.join(out_dir, fname))
        # 2026-10-17: 压缩感知——磁盘可能是 <原文名>.gz，裸 exists 会让 src 上下文恒空
        aa_path = txt_existing(os.path.join(aa_dir, src_name_of(fname)))
        src = read_lines(aa_path) if os.path.exists(aa_path) else []
        for i, l in enumerate(tr):
            if pat.search(l) if pat else (q in l):
                hits.append({"file": fname, "line": i + 1, "idx": i,
                             "tr": l[:200], "src": (src[i][:120] if i < len(src) else "")})
                if len(hits) >= limit:
                    return hits
    return hits


# ---------- 揭示板（本地浏览披露板 dat） ----------

BOARD_ROOT = os.path.join(_DATA, "揭示板")
# 揭示板 dat：每 res 一行，格式 `名前<>mail<>投稿日<>本文<>[标题]`（字段数不固定，需容错解析）
# board_search 的解析缓存：path -> (mtime, parsed)，文件未变则复用
_BOARD_PARSE_CACHE = {}
_HEAD_TAG_RE = re.compile(r"</?b>|<br\s*/?>|&#\d+;|&[a-z]+;", re.I)

# 作者「URL/显示名 → 物理目录名」别名映射（2026-08-24）
# 物理目录用 ASCII 安全名（避免 ◆／ 等特殊字符在 URL/路径的隐患），
# 原名保留为别名：/board/原名 与 /board/别名 都能访问，列表显示原名。
BOARD_ALIASES = {
    "◆wCogfUJ／Uw": "wCogfUJ-Uw",   # やる夫の夏休み（やる夫板のシェルター）
}


def board_dir(author):
    """把 URL 里的作者名（原名或别名）解析为物理目录名；未知返回 None。"""
    if not author or author in (".", "..") or "/" in author or "\\" in author:
        return None
    return BOARD_ALIASES.get(author, author)


def board_display_name(dirname):
    """物理目录名 → 显示名（URL 用原名）；无别名时原样。"""
    for display, d in BOARD_ALIASES.items():
        if d == dirname:
            return display
    return dirname


def board_collections():
    """扫描 揭示板/ 根下所有作者/题材目录。返回 [{name, dir, slaves}]。

    每个子目录视为一个「连载」（作者或题材）；无 index.json 时回退扫描 .dat。
    name = 显示名（URL 用）；dir = 物理目录。
    """
    out = []
    if not os.path.isdir(BOARD_ROOT):
        return out
    for name in sorted(os.listdir(BOARD_ROOT)):
        if name.startswith("."):          # .cache 等隐藏目录不是连载（下载缓存 2026-10-10）
            continue
        d = os.path.join(BOARD_ROOT, name)
        if not os.path.isdir(d):
            continue
        slaves = board_slaves(name)
        out.append({"name": board_display_name(name), "dir": d, "slaves": slaves})
    return out


def board_slaves(author):
    """一个作者/题材目录下的全部贴，按スレ目数字升序。返回
    [{label, eid, res, ts, file}]（eid 从文件名取；res/ts 优先 index.json）。

    文件名统一 `<数〉スレ目_<eid>.dat`；index.json 为权威排序来源。
    author 可以是显示名（原名）或物理目录名（别名映射见 BOARD_ALIASES）。
    """
    d = os.path.join(BOARD_ROOT, board_dir(author))
    if not os.path.isdir(d):
        return []
    # index.json 权威清单（含 res/ts，且是下载时整理好的顺序线索）
    idx = {}
    ip = os.path.join(d, "index.json")
    if os.path.exists(ip):
        try:
            idx = {it.get("eid", ""): it for it in
                   json.load(open(ip, encoding="utf-8")).get("slaves", [])}
        except Exception:
            idx = {}
    out = []
    for f in os.listdir(d):
        if not f.endswith(".dat") or f.startswith(".") or "index" in f:
            continue
        m = re.search(r"(\d+)$", os.path.splitext(f)[0])   # 尾巴 eid
        eid = m.group(1) if m else f
        # スレ目编号：从 index.json 的 label 或文件名（数字_前）
        it = idx.get(eid, {})
        label = it.get("label", "") or f.replace("_" + eid + ".dat", "").replace(".dat", "")
        # スレ目数字（用于排序）：label 里的 N → int
        lm = re.search(r"(\d+)\s*スレ目", label)
        s_no = int(lm.group(1)) if lm else 0
        # 从 index.json 拿 res/ts，缺失回退读 dat 统计
        res = it.get("res")
        ts = it.get("ts", "")
        if res is None or not ts:
            fp = os.path.join(d, f)
            try:
                with open(fp, encoding="utf-8") as fh:
                    content = fh.read()
                if res is None:
                    res = len([ln for ln in content.split("\n") if ln.strip()])
                if not ts:
                    first = content.split("\n")[0]
                    tm = re.search(r"\d{4}/\d{2}/\d{2}\(\S\) \d{2}:\d{2}:\d{2}", first)
                    ts = tm.group(0) if tm else ""
            except Exception:
                res = res or 0
        out.append({"label": (it.get("label") or label).replace(".dat", "") or f,
                    "eid": eid, "res": res or 0, "ts": ts,
                    "no": s_no, "file": f, "title": it.get("title", "")})
    out.sort(key=lambda x: (x["no"], x["eid"]))
    return out


def _clean_dat_field(field):
    """清理 dat 字段里的 HTML 残留（</b>、<br>、html 实体）→ 纯文本。"""
    return re.sub(r"<[^>]+>", "", field).strip()


def _board_parse_cached(path):
    """parse_board_dat 的 mtime 缓存：同一文件未变则复用解析结果（搜索遍历多贴免重复读盘）。"""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    c = _BOARD_PARSE_CACHE.get(path)
    if c and c[0] == mtime:
        return c[1]
    parsed = parse_board_dat(path)
    _BOARD_PARSE_CACHE[path] = (mtime, parsed)
    return parsed


def _board_plain(row):
    """把 _board_row_html 输出（含 <b> 标签/实体）还原成纯文本，供搜索匹配与预览。"""
    return html.unescape(re.sub(r"<[^>]+>", "", row))


# 纯绘图/装饰字符（AA 画线、边框、假名艺术符等）——判定"是否为可读台词行"
_AA_ONLY_RE = re.compile(r"[｜／＼＿￣┌┐└┘─━┃━╋┼├┤┬┴┏┓┗┛┣┫┳┻╠╣╦╩╔╗╚╝═║⌒♪＊*#$&@○●◇◆■□▲△▼▽☆★∴∵~´｀¨`　？?！!。、・；:…\u2014\u2013\u00b7々-]")


def _board_hit_preview(body_plain, ql):
    """从某楼纯文本行里提取贴近关键词的可读预览片段。

    优先：含 q 的那行两侧削掉纯绘图符，保留含词窗口。
    若该行削完仍空，退回含 q 行的原始内容；整体仍空则取第一个非空行。
    """
    # 含关键词的行（按贴序第一个）
    hit_idx = next((i for i, x in enumerate(body_plain) if ql in x.lower()), -1)
    if hit_idx >= 0:
        line = body_plain[hit_idx]
        # 若含 q 行全是绘图符（说明词被切开），取含 q 的行原样
        core = _AA_ONLY_RE.sub("", line)
        if core.strip():
            return core.strip()
        return line.strip()
    # 兜底：取第一个非空行
    for x in body_plain:
        if x.strip():
            return x.strip()
    return ""


def board_search(author, q, limit=200):
    """在作者全部 dat（board_slaves）里全文搜索 q，返回命中列表。

    每个结果: {file, eid, label, no, name, date, preview}
    - file/label 用于跳转定位贴；no 为楼号（配合 page 算出跳转页）
    - preview 为命中的正文行纯文本（剥标签+解码实体），无则取该楼前几行
    - 按贴顺序、楼号升序返回，最多 limit 条
    """
    if not q or not q.strip():
        return []
    ql = q.strip().lower()
    out = []
    slaves = board_slaves(author)
    bdir = board_dir(author)
    for s in slaves:
        parsed = _board_parse_cached(os.path.join(BOARD_ROOT, bdir, s["file"]))
        if not parsed:
            continue
        for r in parsed["res"]:
            body_plain = [_board_plain(x) for x in r["body"]]
            joined = "\n".join(body_plain)
            if ql not in joined.lower():
                continue
            out.append({
                "file": s["file"], "eid": s["eid"], "label": s["label"],
                "no": r["no"], "name": r["name"], "date": r["date"],
                "preview": _board_hit_preview(body_plain, ql),
            })
            if len(out) >= limit:
                return out
    return out


def _board_row_html(raw_row):
    """把揭示板 body 里的一个 `<br>` 片段转成安全可渲染的 HTML 字符串。

    - html.unescape：解码 `&gt;`→>`、`&lt;`→<、`&#9829;`→♥、`&#10084;`→❤ 等实体
    - html.escape：转义残留的 `<>`，防 AA 艺术符/引用符破坏页面
    - 加粗还原：`【1D:N:X】` 骰子结果包 `<b>`（与 AA 阅读器 1D 加粗规则一致；
      只对正文里真正的 `【1D..:..】`，不含名字字段的 `<b>◆作者</b>`——名字已单独清洗）
    - 空行保留（返回 ''，模板据此输出空行）
    """
    row = html.unescape(raw_row)
    # 剥掉原站 <b></b> 标签（它们是骰子结果【1D..:..】的加粗标记；下面统一用 regex 重建）
    row = row.replace("<b>", "").replace("</b>", "")
    row = html.escape(row, quote=False)          # 再转义，防其它 <> 注入
    row = re.sub(r"【(1D[\d:]+)】", r"<b>【\1】</b>", row)   # 骰子结果加粗
    return row


def parse_board_dat(path):
    """解析一条揭示板贴 → {title, url, res:[{no, name, date, id, body:[行]}]}。

    dat 每 res 一行：`名前<>mail<>投稿日<>本文<>[标题]`；字段数可能不足 5。
    名字字段可能带 `<b>◆作者</b>`，投稿日字段含 ID；本文按 `<br>` 切分得到 AA 行。
    body 每行已是 html 安全（实体解码 + escape + 【1D:..】加粗），空行保留。
    """
    with open(path, "rb") as f:
        raw = f.read()
    # 编码容错：优先 UTF-8，失败回退 CP932（揭示板 dat 常为 Shift_JIS）
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = raw.decode("cp932", errors="replace")
    lines = [ln for ln in content.split("\n") if ln.strip()]
    title = ""
    res = []
    for i, ln in enumerate(lines):
        parts = ln.split("<>")
        if len(parts) < 3:
            continue
        name = _clean_dat_field(parts[0])
        date_raw = _clean_dat_field(parts[2])
        # 投稿日 + ID
        date, idv = "", ""
        dm = re.search(r"\d{4}/\d{2}/\d{2}\(\S\) \d{2}:\d{2}:\d{2}", date_raw)
        if dm:
            date = dm.group(0)
        idm = re.search(r"ID[:：]\s*([A-Za-z0-9+/=]+)", date_raw)
        if idm:
            idv = idm.group(1)
        # 本文（可能有第4或5字段标题）
        body_raw = parts[3] if len(parts) > 3 else ""
        rows = [_board_row_html(r.replace("\r", "")) for r in body_raw.split("<br>")]
        # 首条 res 的 body 末尾嵌主题标题（作者帖常见），剥离出 title
        if i == 0 and len(parts) > 4:
            tail = _clean_dat_field(parts[4])
            if tail:
                title = tail
        elif i == 0 and rows:
            last = rows[-1].strip()
            if last and (last.startswith("【") or "です" in last):
                tm = re.search(r"【[^】]*】", last)
                if tm:
                    title = "【" + tm.group(1) + "】"
        res.append({"no": i + 1, "name": name, "date": date, "id": idv,
                    "body": rows})
    # 标题兜底：扫描前几楼 body 里的【…】标题（只取【】块，不取整行 AA）
    if not title:
        for r in res[:3]:
            for row in r["body"]:
                # 匹配【...】...【...】 双块标题（如【R-18】やる夫の夏休み【安価】）或单【...】
                tm = re.search(r"【[^】]*】[^【】]*【[^】]*】|【[^】]*】", row)
                if tm:
                    cand = tm.group(0).strip()
                    # 过滤明显非标题（过短/纯符号）
                    if len(cand) >= 4 and re.search(r"[ぁ-んァ-ヶ一-龠]", cand):
                        title = cand
                        break
            if title:
                break
    if not title:
        title = os.path.splitext(os.path.basename(path))[0]
    return {"title": title, "res": res}


# =========================================================================
# 作品表格改版（2026-08-24）：作者◆提取 / work_meta 读写 / 素材占比统计
# =========================================================================

# 楼主帖头里 ◆tripcode 的提取（tripcode 字符集 = 非「：: 空格 全角空格」字符）
_TRIP_RE = re.compile(r"◆([^\s：:　]+)")

# 帖头行识别：NNN ： ♨ / NNN 名前： ♨ / N 名前： ♨ 投稿日（fukou 的「1 名前： ◆」数字与名前间是空格）
_HEADER_LINE_RE = re.compile(r"^\d{1,5}\s*(?:名前\s*)?[：:]")


def extract_author_tripcode(aa_dir):
    """从作品原文目录提取楼主 ◆tripcode（投票法：统计每个◆出现次数，取众数）。

    楼主判定：整部作品所有 *汉化/aa txt 中出现的 ◆tripcode，出现次数最多者即楼主
    （楼主发帖最多；回帖者只零星出现）。比"第一个◆"鲁棒——文件常从中间楼层开始，
    首个◆可能是回帖者（实测 saki_fc2 首◆=◆gy.Jr0fkrLU3 回帖者，投票法得真楼主 ◆i4P3ia.arsu1）。
    返回 '◆xxxx'；扫不到返回 ''。约扫前 12 本 × 前 600 行控制耗时。"""
    import glob
    if not aa_dir or not os.path.isdir(aa_dir):
        return ""
    files = [os.path.join(aa_dir, n) for n in list_txt(aa_dir)][:12]
    from collections import Counter
    votes = Counter()
    for f in files:
        n = 0
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if n > 600:
                        break
                    n += 1
                    line = line.rstrip("\n")
                    if line.startswith("#") or line.startswith("====="):
                        continue
                    if not _HEADER_LINE_RE.match(line):
                        continue
                    m = _TRIP_RE.search(line)
                    if m:
                        votes[m.group(1)] += 1
        except OSError:
            continue
    if not votes:
        return ""
    return "◆" + votes.most_common(1)[0][0]


def normalize_author(author):
    """作者字段清洗（2026-09-13）。

    详情弹窗旧版保存时把输入直接加前缀写成 `'◆' + 空串 = '◆'`——`'◆'` 是**真值**，
    会把列表页「帖头 ◆ 自动提取」的作者遮蔽成光秃秃一个 `◆`（用户报的「保存导致作者信息丢失」）。
    故 `'◆'` 一律当未设（清空）处理，历史脏数据读取时也自愈。
    """
    a = (author or "").strip()
    return "" if a == "◆" else a


def author_display(work_id, meta_entry, work_dir, aa_dir, override=None):
    """详情弹窗与列表页**共用**的作者取值，返回 (author, is_auto)。

    优先级：work_meta 手动值 → 覆盖表（override）→ 帖头 ◆ 自动提取。
    两处口径必须一致，否则详情显示空、列表显示 ◆xxx，用户会以为"不是同一个作者"。
    """
    manual = normalize_author((meta_entry or {}).get("author"))
    if manual:
        return manual, False
    auto = (override or {}).get(work_id) or extract_author_cached(work_dir, aa_dir) or ""
    return auto, bool(auto)


def extract_author_cached(work_dir, aa_dir):
    """extract_author_tripcode 的磁盘增量缓存（固化重复扫描）。

    缓存 work_dir/meta_cache.json 的 "author" 段，按 aa 文件 mtime+size 指纹命中；
    aa 未变时直接读缓存（零扫描）。
    """
    import glob, json
    if not aa_dir or not os.path.isdir(aa_dir):
        return ""
    files = [os.path.join(aa_dir, n) for n in list_txt(aa_dir)][:12]
    if not files:
        return ""
    fp_parts = [f"{os.path.basename(f)}:{os.path.getmtime(f):.0f}:{os.path.getsize(f)}" for f in files]
    fp = stable_fp(fp_parts)
    cache_path = os.path.join(work_dir, "meta_cache.json")
    try:
        with _meta_cache_lock():
            cache = json.load(open(cache_path, encoding="utf-8")) if os.path.exists(cache_path) else {}
        if cache.get("author_fp") == fp:
            return cache.get("author", "")
    except Exception:
        cache = {}
    result = extract_author_tripcode(aa_dir)
    try:
        with _meta_cache_lock():
            cache["author_fp"] = fp
            cache["author"] = result
            os.makedirs(work_dir, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
    except OSError:
        pass
    return result


# work_meta.json 读写（作品级可编辑元数据：汉化名/标签/素材/作者兜底）
def load_work_meta(path):
    """读 work_meta.json；不存在或损坏返回 {}. path 为 data/work_meta.json。"""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_work_meta(path, data):
    """保存 work_meta.json（缩进可读，无 BOM；元数据供人工审校，故用缩进而非紧凑）。返回 True/False。"""
    try:
        dirn = os.path.dirname(path)
        if dirn:
            os.makedirs(dirn, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        return True
    except OSError:
        return False


def _material_names_for(name):
    """素材名（可带「（作品）」后缀）→ (中文名, 日文原名) 都用于 count 统计。

    2026-08-26: 支持「名（作品）」格式——同名角色跨作品时按作品名精确锁定
    （_split_mat 分离后双重匹配）；查不到返回 (name, name)。
    """
    try:
        cn_part, wn_part = _split_mat(name)
        roles, works = _load_roles_works()
        for c in roles:
            ccn, cjp = c.get("中文") or "", c.get("日文") or ""
            if ccn != cn_part and cjp != cn_part:
                continue
            # 同名多作品: 输入带作品后缀时才精确匹配; 纯名(旧数据)匹配第一条
            if wn_part:
                for wid in (c.get("作品") or []):
                    if works.get(wid) == wn_part:
                        return (ccn or cn_part, cjp or cn_part)
            else:
                return (ccn or cn_part, cjp or cn_part)
        return (cn_part, cn_part)
    except Exception:
        return (cn_part, cn_part) if 'cn_part' in dir() else (name, name)


def _material_match_names(name):
    """素材名 → 统计用匹配词集合（列表）。2026-08-27 素材统计防误判/支持仅日文。

    规则：
    - 解析「名（作品）」+ 可选「仅日文」后缀（如「泉奈（碧蓝档案）仅日文」）
    - 查角色表（作品精确 + 基础名部分匹配）→ 收集 中文名/日文原名
    - 基础名(用户输入)本身作为匹配词（支持短名: 泉奈/爱丽丝——文本常用短名）
    - 繁简变体互相覆盖（愛麗絲↔爱丽丝 同一角色）
    - 「仅日文」→ 只保留日文原名（中文名可能是常见词, 防误判）
    - 排除 len<2 的单字符词（L 等——汉化文本行号前缀 L<数字> 大量碰撞, 必误判）
    """
    try:
        # 先剥离「仅日文」后缀, 再解析「名（作品）」(否则括号不在末尾拆不出作品)
        base = name.strip()
        jp_only = base.endswith("仅日文")
        if jp_only:
            base = base[: -len("仅日文")].strip()
        elif base.endswith("日文"):
            jp_only = True
            base = base[: -len("日文")].strip()
        base, wn = _split_mat(base)
        if not base:
            return []
        roles, works = _load_roles_works()
        words = set()
        words.add(base)
        # 查角色表: 作品精确 + 基础名部分匹配(久田泉奈 含 泉奈)
        for c in roles:
            ccn, cjp = c.get("中文") or "", c.get("日文") or ""
            hit = False
            if ccn == base or cjp == base:
                hit = True
            elif base and (base in ccn or base in cjp) and len(base) >= 2:
                hit = True   # 基础名是角色名的一部分(短名)
            if not hit:
                continue
            if wn:
                ws = c.get("作品") or []
                if not any(works.get(w) == wn for w in ws):
                    # 作品不符: 仍收集日文短名(如 天童アリス→アリス, 多娜多娜爱丽丝文本用アリス)
                    _jp_short = _jp_short_name(cjp)
                    if _jp_short:
                        words.add(_jp_short)
                    continue
            if ccn:
                words.add(ccn)
            if cjp:
                words.add(cjp)
                _jp_short = _jp_short_name(cjp)
                if _jp_short and _jp_short != cjp:
                    words.add(_jp_short)
            # 2026-08-28: 别名归并——同角色多译名(青蛙子/ケロちゃん 并入 泄矢诹访子)
            #   只放明确昵称/别称(不放单字短名防误伤), 素材统计展开全部变体
            for _alias in (c.get("别名") or []):
                _a = str(_alias).strip()
                if len(_a) >= 2:
                    words.add(_a)
        # 繁简变体(爱丽丝↔愛麗絲)
        _add_trad_variants(words)
        # 仅日文: 只留含假名的词
        if jp_only:
            words = {w for w in words if _has_jp_chars(w)}
        # 排除单字符(行号 L<数字> 碰撞等)
        words = {w for w in words if len(w) >= 2}
        return sorted(words)
    except Exception:
        return []


def _jp_short_name(jp):
    """日文原名 → 去姓的短名(久田イズナ→イズナ, 天童アリス→アリス)。无假名部分返回 None。"""
    if not jp:
        return None
    import re as _re
    m = _re.search(r"[ァ-ヶ]+$", jp)   # 末尾片假名段
    if m and len(m.group(0)) >= 2:
        return m.group(0)
    return None


def _has_jp_chars(s):
    import re as _re
    return bool(_re.search(r"[ぁ-んァ-ヶー]", s))


def _add_trad_variants(words):
    """为集合中的中文词补繁简变体(爱丽丝→愛麗絲)。失败静默(无 zhconv 也可用)。"""
    try:
        from zhconv import convert
        for w in list(words):
            try:
                t = convert(w, "zh-hant")
                s = convert(w, "zh-hans")
            except Exception:
                continue
            if t and t != w:
                words.add(t)
            if s and s != w:
                words.add(s)
    except Exception:
        pass


def _split_mat(name):
    """解析素材存储格式「角色名（作品名）」→ (角色名, 作品名)。

    2026-08-26: materials 支持同名角色跨作品（角色名+作品唯一），
    存储带作品后缀「名（作品名）」；无后缀返回 (name, None)。
    兼容旧纯名数据与「名(半角)」格式。
    """
    import re as _re
    if not name:
        return ("", None)
    m = _re.match(r"^(.*?)[（(](.+?)[）)]$", name.strip())
    if m:
        cn = m.group(1).strip()
        wn = m.group(2).strip()
        return (cn, wn) if cn else (name.strip(), None)
    return (name.strip(), None)


_ROLES_WORKS_CACHE = {"sig": None, "roles": [], "works": {}}

def _load_roles_works():
    """加载全局角色表 + 作品表。返回 (角色list, 作品dict{id:名称})。

    2026-09-01 三级重构: 角色块迁出 glossary.json → roles.json。
    2026-09-05: 加模块级缓存（mtime 签名失效）——首页 index 对每作品每素材调 material_display_parts，
    之前每次重读 roles.json/glossary.json profile 占首页冷扫 ~10.8s。
    """
    import json as _json
    _roles_p = os.path.join(_DATA, "roles.json")
    _g_p = os.path.join(_DATA, "glossary.json")
    _sig = _sig_roles_works(_roles_p, _g_p)
    if _ROLES_WORKS_CACHE.get("sig") == _sig:
        return _ROLES_WORKS_CACHE["roles"], _ROLES_WORKS_CACHE["works"]
    roles = []
    if os.path.exists(_roles_p):
        try:
            roles = _json.load(open(_roles_p, encoding="utf-8")).get("角色", [])
        except Exception:
            roles = []
    works = {}
    if os.path.exists(_g_p):
        try:
            _g = _json.load(open(_g_p, encoding="utf-8"))
            works = {w["id"]: w["名称"] for w in _g.get("作品", [])}
        except Exception:
            works = {}
    _ROLES_WORKS_CACHE["sig"] = _sig
    _ROLES_WORKS_CACHE["roles"] = roles
    _ROLES_WORKS_CACHE["works"] = works
    return roles, works


def _sig_roles_works(roles_p, glossary_p):
    """roles.json/glossary.json 的 mtime 签名；文件改动/被删 → 缓存失效重读。"""
    try:
        import os as _o
        parts = []
        for _p in (roles_p, glossary_p):
            parts.append(f"{_o.path.getmtime(_p) if _o.path.exists(_p) else -1:.0f}:{_o.path.getsize(_p) if _o.path.exists(_p) else -1}")
        return "|".join(parts)
    except Exception:
        return ""


_WS_ALIASES_CACHE = None

def _works_aliases():
    """作品 id → 别名列表（缓存）。用于「名（别名）」匹配作品。"""
    global _WS_ALIASES_CACHE
    if _WS_ALIASES_CACHE is None:
        import json as _json
        _p = os.path.join(_DATA, "glossary.json")
        _WS_ALIASES_CACHE = {}
        if os.path.exists(_p):
            _g = _json.load(open(_p, encoding="utf-8"))
            for w in _g.get("作品", []):
                _WS_ALIASES_CACHE[w["id"]] = w.get("别名") or []
    return _WS_ALIASES_CACHE


def resolve_material(name):
    """素材名（可带「（作品）」后缀）→ 候选角色条目列表。

    2026-08-26: 手动添加素材的作品识别——查角色表返回所有可能出处：
    [{cn, jp, wid, wname, note}]；输入带作品后缀时精确锁定该作品。
    查不到返回 []（调用方可让用户手动输入作品）。
    """
    cn_part, wn_part = _split_mat(name)
    roles, works = _load_roles_works()
    out = []
    for c in roles:
        ccn, cjp = c.get("中文") or "", c.get("日文") or ""
        if ccn != cn_part and cjp != cn_part:
            continue
        for wid in (c.get("作品") or []):
            # 排除艺术纹理/やる夫系原创(非真角色出处, 与 mat-scan 同口径)
            if wid in ("texture", "yaruo"):
                continue
            wname = works.get(wid)
            if wn_part and wname != wn_part:
                continue
            out.append({"cn": ccn, "jp": cjp, "wid": wid,
                        "wname": wname or "", "note": c.get("备注", "") or ""})
    return out


def material_frequency(out_dir, names):
    """在汉化目录所有 *_汉化.txt 中统计每个素材人物出现次数(中文名+日文原名都统计)。

    返回 {name: {"count": total, "chapters": 出现章节数}}（只含 >0 的）；
    out_dir 不存在或无汉化文件返回 {}。
    """
    import glob
    if not out_dir or not os.path.isdir(out_dir):
        return {}
    files = [os.path.join(out_dir, n) for n in list_txt(out_dir, "_汉化.txt")]
    if not files:
        return {}
    # 每个素材: 匹配词集合(中文名+日文原名+短名+繁简变体, 2026-08-27 防误判)
    pk = {n: _material_match_names(n) or [n] for n in names if n}
    if not pk:
        return {}
    counts = {n: 0 for n in pk}
    chaps = {n: 0 for n in pk}
    for f in files:
        try:
            # 2026-10-17: files 来自 list_txt（规范名 .txt），磁盘可能是 .gz → 走 read_lines
            text = "\n".join(read_lines(txt_existing(f)))
        except OSError:
            continue
        for n, wl in pk.items():
            c = sum(text.count(w) for w in wl)
            if c:
                counts[n] += c
                chaps[n] += 1
    return {n: {"count": counts[n], "chapters": chaps[n]}
            for n in counts if counts[n] > 0}


_META_CACHE_LOCK = None  # 惰性初始化（避免 import 时创建锁）

def _meta_cache_lock():
    global _META_CACHE_LOCK
    if _META_CACHE_LOCK is None:
        import threading
        _META_CACHE_LOCK = threading.Lock()
    return _META_CACHE_LOCK


def material_display_parts(name):
    """素材名（可带「（作品）」后缀）→ (显示名, 作品名, 出处)。查全局术语表「角色」表。

    返回 (name, 作品名, 出处备注)；查询不到(やる夫系/无作品)返回 (None, None, None)。
    作品名来自「作品」表名称, 出处来自角色表备注(如「现实名百地希留耶」)。
    2026-08-26: 支持「名（作品）」——同名跨作品时按作品名精确锁定。
    """
    try:
        cn_part, wn_part = _split_mat(name)
        roles, works = _load_roles_works()
        for c in roles:
            ccn, cjp = c.get("中文") or "", c.get("日文") or ""
            # 精确 + 部分匹配(短名: 泉奈→久田泉奈; 2026-08-27 与 _material_match_names 同口径)
            hit = (ccn == cn_part or cjp == cn_part) or (
                len(cn_part) >= 2 and (cn_part in ccn or cn_part in cjp))
            if not hit:
                continue
            # 同名多作品: 带作品后缀时只匹配该作品(支持别名: 多娜多娜→多娜多娜 一起来干坏事吧)
            cws = c.get("作品") or []
            if wn_part:
                _wn_lower = wn_part.lower()
                matched = []
                # 2026-08-28: 大小写不敏感 + 子串匹配——mat-scan 知识库 key 是小写 fgo/舰队collection,
                #   全局作品名是 舰队Collection/Fate/Grand Order、别名是斜杠串 'FGO/Fate/Grand Order/...'
                #   精确==/==都会失配→悬浮显示原创。子串匹配可命中缩写/带斜杠别名。
                for wid in cws:
                    _cands = [works.get(wid) or ""] + [a for a in _works_aliases().get(wid, [])]
                    if any(_wn_lower in (a or "").lower() for a in _cands):
                        matched.append(wid)
                        break
                if not matched:
                    continue
                wid = matched[0]
            else:
                wid = cws[0] if cws else None
            if wid == "yaruo":
                return (None, None, None)   # やる夫系原创 不显示
            wname = works.get(wid) if wid else None
            note = c.get("备注", "") or ""
            dn = c.get("中文") or cn_part
            return (dn, wname, note)
        return (cn_part, None, None)
    except Exception:
        return (name, None, None)


def material_display_name(name):
    """素材中文名 → 显示名「名称（作品）」。从全局术语表「角色」表查所属作品。

    角色表条目: {日文, 中文, 作品:[作品id], 分类, 备注}；作品id映射「作品」表名称。
    查不到(やる夫系原创/无作品)返回 None → 调用方不显示。
    """
    dn, wn, _ = material_display_parts(name)
    if dn is None:
        return None
    if wn:
        return f"{name}（{wn}）"
    return name


def material_frequency_cached(work_dir, out_dir, names):
    """素材统计的磁盘增量缓存（固化重复扫描）。

    缓存 work_dir/meta_cache.json 的 "materials" 段，按汉化文件 mtime+size 指纹命中；
    文件未变时直接读缓存（零扫描）。materials 名单变化（names 参数）时 key 失效重算。

    2026-08-30 优化: 指纹计算结果加内存 TTL 缓存（首页每作品多次刷新不重复全量 stat）。
    """
    import glob, json
    if not out_dir or not os.path.isdir(out_dir) or not names:
        return {}
    files = [os.path.join(out_dir, n) for n in list_txt(out_dir, "_汉化.txt")]
    if not files:
        return {}
    # 指纹: 所有汉化文件 mtime+size 的哈希 + 名单(v2: 加章节数结构, 使旧纯数字缓存失效)
    # 内存缓存: (out_dir, tuple(names)) -> (fetched_at, fp) —— 300s 内不重复全量 stat
    _fp_key = (out_dir, tuple(names))
    _now = time.time()
    _fp_hit = _MAT_FP_CACHE.get(_fp_key)
    if _fp_hit and _now - _fp_hit[0] < _MCH_TTL:
        fp = _fp_hit[1]
    else:
        fp_parts = ["v2"] + [n for n in names if n] + [f"{os.path.basename(f)}:{os.path.getmtime(f):.0f}:{os.path.getsize(f)}" for f in files]
        fp = stable_fp(fp_parts)
        _MAT_FP_CACHE[_fp_key] = (_now, fp)
    cache_path = os.path.join(work_dir, "meta_cache.json")
    try:
        with _meta_cache_lock():
            cache = json.load(open(cache_path, encoding="utf-8")) if os.path.exists(cache_path) else {}
        if cache.get("materials_fp") == fp:
            return cache.get("materials", {})
    except Exception:
        cache = {}
    # 重算
    result = material_frequency(out_dir, names)
    try:
        with _meta_cache_lock():
            cache["materials_fp"] = fp
            cache["materials"] = result
            os.makedirs(work_dir, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
    except OSError:
        pass
    return result


def material_chapters(out_dir, name):
    """统计某素材人物在哪些章节（文件名）出现、各出现几次。

    用于详情弹窗「素材 + 具体出现话数」。返回 {file_base: count}（按出现次数降序）。
    """
    import glob
    if not out_dir or not os.path.isdir(out_dir):
        return {}
    files = [os.path.join(out_dir, n) for n in list_txt(out_dir, "_汉化.txt")]
    out = {}
    for f in files:
        try:
            cnt = "\n".join(read_lines(txt_existing(f))).count(name)
        except OSError:
            continue
        if cnt:
            out[os.path.splitext(os.path.basename(f))[0]] = cnt
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def material_chapters_cached(work_dir, out_dir, name):
    """material_chapters 的磁盘增量缓存（固化详情弹窗重复扫描）。

    缓存 work_dir/meta_cache.json 的 "mat_chapters" 段，按汉化文件 mtime+size 指纹命中。
    """
    import glob, json
    if not out_dir or not os.path.isdir(out_dir) or not name:
        return {}
    files = [os.path.join(out_dir, n) for n in list_txt(out_dir, "_汉化.txt")]
    if not files:
        return {}
    _fp_key = (out_dir, "mat_chapters", name)
    _now = time.time()
    _fp_hit = _MAT_FP_CACHE.get(_fp_key)
    if _fp_hit and _now - _fp_hit[0] < _MCH_TTL:
        fp = _fp_hit[1]
    else:
        fp_parts = [name] + [f"{os.path.basename(f)}:{os.path.getmtime(f):.0f}:{os.path.getsize(f)}" for f in files]
        fp = stable_fp(fp_parts)
        _MAT_FP_CACHE[_fp_key] = (_now, fp)
    cache_path = os.path.join(work_dir, "meta_cache.json")
    try:
        with _meta_cache_lock():
            cache = json.load(open(cache_path, encoding="utf-8")) if os.path.exists(cache_path) else {}
        if cache.get("mat_chapters_fp") == fp:
            return cache.get("mat_chapters", {})
    except Exception:
        cache = {}
    result = material_chapters(out_dir, name)
    try:
        with _meta_cache_lock():
            cache["mat_chapters_fp"] = fp
            cache["mat_chapters"] = result
            os.makedirs(work_dir, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
    except OSError:
        pass
    return result


# ========== R18 章节级判定汇总（2026-08-24） ==========

def r18_chapter_judge(work_dir, base):
    """读单章 prep 判定。返回 dict(r18_hit, r18_lines_count, r18_word_hits, text_lines, conc) 或 None。

    conc 保留（行浓度, 仅供参考）；UI 主指标改用 r18_word_hits（命中词次数）。
    """
    import json
    p = os.path.join(work_dir, "prep", base + ".json")
    if not os.path.exists(p):
        return None
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return None
    r18 = d.get("r18", {})
    text_lines = d.get("text_lines", 0)
    hit = r18.get("hit", "no")
    lines_count = r18.get("r18_lines_count", 0)
    word_hits = r18.get("r18_word_hits", 0)
    conc = round(lines_count * 100 / text_lines, 1) if text_lines else 0.0
    return {"r18_hit": hit, "r18_lines_count": lines_count,
            "r18_word_hits": word_hits,
            "text_lines": text_lines, "conc": conc}


def r18_summary(work_dir, out_dir):
    """作品级 R18 汇总（只统计已汉化章, 口径=方案定稿）。

    返回 dict:
      total      = 已汉化章数 (汉化/ *_汉化.txt)
      yes        = 其中判定 yes 的章数
      suspect    = 其中判定 suspect 的章数
      no         = 其中判定 no 的章数
      pct        = yes/total 百分比 (章节占比 A)
      r18_lines  = Σ(已汉化章 R18 行)
      text_lines = Σ(已汉化章文本行)
      conc       = r18_lines/text_lines 百分比 (浓度 B)
      yes_chapters = [{base, conc}]  判定 yes 的章
      suspect_chapters = [{base, conc}]
    """
    import glob
    if not out_dir or not os.path.isdir(out_dir):
        return {"total": 0, "yes": 0, "suspect": 0, "no": 0, "pct": 0.0,
                "r18_lines": 0, "text_lines": 0, "conc": 0.0,
                "yes_chapters": [], "suspect_chapters": []}
    files = [os.path.join(out_dir, n) for n in list_txt(out_dir, "_汉化.txt")]
    yes = suspect = no = r18_lines = text_lines = word_hits = 0
    yes_ch, sus_ch = [], []   # 分开初始化（勿共用一个列表, append 会互相污染）
    for f in files:
        base = os.path.basename(f).replace("_汉化.txt", "")
        j = r18_chapter_judge(work_dir, base)
        if not j:
            continue
        text_lines += j["text_lines"]
        r18_lines += j["r18_lines_count"]
        word_hits += j["r18_word_hits"]
        if j["r18_hit"] == "yes":
            yes += 1
            yes_ch.append({"base": base, "conc": j["conc"], "hits": j["r18_word_hits"]})
        elif j["r18_hit"] == "suspect":
            suspect += 1
            sus_ch.append({"base": base, "conc": j["conc"], "hits": j["r18_word_hits"]})
        else:
            no += 1
    total = len(files)
    return {
        "total": total, "yes": yes, "suspect": suspect, "no": no,
        "pct": round(yes * 100 / total, 1) if total else 0.0,
        "r18_lines": r18_lines, "text_lines": text_lines,
        "word_hits": word_hits,
        "conc": round(r18_lines * 100 / text_lines, 1) if text_lines else 0.0,
        "yes_chapters": yes_ch, "suspect_chapters": sus_ch,
    }


import os as _os
import shutil as _shutil


def delete_surface(cfg, all_work_dirs=None):
    """作品删除面（纯计算，不删盘）。返回 dict:
      root    = commonpath(各 cfg 目录)
      dirs    = 各 cfg 目录规范路径去重
      shared  = root 下是否还有本作品 dirs 之外的其它作品目录（共享根→逐目录删）
      items   = [(kind,path), ...] 待删项，kind in ('dir',)
    规则（v2 5.4）：独占根→删根目录；共享根（如 saki/saki_fc2 共用 aaManga/さーきー/）
    退化为逐 cfg 目录删，绝不误删同根其它作品。
    """
    keys = ('aa_dir', 'out_dir', 'work_dir', 'raw_dir')
    dirs = []
    for k in keys:
        p = cfg.get(k)
        if not p:
            continue
        np = _os.path.normpath(p)
        if np not in dirs:
            dirs.append(np)
    if not dirs:
        return {'root': None, 'dirs': [], 'shared': False, 'items': []}
    root = _os.path.commonpath(dirs)
    shared = False
    if all_work_dirs is not None:
        rp = _os.path.realpath(root)
        # 两侧都用 realpath 口径比：`dirs` 是本作品自己的目录，若拿它们的字面路径与
        # all_work_dirs 的 realpath 比，`aaManga/` 是 junction 时（→ 别的盘）本作品
        # 自己的目录也会被当成「别人的目录」→ shared 恒真 → 只逐目录删、空掉的作品
        # 文件夹留在 aaManga 下（2026-10-12 实测 pokeko_fc2）。
        own = set(_os.path.normpath(_os.path.realpath(d)) for d in dirs)
        for d in all_work_dirs:
            dn = _os.path.normpath(_os.path.realpath(d))
            if dn not in own and dn.startswith(rp + _os.sep):
                shared = True
                break
    items = []
    if shared:
        for d in dirs:
            if _os.path.exists(d):
                items.append(('dir', d))
    else:
        if _os.path.exists(root):
            items.append(('dir', root))
    return {'root': root, 'dirs': dirs, 'shared': shared, 'items': items}


def delete_work(surface):
    """按 delete_surface 的结果删盘。返回实际删除的路径列表。"""
    removed = []
    for kind, path in surface['items']:
        try:
            if _os.path.isdir(path):
                _shutil.rmtree(path)
            elif _os.path.exists(path):
                _os.remove(path)
            removed.append(path)
        except OSError:
            continue
    return removed


def remove_paths(paths):
    """删除一组显式文件/目录路径（交付 HTML / work 级文件等）。返回实际删除列表。"""
    removed = []
    for p in paths or []:
        try:
            if _os.path.isdir(p):
                _shutil.rmtree(p)
            elif _os.path.exists(p):
                _os.remove(p)
            removed.append(p)
        except OSError:
            continue
    return removed


def avg_rating(ratings):
    """作者综合评分：有评分(1-5 int)作品的算术平均（一位小数）；全无评分返回 None。"""
    vals = [r for r in (ratings or []) if isinstance(r, int) and 1 <= r <= 5]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 1)


def _dir_top_snapshot(d):
    """目录顶层文件快照：{(name, mtime, size)} → (maxmtime, nfiles, 确定性指纹)。
    用于 last_update 磁盘缓存失配检测（文件内容改/增删 → 指纹变）。
    Windows 目录自身 mtime 对内容修改不更新，必须用文件级快照。
    指纹用 hashlib.md5（跨进程稳定）；内置 hash() 对 str 元组跨进程随机，磁盘缓存会永久失配。

    2026-09-14: 改 os.scandir —— Windows 上 DirEntry.stat() 直接用目录枚举时已带的数据，
    不再为每个文件多付 isfile()+stat() 两次系统调用。全库 87 个 aa/ 实测 14.33s → 0.14s（100x），
    结果逐字节一致。这是「命中路径也要 stat 全部文件」这个既有缺陷的真凶。
    """
    parts = []
    maxm = 0
    n = 0
    try:
        with _os.scandir(d) as _it:
            for e in _it:
                try:
                    if not e.is_file():
                        continue
                    st = e.stat()
                    parts.append(f"{e.name}:{st.st_mtime:.0f}:{st.st_size}")
                    if st.st_mtime > maxm:
                        maxm = st.st_mtime
                    n += 1
                except OSError:
                    pass
    except OSError:
        pass
    # 确定性指纹：md5(排序后的条目串)，跨进程一致（内置 hash() 会因 PYTHONHASHSEED 变）
    fp = hashlib.md5(str(sorted(parts)).encode("utf-8", "replace")).hexdigest() if parts else ""
    return (maxm, n, fp)


def dir_top_fp(d):
    """目录顶层文件快照的确定性指纹（md5 字符串）。

    供磁盘缓存失效判断复用（main.work_stats 纯原文分支的章节清单缓存）。
    目录不存在/为空返回 ""。
    """
    return _dir_top_snapshot(d)[2]


def last_update_of(aa_dir, out_dir, work_dir=None, refresh=False):
    """作品最新更新时间：aa/ 与 汉化/ 顶层文件 max mtime（下载/翻译产物变化；阅读进度不计）。

    2026-09-05: 磁盘持久缓存（work/last_update.json），指纹 = aa/out 顶层文件级快照
    (mtime+size, md5 确定性哈希)。

    - refresh=False（访存/首页）：**零 stat**，直接读缓存 value（last_update.json 缺失才回退扫）。
      首页逐作品调用不再逐目录 stat 全部文件（profile 实测占首页冷扫 ~12s）。
    - refresh=True（预热/写入后失效重算）：stat 快照 + 写回缓存。warm 后台调用，不阻塞。"""
    cache_path = _os.path.join(work_dir, "last_update.json") if work_dir else ""
    if cache_path and not refresh:
        try:
            if _os.path.exists(cache_path):
                with open(cache_path, encoding="utf-8") as _f:
                    _c = json.load(_f)
                if _c.get("v") == 2:
                    return _c.get("value", 0)
        except Exception:
            pass
        # 缓存缺失 → 回退扫一次并写回（保证首访出值）
        _c2 = {"v": 2, "value": max(_dir_top_snapshot(aa_dir)[0], _dir_top_snapshot(out_dir)[0])}
        try:
            _os.makedirs(work_dir, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as _f:
                json.dump(_c2, _f, ensure_ascii=False)
        except Exception:
            pass
        return _c2["value"]
    # refresh=True 重算（warm 用）：stat 快照 + 写回
    v = max(_dir_top_snapshot(aa_dir)[0], _dir_top_snapshot(out_dir)[0])
    if cache_path:
        try:
            _os.makedirs(work_dir, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as _f:
                json.dump({"v": 2, "value": v}, _f, ensure_ascii=False)
        except Exception:
            pass
    return v


def read_state_with_auto(cur, mode):
    """进度 PUT 自动置「阅读中」的状态机：mode 为汉化类(tr/refine)且 cur != done 时返回 reading，否则保持 cur。"""
    if mode in ('tr', 'refine') and cur != 'done':
        return 'reading'
    return cur


# ---------------------------------------------------------------------------
# 阅读器搜索（2026-10-07，方案 doc/方案归档/工作台阅读器搜索与官方译名地区名方案_2026-10-07.md）
#   跨章、**只搜 text / mixed 行**（AA 艺术行、帖头、空行不搜）；可选中日 / 仅中 / 仅日。
#   实现要点：字节级定位 + 只对命中行查 kind —— 逐行 Python 循环扫 810 章 520MB 要 ~16s，
#   字节 find 只要 1~2s（实测见方案文档）。
# ---------------------------------------------------------------------------
SEARCH_KINDS = ("text", "mixed")
# 行类型码（紧凑索引用；meta 里出现的全部 kind：art/text/header/blank/mixed）
LINE_KIND_CODES = {"text": "t", "mixed": "m", "art": "a", "header": "h", "blank": "b"}
LINE_KIND_BACK = {v: k for k, v in LINE_KIND_CODES.items()}
_LINE_KINDS_CACHE = {}
_SEARCH_CACHE = {}
_SEARCH_CACHE_MAX = 8


def clear_search_cache(work=None):
    """清搜索结果缓存（写盘路径后调用，与 reader_win.invalidate 同处）。"""
    if work is None:
        _SEARCH_CACHE.clear()
        return
    for k in [k for k in _SEARCH_CACHE if k[0] == work]:
        _SEARCH_CACHE.pop(k, None)


def _kinds_path(work_dir):
    return os.path.join(work_dir, "line_kinds.json")


def _meta_dir_sig(work_dir):
    """line_meta_v2 的（最新 mtime, 文件数）——索引失效判据（.json / .json.gz 都计）。"""
    d = os.path.join(work_dir, "line_meta_v2")
    if not os.path.isdir(d):
        return (0.0, 0)
    mx, n = 0.0, 0
    for fn in os.listdir(d):
        if fn.endswith(".json") or fn.endswith(".json.gz"):
            n += 1
            try:
                mx = max(mx, os.path.getmtime(os.path.join(d, fn)))
            except OSError:
                pass
    return (mx, n)


def _build_line_kinds(work_dir):
    """扫 line_meta_v2/*.json[.gz] → {base: 行类型码串}（每行 1 字符，按 txt 行号对齐）。"""
    d = os.path.join(work_dir, "line_meta_v2")
    out = {}
    for base in meta_list(d):
        meta = meta_load(d, base, None)
        if not isinstance(meta, dict):
            continue
        mx = 0
        for k in (meta or {}):
            try:
                mx = max(mx, int(k))
            except (TypeError, ValueError):
                pass
        code = []
        for i in range(1, mx + 1):
            v = (meta or {}).get(str(i)) or {}
            code.append(LINE_KIND_CODES.get(v.get("kind") if isinstance(v, dict) else None, "?"))
        out[base] = "".join(code)
    return out


def line_kinds(work_dir, refresh=False):
    """{base: 行类型码串}（紧凑索引，任务/搜索共用）。

    首用按 line_meta_v2 建、落 work/line_kinds.json；meta 变更（mtime 或文件数变）即重建。
    实测：每次搜索现读 meta 是主要瓶颈（810 章常见词要 26s 里 20s 在 json 解析），
    压缩成每行 1 字符的索引后整库 2~3MB、一次 parse 30ms。
    """
    sig = _meta_dir_sig(work_dir)
    p = _kinds_path(work_dir)
    ent = _LINE_KINDS_CACHE.get(p)
    if ent and not refresh and ent["sig"] == sig:
        return ent["kinds"]
    data = None
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            data = None
    if data and list(data.get("sig") or []) == list(sig) and isinstance(data.get("kinds"), dict):
        _LINE_KINDS_CACHE[p] = {"sig": sig, "kinds": data["kinds"]}
        return data["kinds"]
    kinds = _build_line_kinds(work_dir)
    try:
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"v": 1, "sig": list(sig), "kinds": kinds}, fh, ensure_ascii=False)
    except Exception:
        pass
    _LINE_KINDS_CACHE[p] = {"sig": sig, "kinds": kinds}
    return kinds


def line_kind_of(kinds, base, idx, text=""):
    """取某章某行（1-based）的 kind；索引里没有该章时退回单行启发式。"""
    code = (kinds.get(base) or "")
    if 1 <= idx <= len(code):
        k = LINE_KIND_BACK.get(code[idx - 1])
        if k:
            return k
    return line_type_of(text)


def _scan_bytes(data, qb):
    """字节级找 q → [(1-based 行号, 行文本)]；同一行只算一次。"""
    out, nl, pos, n = [], 0, 0, len(data)
    while True:
        i = data.find(qb, pos)
        if i < 0:
            return out
        nl += data.count(b"\n", pos, i)
        le = data.find(b"\n", i)
        if le < 0:
            le = n
        ls = data.rfind(b"\n", 0, i) + 1
        out.append((nl + 1, data[ls:le].decode("utf-8", "replace").rstrip("\r")))
        pos = le            # 跳到本行末（换行留待下一轮计数）→ 每行最多一条


def _lines_at(data, lns):
    """按（升序）行号列表从字节里取行文本：一次线性遍历，不整章 decode。"""
    out, wi, nl, pos, n = {}, 0, 1, 0, len(data)
    want = sorted(lns)
    while wi < len(want) and pos <= n:
        le = data.find(b"\n", pos)
        if le < 0:
            le = n
        if nl == want[wi]:
            out[nl] = data[pos:le].decode("utf-8", "replace").rstrip("\r")
            while wi < len(want) and want[wi] == nl:
                wi += 1
        pos = le + 1
        nl += 1
    return out


def search_lines(cfg, work, q, scope="both", limit=300, workers=8):
    """阅读器全库搜索（只搜 text/mixed 行）。

    与 work 页那个 `search_work()` 分开：那个是术语/文本小搜索（逐行 read_lines、只搜汉化），
    这个是全库检索（字节级 + 行类型索引，中日/单侧可选）。

    scope: both=中日都搜 / zh=只汉化 / jp=只原文
    返回 {'items':[…], 'total': 命中行数, 'scanned': 章数, 'truncated': bool, 'ms': 毫秒}
    item = {'base','file','tr_file','chapter_no','label','idx'(txt 行号,1-based),'kind','hit','zh','jp'}
    """
    from concurrent.futures import ThreadPoolExecutor
    q = (q or "").strip()
    if not q:
        return {"items": [], "total": 0, "scanned": 0, "truncated": False, "ms": 0}
    ck = (work, q, scope)
    hit_cache = _SEARCH_CACHE.get(ck)
    if hit_cache is not None:
        r = dict(hit_cache)
        r["cached"] = True
        return r
    t0 = time.time()
    mch = merged_chapters(cfg["aa_dir"], cfg["out_dir"], cfg.get("name_style"),
                          os.path.join(cfg["work_dir"], "chapter_order.json"))
    qb = q.encode("utf-8")
    wd = cfg["work_dir"]
    kinds = line_kinds(wd)                                 # 紧凑行类型索引（比逐章读 meta 快两个量级）

    def one(arg):
        no, ch = arg
        base = txt_key(ch["src_file"])
        sides = []
        if scope in ("both", "jp") and ch.get("src_path") and os.path.exists(ch["src_path"]):
            sides.append(("jp", ch["src_path"]))
        if scope in ("both", "zh") and ch.get("has_tr") and ch.get("tr_path") \
                and os.path.exists(ch["tr_path"]):
            sides.append(("zh", ch["tr_path"]))
        blobs, hmap, all_lns = {}, {}, set()
        for side, path in sides:
            try:
                # 2026-10-17: 必须 `txt_bytes`（`.gz` 走解压）——裸 open("rb") 读的是 gzip
                # 压缩字节，搜中日文永远 0 命中（阅读器「搜索不可用」的根因）。
                data = txt_bytes(path)
            except Exception:
                continue
            blobs[side] = data                            # 总是留着：取对侧行要用
            hits = _scan_bytes(data, qb)
            if hits:
                hmap[side] = dict(hits)
                all_lns |= {ln for ln, _ in hits}
        if not all_lns:
            return []
        other = {}
        for side, data in blobs.items():                  # 对侧行文本（一次遍历取回）
            need = [ln for ln in all_lns if ln not in (hmap.get(side) or {})]
            if need:
                other[side] = _lines_at(data, need)
        got = {}
        for ln in sorted(all_lns):
            hit_sides = [s for s in blobs if ln in (hmap.get(s) or {})]
            text = hmap[hit_sides[0]][ln]
            kind = line_kind_of(kinds, base, ln, text)
            if kind not in SEARCH_KINDS:
                continue
            zh_txt = (hmap.get("zh") or {}).get(ln) or other.get("zh", {}).get(ln, "")
            jp_txt = (hmap.get("jp") or {}).get(ln) or other.get("jp", {}).get(ln, "")
            got[ln] = {"base": base, "file": ch["src_file"], "tr_file": ch.get("tr_file") or "",
                       "chapter_no": no + 1, "label": ch.get("title") or base, "idx": ln,
                       "kind": kind, "hit": hit_sides[0] if len(hit_sides) == 1 else "both",
                       "zh": zh_txt, "jp": jp_txt}
        return sorted(got.values(), key=lambda x: x["idx"])

    with ThreadPoolExecutor(max(1, int(workers))) as ex:
        res = list(ex.map(one, list(enumerate(mch))))
    items = [it for lst in res for it in lst]
    total = len(items)
    out = {"items": items[:limit], "total": total, "scanned": len(mch),
           "truncated": total > limit, "ms": int((time.time() - t0) * 1000), "cached": False}
    if len(_SEARCH_CACHE) >= _SEARCH_CACHE_MAX:
        _SEARCH_CACHE.pop(next(iter(_SEARCH_CACHE)), None)
    _SEARCH_CACHE[ck] = out
    return out
