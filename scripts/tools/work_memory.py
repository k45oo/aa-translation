# -*- coding: utf-8 -*-
"""作品知识库: 作品级翻译/校对知识统一存储 (2026-08-25 作品记忆 → 2026-08-27 升级)

存 work/<work>_knowledge.json:
{
  "术语":    {"日文": "确认译名", ...},        # 固定译法(校对确认/人工)
  "语境规则": {"日文词": "本文语境含义", ...},  # 多义词在本文的确定含义
  "人物":    {"日文": "中文译名/身份/简介", ...}, # 角色译名+简介
  "章节简介": {"entry_<eid>": "本章剧情概述(1-3行)", ...},  # serial可含前情衔接; anthology含本章角色译名
  "待确认":  {"术语": {...}, "人物": {...}}     # 人工审校区(AI初稿落这里, 确认后移入正式块)
}
关联度(serial/anthology) 唯一来源 = aa_web_app/data/work_meta.json (与 r18_level 同源, 不存本文件)。

用法:
  load_knowledge(work) -> dict            # 兼容旧 load_memory 名
  save_knowledge(work, mem)
  knowledge_hint(work, base=None, ...)    # 统一注入构造(翻译/校对共用), 兼容旧 memory_hint
  add_from_issues(work, issues) -> int    # 从校对确认问题提取知识
  migrate_memory_to_knowledge(work)       # 迁移 <work>_memory.json -> <work>_knowledge.json (备份后删旧)
"""
import os, sys, json, re, shutil

# Windows GBK stdout: print 知识库日文素材/✓ 崩 UnicodeEncodeError——强制 utf-8 防崩(2026-09-08)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(BASE, "scripts"))
sys.path.insert(0, os.path.join(BASE, "scripts", "works"))
import registry

# 兼容旧名: _memory.json (旧作品记忆文件); 新文件 _knowledge.json
MEM_FNAME = "_knowledge.json"
OLD_MEM_FNAME = "_memory.json"

_KANA = re.compile(r"[ぁ-んァ-ヶ]")

# 简介生成失败哨兵(与 common/gen_chapter_summaries.SUMMARY_FAILED 同值)——
# 生成失败时写入该值; 注入侧必须识别并跳过, 否则把哨兵当简介塞进 prompt。
SUMMARY_FAILED = "__summary_failed__"


# ---------------------------------------------------------------------------
# 路径与读写
# ---------------------------------------------------------------------------

def _work_dir(work):
    try:
        cfg = registry.get(work)
        return cfg["work_dir"]
    except Exception:
        return os.path.join(BASE, "aaManga", work, "work")


def _mem_path(work):
    return os.path.join(_work_dir(work), work + MEM_FNAME)


def _old_mem_path(work):
    return os.path.join(_work_dir(work), work + OLD_MEM_FNAME)


def load_knowledge(work):
    """读作品知识库；文件缺失/损坏返回 {}。"""
    p = _mem_path(work)
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_knowledge(work, mem):
    p = _mem_path(work)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    # 2026-09-12: 原子写(tmp+replace)——简介生成改增量落盘后写盘频繁, 直接覆盖写
    # 中途崩溃/被杀会留下半截 JSON(知识库是人工维护资产, 不能坏)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(mem, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


# 兼容旧接口名 (translate_runner / proofread / 外部脚本仍用 load_memory/save_memory)
load_memory = load_knowledge
save_memory = save_knowledge


# ---------------------------------------------------------------------------
# 关联度 (唯一来源 work_meta.json)
# ---------------------------------------------------------------------------

def _load_work_meta():
    """读 aa_web_app/data/work_meta.json；缺失返回 {}。与 aa_translate._load_work_meta_local 同源。"""
    for p in (os.path.join(BASE, "aa_web_app", "data", "work_meta.json"),
              os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "aa_web_app", "data", "work_meta.json")):
        if os.path.exists(p):
            try:
                return json.load(open(p, encoding="utf-8"))
            except Exception:
                return {}
    return {}


def work_association(work):
    """作品关联度: 'serial'(默认) | 'anthology'。缺省=serial(行为兼容老作品)。"""
    try:
        meta = _load_work_meta()
        v = (meta.get(work) or {}).get("关联度") or (meta.get(work) or {}).get("association")
        return v if v in ("serial", "anthology") else "serial"
    except Exception:
        return "serial"


# ---------------------------------------------------------------------------
# 注入 hint 构造 (翻译/校对共用)
# ---------------------------------------------------------------------------

