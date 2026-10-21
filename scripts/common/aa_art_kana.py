# -*- coding: utf-8 -*-
"""艺术假名表（按**作品语料**标定，零 API）——判「这串假名是画还是台词」的单一权威。

背景（2026-10-10，P1-1）
    `ART_HIRA`/`ART_KATA`（aa_translate.py）是 2026-08 定死的字符黑名单，换一部作品就失灵：
    本作用 ア/イ/キ/ト/フ/ニ/ヾ 等大量片假名当笔画，写死表把它们全当"决定性台词证据"，
    于是艺术碎片被切段送模型译成中文（实测 `work/edits.jsonl` 1104 条人工修正里
    **185 处**属此类：`ア`→啊、`ノイ`→不要、`キト`→基特、`xぅ笏外`→抱歉）。
    判据改为**按作品语料统计**：

        艺术比(c) = 含 c 且「整行产物 == 原文」的行数 / (该行数 + 该字符在产物里消失的行数)

    "整行产物 == 原文" = 这一步什么都不该做的免费真值（技能 bulk-pipeline-changes §二.16），
    零 API、全库扫一遍秒级 → `tools/calibrate_art_kana.py` 落盘
    `<work>/art_kana_<work>.json`；`--all` 合并全部作品得基线 `scripts/config/aa_art_kana.json`。

判据（并集 + 阈值，实测曲线见 doc/方案归档/艺术行否决与AA字体内嵌_2026-10-08.md 续篇）
    1. 写死表（ART_HIRA/ART_KATA/_SMALL_KANA）∪ 语料表（ratio ≥ `AA_ART_KANA_RATIO`，默认 0.8，
       且观测数 ≥ `AA_ART_KANA_MIN_OBS`，默认 20）→ 该字符算"艺术假名"，**不给台词证据**。
       实测本作：写死表只拦 9.6% 艺术段（误杀 1.9%）；阈值 0.8 → **66.6%**（误杀 2.6%）；
       0.9 → 62.8%（1.7%）；0.7 → 72.8%（4.8%）。
    2. 段级**艺术假名守恒**（`lost_art_kana`，默认开）：表内字符（`AA_ART_KANA_KEEP_RATIO`，
       默认 0.9）在源段里出现、译文里却消失 = 把笔画译成了中文 → 该段回滚照抄/重试。
       豁免：术语表/白名单里的日文专名（`アルタイル`→牛郎星 这类正常翻译）。
       注意与 1 的分工：1 决定"要不要送模型"，2 兜住"送出去被译坏"。

env（全部有默认，`AA_ART_KANA_*` 可回滚）
    `AA_ART_KANA`(表文件路径覆盖) `AA_ART_KANA_RATIO`(0.8) `AA_ART_KANA_MIN_OBS`(20)
    `AA_ART_KANA_KEEP`(1) `AA_ART_KANA_KEEP_RATIO`(0.9)
    `AA_ART_SHORTSEG`(1) 乙「短 kana 笔画片段」`AA_ART_SHORTSEG_KANJI`(0)
    `AA_ART_ROWSEG`(1) 甲「整行是画」
"""
import io
import json
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BUNDLED_TABLE = os.path.join(BASE, "scripts", "config", "aa_art_kana.json")
SCHEMA = 1

# 假名（含半角）；`・`(U+30FB) 与 `ヿ`(U+30FF) 不在内——它们是分隔符/特殊符号，
# 与 2026-08-12 「KANA_RE 排除 ・/ヿ」的口径一致。
KANA_RE = re.compile(r'[\u3041-\u309f\u30a0-\u30f6\u30f8-\u30fe\uff66-\uff9f]')
# 借字笔画：AA 用生僻汉字（笏/芹/仍/竿/丐…）画纹理，与假名同一套统计口径
# （写死黑名单 `ART_KANJI` 只有 100 余字，语料表补上剩下的）。
KANJI_RE = re.compile(r'[\u4e00-\u9fff]')
_HIRA_RE = re.compile(r'[\u3041-\u309f]')
_KATA_RE = re.compile(r'[\u30a0-\u30ff\uff61-\uff9f]')
# 真台词保护（沿用 2026-10-08 `art_line_veto` 的口径）：句末标点/引号 → 不是艺术
_PUNCT_RE = re.compile(r'[\u3001\u3002\uff01\uff1f\u2026\u300c\u300d\u300e\u300f\uff08\uff09]')
# 笑声/喘气类重复，不按"同字重复墙"判艺术（沿用 2026-10-08 art_line_veto 的真台词保护口径）
_LAUGH = set('ハホフふわいうえおあンッ')
# 2026-10-20: AA 画笔里的**记号字符**——与艺术假名连用的框线/点缀（`ア＾`/`＾＼`/`｜|`）。
#   它们不在艺术假名表里，旧规则 ① 要求"全部字符都是艺术假名/借字"→ `ア＾` 漏网，
#   被当 text 段送模型 → 出现 `ア→啊` 的艺术损伤（entry_22630 L2341）。
#   只用于「≤3 字纯记号码」这一条，不影响其他判据（标点/引号仍受 _PUNCT_RE 保护）。
_AA_MARK_CHARS = set("＾^｀'´`｜|／/＼\\＿_‐-―─━＝=～~")

_CACHE = {"path": None, "ratios": {}, "obs": {}, "loaded": False}
_FORCE_PATH = None


def _envflag(name, default=True):
    v = os.environ.get(name)
    if v is None:
        return default
    return v not in ("0", "false", "False", "")


def ratio_th():
    """假名艺术比阈值（`AA_ART_KANA_RATIO` > 0.8）。"""
    try:
        return float(os.environ.get("AA_ART_KANA_RATIO") or 0.8)
    except ValueError:
        return 0.8


def kanji_th():
    """借字汉字艺术比阈值（`AA_ART_KANA_KANJI_RATIO` > 0.9）。

    借字（笏/芹/仍/竿/丐…）在语料里几乎只出现在画里（ratio≈1.0），阈值取高些更安全：
    实测 0.8/0.9/0.95/0.98 四档对"台词误杀"与拦截率几乎无差（169~173 段 / 148 行），
    取 0.9 兼顾（防把中日共用字判成借字）。
    """
    try:
        return float(os.environ.get("AA_ART_KANA_KANJI_RATIO") or 0.9)
    except ValueError:
        return 0.9


