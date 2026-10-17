# -*- coding: utf-8 -*-
r"""宝可梦作品固定内容表 —— 翻译前替换固定行并跳过（省 token）。

方案: doc/方案归档/宝可梦固定内容表方案_2026-09-22.md（grilling 12 问共识，D1–D12）
数据源: doc/aa作品相关/ポケモンAA术语wiki/术语表.json（AA 原创术语 3132 条，含日文 effect）

## 为什么这样接（D3）
"跳过"不需要改 aa_translate——它读 ckpt 的 `done` 建 todo，`done` 里的行不进块、
不送模型，落盘时 `out = [done.get(idx, l) …]` 直接出译文（aa_translate.py:1943-1948 /
1982 / 2149）。本模块只负责**把命中行写进 `work/<base>.txt.ckpt.json` 的 done**，
共用文件零改动。

## 两条轨（D5）
- **struct**（结构化）：`[前缀]『名称』…说明` → 名称查 wiki（jp→zh）、说明归一化后
  与 wiki `effect` 逐字一致才命中；重组时**复用源前缀**并 pad 到**源说明起始列**，
  最后补到源行宽（text 行允许超宽，短宽会被 scan_width 报）。
- **whole**（整行）：去空白归一化整行 key 完全一致才替换（零误伤），宽度不同本地 pad。

优先级：`manual` > `derived`；struct > whole（同表内）。

## 三闸（D9）
① 自动硬拦：复用 aa_tm.guard 四重护栏（宽度/残留假名/术语表一致/art 列位）
② 自动报告：与已定稿译文比对 → `conflicts`（不自动改）
③ 人工抽检：`--audit`（前 N 条高频 + conflicts + auto_fail）

## 存储
- 表: `<work_dir>/pokemon_fixed_<work>.json`（knowledge asset，git add -f 入库）
- 命中痕迹: `<work_dir>/fixed_hits/<base>.json`
- 本流程定稿章清单: `<work_dir>/pkmn_chapters.json`（回流只收这些章 → 旧 28 章天然不入表）
"""
import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
for _sp in (_SCRIPTS, _HERE, os.path.join(_SCRIPTS, "works"), os.path.join(_SCRIPTS, "tools")):
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

try:
    from paths import BASE as ROOT
except Exception:                                    # pragma: no cover
    ROOT = os.path.dirname(_SCRIPTS)

WIKI_REL = os.path.join("doc", "aa作品相关", "ポケモンAA术语wiki", "术语表.json")
EXTRA_REL = os.path.join("doc", "aa作品相关", "ポケモンAA术语wiki", "补充术语.json")

# 全角→半角（数字/括号/字母）+ 空白归一：命中判定一律走归一化，落盘一律用源行原样
_FW = str.maketrans("０１２３４５６７８９（）Ｃ＋－．", "0123456789()C+-.")
_SP = re.compile(r"[\u3000 ]+")
STRUCT_RE = re.compile(r"^(?P<pre>[\s\u3000・･]*)『(?P<name>.+?)』(?P<sep>…|\.\.\.)(?P<desc>.*)$")

# 状态行规则轨（Q13 A）：全书只有 6 个状态词 + 15 种等级取值 → 100% 可规则化，
# 译文与模型无关、零风险。词表可在表的 derived.rules.state.map 里人工覆盖。
STATE_MAP = {
    "こうげき": "攻击",
    "ぼうぎょ": "防御",
    "とくこう": "特攻",
    "とくぼう": "特防",
    "すばやさ": "速度",
    "選手の質": "选手素质",
}
STATE_RE = re.compile(
    r"^(?P<pre>[\s\u3000]*)(?P<word>" + "|".join(map(re.escape, STATE_MAP)) +
    r")：(?P<grade>[A-Za-zＡ-Ｚ]{1,3}[+\-－＋]?)(?P<post>[\s\u3000]*)$")

# 闸一（构建期）额外黑名单：wiki desc 已实测的串列值（抓到"附帯"列而非效果列）
BAD_DESC = {"附带", "附带效果", "无", ""}


# copy 轨（Q14 A）：**零翻译风险**的整行复用——命中即原样照抄源行，不涉及任何译文。
#   B 纯图形行：无假名且无汉字（AA 图形/框线/ASCII/URL）——本来就不该送模型
#   A 已交付未改动行：已交付章里 `汉化行 == 原文行`（实证模型判定无需翻译）
# 命中口径：归一化 key 完全一致 → 落盘 = 该次出现的**源行原样**（padding 不动）。
COPY_MIN_LEN = 6
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


# 全角假名（含 ー/ヾ）：出现即说明这行有日文内容，不能当纯图形
_FW_KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
# ASCII 图形笔画（AA 碎片的主要成分）：≥2 个才算"图形行"
_ART_PUNCT = re.compile(r"[:;|/\\_.,'^<>{}()\[\]!\-+*=~\"`$#%&@]")
# 平假名：真日文句子几乎必有（助词），AA 图形碎片用片假名/半角假名当笔画，几乎不用平假名
_HIRA = re.compile(r"[\u3041-\u309f]")


def is_pure_art(line):
    """纯图形行判定（Q14 A 的 B 类）——判错的代价是**该行永不再翻**，故从严。

    2026-09-22 真机修正（entry_10437 smoke）：旧实现用 `A.kana_runs`（残留检测器，已排除
    艺术假名）⇒ `ワー！ﾊﾟﾁﾊﾟﾁﾊﾟﾁ`（欢呼台词，模型本该译「哇！啪啪啪」）被判成纯图形而被
    永久跳过；`とくこう：B`（全平假名状态行）同样中招。现口径：
      ① 有全角假名 → 不是纯图形（除非该行同时有 ≥2 个 ASCII 图形笔画）；
      ② 有汉字 → 不是纯图形；
      ③ 其余（纯 ASCII/半角假名碎片 + 图形笔画）→ 纯图形。
    """
    s = line or ""
    if not s.strip():
        return False
    if _CJK_RE.search(s):
        return False
    if _FW_KANA.search(s) and len(_ART_PUNCT.findall(s)) < 2:
        return False
    if len(_HIRA.findall(s)) >= 2:      # 平假名 ≥2 = 真日文句子（AA 图形不用平假名当笔画）
        return False
    return True


def needs_rule(line, table=None):
    """该行会被状态行规则轨命中？→ copy 轨应让位（规则轨译文精确且带 pad 校验）。"""
    return bool(match_rule(line, table or {}))


_SUSPECT_ASCII_SPLIT = re.compile(r"[A-Za-z\u3000]*[A-Za-z]\u3000+[A-Za-z]")


def has_real_text(line):
    """行内是否含"真日文内容"（平假名，或非艺术汉字）→ True 表示这行有东西要翻译。

    与 `is_pure_art` 的分工：本函数是**内容判据**（用于否决 copy 轨的交付证据），
    不含 ASCII 笔画/全角假名那两条启发式（AA 图形大量使用全角片假名 ム/彡/ノ 与艺术汉字 二/八 当笔画，
    用它们否决会把图形行误判成有内容）。
    """
    s = line or ""
    if _HIRA.search(s):
        return True
    try:
        import aa_translate as A
        art_kanji = A.ART_KANJI
    except Exception:
        art_kanji = set()
    for c in s:
        if "\u4e00" <= c <= "\u9fff" and c not in art_kanji:
            return True
    return False


def suspect_zh(zh):
    """可疑译文探测（闸三用，不自动改）：ASCII 词被全角空格劈开＝模型改坏的迹象。

    实测样本：`◎ポケモンwiki【…】` 的已交付译文是 `◎宝可梦w　iki【…】`（模型在 wiki 中间
    塞了全角空格）。这种条目**不入表**（宁可回落给模型，也别把损坏的行钉成标准），进
    `audit.suspect` 等人工修——修完写进 `manual`/手改 `whole` 即永久生效。
    """
    return "ascii_split" if _SUSPECT_ASCII_SPLIT.search(zh or "") else ""


def guard_build(zh, src_line=None):
    """建表期护栏（宽松版）：只拦**译文本身**的问题——空、残留假名、照抄原文。

    宽度/art 列位是**逐次出现**的属性（同一固定行在不同章里填充不同），不能在建表期
    判定，故留到 match/prefill 期的 `guard()`（四重）逐行把关。
    """
    try:
        import aa_translate as A
    except Exception as e:
        return False, f"no_aa:{e}"
    core = (zh or "").strip()
    if not core:
        return False, "empty"
    if A.kana_runs(core):
        return False, "residual"
    if src_line is not None and core == src_line.strip():
        return False, "echo"
    return True, ""


def state_map(table=None):
    """读状态词表（表里可覆盖；缺则用代码默认）。"""
    try:
        m = ((table or {}).get("derived") or {}).get("rules", {}).get("state", {}).get("map")
        if isinstance(m, dict) and m:
            return m
    except Exception:
        pass
    return STATE_MAP


def match_rule(line, table=None):
    """规则轨匹配（当前只有状态行）。返回 hit dict 或 None。"""
    m = STATE_RE.match(line or "")
    if not m:
        return None
    zh = state_map(table).get(m.group("word"))
    if not zh:
        return None
    return {"track": "rule", "zh": f"{m.group('pre')}{zh}：{m.group('grade')}",
            "term": m.group("word"), "src": "rule", "no_art": _no_art(line)}



# ---------------------------------------------------------------------------
# 归一化
# ---------------------------------------------------------------------------
def key(line):
    """整行轨 key：去掉全部 U+3000 与半角空格（与 aa_tm.key 同口径）。"""
    return _SP.sub("", line or "")


def norm(s):
    """匹配用归一化：去空白 + 全角数字/括号/字母转半角。落盘不用它。"""
    return _SP.sub("", (s or "").translate(_FW)).strip()


# 属性/能力名的两种写法：本作 wiki 页常写平假名（`…味方1体の「こうげき」を上げる。`），
# 章节正文写汉字短形（`…味方1体の「攻」を上げる。`）→ 整行键/说明键都对不上。
# 只用于**查表键归一**，不改落盘文本（实测『潜能/特性』说明族 top40 里 26 条栽在这）。
_HIRA_STAT = {"こうげき": "攻", "ぼうぎょ": "防", "とくこう": "特攻", "とくぼう": "特防",
              "すばやさ": "速", "めいちゅう": "命中", "かいひ": "回避", "たいりょく": "体力"}

# 波ダッシュ/全角チルダ/半角チルダ：日文编码经典分歧（wiki 页写 U+301C `〜`、章节正文写 U+FF5E `～`）
_WAVE = str.maketrans({"〜": "～", "~": "～", "∼": "～"})


def norm_alt(s):
    """属性表记差 + 波ダッシュ分歧归一；与 `norm` 叠加使用，供 wiki 查表用。落盘文本不动。"""
    t = norm(s).translate(_WAVE)
    for h, k in _HIRA_STAT.items():
        if h in t:
            t = t.replace(h, k)
    return t


def wiki_lookup(d, key):
    """查 wiki 索引：先原样键、再属性表记归一后的键。"""
    if not d or not key:
        return None
    v = d.get(key)
    if v is None:
        a = norm_alt(key)
        if a != key:
            v = d.get(a)
    return v


# ---------------------------------------------------------------------------
# wiki
# ---------------------------------------------------------------------------
def wiki_path():
    return os.environ.get("AA_POKEMON_WIKI") or os.path.join(ROOT, WIKI_REL)


def wiki_terms(path=None):
    """读 wiki 术语表 → {归一化日文名: {jp, zh, effect, effect_n, desc, cat, sub}}。

    同名多条目（同名的不同期版本）保留**先出现的**（wiki 内按页序），并记 variants 计数。
    """
    p = path or wiki_path()
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    out = {}
    for t in d.get("terms") or []:
        jp = (t.get("jp") or "").strip()
        if not jp:
            continue
        k = norm(jp)
        rec = {
            "jp": jp,
            "zh": (t.get("zh") or "").strip(),
            "effect": (t.get("effect") or "").strip(),
            "effect_n": norm(t.get("effect")),
            "desc": (t.get("desc") or "").strip(),
            "cat": t.get("cat") or "",
            "sub": t.get("sub") or "",
        }
        if k in out:
            out[k]["_variants"] = out[k].get("_variants", 1) + 1
            continue
        out[k] = rec
    return out


# ---------------------------------------------------------------------------
# 表 读写
# ---------------------------------------------------------------------------
def work_glossary_terms(work, work_dir=None):
    """作品术语表 → {归一化日文名: {jp, zh, cat}}（L2 槽位解析用的补充译名）。

    `カズマ→和真`、`メジロマックイーン→目白麦昆` 这类译名只在**作品表**里、不在 wiki 里；
    不并进来，L2 模板的 `{名}` 槽位就解析不出中文 → 整行落回模型（这是"模板没翻译"的一条根因）。
    """
    if work_dir is None:
        try:
            import registry
            work_dir = registry.get(work)["work_dir"]
        except Exception:
            return {}
    path = os.path.join(work_dir, "glossary_%s.json" % work)
    if not os.path.exists(path):
        return {}
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {}
    for t in (d.get("glossary") or []):
        jp = (t.get("jp") or "").strip()
        zh = (t.get("zh") or "").strip()
        if not jp or not zh or jp == zh or re.search(r"[\u3041-\u3096\u30a1-\u30fa]", zh):
            continue
        out[norm(jp)] = {"jp": jp, "zh": zh, "cat": t.get("cat") or "作品表"}
    return out


