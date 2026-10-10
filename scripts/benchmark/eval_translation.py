# -*- coding: utf-8 -*-
"""模型评估基准：裸测翻译质量 + 速度 + 成本（独立可复用）。

用途：未来替换/新增翻译模型时自动打分选型。测"裸模型能力"（直连 API 看真实水平），
不降级不重试（避免掩盖短板）；可选 --pipeline 走 translate_runner 看管线兜底。

架构（两层分离，见 doc/方案归档/模型评估基准测试方案_2026-09-06.md）：
- 裸测层（默认）：直连各后端，用管线同款块格式 00017|行 + 同款 SYS prompt +
  同款校验函数（aa_translate 权威实现），但不走 call_with_fallback 降级/重试。
  模型 art 列位翻错 → 如实记失败（报告标注"管线中会触发 retry/落人工"）。
- --pipeline（可选）：对指定章节真跑 translate_runner，测端到端 + 兜底。

9 项测试：1常规/2粗口/3R18/4术语遵循/5AA行识别保持/6mix列位/7管线真实/8人工易错段/9极限。

用法:
  python scripts/benchmark/eval_translation.py                     # 裸测全部配置模型
  python scripts/benchmark/eval_translation.py --model runinfra/deepseek-v4-flash
  python scripts/benchmark/eval_translation.py --backend ri,di
  python scripts/benchmark/eval_translation.py --only 1,6,9 --weights art:2
  python scripts/benchmark/eval_translation.py --pipeline --chapters entry_17808
"""
import argparse, json, os, re, sys, time, urllib.request, urllib.error

# 向上两级到 scripts/（与子目录脚本一致，import aa_translate 权威实现）
_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import aa_config
try:
    import aa_translate as T
except Exception:
    T = None  # 校验函数缺失时用内置兜底（正常情况不应发生）
import aa_backend as AB


# ----------------------------------------------------------------------------
# 测试用例库：从 lib/cases.json 加载（人工可编辑改用例/术语/评分参数）
# ----------------------------------------------------------------------------
_CASES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib", "cases.json")
def _load_cases():
    with open(_CASES_PATH, encoding="utf-8") as f:
        return json.load(f)
C = _load_cases()

def _src(case_key):
    """取某 case 的 SRC 字典 {行号: 原文}。case9-extreme 特例用 long/hankaku 子块。
    JSON key 是字符串，返回前转 int 行号（与块格式 %05d 兼容）。"""
    return {int(k): v for k, v in C[case_key]["src"].items()}

def _block(case_key):
    """按 行号|SRC 拼管线块格式。case9-extreme 特例不适用（两子块单独拼）。"""
    return "\n".join(f"{i:05d}|{s}" for i, s in _src(case_key).items())

# 评分参数（JSON 可调）
DEDUCT = C["_scores"]["deduct"]       # {missing,kana,art,width}
COST_W = C["_scores"]["cost_weight"]
BASE_COST = C["_scores"]["base_cost_per_m"]
MIN_COST = C["_scores"]["min_cost_per_m"]


