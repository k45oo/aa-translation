# -*- coding: utf-8 -*-
"""TM（翻译记忆）—— 同作品内**整行复用已定稿译文**，零模型调用（2026-09-15 P3）。

为什么值得（实测，3 部 × 留出 3 章，见 doc/方案归档/token成本优化方案_2026-09-15.md §9）:
  · key 用 `line.strip()` 时命中仅 **4.4%**（行内填充差异导致 miss）；
  · key 改为**去空白归一化**后命中 **21.1%** 的送模型行，三重护栏通过率 **99.2%**（605/610）。
  → 这些行零调用直出（省掉它们的输入尾巴 + 输出）。

口径（决策 D7）:
  · **只同作品**（不跨作品，防角色名/术语互相污染）；
  · 只回填「已定稿」章（`work/refine_ckpt/<base>.json` 存在 = 已校对过）；
  · 复用前四重护栏：① 宽度放得下 ② 无残留假名 ③ 术语表一致 ④ **art 列位一致** —— 任一不过就交回模型；
  · 宽度不同时本地 `pad_to_width` 补齐（零 token）；译文比源宽则丢弃。

存储: `<work_dir>/tm_<work>.json` = {"__built": {base: 指纹}, "<key>": {"zh": 行, "w": 源宽, "base": 来源章}}
开关: `AA_USE_TM`（默认开；=0 关闭）。
"""
import hashlib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# scripts/works 也要在路径里：build() 走 registry 解析作品路径，缺它 → `import registry` 失败
# → 静默返回 {"error": "no_registry"}、既不打印也不写盘（2026-10-11 实测 CLI 空跑）。
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "works"))

_SP = re.compile(r"[\u3000 ]+")
# 「已定稿章」名单文件（旧流程 backup_refine_aa_* 目录于 2026-10-01 清理后留下的信号）
LEGACY_PROOFREAD_FILE = "tm_proofread_legacy.txt"


def enabled():
    """是否启用 TM 复用（2026-09-15 起**默认开**；`AA_USE_TM=0` 关闭）。见 enabled_default_on。"""
    return enabled_default_on()


def key(line):
    """归一化 key：去掉所有 U+3000 与半角空格（行内填充差异不该导致 miss）。"""
    return _SP.sub("", line or "")


def tm_path(work, work_dir=None):
    if not work_dir:
        try:
            import registry
            work_dir = registry.get(work)["work_dir"]
        except Exception:
            return None
    return os.path.join(work_dir, f"tm_{work}.json")


def load(work, work_dir=None):
    """读 TM（不存在返回 {}）。"""
    p = tm_path(work, work_dir)
    if not p or not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save(work, tm, work_dir=None):
    """原子写 TM。"""
    p = tm_path(work, work_dir)
    if not p:
        return False
    tmp = p + ".tmp"
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(tm, f, ensure_ascii=False)
        os.replace(tmp, p)
        return True
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def enabled_default_on():
    """TM 是否默认启用（2026-09-15 起默认开；`AA_USE_TM=0` 关闭）。"""
    return os.environ.get("AA_USE_TM", "1") not in ("0", "false", "False", "")


def _art_cols(line):
    """行内 art 图形字符的 (字符, 显示列) 序列 —— 复用项目的 `_art_anchors`（同一套 art 定义）。

    空白(U+3000/半角空格)与台词字符(假名/汉字/日文标点)都不算 art，因此填充差异不影响本校验，
    而**内部填充变化导致的列位漂移**会被抓出来（宽对框歪）。
    """
    try:
        import aa_translate as A
        return A._art_anchors(line)
    except Exception:
        return None


def guard(src_line, zh_line, glossary_pairs=()):
    """四重护栏: ① 宽度放得下 ② 无残留假名 ③ 术语表一致 ④ **art 列位一致**。返回 (ok, reason)。

    ④ 是 2026-09-15 A/B 后补的: 归一化 key 会让「内容相同但填充不同」的行互相命中，
    若只校字符序列，art 的相对列位可能漂移（宽对框歪）→ 用显示列逐位比对，漂移即丢弃交回模型。
    """
    try:
        import aa_translate as A
    except Exception:
        return False, "no_aa"
    core = (zh_line or "").rstrip("\u3000 ")
    if not core.strip():
        return False, "empty"
    if A.disp_width(core) > A.disp_width(src_line):
        return False, "width"
    if A.kana_runs(core):
        return False, "residual"
    for jp, zh in glossary_pairs or ():
        if not jp or not zh or jp.startswith("re:"):
            continue
        if jp in src_line and zh not in core and jp not in core:
            return False, "glossary"
    _a_src, _a_zh = _art_cols(src_line), _art_cols(core)
    if _a_src is not None and _a_zh is not None and _a_src != _a_zh:
        return False, "art"
    return True, ""


def reuse(tm, src_line, glossary_pairs=()):
    """命中则返回可直接落盘的整行（本地 pad 到源宽）；未命中/护栏不过返回 None。"""
    if not tm:
        return None
    rec = tm.get(key(src_line))
    if not isinstance(rec, dict):
        return None
    zh = rec.get("zh") or ""
    ok, _why = guard(src_line, zh, glossary_pairs)
    if not ok:
        return None
    try:
        import aa_translate as A
        target = A.disp_width(src_line)
        if rec.get("w") == target:
            return zh                       # 源宽相同 → 原样复用（零风险）
        core = zh.rstrip("\u3000 ")         # 源宽不同（行内填充差异）→ 本地重 pad
        if A.disp_width(core) > target:
            return None
        return A.pad_to_width(core, target)
    except Exception:
        return None


