# -*- coding: utf-8 -*-
"""仓库根 .env 的统一读写（方案 doc/方案归档/工作台设置页与配置文件方案_2026-10-08.md 阶段 1）。

**单一用户可改层**。优先级（高→低）：

    真实环境变量  >  <仓库根>\\.env  >  scripts/config/model_config.toml  >  代码默认

* `load_dotenv()` 把 `.env` 里的 `KEY=value` 灌进 `os.environ`（**不覆盖**已存在的真实 env）
  —— 因此「读 os.environ」天然就是上面这条优先级的前两层；工作台设置页保存时同步调它即可立即生效。
* `read_key(name)` 供各后端的 key 读取使用：env → 仓库根 `.env` → hermes 两处 `.env`（保留兜底，
  生成者本机零回归）；`aa_translate.get_key()` 与 `aa_backend._read_env_key()` 都走这里，不再各自实现。
* `write_values()` 行级替换 + 原子写：**保留注释与未改动行**，缺失的键追加到文件末尾。
  值里出现换行会被拒绝（防写坏文件）；刻意不做整表重写（术语表整表覆盖事故同款陷阱）。

零依赖（纯标准库），可被 scripts/ 下任何脚本 import（`common/` 已在 sys.path）。
"""

import os
import re
import threading

_LOCK = threading.Lock()
_CACHE = {"sig": None, "data": {}}
# 本次进程从 .env 灌进 os.environ 的键（force 重载时可把「已从文件删掉的键」摘回去）
_LOADED = set()

# 键名与值的合法性（写入侧护栏）
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")

# 非 AA_ 前缀但属于本项目、需要从 .env 载入 os.environ 的名字（其余非 AA_ 键只按名读值）
ENV_EXTRA = {
    "COMMANDCODE_API_KEY", "SILICONFLOW_API_KEY", "SILICONFLOW_FREE_API_KEY",
    "QWEN_API_KEY", "MINIMAX_CN_API_KEY", "XIAOMI_API_KEY", "XIAOMI_MIMO_API_KEY",
    "DEEPSEEK_API_KEY", "DEEPINFRA_API_KEY", "RUNINFRA_API_KEY",
    "OPENROUTER_API_KEY", "GMI_API_KEY",
    "OPENCODE_GO_API_KEY", "OPENCODE_FREE_ZEN_API_KEY",
}