# ----------------------------------------------------------------------------
# 模型接入清单（本次测试对象 = 2026-09-05 探针已验证的 6 个）
# ----------------------------------------------------------------------------
MODELS = [
    {"name": "runinfra/deepseek-v4-flash",   "backend": "runinfra",  "model": "deepseek-v4-flash",        "r18": True,  "reasoning": "none"},
    {"name": "runinfra/glm-5-3-flash",       "backend": "runinfra",  "model": "glm-5-3-flash",            "r18": True,  "reasoning": "low"},
    {"name": "runinfra/qwen3-8-27b",         "backend": "runinfra",  "model": "qwen3-8-27b",              "r18": True,  "reasoning": "none"},
    {"name": "runinfra/ornith-1-5-35b",      "backend": "runinfra",  "model": "ornith-1-5-35b",           "r18": True,  "reasoning": "none"},
    {"name": "deepinfra/zai-org/GLM-5.3-Flash",     "backend": "deepinfra", "model": "zai-org/GLM-5.3-Flash",       "r18": True,  "reasoning": None},
    {"name": "deepinfra/deepseek-ai/DeepSeek-V4-Flash-0731", "backend": "deepinfra", "model": "deepseek-ai/DeepSeek-V4-Flash-0731", "r18": True, "reasoning": "none"},
    # commandcode 网关: reasoning_effort=none 被 400 拒(枚举 low|medium|high|xhigh|max);
    # 思考关不掉(thinking/enable_thinking/chat_template_kwargs 全被网关静默忽略, 实测 8 变体),
    # 故 max_tokens 抬到生产值, 否则思考吃光预算 content 空被伪判 0 分。
    {"name": "commandcode/deepseek-v4.1-flash", "backend": "commandcode", "model": "deepseek/deepseek-v4.1-flash", "r18": True, "reasoning": "low", "max_tokens": 16384},
    # deepseek 官网: 主 url 是 /responses(非 OpenAI chat), 故 spec 覆盖端点/参数/直连。
    # 思考开关(官方文档 guides/thinking_mode): chat 用 thinking:{type:disabled} —— 实测生效(1.0s/78tok)。
    {"name": "deepseek/deepseek-flash", "backend": "deepseek", "model": "deepseek-flash", "r18": True,
     "reasoning": None, "url": "https://api.deepseek.com/chat/completions",
     "extra": {"thinking": {"type": "disabled"}}, "direct": True},
    # commandcode muse-spark 1.2 vs 1.3 (contributor): 推理模型必须 reasoning_effort=low
    # (无字段 = 默认高思考: 1.2 实测 31.9s/5856tok, 1.3 87.2s/6721tok), 故 max_tokens 抬到生产值。
    {"name": "commandcode/muse-spark-1.2-contributor", "backend": "commandcode", "model": "meta/muse-spark-1.2-contributor", "r18": True, "reasoning": "low", "max_tokens": 16384},
    {"name": "commandcode/muse-spark-1.3-contributor", "backend": "commandcode", "model": "meta/muse-spark-1.3-contributor", "r18": True, "reasoning": "low", "max_tokens": 16384},
    # 小米 MiMo 官网 (api.xiaomimimo.com): 国内直连(不走代理)。思考开关 = thinking:{type:disabled}
    # (实测 v2.6 flash/pro 均生效: reason_tok=0); 官方文档 mimo-v2.6 系列 2026-09-22 发布。
    # **r18=False: V2.6 两模型对 case3(R18) 一律 `finish_reason=content_filter`**
    #   ("The request was rejected because it was considered high risk", 1.3s/1.9s) → 不进 r18 链。
    # 30 行困难块仍需给足预算 → max_tokens 用生产值 16384 (实测闭思考 case10 满分行数)。
    {"name": "mimo/mimo-v2.6-flash", "backend": "mimo", "model": "mimo-v2.6-flash", "r18": False,
     "reasoning": None, "extra": {"thinking": {"type": "disabled"}}, "direct": True, "max_tokens": 16384},
    {"name": "mimo/mimo-v2.6-pro", "backend": "mimo", "model": "mimo-v2.6-pro", "r18": False,
     "reasoning": None, "extra": {"thinking": {"type": "disabled"}}, "direct": True, "max_tokens": 16384},
    # 思考开启形态**不入清单**（实测不可用于 30 行块，见 temp/probe_mimo_think.log）：
    #   case1 六行 23.6s(pro)/29.0s(flash)，思考占 2108/5036 tok；
    #   case10 三十行 302.6s(pro)/171.9s(flash)，思考吃满 32768 预算 → finish=length、content 空、0 行。
    # ---- 2026-09-23 第三方托管的小米 MiMo V2.6-Flash（同模型、不同站点）----
    # deepinfra 托管版 (XiaomiMiMo/MiMo-V2.6-Flash): **思考开关可用** = reasoning_effort=none
    #   (探针 6 行块: none → reason_tok=0/11.5s; 无字段 → 671tok/28.8s; low → 1961tok/210.2s;
    #    thinking:{type:disabled} 被 deepinfra 忽略 → 796tok/44.5s)。R18 见 case3。
    {"name": "deepinfra/XiaomiMiMo/MiMo-V2.6-Flash", "backend": "deepinfra",
     "model": "XiaomiMiMo/MiMo-V2.6-Flash", "r18": True, "reasoning": "none"},
    # commandcode 网关托管版 (xiaomi/mimo-v2.6-flash): cc 网关 reasoning_effort 枚举 low|medium|high|xhigh|max
    #   (none → 400)，故用 low + 生产预算 16384。
    {"name": "commandcode/xiaomi/mimo-v2.6-flash", "backend": "commandcode",
     "model": "xiaomi/mimo-v2.6-flash", "r18": True, "reasoning": "low", "max_tokens": 16384},
]

