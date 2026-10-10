# -*- coding: utf-8 -*-
r"""生成「脱敏代码镜像」并推送到裸仓库（默认 `origin`，即 `../aa-translation`）。

用途：把本仓库的**代码与文档**（不含作品数据、手写数据资产、抓来的网页等）打成
一个**全新历史单提交**的快照，脱敏后 force-push 到镜像仓库。镜像里没有数据，
所以它天然免疫「历史里删不掉的数据对象」与「旧提交里的本机路径」。

用法（从仓库根运行）：
    python scripts/tools/push_code_mirror.py --check     # 只构建 + 跑门禁，不推（rc=2 = 有残留/超限）
    python scripts/tools/push_code_mirror.py             # 构建 + 门禁 + 单提交 + force push
    python scripts/tools/push_code_mirror.py --remote <url|path> --branch main --keep
        --keep    保留构建目录（排查用）；默认删
        --dry-run 构建 + 门禁 + 本地提交，但不 push

快照内容 = 下面的 INCLUDE 白名单 − EXCLUDE 规则；wiki 目录只取「生成物 + 手改源」（不含 `_raw/`）。
门禁（命中即 rc=2 拒推）：未脱敏路径 / 用户名 / 密钥形状 / 体积上限；`.py` 额外要求 py_compile 全过。
提交说明由 `mirror_message()` 依**快照内容 + 当天日期 + 工作库近期提交标题**生成（刻意不含远端信息：
同一份内容在同一天于两处镜像推出同一个 sha，便于交叉核对）。
"""
import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                           # tools/（同目录工具互引）
sys.path.insert(0, os.path.dirname(HERE))          # scripts/
from works import paths                            # noqa: E402  仓库根唯一来源
import sync_codex_skills as scrub_mod              # noqa: E402  脱敏规则与技能副本共用

REPO = paths.BASE

# ---- 快照内容（白名单）----
INCLUDE_DIRS = ["scripts", "aa_web_app", ".codex/skills", "fonts"]
INCLUDE_FILES = ["glossary.json", "roles.json", ".gitattributes", ".gitignore", "LICENSE", "doc/接口文档.md",
                 "一键重启工作台.bat", "一键打包发行包.bat"]
WIKI_GLOB_DIR = os.path.join("doc", "aa作品相关")   # 只取其中的 *wiki 目录
WIKI_KEEP_EXTRA = {".json", ".md", ".txt"}          # 生成物 + 手改源（.txt 是 wiki 说明）

# ---- 排除规则（相对快照根）----
EXCLUDE_DIRS = {
    "scripts/legacy",              # 历史归档（自包含只读，勿再使用；23 个硬编码本机路径在它里面）
    "aa_web_app/data",             # 运行时状态（work_meta/tasks/tts_cache/selection_log…）
    "scripts/tools/dist",          # 打包产物（AA工作台.exe / AA编辑服务.exe，可重建）
    "fonts/subset",                # 字体子集缓存（可重建）
}
EXCLUDE_NAMES = {"__pycache__", ".pytest_cache"}
EXCLUDE_TEXT_PAT = [re.compile(p) for p in (
    r"(?:^|/)__pycache__/",                     # 目录名不在 basename 上，得按路径判
    r"\.py[co]$",
    r"^scripts/benchmark/report/",              # 评测结果（主仓 .gitignore 也忽略 = 生成物）
    r"(^|/)\.bak_", r"_raw/", r"\.bak$", r"\.tmp$", r"\.log$", r"\.log\.\d+$", r"\.pyc$",
    r"SemiBold\.ttf$",            # 只用 Regular（aa_font.py）；SemiBold 24MB 不进镜像
    r"(^|/)\.DS_Store$", r"\.db$", r"\.ydb$", r"\.png$", r"\.jpg$", r"\.jpeg$",
)]
MAX_MB = 100.0

