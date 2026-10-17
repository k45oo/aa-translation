#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""作品术语表生成器 (2026-09-01, 术语表三级重构)

扫描作品全部 aa/ 原文语料，从 全局 glossary.json + roles.json 派生作品级
glossary_<work>.json 的 auto 部分:
  - 常用对 (cat 以「常用-」开头)          : 全量注入, 不过滤
  - 特殊对 (cat 以「特殊-」开头)          : jp 或 zh 命中语料才加入
  - 角色对 (roles.json 全部, 日文+别名)   : 日文/别名 命中语料才加入

manual 部分 (来源=manual, 含旧文件全部条目) 保留不动。
重生成只刷新 auto; 指纹 = roles.json+glossary.json mtime + 章节数。

用法:
  python scripts/common/gen_work_glossary.py --work <id>   # 独立跑
  python scripts/common/gen_work_glossary.py --work <id> --force   # 强制重生成
"""
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # scripts/
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"))

def _aa_txt_names(aa_dir):
    """原文正文 txt 规范名（.txt/.txt.gz 都收）——压缩后 endswith(".txt") 会一章都看不到。"""
    out = []
    try:
        names = os.listdir(aa_dir)
    except OSError:
        return out
    for n in sorted(names):
        if "_汉化" in n or n.endswith(("_台词.txt", "_台词.txt.gz")):
            continue
        if n.endswith(".txt") or n.endswith(".txt.gz"):
            out.append(n)
    return out


def _aa_read_lines(path):
    """读 .txt/.txt.gz → 行列表。"""
    if path.endswith(".gz"):
        import gzip as _gz
        with _gz.open(path, "rt", encoding="utf-8", errors="replace") as f:
            return f.read().split("\n")
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().split("\n")


GLOSSARY_JSON = os.path.join(BASE, "glossary.json")
ROLES_JSON = os.path.join(BASE, "roles.json")


# ---------- 语料扫描 ----------

def _corpus_text(aa_dir):
    """作品 aa/ 全部原文拼接。返回字符串(空目录返回 '')。"""
    parts = []
    try:
        for fn in _aa_txt_names(aa_dir):
            if True:
                try:
                    parts.append("\n".join(_aa_read_lines(os.path.join(aa_dir, fn))))
                except OSError:
                    continue
    except OSError:
        return ""
    return "\n".join(parts)


def _chapter_count(aa_dir):
    try:
        return len(_aa_txt_names(aa_dir))
    except OSError:
        return 0


# ---------- 读取 ----------

def load_global_glossary():
    with open(GLOSSARY_JSON, encoding="utf-8") as f:
        return json.load(f)


def load_roles():
    try:
        with open(ROLES_JSON, encoding="utf-8") as f:
            return json.load(f).get("角色", [])
    except Exception:
        return []


def _pair_cat(p):
    """统一取条目 cat: 对象取 cat 字段, 旧二元组按 jp 前缀推断。"""
    if isinstance(p, dict):
        return p.get("cat", "")
    return ""


def _pair_source(p):
    if isinstance(p, dict):
        return p.get("来源", "manual")
    return "manual"


def _is_common(cat):
    return cat.startswith("常用-")


def _is_special(cat):
    return cat.startswith("特殊-")


# ---------- auto 派生 ----------

def regex_rules_from_global():
    """从全局 glossary.json 取正则术语条目(cat=规则-正则, jp 以 re: 开头)。

    2026-09-06: 作品表生成时【总是】附带全局正则规则(不受"表空才 auto"限制——
    正则如 さん→桑 是通用译名规则, 与作品无关, 每部作品表都该有)。
    返回 [ {jp, zh, cat, 来源:auto} ... ]"""
    g = load_global_glossary()
    out = []
    for p in g.get("glossary", []):
        if isinstance(p, dict):
            jp, zh = p.get("jp"), p.get("zh")
        elif isinstance(p, list) and len(p) == 2:
            jp, zh = p[0], p[1]
        else:
            continue
        if jp and zh and str(jp).startswith("re:"):
            out.append({"jp": jp, "zh": zh, "cat": "正则", "来源": "auto"})
    return out


def derive_auto(corpus, roles, exclude_jp=None):
    """从全局 glossary + roles 派生 auto 条目列表(对象)。

    exclude_jp(2026-09-06, Q14-A): 作品表 manual 已确认的 jp 集合——人工确认的
    译名(来源=manual)不应被 auto 重新派生同 jp 条目覆盖/重复(重复根因:
    roles.json 同名角色 + 人工确认存 manual → 重生成时 auto 又派生同名原文条目)。
    """
    exclude = set(exclude_jp or [])
    g = load_global_glossary()
    out = []
    # 1) 全局 glossary: 常用全量 + 特殊按语料
    for p in g.get("glossary", []):
        jp = p.get("jp") if isinstance(p, dict) else (p[0] if isinstance(p, list) and len(p) == 2 else None)
        zh = p.get("zh") if isinstance(p, dict) else (p[1] if isinstance(p, list) and len(p) == 2 else None)
        if not jp or not zh:
            continue
        if jp.startswith("re:"):
            continue  # 正则不进 prompt/auto
        if jp in exclude:
            continue  # manual 已确认, auto 不重复派生
        cat = _pair_cat(p) or "常用-梗"
        if _is_common(cat):
            # 2026-09-06 分类重构: 作品表 cat 三类(角色/专名/正则), 全局旧分类
            # 仅作注入策略(常用全量/特殊语料), 落作品表统一归"专名"。
            out.append({"jp": jp, "zh": zh, "cat": "专名", "来源": "auto"})
        elif _is_special(cat):
            if jp in corpus or zh in corpus:
                out.append({"jp": jp, "zh": zh, "cat": "专名", "来源": "auto"})
    # 2) roles.json 角色对: 日文/别名 命中语料
    #    2026-09-01 修正1: 备注含「昵称/简称」的条目一律不注入——昵称/简称
    #    用于素材统计定位角色(别名展开), 不是术语替换对; 且单字(憧/玄/衣)
    #    或短昵称(しゅがは→瑞原はやり)直接替换会误伤(把昵称误换成全称)。
    #    2026-09-01 修正2: 角色对 zh 一律用日文原文(アクア→アクア)——未人工
    #    确认的译名不自动替换(避免 阿熊→阿库娅 式错翻); 真实译名+作品经
    #    role_cn/role_works 字段展示, 由工作台人工确认后落地。
    for r in roles:
        jp = r.get("日文")
        zh = r.get("中文")
        if not jp:
            continue
        if jp in exclude:
            continue  # manual 已确认同名, auto 不重复派生角色对
        # 2026-09-01: 艺术纹理/碎片 分类是 AA 绘画碎片(ツモ/ぃぃ 等), 非真角色, 不进术语表
        if (r.get("分类") or "") in ("艺术纹理/碎片", "texture"):
            continue
        note = r.get("备注") or ""
        if "昵称" in note or "简称" in note:
            continue  # AA内昵称/简称/梗词: 只作素材统计索引, 不进术语表
        hit = jp in corpus
        if not hit:
            for al in (r.get("别名") or []):
                if al and al in corpus:
                    hit = True
                    break
        if hit:
            out.append({"jp": jp, "zh": jp, "cat": "角色", "来源": "auto",
                        "role_cn": zh or jp, "role_works": r.get("作品") or []})
    # Q14-A(2026-09-06): auto 内部按 jp 去重——glossary 分支(ポン→碰, 特殊-麻将)与
    # roles 分支(ポン 角色)可能都命中语料产出同 jp → 保留先出现者。
    seen, dedup = set(), []
    for x in out:
        if x["jp"] not in seen:
            seen.add(x["jp"])
            dedup.append(x)
    return dedup


# ---------- 作品表读写 ----------

def work_glossary_path(work_dir, work_id=None):
    """作品 work 目录下 glossary_<work_id>.json。work_id 缺省取 work_dir 上一级目录名。"""
    wid = work_id or os.path.basename(os.path.dirname(os.path.normpath(work_dir)))
    return os.path.join(work_dir, f"glossary_{wid}.json")


def read_work_glossary(path):
    """读作品术语表, 返回 (manual_entries, auto_entries, known_names, schema)。兼容 v1/v2。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return [], [], [], None
    manual, auto = [], []
    for p in data.get("glossary", []):
        if isinstance(p, dict):
            if p.get("来源") == "auto":
                auto.append(p)
            else:
                manual.append(p)
        elif isinstance(p, list) and len(p) == 2:
            manual.append({"jp": p[0], "zh": p[1], "cat": "手动", "来源": "manual"})
    return manual, auto, data.get("known_names", []), data.get("schema")


