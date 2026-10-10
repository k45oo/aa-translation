# -*- coding: utf-8 -*-
"""refine_aa.py —— 独立精译流程（2026-09-06，详见 doc/方案归档/refine_aa独立精译流程方案_2026-09-06.md）。

为已汉化作品做三类质检修复：错位修正 / 漏译补译 / 错译还原，一次遍历全覆盖。
区别于主流程（整行翻译/词级替换）——本流程以段级重建为主路 + 模型裁决。

核心思想（Q8 统一判定循环）：
  对每行：① 现场 segment_line 重判 segs（不信磁盘 meta.kind，Q3）
          ② 按 有台词/无台词/留日文 落三种动作
          ③ 段级重建 / 还原 SRC / 补译
  review_inline 作补充输入，模型三态裁决（补译/还原/跳过）（Q5）

用法：
  python scripts/fix/refine_aa.py <work> [--eids E1,E2] [--dry-run|--write] [--review-inline] [--force]
  --dry-run 默认：报告 + 打印重建对比，不写盘
  --write   写回汉化 txt（自动备份到 work/backup_refine_aa_<ts>/）
  --force   忽略章级增量 ckpt 强制重校（AA_REFINE_FORCE=1 同效；任务入队 --env AA_REFINE_FORCE=1）

章级增量（2026-09-15）：--write 时每章落 work/refine_ckpt/<base>.json（指纹=原文+汉化文件 sha1），
  指纹未变即零调用跳过 —— 重译/改行/术语替换会自动使指纹失效重校。

模型链（2026-09-22「与付费链一致」）：默认**跟随付费链** —— 与主翻译同一批端点、同一模型
  （normal: `mimo-v2.6-flash` → `deepseek-flash` → `deepinfra DeepSeek-V4.1-Flash` → `commandcode` 兜底；
   作品 `r18_level=high` → 按 `r18_backends` 过滤，mimo 不在其中）。逐项降级：前一个自身重试耗尽即换下一个。
  链来源 env `AA_REFINE_BACKEND`（显式，原样不过滤）> TOML `[refine_aa].backends`（`follow_default=false` 时）
  > 付费链。别名 glm/di/deepseek/ds/mimo；写单个即不降级（R18 大尺度作品可钉 `AA_REFINE_BACKEND=glm`
  = deepinfra 路径钉 GLM-5.3-Flash）。`AA_REFINE_MODEL` 只作用于 deepseek 项。
  单次调用预算：`AA_REFINE_CALL_TIMEOUT`（默认 25s）/ `AA_REFINE_CALL_RETRY`（默认 2），
  等价 TOML `[refine_aa].call_timeout` / `.call_retry`（原 160s×3 是 60~189s 长尾来源）。
"""
import sys, os, json, re, time, shutil, argparse, hashlib, functools, collections, concurrent.futures

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # scripts/
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fix"))

import aa_translate as T
import aa_common as C
from registry import get as _reg_get
import fix_mixed_drift as FMD     # rebuild_row / _zone_merge_rebuild / extract_translations 等

# 残留在译文里的连续日文假名(>=2) 判漏译；排除帖头/标题
KANA_RESIDUAL = re.compile(r'[\u3040-\u30ff]{2,}')
HEADER_RE = re.compile(r'^\d+\s*：|^\d+\s*名前')   # 帖头双格式（本棚系/FC2系）


# 2026-09-07: 作品术语表注入 hint(逐章按语料过滤)。_call_glm 拼进 system。
_GLOSS_HINT = ""


def _load_gloss_hint(cfg, base):
    """读作品术语表(glossary_<work>.json) manual 确认对, 按本章原文语料过滤, 生成注入段。
    返回空串=无可用术语(非 registry 作品/无表/无命中)。refine 补译/精译用它锁标准译名。

    2026-10-08（宝可梦链）: 该链的术语一致性由 pre_terms 翻译前强制替换保证，
    故**不再注入术语对**——预替换过的标准译名再注入只会给"模型把已落地译名改回去"留通道。
    P1 的**词表一致性护栏**（拦住模型改坏已落地译名）不受影响，那是另一条路。
    """
    global _GLOSS_HINT
    _GLOSS_HINT = ""
    try:
        import work_pipeline as _WP
        w = cfg.get("work") or ""
        if w and _WP.pipeline_of(w) == "pokemon":
            return ""
    except Exception:
        pass
    try:
        wdir = cfg.get("work_dir", "")
        if not wdir:
            return ""
        import glob
        # 精确匹配 glossary_<work名>.json: work 名 = aaManga 下目录名(cfg 无 work_id 字段)
        # 排除 glossary_candidates.json / glossary_iter.json / 副本 等非术语表文件
        gs = sorted(glob.glob(os.path.join(wdir, "glossary_*.json")))
        if not gs:
            return ""
        gp = None
        for f in gs:
            bn = os.path.basename(f)
            if bn.startswith("glossary_candidates") or bn.startswith("glossary_iter"):
                continue
            if "副本" in bn:
                continue
            gp = f
            break
        if gp is None:
            return ""
        d = json.load(open(gp, encoding="utf-8"))
        gl = d.get("glossary", []) if isinstance(d, dict) else d
        pairs = []
        for p in gl:
            if isinstance(p, dict):
                jp, zh = (p.get("jp") or "").strip(), (p.get("zh") or "").strip()
                src = p.get("来源", "manual")
            elif isinstance(p, list) and len(p) >= 2:
                jp, zh = str(p[0]).strip(), str(p[1]).strip()
                src = "manual"
            else:
                continue
            if not jp or not zh or jp.startswith("re:"):
                continue
            if src != "manual":
                continue  # 只注入人工确认对(auto 未确认不锁)
            pairs.append((jp, zh))
        if not pairs:
            return ""
        # 按本章原文语料过滤(词在本章出现才注入)
        src_p = os.path.join(cfg.get("aa_dir", ""), base + ".txt")
        corpus = ""
        if os.path.exists(src_p):
            try:
                corpus = open(src_p, encoding="utf-8", errors="replace").read()
            except OSError:
                corpus = ""
        hits = [(jp, zh) for jp, zh in pairs if not corpus or (jp in corpus or zh in corpus)]
        if not hits:
            return ""
        s = "、".join(f"{jp}→{zh}" for jp, zh in hits)
        _GLOSS_HINT = (f"\n【本作标准译名】以下词是本作已确认的标准译名，翻译时【必须】使用标准中文译名"
                       f"(优先级最高, 覆盖'专名保留日文'要求; 未列出的其他专名仍保留日文): {s}")
    except Exception:
        _GLOSS_HINT = ""
    return _GLOSS_HINT


# 校对模型后端链（2026-09-22「与付费链一致」）:
#   ① env `AA_REFINE_BACKEND`（显式，逗号分隔，原样用，不过滤；写单个即不降级）
#   ② TOML `[refine_aa].backends`（显式非空且 `follow_default=false` → 固定链）
#   ③ 默认 = **跟随付费链** = `aa_config.backend_chain(work_r18)`：与主翻译同一批端点、
#      同一模型（端点模型一律取链上覆盖：`[default].normal_backends[i].model` > `[backend.x].model`）。
#      作品 r18_level=high → 走 r18_backends（mimo 不在其中，避免无谓的 content_filter 失败）。
#   别名: deepinfra|di → deepinfra（模型随付费链）；glm → deepinfra 且**钉 GLM-5.3-Flash**；
#         deepseek|ds|官网 → deepseek；mimo → mimo；commandcode|cc → commandcode；未识别项忽略。
#   `AA_REFINE_MODEL` 仍只作用于 deepseek 项。
DEFAULT_REFINE_CHAIN = "deepseek,deepinfra"      # 仅当付费链不可读时的兜底（旧默认）
_DEFAULT_CHAIN_LIST = ["deepseek", "deepinfra"]
_GLM_MODEL = "zai-org/GLM-5.3-Flash"             # 显式钉 GLM（R18 大尺度备选；网关价未查证）


def _norm_backend(p):
    """后端名/别名 → 规范后端名（未识别返回 None）。"""
    p = (p or "").strip().lower()
    if p in ("deepinfra", "glm", "di", "dinfra", "glm-5.3-flash"):
        return "deepinfra"
    if p in ("deepseek", "ds", "deepseek-official", "official", "官网"):
        return "deepseek"
    if p in ("mimo", "xiaomi", "小米"):
        return "mimo"
    if p in ("commandcode", "cc", "muse", "muse-spark"):
        return "commandcode"
    return None


def _parse_refine_entries(raw):
    """链字符串 → `[(后端, 模型覆盖或 None)]`（去重键 = (后端, 模型)）。

    两种写法（2026-10-08 阶段 2 起）：
    * **链条目 id** `backend|model`（界面/设置页按条目勾选）→ 后端 + 该模型
    * **旧式后端名/别名**（deepseek / di / glm / mimo / cc…）→ 模型 None（用链上/后端默认），
      `glm` 仍钉 GLM-5.3-Flash（历史语义，R18 大尺度备选）。
    同后端多条目不再被折叠（deepinfra 的 V4.1-Flash / 0731 可分别出现）。
    """
    out, seen = [], set()
    for part in (raw or "").split(","):
        s = part.strip()
        if not s:
            continue
        if "|" in s:
            b_raw, mdl = s.split("|", 1)
            b, mdl = _norm_backend(b_raw), (mdl.strip() or None)
        else:
            b = _norm_backend(s)
            mdl = _GLM_MODEL if s.strip().lower() in ("glm", "glm-5.3-flash") else None
        if not b or (b, mdl) in seen:
            continue
        seen.add((b, mdl))
        out.append((b, mdl))
    return out or [(b, None) for b in _DEFAULT_CHAIN_LIST]


def _parse_refine_chain(raw):
    """链字符串 → 后端名列表（**旧契约**，仅供打印/兼容旧调用方；新代码走 _parse_refine_entries）。"""
    return [b for b, _m in _parse_refine_entries(raw)]


def _chain_models(raw):
    """链字符串里显式钉模型的项 → {后端: 模型}（旧折叠视图，仅供打印）。"""
    return {b: m for b, m in _parse_refine_entries(raw) if m}


def _follow_paid_chain():
    """未显式指定链时是否跟随付费链（TOML `[refine_aa].follow_default`，默认 true）。"""
    try:
        import aa_config as AC
        v = AC.get("refine_aa", "follow_default", None)
        return True if v is None else bool(v)
    except Exception:
        return True


def _work_r18(work):
    """作品级 R18（`work_meta.r18_level == "high"` → True；与 translate_runner 清理阶段同口径）。"""
    if not work:
        return False
    try:
        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(_root, "aa_web_app", "data", "work_meta.json"), encoding="utf-8") as f:
            return ((json.load(f).get(work) or {}).get("r18_level") == "high")
    except Exception:
        return False