def _prod_kana_re():
    """生产口径的假名正则（= `aa_translate.KANA_RE`，权威单一来源）。

    `decisive_kana`（"有台词证据"）必须按**生产口径**判：本模块为了艺术判定把适用
    范围放宽到 `ぁ-ゟ`/`゠-ヶ`（含 `ゝゞ` 等叠字符），而生产
    `KANA_RE` 是 `[ぁ-んァ-ヺー｡-ﾟ]` —— 口径不一致会让 `ゝへ` 这种
    "叠字 + 艺术假名"的绘画片段被当台词抠出来（2026-09-15 事故线复现）。
    """
    try:
        import aa_translate as T
        return T.KANA_RE
    except Exception:
        return KANA_RE


def _prod_hira_re():
    """生产口径的平假名正则（= `aa_translate.HIRA` = `[ぁ-ん]`）。

    本模块的 `_HIRA_RE` 覆盖整个 `ぁ-ゟ`（含 `ゝゞ` 叠字），拿去判
    "行里有≥2 个平假名 → 是台词" 会把 `ゝへ`（叠字 + 艺术假名）这类绘画片段
    判成台词，与生产口径不一致。
    """
    try:
        import aa_translate as T
        return T.HIRA
    except Exception:
        return _HIRA_RE


def override_ratio():
    """表**推翻写死表**的阈值（`AA_ART_KANA_OVERRIDE_RATIO` > 0.15）。

    写死表是 2026-08 别的语料调的，不能因为本作表里 ratio 略低就整体失效
    （实测：`マ` 本作 0.79 → 若让表直接赢，`マっ！` 从"艺术片段"翻成"台词"，
    2026-09-15 定下的两条事故线复现）。只有表给出**明确是台词**的证据
    （ratio ≤ 该值且观测够多）才允许把写死表里的艺术字符翻成台词（`ふ` 本作 0.0）。
    """
    try:
        return float(os.environ.get("AA_ART_KANA_OVERRIDE_RATIO") or 0.15)
    except ValueError:
        return 0.15


def override_obs():
    """推翻写死表所需的最少观测行数（`AA_ART_KANA_OVERRIDE_OBS` > 200）。"""
    try:
        return int(os.environ.get("AA_ART_KANA_OVERRIDE_OBS") or 200)
    except ValueError:
        return 200


def baseline_enabled():
    """是否在没有**本作品**表时使用内置基线表（`AA_ART_KANA_BASELINE=1` 打开；默认关）。

    基线表是全库合并的（别的作品的艺术字符也进来了），对"本作把某字符当台词"的作品
    会过度拦截 → 默认关，缺表时退回写死表（= 原行为），要吃得按作品标定（`calibrate_art_kana.py --work`）。
    """
    return _envflag("AA_ART_KANA_BASELINE", default=False)


def min_obs():
    """最少观测行数（`AA_ART_KANA_MIN_OBS` > 20）——低于此的字符视为未知，不算艺术。"""
    try:
        return int(os.environ.get("AA_ART_KANA_MIN_OBS") or 20)
    except ValueError:
        return 20


def seg_enabled():
    """Rule A（段级判据：这段没可译内容 → 本地照抄）总开关 `AA_ART_KANA_SEG`（默认开）。

    关掉 = 退回"按段落 kind 送模型"的旧行为（艺术笔画碎片会重新进模型）。
    误杀（本作 10 章样本 0.93%：`２８歳`/`０件`/`嘘♥` 这类短标签）若不可接受可按作品关。
    """
    return _envflag("AA_ART_KANA_SEG", default=True)


def keep_enabled():
    """段级艺术假名守恒校验（`AA_ART_KANA_KEEP=0` 关）。"""
    return _envflag("AA_ART_KANA_KEEP", default=True)


def keep_ratio():
    """守恒校验的字符阈值（`AA_ART_KANA_KEEP_RATIO` > 0.9）。"""
    try:
        return float(os.environ.get("AA_ART_KANA_KEEP_RATIO") or 0.9)
    except ValueError:
        return 0.9


_PATH_CACHE = {"key": None, "path": ""}


def table_path(work="", work_dir=""):
    """表文件解析顺序：`AA_ART_KANA` > `<work_dir>/art_kana_<work>.json` > 内置基线 > ""。

    2026-10-21：结果按 (work, work_dir, env, baseline) 缓存——原来每次调用都 `os.listdir(work_dir)`，
    而甲/乙判据是**每行每段**都要表 → 单章上万次目录扫描（实测整部 11 万行跑不完 3 分钟）。
    """
    key = (work, work_dir, os.environ.get("AA_ART_KANA") or "", baseline_enabled())
    if _PATH_CACHE["key"] == key:
        return _PATH_CACHE["path"]
    p = _table_path_uncached(work, work_dir)
    _PATH_CACHE.update({"key": key, "path": p})
    return p


def _table_path_uncached(work="", work_dir=""):
    p = os.environ.get("AA_ART_KANA")
    if p and os.path.exists(p):
        return p
    if work_dir:
        base = work or _work_id_of(work_dir)
        if base:
            cand = os.path.join(work_dir, "art_kana_%s.json" % base)
            if os.path.exists(cand):
                return cand
        for fn in os.listdir(work_dir) if os.path.isdir(work_dir) else []:
            if fn.startswith("art_kana_") and fn.endswith(".json"):
                return os.path.join(work_dir, fn)
    if baseline_enabled() and os.path.exists(BUNDLED_TABLE):
        return BUNDLED_TABLE
    return ""


def _work_id_of(work_dir):
    """`<数据根>/aaManga/<作品目录>/work` → 作品目录名（registry id 未必等于目录名，仅作兜底）。"""
    try:
        return os.path.basename(os.path.dirname(os.path.abspath(work_dir)))
    except Exception:
        return ""


def reset_cache():
    """测试/换作品用：清缓存。"""
    _CACHE.update({"path": None, "ratios": {}, "obs": {}, "loaded": False})
    _PATH_CACHE.update({"key": None, "path": ""})
    _FRAG_CACHE.update({"path": None, "frags": {}, "loaded": False, "key": None})


