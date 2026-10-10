#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""精译独立流程 runner（refine_runner）——对照原文式精译。

设计文档: doc/TODO/精译独立流程细化方案_2026-08-30.md（决策 1-11 + 审批批注 A1-A5）
上游: doc/TODO/台词提取与精译设计_2026-08-29.md 第③节

流程: 输入配对(原文/汉化台词表) → 分块 → prompt(知识库前缀+前文续接+疑点标注)
      → call_siliconflow(V4-Flash 付费直连) → 解析 → 防线(假名/截断) → 回填
      → 宽度校验 → 章级原子落盘 + 块级 ckpt + 脏章 sha1 检测。

用法:
  python scripts/runner/refine_runner.py --work <id> [--chapters 1-3|all|eids] [--force]
任务管理器: 注册 refine 类型(env AA_TASK_ID/AA_TASK_HB/AA_STOP_* 心跳/停止协议, 复用 task_report)。
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import time

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import aa_config as AC
import aa_backend as AB
import aa_translate as AT  # pad_to_width / allow_overwidth / kana_runs / rebuild_mixed_line / load_meta_segments_0based
from extract_dialogue import parse_dialogue_file, ensure_dialogue_file

try:
    from task_report import heartbeat as _hb, check_stop, finalize as _finalize, enabled as _report_enabled
    from task_report import TaskStoppedError
except ImportError:
    _hb = _finalize = lambda *a, **k: None
    check_stop = lambda only=None: None
    TaskStoppedError = RuntimeError

# ---- 台词表行解析（与 extract_dialogue 的 _DLGLINE 同构） ----
DLGLINE = re.compile(r'^L(\d+)(?:_(\d+))?\s+\[(主|用|\?)\]\s?(.*)$')
# 模型输出契约: L<行>[_<段>]|<新中译>
OUT_LINE = re.compile(r'^L(\d+)(?:_(\d+))?\|(.*)$')

# 假名检测（排除艺术假名——同 aa_translate.kana_runs 口径，但需段级判断）
HIRA = re.compile(r'[ぁ-ん]')
KATA = re.compile(r'[ァ-ヶ]')


def _has_kana(text, old=""):
    """段是否含「新增」真实假名（相对旧译）。旧译里已有的假名（角色名/art 照抄）
    不拒；只有新译新增的假名才判未翻译。"""
    if not text:
        return False
    old_kana = set(AT.kana_runs(old)) if old else set()
    new_runs = AT.kana_runs(text)
    if not new_runs:
        return False
    # 每个 run 若整体已在旧译中出现(角色名等) → 不算新增
    for run in new_runs:
        if run not in old_kana and run not in (old or ""):
            return True
    return False