def refine_chain(work=None):
    """本次校对的后端链 `[(backend, model_override)]`（顺序 = 链序；override None = 后端默认）。

    env `AA_REFINE_BACKEND` / TOML `[refine_aa].backends` 既接受**链条目 id**（`backend|model`，
    界面按条目勾选）也接受旧式后端名（2026-10-08 阶段 2）。
    """
    env = os.environ.get("AA_REFINE_BACKEND")
    if env:
        return _parse_refine_entries(env)
    spec = None
    try:
        import aa_config as AC
        spec = AC.get("refine_aa", "backends", None)
    except Exception:
        spec = None
    if spec and not _follow_paid_chain():
        return _parse_refine_entries(spec)
    try:
        import aa_config as AC
        ch = AC.backend_chain(_work_r18(work))
        if ch:
            return [(b, (m or None)) for b, m in ch]
    except Exception:
        pass
    return _parse_refine_entries(DEFAULT_REFINE_CHAIN)


def _refine_chain_spec():
    """链来源: env AA_REFINE_BACKEND > TOML [refine_aa].backends > 代码默认。"""
    v = os.environ.get("AA_REFINE_BACKEND")
    if v:
        return v
    try:
        import aa_config as AC
        v = AC.get("refine_aa", "backends", None)
    except Exception:
        v = None
    return v or DEFAULT_REFINE_CHAIN


def _opt_toml_int(key, default):
    """TOML `[refine_aa].<key>` > default。

    env 一律由调用方**显式字面量**读取（`os.environ.get("AA_...")`），
    以便 tools/gen_interface_docs.py 静态扫出 env 清单。"""
    try:
        import aa_config as AC
        return int(AC.get("refine_aa", key, default))
    except Exception:
        return default


# 单次调用预算(2026-09-15 消长尾): 原 timeout=160s + 3 次重试 → 实测出现 60~189s 单次间隔。
# 惰性读(getter)以便测试/运行中改 env 生效。
def _call_timeout():
    """单次调用上限秒（AA_REFINE_CALL_TIMEOUT > TOML call_timeout > 25）。"""
    v = os.environ.get("AA_REFINE_CALL_TIMEOUT")
    if v:
        try:
            return int(v)
        except ValueError:
            pass
    return _opt_toml_int("call_timeout", 25)


def _call_retry():
    """单后端自身重试次数（AA_REFINE_CALL_RETRY > TOML call_retry > 2）。"""
    v = os.environ.get("AA_REFINE_CALL_RETRY")
    if v:
        try:
            return max(1, int(v))
        except ValueError:
            pass
    return max(1, _opt_toml_int("call_retry", 2))


# 链的真身 = `_REFINE_CHAIN`（后端名序列，旧接口）+ `_REFINE_MDLSEQ`（**按位**模型覆盖，与链等长，
# None = 用 `_REFINE_CHAIN_MODELS` 折叠表/后端默认）。同后端多条目（deepinfra V4.1/0731）靠按位序列共存，
# 而 `_REFINE_CHAIN`/`_REFINE_CHAIN_MODELS` 保持旧语义（测试/工具仍在改它们）。
_REFINE_ENTRIES = _parse_refine_entries(_refine_chain_spec())
_REFINE_CHAIN = [b for b, _m in _REFINE_ENTRIES]
_REFINE_MDLSEQ = [m for _b, m in _REFINE_ENTRIES]
_REFINE_CHAIN_MODELS = {b: m for b, m in _REFINE_ENTRIES if m}


def _set_chain_for_work(work):
    """按作品解析本次校对链（跟随付费链时按 r18 过滤）。`process_work` 开头调用一次。

    模块级链变量沿用旧设计（一个进程一个作品），供 `_call_glm` 与工具打印读取。
    """
    global _REFINE_ENTRIES, _REFINE_CHAIN, _REFINE_CHAIN_MODELS, _REFINE_MDLSEQ
    try:
        ch = refine_chain(work)
    except Exception:
        return _REFINE_CHAIN
    if ch:
        _REFINE_ENTRIES = list(ch)
        _REFINE_CHAIN = [b for b, _m in ch]
        _REFINE_MDLSEQ = [m for _b, m in ch]
        _REFINE_CHAIN_MODELS = {b: m for b, m in ch if m}
    return _REFINE_CHAIN


# 2026-09-22: 旧 `_log_refine_usage`(给自建 GLM 请求补记账)已退休 —— 校对调用一律走
#   aa_backend.call_*（内部 _log_usage 带 tag="refine"），无需自建记账。


def _call_glm(system, body, max_tokens=900, timeout=0, temperature=0.4, gloss_hint=None):
    """校对调用主入口(名沿用 _call_glm)：按 _REFINE_CHAIN 依次尝试，前一个失败降级下一个。

    链（2026-09-22）= 付费链（见 refine_chain）；每个端点走**与主翻译相同的调用函数**
    （aa_backend.call_mimo / call_deepseek / call_deepinfra / call_commandcode），模型取链上覆盖，
    usage 一律记 tag=refine。
    timeout=0(默认) → 用 _call_timeout()（默认 25s，env AA_REFINE_CALL_TIMEOUT / TOML 可调）。

    gloss_hint: 本章术语注入段。段级并发(P1, 2026-09-15)下**显式传参**而非读模块全局
      `_GLOSS_HINT`——多线程同章值相同，但传参杜绝"跨章串味"的隐患。None → 回落全局(兼容旧调用)。
    """
    hint = _GLOSS_HINT if gloss_hint is None else gloss_hint
    if hint and "本作标准译名" not in system:
        system = system + hint
    if not timeout:
        timeout = _call_timeout()
    last_err = None
    for bi, b in enumerate(_REFINE_CHAIN):
        # 模型覆盖：按位序列（_set_chain_for_work 一起写，长度一致时可信）> 旧折叠表
        mdl = None
        if len(_REFINE_MDLSEQ) == len(_REFINE_CHAIN):
            mdl = _REFINE_MDLSEQ[bi]
        if mdl is None:
            mdl = _REFINE_CHAIN_MODELS.get(b)
        try:
            # 2026-09-15 P2-b: 会缓存的后端才拼公共前缀 → MIXED/NORMAL/SWALLOW
            # 三种 system 互相命中（实测交错序列 0% → 55.8% 命中）；网关不拼(白加 token)。
            try:
                import aa_prompt_prefix as _PP
                _sys = _PP.apply(system, b)
            except Exception:
                _sys = system
            import aa_backend as AB
            # 段批可能较长, 给足上限(900 会截断 MIXED 批次); tag 显式传 refine
            # (2026-09-15: 不再改 env AA_USAGE_TAG —— 多线程会互相串)
            _mt = max(max_tokens, 2000)
            if b == "deepseek":
                return AB.call_deepseek(_sys, body, max_tokens=_mt, timeout=timeout,
                                        temperature=temperature,
                                        model=os.environ.get("AA_REFINE_MODEL") or mdl,
                                        retries=_call_retry(), tag="refine")
            if b == "mimo":
                return AB.call_mimo(_sys, body, max_tokens=_mt, timeout=timeout,
                                    temperature=temperature, model=mdl, tag="refine")
            if b == "deepinfra":
                return AB.call_deepinfra(_sys, body, max_tokens=_mt, timeout=timeout,
                                         temperature=temperature, model=mdl,
                                         retries=_call_retry(), tag="refine")
            if b == "commandcode":
                return AB.call_commandcode(_sys, body, max_tokens=_mt, timeout=timeout,
                                           temperature=temperature, model=mdl, tag="refine")
            if b == "opencode_go":
                # 2026-10-10: 付费链首 = opencode Go 门（[backend.opencode_go]）——
                #   必须有这一支，否则落 _call_named 会把校对消耗记成 tag=translate
                return AB.call_opencode_go(_sys, body, max_tokens=_mt, timeout=timeout,
                                           temperature=temperature, model=mdl, tag="refine")
            return AB._call_named(b, _sys, body, model=mdl, max_tokens=_mt, timeout=timeout)
        except Exception as e:
            last_err = e
            nxt = _REFINE_CHAIN[bi + 1] if bi + 1 < len(_REFINE_CHAIN) else None
            if nxt:
                print(f"[refine] {b} 失败({str(e)[:120]}) → 降级 {nxt}", flush=True)
    raise RuntimeError(f"refine 后端链全部失败(链={_REFINE_CHAIN}): {last_err}")


# 2026-09-22「与付费链一致」: 旧 `_call_glm_deepinfra`(deepinfra 路径写死 GLM-5.3-Flash、
#   自建无 reasoning 字段的请求 + 显式 10808 代理)已退休 —— deepinfra 端点改走 aa_backend.call_deepinfra
#   （模型随付费链=DeepSeek-V4.1-Flash；GLM 系自适应 reasoning=low；代理走进程 env HTTPS_PROXY）。
#   需要钉 GLM 时用 `AA_REFINE_BACKEND=glm`（见 _chain_models）。


SYSTEM_MIXED = """你是やる夫AA漫画的资深汉化翻译。用户会发来若干【文本片段】，每行以「行号_段号|」开头。这些片段是 AA 漫画某一行中需要翻译的文字部分（该行其余部分是与画面一体的绘画字符，已由系统处理，不要管）。
要求：
1. 把每个片段翻译成简体中文：自然、贴切、符合漫画语气，不增删内容。
2. 必须完整表达原文语义，不得省略任何实词。
3. 只输出译文片段本身，不要添加任何空格/绘画字符。
4. 角色名、专有名词保留日文原文。
5. 如果片段其实没有可译文字（纯装饰），就原样照抄输出。
6. 必须翻译所有片段，一条都不能漏。
7. 输出格式：每行「行号_段号|翻译后的片段」，一行一条。"""

# 2026-09-06 Q12-A: 纯 text 行(整行台词, 无 AA 图形, 用户内容行为主)用正常翻译提示词
#   ——默认无 AA、正常翻译、强制完整翻(不因"看着像艺术"跳过)、允许译文长于原文(无 art 要保护)。
SYSTEM_NORMAL = """你是日文漫画/网文翻译。用户发来一句日语台词，需要翻译成简体中文。
要求：
1. 自然、贴切、符合口语/漫画语气，完整表达语义，不省略任何实词。
2. 角色名、专有名词保留日文原文（如「プリズナー」→「Prisoner/Prisoner先生」等专名可直接音译或保留）。
3. 这是纯台词（不含 AA 绘画字符），请正常翻译整句，不要担心宽度/长度。
4. 只输出译文本身。"""


SYSTEM_SWALLOW = """你是やる夫AA漫画的日文台词翻译。该台词来自被吞字损坏后需恢复的行。
要求：
1. 翻译成简体中文：自然贴切，完整表达语义，不省略任何实词。
2. 角色名、人名、地名、作品/道具名等专有名词【一律保留日文原文】——
   不译成中文、更不要音译成罗马字/英文/汉语拼音（例：ガッシュ様→ガッシュ様、やる夫→やる夫、ペコリーヌ→ペコリーヌ、できる夫→できる夫）。
3. 只输出译文本身，不要解释、不要加引号或编号。"""