def set_table_path(path):
    """显式指定表文件（测试/实验用；None = 回到自动解析）。"""
    global _FORCE_PATH
    _FORCE_PATH = path
    reset_cache()


def ratios(work="", work_dir="", path=None):
    """艺术比表 {字符: ratio}（带缓存）。表缺失 → {}（判据退回写死表）。"""
    p = path or _FORCE_PATH or table_path(work=work, work_dir=work_dir)
    if not p:
        return {}
    if _CACHE["loaded"] and _CACHE["path"] == p:
        return _CACHE["ratios"]
    data, obs = {}, {}
    try:
        with io.open(p, encoding="utf-8") as f:
            raw = json.load(f)
        data = raw.get("ratios") or {}
        obs = raw.get("obs") or {}
    except Exception:
        data, obs = {}, {}
    _CACHE.update({"path": p, "ratios": data, "obs": obs, "loaded": True})
    return data


def table_stat(ch, table=None, work="", work_dir=""):
    """单个字符的 (ratio, 观测行数)；表里没有 → (None, 0)。

    表**有**该字符且观测足够时以表为准（本作语料标定 = 最贴近事实），
    表里没有/观测不足才退回写死表——"并集"只在缺表时兜底，因为写死表是别的语料调的：
    实测本作 `ふ`(ratio 0.0 真台词) 与 `ー`(0.798) 都被写死表判成艺术，
    于是 `ふふ、ふふふ` 这类笑声段被误杀（10 章样本 3.6% 误杀降不下来）。
    """
    if table is None:
        table = ratios(work=work, work_dir=work_dir)
    r = table.get(ch)
    if r is None:
        return None, 0
    n = 0
    if _CACHE.get("loaded"):
        o = (_CACHE.get("obs") or {}).get(ch)
        if isinstance(o, (list, tuple)):
            n = sum(int(x) for x in o)
        elif isinstance(o, int):
            n = o
    return r, n


_BUILTIN = None


def builtin_sets():
    """写死表（从 aa_translate 惰性取一次，避免循环 import 与热路径重复 import）。"""
    global _BUILTIN
    if _BUILTIN is None:
        try:
            import aa_translate as T
            _BUILTIN = (set(T.ART_HIRA), set(T.ART_KATA), set(T._SMALL_KANA), set(T.ART_KANJI))
        except Exception:
            _BUILTIN = (set(), set(), set(), set())
    return _BUILTIN


def is_art_kana(ch, table=None, th=None):
    """单个假名字符是否"艺术"：**语料表优先**，表里没有才退回写死表。

    表里有该字符且观测 ≥ `AA_ART_KANA_MIN_OBS` → 以表为准（本作语料标定的最贴近事实）；
    否则（缺表 / 新字符 / 观测太少）→ 写死的 ART_HIRA/ART_KATA/_SMALL_KANA 兜底。
    """
    if not ch or not KANA_RE.match(ch):
        return False
    th = ratio_th() if th is None else th
    r, n = table_stat(ch, table)
    hira, kata, small, _kj = builtin_sets()
    _builtin_art = ch in hira or ch in kata or ch in small
    if r is not None and n >= min_obs():
        try:
            if float(r) >= th:
                return True                     # 表说艺术 → 艺术（补写死表缺的 ア/イ/キ…）
            if _builtin_art and not (float(r) <= override_ratio() and n >= override_obs()):
                return True                     # 写死表说艺术、表证据不够硬 → 以写死表为准
            return False
        except (TypeError, ValueError):
            pass
    return _builtin_art


def decisive_kana(text, table=None):
    """文本里的"决定性假名"（非艺术、非小写）——有它才算有台词证据。"""
    if not text:
        return []
    if table is None:
        table = ratios()
    out = []
    kre = _prod_kana_re()
    for ch in text:
        if not kre.match(ch):
            continue
        if is_art_kana(ch, table):
            continue
        out.append(ch)
    return out


def art_kana_lost(src, cand, table=None, th=None):
    """源段里的"艺术假名"在译文里消失的字符列表（段级守恒校验，Rule B）。

    只数 `AA_ART_KANA_KEEP_RATIO`（默认 0.9）以上的字符——0.8~0.9 之间的字符
    （本作 レ/ユ/ヨ/フ 等）在正常译文里也会消失，纳入会误杀。
    """
    if not src or not cand:
        return []
    if table is None:
        table = ratios()
    th = keep_ratio() if th is None else th
    lost = []
    for ch in src:
        if not KANA_RE.match(ch):
            continue
        r = table.get(ch)
        if r is None:
            continue
        try:
            if float(r) < th:
                continue
        except (TypeError, ValueError):
            continue
        if cand.count(ch) < src.count(ch):
            lost.append(ch)
    return lost


def is_art_kanji(ch, table=None, th=None):
    """单个汉字是否"借字笔画"：**语料表优先**，表里没有才退回写死的 `ART_KANJI`。"""
    if not ch or not KANJI_RE.match(ch):
        return False
    th = kanji_th() if th is None else th
    r, n = table_stat(ch, table)
    _h, _k, _s, art_kj = builtin_sets()
    _builtin_art = ch in art_kj
    if r is not None and n >= min_obs():
        try:
            if float(r) >= th:
                return True                     # 借字（笏/仍/芹…）补进表
            if _builtin_art and not (float(r) <= override_ratio() and n >= override_obs()):
                return True
            return False
        except (TypeError, ValueError):
            pass
    return _builtin_art


def real_kanji(text, table=None):
    """文本里的"非艺术汉字"列表。"""
    if not text:
        return []
    if table is None:
        table = ratios()
    return [c for c in text if KANJI_RE.match(c) and not is_art_kanji(c, table)]


def sfx_like(body):
    """半角假名拟声段（项目约定：sfx 要译并加 `span.sfx`）→ 段级守恒校验豁免。"""
    b = body or ""
    hw = sum(1 for c in b if '\uff61' <= c <= '\uff9f')
    if not hw:
        return False
    rest = sum(1 for c in b if KANA_RE.match(c) and not ('\uff61' <= c <= '\uff9f'))
    return hw >= rest