def _fingerprint(path):
    try:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except OSError:
        return None


def build(work, aa_dir=None, out_dir=None, work_dir=None, only_proofread=True,
          force=False, limit=None, quiet=False):
    """回填/增量构建 TM。返回 stats dict（零 API）。

    only_proofread: 只收「已定稿」章：`work/refine_ckpt/<base>.json` 存在，或出现在
        `work/backup_refine_aa_*`（旧流程备份），或列在 `work/tm_proofread_legacy.txt`。
    force: 忽略指纹，整库重建（会覆盖既有条目）。
    """
    import glob
    if not (aa_dir and out_dir and work_dir):
        try:
            import registry
            cfg = registry.get(work)
            aa_dir, out_dir, work_dir = cfg["aa_dir"], cfg["out_dir"], cfg["work_dir"]
        except Exception:
            return {"error": "no_registry"}
    try:
        import aa_translate as A
    except Exception as e:
        return {"error": f"import aa_translate: {e}"}

    tm = {} if force else load(work, work_dir)
    built = tm.setdefault("__built", {})
    meta_dir = os.path.join(work_dir, "line_meta_v2")
    ckpt_dir = os.path.join(work_dir, "refine_ckpt")
    # 「已定稿」判据: 有章级校对 ckpt（新流程）**或**出现在任一校对备份目录（旧流程，
    # 2026-09-15 前 refine 没有 ckpt，但写回前都留了 backup_refine_aa_<ts>/）**或**出现在
    # work/tm_proofread_legacy.txt（2026-10-01 清理旧备份目录前把那份名单落盘，容量回收但语义不变）。
    proofread = set()
    try:
        for f in glob.glob(os.path.join(ckpt_dir, "*.json")):
            proofread.add(os.path.basename(f)[:-5])
    except Exception:
        pass
    try:
        for d in glob.glob(os.path.join(work_dir, "backup_refine_aa_*")):
            try:
                for f in os.listdir(d):
                    if f.endswith("_汉化.txt"):
                        proofread.add(f[:-len("_汉化.txt")])
            except OSError:
                continue
    except Exception:
        pass
    try:
        lp = os.path.join(work_dir, LEGACY_PROOFREAD_FILE)
        if os.path.exists(lp):
            with open(lp, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if ln and not ln.startswith("#"):
                        proofread.add(ln)
    except Exception:
        pass
    st = {"chapters": 0, "skipped": 0, "lines": 0, "kept": 0, "reject": {}}
    files = 0
    for sp in sorted(glob.glob(os.path.join(aa_dir, "entry_*.txt"))):
        base = os.path.basename(sp)[:-4]
        if base.endswith("_台词"):
            continue
        tp = os.path.join(out_dir, base + "_汉化.txt")
        if not os.path.exists(tp):
            continue
        if only_proofread and base not in proofread:
            st["skipped"] += 1
            continue
        fp = f"{_fingerprint(sp)}:{_fingerprint(tp)}"
        if built.get(base) == fp and not force:
            st["skipped"] += 1
            continue
        try:
            src = open(sp, encoding="utf-8").read().split("\n")
            tr = open(tp, encoding="utf-8").read().split("\n")
        except OSError:
            continue
        meta = A.load_meta_0based(meta_dir, base) or {}
        try:
            gp = [(jp, zh) for jp, zh in A._load_glossary_pairs(base) if not jp.startswith("re:")]
        except Exception:
            gp = []
        n_kept = 0
        for i, line in enumerate(src):
            if i >= len(tr) or not line.strip():
                continue
            try:
                if not A.is_translatable(i, line, meta):
                    continue
            except Exception:
                continue
            st["lines"] += 1
            ok, why = guard(line, tr[i], gp)
            if not ok:
                st["reject"][why] = st["reject"].get(why, 0) + 1
                continue
            k = key(line)
            if len(k) < 2 or (k in tm and not force):
                continue
            tm[k] = {"zh": tr[i], "w": A.disp_width(line), "base": base}
            n_kept += 1
        built[base] = fp
        st["chapters"] += 1
        st["kept"] += n_kept
        files += 1
        if limit and files >= limit:
            break
    save(work, tm, work_dir)
    st["entries"] = len([k for k in tm if not k.startswith("__")])
    if not quiet:
        print(f"[tm] {work}: 章 {st['chapters']} 更新 / 跳过 {st['skipped']} | 行 {st['lines']:,} "
              f"收 {st['kept']:,} | TM {st['entries']:,} 条 | 拒 {st['reject']}", flush=True)
    return st


def main(argv=None):
    """CLI: python scripts/common/aa_tm.py --work <id> [--force] [--stats]"""
    argv = list(sys.argv[1:] if argv is None else argv)
    work = None
    force = "--force" in argv
    if "--work" in argv:
        work = argv[argv.index("--work") + 1]
    if not work:
        print("用法: python scripts/common/aa_tm.py --work <id> [--force]")
        return 2
    if "--stats" in argv:
        tm = load(work)
        n = len([k for k in tm if not k.startswith("__")])
        by_base = {}
        for k, v in tm.items():
            if k.startswith("__") or not isinstance(v, dict):
                continue
            by_base[v.get("base")] = by_base.get(v.get("base"), 0) + 1
        print(f"TM {work}: {n:,} 条 / 覆盖 {len(by_base)} 章 / 已建指纹 {len(tm.get('__built', {}))} 章")
        return 0
    st = build(work, force=force)
    if isinstance(st, dict) and st.get("error"):
        # 失败绝不静默（否则「跑过 TM 重建」是假的）——CLI 直接非零退出
        print("[aa_tm] 重建失败: %s" % st["error"], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
