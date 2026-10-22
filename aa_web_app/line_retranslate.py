# -*- coding: utf-8 -*-
"""阅读器「行重译」（Shift+右键行号）：把**单行**重新翻一遍，返回新译文（**不写盘**）。

写盘仍走 `PUT /line/{idx}`（save_line：宽度硬约束 / pad / edits.jsonl 台账 / 统计失效），
本模块只负责「把这一行重新翻一遍」——单一写入口不变。

为什么单独成模块（不塞进 routers/chapters.py）：
  · 走模型要动 `aa_translate` 与**进程 env** 的模块级全局（WORK_ID / AA_BACKEND / 链首模型 env），
    必须集中在一处 + 加锁（与 selection.py 同口径：uvicorn 单进程多线程，全局是共享的）；
  · 判据（行类型闸门 / mixed 段级协议 / 章节级 R18 / 宝可梦固定表直出）要能脱离 web 单测。

口径（与主链同源，禁另造一套）：
  · 行类型闸门 = override > `work/line_meta_v2` 的 kind > `logic.line_type_of`，
    **只放 text / mixed**（AA 艺术行重译必被推位/译坏；帖头/空行无意义）；
  · text 行 → `translate_lines`（宽度/残留/照抄/art 列位四重校验都在它里面）；
  · mixed 行 → 有可发段（meta segments 的 conf>=0.65 ∩ 非艺术，或规则抠段）走
    `translate_lines_mixed`，否则退回整行协议 —— 与 process_chapter 的分流一致；
  · 上下文 = `_chunk_context`（前 3 楼台词），与主链同口径（治「鏡→眼镜」类多义词）；
  · 章节级 R18 = `_chapter_work_r18(base, cfg)`；链首 = `aa_config.auto_backend(r18)`，
    写成 AA_BACKEND + **AA_BACKEND_AUTO=1**（不设 = 云端直连 `[default]` 那个旧默认模型，
    不是本作链上的端点，实测慢且不同源）；
  · `pipeline=pokemon` 的作品先走固定表/本作 wiki 直出（`match_line` + `finalize`，零 API）——
    那些行的标准译文本来就是表里的，送模型只会把它改坏。
"""
import contextlib
import os
import threading
import time

from aa_common import KANA_RE, meta_load
from aa_web_app import logic

_ENV_LOCK = threading.Lock()        # 走模型期间进程级串行（env / aa_translate 全局）
ALLOWED_KINDS = ("text", "mixed")   # 只放文本行与混合行
KIND_CN = {"aa": "AA 艺术", "art": "AA 艺术", "header": "帖头", "blank": "空行"}


def src_name_of(fname):
    return logic.src_name_of(fname)


def read_src_lines(cfg, fname):
    """读该章原文行（aa/）。"""
    return logic.read_lines(logic.txt_path(cfg["aa_dir"], logic.src_name_of(fname)))


def read_tr_lines(cfg, fname):
    """读该章当前译文行（汉化/）；未汉化章返回 []。"""
    try:
        return logic.read_lines(logic.txt_path(cfg["out_dir"], fname))
    except Exception:
        return []


def new_kana(cur, text):
    """重译结果里**当前译文没有**的假名字符（按字符计数，返回排序列表）。

    用途 = 写盘前最后一道闸：段级协议的 meta 段边界有时把台词尾巴留在 art 段
    （实测 `…暮らしてい`/`た　…`：`た` 被切进 art 段），主流转出来就是
    成品里凭空出现一个假名（在补位空白中间，宽度/残留/art 三个局部校验全过）。
    项目铁律「汉化行不写日文假名」，故这类结果一律**拒绝写入**交用户判断。
    """
    from collections import Counter
    ka = Counter(ch for ch in cur if KANA_RE.match(ch))
    kb = Counter(ch for ch in text if KANA_RE.match(ch))
    return sorted((kb - ka).elements())


