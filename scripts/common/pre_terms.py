# -*- coding: utf-8 -*-
r"""翻译前术语强制替换（2026-09-22 · 宝可梦专属链）。

## 为什么

作品术语表把 wiki 条目**注入 prompt** 的做法在本作实测无效：同一条目在已交付章里
仍出现多种译法（`気合い`→气合/气势/毅力、`エース`→エース/王牌、道具名照旧乱翻），
角色名也一样。改为**翻译前强制替换**——在「送模型的那份文本」上先把术语换成标准
译名，模型只需围绕既成事实译其余部分（"翻译时不再注入 wiki"）。

## 铁律

- **只改送模型的文本**：原文文件、宽度 pad、art 列位校验、残留假名检测一律仍用**原行**
  （`aa_translate` 的 body 拼装点调用本模块，其余路径不碰）→ 零宽度/零 art 风险。
- **每次每章现查 wiki**：词源 = 通用 AA wiki（`ポケモンAA术语wiki` 的 `术语表.json`
  术语 + `官方译名对照.json` 官方名）∪ **本作设定 wiki**（`doc/aa作品相关/<作品>wiki`
  的页面标题 + 术语表 + `人工修正.json` 的 `本地译名`）∪ 作品术语表；再按**本章语料**
  过滤，只替换本章真出现的词（`wiki_terms()` 是"全库导入"，那样等于给每章塞 6000 个词）。
- **优先级**：作品术语表(2) > 本作 wiki / 人工补充·作品专属(1) > 通用 AA wiki(0)；
  同优先级时先收集者赢（人工增量在前）。`zh == jp` / 单字词 / 空译名不替换。
- **本作 wiki 词源**（2026-10-03）：本作自己的设定 wiki 提供别处查不到的**本作口径**——
  本作宝可梦个体/人物的「名前」写法（含 576 个体 + 97 人物）与本作原创招式/特性/潜能；
  `人工修正.json` 的 `本地译名` 是人工裁定（含联网核定的官方译名），标 `本作wiki-人工`。
  目录解析：env `AA_WORK_WIKI_DIR` > `work_meta[work]["wiki_dir"]` > `doc/aa作品相关/<作品>wiki`。

## 匹配口径

长词优先 + 非重叠；假名开头加**片假名邻接**守卫——左 `(?<![ァ-ヺー・])` 防
`アメリカ` 里的 `リカ` 式碎片、右 `(?![ァ-ヺー・])` 防 `エース` 吃掉 `エースキラー`。
守卫**对所有长度生效**（2026-09-30 修：原来只给 ≤4 字套守卫，`ストライク` 于是吃掉
`ストライクゾーン`/`ストライク・ジークヴルム`）。左守卫**只防片假名**：左侧平假名多为助词
（`とカズマ`/`はカズマ`），拦了会大面积漏替换（2026-09-22 实测踩过）。词表里更长的词由
"长词优先"先吃，守卫只兜表外复合词。
`re:` 前缀对按正则处理（项目既有约定）；含拉丁字母的词忽略大小写。

## 歧义守卫（2026-09-30）

「宝可梦名 = 日语常用词」的同形条目（`ラッキー` 吉利蛋 / `プラス` 正电 / `クラブ` 大钳蟹）
无条件替换必然误伤——实测本作 150+ 行已出货错译（`当たったらラッキー程度`→`也就吉利蛋程度`）。
规则写在 `补充术语.json` 的「歧义」段（唯一手改入口），按 **jp** 匹配、与词条来自哪张表无关：

- `禁`        该 jp 全来源不替换
- `require`  命中处 ±`require距离`(默认 60) 内必须出现其一，否则不替换
- `deny`     命中处 ±`deny距离`(默认 10) 内出现其一则不替换
- `允许`     显式豁免"≤2 纯假名字目默认不替换"（`めい`/`あの`/`あわ` 这类 2 字条目
  没有词界可言，任何含该片段的常用词都会中招：`煌めいて`→`煌逃足奇袭て`）
- `zh` + `require`（**替译**，2026-10-09）同一 jp 的**两义**——不是"换/不换"，而是
  "换哪个"：语境命中 → 出 `zh`，否则出词表里的默认译名。用于「道具简写 = 角色名」
  这类同形两义（`ラム` = 木子（`ラムのみ` 的简写）/ 拉姆（宝可梦个体名））。没有 `zh`
  的条目仍是上面的"换/不换"语义（`ラッキー`/`カウンター` 等），两套语义不混。

## 开关

env `AA_PRE_TERMS=0` 关闭；非宝可梦管线（`work_pipeline.is_pokemon`）默认不启用。
本作 wiki 词源单独开关：env `AA_PRE_TERMS_WORK_WIKI=0`；目录覆盖 env `AA_WORK_WIKI_DIR`。
显式传 `work=`/`force=True` 可跳过管线判据（报告工具/单测用）。
"""
import collections
import json
import os
import re
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
ROOT = os.path.dirname(_SCRIPTS)
WIKI_DIR_REL = os.path.join("doc", "aa作品相关", "ポケモンAA术语wiki")
WIKI_TERMS = "术语表.json"
WIKI_OFFICIAL = "官方译名对照.json"
WIKI_EXTRA = "补充术语.json"          # 人工增量：通用补充 + 作品专属项（生成器会合并进 wiki）

