# -*- coding: utf-8 -*-
r"""把 技能库里的 AA 相关技能同步成 `.codex/skills/`（Codex CLI 兼容副本）。

为什么需要：`.codex/skills/` 是**派生副本**，手工维护必然失同步（2026-08 那批已明显落后：
改名/合并后的旧目录、已删的 AAhub 内容都还在）。本工具让「重新同步」变成一条命令，
并在生成时做两件事：

1. **格式转换**：frontmatter 只留 `name` + `description`（Codex 读取的最小集）；
   去掉 Hermes 专属工具/编排措辞；统一 LF。
2. **信息脱敏**：本机绝对路径与个人标识一律替换为占位符（`<repo>` / `<repo-bare>` /
   `<skills>` / `<user>`），并在写盘后**复查残留**——命中即拒绝出包（rc=2），
   避免把生成者的目录结构带进可外发的副本里。

用法（从仓库根运行）：
    python scripts/tools/sync_codex_skills.py                 # 全量重建 .codex/skills
    python scripts/tools/sync_codex_skills.py --check         # 只校验，不写盘（rc=2 表示有残留/失同步）
    python scripts/tools/sync_codex_skills.py --src <技能库>  # 换源（默认 env AA_SKILLS_SRC）

源目录解析顺序：`--src` > env `AA_SKILLS_SRC` / `HERMES_SKILLS_DIR` > `HERMES_HOME` 下的档案技能目录
> 与仓库同级的 agent 安装目录（`<repo-parent>` 下的 profiles 布局）。
"""
import argparse
import io
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from works import paths  # noqa: E402  仓库根唯一来源

REPO = paths.BASE
DST = os.path.join(REPO, ".codex", "skills")

# 纳入同步的技能：名字以 aa- 开头，或显式列出的 AA 管线配套技能（非 AA 的图漫/通用技能不收）
EXTRA_NAMES = {"board-raw-download"}
SKIP_NAMES = {"aa-r18-and-glossary-ops"}      # 历史合并目录（无 SKILL.md）

# ---- 脱敏规则：顺序敏感（先长后短）----
PATH_RULES = [
    (re.compile(r"[Ff]:[\\/]{1,2}ai[\\/]{1,2}aa-translation", re.I), "<repo-bare>"),
    (re.compile(r"<repo-bare>", re.I), "<repo-bare>"),
    (re.compile(r"[Ff]:[\\/]{1,2}ai[\\/]{1,2}AA翻译"), "<repo>"),
    (re.compile(r"<repo>"), "<repo>"),
    (re.compile(r"[Ff]:[\\/]{1,2}ai(?=[\\/])"), "<repo-parent>"),
    (re.compile(r"<repo-parent>/"), "<repo-parent>/"),
    (re.compile(r"[Ff]:[\\/]{1,2}ai(?![\\/A-Za-z\u4e00-\u9fff])"), "<repo-parent>"),
    (re.compile(r"<repo-parent>(?![\\/A-Za-z])"), "<repo-parent>"),
    (re.compile(r"[Ff]:[\\/]{1,2}hermes[\\/]{1,2}profiles[\\/]{1,2}translation[\\/]{1,2}skills"), "<skills>"),
    (re.compile(r"[Ff]:[\\/]{1,2}hermes"), "<hermes>"),
    (re.compile(r"[Ff]:[\\/]AppData[\\/]Roaming"), "<appdata>"),
    # 兜底：盘符已被前面规则吃掉、但仍以「仓库目录名 + 已知子目录」形式出现的相对路径
    (re.compile(r"(?<![\\/\w])AA翻译[\\/]{1,2}(?=(?:aaManga|交付HTML|揭示板|scripts|doc|fonts|aa_web_app|temp|导出))"),
     "<repo>/"),
    (re.compile(r"[Cc]:[\\/]{1,2}Users[\\/]{1,2}[^\\/\s\"'`]+", re.I), "<user>"),
    (re.compile(r"<user>/\s\"'`]+", re.I), "<user>"),
]
# Hermes 专属工具/编排措辞 → 通用说法（副本给别的 agent 用）
TERM_RULES = [
    (re.compile(r"\bskill_view\s*\([^)]*\)"), "（技能内容）"),
    (re.compile(r"\bskill_manage\s*\([^)]*\)"), "（技能维护）"),
    (re.compile(r"\bskills_list\b"), "技能清单"),
    (re.compile(r"\bdelegate_task\b"), "子代理"),
    (re.compile(r"\bcronjob_manage\b"), "定时任务"),
    (re.compile(r"技能库"), "技能库"),
    (re.compile(r"\(user-owned\)"), ""),
]
# 残留门禁：写完必须为 0（否则 rc=2）——只在「路径后跟真实名」时报，避免误伤文档里
# 讨论这些前缀本身的句子（如「脱敏门禁拦 用户名/C:\Users」）。
LEFTOVER = [
    re.compile(r"[Ff]:[\\/]{1,2}ai[\\/]{1,2}[^\s`\"'）)]"), re.compile(r"<repo-parent>/[^\s`\"'）)]"),
    re.compile(r"[Ff]:[\\/]{1,2}hermes[\\/]"), re.compile(r"[Cc]:[\\/]{1,2}Users[\\/]{1,2}[A-Za-z]"),
    re.compile(r"hermes[/\\]profiles"),
]


