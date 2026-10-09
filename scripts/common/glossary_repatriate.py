# -*- coding: utf-8 -*-
"""作品术语表 → 全局术语表 反哺（2026-09-20）。

方案: doc/方案归档/术语表反哺与候选池差集修复方案_2026-09-20.md（grilling 7 决策）

零 API。扫作品表 work/glossary_<work>.json 的 manual 条目 → 与全局
(glossary.json + roles.json) 差集 → 列出可反哺项（按跨作品次数排序、标短词与嵌入率）
→ 人工勾选 → 写入全局。**不做自动同步**：自动同步会把作品专属对污染全局，
                       分三级的目的正在于此。

写入规则（决策 2/3/4）:
  cat=角色 + 有译名 → roles.json 新条目（作品=[来源作品]/分类=角色/备注标反哺来源）+ known_names
  cat=专名          → glossary.json 替换对 + known_names；cat 必须是 `特殊-专名`
                      （gen_work_glossary.derive_auto 只派生 常用-*/特殊-* 前缀的全局对；
                        写 `专名` = 永不派生 = 白做，写缺失 = 默认 常用-梗 = 全量注入别部作品）
  短词(<=3 字)的专名 → 自动包 `re:` 词边界（aa_translate.py:1151 会把 re: 对过滤掉不注入
                      prompt，只剩本地替换——"宁可价值减半也不制造硬伤"的取舍，决策 4）
  一律跳过: `re:` 开头 / 缺译名 / zh==jp / 语法功能词(FUNC_WORDS，入 known_names 会掩盖漏译)

  作品白名单项（2026-09-20 用户要求；`类型`="白名单"，来自作品表 known_names）
      → 只写全局 glossary.json `known_names`（不生成替换对/角色）；短词无替换风险故不设
        `re:` 闸门；跳过判据见 `_name_skip_reason`（re: 开头 / 语法功能词）。
      候选窗「白名单」按钮写的是**本作品**表 known_names，跨作品收录一律由此处手动勾选。

对外接口:
  global_known()             → 全局已收录 term 集合（归一化）
  cross_work_index()         → {term: {work_id,...}} 跨作品索引（manual 替换对）
  cross_work_names_index()   → 同上，但统计各作品表 known_names（作品白名单）
  scan(work_id)              → {"work", "items", "summary"}（items 供 UI 列表 + 勾选）
  build_payload(items, work) → {"roles","pairs","known","skipped"} 待写入内容（不落盘）
  apply(items, work_id)      → 落盘（先备份）+ 返回写入统计

CLI: scripts/tools/gen_repatriate.py --work <id> [--apply --select auto|cross|all|--terms a,b]
"""
import glob
import json
import os
import re
import shutil
import sys
import time

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_THIS_DIR)
for _sp in (_SCRIPTS, _THIS_DIR, os.path.join(_SCRIPTS, "works"), os.path.join(_SCRIPTS, "tools")):
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

from paths import BASE                                    # noqa: E402

# ---- 路径/常量（测试可整体 patch 这些模块级常量） ----
GLOSSARY_JSON = os.path.join(BASE, "glossary.json")
ROLES_JSON = os.path.join(BASE, "roles.json")
BACKUP_ROOT = os.path.join(BASE, ".glossary_backup")
AA_MANGA = os.path.join(BASE, "aaManga")
BACKUP_KEEP = 20
SHORT_LEN = 3                      # 短词阈值（<=3 字走"默认不勾 + re: 边界"闸门）
ROLE_CAT = "角色"
SPECIAL_CAT = "特殊-专名"           # 决策 3：只有 常用-/特殊- 前缀才会派生到作品表

# re: 词边界的邻字符类（假名 + 长音 + 中点；词含汉字时再加中日共用汉字）
_KANA_CLS = "\u3041-\u309f\u30a1-\u30ff\u30fc\u30fb"
_CJK_CLS = "\u4e00-\u9fff\u3005"
_KANA_RE = re.compile("[" + _KANA_CLS + "]")
_CJK_RE = re.compile("[" + _CJK_CLS + "]")

