# -*- coding: utf-8 -*-
"""打包公共实现：脱敏 / 本机痕迹与密钥复查 / 语法检查 / 白名单拷贝（2026-10-09 抽出）。

原本只存在于 `build_edit_server.py`；现在两处共用：

* `scripts/tools/build_edit_server.py` —— 交付页「编辑服务」单文件 exe（随合集外发）；
* `scripts/tools/build_release.py` —— 「开箱即用」发行包（源码 + 冻结工作台 exe）。

**为什么必须脱敏**：这些都是会外发出去的东西（交付夹、发行包）。生成者本机路径、
用户名、key 名一旦夹带，等于把目录结构与凭据线索一起送出去。脱敏只作用于打包时的
staging 拷贝，**仓库文件一个字节都不动**。

三类检查（全部是硬门禁，命中即拒绝出包）：

1. `desensitize()` —— 本机路径 → `<repo>`（规则见 `DESENS`，防误伤说明见下）；
2. `leftovers()` —— 脱敏后仍带本机痕迹（用户名/盘符目录/临时目录）的文件；
3. `secret_hits()` —— 真 key 值模式（`sk-…`/`tp-…`/长 hex）与 key 环境变量名清单。

`compile_all()` 兜底：脱敏是正则替换，理论上可能改坏语法 → 逐个 `compile()` 一遍
（只查语法，不留 .pyc）。
"""

import os
import re

# ---------------------------------------------------------------- 脱敏规则

