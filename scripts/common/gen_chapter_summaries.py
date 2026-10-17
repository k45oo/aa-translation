# -*- coding: utf-8 -*-
"""章节简介生成器: 为作品知识库生成 <work>_knowledge.json 的「章节简介」块 (2026-08-27)

- 输入: 原文台词提取(src 逻辑: 文本行过滤, 非全 AA 原文——大肉章省钱且聚焦剧情)
- 输出: work/<work>_knowledge.json 的 章节简介 = {base: "1-3行中文简介"}
- 关联度(work_meta.json): serial 可引用前章简介(剧情连贯); anthology 逐章独立 + 提取本章出场角色译名
- 增量模式: 默认 skip 已有键, 只补缺失(--force 重生成全部)
- R18 作品: 按 work_meta.r18_level 选后端链(call_with_fallback, work_r18=True 走 r18 链)
- 人工环节: 生成后抽查(可选); 简介直接入库(低风险, 不强制确认)

用法:
  python scripts/common/gen_chapter_summaries.py --work <id> [--force] [--chapters entry_1,entry_2]
"""
import os, sys, json

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(BASE, "scripts"))
sys.path.insert(0, os.path.join(BASE, "scripts", "works"))
sys.path.insert(0, os.path.join(BASE, "scripts", "tools"))
sys.path.insert(0, os.path.join(BASE, "scripts", "common"))

from aa_common import txt_read, txt_existing   # 压缩感知
import registry
import work_memory as WM

# 任务管理器协议(心跳/停止): 未经管理器直跑时全部 no-op
try:
    from task_report import heartbeat as _hb, peek_stop as _peek_stop
except ImportError:  # pragma: no cover
    def _hb(*a, **k):
        return None

    def _peek_stop(only=None):
        return None

# 2026-09-01: 简介生成失败哨兵——重试仍失败(输出含日文假名)写此值, 下次增量 skip 不再重试烧 token
SUMMARY_FAILED = "__summary_failed__"

SUMMARY_SYSTEM = (
    "你是AA漫画(2ch字符画)剧情分析师。根据【本章台词】(日文原文), 用简体中文写1-3行本章剧情简介。\n"
    "要求:\n"
    "1. 概括本章发生的关键事件/场景/人物互动, 不要罗列台词。\n"
    "2. 【必须】列出本章出场的主要角色, 角色名用简体中文译名(如 やる夫→亚鲁欧、キャル→凯露), 不知道译名才保留日文。\n"
    "3. 【必须】整段输出必须是简体中文, 禁止输出任何日文/假名/片假名。\n"
    "4. 只输出简介正文, 不要输出分析过程/前言后语/行号。\n"
    "5. 字数控制在50-120字。"
)

SERIAL_SUFFIX = (
    "\n\n【前情提要】以下是上一章的简介(剧情连贯用, 本章简介需衔接但不要复述):\n{prev}"
)


# chr(10)=换行: 显式构造, 避免转义层把字面反斜杠写进源码
_GLOSS_SUFFIX = chr(10) + "【本章标准译名对照(简介中必须使用这些中文译名)】"


def _load_work_meta():
    try:
        p = os.path.join(BASE, "aa_web_app", "data", "work_meta.json")
        return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    except Exception:
        return {}


def _r18_level(work, meta):
    return (meta.get(work) or {}).get("r18_level", "")


def _chapter_dialogue(work, base):
    """提取本章原文台词(读 extract_dialogue 生成的 v3 meta 口径 _台词.txt), 返回 (list[(行号, 行)], 标题)。

    2026-08-30 收编：不再自跑占比启发式（第2/3套口径），统一读 extract_dialogue 的
    aa/<base>_台词.txt（v3 meta segments 段级剥离, mixed 行右侧台词也能抓到）。
    若无 _台词.txt → 用 ensure_dialogue_file 生成（meta 缺失自动生成, raw 缺失中断）。
    """
    import extract_dialogue as ED
    cfg = registry.get(work)
    aa = cfg["aa_dir"]
    src_p = txt_existing(os.path.join(aa, base + ".txt"))
    if not os.path.exists(src_p):
        return None, None
    lines = txt_read(src_p)
    title = lines[0].lstrip("# ").strip() if lines and lines[0].startswith("#") else base
    # 确保 v3 meta 口径 _台词.txt 存在（缺失生成）
    dlg_p = os.path.join(aa, base + "_台词.txt")
    try:
        ED.ensure_dialogue_file(cfg, base, src_only=True)
    except Exception as e:
        print(f"  [warn] {base}: 台词提取失败 {str(e)[:60]}, 降级为纯文本行")
        dlg_p = None
    if dlg_p and os.path.exists(dlg_p):
        entries = ED.parse_dialogue_file(dlg_p)
        out = []
        for e in entries:
            st = e["text"].strip()
            if not st:
                continue
            # 只保留有实际内容的台词段（单字/纯标点片段对简介意义小, 但保留回/ん-等拟声不误伤）
            tag = "主" if e["op"] == "主" else ("用" if e["op"] == "用" else "?")
            out.append(f"L{e['line_no']} [{tag}] {st}")
    else:
        # 降级: 纯文本行(无 AA 剥离, 保留旧行为)
        out = []
        for i, s in enumerate(lines):
            if not s.strip() or s.startswith('#') or s.startswith('='):
                continue
            out.append(f"L{i+1} {s.strip()}")
    # 截断(超长台词截前 4000 字, 足够生成简介)
    if len("".join(out)) > 4000:
        out = out[:int(4000 / 30)]
    return out, title


