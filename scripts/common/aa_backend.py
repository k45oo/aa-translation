# -*- coding: utf-8 -*-
"""模型后端分发器：统一配置（scripts/config/model_config.toml）驱动。

后端切换（AA_BACKEND 环境变量）：
- 未设 / cloud → 配置 [default].backend 指定的后端（默认 commandcode）
- siliconflow → https://api.siliconflow.cn/v1/chat/completions
  - 翻译/残留检测: THUDM/GLM-4-9B-0414（免费，实测 1.9s 极快、低温 t=0.1 质量稳定）
  - 图片文字识别: deepseek-ai/DeepSeek-OCR（实测 2.8s；对 AA 字符画是幻觉，仅适用自然文字图）
  - 必须 enable_thinking=false（siliconflow 官方思考关闭参数, 2026-08-31 起统一)
- qwen-vl → SiliconFlow Qwen3-VL（视觉+文本双用，无 R18 审查）
- minimax / mimo / runinfra / openrouter / ollama(已停用) 见 config

所有配置项优先级: 环境变量(AA_*) > model_config.toml > 代码默认值。
key 从仓库根 .env 读（唯一来源；真实环境变量优先，2026-10-10 起无 hermes 兜底）。
"""
import json, os, time, urllib.request, urllib.error, socket, threading

# 2026-09-01: 全局 socket 默认超时兜底——urllib urlopen(timeout=N) 对 CLOSE_WAIT/半开连接
# （对端已 FIN 本地未感知）读操作可能永不超时 → 请求无限挂 → worker 占死 → 免费链假死
# （akari_series/bar_miku 反复假死根因之一）。setdefaulttimeout 让所有未显式传 timeout
# 的 socket 操作也受上限约束；显式 timeout= 的 urlopen 仍优先用各自值。
socket.setdefaulttimeout(300)

import aa_config  # 同目录加载器（sys.path 已含 common/）
import aa_env     # 仓库根 .env 统一读写（2026-10-08 阶段 1；与 aa_config 同一优先级口径）

# 2026-08-20: call_* 后端函数 usage 落盘（复用 aa_config 的 [usage].file 路径）
# 背景: auto_backend 设 AA_BACKEND 后翻译走 call_*, 而 _log_usage 只在 cloud 直接模式调用
# → 各 call_* 成功时补记, 保证 usage.jsonl 统计完整

# 2026-09-04: 模型↔行号映射——翻译层每次调用前 set 本次翻的行号列表,
#   _log_usage 落盘时带上 → usage.jsonl 每行含 lines, 供后期按模型比对质量。
#   thread-local: 多 worker 线程各占一块, 互不污染。
_USAGE_CTX = threading.local()

def set_usage_lines(lines):
    """翻译层在每次模型调用前设置本次翻译的行号列表(用于 usage 落盘 lines 字段)。"""
    _USAGE_CTX.lines = lines

def clear_usage_lines():
    _USAGE_CTX.lines = None

def _pick(d, *paths):
    """按路径顺序取第一个**非 None** 值（0 是有效值——"命中 0" 与 "不支持" 必须区分）。"""
    for p in paths:
        cur = d
        for k in p:
            if not isinstance(cur, dict):
                cur = None
                break
            cur = cur.get(k)
        if cur is not None:
            return cur
    return None


def _usage_extra(u):
    """从响应 usage 取 (缓存命中输入 token, 思考 token)——跨厂商字段名差异。

    2026-09-15 成本口径 P0（doc/方案归档/token成本优化方案_2026-09-15.md）: 取不到返回 None,
    用于区分「该端点不提供该字段」与「命中 0 / 思考 0」——前者不能按 0 计费, 后者可以。
    · DeepSeek chat/completions: prompt_cache_hit_tokens
    · OpenAI 兼容(deepinfra/runinfra/commandcode/siliconflow): prompt_tokens_details.cached_tokens
    · Responses(官网/OpenAI): input_tokens_details.cached_tokens / output_tokens_details.reasoning_tokens
    """
    if not isinstance(u, dict):
        return None, None
    ch = _pick(u, ("prompt_cache_hit_tokens",),
               ("prompt_tokens_details", "cached_tokens"),
               ("input_tokens_details", "cached_tokens"))
    rt = _pick(u, ("completion_tokens_details", "reasoning_tokens"),
               ("output_tokens_details", "reasoning_tokens"),
               ("reasoning_tokens",))
    return ch, rt


def _cost_fields():
    """是否落盘成本字段（cache_hit/reasoning）。AA_USAGE_COST=0 回退旧格式（惰性读取, 便于测试）。"""
    return os.environ.get("AA_USAGE_COST", "1") not in ("0", "false", "False")


def _log_usage(prompt_tokens, completion_tokens, model=None, backend=None, tag=None, u=None):
    try:
        # tag: 显式覆盖(如 refine_aa 校对记 "refine"); 否则 env AA_USAGE_TAG > 配置默认
        tag = tag or os.environ.get("AA_USAGE_TAG") or aa_config.usage_tag()
        rec = {
            "tag": tag, "ts": time.time(),
            "task": os.environ.get("AA_TASK_ID"),
            "prompt": prompt_tokens, "completion": completion_tokens,
            "total": prompt_tokens + completion_tokens,
            "model": model, "backend": backend,
        }
        _ln = getattr(_USAGE_CTX, "lines", None)
        if _ln:
            rec["lines"] = _ln
        # 2026-09-15 P0: 缓存命中输入 + 思考 token（成本口径, 缺省不写; reasoning=0 不写省体积）
        if _cost_fields():
            _ch, _rt = _usage_extra(u)
            if _ch is not None:
                rec["cache_hit"] = _ch
            if _rt:
                rec["reasoning"] = _rt
        # 2026-08-31: 大小限制+滚动（超 10MB 归档 .1/.2/.3, 防止 usage.jsonl 无限增长）
        from aa_usage import append_usage
        append_usage(rec)
    except Exception:
        pass

# ---- 由统一配置驱动（aa_config 惰性读取） ----
def _backend_cfg(name):
    return aa_config.backend(name)
def _url(name, default=""):
    return _backend_cfg(name).get("url", default) or default

def _model(name, default=""):
    return _backend_cfg(name).get("model", default) or default