# 本作设定 wiki（`doc/aa作品相关/<作品>wiki`，由 gen_konosin_wiki.py 之类生成器产出）：
# 页面标题 = 本作宝可梦个体/人物的「名前」口径，术语表 = 本作原创招式/特性/潜能。
# 它是**本作自己的**口径（已经过 人工修正.json 裁定），故排在通用 AA wiki 之前、
# 人工作品术语表之后；词源标签 `本作wiki`，人工裁定表 `本作wiki-人工`。
WIKI_WORK_ROOT_REL = os.path.join("doc", "aa作品相关")
WORK_WIKI_TERMS = "术语表.json"
WORK_WIKI_EXTRA = "人工修正.json"      # 人工裁定：`本地译名`/`修正`（后者只作提示，不整句替换）
WORK_WIKI_SRC = "本作wiki"
WORK_WIKI_SRC_MANUAL = "本作wiki-人工"
WORK_WIKI_QUOTES_SRC = "本作wiki-引号"
_GENERIC_WIKI_LEAF = "ポケモンAA术语wiki"   # 通用词源目录，别当成本作 wiki

_KANA_L = re.compile(r"[ぁ-ゖァ-ヺー・]")
_KATA_END = re.compile(r"[ァ-ヺー]")
_LATIN = re.compile(r"[A-Za-z]")
_SINGLE_CJK = re.compile(r"^[\u4e00-\u9fff]$")
_SHORT_KANA = re.compile(r"^[ぁ-ゖァ-ヺー・]{1,2}$")   # ≤2 纯假名：没有词界，默认不替换

_lock = threading.Lock()
_wiki_cache = {"key": None, "pairs": None}
_wwiki_cache = {"key": None, "pairs": None}
_extra_cache = {"key": None, "pairs": None}
_amb_cache = {"key": None, "rules": None}
_pipe_cache = {}
_log_once = set()            # 章首词表日志只打一次（多线程并发构建时会重复）
_chapter_cache = {}          # (work, base, mtime, lines) -> compiled pairs


# ---------------------------------------------------------------------------
# wiki
# ---------------------------------------------------------------------------
def wiki_dir(path=None):
    return path or os.environ.get("AA_POKEMON_WIKI_DIR") or os.path.join(ROOT, WIKI_DIR_REL)


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# 本作设定 wiki（doc/aa作品相关/<作品>wiki）
# ---------------------------------------------------------------------------
def _work_meta(work):
    """`aa_web_app/data/work_meta.json` 里该作品的条目（读不到返回 {}）。"""
    p = os.path.join(ROOT, "aa_web_app", "data", "work_meta.json")
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    e = d.get(work)
    return e if isinstance(e, dict) else {}


def work_wiki_dir(work=None, path=None):
    """本作设定 wiki 目录 → 绝对路径；没有则 ""（该词源缺席，不影响其它词源）。

    优先级：显式 path / env `AA_WORK_WIKI_DIR` > `work_meta[work]["wiki_dir"]`
    （相对仓库根或绝对路径）> 约定 `doc/aa作品相关/<作品>wiki`。
    通用词源目录 `ポケモンAA术语wiki` 永不当选（那是 wiki_dir() 的词源，两者独立）。
    """
    if path:
        d = path
    else:
        d = os.environ.get("AA_WORK_WIKI_DIR", "").strip()
        if not d and work:
            d = (_work_meta(work).get("wiki_dir") or "").strip()
        if not d and work:
            d = os.path.join(WIKI_WORK_ROOT_REL, work + "wiki")
    if not d:
        return ""
    if not os.path.isabs(d):
        d = os.path.join(ROOT, d)
    d = os.path.abspath(d)
    if os.path.basename(d) == _GENERIC_WIKI_LEAF or not os.path.isdir(d):
        return ""
    return d


def work_wiki_enabled():
    return os.environ.get("AA_PRE_TERMS_WORK_WIKI", "1").strip() != "0"