def seg_is_art(body, table=None):
    """段是否"没有可译内容"（艺术片段）——Rule A（选段层：命中则本地照抄、不进模型）。

    判据（阈值/曲线由本作语料标定，见模块注释与 doc/方案归档/段级艺术判据_2026-10-10.md）：
      ① ≤3 字且每个字符都在艺术表（kana/借字）里 → 艺术（`ア`/`ノ`/`ニニ` 型）
      ② <2 字或显示宽 <4 → 不是艺术（太短无从判断）
      ③ 含 ≥2 平假名（**生产口径** HIRA）→ 不是艺术（`ふふ、ふふふ`/`ふぅーーっ！！` 型笑声/感叹）
      ④ 含句末标点或引号（、。！？…「」『』（））→ 不是艺术（沿用 art_line_veto 的真台词保护口径）
      ⑤ ≤4 字、无决定性假名、非艺术汉字 ≤1、且不像半角假名拟声 → 艺术（`＜米フ`/`ア〃`/`笏外` 型碎片）
      ⑥ 同字重复墙 → 艺术（重复字符艺术比 ≥0.5，或片假名/半角假名连 4+：`エエエエエ`/`ニニニ`；
         笑声（ハ/ホ/フ/ふ…）与平假名重复由 ③ 先挡）
      ⑦ 有决定性假名（非艺术假名）→ 不是艺术
      ⑧ 有 ≥2 个非艺术汉字且无相邻重复 → 不是艺术（`流石私の娘…` 型）
      ⑨ 其余 → 艺术

    实测（本作 10 章样本；基线 = 改前口径送模型段 10,382）：
      送模型段 10,382 → 8,047（−22.5%）；不再送的 2,336 段里 2,236（95.7%）本来就是画、
      97（占基线 0.93%）是误杀（`２８歳`/`０件`/`嘘♥` 这类短标签/拟声，按作品可关）；
      181 条人工修正的艺术损伤行覆盖 152（84%，其中 Rule B 段级回滚 17）。
      开关 `AA_ART_KANA_SEG=0` 退回"按段落 kind 送模型"的旧行为。整条曲线见方案文档。
    """
    b = (body or "").strip(" \u3000\u200a\u2002\u2005\u2006\u2009")
    if not b:
        return False
    if not seg_enabled():
        return False
    if table is None:
        table = ratios()
    if len(b) <= 3 and all(is_art_kana(c, table) or is_art_kanji(c, table)
                           or c in _AA_MARK_CHARS for c in b):
        return True                       # ① ≤3 字纯艺术串（`ア`/`ノ`/`ニニ`/`ア＾`）
    # ①-bis（2026-10-20）：「艺术假名 + AA 记号」混排的短残片（`ア＾`）——`ア` 不在艺术假名表里
    #   （它在真片假名词里太常见），纯假名判据必然漏网 → 补一条「带 ≥1 个 AA 记号、无平假名、
    #   无汉字、≤3 字」判艺术。真台词（`えっ？`/`ありがとう`/含引号标点）都不满足。
    if (len(b) <= 3 and any(c in _AA_MARK_CHARS for c in b)
            and not _HIRA_RE.search(b) and not KANJI_RE.search(b)
            and len(_KATA_RE.findall(b)) <= 1):
        return True
    if len(b) < 2 or _disp(b) < 4:
        return False
    if len(_prod_hira_re().findall(b)) >= 2:
        return False                      # ③ 笑声/感叹类短台词（`ふふ、ふふふ`）
    if _PUNCT_RE.search(b):
        return False                      # ④ 句末标点/引号 = 真台词保护
    if len(b) <= 4 and not decisive_kana(b, table) and len(real_kanji(b, table)) <= 1 \
            and not sfx_like(b):
        return True                       # ② 短碎片（`＜米フ`/`ア〃`/`笏外`）
    _m = re.search(r"(.)\1{2,}", b)
    if _m and _m.group(1) not in _LAUGH:
        _c = _m.group(1)
        _r = table.get(_c)
        # ⑧ 同字重复墙：艺术比够高的直接判艺术；片假名/半角假名连 4+ 次也算
        #    （`エエエエエ`/`ニニニ`/`ﾆﾆﾆﾆ`；`ハハハ` 笑声与平假名重复由 ③/_LAUGH 保护）
        if _r is not None and _r >= 0.5:
            return True
        if not _prod_hira_re().match(_c) and len(_m.group(0)) >= 4:
            return True
    if decisive_kana(b, table):
        return False
    kj = real_kanji(b, table)
    if len(kj) >= 2 and not re.search(r"(.)\1", b):
        return False
    return True


# ---------------------------------------------------------------------------
# 甲/乙：**短 kana 笔画片段**（2026-10-21）
#
# 背景（資格世界の裏街道 edits.jsonl 复核）：121 处「艺术笔画被译成中文」全是 1~2 个汉字
# 吃掉 1~4 个假名（んう→嗯唔、ぅて→唔、ヒり→哔哩、ア→啊、んら→嗯啦）。实测这些段全被
# Rule A 放过：③「≥2 平假名 = 台词」96 段、②「太短放行」44 段、⑦「有决定性假名」21 段
# （ヒ/ト/ア 不在写死表里）。**字符级语料表救不了这一类**——本作 ん=0.50 / う=0.03 /
# て=0.006 / ア=0.24，这些字符在真台词里同样常见，粒度不对。
#
# 两条新判据都走**结构与上下文**，不猜字符语义：
#   乙 `seg_short_art`：2~4 字纯 kana、无标点/数字、平假名 ≤2、实义汉字 ≤阈值、
#      左或右邻段是 art、本行无系统标记（※【】『』「」♪/数字）→ 是画
#   甲 `row_is_art`  ：整行画密（艺术字符 ≥45%）+ 每个 text 段都符合乙的形状 + 无系统标记
#      → 整行本地照抄（这类行没有任何可译内容，送模型只会改画面）
#
# 实测（本作 113 章；真值 = 用户 108 条「中文字→假名」修正行）：乙拦 377 行、甲拦 77 行，
# 合起来覆盖 33 行（31%）；被拦的非损伤段 65 个里约 85% 是尚未修的同款损伤
# （∠ア17 / トゝ沙9 / シノ6 / ア゛4 / ィぅて芹4 …），真台词误判约 10 段（`これ`/`サンも`/
# `お前と` 型，被误判时结果是"留日文"，会被漏译体检捞回——方向安全）。
# 开关 `AA_ART_SHORTSEG=0` / `AA_ART_ROWSEG=0`。
_SYS_MARK_RE = re.compile('[※【】『』「」＊♪]|[0-9０-９]')
_SEG_BLOCK_RE = re.compile('[、。！？…「」『』（）]|[0-9A-Za-z０-９Ａ-Ｚａ-ｚ]')
# 乙用的宽松口径：句读/数字/两个以上 ASCII 才挡——单个 ASCII 字母在本作是笔画
# （`んぅr'` 的 r、`‐t゛ア` 的 t），按 `_SEG_BLOCK_RE` 会连真笔画一起放过。
_SEG_PUNCT_RE = re.compile('[、。！？…「」『』（）]')
_SEG_DIGIT_RE = re.compile('[0-9０-９]')
_SEG_ASCII2_RE = re.compile('[A-Za-zＡ-Ｚａ-ｚ]{2,}')
ROW_DENSE_RATIO = 0.45
_ARTSET = {"set": None}


