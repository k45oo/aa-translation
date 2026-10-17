# -*- coding: utf-8 -*-
"""refine_aa 校对「等价性 / 提速」验收工具（2026-09-15 落地，配 doc/方案归档/校对提速方案_2026-09-15.md）。

用途：改动 refine_aa（重构、换链、并发化）后，用**校对前备份**当同源输入，
     ① 证明新旧实现产出逐条相同（等价性，用确定性 mock 排除模型抖动）；
     ② 量出真 API 下的墙钟提速比（workers=1/8/16）。

用法：
  # 等价性（旧版 git rev vs 当前工作树；默认 rev=HEAD）
  python scripts/tools/ab_refine_verify.py --work "やる夫のハーレム大王国" --eids 24862,24829
  python scripts/tools/ab_refine_verify.py --work <id> --eids <eid> --rev 81fb50e0 --workers 8

  # 真 API 计时（三档并发对比）
  python scripts/tools/ab_refine_verify.py --work <id> --eids <eid> --mode timing --workers-list 1,8,16

输入从哪来：refine_aa 每次 `--write` 前会把整章汉化原文备份到
  `<work>/work/backup_refine_aa_<时间戳>/<base>_汉化.txt` —— 这就是"校对前状态"。
  `--eids` 给定 eid 时工具自动找**最新**含该章的备份（也可 `--bak <目录名>` 指定）。
  没有任何备份时：先 `python scripts/fix/refine_aa.py <work> --eids <eid> --write` 跑一次
  （会顺带备份），或从 `汉化/` 里换一份想验证的旧译文。

注意：本工具**永不写盘**（do_write=False），只读备份 + 内存比对。
"""
import os
import re
import sys
import json
import time
import glob
import hashlib
import argparse
import importlib.util
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))          # scripts/
_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_SCRIPTS, "common"))
sys.path.insert(0, os.path.join(_SCRIPTS, "tools"))
sys.path.insert(0, os.path.join(_SCRIPTS, "fix"))
sys.path.insert(0, os.path.join(_SCRIPTS, "works"))   # registry.py（勿依赖他模块的 import 副作用）

REPO = os.path.dirname(_SCRIPTS)