def _quoted_pairs(data):
    """平行对里「同位置引号内容」的名字对 → [(jp, zh, src, cat)]。

    本作 wiki 页正文里有大量名字（队伍/道具/潜能/形态）只在**平行对**里出现，
    没进 `terms[]`，此前因此漏替换：编成表里 `《星ノ紅玉》`、`『金魔女の陰謀』`、
    `『鉄沼魚の巨腕』`、`かっこよさ` 全是日文原样送模型（pokemon_fixed 侧的同名
    收集只喂模板/名字解析，替换轨看不到）。约束见 `pokemon_fixed._harvest_quoted_pairs`
    （两侧引号数一一对应、中文侧不得含假名）。
    `AA_PRE_TERMS_WORK_WIKI_QUOTES=0` 关闭。
    """
    if os.environ.get("AA_PRE_TERMS_WORK_WIKI_QUOTES", "1").strip() == "0":
        return []
    try:
        import pokemon_fixed as _PF          # 惰性导入：pokemon_fixed 反向引用本模块也是惰性，不成环
        names = {}
        _PF._harvest_quoted_pairs(data, names)
    except Exception:
        return []
    return [(jp, zh, WORK_WIKI_QUOTES_SRC, "引号名") for jp, zh in names.items()]


def work_wiki_pairs(work=None, path=None, refresh=False):
    """本作 wiki 译名词源 → [(jp, zh, src, cat)]。

    两个文件：
      `术语表.json`    `pages[]` 页面标题（本作宝可梦个体/人物/系统页的「名前」口径）
                       + `terms[]` 术语表（本作原创招式/特性/潜能、官方对照）→ src=`本作wiki`
      `人工修正.json`  `本地译名`（人工裁定的日文名→中文名，含联网核定结果）→ src=`本作wiki-人工`
                       （`修正` 是整行原文覆盖，只对【名前】行有意义，不进词表）
    缓存键含 work 与文件指纹，wiki 重建后自动失效。
    """
    if not work_wiki_enabled():
        return []
    d = work_wiki_dir(work, path)
    if not d:
        return []
    tp = os.path.join(d, WORK_WIKI_TERMS)
    mp = os.path.join(d, WORK_WIKI_EXTRA)
    stamp = []
    for p in (tp, mp):
        try:
            stamp.append((os.path.getmtime(p), os.path.getsize(p)))
        except OSError:
            stamp.append((0, 0))
    key = (d, work) + tuple(stamp)
    with _lock:
        if not refresh and _wwiki_cache["pairs"] is not None and _wwiki_cache["key"] == key:
            return _wwiki_cache["pairs"]

    out = []
    man = _load_json(mp)
    for e in (man.get("本地译名") or []):
        jp, zh = "", ""
        if isinstance(e, (list, tuple)) and len(e) >= 2:
            jp, zh = e[0], e[1]
        elif isinstance(e, dict):
            jp, zh = e.get("jp"), e.get("zh")
        jp, zh = (jp or "").strip(), (zh or "").strip()
        if jp and zh:
            out.append((jp, zh, WORK_WIKI_SRC_MANUAL, "人工译名"))
    data = _load_json(tp)
    for t in (data.get("terms") or []):
        jp = (t.get("jp") or "").strip()
        zh = (t.get("zh") or "").strip()
        if jp and zh:
            out.append((jp, zh, WORK_WIKI_SRC, (t.get("cat") or "术语").strip()))
    for p in (data.get("pages") or []):
        jp = (p.get("name") or "").strip()
        zh = (p.get("title") or "").strip()
        if jp and zh:
            out.append((jp, zh, WORK_WIKI_SRC, (p.get("cat") or "页面").strip()))
    out.extend(_quoted_pairs(data))
    with _lock:
        _wwiki_cache["key"] = key
        _wwiki_cache["pairs"] = out
    return out