# 价格表（2026-09-06 快照，$/1M token）。换价需人工更新。
PRICES = {
    "runinfra/deepseek-v4-flash":             (0.13, 0.27),
    "runinfra/glm-5-3-flash":                 (0.13, 0.27),
    "runinfra/qwen3-8-27b":                   (0.13, 0.27),
    "runinfra/ornith-1-5-35b":                (0.13, 0.27),
    "deepinfra/zai-org/GLM-5.3-Flash":        (0.35, 0.35),
    # 2026-09-23 实测 deepinfra /v1/openai/models 实时价: in $0.06 / out $0.18 (cache_read $0.015)
    # (2026-09-06 快照曾为 $0.09/$0.09 —— 输入降价、输出涨价)
    "deepinfra/deepseek-ai/DeepSeek-V4-Flash-0731": (0.06, 0.18),
    # commandcode 官网模型页 https://commandcode.ai/models/deepseek-v4-1-flash：
    # off-peak $0.15/$0.60 (cache $0.003)，peak(周一~五 01-04 & 06-10 UTC) 翻倍 $0.30/$1.20。
    "commandcode/deepseek-v4.1-flash":        (0.15, 0.60),
    # deepseek 官网 deepseek-flash(V4.1-Flash) 官方价: 输入未命中 ¥1/M、输出 ¥4/M(空闲时段, 高峰翻倍),
    # 按 ¥7.15/$ 折算 ≈ $0.14/$0.56。缓存命中 ¥0.02/M 未计入。
    "deepseek/deepseek-flash":                (0.14, 0.56),
    # commandcode 文档标价 1.2/1.3 contributor 同为 $0.10/$0.20（模型页 no markup 口径）
    "commandcode/muse-spark-1.2-contributor": (0.10, 0.20),
    "commandcode/muse-spark-1.3-contributor": (0.10, 0.20),
    # 小米 MiMo 官网按量计费(海外价, mimo.mi.com/docs/zh-CN/price/pay-as-you-go, 2026-09-21 更新):
    # flash $0.0028/$0.14/$0.28, pro $0.0036/$0.435/$0.87 (hit/miss/out); 国内价 ¥0.02/¥1/¥2 与 ¥0.025/¥3/¥6。
    "mimo/mimo-v2.6-flash": (0.14, 0.28),
    "mimo/mimo-v2.6-pro":   (0.435, 0.87),
    # 2026-09-23 第三方托管版同价 (deepinfra 实时价 / commandcode 模型页): flash $0.14/$0.28 (cache_read $0.0028)
    "deepinfra/XiaomiMiMo/MiMo-V2.6-Flash": (0.14, 0.28),
    "commandcode/xiaomi/mimo-v2.6-flash":   (0.14, 0.28),
}
BASE_COST = 2.0  # 成本折算基准 ($/1M, 参照 dashscope qwen3.5-27b 量级)
MIN_COST = 0.09
COST_W = 0.30  # 综合分成本权重

PROXY = aa_config.proxy_dict()   # 代理统一口径（2026-10-08 阶段 4）
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


# ----------------------------------------------------------------------------
# 复用 aa_translate 权威校验函数（缺模块时本地兜底）
# ----------------------------------------------------------------------------
def _art_ok(src, cand, tol=4, min_art=3):
    if T and hasattr(T, "art_stroke_preserved"):
        return T.art_stroke_preserved(src, cand, tol=tol, min_art=min_art)
    return _art_ok_fallback(src, cand, tol, min_art)

def _art_ok_fallback(src, cand, tol=4, min_art=3):
    ART = set('／＼│｜┃━─┌┐└┘├┤┬┴┼＿￣|()[]（）ｰ⊂⊃ミ彡乂爻≧≦ﾆﾄﾘ:;,。ヽﾉゝゞノニマハレヘ〆=<>^vV~-+')
    def anchors(line):
        out, col = [], 0
        for c in line:
            if not c.isspace() and c in ART:
                out.append((c, col))
            col += 1 if ord(c) < 128 else 2
        return out
    a = anchors(src)
    if len(a) < min_art:
        return True
    ac = {}
    for c, col in anchors(cand):
        ac.setdefault(c, []).append(col)
    for c, col in a:
        if not any(abs(col - cc) <= tol for cc in ac.get(c, [])):
            return False
    return True

def _kana_residual(line):
    if T and hasattr(T, "kana_runs"):
        return T.kana_runs(line)
    art = set("つだりへぢづゑゐいうしふとこっッノミニハマレヘヾーｰ")
    return [s for s in re.findall(r"[ぁ-んァ-ヶ]+", line)
            if len(re.sub("[" + "".join(art) + "]", "", s)) >= 2]

def _dw(s):
    if T and hasattr(T, "disp_width"):
        return T.disp_width(s)
    return sum(1 if ord(c) < 128 else 2 for c in s)

def _pad(line, target):
    """管线同款 pad：短则补 U+3000 到 target 宽（只补不裁）。"""
    if T and hasattr(T, "pad_to_width"):
        try:
            return T.pad_to_width(line, target)
        except Exception:
            pass
    w = _dw(line)
    if w < target:
        need = target - w
        full, half = divmod(need, 2)
        return line + "\u3000" * full + (" " if half else "")
    return line

def _width_ok(src, cand):
    """宽度判定：pad 候选到 SRC 宽后再比（模拟管线落盘前 pad）。"""
    return _dw(cand) <= _dw(src) or _dw(_pad(cand, _dw(src))) == _dw(src)

def _parse(text):
    if T and hasattr(T, "parse_result"):
        return T.parse_result(text)
    out = {}
    for line in text.splitlines():
        m = re.match(r"^(\d{1,5})\|(.*)$", line.strip())
        if m:
            out[int(m.group(1))] = m.group(2)
    return out

def _is_text_line(line):
    if T and hasattr(T, "is_text_line"):
        return T.is_text_line(line)
    return bool(re.search(r"[ぁ-んァ-ヶ]", line))

def _line_kind(line):
    if T and hasattr(T, "line_kind"):
        return T.line_kind(line)
    return "text" if _is_text_line(line) else "art"