def _art_kana_only(body):
    """段内只有艺术/小写假名、无**决定性**台词证据 → 不该送模型。

    P2(2026-10-03)：本季两起事故（`ミニニニニ`→「迷你尼尼尼」、`ﾆﾆﾆ`→「你你你哈」）都是
    swallow 把纯艺术假名段当台词段送模型。判据复用主链的 `_rule_dialogue_run_ok`
    （艺术假名 ART_HIRA/ART_KATA 与小写假名都不算证据）。
    """
    b = (body or "").strip(" \u3000\u200a\u2002\u2005\u2006\u2009")
    if not b or not re.search(r"[\u3040-\u30ff\uff61-\uff9f]", b):
        return False
    if re.fullmatch(r"([\uff61-\uff9f]{1,2})\1{2,}", b):
        return True        # 半角假名单元重复 ≥3 次（ﾆﾆﾆ / ｶﾞｶﾞｶﾞ）= 项目既有口径的"艺术装饰"
    return not T._rule_dialogue_run_ok(b)


def _restore_art_kana(s, h, segs):
    """TR 里"艺术假名段"被译成中文 → 段级还原为 SRC 原文（零模型）。返回新行或 None。"""
    out, shift, hit = h, 0, False
    for sg in segs:
        if sg.get("kind") != "text":
            continue
        a, b = sg["start"] + shift, sg["end"] + shift
        if a < 0 or b > len(out):
            continue
        raw = s[sg["start"]:sg["end"]]
        body = raw.strip(" \u3000\u200a\u2002\u2005\u2006\u2009")
        if len(body) < 2 or not _art_kana_only(body):
            continue
        span = out[a:b]
        if span == raw or not re.search(r"[\u4e00-\u9fff]", span) \
                or re.search(r"[\u3040-\u30ff\uff61-\uff9f]", span):
            continue                      # 未被改 / 没被译成中文 / 半译留假名 → 不动
        out = out[:a] + body + out[b:]
        shift += len(body) - (b - a)
        hit = True
    return out if hit else None


def _tail_dial_seg(s, segs):
    """行尾完整台词段（text 段，宽>=14，含假名，非图形残片）。

    2026-09-06 放宽: 不要求段在物理行尾——mixed 行台词后常跟短边框 art
    (气泡尾 │/／, L212/262 型)。取"最后一个真台词段":
      - 若 segs[-1] 是 text → 用它
      - 若 segs[-1] 是窄边框 art(宽<=8) → 向左找最后一个 text
    用于 rebuild 提不到译文(TR 被吞)时的模型补译回填。返回 (seg, text) 或 None。"""
    seg = None
    if segs and segs[-1]["kind"] == "text":
        seg = segs[-1]
    elif segs and segs[-1]["kind"] == "art":
        # 2026-09-06: segs[-1] 是 art 时向左找最后一个 text——无论 art 宽窄
        # (L848: 气泡框 '::::|' 包在台词后, art 宽26仍要跨过找台词 '流石私の娘…')。
        for _sg in reversed(segs[:-1]):
            if _sg["kind"] == "text":
                seg = _sg
                break
    if seg is None:
        return None
    t = s[seg["start"]:seg["end"]]
    w = seg.get("src_width") or (seg["end"] - seg["start"])
    if w < 6:
        return None
    body = t.strip(" \u3000\u200a\u2002\u2005\u2006\u2009")
    if len(body) < 4:
        return None
    if FMD._looks_graphic_fragment(t, w):
        return None
    if _art_kana_only(body):
        return None             # P2: 纯艺术/小假名段（ミニニニニ/ﾆﾆﾆ 型）不该当台词送模型
    kana_n = len([ch for ch in body if "\u3040" <= ch <= "\u30ff"])
    cjk_n = len([ch for ch in body if "\u4e00" <= ch <= "\u9fff"])
    if kana_n < 2 and not (kana_n >= 1 and cjk_n >= 2):
        # 需 ≥2 假名, 或 1 假名+≥2 汉字(流石私の娘… 型: 汉字多假名少也是台词)
        return None
    return seg, body


def _rebuild_tail_seg(s, seg, zh, seg_width):
    """把译文回填到台词段位置（SRC 前缀原样 + 译文 pad 到段宽）。

    2026-09-06: text/mixed 行右侧允许少量超宽——译文超段宽时不再硬压(会丢语义),
    直接替换段内容自然扩展(超宽由调用方校验 ≤6 格且不碰后段 art 把关)。"""
    from aa_translate import pad_segment, disp_width
    zh = zh.strip()
    if disp_width(zh) <= seg_width:
        zh = pad_segment(zh, seg_width)
    # 2026-09-06 fix: 保留段后内容(台词段后可能还有气泡边框 art │/／, 丢它会裁掉边框且宽度失配)
    return s[:seg["start"]] + zh + s[seg["end"]:]


def _ok_width(nl_w, ref_w):
    """text/mixed 行补译宽度校验: 允许少量超宽(≤6格, 用户规则: 右侧允许超)。
    art 严格等宽由 _verify_art_alignment 另把关。"""
    return nl_w >= ref_w - 2 and nl_w <= ref_w + 6


def _pad_line_to_src(nl, s):
    """写回前定型: 重建/补译结果短于 SRC 时补 U+3000 到 SRC 显示宽(只补不裁, 超宽不动)。

    2026-09-13: poly(text_normal_ok) 原样拼 `lead + 译文` 从不补宽 → 产 SHORT 行
    (akari_haisin 23908 实测 10 行, 如 85→79、90→67), 而清理链 fix_width 跑在 refine
    之前, 无人兜底。此处集中定型, 后续 fix_width 复验应恒为 0。"""
    if not nl:
        return nl
    sw, nw = C.disp_width(s), C.disp_width(nl)
    if nw >= sw:
        return nl
    try:
        from aa_translate import pad_to_width
        return pad_to_width(nl, sw)
    except Exception:
        return nl


def _is_real_missed(src_row, tr_row, segs=None, high=None):
    """真漏译判定：SRC 台词段(high-conf text)含日文，且新行(TR)**整段留日文未翻**。
    铁律（Q3/方案§7 + D 决策 2026-09-06）：
      - 不能裸 TR 含假名扫（误报标题/帖头/纯图形行）
      - 不能只查"TR 台词段含连续假名/假名占比"——角色名假名(シオニーちゃん)保留正确,
        被 segment_line 切碎会误判漏译(21099 L1030/1032 实测误报)。
      - **判据(简洁可靠)**：
        ① `TR==SRC`(整行一字未改) → 漏译强信号。21099 实测真漏译行全 TR==SRC。
        ② TR 台词段(high text)若不含中文汉字(CJK) → 该台词段仍整段日文 → 漏译。
           (已翻行台词段必含中文汉字; 角色名假名+中文混排也含中文汉字, 不误判; 纯 art 行已排除)
      → 不依赖角色名白名单/假名占比。"""
    if src_row.startswith("#") or HEADER_RE.match(src_row):
        return False
    kind = T.line_kind(src_row)
    if kind not in ('text', 'mixed'):
        return False    # art 行不是漏译（该还原不是补译）
    if segs is None:
        from line_classify_v2 import segment_line
        segs = segment_line(src_row, treat_hankaku_as_text=True)
    if high is None:
        high = T._confident_text_segs(segs)
    if not high:
        return False    # SRC 无台词段 → 不是漏译（该还原不是补译）

    # ① 整行未改(留日文) → 漏译
    if tr_row == src_row:
        return True

    # ② TR 台词区含中文汉字吗? 若任一台词段(high text)不含中文汉字 → 仍整段日文 → 漏译
    from line_classify_v2 import segment_line as _sl
    tr_segs = _sl(tr_row, treat_hankaku_as_text=True)
    tr_high = T._confident_text_segs(tr_segs)
    if not tr_high:
        # TR 无高置信台词段：全译成中文(已翻) 或 整行仍日文(漏译)
        body_nz = [c for c in tr_row if not c.isspace() and c != '\u3000']
        return bool(body_nz) and not re.search(r'[\u4e00-\u9fff]', ''.join(body_nz))
    # TR 有台词段 → 看该行台词区**整体**是否已翻译
    #   任一台词段含中文汉字 = 翻译已发生(角色名假名+中文混排也含中文) → 已翻
    #   全部台词段无中文汉字 = 整段日文未翻(含整行拆多段的真漏译) → 漏译
    #   (L262 '和やかな雰囲気です！素晴らしい！' 两段都无中文 → 漏译;
    #    L1030 'シオニーちゃん突然杀出来了' 的'シオ'碎段无中文但'突然杀出来了'含中文 → 已翻)
    any_zh = False
    for si in tr_high:
        sg = tr_segs[si]
        seg_tr = tr_row[sg['start']:sg['end']]
        if re.search(r'[\u4e00-\u9fff]', ''.join(c for c in seg_tr if c != '\u3000')):
            any_zh = True
            break
    return not any_zh


def _judge_review_line(src_row, tr_row):
    """review_inline 行三态裁决（Q5）：补译 / 还原 / 跳过。
    纯模型裁决，返回 one of '补译'|'还原'|'跳过'。空译文→补译；无台词→还原；已对→跳过。"""
    from line_classify_v2 import segment_line
    segs = segment_line(src_row, treat_hankaku_as_text=True)
    high = T._confident_text_segs(segs)
    if not high:
        return '还原'   # 全低置信=无台词（纯图形）→ 还原
    # 有台词：TR 留日文/空 → 补译；已中文 → 跳过
    body2 = tr_row.replace('\u3000', '').strip()
    if not body2 or KANA_RESIDUAL.search(body2):
        return '补译'
    return '跳过'


# ---------------- P1 段级并发：job 单元（2026-09-15）----------------
# 重构原则：只把「发模型调用 → 校验 → 出 edit」搬出内层循环并发跑；行内的本地判定
# (segment_line / dial_segs / _is_real_missed / art 对齐 / rebuild_row) 仍同步执行。
# 分支里的比较对象、原因串逐字保留（差异用参数表达），保证与串行版等价。
# 每个 job 恰好产出一条 edit；返回 None = 未产出，由调用方决定回退 edit。

def _pre_terms(text, base, work):
    """宝可梦链：校对里"重译原文"的路径同样走翻译前术语强制替换（2026-09-22）。

    swallow/poly 会把**日文原文**送模型 → 不替换就会出现"替换过又被改回去"的漂移
    （实测 entry_16582 L4072 把 双看板 改写成 双招牌）。非宝可梦作品/关掉开关时
    `pre_terms.apply` 内部直接返回原文（零影响、零宽度风险——宽度校验仍用原行）。
    """
    if not text or not base or not work:
        return text
    try:
        import pre_terms as PT
        return PT.apply(text, base, work=work)
    except Exception:
        return text