def merged_terms(work, work_dir=None):
    """固定表 / L2 槽位用的合并词表 = 通用 AA wiki 术语 ∪ 作品表译名 ∪ **本作 wiki 名字**。

    本作 wiki 的名字（`work_wiki_sources()["names"]`，含 `人工修正.本地译名`）必须并进来：
    `◎{名}に「特大のダメージ」！` 这类模板的 `{名}` 槽位靠它解析——只给通用 wiki + 作品表时，
    本作借名个体（インコマン/できる夫…）解析不出中文 → 槽位回退日文 → 整行落回模型。
    """
    out = dict(wiki_terms())
    out.update(work_glossary_terms(work, work_dir))
    try:                       # 补充术语.json 的人工增量（通用补充 + works.<作品> 作品专属，如场地名）
        import pre_terms as PT
        for jp, zh, _src, _cat in PT.extra_pairs(work):
            if jp and zh:
                out.setdefault(jp, zh)
    except Exception:
        pass
    try:
        src = work_wiki_sources(work)
        if src:
            out.update(src.get("names") or {})
    except Exception:
        pass
    return out


def table_path(work, work_dir=None):
    if not work_dir:
        try:
            import registry
            work_dir = registry.get(work)["work_dir"]
        except Exception:
            return None
    return os.path.join(work_dir, f"pokemon_fixed_{work}.json")


def empty_table(wiki_snapshot="", note=""):
    return {
        "meta": {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "wiki_snapshot": wiki_snapshot,
                 "note": note, "counts": {}},
        "derived": {"struct": {}, "whole": {}, "copy": {},
                    "rules": {"state": {"map": dict(STATE_MAP)}}},
        "manual": {"struct": {}, "whole": {}},
        "conflicts": [],
        "audit": {"auto_fail": [], "suspect": []},
    }


def load(work, work_dir=None):
    p = table_path(work, work_dir)
    if not p or not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    d.setdefault("derived", {}).setdefault("struct", {})
    d["derived"].setdefault("whole", {})
    d["derived"].setdefault("copy", {})
    d["derived"].setdefault("rules", {"state": {"map": dict(STATE_MAP)}})
    d.setdefault("manual", {}).setdefault("struct", {})
    d["manual"].setdefault("whole", {})
    d.setdefault("conflicts", [])
    d.setdefault("audit", {}).setdefault("auto_fail", [])
    d["audit"].setdefault("suspect", [])
    return d