def _sha1_file(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _est_tokens(seg_texts):
    """粗略 token 估算: CJK/假名按 1.5, ASCII 按 0.3。"""
    n = 0
    for t in seg_texts:
        for ch in t:
            if ord(ch) < 128:
                n += 0.3
            else:
                n += 1.5
    return int(n)


class RefineCfg:
    """[refine] 节 + 运行时参数。"""

    def __init__(self, work):
        self.work = work
        self.backend = AC.get("refine", "backend", "commandcode")
        self.model = AC.get("refine", "model", "deepseek/deepseek-v4-flash")
        self.key_env = AC.get("refine", "key_env", "COMMANDCODE_API_KEY")
        self.max_tokens = int(AC.get("refine", "max_tokens", 32768))
        self.temperature = float(AC.get("refine", "temperature", 0.1))
        self.reasoning_effort = AC.get("refine", "reasoning_effort", "low")
        self.chunk_max_segs = int(AC.get("refine", "chunk_max_segs", 250))
        self.chunk_max_tokens = int(AC.get("refine", "chunk_max_tokens", 12000))
        self.retry_single = int(AC.get("refine", "retry_single", 1))
        self.retry_chunk = int(AC.get("refine", "retry_chunk", 1))
        # registry
        import registry
        self.cfg = registry.WORKS[work]
        self.work_dir = self.cfg["work_dir"]
        self.aa_dir = self.cfg["aa_dir"]
        self.out_dir = self.cfg["out_dir"]
        self.refine_dir = os.path.join(os.path.dirname(self.out_dir), "精译")
        self.ckpt_dir = os.path.join(self.work_dir, "refine_ckpt")
        self.review_path = os.path.join(self.work_dir, "refine_review.txt")
        os.makedirs(self.refine_dir, exist_ok=True)
        os.makedirs(self.ckpt_dir, exist_ok=True)


def _chapter_order(cfg):
    """章节文件列表（复用 gen_aa_html.chapter_order，管线同款排序）。"""
    from gen_aa_html import chapter_order
    return chapter_order(cfg["out_dir"], cfg.get("name_style"), cfg.get("work_dir"))


def _load_knowledge_prefix(rf, base):
    """知识库前缀: 术语/人物 + 本章中文简介（A5）。缺失返回空字符串不报错。"""
    parts = []
    kb = os.path.join(rf.work_dir, f"{rf.work}_knowledge.json")
    if os.path.exists(kb):
        try:
            d = json.load(open(kb, encoding="utf-8"))
        except Exception:
            d = {}
        # 人物
        chars = d.get("人物") or {}
        if chars:
            names = "、".join(f"{k}({v})" for k, v in list(chars.items())[:40])
            parts.append(f"角色译名对照: {names}")
        # 术语
        terms = d.get("术语") or {}
        if terms:
            ts = "、".join(f"{k}={v}" for k, v in list(terms.items())[:30])
            parts.append(f"术语表: {ts}")
        # 章节简介（键可能是标题或 base）
        intro = ""
        for k in (base, os.path.splitext(base)[0]):
            if isinstance(d.get("章节简介"), dict) and k in d["章节简介"]:
                intro = d["章节简介"][k]
                break
        if intro:
            parts.append(f"本章剧情简介: {intro}")
    # 全局 glossary 提示（复用 aa_translate.glossary_prompt_hint 的知识库注入逻辑）
    hint = ""
    try:
        hint = AT.glossary_prompt_hint(base=base)
    except Exception:
        hint = ""
    if hint:
        parts.append(hint)
    return "\n".join(parts)


def _load_proofread_issues(rf, base):
    """加载 proofread issues 按行号索引。issues line 是整行号（1-based），
    mixed 行展开到该行所有段（A3）。返回 {line_no: [issue,...]}。"""
    p = os.path.join(rf.work_dir, f"proofread_{base}.json")
    if not os.path.exists(p):
        return {}
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for iss in d.get("issues", []):
        ln = iss.get("line")
        if not ln:
            continue
        out.setdefault(int(ln), []).append(iss)
    return out


# 台词净化: 剥离行首/行内 art 残留字符（mixed 行 text 段可能带 art 前缀）
# U+3000 全角空格/半角空格/常见 art 符号（､ｨｲｱ 等半角假名/符号 + ASCII 画线符号）
_ART_LEAD = re.compile(r'^[\s\u3000､ｨｩｪｫｬｭｮｯｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜｦﾝﾞﾟ･ｰ｡｢｣ｧｨｩｪｫｬｭｮｯｶﾞｷﾞｸﾞｹﾞｺﾞｻﾞｼﾞｽﾞｾﾞｿﾞﾀﾞﾁﾞﾂﾞﾃﾞﾄﾞﾊﾞﾋﾞﾌﾞﾍﾞﾎﾞ/;:iI|!\\(){}<>_=+~^*\'\"`\-]+')
# 真台词判定: 含汉字(非ART) 或 真假名 run
_HANZI = re.compile(r'[\u4e00-\u9fff]')


def _clean_dialogue_text(t):
    """剥离台词段行首 art 残留；若净化后无真台词（纯 art）返回空串（调用方跳过）。"""
    if not t:
        return t
    t = _ART_LEAD.sub('', t)
    # 纯 art 检测: 无汉字且无真假名 → 空
    runs = AT.kana_runs(t)
    if not _HANZI.search(t) and not runs:
        return ''
    # 无汉字 + 假名 run 全是单字符重复(メメメ/ﾒﾒﾒ art 纹理) → 空
    if not _HANZI.search(t) and runs and all(len(set(r)) <= 1 for r in runs):
        return ''
    return t


def _pair_dialogue(rf, base, force=False):
    """原文+汉化台词表配对。返回 (pairs, meta_segs)。

    pairs: [{line_no, seg_idx, src, cn, op}] 按行号段号序（text 已净化 art 残留）。
    meta_segs: load_meta_segments_0based 结果（mixed 行回填用）。
    台词表过期检测: 汉化文件 mtime 新于汉化台词表 → 重新生成（方案 A5/脏章）。
    """
    # 过期检测: 汉化文件比台词表新 → 需重新生成
    cn_file = os.path.join(rf.out_dir, f"{base}_汉化.txt")
    cn_dlg = os.path.join(rf.out_dir, f"{base}_台词.txt")
    stale = False
    if os.path.exists(cn_file) and os.path.exists(cn_dlg):
        stale = os.path.getmtime(cn_file) > os.path.getmtime(cn_dlg) + 1
    aa_dlg = ensure_dialogue_file(rf.cfg, base, src_only=True, overwrite=force)
    cn_dlg2 = ensure_dialogue_file(rf.cfg, base, out_dir=rf.out_dir, src_only=False,
                                   overwrite=force or stale)
    src_list = parse_dialogue_file(aa_dlg)
    cn_list = parse_dialogue_file(cn_dlg2)
    cn_map = {(d["line_no"], d["seg_idx"]): d for d in cn_list}
    pairs = []
    for d in src_list:
        key = (d["line_no"], d["seg_idx"])
        cn = cn_map.get(key)
        src = _clean_dialogue_text(d["text"])
        if not src:
            continue  # 纯 art 段(分类器误判)不送模型, 回填时原样保留
        pairs.append({
            "line_no": d["line_no"],
            "seg_idx": d["seg_idx"],
            "src": src,
            "cn": _clean_dialogue_text(cn["text"]) if cn else "",  # A4: 旧译空 = 待补译
            "op": d["op"],
        })
    meta_segs = AT.load_meta_segments_0based(
        os.path.join(rf.work_dir, "line_meta_v2"), base)
    return pairs, meta_segs


def _chunk(pairs, rf):
    """分块: ≤chunk_max_segs 段 + 预估 token ≤chunk_max_tokens。

    段按原行号顺序排列；跨块边界时若当前段与块尾同楼（相同 line_no），
    把整楼挪到下一块（不切断同楼连续语境，方案决策 3）。"""
    chunks, cur, cur_tok = [], [], 0
    for p in pairs:
        est = _est_tokens([p["src"], p["cn"]]) + 8  # 行号开销
        if cur and (len(cur) >= rf.chunk_max_segs or cur_tok + est > rf.chunk_max_tokens):
            # 同楼尾段跨边界 → 回退该楼到下一块
            if p["line_no"] == cur[-1]["line_no"]:
                while cur and cur[-1]["line_no"] == p["line_no"]:
                    popped = cur.pop()
                    cur_tok -= _est_tokens([popped["src"], popped["cn"]]) + 8
                if not cur:
                    # 极端: 整块只有这一个楼（超大楼超上限）→ 强制入块
                    chunks.append([p])
                    cur_tok = 0
                    continue
            chunks.append(cur)
            cur, cur_tok = [], 0
        cur.append(p)
        cur_tok += est
    if cur:
        chunks.append(cur)
    return chunks


def _build_prompt(rf, base, chunk, prev_refined, issues_by_line):
    """构造 user prompt。prev_refined: 前文已精译对照（原|新译）。"""
    lines = []
    for p in chunk:
        iss = issues_by_line.get(p["line_no"], [])
        mark = ""
        if iss:
            whys = "；".join(str(x.get("why", "")) for x in iss[:2])
            mark = f"〈校对疑点: {whys}〉"
        lines.append(f"L{p['line_no']}{'_' + str(p['seg_idx']) if p['seg_idx'] else ''}|{p['src']}|{p['cn']}{mark}")
    user = "\n".join(lines)
    system = _load_knowledge_prefix(rf, base)
    if prev_refined:
        prev_txt = "\n".join(f"原: {s}\n译: {t}" for s, t in prev_refined)
        system += "\n\n本章已精译前文对照（保持译名/语气连贯）:\n" + prev_txt
    return system, user


def _parse_output(text):
    """解析模型输出 → {(line_no, seg_idx): 译文}。解析失败段返回 None 由调用方保留旧译。"""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        m = OUT_LINE.match(line)
        if m:
            ln = int(m.group(1))
            si = int(m.group(2)) if m.group(2) else 0
            out[(ln, si)] = m.group(3).strip()
    return out


def _call_commandcode_ds(system, user, rf, key, timeout=180):
    """直接调 commandcode deepseek-v4-flash（推理模型）。

    必须 reasoning_effort=low（否则 reasoning 吃光输出 content 空）+ 浏览器 UA
    （无 UA 403 code 1010）+ 大 max_tokens（rf.max_tokens=32768, reasoning 吃 token）。
    参考 call_commandcode 的 URL/UA/重试约定，但不受其模型白名单限制。
    """
    import json as _json
    import urllib.request
    payload = {
        "model": rf.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user}],
        "max_tokens": rf.max_tokens,
        "temperature": rf.temperature,
        "reasoning_effort": "low",
        "thinking": {"type": "enabled"},   # deepseek-v4 推理模型必需(参考官方文档)
    }
    data = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "https://api.commandcode.ai/provider/v1/chat/completions",
        data=data,
        headers={
            "Authorization": "Bearer " + (key or ""),
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = _json.load(r)
            msg = resp["choices"][0]["message"].get("content", "")
            if not msg:
                # 推理模型偶发 content 空(reasoning 吃光) → 重试
                raise RuntimeError("commandcode 空响应(reasoning吃光输出)")
            u = resp.get("usage", {}) or {}
            AB._log_usage(u.get("input_tokens") or u.get("prompt_tokens") or 0,
                          u.get("output_tokens") or u.get("completion_tokens") or 0,
                          u=u, model=rf.model, backend="commandcode")
            return msg
        except Exception as e:
            last_err = e
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"commandcode ds failed: {last_err}")


