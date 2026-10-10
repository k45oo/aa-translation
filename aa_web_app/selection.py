# -*- coding: utf-8 -*-
"""划词翻译 + 朗读 TTS（2026-09-03；2026-09-20 强化）。

翻译（统一日→中）:
  deepseek/deepseek-flash 优先 → deepinfra/DSV4-Flash → runinfra/glm-5-3 兜底（见 _do_translate）。
  传 work/base 时注入 aa_translate.glossary_prompt_hint(base):
  术语表按章节语料过滤 + 作品知识库(角色/语境/章节简介) —— 与整章翻译同口径。
  记账: usage.jsonl(tag=selection_translate) + aa_web_app/data/selection_log.jsonl
  （每次划词的 通道/耗时/是否照抄/force —— 「划词超时」靠它反查，usage.jsonl 只有 token 无耗时）。

TTS（朗读原文）:
  edge-tts ja-JP-NanamiNeural, 显式走 v2rayN 代理(127.0.0.1:10808),
  音频按 (voice|text) md5 缓存 aa_web_app/data/tts_cache/<hash>.mp3, 重复朗读零请求。

并发: 全程 _LOCK 串行化(env AA_FREE_MODE/AA_USAGE_TAG + aa_translate WORK_ID 是
进程级全局, 浏览器多标签同时划词会互踩; 划词是低频人工操作, 串行无感)。

2026-09-20 三项收敛（用户实测「译文仍是日文原文」+「等很久」）:
  ① 超时收敛: 单候选 timeout 120s→_CALL_TIMEOUT(30s) + 候选内重试 3→_CALL_RETRY(1)
     （原先 3 候选 × 3 重试 × 120s ≈ 最坏 18 分钟）。
  ② 照抄检测补齐: 旧判据只认「源文 ≥8 连续假名出现在译文」，短句（AA 台词多为 3~7 假名）
     即使模型整句照抄也判为正常 → 弹窗直接显示日文。现改最长公共假名子串判据（见 _looks_echo）。
  ③ 强制翻译: 全候选照抄时自动再跑一轮「换通道 + 强化指令」(force)，仍照抄才返回并标
     echo=True（前端给黄色提示 + 「重译」按钮）；force=True 也可由前端按钮显式触发。
"""
import asyncio
import hashlib
import json
import os
import re
import sys
import threading
import time

