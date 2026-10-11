# -*- coding: utf-8 -*-
r"""生成「脱敏代码镜像」并推送到裸仓库（默认 `origin`，即 `../aa-translation`）。

用途：把本仓库的**代码与文档**（不含作品数据、手写数据资产、抓来的网页等）打成
一个**全新历史**的快照，脱敏后 force-push 到镜像仓库。镜像里没有数据，
所以它天然免疫「历史里删不掉的数据对象」与「旧提交里的本机路径」。

**2026-10-10 起默认走「历史重放」**：不再是一条合成的单提交快照，而是把工作库的提交
**逐条重放**成脱敏提交（真实提交说明、真实作者与提交时间、每条提交的树 = 该时点的脱敏内容）。
同一个本地提交 + 同一份脱敏规则 → 两处镜像重放出的 sha 相同（仍可交叉核对）。
`--history 0` 回到旧行为（单提交快照，提交时间固定为当天 00:00，说明由 `mirror_message()` 生成）。

**2026-10-11 起默认增量**：只重放「镜像还没有的提交」，叠在镜像现有 tip 之上（`--full` 强制全量重建）。
原因：全量重建每条提交要「导出 → 脱敏重写整棵快照 → 门禁全量扫 → 提交」，实测 ≈20s/条
（426 文件/56MB × N 条），而多数提交上次已推过、内容没变，重跑纯属浪费。
底座识别 = 状态文件（`temp/mirror_state.json`，不进镜像）→ 否则拿镜像 tip 的
(脱敏说明, 作者/提交时间) 对号 + **树逐文件校验**（不一致就回退全量重建，兼容本地 rebase/amend）。
门禁只对**新提交**跑；push 用普通 fast-forward（非 ff 就报错让重跑，不静默覆盖）。

用法（从仓库根运行）：
    python scripts/tools/push_code_mirror.py --check     # 只构建 + 跑门禁 + 与镜像比 blob（rc=2 落后）
    python scripts/tools/push_code_mirror.py             # 增量：只推镜像还没有的提交（快）
    python scripts/tools/push_code_mirror.py --full      # 全量重建（重放最近 --history 条，force push）
    python scripts/tools/push_code_mirror.py --history 5 # 全量重建时只重放最近 5 条
    python scripts/tools/push_code_mirror.py --remote <url|path> --branch main --keep
        --keep    保留构建目录（排查用）；默认删
        --dry-run 构建 + 门禁 + 本地提交，但不 push

快照内容 = 下面的 INCLUDE 白名单 − EXCLUDE 规则；wiki 目录只取「生成物 + 手改源」（不含 `_raw/`）。
门禁（命中即 rc=2 拒推）：未脱敏路径 / 用户名 / 密钥形状 / 体积上限；`.py` 额外要求进程内编译全过；
重放模式下**每条提交**都要过门禁（增量模式只对本次新增的提交跑），提交说明同样脱敏 + 过残留检查。
历史重放还有一道自检：镜像 tip 的树必须与「最后一条被重放的提交」的脱敏快照逐文件相同，否则拒推。
镜像内容 = **已提交**内容：工作区里未提交的改动不进镜像（只打印一行提示）。
"""
import argparse
import io
import json
import os
import re
import shutil
import stat
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
    r"(^|/)AGENTS\.md$",          # 用户指定（2026-10-10）：AGENTS.md 不进图库/镜像（含 aa_web_app/AGENTS.md）
    r"SemiBold\.ttf$",            # 只用 Regular（aa_font.py）；SemiBold 24MB 不进镜像
    r"(^|/)\.DS_Store$", r"\.db$", r"\.ydb$", r"\.png$", r"\.jpg$", r"\.jpeg$",
)]
MAX_MB = 100.0
HISTORY_DEFAULT = 20          # 历史重放默认条数（最近 N 条本地提交逐条重放成脱敏提交）

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