def memory_hint(work, base=None, with_summary=True, max_people=20, with_terms=True):
    """生成注入校对/翻译 prompt 的作品知识段 (兼容旧签名: memory_hint(work))。

    - 术语/语境规则/人物: 固定译法白名单 + 语境引导
    - 章节简介: base 给出时注入对应章节简介 (serial/anthology 均注入, 1-3行)
    - 人物简介: serial 注入已确认人物(按出现频次裁剪, 硬上限 max_people);
               anthology 不注入人物详情(防跨章污染, 角色译名已并入章节简介)
    - with_terms=False: 跳过【作品记忆-固定译法】段（宝可梦链用：术语已由 pre_terms
      翻译前强制替换保证，2026-10-08 用户决策）
    """
    mem = load_knowledge(work)
    if not mem:
        return ""
    parts = []
    terms = mem.get("术语", {}) if with_terms else {}
    if terms:
        t = " ".join(f"{jp}={zh}" for jp, zh in terms.items())
        parts.append(f"【作品记忆-固定译法(勿标误译,必须使用)】{t}")
    rules = mem.get("语境规则", {})
    if rules:
        r = " ".join(f"{jp}在本文={zh}" for jp, zh in rules.items())
        parts.append(f"【作品记忆-语境(多义词按此含义理解)】{r}")
    people = mem.get("人物", {})
    if people and work_association(work) != "anthology":
        # serial: 注入已确认人物; 超上限按原文件顺序裁剪(人工确认顺序≈重要度)
        items = list(people.items())
        if len(items) > max_people:
            items = items[:max_people]
        p = " ".join(f"{jp}={zh}" for jp, zh in items)
        parts.append(f"【作品记忆-角色译名】{p}")
    if with_summary and base:
        sums = mem.get("章节简介", {})
        s = sums.get(base)
        # 2026-09-12: 失败哨兵 __summary_failed__ 不是简介, 不注入(白烧 token + 无意义文本)
        if s and s != SUMMARY_FAILED:
            parts.append(f"【本章剧情简介】{s}")
    return ("\n" + "\n".join(parts) + "\n") if parts else ""


# 统一注入构造 (供 aa_translate / proofread 共用; 后续可在此并入全局术语表逻辑)
def knowledge_hint(work, base=None, max_people=20):
    """统一知识注入入口: 作品知识库段 (术语/语境/人物/章节简介)。

    与 memory_hint 等价, 名字更贴合新语义; 保持 memory_hint 作别名以兼容旧调用。
    """
    return memory_hint(work, base=base, with_summary=True, max_people=max_people)


# ---------------------------------------------------------------------------
# 校对确认问题 -> 知识积累
# ---------------------------------------------------------------------------

def add_from_issues(work, issues):
    """从校对问题清单提取知识:
    - 术语类问题(同词多译/约定译名): 建议译名 → 术语知识
    - 多义词确认(鏡=眼镜): 语境规则
    - 角色名: 人物知识
    只收录高置信(自动矫正过的, 由调用方传 issues 的 confirmed 标记)。
    过滤: new 含日文假名(改回日文=不可靠) / 超长(整句非词) / 纯标点。
    """
    mem = load_knowledge(work)
    added = 0
    for iss in issues:
        if not iss.get("confirmed"):
            continue
        typ = iss.get("type", "")
        old, new = iss.get("old", ""), iss.get("new", "")
        if not old or not new or len(old) > 8 or len(new) > 8:
            continue
        if _KANA.search(new):
            continue   # 建议改回日文=不可靠, 不积累
        if old == new or len(old) < 2 or len(new) < 2:
            continue
        if "术语" in typ or "译名" in typ or "同词" in typ:
            if old not in mem.get("术语", {}):
                mem.setdefault("术语", {})[old] = new
                added += 1
        elif "角色" in typ or "人物" in typ:
            if old not in mem.get("人物", {}):
                mem.setdefault("人物", {})[old] = new
                added += 1
        elif "多义" in typ or "语境" in typ:
            if old not in mem.get("语境规则", {}):
                mem.setdefault("语境规则", {})[old] = new
                added += 1
        elif "误译" in typ:
            # 误译但短词(2-4字)且无假名 = 可能是角色名/术语统一(如 亚鲁夫→亚鲁欧)
            if len(old) <= 4 and len(new) <= 4:
                if old not in mem.get("术语", {}):
                    mem.setdefault("术语", {})[old] = new
                    added += 1
    if added:
        save_knowledge(work, mem)
    return added


# ---------------------------------------------------------------------------
# 迁移 (memory.json -> knowledge.json)
# ---------------------------------------------------------------------------

def migrate_memory_to_knowledge(work, backup_dir=None):
    """把旧 <work>_memory.json 迁移为 <work>_knowledge.json。

    - 先复制旧文件到 work/backup/ (审批意见9: 迁移先备份再删旧)
    - 旧四键直接搬入新文件; 无冲突
    - 迁移后删除旧文件
    返回: True=已迁移, False=无旧文件/已迁移过
    """
    old_p = _old_mem_path(work)
    if not os.path.exists(old_p):
        return False
    try:
        old = json.load(open(old_p, encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(old, dict):
        return False
    mem = load_knowledge(work)
    for key in ("术语", "语境规则", "人物"):
        if isinstance(old.get(key), dict):
            mem.setdefault(key, {}).update(old[key])
    # 备份旧文件
    wd = _work_dir(work)
    bdir = backup_dir or os.path.join(wd, "backup")
    os.makedirs(bdir, exist_ok=True)
    bak = os.path.join(bdir, work + OLD_MEM_FNAME)
    try:
        shutil.copy2(old_p, bak)
    except Exception:
        pass
    save_knowledge(work, mem)
    try:
        os.remove(old_p)
    except Exception:
        pass
    return True


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--show", action="store_true", help="显示作品知识库")
    ap.add_argument("--base", default=None, help="章节 base(entry_<eid>), 显示时附带章节简介")
    ap.add_argument("--migrate", action="store_true", help="迁移旧 _memory.json -> _knowledge.json")
    a = ap.parse_args()
    if a.migrate:
        ok = migrate_memory_to_knowledge(a.work)
        print(f"[migrate] {a.work}: {'已迁移' if ok else '无旧文件/已迁移'}")
    elif a.show:
        mem = load_knowledge(a.work)
        print(json.dumps(mem, ensure_ascii=False, indent=1) if mem else "(空)")
    else:
        print(memory_hint(a.work, base=a.base))