# ---------------------------------------------------------------------------
# 说话人标记【…】结构护栏（2026-10-02）
# ---------------------------------------------------------------------------
# 背景（entry_22208 实测）：主翻译对「名字不在术语表」的【名前】行会失败（模型保留
# 日文名 → 残留假名校验拒收）→ 整行留日文落 review_inline；refine 的漏译补译链
# （poly 整行 / swallow 段级）本来只校验「无残留假名 + 宽度」，对行首【…】标记没有
# 任何约束 → 模型丢括号（裸句）或改写成「名字：」都照样落地。本作实测 635 行
# （1.11%）丢括号，其中 111 行是「名：」式。
# 铁律：**源行首有【…】标记时，译文必须同形开头**；名字翻译交给术语表
# （pre_terms，卡ズミラクル→和真奇迹），表里没有则原样保留标记（未确认译名不猜）。
# 2026-10-03 收紧（用户指定，方案 doc/方案归档/说话人标记护栏收紧方案_2026-10-03.md）：
#   ① 仅文本行：护栏只在 _job_poly（纯 text 行）生效；_job_swallow（mixed/art 段级）
#      一律不做标记约束——段级强改 art/mixed 行曾把艺术假名译坏（已回退）。
#   ② 骰子安全：标记分五类（tag_class），dice/anka/latin 类**不打重试**，只做
#      「数字序列 + 括号冒号骨架」校验，不符就用源标记逐字回填；art 碎片完全不进护栏。
#   ③ 行内骰子：译文必须含源行里的每个 【NdM:T(…)】 片段，否则放弃该次改写（_job_poly）。
_TAG_RE = re.compile(r"^([ \u3000]*)【((?:[^【】\n]|【[^】\n]{0,40}】){1,40})】")
# 模型被要求把【名前】改写成「名前：」时的残留前缀（只在源行有标记时才剥）
_LEAD_COLON_RE = re.compile(r"^([ \u3000]*)([^：:\n，。！？…「」『』]{1,20})[：:]")
TAG_RULE = ("【格式硬要求】原文以【…】开头（说话人标记）。译文必须同样以【…】开头，"
            "只把括号内的名字翻译成中文；严禁丢弃【】、严禁改写成「名字：」或用其他标点替代。"
            "括号内若含数字、骰子记法（如 1D100:42 / 3D15:22(9+5+8)）或安価记法"
            "（下5 or このレスから5分 之类），这些部分必须原样保留：禁止改动数字、冒号、"
            "括号数量与顺序，禁止翻译、增删或改写成中文标点。")

# 骰子/安価/系统/拉丁 标记识别（决定「只做结构校验、不打重试」）
DICE_INNER_RE = re.compile(r"\d+\s*[dD]\s*\d+")
DICE_FRAG_RE = re.compile(r"【\s*\d+\s*[dD]\s*\d+[^】]*】")
ANKA_RE = re.compile(r"安価|下\s*\d|このレス|レスから|判定|先制|命中|使用有無|交代先|変化技|ダイス")
LATIN_TAG_RE = re.compile(r"^[A-Za-z0-9_\-.・ \u3000]{1,12}$")
_ART_INNER_RE = re.compile(r"[\uff61-\uff9f]|[|｜/\\＿=＝+＋*＊<>＜＞≦≧￣＼／^＾~～'\"｀]|ヽ|ヾ|\u3000{2,}")
_KANA_IN = re.compile(r"[\u3040-\u30ff]")
_KANJI_IN = re.compile(r"[\u4e00-\u9fff]")


def _skeleton(s):
    """数字序列 + 括号/冒号骨架：骰子/安価标记「词可译、结构不许动」的校验基线。"""
    return re.sub(r"[^\d【】（）〔〕『』=:：+]", "", s or "")


def tag_inner(tag):
    """'【Part（【1D912:674】）スレ主】' → 'Part（【1D912:674】）スレ主'（允许一层嵌套）。"""
    m = _TAG_RE.match(tag or "")
    return m.group(2) if m else (tag or "").strip("【】")


def tag_class(inner):
    """标记类型：speaker | dice | anka | latin | art（art = 完全不进护栏）。"""
    inner = inner or ""
    if DICE_INNER_RE.search(inner) or "【" in inner:
        return "dice"
    if ANKA_RE.search(inner):
        return "anka"
    if LATIN_TAG_RE.match(inner):
        return "latin"
    if _ART_INNER_RE.search(inner) or len(inner) > 20:
        return "art"
    if _KANA_IN.search(inner) or _KANJI_IN.search(inner):
        return "speaker"
    return "latin"


def speaker_tag(s):
    """行首说话人标记 → (前导空白, 标记全文，如 '【和真】') 或 None。"""
    m = _TAG_RE.match(s or "")
    if not m:
        return None
    return m.group(1), m.group(0)[len(m.group(1)):]


def _strip_lead_colon(zh):
    """剥掉模型加在行首的「名字：」（源行本来就有【…】时才调用）。"""
    m = _LEAD_COLON_RE.match(zh or "")
    if not m:
        return zh
    body = zh[m.end():]
    return (m.group(1) + body) if body.strip() else zh


def _tag_zh(tag_src, base, work, cls="speaker"):
    """回填用的标记。

    speaker → 括号内名字过一遍术语替换（pre_terms），仍含假名则原样保留；
    dice    → 骰子片段逐字保留，其余（如 Part/スレ主）允许术语替换，骨架不符即整枚原文；
    anka    → 允许术语替换，但数字/括号/冒号骨架必须一致，不符即整枚原文；
    latin/art → 一律逐字用源标记。
    """
    raw = (tag_src or "").strip()
    inner = tag_inner(raw)
    if not inner:
        return raw
    if cls in ("latin", "art"):
        return raw
    new = inner
    try:
        new = _pre_terms(inner, base, work) or inner
    except Exception:
        pass
    new = (new or "").strip()
    if not new or _KANA_IN.search(new):
        new = inner.strip()          # 表里没有 / 替换失败 → 保留原文（补表清单另报）
    if cls == "dice":
        frags = DICE_FRAG_RE.findall(inner)
        if any(f not in new for f in frags) or _skeleton(new) != _skeleton(inner):
            return raw
    elif cls == "anka":
        if _skeleton(new) != _skeleton(inner):
            return raw
    return "【" + new + "】"


def guard_speaker_tag(zh, tag, body, sys_prompt, gloss_hint=None, base=None, work=None):
    """护栏（仅文本行）：译文丢失/改坏行首【…】→ 按标记类型处置。

    返回 (译文, 状态后缀)。状态 ""=合规或未介入，"+tag_retry"=重试后合规，
    "+tag_reattach"=源标记回填，"+tag_fix"=结构骨架不符、改回源标记。
    """
    if not tag or not zh:
        return zh, ""
    _lead, tag_src = tag
    cls = tag_class(tag_inner(tag_src))
    if cls == "art":
        return zh, ""                       # 艺术碎片：不约束、不重试、不回填
    m = _TAG_RE.match(zh)
    if m:
        if cls == "speaker":
            return zh, ""                   # 说话人名形状已对 → 不介入
        src_inner, dst_inner = tag_inner(tag_src), m.group(2)
        if cls == "latin":
            ok = (dst_inner == src_inner)
        else:
            ok = (_skeleton(dst_inner) == _skeleton(src_inner)
                  and all(f in dst_inner for f in DICE_FRAG_RE.findall(src_inner)))
        if ok:
            return zh, ""
        body_zh = zh[m.end():].lstrip(" \u3000")
        if not body_zh:
            # 整行就是一枚标记（安価/系统行，本作 2364 行）→ 直接用源标记，别无正文可保
            return _tag_zh(tag_src, base, work, cls), "+tag_fix"
        return _tag_zh(tag_src, base, work, cls) + body_zh, "+tag_fix"
    zh_clean = _strip_lead_colon(zh).lstrip(" \u3000")
    if not zh_clean:
        return zh, ""
    if cls == "speaker" and sys_prompt and body:
        try:
            zh2 = _call_glm(sys_prompt + "\n\n" + TAG_RULE, body, gloss_hint=gloss_hint)
            zh2 = re.sub(r"^\d+\|", "", (zh2 or "").strip()).strip().strip('"').strip("「」")
            zh2 = T.one_line(zh2)
            if zh2 and _TAG_RE.match(zh2):
                return zh2, "+tag_retry"
        except Exception:
            pass
    return _tag_zh(tag_src, base, work, cls) + zh_clean, "+tag_reattach"


def _swallow_pre(i, s, h, seg, body, ref_proper,
                 proper_reason="swallow_proper_noun", honorific=True):
    """swallow 送模型前的本地判定：专名敬称整段 → 用 SRC 原文回填（零调用）。返回 edit|None。"""
    if not honorific:
        return None
    try:
        seg_w = seg.get("src_width") or (seg["end"] - seg["start"])
        if re.fullmatch(
                r"[\u3040-\u30ff\u4e00-\u9fff・ー々\s、。]+様"
                r"(?:、[\u3040-\u30ff\u4e00-\u9fff・ー々\s、。]+様)*。?", body.strip()):
            nl = _rebuild_tail_seg(s, seg, body, seg_w)
            if nl is not None and nl != ref_proper and _ok_width(C.disp_width(nl), C.disp_width(ref_proper)):
                return (i, s, h, nl, "rebuild", True, proper_reason)
    except Exception:
        return None
    return None


def _swallow_apply(i, s, h, seg, body, zh, ref_proper, ref_model, ok_reason,
                   err_reason="swallow_translate_err", strip_idx=True,
                   squeeze_retry=False, gloss_hint=None):
    """swallow 的后半段：给定译文 zh → 清洗/回填段位/宽度校验 → edit|None。

    单行路径 = `_swallow_pre` → 调模型 → 本函数；**批量路径** = 块回包里的译文 → 本函数。
    两条路走同一段回填与校验代码，语义一致（zh 的 strip_idx 清洗在这里做，故批量路径同样生效）。
    """
    try:
        seg_w = seg.get("src_width") or (seg["end"] - seg["start"])
        zh = re.sub(r'^\d+\|', '', zh or '') if strip_idx else (zh or '')
        zh = (zh or '').strip().strip('"').strip('「」')
        zh = T.one_line(zh)     # 护栏：译文折行会把 1 行写成 2 行(行号错位, 2026-09-22)
        # 2026-10-03: 段级路径不再做说话人标记约束（护栏仅文本行；
        # 段级强改 mixed/art 行曾把艺术假名译坏 → 见 doc/方案归档/说话人标记护栏收紧方案_2026-10-03.md）
        nl = _rebuild_tail_seg(s, seg, zh, seg_w)
        if nl is None and zh and squeeze_retry:
            # 译文超段宽压不下 → 提示模型精简重试一次(L764 18格: '这年纪就当提督了哦？'22格超)
            try:
                zh2 = _call_glm(SYSTEM_SWALLOW, f"译文须≤{seg_w//2}个全角字,请精简: {body}",
                                gloss_hint=gloss_hint)
                zh2 = (zh2 or '').strip().strip('"').strip('「」')
                zh2 = T.one_line(zh2)
                nl = _rebuild_tail_seg(s, seg, zh2, seg_w)
            except Exception:
                pass
        if nl is not None and nl != ref_model and _ok_width(C.disp_width(nl), C.disp_width(ref_model)):
            return (i, s, h, nl, "rebuild", True, ok_reason)
    except Exception as e:
        return (i, s, h, h, "rebuild", False, f"{err_reason}:{e}")
    return None