# 保持旧符号（旧脚本可能 import 这些常量；值从配置来）
SF_URL = _url("siliconflow", "https://api.siliconflow.cn/v1/chat/completions")
SF_MODEL = os.environ.get("AA_SF_MODEL") or _model("siliconflow", "THUDM/GLM-4-9B-0414")
SF_OCR_MODEL = _backend_cfg("siliconflow").get("ocr_model", "deepseek-ai/DeepSeek-OCR")
DSC_URL = _url("dashscope", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
DSC_MODEL = os.environ.get("AA_QS_MODEL") or _model("dashscope", "qwen3.5-27b")
QWEN_VL_URL = _url("qwen-vl", SF_URL)
QWEN_VL_MODEL = os.environ.get("AA_QWEN_VL_MODEL") or _model("qwen-vl", "Qwen/Qwen3-VL-32B-Instruct")
QWEN_VL_8B = _backend_cfg("qwen-vl").get("qwen_vl_8b", "Qwen/Qwen3-VL-8B-Instruct")
QWEN_VL_30B_A3B = _backend_cfg("qwen-vl").get("qwen_vl_30b", "Qwen/Qwen3-VL-30B-A3B-Instruct")
OR_URL = _url("openrouter", "https://openrouter.ai/api/v1/chat/completions")
OR_MODEL = os.environ.get("AA_OR_MODEL") or _model("openrouter", "google/gemma-4-26b-a4b-it:free")
MM_URL = _url("minimax", "https://api.minimaxi.com/v1/chat/completions")
MM_MODEL = os.environ.get("AA_MM_MODEL") or _model("minimax", "MiniMax-M3")
# 2026-09-29: 小米官网 BASE_URL 变动——mimo 改走 **token plan 专用链接**(token-plan-cn, key=XIAOMI_API_KEY
#   形如 tp-xxx)。实测两套 key/端点互不通用(旧 key 在新链接 401, tp key 在旧链接 401); MIMO 是 normal 链首
#   且命中前缀缓存, 切过去后翻译+校对消耗落进套餐(每日 00:00-08:00 系数 0.8×, 见 [pricing.rates].off)。
#   回滚: 改 TOML [backend.mimo].url/key_env, 或临时 AA_MIMO_URL=<旧链接> AA_MIMO_KEY_ENV=XIAOMI_MIMO_API_KEY。
MIMO_URL = os.environ.get("AA_MIMO_URL") or _url("mimo", "https://token-plan-cn.xiaomimimo.com/v1/chat/completions")
MIMO_MODEL = os.environ.get("AA_MIMO_MODEL") or _model("mimo", "mimo-v2.6-flash")
RI_URL = _url("runinfra", "https://api.runinfra.ai/v1/chat/completions")
RI_MODEL = os.environ.get("AA_RI_MODEL") or _model("runinfra", "deepseek-v4-flash")
CC_URL = _url("commandcode", "https://api.commandcode.ai/provider/v1/chat/completions")
CC_MODEL = os.environ.get("AA_CC_MODEL") or _model("commandcode", "xiaomi/mimo-v2.5")
CC_FALLBACK_MODEL = os.environ.get("AA_CC_FALLBACK") or _backend_cfg("commandcode").get(
    "fallback_model", "meta/muse-spark-1.3-contributor")
CC_UA = _backend_cfg("commandcode").get(
    "ua", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
OLLAMA_URL = os.environ.get("AA_OLLAMA_URL") or _url("ollama", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("AA_OLLAMA_MODEL") or _model("ollama", "llama3.1:8b-instruct-q4_K_M")
# deepseek 官网(Responses 端点) / deepinfra(OpenAI 兼容 chat) —— 2026-08-22 commandcode 周限额临时替换
DS_URL = os.environ.get("AA_DS_URL") or _url("deepseek", "https://api.deepseek.com/responses")
DS_MODEL = os.environ.get("AA_DS_MODEL") or _model("deepseek", "deepseek-flash")
DI_URL = os.environ.get("AA_DI_URL") or _url("deepinfra", "https://api.deepinfra.com/v1/openai/chat/completions")
DI_MODEL = os.environ.get("AA_DI_MODEL") or _model("deepinfra", "deepseek-ai/DeepSeek-V4.1-Flash")


def _env_file_paths():
    """key 读取的 .env 候选路径（**只有仓库根 .env**，2026-10-10 起无 hermes 兜底）。"""
    return aa_env.read_paths()


def _read_env_key(name, paths=None):
    # 2026-10-08 阶段 1: 统一走 aa_env（真实 env → 仓库根 .env），
    # 与 aa_translate.get_key / aa_config._env_key_exists 同一实现，不再各写一份。
    # 2026-10-10: hermes 档案兜底路径废除 → 只剩仓库根 .env 一个文件来源。
    if paths is None:
        return aa_env.read_key(name)
    return aa_env.read_key(name, paths)


def _sf_key():
    k = _read_env_key("SILICONFLOW_API_KEY", _env_file_paths())
    if k:
        return k
    raise RuntimeError("no SILICONFLOW_API_KEY in 仓库根 .env")


def _sf_free_key():
    """siliconflow 免费模型专用 key(独立配额)。2026-08-29 用户新增。"""
    k = _read_env_key("SILICONFLOW_FREE_API_KEY", _env_file_paths())
    if k:
        return k
    return _sf_key()  # 未设则回退主 key


def _or_key():
    k = _read_env_key("OPENROUTER_API_KEY", _env_file_paths())
    if k:
        return k
    raise RuntimeError("no OPENROUTER_API_KEY in 仓库根 .env")




def _mm_key():
    """MiniMax key 从仓库根 .env 读（唯一来源）。"""
    k = _read_env_key("MINIMAX_CN_API_KEY", _env_file_paths())
    if k:
        return k
    raise RuntimeError("no MINIMAX_CN_API_KEY in 仓库根 .env")


def _mimo_key():
    """mimo 的 key。

    2026-09-29: 端点切 token plan 后，key 变量名由 `[backend.mimo].key_env` 决定（现 = `XIAOMI_API_KEY`，
    形如 `tp-xxx`）；`AA_MIMO_KEY_ENV` 可临时改读别的变量名（回滚用 `XIAOMI_MIMO_API_KEY`）。
    指定的名字读不到再兜底旧按量 key 名，最后才抛错——避免「换了 TOML 但 .env 还没建 tp key」时直接崩。
    """
    paths = _env_file_paths()
    name = (os.environ.get("AA_MIMO_KEY_ENV")
            or _backend_cfg("mimo").get("key_env") or "XIAOMI_MIMO_API_KEY")
    for n in (name, "XIAOMI_MIMO_API_KEY", "XIAOMI_API_KEY"):
        k = _read_env_key(n, paths)
        if k:
            return k
    raise RuntimeError(f"no {name} in 仓库根 .env")


def _ri_key():
    k = _read_env_key("RUNINFRA_API_KEY", _env_file_paths())
    if k:
        return k
    raise RuntimeError("no RUNINFRA_API_KEY in 仓库根 .env")


def _dashscope_key():
    k = _read_env_key("QWEN_API_KEY", _env_file_paths())
    if k:
        return k
    raise RuntimeError("no QWEN_API_KEY in 仓库根 .env")


def _cc_key():
    k = _read_env_key("COMMANDCODE_API_KEY", _env_file_paths())
    if k:
        return k
    raise RuntimeError("no COMMANDCODE_API_KEY in 仓库根 .env")


def _deepseek_key():
    k = _read_env_key("DEEPSEEK_API_KEY", _env_file_paths())
    if k:
        return k
    raise RuntimeError("no DEEPSEEK_API_KEY in 仓库根 .env")


def _deepinfra_key():
    k = _read_env_key("DEEPINFRA_API_KEY", _env_file_paths())
    if k:
        return k
    raise RuntimeError("no DEEPINFRA_API_KEY in 仓库根 .env")


def backend():
    v = os.environ.get("AA_BACKEND")
    if v:
        vl = v.lower()
        if vl in ("siliconflow", "sf", "silicon", "限免"):
            return "siliconflow"
        if vl in ("qwen-vl", "vl", "qwen3-vl", "qwen-vl-32b", "silicon-vl"):
            return "qwen-vl"
        if vl in ("openrouter", "or", "or_free"):
            return "openrouter"
        if vl in ("minimax", "mm", "minimax-cn"):
            return "minimax"
        if vl in ("mimo", "xiaomi", "mimo-cn"):
            return "mimo"
        if vl in ("runinfra", "ri", "run-infra"):
            return "runinfra"
        if vl in ("commandcode", "cc"):
            return "commandcode"
        if vl in ("ollama", "local", "1", "true"):
            return "ollama"
        if vl in ("deepseek", "ds", "deepseek-official", "官网"):
            return "deepseek"
        if vl in ("deepinfra", "di"):
            return "deepinfra"
        if vl in ("dashscope", "ds2", "aliyun", "qwen-cn", "阿里"):
            return "dashscope"
        # 2026-10-11: opencode 两门（Go 门 / Zen 门）——**漏登记 = 静默回落云端直连**：
        #   AA_BACKEND=opencode_go 时 backend() 返回 "cloud" → aa_translate.call_api 走
        #   云端直接模式(gateway=[default].gateway=commandcode, model=AA_MODEL)，把
        #   opencode 专属裸模型名 `deepseek-v4.1-flash` 发给 commandcode →
        #   400 unsupported_model、每个块重试 5 次后失败 → **整章静默留日文**
        #   (2026-10-10/11 三个作品 254 个块全灭：供給源/ハーレム大王国/ドスケベナイト)。
        if vl in ("opencode_go", "opencode-go", "ocgo", "opencode go"):
            return "opencode_go"
        if vl in ("opencode_zen", "opencode-zen", "oczen", "opencode zen"):
            return "opencode_zen"
    # 未设 → cloud(原语义: 走 call_api 自带的云端直接模式, gateway/model/key 从配置 [default] 读)
    return "cloud"


# ---- 自动降级重试（2026-08-22）：某个后端 429/incomplete/R18被拒 后自动切下一个候选 ----
import threading as _t

_BACKEND_LOCK = _t.Lock()
# 当前"正在用"的后端名(None=未定, 每次调用现场探测)
_PREFERRED_BACKEND = {"name": None}


def reset_preferred_backend():
    """章边界清空端点记忆（每章开始调用）。

    两个用途:
      1. 2026-08-24: 降级记忆不跨章——low 作品 yes/no 章交替时, 上章 R18 后端降级记忆
         会让 no 章也从 R18 后端开始(浪费 normal 免费后端)。
      2. 2026-09-15 (P1): **章内 pin 会缓存的端点**——清空后本章首个调用钉住
         [backend.*].cache=true 的端点(实测只有官网 deepseek 会命中前缀缓存),
         本章后续调用全部复用它; 跨章不再轮询分散付费负载(网关只作降级/溢出)。
         AA_PIN_CACHE_ENDPOINT=0 / [default].pin_cache_endpoint=false 回退旧轮询。
    """
    with _BACKEND_LOCK:
        _PREFERRED_BACKEND["name"] = None


def _cacheable_endpoint(chain):
    """链上第一个「会命中前缀缓存」的端点名；没有则 None（回落端点轮询）。"""
    for _b, _m in chain:
        try:
            if aa_config.cache_safe(_b):
                return _b
        except Exception:
            pass
    return None


# ---- 付费链端点轮询（2026-09-02: 并行均匀分散到不同端点）----
# 与免费链 _free_round_robin 同构但轮询单位=【端点】: 并发 worker 先让每个端点各占一个,
# 多出的 worker 按端点数均匀轮流; 端点内模型按轮次切换。
# 3并发(sf,cc两端点) = sf1 + cc1 + sf1 —— 不出现前两个 worker 同挤一个端点。
_chain_round = {"i": 0}


def _endpoint_round_chain(chain):
    """健康态端点均匀轮询(2026-09-02 用户纠正): 返回以「本轮该落的 (端点,模型)」
    打头的链。线程安全; 链长<=1 原样返回。
    选择: i % 端点数 选端点, (i // 端点数) % 端点内模型数 选端点内模型。
    例 链=[sf-Qwen, sf-DSV3, cc-muse, cc-mimo]: i=0→sf-Qwen, 1→cc-muse,
    2→sf-DSV3, 3→cc-mimo, 4→sf-Qwen…(3并发=sf,cc,sf; 4并发=sf,cc,sf,cc)
    """
    if len(chain) <= 1:
        return chain
    eps = []
    for b, _ in chain:
        if b not in eps:
            eps.append(b)
    with _BACKEND_LOCK:
        i = _chain_round["i"]
        _chain_round["i"] = i + 1
    ep = eps[i % len(eps)]
    ep_items = [t for t in chain if t[0] == ep]
    pick = ep_items[(i // len(eps)) % len(ep_items)]
    return [pick] + [t for t in chain if t is not pick]



def _model_for_backend(b, config_model=None):
    """后端 model 覆盖优先级: AA_<BACKEND>_MODEL env > 配置链 model > 后端默认。"""
    me = aa_config.model_env(b)
    if me and os.environ.get(me):
        return os.environ[me]
    return config_model


def _call_named(b, system, body, model=None, max_tokens=16384, timeout=180):
    """按后端名调用对应 call_* 函数（model 已含 env 覆盖解析）。"""
    if b == "siliconflow":
        return call_siliconflow(system, body, max_tokens=max_tokens, timeout=timeout, model=model)
    if b == "dashscope":
        return call_dashscope(system, body, max_tokens=max_tokens, timeout=timeout, model=model)
    if b == "openrouter":
        return call_openrouter(system, body, max_tokens=max_tokens, timeout=timeout, model=model)
    if b == "minimax":
        return call_minimax(system, body, max_tokens=max_tokens, timeout=timeout, model=model)
    if b == "mimo":
        return call_mimo(system, body, max_tokens=max_tokens, timeout=timeout, model=model)
    if b == "runinfra":
        return call_runinfra(system, body, max_tokens=max_tokens, timeout=timeout, model=model)
    if b == "qwen-vl":
        return call_qwen_vl(system, body, max_tokens=max_tokens, timeout=timeout, model=model)
    if b == "commandcode":
        return call_commandcode(system, body, max_tokens=max_tokens, timeout=timeout, model=model)
    if b == "ollama":
        return call_ollama(system, body, max_tokens=max_tokens, timeout=timeout, model=model)
    if b == "deepseek":
        return call_deepseek(system, body, max_tokens=max_tokens, timeout=timeout, model=model)
    if b == "deepinfra":
        return call_deepinfra(system, body, max_tokens=max_tokens, timeout=timeout, model=model)
    if b == "opencode_go":
        return call_opencode_go(system, body, max_tokens=max_tokens, timeout=timeout, model=model)
    if b == "opencode_zen":
        return call_opencode_zen(system, body, max_tokens=max_tokens, timeout=timeout, model=model)
    raise RuntimeError(f"未知后端: {b!r}")


def _degrade_reason(e):
    """判断异常是否属于可自动降级(换后端)的失败类型, 返回理由或 None。"""
    s = str(e)
    low = s.lower()
    if "429" in s or "rate" in low or "quota" in low or "limit" in low:
        return "限流/配额(429)"
    if "incomplete" in low or "truncated" in low:
        return "响应截断(incomplete)"
    if "r18" in low and ("审核" in s or "内容" in s or "reject" in low or "被拒" in s):
        return "R18 内容被审核拒绝"
    if "空响应" in s or "empty" in low:
        return "空响应"
    # 2026-08-28: 超时也降级换后端——commandcode 网关超时(网络/排队)换 mimo 可能通;
    #   曾致 yukika_robot 清理阶段崩溃(read operation timed out 3次全超时后 raise)
    if "timed out" in low or "timeout" in low or "超时" in s:
        return "超时(timeout)"
    return None


# ---- 免费模型特殊流程（2026-08-26）----
# 真免费模型(GLM-4-9B: siliconflow 限免最快无过滤),
#             poolside/laguna-s-2.1-free(commandcode, 快),
#             minimax/minimax-m3-free(commandcode, 1M ctx, 间歇503兜底)
# 2026-08-27: stealth/ox-alpha 已从 commandcode 下架(model 列表消失), 移除。
#           同日 opencode zen 免费模型测试加入: mimo-v2.5-free / hy3-free / big-pickle
#           GMI MiniMax-M3/M2.7 测试后未加入——内容安全过滤强(性内容+粗口「この野郎」
#           都被拒, 且拒绝语「言えません」无关键词可检测, 会当正常翻译返回造成漏译)。
#           保留 call_gmi() 供手动/非R18纯良作品用。
# 设计: 全免费链 + 无限重试(429/503/空响应/超时→退避+轮换) + 多模型并发。
# 由调用方控制并发(AA_FREE_WORKERS), 本函数只保证单个请求最终成功或用户中断。
# 每项 (模型名, 后端, key[, max_tokens]): commandcode / siliconflow / opencode_zen
# key: None=主key, "FREE"=对应的 *_FREE_API_KEY(仅免费模型)
# 第 4 元素(可选, 2026-09-15): 该模型专用输出预算——推理型免费模型在链默认
#   max_tokens(16384)下会把预算全烧在思考上导致空 content, 需单独抬高。
# 2026-08-29: commandcode 免费模型(laguna/m3)拆两路 = 主key + freekey, 免费链 6→8(+2)
#             siliconflow GLM-4-9B 再拆一路 = +SILICONFLOW_FREE_API_KEY, 免费链 8→9(+1)
# 2026-09-15: commandcode free key 通道**移除**(用户要求)——COMMANDCODE_FREE_API_KEY 与主 key
#   在免费模型上共用同一日配额(LongCat 100/天 两侧同时 429 同一条消息), 双通道无增益;
#   _cc_free_key() 一并删除, commandcode 免费模型只剩主 key 通道。
# 2026-10-10 免费目录轮换（探针实测 temp/probe_free_models_20260905.py 口径 + 全平台逐个验）:
#   ⚠️ **opencode 免费档整体失效**（12 条全删）——外部客户端（含本项目）已用不了:
#      · zen 门: 403 FreeTierError「OpenCode's free tier can only be used from within OpenCode」
#        （mimo-v2.6-flash-free / ling-3.1-flash-free / nemotron-3.5-lightning-free / step-5-preview-free /
#          jev-1.13-free / big-pickle / nemotron-3-ultra-free 全 403；mimo-v2.5-free 410 ModelDeprecated；
#          ling-3.0-flash-fin-free / space-bunny-free 400；muse-spark-1.2/1.3-contributor-free 403 RegionError）
#      · go 门（带 x-opencode-session）: 同一批 400 server_error（只有 longcat-2.5-preview-free 偶发 200，不稳）
#      ⇒ `OPENCODE_FREE_ZEN_API_KEY` 与 `_zen_key()/_zen_free_key()` 保留代码但不进链（opencode 若放开外部免费档可加回）。
#   commandcode 免费池（实拉 /provider/v1/models 87 个模型里的 free 名单 + 逐个探针）:
#      + inclusionai/ling-3.1-flash:free（6/6 行、art 列位 0 坏、13.2s）
#      + stealth/glyph-cluster:free（6/6 行、art 列位 0 坏、16.3s）
#      - meituan/longcat-2.0:free（403「The free LongCat 2.0 tier has been retired → now a paid model」）
#      - inclusionai/ling-3.0-flash-sante:free（404「This model is unavailable for free」）
#      · poolside/laguna-s-2.1-free 保留（429/503 间歇上游过载，免费链退避轮换处理）
#   推理型免费模型必须带 per-model 预算（第 4 元素）：ling-3.1-flash / glyph-cluster 实测 32768
#     （链默认 16384 会被思考烧光 → 空 content 被当失败轮换）。
#   siliconflow GLM-4-9B 双通道（FREE + 主 key）**移除**：2026-10-10 实测两 key 均 HTTP 402
#     「account balance is insufficient」——充值后可加回两行（限免模型仍是最快无过滤的免费项）。
#   ⇒ 免费链 14 → **3 条，且全在 commandcode 单平台**（跨平台分散/配额独立的好处暂时没了；
#      平台级故障时只剩退避重试）。想补平台需另找免费源（OpenRouter :free 等）。
#   R18 补测（2026-10-10, 三行露骨台词）: stealth/glyph-cluster:free **直接拒翻**（命中拒绝语关键词 →
#     按漏译跳过不崩章）; ling-3.1-flash:free 当场 429。免费档 R18 明显弱于旧链（GLM-4-9B/laguna 曾过 ~70%）。
FREE_MODELS = [
    ("inclusionai/ling-3.1-flash:free", "commandcode", None, 32768),   # 2026-10-10 新: 6/6 行 art 列位 0 坏 13.2s
    ("stealth/glyph-cluster:free", "commandcode", None, 32768),        # 2026-10-10 新: 6/6 行 art 列位 0 坏 16.3s
    ("poolside/laguna-s-2.1-free", "commandcode", None),               # 保留: 间歇 429/503(上游过载), 免费链自动轮换
]
FREE_MAX_BACKOFF = int(os.environ.get("AA_FREE_MAX_BACKOFF", "300"))  # 退避封顶 5min
FREE_BASE_BACKOFF = int(os.environ.get("AA_FREE_BASE_BACKOFF", "10")) # 退避起步 10s

FREE_MAX_ROUNDS = int(os.environ.get("AA_FREE_MAX_ROUNDS", "5"))  # 2026-09-01: max fail rounds before giving up


def _zen_key():
    """opencode 主 key（`OPENCODE_GO_API_KEY`）——Go 门订阅/ Zen 门按量都用它。

    2026-10-10 起它同时是付费链首（`[backend.opencode_go]`）的 key；免费档已对第三方客户端关闭。
    """
    k = _read_env_key("OPENCODE_GO_API_KEY", _env_file_paths()) or _read_env_key("OPENCODE_GO_API_KEY")
    if k:
        return k
    raise RuntimeError("no OPENCODE_GO_API_KEY")

def _zen_free_key():
    """opencode 免费档专用 key(`OPENCODE_FREE_ZEN_API_KEY`, 独立配额/限流)。

    2026-08-31 新增; 读仓库根 .env, 未设回退主 key。
    ⚠️ 2026-10-10: opencode 免费档已变成 OpenCode 客户端专属（第三方一律 403 FreeTierError），
    该 key 目前**无使用者**（FREE_MODELS 已无 opencode 条目）；函数保留以便日后放开时直接加回。
    """
    k = _read_env_key("OPENCODE_FREE_ZEN_API_KEY", _env_file_paths())
    if k:
        return k
    return _zen_key()  # 未设则回退主 key


# ---------------------------------------------------------------- opencode zen 系（2026-10-10 重构）
# 两个门（探针实测见 doc/翻译模型对比.md 2026-10-10 段）：
#   Zen 门 `/zen/v1/chat/completions`  按量付费（$0.30 in / $1.20 out per M）——**免费模型只在此门**；
#   Go  门 `/zen/go/v1/chat/completions` $10/月订阅含用量（折算 $0.15/$0.60）——**必须** `x-opencode-session`
#          （不带 → 400 MissingSessionID）；同 session + 同前缀实测命中 1280/1340 = 95.5%。
# 两门共用同一 key（.env 里 OPENCODE_GO_API_KEY / OPENCODE_ZEN_API_KEY 实测同值）；
# 关思考：Go 门 `reasoning_effort="none"` 与 `thinking:{type:disabled}` 都有效（reasoning_tokens=0）。

_ZEN_SESSION = {"id": None}     # 翻译层每章 set_zen_session() 覆盖（单键赋值，GIL 下原子，够用）
_ZEN_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def set_zen_session(sid):
    """设置 Go 门的 `x-opencode-session`（翻译层在章边界调用；None/空 = 清空回默认）。

    官方口径（docs/go#where-can-i-use-it）：一段会话一个稳定 id → 路由亲和 + 前缀缓存。
    """
    _ZEN_SESSION["id"] = (str(sid).strip() or None) if sid else None


def zen_session():
    """Go 门实际发出的 session id。

    优先级：env `AA_OCGO_SESSION` > `set_zen_session()`（每章一个）> env `AA_TASK_ID` > `"aa-translation"`。
    非 ASCII 一律换成 `-`：HTTP 头只能 latin-1，带中文会直接 UnicodeEncodeError（实测踩过）。
    """
    sid = (os.environ.get("AA_OCGO_SESSION") or _ZEN_SESSION.get("id")
           or (("aa-task-" + os.environ["AA_TASK_ID"]) if os.environ.get("AA_TASK_ID")
               else "aa-translation"))
    # 只留 ASCII 字母数字与 _.:-（`str.isalnum()` 对中文也返回 True，不能用它过滤）
    sid = "".join(c if ("a" <= c <= "z" or "A" <= c <= "Z" or c.isdigit() or c in "_.:-") else "-"
                  for c in str(sid))
    return sid[:64] or "aa-translation"


def _zen_chat(system, body, url, key, backend, model_default, model=None, temperature=0.4,
              max_tokens=16384, timeout=180, need_session=False, retries=3, tag=None):
    """opencode zen 系端点通用调用（Zen / Go 两门共用；Go 门额外带 `x-opencode-session`）。

    关思考：首选 `reasoning_effort="none"`（两门实测有效）；但上游池里偶有实例对该字段直接
    400（`none reasoning effort is not supported by deepseek-v4.1-flash model`）→ **自适应重试**
    改发 `thinking:{"type":"disabled"}`（同样实测 reasoning_tokens=0）。不能把 400 抛给调用方：
    400 不在 `_degrade_reason()` 的可降级集合里，会让整次调用硬失败而不是换端点。

    retries: 网络/服务端类失败自身重试次数（默认 3）。**自适应换法那一跳不占预算**
    （`tries = max(tries, attempt + 1)`）——否则 retries=1 时（划词/短请求的收敛口径）
    400 的自适应重试会被提前掐掉，等于白关思考。`timeout` 是**单次**请求超时。
    """
    import urllib.request, urllib.error, json as _json
    headers = {
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "User-Agent": _ZEN_UA,
    }
    if need_session:
        headers["x-opencode-session"] = zen_session()

    def _payload(extra):
        p = {
            "model": model or model_default,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": body}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        p.update(extra)
        return p

    last_err = None
    extra = {"reasoning_effort": "none"}
    tries = max(1, int(retries or 1))
    attempt = 0
    while attempt < tries:
        attempt += 1
        try:
            data = _json.dumps(_payload(extra), ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = _json.load(r)
            msg = resp["choices"][0]["message"].get("content", "")
            fr = (resp.get("choices") or [{}])[0].get("finish_reason") or ""
            u = resp.get("usage") or {}
            if fr == "length":
                raise RuntimeError("%s output truncated (finish_reason=length): %d tokens"
                                   % (backend, u.get("completion_tokens") or 0))
            if not msg:
                raise RuntimeError("%s 空响应: %s" % (backend, _json.dumps(resp, ensure_ascii=False)[:200]))
            try:
                _log_usage(u.get("prompt_tokens") or u.get("input_tokens") or 0,
                           u.get("completion_tokens") or 0, u=u,
                           model=model or model_default, backend=backend, tag=tag)
            except Exception:
                pass
            return msg
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            if e.code == 400 and "reasoning" in detail and "reasoning_effort" in extra:
                print("[%s] reasoning_effort=none 被上游拒(400) → 改 thinking:{type:disabled} 重试"
                      % backend, flush=True)
                extra = {"thinking": {"type": "disabled"}}
                tries = max(tries, attempt + 1)      # 自适应换法不吃重试预算
                continue
            last_err = RuntimeError("HTTP %s %s" % (e.code, detail[:200]))
        except Exception as e:
            last_err = e
        if attempt < tries:
            time.sleep(2 * attempt)              # 只在「还有下一次」时退避（末次失败白等 2s，划词会白等）
    raise RuntimeError("%s failed after retries: %s" % (backend, last_err))


def call_opencode_zen(system, body, max_tokens=16384, timeout=180, temperature=0.4, model=None, key=None,
                      retries=3, tag=None):
    """Zen 门 `/zen/v1/chat/completions`（按量；免费模型**只在此门**可用，Go 门不认 free 模型名）。

    2026-08-27: opencode 免费模型测试(6 个)选出的可用者: mimo-v2.5-free / hy3-free /
    big-pickle(快 2-3s, 质量好, R18 无过滤)。free 模型必须 reasoning_effort=none
    (否则推理吃光输出), 必须浏览器 UA。2026-10-10 复测: 免费档在**非 OpenCode 客户端**里
    会 403 FreeTierError（`big-pickle` 实测），免费链是否仍可用需另测。
    2026-09-05: hy3-free 从官网免费列表消失; 新增 ling-3.0-flash-fin-free(1.6s 快+稳 art好) /
    nemotron-3.5-lightning-free(3.0s) / nemotron-3.6-ultra-free(50s 慢兜底)。
    key: None=主 key / "FREE"=免费专用 key / 其他字面值=直接用。
    """
    url = os.environ.get("AA_OCZEN_URL") or _url(
        "opencode_zen", "https://opencode.ai/zen/v1/chat/completions")
    k = _zen_free_key() if key == "FREE" else (_zen_key() if key is None else key)
    return _zen_chat(system, body, url, k, "opencode_zen",
                     os.environ.get("AA_OCZEN_MODEL") or "mimo-v2.5-free", model=model,
                     temperature=temperature, max_tokens=max_tokens, timeout=timeout,
                     retries=retries, tag=tag)


def call_opencode_go(system, body, max_tokens=16384, timeout=180, temperature=0.4, model=None, key=None,
                     retries=3, tag=None):
    """Go 门 `/zen/go/v1/chat/completions`（$10/月订阅含用量）——**必须** `x-opencode-session`。

    2026-10-10 实测: 不带该头 = 400 MissingSessionID；带稳定 session 后同前缀缓存命中
    1280/1340 = 95.5%；关思考两法皆有效；R18 台词正常译出（无过滤）。
    key: 取 `[backend.opencode_go].key_env`（默认 OPENCODE_GO_API_KEY），取不到回退主 key 名。
    端点/env 逃生口: `AA_OCGO_URL`、`AA_OCGO_SESSION`（session 覆盖）。
    retries: 自身重试次数（默认 3，与 call_deepseek/deepinfra 同口径）；划词等短请求传 1
    （`selection._invoke` 统一传 `AA_SEL_RETRY`，失败要快、好换下一候选）。
    """
    url = os.environ.get("AA_OCGO_URL") or _url(
        "opencode_go", "https://opencode.ai/zen/go/v1/chat/completions")
    key_env = _backend_cfg("opencode_go").get("key_env") or "OPENCODE_GO_API_KEY"
    k = key or _read_env_key(key_env, _env_file_paths()) or _zen_key()
    # 2026-10-11: 支持 AA_OCGO_MODEL(与 call_deepseek 的 AA_DS_MODEL 同口径)——链条目 model
    #   优先级最高, 这里只是给「直接调 call_opencode_go 不带 model」的场景一个显式覆盖口。
    mdl = model or os.environ.get("AA_OCGO_MODEL") or _model("opencode_go", "deepseek-v4.1-flash")
    return _zen_chat(system, body, url, k, "opencode_go", "deepseek-v4.1-flash", model=mdl,
                     temperature=temperature, max_tokens=max_tokens, timeout=timeout,
                     need_session=True, retries=retries, tag=tag)


def _gmi_key():
    """GMI Cloud key(GMI_API_KEY, 仓库根 .env)。"""
    k = _read_env_key("GMI_API_KEY", _env_file_paths()) or _read_env_key("GMI_API_KEY")
    if k:
        return k
    raise RuntimeError("no GMI_API_KEY")


def call_gmi(system, body, max_tokens=16384, timeout=180, temperature=0.4, model=None):
    """走 GMI Cloud /v1/chat/completions（OpenAI 兼容, api.gmi-serving.com）。

    2026-08-27: MiniMax Week 限时免费(截止 9.6) MiniMaxAI/MiniMax-M3(快2-3s稳) /
    MiniMaxAI/MiniMax-M2.7(3.5-7s, 偶发空响应)。R18 无过滤。
    """
    import urllib.request, urllib.error, json as _json
    url = "https://api.gmi-serving.com/v1/chat/completions"
    payload = {
        "model": model or "MiniMaxAI/MiniMax-M3",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": body}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    data = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": "Bearer " + _gmi_key(),
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
    })
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = _json.load(r)
            msg = resp["choices"][0]["message"].get("content", "")
            if not msg:
                raise RuntimeError("gmi 空响应: " + _json.dumps(resp, ensure_ascii=False)[:200])
            return msg
        except Exception as e:
            last_err = e
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"gmi failed after retries: {last_err}")


def _call_free_model(mdl, backend, system, body, max_tokens, timeout, key=None):
    """按后端调对应 call_* 函数。返回文本。key: None=主key, "FREE"=对应 *_FREE_API_KEY(仅免费模型)。"""
    if backend == "siliconflow":
        return call_siliconflow(system, body, max_tokens=max_tokens, timeout=timeout, model=mdl,
                                key=_sf_free_key() if key == "FREE" else None)
    if backend == "opencode_zen":
        return call_opencode_zen(system, body, max_tokens=max_tokens, timeout=timeout, model=mdl, key=_zen_free_key() if key == "FREE" else None)
    if backend == "gmi":
        return call_gmi(system, body, max_tokens=max_tokens, timeout=timeout, model=mdl)
    # commandcode: 免费模型一律走主 key（2026-09-15 free key 通道移除——free key 与主 key
    #   共用同一免费模型配额, 双通道无并发/配额增益, 只增加一份要维护的 key）
    return call_commandcode(system, body, max_tokens=max_tokens, timeout=timeout, model=mdl)


# 免费链轮询指针(线程安全): 多 worker 并发时每个 worker 从不同免费模型开始,
# 天然分散到 GLM/laguna/minimax 并行(各自平台限流独立), 避免全抢 GLM。
_free_round = {"i": 0}
_free_round_lock = _t.Lock()


def _free_round_robin_start():
    """返回本轮起始模型索引(轮询), 使并发 worker 错开模型。"""
    with _free_round_lock:
        i = _free_round["i"]
        _free_round["i"] = (i + 1) % max(len(FREE_MODELS), 1)
        return i % max(len(FREE_MODELS), 1)


def _call_free_chain(system, body, max_tokens=16384, timeout=180):
    """免费链无限重试: 从轮询起始模型开始依次尝试 FREE_MODELS,
    429/503/空响应/超时 → 指数退避+轮换。多 worker 并发时各从不同模型起步 → 多模型并行。

    不抛异常(除非全部模型连续 N 轮都失败且退避到封顶——此时也继续, 只是放慢)。
    返回 (text, model_name)。R18 内容被模型拒绝时抛 RuntimeError(调用方按漏译跳过)。
    """
    import random
    start = _free_round_robin_start()
    n_fail = 0          # 连续失败轮数(每轮遍历全部模型)
    last_err = None
    while True:
        for i in range(len(FREE_MODELS)):
            item = FREE_MODELS[(start + i) % len(FREE_MODELS)]
            mdl, be, key = item[0], item[1], item[2]
            # 2026-09-15: 条目可带第 4 元素 = 该模型专用输出预算(推理型免费模型预算不足→空 content)
            mt = item[3] if len(item) > 3 else max_tokens
            try:
                txt = _call_free_model(mdl, be, system, body, mt, timeout, key=key)
                if txt and txt.strip():
                    # 内容被拒(免费模型安全过滤, 200 响应但拒翻) → 空串按漏译跳过
                    if any(k in txt for k in ("无法协助", "不能协助", "无法翻译", "不能翻译", "无法帮你", "不能帮你")):
                        print(f"[free-chain] {mdl} 内容被拒, 该块按漏译跳过: {txt[:60]}", flush=True)
                        return "", mdl
                    return txt, mdl
                last_err = RuntimeError(f"{mdl} 空响应")
            except RuntimeError as e:
                last_err = e
                # R18 被拒(异常形式, 少见) → 同样按漏译跳过
                if "无法" in str(e) or "拒绝" in str(e) or "不能协助" in str(e) or "无法协助" in str(e):
                    print(f"[free-chain] {mdl} 内容被拒(异常), 该块按漏译跳过: {str(e)[:80]}", flush=True)
                    return "", mdl
            except Exception as e:
                last_err = e
            # 换下一模型前小等
            time.sleep(2)
        n_fail += 1

        if n_fail > FREE_MAX_ROUNDS:
            raise RuntimeError("free-chain: all models failed %d rounds, give up this chunk: %s" % (FREE_MAX_ROUNDS, last_err))
        backoff = min(FREE_BASE_BACKOFF * (2 ** min(n_fail - 1, 5)), FREE_MAX_BACKOFF)
        jitter = random.uniform(0.5, 1.5)
        print(f"[free-chain] 全部免费模型失败 {n_fail} 轮 ({last_err}) → 退避 {int(backoff*jitter)}s", flush=True)
        time.sleep(backoff * jitter)


def call_with_fallback(work_r18, system, body, max_tokens=16384, timeout=180, low_r18=False):
    """后端自动降级重试（2026-08-22）。

    - 显式设 AA_BACKEND=N 时: 从 N 开始, 失败后按配置链依次尝试其后候选
      (配置链按 work_r18 取 r18_backends/normal_backends)。
    - **AA_BACKEND_AUTO=1(runner 自动选的链首)例外**: 那只是代码默认, 不参与「组前置」——
      链序/勾选集以 `AA_CHAIN` 为准(2026-10-11, 否则定时任务的 AA_CHAIN=mimo 与控制台取消
      勾选链首都会被盖过)。用户手写的 AA_BACKEND 照旧组前置。
    - 未设时: 从配置链第一个可用的开始。
    - 记住当前可用后端(_PREFERRED_BACKEND), 后续调用优先复用, 避免每次从链头重试已坏的。
    - low_r18(2026-08-25): 低含量 R18 作品的 yes 章——忽略显式 AA_BACKEND(防 normal
    - AA_FREE_MODE=1(2026-08-26): 真免费模型特殊流程——所有模型用免费模型,
      降低并发, 无限重试, 不在乎时间。429/503/空响应/超时都退避重试+模型轮换, 不放弃。
    全部候选失败才抛最后异常。返回 (text, backend_name)。
    """
    # 2026-08-26 免费模式: 全部免费模型 + 无限重试
    if os.environ.get("AA_FREE_MODE") in ("1", "true", "True"):
        return _call_free_chain(system, body, max_tokens=max_tokens, timeout=timeout)
    chain = [t for t in aa_config.backend_chain(work_r18)]
    explicit = os.environ.get("AA_BACKEND")
    # 2026-10-11: 区分「用户显式指定」与「runner 自动选出的链首」(AA_BACKEND_AUTO=1)。
    #   自动选出的是**代码的默认**, 不是用户选择 → 不参与下面的「组前置」, 否则会把链首强行插到
    #   最前面, 盖过 AA_CHAIN 勾选集。实测症状(修复前):
    #     ① 定时任务只写 AA_CHAIN=mimo → 实际尝试顺序 [opencode_go, mimo]（「只走小米 + 00:00-08:00
    #        折扣时段」被绕过, 白等折扣）
    #     ② 控制台取消勾选链首(AA_CHAIN=deepinfra+deepseek) → [deepseek, opencode_go, deepinfra],
    #        被取消的 opencode_go 照样被用
    #   `aa_prompt_prefix` 早就是同口径(自动值不参与拼接), 这里补齐。
    _auto_bk = os.environ.get("AA_BACKEND_AUTO") in ("1", "true", "True")
    _user_explicit = bool(explicit) and explicit.lower() not in ("cloud", "") and not _auto_bk
    # 2026-10-11 用户口径「**手动选择即不降级**（只使用选择和设置保存的模型）」：
    #   `AA_CHAIN`（控制台勾选 / 设置页保存的链）非空 = 用户显式选择 → 整轮调度只在这批端点内：
    #     ① 链已被 backend_chain 收窄到勾选集（选不中会抛 ChainUnavailable，**不回退整条链**）；
    #     ② **不参与下面的「显式 AA_BACKEND 组前置」**——那会把链外端点插到最前面，实测症状：
    #        `AA_CHAIN=opencode_go|…` + .env 里遗留 `AA_BACKEND=deepinfra` → 失败后降级到没勾选的端点。
    #   未设 AA_CHAIN = 用整条链 → 链内降级语义完全不变（旧行为）。
    _sel = aa_config.chain_names_override()
    if _user_explicit and not _sel:
        if not low_r18:
            # 低含量 R18 的 yes 章(low_r18=True)忽略显式 AA_BACKEND——否则 R18 作品的
            # auto_backend 会把 normal 链首提到链首覆盖章节级选择(2026-09-07: 原 zhipu
            # 优先分支已随 zhipu 移除, low_r18 仅保留「跳过显式」语义)。
            # 显式后端改「组前置」——保留该后端在链上的全部配置模型条目
            # (旧版 insert (explicit,None) 会丢掉同后端其他模型, 轮询分散失效);
            # 链上没有该后端(key 缺失被过滤等)才按旧行为插入头并靠 env 解析模型。
            _grp = [t for t in chain if t[0].lower() == explicit.lower()]
            _rest = [t for t in chain if t[0].lower() != explicit.lower()]
            chain = (_grp + _rest) if _grp else [(explicit, None)] + _rest

    # 同(后端,模型)去重(保序取首个)——显式头(env 解析)与配置条目可能同模型
    _seen, _dd = set(), []
    for _t in chain:
        _b, _m = _t
        _mdl = _m if _m else _model_for_backend(_b, _m)
        if (_b, _mdl) in _seen:
            continue
        _seen.add((_b, _mdl))
        _dd.append(_t)
    chain = _dd

    # 2026-09-21 兜底端点(`fallback_only = true`, 如 commandcode): 用户口径「默认不使用, 只在其他端点
    # 均失效时启用」——从健康态轮询与 pin 目标里摘出去(否则无 cache 端点时轮询会让它占 1/N 流量),
    # 只保留在链尾作最后一档降级; 显式 AA_BACKEND 指定该端点时照旧放链首(手动选择优先)。
    try:
        _bottom = aa_config.fallback_only_names(work_r18)
    except Exception:
        _bottom = set()
    if _user_explicit and explicit.lower() in _bottom:
        _bottom = _bottom - {explicit.lower()}
    _rot = [t for t in chain if t[0] not in _bottom] if _bottom else chain

    # 优先复用降级记忆(某端点确认坏→钉住可用端点, 本章内不反复撞);
    # 无记忆(健康态)时端点均匀轮询起步——并发 worker 先让每个端点各占一个,
    # 多出的再均匀轮流, 端点内模型按轮次切换(sf/cc 各平台限流独立;
    # 2026-09-02 用户纠正: 轮询单位=端点非链条目, 3并发=sf1+cc1+sf1)。
    # 2026-09-15 (P1) 章内 pin: 章首(记忆为空)钉住会缓存的端点; 缓存实测只在官网 deepseek 命中,
    # 而健康态旧逻辑"每次调用都轮询"会把同一章打散到零命中的网关 → 白丢 79% 输入折扣。
    # 显式 AA_BACKEND(非 cloud) 是用户的手动选择 → 不抢着 pin（否则 AA_BACKEND=deepinfra 会被钉回官网）。
    _explicit_bk = (os.environ.get("AA_BACKEND") or "").lower()
    if aa_config.pin_cache_endpoint() and (not _explicit_bk or _explicit_bk == "cloud" or _auto_bk):
        _pin = _cacheable_endpoint(_rot)
        if _pin:
            with _BACKEND_LOCK:
                if _PREFERRED_BACKEND["name"] is None:
                    _PREFERRED_BACKEND["name"] = _pin
    with _BACKEND_LOCK:
        pref = _PREFERRED_BACKEND["name"]
    if pref:
        chain = [t for t in chain if t[0] == pref] + [t for t in chain if t[0] != pref]
    else:
        chain = _endpoint_round_chain(_rot) + [t for t in chain if t[0] in _bottom]

    last_err = None
    for _bi, (b, m) in enumerate(chain):
        try:
            # 条目自带模型(配置链)优先; 条目无模型(显式插入头)才用 env AA_*_MODEL/后端默认
            txt = _call_named(b, system, body, model=(m if m else _model_for_backend(b, m)),
                              max_tokens=max_tokens, timeout=timeout)
            # 2026-09-02: 仅「降级后成功」(链上前面候选失败过, _bi>0)才记可用后端,
            # 健康直出不钉住——让并发调用继续轮询分散到不同端点。
            # (旧版每次成功都钉, 并发全挤到单一端点; 每章 reset_preferred_backend 清记忆)
            if _bi > 0:
                with _BACKEND_LOCK:
                    if _PREFERRED_BACKEND["name"] is None:
                        _PREFERRED_BACKEND["name"] = b
            return txt, b
        except Exception as e:
            reason = _degrade_reason(e)
            if reason is None:
                # 非可降级错误(如请求构造/网络中断非限流) → 立即抛, 不试其他后端
                raise
            last_err = f"[{b}] {e}"
            print(f"[auto-degrade] {b} {reason} → 换下一候选 {next((t[0] for t in chain if t[0]!=b), '无')}", flush=True)
            with _BACKEND_LOCK:
                if _PREFERRED_BACKEND["name"] == b:
                    _PREFERRED_BACKEND["name"] = None
    raise RuntimeError(f"全后端失败: {last_err}")


def call_qwen_vl(system, body, max_tokens=16384, timeout=120, temperature=0.1, model=None, image_b64=None, image_url=None):
    """走 SiliconFlow Qwen3-VL（视觉+文本双用，无 R18 审查）。

    纯文本：model=Qwen3-VL-32B-Instruct，OpenAI 兼容，直连无代理，0.7-2.5s/4行
    视觉：image_b64 或 image_url 指定图片（data:image/png;base64,... 或 http URL），
          图文 33s 正常（vl-image-r18 实测 64x64 白图+露骨文本直过）
    必须 enable_thinking=false 关推理（siliconflow 官方参数; 2026-08-31 实测 0.7s vs 1.2s 更快）。
    model 可覆盖：AA_QWEN_VL_MODEL / QWEN_VL_8B / QWEN_VL_30B_A3B

    2026-08-27: 视觉一律走 Qwen3-VL(付费)。曾试免费视觉(ox-alpha image_url),
    但 ox-alpha 已从 commandcode 下架且 laguna-free 视觉不可靠 → 回退 qwen-vl。
    """
    # (2026-08-27 移除 ox-alpha 免费视觉——模型下架, 视觉保持 qwen-vl)
    if image_b64 and image_url:
        raise ValueError("image_b64 与 image_url 二选一")
    if image_b64:
        data_url = f"data:image/png;base64,{image_b64}" if not image_b64.startswith("data:") else image_b64
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": body},
            ]},
        ]
    elif image_url:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": image_url}},
                {"type": "text", "text": body},
            ]},
        ]
    else:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": body},
        ]
    payload = {
        "model": model or QWEN_VL_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "enable_thinking": False,   # 官方参数(2026-08-31): 关闭思考, 视觉模型也更快
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(QWEN_VL_URL, data=data, headers={
        "Authorization": "Bearer " + _sf_key(),
        "Content-Type": "application/json",
    })
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.load(r)
            msg = resp["choices"][0]["message"].get("content", "")
            if not msg:
                raise RuntimeError("qwen-vl 空响应: " + json.dumps(resp, ensure_ascii=False)[:200])
            u = resp.get("usage", {}) or {}
            _log_usage(u.get("input_tokens") or u.get("prompt_tokens") or 0,
                       u.get("output_tokens") or u.get("completion_tokens") or 0, u=u, model=model, backend="qwen_vl")
            return msg
        except Exception as e:
            last_err = e
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"qwen-vl failed after retries: {last_err}")