_SYS = None
def build_sys():
    global _SYS
    if _SYS:
        return _SYS
    if T and hasattr(T, "SYSTEM"):
        _SYS = T.SYSTEM
    else:
        _SYS = ("你是やる夫AA漫画的资深汉化翻译。用户会发来若干行AA漫画文本，每行以「序号|」开头。"
                "这些行【全部】包含日语文字，需要你翻译。\n"
                "要求：1.把每行日语翻译成简体中文，自然贴切，不增删。\n"
                "2.行内绘画字符(全角空格/边框/半角片假名/ASCII艺术符)必须原样保留。\n"
                "3.译文显示宽度必须与原行一致，缩短用全角空格填充原文字区域。\n"
                "4.译文必须比原文更短或等长，禁止残留原文文字。\n5.角色名保留日文。\n"
                "6.必须翻译【所有】行，纯绘画行原样照抄。\n7.输出仅「序号|完整行」，无其他内容。")
    return _SYS


# ----------------------------------------------------------------------------
# 直连调用
# ----------------------------------------------------------------------------
def _url_for(backend):
    return aa_config.backend(backend).get("url", "")

def _key_for(backend):
    envname = aa_config.backend_key_env(backend)
    if envname:
        return AB._read_env_key(envname, AB._env_file_paths())
    return None

def _model_for(backend, override=None):
    if override:
        return override
    envname = aa_config.model_env(backend)
    if envname and os.environ.get(envname):
        return os.environ[envname]
    return aa_config.backend(backend).get("model", "")

def direct_call(spec, body, max_tokens=2048, timeout=180, temperature=0.4):
    # 思考模型(如 cc/deepseek-v4.1-flash, reasoning 无法关闭)会吃掉整个输出预算 →
    # spec 可显式给 "max_tokens" 抬高上限(生产 = [default].max_output_tokens)。
    max_tokens = spec.get("max_tokens") or max_tokens
    # spec 可覆盖端点(同一后端多端点/或后端主 url 非 OpenAI chat 格式时, 如 deepseek 官网 /responses)
    url = spec.get("url") or _url_for(spec["backend"])
    if not url:
        raise RuntimeError(f"{spec['name']} 无 url")
    model = _model_for(spec["backend"], spec.get("model"))
    key = _key_for(spec["backend"])
    if not key:
        raise RuntimeError(f"{spec['name']} 无 key")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": build_sys()},
            {"role": "user", "content": body}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    rz = spec.get("reasoning")
    if rz:
        payload["reasoning_effort"] = rz
    # spec 可注入后端专属参数(如 deepseek 官网关思考: {"thinking":{"type":"disabled"}})
    payload.update(spec.get("extra") or {})
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "User-Agent": UA,
    })
    # spec["direct"]=True 绕过代理(国内直连端点, 如 api.deepseek.com)
    opener = (urllib.request.build_opener() if spec.get("direct")
              else urllib.request.build_opener(urllib.request.ProxyHandler(PROXY)))
    # 网络重试（runinfra 偶发 10054/429；基准测试需耐受，重试 3 次）
    last_err = None
    for attempt in range(3):
        t0 = time.time()
        try:
            with opener.open(req, timeout=timeout) as r:
                resp = json.load(r)
            dt = time.time() - t0
            msg = resp["choices"][0]["message"].get("content", "")
            if not msg:
                raise RuntimeError("空响应: " + json.dumps(resp, ensure_ascii=False)[:200])
            u = resp.get("usage", {}) or {}
            p = u.get("input_tokens") or u.get("prompt_tokens") or 0
            c = u.get("output_tokens") or u.get("completion_tokens") or 0
            return msg, {"prompt": p, "completion": c, "total": p + c, "sec": dt}
        except Exception as e:
            last_err = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{spec['name']} 直连失败(重试3次): {last_err}")


# ----------------------------------------------------------------------------
# 9 项测试（除 7 管线真实）—— 用独立用例块构造，与 _deduct 解耦
# ----------------------------------------------------------------------------
def _fail_case(e, n_lines):
    return {"score": 0, "items": [], "usage": None, "raw": None,
            "detail": {"missing": n_lines, "kana": 0, "error": str(e)}}

def _deduct_items(res, src_map):
    detail = {"missing": 0, "kana": 0, "art": 0, "width": 0}
    items = []
    for i in src_map:
        if i not in res:
            detail["missing"] += 1
            items.append({"line": i, "ok": False, "score": 0, "msg": "缺行"})
    for i, s in src_map.items():
        if i not in res:
            continue
        t = res[i]
        reasons = []
        art_bad = False
        if _line_kind(s) in ("text", "mixed") and not _art_ok(s, t):
            art_bad = True
            detail["art"] += 1
            reasons.append("art列位坏")
        kr = _kana_residual(t)
        if kr:
            detail["kana"] += len(kr)
            reasons.append("假名残留" + str(kr))
        if _width_ok(s, t):
            pass
        else:
            detail["width"] += 1
            reasons.append(f"宽{_dw(s)}→{_dw(t)}")
        if art_bad:
            items.append({"line": i, "ok": False, "score": max(0, 100 - DEDUCT["art"]),
                          "msg": ";".join(reasons)})
        elif reasons:
            items.append({"line": i, "ok": False, "score": max(0, 100 - DEDUCT["kana"]),
                          "msg": ";".join(reasons)})
        else:
            items.append({"line": i, "ok": True, "score": 100, "msg": ""})
    return detail, items