def _load_mod(name, path):
    """按路径加载模块（同名可并存：用于同时持有旧版/新版 refine_aa）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def _load_rev_module(rev):
    """取 git <rev> 版 refine_aa.py 到临时文件并加载（旧版对照）。"""
    import tempfile
    src = subprocess.run(["git", "show", f"{rev}:scripts/fix/refine_aa.py"],
                         capture_output=True, cwd=REPO, check=True).stdout.decode("utf-8")
    p = os.path.join(tempfile.gettempdir(), f"refine_aa_old_{re.sub(r'[^0-9a-zA-Z]', '_', rev)}.py")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    # 旧版自带 sys.path.insert(相对 __file__)，落在临时目录会指错 —— 本模块已把 scripts/ 与
    # scripts/common 插到 sys.path 最前，故它那两行 insert 是无效追加，import 仍解析到本仓。
    return _load_mod(f"refine_aa_old_{re.sub(r'[^0-9a-zA-Z]', '_', rev)}", p)


def _find_input(work_dir, eid, bak=None):
    """找该章的校对前输入（backup_refine_aa_* 里最新的那份）。返回 (镜像目录, base)。"""
    base = f"entry_{eid}"
    if bak:
        d = bak if os.path.isabs(bak) else os.path.join(work_dir, bak)
        if not (os.path.exists(os.path.join(d, base + "_汉化.txt"))
                or os.path.exists(os.path.join(d, base + "_汉化.txt.gz"))):
            raise SystemExit(f"✗ {d} 里没有 {base}_汉化.txt")
        return d, base
    cands = sorted(d for d in glob.glob(os.path.join(work_dir, "backup_refine_aa_*"))
                   if (os.path.exists(os.path.join(d, base + "_汉化.txt"))
                       or os.path.exists(os.path.join(d, base + "_汉化.txt.gz"))))
    if not cands:
        raise SystemExit(f"✗ {base}: 找不到 backup_refine_aa_* 里的校对前输入\n"
                         f"  先跑一次: python scripts/fix/refine_aa.py \"{os.path.basename(os.path.dirname(work_dir))}\" "
                         f"--eids {eid} --write  （会顺带备份）")
    return cands[-1], base


def _mk_mock(mod_normal_systems):
    """确定性 mock 模型：同 (system 前缀, body) → 同译文，两侧共用，排除模型抖动。"""
    def mock(system, body, **kw):
        mock.calls.append(body)
        h = hashlib.sha1((system[:20] + "|" + body).encode("utf-8")).hexdigest()[:3]
        if system in mod_normal_systems:
            return f"这是第{h}号测试译文"
        if "精简" in body:
            return f"短译{h}"
        return f"测试译文{h}"
    mock.calls = []
    return mock


def _run(mod, cfg, base, workers, mock):
    mod._call_glm = mock
    old_w = os.environ.get("AA_REFINE_WORKERS")
    os.environ["AA_REFINE_WORKERS"] = str(workers)
    t0 = time.time()
    try:
        _b, stats, edits = mod.refine_chapter(cfg, base, do_write=False)
    finally:
        if old_w is None:
            os.environ.pop("AA_REFINE_WORKERS", None)
        else:
            os.environ["AA_REFINE_WORKERS"] = old_w
    return {"dt": time.time() - t0, "stats": stats, "edits": edits}


def _first_diff(a, b):
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else None
        y = b[i] if i < len(b) else None
        if x != y:
            return i, x, y
    return None


def main():
    ap = argparse.ArgumentParser(description="refine_aa 校对改动验收（等价性 / 提速）")
    ap.add_argument("--work", required=True, help="registry 作品 id")
    ap.add_argument("--eids", required=True, help="章节 eid，逗号分隔")
    ap.add_argument("--mode", choices=["equiv", "timing"], default="equiv")
    ap.add_argument("--rev", default="HEAD", help="等价性模式的旧版 git rev（默认 HEAD）")
    ap.add_argument("--workers", type=int, default=8, help="等价性模式的并发度（默认 8）")
    ap.add_argument("--workers-list", default="1,8,16", help="timing 模式的并发档（默认 1,8,16）")
    ap.add_argument("--bak", default=None, help="指定 backup 目录名（默认自动找最新）")
    a = ap.parse_args()

    from registry import get as reg_get
    cfg0 = reg_get(a.work)
    work_dir = cfg0["work_dir"]
    print(f"作品: {a.work}\n  汉化: {cfg0['out_dir']}\n  工作: {work_dir}")

    if a.mode == "equiv":
        new = _load_mod("refine_aa", os.path.join(_SCRIPTS, "fix", "refine_aa.py"))
        old = _load_rev_module(a.rev)
        print(f"旧版 = git {a.rev}（作为基线） ｜ 新版 = 当前工作树\n")
        normals = {new.SYSTEM_NORMAL, old.SYSTEM_NORMAL}
        fails = 0
        for eid in [e.strip() for e in a.eids.split(",") if e.strip()]:
            d, base = _find_input(work_dir, eid, a.bak)
            cfg = dict(cfg0)
            cfg["out_dir"] = d
            mock = _mk_mock(normals)
            r_old = _run(old, cfg, base, 1, mock)
            n1 = len(mock.calls)
            mock = _mk_mock(normals)
            r_new1 = _run(new, cfg, base, 1, mock)
            n2 = len(mock.calls)
            mock = _mk_mock(normals)
            r_neww = _run(new, cfg, base, a.workers, mock)
            n3 = len(mock.calls)
            same_e = r_old["edits"] == r_new1["edits"] == r_neww["edits"]
            same_s = r_old["stats"] == r_new1["stats"] == r_neww["stats"]
            ok = same_e and same_s and n1 == n2 == n3
            print(f"=== {base} ===  输入: {os.path.basename(d)}")
            print(f"  旧版串行   {r_old['dt']:6.2f}s  调用{n1:4d}  {r_old['stats']}")
            print(f"  新版串行   {r_new1['dt']:6.2f}s  调用{n2:4d}  {r_new1['stats']}")
            print(f"  新版并发{a.workers:<3d}{r_neww['dt']:6.2f}s  调用{n3:4d}  {r_neww['stats']}")
            print(f"  → edits逐条相同 {same_e} ｜ stats相同 {same_s} ｜ 调用数一致 {n1 == n2 == n3}"
                  f" ｜ {'✓ 等价' if ok else '✗ 不等价'}")
            if not ok:
                fails += 1
                fd = _first_diff(r_old["edits"], r_neww["edits"])
                if fd:
                    print(f"    首处不同 #{fd[0]}:\n      旧 {fd[1]}\n      新 {fd[2]}")
        print(f"\n结论: {'全部等价 ✓' if not fails else f'{fails} 章不等价 ✗'}")
        return 1 if fails else 0

    # ---- timing: 真 API（不 mock），量三档并发 ----
    new = _load_mod("refine_aa", os.path.join(_SCRIPTS, "fix", "refine_aa.py"))
    wl = [int(x) for x in a.workers_list.split(",") if x.strip()]
    print(f"链路: {new._REFINE_CHAIN} ｜ 单次上限 {new._call_timeout()}s×{new._call_retry()}\n")
    for eid in [e.strip() for e in a.eids.split(",") if e.strip()]:
        d, base = _find_input(work_dir, eid, a.bak)
        cfg = dict(cfg0)
        cfg["out_dir"] = d
        print(f"=== {base} ===  输入: {os.path.basename(d)}")
        base_dt = None
        for w in wl:
            r = _run(new, cfg, base, w, new._call_glm)   # 真 _call_glm（真 API）
            if base_dt is None:
                base_dt = r["dt"]
            n_ok = sum(1 for e in r["edits"] if e[5])
            print(f"  workers={w:<3d} {r['dt']:6.1f}s  相对串行 {base_dt / max(r['dt'], 1e-9):5.2f}×  "
                  f"编辑 {len(r['edits'])}（写回 {n_ok}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