def call_commandcode(system, body, max_tokens=16384, timeout=180, temperature=0.4, model=None, image_b64=None, key=None, tag=None):
    """走 CommandCode /provider/v1/chat/completions（OpenAI 兼容，opencode-go 弃用后的新主网关）。

    首选 xiaomi/mimo-v2.5（8s/块、reasoning 极少、无内容过滤、质量好）；
    回退 meta/muse-spark-1.3-contributor（推理模型，必须 reasoning_effort=low，
    默认 max 会把输出吃光 content 空 finish=length）。
    必须带浏览器 UA（无 UA 403 code 1010）；偶发 10054 连接重置，重试即可。
    mimo 偶发慢/超时（30s+）——首试 timeout 用短值，超时就快速回退 muse-spark。

    image_b64(2026-08-26): 视觉输入(OpenAI 兼容 image_url 格式), ox-alpha 支持。
    key(2026-08-29): 免费模型传 COMMANDCODE_FREE_API_KEY(独立配额), 缺省主 key。
    key(2026-09-15): commandcode free key 通道已移除(与主 key 共用免费配额, 无增益)——
       免费链对所有 commandcode 条目都不再传 key; 参数保留供手动调用。
    """
    auth_key = key or _cc_key()
    attempts = [(model or CC_MODEL, {}, 60)]   # (model, extra, timeout)
    if model is None:
        attempts.append((CC_FALLBACK_MODEL, {"reasoning_effort": "low"}, timeout))
    else:
        # 2026-08-19: model 显式指定 muse-spark(低价链)时也必须 reasoning_effort=low,
        # 否则思考吃光输出空(finish=length) — auto_backend 的 normal_backends 用
        # 2026-08-26: stealth/ox-alpha(免费推理模型)同样必须 low, 不带则空响应/超时
        if "muse-spark" in model or "ox-alpha" in model:
            attempts[0] = (model, {"reasoning_effort": "low"}, 120)
    last_err = None
    for mdl, extra, tmo in attempts:
        if image_b64:
            data_url = f"data:image/png;base64,{image_b64}" if not image_b64.startswith("data:") else image_b64
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": body},
                ]},
            ]
        else:
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": body}]
        payload = {
            "model": mdl,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        payload.update(extra)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(CC_URL, data=data, headers={
            "Authorization": "Bearer " + auth_key,
            "Content-Type": "application/json",
            "User-Agent": CC_UA,
        })
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=tmo) as r:
                    resp = json.load(r)
                msg = resp["choices"][0]["message"].get("content", "")
                if not msg:
                    raise RuntimeError("commandcode 空响应: " + json.dumps(resp, ensure_ascii=False)[:200])
                u = resp.get("usage", {}) or {}
                _log_usage(u.get("input_tokens") or u.get("prompt_tokens") or 0,
                           u.get("output_tokens") or u.get("completion_tokens") or 0, u=u,
                           model=model, backend="commandcode", tag=tag)
                return msg
            except Exception as e:
                last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"commandcode failed after retries: {last_err}")