# 镜像 README（写进快照根；源仓库的 README 不参与快照）
ONLINE_README = """# AA 翻译 —— やる夫系 AA 漫画汉化管线

把日文 AA 漫画（2ch 字符画）批量汉化为简体中文：**抓页 → 提取 → 分块翻译 → 漏译清理 →
宽度/残留校验 → 术语统一 → HTML 合集**。艺术行（纯字符画）本地拼接、不送模型，只把文本行
交给大模型，因此成本低、误伤少；翻译过程有术语表注入、R18 分级、端点自动降级兜底。

* **命令行零依赖**：`scripts/` 下的脚本只用 Python 标准库，Python 3.11+ 直接跑。
* **图形界面（矫正工作台）**：本地 Web 应用（FastAPI）——单章矫正、翻译控制台、校对、术语表、
  任务看板、作品导入导出、设置页；默认 <http://127.0.0.1:8765>。

## 一、环境准备

```bash
python -m venv .venv
.venv/Scripts/pip install fastapi uvicorn jinja2      # 只用命令行的话可以跳过
```

## 二、填一个 API key

* 启动工作台（仓库根双击 `一键重启工作台.bat`，或 `python scripts/tools/aa_web.py`）→ 右上
  「设置 → 密钥」→ 填 `DEEPSEEK_API_KEY`（<https://platform.deepseek.com/> 注册后在 API Keys 创建）
  → 「测试连接」显示可用即可。key 只写在本机 `.env`，不会外传。
* 也可以直接建仓库根 `.env`：`DEEPSEEK_API_KEY=sk-…`。优先级：真实环境变量 > `.env` >
  `scripts/config/model_config.toml` > 代码默认。

## 三、跑通一部作品

| 步骤 | 做法 |
|---|---|
| 添加作品 | 工作台首页「＋ 添加作品」→ 粘贴原站**分类页 URL**（yaruobook 三站 / r401 等）→ 确认 |
| 下载原文 | 作品列表「更多 → 更新」；或 `python scripts/runner/dl_runner.py --work <id> --chapters all` |
| 生成术语表 | **翻译前必须有作品术语表**（缺表 runner 会中止并给出命令）：工作台作品页「生成术语候选」→ 勾选确认；或零 token 派生 `python scripts/common/gen_work_glossary.py --work <id> --force` |
| 翻译 | 工作台「翻译控制台」勾章节；或 `python scripts/runner/translate_runner.py --work <id> --chapters new` |
| 校对 | 翻译后自动跑 `refine_aa`（漏译 / 残留假名 / 艺术行还原一次覆盖）；也可单独跑校对任务（工作台校对页选章） |
| 生成合集 | 工作台「重生成合集」；或 `python scripts/common/gen_aa_html.py --work <id>` |

**产物**：`aaManga/<作品>/汉化/*_汉化.txt`（与原文逐行等宽）+ `aaManga/交付HTML/<标题>_汉化合集/`
（`index.html` + 章节数据，右上可切「译 / 原文」；外置数据形态要整目录拷贝才能离线打开）。

## 四、命令行速查

```
python scripts/runner/dl_runner.py         --work <id> [--chapters all|new|1-20]  # 下载章节
python scripts/runner/translate_runner.py  --work <id> [--chapters new] [--force] # 翻译 + 清理(+校对)
python scripts/common/scan_width.py        --work <id>                            # 宽度校验
python scripts/fix/refine_aa.py            --work <id> [--eids …] [--write]       # 漏译清理 / 校对
python scripts/common/gen_aa_html.py       --work <id>                            # 生成交付文件夹
```

全部命令与 env 见 `doc/接口文档.md`；脚本分级 / 废弃清单见 `scripts/_inventory.json`；
`scripts/` 结构的权威说明见 `scripts/README.md`。

## 五、打包成开箱即用的发行包

```bash
一键打包发行包.bat                      # 或 python scripts/tools/build_release.py --out 导出
```

产出 `导出/AA翻译_发行包_<日期>.zip`：干净源码 + `AA工作台.exe`（不装 Python 也能双击用）+
示例作品 + 独立 README / requirements.txt / .env.example。详见 `scripts/README.md`。

## 六、注意事项

* **代理**：抓原站、DeepInfra 等端点需要本地代理（如 v2rayN `http://127.0.0.1:10808`），
  在工作台「设置 → 代理」里填。
* **R18 作品**：部分后端有内容审查（dashscope 不可用、MiniMax 不支持），作品可标 R18 等级，
  翻译时自动走 `r18_backends` 链。
* **术语表**：全局 `glossary.json` + 角色表 `roles.json` 随仓库提供；**作品级术语表需要自己生成**。
* **作品数据不在仓库内**：`aaManga/`（原文 / 汉化 / 交付物）与作品手写详情由 runner 生成，
  `.gitignore` 已排除，不会误提交。
* 工作台只监听本机 `127.0.0.1`、无鉴权，不要暴露到公网。
* 自测：`python -m unittest discover -s scripts/tests`（约 2 分钟，零 API）。

## 许可 / 来源

* **代码**：MIT，见 [`LICENSE`](LICENSE)。
* **字体**：`fonts/SarasaMonoSC-Regular.ttf`（Sarasa Mono SC，等距更纱黑体）采用 SIL Open Font
  License 1.1，许可证全文见 [`fonts/OFL.txt`](fonts/OFL.txt)（分发字体时请一并保留该文件）。
* **作品内容**版权归原作者：本仓库不含任何作品文本；Releases 的发行包内含 1 部示例作品的
  前 3 话原文，仅用于跑通流程，可自行删除。
"""


