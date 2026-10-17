# -*- coding: utf-8 -*-
r"""打包「开箱即用」发行包（方案 doc/方案归档/发行包打包方案_2026-10-09.md）。

一条命令产出 `导出/AA翻译_发行包_<YYYYMMDD>/` 与同名 `.zip`：

    python scripts/tools/build_release.py [--out DIR] [--no-exe] [--keep-stage]
                                          [--no-verify] [--smoke-api]

包内容（顶层白名单见 `TOP_ENTRIES`）：干净源码 + 冻结工作台 exe + 示例作品 3 话 +
宝可梦 wiki 翻译素材 + 独立配置（README / LICENSE / requirements.txt / .env.example / 两个 bat；
字体随包带上 `fonts/OFL.txt`）。

设计要点
--------
* **脱敏、痕迹复查、密钥复查** 全部走 `release_common.py`（与交付页编辑服务 exe 同一套规则）；
* **作品痕迹清空**：`registry.py` 只留示例作品、全局术语/角色表换空骨架、`AGENTS.md` 不进包；
* **默认链改写**：TOML 其余内容（含实测注释与单价表）原样保留，只改 `[default]` 与两条链，
  让使用者填一个 deepseek key 就能跑通；
* **两级自检**：硬门禁（残留 / 密钥 / 白名单 / 语法）+ 解包冒烟（单测基线对齐 + 零 token 链路）；
* **代码根 / 数据根分离**：`AA_BASE` 未设置时行为与仓库内完全一致（见方案 §5）。

注意：本文件的字符串里出现的反斜杠路径一律用 `BS` 常量拼接 —— bat 文本必须逐字节正确
（`\a`/`\t` 被当成转义控制字符曾写坏启动器，fact91）。
"""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_SCRIPTS)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import release_common as rc  # noqa: E402

BS = chr(92)                     # 反斜杠（拼 bat 用；本文件里不写裸的）
DATE = datetime.date.today().strftime("%Y%m%d")
NAME = "AA翻译_发行包_" + DATE
EXE_NAME = "AA工作台.exe"
EXE_SRC = os.path.join(_HERE, "dist", EXE_NAME)
# 发行包标记文件（2026-10-09）：包内测试据此跳过"依赖生成者完整配置/数据"的用例
# —— 见 scripts/tests/_work_guard.py 的 repo_only()。没有它，包内会红 27 例。
RELEASE_MARKER = ".release_package"
MARKER_TEXT = """这是 AA 翻译「发行包」标记文件（由 scripts/tools/build_release.py 写入）。

包里的配置与数据是**为发行裁过的**：默认模型链只留公共可取 key 的端点、registry 只带
一部示例作品、全局术语/角色表为发行版内容。因此 `scripts/tests/` 里那些断言"生成者完整
模型链 / 全部作品 / 完整术语表"的用例会在本包内自动跳过（装饰器 repo_only），
其余用例照常运行。删除本文件即把整套测试按"完整仓库"模式运行。
"""

# 示例作品（打包时只带前 N 话原文；registry / work_meta / 术语表都只留它）
EXAMPLE = {"id": "baitou", "dir": "白头与染灰魔女", "n_chapters": 3}
# 宝可梦 wiki 翻译素材（方案 Q3-A：只带翻译必需的 JSON）
WIKI_REL = os.path.join("doc", "aa作品相关")
GENERIC_WIKI = "ポケモンAA术语wiki"
WORK_WIKIS = ("この素晴らしいシンオウに祝福をwiki", "ポケの子wiki")
WIKI_FILES_GENERIC = ("术语表.json", "官方译名对照.json", "补充术语.json")
WIKI_FILES_WORK = ("术语表.json", "官方译名对照.json", "人工修正.json")

# 包根下允许出现的东西（多一个就拒绝出包 —— 防"顺手带进去"的目录）
TOP_ENTRIES = {"scripts", "aa_web_app", "aaManga", "doc", "fonts",
               "README.md", "工作台使用说明.md", "LICENSE", "requirements.txt", ".env.example",
               "安装依赖.bat", "启动工作台.bat", "glossary.json", "roles.json",
               RELEASE_MARKER, EXE_NAME}

# 图文操作手册（2026-10-10）：源文件在仓库 doc/ 下，打包时复制到**包根**
# （使用者解包先看到的就是它，和 README.md 并排）；配图留在包内 doc/使用说明图/，
# 因此复制时把 md 里的相对链接 "使用说明图/xxx.png" 改写成 "doc/使用说明图/xxx.png"。
MANUAL_SRC_REL = os.path.join("doc", "工作台使用说明.md")
MANUAL_DST = "工作台使用说明.md"
SHOTS_DIR = "使用说明图"

SCRIPTS_SKIP_DIRS = {"legacy", "logs", "benchmark", "dist", "build", "__pycache__"}
SCRIPTS_SKIP_GLOBS = ("*.pyc", "*.log", "*.bak", "*.bak_*", "*_副本*", "*- 副本*", "*.tmp")
WEBAPP_SKIP_DIRS = {"__pycache__", "data"}          # data/ 是运行时数据，另生成空骨架
WEBAPP_SKIP_GLOBS = ("*.pyc", "*.log", "*.log.[0-9]", "AGENTS.md", "*.bak*")

# 凭据入口（README / .env.example 里列出，供使用者照着申请）
KEY_HELP = [
    ("DEEPSEEK_API_KEY", "deepseek 官网（默认链首）", "https://platform.deepseek.com/"),
    ("SILICONFLOW_API_KEY", "硅基流动（备用端点 / 视觉模型）", "https://cloud.siliconflow.cn/"),
    ("DEEPINFRA_API_KEY", "DeepInfra（最便宜，需国际支付 + 代理）", "https://deepinfra.com/"),
    ("XIAOMI_API_KEY", "小米 token 套餐（mimo；00:00-08:00 折扣）", "https://platform.xiaomimimo.com/"),
    ("COMMANDCODE_API_KEY", "commandcode 网关（兜底端点）", "https://commandcode.ai/"),
    ("QWEN_API_KEY", "阿里 dashscope（R18 不可用）", "https://bailian.console.aliyun.com/"),
    ("MINIMAX_CN_API_KEY", "MiniMax（R18 不支持）", "https://platform.minimaxi.com/"),
    ("RUNINFRA_API_KEY", "runinfra（网关，需代理）", "-"),
    ("OPENROUTER_API_KEY", "OpenRouter", "https://openrouter.ai/"),
]

# 现有第三方依赖（工作台 + 朗读）；版本在打包时从跑本脚本的解释器（应为 .venv）里取
REQUIRED_PKGS = ["fastapi", "uvicorn", "jinja2", "pydantic", "starlette",
                 "anyio", "h11", "edge-tts", "zhconv"]


# ============================================================ 复制 / 生成

def _log(msg):
    print(msg, flush=True)


def _copy_tree(src, dst, skip_dirs=(), skip_globs=()):
    return rc.copy_filtered(src, dst, ignore_dirs=set(skip_dirs), ignore_globs=tuple(skip_globs))


def _read(p):
    with open(p, "rb") as f:
        return f.read().decode("utf-8")


def _write(p, text, encoding="utf-8", newline=None):
    """写文本（原子性不重要，staging 每次重建）。newline 指定行尾（bat 要 CRLF）。"""
    os.makedirs(os.path.dirname(p), exist_ok=True)
    data = text if newline is None else text.replace("\n", newline)
    with open(p, "wb") as f:
        f.write(data.encode(encoding))