def call_runinfra(system, body, max_tokens=16384, timeout=120, temperature=0.4, model=None, retries=3):
    """走 RunInfra /v1/chat/completions（OpenAI 兼容）。
    DeepSeek V4 Flash: $0.13/$0.27 per 1M tokens，注册送 $10 credits。
    ⚠️ DeepSeek V4 Flash 有 reasoning 特性，必须 reasoning_effort=none 否则输出被思考吃光。
    2026-09-06: glm-5-3-flash 不吃 reasoning_effort=none(400)——按模型自适应: glm 系用 low。

    retries: 网络类失败自身重试次数(默认 3)。划词翻译传 1(2026-09-20 超时收敛)。"""
    mdl = model or RI_MODEL
    # glm 系须 low (none→400 实测 2026-09-05); 其余 none
    _rz = "low" if "glm" in mdl.lower() else "none"
    payload = {
        "model": mdl,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": body}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "reasoning_effort": _rz,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(RI_URL, data=data, headers={
        "Authorization": "Bearer " + _ri_key(),
        "Content-Type": "application/json",
    })
    # 2026-09-06: runinfra 直连偶发 SSL EOF/10054(NO_PROXY 含 runinfra → 不走环境代理) → 默认走代理。
    # 2026-10-08 阶段 4: 代理地址/策略统一走 aa_config（AA_PROXY / AA_USE_PROXY / TOML），
    #   显式 AA_USE_PROXY=0 → 直连（打包给别人后没代理也能跑，不再硬编码 10808）。
    _ri_opener = aa_config.proxy_opener() or urllib.request.build_opener()
    last_err = None
    for attempt in range(max(1, retries)):
        try:
            t0 = time.time()
            with _ri_opener.open(req, timeout=timeout) as r:
                resp = json.load(r)
            msg = resp["choices"][0]["message"].get("content", "")
            if not msg:
                raise RuntimeError("runinfra 空响应: " + json.dumps(resp, ensure_ascii=False)[:200])
            u = resp.get("usage", {}) or {}
            _log_usage(u.get("input_tokens") or u.get("prompt_tokens") or 0,
                       u.get("output_tokens") or u.get("completion_tokens") or 0, u=u, model=model, backend="runinfra")
            return msg
        except Exception as e:
            last_err = e
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"runinfra failed after retries: {last_err}")