def _score(detail, n):
    nt = n or 1
    pen = detail.get("missing", 0) * DEDUCT["missing"] + detail.get("kana", 0) * DEDUCT["kana"] + \
          detail.get("art", 0) * DEDUCT["art"] + detail.get("width", 0) * DEDUCT["width"]
    if detail.get("missing", 0) >= nt:
        return 0
    return max(0, 100 - pen)

# 用例 1：常规翻译
def case_normal(spec):
    src = _src("case1_normal")
    try:
        msg, usage = direct_call(spec, _block("case1_normal"))
    except Exception as e:
        return _fail_case(e, len(src))
    res = _parse(msg)
    detail, items = _deduct_items(res, src)
    return {"score": _score(detail, len(src)), "items": items, "usage": usage,
            "raw": msg, "detail": detail}


# 用例 2：粗口
def case_vulgar(spec):
    src = _src("case2_vulgar")
    try:
        msg, usage = direct_call(spec, _block("case2_vulgar"))
    except Exception as e:
        return _fail_case(e, len(src))
    res = _parse(msg)
    missing = [i for i in src if i not in res]
    kana = sum(len(_kana_residual(res[i])) for i in res if _kana_residual(res[i]))
    score = 100 - len(missing) * DEDUCT["missing"] - kana * DEDUCT["kana"]
    return {"score": max(0, score), "items": [], "usage": usage, "raw": msg,
            "detail": {"missing": len(missing), "kana": kana}}


# 用例 3：R18（仅 r18=true 后端跑）
def case_r18(spec):
    if not spec.get("r18"):
        return {"score": None, "n/a": True, "usage": None, "raw": None,
                "detail": None, "reason": "后端 r18=false，跳过（N/A）"}
    src = _src("case3_r18")
    try:
        msg, usage = direct_call(spec, _block("case3_r18"))
    except Exception as e:
        return _fail_case(e, len(src))
    res = _parse(msg)
    missing = [i for i in src if i not in res]
    kana = sum(len(_kana_residual(res[i])) for i in res if _kana_residual(res[i]))
    score = 100 - len(missing) * DEDUCT["missing"] - kana * DEDUCT["kana"]
    if not res:
        score = 0  # 空输出=审核拦截/拒译
    return {"score": max(0, score), "items": [], "usage": usage, "raw": msg,
            "detail": {"missing": len(missing), "kana": kana}}


# 用例 4：术语表遵循
def case_glossary(spec):
    src = _src("case4_glossary")
    pairs = C["case4_glossary"]["pairs"]
    hint = C["case4_glossary"]["hint"]
    try:
        msg, usage = direct_call(spec, hint + _block("case4_glossary"))
    except Exception as e:
        return _fail_case(e, len(src))
    res = _parse(msg)
    hits = {}
    for i, s in src.items():
        if i not in res:
            continue
        t = res[i]
        hits[i] = sum(1 for jp, zh in pairs.items() if jp in s and zh in t)
    total_terms = sum(1 for s in src.values() for jp in pairs if jp in s)
    got_terms = sum(hits.values())
    score = 100 * (got_terms / total_terms) if total_terms else 100
    kana = sum(len(_kana_residual(res[i])) for i in res if _kana_residual(res[i]))
    score -= kana * DEDUCT["kana"]
    return {"score": max(0, score), "items": [], "usage": usage, "raw": msg,
            "detail": {"terms": f"{got_terms}/{total_terms}", "kana": kana}}


# 用例 5：AA 行识别 / 被要求翻译时保持 AA 不变
def case_art_keep(spec):
    src = _src("case5_art_keep")
    try:
        msg, usage = direct_call(spec, _block("case5_art_keep"))
    except Exception as e:
        return _fail_case(e, len(src))
    res = _parse(msg)
    unchanged = sum(1 for i, s in src.items()
                    if i in res and _dw(res[i]) == _dw(s))
    score = 100 * (unchanged / len(src))
    return {"score": round(score), "items": [], "usage": usage, "raw": msg,
            "detail": {"unchanged": f"{unchanged}/{len(src)}"}}


# 用例 6：mix 行翻译 AA 位置保持
def case_mix(spec):
    src = _src("case6_mix")
    try:
        msg, usage = direct_call(spec, _block("case6_mix"))
    except Exception as e:
        return _fail_case(e, len(src))
    res = _parse(msg)
    missing = [i for i in src if i not in res]
    art_bad = [i for i, s in src.items() if i in res and not _art_ok(s, res[i])]
    width_bad = [i for i, s in src.items() if i in res and not _width_ok(s, res[i])]
    score = 100 - len(missing) * DEDUCT["missing"] - len(art_bad) * DEDUCT["art"] - len(width_bad) * DEDUCT["width"]
    return {"score": max(0, score), "items": [], "usage": usage, "raw": msg,
            "detail": {"missing": len(missing), "art_bad": art_bad, "width_bad": width_bad}}