def extra_pairs(work=None, path=None, refresh=False):
    """补充术语.json → [(jp, zh, src, cat)]。

    两个来源（人工维护，wiki 生成器只负责把它们并进 index.html）：
      terms          → 通用补充（src=wiki-补充），多为ポケマス说明的 effect/desc 平行文本对齐捞出
      works[<作品>]  → 作品专属（src=作品专属），场地/队伍/地名等只有本作用得到的译名；
                       其中的 effects 段（场地效果整句/模板）同样并入，字面句会被替换、
                       带 {槽位} 的模板匹配不到（语料过滤丢弃），只当图鉴说明
    缓存键含 work（同一进程里可能处理多部作品）。
    """
    d = wiki_dir(path)
    ep = os.path.join(d, WIKI_EXTRA)
    try:
        key = ("extra", work, os.path.getmtime(ep), os.path.getsize(ep))
    except OSError:
        key = ("extra", work, None, None)
    with _lock:
        if not refresh and _extra_cache["pairs"] is not None and _extra_cache["key"] == key:
            return _extra_cache["pairs"]

    out = []
    data = _load_json(ep) if os.path.exists(ep) else {}
    for t in (data.get("terms") or []):
        jp = (t.get("jp") or "").strip()
        zh = (t.get("zh") or "").strip()
        if not jp or not zh:
            continue
        out.append((jp, zh, "wiki-补充", (t.get("cat") or "补充").strip()))
    if work:
        sec = (data.get("works") or {}).get(work) or {}
        for t in (sec.get("terms") or []):
            jp = (t.get("jp") or "").strip()
            zh = (t.get("zh") or "").strip()
            if not jp or not zh:
                continue
            out.append((jp, zh, "作品专属", (t.get("cat") or "作品专属").strip()))
        for e in (sec.get("effects") or []):
            # 场地效果类整句/模板：带 {槽位} 的模板永远匹配不到（语料过滤会丢弃），
            # 字面句才是真正会被替换的（例：◆フィールド効果 → ◆场地效果）
            jp = (e.get("jp") or "").strip()
            zh = (e.get("zh") or "").strip()
            if not jp or not zh:
                continue
            out.append((jp, zh, "作品专属", (e.get("cat") or "场地").strip()))
    with _lock:
        _extra_cache["key"] = key
        _extra_cache["pairs"] = out
    return out


def wiki_pairs(path=None, refresh=False, work=None):
    """wiki 译名词源 → [(jp, zh, src, cat)]（AA 原创术语 + 官方译名对照 + 人工补充/作品专属）。

    缓存键 = 三个文件 (mtime, size) + work（作品专属项随作品变化），wiki 重建后自动失效。
    """
    d = wiki_dir(path)
    tp = os.path.join(d, WIKI_TERMS)
    op = os.path.join(d, WIKI_OFFICIAL)
    try:
        key = tuple((os.path.getmtime(p), os.path.getsize(p)) for p in (tp, op))
    except OSError:
        key = None
    with _lock:
        if not refresh and _wiki_cache["pairs"] is not None and _wiki_cache["key"] == key and _wiki_cache.get("work") == work:
            return _wiki_cache["pairs"]

    out = []
    # 人工增量优先（同 jp 时人工条目赢过生成的 wiki 批量条目，src 也才能保住
    # wiki-补充 / 作品专属 的标签——整句放行就是按这个标签判断的）
    out.extend(extra_pairs(work, path=path, refresh=refresh))
    for t in (_load_json(tp).get("terms") or []):
        jp = (t.get("jp") or "").strip()
        zh = (t.get("zh") or "").strip()
        if not jp or not zh:
            continue
        cat = (t.get("cat") or "").strip()
        out.append((jp, zh, "wiki-术语", cat or "术语"))
    for t in (_load_json(op).get("official") or []):
        jp = (t.get("jp") or "").strip()
        zh = (t.get("zh") or "").strip()
        if not jp or not zh:
            continue
        out.append((jp, zh, "wiki-官方", (t.get("kind") or "官方").strip()))
    out.extend(extra_pairs(work, path=path, refresh=refresh))
    with _lock:
        _wiki_cache["key"] = key
        _wiki_cache["work"] = work
        _wiki_cache["pairs"] = out
    return out


# ---------------------------------------------------------------------------
# 歧义守卫（补充术语.json「歧义」段）
# ---------------------------------------------------------------------------
def _compile_amb(text, jp, field):
    """歧义段的正则字段 → 编译对象（坏正则当作无该字段，只告警不中断）。"""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return re.compile(text)
    except re.error as e:
        print("[pre_terms] 警告: 歧义规则 %s 的 %s 不是合法正则（%s）→ 忽略该字段" % (jp, field, e),
              flush=True)
        return None


def ambiguity_rules(path=None, refresh=False):
    """`补充术语.json` 的「歧义」段 → {jp: rule}。

    rule = {jp, 禁, 允许, zh, cat, require, deny, require距离, deny距离, note}
    —— 同形常用词（ラッキー/プラス/クラブ…）与 ≤2 字假名条目的语境守卫；
    带 `zh` 的条目 = 替译（同形两义按语境二选一，见模块 docstring）。
    """
    ep = os.path.join(wiki_dir(path), WIKI_EXTRA)
    try:
        key = (os.path.getmtime(ep), os.path.getsize(ep))
    except OSError:
        key = (None, None)
    with _lock:
        if not refresh and _amb_cache["rules"] is not None and _amb_cache["key"] == key:
            return _amb_cache["rules"]

    out = {}
    data = _load_json(ep) if os.path.exists(ep) else {}
    for r in (data.get("歧义") or []):
        jp = (r.get("jp") or "").strip()
        if not jp:
            continue
        out[jp] = {
            "jp": jp,
            "禁": bool(r.get("禁")),
            "允许": bool(r.get("允许")),
            "zh": (r.get("zh") or "").strip(),          # 替译：语境命中时改出这个译名
            "cat": (r.get("cat") or "").strip(),
            "require": _compile_amb(r.get("require"), jp, "require"),
            "deny": _compile_amb(r.get("deny"), jp, "deny"),
            "require距离": int(r.get("require距离") or 60),
            "deny距离": int(r.get("deny距离") or 10),
            "note": (r.get("note") or "").strip(),
        }
    with _lock:
        _amb_cache["key"] = key
        _amb_cache["rules"] = out
    return out