def call_mimo(system, body, max_tokens=16384, timeout=120, temperature=0.1, model=None, tag=None):
    """走小米 MiMo /v1/chat/completions（OpenAI 兼容）。
    思考走 reasoning_content 字段(content 干净)，带 thinking disabled 提速(实测 v2.5 1.9s/pro 2.6s)。
    key 读仓库根 .env（变量名 = [backend.mimo].key_env，2026-09-29 起 = XIAOMI_API_KEY/token plan；
    AA_MIMO_KEY_ENV 可改名前，旧 XIAOMI_MIMO_API_KEY 作兜底）。
    tag: usage 记录用的 tag（refine_aa 校对传 "refine"；None → env AA_USAGE_TAG/配置默认）。"""
    payload = {
        "model": model or MIMO_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": body}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "thinking": {"type": "disabled"},
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(MIMO_URL, data=data, headers={
        "Authorization": "Bearer " + _mimo_key(),
        "Content-Type": "application/json",
    })
    last_err = None
    for attempt in range(3):
        try:
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.load(r)
            msg = resp["choices"][0]["message"].get("content", "")
            if not msg:
                raise RuntimeError("mimo 空响应: " + json.dumps(resp, ensure_ascii=False)[:200])
            u = resp.get("usage", {}) or {}
            _log_usage(u.get("input_tokens") or u.get("prompt_tokens") or 0,
                       u.get("output_tokens") or u.get("completion_tokens") or 0, u=u,
                       model=model, backend="mimo", tag=tag)
            return msg
        except Exception as e:
            last_err = e
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"mimo failed after retries: {last_err}")