def _resolve_remote(url):
    if not url:
        try:
            url = subprocess.run(["git", "remote", "get-url", "origin"], cwd=REPO,
                                 capture_output=True, text=True, check=True).stdout.strip()
        except subprocess.CalledProcessError:
            url = ""
        if not url:
            raise SystemExit("找不到 remote origin，用 --remote 指定镜像仓库")
    if re.match(r"^(https?|ssh|git|file)://", url) or url.startswith("git@"):
        return url
    return os.path.abspath(os.path.join(REPO, url))


def _excluded(rel):
    rel = rel.replace(os.sep, "/")
    if rel in EXCLUDE_DIRS or any(rel.startswith(d + "/") for d in EXCLUDE_DIRS):
        return True
    if os.path.basename(rel) in EXCLUDE_NAMES:
        return True
    return any(p.search(rel) for p in EXCLUDE_TEXT_PAT)


def build(dst):
    """按白名单拷贝 + 脱敏；返回 (文件数, 逐目录统计)。"""
    copies = []
    for d in INCLUDE_DIRS:
        src = os.path.join(REPO, d)
        if not os.path.isdir(src):
            continue
        for dirpath, dirnames, filenames in os.walk(src):
            dirnames[:] = [x for x in dirnames if x not in EXCLUDE_NAMES]
            for fn in filenames:
                p = os.path.join(dirpath, fn)
                rel = os.path.relpath(p, REPO)
                if not _excluded(rel):
                    copies.append((p, rel))
    for f in INCLUDE_FILES:
        p = os.path.join(REPO, f)
        if os.path.isfile(p):
            copies.append((p, f))
    wiki_root = os.path.join(REPO, WIKI_GLOB_DIR)
    if os.path.isdir(wiki_root):
        for name in sorted(os.listdir(wiki_root)):
            d = os.path.join(wiki_root, name)
            if not (os.path.isdir(d) and name.endswith("wiki")):
                continue
            for dirpath, dirnames, filenames in os.walk(d):
                dirnames[:] = [x for x in dirnames if x != "_raw"]
                for fn in filenames:
                    p = os.path.join(dirpath, fn)
                    rel = os.path.relpath(p, REPO)
                    ext = os.path.splitext(fn)[1].lower()
                    if ext in WIKI_KEEP_EXTRA or fn == "index.html" or _excluded(rel):
                        if not _excluded(rel):
                            copies.append((p, rel))

    per_dir, scrub_ext = {}, {".md", ".txt", ".json", ".toml", ".bat", ".py", ".js", ".css",
                              ".html", ".gitattributes", ".gitignore", "NOEXT"}
    for src, rel in copies:
        out = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        ext = os.path.splitext(rel)[1].lower()
        if ext in scrub_ext:
            t = io.open(src, encoding="utf-8", errors="ignore").read()
            io.open(out, "w", encoding="utf-8", newline="\n").write(scrub_mod.scrub(t))
        else:
            shutil.copy2(src, out)
        per_dir[rel.split(os.sep)[0]] = per_dir.get(rel.split(os.sep)[0], 0) + 1
    io.open(os.path.join(dst, "README.md"), "w", encoding="utf-8", newline="\n").write(ONLINE_README)
    per_dir["README.md(生成)"] = 1
    return len(copies) + 1, per_dir