def _job_swallow(i, s, h, seg, body, ref_proper, ref_model, ok_reason,
                 gloss_hint, proper_reason="swallow_proper_noun",
                 err_reason="swallow_translate_err",
                 honorific=True, strip_idx=True, squeeze_retry=False,
                 base=None, work=None):
    """台词段残留 → 专名保留(可选) 或 模型补译并回填段位（单行路径）。

    对应原 swallow 三处分支（参数保持逐字语义）：
      · 漏译 mixed 补译: honorific=True, ref_proper=h, ref_model=s, strip_idx=True,  squeeze_retry=True
      · art 对齐台词被吞: honorific=True, ref_proper=h, ref_model=h, strip_idx=False, squeeze_retry=False
      · rebuild 失败补译: honorific=False, ref_model=s, strip_idx=True,  squeeze_retry=False
    """
    ed = _swallow_pre(i, s, h, seg, body, ref_proper, proper_reason, honorific)
    if ed is not None:
        return ed
    try:
        zh = _call_glm(SYSTEM_SWALLOW, body, gloss_hint=gloss_hint)
    except Exception as e:
        return (i, s, h, h, "rebuild", False, f"{err_reason}:{e}")
    return _swallow_apply(i, s, h, seg, body, zh, ref_proper, ref_model, ok_reason,
                          err_reason=err_reason, strip_idx=strip_idx,
                          squeeze_retry=squeeze_retry, gloss_hint=gloss_hint)


def _poly_apply(i, s, h, lead, body, resp, gloss_hint=None, base=None, work=None):
    """poly 的后半段：给定回包 → 清洗/说话人标记与骰子护栏/补宽 → edit。"""
    try:
        zh = (resp or '').strip()
        zh = re.sub(r'^\d+\|', '', zh)           # 防模型回带序号/引号
        zh = zh.strip().strip('"').strip('「」')
        zh = T.one_line(zh)                       # 护栏：同上(行号错位, 2026-09-22)
        if not zh or zh == body or not re.search(r'[\u4e00-\u9fff]', zh):
            return (i, s, h, h, "poly", False, "zh_empty_or_copy")
        # 说话人标记护栏：源行首【…】必须保留（模型丢括号/改「名：」都不放过）
        _tsfx = ""
        tag = speaker_tag(s)
        if tag:
            _gh = _GLOSS_HINT if gloss_hint is None else gloss_hint
            zh, _tsfx = guard_speaker_tag(zh, tag, body, SYSTEM_NORMAL,
                                          _gh, base, work)
        # 行内骰子护栏（2026-10-03）：源行里每个 【NdM:T(…)】 必须逐字出现，
        # 否则放弃这次改写（保留原行）→ 落 review_inline 供人工
        _dfrags = DICE_FRAG_RE.findall(s)
        if _dfrags and any(f not in zh for f in _dfrags):
            return (i, s, h, h, "poly", False, "dice_err:骰子片段丢失或被改")
        return (i, s, h, _pad_line_to_src(lead + zh, s), "poly", True,
                "text_normal_ok" + _tsfx)
    except Exception as e:
        return (i, s, h, h, "poly", False, f"err:{e}")


def _job_poly(i, s, h, lead, body, gloss_hint, base=None, work=None):
    """纯 text 行整行翻译（正常提示词，允许超宽）。"""
    try:
        resp = _call_glm(SYSTEM_NORMAL, body, gloss_hint=gloss_hint)
    except Exception as e:
        return (i, s, h, h, "poly", False, f"err:{e}")
    return _poly_apply(i, s, h, lead, body, resp, gloss_hint=gloss_hint,
                       base=base, work=work)


# ---------------------------------------------------------------------------
# P0(2026-10-03) 批量请求：同章同类 job 合并成块（复用主翻译「NNN|文本」协议）
# ---------------------------------------------------------------------------
# 动机（实测）：refine 每次调用 prompt 中位 503 tok，其中固定头部(SYSTEM+术语 hint+公共前缀)
#   436~545 tok、正文只有 ~10 tok → 单章 2,685 次调用里 99% 的 token 与往返在重发同一段头部。
#   合并成 ≤N 条/块后调用数 ↓ ~10×、prompt ↓ ~20×（见 doc/方案归档/refine_aa优化方案_2026-10-03.md）。
_BATCH_HINT = ("\n\n【批量模式】用户发来的内容每行以「5位行号|」开头，每行是一条**独立**的待译文本"
               "（行与行之间无上下文关系，不要互相参照）。逐行翻译，输出格式与输入完全一致："
               "「5位行号|译文」，行号原样照抄（保持 5 位、不得改动），一行一条，"
               "不得漏行、不得合并、不得添加解释或空行。")


def _batch_size():
    """每块条数：env AA_REFINE_BATCH（0=关闭批量，回单行路径）> TOML [refine_aa].batch > 50。"""
    v = os.environ.get("AA_REFINE_BATCH")
    if v is not None and str(v).strip() != "":
        try:
            return max(0, int(v))
        except ValueError:
            pass
    return max(0, _opt_toml_int("batch", 50))


def _parse_block(resp, want):
    """块回包 → {行号: 译文}；只收 want 里的行号（漏行由调用方回退单行）。"""
    got = {}
    for ln in (resp or "").split("\n"):
        m = re.match(r"^\s*(\d{1,5})\s*\|\s?(.*)$", ln)
        if not m:
            continue
        idx = int(m.group(1))
        zh = m.group(2).strip()
        if idx in want and zh:
            got.setdefault(idx, zh)
    return got


def _run_one(fn):
    """跑一个 job：job 内部已 catch，这里只防意外异常（None = 未产出，不丢行）。"""
    try:
        return fn()
    except Exception:
        return None


def _run_jobs(jobs, workers):
    """并发执行 jobs=[(行号, callable)] → {行号: edit|None}。"""
    out = {}
    if not jobs:
        return out
    if workers <= 1 or len(jobs) == 1:
        for i, fn in jobs:
            out[i] = _run_one(fn)
        return out
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_run_one, fn): i for i, fn in jobs}
        for fut in concurrent.futures.as_completed(futs):
            i = futs[fut]
            try:
                out[i] = fut.result()
            except Exception:       # 兜底：不因单行异常丢整章
                out[i] = None
    return out


_Job = collections.namedtuple("_Job", "i kind body apply single fallback")
# i      行号(0-based)
# kind   "swallow" | "poly"（决定用 SYSTEM_SWALLOW / SYSTEM_NORMAL）
# body   送模型的文本（swallow=段落原文；poly=整行去空白）
# apply  callable(zh) -> edit|None  —— 用给定译文走回填/校验
# single callable()   -> edit|None  —— 单行路径（= 原 job 闭包；批量回退时用）
# fallback 调用方回退 edit（job 未产出且单行也失败时用；None = 不动）


def _run_jobs_batched(jobs, workers, gloss_hint=None, chunk=0, base=None):
    """批量跑 jobs（同 kind 合并成块）→ {行号: edit}。

    jobs = [_Job]。每块 = 一次调用：正文 `%05d|文本` 若干行，回包按行号拆回，逐行走 `apply`
    （回填/宽度/art 校验与单行路径同一段代码）。**回包漏行 / apply 未产出 / 整块失败的行
    一律回退单行路径**（`single`），因此质量与单行等价，最坏情况退化为原调用次数。
    """
    out = {}
    if not jobs:
        return out
    chunk = chunk or _batch_size()
    groups = collections.OrderedDict()
    for j in jobs:
        groups.setdefault(j.kind, []).append(j)

    def _one_block(blk):
        system = SYSTEM_SWALLOW if blk[0].kind == "swallow" else SYSTEM_NORMAL
        body = "\n".join("%05d|%s" % (j.i, j.body) for j in blk)
        want = set(j.i for j in blk)
        try:
            resp = _call_glm(system + _BATCH_HINT, body, max_tokens=2000,
                             gloss_hint=gloss_hint)
        except Exception as e:
            print("[refine] 批量块失败(%s) → %d 行回退单行: %s"
                  % (blk[0].kind, len(blk), str(e)[:100]), flush=True)
            return {}
        return _parse_block(resp, want)

    blocks = []
    for _kind, items in groups.items():
        for k in range(0, len(items), chunk):
            blocks.append(items[k:k + chunk])
    got_by_block = {}
    if workers <= 1 or len(blocks) == 1:
        for bi, blk in enumerate(blocks):
            got_by_block[bi] = _one_block(blk)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_one_block, blk): bi for bi, blk in enumerate(blocks)}
            for fut in concurrent.futures.as_completed(futs):
                bi = futs[fut]
                try:
                    got_by_block[bi] = fut.result()
                except Exception:
                    got_by_block[bi] = {}

    n_single = 0
    for bi, blk in enumerate(blocks):
        got = got_by_block.get(bi) or {}
        for j in blk:
            zh = got.get(j.i)
            ed = None
            if zh is not None:
                ed = _run_one(functools.partial(j.apply, zh))
            if ed is None:                       # 漏行 / 回填失败 → 单行路径
                n_single += 1
                ed = _run_one(j.single)
            out[j.i] = ed
    if n_single:
        print("[refine] 批量块回退单行 %d 行（漏行/超宽/校验未过）" % n_single, flush=True)
    return out


def _refine_workers():
    """段级并发度(P1): env AA_REFINE_WORKERS > TOML [refine_aa].workers > 8。"""
    v = os.environ.get("AA_REFINE_WORKERS")
    if v:
        try:
            return max(1, int(v))
        except ValueError:
            pass
    return max(1, _opt_toml_int("workers", 8))


def _fixed_whitelist(work, work_dir, src, tr):
    """固定表预填行（当前译文与表口径一致）→ 索引集合：内容表产出，校对不该动。

    P1(2026-10-03)：`common/pokemon_fixed.py` 预填的状态行/『名称』说明由表生成，
    校对若去"改好"它们只会引入漂移（方案 doc/方案归档/宝可梦固定内容表方案_2026-09-22.md §8 待办）。
    copy/whole 轨比 zh 逐字；struct 轨比 `zh_term`+`zh_desc` 是否都还在行里（人工改过的行不会被跳过）。
    """
    try:
        import pokemon_fixed as PF
        if not PF.enabled():
            return set()
        tbl = PF.load(work, work_dir)
        if not tbl:
            return set()
        idx = set()
        for i, s in enumerate(src):
            if not s or s.startswith("#"):
                continue
            h = tr[i] if i < len(tr) else ""
            hit = PF.match_copy(s, tbl) or PF.match_whole(s, tbl)
            if hit and (hit.get("zh") or "") == h:
                idx.add(i)
                continue
            st = PF.match_struct(s, tbl)
            if st and st.get("zh_term") and st["zh_term"] in h \
                    and (st.get("zh_desc") or "") in h:
                idx.add(i)
        return idx
    except Exception:
        return set()