def _read_lines(p):
    """读成行列表 + 该文件的主导换行 → `(lines, eol)`，行的尾随 CR 已剥掉。

    为什么不用 `_read(p).split("\n")`：工作区里的文件可能**整份是 CRLF**（实测
    `scripts/works/registry.py` 就是，由别的工具写出；`core.autocrlf=false` 也救不了），
    此时 `lines[i] == "}"` 这类精确匹配会静默失配 → 变换报"找不到块"。
    读时归一、写回时按原风格还原，两边都不破坏。
    """
    raw = open(p, "rb").read().decode("utf-8")
    n_crlf = raw.count("\r\n")
    eol = "\r\n" if n_crlf and n_crlf * 2 >= raw.count("\n") else "\n"
    return [l[:-1] if l.endswith("\r") else l for l in raw.split("\n")], eol


def _write_lines(p, lines, eol="\n"):
    """按给定换行写回（与 `_read_lines` 配对）。"""
    open(p, "wb").write(eol.join(lines).encode("utf-8"))


def stage_scripts(stage):
    """拷贝 scripts/ 树（剔除 legacy / logs / benchmark / 构建产物 / 备份）。"""
    dst = os.path.join(stage, "scripts")
    n = _copy_tree(os.path.join(_ROOT, "scripts"), dst,
                   skip_dirs=SCRIPTS_SKIP_DIRS, skip_globs=SCRIPTS_SKIP_GLOBS)
    _log("  scripts/          %d 个文件" % n)


def stage_webapp(stage):
    """拷贝 aa_web_app/（代码 + templates + static；data/ 与日志另处理）。"""
    dst = os.path.join(stage, "aa_web_app")
    n = _copy_tree(os.path.join(_ROOT, "aa_web_app"), dst,
                   skip_dirs=WEBAPP_SKIP_DIRS, skip_globs=WEBAPP_SKIP_GLOBS)
    _log("  aa_web_app/       %d 个文件" % n)


def stage_assets(stage):
    """字体（含 OFL 许可证文本）+ CLI/env 接口文档 + 宝可梦 wiki 翻译素材 + 代码许可。"""
    font = os.path.join(_ROOT, "fonts", "SarasaMonoSC-Regular.ttf")
    if os.path.isfile(font):
        dst = os.path.join(stage, "fonts")
        os.makedirs(dst, exist_ok=True)
        shutil.copy2(font, os.path.join(dst, "SarasaMonoSC-Regular.ttf"))
        # OFL 1.1 要求分发字体时随附许可证文本（少了这份就是不合法分发）
        ofl = os.path.join(_ROOT, "fonts", "OFL.txt")
        if os.path.isfile(ofl):
            shutil.copy2(ofl, os.path.join(dst, "OFL.txt"))
        else:
            _log("  ⚠️ fonts/OFL.txt 缺失 —— 随包分发字体必须带 OFL 许可证文本")
        # AA 字体：2026-10-11 起「手机端列位不错位」全靠它（与 MS PGothic 度量一致，见根
        # AGENTS.md「交付页内嵌 AA 等宽字体」）——包里缺它就渲染出源站手机上那种漂移 ~127 格。
        for fn in ("aahub.ttf", "aahub_LICENSE.txt"):
            src = os.path.join(_ROOT, "fonts", fn)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(dst, fn))
            else:
                _log("  ⚠️ fonts/%s 缺失 —— 包里交付页在手机端会「错位」" % fn)
    # 代码许可是 MIT：分发副本要带 LICENSE
    lic = os.path.join(_ROOT, "LICENSE")
    if os.path.isfile(lic):
        _write(os.path.join(stage, "LICENSE"), _read(lic))
    doc_src = os.path.join(_ROOT, "doc", "接口文档.md")
    if os.path.isfile(doc_src):
        _write(os.path.join(stage, "doc", "接口文档.md"), _read(doc_src))
    n_wiki = 0
    for d, files in [(GENERIC_WIKI, WIKI_FILES_GENERIC)] + [(w, WIKI_FILES_WORK) for w in WORK_WIKIS]:
        for fn in files:
            src = os.path.join(_ROOT, WIKI_REL, d, fn)
            if os.path.isfile(src):
                dst = os.path.join(stage, WIKI_REL, d, fn)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                n_wiki += 1
    _log("  fonts/ doc/ wiki   %d 个 wiki JSON" % n_wiki)


def stage_example(stage):
    """示例作品：前 N 话原文 + 章节相关空目录 + 示例作品术语表（翻译前必须非空）。"""
    wid, wdir, n = EXAMPLE["id"], EXAMPLE["dir"], EXAMPLE["n_chapters"]
    src_aa = os.path.join(_ROOT, "aaManga", wdir, "aa")
    out_aa = os.path.join(stage, "aaManga", wdir, "aa")
    os.makedirs(out_aa, exist_ok=True)
    names = sorted(f for f in os.listdir(src_aa)
                   if (f.endswith(".txt") or f.endswith(".txt.gz"))
                   and "_汉化" not in f and "_台词" not in f)[:n]
    for fn in names:
        shutil.copy2(os.path.join(src_aa, fn), os.path.join(out_aa, fn))
        _b = fn[:-3] if fn.endswith(".gz") else fn
        cm = os.path.join(src_aa, (_b[:-4] if _b.endswith(".txt") else _b) + ".colormap.json")
        if os.path.isfile(cm):
            shutil.copy2(cm, os.path.join(out_aa, os.path.basename(cm)))
    for sub in ("汉化", "work", "raw"):
        os.makedirs(os.path.join(stage, "aaManga", wdir, sub), exist_ok=True)
    # 作品术语表：translate_runner 见空表会中止（用户规则），示例作品带一条通用规则起步
    gl = {"schema": "AA作品术语表 v1", "更新日期": datetime.date.today().isoformat(),
          "glossary": [{"jp": "re:([一-龠]{1,4})さん", "zh": BS + "1桑",
                        "cat": "正则", "来源": "示例"}]}
    _write(os.path.join(stage, "aaManga", wdir, "work", "glossary_%s.json" % wid),
           json.dumps(gl, ensure_ascii=False, indent=1))
    _log("  示例作品            %d 话（%s）" % (len(names), " / ".join(f[:12] for f in names)))


# ------------------------------------------------------------ 变换

def transform_registry(stage):
    """registry.py：WORKS 只留示例作品（路径用 os.path.join，避免反斜杠转义坑）。"""
    p = os.path.join(stage, "scripts", "works", "registry.py")
    lines, eol = _read_lines(p)
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith("WORKS = {"))
        end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    except StopIteration:
        raise SystemExit("[registry] 找不到 WORKS 块（结构变了，同步改本脚本）")
    wid, wdir = EXAMPLE["id"], EXAMPLE["dir"]
    base = 'BASE'
    join = lambda *parts: "os.path.join(%s, %s)" % (
        base, ", ".join('"%s"' % x for x in parts))
    block = [
        "WORKS = {",
        "    # ⚠ 发行包（%s）：只带一个示例作品（前 %d 话原文，用于跑通全流程）。"
        % (DATE, EXAMPLE["n_chapters"]),
        "    # 新增自己的作品：工作台首页「＋ 添加作品」，或按本文件头部 4 步流程手动注册。",
        '    "%s": dict(' % wid,
        '        r401_cid="12876",',
        '        site="yaruonichijou",',
        '        title="白头与染灰魔女",',
        "        aa_dir=%s," % join("aaManga", wdir, "aa"),
        "        out_dir=%s," % join("aaManga", wdir, "汉化"),
        "        work_dir=%s," % join("aaManga", wdir, "work"),
        "        raw_dir=%s," % join("aaManga", wdir, "raw"),
        '        name_style="eid_title",',
        "        r18=False,               # 非 R18(普通向)",
        '        url_pattern="https://yaruonichijou.com/blog-entry-{eid}.html",',
        "        min_size=100000,          # dl 已存在且大于该字节数则跳过",
        "        html_out=%s," % join("aaManga", "交付HTML", wdir + "_汉化合集"),
        "    ),",
        "}",
    ]
    _write_lines(p, lines[:start] + block + lines[end + 1:], eol)
    _log("  变换 registry.py  WORKS → 1 个示例作品（原 %d 行）" % (end - start - 1))