def user_names():
    """要脱敏的用户名：env `AA_SCRUB_NAMES`（逗号分隔）∪ 当前系统用户名（不写死任何名字）。"""
    names = [n.strip() for n in os.environ.get("AA_SCRUB_NAMES", "").split(",") if n.strip()]
    for k in ("USERNAME", "USER", "LOGNAME"):
        v = os.environ.get(k)
        if v and v.lower() not in ("root", "user", "admin"):
            names.append(v)
    return sorted(set(names), key=len, reverse=True)


def user_rules():
    return [(re.compile(r"\b" + re.escape(n) + r"\b", re.I), "<user>") for n in user_names()]


def leftover_patterns():
    return LEFTOVER + [re.compile(r"\b" + re.escape(n) + r"\b", re.I) for n in user_names()]
# 密钥形状门禁（技能里只该出现 env 变量名，不该出现值）
SECRETS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), re.compile(r"\btp-[A-Za-z0-9]{12,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"), re.compile(r"\bAKIA[0-9A-Z]{12,}"),
    re.compile(r"\b[0-9a-f]{40}\b"),
]


def find_skills(src):
    out = []
    for dirpath, dirnames, filenames in os.walk(src):
        if ".archive" in os.path.normpath(dirpath).split(os.sep):
            continue
        if "SKILL.md" not in filenames:
            continue
        name = os.path.basename(dirpath)
        if name in SKIP_NAMES:
            continue
        rel = os.path.relpath(dirpath, src)
        in_aa_tree = rel.split(os.sep)[0] in ("media", "aa-translation") or rel.split(os.sep)[0].startswith("aa-")
        if in_aa_tree and (name.startswith("aa-") or name in EXTRA_NAMES):
            out.append((name, dirpath))
    out.sort()
    return out


def slim_frontmatter(text, name):
    """frontmatter 只留 name + description。"""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end < 0:
        return text
    fm = text[3:end]
    desc = ""
    for line in fm.split("\n"):
        if line.strip().lower().startswith("description:"):
            desc = line.split(":", 1)[1].strip().strip('"').strip("'")
    if not desc:
        desc = f"Use when 处理 AA 汉化管线相关任务（{name}）。"
    body = text[end + 4:].lstrip("\n")
    return f"---\nname: {name}\ndescription: {desc}\n---\n\n{body}"


def scrub(text):
    for pat, rep in PATH_RULES + user_rules():
        text = pat.sub(rep, text)
    for pat, rep in TERM_RULES:
        text = pat.sub(rep, text)
    return text


def transform(text, name):
    return scrub(slim_frontmatter(text, name)).replace("\r\n", "\n").replace("\r", "\n")


def write_skill(dst_root, name, src_dir, check):
    """同步一个技能：写 SKILL.md + references/**；并清掉副本里多出来的旧文件（镜像=精确复刻）。"""
    written, expected = [], set()
    entries = []
    for dirpath, dirnames, filenames in os.walk(src_dir):
        rel = os.path.relpath(dirpath, src_dir)
        for fn in filenames:
            if fn.lower().endswith(".md"):
                entries.append((os.path.join(dirpath, fn), fn if rel == "." else os.path.join(rel, fn)))
    for src_p, rel_p in entries:
        expected.add(os.path.normpath(rel_p))
        dst_p = os.path.join(dst_root, name, rel_p)
        new = transform(io.open(src_p, encoding="utf-8", errors="ignore").read(), name)
        old = None
        if os.path.exists(dst_p):
            old = io.open(dst_p, encoding="utf-8", errors="ignore").read().replace("\r\n", "\n")
        if old != new:
            written.append(rel_p)
            if not check:
                os.makedirs(os.path.dirname(dst_p), exist_ok=True)
                io.open(dst_p, "w", encoding="utf-8", newline="\n").write(new)
    # 副本里残留的旧文件（旧版是平铺 references，改名/合并后会留下同名不同层的文件）
    skill_dir = os.path.join(dst_root, name)
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            rel = os.path.normpath(os.path.relpath(p, skill_dir))
            if rel not in expected:
                written.append(rel + "（删除·副本多余）")
                if not check:
                    os.remove(p)
    return written