# 用例 8：人工修改易错段（黄金基准）
def case_human_edit(spec):
    src = _src("case8_human_edit")
    try:
        msg, usage = direct_call(spec, _block("case8_human_edit"))
    except Exception as e:
        return _fail_case(e, len(src))
    res = _parse(msg)
    missing = [i for i in src if i not in res]
    kana = sum(len(_kana_residual(res[i])) for i in res if _kana_residual(res[i]))
    score = 100 - len(missing) * DEDUCT["missing"] - kana * DEDUCT["kana"]
    return {"score": max(0, score), "items": [], "usage": usage, "raw": msg,
            "detail": {"missing": len(missing), "kana": kana}}


# 用例 9：极限测试（超长行 + 半角假名密集 + 艺术假名伪装台词）
def case_extreme(spec):
    long_src = {int(k): v for k, v in C["case9_extreme"]["long"].items()}
    hk_src = {int(k): v for k, v in C["case9_extreme"]["hankaku"].items()}
    long_block = "\n".join(f"{i:05d}|{s}" for i, s in long_src.items())
    hk_block = "\n".join(f"{i:05d}|{s}" for i, s in hk_src.items())
    try:
        m1, u1 = direct_call(spec, long_block)
    except Exception as e:
        return _fail_case(e, 1)
    try:
        m2, u2 = direct_call(spec, hk_block)
    except Exception as e:
        m2, u2 = "", {"prompt": 0, "completion": 0, "total": 0, "sec": 0}
    res_long = _parse(m1); res_hk = _parse(m2)
    l_missing = [i for i in long_src if i not in res_long]
    l_art = sum(1 for i, s in long_src.items() if i in res_long and not _art_ok(s, res_long[i], tol=6))
    l_kana = sum(len(_kana_residual(res_long[i])) for i in res_long if _kana_residual(res_long[i]))
    hk_missing = [i for i in hk_src if i not in res_hk]
    hk_kana = sum(len(_kana_residual(res_hk[i])) for i in res_hk if _kana_residual(res_hk[i]))
    score = 100
    score -= len(l_missing) * DEDUCT["missing"] + l_art * DEDUCT["art"] + l_kana * DEDUCT["kana"]
    score -= len(hk_missing) * DEDUCT["missing"] + hk_kana * 3  # 半角拟声 art 装饰容忍(轻罚)
    usage = {"prompt": u1.get("prompt", 0) + u2.get("prompt", 0),
             "completion": u1.get("completion", 0) + u2.get("completion", 0),
             "total": u1.get("total", 0) + u2.get("total", 0),
             "sec": u1.get("sec", 0) + u2.get("sec", 0)}
    return {"score": max(0, score), "items": [], "usage": usage,
            "raw": m1 + "\n" + m2,
            "detail": {"long_missing": len(l_missing), "long_art": l_art,
                       "long_kana": l_kana, "hk_missing": len(hk_missing), "hk_kana": hk_kana}}


# 用例 7：管线真实层（--pipeline）
def case_pipeline(spec, chapters):
    import subprocess
    runner = os.path.join(_SCRIPTS, "runner", "translate_runner.py")
    cmd = [sys.executable, runner, "--work", "できる夫はヴァーレン大陸で成り上がるようです",
           "--chapters", chapters, "--force", "--no-proofread"]
    env = dict(os.environ)
    env["AA_BACKEND"] = spec["backend"]
    if spec["backend"] == "runinfra":
        env["AA_RI_MODEL"] = spec.get("model", "")
    elif spec["backend"] == "deepinfra":
        env["AA_DI_MODEL"] = spec.get("model", "")
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=1200)
        ok = proc.returncode == 0
        return {"score": 100 if ok else 0, "items": [], "usage": None,
                "raw": (proc.stdout or "")[-2000:] + "\n" + (proc.stderr or "")[-1000:],
                "detail": {"returncode": proc.returncode, "sec": round(time.time() - t0, 1)}}
    except Exception as e:
        return {"score": 0, "items": [], "usage": None, "raw": None,
                "detail": {"error": str(e)}}