def call_minimax(system, body, max_tokens=16384, timeout=120, temperature=0.1, model=None):
    """走 MiniMax 国内 OpenAI 兼容端点 /v1/chat/completions。
    M3: thinking disabled 生效(实测 1.3s 质量好)；M2.7: 思考关不掉且混在 content，
    统一剥离 <think>...</think> 块(对 M3 无副作用)。key 读仓库根 .env。"""
    import re
    payload = {
        "model": model or MM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": body}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "thinking": {"type": "disabled"},
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(MM_URL, data=data, headers={
        "Authorization": "Bearer " + _mm_key(),
        "Content-Type": "application/json",
    })
    last_err = None
    for attempt in range(3):
        try:
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.load(r)
            msg = resp["choices"][0]["message"].get("content", "")
            if not msg:
                raise RuntimeError("minimax 空响应: " + json.dumps(resp, ensure_ascii=False)[:200])
            msg = re.sub(r"<think>.*?</think>", "", msg, flags=re.S).strip()
            if not msg:
                raise RuntimeError("minimax 剥 think 后空响应")
            u = resp.get("usage", {}) or {}
            _log_usage(u.get("input_tokens") or u.get("prompt_tokens") or 0,
                       u.get("output_tokens") or u.get("completion_tokens") or 0, u=u, model=model, backend="minimax")
            return msg
        except Exception as e:
            last_err = e
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"minimax failed after retries: {last_err}")



