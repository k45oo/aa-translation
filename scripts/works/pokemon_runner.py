# -*- coding: utf-8 -*-
"""宝可梦 AA 作品专属翻译 runner（方案 2026-09-22；pipeline=pokemon 的唯一合法入口）。

流程（§4.1 步骤序）：
  ① 管线校验：work_meta.pipeline == "pokemon"（否则拒跑，逃生口 AA_ALLOW_POKEMON=1）
  ② 固定表自检：work/pokemon_fixed_<work>.json 缺失/要求重建 → 建表（wiki 派生 + copy 轨）
  ③ 作品术语表自检：无表直接中断（翻译硬约束：只读作品术语表）；wiki 术语未同步则提示
  ④ 逐章**预填**：匹配四轨（copy→规则→结构化→整行）→ 过四重护栏 → 原子写 ckpt done
     并落命中痕迹 work/fixed_hits/<base>.json（零 API，命中行不进块、不送模型）
  ⑤ 交给通用链翻译（子进程 translate_runner，env AA_USE_TM=0 + AA_PIPELINE_OWNER=pokemon）
     —— 翻译/清理/校对全部复用通用实现（方案 D2：不 fork 核心）；合集手动生成
        （`common/gen_aa_html.py --work <id>` 或工作台「重生成合集」）
  ⑥ 汇总：预填命中率、跳过行数、token 归因（usage.jsonl 按 tag）

用法：
    python scripts/works/pokemon_runner.py --work <id> [--chapters new|all|eids|1-20]
        [--force] [--no-proofread] [--workers N]
        [--rebuild-table] [--min-freq N] [--no-prefill] [--dry-prefill]

--chapters 语义与通用 runner 一致（new=本地 aa/ 里未译的章）。
--rebuild-table：重生成 struct 轨（wiki 派生）并重建 copy 轨（全库扫描，约 1-2 分钟）。
--dry-prefill  ：只统计命中率，不落 ckpt、不翻译（验收用）。
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRIPTS = os.path.join(ROOT, "scripts")
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "works"),
           os.path.join(_SCRIPTS, "runner")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import registry               # noqa: E402
from aa_spawn import child_env, py_prefix   # 起子任务统一前缀（冻结 exe 走 --cli）
import work_pipeline as WP     # noqa: E402
import task_report as TR       # noqa: E402
import pokemon_fixed as PF     # noqa: E402
import translate_runner as TRr  # noqa: E402  复用清单解析与通用链（D2）

_RC_STOPPED = getattr(TRr, "RC_STOPPED", 41)


def log(msg):
    print(msg, flush=True)


def _hb(phase, idx=None, total=None):
    TR.heartbeat(phase=phase, idx=idx, total=total, force=True)


def _bases_of(eids):
    """eid 或 base 两种写法都能接（--chapters 传 entry_10437 或 10437 均可）。"""
    out = []
    for e in eids:
        s = str(e).strip()
        if not s:
            continue
        out.append(s if s.startswith("entry_") else "entry_" + s)
    return out


def main():
    args = sys.argv[1:]
    work = None
    chapters = "new"
    if "--work" in args:
        i = args.index("--work")
        work = args[i + 1] if i + 1 < len(args) else None
    if "--chapters" in args:
        i = args.index("--chapters")
        chapters = args[i + 1] if i + 1 < len(args) else "new"
    force = "--force" in args
    rebuild = "--rebuild-table" in args
    no_prefill = "--no-prefill" in args
    dry_prefill = "--dry-prefill" in args
    normalize_only = "--normalize-only" in args
    normalize = normalize_only or ("--no-normalize" not in args)
    min_freq = 2
    if "--min-freq" in args:
        min_freq = int(args[args.index("--min-freq") + 1])
    if not work:
        raise SystemExit("usage: pokemon_runner.py --work <id> [--chapters new|all|eids|1-20] "
                         "[--force] [--no-proofread] [--workers N] [--rebuild-table]")

    # ① 管线校验
    pl = WP.pipeline_of(work)
    if pl != WP.POKEMON and os.environ.get("AA_ALLOW_POKEMON") not in ("1", "true", "True"):
        raise SystemExit(f"[pipeline] 作品「{work}」的 work_meta.pipeline = {pl}（非 pokemon），"
                         f"专属 runner 拒绝执行。\n"
                         f"  该作品请走通用链：python scripts/runner/translate_runner.py --work {work} ...\n"
                         f"  确认要用专属链：先在作品详情把 pipeline 改为 pokemon，或 AA_ALLOW_POKEMON=1 强制。")
    cfg = registry.get(work)
    work_dir, aa_dir, out_dir = cfg["work_dir"], cfg["aa_dir"], cfg["out_dir"]
    log(f"[pokemon] 作品 {work} | pipeline={pl} | 固定表链 独立于 TM（AA_USE_TM=0）")

    # ③ 作品术语表自检（翻译硬约束：无表即中断）
    gpath = os.path.join(work_dir, f"glossary_{work}.json")
    if not os.path.exists(gpath):
        raise SystemExit(f"[pokemon] 缺作品术语表：{gpath}\n"
                         f"  先生成：python scripts/tools/gen_pokemon_glossary.py --work {work} "
                         f"--from-wiki（正向同步本地宝可梦 wiki）")
    try:
        _gl = json.load(open(gpath, encoding="utf-8"))
        _n = len(_gl.get("glossary") or [])
    except Exception as e:
        raise SystemExit(f"[pokemon] 作品术语表读取失败：{e}")
    _nwiki = sum(1 for e in (_gl.get("glossary") or []) if e.get("来源") == "wiki")
    log(f"[pokemon] 作品术语表 {_n} 条（其中 wiki 同步 {_nwiki} 条）")

    # ② 固定表
    tpath = PF.table_path(work, work_dir)
    if rebuild or not os.path.exists(tpath):
        log("[pokemon] 建固定表（wiki 派生 + copy 轨；全库扫描，零 API）…")
        st = PF.build(work, aa_dir, out_dir, work_dir)
        _hb("固定表", 1, 3)
        log(f"[pokemon]   struct 轨 {st.get('kept', 0)} 条 | 闸一拒 {st.get('reject', {})}")
        cop = PF.collect_copy(work, aa_dir, out_dir, work_dir, min_freq=min_freq)
        _hb("固定表", 2, 3)
        log(f"[pokemon]   copy 轨 {cop.get('copy', 0)} 条"
            f"（纯图形 {cop.get('copy_pure_art', 0)} + 已交付未改动 {cop.get('copy_unchanged', 0)}）")
        _hb("固定表", 3, 3)
    else:
        t = PF.load(work, work_dir) or {}
        c = (t.get("meta") or {}).get("counts") or {}
        log(f"[pokemon] 固定表 {os.path.basename(tpath)}：struct {c.get('struct', 0)} / "
            f"whole {c.get('whole', 0)} / copy {c.get('copy', 0)} 条")

    # ④ 解析章节 + 预填
    eids, _entries = TRr.select_eids(cfg, chapters)
    eids = [e for e in (eids or [])]
    if force:
        # --force 时预填不再受限（重译章已有 ckpt 也刷新命中），由通用链负责重译
        pass
    log(f"[pokemon] 目标章 {len(eids)} 个：{', '.join(eids[:8])}{' …' if len(eids) > 8 else ''}")
    if normalize_only:
        if chapters == "new":
            eids, _entries = TRr.select_eids(cfg, "all")
        nst = PF.normalize_chapters(work, _bases_of(eids), aa_dir, out_dir, work_dir)
        if "error" in nst:
            log(f"[pokemon] L2 规范化失败：{nst['error']}")
            return 1
        log(f"[pokemon] L2 规范化（--normalize-only）：{len(eids)} 章 | 改动 {nst['changed']} 行 / "
            f"{nst['chapters']} 章 | 跳过 {nst['skipped']} 章"
            + (f" | 备份 {os.path.relpath(nst['backup'], ROOT)}" if nst.get("backup") else ""))
        for s in nst.get("samples", []):
            log(f"[pokemon]   {s[0]}:{s[1]}  {s[2]}  →  {s[3]}")
        return 0
    if not no_prefill and eids:
        st = PF.prefill(work, _bases_of(eids), aa_dir, out_dir, work_dir, dry=dry_prefill)
        if "error" in st:
            log(f"[pokemon] 预填失败：{st['error']}（继续走通用链，命中行不会跳过）")
        else:
            hit = sum(st.get("hit_" + k, 0) for k in ("struct", "whole", "rule", "copy", "tpl"))
            pct = hit / st["lines"] * 100 if st.get("lines") else 0.0
            if st.get("disabled"):
                log("[pokemon] 固定表已关闭（AA_FIXED_TABLE=0）→ 全部行交模型（A/B 对照档）")
            else:
                log(f"[pokemon] 预填 {st.get('chapters', 0)} 章 | 送模型行 {st.get('lines', 0):,} | "
                    f"命中 {hit:,} = {pct:.1f}%"
                    f"（copy {st.get('hit_copy', 0):,} / rule {st.get('hit_rule', 0):,} / "
                    f"tpl {st.get('hit_tpl', 0):,} / "
                    f"struct {st.get('hit_struct', 0):,} / whole {st.get('hit_whole', 0):,}）"
                    f" | 护栏拒 {st.get('reject', {})}"
                    f"{' | dry（未落盘）' if dry_prefill else ''}")
            _hb("预填", st.get("chapters", 1), len(eids))
    if dry_prefill:
        log("[pokemon] --dry-prefill：到此为止（未翻译）")
        return 0

    # ⑤ 通用链（子进程；env 交接标记 + 关 TM）
    cmd = py_prefix() + [os.path.join(_SCRIPTS, "runner", "translate_runner.py"),
           "--work", work, "--chapters", ",".join(eids) if eids else "new"]
    if force:
        cmd.append("--force")
    if "--no-proofread" in args:
        cmd.append("--no-proofread")
    for k in ("--workers",):
        if k in args:
            cmd += [k, args[args.index(k) + 1]]
    env = child_env()       # 子任务环境（冻结 exe 下带上 AA_BASE/AA_ENV）
    env["AA_USE_TM"] = "0"                 # 方案 D1：宝可梦作品独立于 TM
    env["AA_PIPELINE_OWNER"] = WP.POKEMON  # 方案 D10：内部交接豁免
    env.setdefault("AA_USAGE_TASK", f"pokemon:{work}")
    log(f"[pokemon] 交通用链：{' '.join(cmd[1:])}")
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", bufsize=1)
    stop_seen = False
    for line in proc.stdout:
        log("  " + line.rstrip())
        if TR.peek_stop and TR.peek_stop():
            if not stop_seen:
                stop_seen = True
                log("[pokemon] 收到停止标志（等通用链在章节边界收尾）")
    rc = proc.wait()
    log(f"[pokemon] 通用链退出 rc={rc}"
        + ("（已停止，进度保留可续跑）" if rc == _RC_STOPPED else ""))

    # ⑥ L2 后置规范化（零 API）：把已译行的骨架字段拉回同一口径
    if normalize and rc == 0 and eids:
        nst = PF.normalize_chapters(work, _bases_of(eids), aa_dir, out_dir, work_dir)
        if "error" in nst:
            log(f"[pokemon] L2 规范化失败：{nst['error']}（不影响译文）")
        else:
            log(f"[pokemon] L2 规范化：改动 {nst['changed']} 行 / {nst['chapters']} 章"
                + (f" | 跳过 {nst['skipped']} 章" if nst["skipped"] else "")
                + (f" | 备份 {os.path.relpath(nst['backup'], ROOT)}" if nst.get("backup") else ""))
            for s in nst.get("samples", []):
                log(f"[pokemon]   {s[0]}:{s[1]}  {s[2]}  →  {s[3]}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