def _amb_ok(text, a, b, rule):
    """命中处 [a,b) 是否通过语境守卫（deny 优先，require 必须有其一）。"""
    if rule is None:
        return True
    if rule["deny"] is not None:
        d = rule["deny距离"]
        if rule["deny"].search(text[max(0, a - d):min(len(text), b + d)]):
            return False
    if rule["require"] is not None:
        d = rule["require距离"]
        if not rule["require"].search(text[max(0, a - d):min(len(text), b + d)]):
            return False
    return True


# ---------------------------------------------------------------------------
# 作品术语表
# ---------------------------------------------------------------------------
def work_id(explicit=None):
    """当前作品 id：显式 > `aa_translate.WORK_ID` > 从路径反推。

    `translate_runner` 直接设 `AA_DIR/OUT_DIR/WORK_DIR` 三个全局、**不设 WORK_ID**，
    而它正是生产入口 → 只认 WORK_ID 会让替换静默失效（2026-09-22 实测两次端到端
    smoke 零替换）。故按仓库约定从 `aaManga/<work>/<aa|汉化|work>` 反推。
    """
    if explicit:
        return explicit
    try:
        import aa_translate as A
        if getattr(A, "WORK_ID", ""):
            return A.WORK_ID
        # 脚本方式跑时 aa_translate 是 __main__：上面 import 拿到的是**第二份副本**
        m = sys.modules.get("__main__")
        if m is not None and str(getattr(m, "__file__", "")).endswith("aa_translate.py"):
            if getattr(m, "WORK_ID", ""):
                return m.WORK_ID
            for attr in ("WORK_DIR", "AA_DIR", "OUT_DIR"):
                d = getattr(m, attr, "") or ""
                if d:
                    w = os.path.basename(os.path.dirname(os.path.abspath(d)))
                    if w and w != "aaManga":
                        return w
        for attr in ("WORK_DIR", "AA_DIR", "OUT_DIR"):
            d = getattr(A, attr, "") or ""
            if not d:
                continue
            w = os.path.basename(os.path.dirname(os.path.abspath(d)))
            if w and w != "aaManga":
                return w
    except Exception:
        pass
    return ""


def work_dir_of(work):
    """作品 work/ 目录：registry → 仓库约定 aaManga/<work>/work（同 _aa_dir 的回退链）。"""
    try:
        import registry
        d = (registry.get(work) or {}).get("work_dir") or ""
        if d and os.path.isdir(d):
            return d
    except (Exception, SystemExit):
        pass
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    d = os.path.join(root, "aaManga", work, "work")
    return d if os.path.isdir(d) else ""


def work_pairs(work, work_dir=None):
    """作品术语表条目 → [(jp, zh, src, cat)]（含 manual/auto；`re:` 原样保留）。"""
    wd = work_dir or work_dir_of(work)
    if not wd:
        return []
    out = []
    for fn in sorted(os.listdir(wd)) if os.path.isdir(wd) else []:
        if not (fn.startswith("glossary_") and fn.endswith(".json")):
            continue
        if fn in ("glossary_candidates.json", "glossary_iter.json"):
            continue
        d = _load_json(os.path.join(wd, fn))
        rows = d.get("glossary")
        if not isinstance(rows, list):        # 只有 {"glossary":[...]} 才是真作品表
            continue
        for e in rows:
            jp = (e.get("jp") or "").strip()
            zh = (e.get("zh") or "").strip()
            if not jp or not zh:
                continue
            out.append((jp, zh, "作品表-" + (e.get("来源") or "manual"),
                        (e.get("cat") or "").strip()))
    return out