def _work_glossary_names(work, corpus, limit=120):
    """作品术语表(glossary_<work>.json) → 简介 prompt 用的译名对照串 (2026-09-12)。

    口径与翻译链一致: 只读作品表(不读全局), manual 对全量, auto 对仅本章语料命中才附;
    跳过 re: 正则规则与 zh==jp(未人工确认译名, 注入等于教模型保留日文)。
    返回 "" = 无表/无命中。简介是低风险产物, 无表不中断(与翻译链硬前置不同)。
    corpus = 本章台词原文(过滤语料)。
    """
    try:
        import gen_work_glossary as GWG
        cfg = registry.get(work) or {}
        wdir = cfg.get("work_dir", "")
        if not wdir:
            return ""
        path = GWG.work_glossary_path(wdir, work)
        if not os.path.exists(path):
            return ""
        manual, auto, _known, _schema = GWG.read_work_glossary(path)
        out, seen = [], set()
        for p in list(manual) + list(auto):
            if not isinstance(p, dict):
                continue
            jp = (p.get("jp") or "").strip()
            zh = (p.get("zh") or "").strip()
            if not jp or not zh or jp.startswith("re:") or zh == jp:
                continue
            if jp in seen:
                continue
            if p.get("来源") == "auto" and jp not in corpus:
                continue
            seen.add(jp)
            out.append(f"{jp}={zh}")
            if len(out) >= limit:
                break
        return "、".join(out)
    except Exception:
        return ""


def _save_progress(work, mem):
    """增量落盘知识库(原子写) + 容错。每章调用一次(2026-09-12)。

    原实现只在 1030 章循环结束后 save_knowledge 一次——单章 1 分钟左右、整批 20 小时，
    中途崩溃/被 kill 已生成简介全部丢失(本作品已因此废掉 4 次任务)。
    """
    try:
        WM.save_knowledge(work, mem)
    except Exception as e:
        print(f"  [warn] 知识库增量落盘失败: {str(e)[:60]}")