def transform_toml(stage):
    """model_config.toml：只改 [default] 四个键 + 两条链，其余（含实测注释/单价表）原样保留。"""
    p = os.path.join(stage, "scripts", "config", "model_config.toml")
    _ln, _eol = _read_lines(p)
    text = "\n".join(_ln)          # 归一成 LF 做文本处理，写回时还原原风格
    # ① [default] 段内四个键（保留行尾注释）
    dflt = {"backend": '"deepseek"', "model": '"deepseek-flash"',
            "gateway": '"https://api.deepseek.com/responses"',
            "key_env": '"DEEPSEEK_API_KEY"'}
    out, section = [], ""
    for ln in text.split("\n"):
        m = re.match(r"^\[([^\]]+)\]", ln)
        if m:
            section = m.group(1)
        if section == "default":
            m2 = re.match(r"^([A-Za-z_]+)\s*=\s*(.*?)(\s*#.*)?$", ln)
            if m2 and m2.group(1) in dflt:
                ln = "%s = %s%s" % (m2.group(1), dflt[m2.group(1)], m2.group(3) or "")
        out.append(ln)
    text = "\n".join(out)
    # ② 两条链：整体替换成公共可取 key 的端点
    chain_note = [
        "# ⚠ 发行包（%s）默认链：只放**公共就能注册拿到 key**的端点，填一个 DEEPSEEK_API_KEY 即可用。"
        % DATE,
        "# 其余端点（deepinfra 最便宜 / 小米 token 套餐 / commandcode 兜底）定义都在下面，",
        "# 拿到 key 后把条目加回本列表即可，或在工作台「设置 → 模型」里勾选。",
    ]
    normal = chain_note + [
        "normal_backends = [",
        '  {backend="deepseek", model="deepseek-flash"},        '
        "# 链首：deepseek 官网（无需代理；cache=true → 章内 pin 目标）",
        '  {backend="siliconflow", model="THUDM/GLM-4-9B-0414"},'
        "  # 备用：硅基流动（国内直连、有免费额度）；此模型偏弱，建议按 [backend.siliconflow] 注释自换",
        "]",
    ]
    r18 = chain_note + [
        "r18_backends = [",
        '  {backend="deepseek", model="deepseek-flash"},        '
        "# 链首：官网放行普通 R18（长露骨章仍可能被审核拒 → 建议加 deepinfra）",
        '  {backend="siliconflow", model="THUDM/GLM-4-9B-0414"},'
        "  # 备用：硅基流动（无审查实测可用）",
        "]",
    ]
    for key, block in (("normal_backends", normal), ("r18_backends", r18)):
        lines = text.split("\n")
        try:
            s = next(i for i, l in enumerate(lines) if re.match(r"^%s = \[" % key, l))
            e = next(i for i in range(s + 1, len(lines)) if lines[i].strip() == "]")
        except StopIteration:
            raise SystemExit("[toml] 找不到 %s 列表（结构变了，同步改本脚本）" % key)
        # 顺手删掉紧贴其上的旧注释块？不删 —— 保留生成者的实测依据（方案 Q7）
        text = "\n".join(lines[:s] + block + lines[e + 1:])
    _write_lines(p, text.split("\n"), _eol)
    _log("  变换 model_config.toml  [default] + 两条付费链 → deepseek / siliconflow")


def transform_aa_env(stage):
    """aa_env.py：生成者本机的 hermes 兜底路径对使用者无意义 → 置空（保行为可预期）。"""
    p = os.path.join(stage, "scripts", "common", "aa_env.py")
    _ln, _eol = _read_lines(p)
    text = "\n".join(_ln)
    m = re.search(r'(def fallback_paths\(\):\n(?:    """.*?"""\n)?)(.*?)\n\n', text, re.S)
    if not m:
        raise SystemExit("[aa_env] 找不到 fallback_paths（结构变了，同步改本脚本）")
    body = ('    # 发行包：生成者本机的兜底路径已移除（对使用者不存在，留着只会误导）。\n'
            '    return ()')
    _write_lines(p, (text[:m.start(2)] + body + text[m.end(2):]).split("\n"), _eol)
    _log("  变换 aa_env.py        fallback_paths() → ()")


def transform_image_manga(stage):
    r"""main.py：清空 `IMAGE_MANGA` 图片漫画注册表。

    表里那两条是**生成者本机的图片目录**（`aaManga\交付HTML\東方新異譚\comic_seamless`、
    `manhuagui_\comic_seamless`），使用者机器上不存在——留着只会让 `/manga` 表格显示
    两行死路径（而且脱敏后还会变成 `<repo>\...` 的怪路径）。清空 = 表格无行，页面照常。
    """
    p = os.path.join(stage, "aa_web_app", "main.py")
    lines, eol = _read_lines(p)
    try:
        s = next(i for i, l in enumerate(lines) if l.startswith("IMAGE_MANGA = {"))
        e = next(i for i in range(s + 1, len(lines)) if lines[i] == "}")
    except StopIteration:
        raise SystemExit("[main.py] 找不到 IMAGE_MANGA 块（结构变了，同步改本脚本）")
    block = [
        "IMAGE_MANGA = {}",
        "# 发行包：图片漫画注册表已清空（原条目是生成者本机的图片目录，使用者没有；",
        "# 留着只会让 /manga 表格显示死路径）。要挂自己的图片漫画：",
        '#   IMAGE_MANGA = {"名称": r"<章节根目录>"}   # 子目录=章节，目录内图片按文件名排序',
    ]
    _write_lines(p, lines[:s] + block + lines[e + 1:], eol)
    _log("  变换 main.py          IMAGE_MANGA 图片漫画注册表 → 空（原 %d 条）" % (e - s - 1))


def write_skeletons(stage):
    """全局术语/角色表换空骨架；工作台 data/ 只留示例作品的 meta 与空 tasks/。"""
    today = datetime.date.today().isoformat()
    # glossary.json = **真表**（2026-10-09 用户指定：术语表 + 角色块 + 残留白名单 known_names 一起随包）：
    # 这三样是通用知识（角色译名多来自维基、替换对是通用规则），使用者拿到即可用，不必从零攒。
    # 注意 known_names 是**残留检测白名单**（aa_translate.KNOWN_NAMES 直接读它）——空表会让残留校验假阳性。
    _gloss_src = os.path.join(_ROOT, "glossary.json")
    if os.path.isfile(_gloss_src):
        shutil.copy2(_gloss_src, os.path.join(stage, "glossary.json"))
        _gl = json.loads(_read(_gloss_src))
        n_gl = "作品 %d/角色 %d/替换对 %d/白名单 %d" % (
            len(_gl.get("作品", [])), len(_gl.get("角色", [])),
            len(_gl.get("glossary", [])), len(_gl.get("known_names", [])))
    else:
        n_gl = "缺失"
        _write(os.path.join(stage, "glossary.json"),
               json.dumps({"schema": "AA翻译术语表 v2", "更新日期": today,
                           "项目": "（发行包：未找到源 glossary.json）",
                           "作品": [], "角色": [], "glossary": [], "known_names": []},
                          ensure_ascii=False, indent=1))
    # roles.json = **真角色表**（2026-10-09 用户指定保留）：角色译名是通用知识（多来自维基），
    # 使用者最需要它——素材统计、同名归并、残留白名单都靠它。
    _roles_src = os.path.join(_ROOT, "roles.json")
    if os.path.isfile(_roles_src):
        shutil.copy2(_roles_src, os.path.join(stage, "roles.json"))
        n_roles = len(json.loads(_read(_roles_src)).get("角色", []))
    else:
        n_roles = 0
        _write(os.path.join(stage, "roles.json"),
               json.dumps({"schema": "AA角色表 v1", "更新日期": today, "角色": []},
                          ensure_ascii=False, indent=1))
    # 发行包标记（包内测试据此跳过生成者专属用例）
    _write(os.path.join(stage, RELEASE_MARKER), MARKER_TEXT)
    src = os.path.join(_ROOT, "aa_web_app", "data", "work_meta.json")
    meta = json.loads(_read(src)) if os.path.isfile(src) else {}
    keep = {"_meta": meta.get("_meta", {})}
    if EXAMPLE["id"] in meta:
        keep[EXAMPLE["id"]] = meta[EXAMPLE["id"]]
    keep["_meta"] = dict(keep.get("_meta") or {})
    _write(os.path.join(stage, "aa_web_app", "data", "work_meta.json"),
           json.dumps(keep, ensure_ascii=False, indent=1))
    os.makedirs(os.path.join(stage, "aa_web_app", "data", "tasks"), exist_ok=True)
    _log("  真表                glossary.json(%s) / roles.json(%d 角色)" % (n_gl, n_roles))
    _log("  裁剪                work_meta.json(仅示例作品) / 其余作品数据不进包")