def gates(dst, max_mb):
    """门禁：未脱敏路径 / 用户名 / 密钥形状 / 体积 / .py 可编译。返回 (errors, warnings)。"""
    errors, warns = [], []
    py_bad = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(dst):
        if ".git" in dirpath.split(os.sep):
            continue
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, dst)
            try:
                total += os.path.getsize(p)
            except OSError:
                pass
            ext = os.path.splitext(fn)[1].lower()
            if ext in (".md", ".txt", ".json", ".toml", ".bat", ".py", ".js", ".css",
                       ".html", ".gitattributes", ".gitignore"):
                t = io.open(p, encoding="utf-8", errors="ignore").read()
                for pat in scrub_mod.leftover_patterns():
                    for m in pat.finditer(t):
                        errors.append(f"未脱敏路径/用户名 {rel}: {m.group(0)}")
                for pat in (re.compile(r"\b(?:sk-[A-Za-z0-9_\-]{20,}|tp-[A-Za-z0-9]{12,}|"
                                       r"ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b"),):
                    for m in pat.finditer(t):
                        errors.append(f"疑似密钥形状 {rel}: {m.group(0)[:12]}…")
            if ext == ".py":
                # 进程内编译检查：不要用 `python -m py_compile`（它会就地写出 __pycache__/*.pyc，
                # 污染快照 → 数量与提交内容对不上；曾因此让 214 个 pyc 混进快照目录）
                try:
                    compile(io.open(p, encoding="utf-8", errors="ignore").read(), p, "exec")
                except SyntaxError as e:
                    py_bad.append(f"{rel}: {e.msg} (line {e.lineno})")
    if py_bad:
        errors += [f".py 编译失败 {x}" for x in py_bad]
    mb = total / 1048576
    if mb > max_mb:
        errors.append(f"体积 {mb:.1f}MB 超过上限 {max_mb}MB（--max-mb 可调）")
    if total == 0:
        errors.append("快照为空")
    return errors, warns, total



def _is_url(remote):
    """远端是不是 URL（https/ssh/git/file 或 git@host:path）——URL 远端不能直接 git -C。"""
    return bool(re.match(r"^(https?|ssh|git|file)://", remote or "")) or (remote or "").startswith("git@")


_PROBE = {}


def _probe(remote):
    """URL 远端：裸克隆（--filter=blob:none，只取树不取文件内容）到临时目录，进程内复用。"""
    if remote not in _PROBE:
        d = tempfile.mkdtemp(prefix="aa_mirror_probe_")
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")     # 缺凭据就快速失败，别卡在提示
        r = subprocess.run(["git", "clone", "--bare", "--filter=blob:none", "--quiet", remote, d],
                           capture_output=True, text=True, env=env)
        _PROBE[remote] = d if r.returncode == 0 else ""
        if not r.returncode:
            pass
        elif "could not read Username" in (r.stderr or "") or "Authentication failed" in (r.stderr or ""):
            print(f"⚠️ 无法认证 {remote}：{r.stderr.strip().splitlines()[-1][:120]}", file=sys.stderr)
    return _PROBE[remote]


def remote_tree(remote, branch):
    """读镜像仓库当前分支的 {路径: blob sha}（不存在→None；URL 远端走临时裸克隆）。"""
    target = _probe(remote) if _is_url(remote) else remote
    if not target:
        return None
    r = subprocess.run(["git", "-C", target, "ls-tree", "-r", "--format=%(objectname) %(path)", branch],
                       capture_output=True, text=True)
    if r.returncode:
        return None
    out = {}
    for line in r.stdout.splitlines():
        if " " in line:
            sha, path = line.split(" ", 1)
            out[path.strip()] = sha
    return out


def local_tree(dst, branch):
    """在快照里临时 git add，取 {路径: blob sha}。"""
    subprocess.run(["git", "init", "-q", "-b", branch], cwd=dst, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=dst, capture_output=True)
    r = subprocess.run(["git", "ls-files", "-s"], cwd=dst, capture_output=True, text=True)
    out = {}
    for line in r.stdout.splitlines():
        meta, path = line.split("\t", 1)
        out[path.strip()] = meta.split()[1]
    return out


def _recent_subjects(limit=12):
    """工作库最近的提交标题（写进镜像提交说明，读起来像 changelog）。

    只取标题、不带 sha：镜像里没有这些提交（全新历史的单提交快照），给 sha 反而误导。
    """
    try:
        r = subprocess.run(["git", "log", f"-{limit}", "--no-merges", "--format=%s"],
                           cwd=REPO, capture_output=True, text=True, timeout=20)
    except Exception:
        return []
    if r.returncode:
        return []
    return [s.strip() for s in r.stdout.splitlines() if s.strip()][:limit]


