#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""audit_alignment: 对齐审计 + 定向回修（治「模型块内串行错位」）。

背景（2026-10-16 实测）：主翻译块用「行号|原文」清单送模型，模型偶发**丢行并整体串行**——
把后面某行的译文写到了这一行的行号下（实测 entry_43337：`2768←tr(2770)`、`2770←tr(2907)`、
`2911←tr(2915)` 等链式错位；错位的值仍按「本行源宽」补位，故宽度校验恒过、残留/照抄/art
三个局部校验也全过 → 错位静默落地）。补译只重发「缺失的行号」，串行行没有信号。

本工具用**独立重译 + 互比**定位：
  ① 把本章可译行重新翻译一遍（NEW，走主链，禁用 TM/固定表，零写盘）；
  ② 逐行比较现译文 CUR[i] 与 NEW[i]（本行）、与 NEW[j]（±N 个可译行以内）的相似度；
     若 CUR[i] 明显更像**别行**的新译 → 判串行错位，修法 = 用 NEW[i]；
  ③ 另外抓「现译文 = 原文」（未翻译/被还原）的行，同样用 NEW[i] 补上。
只改判定的行；四重校验（宽 / 残留假名 / 照抄 / art 列位）不过则只进「待人工确认」，不写盘。

默认 dry-run；`--apply` 才写盘（先备份 work/backup_align_<ts>/，改动写 work/edits.jsonl
供阅读器逐行还原，同步 ckpt 防重跑复活旧值）。多章并发用 `-j N`（自身子进程，隔离全局）。

用法:
  python scripts/fix/audit_alignment.py --work ポケの子 --all --apply -j 6
  python scripts/fix/audit_alignment.py --work ポケの子 --base entry_43337
  python scripts/fix/audit_alignment.py --work ポケの子 --eids 43337,43339 --apply
  python scripts/fix/audit_alignment.py --work ポケの子 --all --dry-run --limit 5
