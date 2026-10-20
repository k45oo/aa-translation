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


def table_path(work="", work_dir=""):
    """表文件解析顺序：`AA_ART_KANA` > `<work_dir>/art_kana_<work>.json` > 内置基线 > ""。"""
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