def shortseg_enabled():
    """乙「短 kana 片段判画」开关（`AA_ART_SHORTSEG=0` 关）。"""
    return _envflag("AA_ART_SHORTSEG", default=True)


def rowseg_enabled():
    """甲「整行是画」开关（`AA_ART_ROWSEG=0` 关）。"""
    return _envflag("AA_ART_ROWSEG", default=True)


def shortseg_kanji_max():
    """甲/乙 允许的**实义汉字**上限（`AA_ART_SHORTSEG_KANJI`，默认 0）。

    0 = 段内不许有实义汉字（`お前と`/`様は`/`決める` 这类真台词因此不会被误判成画）；
    调到 1 可多拦住 `トゝ沙`/`‐く公`/`ら然`（本作实测 +6 条损伤行），代价是
    `お前と`/`お前に`/`様は`/`今のお` 4 处真台词会被留日文。
    """
    try:
        return int(os.environ.get("AA_ART_SHORTSEG_KANJI") or 0)
    except ValueError:
        return 0


def art_charset():
    """艺术字符集（写死表 ∪ 借字 ∪ 笔画记号）——只用于甲的行级"画密度"。"""
    if _ARTSET["set"] is None:
        hira, kata, small, kj = builtin_sets()
        try:
            import aa_translate as T
            stroke = set(T._ART_STROKE_CHARS)
        except Exception:
            stroke = set()
        _ARTSET["set"] = set(hira) | set(kata) | set(small) | set(kj) | stroke
    return _ARTSET["set"]


def _seg_norm(b):
    """去全部空白（含 U+3000 等 Unicode 空格）——`str.split()` 无参即为全空白口径。"""
    return "".join((b or "").split())


def seg_short_art(body, table=None, line=None, segs=None, si=None):
    """乙：短 kana 笔画片段（2~4 字）→ 是画，不该送模型。

    `segs`/`si` 给了就要求「左或右邻段是 art」；只给 segs 不给 si 无法查邻段 → 返回 False
    （保守：没有上下文就不拦）。`line` 用于排除系统行（安価/骰子/※ 提示行必须翻译）。
    """
    if not shortseg_enabled():
        return False
    b = (body or "").strip()
    nb = _seg_norm(b)
    if not (2 <= len(nb) <= 4):
        return False
    if _SEG_PUNCT_RE.search(b) or _SEG_DIGIT_RE.search(b) or _SEG_ASCII2_RE.search(b):
        return False
    if line is not None:
        # 系统标记只看**段附近**（±6 字）：`ヒり`/`トゝ沙`/`んうヾ` 常出现在「画 + 『台词』」
        # 的混合行里，按整行判会把真艺术放过（实测本作 15 行损伤栽在这）。而 `では` 这类
        # 骰子安価短句的标记就在它旁边，窗口判据仍能挡住。
        if segs is not None and si is not None and 0 <= si < len(segs):
            _a = int(segs[si].get("start", 0) or 0)
            _b = int(segs[si].get("end", 0) or 0)
            if _SYS_MARK_RE.search(line[max(0, _a - 6):_b + 6]):
                return False
        elif _SYS_MARK_RE.search(line):
            return False
    if table is None:
        table = ratios()
    if len(real_kanji(b, table)) > shortseg_kanji_max():
        return False
    if len(_prod_hira_re().findall(b)) > 2:
        return False
    if segs is not None:
        if si is None or si < 0 or si >= len(segs):
            return False
        left = segs[si - 1].get("kind") if si > 0 else None
        right = segs[si + 1].get("kind") if si + 1 < len(segs) else None
        if left != "art" and right != "art":
            return False
    return True


def row_is_art(line, segs, table=None):
    """甲：整行就是画（行内没有任何可译内容）→ 本地照抄，别送模型。

    与 `seg_short_art` 的分工：乙管"行里有一小段笔画、其余是别的"，甲管"整行都是笔画"。
    甲比乙严——要求画密度 ≥45%、每个 text 段都符合乙的形状、且无系统标记/无标点数字。
    """
    if not rowseg_enabled() or not line or not segs:
        return False
    if _SYS_MARK_RE.search(line):
        return False
    aset = art_charset()
    # 密度按**非空白**字符算：AA 行里 U+3000 占位极多，按整行长算会把画密摊薄
    body = [c for c in line if not c.isspace()]
    if not body:
        return False
    art = sum(1 for c in body if c in aset)
    if art < max(6, len(body) * ROW_DENSE_RATIO):
        return False
    if table is None:
        table = ratios()
    texts = [sg for sg in segs if sg.get("kind") == "text"]
    if not texts:
        return False
    short_kana = False
    for sg in texts:
        b = line[sg.get("start", 0):sg.get("end", 0)].strip()
        if _SEG_BLOCK_RE.search(b):
            return False                      # 标点/数字 = 真台词（動機？/はい。/おや？）
        if len(real_kanji(b, table)) > shortseg_kanji_max():
            return False
        if len(_prod_hira_re().findall(b)) > 2:
            return False
        if len(_seg_norm(b)) > 4:
            return False
        if KANA_RE.search(b):
            short_kana = True
    return short_kana