def save(work, table, work_dir=None):
    p = table_path(work, work_dir)
    if not p:
        return False
    tmp = p + ".tmp"
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(table, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
        return True
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def enabled():
    """固定表是否启用（`AA_FIXED_TABLE=0` 关闭）。"""
    return os.environ.get("AA_FIXED_TABLE", "1") not in ("0", "false", "False", "")


def preterm_enabled():
    """替换后残留为空 → 直出（判据 A）是否启用（`AA_FIXED_PRETERM=0` 回退成"全交模型"）。"""
    return os.environ.get("AA_FIXED_PRETERM", "1") not in ("0", "false", "False", "")


# ---------------------------------------------------------------------------
# 匹配
# ---------------------------------------------------------------------------
def match_struct(line, table, terms=None):
    """结构化轨匹配。命中返回 dict（含 zh / term / col / pre / sep），否则 None。"""
    m = STRUCT_RE.match(line or "")
    if not m:
        return None
    name_n, desc = norm(m.group("name")), m.group("desc")
    if not desc.strip():
        return None
    ents = _entries(table, "struct", terms).get(name_n)
    if not ents:
        return None
    if norm(desc) != ents.get("effect_n"):
        return None
    zh_desc = ents.get("desc") or ""
    if not zh_desc:
        return None
    return {
        "track": "struct",
        "zh": None,                       # 由 assemble struct 生成
        "zh_term": ents.get("zh") or m.group("name"),
        "zh_desc": zh_desc,
        "term": ents.get("jp") or m.group("name"),
        "src": ents.get("_src", "wiki"),
        "pre": m.group("pre"),
        "sep": m.group("sep"),
        # 说明起始列：desc 组含前导空白，必须把它算进去，否则说明会被顶到 … 后面左对齐
        # （2026-09-22 修：曾漏算 → 交付里 `『気合い』…　　　　稀に…` 的说明列 28 变 13，文字整体左移）
        "col": _disp_width(line[:m.start("desc")]) + _disp_width(desc[:len(desc) - len(desc.lstrip())]),
        "no_art": _no_art(line),
    }


def match_whole(line, table):
    """整行轨匹配（归一化 key 完全一致）。"""
    rec = _entries(table, "whole", None).get(key(line))
    if not isinstance(rec, dict) or not rec.get("zh"):
        return None
    return {"track": "whole", "zh": rec["zh"], "term": rec.get("term", ""),
            "src": rec.get("src", "reuse"), "w": rec.get("w")}


def match_copy(line, table):
    """copy 轨匹配：命中即整行原样（连 padding 都不动），返回 hit dict 或 None。"""
    rec = ((table or {}).get("derived") or {}).get("copy") or {}
    k = key(line)
    if k in rec:
        return {"track": "copy", "copy": True, "zh": line, "term": "",
                "src": (rec[k] or {}).get("_src", "copy")}
    return None


# ---------------------------------------------------------------------------
# L2 模板/槽位规则表（方案 §L2）
# ---------------------------------------------------------------------------
# 招式行：`◎「技名」…　分类/类型/范围/威力/命中/接触/效果`
# 槽位字典是**数据**（可在 补充术语.json 的 `槽位` 段人工覆盖），模式是**结构**（代码）。
# 为什么必须 L2：这一行的骨架在 589 行里逐字重复，模型每次译得不一样
# （实测同一行有 `物/龍/単`、`物/龙/单` 两种写法）→ 一致性只能靠规则。
TPL_RULES = [
    {
        "name": "状态行",
        "rx": re.compile(
            r"(?P<词>" + "|".join(map(re.escape, STATE_MAP)) +
            r")：(?P<等级>[A-Za-zＡ-Ｚ]{1,3}[+\-－＋]?)"
        ),
        "slots": {"词": ("state", None), "等级": ("keep", None)},
    },
    {
        "name": "招式行",
        "rx": re.compile(
            r"◎[「『](?P<技>[^」』]{1,24})[」』]…+[ \u3000]*"
            r"(?P<分类>物理|特殊|变化|物|特|変)/(?P<类型>[^/]{1,10})/(?P<范围>[^/]{1,4})/"
            r"(?P<威力>[^/]{1,5})/(?P<命中>[^/]{1,5})/(?P<接触>[○〇×\-])/(?P<效果>.*)$"
        ),
        "slots": {
            "技": ("term", None),
            "分类": ("map", "分类"),
            "类型": ("typemap", "类型"),
            "范围": ("map", "范围"),
            "威力": ("keep", None),
            "命中": ("keep", None),
            "接触": ("map", "接触"),
            "效果": ("terms_text", "效果"),
        },
    },
]

TPL_SLOT_DEFAULTS = {
    # 宽度中性优先：招式行在 AA 行里是**定列骨架**，替换后列位一动 art 锚点校验就拒
    # （`aa_tm.guard` 的 art 列位 ±4），所以槽位一律取「同宽短形」——
    # 1 个汉字位就还 1 个汉字位（物→物、龍→龙、単→单），不是官方全称（物理/幽灵/单体）。
    # 依据：已交付汉化里模型自己收敛到的写法就是短形（`物/龙/单/40/100/×`）。
    "分类": {"物": "物", "特": "特", "変": "变"},
    "类型": {
        "ノ": "普", "炎": "火", "水": "水", "草": "草", "電": "电", "氷": "冰",
        "闘": "斗", "毒": "毒", "地": "地", "飛": "飞", "超": "超",
        "虫": "虫", "岩": "岩", "霊": "灵", "龍": "龙", "悪": "恶", "闇": "暗",
        "鋼": "钢", "妖": "妖",
    },
    "范围": {"単": "单", "全": "全", "複": "多", "自": "自", "自分": "自身",
             "自陣": "自阵", "敵陣": "敌阵"},
    "接触": {"〇": "○", "○": "○", "×": "×", "-": "-"},   # 统一写法，已规范的原样保留
    "效果": {"麻痺": "麻痹", "火傷": "灼伤", "凍り": "冰冻", "眠り": "睡眠",
             "混乱": "混乱", "怯み": "畏缩"},
}
_SLOT_OVER_CACHE = None


def slot_overrides():
    """`补充术语.json` 的 `槽位` 段（人工可改的槽位字典覆盖）。"""
    global _SLOT_OVER_CACHE
    if _SLOT_OVER_CACHE is not None:
        return _SLOT_OVER_CACHE
    path = os.path.join(ROOT, EXTRA_REL)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        over = data.get("槽位") or {}
    except Exception:
        over = {}
    _SLOT_OVER_CACHE = {k: v for k, v in over.items() if isinstance(v, dict)}
    return _SLOT_OVER_CACHE


def tpl_slots(table=None):
    """槽位字典 = 代码默认 + 补充术语.json `槽位` 段覆盖（数据可人工改）。"""
    out = {k: dict(v) for k, v in TPL_SLOT_DEFAULTS.items()}
    over = dict(slot_overrides())
    over.update(((table or {}).get("slots_override") or {}))
    for k, v in over.items():
        if isinstance(v, dict):
            out.setdefault(k, {}).update(v)
    return out


def _typemap(text, m, slots):
    """`霊` → `幽灵`；`悪（炎）` → `恶（火）`（复合属性逐字映射）。"""
    out = []
    for ch in text:
        if ch in m:
            out.append(m[ch])
        elif ch in "（）()・/ " or not re.match(r"[^\x00-\x7f]", ch):
            out.append(ch)
        else:
            return None                      # 有未收录的属性字 → 整条规则不命中
    return "".join(out)


def _zh_of(v):
    """术语表取值可能是 `{jp: "中文"}` 或 `{jp: {zh: "中文", …}}` 两种形态。"""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return v.get("zh") or ""
    return ""


def _terms_text(text, m, slots, terms):
    """效果文本：术语替换（长词优先）+ 槽位小表；`10割` 的「割」只在数字后替换。"""
    out = text
    if terms:
        for jp in _sorted_terms(terms):
            zh = _zh_of(terms[jp])
            if jp and zh and jp in out:
                out = out.replace(jp, zh)
    for jp, zh in sorted((m or {}).items(), key=lambda kv: -len(kv[0])):
        if jp and jp in out:
            out = out.replace(jp, zh)
    return re.sub(r"(?<=\d)割", "成", out)


def _kana_left(text):
    """规则覆盖后仍有平假名/片假名连串 → 说明还有要模型翻的东西，规则不接管。"""
    return bool(re.search(r"[ぁ-ゖァ-ヺー]{2,}", text))


def _kana_any(text):
    """规则输出里有没有**任何**假名（含单个）——规则输出必须零假名。"""
    return bool(re.search(r"[ぁ-ゖァ-ヺー]", text or ""))


def _apply_spans(line, spans):
    out = line
    for s, e, zh in sorted(spans, key=lambda x: -x[0]):
        out = out[:s] + zh + out[e:]
    return out


def resolve_slot(spec, raw, slots, terms, table=None):
    kind, key = spec
    if raw is None:
        return None
    if kind == "keep":
        return raw
    if kind == "map":
        return (slots.get(key) or {}).get(raw)
    if kind == "state":
        return state_map(table).get(raw)
    if kind == "typemap":
        return _typemap(raw, slots.get(key) or {}, slots)
    if kind == "term":
        return _zh_of((terms or {}).get(raw)) or None
    if kind == "terms_text":
        return _terms_text(raw, slots.get(key), slots, terms)
    return None


_TPL_TERMS_CACHE = None
_TPL_MERGED = (None, None)          # (extra 引用, 合并结果)
_SORTED_TERMS = (None, [])          # (terms 引用, 长词优先键序)


def tpl_terms(extra=None):
    """L2 槽位用的术语表 = 官方译名对照（招式/特性/道具/宝可梦/属性）+ 传入的 terms。

    为什么单独一张：招式名（`したでなめる`）是**官方**译名，不在 AA 原创术语表里，
    只查 wiki_terms() 会解析失败 → 招式行整条落回模型（这正是"招式文本没替换"的根因）。
    """
    global _TPL_TERMS_CACHE, _TPL_MERGED
    if _TPL_TERMS_CACHE is None:
        out = {}
        path = os.path.join(ROOT, "doc", "aa作品相关", "ポケモンAA术语wiki", "官方译名对照.json")
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for rec in (data.get("official") or []):
                jp, zh = rec.get("jp"), rec.get("zh")
                if jp and zh and jp not in out:
                    out[jp] = zh
        except Exception:
            out = {}
        _TPL_TERMS_CACHE = out
    if not extra:
        return _TPL_TERMS_CACHE
    if _TPL_MERGED[0] is extra:          # 同一份 terms 反复用（逐行调用）→ 复用合并结果
        return _TPL_MERGED[1]
    merged = dict(_TPL_TERMS_CACHE)
    for jp, v in extra.items():
        zh = _zh_of(v)
        if zh:
            merged[jp] = zh
    _TPL_MERGED = (extra, merged)
    return merged


def _sorted_terms(terms):
    """术语长词优先的键序（逐行调用要缓存，否则每行排序 4000 条）。"""
    global _SORTED_TERMS
    if _SORTED_TERMS[0] is terms:
        return _SORTED_TERMS[1]
    keys = sorted(terms, key=len, reverse=True)
    _SORTED_TERMS = (terms, keys)
    return keys


def _drop_overlaps(spans):
    """同一行多规则命中时去重叠：先按起点、同起点取长匹配，重叠的丢弃。"""
    out, last_end = [], -1
    for s, e, zh in sorted(spans, key=lambda x: (x[0], -(x[1] - x[0]))):
        if s < last_end:
            continue
        out.append((s, e, zh))
        last_end = e
    return out


_SLOT_TAIL = {"技": "」", "词": "："}      # 跟着槽位一起替换的尾字符（补位放它后面）


_BR_OPEN = "「『"
_BR_CLOSE = "」』"


def _echo_brackets(src, out):
    """输出**回显源行的引号风格**（模板只写了一种，「」/『』在 AA 里是混用的）。

    只在两侧引号数量一一对应时改写（否则原样返回，绝不猜）。
    """
    if not src:
        return out
    so = [c for c in src if c in _BR_OPEN]
    oo = [c for c in out if c in _BR_OPEN]
    sc = [c for c in src if c in _BR_CLOSE]
    oc = [c for c in out if c in _BR_CLOSE]
    if len(so) != len(oo) or len(sc) != len(oc):
        return out
    res, i, j = [], 0, 0
    for c in out:
        if c in _BR_OPEN:
            res.append(so[i])
            i += 1
        elif c in _BR_CLOSE:
            res.append(sc[j])
            j += 1
        else:
            res.append(c)
    return "".join(res)


def _pad_to_span(zh, raw, A):
    """把替换文本补到源 span 的显示宽度（不足补全角空格，超宽返回 None）。

    招式行在 AA 行里是定列骨架：替换后列位一动，`aa_tm.guard` 的 art 锚点校验（±4）就拒。
    """
    w_src, w_zh = A.disp_width(raw), A.disp_width(zh)
    if w_zh > w_src:
        return None
    if w_zh < w_src:
        zh += "\u3000" * ((w_src - w_zh) // 2)
        if (w_src - w_zh) % 2:
            zh += " "
    return zh


_TPL_TPL_CACHE = None


_CONN_SRC = r"(?:によって|により|を込めて|を込め|に|で)"
_CONN_ALT = r"(?:状態)?(?:によって|により|を込めて|を込め|に|で)"
_CONN_LONG = r"(?:によって|により|を込めて|を込め)"     # 会被编译期放宽的连接词（长形）——预筛字面量要避开


def tpl_lax_enabled():
    """模板连接词容错开关（`AA_TPL_LAX=0` 关）。"""
    return os.environ.get("AA_TPL_LAX", "1").strip() != "0"


def tpl_templates():
    """`补充术语.json` 的 `模板` 段 → L2 整句模板规则（零代码扩展 L2 覆盖）。

    一条模板 = `{"jp": "◎フィールド：《{场}》（>>{N}）", "zh": "◎场地：《{场}》（>>{N}）"}`：
    jp 里的 `{名}` 是变量（编译成 `(?P<gN>.+?)`），zh 里同名变量取该槽位的**术语替换后**文本。
    与 `effects` 段的区别：effects 只进 wiki 展示（带 {槽位} 的模板 pre_terms 匹配不到），
    本段才是**真正会接管整行**的模板。
    """
    global _TPL_TPL_CACHE
    if _TPL_TPL_CACHE is not None:
        return _TPL_TPL_CACHE
    path = os.path.join(ROOT, EXTRA_REL)
    rules = []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for rec in (data.get("模板") or []):
            jp = (rec.get("jp") or "").strip()
            zh = (rec.get("zh") or "").strip()
            if not jp or not zh:
                continue
            names = re.findall(r"\{([^{}]+)\}", jp)
            if not names or not re.findall(r"\{([^{}]+)\}", zh):
                continue
            # 槽位可写 `{名?}` = 可选（正则 `(?:(?P<gN>.+?))?`，缺省时中文侧该变量替换成空串）。
            # 只能放**行尾**：非贪婪可选组在中段会抢长度（`{A?}…` 吞掉后半行）。
            # 用途：`{名}に「特大のダメージ」！{尾?}` 这类"结算行 + 可选 @状态/尾注"。
            rx_src = jp
            for i, n in enumerate(names):
                rx_src = rx_src.replace("{" + n + "}", "\x00%d\x00" % i)
            rx_src = re.escape(rx_src)
            # 连接词容错（必须在引号放宽**之前**做：那时引号还是单字符，lookbehind 才能定位）：
            # 槽位后的 `「…」によって` 也吃 `状態によって`（中缀）/`により`/`に`/`で`/`を込めて`
            # —— 同一语义、同一中文模板，只是原作写法多变（实测皮一枚族栽在这）。
            # 只在**紧跟引号收尾**处放宽：否则模板字面里词内的 `に`/`で` 会被换成同义集，
            # 凭空接受乱码写法（`にんぎょう` ↔ `でんぎょう`）。
            if tpl_lax_enabled():
                rx_src = re.sub(r"(?<=[」』])(?:状態)?" + _CONN_SRC, _CONN_ALT, rx_src)
            # 引号互认：AA 里「」/『』混用（属性/状态用「」、潜能/队伍名用『』），
            # 模板只写了一种 → 编译时两种都收，输出按**源行实际引号**回显（`_echo_brackets`）。
            for _a, _b in (("「", "『"), ("」", "』")):
                rx_src = rx_src.replace(re.escape(_a), "[%s%s]" % (_a, _b))
            for i, n in enumerate(names):
                rep = ("(?:(?P<g%d>.+?))?" % i) if n.endswith("?") else ("(?P<g%d>.+?)" % i)
                rx_src = rx_src.replace(re.escape("\x00%d\x00" % i), rep)
            # 廉价预筛用字面量：模板里最长的**无引号、无连接词**字面段（≥3 字）。
            # 为什么需要：前缀放宽成 `[\s\S]*?` 后，每行都要为每条模板做一次回溯搜索 ——
            # 85 条模板 × 全库 70 万行 = 跑不完（实测单次全库扫描 >7 分钟没结束）。
            # 有它则先用 `in line` 廉价排除，绝大多数行直接被跳掉（性能与语义都不变）。
            need = ""
            for seg in re.split(r"\{[^{}]+\}|" + _CONN_LONG, jp):
                for piece in re.split(r"[「」『』【】《》（）]", seg or ""):
                    s = piece.strip()
                    if len(s) >= 3 and len(s) > len(need):
                        need = s
            try:
                # 行首装饰前缀**任意字符**（惰性）单独成组，命中后原样回显：系统行常嵌在 AA 图形行里
                # （`\|: : : : : :人…　◎アスカのレベルが「2」上がった！（Lv：30⇒32）`），装饰是排版的一部分。
                # 惰性组 = 「找到最早的、能让骨架匹配到行尾的位置」，所以整行锚定语义不变
                # （骨架仍必须吃掉到行尾）。前缀含假名时 `match_tpl` 的整行假名检查会拒 → 不会被
                # 用来"翻译半句话"；AA 图形里的半角假名同理（保守送回模型）。
                rx = re.compile(r"^(?P<__pre>[\s\S]*?)" + rx_src + r"\s*$")
            except re.error:
                continue
            rules.append({
                "name": "模板:" + (rec.get("cat") or "模板"),
                "rx": rx,
                "need": need,
                "mode": "template",
                "jp": jp,
                "zh": zh,
                "names": names,
                "slots": {"g%d" % i: ("terms_text", None) for i in range(len(names))},
            })
    except Exception as e:
        # 静默丢光所有模板曾把一个真 bug（`_CONN_RX` 类型错）藏了一轮 —— 失败要响。
        print("[pokemon_fixed] 模板段装载失败（本次不启用整句模板）: %r" % (e,), file=sys.stderr)
        rules = []
    _TPL_TPL_CACHE = rules
    return rules


def _tpl_all_rules():
    return list(TPL_RULES) + list(tpl_templates())


def _tpl_spans(line, table, terms, mode):
    """对一行跑全部规则，收集可解析的槽位 span。

    mode='pre'  原文行 → 槽位按规则解析并**补到源 span 宽度**（列位不动才过 art 校验）
    mode='post' 汉化行 → 只改「仍是日文/非标准」的字段（模型译过也拉回同一口径）
    """
    slots = tpl_slots(table)
    terms = tpl_terms(terms)
    try:
        import aa_translate as A
    except Exception:
        A = None
    spans, rules = [], []
    for rule in _tpl_all_rules():
        need = rule.get("need")
        if need and need not in (line or ""):
            continue                      # 廉价预筛：模板的长字面量不出现 → 不可能命中
        for m in rule["rx"].finditer(line or ""):
            if rule.get("mode") == "template":
                # 整句模板：变量术语替换后填回 zh 模板，整段替换 + 补位到源宽。
                # 系统行（非 AA 图形）用**段尾补位**（与 pad_to_width 同口径），并跳过 art 锚点校验：
                # 模板已锚定整行、行内不可能有 AA 笔画，`《》（）` 被 art 校验当锚点会误拒。
                vals = []
                for i, _n in enumerate(rule.get("names") or []):
                    raw = m.group("g%d" % i) or ""        # 可选槽位缺省 → 空串（不是 None）
                    v = resolve_slot(("terms_text", None), raw, slots, terms, table) if raw else ""
                    vals.append(v if v is not None else raw)
                out_zh = rule["zh"]
                for i, n in enumerate(rule.get("names") or []):
                    out_zh = out_zh.replace("{" + n + "}", vals[i])
                s, e = m.start(), m.end()
                seg = line[s:e]
                # 行首装饰（◎/―――/全角空格）原样回显 + 引号按源行风格回显
                out_zh = _echo_brackets(seg, (m.groupdict().get("__pre") or "") + out_zh)
                if A is not None:
                    padded = _pad_to_span(out_zh, seg, A)
                    if padded is not None:
                        out_zh = padded
                    else:
                        # 中文比日文长、放不进源 span：**纯文本行**不该因此整行回落模型
                        # （项目口径：text/mixed 允许超宽，只有 art 行严格等宽）。
                        # 模板正则锚定整行（`^\s*…\s*$`），能匹配的一定是整行，行内不存在
                        # 「右边还有 AA 图形需要对齐」的情形；宽度最终由 finalize 决定
                        # （`allow_overwidth` 放行 text/mixed、art 仍拒）。
                        # 实测：`自陣：エクシア：速+1` → `自阵：能天使高达：速+1` 因长 1 列被丢，
                        # 整族 332 行退回模型。
                        if A.line_kind(line) not in ("text", "mixed"):
                            continue
                if mode == "pre" or out_zh != seg:
                    spans.append((s, e, out_zh))
                    rules.append(rule["name"])
                continue
            sub, ok = [], True
            for name, spec in rule["slots"].items():
                raw = m.group(name)
                zh = resolve_slot(spec, raw, slots, terms, table)
                if zh is None:
                    # post 模式是「规范化」：解不出的字段说明它已是中文 → 原样保留，不算失败
                    if mode == "post":
                        continue
                    ok = False
                    break
                s, e = m.start(name), m.end(name)
                if name in _SLOT_TAIL:                  # 引号/冒号跟着槽位一起换，补位放它后面
                    tail = _SLOT_TAIL[name]
                    src_tail = line[e:e + len(tail)]
                    if len(src_tail) == len(tail) and tail in _BR_CLOSE and src_tail in _BR_CLOSE:
                        tail = src_tail                 # 源行用 『』/』』 就跟着用
                    e += len(tail)
                    zh = zh + tail
                if A is not None:
                    padded = _pad_to_span(zh, line[s:e], A)
                    if padded is None:
                        # 译文比原文宽：pre 整行交回模型；post 只跳过这个字段（列位不能让）
                        if mode == "pre":
                            ok = False
                            break
                        continue
                    zh = padded
                if mode == "pre" or zh != line[s:e]:
                    sub.append((s, e, zh))
            if ok:
                spans.extend(sub)
                rules.append(rule["name"])
    if not spans:
        return None, []
    return _drop_overlaps(spans), sorted(set(rules))


def match_tpl(line, table=None, terms=None):
    """L2 模板轨：槽位全解析成功、且覆盖后无假名残留才接管（否则交回模型）。"""
    spans, rules = _tpl_spans(line, table, terms, "pre")
    if not spans:
        return None
    out = _apply_spans(line, spans)
    if _kana_left(out) or _kana_any(out):
        # 规则输出的槽位都来自术语表/映射，正常一个假名都不该剩；
        # 单个假名（`玉藻の前` 的 の）也说明槽位没解析成中文 → 交回模型
        return None
    hit = {"track": "tpl", "zh": out, "rule": "+".join(rules), "src": "tpl"}
    if any(r.startswith("模板:") for r in rules):
        hit["no_art"] = True          # 整句模板 = 系统行，art 锚点校验对它不适用
    return hit


def normalize_tpl(zh_line, table=None, terms=None):
    """L2 后置规范化：把已汉化行里的骨架字段拉回同一口径。

    只动骨架（状态词/技名/分类/类型/范围/接触）+ 效果里的假名状态词与「N割」，
    不碰模型写的效果正文；字段解析不全时原样返回（宁可不改）。
    """
    spans, _ = _tpl_spans(zh_line, table, terms, "post")
    if not spans:
        return zh_line
    out = _apply_spans(zh_line, spans)
    try:
        import aa_translate as A
        if not A.art_stroke_preserved(zh_line, out):
            return zh_line          # 改完 art 列位动了 → 宁可不改
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# 本作设定 wiki 固定内容源（2026-10-08）
# ---------------------------------------------------------------------------
# 为什么：本作 wiki（doc/aa作品相关/<作品>wiki）的页面是**逐行 jp/zh 平行**的，
# 而章节里的「【特性】名…说明」「『潜能名』…　　　说明」行与 wiki 原文逐字一致 →
# 可零模型直出。实测 ポケの子：『』… 行 7704 里 84.5% 命中，【特性】行 821 里 96.1% 命中
# （只读 terms[].desc_zh 只有 26% / 52%——页面平行对的覆盖高得多）。
WORK_WIKI_ROOT = os.path.join("doc", "aa作品相关")
WORK_WIKI_TERMS = "术语表.json"
WORK_WIKI_FIX = "人工修正.json"
_WW_CACHE = {}

_AB_SEARCH = re.compile(r"【特性】\s*(?P<name>[^…\u3000]{1,24}?)\s*(?P<sep>…+|\u3000+)\s*(?P<desc>.*)$")
_ST_SEARCH = re.compile(r"『(?P<name>[^』\n]{1,40})』(?P<sep>…|\.\.\.)(?P<desc>.*)$")
# 道具/指令的「・名…说明」形式（`・ボコのみ…　体力が満タンの時…`、`・技マシン…　「A/B/C」`）
_DOT_SEARCH = re.compile(r"・(?P<name>[^…\n]{1,30}?)(?P<sep>…+|\.\.\.)(?P<desc>.*)$")


def work_wiki_dir(work, path=None):
    """本作设定 wiki 目录；没有则 ""。优先复用 pre_terms 的解析（含 work_meta.wiki_dir）。"""
    try:
        import pre_terms as PT
        d = PT.work_wiki_dir(work, path)
        if d:
            return d
    except Exception:
        pass
    d = os.path.join(ROOT, WORK_WIKI_ROOT, (work or "") + "wiki")
    return d if os.path.isdir(d) else ""


_Q_PAIRS = (re.compile(r"『([^』]{1,24})』"), re.compile(r"「([^」]{1,24})」"),
            re.compile(r"《([^》]{1,24})》"), re.compile(r"【([^】]{1,24})】"))


def _harvest_quoted_pairs(data, names):
    """平行对里「同位置引号内容」= 本作 wiki 已给中文的名字（队伍/道具/潜能/形态/招式）。

    名字表原本只收术语表 `terms[]` 与页面标题 → wiki 正文里明明有中文的
    `《星ノ紅玉》`、`『金魔女の陰謀』`、`ハープ` 仍被当缺词送模型（编成表族 200+ 行）。
    约束：两侧引号数一一对应、日文侧长度 ≥2、中文侧不得含假名（防把半成品当译名）。
    """
    for p in (data.get("pages") or []):
        for b in (p.get("blocks") or []):
            a, z = b.get("jp"), b.get("zh")
            if not (isinstance(a, list) and isinstance(z, list) and len(a) == len(z)):
                continue
            for jl, zl in zip(a, z):
                jl, zl = (jl or "").strip(), (zl or "").strip()
                if not jl or not zl:
                    continue
                for rx in _Q_PAIRS:
                    ja, za = rx.findall(jl), rx.findall(zl)
                    if not ja or len(ja) != len(za):
                        continue
                    for x, y in zip(ja, za):
                        x, y = x.strip(), y.strip()
                        if len(x) < 2 or not y or norm(x) == norm(y) or _kana_any(y):
                            continue
                        names.setdefault(norm(x), y)


def work_wiki_sources(work, path=None, refresh=False):
    """本作 wiki → {"lines": {...}, "names": {...}, "descs": {...}}（归一化 key）。

    - lines: 页面 text 块的逐行平行对（jp 整行 → zh 整行）
    - names: 术语表的『名』译名（去括号）+ 人工修正.本地译名
    - descs: 术语表 desc→desc_zh + 平行对里「『名』…说明」行的说明两侧
    人工修正.json 的 `修正`（整行覆盖）优先级最高（唯一手改入口）。
    """
    d = work_wiki_dir(work, path)
    if not d:
        return None
    tp, mp = os.path.join(d, WORK_WIKI_TERMS), os.path.join(d, WORK_WIKI_FIX)
    stamp = []
    for p in (tp, mp):
        try:
            stamp.append((os.path.getmtime(p), os.path.getsize(p)))
        except OSError:
            stamp.append((0, 0))
    ck = (d,) + tuple(stamp)
    if not refresh and _WW_CACHE.get("key") == ck:
        return _WW_CACHE["val"]
    data = _load_json_file(tp)
    lines, names, descs = {}, {}, {}
    for t in (data.get("terms") or []):
        jp = norm(t.get("jp")).strip("『』")
        zh = (t.get("zh") or "").strip().strip("『』")
        if jp and zh:
            names.setdefault(jp, zh)
        dj, dz = norm(t.get("desc")), (t.get("desc_zh") or "").strip()
        if dj and dz:
            descs.setdefault(dj, dz)
    for p in (data.get("pages") or []):
        for b in (p.get("blocks") or []):
            a, z = b.get("jp"), b.get("zh")
            if not (isinstance(a, list) and isinstance(z, list) and len(a) == len(z)):
                continue
            for jl, zl in zip(a, z):
                jl, zl = (jl or "").strip(), (zl or "").strip()
                if not jl or not zl or jl == zl:
                    continue
                lines.setdefault(norm(jl), zl)
                lines.setdefault(norm_alt(jl), zl)       # 属性表记差（`こうげき`↔`攻`）也建键
                sp = _split_zh_line(zl)
                if not sp:
                    continue
                m = STRUCT_RE.match(jl) or _AB_LINE_RE.match(jl) or _DOT_LINE_RE.match(jl)
                if m and m.group("desc").strip():
                    descs.setdefault(norm(m.group("desc")), sp[1])
                    descs.setdefault(norm_alt(m.group("desc")), sp[1])
    man = _load_json_file(mp)
    for p in (data.get("pages") or []):       # 页面标题（本作 wiki 的「名前」口径，与 pre_terms 同源）
        jp, zh = (p.get("name") or "").strip(), (p.get("title") or "").strip()
        if jp and zh and jp != zh:
            names.setdefault(norm(jp).strip("『』"), zh.strip("『』"))
    _harvest_quoted_pairs(data, names)        # 平行对里引号包住的名字（队伍/道具/潜能/形态…）
    for k, v in (man.get("修正") or {}).items():
        if k and v:
            lines[norm(k)] = v                     # 人工整行覆盖（最高优先）
    for e in (man.get("本地译名") or []):
        if isinstance(e, (list, tuple)) and len(e) >= 2 and e[0] and e[1]:
            names[norm(str(e[0])).strip("『』")] = str(e[1]).strip()
    val = {"dir": d, "lines": lines, "names": names, "descs": descs}
    _WW_CACHE["key"], _WW_CACHE["val"] = ck, val
    return val


_AB_LINE_RE = re.compile(r"^[\s\u3000]*【特性】\s*(?P<name>[^…\u3000]{1,24}?)\s*(?P<sep>…+|\u3000+)\s*(?P<desc>.*)$")
_DOT_LINE_RE = re.compile(r"^[\s\u3000]*・(?P<name>[^…\n]{1,30}?)(?P<sep>…+|\.\.\.)(?P<desc>.*)$")


def _load_json_file(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _split_zh_line(z):
    """wiki 侧整行译文 → (头部含名, 说明)；拆不出返回 None。

    头部 = 『中文名』… / 【特性】中文名…（含名后的分隔符，后面的填充空白丢弃）。
    """
    z = (z or "").strip()
    if not z:
        return None
    i = z.find("』")
    cut = i + 1 if i >= 0 else (z.find("】") + 1 if "】" in z else -1)
    if cut <= 0:
        # `・{名}…说明`（道具/指令说明行）：头部 = 第一个 … 之前（含 …）
        j = z.find("…")
        if j > 0 and "・" in z[:j]:
            return z[:j + 1], z[j + 1:].strip()
        return None
    rest = z[cut:]
    k = rest.find("…")
    step = 1
    if k < 0:
        k = rest.find("...")
        step = 3
        if k < 0:
            return None
    return z[:cut] + rest[:k + step], rest[k + step:].strip()


_BRACKETS = str.maketrans("", "", "『』「」《》（）()[]")


def _nobrack(s):
    return (s or "").translate(_BRACKETS)


_QUOTE_OPEN = "『「【《“‘"
_QUOTE_CLOSE = "』」】》”’"


def _quote_spans(s):
    """引号/括号对的位置 → [(start, end, 内容)]（只认成对出现的）。"""
    out = []
    depth_open = None
    start = 0
    for i, ch in enumerate(s or ""):
        if depth_open is None and ch in _QUOTE_OPEN:
            depth_open, start = ch, i
        elif depth_open is not None and ch in _QUOTE_CLOSE:
            out.append((start, i + 1, s[start + 1:i]))
            depth_open = None
    return out


def _std_zh_map(pairs):
    """本章标准译名对 → {归一化 jp: zh}（`re:` 对不进；长的优先）。"""
    m = {}
    for p in pairs or ():
        jp, zh = (p.get("jp") or ""), (p.get("zh") or "")
        if not jp or not zh or jp.startswith("re:"):
            continue
        k = norm(jp).strip()
        if k and k not in m:
            m[k] = zh
    return m


def qq_align_enabled():
    """引号序号对齐开关（`AA_QQ_ALIGN=0` 关）。"""
    return os.environ.get("AA_QQ_ALIGN", "1").strip() != "0"


def _align_quotes(src_seg, zh_line, pairs):
    """按**引号序号**把中文输出里的名字对齐到本章标准译名。

    动机（2026-10-08 实测）：wiki 自己的中文口径常与标准表不一致
    （`『対闘耐性』…` 的 wiki 中文是 `『对格耐性』…「格斗」`，标准表是 `对斗耐性`/`斗`），
    于是 `_names_consistent` 自检把它们全拒了 —— 『潜能/特性』说明族 41%、【特性】族 53%
    送模型行是这么丢的。这里在**装配之前**把输出里第 k 个引号的内容换成标准 zh，
    自检因此由构造保证通过，且输出与标准表统一。

    ⚠️ 必须在宽度补位**之前**做（换名会改宽度）。引号数量对不上就整条不动（绝不猜）。
    """
    m = _std_zh_map(pairs)
    if not m or not zh_line or not qq_align_enabled():
        return zh_line
    a = _quote_spans(src_seg)
    b = _quote_spans(zh_line)
    if not a or len(a) != len(b):
        return zh_line
    fix = []
    for k, (_s, _e, inner) in enumerate(a):
        key = norm(inner).strip()
        zh = m.get(key) if key else None
        if not zh or norm(zh) == key:
            continue
        fix.append((b[k][0], b[k][1], zh))
    if not fix:
        return zh_line
    out = zh_line
    for s, e, zh in reversed(fix):                 # 从后往前改，偏移不失效
        out = out[:s + 1] + zh + out[e - 1:]       # 只换内容，括号沿用输出自己的
    return out


def _names_consistent(src_line, out, pairs):
    """译名一致性自检：本章标准译名对里，源行**引号内整段**就是该 jp 而输出没用标准 zh → 弃。

    实测两个假阳性（必须避开，否则本轨太容易被自己人否掉）：
      · `『エース』→『王牌』` vs wiki 译文里的「王牌」——括号写法不同（『』/「」），
        故比较前**双方都去括号**；
      · `ハンター→猎手` 被 `モンスターハンター` 里命中——故 jp 用 **pre_terms 的片假名邻接守卫**
        正则判定（与替换轨同一口径）。
    第三个（2026-10-08）：`敵陣→敌阵`（编成表标签的短形）会去和 wiki 正文里的「敌方阵营」较劲
    —— 正文用长形是合理的中文，所以只对**引号内**出现的 jp 强制一致（名字基本都带括号，
    正文词不强制），既保住"wiki 用了旧译名"的拦截，又不误杀正文措辞差异。
    第四个（2026-10-08 收紧）：只对「引号内容**整体等于**该 jp」的对强制（原判据是"命中位置落在
    引号内"，会被子串误伤：标准对有 `つるぎ→斩`、`勝鬨→胜哄`，而它们在正文里只是
    `「せいなるつるぎ」`(圣剑)/`『撃槍姫の勝鬨』`(凯歌) 的一部分 → 把正确译文整片拒掉，
    实测『潜能/特性』说明族 top40 里 19 条）。
    """
    spans = [norm(inner) for _s, _e, inner in _quote_spans(src_line)]
    if not spans:
        return True
    out_n = _nobrack(out)
    for p in pairs or ():
        jp = p.get("jp") or ""
        zh = p.get("zh") or ""
        if not jp or not zh or jp.startswith("re:"):
            continue
        if norm(jp) not in spans:
            continue
        if _nobrack(zh) not in out_n:
            return False
    return True


def match_work_wiki(line, work=None, path=None, pairs=None, terms=None):
    """本作 wiki 固定内容轨：『名』…说明 / 【特性】名…说明 → 中文整行（列位按源行补）。

    三条路（优先级）：①整行/整段平行对 ②名+说明分别命中 ③放弃（交模型）。
    自检：中文侧无残留假名、非照抄、译名与本章标准译名一致（`pairs` 给出时）。
    `no_art` 走 `_no_art()`（纯文本行跳过 art 列位校验）：本轨只重写「【特性】/『名』…」文本单元，
    行内 AA 笔画（在单元之前）逐字保留，而 `（）、（）` 这类文本括号会被 art 校验当笔画 → 误拒。

    `terms`（合并词表）用于**名字的兜底解析**：`対闘回避` 这类名字不在 wiki `terms[]` 里
    （wiki 只收 `対〇回避` 家族占位），但其**说明**与同族其它条目逐字相同 → 靠 wiki 的
    说明表 + 我们的名字表就能拼出中文（实测「対◯回避/耐性/迫撃/反撃」整族 700+ 行）。
    """
    src = work_wiki_sources(work, path)
    if not src or not (line or "").strip():
        return None
    m = _AB_SEARCH.search(line)
    if m is None:
        m = _ST_SEARCH.search(line)
    if m is None:
        m = _DOT_SEARCH.search(line)
    if m is None:
        return None
    start = m.start()
    seg = line[start:]
    desc = m.group("desc")
    col = (_disp_width(line[:m.start("desc")])
           + _disp_width(desc[:len(desc) - len(desc.lstrip())]))
    line_zh = wiki_lookup(src["lines"], norm(seg))
    if line_zh:
        line_zh = _align_quotes(seg, line_zh, pairs)      # 名字对齐到标准表（装配/补位之前）
        sp = _split_zh_line(line_zh)
        if sp:
            out = line[:start] + _assemble_ww(sp[0], sp[1], col)
            if _ww_ok(out[start:]) and _names_consistent(line, out, pairs):
                return {"track": "workwiki", "zh": out, "term": m.group("name"),
                        "src": "本作wiki", "no_art": _no_art(line)}
    name_n = norm(m.group("name")).strip("『』")
    zh_name = src["names"].get(name_n)
    if not zh_name and terms:
        v = terms.get(name_n) or terms.get(m.group("name"))
        if isinstance(v, dict):
            v = v.get("zh")
        if isinstance(v, str) and v and v != name_n:
            zh_name = v
    zh_name = _std_zh_map(pairs).get(name_n.strip()) or zh_name     # 标准表优先
    zh_desc = wiki_lookup(src["descs"], norm(desc))
    if zh_desc:
        zh_desc = _align_quotes(desc, zh_desc, pairs)     # 说明里的引号名同样对齐
    if zh_name and zh_desc and norm(zh_name) != name_n:
        head_zh = line[start:m.start("name")] + zh_name + line[m.end("name"):m.start("desc")]
        out = line[:start] + _assemble_ww(head_zh, zh_desc, col)
        if _ww_ok(out[start:]) and _names_consistent(line, out, pairs):
            return {"track": "workwiki", "zh": out, "term": m.group("name"),
                    "src": "本作wiki", "no_art": _no_art(line)}
    return None


_ART_KANA_WW = set("つだりへぢづゑゐいうしふとこっッノミニハマレヘヾ")


def _ww_ok(zh_part):
    """直出件自检：无残留假名（艺术假名不算）、无替换符、非空。"""
    s = zh_part or ""
    if not s.strip() or "\ufffd" in s:
        return False
    for c in s:
        if ("\u3041" <= c <= "\u309f" or "\u30a1" <= c <= "\u30fa"
                or "\uff61" <= c <= "\uff9f") and c not in _ART_KANA_WW:
            return False
    return True


def _assemble_ww(head_zh, desc_zh, col):
    """头部 + 补位到源说明列 + 说明（与 struct 轨 assemble 同口径）。"""
    head = (head_zh or "").rstrip()
    gap = (col or 0) - _disp_width(head)
    if gap >= 1:
        full, half = divmod(gap, 2)
        head += "\u3000" * full + (" " if half else "")
    elif gap == 0:
        head += ""
    else:
        head += " "
    return head + (desc_zh or "")


def match_line(line, table, terms=None, work=None, work_dir=None, pairs=None):
    """六轨匹配（规则 > 模板 > 本作wiki > copy > 结构化 > 整行）。返回 dict 或 None。

    规则轨（状态行）在 copy 前：`とくこう：B` 这类全假名状态行曾同时落进 copy 轨
    （旧的纯图形判定用残留检测器 `kana_runs`，假名被当艺术假名排除）→ 被原样照抄而漏译。
    规则轨是 6 词词表精确匹配，优先它不会误伤。

    模板轨（L2）在 copy 前：`◎「技名」…　分类/类型/范围/威力/命中/接触/效果` 这类
    骨架行必须逐字一致（实测同一行模型给过 `物/龍/単` 和 `物/龙/单` 两种），
    且槽位全解析 + 覆盖后无假名残留才接管。

    本作 wiki 轨（2026-10-08）在 copy 前：`【特性】名…说明` / `『潜能名』…说明` 是本作
    wiki 的原文，命中即整行中文直出；排 copy 前是因为 copy 语义是"整行原样照抄"，
    对这几族曾被判"无需翻译"的日文行照抄 = 漏译。
    """
    hit = match_rule(line, table)
    if hit:
        return hit
    hit = match_tpl(line, table, terms)
    if hit:
        return hit
    if work or work_dir:
        w = work or os.path.basename(os.path.dirname(work_dir))
        hit = match_work_wiki(line, w, pairs=pairs, terms=terms)
        if hit:
            return hit
    hit = match_copy(line, table)
    if hit:
        return hit
    hit = match_struct(line, table, terms)
    if hit:
        return hit
    return match_whole(line, table)


# ---------------------------------------------------------------------------
# 判据 A（2026-10-08）：替换后还需不需要翻译
# ---------------------------------------------------------------------------
def pre_terms_module():
    """装载 pre_terms（宝可梦连的替换轨）；不可用返回 None。"""
    try:
        import pre_terms as PT
        return PT
    except Exception:
        return None


def residue_of(line, base=None, work=None, pairs=None):
    """把被术语替换覆盖的跨度挖掉 → (剩余片段, 命中列表, 替换后文本)。

    判据 A 的口径：**只看未被替换覆盖的片段**——里面有假名或非艺术汉字 = 还有日文要译；
    只剩 ASCII/标点/空白 = 这行替换完就等于译完（可零模型直出）。
    """
    PT = pre_terms_module()
    if PT is None:
        return line, [], line
    try:
        new, hits = PT.apply_hits(line, base=base, pairs=pairs, work=work)
    except Exception:
        return line, [], line
    if not hits:
        return line, [], new
    spans, pos = [], 0
    for h in sorted((h for h in hits if isinstance(h.get("at"), int)), key=lambda x: x["at"]):
        a = h["at"]
        if a > pos:
            spans.append(new[pos:a])
        pos = max(pos, a + len(h.get("zh") or ""))
    spans.append(new[pos:])
    return "".join(spans), hits, new


def _art_kanji_set():
    """aa_translate.ART_KANJI（艺术汉字白名单，两个副本共享同一份定义）。"""
    try:
        import aa_translate as A
        return A.ART_KANJI
    except Exception:
        return set()


def needs_translation(line, base=None, work=None, pairs=None):
    """判据 A：返回 (还需翻译?, 剩余片段, 命中列表, 替换后文本)。

    剩余片段里的判据：① 含假名（全角含 ーヾ；半角片假名也算）② 含非艺术汉字。
    只用「假名」不够——实测 `◎今回の編成（戦闘準備：>>1921）` 一个假名都没有，
    全是日文汉字；半角假名也不能漏（`ｶﾞｶﾞｶﾞ` 是艺术，但 `ｷｭｳｯ` 是台词，
    故半角假名一律算"有日文"，与 is_text_line 的重复度启发式不同——这里宁可送模型）。

    例外：`・`(U+30FB 片假名中点)、`･`(半角中点)、`゠` 是**分隔符**不是日文内容——
    实测 `・技マシン…　「A/B/C」` 与 `・「もちものセット」…` 两族因 `・` 落在
    U+30A0–30FF 区间被当成假名，替换后残留只剩标点却仍判"需翻译"（各 11 行）。
    """
    residue, hits, new = residue_of(line, base=base, work=work, pairs=pairs)
    if not residue.strip():
        return False, residue, hits, new
    core = "".join(c for c in residue if c not in _NEUTRAL_KANA)
    if _FW_KANA.search(core) or re.search(r"[\uff61-\uff9f]", core):
        return True, residue, hits, new
    art = _art_kanji_set()
    for c in core:
        if "\u4e00" <= c <= "\u9fff" and c not in art:
            return True, residue, hits, new
    return False, residue, hits, new


_NEUTRAL_KANA = set("・･゠")


def _entries(table, sect, terms):
    """manual 优先 + derived，合并成 {key: rec}（缓存挂在 table['_cache'] 上）。"""
    cache = table.setdefault("_cache", {})
    ck = f"{sect}:{'wiki' if terms is not None else 'self'}"
    hit = cache.get(ck)
    if hit is not None:
        return hit
    out = dict((table.get("derived") or {}).get(sect) or {})
    if terms is not None and sect == "struct":
        for k, v in (terms or {}).items():
            # 词表取值两种形态：wiki_terms() 的结构化 dict（带 effect_n）与合并词表的**纯字符串**
            # （作品表/本作 wiki 名字/补充术语）——后者只是译名，没有说明，不能当 struct 条目。
            if not isinstance(v, dict):
                continue
            if k not in out and v.get("effect_n"):
                out[k] = dict(v, _src="wiki")
            elif k in out:
                out[k] = dict(out[k], _src=out[k].get("_src", "wiki"))
    man = (table.get("manual") or {}).get(sect) or {}
    for k, v in man.items():
        out[k] = dict(v, _src=v.get("_src", "manual"))
    cache[ck] = out
    return out


def assemble(hit, src_line):
    """struct 命中 → 组装中文行：源前缀 + 『译名』… + pad 到源说明起始列 + 译文。"""
    pre, sep = hit.get("pre") or "", hit.get("sep") or "…"
    head = f"{pre}『{hit['zh_term']}』{sep}"
    pad_col = hit.get("col") or 0
    cur = _disp_width(head)
    gap = pad_col - cur
    if gap >= 1:
        full, half = divmod(gap, 2)
        head += "\u3000" * full + (" " if half else "")
    else:
        head += " "
    return head + hit["zh_desc"]


def _disp_width(s):
    try:
        import aa_translate as A
        return A.disp_width(s)
    except Exception:
        return sum(1 if ord(c) < 128 else 2 for c in (s or ""))


# ---------------------------------------------------------------------------
# 护栏（复用 TM 的四重护栏，口径完全一致）
# ---------------------------------------------------------------------------
def guard(src_line, zh_line, glossary_pairs=()):
    """① 宽度放得下 ② 无残留假名 ③ 术语表一致 ④ art 列位一致。返回 (ok, reason)。"""
    try:
        import aa_tm as TM
        return TM.guard(src_line, zh_line, glossary_pairs)
    except Exception as e:
        return False, f"no_guard:{e}"


def _no_art(line):
    """该行的固定内容改写是否需要跳过 art 列位校验 = 它是**纯文本行**。

    内容轨（状态行/模板/本作 wiki/结构化）只重写「文本单元」，行内 AA 笔画逐字保留，
    所以对纯文本行不需要 art 校验；反过来 art 校验会把 `（）、：-` 这类**文本标点**
    当成图形笔画 → 大量误拒（实测 `こうげき：A-`→`攻击：A-`、`『…』…（0.85倍）…` 全被拒）。
    混合行（AA 图形 + 文本单元）**不跳过**：那种行里文本单元可能夹在图形中，改宽会错位。
    """
    try:
        import aa_translate as A
        return A.line_kind(line) == "text"
    except Exception:
        return False


def allow_overwidth_src(line):
    """该源行是否允许超宽（text/mixed/header/skip 允许；art 严格等宽）——同 scan_width 口径。"""
    try:
        import aa_translate as A
        return A.allow_overwidth(line)
    except Exception:
        return False


def finalize(src_line, hit, glossary_pairs=()):
    """命中 → 落盘用整行（pad 到源宽）。返回 (ok, zh, why)。"""
    if hit.get("copy"):
        return True, src_line, ""              # copy 轨：原样照抄（零风险，连 padding 不动）
    try:
        import aa_translate as A
    except Exception as e:
        return False, None, f"no_aa:{e}"
    zh = hit["zh"] if hit.get("zh") else assemble(hit, src_line)
    # 2026-09-22: 表内 zh 存的是**内容**（键已归一化空白），行首缩进取**该次源行的**——
    # 否则 seed/whole 条目里带的那个前导全角空格会让内容整体右移 1 列（pad 只补行尾）。
    _lead = src_line[:len(src_line) - len(src_line.lstrip())]
    _core = zh.strip()
    if _lead:
        zh = _lead + _core
    ok, why = guard(src_line, zh, glossary_pairs)
    if not ok and hit.get("no_art") and why == "art":
        ok, why = True, ""            # 纯文本行（系统行/状态行/本作 wiki 行）：行内无 AA 笔画
    if not ok and why == "width" and allow_overwidth_src(src_line):
        # guard 的宽度闸是为 TM 复用/art 行设的「一律不超宽」；而项目口径（scan_width /
        # 工作台 save_line）对 text/mixed/header/skip **允许超宽**——译文比日文长是正常的。
        # 固定内容直出（潜能/特性说明的中文常比日文长）走这条豁免；art 行仍然严格。
        ok, why = True, ""
    if not ok:
        return False, None, why
    target = A.disp_width(src_line)
    if A.disp_width(zh.rstrip("\u3000 ")) < target:
        zh = A.pad_to_width(zh.rstrip("\u3000 "), target)
    if A.disp_width(zh) > target and not allow_overwidth_src(src_line):
        return False, None, "width"           # art 行严格等宽；text/mixed 允许超宽（2026-10-08）
    return True, zh, ""


# ---------------------------------------------------------------------------
# 建表
# ---------------------------------------------------------------------------
def build(work, aa_dir=None, out_dir=None, work_dir=None, rebuild=False, terms=None,
          limit_audit=200, quiet=False, reuse=False):
    """建/更新表。derived 重生成（manual 永不覆盖）；返回 stats dict。

    reuse: 额外从**本流程定稿章**（`work/pkmn_chapters.json`）回流整行条目（D6：旧 28 章不入表）。
    """
    if not (aa_dir and out_dir and work_dir):
        try:
            import registry
            cfg = registry.get(work)
            aa_dir, out_dir, work_dir = cfg["aa_dir"], cfg["out_dir"], cfg["work_dir"]
        except Exception:
            return {"error": "no_registry"}
    try:
        import aa_translate as A
        import aa_tm as TM
    except Exception as e:
        return {"error": f"import: {e}"}

    terms = terms or wiki_terms()
    if not terms:
        return {"error": f"wiki 缺失: {wiki_path()}"}
    old = None if rebuild else load(work, work_dir)
    table = old or empty_table(wiki_snapshot=time.strftime("%Y-%m-%d"),
                               note="derived=wiki 派生(+回流)；manual=人工修正，永不覆盖")
    table["derived"] = {"struct": {}, "whole": {},
                        "copy": (table.get("derived") or {}).get("copy", {}) or {},
                        "rules": {"state": {"map": dict(STATE_MAP)}}}
    auto_fail = []

    # ---- struct: wiki 派生（闸一在构建期先过一遍，脏条目只进 audit 不入表）
    n_ok = 0
    for k, v in terms.items():
        if not v.get("effect_n"):
            continue
        desc = v.get("desc") or ""
        if desc.strip() in BAD_DESC:
            auto_fail.append({"key": k, "jp": v["jp"], "why": "desc_placeholder", "desc": desc})
            continue
        if not v.get("zh"):
            auto_fail.append({"key": k, "jp": v["jp"], "why": "no_zh", "desc": desc})
            continue
        if A.kana_runs(desc):
            auto_fail.append({"key": k, "jp": v["jp"], "why": "desc_residual_kana", "desc": desc[:60]})
            continue
        if _disp_width(desc) + 4 < _disp_width(v["effect"]) * 0.5:
            auto_fail.append({"key": k, "jp": v["jp"], "why": "desc_too_short",
                              "desc": desc[:60], "effect": v["effect"][:60]})
            continue
        table["derived"]["struct"][k] = {
            "jp": v["jp"], "zh": v["zh"], "effect_n": v["effect_n"],
            "desc": desc, "cat": v.get("cat", ""), "sub": v.get("sub", ""), "_src": "wiki",
        }
        n_ok += 1

    # ---- whole: 回流（只收本流程定稿章）
    if reuse:
        st = collect_reuse(work, aa_dir, out_dir, work_dir, table, terms)
    else:
        st = {"reuse_kept": 0, "reuse_reject": {}}

    table["audit"]["auto_fail"] = auto_fail
    table["meta"]["counts"] = {
        "struct": len(table["derived"]["struct"]),
        "whole": len(table["derived"]["whole"]),
        "manual": len(table["manual"]["struct"]) + len(table["manual"]["whole"]),
        "auto_fail": len(auto_fail),
        "wiki_terms": len(terms),
    }
    table.pop("_cache", None)
    okf = save(work, table, work_dir) if work_dir else False
    if not quiet:
        c = table["meta"]["counts"]
        print(f"[fixed] {work}: struct {c['struct']:,} 条（wiki {c['wiki_terms']:,} 中）"
              f" | whole {c['whole']:,} | manual {c['manual']} | 闸一拒 {c['auto_fail']}"
              f" | 回流 {st.get('reuse_kept', 0):,} | 落盘 {okf}", flush=True)
    return {"table": table_path(work, work_dir), "counts": table["meta"]["counts"], **st}


def collect_reuse(work, aa_dir, out_dir, work_dir, table, terms, only=None):
    """从**本流程定稿章**回流整行条目（D6：旧已译章不进表）。

    定稿章判据 = `work/pkmn_chapters.json` 列出（pokemon_runner 每章成功后追加）。
    行须过 guard、且未被 struct 轨覆盖（struct 命中优先，避免重复）。
    """
    try:
        import aa_translate as A
    except Exception as e:
        return {"reuse_kept": 0, "reuse_reject": {"no_aa": 1}}
    p = os.path.join(work_dir, "pkmn_chapters.json")
    bases = []
    if os.path.exists(p):
        try:
            bases = json.load(open(p, encoding="utf-8")) or []
        except Exception:
            bases = []
    if only:
        bases = [b for b in bases if b in set(only)]
    reject = {}
    kept = 0
    meta_dir = os.path.join(work_dir, "line_meta_v2")
    for base in bases:
        sp = A.txt_existing(os.path.join(aa_dir, base + ".txt"))
        tp = A.txt_existing(os.path.join(out_dir, base + "_汉化.txt"))
        if not (os.path.exists(sp) and os.path.exists(tp)):
            continue
        src = A.txt_read(sp)
        tr = A.txt_read(tp)
        meta = A.load_meta_0based(meta_dir, base)
        try:
            gp = [(j, z) for j, z in A._load_glossary_pairs(base) if not j.startswith("re:")]
        except Exception:
            gp = []
        for i, line in enumerate(src):
            if i >= len(tr) or not line.strip():
                continue
            try:
                if not A.is_translatable(i, line, meta):
                    continue
            except Exception:
                continue
            if match_struct(line, table, terms):
                continue                              # struct 轨接管
            ok, why = guard(line, tr[i], gp)
            if not ok:
                reject[why] = reject.get(why, 0) + 1
                continue
            k = key(line)
            if len(k) < 6 or k in table["derived"]["whole"] or k in table["manual"]["whole"]:
                continue
            table["derived"]["whole"][k] = {
                "zh": tr[i], "w": A.disp_width(line), "base": base, "_src": "reuse",
            }
            kept += 1
    return {"reuse_kept": kept, "reuse_reject": reject}


def collect_copy(work, aa_dir=None, out_dir=None, work_dir=None, min_freq=2, bases=None,
                 quiet=False, save_it=True):
    """建 copy 轨（零翻译风险整行复用）：B 纯图形行 + A 已交付未改动行（Q14 A）。

    只在**归一化 key 全书出现 ≥ min_freq 次**时收录（只出现一次的行省不到什么，别撑大表）。
    命中时落盘 = 该次出现的源行原样（padding 不动）。
    """
    if not (aa_dir and out_dir and work_dir):
        try:
            import registry
            cfg = registry.get(work)
            aa_dir, out_dir, work_dir = cfg["aa_dir"], cfg["out_dir"], cfg["work_dir"]
        except Exception:
            return {"error": "no_registry"}
    try:
        import aa_translate as A
    except Exception as e:
        return {"error": f"import aa_translate: {e}"}
    table = load(work, work_dir)
    if not table:
        return {"error": "表不存在，先 --build"}
    meta_dir = os.path.join(work_dir, "line_meta_v2")
    freq, pure, unchanged, changed = {}, {}, {}, set()
    lines_n = 0
    for base in _aa_bases(aa_dir, bases):
        sp = A.txt_existing(os.path.join(aa_dir, base + ".txt"))
        if not os.path.exists(sp):
            continue
        lines = A.txt_read(sp)
        tp = A.txt_existing(os.path.join(out_dir, base + "_汉化.txt"))
        tr = A.txt_read(tp) if os.path.exists(tp) else None
        meta = A.load_meta_0based(meta_dir, base)
        for i, l in enumerate(lines):
            if not l.strip():
                continue
            try:
                if not A.is_translatable(i, l, meta):
                    continue
            except Exception:
                continue
            lines_n += 1
            k = key(l)
            if len(k) < COPY_MIN_LEN:
                continue
            freq[k] = freq.get(k, 0) + 1
            if tr is not None and i < len(tr):
                # 2026-09-22 修复：模型**动过**这行 ⇒ 判定为"有内容"⇒ 该 key 禁止进 copy 轨。
                # （entry_10437 smoke 实证：`ワー！ﾊﾟﾁﾊﾟﾁﾊﾟﾁ` 被旧判定当纯图形而永久跳过翻译，
                #   但已交付章里模型确实把它译了 ⇒ 交付证据优先于启发式。）
                if tr[i].strip() != l.strip():
                    changed.add(k)
                elif not has_real_text(l):
                    # 2026-09-22 二次修复：`unchanged` 只收"行内没有真日文内容"的行。
                    # 实证（5 章真机）：`…【現在のパーティ】（データは>>4-7にて）` 这类**带日文说明的图形行**
                    # 在 3 个已交付章里模型没译（旧交付的漏译），却被 `tr==src` 当成"稳定"证据收进 copy 轨
                    # → 新章照抄日文（refine_aa 只补回半句）。交付证据不等于"无需翻译"，必须叠加内容判据。
                    unchanged.setdefault(k, l)
            if is_pure_art(l) and not needs_rule(l, table):
                pure.setdefault(k, l)
    copy = {}
    for k, l in pure.items():
        if freq.get(k, 0) >= min_freq and k not in changed:
            copy[k] = {"_src": "pure_art", "n": freq[k]}
    for k, l in unchanged.items():
        if freq.get(k, 0) >= min_freq and k not in changed and k not in copy:
            copy[k] = {"_src": "unchanged", "n": freq[k]}
    drop_changed = sum(1 for k in pure if k in changed)
    table["derived"]["copy"] = copy
    table["meta"]["counts"]["copy"] = len(copy)
    table["meta"]["counts"]["copy_pure_art"] = sum(1 for v in copy.values() if v["_src"] == "pure_art")
    table["meta"]["counts"]["copy_unchanged"] = sum(1 for v in copy.values() if v["_src"] == "unchanged")
    table["meta"]["counts"]["copy_drop_changed"] = drop_changed
    if save_it:
        save(work, table, work_dir)
    if not quiet:
        c = table["meta"]["counts"]
        print(f"[copy] {work}: 送模型行 {lines_n:,} | copy 轨 {len(copy):,} 条"
              f"（纯图形 {c['copy_pure_art']:,} + 已交付未改动 {c['copy_unchanged']:,}）"
              f" | 交付证据否决(模型动过) {c.get('copy_drop_changed', 0):,}"
              f" | min_freq {min_freq}", flush=True)
    return {"copy": len(copy), "copy_pure_art": table["meta"]["counts"]["copy_pure_art"],
            "copy_unchanged": table["meta"]["counts"]["copy_unchanged"],
            "copy_drop_changed": drop_changed, "lines": lines_n}


# ---------------------------------------------------------------------------
# 扫族频次 + 种子轨（Q13 A：28 章只贡献 top-N 高频固定行的译文，逐条人工可见）
# ---------------------------------------------------------------------------
def _aa_bases(aa_dir, bases=None):
    import glob
    if bases:
        return list(bases)
    out = []
    for base in A.list_txt(aa_dir, exclude=("_台词.txt", "_汉化.txt")):
        if not base.endswith("_台词"):
            out.append(base)
    return out


def _keep_sample(sample, k, line, max_art=3):
    """给固定行留一条**最干净**的样例行（art 锚点最少）——送模型时不带图形噪声。"""
    try:
        import aa_translate as A
        na = len(A._art_anchors(line))
    except Exception:
        na = 99
    cur = sample.get(k)
    if cur is None or na < cur[0]:
        sample[k] = (na, line)


def scan_families(work=None, aa_dir=None, work_dir=None, bases=None, quiet=False):
    """扫原文统计固定行族频次与状态行覆盖率（零 API；810 章约 1~2 分钟）。

    返回 {lines, families, top{◎/【}, state_combos, state_unmatched}
    """
    import aa_translate as A
    if not aa_dir:
        try:
            import registry
            cfg = registry.get(work)
            aa_dir, work_dir = cfg["aa_dir"], cfg["work_dir"]
        except Exception:
            return {"error": "no_registry"}
    meta_dir = os.path.join(work_dir, "line_meta_v2")
    fam = {"状态(规则)": 0, "◎": 0, "【】": 0, "其他": 0}
    cnt = {"◎": {}, "【】": {}}
    sample = {}                       # key -> 原始样例行（供模型填充/人工审）
    combos, unmatched = {}, {}
    total = 0
    for base in _aa_bases(aa_dir, bases):
        sp = A.txt_existing(os.path.join(aa_dir, base + ".txt"))
        if not os.path.exists(sp):
            continue
        lines = A.txt_read(sp)
        meta = A.load_meta_0based(meta_dir, base)
        for i, l in enumerate(lines):
            if not l.strip():
                continue
            try:
                if not A.is_translatable(i, l, meta):
                    continue
            except Exception:
                continue
            total += 1
            m = STATE_RE.match(l)
            if m:
                k = (m.group("word"), m.group("grade"))
                combos[k] = combos.get(k, 0) + 1
                if m.group("word") in STATE_MAP:
                    fam["状态(规则)"] += 1
                else:
                    unmatched[k] = unmatched.get(k, 0) + 1
                continue
            s = l.lstrip().lstrip("\u3000")
            kk = key(l)
            if s.startswith("◎") or s.startswith("【"):
                fk = "◎" if s.startswith("◎") else "【】"
                fam[fk] += 1
                cnt[fk][kk] = cnt[fk].get(kk, 0) + 1
                _keep_sample(sample, kk, l)
            else:
                fam["其他"] += 1
    res = {"lines": total, "families": fam,
           "top": {k: sorted(v.items(), key=lambda x: -x[1]) for k, v in cnt.items()},
           "sample": sample, "state_combos": combos, "state_unmatched": unmatched}
    if not quiet:
        print(f"[scan] 送模型行 {total:,} | 族 {fam} | 状态组合 {len(combos)}"
              f"（未覆盖 {len(unmatched)}）", flush=True)
    return res


def _delivered_variants(aa_dir, out_dir, work_dir, keys, bases=None):
    """已交付章里这些 key 的译文变体：{key: {"zh": {variant: n}, "src": 样例行}}。"""
    import aa_translate as A
    meta_dir = os.path.join(work_dir, "line_meta_v2")
    out = {}
    for base in _aa_bases(aa_dir, bases):
        tp = A.txt_existing(os.path.join(out_dir, base + "_汉化.txt"))
        sp = A.txt_existing(os.path.join(aa_dir, base + ".txt"))
        if not (os.path.exists(tp) and os.path.exists(sp)):
            continue
        src, tr = A.txt_read(sp), A.txt_read(tp)
        meta = A.load_meta_0based(meta_dir, base)
        for i, l in enumerate(src):
            k = key(l)
            if k not in keys or i >= len(tr):
                continue
            rec = out.setdefault(k, {"zh": {}, "src": l})
            rec["zh"][tr[i]] = rec["zh"].get(tr[i], 0) + 1
    return out


def seed(work, top=300, families=("◎", "【】"), aa_dir=None, out_dir=None, work_dir=None,
         dry=False, quiet=False, bases=None):
    """种子轨：把 ◎/【】 高频固定行的译文入表（优先取已交付译文，逐条人工可审）。

    规则：
      · 已交付译文只有**一种变体** → 过闸一后入 `derived.whole`（_src=seed-delivered）
      · 多种变体（漂移）→ 写 `conflicts`，**不入表**（交人裁决）
      · 无已交付译文 → 写 `audit.pending_seed`（待模型/人工补）
    """
    if not (aa_dir and out_dir and work_dir):
        try:
            import registry
            cfg = registry.get(work)
            aa_dir, out_dir, work_dir = cfg["aa_dir"], cfg["out_dir"], cfg["work_dir"]
        except Exception:
            return {"error": "no_registry"}
    table = load(work, work_dir)
    if not table:
        return {"error": "表不存在，先 --build"}
    scan = scan_families(work, aa_dir, work_dir, bases=bases, quiet=True)
    if "error" in scan:
        return scan
    picked = []
    for fam in families:
        for k, c in (scan["top"].get(fam) or [])[:top]:
            picked.append((fam, k, c))
    keys = {k for _, k, _ in picked}
    dv = _delivered_variants(aa_dir, out_dir, work_dir, keys, bases=bases)
    smp = scan.get("sample") or {}
    st = {"picked": len(picked), "seeded": 0, "conflict": 0, "pending": 0, "reject": {}}
    conflicts, pending = [], []
    for fam, k, c in picked:
        rec = dv.get(k)
        if not rec:
            pending.append({"key": k, "family": fam, "count": c,
                            "src": (smp.get(k) or (0, ""))[1]})
            st["pending"] += 1
            continue
        variants = rec["zh"]
        if len(variants) > 1:
            conflicts.append({"key": k, "family": fam, "count": c,
                              "variants": [{"zh": z, "n": n} for z, n in
                                           sorted(variants.items(), key=lambda x: -x[1])]})
            st["conflict"] += 1
            continue
        zh = next(iter(variants))
        ok, why = guard_build(zh, rec["src"])
        if not ok:
            st["reject"][why] = st["reject"].get(why, 0) + 1
            continue
        _sus = suspect_zh(zh)
        if _sus:
            st.setdefault("suspect", 0)
            st["suspect"] += 1
            table.setdefault("audit", {}).setdefault("suspect", []).append(
                {"kind": _sus, "key": k, "src": rec["src"], "zh": zh.strip(),
                 "family": fam, "n": c, "_src": "seed-delivered"})
            continue
        table["derived"]["whole"][k] = {"zh": zh.rstrip("\u3000 "), "w": _disp_width(rec["src"]),
                                        "_src": "seed-delivered", "family": fam, "n": c}
        st["seeded"] += 1
    for cf in conflicts:                      # 冲突去重合并（保留最新）
        table["conflicts"] = [c for c in table.get("conflicts", []) if c.get("key") != cf["key"]]
        table["conflicts"].append(cf)
    table["audit"]["pending_seed"] = pending
    table["meta"]["counts"]["whole"] = len(table["derived"]["whole"])
    table["meta"]["counts"]["conflicts"] = len(table.get("conflicts") or [])
    if not dry:
        save(work, table, work_dir)
    if not quiet:
        print(f"[seed] 候选 {st['picked']} | 入表 {st['seeded']} | 冲突(漂移,待人裁) {st['conflict']}"
              f" | 待补(无已交付译文) {st['pending']} | 护栏拒 {st['reject']}", flush=True)
    return st


# ---------------------------------------------------------------------------
# 种子补齐（模型一次性翻译 pending_seed；Q13 A 的"缺口用模型补齐"，约 ¥0.05）
# ---------------------------------------------------------------------------
SYS_FIXED = """你在汉化日式AA漫画（やる夫スレ）里的**固定格式行**。这些行在作品里反复出现
（战斗提示、系统标题、状态栏等），译文将被复用上千次，因此必须准确、简洁、口径统一。

输入：JSON 数组，每项 {"i": 序号, "jp": 日文行}。逐行翻译并输出 JSON 数组，每项 {"i": 同序号, "zh": 中文行}。

要求：
1. 行数与序号必须与输入完全一致，不得增删行、不得合并。
2. **符号与结构原样保留**：◎ / 【】 / 『』 / 「」 / ： / （） / 全角空格与行内填充；行首缩进与「【】」方括号不可改动。
3. 术语用给定译名对照；宝可梦名/招式/道具/特性一律用宝可梦官方中文名。
4. 数字、等级代号（A/B/S/C 等）、URL、T 计数等保持原样（全角数字转半角）。
5. 只输出 JSON 数组，不要解释、不要代码块。
"""


def _seed_hint(lines, terms):
    """行内出现的 wiki 术语对照（jp→zh），供模型统一口径。"""
    pairs = {}
    for l in lines:
        for k, v in terms.items():
            if v.get("jp") and v.get("zh") and v["jp"] in l:
                pairs[v["jp"]] = v["zh"]
    return pairs


def fill_seed(work, batch=25, workers=4, limit=None, aa_dir=None, out_dir=None,
              work_dir=None, dry=False, quiet=False):
    """把 `audit.pending_seed` 里（无已交付译文）的高频固定行交模型一次性翻译并入表。"""
    if not work_dir:
        try:
            import registry
            cfg = registry.get(work)
            aa_dir, out_dir, work_dir = cfg["aa_dir"], cfg["out_dir"], cfg["work_dir"]
        except Exception:
            return {"error": "no_registry"}
    try:
        import aa_backend
    except Exception as e:
        return {"error": f"import aa_backend: {e}"}
    table = load(work, work_dir)
    if not table:
        return {"error": "表不存在"}
    pend = list(table["audit"].get("pending_seed") or [])
    if limit:
        pend = pend[:limit]
    if not pend:
        if not quiet:
            print("[fill] pending_seed 为空")
        return {"filled": 0, "reject": {}, "failed": 0}
    terms = wiki_terms()
    os.environ["AA_USAGE_TAG"] = "pokemonfixed"
    items = [(i, p) for i, p in enumerate(pend) if (p.get("src") or "").strip()]
    chunks = [items[i:i + batch] for i in range(0, len(items), batch)]

    def work_one(ch):
        body = json.dumps([{"i": i, "jp": p["src"]} for i, p in ch], ensure_ascii=False)
        hint = _seed_hint([p["src"] for _, p in ch], terms)
        if hint:
            body = "已知译名对照：" + json.dumps(hint, ensure_ascii=False) + "\n" + body
        try:
            txt, _be = aa_backend.call_with_fallback(False, SYS_FIXED, body,
                                                     max_tokens=4096, timeout=240)
        except Exception as e:
            return ch, {}, str(e)
        got = {}
        t = (txt or "").strip()
        t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
        i0, j0 = t.find("["), t.rfind("]")
        if i0 >= 0 and j0 > i0:
            t = t[i0:j0 + 1]
        try:
            arr = json.loads(t)
        except Exception as e:
            return ch, {}, f"json:{e}"
        for it in arr:
            if isinstance(it, dict) and "zh" in it:
                try:
                    got[int(it.get("i"))] = str(it["zh"])
                except Exception:
                    continue
        return ch, got, ""

    st = {"filled": 0, "reject": {}, "failed": 0, "pending_left": 0}
    import concurrent.futures as cf
    done_keys = set()
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for ch, got, err in ex.map(work_one, chunks):
            if err:
                st["failed"] += len(ch)
                continue
            for i, p in ch:
                zh = (got.get(i) or "").strip()
                if not zh:
                    st["failed"] += 1
                    continue
                ok, why = guard_build(zh, p["src"])
                if not ok:
                    st["reject"][why] = st["reject"].get(why, 0) + 1
                    continue
                _sus = suspect_zh(zh)
                if _sus:
                    st["reject"][_sus] = st["reject"].get(_sus, 0) + 1
                    table.setdefault("audit", {}).setdefault("suspect", []).append(
                        {"kind": _sus, "key": p["key"], "src": p["src"], "zh": zh,
                         "family": p.get("family", ""), "_src": "seed-model"})
                    continue
                table["derived"]["whole"][p["key"]] = {
                    "zh": zh, "w": _disp_width(p["src"]), "_src": "seed-model",
                    "family": p.get("family", ""), "n": p.get("count", 0),
                }
                done_keys.add(p["key"])
                st["filled"] += 1
    left = [p for p in table["audit"].get("pending_seed", []) if p["key"] not in done_keys]
    table["audit"]["pending_seed"] = left
    st["pending_left"] = len(left)
    table["meta"]["counts"]["whole"] = len(table["derived"]["whole"])
    if not dry:
        save(work, table, work_dir)
    if not quiet:
        print(f"[fill] 入表 {st['filled']}（seed-model）| 失败 {st['failed']}"
              f" | 护栏拒 {st['reject']} | 待补剩 {st['pending_left']}", flush=True)
    return st


# ---------------------------------------------------------------------------
# 预填（翻译前写 ckpt done = 跳过送模型）
# ---------------------------------------------------------------------------
def ckpt_path(work_dir, base):
    return os.path.join(work_dir, base + ".txt.ckpt.json")


def _read_txt(path):
    """读 .txt / .txt.gz 成行列表（口径同全库：split('\\n')，不 trim）。"""
    if path.endswith(".gz"):
        import gzip
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return f.read().split("\n")
    with open(path, encoding="utf-8") as f:
        return f.read().split("\n")


def normalize_chapters(work, bases, aa_dir=None, out_dir=None, work_dir=None, dry=False,
                       stamp=None):
    """L2 后置规范化：把已译章里的骨架字段拉回同一口径（零 API）。

    改的是**已汉化行**的骨架（状态行词/等级、招式行的 技名/分类/类型/范围/接触、
    效果里的假名状态词与「N割」），不碰模型写的正文；解析不全的行原样保留。
    行数与原文不一致的章跳过（防行号错位），改动前整章备份到 `work/backup_tpl_<ts>/`。
    """
    if not (aa_dir and out_dir and work_dir):
        try:
            import registry
            cfg = registry.get(work)
            aa_dir, out_dir, work_dir = cfg["aa_dir"], cfg["out_dir"], cfg["work_dir"]
        except Exception:
            return {"error": "no_registry"}
    try:
        import aa_translate as A
    except Exception as e:
        return {"error": f"import aa_translate: {e}"}
    table = load(work, work_dir)
    terms = merged_terms(work, work_dir)     # 本作 wiki 名字/补充术语也进槽位解析（同 prefill）
    stamp = stamp or time.strftime("%Y%m%d_%H%M%S")
    bdir = os.path.join(work_dir, "backup_tpl_" + stamp)
    st = {"chapters": 0, "changed": 0, "skipped": 0, "backup": "", "samples": []}
    for base in bases:
        zp = A.txt_existing(os.path.join(out_dir, base + "_汉化.txt"))
        if not os.path.exists(zp):
            continue
        src_p = A.txt_existing(os.path.join(aa_dir, base + ".txt"))
        try:
            lines, src = _read_txt(zp), _read_txt(src_p)
        except Exception:
            st["skipped"] += 1
            continue
        if len(src) != len(lines):               # 行号对齐护栏：不一致宁可不动
            st["skipped"] += 1
            continue
        new = [normalize_tpl(l, table, terms) for l in lines]
        # 源行模板回填：原文命中整句模板的行，汉化行一律取模板译文
        # （模板是权威口径；模型此前在这些系统行上留下未译/半译，正是用户报的"模板没翻译"）
        tpl_fixed = 0
        for i, sl in enumerate(src):
            h = match_line(sl, table, terms, work=work)
            if not h or h.get("track") != "tpl" or not str(h.get("rule", "")).startswith("模板:"):
                continue
            ok, exp, _why = finalize(sl, h)
            if ok and exp and not _kana_any(exp) and new[i] != exp:
                new[i] = exp
                tpl_fixed += 1
        st["tpl_fixed"] = st.get("tpl_fixed", 0) + tpl_fixed
        ch = [i for i, (a, b) in enumerate(zip(lines, new)) if a != b]
        if not ch:
            continue
        st["chapters"] += 1
        st["changed"] += len(ch)
        if len(st["samples"]) < 3:
            i = ch[0]
            st["samples"].append((base, i + 1, lines[i].strip()[:60], new[i].strip()[:60]))
        if dry:
            continue
        os.makedirs(bdir, exist_ok=True)
        import shutil
        shutil.copy2(zp, os.path.join(bdir, os.path.basename(zp)))
        A.txt_write(zp, new)
        st["backup"] = bdir
    if st["backup"]:
        A.prune_backup_dirs(work_dir, "backup_tpl_")     # 同类只留最近 2 次运行（2026-10-01）
    return st


def prefill(work, bases, aa_dir=None, out_dir=None, work_dir=None, quiet=False, dry=False):
    """把固定表命中的行写进各章 ckpt 的 done（零 API）。返回 stats dict。

    `AA_FIXED_TABLE=0` 时整链关闭（A/B 对照用）：返回 disabled=True，不写任何 ckpt。
    """
    if not enabled():
        return {"disabled": True, "chapters": 0, "lines": 0, "hit": 0,
                "hit_struct": 0, "hit_whole": 0, "hit_rule": 0, "hit_copy": 0, "reject": {}}
    if not (aa_dir and out_dir and work_dir):
        try:
            import registry
            cfg = registry.get(work)
            aa_dir, out_dir, work_dir = cfg["aa_dir"], cfg["out_dir"], cfg["work_dir"]
        except Exception:
            return {"error": "no_registry"}
    try:
        import aa_translate as A
    except Exception as e:
        return {"error": f"import aa_translate: {e}"}
    table = load(work, work_dir)
    if not table:
        return {"error": "表不存在，先 --build"}
    terms = merged_terms(work, work_dir)     # 通用 wiki ∪ 作品表 ∪ 本作 wiki 名字（L2 槽位解析用）
    meta_dir = os.path.join(work_dir, "line_meta_v2")
    hits_dir = os.path.join(work_dir, "fixed_hits")
    st = {"chapters": 0, "lines": 0, "hit_struct": 0, "hit_whole": 0, "hit_rule": 0, "hit_copy": 0,
          "hit_tpl": 0, "hit_workwiki": 0, "hit_preterm": 0, "reject": {}, "skipped_chapters": 0}
    for base in bases:
        sp = A.txt_existing(os.path.join(aa_dir, base + ".txt"))
        if not os.path.exists(sp):
            st["skipped_chapters"] += 1
            continue
        lines = A.txt_read(sp)
        meta = A.load_meta_0based(meta_dir, base)
        cp = ckpt_path(work_dir, base)
        done = {}
        if os.path.exists(cp):
            try:
                d = json.load(open(cp, encoding="utf-8"))
                if d.get("__lines") == len(lines):
                    done = {int(k): v for k, v in d.items() if k != "__lines"}
            except Exception:
                done = {}
        try:
            gp = [(j, z) for j, z in A._load_glossary_pairs(base) if not j.startswith("re:")]
        except Exception:
            gp = []
        try:                                   # 判据 A 用：本章的替换轨词表
            pt_pairs = pre_terms_module().pairs_for(base, work=work) if pre_terms_module() else []
        except Exception:
            pt_pairs = []
        hits = {}
        for i, line in enumerate(lines):
            if not line.strip() or i in done:
                continue
            try:
                if not A.is_translatable(i, line, meta):
                    continue
            except Exception:
                continue
            st["lines"] += 1
            hit = match_line(line, table, terms, work=work, pairs=pt_pairs)
            if not hit and preterm_enabled():
                # 判据 A（2026-10-08）：替换后残留只剩 ASCII/标点/空白 = 替换完即译完 → 直出。
                # 与固定内容同一条路（写 ckpt done + 四重护栏），只是"译文"来自替换后的文本。
                need, _res, phits, new = needs_translation(line, base=base, work=work, pairs=pt_pairs)
                if not need and phits:
                    hit = {"track": "preterm", "zh": new, "term": "", "src": "替换轨",
                           "no_art": _no_art(line)}
            if not hit:
                continue
            ok, zh, why = finalize(line, hit, gp)
            if not ok:
                st["reject"][why] = st["reject"].get(why, 0) + 1
                continue
            done[i] = zh
            st["hit_" + hit["track"]] = st.get("hit_" + hit["track"], 0) + 1
            hits[str(i)] = {"track": hit["track"], "term": hit.get("term", ""),
                            "src": hit.get("src", ""), "zh": zh}
        st["chapters"] += 1
        if dry or not hits:
            continue
        os.makedirs(hits_dir, exist_ok=True)
        with open(os.path.join(hits_dir, base + ".json"), "w", encoding="utf-8") as f:
            json.dump({"base": base, "chapters_lines": len(lines), "hits": hits},
                      f, ensure_ascii=False, indent=1)
        if done:
            A._atomic_write_ckpt(cp, {"__lines": len(lines),
                                      **{str(k): v for k, v in done.items()}})
    if not quiet:
        hit = sum(st.get("hit_" + t, 0) for t in ("struct", "whole", "rule", "copy", "tpl",
                                                  "workwiki", "preterm"))
        pct = hit / st["lines"] * 100 if st["lines"] else 0
        print(f"[fixed] 预填 {st['chapters']} 章 | 送模型行 {st['lines']:,} | 命中 {hit:,} "
              f"（struct {st['hit_struct']:,} / whole {st['hit_whole']:,} / rule {st.get('hit_rule', 0):,}"
              f" / copy {st.get('hit_copy', 0):,} / tpl {st.get('hit_tpl', 0):,}"
              f" / 本作wiki {st.get('hit_workwiki', 0):,} / 替换直出 {st.get('hit_preterm', 0):,}）"
              f" = {pct:.1f}% | 护栏拒 {st['reject']}", flush=True)
    return st


# ---------------------------------------------------------------------------
# 回放（验收①：表 vs 已交付译文，零 API）
# ---------------------------------------------------------------------------
def replay(work, bases=None, max_examples=200, aa_dir=None, out_dir=None, work_dir=None):
    """把表拿去匹配**已交付**的章，比对"表译文 vs 已交付译文"（不动任何文件）。"""
    if not (aa_dir and out_dir and work_dir):
        try:
            import registry
            cfg = registry.get(work)
            aa_dir, out_dir, work_dir = cfg["aa_dir"], cfg["out_dir"], cfg["work_dir"]
        except Exception:
            return {"error": "no_registry"}
    import glob
    try:
        import aa_translate as A
    except Exception as e:
        return {"error": f"import aa_translate: {e}"}
    table = load(work, work_dir)
    if not table:
        return {"error": "表不存在，先 --build"}
    terms = merged_terms(work, work_dir)
    meta_dir = os.path.join(work_dir, "line_meta_v2")
    if bases is None:
        bases = []
        for base in A.list_txt(aa_dir, exclude=("_台词.txt", "_汉化.txt")):
            if base.endswith("_台词"):
                continue
            if os.path.exists(A.txt_existing(os.path.join(out_dir, base + "_汉化.txt"))):
                bases.append(base)
    res = {"chapters": 0, "lines": 0, "hit_struct": 0, "hit_whole": 0, "hit_rule": 0, "hit_copy": 0,
           "same": 0, "diff": 0, "reject": {}, "examples": [], "by_term": {}}
    for base in bases:
        sp = A.txt_existing(os.path.join(aa_dir, base + ".txt"))
        tp = A.txt_existing(os.path.join(out_dir, base + "_汉化.txt"))
        if not (os.path.exists(sp) and os.path.exists(tp)):
            continue
        src = A.txt_read(sp)
        tr = A.txt_read(tp)
        meta = A.load_meta_0based(meta_dir, base)
        try:
            gp = [(j, z) for j, z in A._load_glossary_pairs(base) if not j.startswith("re:")]
        except Exception:
            gp = []
        res["chapters"] += 1
        for i, line in enumerate(src):
            if i >= len(tr) or not line.strip():
                continue
            try:
                if not A.is_translatable(i, line, meta):
                    continue
            except Exception:
                continue
            res["lines"] += 1
            hit = match_line(line, table, terms)
            if not hit:
                continue
            ok, zh, why = finalize(line, hit, gp)
            if not ok:
                res["reject"][why] = res["reject"].get(why, 0) + 1
                continue
            res["hit_" + hit["track"]] = res.get("hit_" + hit["track"], 0) + 1
            term = hit.get("term") or ""
            _tk = term if term else f"（{hit['track']} 轨整行）"   # 空 term 会把 copy/rule 全并成一条 → 按轨命名
            res["by_term"][_tk] = res["by_term"].get(_tk, 0) + 1
            if zh.rstrip() == tr[i].rstrip():
                res["same"] += 1
            else:
                res["diff"] += 1
                if len(res["examples"]) < max_examples:
                    res["examples"].append({"base": base, "line": i, "track": hit["track"],
                                            "term": term, "src": line, "table": zh,
                                            "delivered": tr[i]})
    hit = sum(res.get("hit_" + t, 0) for t in ("struct", "whole", "rule", "copy"))
    res["hit"] = hit
    res["hit_pct"] = (hit / res["lines"] * 100) if res["lines"] else 0.0
    res["same_pct"] = (res["same"] / hit * 100) if hit else 0.0
    return res


def write_replay_report(work, res, work_dir=None):
    if work_dir is None:
        try:
            import registry
            work_dir = registry.get(work)["work_dir"]
        except Exception:
            return None
    p = os.path.join(work_dir, "fixed_replay_report.md")
    top = sorted(res.get("by_term", {}).items(), key=lambda x: -x[1])[:40]
    with open(p, "w", encoding="utf-8") as f:
        f.write(f"# 固定表回放报告 — {work}\n\n")
        f.write(f"- 日期: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- 章 {res['chapters']} / 送模型行 {res['lines']:,}\n")
        f.write(f"- 命中 {res['hit']:,}（struct {res['hit_struct']:,} / whole {res['hit_whole']:,}"
                f" / rule {res.get('hit_rule', 0):,} / copy {res.get('hit_copy', 0):,}）"
                f" = **{res['hit_pct']:.1f}%**\n")
        f.write(f"- 与已交付译文逐字相同 {res['same']:,} / 不同 {res['diff']:,}"
                f" = 相同率 **{res['same_pct']:.1f}%**\n")
        f.write(f"- 护栏拒: {res['reject']}\n\n## 命中条目 top 40\n\n")
        for t, c in top:
            f.write(f"- {c:5d}  {t}\n")
        f.write("\n## 差异样例（表 / 已交付）\n\n")
        for e in res.get("examples", [])[:80]:
            f.write(f"### {e['base']} line {e['line']} [{e['track']}/{e['term']}]\n")
            f.write(f"- SRC : {e['src'].rstrip()}\n- 表  : {e['table'].rstrip()}\n"
                    f"- 已交付: {e['delivered'].rstrip()}\n\n")
    return p


def stats(work, work_dir=None):
    t = load(work, work_dir)
    if not t:
        return {"error": "表不存在"}
    c = (t.get("meta") or {}).get("counts") or {}
    by_cat = {}
    for k, v in (t["derived"]["struct"] or {}).items():
        by_cat[v.get("cat", "?")] = by_cat.get(v.get("cat", "?"), 0) + 1
    return {"path": table_path(work, work_dir), "counts": c, "struct_by_cat": by_cat,
            "manual": {s: len(t["manual"].get(s) or {}) for s in ("struct", "whole")},
            "auto_fail": len(t["audit"].get("auto_fail") or []),
            "conflicts": len(t.get("conflicts") or [])}