# 本机路径脱敏：命中的一律换成占位符（只作用于打包 staging 里的文本拷贝）。
# 关键防误伤：盘符前必须**不是字母数字**（否则 `https://api...` 里的 `s://api` 会被当成
# 「s 盘 + 路径」，把 URL 打成 `http` + 占位符——实测踩过）。
DESENS = [
    # Python 源码里的转义写法（`'<repo>\\aaManga\\X\\aa'`：盘符后 2 个以上分隔符）
    (re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:(?:[\\/]{2,}[^\\/\"'\s,)]+)+"), "<repo>"),
    # 注释/文档里的单反斜杠或正斜杠写法（`<repo>\...` / `<repo-parent>/...`）
    (re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:ai|hermes|Users)(?:[\\/][^\\/\s\"',)]+)*"), "<repo>"),
    # 其它盘符路径（大写盘符 + 至少两段，如 `D:\backup\xx`）
    (re.compile(r"(?<![A-Za-z0-9])[A-Z]:\\[^\\\s\"',)]+\\[^\\\s\"',)]+"), "<repo>"),
]
DESENS_EXT = (".py", ".md", ".json", ".toml", ".txt", ".bat", ".js", ".css", ".html")

# 脱敏后仍不该出现的东西（生成者用户名 / 本机盘符目录 / 打包机临时目录）
LEFTOVER = re.compile(r"<user>|C:[\\/]+Users|F:[\\/]+ai|-Users-|AppData[\\/]+Local[\\/]+Temp")
# 放宽版（只拦**个人信息**，放行项目自身的 `<repo>`）——给测试夹具用，见 NO_DESENS_DIRS
LEFTOVER_RELAXED = re.compile(r"<user>|C:[\\/]+Users|-Users-|AppData[\\/]+Local[\\/]+Temp")

# **免脱敏目录**（相对 staging 根，`/` 分隔前缀）：
# 测试夹具里的绝对路径是**故意伪造**的（`r"C:\tmp\f.ttf"`、`"<repo>/aaManga/某作品/aa"`），
# 它们要么用来验证路径解析、要么用来验证宽度…被脱敏成 `<repo>` 后**断言就废了**
# （实测：test_env_switches 因 `AA_AA_FONT_FILE` 被替换而失败）。所以这些目录不脱敏，
# 但仍过一遍"放宽版"痕迹扫描（个人信息照拦）。
NO_DESENS_DIRS = ("scripts/tests",)

# 自带这些模式串的文件（用它们找痕迹，复查时必然自己命中自己）→ 跳过
PATTERN_HOLDERS = {"release_common.py", "build_release.py", "build_edit_server.py"}
# 跳过的目录名（不分层级）
SKIP_DIRS = {"__pycache__", ".git", "node_modules"}

# 真 key 值的形态：OpenAI 系 `sk-`、小米 token plan `tp-`、长 hex/长 token
SECRET_RES = [
    ("sk- 明文", re.compile(r"sk-[A-Za-z0-9_\-]{16,}")),
    ("tp- 明文", re.compile(r"tp-[A-Za-z0-9_\-]{16,}")),
    ("长 hex(≥40)", re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{40,}(?![0-9a-fA-F])")),
    ("Bearer 明文", re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{20,}")),
]


def _text_files(root, exts=DESENS_EXT, skip_dirs=SKIP_DIRS):
    """遍历 root 下的文本文件 → (绝对路径, 相对路径)；跳过 __pycache__ 等与不匹配后缀的。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in sorted(filenames):
            if not fn.lower().endswith(tuple(exts)):
                continue
            p = os.path.join(dirpath, fn)
            yield p, os.path.relpath(p, root).replace(os.sep, "/")


def _in_no_desens(rel):
    """该相对路径是否落在免脱敏目录里（见 NO_DESENS_DIRS）。"""
    return any(rel == d or rel.startswith(d + "/") for d in NO_DESENS_DIRS)


def desensitize(stage, skip_files=PATTERN_HOLDERS):
    """就地脱敏 staging 里的文本源码 → (改动文件数, 替换次数)。二进制读写以保住行尾。

    跳过 `NO_DESENS_DIRS`（测试夹具，见该常量的说明）。
    """
    files = hits = 0
    for p, rel in _text_files(stage):
        if os.path.basename(rel) in skip_files or _in_no_desens(rel):
            continue
        try:
            txt = open(p, "rb").read().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new = txt
        for pat, rep in DESENS:
            new, n = pat.subn(rep, new)
            hits += n
        if new == txt:
            continue
        open(p, "wb").write(new.encode("utf-8"))
        files += 1
    return files, hits


def leftovers(stage, skip_files=PATTERN_HOLDERS):
    """复查：仍带本机痕迹的文件 → [(相对路径, 命中串)]。

    免脱敏目录（测试夹具）用**放宽版**模式：只拦个人信息，放行项目自身的 `<repo>`。
    """
    out = []
    for p, rel in _text_files(stage):
        if os.path.basename(rel) in skip_files:
            continue
        try:
            txt = open(p, "rb").read().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        pat = LEFTOVER_RELAXED if _in_no_desens(rel) else LEFTOVER
        m = pat.search(txt)
        if m:
            out.append((rel, m.group(0)))
    return out


def secret_hits(stage, skip_files=PATTERN_HOLDERS):
    """复查：出现真 key 值形态的文件 → [(相对路径, 形态名)]（key 名字符串合法，不在此列）。"""
    out = []
    for p, rel in _text_files(stage):
        if os.path.basename(rel) in skip_files:
            continue
        try:
            txt = open(p, "rb").read().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for name, pat in SECRET_RES:
            if pat.search(txt):
                out.append((rel, name))
                break
    return out


def compile_all(stage):
    """所有 .py 过一遍 compile()（脱敏不该改坏语法）→ [(相对路径, 错误信息)]。"""
    bad = []
    for p, rel in _text_files(stage, exts=(".py",)):
        try:
            src = open(p, "rb").read().decode("utf-8")
        except (OSError, UnicodeDecodeError) as e:
            bad.append((rel, "读取失败: %s" % e))
            continue
        try:
            compile(src, p, "exec")
        except SyntaxError as e:
            bad.append((rel, "SyntaxError: %s line %s" % (e.msg, e.lineno)))
    return bad


# ---------------------------------------------------------------- 拷贝

def copy_filtered(src, dst, ignore_names=(), ignore_dirs=(), ignore_globs=()):
    """把 src 拷到 dst，按名/目录/glob 排除（`.gitignore` 风格不做，只做显式排除）。

    返回实际拷贝的文件数。`ignore_names` 命中文件名（含扩展名前缀匹配需自己写 glob）。
    """
    import fnmatch
    import shutil

    n = 0
    if not os.path.isdir(src):
        return 0
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames
                       if d not in ignore_dirs and d != "__pycache__"]
        target = os.path.join(dst, os.path.relpath(dirpath, src))
        os.makedirs(target, exist_ok=True)
        for fn in filenames:
            if fn in ignore_names or fn.endswith(".pyc"):
                continue
            if any(fnmatch.fnmatch(fn, g) for g in ignore_globs):
                continue
            shutil.copy2(os.path.join(dirpath, fn), os.path.join(target, fn))
            n += 1
    return n


def manifest(root, skip_dirs=SKIP_DIRS):
    """包内清单（相对路径，`/` 分隔，排序）——用于白名单核对与人工复查。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in filenames:
            out.append(os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/"))
    return sorted(out)


def dir_size(root):
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                pass
    return total


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0