def write_requirements(stage):
    """requirements.txt：版本从跑本脚本的解释器取（应为 .venv），取不到就不写版本。"""
    import importlib.metadata as md
    lines = ["# AA 翻译 工作台依赖（安装依赖.bat 会自动装；CLI 管线本身零依赖）",
             "# 版本 = 打包机的 .venv 实测版本，装不上时可放开版本约束"]
    for pkg in REQUIRED_PKGS:
        try:
            lines.append("%s==%s" % (pkg, md.version(pkg)))
        except Exception:
            lines.append(pkg)
    _write(os.path.join(stage, "requirements.txt"), "\n".join(lines) + "\n")
    _log("  requirements.txt      %d 个依赖" % len(REQUIRED_PKGS))


def write_env_example(stage):
    body = ["# AA 翻译 配置示例 —— 复制为 .env 并填自己的 key（工作台「设置」页也会写这里）",
            "# 优先级：真实环境变量 > 本文件(.env) > scripts/config/model_config.toml > 代码默认",
            "# 只填默认链需要的那一个（DEEPSEEK_API_KEY）就能开始用。", ""]
    body += ["# ---- API key（按需填；key 名与 TOML 的 [backend.*].key_env 对应）----"]
    for name, what, url in KEY_HELP:
        body.append("# %s —— %s %s" % (name, what, url))
        body.append("# %s=" % name)
    body += ["", "# ---- 常用选项（都可留空）----",
             "# AA_PORT=8766              # 工作台端口（exe 默认 8766 / 源码路线 8765；被占自动顺延）",
             "# AA_BASE=                  # 数据根（默认 = 代码根；可把作品数据放别的盘）",
             "# AA_PROXY=http://127.0.0.1:10808   # 本地代理（v2rayN 等）",
             "# AA_USE_PROXY=auto         # auto | 0 | 1",
             "# AA_SEL_ENABLE=1           # 划词翻译 + 朗读总开关",
             "# AA_CHAIN=                 # 只跑指定链条目 id（backend|model），留空 = 整条链"]
    _write(os.path.join(stage, ".env.example"), "\n".join(body) + "\n")
    _log("  .env.example          9 个 key + 常用项")


def write_bats(stage):
    """两个 bat（GBK + CRLF）：安装依赖 / 启动工作台。反斜杠用 BS 常量拼，防 \\a 变 BEL。"""
    venv = '".venv' + BS + 'Scripts' + BS + 'python.exe"'
    web = "scripts" + BS + "tools" + BS + "aa_web.py"
    install = [
        "@echo off",
        "chcp 936 >nul",
        "setlocal",
        'cd /d "%~dp0"',
        "echo ============================================",
        "echo  AA 翻译 - 安装工作台依赖（首次使用跑一次）",
        "echo ============================================",
        "where python >nul 2>nul",
        "if errorlevel 1 (",
        "  echo [x] 没找到 python。请先装 Python 3.11+ 并勾选 Add Python to PATH",
        "  echo     下载: https://www.python.org/downloads/",
        "  pause & exit /b 1",
        ")",
        'if exist ".venv' + BS + 'Scripts' + BS + 'python.exe" (echo [=] .venv 已存在，跳过创建) else (',
        "  echo [*] 创建虚拟环境 .venv ...",
        "  python -m venv .venv",
        "  if errorlevel 1 (echo [x] 创建虚拟环境失败 & pause & exit /b 1)",
        ")",
        "echo [*] 安装依赖（首次约 1~3 分钟）...",
        venv + " -m pip install --upgrade pip",
        venv + " -m pip install -r requirements.txt",
        "if errorlevel 1 (echo [x] 依赖安装失败，请检查网络后重试 & pause & exit /b 1)",
        "echo [√] 依赖就绪。双击「启动工作台.bat」开始使用（或直接双击 " + EXE_NAME + "）",
        "pause",
    ]
    start = [
        "@echo off",
        "chcp 936 >nul",
        "setlocal",
        'cd /d "%~dp0"',
        'if not exist ".venv' + BS + 'Scripts' + BS + 'python.exe" (',
        "  echo [x] 还没装依赖：双击「安装依赖.bat」，或直接双击 " + EXE_NAME,
        "  pause & exit /b 1",
        ")",
        "echo [*] 启动工作台（浏览器会自动打开；关闭本窗口即停止）...",
        venv + " " + web,
        "echo.",
        "echo [=] 工作台已退出。",
        "pause",
    ]
    for fn, lines in (("安装依赖.bat", install), ("启动工作台.bat", start)):
        _write(os.path.join(stage, fn), "\n".join(lines) + "\n", encoding="gbk", newline="\r\n")
    _log("  两个 bat            安装依赖.bat / 启动工作台.bat（GBK + CRLF）")