def _call_refine(rf, system, user, timeout=None, retries=5):
    """精译模型通道（按 [refine].backend 路由，不走免费链/降级，无自动兜底）。

    2026-08-30 实测: SiliconFlow V4-Flash 持续挂起 → 改 commandcode deepseek-v4-flash
    （推理模型, reasoning_effort=low, max_tokens 大否则 content 空）。
    key: 按 [refine].key_env 从 .env 读。timeout: 块大小自适应。"""
    key = None
    if rf.key_env:
        # 2026-10-10: key 唯一来源 = 仓库根 .env（+ 真实 env 优先），不再硬编码 hermes 档案路径
        k = AB._read_env_key(rf.key_env) if hasattr(AB, "_read_env_key") else None
        key = k or os.environ.get(rf.key_env)
    backend = rf.backend
    last = None
    for i in range(retries):
        try:
            if backend == "commandcode":
                # 推理模型: 必须 reasoning_effort=low + 大 max_tokens(reasoning 吃输出)
                # call_commandcode 不对 deepseek 自动加 reasoning → 直接构造请求
                return _call_commandcode_ds(system, user, rf, key, timeout or 180)
            return AB.call_siliconflow(system, user, max_tokens=rf.max_tokens,
                                       temperature=rf.temperature, model=rf.model, key=key,
                                       timeout=timeout or 120)
        except Exception as e:
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"refine {backend} failed after {retries} retries: {last}")