def _tm_whitelist(work, work_dir, src, tr):
    """TM 整行复用行（表里 zh 与当前汉化逐字相同）→ 索引集合：来自已定稿章，校对不该动。"""
    try:
        import aa_tm
        if not aa_tm.enabled_default_on():
            return set()
        tm = aa_tm.load(work, work_dir) or {}
        if not tm:
            return set()
        idx = set()
        for i, s in enumerate(src):
            if not s or s.startswith("#"):
                continue
            rec = tm.get(aa_tm.key(s))
            if isinstance(rec, dict) and rec.get("zh") and i < len(tr) and rec["zh"] == tr[i]:
                idx.add(i)
        return idx
    except Exception:
        return set()


def _term_guard_reason(old, new, pairs):
    """P1 词表护栏：old 里已落地的标准中文名，编辑后不见了 → 拒绝该编辑。返回原因或 ""。

    "已落地"= 标准译名的中文写法在 old 里出现（说明这项已按词表替换/已正确）；
    编辑把它改掉（或改回日文）都会漂移译名口径 → 宁可不改（该行保持原样）。
    """
    for p in (pairs or ()):
        zh = p.get("zh") or ""
        jp = p.get("jp") or ""
        if not zh or zh not in old or zh in new:
            continue
        if (not jp.startswith("re:")) and jp in new:
            return "%s 被改回日文" % jp
        return "标准译名 %s(%s) 被改掉" % (zh, jp)
    return ""


def art_gate_enabled():
    """行级艺术闸门是否启用（默认开，显式 `AA_REFINE_ART_GATE=0` 关闭/回滚）。"""
    v = os.environ.get("AA_REFINE_ART_GATE")
    if v is None:
        return True
    return v not in ("0", "false", "False", "")


def _row_model_gate(idx, src_lines, meta0):
    """该行是否「模型不该碰」= 主链判据 `T.is_translatable` 为假。meta 缺失/异常 → 放行。

    2026-10-10 行级艺术闸门（`refine_chapter` 派发 jobs 前过滤）：
    refine 现场重判 segment_line（Q3 不信磁盘 meta.kind），于是会从「整行就是画」的行里切出
    艺术碎片段当台词段送模型（实测 `xぅ笏外`→「抱歉」、`芹仍てﾊ`→「芹仍て嗯」）。本书 1104 条
    人工修正归因：185 处「艺术假名被译成中文」里 64 处由本环节引入，其中 50 处所在行的
    meta.kind=art / `art_line_veto` 命中 —— 主链压根不会送这些行。
    过滤只作用于**模型调用**；本地路径（`_restore_art_kana`/`no_text_restore`/`rebuild_row`
    对齐修复）都发生在过滤之前，行为不变。
    """
    if not art_gate_enabled() or not meta0:
        return False
    if idx < 0 or idx >= len(src_lines):
        return False
    try:
        return not T.is_translatable(idx, src_lines[idx], meta0)
    except Exception:
        return False