# 右邻这些假名不算"被夹"（正常日语接续）：助词/断定/长音促音/敬称·爱称头字
# ——否则「ミクが来た」会被误判成 ミク 是更长词的一部分（2026-09-20 实测修正）
_KANA_OK_RIGHT = set("がはをにのともでへやかねよなさぞぜわだんちくーっッ"
                     "様殿君氏")

_KNOWN_CACHE = {}                  # (glossary_mtime, roles_mtime) -> set
_FUNC_WORDS = None


def _norm(s):
    return re.sub(r"\s+", "", s or "").lower()


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_json(path, data, indent=1):
    """原子写（tmp + os.replace），行尾 LF。"""
    tmp = path + ".tmp_repatriate"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)


def func_words():
    """语法功能词（复用 gen_glossary_ai.FUNC_WORDS；导入失败退化为空集）。"""
    global _FUNC_WORDS
    if _FUNC_WORDS is None:
        try:
            import gen_glossary_ai as GA
            _FUNC_WORDS = set(GA.FUNC_WORDS)
        except Exception:
            _FUNC_WORDS = set()
    return _FUNC_WORDS


# ---------- 全局已收录 term ----------

def global_known(use_cache=True):
    """全局术语表已收录的 term（归一化）: known_names + glossary 对(jp/zh)
    + glossary 角色块(日/中) + roles.json(日文/中文/别名)。"""
    key = (os.path.getmtime(GLOSSARY_JSON) if os.path.exists(GLOSSARY_JSON) else 0,
           os.path.getmtime(ROLES_JSON) if os.path.exists(ROLES_JSON) else 0)
    if use_cache and key in _KNOWN_CACHE:
        return _KNOWN_CACHE[key]
    out = set()

    def _add(*vals):
        for v in vals:
            if v:
                out.add(_norm(v))

    g = _load_json(GLOSSARY_JSON)
    for n in g.get("known_names", []):
        _add(n)
    for p in g.get("glossary", []):
        if isinstance(p, dict):
            _add(p.get("jp"), p.get("zh"))
        elif isinstance(p, list) and p:
            _add(p[0], p[1] if len(p) > 1 else "")
    for r in g.get("角色", []) or []:
        _add(r.get("日文"), r.get("中文"))
    rd = _load_json(ROLES_JSON)
    for r in rd.get("角色", []) or []:
        _add(r.get("日文"), r.get("中文"))
        for al in (r.get("别名") or []):
            _add(al)
    _KNOWN_CACHE[key] = out
    return out


def _role_exists(jp, roles):
    n = _norm(jp)
    for r in roles:
        if _norm(r.get("日文")) == n or _norm(r.get("中文")) == n:
            return True
        for al in (r.get("别名") or []):
            if _norm(al) == n:
                return True
    return False


# ---------- 作品表扫描 ----------

def _looks_like_work_table(path):
    """真作品表长这样：{"glossary": [...]}。

    work/ 目录里还躺着 `glossary_candidates.json` / `glossary_iter.json`（都有 glossary_ 前缀，
    字母序还排在真表前面）——必须靠数据结构区分，不能只看文件名（2026-09-20 实测：
    中_学性奴隷日記 因 glob 取到 glossary_candidates.json → 反哺列表恒空）。
    """
    return isinstance(_load_json(path).get("glossary"), list)


def work_glossary_files(root=None):
    """全部作品表路径 [(work_id, path)]。默认扫 aaManga/*/work/glossary_*.json
    与仓库根一级目录 */work/glossary_*.json（兼容旧的根级作品目录）。

    只认 `_looks_like_work_table()` 通过的文件（排除 candidates/iter 等旁支 JSON）。
    """
    root = root or BASE
    out, seen = [], set()
    pats = [os.path.join(root, "aaManga", "*", "work", "glossary_*.json"),
            os.path.join(root, "*", "work", "glossary_*.json")]
    for pat in pats:
        for p in sorted(glob.glob(pat)):
            if p in seen:
                continue
            seen.add(p)
            if not _looks_like_work_table(p):
                continue
            wid = os.path.basename(p)[len("glossary_"):-len(".json")]
            out.append((wid, p))
    return out