README = """# AA 翻译 —— やる夫系 AA 漫画汉化管线

把日文 AA 漫画（2ch 字符画）批量汉化成简体中文：**抓页 → 提取 → 分块翻译 → 漏译清理 →
宽度/残留校验 → 术语统一 → HTML 合集**。艺术行（纯字符画）本地拼接不送模型，只把文本行
交给大模型，因此成本与误伤都低。

* **不装 Python 也能用**：双击 `AA工作台.exe`（图形界面：添加作品 / 下载 / 翻译 / 校对 /
  生成合集 / 术语表 / 任务看板 / 设置）→ 自动打开 **http://127.0.0.1:8766**
  （exe 默认 8766，源码路线默认 8765，两条路可同时开；`AA_PORT` 可改，端口被占自动顺延）。
* **命令行（CLI）零依赖**：装在 `scripts/` 下的脚本是纯标准库，任何 Python 3.11+ 直接跑。
* **图形界面（源码路线）**：先双击 `安装依赖.bat`，再双击 `启动工作台.bat` → http://127.0.0.1:8765
* **图文操作手册**：[`工作台使用说明.md`](工作台使用说明.md) —— 界面截图 + 逐步说明
  （设置 / 下载 / 翻译 / 校对 / 阅读纠错），没有编程经验也能照着做。

## 一、三步开始

1. **打开工作台**：双击 `AA工作台.exe`（或源码路线跑 `启动工作台.bat`）。首次启动会在本目录
   生成 `.env`、`aaManga/` 等运行时文件。
2. **填一个 API key**：工作台右上「设置 → 密钥」→ 填 `DEEPSEEK_API_KEY`（
   https://platform.deepseek.com/ 注册后在「API Keys」里创建）→「测试连接」显示可用即可。
   key 只写在本目录 `.env`，不会外传。
3. **跑示例作品**：首页已有示例作品「白头与染灰魔女」（前 3 话原文）→「翻译控制台」勾选章节
   → 开始。翻译完成后按作品页「重生成合集」出交付文件夹。

> 只想看效果、不想花 token：示例作品可以只跑「校对」或自己挑 1 话；每块翻译前会打印预计消耗。

## 二、翻译自己的作品

| 步骤 | 做法 |
|---|---|
| 添加作品 | 首页「＋ 添加作品」→ 粘贴原站**分类页 URL**（支持 yaruobook 三站 / r401 等）→ 确认 |
| 下载原文 | 作品列表「更多 → 更新」，或 `python scripts/runner/dl_runner.py --work <id> --chapters all` |
| 生成术语表 | **翻译前必须有作品术语表**（否则 runner 会中止提示）：工作台作品页「生成术语候选…」→ 勾选确认 → 写入作品表；或零 token 快速派生 `python scripts/common/gen_work_glossary.py --work <id> --force` |
| 翻译 | 工作台「翻译控制台」勾章节；或 `python scripts/runner/translate_runner.py --work <id> --chapters new` |
| 校对 | 翻译后自动跑 `refine_aa`（漏译/残留/艺术行还原）；也可单独跑校对任务 |
| 生成合集 | 工作台「重生成合集」；或 `python scripts/common/gen_aa_html.py --work <id>` |

**产物**：`aaManga/<作品>/汉化/*_汉化.txt`（与原文逐行等宽）+ `aaManga/交付HTML/<标题>_汉化合集/`
（`index.html` + 章节数据，整目录拷贝才能离线打开）。

## 三、配置

* 唯一用户可改层 = 本目录 `.env`（工作台「设置」页保存的就是它）。优先级：
  `真实环境变量 > .env > scripts/config/model_config.toml > 代码默认`。
* `scripts/config/model_config.toml` 是**只读**配置，里面是全部后端定义 + 各端点实测单价与
  经验注释（哪个快、哪个便宜、哪个有内容审查、是否需要代理）。**想换模型/加端点改这里**。
* 默认链 = `deepseek 官网 → 硅基流动`（都无需代理）。想用更便宜的
  DeepInfra（$0.06/M）或小米 token 套餐，把 key 填进 `.env` 后在工作台「设置 → 模型」
  勾选对应端点即可。

## 四、命令行

```
python scripts/runner/dl_runner.py        --work <id> [--chapters all|new|1-20]   # 下载
python scripts/runner/translate_runner.py --work <id> [--chapters new] [--force]  # 翻译+校对
python scripts/common/scan_width.py       --work <id>                             # 宽度校验
python scripts/fix/refine_aa.py           --work <id> [--eids ...] [--write]       # 漏译清理
python scripts/common/gen_aa_html.py      --work <id>                             # 生成合集
```

完整命令/env 清单见 `doc/接口文档.md`（自动生成，勿手改）。
自测：`.venv/Scripts/python.exe -m unittest discover -s scripts/tests`（约 2 分钟，零 API）。

## 五、注意事项 / 已知坑

* **需要代理**时（DeepInfra / runinfra / 抓原站被墙）：`v2rayN` 之类本地代理填到
  「设置 → 代理」，默认地址 `http://127.0.0.1:10808`。
* **R18 作品**：部分后端有内容审查（dashscope 不可用、MiniMax 不支持）。作品详情里可标
  R18 等级，翻译时会自动选 `r18_backends` 链。
* **全局术语表 / 角色表 / 残留白名单随包提供**（`glossary.json` + `roles.json`：作品表、改编自维基的
  角色译名、通用替换对、`known_names` 白名单）——开箱即用。**作品级术语表仍需自己生成**
  （翻译前 runner 会硬检，缺表就中止并给生成命令）。
* **示例作品是第三方作品原文**（3 话，仅供跑通流程），跑通后可直接删掉
  `aaManga/白头与染灰魔女/`；宝可梦 wiki 素材来自公开 wiki，`fonts/SarasaMonoSC-Regular.ttf`
  为 OFL 授权字体。
* 工作台只监听本机 `127.0.0.1`、无鉴权，不要在公网暴露。
"""


def stage_manual(stage):
    """图文操作手册 → 包根 `工作台使用说明.md` + 包内 `doc/使用说明图/*.png`。

    源文件在仓库 `doc/工作台使用说明.md`；手册里的图片链接写成相对自身的
    `使用说明图/xxx.png`，复制到包根后要改写成 `doc/使用说明图/xxx.png`
    （图仍放在 doc/ 下，免得包根堆一堆图片目录）。
    """
    src = os.path.join(_ROOT, MANUAL_SRC_REL)
    if not os.path.isfile(src):
        _log("  ⚠️ 缺少 %s —— 发行包不会带图文操作手册" % MANUAL_SRC_REL)
        return 0
    text = _read(src).replace("](%s/" % SHOTS_DIR, "](doc/%s/" % SHOTS_DIR)
    _write(os.path.join(stage, MANUAL_DST), text)
    shots_src = os.path.join(_ROOT, "doc", SHOTS_DIR)
    n = 0
    if os.path.isdir(shots_src):
        for fn in sorted(os.listdir(shots_src)):
            if fn.lower().endswith(".png"):
                dst = os.path.join(stage, "doc", SHOTS_DIR)
                os.makedirs(dst, exist_ok=True)
                shutil.copy2(os.path.join(shots_src, fn), os.path.join(dst, fn))
                n += 1
    _log("  %s    %d 张配图 → doc/%s/" % (MANUAL_DST, n, SHOTS_DIR))
    return n


def write_readme(stage):
    _write(os.path.join(stage, "README.md"), README)
    _log("  README.md            新写（快速开始 / 配置 / CLI / 坑）")


def build_exe_for_release(stage, out_path):
    """从**发行包的 staging 树**打 exe（而不是另 staging 一份仓库源码）。

    这是一次事故换来的规矩：曾经 `build_workbench_exe.py` 自己 staging 仓库源码，
    于是包里的源码被裁成 1 部示例作品、exe 里的 registry 却还是生成者的 45 部
    —— 双击 exe 首页列出全部作品。exe 与包必须同源（同一棵 staging）。
    """
    script = os.path.join(_HERE, "build_workbench_exe.py")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cmd = [sys.executable, script, "--stage", stage, "--out", out_path, "--no-probe"]
    _log("  打 exe（源 = 发行包 staging）...")
    rc = subprocess.call(cmd)
    if rc:
        return False
    return os.path.isfile(out_path)


# ============================================================ 门禁