def _refine_chunk(rf, base, chunk, prev_refined, issues_by_line, reporter=None):
    """精译一个块。返回 (translations, rejections, stats)。"""
    system, user = _build_prompt(rf, base, chunk, prev_refined, issues_by_line)
    expect = {(p["line_no"], p["seg_idx"]) for p in chunk}
    translations, rejections = {}, []
    stats = {"retried": 0, "rejected": 0, "missing": 0}
    # timeout 按块大小自适应: 30段→100s(实测 39s 成功), 60段→120s; 挂起靠重试兜底
    to = min(150, 70 + len(chunk))
    for attempt in range(rf.retry_chunk + 1):
        text = _call_refine(rf, system, user, timeout=to)
        parsed = _parse_output(text)
        # 只保留本块期望的段（模型偶发输出错位行号/幻觉, 过滤防污染）
        parsed = {k: v for k, v in parsed.items() if k in expect}
        # 块级截断检测: 返回段数远少于输入 → 重试
        if len(parsed) < len(expect) * 0.6:
            stats["retried"] += 1
            if attempt < rf.retry_chunk:
                if reporter:
                    _log(f"    块输出截断({len(parsed)}/{len(expect)}), 重试 {attempt + 1}")
                continue
            stats["missing"] += len(expect) - len(parsed)
        # 逐段防线: 假名拒用（新增假名才拒；旧译已有的角色名/art 照抄不拒）
        old_map = {(p["line_no"], p["seg_idx"]): p["cn"] for p in chunk}
            # 第一遍: 收集含新增假名的段（批量重试，不逐段调用——SiliconFlow 挂起时逐段=灾难）
        bad = {key: t for key, t in parsed.items() if _has_kana(t, old_map.get(key, ""))}
        if bad and rf.retry_single > 0:
            retry_body = "\n".join(
                f"L{k[0]}{'_' + str(k[1]) if k[1] else ''}|{v}" for k, v in bad.items())
            try:
                rt = _call_refine(rf, system, retry_body, timeout=60, retries=2)
                rp = _parse_output(rt)
                for key in list(bad):
                    nt = rp.get(key, bad[key])
                    if not _has_kana(nt, old_map.get(key, "")):
                        bad[key] = nt
                        stats["retried"] += 1
            except Exception as e:
                            pass
            # 第二遍: 应用/拒用
        for key, t in parsed.items():
            old = old_map.get(key, "")
            if key in bad:
                t = bad[key]
                if _has_kana(t, old):
                    stats["rejected"] += 1
                    rejections.append((key, t))
                    continue  # 拒用 → 保留旧译
            translations[key] = t
        # 缺段（未返回的）→ 保留旧译 + 统计
        got = set(translations) | {r[0] for r in rejections}
        stats["missing"] += len(expect - got)
        break
    return translations, rejections, stats