def work_glossary_path(work_id, work_dir=None):
    """该作品的术语表文件（文件名里的 work_id 可能与目录名不同，如 sujin_otona → 大人のお店）。

    优先同名文件；否则取该目录下第一份真作品表（`_looks_like_work_table` 过滤掉
    candidates/iter 旁支）；都没有则回退默认名（调用方按「不存在」处理）。
    """
    if not work_dir:
        import registry
        work_dir = registry.get(work_id)["work_dir"]
    exact = os.path.join(work_dir, f"glossary_{work_id}.json")
    if os.path.exists(exact) and _looks_like_work_table(exact):
        return exact
    hits = [p for p in sorted(glob.glob(os.path.join(work_dir, "glossary_*.json")))
            if _looks_like_work_table(p)]
    return hits[0] if hits else exact


def entries(path, manual_only=False):
    """作品表条目 [{jp, zh, cat, 来源}]。manual_only=True 只取人工条目
    （缺席 来源 的旧二元组也视为 manual）。"""
    d = _load_json(path)
    out = []
    for p in d.get("glossary", []) or []:
        if isinstance(p, dict):
            src = p.get("来源") or "manual"
            if manual_only and src != "manual":
                continue
            jp, zh, cat = p.get("jp"), p.get("zh"), p.get("cat") or ""
        elif isinstance(p, list) and len(p) >= 2:
            src = "manual"          # 旧二元组无 来源 字段 → 视为人工条目
            jp, zh, cat = p[0], p[1], ""
        else:
            continue
        if not jp:
            continue
        out.append({"jp": str(jp).strip(), "zh": str(zh or "").strip(),
                    "cat": str(cat), "来源": src})
    return out


def _manual_entries(path):
    """作品表的 manual 条目（反哺候选只认人工条目）。"""
    return entries(path, manual_only=True)


def work_table_terms(root=None):
    """各作品表（manual+auto）里的 jp/zh 归一化集合。

    2026-09-20 缺口修复：候选池差集口径（build_term_pool.load_known）与 docstring
    声明对齐——作品表里已有的词就不是"新候选"。要让它重新出现，删掉作品表那行即可。
    """
    out = set()
    for _wid, path in work_glossary_files(root):
        for e in entries(path):
            if e["jp"]:
                out.add(_norm(e["jp"]))
            if e["zh"]:
                out.add(_norm(e["zh"]))
    return out


def cross_work_index(root=None):
    """{归一化 jp: {work_id, ...}} —— 各作品表 manual 条目的跨作品分布。"""
    idx = {}
    for wid, path in work_glossary_files(root):
        for e in _manual_entries(path):
            idx.setdefault(_norm(e["jp"]), set()).add(wid)
    return idx


def cross_work_names_index(root=None):
    """{归一化 term: {work_id, ...}} —— 各作品表 `known_names`（作品白名单）的跨作品分布。

    2026-09-20 用户要求：候选窗「白名单」写**本作品**表 known_names；一个词该不该进全局
    由反哺区块**手动勾选**决定，判据之一就是"别部作品也把它当白名单"。
    """
    idx = {}
    for wid, path in work_glossary_files(root):
        for n in (_load_json(path).get("known_names") or []):
            n = (n or "").strip()
            if n:
                idx.setdefault(_norm(n), set()).add(wid)
    return idx


def _name_skip_reason(term):
    """白名单项（只有词、没有译名）不可反哺的原因（空串 = 可反哺）。

    与替换对判据不同：白名单只影响"这个词算不算已知词"，不替换正文
    → 短词没有误替换风险（无需 re: 闸门），也谈不上"缺译名"。
    """
    if not (term or "").strip():
        return "空词"
    if term.startswith("re:"):
        return "正则（作品专属，勿入全局）"
    if term in func_words():
        return "语法功能词（入 known_names 会掩盖漏译）"
    return ""