def gates(stage):
    """硬门禁：白名单 / 脱敏残留 / 密钥 / 语法。返回问题列表（空 = 通过）。"""
    bad = []
    top = set(os.listdir(stage))
    extra = top - TOP_ENTRIES
    if extra:
        bad.append("顶层出现白名单外的条目: %s" % ", ".join(sorted(extra)))
    left = rc.leftovers(stage)
    if left:
        bad.append("仍有本机痕迹 %d 处，例如 %s" % (len(left), left[:5]))
    sec = rc.secret_hits(stage)
    if sec:
        bad.append("发现疑似真 key 明文: %s" % sec[:5])
    syn = rc.compile_all(stage)
    if syn:
        bad.append("语法检查失败: %s" % syn[:5])
    # 内容级抽查（防止"干净但东西没进去"）
    must = ["scripts/tools/aa_web.py", "scripts/config/model_config.toml", "README.md",
            "requirements.txt", ".env.example", "aa_web_app/main.py", "aa_web_app/web_state.py",
            "doc/接口文档.md", "glossary.json", "roles.json",
            # 图文操作手册（包根 md + doc/使用说明图/*.png 配图目录）
            MANUAL_DST, os.path.join("doc", SHOTS_DIR),
            # 许可：代码 MIT + 字体 OFL（分发字体必须随附 OFL 文本，缺了就是不合规分发）
            "LICENSE", os.path.join("fonts", "OFL.txt"),
            # AA 字体 + 许可（手机端列位靠它，见根 AGENTS.md「交付页内嵌 AA 等宽字体」）
            os.path.join("fonts", "aahub.ttf"), os.path.join("fonts", "aahub_LICENSE.txt"),
            os.path.join("aaManga", EXAMPLE["dir"], "work", "glossary_%s.json" % EXAMPLE["id"])]
    missing = [m for m in must if not os.path.exists(os.path.join(stage, m))]
    if missing:
        bad.append("缺少必需文件: %s" % missing)
    # 注册表断言（变换漏了就拦住：图片漫画表必须为空）
    # 2026-10-14 main.py 拆成 web_state.py + routers/，IMAGE_MANGA 常量现在 web_state.py
    # （两份都读，兼容旧布局/新布局）
    try:
        _main_src = (_read(os.path.join(stage, "aa_web_app", "main.py"))
                     + _read(os.path.join(stage, "aa_web_app", "web_state.py")))
    except OSError:
        _main_src = ""
    if "IMAGE_MANGA = {}" not in _main_src:
        bad.append("aa_web_app 的 IMAGE_MANGA 未清空（图片漫画注册表变换没跑）")
    # 反向抽查：不该出现的东西
    forbidden = ["AGENTS.md", "mitm_mcp_traffic.db", "acgn_东方心绮谭", "legacy", "logs",
                 "benchmark", ".codex", "导出", "交付HTML", "揭示板", "aaManga/ポケの子"]
    hits = []
    for root, dirs, files in os.walk(stage):
        for f in files + dirs:
            for fd in forbidden:
                if f == fd or f.startswith(fd):
                    hits.append(os.path.relpath(os.path.join(root, f), stage))
    if hits:
        bad.append("带进了不该有的东西: %s" % hits[:8])
    return bad


# ============================================================ 产物

def _looks_like_live_data_root(d):
    r"""产物目录里是不是带着**运行中的数据**（有人把 exe 放在里面跑过）。

    事故背景：测试实例直接跑在 `导出/<包>/` 里（数据根 = 包目录）。重建会删掉整棵目录树，
    所以删（更准确说：挪开）之前先认人。

    2026-10-10 修正：**上一次打出的包本身**就带非空 `aaManga/`（示例作品）+ 示例
    `work_meta.json`，旧判据会把每个旧包都判成"活数据根" → 同一个 `--out` 永远拒绝重打，
    `_set_aside_out_dir()` 的挪开机制永远轮不上。

    现按「包标记 + 强信号」区分：**有 `.release_package` 的老包 → 不算活数据**（交给挪开机制，
    只 `os.replace` 不删，数据一个字节不丢）；只有出现**别的作品**、**填过 key 的 `.env`**、
    或**真出过交付物**才算活数据。注意：冒烟测试就在包目录里跑 exe/脚本，会留下
    `汉化/*.txt`、`work/*` 缓存、`read_state.json` 等痕迹——**这些不算"被人用过"**。
    
    """
    if not os.path.isdir(d):
        return False
    marker = os.path.isfile(os.path.join(d, ".release_package"))

    env = os.path.join(d, ".env")
    if os.path.isfile(env):
        try:
            txt = open(env, encoding="utf-8", errors="ignore").read()
        except OSError:
            return True
        if re.search(r"^\s*[A-Za-z0-9_]+\s*=\s*\S", txt, re.M):
            return True                                # 用户填过 key/端口
        if not marker:
            return True                                # 有 .env 又不是包目录 → 认活

    am = os.path.join(d, "aaManga")
    if os.path.isdir(am):
        subs = [n for n in os.listdir(am) if os.path.isdir(os.path.join(am, n))]
        if not marker:
            return True if (subs or any(os.scandir(am))) else False
        if [n for n in subs if n != EXAMPLE["dir"]]:
            return True                                # 包目录里冒出别的作品 = 真数据

    for sub in ("交付HTML", "揭示板", "导出"):
        p = os.path.join(d, sub)
        if not os.path.isdir(p):
            continue
        if not marker:
            if any(os.scandir(p)):
                return True                            # 非包目录里有交付物/揭示板/导出包
            continue
        # 包目录：冒烟测试会建空壳目录，只有**真出过产物**才算活数据
        for root, dirs, files in os.walk(p):
            if [f for f in files if not f.startswith(".")]:
                return True
    if not marker and os.path.isfile(os.path.join(d, "aa_web_app", "data", "work_meta.json")):
        return True                                    # 有作品元数据
    return False


def _set_aside_out_dir(out_dir, keep=2):
    r"""把已存在的产物目录**挪开**（`<名>.old_<时间戳>`），绝不 `rmtree`。

    为什么：`shutil.rmtree` 一旦误判就是**不可恢复**的数据丢失 —— 实测两次（一次是 Explorer 占住目录、
    rmtree 删到一半报错；一次是守卫没拦住、把正在跑工作台实例的数据根整个换掉）。
    改名为 `.old_<ts>` 是原子操作、瞬间完成、还能反悔：出包成功后由本函数顺手清掉更早的 `.old_*`
    （只留最近 `keep` 份）。
    """
    if not os.path.isdir(out_dir):
        return None
    ts = time.strftime("%Y%m%d_%H%M%S")
    aside = out_dir + ".old_" + ts
    n = 1
    while os.path.exists(aside):
        n += 1
        aside = "%s.old_%s_%d" % (out_dir, ts, n)
    os.replace(out_dir, aside)
    # 只留最近 keep 份
    parent, base = os.path.dirname(out_dir), os.path.basename(out_dir) + ".old_"
    olds = sorted(d for d in os.listdir(parent) if d.startswith(base)) if os.path.isdir(parent) else []
    for d in olds[:-keep] if keep > 0 else olds:
        shutil.rmtree(os.path.join(parent, d), ignore_errors=True)
    return aside


def make_zip(stage, zip_path, empty_dirs):
    if os.path.exists(zip_path):
        os.remove(zip_path)
    base = os.path.basename(stage)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for rel in empty_dirs:
            z.writestr(zipfile.ZipInfo(base + "/" + rel.replace(os.sep, "/") + "/"), b"")
        for root, dirs, files in os.walk(stage):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in sorted(files):
                p = os.path.join(root, f)
                arc = base + "/" + os.path.relpath(p, stage).replace(os.sep, "/")
                z.write(p, arc)
    return zip_path


def verify_unpacked(unpack, venv_py, smoke_api=False, baseline_file=None):
    """解包冒烟：单测基线对齐 + 零 token 链路。返回 (ok, 报告行)。"""
    rep, ok = [], True
    # ① 单测（零 API）：与仓库基线对比，不允许出现基线之外的新失败
    #    环境**消毒**：操作者 shell 里的 AA_ENV/AA_ENV_PATH/*_API_KEY 会盖掉用例夹具，
    #    让"包内自检"报出与包无关的假失败（实测：AA_ENV 指向别处 + 环境有 OPENCODE_GO_API_KEY
    #    → TestKeyCache 4 例全红）。自检要验的是**这个包**，不是操作者的机器。
    tenv = dict(os.environ)
    for k in [k for k in list(tenv) if k in ("AA_ENV", "AA_ENV_PATH") or k.endswith("_API_KEY")]:
        tenv.pop(k, None)
    r = subprocess.run([venv_py, "-m", "unittest", "discover", "-s", "scripts/tests"],
                       cwd=unpack, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=1800, env=tenv)
    fails = sorted(set(re.findall(r"(?:FAIL|ERROR): (\S+)", r.stderr or "")))
    tail = [l for l in (r.stderr or "").splitlines() if l.startswith("Ran ")]
    summary = [l for l in (r.stderr or "").splitlines() if l.startswith(("OK", "FAILED"))]
    rep.append("    单测: %s %s" % (tail[0] if tail else "?",
                                   summary[0] if summary else "(无汇总行)"))
    rep.append("    失败用例数: %d" % len(fails))
    if not tail:
        rep.append("    [×] 没有拿到 unittest 汇总行 —— 多半是解释器/导入层直接崩了")
        rep.append("      %s" % ((r.stderr or "")[-400:].replace("\n", " | ")))
        ok = False
    if baseline_file and os.path.isfile(baseline_file):
        base = set(json.load(open(baseline_file, encoding="utf-8")))
        new = [f for f in fails if f not in base]
        rep.append("    基线外新失败: %s" % (new or "无"))
        if new:
            ok = False
    for f in fails[:8]:
        rep.append("      · %s" % f)
    # ② 零 token 链路：章节准备 + 台词提取（都不调模型）
    env = dict(os.environ)
    env.pop("AA_ENV_PATH", None)
    steps = [
        ["scripts/common/chapter_prep.py", "--work", EXAMPLE["id"]],
        ["scripts/common/extract_dialogue.py", "--work", EXAMPLE["id"], "--src"],
    ]
    for cmd in steps:
        rr = subprocess.run([venv_py] + cmd, cwd=unpack, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=900, env=env)
        rep.append("    %s → rc=%d" % (os.path.basename(cmd[0]), rr.returncode))
        if rr.returncode != 0:
            ok = False
            rep.append("      %s" % ((rr.stdout or "")[-300:] + (rr.stderr or "")[-300:]))
    return ok, rep