def row_all_art(line, segs, table=None):
    """该行**所有 text 段都判画**（Rule A ∪ 乙）→ 整行没有可译内容（2026-10-21）。

    补的是"有 segments 但没有一段可发"的行：旧实现只看 `_sendable_text_segs` 是否为空，
    空了就把该行退回**整行协议**——模型于是把画一起改写（`ﾉﾉ斗ぅて`→唔 这类事故就是这么来的）。
    注意它比 `row_is_art` 宽（不要求画密度/段长），但只看"每段都判画"，不动低置信段：
    低置信段（conf<0.65）不在此列，仍由 `mixed_seg_rule_enabled` 那条老路去补。
    """
    if not rowseg_enabled() or not line or not segs:
        return False
    if table is None:
        table = ratios()
    texts = [si for si, sg in enumerate(segs) if sg.get("kind") == "text"]
    if not texts:
        return False
    return all(seg_is_art_ctx(line, segs, si, table) for si in texts)


# ---------------------------------------------------------------------------
# 丙：作品级「艺术片段表」（2026-10-21）
#
# 甲/乙 走结构（画密 + 邻段 + 长度），但**同一幅画的固定笔画串**它们有时够不到
# （邻段被 meta 切成 text、行里有真台词、段长 5 字…）。丙补这一层：
#   表 `work/art_frag_<work>.json`，key = **去空白的短片段**（2~6 字，纯 kana）
#   数据源 = ① `edits.jsonl` 里「中文字→假名」的还原 span（用户亲口说这是画 = 真值）
#            ② 语料：整行产物==原文的行里出现的短段（art 计数，弱证据）
#   命中即本地照抄（不送模型）。表缺失/未命中 → 判据退回甲/乙，行为不变。
# 只按**作品**生效（不同作品各有各的画法），CLI：`tools/calibrate_art_frag.py --work <id>`。
FRAG_MIN_LEN = 2
FRAG_MAX_LEN = 6
FRAG_MIN_ART = 3          # 语料弱证据：整行未动行里至少出现 3 次才进表
FRAG_RATIO_MIN = 0.5      # 且 art/(art+lost) ≥ 0.5
# 常用日语词（真台词）——哪怕语料里恰好常出现在画里也不能拦（实测 `これ`/`には`/`うーん` 误伤）
_FRAG_STOP = frozenset([
    "これ", "それ", "あれ", "どれ", "ここ", "そこ", "どこ", "には", "では", "ので", "から",
    "まで", "より", "など", "って", "です", "ます", "うん", "ええ", "はい", "いい", "そう",
    "どう", "ある", "いる", "こと", "もの", "とき", "ため", "でも", "しか", "ほど", "まま",
    "よう", "らしい", "ください", "ません", "でした", "じゃ", "あと", "いま", "また",
])
_FRAG_CACHE = {"path": None, "frags": {}, "loaded": False}


def frag_enabled():
    """丙「艺术片段表」开关（`AA_ART_FRAG=0` 关）。"""
    return _envflag("AA_ART_FRAG", default=True)


def frag_path(work="", work_dir=""):
    """表文件：`AA_ART_FRAG_FILE` > `<work_dir>/art_frag_<work>.json` > ""。"""
    p = os.environ.get("AA_ART_FRAG_FILE")
    if p and os.path.exists(p):
        return p
    if work_dir:
        base = work or _work_id_of(work_dir)
        if base:
            cand = os.path.join(work_dir, "art_frag_%s.json" % base)
            if os.path.exists(cand):
                return cand
    return ""


def edits_path(work_dir):
    return os.path.join(work_dir, "edits.jsonl") if work_dir else ""


def restore_spans_from_edits(path):
    """从 `edits.jsonl` 取「中文字 → 假名」的还原 span（用户亲手改回原文 = 画）→ [片段文本]。"""
    if not path or not os.path.exists(path):
        return []
    import difflib
    cjk = re.compile("[一-鿿]")
    out = []
    try:
        with io.open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                before, after = r.get("before") or "", r.get("after") or ""
                if not before or not after:
                    continue
                for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
                        None, before, after, autojunk=False).get_opcodes():
                    if tag == "equal":
                        continue
                    old, new = before[i1:i2], after[j1:j2]
                    if cjk.search(old) and not cjk.search(new) and KANA_RE.search(new):
                        out.append(new)
    except Exception:
        return out
    return out


def frag_auto_refresh():
    """`AA_ART_FRAG_AUTO=0` 关「用 edits.jsonl 现挖补表」（默认开）。"""
    return _envflag("AA_ART_FRAG_AUTO", default=True)


def frags(work="", work_dir="", path=None):
    """艺术片段表 {frag: {src, edits, art, lost, kept}}（带缓存）。缺表 → {}。

    自更新（2026-10-21）：`work/edits.jsonl` 比表新时，把里面「中文字→假名」的还原 span
    现挖补进表（零 API、毫秒级）——用户每修一处艺术损伤，下一章就不会再犯。
    `AA_ART_FRAG_AUTO=0` 关。
    """
    p = path or frag_path(work=work, work_dir=work_dir)
    ep = edits_path(work_dir)
    emt = os.path.getmtime(ep) if (ep and os.path.exists(ep) and frag_auto_refresh()) else 0
    ck = (p, emt)
    if _FRAG_CACHE["loaded"] and _FRAG_CACHE.get("key") == ck:
        return _FRAG_CACHE["frags"]
    data = {}
    if p:
        try:
            with io.open(p, encoding="utf-8") as f:
                raw = json.load(f)
            data = dict(raw.get("frags") or {})
        except Exception:
            data = {}
    if emt:
        fresh = mine_frags([], restore_spans_from_edits(ep))
        for k, v in fresh.items():
            if k not in data:
                data[k] = v
    _FRAG_CACHE.update({"path": p, "frags": data, "loaded": True, "key": ck})
    return data


def reset_frag_cache():
    _FRAG_CACHE.update({"path": None, "frags": {}, "loaded": False, "key": None})


def _frag_norm(b):
    return "".join((b or "").split())