def _skip_reason(jp, zh):
    """不可反哺的原因（空串 = 可反哺）。"""
    if jp.startswith("re:"):
        return "正则（作品专属，勿入全局）"
    if not zh:
        return "缺译名"
    if _norm(jp) == _norm(zh):
        return "无译名（中日同形）"
    if jp in func_words():
        return "语法功能词（入 known_names 会掩盖漏译）"
    return ""


def re_wrap(term):
    """包 re: 词边界（左右非假名/长音/中点；含汉字时再加中日共用汉字）。

    与全局既有 鸡尾酒 短词规则同构（re:(?<![ァ-ヶー・])ジン(?![ァ-ヶー・])），
    词含汉字时额外挡汉字邻接，避免把更长汉字词改坏。
    """
    cls = _KANA_CLS + (_CJK_CLS if _CJK_RE.search(term) else "")
    return f"re:(?<![{cls}]){re.escape(term)}(?![{cls}])"


# ---------- 嵌入率（短词风险度量） ----------

def _corpus_files(aa_dir):
    out = []
    try:
        names = sorted(os.listdir(aa_dir))
    except OSError:
        return out
    for fn in names:
        if not fn.endswith(".txt"):
            continue
        if fn.endswith("_台词.txt"):
            continue
        out.append(os.path.join(aa_dir, fn))
    return out