def kind_of(cfg, fname, idx, src_lines=None):
    """行类型（override > line_meta_v2.kind > 规则）——与阅读器行号列同一分类链。

    返回 text / mixed / art / header / blank（art 是 override 里写的 kind 值，
    与 meta 的 kind 同词表；阅读器显示时映射成 aa）。
    """
    base = logic.txt_key(logic.src_name_of(fname))
    try:
        ov = (logic.load_overrides(cfg["work_dir"], base) or {}).get(str(idx + 1))
    except Exception:
        ov = None
    if ov:
        return ov
    meta = meta_load(os.path.join(cfg["work_dir"], "line_meta_v2"), base, None) or {}
    mv = meta.get(str(idx + 1)) if isinstance(meta, dict) else None
    mk = mv.get("kind") if isinstance(mv, dict) else None
    if mk:
        return mk
    src = src_lines if src_lines is not None else read_src_lines(cfg, fname)
    return logic.line_type_of(src[idx]) if 0 <= idx < len(src) else "blank"


def _err(code, msg):
    return {"ok": False, "code": code, "error": msg}


@contextlib.contextmanager
def _chain_env(work_r18):
    """临时把进程 env 指到本作链首（退出即逐键还原）。yield 实际后端名。

    用户显式设了 AA_BACKEND（`.env` 逃生口）→ 原样尊重，不覆盖、不设 AUTO 位。
    AA_BACKEND_AUTO=1 的语义（与 runner 同口径）：这只是「代码默认的链首」，
    不参与组前置——链序/勾选集以 `AA_CHAIN`（控制台勾选 / 设置页保存）为准。
    """
    import aa_config as AC
    cur = (os.environ.get("AA_BACKEND") or "").strip()
    if cur and cur.lower() != "cloud":
        yield cur
        return
    b, m = AC.auto_backend(bool(work_r18))
    mkey = (AC.model_env(b) if m else "") or ""
    keys = ["AA_BACKEND", "AA_BACKEND_AUTO"] + ([mkey] if mkey else [])
    old = {k: os.environ.get(k) for k in keys}
    try:
        os.environ["AA_BACKEND"] = b
        os.environ["AA_BACKEND_AUTO"] = "1"
        if mkey:
            os.environ.setdefault(mkey, m)
        yield b
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _segments_for(T, cfg, base, idx, line):
    """mixed 行的段级 segments（无可发段 → None，退回整行协议）。同一行分流的单一入口。"""
    if not T.mixed_segments_enabled():
        return None
    segs = None
    try:
        seg_meta = T.load_meta_segments_0based(
            os.path.join(cfg["work_dir"], "line_meta_v2"), base)
        segs = (seg_meta or {}).get(idx)
    except Exception:
        segs = None
    if segs and T._sendable_text_segs(line, segs):
        return segs
    if T.mixed_seg_rule_enabled():
        segs2 = T.mixed_rule_segments(line)
        if segs2 and T._sendable_text_segs(line, segs2):
            return segs2
    return None


def pokemon_direct(work, cfg, base, line):
    """宝可梦管线：固定表 / 本作 wiki / 模板 / 替换轨直出（零 API）。

    命中返回 (zh, track)；未命中返回 (None, "")——调用方落回模型。
    与 `pokemon_fixed.prefill` 逐行同一套（match_line → 判据 A → finalize 四重护栏），
    否则「重译」会把表里的标准译名换成模型现编的。
    """
    try:
        import pokemon_fixed as PF
        import aa_translate as T
    except Exception:
        return None, ""
    try:
        table = PF.load(work, cfg["work_dir"])
        if not table:
            return None, ""
        terms = PF.merged_terms(work, cfg["work_dir"])
    except Exception:
        return None, ""
    try:
        import pre_terms as _PT
        pt_pairs = _PT.pairs_for(base, work=work)
    except Exception:
        pt_pairs = []
    hit = PF.match_line(line, table, terms, work=work, pairs=pt_pairs)
    if not hit and PF.preterm_enabled():
        try:
            need, _res, phits, new = PF.needs_translation(line, base=base, work=work,
                                                         pairs=pt_pairs)
            if not need and phits:
                hit = {"track": "preterm", "zh": new, "term": "", "src": "替换轨",
                       "no_art": PF._no_art(line)}
        except Exception:
            pass
    if not hit:
        return None, ""
    try:
        gp = [(j, z) for j, z in T._load_glossary_pairs(base) if not j.startswith("re:")]
    except Exception:
        gp = []
    try:
        ok, zh, _why = PF.finalize(line, hit, gp)
    except Exception:
        return None, ""
    return (zh, hit.get("track", "")) if ok else (None, "")