def _collect(src_root):
    """按白名单列出要进快照的文件 `[(绝对路径, 相对路径)]`。

    `src_root` = 仓库根（工作区快照）或某提交的导出目录（历史重放），两者共用同一套规则。
    """
    copies = []
    for d in INCLUDE_DIRS:
        src = os.path.join(src_root, d)
        if not os.path.isdir(src):
            continue
        for dirpath, dirnames, filenames in os.walk(src):
            dirnames[:] = [x for x in dirnames if x not in EXCLUDE_NAMES]
            for fn in filenames:
                p = os.path.join(dirpath, fn)
                rel = os.path.relpath(p, src_root)
                if not _excluded(rel):
                    copies.append((p, rel))
    for f in INCLUDE_FILES:
        p = os.path.join(src_root, f)
        if os.path.isfile(p):
            copies.append((p, f))
    wiki_root = os.path.join(src_root, WIKI_GLOB_DIR)
    if os.path.isdir(wiki_root):
        for name in sorted(os.listdir(wiki_root)):
            d = os.path.join(wiki_root, name)
            if not (os.path.isdir(d) and name.endswith("wiki")):
                continue
            for dirpath, dirnames, filenames in os.walk(d):
                dirnames[:] = [x for x in dirnames if x != "_raw"]
                for fn in filenames:
                    p = os.path.join(dirpath, fn)
                    rel = os.path.relpath(p, src_root)
                    ext = os.path.splitext(fn)[1].lower()
                    if ext in WIKI_KEEP_EXTRA or fn == "index.html" or _excluded(rel):
                        if not _excluded(rel):
                            copies.append((p, rel))
    return copies


def _rmtree(path):
    """删目录树：① 先清只读位（git 有时把 loose object 写成只读，`shutil.rmtree` 会
    PermissionError 静默留下整个目录）② 再删。

    注意**删不掉的两种情况**（实测）：只读位（本函数解决）；以及文件被外部句柄占住
    （杀软实时扫描/Sysmon 等——属性正常但 `rm -rf` 也报 "Directory not empty"，只能等句柄
    释放或重启）。第二次情况会留下**空壳目录**，不是内容泄漏。
    """
    if not path or not os.path.isdir(path):
        return
    for dp, _dns, fns in os.walk(path):
        for fn in fns:
            p = os.path.join(dp, fn)
            try:
                if not (os.stat(p).st_mode & stat.S_IWRITE):
                    os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)


def _write_snapshot(dst, src_root):
    """把 `src_root` 的白名单内容脱敏写进 dst（+ 生成 README）。返回 (文件数, 逐目录统计)。"""
    copies = _collect(src_root)
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


def build(dst):
    """按白名单拷贝 + 脱敏（工作区快照）；返回 (文件数, 逐目录统计)。"""
    return _write_snapshot(dst, REPO)


# ---------------------------------------------------------------------------
# 历史重放（2026-10-10 用户指定）：镜像不再是「单提交快照」，而是把工作库最近的提交
# **逐条重放**成脱敏提交（真实提交说明 + 真实作者时间/提交时间）。两条铁律：
#   ① 每条提交的树 = 该提交时点的工作区白名单内容（脱敏后），不是 diff；
#   ② 提交说明同样过脱敏 + 残留门禁（说明里可能带本机路径）。
# 同一个提交 + 同一份脱敏规则 → 两处镜像重放出的 sha 相同（仍可交叉核对）。
# ---------------------------------------------------------------------------

def _clear_tree(dst):
    """清空快照目录（保留 .git）——重放到下一条提交前调用。"""
    for name in os.listdir(dst):
        if name == ".git":
            continue
        p = os.path.join(dst, name)
        if os.path.isdir(p):
            _rmtree(p)
        else:
            try:
                os.remove(p)
            except OSError:
                pass