def frag_shape_ok(text):
    """片段/段是否"像一个纯 kana 的短笔画"（挖掘与命中两侧共用同一口径）。"""
    b = (text or "").strip()
    nb = _frag_norm(b)
    if not (FRAG_MIN_LEN <= len(nb) <= FRAG_MAX_LEN):
        return False
    if not KANA_RE.search(nb):
        return False
    if _SEG_PUNCT_RE.search(b) or _SEG_DIGIT_RE.search(b) or _SEG_ASCII2_RE.search(b):
        return False
    if KANA_RE.sub("", nb):            # 除假名外还有别的字符（汉字/记号）→ 不是纯 kana 片段
        return False
    return True


def seg_frag_art(body, frag_table=None, line=None, segs=None):
    """丙：段是否命中作品的艺术片段表（命中 → 是画）。"""
    if not frag_enabled():
        return False
    b = (body or "").strip()
    nb = _frag_norm(b)
    if not (FRAG_MIN_LEN <= len(nb) <= FRAG_MAX_LEN):
        return False
    if not KANA_RE.search(nb):
        return False
    if _SEG_PUNCT_RE.search(b) or _SEG_DIGIT_RE.search(b) or _SEG_ASCII2_RE.search(b):
        return False
    if frag_table is None:
        frag_table = frags()
    if not frag_table:
        return False
    if real_kanji(b):
        return False
    if segs is not None and not any(sg.get("kind") == "art" for sg in segs):
        return False             # 行里没有 art 段 = 不是画行，丙不表态
    hit = None
    if nb in frag_table:
        hit = nb
    else:
        for f in frag_table:
            if len(f) >= 3 and f in nb:      # 短片段被包在更长段里（`ィぅて芹` ⊃ `ィぅて`）
                hit = f
                break
    if not hit or hit in _FRAG_STOP or nb in _FRAG_STOP:
        return False
    return True


def mine_frags(rows, edits_texts=(), min_art=FRAG_MIN_ART, ratio_min=FRAG_RATIO_MIN):
    """挖艺术片段表。`rows` = [(src_line, tr_line, segs_or_None)]；`edits_texts` = 用户还原的片段。

    返回 {frag: {"src": "edits"/"corpus"/"both", "edits": n, "art": n, "lost": n, "kept": n}}。
    口径：edits 命中（真值）直接进表；语料侧要 art ≥ min_art 且 art/(art+lost) ≥ ratio_min。
    """
    out = {}
    for t in edits_texts:
        if not frag_shape_ok(t):
            continue
        nb = _frag_norm(t)
        rec = out.setdefault(nb, {"src": "", "edits": 0, "art": 0, "lost": 0, "kept": 0})
        rec["edits"] += 1
    for src, tr, segs in rows:
        if not segs:
            continue
        for sg in segs:
            if sg.get("kind") != "text":
                continue
            raw = src[sg.get("start", 0):sg.get("end", 0)]
            if not frag_shape_ok(raw):
                continue
            nb = _frag_norm(raw)
            rec = out.setdefault(nb, {"src": "", "edits": 0, "art": 0, "lost": 0, "kept": 0})
            if src == tr:
                rec["art"] += 1
            elif nb in tr:
                rec["kept"] += 1
            else:
                rec["lost"] += 1
    keep = {}
    for frag, rec in out.items():
        if frag in _FRAG_STOP:
            continue
        a, l = rec["art"], rec["lost"]
        corpus_ok = a >= min_art and (a / float(a + l) >= ratio_min if (a + l) else False)
        edits_ok = rec["edits"] > 0
        if not (corpus_ok or edits_ok):
            continue
        rec["src"] = "both" if (corpus_ok and edits_ok) else ("edits" if edits_ok else "corpus")
        keep[frag] = rec
    return keep


def line_is_art(line, table=None):
    """甲-lite：**没有 segments 的行**整行就是画（2026-10-21）。

    与 `row_is_art` 同一口径，但只看行本身（meta 没切出段的行拿不到"对话区"）：
    无系统标记 / 无句读 / 无数字 + 艺术字符占比 ≥45% + 零实义汉字 + 假名 1~4 个。
    实测本作（113 章）：命中 20,231 行，其中"当前还会被送模型"的 153 行全是没有可译内容的
    纯笔画行（`ﾆﾐﾉ`×34 / `じトﾄﾉ`×25 / `ﾛﾛ､`×22 …），唯一产物含中文的那行本身就是艺术损伤
    （`ﾆﾁｬｱ`→喵哈哈）。
    """
    if not rowseg_enabled() or not line:
        return False
    if _SYS_MARK_RE.search(line) or _SEG_PUNCT_RE.search(line) or _SEG_DIGIT_RE.search(line):
        return False
    aset = art_charset()
    body = [c for c in line if not c.isspace()]
    if not body:
        return False
    art = sum(1 for c in body if c in aset)
    if art < max(6, len(body) * ROW_DENSE_RATIO):
        return False
    if table is None:
        table = ratios()
    if real_kanji(line, table):
        return False
    kana = [c for c in line if KANA_RE.match(c)]
    if not kana or len(kana) > 4:
        return False
    return True


def line_frag_art(line, frag_table=None):
    """丙-行级：整行的**每个假名都落在艺术片段表的匹配区间里** → 整行是画。

    比"任意命中"严得多：台词行里的助词/活用一定是表外假名 → 不会命中。专治
    `ﾉﾉ斗ぅて` / `ﾐｬｯネ` / `ニム` 这类**没有 meta 段**的整行协议行（甲/乙 够不到，
    模型会把整行重写）。实测（本作 LOCO 留一章）：拦 53 行、覆盖 16/108 损伤行，
    被拦的非损伤行全是尚未修的同款损伤（`喵`/`呜`/`战斗`），零真台词误伤。
    """
    if not frag_enabled() or not line:
        return False
    if _SYS_MARK_RE.search(line) or _SEG_PUNCT_RE.search(line) or _SEG_DIGIT_RE.search(line):
        return False
    body = [c for c in line if not c.isspace()]
    if not body:
        return False
    aset = art_charset()
    art = sum(1 for c in body if c in aset)
    if art < max(6, len(body) * ROW_DENSE_RATIO):
        return False
    kana_pos = [i for i, c in enumerate(line) if KANA_RE.match(c)]
    if not kana_pos:
        return False
    if frag_table is None:
        frag_table = frags()
    if not frag_table:
        return False
    covered = set()
    have = set(line)
    for frag in frag_table:
        if len(frag) < FRAG_MIN_LEN or frag in _FRAG_STOP or frag[0] not in have:
            continue                      # 首字符不在行内 → 必然不命中（省 28×find/行）
        start = line.find(frag)
        while start >= 0:
            covered.update(range(start, start + len(frag)))
            start = line.find(frag, start + 1)
    return all(i in covered for i in kana_pos)