def call_openrouter(system, body, max_tokens=16384, timeout=120, temperature=0.4, model=None):
    """走 OpenRouter /api/v1/chat/completions（免费模型备用）。
    必须带 HTTP-Referer + X-Title（openrouter 要求，缺了 401）。
    坑（2026-08-14 实测）：免费模型限流频繁(429)、gemma 偶发可用、
    nemotron 系输出思考过程、gpt-oss 易截断 → 仅作 siliconflow 不可用时的兜底。"""
    payload = {
        "model": model or OR_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": body}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(OR_URL, data=data, headers={
        "Authorization": "Bearer " + _or_key(),
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost",
        "X-Title": "AA-Translator",
    })
    last_err = None
    for attempt in range(3):
        try:
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.load(r)
            msg = resp["choices"][0]["message"].get("content", "")
            fr = (resp.get("choices") or [{}])[0].get("finish_reason") or ""
            if fr == "length":
                u = resp.get("usage", {}) or {}
                raise RuntimeError("siliconflow output truncated (finish_reason=length): %d tokens" % (u.get("output_tokens") or u.get("completion_tokens") or 0))

            if not msg:
                raise RuntimeError("openrouter 空响应")
            u = resp.get("usage", {}) or {}
            _log_usage(u.get("input_tokens") or u.get("prompt_tokens") or 0,
                       u.get("output_tokens") or u.get("completion_tokens") or 0, u=u, model=model, backend="openrouter")
            return msg
        except Exception as e:
            last_err = e
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"openrouter failed after retries: {last_err}")