def _export_commit(sha, out_dir):
    """把某条提交里白名单路径的内容导出到 out_dir（该提交里不存在的路径自动跳过）。

    ⚠️ 给 git 的路径一律转正斜杠：`WIKI_GLOB_DIR` 是 `os.path.join` 出来的，在 Windows 上是
    `doc\\aa作品相关` → 当 `-e <sha>:doc\\aa作品相关` 传过去直接 rc=128（整棵 wiki 素材静默丢掉）。
    """
    import tarfile
    paths = []
    for p in list(INCLUDE_DIRS) + list(INCLUDE_FILES) + [WIKI_GLOB_DIR]:
        gp = p.replace(os.sep, "/")
        if subprocess.run(["git", "-C", REPO, "cat-file", "-e", f"{sha}:{gp}"],
                          capture_output=True).returncode == 0:
            paths.append(gp)
    if not paths:
        return 0
    r = subprocess.run(["git", "-C", REPO, "archive", "--format=tar", sha, "--"] + paths,
                       capture_output=True)
    if r.returncode:
        raise SystemExit("git archive %s 失败: %s" % (sha, r.stderr.decode("utf-8", "ignore")[:200]))
    with tarfile.open(fileobj=io.BytesIO(r.stdout), mode="r:") as tf:
        for m in tf.getmembers():
            name = m.name.replace("\\", "/")
            if name.startswith("./"):            # ⚠️ 别用 lstrip("./")：那会把 .codex/ 的点的也切掉
                name = name[2:]
            if not m.isfile() or name.startswith("/") or ".." in name.split("/"):
                continue
            out = os.path.join(out_dir, name)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            f = tf.extractfile(m)
            if f is None:
                continue
            with open(out, "wb") as w:
                shutil.copyfileobj(f, w)
    return len(paths)