"""
import argparse, concurrent.futures, difflib, json, os, re, shutil, subprocess, sys, time

_THIS = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_THIS)
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "works")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("AA_USE_TM", "0")            # 审计用「独立重译」，绝不复用 TM/旧译
os.environ.setdefault("AA_FIXED_TABLE", "0")       # 不预填固定表（NEW 要独立）

import aa_translate as T                            # noqa: E402
import registry                                     # noqa: E402

# ---- 判据（2026-10-16 在 entry_43337 上标定：12/12 真、0 误报；详见 doc/方案归档）----
D_OWN = 0.45        # 串行候选：best 比 own 至少高这么多
D_BS = 0.65         # 串行候选：best 绝对相似度下限
D_SRC = 0.50        # 串行候选：两行源文本身不能太像（否则是正常近义行）
WIN = 60            # 互比窗口（可译行位置数）
KANA = re.compile(r'[\u3040-\u309f\u30a0-\u30ff\uff61-\uff9f]')
CJK = re.compile(r'[\u4e00-\u9fff]')
HDR = re.compile(r'^\d{1,5}\s*(：|名前)')


def norm(s):
    return re.sub(r'[\s\u3000]', '', s or '')


def sim(a, b):
    a, b = norm(a), norm(b)
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def kind_of(meta, i):
    v = meta.get(i) if hasattr(meta, "get") else None
    if isinstance(v, tuple):
        return v[0]
    if isinstance(v, dict):
        return v.get("kind")
    return v


def _hit_lines(work_dir, base):
    """固定表/wiki 直出命中行（这些行是直出，不经模型 → 不要用重译去覆盖）。"""
    p = os.path.join(work_dir, "fixed_hits", base + ".json")
    if not os.path.exists(p):
        return set()
    try:
        d = json.load(open(p, encoding="utf-8"))
        return {int(k) for k in (d.get("hits") or {})}
    except Exception:
        return set()


def verdict(cur, src, new, own, best, best_line, src_sim):
    """判据（纯函数，可单测）：'未翻译' / '串行错位' / None。

    own      = 现译文与「本行新译」的相似度
    best     = 现译文与「最像的别行新译」的相似度（best_line 为那行号）
    src_sim  = 本行源文与 best_line 源文的相似度
    """
    if not new or KANA.search(new) or not CJK.search(new) or len(norm(new)) < 4:
        return None
    if norm(cur) == norm(src) and len(KANA.findall(src)) >= 2:
        return "未翻译"
    if (best_line is not None and (best - own) >= D_OWN and best >= D_BS
            and src_sim < D_SRC):
        return "串行错位"
    return None


def guard_why(src, cand):
    """四重校验（与主链同源）：不过则进「待人工确认」，不写盘。"""
    why = []
    if T.disp_width(cand) < T.disp_width(src):
        why.append("宽度短")
    if T.residual_suspect(cand):
        why.append("残留假名")
    if T.is_copy_failure(src, cand):
        why.append("照抄")
    if not T.art_stroke_preserved(src, cand):
        why.append("art列位")
    return why


def audit_chapter(work, base, cfg, batch=50, quiet=False):
    """审计单章 → (candidates, stats)。candidates: [dict]（含判据细节，未写盘）"""
    T.AA_DIR, T.OUT_DIR, T.WORK_DIR = cfg["aa_dir"], cfg["out_dir"], cfg["work_dir"]
    T.WORK_ID = work
    sp, zp = os.path.join(cfg["aa_dir"], base + ".txt"), os.path.join(cfg["out_dir"], base + "_汉化.txt")
    if not (os.path.exists(sp) and os.path.exists(zp)):
        return [], {"skipped": "缺源文/汉化"}
    L = T.txt_read(sp)
    Z = T.txt_read(zp)
    meta = T.load_meta_0based(os.path.join(cfg["work_dir"], "line_meta_v2"), base) or {}
    hits = _hit_lines(cfg["work_dir"], base)
    rows = [(i, s) for i, s in enumerate(L)
            if T.is_translatable(i, s, meta) and KANA.search(s)]
    if not rows:
        return [], {"skipped": "无可译行"}
    # ① 独立重译（与主链同源的一批调用；块内并发，与生产 process_chapter 同口径）
    NEW = {}
    chunks = [rows[k:k + batch] for k in range(0, len(rows), batch)]
    workers = max(1, min(int(os.environ.get("AA_AUDIT_IN_WORKERS", T.MAX_WORKERS)), len(chunks) or 1))

    def _one(chunk):
        return T.translate_lines(chunk, work_r18=None, low_r18=False,
                                 context=T._chunk_context(L, chunk[0][0]), base=base)

    if workers > 1 and len(chunks) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            for fixed, _missed, _warns in ex.map(_one, chunks):
                NEW.update(fixed)
    else:
        for chunk in chunks:
            fixed, _missed, _warns = _one(chunk)
            NEW.update(fixed)
    # ② 互比
    idxs = [i for i, _ in rows]
    pos = {i: k for k, i in enumerate(idxs)}
    cand, review = [], []
    for i in idxs:
        if kind_of(meta, i) not in (None, "text") or i in hits or HDR.match(L[i]):
            continue
        cur = Z[i] if i < len(Z) else L[i]
        new = NEW.get(i, "")
        if not new or KANA.search(new) or not CJK.search(new) or len(norm(new)) < 4:
            continue
        own, bj, bs = 1.0, None, 0.0
        for j in idxs:
            if j == i or abs(pos[j] - pos[i]) > WIN:
                continue
            nj = NEW.get(j, "")
            if not nj:
                continue
            s = sim(cur, nj)
            if s > bs:
                bj, bs = j, s
        own = sim(cur, new)
        kind = verdict(cur, L[i], new, own, bs, bj,
                       sim(L[i], L[bj]) if bj is not None else 1.0)
        if kind is None:
            continue
        cand_new = T.pad_to_width(new.strip("\n"), T.disp_width(L[i]))
        why = guard_why(L[i], cand_new)
        rec = {"base": base, "line": i, "kind": kind,
               "cur": cur, "new": cand_new, "own": round(own, 2), "best": round(bs, 2),
               "best_line": bj, "why": why}
        (review if why else cand).append(rec)
    st = {"lines": len(rows), "new_ok": len(NEW), "cand": len(cand), "review": len(review)}
    if not quiet:
        print(f"[audit] {base}: 可译 {st['lines']} | 重译成功 {st['new_ok']} | "
              f"可落地 {st['cand']} | 待确认 {st['review']}", flush=True)
    return cand + review, st


def apply_chapter(work, base, cfg, changes):
    """写盘：备份 → 汉化 + ckpt + edits.jsonl。返回落地行数。"""
    ok = [c for c in changes if not c["why"]]
    if not ok:
        return 0
    zp = os.path.join(cfg["out_dir"], base + "_汉化.txt")
    ckp = os.path.join(cfg["work_dir"], base + ".txt.ckpt.json")
    L = T.txt_read(os.path.join(cfg["aa_dir"], base + ".txt"))
    Z = T.txt_read(zp)
    ts = time.strftime("%Y%m%d_%H%M%S")
    bakdir = os.path.join(cfg["work_dir"], "backup_align_" + ts)
    os.makedirs(bakdir, exist_ok=True)
    shutil.copy2(zp, os.path.join(bakdir, base + "_汉化.txt"))
    ck = {}
    if os.path.exists(ckp):
        shutil.copy2(ckp, os.path.join(bakdir, base + ".txt.ckpt.json"))
        try:
            ck = json.load(open(ckp, encoding="utf-8"))
        except Exception:
            ck = {}
    for c in ok:
        i = c["line"]
        if i < len(Z):
            Z[i] = c["new"]
        if str(i) in ck:
            ck[str(i)] = c["new"]
    open(zp, "w", encoding="utf-8").write("\n".join(Z))
    if ck:
        ck["__lines"] = len(L)
        T._atomic_write_ckpt(ckp, ck)
    ts_h = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(os.path.join(cfg["work_dir"], "edits.jsonl"), "a", encoding="utf-8") as f:
        for c in ok:
            f.write(json.dumps({"ts": ts_h, "work": work, "file": base + "_汉化.txt",
                                "line": c["line"] + 1,
                                "type": "错位" if c["kind"] == "串行错位" else "漏译",
                                "line_type": "", "before": c["cur"], "after": c["new"]},
                               ensure_ascii=False) + "\n")
    try:                                            # 同类备份只留最近 2 次（仓库约定）
        T.prune_backup_dirs(cfg["work_dir"], "backup_align_")
    except Exception:
        pass
    print(f"[apply] {base}: 落地 {len(ok)} 行（校验不过 {len(changes) - len(ok)}）备份 {os.path.basename(bakdir)}",
          flush=True)
    return len(ok)


def _bases_of(cfg, work, args):
    aa = cfg["aa_dir"]
    all_b = sorted(os.path.splitext(f)[0] for f in os.listdir(aa)
                   if f.startswith("entry_") and f.endswith(".txt") and "_台词" not in f)
    if args.base:
        return [args.base]
    if args.eids:
        want = {"entry_" + e.strip() for e in args.eids.split(",") if e.strip()}
        return [b for b in all_b if b in want]
    if args.all:
        return all_b[: args.limit] if args.limit else all_b
    return []


def main():
    ap = argparse.ArgumentParser(description="对齐审计 + 定向回修（块内串行错位）")
    ap.add_argument("--work", required=True)
    ap.add_argument("--base")
    ap.add_argument("--eids")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--batch", type=int, default=50, help="重译每块行数")
    ap.add_argument("--apply", action="store_true", help="写盘（默认 dry-run）")
    ap.add_argument("--dry-run", action="store_true", help="显式 dry-run（默认）")
    ap.add_argument("-j", "--workers", type=int, default=1, help="章级并发（自身子进程）")
    ap.add_argument("--report", default="", help="审计明细 JSON 汇总路径（默认 work/audit_align/_summary.json）")
    ap.add_argument("--backend", default="", help="重译用后端（默认走当前链）")
    ap.add_argument("--resume", action="store_true", help="跳过汇总里已审计过的章（长跑可中断续跑）")
    a = ap.parse_args()
    if a.backend:
        os.environ["AA_BACKEND"] = a.backend
    else:
        # 默认识别主线：与 translate_runner 同口径取链首（否则 AA_BACKEND 未设 = 云端直连
        # 旧默认模型 xiaomi/mimo-v2.5，实测慢且质量不同源 → 互比判据会失真）
        try:
            import aa_config as _AC
            _b, _m = _AC.auto_backend(False)
            os.environ.setdefault("AA_BACKEND", _b)
            if _m:
                os.environ.setdefault(_AC.model_env(_b), _m)
            os.environ["AA_BACKEND_AUTO"] = "1"
            print(f"[audit] 后端 = {_b}" + (f"/{_m}" if _m else "") + "（链首，--backend 可覆盖）", flush=True)
        except Exception as _e:
            print(f"[audit] 链首解析失败({_e})，用当前环境", flush=True)
    cfg = registry.get(a.work)
    if not cfg:
        print(f"[error] 未知作品: {a.work}")
        return 2
    bases = _bases_of(cfg, a.work, a)
    if not bases:
        print("[error] 未选定章节（--base/--eids/--all）")
        return 2
    if a.resume:
        try:
            done = json.load(open(a.report or os.path.join(cfg["work_dir"], "audit_align", "_summary.json"),
                                  encoding="utf-8")).get("chapters", {})
            skip = {b for b, st in done.items() if not st.get("error") and st.get("rc", 0) == 0}
            bases = [b for b in bases if b not in skip]
            print(f"[audit] --resume: 跳过已审计 {len(skip)} 章，剩余 {len(bases)} 章", flush=True)
        except Exception:
            pass
        if not bases:
            print("[audit] 全部已审计，无事可做")
            return 0

    # 章级并发：以自身子进程跑单章，避免 aa_translate 全局被多线程污染
    if a.workers > 1 and len(bases) > 1:
        print(f"[audit] {a.work}: {len(bases)} 章，-j {a.workers}")
        env = dict(os.environ)
        cmd_extra = ["--apply"] if a.apply else []
        if a.batch != 50:
            cmd_extra += ["--batch", str(a.batch)]
        if a.backend:
            cmd_extra += ["--backend", a.backend]
        running = []
        stats_all = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as ex:
            futs = {}
            it = iter(bases)
            for _ in range(min(a.workers, len(bases))):
                b = next(it, None)
                if b is None:
                    break
                futs[ex.submit(_run_one, b, a, cmd_extra, env)] = b
            while futs:
                done, _ = concurrent.futures.wait(futs, return_when=concurrent.futures.FIRST_COMPLETED)
                for f in done:
                    b = futs.pop(f)
                    try:
                        st = f.result()
                    except Exception as e:
                        st = {"base": b, "error": str(e)}
                    stats_all.append(st)
                    print(f"  [{st.get('base', b)}] {json.dumps({k: v for k, v in st.items() if k != 'base'}, ensure_ascii=False)}", flush=True)
                    b2 = next(it, None)
                    if b2 is not None:
                        futs[ex.submit(_run_one, b2, a, cmd_extra, env)] = b2
        _write_summary(cfg, a, stats_all)
        return 0

    stats_all, allrec = [], []
    for b in bases:
        changes, st = audit_chapter(a.work, b, cfg, batch=a.batch)
        st["base"] = b
        n_applied = 0
        if a.apply and changes:
            n_applied = apply_chapter(a.work, b, cfg, changes)
        st["applied"] = n_applied
        allrec += changes
        stats_all.append(st)
        for c in changes:
            flag = "落地" if (a.apply and not c["why"]) else ("待确认" if c["why"] else "候选")
            print(f"  {b} {c['line']:5d} [{c['kind']}] {flag} own={c['own']} best={c['best']}"
                  f"@{c['best_line']} {'/'.join(c['why'])}")
            print(f"        OLD: {c['cur'].strip()[:70]}")
            print(f"        NEW: {c['new'].strip()[:70]}")
    _write_summary(cfg, a, stats_all, allrec)
    return 0


def _run_one(base, a, cmd_extra, env):
    cmd = [sys.executable, os.path.abspath(__file__), "--work", a.work, "--base", base] + cmd_extra
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    out = (p.stdout or "").strip().splitlines()
    try:                              # 逐章明细留档（人工复核「改了什么」）
        logdir = os.path.join(registry.get(a.work)["work_dir"], "audit_align")
        os.makedirs(logdir, exist_ok=True)
        with open(os.path.join(logdir, base + ".log"), "w", encoding="utf-8") as f:
            f.write((p.stdout or "") + (("\n[stderr]\n" + p.stderr) if p.returncode else ""))
    except Exception:
        pass
    st = {"base": base, "rc": p.returncode}
    for ln in out:
        if ln.startswith("[audit] "):
            m = re.search(r'可译 (\d+) \| 重译成功 (\d+) \| 可落地 (\d+) \| 待确认 (\d+)', ln)
            if m:
                st.update(lines=int(m.group(1)), new_ok=int(m.group(2)),
                          cand=int(m.group(3)), review=int(m.group(4)))
        if ln.startswith("[apply] "):
            m = re.search(r'落地 (\d+)', ln)
            if m:
                st["applied"] = int(m.group(1))
    if p.returncode != 0:
        st["stderr"] = (p.stderr or "")[-400:]
    return st


def _write_summary(cfg, a, stats_all, recs=None):
    outdir = os.path.join(cfg["work_dir"], "audit_align")
    os.makedirs(outdir, exist_ok=True)
    path = a.report or os.path.join(outdir, "_summary.json")
    try:
        old = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    except Exception:
        old = {}
    old.setdefault("chapters", {})
    for st in stats_all:
        old["chapters"][st.get("base", "?")] = st
    old["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    old["mode"] = "apply" if a.apply else "dry-run"
    json.dump(old, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    tot = sum(st.get("cand", 0) for st in stats_all)
    rev = sum(st.get("review", 0) for st in stats_all)
    app = sum(st.get("applied", 0) for st in stats_all)
    print(f"[audit] 汇总: 章 {len(stats_all)} | 候选 {tot} | 已落地 {app} | 待确认 {rev} | {path}")


if __name__ == "__main__":
    sys.exit(main())