# ---------------------------------------------------------------------------
# 章词语料过滤 + 正则
# ---------------------------------------------------------------------------
def _aa_dir(work):
    """作品原文目录：registry → 仓库约定的 aaManga/<work>/aa。

    必须稳（返回空会让章词语料过滤失效 → 全库 wiki 词对无约束替换，见 build_pairs 的熔断）。
    """
    try:
        import registry
        d = (registry.get(work) or {}).get("aa_dir") or ""
        if d and os.path.isdir(d):
            return d
    except (Exception, SystemExit):
        pass
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    d = os.path.join(root, "aaManga", work, "aa")
    return d if os.path.isdir(d) else ""


def chapter_corpus(work, base):
    """本章原文全文（用于"只替换本章真出现的词"）。"""
    if not (work and base):
        return ""
    p = os.path.join(_aa_dir(work), base + ".txt")
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def regex_for(jp):
    """单条 pair → 编译后的正则（含短词守卫）。返回 None 表示不可用。"""
    jp = (jp or "").strip()
    if not jp:
        return None
    if jp.startswith("re:"):
        try:
            return re.compile(jp[3:])
        except re.error:
            return None
    if len(jp) < 2 or _SINGLE_CJK.match(jp):
        return None                            # 单字词误伤面太大（项目既有铁律）
    pat = re.escape(jp)
    if _KANA_L.match(jp[0]):
        # 左邻片假名/长音/中黑 = 更长片假名词的一部分（アメリカ 里的 リカ）；
        # 左侧是平假名多为助词（とカズマ/はカズマ），必须放行。
        # 2026-09-30：原来只给 ≤4 字套守卫 → ストライク 吃掉了 ストライク・ジークヴルム，
        # 现对所有长度生效（更长的表内词仍由"长词优先"先吃）。
        pat = r"(?<![ァ-ヺー・])" + pat
    if _KATA_END.match(jp[-1]):
        pat = pat + r"(?![ァ-ヺー・])"          # 片假名续写 = 更长词的一部分
    return re.compile(pat, re.IGNORECASE if _LATIN.search(jp) else 0)


def build_pairs(work, base=None, corpus=None, extra=(), force=False, wiki=True):
    """本章可替换词表 → [dict(jp, zh, src, cat, rx)]，长词优先。

    wiki=False 时只用作品术语表（对照实验用）；extra 可追加 (jp, zh, src, cat)。
    """
    rows = []
    if wiki:
        rows.extend(wiki_pairs(work=work))
        rows.extend(work_wiki_pairs(work))     # 本作设定 wiki（本作口径，排在通用 wiki 之前）
    rows.extend(work_pairs(work))
    rows.extend(extra or ())
    derived_corpus = corpus is None
    if derived_corpus:
        corpus = chapter_corpus(work, base) if base else ""
        if not corpus:
            # 熔断：语料读不到 → 不做"无约束全库替换"（短词会乱撞），宁可不替换
            print("[pre_terms] 警告: 读不到本章原文语料（work=%s base=%s）→ 跳过术语替换" % (work, base))
            return []

    out = []
    seen = {}
    skipped = {"dirty_zh": 0, "禁替换": 0, "短假名未授权": 0}
    amb = ambiguity_rules()
    for jp, zh, src, cat in rows:
        jp = (jp or "").strip()
        zh = (zh or "").strip()
        if not jp or not zh or jp == zh:
            continue
        rule = amb.get(jp)
        if rule is not None and rule["禁"]:
            skipped["禁替换"] += 1             # 歧义段显式禁替换（同形常用词/脏条目）
            continue
        if rule is None and not jp.startswith("re:") and _SHORT_KANA.match(jp):
            # ≤2 纯假名没有词界可言：めい 吃掉 煌めいて、あわ 吃掉 あわよくば。
            # 要保留必须在歧义段给出 允许/require/deny（显式授权）。
            skipped["短假名未授权"] += 1
            continue
        alt = (rule or {}).get("zh") or ""           # 替译译名（语境命中时才用）
        if alt and alt == jp:
            alt = ""
        if _KANA_L.search(zh) or _KANA_L.search(alt) or "\ufffd" in zh or "\ufffd" in jp:
            skipped["dirty_zh"] += 1           # 脏译名（残留假名/替换符）→ 绝不落地
            continue
        if len(jp) > (60 if src.startswith(("wiki-补充", "作品专属")) else 20):
            # 人工维护的补充/作品专属允许整句（场地效果模板等）；wiki 批量条目仍按词处理，
            # 防止 description 混进 terms 造成大段替换
            continue
        if (corpus and jp.startswith("re:") is False and jp not in corpus
                and zh not in corpus and (not alt or alt not in corpus)):
            continue                           # 本章没出现 → 不替换（省匹配开销，语义更干净）
        # 人工作品术语表 = 2；人工维护的补充/作品专属 + 本作 wiki = 1；通用 AA wiki = 0
        rank = (2 if src.startswith("作品表")
                else (1 if src.startswith(("作品专属", "wiki-补充", WORK_WIKI_SRC)) else 0))
        prev = seen.get(jp)
        if prev is not None:
            if rank > prev["rank"]:            # 作品表优先覆盖 wiki
                pass
            else:
                continue
        rx = regex_for(jp)
        if rx is None:
            continue
        seen[jp] = {"jp": jp, "zh": zh, "src": src, "cat": cat, "rx": rx, "rank": rank,
                    "amb": rule}
    out = list(seen.values())
    # wiki 与作品表同名的不同词（非同 jp）：长词优先，短的留后
    out.sort(key=lambda r: (-len(r["jp"] if not r["jp"].startswith("re:") else r["jp"][3:]),
                            r["jp"]))
    for r in out:
        r["skipped"] = dict(skipped)
    return out