def _probe_exe_cli(root, exe):
    """关键回归：**冻结 exe 起子任务**能不能跑。

    工作台的下载/翻译/校对/生成合集都是 `subprocess` 起子任务；冻结后 `sys.executable` 是 exe
    自己，若不改走 `--cli` 模式，就会"再起一份工作台"（任务卡住 / 表现为下载失败）。
    这里直接跑一次真实子任务（零 token 的 chapter_prep），rc=0 才算过。
    """
    script = os.path.join(root, "scripts", "common", "chapter_prep.py")
    env = dict(os.environ, AA_BASE=root, AA_ENV=os.path.join(root, ".env"))
    for k in ("AA_ENV_PATH", "PYTHONPATH"):
        env.pop(k, None)
    try:
        r = subprocess.run([exe, "--cli", script, "--work", EXAMPLE["id"]], cwd=root, env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=600)
    except Exception as e:
        return False, "起子任务异常: %s" % e
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        return False, "起子任务 rc=%d（%s）" % (r.returncode, out.strip().replace("\n", " | ")[-200:])
    if "chapter_prep" not in out:
        return False, "起子任务 rc=0 但没产出 chapter_prep 日志（%s）" % out.strip()[-160:]
    # ② 依赖齐全（exe 把 scripts/ 当数据带，PyInstaller 看不见它们内部的 import；漏一个只在跑到
    #    那条路时才炸——实测「生成术语候选」炸在 build_term_pool → import janome）
    ci = os.path.join(root, "scripts", "tools", "check_imports.py")
    if os.path.isfile(ci):
        try:
            r2 = subprocess.run([exe, "--cli", ci], cwd=root, env=env, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=600)
        except Exception as e:
            return False, "依赖自检异常: %s" % e
        if r2.returncode != 0:
            return False, "依赖自检失败：%s" % ((r2.stdout or "") + (r2.stderr or "")).strip()[-220:]
    # ③ 子进程必须命中**数据根**的 registry（不是解包目录那份）
    #    实测踩过：--cli 分支跑在 registry 引导之前 → 使用者一按「生成术语候选」就报「未知作品: X（可用: baitou）」
    cr = os.path.join(root, "scripts", "tools", "check_registry.py")
    if os.path.isfile(cr):
        try:
            r3 = subprocess.run([exe, "--cli", cr, EXAMPLE["id"]], cwd=root, env=env,
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=600)
        except Exception as e:
            return False, "registry 自检异常: %s" % e
        out3 = (r3.stdout or "") + (r3.stderr or "")
        data_reg = os.path.join(root, "scripts", "works", "registry.py")
        if r3.returncode != 0:
            return False, "registry 自检 rc=%d（%s）" % (r3.returncode, out3.strip()[-200:])
        # 两侧都要 normcase（Windows 大小写不敏感；只归一化一侧会误报）
        out_norm = os.path.normcase(out3.replace("/", os.sep))
        if os.path.normcase(data_reg) not in out_norm:
            return False, ("子进程命中的不是数据根 registry（%s）" % out3.strip()[-200:])
    # ④ 真跑一次分词器：janome 这类包**按路径 mmap 读自己的词典**，收进 PYZ 照样报
    #    FileNotFoundError: _MEI*/janome/sysdic/entries_compact0.py —— 只验 import 抓不到这一层。
    #    build_term_pool 是零 token 的（候选池构建不调模型），拿它当"真机"验收。
    bt = os.path.join(root, "scripts", "common", "build_term_pool.py")
    if os.path.isfile(bt):
        probe_dir = os.path.join(root, "temp", "probe_dialogue")
        try:
            os.makedirs(probe_dir, exist_ok=True)
            with open(os.path.join(probe_dir, "probe_台词.txt"), "w", encoding="utf-8") as f:
                f.write("やる夫が来た。やる夫は笑った。やる夫と魔女が話した。\n"
                        "魔女は魔法を使った。魔女は笑った。やる夫は驚いた。\n")
            r4 = subprocess.run([exe, "--cli", bt, "--aa", probe_dir, "--min-freq", "2",
                                 "--top", "5"], cwd=root, env=env, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=600)
        except Exception as e:
            return False, "分词器真机验收异常: %s" % e
        finally:
            shutil.rmtree(probe_dir, ignore_errors=True)
        out4 = (r4.stdout or "") + (r4.stderr or "")
        if r4.returncode != 0 or "候选" not in out4:
            return False, "分词器真机验收失败 rc=%d（%s）" % (r4.returncode, out4.strip()[-260:])
    return True, ("起子任务 `--cli chapter_prep` rc=0 ✓ · 依赖自检 rc=0 ✓ · 子进程用数据根 registry ✓"
                  " · 分词器真机跑通 ✓")


def probe_exe_in_package(root, timeout=180):
    """在**解包后的发行包目录**里真起一次 exe：HTTP 200 + 首页能列出示例作品 + /manga 空表。

    这是唯一能证明"不装 Python 的人双击真的能用"的检查；数据根 = 包目录（AA_BASE 指过去），
    所以它同时验证示例作品/图标/模板都在包内。端口从 8799 起找空的，绝不碰 8765。
    """
    exe = os.path.join(root, EXE_NAME)
    if not os.path.isfile(exe):
        return None, "包内没有 %s（跳过 exe 探测）" % EXE_NAME
    import socket as _sock
    import urllib.request as _url
    import tempfile
    cli_ok, cli_note = _probe_exe_cli(root, exe)
    port = None
    for cand in range(8799, 8810):
        with _sock.socket() as s:
            if s.connect_ex(("127.0.0.1", cand)) != 0:
                port = cand
                break
    if port is None:
        return False, "8799-8809 都被占用，跳过"
    env = dict(os.environ)
    env.update({"AA_BASE": root, "AA_PORT": str(port)})
    for k in ("AA_ENV", "AA_ENV_PATH", "PYTHONPATH"):
        env.pop(k, None)
    p = subprocess.Popen([exe], env=env, cwd=root,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    body, t0, ok = "", time.time(), False
    try:
        while time.time() - t0 < timeout:
            try:
                with _url.urlopen("http://127.0.0.1:%d/" % port, timeout=2) as r:
                    if r.status == 200:
                        body = r.read().decode("utf-8", "replace")
                        ok = True
                        break
            except Exception:
                time.sleep(1)
        if not ok:
            return False, "%ds 内没起来（端口 %d）· %s" % (timeout, port, cli_note)
        # registry 持久化：启动后数据根里应出现 scripts/works/registry.py（否则"加作品"写进解包目录，
        # 重启就丢 —— 表现为作品页看不到章节）
        reg_seeded = os.path.isfile(os.path.join(root, "scripts", "works", "registry.py"))
        has_work = EXAMPLE["dir"] in body
        try:
            with _url.urlopen("http://127.0.0.1:%d/manga" % port, timeout=3) as r2:
                manga = r2.read().decode("utf-8", "replace")
            n_manga = manga.count('<td class="strong">')
        except Exception as e:
            n_manga = -1
        return (has_work and n_manga == 0 and cli_ok and reg_seeded), (
            "HTTP 200（%.1fs）· 首页示例作品 %s · /manga 表格 %s · registry 落数据根 %s · %s"
            % (time.time() - t0, "✓" if has_work else "✗ 未找到",
               "0 行 ✓" if n_manga == 0 else "异常(%d)" % n_manga,
               "✓" if reg_seeded else "✗", cli_note))
    finally:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def make_baseline(venv_py, base_file):
    """仓库内跑一次单测 → 记下失败用例名，作为解包冒烟的"允许基线"。

    动机：仓库当前就有 5 例已知失败（4 例受本机 .env 影响、1 例既有 bug）。
    出包不该被它们卡住，但**基线之外的新失败**必须拦住。
    """
    _log("  生成单测基线（仓库内跑一次，约 2 分钟）...")
    r = subprocess.run([venv_py, "-m", "unittest", "discover", "-s", "scripts/tests"],
                       cwd=_ROOT, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=1800)
    fails = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\S+)", r.stderr or "", re.M)))
    os.makedirs(os.path.dirname(base_file), exist_ok=True)
    with open(base_file, "w", encoding="utf-8") as f:
        json.dump(fails, f, ensure_ascii=False, indent=1)
    _log("  基线失败 %d 例: %s" % (len(fails), fails or "无"))
    return fails