def refine_chapter(cfg, base, do_write=False, review_inline=False):
    """单章 refine。返回 (base, 统计dict, 改行list)。"""
    aa_dir, out_dir, work_dir = cfg["aa_dir"], cfg["out_dir"], cfg["work_dir"]
    src_p = os.path.join(aa_dir, base + ".txt")
    tr_p = os.path.join(out_dir, base + "_汉化.txt")
    meta_p = os.path.join(work_dir, "line_meta_v2", base + ".json")
    if not all(os.path.exists(p) for p in (src_p, tr_p, meta_p)):
        return base, {"skipped": 1}, []
    src = open(src_p, encoding="utf-8").read().split("\n")
    tr = open(tr_p, encoding="utf-8").read().split("\n")
    meta = json.load(open(meta_p, encoding="utf-8"))
    # 2026-10-10: 行级艺术闸门用 0-based meta（kind/reason），与主链同一份判据
    _meta0 = T.load_meta_0based(os.path.join(work_dir, "line_meta_v2"), base)
    if len(src) != len(tr):
        return base, {"skip_len": f"{len(src)}!={len(tr)}"}, []

    # 2026-09-07: 作品术语表注入(逐章按语料过滤, 补译/精译锁标准译名)
    # 2026-09-15(P1): 取回本章 hint 显式传给并发 job —— 不再让线程去读模块全局 _GLOSS_HINT
    gloss_hint = _load_gloss_hint(cfg, base)
    workers = _refine_workers()
    _work = cfg.get("work") or ""     # 术语替换（宝可梦链）用

    # P0(2026-10-03): 批量块大小（0=关闭 → 走单行路径，行为与 2026-09-15 版一致）
    _bsz = _batch_size()
    # P1(2026-10-03): 词表一致性护栏 —— 本章标准译名（pre_terms，非宝可梦作品为空 → 护栏不生效）
    _term_pairs = []
    if os.environ.get("AA_REFINE_TERM_GUARD", "1").strip() != "0" and _work:
        try:
            import pre_terms as PT
            _term_pairs = PT.pairs_for(base, work=_work)
        except Exception:
            _term_pairs = []
    # P1(2026-10-03): 白名单 —— 固定表预填行 / TM 复用行（都已是定稿口径）不校对
    _skip_idx = set()
    if _work:
        if os.environ.get("AA_REFINE_SKIP_FIXED", "1").strip() != "0":
            _skip_idx |= _fixed_whitelist(_work, work_dir, src, tr)
        if os.environ.get("AA_REFINE_SKIP_TM", "1").strip() != "0":
            _skip_idx |= _tm_whitelist(_work, work_dir, src, tr)

    from line_classify_v2 import segment_line
    from fix_mixed_drift import rebuild_row
    # _verify_art_alignment 在 aa_translate(T) 命名空间, 不在 fix_mixed_drift

    edits = []       # 记录 (i, src_row, old_row, new_row, action, ok, reason)
    jobs = []        # P1: [(行号, callable)] —— 需要模型的段落行，循环结束后并发跑
    n_skip_white = 0
    n_art_gate = 0   # 2026-10-10: 行级艺术闸门拦下的行数（主链不送模型 → 不切段补译）
    for i, (s, h) in enumerate(zip(src, tr)):
        if s.startswith("#"):
            continue
        if i in _skip_idx:          # P1: 固定表预填行 / TM 复用行 → 不动
            n_skip_white += 1
            continue
        # Q3: 现场重判 segs（不信磁盘 meta.kind）
        segs = segment_line(s, treat_hankaku_as_text=True)
        # P2(2026-10-03): 艺术假名段被译成中文 → 先段级还原成原文（零模型，防"画面被改"）
        _h2 = _restore_art_kana(s, h, segs)
        if _h2 is not None:
            edits.append((i, s, h, _h2, "restore", True, "art_kana_restore"))
            h = _h2                 # 后续判定基于还原后的行（同索引的后续 edit 会串起来）
        high = T._confident_text_segs(segs)
        kind = T.line_kind(s)

        # 2026-09-06: "真台词区"判定——宽>=6 的 text 段(不限 conf) 且非图形残片 且含假名>=2
        # (v3 meta 里整句台词 conf=None、图形反被切成 conf=0.4 text 碎段 → 不能只看 conf)
        from line_classify_v2 import segment_line as _sl
        from fix_mixed_drift import _looks_graphic_fragment
        dial_segs = []
        for _si, _sg in enumerate(segs):
            if _sg["kind"] != "text":
                continue
            _t = s[_sg["start"]:_sg["end"]]
            _w = _sg.get("src_width") or (_sg["end"] - _sg["start"])
            _body = _t.strip(" \u3000\u200a\u2002\u2005\u2006\u2009")
            if _w < 6 or len(_body) < 2:
                continue
            if _looks_graphic_fragment(_t, _w):
                continue
            if _art_kana_only(_body):
                continue        # P2: 纯艺术/小假名段不是台词（送模型必被译坏）
            if len([c for c in _body if "\u3040" <= c <= "\u30ff"]) >= 2 or \
               (re.search(r"[\u4e00-\u9fff]", _body) and re.search(r"[\u3040-\u30ff]", _body)):
                dial_segs.append(_si)

        if not dial_segs:
            # 动作3 错译还原/跳过：无真台词(纯 art + 图形残片)。
            # 2026-09-06 修复: 还原只对"TR 被乱改"的行——TR 含中文(CJK)说明已正确翻译
            # (如 L1601 SRC='乙' TR='辛苦了'、L1089 '多分…？'→'大概…？'), 还原会覆写掉
            # 好译文 → 必须保留。只有 TR 不含有效中文(纯日文残/乱码/图形被改)才还原 SRC。
            _tr_body = h.replace("\u3000", "").strip(" \u200a\u2002\u2005\u2006\u2009")
            _tr_has_cjk = bool(re.search(r"[\u4e00-\u9fff]", _tr_body))
            if h != s and not _tr_has_cjk:
                edits.append((i, s, h, s, "restore", True, "no_text_restore"))
            continue

        # --- 漏译判定：先过滤（text/mixed 且非帖头）再查 TR 留日文 ---
        if _is_real_missed(s, h, segs, high):
            # Q12-A(2026-09-06): mixed 行漏译不自动补 → 收集人工清单(用户手动修改)。
            #   理由: mixed 行补译风险高(切碎/残留/宽度错位), 用户明确"mix行漏译以手动修改"。
            #   含 art 段的 text 行(L613 型: line_kind=text 但行内图形+台词)同归 mixed 人工。
            has_art = any(sg['kind'] == 'art' for sg in segs)
            if kind == 'mixed' or kind == 'art' or (kind == 'text' and has_art):
                # 2026-09-06: 漏译行自动补译——TR 台词段(对应 SRC 尾台词段位置)若仍整段日文
                # (无中文)或只剩符号 → 模型补译该段。判定看台词段区域而非整行(行首图形可能
                # 含汉字如 AA 用 '八'/'下' 作画, 误判已译)。
                tail = _tail_dial_seg(s, segs)
                if tail is not None:
                    seg, body = tail
                    _tr_dial = h[seg["start"]:seg["end"]].replace("\u3000", "").strip(" \u200a\u2002\u2005\u2006\u2009")
                    # 已译判据: 台词段无连续假名串(含长音ー; 中文译文不会出现 ひらがな;
                    # 日文残留必有, 如 賄います/ふーん/うーん)。SRC 原文段与 TR 段相同必漏译。
                    _dial_ok = (not re.search(r"[\u3041-\u3096ー]{2,}", _tr_dial)) and bool(_tr_dial) \
                               and _tr_dial != body
                    if not _dial_ok:
                        # 台词段仍日文/被吞 → 纯专名敬称保留原文, 否则模型补译(P1: 进 job 并发)
                        _pt_b = _pre_terms(body, base, _work)
                        _ed_pre = _swallow_pre(i, s, h, seg, _pt_b, h, honorific=True)
                        if _ed_pre is not None:      # 专名敬称整段 → 本地回填，零调用（与单行路径同一步）
                            edits.append(_ed_pre)
                            continue
                        jobs.append(_Job(
                            i, "swallow", _pt_b,
                            functools.partial(_swallow_apply, i, s, h, seg, _pt_b,
                                              ref_proper=h, ref_model=s,
                                              ok_reason="swallow_translate",
                                              squeeze_retry=True, gloss_hint=gloss_hint),
                            functools.partial(
                                _job_swallow, i, s, h, seg, _pt_b,
                                ref_proper=h, ref_model=s,
                                ok_reason="swallow_translate", gloss_hint=gloss_hint,
                                squeeze_retry=True, base=base, work=_work),
                            # job 未产出(译文用不上) → 落人工清单(与串行版同)
                            (i, s, h, h, "manual_mixed", False, "mixed_missed_manual")))
                        continue
                edits.append((i, s, h, h, "manual_mixed", False,
                              "mixed_missed_manual"))
                continue
            # text 行(真纯 text: 无 art 段)漏译 → 整行翻译(用户内容行为主, 正常提示词, 允许超宽)
            # 2026-09-06: 仅当整行确无图形残片(真纯文本)才整行翻; 有残片/art 落 manual
            _has_frag = any(not _looks_graphic_fragment(s[_sg["start"]:_sg["end"]],
                             _sg.get("src_width") or (_sg["end"] - _sg["start"]))
                             for _sg in segs if _sg["kind"] == "text"
                             and (_sg.get("confidence", 1.0) < 0.65))
            if kind == 'text' and not any(sg["kind"] == "art" for sg in segs) and not _has_frag:
                # 保留前导空白(排版对齐), 只翻译正文; 纯 text 行无 art 保护(可超宽) → P1: job
                lead = s[:len(s) - len(s.lstrip(' \u3000'))]
                _pt_p = _pre_terms(s.strip(), base, _work)
                jobs.append(_Job(
                    i, "poly", _pt_p,
                    functools.partial(_poly_apply, i, s, h, lead, _pt_p,
                                      gloss_hint=gloss_hint, base=base, work=_work),
                    functools.partial(_job_poly, i, s, h, lead, _pt_p,
                                      gloss_hint, base, _work),
                    None))
            else:
                edits.append((i, s, h, h, "manual_mixed", False,
                              "mixed_missed_manual"))
            continue

        # --- 动作1 错位修正 ---
        # 2026-09-06: mixed 与 text 但【行首是真 art 宽块】都检查对齐(主流程整行翻译会吞
        # text 行行首 art——L1322/1406/1480 等 SRC 行首房子/立绘被吞只剩台词+垫宽)。
        # 触发条件: 行首 seg0 为 art 且 src_width>=8(真画面块)。词内碎段(王子/ニ/一人/冒号
        # 被 segment_line 误切 art)不在行首或宽度<8 → 不触发(L553/327/315/125 等骰子/选项行)。
        _lead_art = bool(segs) and segs[0]["kind"] == "art" \
            and (segs[0].get("src_width") or (segs[0]["end"] - segs[0]["start"])) >= 8
        if kind == 'mixed' or (kind == 'text' and _lead_art):
            # 2026-09-06: art 对齐 True 也可能台词区被吞(图形完整但台词段被换成 。/…——
            # L1577/1579: art 列位没坏 → _verify 通过, 但 conf=None 尾段在 TR 里只剩符号)。
            # 这类行走 swallow_translate 补译, 不进 rebuild。
            if T._verify_art_alignment(s, h, segs):
                tail_sw = _tail_dial_seg(s, segs)
                if tail_sw is not None:
                    _ts, _tb = tail_sw
                    # TR 台词区(段起点之后)是否只剩符号/空白(无中文无 ≥2 连续假名)
                    _tr_tail = h[_ts["start"]:].replace("\u3000", "").strip(" \u200a\u2002\u2005\u2006\u2009")
                    _tr_ok = bool(re.search(r"[\u4e00-\u9fff]", _tr_tail)) or \
                             bool(re.search(r"[\u3041-\u3096]{2,}", _tr_tail))
                    if not _tr_ok:
                        # 被吞 → 保留专名或模型补译(P1: job; 未产出则此行不动)
                        _pt_t = _pre_terms(_tb, base, _work)
                        _ed_pre = _swallow_pre(i, s, h, _ts, _pt_t, h, honorific=True)
                        if _ed_pre is not None:
                            edits.append(_ed_pre)
                            continue
                        jobs.append(_Job(
                            i, "swallow", _pt_t,
                            functools.partial(_swallow_apply, i, s, h, _ts, _pt_t,
                                              ref_proper=h, ref_model=h,
                                              ok_reason="swallow_translate",
                                              strip_idx=False, gloss_hint=gloss_hint),
                            functools.partial(
                                _job_swallow, i, s, h, _ts, _pt_t,
                                ref_proper=h, ref_model=h,
                                ok_reason="swallow_translate", gloss_hint=gloss_hint,
                                strip_idx=False, base=base, work=_work),
                            None))
                continue  # art 对齐正确 → 不动
            if not T._verify_art_alignment(s, h, segs):
                out_notes = []
                new_line, ok, reason = rebuild_row(s, h, segs, out_notes)
                if ok and new_line is not None:
                    edits.append((i, s, h, new_line, "rebuild", True, reason))
                else:
                    # 2026-09-06: rebuild 提不到译文(TR 已被吞只剩符号/无中文候选)时,
                    # 若行尾有完整台词段(conf=None 宽>=14) → 模型补译该段回填, 不放弃。
                    # (17810 L1482/1529/1531/1577/1579: SRC 整句台词被旧还原逻辑吞成 。/…)
                    tail = _tail_dial_seg(s, segs)
                    if tail is not None:
                        seg, body = tail
                        _pt_t2 = _pre_terms(body, base, _work)
                        jobs.append(_Job(
                            i, "swallow", _pt_t2,
                            functools.partial(_swallow_apply, i, s, h, seg, _pt_t2,
                                              ref_proper=h, ref_model=s,
                                              ok_reason=f"tail_translate:{reason}",
                                              err_reason="tail_translate_err",
                                              gloss_hint=gloss_hint),
                            functools.partial(
                                _job_swallow, i, s, h, seg, _pt_t2,
                                ref_proper=h, ref_model=s,
                                ok_reason=f"tail_translate:{reason}", gloss_hint=gloss_hint,
                                err_reason="tail_translate_err", honorific=False,
                                base=base, work=_work),
                            (i, s, h, h, "rebuild", False, f"tail_translate_oversize:{reason}")))
                    else:
                        edits.append((i, s, h, h, "rebuild", False, reason))

    # ---- P1(2026-09-15): 收集完毕 → 并发跑所有需要模型的段落，再按行号合并 ----
    # 行内本地判定已同步完成(segment_line/_is_real_missed/art 对齐/rebuild_row)，
    # 这里只是把"发模型调用"并行化；结果仍按行号排序，与串行版顺序一致。
    if jobs:
        # 2026-10-10: 行级艺术闸门 —— 主链不会送模型的行（art_line_veto / meta kind=art 且无
        #   3+ 真台词假名 / header / blank）一律不派模型 job，按「job 未产出」走调用方回退
        #   （mixed 补译 → 人工清单；poly → 不动）。本地路径已在循环内跑完，不受影响。
        if art_gate_enabled():
            _kept = [j for j in jobs if not _row_model_gate(j.i, src, _meta0)]
            n_art_gate = len(jobs) - len(_kept)
            if n_art_gate:
                print("[%s] 行级艺术闸门拦下 %d 行（主链不送模型 → 不切段补译）"
                      % (base, n_art_gate), flush=True)
            jobs = _kept
        # P0(2026-10-03): 默认批量（同章同类合并成块，调用数 ↓ ~10×）；AA_REFINE_BATCH=0 回退单行
        if jobs and _bsz > 0 and len(jobs) > 1:
            results_by_row = _run_jobs_batched(jobs, workers, gloss_hint=gloss_hint,
                                               chunk=_bsz, base=base)
        else:
            results_by_row = _run_jobs([(j.i, j.single) for j in jobs], workers)
        for j in jobs:
            ed = results_by_row.get(j.i)
            if ed is None:
                ed = j.fallback    # job 未产出 → 调用方指定的回退 edit(可能为 None=不动)
            if ed is not None:
                edits.append(ed)
        edits.sort(key=lambda e: e[0])

    # review_inline 补充输入（Q5）
    if review_inline:
        rv_p = os.path.join(work_dir, "review_inline.txt")
        if os.path.exists(rv_p):
            pass  # 已在主循环覆盖全章节；review_inline 行若未被主循环处理则由裁决兜底。

    # 2026-09-13: 写回前统一定型 —— ① 所有 ok 编辑 pad 到 SRC 宽(只补不裁, poly 曾产短宽行);
    #   ② 处理后仍含连续假名(≥2)的行在 reason 标「+残留假名」并计数——半翻行/专名保留
    #   都会命中, 目的是让它们**可见**(原先记 OK 静默写回, 例 L402 日语+中文尾巴共存)。
    _partial = 0
    _norm = []
    for (i, s, old, new, act, ok, reason) in edits:
        if ok and new:
            new = _pad_line_to_src(new, s)
            if act != "restore" and KANA_RESIDUAL.search(new) and "残留假名" not in reason:
                reason = f"{reason}+残留假名"
                _partial += 1
        _norm.append((i, s, old, new, act, ok, reason))
    edits = _norm

    # P1(2026-10-03): 词表一致性护栏 —— 编辑把"已落地的标准译名"改掉/改回日文 → 丢弃该编辑
    _guard_rej = []
    if _term_pairs:
        _kept = []
        for e in edits:
            _i, _s, _old, _new, _act, _ok, _reason = e
            if _ok and _new and _new != _old and _act != "restore":
                _bad = _term_guard_reason(_old, _new, _term_pairs)
                if _bad:
                    _guard_rej.append((_i, _bad))
                    continue           # 宁可不改：保持原行
            _kept.append(e)
        edits = _kept
        if _guard_rej:
            print("[%s] 词表护栏拦下 %d 处编辑（标准译名被改/改回日文）：%s"
                  % (base, len(_guard_rej),
                     "、".join("L%d %s" % (i + 1, r) for i, r in _guard_rej[:6])), flush=True)

    # 计数(2026-09-15 P1: 原在分支内 +=1，现统一由最终 edits 派生——与串行版同口径:
    #   restore 全算; rebuild/poly 只算写回的(ok=True))
    n_restore = sum(1 for e in edits if e[4] == "restore")
    n_rebuild = sum(1 for e in edits if e[4] == "rebuild" and e[5])
    n_poly = sum(1 for e in edits if e[4] == "poly" and e[5])
    n_artk = sum(1 for e in edits if e[6] == "art_kana_restore")

    return base, {"restore": n_restore, "rebuild": n_rebuild, "poly": n_poly,
                  "total": len(edits), "partial_kana": _partial,
                  "art_kana_restore": n_artk, "skip_white": n_skip_white,
                  "art_gate_skip": n_art_gate,
                  "term_guard_rejected": len(_guard_rej)}, edits


def _file_sha1(p):
    """文件 sha1（章级增量指纹用；缺失返回 ""）。"""
    if not os.path.exists(p):
        return ""
    try:
        h = hashlib.sha1()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _ckpt_path(cfg, base):
    return os.path.join(cfg["work_dir"], "refine_ckpt", base + ".json")