def pairs_for(base, work=None, force=False, wiki=True):
    """按章取词表（带缓存；wiki/作品表文件变更自动失效）。"""
    w = work_id(work)
    if not w or not base:
        return []
    if not force and not enabled(w):
        return []
    stamp = []
    wwd = work_wiki_dir(w)
    for p in (os.path.join(_aa_dir(w), base + ".txt"),
              os.path.join(wiki_dir(), WIKI_TERMS),
              os.path.join(wiki_dir(), WIKI_EXTRA),
              os.path.join(wwd, WORK_WIKI_TERMS) if wwd else "",
              os.path.join(wwd, WORK_WIKI_EXTRA) if wwd else ""):
        try:
            stamp.append((os.path.getmtime(p), os.path.getsize(p)))
        except OSError:
            stamp.append((0, 0))
    ck = (w, base) + tuple(stamp)
    with _lock:
        hit = _chapter_cache.get(ck)
    if hit is not None:
        return hit
    pairs = build_pairs(w, base, force=force, wiki=wiki)
    with _lock:
        _chapter_cache.clear()
        _chapter_cache[ck] = pairs
    with _lock:
        _logged = ck in _log_once
        _log_once.add(ck)
    if not _logged and os.environ.get("AA_PRE_TERMS_QUIET", "") != "1":
        srcs = collections.Counter(p["src"] for p in pairs)
        sk = pairs[0]["skipped"] if pairs else {}
        guarded = sum(1 for p in pairs if p.get("amb") is not None)
        alts = sum(1 for p in pairs if (p.get("amb") or {}).get("zh"))
        print("[pre_terms] %s: 可用词 %d（%s；脏译名 %d / 禁替换 %d / 短假名未授权 %d；歧义守卫 %d%s）" % (
            base, len(pairs),
            " / ".join("%s %d" % (k, v) for k, v in sorted(srcs.items())) or "空",
            sk.get("dirty_zh", 0), sk.get("禁替换", 0), sk.get("短假名未授权", 0), guarded,
            ("；替译 %d" % alts) if alts else ""))
    return pairs


def clear_cache():
    with _lock:
        _wiki_cache["key"] = None
        _wiki_cache["pairs"] = None
        _wwiki_cache["key"] = None
        _wwiki_cache["pairs"] = None
        _extra_cache["key"] = None
        _extra_cache["pairs"] = None
        _amb_cache["key"] = None
        _amb_cache["rules"] = None
        _chapter_cache.clear()
        _pipe_cache.clear()
        _log_once.clear()


# ---------------------------------------------------------------------------
# 替换
# ---------------------------------------------------------------------------
def _zh_of(p, text, a, b):
    """命中 [a,b) 该出哪个译名 → (zh, 是否替译义)；zh 为空 = 不替换。

    - 歧义段带 `zh`（替译）：语境命中 → 替译义，否则词表默认译名（**两义都替换**）；
    - 歧义段只有 require/deny（原有语义）：语境不命中 → 不替换；
    - 无歧义规则：词表译名。
    """
    rule = p.get("amb")
    alt = (rule or {}).get("zh") or ""
    if alt:
        return (alt, True) if _amb_ok(text, a, b, rule) else (p["zh"], False)
    if rule is not None and not _amb_ok(text, a, b, rule):
        return "", False
    return p["zh"], False