def _refine_chapter(rf, base, reporter=None, force=False):
    """精译一章。返回 stats dict。"""
    t0 = time.time()
    if reporter:
        _log(f"[refine] {base} 开始")
    pairs, meta_segs = _pair_dialogue(rf, base, force=force)
    if not pairs:
        if reporter:
            _log(f"    {base} 无台词段, 跳过")
        return {"segs": 0, "chunks": 0, "changed": 0, "rejected": 0, "seconds": 0}
    # 脏章检测
    cn_dlg = os.path.join(rf.out_dir, f"{base}_台词.txt")
    sha = _sha1_file(cn_dlg) if os.path.exists(cn_dlg) else ""
    ckpt_path = os.path.join(rf.ckpt_dir, f"{base}.json")
    ckpt = {}
    if os.path.exists(ckpt_path) and not force:
        try:
            ckpt = json.load(open(ckpt_path, encoding="utf-8"))
        except Exception:
            ckpt = {}
    if ckpt.get("sha1") == sha and ckpt.get("done"):
        if reporter:
            _log(f"    {base} 已精译且汉化未变, 跳过")
        return {"segs": len(pairs), "chunks": 0, "changed": 0, "rejected": 0, "seconds": 0}
    # 分块
    chunks = _chunk(pairs, rf)
    issues_by_line = _load_proofread_issues(rf, base)
    # 前文已精译对照（跨块续接）
    prev_refined = [(p["src"], ckpt.get("translations", {}).get(str((p["line_no"], p["seg_idx"])), p["cn"]))
                    for p in pairs[:0]]  # 首块无前文; 续跑时从 ckpt 恢复
    if ckpt.get("translations"):
        prev_refined = [(p["src"], ckpt["translations"].get(f"{p['line_no']}_{p['seg_idx']}", p["cn"]))
                        for p in pairs]
    translations, rejections, stats = {}, [], {"retried": 0, "rejected": 0, "missing": 0}
    done_chunks = set(ckpt.get("done_chunks", []))
    for ci, chunk in enumerate(chunks):
        if ci in done_chunks:
            # 从 ckpt 恢复已完成块的译文
            for p in chunk:
                k = f"{p['line_no']}_{p['seg_idx']}"
                if k in ckpt.get("translations", {}):
                    translations[(p["line_no"], p["seg_idx"])] = ckpt["translations"][k]
            continue
        check_stop(only="asap")  # 块边界停止检测（尽快停；chapter 标志留给章节边界）
        if reporter:
            _log(f"    块 {ci + 1}/{len(chunks)} ({len(chunk)} 段)")
        t, r, st = _refine_chunk(rf, base, chunk, prev_refined, issues_by_line, reporter)
        translations.update(t)
        rejections.extend(r)
        for k in ("retried", "rejected", "missing"):
            stats[k] += st[k]
        # 保存块 ckpt（续跑）
        ckpt["translations"] = {f"{p['line_no']}_{p['seg_idx']}": translations.get((p["line_no"], p["seg_idx"]), p["cn"])
                                for p in pairs}
        ckpt["done_chunks"] = sorted(done_chunks | {ci})
        ckpt["sha1"] = sha
        _atomic_write(ckpt_path, ckpt)
        done_chunks.add(ci)
        prev_refined = [(p["src"], translations.get((p["line_no"], p["seg_idx"]), p["cn"])) for p in pairs]
    # 回填: 读原文整行 + 汉化整行, 生成精译文件
    changed = _backfill(rf, base, pairs, translations, meta_segs, reporter)
    ckpt["done"] = True
    ckpt["sha1"] = sha
    ckpt["changed"] = changed
    ckpt["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _atomic_write(ckpt_path, ckpt)
    if reporter:
        _log(f"[refine] {base} 完成: {len(chunks)} 块, 改 {changed} 行, "
                     f"拒用 {stats['rejected']}, 缺 {stats['missing']}, {time.time() - t0:.0f}s")
    return {"segs": len(pairs), "chunks": len(chunks), "changed": changed,
            "rejected": stats["rejected"], "seconds": time.time() - t0}


def _backfill(rf, base, pairs, translations, meta_segs, reporter=None):
    """段级回填 → 精译/<base>_精译.txt（章级原子落盘，不覆盖汉化/）。

    text 行整行替换; mixed 行用 rebuild_mixed_line(load_meta_segments_0based);
    art/header/skip 行原样拷贝; 回填后 pad_to_width 到 SRC 宽。
    返回改动行数。
    """
    aa_path = os.path.join(rf.aa_dir, f"{base}.txt")
    cn_path = os.path.join(rf.out_dir, f"{base}_汉化.txt")
    if not os.path.exists(aa_path) or not os.path.exists(cn_path):
        if reporter:
            _log(f"    ! {base} 缺原文/汉化文件, 跳过回填")
        return 0
    src_lines = open(aa_path, encoding="utf-8").read().split("\n")
    cn_lines = open(cn_path, encoding="utf-8").read().split("\n")
    n = max(len(src_lines), len(cn_lines))
    out_lines = []
    changed = 0
    # 按行聚合段
    by_line = {}
    for p in pairs:
        by_line.setdefault(p["line_no"], []).append(p)
    for i in range(n):
        line_no = i + 1
        if line_no >= len(cn_lines):
            out_lines.append(src_lines[i] if i < len(src_lines) else "")
            continue
        orig = cn_lines[i]
        # 跳过行(# / =====)原样
        if orig.lstrip().startswith("#") or orig.lstrip().startswith("====="):
            out_lines.append(orig)
            continue
        segs = by_line.get(line_no)
        if not segs:
            out_lines.append(orig)  # 无台词段 → 原样
            continue
        # 判断 mixed: meta_segs 有该行且含 art 段
        seg0 = meta_segs.get(line_no - 1) if meta_segs else None
        is_mixed = seg0 and any(sg["kind"] == "art" for sg in seg0)
        if is_mixed:
            # mixed: 只替换 text 段
            tmap = {}
            ok = True
            for p in segs:
                si = p["seg_idx"]
                t = translations.get((p["line_no"], p["seg_idx"]), p["cn"])
                if t is None:
                    ok = False
                    break
                # 找到 meta segments 里对应 text 段下标（seg_idx 与 segments 顺序一致）
                tmap[si] = t
            if ok and seg0:
                rebuilt = AT.rebuild_mixed_line(orig, seg0, tmap)
                if rebuilt is not None:
                    out_lines.append(rebuilt)
                    changed += 1
                    continue
            out_lines.append(orig)
        else:
            # text 行: 整行替换（段 0 的译文）
            p = segs[0]
            t = translations.get((p["line_no"], p["seg_idx"]))
            if t is None:
                t = p["cn"]
            # 保留原文行首空白（台词表 text 保留行内空格）
            lead = orig[:len(orig) - len(orig.lstrip(" \u3000"))]
            t = lead + t.lstrip(" \u3000")
            # pad 到 SRC 宽（text 行允许超宽时跳过）
            srcw = AT.disp_width(src_lines[i]) if i < len(src_lines) else AT.disp_width(orig)
            if not AT.allow_overwidth(src_lines[i] if i < len(src_lines) else orig):
                t = AT.pad_to_width(t, srcw)
            if t != orig:
                changed += 1
            out_lines.append(t)
    # 原子写
    target = os.path.join(rf.refine_dir, f"{base}_精译.txt")
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out_lines))
    os.replace(tmp, target)
    return changed