def embed_rates(aa_dir, terms):
    """{term: 嵌入率} —— 词被邻接假名夹住（疑似更长词的一部分）的比例。

    判据: 左邻是假名（如 初音ミク 里的 ミク、アメリカ 里的 リカ）**或** 右邻是假名且
    不是正常接续（助词/断定/长音促音/敬称头字，见 _KANA_OK_RIGHT）——「ミクが来た」
    不该算被夹。按作品语料统计（流式逐文件，不整库驻留）；无语料/无命中 → 不在结果里。
    """
    terms = [t for t in dict.fromkeys(terms) if t]
    if not terms:
        return {}
    rx = re.compile("|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True)))
    tot = {t: 0 for t in terms}
    emb = {t: 0 for t in terms}
    for p in _corpus_files(aa_dir):
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                txt = f.read()
        except OSError:
            continue
        for m in rx.finditer(txt):
            t = m.group(0)
            tot[t] += 1
            l = txt[m.start() - 1] if m.start() > 0 else ""
            r = txt[m.end()] if m.end() < len(txt) else ""
            if (l and _KANA_RE.match(l)) or \
               (r and _KANA_RE.match(r) and r not in _KANA_OK_RIGHT):
                emb[t] += 1
    return {t: round(emb[t] / tot[t], 4) for t in terms if tot[t]}


# ---------- 候选列表 ----------

def scan(work_id, with_embed=True):
    """该作品可反哺项。返回 {"work", "items", "summary"}。

    items[i] = {jp, zh, cat, 跨作品, 作品[], 短词, 嵌入率, 建议("角色"/"专名"/"跳过"),
                跳过(原因), 选中(默认建议值), jp_out(实际写入的 jp)}
    默认勾选 = 跨作品>=2 且非短词 且可反哺（决策 4/5）；短词与单作品项需人工勾。
    """
    import registry
    cfg = registry.get(work_id)
    aa_dir = cfg.get("aa_dir")
    path = work_glossary_path(work_id, cfg.get("work_dir"))
    known = global_known()
    idx = cross_work_index()
    idx_names = cross_work_names_index()
    entries = _manual_entries(path)

    items, seen = [], set()
    for e in entries:
        jp, zh, cat = e["jp"], e["zh"], e["cat"]
        if _norm(jp) in seen:
            continue
        seen.add(_norm(jp))
        if _norm(jp) in known:
            continue                                    # 全局已有 → 不是候选
        reason = _skip_reason(jp, zh)
        works = sorted(idx.get(_norm(jp), set()) | {work_id})
        short = len(jp) <= SHORT_LEN
        kind = "角色" if cat == ROLE_CAT else ("专名" if not reason else "跳过")
        if reason:
            kind = "跳过"
        suggest = (not reason) and len(works) >= 2 and not short
        items.append({
            "jp": jp, "zh": zh, "cat": cat, "类型": "替换对",
            "跨作品": len(works), "作品": works,
            "短词": short, "嵌入率": None,
            "建议": kind, "跳过": reason, "选中": suggest,
            "jp_out": re_wrap(jp) if (kind == "专名" and short) else jp,
        })

    # 作品白名单（known_names）：2026-09-20 用户要求——候选窗「白名单」只写本作品表，
    # 要不要进全局由这里**手动勾选**。只写全局 known_names（不生成替换对/角色）。
    table = _load_json(path)
    for n in (table.get("known_names") or []):
        n = (n or "").strip()
        if not n or _norm(n) in seen or _norm(n) in known:
            continue
        seen.add(_norm(n))
        reason = _name_skip_reason(n)
        works = sorted(idx_names.get(_norm(n), set()) | {work_id})
        items.append({
            "jp": n, "zh": "", "cat": "", "类型": "白名单",
            "跨作品": len(works), "作品": works,
            "短词": len(n) <= SHORT_LEN, "嵌入率": None,
            "建议": "跳过" if reason else "白名单",
            "跳过": reason,
            # 白名单短词无替换风险（不改进正文）→ 不设短词闸门，跨作品 >=2 即默认勾
            "选中": (not reason) and len(works) >= 2,
            "jp_out": n,
        })

    if with_embed and aa_dir:
        shorts = [it["jp"] for it in items if it["短词"] and it.get("类型") != "白名单"]
        rates = embed_rates(aa_dir, shorts) if shorts else {}
        for it in items:
            if it["短词"] and it.get("类型") != "白名单":
                it["嵌入率"] = rates.get(it["jp"])
    items.sort(key=lambda x: (bool(x["跳过"]), -x["跨作品"], -len(x["jp"]), x["jp"]))
    summary = {
        "合计": len(items),
        "可反哺": sum(1 for it in items if not it["跳过"]),
        "跨作品": sum(1 for it in items if not it["跳过"] and it["跨作品"] >= 2),
        "短词": sum(1 for it in items if not it["跳过"] and it["短词"] and it.get("类型") != "白名单"),
        "白名单": sum(1 for it in items if not it["跳过"] and it.get("类型") == "白名单"),
        "建议勾选": sum(1 for it in items if it["选中"]),
        "跳过": sum(1 for it in items if it["跳过"]),
    }
    return {"work": work_id, "items": items, "summary": summary}


# ---------- 写入 ----------

def build_payload(items, work_id):
    """算待写入内容（不落盘）。items = 勾选项（scan() 的 item 或 {jp,zh,cat}）。"""
    roles_data = _load_json(ROLES_JSON)
    roles = roles_data.get("角色", []) or []
    gl = _load_json(GLOSSARY_JSON)
    pairs = gl.get("glossary", []) or []
    known = gl.get("known_names", []) or []
    known_set = {_norm(x) for x in known}
    have_pair = set()
    for p in pairs:
        if isinstance(p, dict):
            have_pair.add(_norm(p.get("jp")))
        elif isinstance(p, list) and p:
            have_pair.add(_norm(p[0]))

    new_roles, new_pairs, new_known, skipped = [], [], [], []
    for it in items:
        jp = (it.get("jp") or "").strip()
        zh = (it.get("zh") or "").strip()
        cat = (it.get("cat") or "").strip()
        # 白名单项（2026-09-20）：只有词，只写全局 known_names
        if (it.get("类型") or "").strip() == "白名单":
            reason = _name_skip_reason(jp)
            if reason:
                skipped.append({"jp": jp, "why": reason})
            elif _norm(jp) in known_set:
                skipped.append({"jp": jp, "why": "全局白名单已有"})
            else:
                new_known.append(jp)
                known_set.add(_norm(jp))
            continue
        reason = _skip_reason(jp, zh)
        if reason:
            skipped.append({"jp": jp, "why": reason})
            continue
        if cat == ROLE_CAT:
            if _role_exists(jp, roles):
                skipped.append({"jp": jp, "why": "全局角色表已有"})
            else:
                new_roles.append({"日文": jp, "中文": zh, "作品": [work_id],
                                  "分类": "角色",
                                  "备注": f"反哺自 {work_id}"})
            if _norm(jp) not in known_set:
                new_known.append(jp)
                known_set.add(_norm(jp))
        else:
            jp_out = re_wrap(jp) if len(jp) <= SHORT_LEN else jp
            if _norm(jp_out) in have_pair or _norm(jp) in have_pair:
                skipped.append({"jp": jp, "why": "全局替换对已有"})
            else:
                new_pairs.append({"jp": jp_out, "zh": zh, "cat": SPECIAL_CAT,
                                  "来源": "manual", "备注": f"反哺自 {work_id}"})
                have_pair.add(_norm(jp_out))
            if _norm(jp) not in known_set:
                new_known.append(jp)
                known_set.add(_norm(jp))
    return {"roles": new_roles, "pairs": new_pairs, "known": new_known, "skipped": skipped}


def _backup(paths, ts=None):
    ts = ts or time.strftime("%Y%m%d%H%M%S")
    dest = os.path.join(BACKUP_ROOT, ts)
    os.makedirs(dest, exist_ok=True)
    n = 0
    for p in paths:
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(dest, os.path.basename(p)))
            n += 1
    try:
        dirs = sorted(d for d in os.listdir(BACKUP_ROOT)
                      if os.path.isdir(os.path.join(BACKUP_ROOT, d)))
        for d in dirs[:-BACKUP_KEEP] if len(dirs) > BACKUP_KEEP else []:
            shutil.rmtree(os.path.join(BACKUP_ROOT, d), ignore_errors=True)
    except OSError:
        pass
    return ts, n