def apply_hits(text, base=None, pairs=None, work=None, force=False):
    """把 text 里的术语换成标准译名 → (新文本, [命中 {"jp","zh","src","cat","at"[,"alt"]}])。

    单遍实现：先在**原文**上收集所有候选跨度 → 同起点取长词 → 重叠只留先到的 →
    再按跨度拼新文本。`at` = 该译名在**新文本**里的起始下标（报告做高亮用），
    故必须在替换**之后**计算（逐对顺序替换时位置会互相错位——2026-09-22 踩过）。
    候选译名逐条算（`_zh_of`）：同形两义（歧义段 `zh` 替译）按语境二选一，
    命中带 `alt` 标记（报告/日志据此区分）。
    """
    if not text:
        return text, []
    ps = pairs if pairs is not None else pairs_for(base, work=work, force=force)
    if not ps:
        return text, []
    cands = []
    for p in ps:
        try:
            for m in p["rx"].finditer(text):
                a, b = m.span()
                if b <= a:
                    continue
                zh, alt = _zh_of(p, text, a, b)
                if zh:
                    cands.append((a, b, p, zh, alt))
        except Exception:
            continue
    if not cands:
        return text, []
    cands.sort(key=lambda c: (c[0], -(c[1] - c[0])))     # 同起点：长词优先
    picked, last_end = [], -1
    for c in cands:
        if c[0] < last_end:
            continue                                     # 与已选跨度重叠 → 跳过（非重叠铁律）
        picked.append(c)
        last_end = c[1]
    out, pos, hits, at = [], 0, [], 0
    for a, b, p, zh, alt in picked:
        frag = text[a:b]
        if frag == zh:
            continue                                     # 已是标准译名 → 不算命中
        head = text[pos:a]
        out.append(head)
        at += len(head)
        out.append(zh)
        h = {"jp": frag, "zh": zh, "src": p["src"], "cat": p["cat"], "at": at}
        if alt:
            h["alt"] = True
        hits.append(h)
        at += len(zh)
        pos = b
    out.append(text[pos:])
    return "".join(out), hits


def apply(text, base=None, work=None):
    """`aa_translate` 钩子入口（异常一律回退原文，绝不阻断翻译）。

    `work` 由调用方显式传入：`aa_translate` **以脚本方式**跑时本模块即 `__main__`，
    再 `import aa_translate` 会得到第二份模块副本（WORK_ID 为空、路径为代码默认值）
    → 按 `A.WORK_ID` 反查会解析成别的作品、替换静默失效（2026-09-22 实测）。
    """
    try:
        out, _ = apply_hits(text, base=base, work=work)
        return out
    except Exception as e:
        if os.environ.get("AA_PRE_TERMS_DEBUG", "").strip() == "1":
            print("[pre_terms-debug] apply 异常 %s: %s" % (type(e).__name__, e), flush=True)
        return text


# ---------------------------------------------------------------------------
# 开关
# ---------------------------------------------------------------------------
def _pipeline_of(work):
    """管线归属：work_pipeline（同目录）→ 直接读 work_meta.json（不依赖 sys.path）。

    直接读的兜底是必需的：aa_translate 以脚本方式跑时 sys.path[0]=scripts/，
    `import work_pipeline`（在 scripts/common/）会 ImportError；旧写法把它吞成
    False → 机制静默失效（2026-09-22 第三次踩同类坑：静默 except 掩盖导入失败）。
    """
    try:
        import work_pipeline as WP
        return WP.pipeline_of(work) or "generic"
    except Exception:
        pass
    try:
        with open(os.path.join(ROOT, "aa_web_app", "data", "work_meta.json"),
                  encoding="utf-8") as f:
            d = json.load(f).get(work)
        if isinstance(d, dict):
            return (d.get("pipeline") or "").strip() or "generic"
    except (OSError, ValueError):
        pass
    print("[pre_terms] 警告: 无法判定作品 %s 的管线归属 → 本次不做术语替换" % work, flush=True)
    return "generic"


def enabled(work=None):
    """开关：`AA_PRE_TERMS=0` 关；否则只看管线归属（非宝可梦 = 关闭）。"""
    if os.environ.get("AA_PRE_TERMS", "1").strip() == "0":
        return False
    w = work_id(work)
    if not w:
        return False
    with _lock:
        hit = _pipe_cache.get(w)
    if hit is None:
        hit = _pipeline_of(w) == "pokemon"
        with _lock:
            _pipe_cache[w] = hit
    return hit        # 缓存：管线判定会读 work_meta.json（每行调用 → 必须缓存）


def file_lines(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().split("\n")
    except OSError:
        return []


def scan_lines(lines, pairs, keep=None):
    """逐行替换 → [(idx, src, new, [hits])]，只回有命中的行。

    keep(idx, line)：可选谓词（报告里传"是否送模型行" `aa_translate.is_translatable`，
    把帖头/art 行排除——那些行根本不会送模型，替换无意义）。
    """
    out = []
    for i, l in enumerate(lines):
        if not l.strip():
            continue
        if keep is not None and not keep(i, l):
            continue
        new, hits = apply_hits(l, pairs=pairs)
        if hits:
            out.append((i, l, new, hits))
    return out