_DIR = os.path.dirname(os.path.abspath(__file__))       # aa_web_app/
_ROOT = os.path.dirname(_DIR)                            # 仓库根
_SCRIPTS = os.path.join(_ROOT, "scripts")
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common"),
           os.path.join(_SCRIPTS, "works"), os.path.join(_SCRIPTS, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 数据根（2026-10-09 方案 §5）：朗读缓存/划词日志是可写状态，落数据根。
import paths as _paths
_DATA = _paths.BASE

_LOCK = threading.Lock()
_LOG_LOCK = threading.Lock()

# 默认划词链（条目 = 后端+模型；2026-09-13 用户指定的三通道）——`AA_SEL_CHAIN` 未设时用它，
# 行为与旧版逐字节一致（2026-10-08 阶段 3 起可配，见方案 §3 Q4）。
DEFAULT_SEL_ENTRIES = (
    ("deepseek", "deepseek-flash"),
    ("deepinfra", "deepseek-ai/DeepSeek-V4-Flash-0731"),
    ("runinfra", "glm-5-3-flash"),
)

MAX_SEL_LEN = 500        # 单次划词翻译上限（与前端一致）
TTS_MAX_LEN = 400        # 单次朗读上限
_TTS_VOICE = os.environ.get("AA_TTS_VOICE", "ja-JP-NanamiNeural")
_TTS_VOLUME = os.environ.get("AA_TTS_VOLUME", "-50%")   # 2026-09-03: 音量减半
def _tts_proxy():
    """朗读走的代理：AA_TTS_PROXY > AA_PROXY > TOML > 默认（调用时现取，改设置无需重启）。"""
    v = os.environ.get("AA_TTS_PROXY")
    if v:
        return v
    try:
        import aa_config
        return aa_config.proxy_url()
    except Exception:
        return "http://127.0.0.1:10808"
_TTS_DIR = os.path.join(_DATA, "aa_web_app", "data", "tts_cache")


def _env_int(name, default):
    try:
        return int(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


def _env_get(name, default=None):
    """按 真实 env → 仓库根 .env → hermes 兜底 取值（aa_env 统一口径）。"""
    try:
        import aa_env
        return aa_env.get(name, default)
    except Exception:
        return os.environ.get(name, default)


def sel_enabled():
    """划词翻译 + 朗读总开关（`AA_SEL_ENABLE`，默认 1）。关 = 前端不挂监听 + 后端拒（双端硬关）。"""
    v = str(_env_get("AA_SEL_ENABLE", "1") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def sel_chain():
    """本次划词候选链 `[(backend, model_or_None)]`。

    `AA_SEL_CHAIN`（设置页「划词」区块写）接受**链条目 id**（`backend|model`）或裸后端名
    （裸名 = 用该后端默认模型，与整章链同口径）；未设/解析空 → `DEFAULT_SEL_ENTRIES`。
    """
    raw = _env_get("AA_SEL_CHAIN", "")
    out = []
    for part in str(raw or "").split(","):
        s = part.strip()
        if not s:
            continue
        if "|" in s:
            b, m = s.split("|", 1)
            b, m = b.strip().lower(), m.strip()
        else:
            b, m = s.lower(), ""
        if not b or (b, m or None) in out:
            continue
        out.append((b, m or None))
    if out:
        return tuple(out)
    try:                                    # 默认 = TOML [selection].backends（2026-10-08 阶段 3）
        import aa_config
        return tuple(aa_config.selection_defaults())
    except Exception:
        return DEFAULT_SEL_ENTRIES


def _backend_default_model(b):
    """后端默认模型（展示名用；取不到就回后端名）。"""
    try:
        import aa_config
        return aa_config.backend(b).get("model") or b
    except Exception:
        return b


def _call_fn(b):
    """后端名 → 划词用的文本调用函数（None = 未支持的后端）。

    ⚠️ 设置页「划词链」的候选来自 `aa_config.selection_catalog()`（两条付费链条目 ∪ 划词默认链）——
    **付费链上有的后端这里都要有**，缺一个就会在 `_sel_candidates` 里被静默跳过（用户看到的是
    「永远在用第二候选」，2026-10-10 就是 opencode_go 缺这一行：链首明明配了它，实际每次落 mimo）。
    """
    import aa_backend as AB
    return {
        "deepseek": AB.call_deepseek, "deepinfra": AB.call_deepinfra,
        "runinfra": AB.call_runinfra, "mimo": AB.call_mimo,
        "opencode_go": AB.call_opencode_go,
        "commandcode": AB.call_commandcode, "siliconflow": AB.call_siliconflow,
        "dashscope": AB.call_dashscope, "minimax": AB.call_minimax,
        "openrouter": AB.call_openrouter,
    }.get(b)


def _invoke(fn, system, text, model):
    """调用一个候选后端。

    优先带 `retries=`（2026-09-20 超时收敛引入，2026-10-10 opencode_go 也补上该参数）；
    仍没有该参数的后端函数 → TypeError（消息含 retries）时回退为不带调用。
    """
    try:
        return fn(system, text, max_tokens=2048, timeout=_CALL_TIMEOUT,
                  model=model, retries=_CALL_RETRY)
    except TypeError as e:
        if "retries" not in str(e):
            raise
        return fn(system, text, max_tokens=2048, timeout=_CALL_TIMEOUT, model=model)


def _sel_candidates(avoid=None):
    """候选列表 `[(call_fn, model, 展示名)]`（按 sel_chain() 顺序；avoid 项排到最后）。

    链里出现**未支持的后端名**（`_call_fn` 给不出）→ 打 stderr 警告（否则静默少一个候选，
    症状是「配了链首却每次走第二候选」，2026-10-10 opencode_go 就这么漏的）。
    """
    cands, unknown = [], []
    for b, m in sel_chain():
        fn = _call_fn(b)
        if not fn:
            unknown.append(b)
            continue
        cands.append((fn, m, "%s/%s" % (b, m or _backend_default_model(b))))
    if unknown:
        print("[selection] 划词链里有不支持的后端 %s → 已跳过（候选: %s）"
              % (",".join(unknown), ", ".join(c[2] for c in cands) or "无"),
              file=sys.stderr, flush=True)
    if avoid:
        cands = [c for c in cands if c[2] != avoid] + [c for c in cands if c[2] == avoid]
    return cands


# 单候选单次调用超时 / 候选内自身重试（2026-09-20：120/3 → 30/1）。
# 最坏等待 ≈ 3 候选 × 30s = 90s（原先 3 候选 × 3 次 × 120s + 退避 ≈ 18 分钟）。
_CALL_TIMEOUT = _env_int("AA_SEL_TIMEOUT", 30)
_CALL_RETRY = _env_int("AA_SEL_RETRY", 1)

# 划词专用日志（含耗时/通道/照抄标记）。超 2MB 滚动为 .1（只留一份，人工反查用）。
_SEL_LOG = os.path.join(_DATA, "aa_web_app", "data", "selection_log.jsonl")
_SEL_LOG_MAX = _env_int("AA_SEL_LOG_MAX_BYTES", 2 * 1024 * 1024)

# 用户 2026-09-03 拍板: 整段原样送模型, prompt 不写「已有中文保留不动」
# (写保留不动 → 中文较多时模型可能整段不翻)。纯「译成中文」+完整输出。
_SYSTEM = (
    "你是一个日语→简体中文翻译器。把用户给出的文本整段翻译成简体中文并完整输出。"
    "不要省略任何内容，不要解释，不要输出任何其它文字。"
)
# 艺术护栏（2026-09-13 换 deepseek-flash 后加的，实测必需）：选区常含 ASCII 艺术，
# 无护栏时 deepseek-flash 会把艺术整段丢掉（实测 2/2）、deepinfra 则原样保留。
# 代价：极少数短纯文本句会被护栏带到"照抄原文"，故 _do_translate 有照抄检测 + 去护栏重试。
_ART_GUARD = (
    "原文中的非日语部分（半角/全角符号、空格、括号、图形字符）属于画面排版，"
    "必须原样保留、不得翻译或改写，只翻译其中的日语文字。"
)
# 强制重译提示（force 轮）：只加提示不动护栏，艺术部分仍照旧保留。
_FORCE_HINT = (
    "上一次回答直接输出了日语原文，这是错误的。这次必须输出简体中文译文："
    "除了作品专有人名可以保留日文，其他地方禁止出现日文假名。"
)
_KANA_RE = re.compile(r"[\u3041-\u309f\u30a0-\u30ff\uff66-\uff9f]")

# 照抄判据（见 _looks_echo）：长句看 8 连串（兼容旧行为），短句看最长公共假名子串占比。
_ECHO_RUN = 8            # 源文假名 ≥8：译文出现 8 连串 → 照抄（保留的专名只有 2~4 个假名）
_ECHO_MIN = 3            # 短句：公共假名串 ≥3 且 ≥源文假名 60% → 照抄
_ECHO_RATIO = 0.6


def _kana_str(s):
    return "".join(_KANA_RE.findall(s or ""))


def _lcs_len(a, b):
    """最长公共子串长度（划词文本 ≤500 字，DP 足够快）。"""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for ch in a:
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if ch == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def _looks_echo(src, out, allow=()):
    """译文是否把原文（假名部分）照抄了回来。

    2026-09-20 修：旧实现只在源文假名 ≥8 时检测，短句（AA 台词多为 3~7 假名）照抄
    一律判为"正常译文" → 弹窗直接显示日文原文（用户实测投诉）。
    现判据：扣掉白名单专名（allow，如 やる夫/ドラえもん 的假名部分）后取最长公共假名子串 L，
      · 源文假名 ≥8: L ≥ 8（等价旧行为）
      · 源文假名 <8: L ≥ 3 且 L ≥ 源文假名数 × 0.6
    """
    ks = _kana_str(src)
    ko = _kana_str(out)
    if not ks or not ko:
        return False
    for a in (allow or ()):
        if a:
            ko = ko.replace(a, "")
    if not ko:
        return False
    L = _lcs_len(ks, ko)
    if len(ks) >= _ECHO_RUN:
        return L >= _ECHO_RUN
    return L >= _ECHO_MIN and L >= len(ks) * _ECHO_RATIO


# 照抄白名单（专名假名）缓存：{work_id: [假名串]}
_ALLOW_CACHE = {}


def _allow_kana(work):
    """译文里允许保留的日文专名假名串（KNOWN_NAMES + 本作品术语表 jp 侧）。

    不扣掉这些 → 正常译文里的「やる夫」会被判成照抄。取不到就返回空（退回严格判定）。
    """
    key = work or ""
    if key in _ALLOW_CACHE:
        return _ALLOW_CACHE[key]
    names = set()
    try:
        at = _get_flat()
        for n in (getattr(at, "KNOWN_NAMES", None) or ()):
            names.add(n)
        # 作品术语表 jp 侧（依赖 _build_prompt 已设好 WORK_ID/AA_DIR/WORK_DIR 全局）
        try:
            for jp, _zh in (at._load_glossary_pairs(None) or []):
                names.add(jp)
        except BaseException:
            pass
    except BaseException:
        pass
    out = [k for k in (_kana_str(n) for n in names) if k]
    _ALLOW_CACHE[key] = out
    return out


def _log_sel(rec):
    """划词记账（耗时/通道/照抄）。失败静默——记账绝不影响翻译。"""
    try:
        os.makedirs(os.path.dirname(_SEL_LOG), exist_ok=True)
        with _LOG_LOCK:
            if os.path.exists(_SEL_LOG) and os.path.getsize(_SEL_LOG) > _SEL_LOG_MAX:
                try:
                    os.replace(_SEL_LOG, _SEL_LOG + ".1")
                except OSError:
                    pass
            with open(_SEL_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


# aa_translate(scripts/ 平铺真实现) 惰性加载: 模块 import 会改 sys.path, 首次调用才触发
_AT = None


def _get_flat():
    global _AT
    if _AT is None:
        import aa_translate as _m
        _AT = _m
    return _AT


def _build_prompt(work, base):
    """术语注入: work 存在时按章节过滤(术语表+知识库+章节简介)。board 页无 work → 纯裸翻。"""
    if not work:
        return _SYSTEM + _ART_GUARD
    try:
        at = _get_flat()
        import registry as _R
        cfg = _R.get(work)
        # 与 registry 模式一致地设置作品上下文（glossary_prompt_hint 读模块全局）
        at.WORK_ID = work
        at.AA_DIR = cfg["aa_dir"]
        at.OUT_DIR = cfg["out_dir"]
        at.WORK_DIR = cfg["work_dir"]
        hint = at.glossary_prompt_hint(base or None)
        return _SYSTEM + _ART_GUARD + (hint or "")
    except BaseException as _e:
        # 必须 BaseException 而非 Exception: 作品术语表缺失时 aa_translate 按「翻译硬前置」
        # raise SystemExit —— except Exception 接不住 → SystemExit 逃逸请求线程 → uvicorn 进程
        # 整体死亡(2026-09-08 实测: 无术语表作品划词 500 + 后台崩)。
        # 划词是临时辅助翻译, 术语注入失败一律裸翻兜底, 绝不允许杀死服务。
        if not isinstance(_e, SystemExit):
            import traceback as _tb
            _tb.print_exc()
        return _SYSTEM + _ART_GUARD   # 术语注入失败不阻塞翻译（裸翻兜底）


def translate_text(text, work=None, base=None, force=False, avoid=None):
    """划词翻译入口。返回 {"text","via","elapsed","echo"}。

    force=True: 前端「重译」按钮 —— 有非空结果就返回（跳过照抄检测）+ 强化指令 + avoid 通道排后。
    非 force: 若全候选都照抄，自动再跑一轮 force 语义（换通道重试），仍照抄才带 echo=True 返回。
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("空文本")
    if len(text) > MAX_SEL_LEN:
        raise ValueError(f"单次最多 {MAX_SEL_LEN} 字（当前 {len(text)}）")
    system = _build_prompt(work, base)
    allow = _allow_kana(work)
    t0 = time.time()
    with _LOCK:
        try:
            res = _do_translate(system, text, force=force, avoid=avoid, allow=allow)
            if res["echo"] and not force:
                # 自动强制重译一轮：换通道 + 强化指令（用户实测「仍显示原文」的自愈路径）
                try:
                    res2 = _do_translate(system, text, force=True, avoid=res["via"], allow=allow)
                    res2["elapsed"] = round(res["elapsed"] + res2["elapsed"], 2)
                    res2["auto_retry"] = True
                    res = res2
                except RuntimeError:
                    pass
        except RuntimeError as e:
            _log_sel({"ts": round(time.time(), 3), "chars": len(text), "ok": False,
                      "err": str(e)[:200], "elapsed": round(time.time() - t0, 2),
                      "force": bool(force), "avoid": avoid or ""})
            raise
        _log_sel({"ts": round(time.time(), 3), "chars": len(text), "ok": True,
                  "via": res.get("via"), "elapsed": res.get("elapsed"),
                  "echo": bool(res.get("echo")), "auto_retry": bool(res.get("auto_retry")),
                  "total_elapsed": round(time.time() - t0, 2),
                  "force": bool(force), "avoid": avoid or ""})
        return res


def _do_translate(system, text, force=False, avoid=None, allow=()):
    """划词翻译通道 = `AA_SEL_CHAIN`（设置页可配，条目 id）> 默认三通道（2026-09-13 用户指定）：
    deepseek 官网 `deepseek-flash`(V4.1-Flash，思考已关 reasoning_effort=none → 0.6~1.1s，$0.14/$0.56) 优先
    → deepinfra `DeepSeek-V4-Flash-0731`($0.09/M 最便宜, 2s, 艺术字符保持最稳) 兜底
    → runinfra `glm-5-3-flash`($0.2/M, 术语遵循强) 二兜底。
    划词是纯文本翻译(无 AA 图形列位要求), 不走整章付费链 call_with_fallback。

    每候选先带护栏（保艺术）→ 照抄则去护栏重试（保译文），两轮都不行降级下一候选。
    force=True: avoid 通道排到最后（避免同一模型给出同样结果）+ 追加 _FORCE_HINT +
                有非空结果就接受（包括照抄，绝不因检测而报错）。
    返回 {"text","via","elapsed","echo"}；全部候选都拿不到非空文本才 raise RuntimeError。
    """
    import aa_backend as AB
    _old_tag = os.environ.get("AA_USAGE_TAG")
    os.environ["AA_USAGE_TAG"] = "selection_translate"
    last_err = None
    t0 = time.time()
    try:
        # 候选链 = AA_SEL_CHAIN（设置页可配）> 默认三通道；模型随链（model=None = 后端默认）
        cands = _sel_candidates(avoid)
        if not cands:
            raise RuntimeError("划词链里没有可用的后端（检查设置页「划词」区块的 AA_SEL_CHAIN）")
        base_sys = (system + _FORCE_HINT) if force else system
        sys_ng = base_sys.replace(_ART_GUARD, "") if _ART_GUARD in base_sys else None
        first_echo = None
        for fn, model, name in cands:
            for sysp in ([base_sys, sys_ng] if sys_ng else [base_sys]):
                try:
                    txt = _invoke(fn, sysp, text, model)
                except Exception as e:
                    last_err = e
                    break                       # 后端故障 → 下一候选（重试同一后端无意义）
                if txt and txt.strip() and not _looks_echo(text, txt, allow):
                    return {"text": txt.strip(), "via": name,
                            "elapsed": round(time.time() - t0, 2), "echo": False}
                if txt and txt.strip():
                    if first_echo is None:
                        first_echo = (txt.strip(), name)
                    last_err = RuntimeError(f"{name} 照抄原文: {txt[:40]!r}")
                else:
                    last_err = RuntimeError(f"{name} 空响应")
        if first_echo is not None:
            # 全通道照抄：接受（宁可给原文也不报错），echo 标记交调用方决定是否自动重译
            txt, name = first_echo
            return {"text": txt, "via": name,
                    "elapsed": round(time.time() - t0, 2), "echo": True}
        raise RuntimeError(f"划词候选链全失败: {last_err}")
    finally:
        if _old_tag is None:
            os.environ.pop("AA_USAGE_TAG", None)
        else:
            os.environ["AA_USAGE_TAG"] = _old_tag


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------

def tts_path(text, voice=None):
    """合成或命中缓存, 返回 mp3 绝对路径。走显式代理。volume 纳入缓存 key。"""
    voice = voice or _TTS_VOICE
    text = (text or "").strip()
    if not text:
        raise ValueError("空文本")
    if len(text) > TTS_MAX_LEN:
        raise ValueError(f"朗读文本过长（≤{TTS_MAX_LEN} 字，当前 {len(text)}）")
    os.makedirs(_TTS_DIR, exist_ok=True)
    key = hashlib.md5((voice + "|" + _TTS_VOLUME + "|" + text).encode("utf-8")).hexdigest()[:16]
    path = os.path.join(_TTS_DIR, key + ".mp3")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    with _LOCK:
        # 二次检查（并发同文本只合成一次）
        if not (os.path.exists(path) and os.path.getsize(path) > 0):
            _synth(text, voice, path)
    return path


async def _synth_async(text, voice, path):
    from edge_tts import Communicate
    # 2026-09-03: volume=-50% 音量减半（朗读太响）
    com = Communicate(text, voice, volume="-50%", proxy=_tts_proxy(),
                      connect_timeout=15, receive_timeout=60)
    with open(path, "wb") as f:
        async for chunk in com.stream():
            if chunk.get("type") == "audio" and chunk.get("data"):
                f.write(chunk["data"])


def _synth(text, voice, path):
    try:
        asyncio.run(_synth_async(text, voice, path))
    except Exception as e:
        # 失败清理半成品, 明确报错(代理未开/断网最常见)
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
        raise RuntimeError(f"edge-tts 合成失败: {e}（v2rayN 代理 {_tts_proxy()} 是否运行？）") from e
    if not (os.path.exists(path) and os.path.getsize(path) > 0):
        raise RuntimeError("edge-tts 合成结果为空")