def gen_summaries(work, force=False, chapters=None, max_chars=4000):
    """为作品生成/补全章节简介。返回 (新增数, 跳过数, 失败数)。"""
    cfg = registry.get(work)
    aa = cfg["aa_dir"]
    mem = WM.load_knowledge(work)
    sums = mem.setdefault("章节简介", {})
    assoc = WM.work_association(work)
    meta = _load_work_meta()
    r18lvl = _r18_level(work, meta)
    work_r18 = (r18lvl == "high")  # high 整部 r18 链; low 按章(简介生成低风险, 走 normal)
    # 章节清单
    if chapters:
        bases = [c if c.startswith("entry_") else "entry_" + c for c in chapters.split(",")]
    else:
        bases = sorted(f.replace(".txt", "") for f in os.listdir(aa)
                       if f.endswith(".txt") and "_汉化" not in f and "_台词" not in f)
    new_n = skip_n = fail_n = 0
    prev_sum = None  # serial: 上一章简介(前情提要)
    n_total = len(bases)
    for i, base in enumerate(bases):
        # 2026-09-12: 增量落盘(上一章结果) + 心跳 + 停止检测。
        #   心跳让任务页进度真实前进(translate_runner 原写死 _hb("简介", 0, ...) 恒 0/总数)。
        if i:
            _save_progress(work, mem)
        _hb("简介", i + 1, n_total, force=True)   # force: 绕过 1s 节流, 保证进度逐章可见
        _stop = _peek_stop()
        if _stop:
            # 只 peek 不消费: 标志留给 translate_runner 的章节/块边界消费, 否则外层收不到停止请求
            print(f"[gen_summaries] 收到停止标志({_stop}), 本章边界退出"
                  f"——已生成部分已落盘, 下次增量续跑")
            break
        if not force and base in sums:
            # 2026-09-01: 失败哨兵(重试仍失败)不再重试——避免每次重跑任务都烧 token 重生成
            if sums[base] == SUMMARY_FAILED:
                fail_n += 1
                continue
            prev_sum = sums[base]
            skip_n += 1
            continue
        dlg, title = _chapter_dialogue(work, base)
        if not dlg:
            print(f"  {base}: 无原文, 跳过")
            fail_n += 1
            continue
        body = f"【本章标题】{title}\n【本章台词】\n" + "\n".join(dlg)
        system = SUMMARY_SYSTEM
        if assoc == "serial" and prev_sum:
            system += SERIAL_SUFFIX.format(prev=prev_sum)
        # 2026-08-27 审核修: anthology 简介角色译名需对照(否则模型自拟译名与标准不一致)
        #   把知识库 人物 + 全局角色表 中本章原文出现过的译名对附进 prompt
        if assoc == "anthology":
            try:
                import json as _json
                # 2026-09-01 三级重构: 角色块迁出 glossary.json → roles.json
                _gl = _json.load(open(os.path.join(BASE, "roles.json"), encoding="utf-8"))
                _pairs = {}
                for _jp, _zh in (mem.get("人物", {}) or {}).items():
                    _pairs[_jp] = _zh
                for _r in _gl.get("角色", []):
                    _jp = (_r or {}).get("日文", "")
                    if _jp and _jp not in _pairs:
                        _pairs[_jp] = (_r.get("中文", "") or _jp)
                _src = "".join(dlg)
                _hit = [f"{jp}={zh}" for jp, zh in _pairs.items() if jp and jp in _src][:20]
                if _hit:
                    system += "\n【本章角色译名对照(简介中请使用这些中文译名)】" + " ".join(_hit)
            except Exception:
                pass
        # 2026-09-12: 作品术语表译名对照注入(与翻译链同口径)——简介用错译名会随
        #   【本章剧情简介】注入翻译 prompt 传染全章, 故先在简介阶段把译名锁住。
        _gname = _work_glossary_names(work, "".join(dlg))
        if _gname:
            system += _GLOSS_SUFFIX + _gname
        try:
            import aa_backend as AB
            txt, _fb = AB.call_with_fallback(work_r18, system, body, max_tokens=600, timeout=120)
        except Exception as e:
            print(f"  {base}: 生成失败 {str(e)[:60]}")
            sums[base] = SUMMARY_FAILED   # 2026-09-01: 失败哨兵
            fail_n += 1
            continue
        # 2026-08-27 审核修: 保留 1-3 行全部(原只取第一行, 砍掉剧情细节)
        s = (txt or "").strip()
        if not s or len(s) < 10:
            print(f"  {base}: 空/过短输出, 跳过")
            sums[base] = SUMMARY_FAILED
            fail_n += 1
            continue
        # 2026-08-27: 中文校验——简介必须简体中文(角色名除外), 含日文假名则重试1次, 仍日文丢弃
        import re as _re
        _kana = _re.compile(r"[ぁ-んァ-ヶ]")
        if _kana.search(s):
            print(f"  {base}: 输出含日文假名, 重试(强化中文)")
            try:
                txt2, _fb2 = AB.call_with_fallback(work_r18, system + "\n【再次强调】必须输出简体中文! 禁止日文假名! 只允许角色名保留日文。", body, max_tokens=600, timeout=120)
                s2 = (txt2 or "").strip()
                if not _kana.search(s2) and len(s2) >= 10:
                    s = s2
                else:
                    print(f"  {base}: 重试仍含日文, 丢弃(不注入)")
                    sums[base] = SUMMARY_FAILED   # 2026-09-01: 失败哨兵, 下次 skip 不重试
                    fail_n += 1
                    continue
            except Exception:
                print(f"  {base}: 重试失败, 丢弃")
                sums[base] = SUMMARY_FAILED
                fail_n += 1
                continue
        sums[base] = s[:300]  # 保留多行但总长限300
        prev_sum = s
        new_n += 1
        print(f"  {base}: {s[:60]}...")
    WM.save_knowledge(work, mem)
    print(f"[gen_summaries] {work}({assoc}, r18={r18lvl}): 新增{new_n} 跳过{skip_n} 失败{fail_n}")
    return new_n, skip_n, fail_n


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--force", action="store_true", help="重生成全部(默认增量 skip 已有键)")
    ap.add_argument("--chapters", default="", help="只处理指定章(逗号分隔 base 或 eid)")
    a = ap.parse_args()
    gen_summaries(a.work, force=a.force, chapters=a.chapters or None)