# 用例 10：长困难块压力（30 行 art 密集 mixed 整行，21095 事故语料）
def case_hard_block(spec):
    src = _src("case10_hard_block")
    body = "\n".join(f"{i:05d}|{s}" for i, s in src.items())
    try:
        msg, usage = direct_call(spec, body, max_tokens=4096)
    except Exception as e:
        return _fail_case(e, len(src))
    res = _parse(msg)
    missing = [i for i in src if i not in res]
    # 已返回行: art 列位校验(整行翻译必须保持图形)
    art_bad = []
    for i, s in src.items():
        if i in res:
            # 纯 art 行(无可译假名)允许原样; 有台词的 mixed 行须 art 列位保持
            if not _art_ok(s, res[i], tol=6, min_art=3):
                art_bad.append(i)
    # 计分: 缺行是主罚(30行块漏行=事故根因), art 列位次之
    score = 100
    score -= len(missing) * (DEDUCT["missing"] * 2)   # 缺行双倍罚(长块漏行严重)
    score -= len(art_bad) * DEDUCT["art"]
    return {"score": max(0, score), "items": [], "usage": usage, "raw": msg,
            "detail": {"missing": sorted(missing), "n_missing": len(missing),
                       "art_bad": sorted(art_bad), "n_lines": len(src)}}


# ----------------------------------------------------------------------------
# 聚合 & 报告
# ----------------------------------------------------------------------------
CASES = [(1, "常规翻译", case_normal), (2, "粗口", case_vulgar), (3, "R18", case_r18),
         (4, "术语表遵循", case_glossary), (5, "AA行识别保持", case_art_keep),
         (6, "mix列位保持", case_mix), (8, "人工修改易错段", case_human_edit),
         (9, "极限测试", case_extreme), (10, "长困难块压力", case_hard_block)]

def run_all(spec, only=None):
    results = {}
    for cid, name, fn in CASES:
        if only and cid not in only:
            continue
        results[cid] = fn(spec)
    return results

def _model_price(spec):
    # 去掉 --max-tokens/--reasoning 覆盖后缀（name[mt32768,rzlow]），否则价格查表失配
    base = spec["name"].split("[", 1)[0]
    p = PRICES.get(base)
    if not p:
        for k, v in PRICES.items():
            if base.startswith(k.rsplit("/", 1)[0] + "/"):
                p = v; break
    return p or (0.13, 0.27)

def _cost_score(spec):
    info = _model_price(spec)
    avg = (info[0] + info[1]) / 2
    span = max(BASE_COST - MIN_COST, 1e-9)
    return max(0, 100 - 100 * (avg - MIN_COST) / span)

def aggregate(results, spec):
    scores = [r["score"] for r in results.values() if r.get("score") is not None]
    if not scores:
        return 0.0
    quality = sum(scores) / len(scores)
    cost = _cost_score(spec)
    return quality * (1 - COST_W) + cost * COST_W

def _mk_report(results, spec, md_parts):
    """每模型产出 JSON 明细 + 汇总 markdown 行。"""
    comp = aggregate(results, spec)
    meta = {"model": spec["name"], "backend": spec["backend"], "model_id": spec["model"],
            "reasoning": spec.get("reasoning"), "r18": spec.get("r18"), "composite": round(comp, 1)}
    for cid, r in results.items():
        meta[f"case_{cid}"] = r.get("score")
    # 速度/成本汇总
    usages = [r.get("usage") for r in results.values() if r.get("usage")]
    total_sec = sum(u.get("sec", 0) for u in usages)
    total_tok = sum(u.get("total", 0) for u in usages)
    p = _model_price(spec)
    avg = (p[0] + p[1]) / 2
    meta["speed_sec"] = round(total_sec, 1)
    meta["tokens"] = total_tok
    meta["price_per_m"] = avg
    md_parts.append({"name": spec["name"], "composite": round(comp, 1),
                     "cases": {cid: r.get("score") for cid, r in results.items()},
                     "speed": round(total_sec, 1), "price": avg, "r18": spec.get("r18")})
    return meta, md_parts