def retranslate_line(work, cfg, fname, idx):
    """重译单行（不写盘）。返回 dict：

    成功 `{ok, text, kind, track, backend, elapsed, warnings, source}`；
    失败 `{ok: False, code, error}`（code 直接给路由当 HTTP 状态）。
    """
    if not (fname.endswith("_汉化.txt") or fname.endswith("_汉化.txt.gz")):
        return _err(422, "未汉化章节不可重译（仅原文可读）")
    src_lines = read_src_lines(cfg, fname)
    if not (0 <= idx < len(src_lines)):
        return _err(404, f"行号越界: {idx + 1}")
    src = src_lines[idx]
    if not src.strip():
        return _err(422, "空行不可重译")
    kind = kind_of(cfg, fname, idx, src_lines)
    if kind not in ALLOWED_KINDS:
        return _err(422, f"该行是 {KIND_CN.get(kind, kind)} 行，不可重译（只支持文本行 / 混合行）")

    import time
    base = logic.txt_key(logic.src_name_of(fname))
    t0 = time.time()
    try:
        import aa_translate as T
    except Exception as e:                      # 缺 aa_translate（异常环境）
        return _err(500, f"aa_translate 不可用: {e}")

    # ① 宝可梦管线先走直出（零 API）
    try:
        import work_pipeline as WP
        is_pk = WP.pipeline_of(work) == WP.POKEMON
    except Exception:
        is_pk = False
    if is_pk:
        try:
            zh, track = pokemon_direct(work, cfg, base, src)
        except BaseException as e:             # 见下方 SystemExit 说明
            return _err(409, f"固定表直出失败: {type(e).__name__}: {e}")
        if zh:
            return {"ok": True, "text": zh, "kind": kind, "track": track or "fixed",
                    "backend": "", "elapsed": round(time.time() - t0, 2),
                    "warnings": [], "source": "fixed"}

    # ② 模型重译（一次调用；四重校验在 translate_lines* 内部）
    #    ⚠️ BaseException 而非 Exception：`glossary_prompt_hint` 在**作品术语表缺失**时
    #    设计性 `raise SystemExit`（翻译硬前置）。SystemExit 逃出请求线程会**直接杀
    #    uvicorn**（2026-09-08 划词 500 事故同款），故在此转成给用户看的错误（消息里
    #    带着「先生成作品术语表」的两条路径），绝不外抛。
    try:
        with _ENV_LOCK:
            old_wid = T.WORK_ID
            T.WORK_ID = work
            try:
                r18, low18 = T._chapter_work_r18(base, cfg)
                segs = _segments_for(T, cfg, base, idx, src) if kind == "mixed" else None
                with _chain_env(r18) as bk:
                    if segs:
                        fixed, missed, warns = T.translate_lines_mixed(
                            [(idx, src, segs)], work_r18=r18, low_r18=low18, base=base)
                    else:
                        ctx = ""
                        try:
                            ctx = T._chunk_context(src_lines, idx)
                        except Exception:
                            ctx = ""
                        fixed, missed, warns = T.translate_lines(
                            [(idx, src)], work_r18=r18, low_r18=low18, context=ctx, base=base)
            finally:
                T.WORK_ID = old_wid
    except SystemExit as e:
        return _err(409, str(e) or "作品术语表缺失（翻译硬前置）——先生成作品术语表")
    except BaseException as e:
        return _err(502, f"重译调用失败: {type(e).__name__}: {e}")

    text = (fixed or {}).get(idx)
    if not text or text == src:
        why = ("、".join(warns[-2:]) if warns else "模型未返回有效译文（未返回 / 四重校验不过）")
        return _err(502, f"未产出可用译文：{why}")
    cur = read_tr_lines(cfg, fname)
    cur_l = cur[idx] if idx < len(cur) else ""
    # 参照口径：当前译文；该行译文缺失（行数不齐等）→ 退回原文行（原文里的假名必然合法）
    extra = new_kana(cur_l if cur_l.strip() else src, text)
    if extra:
        return _err(422, "重译结果里出现了当前译文没有的假名（%s）——疑似段级边界把台词当艺术留下，"
                         "已放弃写入；要接受请手工编辑该行" % "、".join(extra[:5]))
    return {"ok": True, "text": text, "kind": kind, "track": "mixed-seg" if segs else "model",
            "backend": bk or "", "elapsed": round(time.time() - t0, 2),
            "warnings": list(warns or [])[:5], "source": "model"}