def seg_is_art_ctx(line, segs, si, table=None, frag_table=None):
    """Rule A ∪ 乙（短 kana 片段）∪ 丙（作品片段表）——主链/校对共用入口。"""
    if not segs or si < 0 or si >= len(segs):
        return False
    if table is None:
        table = ratios()
    sg = segs[si]
    body = (line or "")[sg.get("start", 0):sg.get("end", 0)]
    if seg_is_art(body, table):
        return True
    if seg_short_art(body, table, line=line, segs=segs, si=si):
        return True
    return seg_frag_art(body, frag_table, line=line, segs=segs)


def art_keep_violation(src, cand, table=None):
    """段级**艺术假名守恒**违规检测 —— Rule B（送出去被译坏时的回滚判据）。

    返回原因串（'' = 无违规）。保守口径，只在"这段不像正常台词/拟声词"时才判，
    实测（本作 10 章 4,746 个已译段）整口径会误杀 329 段（sfx/片假名台词），
    收紧后为下列三条门禁：

      门禁 ① 源段有平假名 → 放过（正常台词，`ﾉｰｶｯﾄ`→无删减 这类同形误判由此排除）
      门禁 ② 源段是半角假名拟声（`sfx_like`）→ 放过（`ｸﾝｸﾝｸﾝ`→嗅嗅嗅 是项目约定的 sfx 翻译）
      判据 A 艺术假名（≥`AA_ART_KANA_KEEP_RATIO`，默认 0.9）**同字重复串**（≥2 次）在译文里丢失
             （`ニニニニ`→妮妮妮、`ノノ`→诺诺、`ﾆﾆ`→嗯嗯 型）
      判据 B 艺术假名丢失 **且** 译文引入了源段没有的汉字（`―キト`→基特 型）
    """
    if not src or not cand or src == cand:
        return ""
    if not keep_enabled():
        return ""
    if table is None:
        table = ratios()
    if re.search(r'[\u3041-\u309f]', src):
        return ""                       # 门禁①：有平假名 = 正常台词
    if sfx_like(src):
        return ""                       # 门禁②：半角假名拟声（要译 + 加 sfx 样式）
    th = keep_ratio()
    rep_lost, single_lost = [], []
    seen = set()
    for ch in src:
        if ch in seen or not KANA_RE.match(ch):
            continue
        seen.add(ch)
        r = table.get(ch)
        if r is None:
            continue
        try:
            if float(r) < th:
                continue
        except (TypeError, ValueError):
            continue
        n_src, n_cand = src.count(ch), cand.count(ch)
        if n_cand >= n_src:
            continue
        (rep_lost if n_src >= 2 else single_lost).append(ch)
    if rep_lost:
        return "艺术重复串丢失:" + "".join(rep_lost)
    if single_lost:
        new_kj = [c for c in real_kanji(cand, table) if c not in src]
        if new_kj:
            return "艺术假名丢失且引入汉字:" + "".join(single_lost)
    return ""


_KANJI_RE = re.compile(r'[\u4e00-\u9fff]')


def _disp(s):
    try:
        import aa_translate as T
        return T.disp_width(s)
    except Exception:
        return sum(1 if ord(c) < 0x100 else 2 for c in s)


# ---------------------------------------------------------------------------
# 标定（生产者）：零 API，扫 aa/ ↔ 汉化/ 逐行统计
# ---------------------------------------------------------------------------
def _kana_chars(line):
    return set(c for c in line if KANA_RE.match(c) or KANJI_RE.match(c))


def calibrate_pairs(pairs, min_obs_rows=20):
    """pairs = [(src_lines, tr_lines), ...] → (ratios, obs, rows)。

    obs[ch] = [art_rows, kept_rows, lost_rows]（三计数）：
      · art  = 整行产物 == 原文（这一步什么都不该做）→ 该行里所有字符计 art
      · kept = 行被改过、且该字符仍出现在产物里（共同字符/保留）→ 仅作诊断，**不进 ratio 分母**
      · lost = 行被改过、该字符消失（被译走）
    ratio = art / (art + lost)：把"产物里保留"计入分母会把中日共用字（米/木/人/七…）
    整体拉出艺术表（实测 笏 0.797、艺术段拦截 24,880 → 75），故只看这两个计数。
    行口径：`open(...).read().split('\n')`（与主链一致）。
    """
    art, kept, lost = {}, {}, {}
    rows = 0
    for src, tr in pairs:
        for i, s in enumerate(src):
            if s.startswith("#"):
                continue
            rows += 1
            h = tr[i] if i < len(tr) else ""
            chars = _kana_chars(s)
            if not chars:
                continue
            if s == h:
                for c in chars:
                    art[c] = art.get(c, 0) + 1
            else:
                for c in chars:
                    if c in h:
                        kept[c] = kept.get(c, 0) + 1
                    else:
                        lost[c] = lost.get(c, 0) + 1
    t = sorted(set(art) | set(kept) | set(lost))
    obs = {c: [art.get(c, 0), kept.get(c, 0), lost.get(c, 0)] for c in t}
    ratios = {}
    for c in t:
        a, _k, l = obs[c]
        if (a + l) < 1:
            continue
        # 口径 = art/(art+lost)（**不是**把 kept 计进分母）：中日共用字（米/木/人/七…）
        # 在译文里"保留下来"是因为它们本来也是中文，计入分母会把借字全体拉回非艺术
        # （实测：口径含 kept 时 笏 0.797 掉到阈值下、艺术段拦截 24,880 → 75）。
        ratios[c] = round(a / float(a + l), 4)
    return ratios, obs, rows