def _ckpt_ok(cfg, base):
    """章级增量判定(2026-09-15)：上次校对完成且原文+汉化文件 sha1 均未变 → 可零调用跳过。

    指纹含**汉化文件本身** —— 重译/工作台改行/术语替换都会使其失效 → 自动重校(安全方向)。
    另存 unfixed 计数(模型修不动、落人工的行)，仅供任务页/报告观察，不阻止跳过
    (否则 真・女神転生 类每轮 112 次调用的行会被无限重问)。
    """
    p = _ckpt_path(cfg, base)
    if not os.path.exists(p):
        return False
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(d, dict) or not d.get("done"):
        return False
    src_sha = _file_sha1(os.path.join(cfg["aa_dir"], base + ".txt"))
    tr_sha = _file_sha1(os.path.join(cfg["out_dir"], base + "_汉化.txt"))
    if not src_sha or not tr_sha:
        return False
    return d.get("src_sha1") == src_sha and d.get("tr_sha1") == tr_sha


def _ckpt_save(cfg, base, stats, edits):
    """落章级 ckpt（原子写；写盘后调用，指纹=当前磁盘文件）。"""
    try:
        p = _ckpt_path(cfg, base)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        d = {
            "done": True,
            "src_sha1": _file_sha1(os.path.join(cfg["aa_dir"], base + ".txt")),
            "tr_sha1": _file_sha1(os.path.join(cfg["out_dir"], base + "_汉化.txt")),
            "changed": sum(1 for e in edits if e[5] and e[3] != e[2]),
            "unfixed": sum(1 for e in edits if not e[5]),
            "partial_kana": stats.get("partial_kana", 0),
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, p)
    except Exception:
        pass


def process_work(work, eids=None, do_write=False, review_inline=False, force=False):
    cfg = _reg_get(work)
    cfg["work"] = work          # 术语替换钩子需要作品 id（registry 配置里没有）
    # 2026-09-22: 校对链 = 付费链（按作品 r18 过滤）→ 每个作品解析一次
    _ch = _set_chain_for_work(work)
    print(f"[refine] 后端链(跟随付费链): {_ch}"
          + (f" 模型覆盖 {_REFINE_CHAIN_MODELS}" if _REFINE_CHAIN_MODELS else ""), flush=True)
    out_dir = cfg["out_dir"]
    if eids:
        files = [f"entry_{e}.txt" for e in eids]
    else:
        files = sorted(f for f in os.listdir(cfg["aa_dir"]) if f.endswith(".txt")
                       and not f.endswith("_台词.txt"))
    import task_report as TR   # env 无 AA_TASK_ID 自动 no-op(直跑零侵入)
    results = []
    n_files = len(files)
    n_skip = 0
    # 2026-10-02: review_inline 去重键（重跑不追加重复行）——键 = `entry_X.txt | line N |`
    _rv_seen = set()
    if do_write:
        _rv_p = os.path.join(cfg["work_dir"], "review_inline.txt")
        if os.path.exists(_rv_p):
            try:
                with open(_rv_p, encoding="utf-8") as _f:
                    for _ln in _f:
                        _m = re.match(r"^(entry_\d+\.txt) \| line (\d+) \|", _ln)
                        if _m:
                            _rv_seen.add(f"{_m.group(1)} | line {_m.group(2)} |")
            except Exception:
                _rv_seen = set()
    # 2026-10-01: 备份目录按**每次运行**一个（原来每章一个 → 一次 20 章跑出 20 个目录，
    # 「同类只留最近 2 次」就会把本次前半段的章也删掉）。本目录装本次所有改动章的覆写前文件。
    _bak_dir = ""
    if do_write:
        _bak_dir = os.path.join(cfg["work_dir"], "backup_refine_aa_" + time.strftime("%Y%m%d%H%M%S"))
    for fi, fname in enumerate(files):
        # 2026-09-07: 任务协议——章节边界停止 + 心跳(校对阶段进度)
        st = TR.check_stop()
        if st:
            print(f"[task] 收到停止请求({st}), 校对在章节边界退出", flush=True)
            raise _TaskStop()
        TR.heartbeat("校对", fi, n_files)
        base = fname[:-4]
        # 2026-09-15 章级增量(P3): 上次已校对完且原文/汉化 sha1 未变 → 零调用跳过。
        #   只在 --write 生效(dry 跑始终处理，供 A/B 对比)；--force / AA_REFINE_FORCE=1 强制重校。
        if do_write and not force and _ckpt_ok(cfg, base):
            n_skip += 1
            # 行首 [entry_xxx] 契约必须保留——translate_runner._refine_missing 据此算已校对章
            print(f"[{base}] 已校对且原文/译文未变动, 跳过(0 次调用; --force 可强制重校)")
            results.append((base, {"skipped_ckpt": 1, "restore": 0, "rebuild": 0, "poly": 0,
                                   "total": 0, "partial_kana": 0}, []))
            TR.heartbeat("校对", fi + 1, n_files, force=True)
            continue
        base, stats, edits = refine_chapter(cfg, base, do_write, review_inline)
        # 2026-10-02: 校对阶段的**模型/调用失败**行不再静默——落 review_inline 供人工补。
        #   实测定时任务钉 `AA_REFINE_BACKEND=mimo`（不降级）时 429 会把章尾几行留成日文，
        #   原先只在日志打一行 FAIL，产物里完全看不出（entry_22208 L2863 即此）。
        if do_write:
            _fail = [e for e in edits if (not e[5]) and re.search(r"(?:^|:)err:|_err", str(e[6]))]
            if _fail:
                _new = []
                for e in _fail:
                    k = f"{fname} | line {e[0]} |"
                    if k not in _rv_seen:
                        _rv_seen.add(k)
                        _new.append(f"{k} {e[1]} [refine-fallback: {str(e[6])[:80]}]\n")
                if _new:
                    try:
                        with open(os.path.join(cfg["work_dir"], "review_inline.txt"),
                                  "a", encoding="utf-8") as f:
                            f.write("".join(_new))
                        print(f"[{base}] {len(_new)} 行校对调用失败 -> review_inline.txt(人工补)",
                              flush=True)
                    except Exception as _e:
                        print(f"[{base}] 写 review_inline 失败: {_e}", flush=True)
        if edits:
            _pk = stats.get("partial_kana", 0)
            print(f"[{base}] 三类修复: 还原{stats['restore']} 重建{stats['rebuild']} 补译{stats['poly']} (共{stats['total']})"
                  + (f" ⚠️其中 {_pk} 行写回后仍含连续假名(专名保留/半翻, 见 reason)" if _pk else ""))
            for i, s, old, new, act, ok, reason in edits:
                flag = "OK" if ok else "FAIL"
                print(f"  L{i+1} [{act}/{flag}/{reason}] 原:{old[-26:]!r} -> 新:{new[-26:]!r}")
        else:
            print(f"[{base}] 无问题")
        results.append((base, stats, edits))
        # 2026-09-13 修 off-by-one: 本章**完成后**再报 idx=fi+1（原先只在循环头报 fi，
        # 最后一章永不报"已完成" → 界面显示 14/15，即"待译 1"永远挂着）。
        TR.heartbeat("校对", fi + 1, n_files, force=True)
        if do_write and edits:
            # 备份整章（本次运行共用同一个 backup 目录，见 process_work 开头）
            bak = _bak_dir
            os.makedirs(bak, exist_ok=True)
            shutil.copy2(os.path.join(out_dir, fname.replace(".txt", "_汉化.txt")),
                         os.path.join(bak, fname.replace(".txt", "_汉化.txt")))
            new_tr = list(open(os.path.join(out_dir, fname.replace(".txt", "_汉化.txt")), encoding="utf-8").read().split("\n"))
            for i, s, old, new, act, ok, reason in edits:
                if ok and new and new != old and new_tr[i] == old:
                    new_tr[i] = T.one_line(new)     # 护栏：行内换行会把 1 行写成 2 行
            # 写回兜底(2026-09-22)：汉化行数必须与原文一致——不一致即拒写（曾静默多写 2/4 行 → 全章行号右移）
            n_src = len(C.txt_read(C.txt_existing(os.path.join(cfg["aa_dir"], fname))))
            if len(new_tr) != n_src:
                print(f"[{base}] !! 行数不齐(汉化 {len(new_tr)} vs 原文 {n_src}) -> 跳过写回以免全章行号错位；"
                      f"请人工核对本章(常见根因: 模型译文折行被写回)", flush=True)
                stats["write_skipped"] = 1
            else:
                with open(os.path.join(out_dir, fname.replace(".txt", "_汉化.txt")), "w", encoding="utf-8") as f:
                    f.write("\n".join(new_tr))
                print(f"  -> 已写回并备份到 {bak}")
        # 2026-09-15 章级增量(P3): 本章校对完成(无论有无改动)即落 ckpt——指纹=原文+汉化文件 sha1。
        #   缺原文/缺 meta/长度不齐的章不落(下次仍会尝试)。
        if (do_write and not stats.get("skip_len") and not stats.get("skipped")
                and not stats.get("write_skipped")):
            _ckpt_save(cfg, base, stats, edits)
    if do_write:
        # 2026-10-01: 备份目录按**每次运行**一个，且只保留最近 2 次运行（更早的整目录删）。
        if os.path.isdir(_bak_dir):
            C.prune_backup_dirs(cfg["work_dir"], "backup_refine_aa_")
    if do_write and n_skip:
        print(f"[note] {n_skip}/{n_files} 章命中增量 ckpt 零调用跳过(指纹=原文+汉化 sha1; --force 强制重校)")
    return results


class _TaskStop(Exception):
    """任务停止信号(章节边界). 由 process_work 内部抛, main 捕获转退出码 41。"""
    pass


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    work = args[0] if args else None
    eids = None
    if "--eids" in sys.argv:
        eids = sys.argv[sys.argv.index("--eids") + 1].split(",")
    do_write = "--write" in sys.argv
    review_inline = "--review-inline" in sys.argv
    force = "--force" in sys.argv or os.environ.get("AA_REFINE_FORCE") == "1"
    if not work:
        print(__doc__)
        sys.exit(1)
    import task_report as TR
    try:
        results = process_work(work, eids, do_write, review_inline, force=force)
        tot_r = sum(s.get("restore", 0) for _, s, _ in results)
        tot_b = sum(s.get("rebuild", 0) for _, s, _ in results)
        tot_p = sum(s.get("poly", 0) for _, s, _ in results)
        n_sk = sum(1 for _, s, _ in results if s.get("skipped_ckpt"))
        print(f"\n总计: 还原{tot_r} 重建{tot_b} 补译{tot_p}"
              + (f" | 增量跳过 {n_sk} 章" if n_sk else ""))
        TR.finalize(0)
        sys.exit(0)
    except _TaskStop:
        print("[task] 用户停止, 校对在章节边界退出")
        TR.finalize(41)
        sys.exit(41)