def _mirror_counts(dst):
    """快照内的结构性计数（站点解析器 / 测试文件 / 技能 / wiki 目录），供目录注记用。"""
    def count(sub, pred):
        n = 0
        for _dp, _dn, filenames in os.walk(os.path.join(dst, *sub.split("/"))):
            n += sum(1 for fn in filenames if pred(fn))
        return n

    wiki_root = os.path.join(dst, "doc", "aa作品相关")
    wikis = 0
    if os.path.isdir(wiki_root):
        wikis = len([x for x in os.listdir(wiki_root)
                     if x.endswith("wiki") and os.path.isdir(os.path.join(wiki_root, x))])
    return {
        "sites": count("scripts/sites", lambda fn: fn.endswith(".py") and not fn.startswith("__")
                       and fn != "_base.py"),
        "tests": count("scripts/tests", lambda fn: fn.startswith("test_") and fn.endswith(".py")),
        "skills": count(".codex/skills", lambda fn: fn == "SKILL.md"),
        "wikis": wikis,
    }


def mirror_message(dst, stamp, n, total, per_dir, subjects=None):
    """镜像单提交的说明。

    **刻意只用「快照内容 + 当天日期 + 工作库近期提交标题」**，不引入任何远端相关信息
    （例如「相对上次快照的 diff」）——否则两处镜像（本地裸仓库 / GitHub）在同一份内容上
    会因 push 顺序不同而得到不同 sha，破坏「同内容同日期 = 同 sha 可交叉核对」。
    """
    c = _mirror_counts(dst)
    note = {
        "scripts": ("共享实现 common/ · runner/ 入口（下载/翻译/校对/揭示板）· sites/ "
                    f"{c['sites']} 个站点解析器\n"
                    "tools/ 独立 CLI · fix/ 修复 · verify/ 校验 · benchmark/ 模型评估 · tests/ "
                    f"{c['tests']} 个测试文件"),
        ".codex": f"Codex CLI 兼容格式的技能副本（{c['skills']} 个技能，由 tools/sync_codex_skills.py 生成）",
        "aa_web_app": "本地矫正工作台（FastAPI）：main 路由 · logic · task_manager · settings · work_pack · selection",
        "doc": f"方案归档 + 作品 wiki 素材（{c['wikis']} 部）",
        "fonts": "Sarasa Mono SC（OFL 1.1）+ 许可文本",
    }
    L = [f"AA翻译管线代码与文档快照（{stamp}）", ""]
    L += [
        "やる夫系 AA 漫画（2ch 字符画）汉化管线：抓页 → 提取 → 分块翻译 → 漏译清理 → 宽度/残留校验 →",
        "术语统一 → HTML 合集。命令行脚本只用 Python 标准库（3.11+）；另带本地矫正工作台（FastAPI）。",
        "",
        "本仓库是**全新历史的单提交快照**（每次同步 force-push 覆盖）：只含代码 / 文档 / 技能 / 字体 /",
        "术语表；作品数据（原文·汉化·抓取的网页·任务与使用记录）、legacy 归档与构建产物均不入库。",
        "",
        f"内容：{n} 文件 / {total / 1048576:.1f} MB",
    ]
    singles = []
    for k, v in sorted(per_dir.items(), key=lambda kv: (-kv[1], kv[0])):
        top = k.split("/")[0]
        # 目录（k == top 且在注记表里）走目录行；其余（顶层单文件 + doc/接口文档.md 这类深路径文件）平铺
        if top in note and k == top:
            head, *more = note[top].split("\n")
            L.append(f"  {k:<18}{v:>4}  {head}")
            L += [f"  {'':<18}{'':>4}  {m}" for m in more]
        else:
            singles.append(k.replace("(生成)", ""))
    if singles:
        L.append("  另含单文件：" + " · ".join(sorted(singles)))
    subj = _recent_subjects() if subjects is None else list(subjects)
    if subj:
        L += ["", f"快照内的近期改动（工作库最近 {len(subj)} 条提交，对照片里已包含哪些功能）"]
        L += [f"  - {s}" for s in subj]
    L += [
        "",
        "上手见 `README.md`（环境准备 → 填 API key → 添加作品 → 下载原文 → 生成术语表 → 翻译 → 校对 → 合集）；",
        "命令与 env 清单见 `doc/接口文档.md`，脚本分级见 `scripts/_inventory.json`，`scripts/` 结构说明见 `scripts/README.md`。",
    ]
    return "\n".join(L).rstrip() + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--remote", default="", help="镜像仓库（默认取 remote origin 解析成绝对路径）")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--max-mb", type=float, default=MAX_MB)
    ap.add_argument("--check", action="store_true", help="只构建 + 门禁，不提交不推")
    ap.add_argument("--dry-run", action="store_true", help="构建 + 提交，但不 push")
    ap.add_argument("--keep", action="store_true", help="保留构建目录")
    args = ap.parse_args()

    remote = _resolve_remote(args.remote)
    dst = tempfile.mkdtemp(prefix="aa_mirror_")
    try:
        n, per_dir = build(dst)
        errors, warns, total = gates(dst, args.max_mb)
        print(f"快照目录: {dst}\n文件 {n} 个 / {total/1048576:.1f} MB（上限 {args.max_mb}MB）")
        for k, v in sorted(per_dir.items(), key=lambda kv: -kv[1]):
            print(f"   {v:5d}  {k}")
        if errors:
            print(f"\n❌ 门禁失败 {len(errors)} 项（拒绝推送）:", file=sys.stderr)
            for e in errors[:25]:
                print("   " + e, file=sys.stderr)
            return 2
        print("\n门禁通过：无未脱敏路径/用户名、无密钥形状、体积达标、.py 全部可编译")
        if args.check:
            want = local_tree(dst, args.branch)
            have = remote_tree(remote, args.branch)
            if have is None:
                print(f"\n⚠️ 镜像 {remote} 还没有 {args.branch} 分支 → 需推送")
                return 2
            added = sorted(set(want) - set(have))
            removed = sorted(set(have) - set(want))
            changed = sorted(k for k in set(want) & set(have) if want[k] != have[k])
            if added or removed or changed:
                print(f"\n⚠️ 镜像落后：新增 {len(added)} / 删除 {len(removed)} / 变更 {len(changed)}")
                for k in (added + changed + removed)[:15]:
                    print("   " + k)
                return 2
            print(f"\n✅ 与镜像一致（{len(want)} 文件，{args.branch}）")
            return 0

        subprocess.run(["git", "init", "-q", "-b", args.branch], cwd=dst, check=True)
        subprocess.run(["git", "add", "-A"], cwd=dst, check=True)
        stamp = time.strftime("%Y-%m-%d")
        # 作者/提交时间固定 → 同一份内容在同一日期下 commit sha 恒定（两处镜像可交叉核对）
        env = dict(os.environ, GIT_AUTHOR_DATE=f"{stamp}T00:00:00+08:00",
                   GIT_COMMITTER_DATE=f"{stamp}T00:00:00+08:00")
        msg = mirror_message(dst, stamp, n, total, per_dir)
        subprocess.run(["git", "-c", "user.name=AA翻译镜像", "-c", "user.email=mirror@localhost",
                        "commit", "-q", "-F", "-"],
                       cwd=dst, check=True, env=env, input=msg.encode("utf-8"))
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=dst,
                              capture_output=True, text=True).stdout.strip()
        idx = subprocess.run(["git", "ls-files"], cwd=dst, capture_output=True, text=True).stdout
        n_idx = len([x for x in idx.splitlines() if x.strip()])
        if n_idx != n:
            print(f"⚠️ 构建 {n} 个文件、实进快照 {n_idx} 个（差 {n - n_idx}）——检查 EXCLUDE 规则",
                  file=sys.stderr)
        print(f"本地快照提交: {head}")
        if args.dry_run:
            print("--dry-run：未推送")
            return 0

        r = subprocess.run(["git", "push", "--force", remote, f"{args.branch}:{args.branch}"],
                           cwd=dst, capture_output=True, text=True,
                           env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode:
            print("❌ push 失败", file=sys.stderr)
            return 3
        if os.path.isdir(os.path.join(remote, "refs")):        # 本地裸仓库：顺手把 HEAD 指到该分支
            subprocess.run(["git", "symbolic-ref", "HEAD", f"refs/heads/{args.branch}"], cwd=remote)
            print(f"镜像仓库 HEAD → refs/heads/{args.branch}")
        print(f"\n✅ 已推送 {head} → {remote} ({args.branch})")
        return 0
    finally:
        if not args.keep and not args.check:
            shutil.rmtree(dst, ignore_errors=True)
        elif not args.keep:
            pass
        for d in set(_PROBE.values()):
            if d and not args.keep:
                shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