def write_work_glossary(path, manual, auto, known_names=None):
    """写作品术语表: manual 在前, auto 在后。"""
    data = {
        "schema": "AA翻译作品术语表 v2",
        "更新日期": time.strftime("%Y-%m-%d"),
        "glossary": manual + auto,
    }
    if known_names:
        data["known_names"] = known_names
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------- 指纹 ----------

def _fingerprint():
    """指纹 = roles.json+glossary.json mtime + 章节数无关(生成器在作品级跑, 章节数由调用方传)。"""
    mtimes = []
    for p in (GLOSSARY_JSON, ROLES_JSON):
        try:
            mtimes.append(os.path.getmtime(p))
        except OSError:
            mtimes.append(0)
    return tuple(mtimes)


# ---------- 主入口 ----------

def generate(work_dir, work_id, force=False):
    """生成/刷新作品术语表。work_dir = registry 的 work_dir, work_id = registry 键。

    返回 (path, auto_count, manual_count, regenerated)。
    """
    work_root = os.path.dirname(os.path.normpath(work_dir))
    aa_dir = os.path.join(work_root, "aa")

    path = work_glossary_path(work_dir, work_id)
    manual, auto, kn, _ = read_work_glossary(path)

    corpus = _corpus_text(aa_dir)
    if not corpus:
        # 无语料, 无法派生 auto (原样保留 manual)
        return path, len(auto), len(manual), False

    fingerprint = _fingerprint() + (_chapter_count(aa_dir),)
    fp_file = path + ".fp"
    need = force
    if not need:
        try:
            with open(fp_file, encoding="utf-8") as f:
                old_fp = tuple(json.load(f))
            need = old_fp != fingerprint
        except Exception:
            need = True

    if need:
        roles = load_roles()
        # Q14-A(2026-09-06): auto 派生排除 manual 已确认的 jp——人工确认译名不被
        # auto 重复覆盖(roles.json 同名角色会再次派生同名原文条目 → 与 manual 重复)。
        manual_jp = {m.get("jp") for m in manual if isinstance(m, dict) and m.get("jp")}
        # 2026-09-06 用户规则: 作品术语表禁止填 auto——auto 仅在表整体为空(无 manual
        # 无 auto)时生成一次作起步; 已有内容(哪怕全是 manual)后重生成不再灌 auto,
        # 防止 auto 派生词干扰/覆盖人工确认的译名(角色译名以 manual 为准)。
        # 例外: 全局正则规则(re: さん→桑 等)总是附带, 不算 auto 填充。
        regex_new = regex_rules_from_global()
        if manual or auto:
            # 2026-09-06 用户规则(对 force 同样适用): 作品术语表禁止填 auto——
            # auto 仅表整体为空时派生起步; 表已有内容(manual 或 auto)不再派生灌 auto,
            # 防自动派生干扰人工确认译名。force 只跳过指纹强制重写(内容同规则)。
            # 已有 auto 保留 + 补全局正则(去重)——Q14-A 曾覆盖为仅正则把 auto 全清
            # (bar_miku 44条→1条事故)。
            seen_re = {x["jp"] for x in auto if x.get("jp", "").startswith("re:")}
            auto_new = list(auto)
            for r_ in regex_new:
                if r_["jp"] not in seen_re:
                    auto_new.append(r_)
        else:
            auto_new = regex_new + derive_auto(corpus, roles, exclude_jp=manual_jp)
        write_work_glossary(path, manual, auto_new, kn)
        with open(fp_file, "w", encoding="utf-8") as f:
            json.dump(list(fingerprint), f)
        auto = auto_new
        regenerated = True
    else:
        regenerated = False
    return path, len(auto), len(manual), regenerated


def main():
    import argparse
    ap = argparse.ArgumentParser(description="作品术语表生成器")
    ap.add_argument("--work", required=True, help="作品 id (registry)")
    ap.add_argument("--force", action="store_true", help="强制重生成")
    args = ap.parse_args()

    sys.path.insert(0, os.path.join(BASE, "scripts"))
    sys.path.insert(0, os.path.join(BASE, "scripts", "works"))
    import registry
    work = registry.WORKS.get(args.work)
    if not work:
        print(f"[gen_work_glossary] 未找到作品 {args.work}")
        sys.exit(1)
    path, auto_n, manual_n, regen = generate(work["work_dir"], args.work, force=args.force)
    print(f"[gen_work_glossary] {args.work}: auto={auto_n} manual={manual_n} regenerated={regen} -> {path}")


if __name__ == "__main__":
    main()