def call_dashscope(system, body, max_tokens=16384, timeout=180, temperature=0.1, model=None):
    """走阿里云 DashScope 兼容端点 /compatible-mode/v1/chat/completions（OpenAI 兼容）。

    qwen 系默认开思考 → 必须 enable_thinking:false 关推理（qwen3.5-plus 80s/4600tok →
    4s/200tok, 23x 提速, 2026-09-02 实测）。
    ⚠️ R18 不可用：DashScope 平台级输出审核，R18 内容 400 DataInspectionFailed。
    ⚠️ 输入限制: 模型 max_input_tokens 上限（qwen3.5-flash=129024≈128k）——body token 超限
    直接抛错（走降级链接管, 不硬发撞 429/截断）。2026-09-02 用户要求「限制输入<=128k」。
    ⚠️ qwen-mt 系列不支持 system 角色, 本函数用于常规 qwen 模型(含 system)。
    """
    if model is None:
        model = DSC_MODEL
    # 粗略 token 估算(中文/日文 ~1-2 字符/token, 按 3 字符安全折半): body+system 超 128k 直接拒
    _MAX_IN = int(os.environ.get("AA_DS_MAX_INPUT", "128000"))
    _est = (len(system) + len(body)) // 3  # 保守: 3 字符≈1 token(实际中文外更密)
    if _est > _MAX_IN:
        raise RuntimeError(f"dashscope {model} 输入超限: 估算 {_est} tok > {_MAX_IN} (AA_DS_MAX_INPUT)")
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": body}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "enable_thinking": False,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(DSC_URL, data=data, headers={
        "Authorization": "Bearer " + _dashscope_key(),
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",  # DashScope 必需 UA 否则 403
    })
    last_err = None
    for attempt in range(3):
        try:
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.load(r)
            msg = resp["choices"][0]["message"].get("content", "")
            if not msg:
                raise RuntimeError("dashscope 空响应: " + json.dumps(resp, ensure_ascii=False)[:200])
            u = resp.get("usage", {}) or {}
            _log_usage(u.get("input_tokens") or u.get("prompt_tokens") or 0,
                       u.get("output_tokens") or u.get("completion_tokens") or 0,
                       u=u, model=model, backend="dashscope")
            return msg
        except Exception as e:
            last_err = e
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"dashscope failed after retries: {last_err}")


def call_siliconflow(system, body, max_tokens=16384, timeout=120, temperature=0.1, model=None, key=None):
    """走 SiliconFlow /v1/chat/completions（OpenAI 兼容）。
    必须 enable_thinking=false 关推理（siliconflow 官方参数; 实测对 GLM-4-9B/Qwen3-VL
    更快更干净, 对推理模型 GLM-Z1 也可用; 2026-08-31 用户拍板同官方文档统一）。
    temperature 默认 0.1（GLM-4-9B 低温质量稳定，0.4 会偶发误译）。
    key(2026-08-29): 免费模型传 SILICONFLOW_FREE_API_KEY(独立配额), 缺省主 key。"""
    auth_key = key or _sf_key()
    payload = {
        "model": model or SF_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": body}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "enable_thinking": False,   # 官方参数(2026-08-31): 关闭思考通用/更快更干净
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(SF_URL, data=data, headers={
        "Authorization": "Bearer " + auth_key,
        "Content-Type": "application/json",
    })
    last_err = None
    for attempt in range(3):
        try:
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.load(r)
            msg = resp["choices"][0]["message"].get("content", "")
            fr = (resp.get("choices") or [{}])[0].get("finish_reason") or ""
            if fr == "length":
                u = resp.get("usage", {}) or {}
                raise RuntimeError("siliconflow output truncated (finish_reason=length): %d tokens" % (u.get("output_tokens") or u.get("completion_tokens") or 0))

            if not msg:
                raise RuntimeError("siliconflow 空响应: " + json.dumps(resp, ensure_ascii=False)[:200])
            u = resp.get("usage", {}) or {}
            _log_usage(u.get("input_tokens") or u.get("prompt_tokens") or 0,
                       u.get("output_tokens") or u.get("completion_tokens") or 0, u=u, model=model, backend="siliconflow")
            return msg
        except Exception as e:
            last_err = e
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"siliconflow failed after retries: {last_err}")


def call_ollama(system, body, max_tokens=16384, timeout=900, temperature=0.4):
    """走本地 Ollama /api/chat（OpenAI 兼容 messages 格式）。已停用，保留回退。"""
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": body}],
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL + "/api/chat", data=data,
        headers={"Content-Type": "application/json"})
    last_err = None
    for attempt in range(3):
        try:
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.load(r)
            msg = resp.get("message", {}).get("content", "")
            if not msg:
                raise RuntimeError("ollama 空响应: " + json.dumps(resp, ensure_ascii=False)[:200])
            u = resp.get("usage", {}) or {}
            _log_usage(u.get("input_tokens") or u.get("prompt_tokens") or 0,
                       u.get("output_tokens") or u.get("completion_tokens") or 0, u=u, model=model, backend="ollama")
            return msg
        except Exception as e:
            last_err = e
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"ollama failed after retries: {last_err}")


def call_deepseek(system, body, max_tokens=16384, timeout=180, temperature=0.4, model=None, retries=3, tag=None):
    """走 deepseek 官网 Responses 端点（POST {base}/responses，与 ds_websearch 同源）。

    model 默认 deepseek-flash（V4.1-Flash；旧名 deepseek-v4-flash/-vision-exp 已下线但仍可调）。
    Responses 格式：instructions/input/max_output_tokens。
    ⚠️ 关思考在 **Responses 端点必须用 {"reasoning": {"effort": "none"}}**（官方 guides/thinking_mode）；
    发 chat 端点的 {"thinking":{"type":"disabled"}} 会被**静默忽略**仍满思考（实测 6 行块：23.7s/5768
    思考 token vs 关掉后 0.7s/80 token）。思考模式还会忽略 temperature（文档明示）。
    响应 output[] 里 type=message 的 content[].text 拼接。
    ⚠️ 官网对 R18 内容可能触发内容审核拒绝 → 检测到整合法被拒信号时报明确错误，由调用方换 deepinfra。

    retries: 网络类失败自身重试次数(默认 3)。refine_aa 单行短请求传 2 —— 短请求失败得快，
    多试无益(2026-09-15 消长尾)。
    tag: usage 记账标签（如 refine）。不再靠调用方改 env AA_USAGE_TAG —— 段级并发下
    os.environ 是进程级全局，多线程改/还原会互相串（2026-09-15）。"""
    payload = {
        "model": model or DS_MODEL,
        "instructions": system,
        "input": body,
        "max_output_tokens": max_tokens,
        "temperature": temperature,
        "reasoning": {"effort": "none"},
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(DS_URL, data=data, headers={
        "Authorization": "Bearer " + _deepseek_key(),
        "Content-Type": "application/json",
    })
    last_err = None
    for attempt in range(max(1, retries)):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.load(r)
            # 内容审核拒绝检测（官网 Responses 拒绝时 status=failed 或 error 含 policy/审核字段）
            if resp.get("status") == "failed":
                err = resp.get("error") or resp.get("incomplete_details") or {}
                detail = json.dumps(err, ensure_ascii=False)
                if any(k in detail.lower() for k in ("policy", "审核", "moderat", "sensitive", "有害", "politeness")):
                    raise RuntimeError(
                        f"deepseek 官网 R18 内容被审核拒绝: {detail[:200]} —— 请换 AA_BACKEND=deepinfra "
                        "(deepseek-ai/DeepSeek-V4-Flash-0731)")
            texts = []
            for i in resp.get("output", []):
                if i.get("type") != "message":
                    continue
                for c in i.get("content", []):
                    if c.get("type") in ("output_text", "input_text") and c.get("text"):
                        texts.append(c["text"])
            msg = texts[-1] if texts else ""
            if not msg:
                raise RuntimeError("deepseek 官网空响应: " + json.dumps(resp, ensure_ascii=False)[:200])
            u = resp.get("usage", {}) or {}
            _log_usage(u.get("input_tokens") or u.get("prompt_tokens") or 0,
                       u.get("output_tokens") or u.get("completion_tokens") or 0,
                       u=u, model=model or DS_MODEL, backend="deepseek", tag=tag)
            return msg
        except RuntimeError:
            raise
        except Exception as e:
            last_err = e
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"deepseek failed after retries: {last_err}")


def call_deepinfra(system, body, max_tokens=16384, timeout=180, temperature=0.4, model=None, retries=3, tag=None):
    """走 DeepInfra /v1/openai/chat/completions（OpenAI 兼容）。

    model 默认 deepseek-ai/DeepSeek-V4.1-Flash（2026-09-21 更新，原 V4-Flash-0731；deepinfra 折后
    $0.06/$0.18 = 30% 折扣价，552B MoE 多模态）。
    deepseek 系有 reasoning 特性，必须 reasoning_effort=none（否则输出被思考吃光）。
    DeepInfra 无额外内容审查（provider 直供），R18 可过。

    retries: 网络类失败自身重试次数(默认 3, 与 call_deepseek 同口径)。划词翻译传 1
    —— 短请求失败得快, 且划词链本身有 3 个候选兜底, 单候选重复重试只会把用户等待
    时间乘以 3(2026-09-20 超时收敛)。"""
    mdl = model or DI_MODEL
    # 2026-09-07: GLM 系(zai-org/GLM-5.3-Flash)不吃 reasoning_effort=none(400/思考吃输出)
    #   ——与 call_runinfra 同款自适应: glm 系用 low, 其余 none。
    _rz = "low" if "glm" in mdl.lower() else "none"
    payload = {
        "model": mdl,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": body}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "reasoning_effort": _rz,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(DI_URL, data=data, headers={
        "Authorization": "Bearer " + _deepinfra_key(),
        "Content-Type": "application/json",
    })
    last_err = None
    for attempt in range(max(1, retries)):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.load(r)
            msg = resp["choices"][0]["message"].get("content", "")
            if not msg:
                raise RuntimeError("deepinfra 空响应: " + json.dumps(resp, ensure_ascii=False)[:200])
            u = resp.get("usage", {}) or {}
            _log_usage(u.get("input_tokens") or u.get("prompt_tokens") or 0,
                       u.get("output_tokens") or u.get("completion_tokens") or 0, u=u,
                       model=model, backend="deepinfra", tag=tag)
            return msg
        except Exception as e:
            last_err = e
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"deepinfra failed after retries: {last_err}")