def _atomic_write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


    

def _log(msg):
    """本地日志: 有任务时写心跳 detail, 始终 print。"""
    print(msg, flush=True)
    try:
        _hb(detail=msg)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description="精译独立流程 runner")
    ap.add_argument("--work", required=True, help="作品 id (registry)")
    ap.add_argument("--chapters", default="all", help="1-3|all|eids(逗号)")
    ap.add_argument("--force", action="store_true", help="忽略 ckpt 强制重译")
    ap.add_argument("--dry", action="store_true", help="只配对分块不调用模型")
    args = ap.parse_args()
    rf = RefineCfg(args.work)
    # 2026-09-07: 精译术语注入走作品表——设 AT 模块级 WORK_ID(否则 glossary_prompt_hint
    # 读到 WORK_ID 空 → 回退全局 glossary, 违反"翻译/校对只用作品术语表"用户规则)
    try:
        AT.WORK_ID = args.work
        AT.AA_DIR = rf.cfg["aa_dir"]
    except Exception:
        pass
    reporter = True if _report_enabled() else None
    chapters = _chapter_order(rf.cfg)
    if args.chapters == "all":
        sel = chapters
    elif "-" in args.chapters:
        a, b = args.chapters.split("-")
        sel = chapters[int(a) - 1:int(b)]
    elif "," in args.chapters:
        # 逗号 eids（entry_xxx 的数字部分）
        sel = [c for c in chapters if os.path.basename(c).replace("_汉化.txt", "") in args.chapters.split(",")]
    else:
        # 单个数字 = 章节序号(1-based)
        sel = chapters[int(args.chapters) - 1:int(args.chapters)]
    total_changed = 0
    for i, fname in enumerate(sel):
        # fname 是全路径(gen_aa_html.chapter_order 返回 join); 取 basename 去 _汉化
        base = os.path.basename(fname).replace("_汉化.txt", "")
        # 章节边界停止检测（译完本章后停）
        if check_stop() == "chapter":
            if reporter:
                _log("[refine] 章节边界停止, 已保存 ckpt")
            break
        if reporter:
            _hb(phase="refine", chapter_idx=i + 1, chapter_total=len(sel))
        try:
            if args.dry:
                # dry: 只配对+分块, 不调模型
                pairs, meta_segs = _pair_dialogue(rf, base)
                chunks = _chunk(pairs, rf)
                mixed_n = sum(1 for ln in meta_segs or {} if any(s["kind"] == "art" for s in meta_segs[ln]))
                print(f"  {base}: {len(pairs)} 段, {len(chunks)} 块, mixed行 {mixed_n}")
                continue
            st = _refine_chapter(rf, base, reporter, force=args.force)
            total_changed += st["changed"]
        except TaskStoppedError:
            if reporter:
                _log("[refine] 用户停止, 已保存块 ckpt 可续跑")
            return 41
        except Exception as e:
            if reporter:
                _log(f"[refine] {base} 失败: {e}")
            print(f"FAIL {base}: {e}", file=sys.stderr)
    if reporter:
        _finalize(0)
    print(f"refine done: {len(sel)} 章, {total_changed} 行改动")


if __name__ == "__main__":
    main()