def commit_list(n):
    """最近 n 条本地提交（**按拓扑序旧 → 新**）。含 sha/说明/作者/真实时间，供历史重放用。

    用 `--topo-order`（而非默认的提交时间倒序）：拆提交会带来「子提交的时间早于父提交」的
    真实情况（把一次批量的改动按文件 mtime 拆开），按时间排会把父子交错打乱。
    """
    fmt = "%H%x1f%an%x1f%ae%x1f%aI%x1f%cI%x1f%s%x1f%b%x1e"
    r = subprocess.run(["git", "-C", REPO, "log", f"-{int(n)}", "--no-merges", "--topo-order",
                        f"--format={fmt}"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise SystemExit("读本地提交失败: " + (r.stderr or "")[:200])
    out = []
    for rec in r.stdout.split("\x1e"):
        rec = rec.strip("\n")
        if not rec.strip():
            continue
        parts = rec.split("\x1f")
        if len(parts) < 7:
            continue
        out.append({"sha": parts[0].strip(), "author": parts[1].strip() or "AA翻译镜像",
                    "email": parts[2].strip() or "mirror@localhost",
                    "author_date": parts[3].strip(), "commit_date": parts[4].strip() or parts[3].strip(),
                    "subject": parts[5].strip(), "body": parts[6].strip("\n")})
    return list(reversed(out))


def _scrub_message(msg):
    """提交说明过脱敏 + 残留门禁（说明里常出现本机路径，不能直接搬过去）。"""
    txt = scrub_mod.scrub(msg)
    bad = [m.group(0) for pat in scrub_mod.leftover_patterns() for m in pat.finditer(txt)]
    if bad:
        raise SystemExit("提交说明里有未脱敏内容，拒绝推送: " + ", ".join(sorted(set(bad))[:5]))
    return txt


def _included_path(rel):
    """某路径是否会进快照（用于判断「未提交改动会不会影响镜像内容」）。"""
    rel = rel.replace(os.sep, "/")
    if _excluded(rel):
        return False
    if rel in INCLUDE_FILES:
        return True
    if any(rel == d or rel.startswith(d + "/") for d in INCLUDE_DIRS):
        return True
    wroot = WIKI_GLOB_DIR.replace(os.sep, "/") + "/"
    if rel.startswith(wroot):
        parts = rel.split("/")
        if len(parts) >= 3 and parts[2].endswith("wiki"):
            return os.path.splitext(rel)[1].lower() in WIKI_KEEP_EXTRA or rel.endswith("/index.html")
    return False


def dirty_mirrored():
    """工作区里**会进快照**的未提交改动（AGENTS.md 这类不在白名单的改动不算）。"""
    r = subprocess.run(["git", "-C", REPO, "status", "--porcelain", "-z"], capture_output=True)
    txt = r.stdout.decode("utf-8", "replace")
    bad = []
    for ent in [x for x in txt.split("\0") if x.strip()]:
        path = ent[3:].strip()
        if path and _included_path(path):
            bad.append(path)
    return bad


def _replay_one(dst, c, max_mb):
    """重放一条提交：导出该提交的白名单内容 → 脱敏重写整棵快照 → 门禁 → 提交。

    返回 (ok, 短 head 或 门禁错误列表, 构建文件数, 实进索引文件数)。
    """
    ex = tempfile.mkdtemp(prefix="aa_mirror_ex_")
    try:
        _export_commit(c["sha"], ex)
        _clear_tree(dst)
        nfiles, _per = _write_snapshot(dst, ex)
    finally:
        _rmtree(ex)
    errors, _warns, _total = gates(dst, max_mb)
    if errors:
        return False, errors, nfiles, 0
    subprocess.run(["git", "add", "-A"], cwd=dst, check=True)
    msg = _scrub_message(((c["subject"] + "\n\n" + c["body"]).strip() + "\n"))
    env = dict(os.environ, GIT_AUTHOR_DATE=c["author_date"], GIT_COMMITTER_DATE=c["commit_date"])
    subprocess.run(["git", "-c", f"user.name={c['author']}", "-c", f"user.email={c['email']}",
                    "commit", "-q", "--allow-empty", "-F", "-"], cwd=dst, check=True, env=env,
                   input=msg.encode("utf-8"))
    idx = subprocess.run(["git", "ls-files"], cwd=dst, capture_output=True, text=True).stdout
    n_idx = len([x for x in idx.splitlines() if x.strip()])
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=dst,
                          capture_output=True, text=True).stdout.strip()
    return True, head, nfiles, n_idx


def _tip_check(dst, last_sha):
    """一致性自检：镜像 tip 的树必须 == 该提交的脱敏快照（防导出/顺序出错）。

    返回 (不一致清单, 期望树)。
    """
    ex2 = tempfile.mkdtemp(prefix="aa_mirror_chk_src_")
    chk = tempfile.mkdtemp(prefix="aa_mirror_chk_")
    try:
        _export_commit(last_sha, ex2)
        _write_snapshot(chk, ex2)
        want = local_tree(chk, "aa-mirror-chk")
    finally:
        _rmtree(ex2)
        _rmtree(chk)
    have = commit_tree(dst, "HEAD")
    diff = sorted(set(want) ^ set(have)) + [k for k in sorted(set(want) & set(have)) if want[k] != have[k]]
    return diff, want


def push_history(dst, remote, branch, n, max_mb, dry_run=False):
    """把最近 n 条本地提交逐条重放成脱敏提交并 force-push。返回 (ok, 说明)。"""
    commits = commit_list(n)
    if not commits:
        raise SystemExit("没有可重放的提交（检查 --history）")
    print(f"历史重放：最近 {len(commits)} 条提交（真实提交说明 + 真实时间）")
    subprocess.run(["git", "init", "-q", "-b", branch], cwd=dst, check=True)
    for c in commits:
        ok, res, nfiles, n_idx = _replay_one(dst, c, max_mb)
        if not ok:
            print(f"\n❌ 门禁失败（提交 {c['sha'][:7]} {c['subject'][:40]}）:", file=sys.stderr)
            for e in res[:10]:
                print("   " + e, file=sys.stderr)
            return False, c["sha"]
        warn = "" if n_idx == nfiles else f"  ⚠️ 构建 {nfiles} / 进快照 {n_idx}"
        print(f"   {res}  {c['commit_date'][:19]}  {c['subject'][:52]}{warn}")
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=dst,
                          capture_output=True, text=True).stdout.strip()
    full = subprocess.run(["git", "rev-parse", "HEAD"], cwd=dst,
                          capture_output=True, text=True).stdout.strip()
    # 一致性自检：镜像 tip 的树必须 == 「最后一条被重放的提交」的脱敏快照（防导出/顺序出错）
    diff, want = _tip_check(dst, commits[-1]["sha"])
    if diff:
        print("❌ 镜像 tip 与「最后一条重放提交」的快照不一致（%d 处），拒绝推送:" % len(diff),
              file=sys.stderr)
        for k in diff[:10]:
            print("   " + k, file=sys.stderr)
        return False, head
    print(f"✅ 一致性自检：tip 与 {commits[-1]['sha'][:7]} 的脱敏快照逐文件相同（{len(want)} 文件）")
    if dry_run:
        print(f"\n--dry-run：未推送（本地已重放 {len(commits)} 条，HEAD={head}）")
        return True, head
    r = subprocess.run(["git", "push", "--force", remote, f"{branch}:{branch}"], cwd=dst,
                       capture_output=True, text=True, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode:
        print("❌ push 失败", file=sys.stderr)
        return False, head
    if os.path.isdir(os.path.join(remote, "refs")):        # 本地裸仓库：顺手把 HEAD 指到该分支
        subprocess.run(["git", "symbolic-ref", "HEAD", f"refs/heads/{branch}"], cwd=remote)
        print(f"镜像仓库 HEAD → refs/heads/{branch}")
    print(f"\n✅ 已推送 {len(commits)} 条提交（HEAD={head}）→ {remote} ({branch})")
    _save_state(remote, branch, commits[-1]["sha"], full)
    return True, head


# ---------------------------------------------------------------------------
# 增量推送（2026-10-11 用户指定）：只重放「镜像还没有的提交」，叠在镜像现有历史之上。
#
# 为什么要：全量重建时**每条提交**都要「导出 → 脱敏重写整棵快照（426 文件/56MB）→ 门禁全量扫
# （读所有文本 + 编译所有 .py）→ 提交」，实测 ≈20s/条，默认 20 条 ⇒ 约 7 分钟（超过前台上限）；
# 而其中大多是上次已推过、内容没变的历史，重跑纯属浪费。
#
# 底座怎么认：镜像里的提交保留**原提交说明与作者/提交时间**（脱敏后），所以拿远端 tip 的
# (脱敏说明, 提交时间) 去本地历史对号，再**用树逐文件比对**确认（远端树 vs 该提交的脱敏快照）；
# 对不上就回退全量重建（本地 rebase/amend 过 → 自愈）。命中后记 `temp/mirror_state.json`
# （不进镜像），下次直接命中，0 秒认底座。
# 门禁只对**新提交**跑（旧提交当初推送时已过门禁）；push 走普通 fast-forward，**不用 --force**
# （镜像被别处改过就报错让重跑，不静默覆盖；真要重建用 --full）。
# ---------------------------------------------------------------------------

def _state_path():
    return os.path.join(REPO, "temp", "mirror_state.json")


def _load_state():
    try:
        with io.open(_state_path(), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_state(remote, branch, local_sha, mirror_sha):
    """记「本地某提交 ↔ 镜像某提交」的对应（增量对号用；temp/ 不进镜像）。"""
    try:
        p = _state_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        st = _load_state()
        st["%s#%s" % (remote, branch)] = {"local": local_sha, "mirror": mirror_sha,
                                          "ts": int(time.time())}
        tmp = p + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
    except Exception as e:                     # 状态文件只是加速器，写失败不影响推送
        print(f"ℹ️ 记增量状态失败（不影响推送）: {e}", file=sys.stderr)


def _remote_tip(remote, branch):
    """镜像分支当前 tip 的完整 sha（分支不存在/连不上 → None）。"""
    r = subprocess.run(["git", "ls-remote", remote, "refs/heads/%s" % branch],
                       capture_output=True, text=True,
                       env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    if r.returncode or not r.stdout.strip():
        return None
    return r.stdout.split()[0].strip()


def _remote_meta(remote, branch):
    """镜像 tip 的 {sha, message, author_date, commit_date}（URL 远端走临时裸克隆）。"""
    target = _probe(remote) if _is_url(remote) else remote
    if not target:
        return None
    fmt = "%H%x1f%aI%x1f%cI%x1f%B%x1e"
    r = subprocess.run(["git", "-C", target, "log", "-1", "--no-merges", "--format=" + fmt,
                        "refs/heads/%s" % branch], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode or not r.stdout.strip():
        return None
    parts = r.stdout.split("\x1e")[0].split("\x1f")
    if len(parts) < 4:
        return None
    return {"sha": parts[0].strip(), "author_date": parts[1].strip(),
            "commit_date": parts[2].strip(), "message": parts[3].strip()}


def _scrub_commit_msg(c):
    """本地提交在镜像里的说明（与 _replay_one 写进去的同一口径）。"""
    try:
        return _scrub_message(((c["subject"] + "\n\n" + c["body"]).strip() + "\n")).strip()
    except SystemExit:
        return None


def find_base(remote, branch, mirror_tip, scan=300):
    """找「镜像 tip 对应的本地提交」。返回 (base_sha|None, 说明)。

    顺序：状态文件（快） → (说明+提交时间) 对号 + **树逐文件校验**（准，约一条提交的成本）。
    """
    st = _load_state().get("%s#%s" % (remote, branch)) or {}
    if st.get("mirror") == mirror_tip and st.get("local"):
        if subprocess.run(["git", "-C", REPO, "cat-file", "-e", str(st["local"]) + "^{commit}"],
                          capture_output=True).returncode == 0:
            return st["local"], "状态文件命中"
    meta = _remote_meta(remote, branch)
    if not meta:
        return None, "读不到镜像 tip 的元数据"
    for c in reversed(commit_list(scan)):        # 新 → 旧
        if c["commit_date"] != meta["commit_date"] or _scrub_commit_msg(c) != meta["message"]:
            continue
        if not meta["author_date"] == c["author_date"]:
            continue
        ex = tempfile.mkdtemp(prefix="aa_mirror_base_")
        chk = tempfile.mkdtemp(prefix="aa_mirror_basechk_")
        try:
            _export_commit(c["sha"], ex)
            _write_snapshot(chk, ex)
            if local_tree(chk, "aa-mirror-basechk") == remote_tree(remote, branch):
                return c["sha"], "说明+时间+树校验命中"
        finally:
            _rmtree(ex)
            _rmtree(chk)
        return None, "说明+时间对得上但树比对不一致（本地历史被改写过？）"
    return None, "本地最近 %d 条历史里找不到镜像 tip 对应的提交" % scan


def new_commits_after(base):
    """base 之后（含）要重放的提交，按拓扑序旧 → 新。"""
    r = subprocess.run(["git", "-C", REPO, "rev-list", "--reverse", "--no-merges",
                        "%s..HEAD" % base], capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        return []
    shas = [x.strip() for x in r.stdout.splitlines() if x.strip()]
    if not shas:
        return []
    meta = {c["sha"]: c for c in commit_list(len(shas) + 5)}
    return [meta[s] for s in shas if s in meta]


def push_incremental(dst, remote, branch, max_mb, history, dry_run=False):
    """增量：只重放镜像还没有的提交（叠在镜像现有 tip 上）。返回 (ok, 说明)。"""
    mirror_tip = _remote_tip(remote, branch)
    if not mirror_tip:
        print("增量同步：镜像还没有 %s 分支 → 首次推送（全量重建）" % branch)
        return push_history(dst, remote, branch, history, max_mb, dry_run=dry_run)
    base, how = find_base(remote, branch, mirror_tip)
    if not base:
        print(f"增量同步：没找到底座（{how}）→ 回退全量重建（force push）")
        return push_history(dst, remote, branch, history, max_mb, dry_run=dry_run)
    print(f"增量同步：底座 = 本地 {base[:7]} ↔ 镜像 {mirror_tip[:7]}（{how}）")
    todo = new_commits_after(base)
    if not todo:
        print(f"✅ 已是最新：镜像 tip {mirror_tip[:7]} 就是这个提交，无需推送")
        return True, mirror_tip
    print(f"要重放 {len(todo)} 条新提交（旧的 {history - len(todo) if history > len(todo) else 0} 条"
          f"已推过，不再重跑）")
    # 取镜像历史到 dst：新提交要叠在它上面（父提交 = 镜像 tip）
    subprocess.run(["git", "init", "-q", "-b", branch], cwd=dst, check=True)
    f = subprocess.run(["git", "fetch", "-q", "--no-tags", remote,
                        "refs/heads/%s" % branch], cwd=dst, capture_output=True, text=True,
                       env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    if f.returncode:
        print("❌ 取镜像历史失败: " + (f.stderr or "")[:200], file=sys.stderr)
        return False, ""
    subprocess.run(["git", "reset", "-q", "--hard", "FETCH_HEAD"], cwd=dst, check=True)
    for c in todo:
        ok, res, nfiles, n_idx = _replay_one(dst, c, max_mb)
        if not ok:
            print(f"\n❌ 门禁失败（提交 {c['sha'][:7]} {c['subject'][:40]}）:", file=sys.stderr)
            for e in res[:10]:
                print("   " + e, file=sys.stderr)
            return False, c["sha"]
        warn = "" if n_idx == nfiles else f"  ⚠️ 构建 {nfiles} / 进快照 {n_idx}"
        print(f"   {res}  {c['commit_date'][:19]}  {c['subject'][:52]}{warn}")
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=dst,
                          capture_output=True, text=True).stdout.strip()
    full = subprocess.run(["git", "rev-parse", "HEAD"], cwd=dst,
                          capture_output=True, text=True).stdout.strip()
    diff, want = _tip_check(dst, todo[-1]["sha"])
    if diff:
        print("❌ 镜像 tip 与「最后一条重放提交」的快照不一致（%d 处），拒绝推送:" % len(diff),
              file=sys.stderr)
        for k in diff[:10]:
            print("   " + k, file=sys.stderr)
        return False, head
    print(f"✅ 一致性自检：tip 与 {todo[-1]['sha'][:7]} 的脱敏快照逐文件相同（{len(want)} 文件）")
    if dry_run:
        print(f"\n--dry-run：未推送（本地已重放 {len(todo)} 条，HEAD={head}）")
        return True, head
    r = subprocess.run(["git", "push", remote, "%s:%s" % (branch, branch)], cwd=dst,
                       capture_output=True, text=True, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode:
        print("❌ push 失败（非 fast-forward？镜像被别处改过——重跑一次，或用 --full 重建）",
              file=sys.stderr)
        return False, head
    if os.path.isdir(os.path.join(remote, "refs")):        # 本地裸仓库：顺手把 HEAD 指到该分支
        subprocess.run(["git", "symbolic-ref", "HEAD", f"refs/heads/{branch}"], cwd=remote)
        print(f"镜像仓库 HEAD → refs/heads/{branch}")
    print(f"\n✅ 已增量推送 {len(todo)} 条提交（HEAD={head}）→ {remote} ({branch})")
    _save_state(remote, branch, todo[-1]["sha"], full)
    return True, head


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


def commit_tree(dst, rev="HEAD"):
    """取某提交的 {路径: blob sha}（镜像一致性自检用）。"""
    r = subprocess.run(["git", "-C", dst, "ls-tree", "-r", "--format=%(objectname) %(path)", rev],
                       capture_output=True, text=True)
    out = {}
    for line in r.stdout.splitlines():
        if " " in line:
            sha, path = line.split(" ", 1)
            out[path.strip()] = sha
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
    ap.add_argument("--keep", action="store_true", help="保留构建目录（排查用）")
    ap.add_argument("--history", type=int, default=HISTORY_DEFAULT,
                    help=f"全量重建时重放最近 N 条本地提交（默认 {HISTORY_DEFAULT}）；"
                         f"0 = 旧行为（单提交快照，时间固定为当天 00:00）")
    ap.add_argument("--full", action="store_true",
                    help="强制全量重建历史（默认**增量**：只重放镜像还没有的提交，叠在镜像现有 tip 上）")
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
        if args.history:
            # 镜像内容 = **已提交**内容（历史重放），因此工作区里未提交的改动不进镜像；
            # 只提示一句，不阻塞（并行会话改一半的脚本不该拦住同步）。
            dirty = dirty_mirrored()
            if dirty:
                print("ℹ️ 工作区有未提交的「会进快照」的改动（不进本次镜像）："
                      + ", ".join(dirty[:6]) + ("…" if len(dirty) > 6 else ""))
            if args.full:
                ok, _head = push_history(dst, remote, args.branch, args.history, args.max_mb,
                                         dry_run=args.dry_run)
            else:
                # 2026-10-11 起默认增量：只重放镜像还没有的提交（见 push_incremental 注释）
                ok, _head = push_incremental(dst, remote, args.branch, args.max_mb, args.history,
                                             dry_run=args.dry_run)
            return 0 if ok else 3
        # ---- 旧路径（--history 0）：单提交快照，时间固定为当天 00:00 ----
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
            _rmtree(dst)
        elif not args.keep:
            pass
        for d in set(_PROBE.values()):
            if d and not args.keep:
                _rmtree(d)


if __name__ == "__main__":
    raise SystemExit(main())