def write_reports(results_list, outdir, date):
    """全部模型集中写 markdown + 各模型 JSON。"""
    os.makedirs(outdir, exist_ok=True)
    # markdown 汇总表
    lines = ["# 模型评估报告（基准测试）", "", f"> 日期: {date}", "",
             "| 模型 | 综合分 | 常规 | 粗口 | R18 | 术语 | AA识别 | mix列位 | 人工易错 | 极限 | 长困难块 | 均速(秒) | 价格($/M) |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for m in _md_rows:
        c = m["cases"]
        def cell(v):
            return "N/A" if v is None else f"{v:g}"
        # R18 位置: cases[3]; AA识别: cases[5]; mix: cases[6]; 人工: cases[8]; 极限: cases[9]; 长困难块: cases[10]
        lines.append(
            f"| {m['name']} | {m['composite']:g} | {cell(c.get(1))} | {cell(c.get(2))} | {cell(c.get(3))} | "
            f"{cell(c.get(4))} | {cell(c.get(5))} | {cell(c.get(6))} | {cell(c.get(8))} | {cell(c.get(9))} | "
            f"{cell(c.get(10))} | "
            f"{m['speed']:g} | {m['price']:g} |")
    lines += ["", "## 计分说明",
              "- 每项 0~100 扣分制：缺行-10/行、假名残留-5/处、art列位-30/行、宽度-10/行",
              "- R18 对 r18=false 后端标 N/A，不进总分分母", "",
              "## 裸测 vs 管线", "- 裸测直连 API 不降级不重试，如实记失败；"
              "管线中 art 列位坏会触发 retry/落人工，最终不写坏盘。"]
    md_path = os.path.join(outdir, f"benchmark_{date}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    # 各模型 JSON
    for meta in results_list:
        jp = os.path.join(outdir, f"{meta['model'].replace('/', '_')}_{date}.json")
        with open(jp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
    return md_path, [os.path.join(outdir, f"{m['name'].replace('/', '_')}_{date}.json") for m in _md_rows]



# ----------------------------------------------------------------------------
# argparse CLI
# ----------------------------------------------------------------------------
def _select_models(args):
    """按 --backend/--model 过滤 MODELS。"""
    if args.model:
        names = [m.strip() for m in args.model.split(",") if m.strip()]
        return [m for m in MODELS if any(m["name"] in n or n in m["name"] for n in names)]
    if args.backend:
        allow = set(b.strip() for b in args.backend.split(",") if b.strip())
        alias = {"ri": "runinfra", "di": "deepinfra", "sf": "siliconflow", "cc": "commandcode"}
        allow = {alias.get(a, a) for a in allow}
        return [m for m in MODELS if m["backend"] in allow]
    return list(MODELS)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="模型评估基准：裸测翻译质量+速度+成本（独立可复用）",
        epilog="详见 doc/方案归档/模型评估基准测试方案_2026-09-06.md")
    ap.add_argument("--model", help="模型名过滤，逗号分隔（如 runinfra/deepseek-v4-flash）")
    ap.add_argument("--backend", help="后端过滤: ri/di 或 runinfra/deepinfra")
    ap.add_argument("--only", help="仅跑指定测试项，逗号分隔（如 1,6,9）")
    ap.add_argument("--pipeline", action="store_true", help="额外跑管线真实层")
    ap.add_argument("--chapters", default="entry_17808", help="--pipeline 时指定章节")
    ap.add_argument("--outdir", default=os.path.join(_SCRIPTS, "benchmark", "report"),
                    help="报告输出目录（默认 scripts/benchmark/report）")
    ap.add_argument("--dry", action="store_true", help="不调 API，只测脚本/校验函数（本地）")
    ap.add_argument("--max-tokens", type=int, default=None,
                    help="覆盖各模型的 max_tokens（思考不可关的模型调预算用，如 cc/deepseek-v4.1-flash 32768）")
    ap.add_argument("--reasoning", default=None,
                    help="覆盖 reasoning 字段（none/low/medium/high；cc 网关枚举只有 low|medium|high|xhigh|max）")
    args = ap.parse_args(argv)

    models = _select_models(args)
    # 全局覆盖：便于同一支模型扫不同 max_tokens/思考档（报告里名字带上覆盖值，避免同名混淆）
    for m in models:
        if args.max_tokens:
            m["max_tokens"] = args.max_tokens
        if args.reasoning:
            m["reasoning"] = None if args.reasoning in ("none", "-") else args.reasoning
        tag = []
        if args.max_tokens:
            tag.append("mt%d" % args.max_tokens)
        if args.reasoning:
            tag.append("rz%s" % args.reasoning)
        if tag:
            m["name"] = m["name"] + "[" + ",".join(tag) + "]"
    only = [int(x) for x in args.only.split(",")] if args.only else None
    date = time.strftime("%Y%m%d")

    if args.dry:
        print("=== DRY RUN: 不调 API，验证校验函数与语料构造 ===")
        for spec in models:
            print(f"[{spec['name']}] building_sys={len(build_sys())} chars, "
                  f"case1={len(_src('case1_normal'))} lines")
        for cid, name, fn in CASES:
            print(f"  用例 {cid} {name}: 函数 {fn.__name__} 就绪")
        print("DRY OK")
        return 0

    global _md_rows
    _md_rows = []
    results_list = []
    for spec in models:
        print(f"\n{'='*60}\n=== 测试 {spec['name']} ({spec['backend']}/{spec['model']}) ===", flush=True)
        results = run_all(spec, only=only)
        for cid, r in sorted(results.items()):
            s = r.get("score")
            detail = r.get("detail") or {}
            u = r.get("usage") or {}
            t = ("  %5.1fs" % u["sec"]) if u.get("sec") else ""
            print(f"  case{cid:>2}: {'N/A' if s is None else f'{s:g}'}{t}  {detail}", flush=True)
        if args.pipeline:
            p = case_pipeline(spec, args.chapters)
            results[7] = p
            print(f"  case 7 pipeline: {p.get('score')}  {p.get('detail')}")
        meta, _md_rows = _mk_report(results, spec, _md_rows)
        results_list.append(meta)
        # 增量落盘：思考不可控/巨慢的模型可能卡死整批，逐模型写报告避免全丢（2026-09-22）
        write_reports(results_list, args.outdir, date)

    md_path, json_paths = write_reports(results_list, args.outdir, date)
    print(f"\n报告: {md_path}")
    for jp in json_paths:
        print(f"  {jp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