def repo_root():
    """仓库根（scripts/common/aa_env.py 上跳 3 级）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def env_path():
    """用户可改层 .env 的路径（默认 <仓库根>\\.env；AA_ENV / AA_ENV_PATH 可覆盖，测试隔离用）。"""
    p = os.environ.get("AA_ENV") or os.environ.get("AA_ENV_PATH")
    return p if p else os.path.join(repo_root(), ".env")


def fallback_paths():
    """只读兜底路径（生成者本机的 hermes 档案 .env）—— 打包给别人后这两处不存在，自动跳过。"""
    return (os.path.join("F:", os.sep, "hermes", "profiles", "translation", ".env"),
            os.path.join("F:", os.sep, "hermes", ".env"))


def read_paths():
    """按优先级返回 key 查找路径：仓库根 .env → hermes 翻译档案 → hermes 主。"""
    return (env_path(),) + fallback_paths()


# ---------------------------------------------------------------- 解析 / 读

def _parse(text):
    """`KEY=value` 文本 → {KEY: value}（跳过注释/空行；去引号；后出现的同名键覆盖前者）。"""
    out = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith(";"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        out[key] = val
    return out


def _read_text(path):
    with open(path, "rb") as f:
        raw = f.read()
    return raw.decode("utf-8-sig", errors="replace")


def load(force=False):
    """仓库根 .env 的解析结果（按 (mtime, size) 缓存；文件不存在返回 {}）。"""
    p = env_path()
    try:
        st = os.stat(p)
        sig = (p, st.st_mtime_ns, st.st_size)
    except OSError:
        sig = (p, None, None)
    if not force and _CACHE["sig"] == sig:
        return _CACHE["data"]
    data = {}
    if sig[1] is not None:
        try:
            data = _parse(_read_text(p))
        except OSError:
            data = {}
    _CACHE["sig"] = sig
    _CACHE["data"] = data
    return data


def load_dotenv(force=False):
    """把 .env 的值灌进 os.environ（不覆盖已存在的真实 env 与非空值）。

    返回本次新设的键数。可重复调用（幂等）；`force=True` 时用文件值覆盖进程内旧值，
    并把「上次是从 .env 灌进来、这次文件里已删掉」的键从 os.environ 摘回去
    —— 设置页保存后调它即让本进程立刻吃到新配置（含删除）。
    """
    data = load(force=True)
    eligible = {k: v for k, v in data.items()
                if k.startswith("AA_") or k in ENV_EXTRA}
    n = 0
    with _LOCK:
        if force:
            for k in list(_LOADED - set(eligible)):
                os.environ.pop(k, None)
                _LOADED.discard(k)
            for k, v in eligible.items():
                if os.environ.get(k) != v:
                    os.environ[k] = v
                    n += 1
                _LOADED.add(k)
            return n
        for k, v in eligible.items():
            if not os.environ.get(k):
                os.environ[k] = v
                _LOADED.add(k)
                n += 1
    return n


def get(name, default=None):
    """按优先级取单个值：真实/进程 env → 仓库根 .env → hermes 兜底 → default。"""
    v = os.environ.get(name)
    if v:
        return v
    for p in read_paths():
        try:
            if not os.path.exists(p):
                continue
            fv = _parse(_read_text(p)).get(name)
        except OSError:
            continue
        if fv:
            return fv
    return default


def read_key(name, paths=None):
    """按路径顺序查 key 值（env 优先，其次各 .env 文件）；查不到返回 None。

    路径顺序 = 仓库根 .env → hermes 翻译档案 → hermes 主（打包给别人后 hermes 不存在，自动跳过）。
    """
    v = os.environ.get(name)
    if v:
        return v
    for p in (paths or read_paths()):
        try:
            if not os.path.exists(p):
                continue
            fv = _parse(_read_text(p)).get(name)
        except OSError:
            continue
        if fv:
            return fv
    return None


def has_key(name, paths=None):
    return bool(read_key(name, paths))


def mask(value):
    """key 的展示形态：只留尾 4 位（防截屏/转发泄露）。未配置返回空串。"""
    v = (value or "").strip()
    if not v:
        return ""
    tail = v[-4:] if len(v) > 4 else ""
    return ("…" + tail) if tail else "…"


# ---------------------------------------------------------------- 写

def write_values(updates, deletes=(), path=None):
    """行级更新 .env（原子写）：返回实际改动的键名列表。

    * `updates`: {KEY: value} —— 已存在的键**原地替换**那一行，否则追加到末尾；值里禁换行。
    * `deletes`: 要删除的键名（整行删掉）。
    * 未被提及的行（含注释/空行）**逐字节保留**；文件不存在的行尾风格按 LF 新建。
    """
    p = path or env_path()
    clean = {}
    for k, v in (updates or {}).items():
        if not _KEY_RE.match(k or ""):
            raise ValueError("非法键名: %r" % (k,))
        s = "" if v is None else str(v)
        if "\n" in s or "\r" in s:
            raise ValueError("值里不允许换行: %s" % k)
        clean[k] = s.strip()
    dels = {d for d in (deletes or ()) if d}

    with _LOCK:
        if os.path.exists(p):
            text = _read_text(p)
            nl = "\r\n" if "\r\n" in text else "\n"
            lines = text.splitlines()
        else:
            nl, lines = "\n", []
        changed, seen = [], set()
        out = []
        for line in lines:
            m = _LINE_RE.match(line)
            key = m.group(1) if m else None
            if key and key in dels:
                changed.append(key)
                continue
            if key and key in clean:
                seen.add(key)
                if key not in changed:
                    changed.append(key)
                out.append("%s=%s" % (key, clean[key]))
                continue
            out.append(line)
        for k, v in clean.items():
            if k in seen or k in dels:
                continue
            if k not in changed:
                changed.append(k)
            out.append("%s=%s" % (k, v))
        body = nl.join(out)
        if out:
            body += nl
        tmp = p + ".tmp"
        with open(tmp, "wb") as f:
            f.write(body.encode("utf-8"))
        os.replace(tmp, p)
        _CACHE["sig"] = None
        load(force=True)
    if changed:
        load_dotenv(force=True)
    return changed