def apply(items, work_id, backup=True):
    """把勾选项写入全局。返回 {roles, pairs, known, skipped, backup}。"""
    items = [it for it in items if it and (it.get("jp") or "").strip()]
    if not items:
        return {"roles": 0, "pairs": 0, "known": 0, "skipped": [], "backup": None,
                "msg": "无可写入项"}
    pay = build_payload(items, work_id)
    if not (pay["roles"] or pay["pairs"] or pay["known"]):
        return {"roles": 0, "pairs": 0, "known": 0, "skipped": pay["skipped"],
                "backup": None, "msg": "全部已在全局，无写入"}
    ts = None
    if backup:
        ts, _ = _backup([ROLES_JSON, GLOSSARY_JSON])
    today = time.strftime("%Y-%m-%d")
    if pay["roles"]:
        rd = _load_json(ROLES_JSON)
        rd.setdefault("角色", []).extend(pay["roles"])
        rd["更新日期"] = today
        _write_json(ROLES_JSON, rd, indent=2)
    gl = _load_json(GLOSSARY_JSON)
    if pay["pairs"]:
        gl.setdefault("glossary", []).extend(pay["pairs"])
    if pay["known"]:
        kn = list(gl.get("known_names", []) or [])
        have = {_norm(x) for x in kn}
        kn.extend([x for x in pay["known"] if _norm(x) not in have])
        gl["known_names"] = sorted(kn)
    if pay["pairs"] or pay["known"]:
        gl["更新日期"] = today
        _write_json(GLOSSARY_JSON, gl, indent=1)
    return {"roles": len(pay["roles"]), "pairs": len(pay["pairs"]),
            "known": len(pay["known"]), "skipped": pay["skipped"], "backup": ts,
            "msg": f"写全局: 角色 {len(pay['roles'])} / 替换对 {len(pay['pairs'])} / "
                   f"白名单 {len(pay['known'])}"}