def main(argv=None):
    ap = argparse.ArgumentParser(description="打包开箱即用发行包")
    ap.add_argument("--out", default=os.path.join(_ROOT, "导出"), help="产物落盘目录（默认 导出/）")
    ap.add_argument("--no-exe", action="store_true", help="不打 exe（复用现有产物；没有就跳过）")
    ap.add_argument("--keep-stage", action="store_true", help="保留 staging 目录（排查用）")
    ap.add_argument("--no-verify", action="store_true", help="跳过解包冒烟")
    ap.add_argument("--smoke-api", action="store_true", help="额外用真实 key 翻 1 话（花钱）")
    a = ap.parse_args(argv)

    # 产物目录检查**前置**：若那里跑着工作台实例（数据根），直接拒绝 —— 别等 staging/测试/exe 都跑完
    # （7 分钟）才说"不能覆盖"。也便于 `一键打包发行包.bat` 立刻改输出目录重跑。
    out_dir = os.path.join(a.out, NAME)
    if os.path.isdir(out_dir) and _looks_like_live_data_root(out_dir):
        _log("=== 拒绝出包：%s 里带着运行中的数据（.env / aaManga / work_meta 等）" % out_dir)
        _log("    这里像是有人把 exe 放在里面跑过的数据根 —— 删掉会丢数据。")
        _log("    改用别的输出目录（--out），或先把数据挪走。")
        return 6

    stage = os.path.join(_ROOT, "temp", "release_stage", NAME)
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    os.makedirs(stage)
    _log("=== staging → %s" % stage)
    stage_scripts(stage)
    stage_webapp(stage)
    stage_assets(stage)
    stage_example(stage)
    _log("=== 变换（作品痕迹清空 / 默认链改写 / 脱敏）")
    transform_registry(stage)
    transform_toml(stage)
    transform_aa_env(stage)
    transform_image_manga(stage)
    write_skeletons(stage)
    write_requirements(stage)
    write_env_example(stage)
    write_bats(stage)
    write_readme(stage)
    stage_manual(stage)

    n_files, n_hits = rc.desensitize(stage)
    _log("=== 脱敏: %d 个文件 / %d 处本机路径" % (n_files, n_hits))
    bad = gates(stage)
    if bad:
        _log("=== 拒绝出包：硬门禁未通过")
        for b in bad:
            _log("   ✗ %s" % b)
        return 3
    _log("=== 硬门禁通过（白名单 / 残留 / 密钥 / 语法 / 必需文件）")

    # exe 必须在**脱敏+变换之后**、从同一棵 staging 打（否则 exe 里是生成者的作品清单）
    has_exe = False
    if a.no_exe:
        _log("  --no-exe：本次不带 AA工作台.exe（只出源码包）")
    else:
        exe_tmp = os.path.join(_ROOT, "temp", "release_exe", EXE_NAME)
        if not build_exe_for_release(stage, exe_tmp):
            _log("=== 拒绝出包：exe 构建失败")
            return 5
        shutil.copy2(exe_tmp, os.path.join(stage, EXE_NAME))
        has_exe = True
        _log("  %s  %.1f MB（源 = 本次 staging）" % (EXE_NAME, os.path.getsize(exe_tmp) / 1048576.0))

    out_dir = os.path.join(a.out, NAME)
    if os.path.isdir(out_dir):
        if _looks_like_live_data_root(out_dir):
            _log("=== 拒绝出包：%s 里带着运行中的数据（.env / aaManga / work_meta 等）" % out_dir)
            _log("    这里像是有人把 exe 放在里面跑过的数据根 —— 删掉会丢数据。")
            _log("    改用别的输出目录（--out），或先把数据挪走。")
            return 6
        aside = _set_aside_out_dir(out_dir)
        if aside:
            _log("  旧产物已挪到 %s（出包成功后自动只留最近 2 份，不会删数据）"
                 % os.path.basename(aside))
    shutil.copytree(stage, out_dir)
    empty_dirs = [os.path.join("aa_web_app", "data", "tasks"),
                  os.path.join("aaManga", EXAMPLE["dir"], "汉化"),
                  os.path.join("aaManga", EXAMPLE["dir"], "raw"),
                  os.path.join("aaManga", EXAMPLE["dir"], "work")]
    zip_path = make_zip(stage, os.path.join(a.out, NAME + ".zip"), empty_dirs)
    _log("=== 产物: %s（%s）" % (out_dir, rc.human(rc.dir_size(out_dir))))
    _log("=== 压缩包: %s（%s）" % (zip_path, rc.human(os.path.getsize(zip_path))))

    if a.no_verify:
        return 0
    _log("=== 解包冒烟")
    venv_py = os.path.join(_ROOT, ".venv", "Scripts", "python.exe")
    base_file = os.path.join(_ROOT, "temp", "release_baseline_tests.json")
    if not os.path.isfile(base_file):
        make_baseline(venv_py, base_file)
    unpack = os.path.join(_ROOT, "temp", "release_verify")
    if os.path.isdir(unpack):
        shutil.rmtree(unpack)
    os.makedirs(unpack)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(unpack)
    root = os.path.join(unpack, NAME)
    verdict = [("zip 内容 = 目录内容", rc.manifest(root) == rc.manifest(out_dir))]
    ok_u, rep = verify_unpacked(root, venv_py, a.smoke_api, baseline_file=base_file)
    verdict.append(("解包冒烟", ok_u))
    _log("  zip 与目录清单一致: %s" % verdict[0][1])
    for line in rep:
        _log(line)
    if has_exe:
        ok_x, note_x = probe_exe_in_package(root)
        verdict.append(("exe 探测", bool(ok_x)))
        _log("    exe: %s" % note_x)
    if a.smoke_api:
        _log("    (--smoke-api 预留：用真实 key 翻 1 话，默认关闭)")
    allok = all(v for _n, v in verdict)
    _log("=== 自检: %s" % ("通过" if allok else "未通过"))
    if not a.keep_stage:
        shutil.rmtree(unpack, ignore_errors=True)
    return 0 if allok else 4


if __name__ == "__main__":
    sys.exit(main())