def default_src():
    """技能库默认位置：env 优先，其次按常见布局探测（找不到就要求 --src）。"""
    for k in ("AA_SKILLS_SRC", "HERMES_SKILLS_DIR"):
        v = os.environ.get(k)
        if v:
            return v
    cands = []
    home = os.environ.get("HERMES_HOME")
    if home:
        cands += [os.path.join(home, "profiles", "translation", "skills"),
                  os.path.join(home, "skills")]
    up = os.path.dirname(REPO)
    cands += [os.path.join(up, "hermes", "profiles", "translation", "skills"),
              os.path.join(os.path.dirname(up), "hermes", "profiles", "translation", "skills")]
    for c in cands:
        if os.path.isdir(c):
            return c
    return cands[0] if cands else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=default_src())
    ap.add_argument("--dst", default=DST)
    ap.add_argument("--check", action="store_true", help="只校验（不写盘）：rc=2 表示有残留或副本失同步")
    ap.add_argument("--no-clean", action="store_true", help="不删除副本里多出来的旧技能目录")
    args = ap.parse_args()

    if not os.path.isdir(args.src):
        print(f"技能库不存在: {args.src}\n（用 --src 或 env AA_SKILLS_SRC 指定）", file=sys.stderr)
        return 1
    skills = find_skills(args.src)
    if not skills:
        print(f"没找到任何 AA 技能: {args.src}", file=sys.stderr)
        return 1

    changed, per_skill = [], {}
    for name, src_dir in skills:
        w = write_skill(args.dst, name, src_dir, args.check)
        per_skill[name] = w
        changed += [f"{name}/{x}" for x in w]

    stale = []
    if os.path.isdir(args.dst):
        keep = {n for n, _ in skills} | {"README.md"}
        for entry in sorted(os.listdir(args.dst)):
            if entry not in keep and os.path.isdir(os.path.join(args.dst, entry)):
                stale.append(entry)
                if not args.check and not args.no_clean:
                    shutil.rmtree(os.path.join(args.dst, entry))

    # 门禁：残留 + 密钥形状
    leaks, secrets = [], []
    if os.path.isdir(args.dst):
        for dirpath, dirnames, filenames in os.walk(args.dst):
            for fn in filenames:
                p = os.path.join(dirpath, fn)
                t = io.open(p, encoding="utf-8", errors="ignore").read()
                for pat in leftover_patterns():
                    for m in pat.finditer(t):
                        leaks.append((os.path.relpath(p, args.dst), m.group(0)))
                for pat in SECRETS:
                    for m in pat.finditer(t):
                        # 允许 git 短 hash 之外的 40 位 hex（正文里的 sha1 指纹说明）→ 只报前 8 位供人工判断
                        secrets.append((os.path.relpath(p, args.dst), m.group(0)[:12]))

    print(f"源: {args.src}\n目标: {args.dst}\n技能 {len(skills)} 个 / 文件变更 {len(changed)} 个")
    for name in sorted(per_skill):
        if per_skill[name]:
            print(f"  {name}: {len(per_skill[name])} 个文件更新")
    print("  旧目录清理:", ", ".join(stale) if stale else "无")
    if leaks:
        print(f"\n❌ 脱敏残留 {len(leaks)} 处（拒绝出包）:", file=sys.stderr)
        for f, s in leaks[:20]:
            print(f"   {f}: {s}", file=sys.stderr)
    if secrets:
        print(f"\n❌ 疑似密钥形状 {len(secrets)} 处（人工确认）:", file=sys.stderr)
        for f, s in secrets[:20]:
            print(f"   {f}: {s}…", file=sys.stderr)
    if args.check and (changed or stale):
        print("\n⚠️ 副本与技能库不一致（需重新同步）", file=sys.stderr)
    print("\nOK" if not (leaks or secrets or (args.check and (changed or stale))) else "\nFAILED")
    return 2 if (leaks or secrets or (args.check and (changed or stale))) else 0


if __name__ == "__main__":
    raise SystemExit(main())
