# -*- coding: utf-8 -*-
"""pass3: 漏译迭代清理——白名单残留清零校验 + stuck连续2轮才break + 顽固行导出 review。

用法：python common/pass3.py --work <id>
白名单 = 全语料频次>=3 的假名片段(名字/艺术纹理) ∪ glossary.json known_names。
顽固行导出 <work_dir>/pass3_review.txt 人工翻译后用「子串替换 + pad_to_width」回填。

M2（2026-08-11 从平铺版移植）：文件级并行（单文件内部轮次仍串行，收敛逻辑依赖轮间状态）；
429 率 >10% 自动降并发 (4→2→1)；AA_PASS3_PARALLEL=0 关闭回退串行。
P0-C：manual_fix.json 人工修复行跳过（不覆盖人工成果）。
"""
import os, re, sys, collections, concurrent.futures, time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(_SCRIPT_DIR), "works"))

import registry
import aa_common as C
import aa_api as A

HEADER_RE = re.compile(r'^\d+\s*：|^\d+\s*名前')

# 429 限流统计
_429_COUNT = 0
_CALL_COUNT = 0
_lock = __import__('threading').Lock()


def _flag_on(name, default_on=True):
    v = os.environ.get(name)
    if v is None:
        return default_on
    return v not in ("0", "false", "False", "")


def _call_api(system, body, **kw):
    """包装 A.call_api，统计调用次数与 429（供并发降级判断）。"""
    global _429_COUNT, _CALL_COUNT
    with _lock:
        _CALL_COUNT += 1
    try:
        return A.call_api(system, body, **kw)
    except Exception as e:
        if '429' in str(e) or 'rate' in str(e).lower():
            with _lock:
                _429_COUNT += 1
        raise


def build_whitelist(OUT_DIR):
    """白名单 = 全语料频次>=3 的假名片段(名字/艺术纹理) ∪ 已知名字"""
    freq = collections.Counter()
    for fname in C.list_txt(OUT_DIR, "_汉化.txt"):
        for l in C.txt_read(os.path.join(OUT_DIR, fname)):
            for s in C.kana_runs(l):
                freq[s] += 1
    return set(s for s, n in freq.items() if n >= 3) | set(C.KNOWN_NAMES)


def residual_bad(line, whitelist):
    return [s for s in C.kana_runs(line) if s not in whitelist]


PASS3_SYSTEM = """你是やる夫AA漫画的汉化翻译。下面每行以「序号|」开头。这些行【被检测出包含尚未翻译的日语文字】。

对每一行：
- 如果行中有日语台词/旁白/拟声词（假名或日文汉字组成的词句，含混在绘画字符中的），把它翻译成简体中文【并完整替换原片段，禁止追加在译文后面】。保持行内绘画字符和整行显示宽度不变（译文变短用全角空格U+3000填充原文字区域，变长则精简）。
- 如果行中只剩【角色名/专有名词】（如 キング、フランドール、ロベルタ、アリス、イギィア）或【绘画装饰假名】或已无日语文字，则原样照抄输出。
- 输出全部行，格式「序号|处理后的完整行」，不要输出任何其他内容。"""


def fix_lines(flagged, whitelist):
    """flagged: (idx, line). Returns (fixed_dict, still_list)
    校验：宽度==原行 且 残留假名(白名单外)清零"""
    pending = {i: l for i, l in flagged}
    fixed = {}
    for round_ in range(2):
        if not pending:
            break
        body = "\n".join(f"{i:05d}|{l}" for i, l in pending.items())
        text = _call_api(PASS3_SYSTEM, body)
        res = A.parse_result(text)
        for num, nl in res.items():
            if num in pending and num not in fixed:
                cand = C.pad_to_width(nl, C.disp_width(pending[num]))
                if C.disp_width(cand) != C.disp_width(pending[num]):
                    continue
                if not residual_bad(cand, whitelist):
                    fixed[num] = cand
        pending = {i: l for i, l in pending.items() if i not in fixed}
    return fixed, list(pending.keys())


def _process_one(path, whitelist, work_dir, review):
    """单个文件：内部轮次串行清理。返回 (fname, 剩余残留数)。"""
    fname = os.path.basename(path)
    lines = C.txt_read(C.txt_existing(path))
    # B2 同步: mixed 段级默认开（AA_USE_MIXED_SEGMENTS=0 关闭）时按 segments 跳过 art 段残留检测
    seg_meta = {}
    if _flag_on("AA_USE_MIXED_SEGMENTS"):
        base = fname.replace("_汉化.txt", "")
        seg_meta = C.load_segments_meta(os.path.join(work_dir, "line_meta_v2"), base)
    prev, no_change = -1, 0
    for iteration in range(4):
        flagged = [(i, l) for i, l in enumerate(lines)
                   if C.residual_bad_segs(l, seg_meta.get(i), whitelist)
                   and not l.startswith(('#', '=')) and not HEADER_RE.match(l)
                   and not C.is_manual_fix(work_dir, fname, i, l)  # P0-C: 人工修复行跳过
                   and seg_meta.get(i) is None]  # 2026-09-07 grilling-C: 砍 mixed 分支留 refine(同 pass2, 整行送模型对 art 密集 mixed 清不动)
        if not flagged:
            break
        if len(flagged) == prev:
            no_change += 1
            if no_change >= 2:      # 连续2轮无变化才 break（stuck）
                break
        else:
            no_change = 0
        prev = len(flagged)
        print(f"{fname[:20]} iter{iteration}: {len(flagged)} -> fixing", flush=True)
        chunks = [flagged[i:i+40] for i in range(0, len(flagged), 40)]
        fixed_all = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            futs = {ex.submit(fix_lines, c, whitelist): c for c in chunks}
            for fut in concurrent.futures.as_completed(futs):
                fixed, _ = fut.result()
                fixed_all.update(fixed)
        for idx, nl in fixed_all.items():
            lines[idx] = nl
        C.txt_write_form(C.txt_out_path(path), lines)
    remain = [(i, l) for i, l in enumerate(lines)
              if C.residual_bad_segs(l, seg_meta.get(i), whitelist)
              and not l.startswith(('#', '=')) and not HEADER_RE.match(l)
              and seg_meta.get(i) is None]  # 2026-09-07 grilling-C: mixed 行不留 pass3_review(留 refine)
    print(f"{fname[:20]}: FINAL residual {len(remain)}", flush=True)
    for i, l in remain:
        review.append(f"{fname} | line {i} | {l}")
    return fname, len(remain)


def main(work=None):
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    cfg = registry.get(work or (args[0] if args else "baitou"))
    OUT_DIR = cfg["out_dir"]
    WORK_DIR = cfg["work_dir"]
    REVIEW_FILE = os.path.join(WORK_DIR, "pass3_review.txt")
    whitelist = build_whitelist(OUT_DIR)
    print(f"whitelist size: {len(whitelist)}", flush=True)
    review = []
    files = C.list_txt(OUT_DIR, "_汉化.txt")
    paths = [os.path.join(OUT_DIR, f) for f in files]
    if _flag_on("AA_PASS3_PARALLEL") and len(paths) > 1:
        # M2: 文件级并行（单文件内部轮次串行）；429 自适应降并发（批次间生效）
        workers = 4
        print(f"pass3 parallel: {len(paths)} files, workers={workers}", flush=True)
        done_cnt = 0
        idx = 0
        while idx < len(paths):
            batch = paths[idx:idx + workers]
            idx += workers
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_process_one, p, whitelist, WORK_DIR, review): p for p in batch}
                for fut in concurrent.futures.as_completed(futs):
                    done_cnt += 1
                    try:
                        fut.result()
                    except Exception as e:
                        print(f"FAILED {os.path.basename(futs[fut])}: {e}", flush=True)
            # 429 自适应降并发（本批完成后生效于下一批）
            if _CALL_COUNT >= 10 and _429_COUNT / _CALL_COUNT > 0.10 and workers > 1:
                workers = max(1, workers // 2)
                print(f"  429 率 {_429_COUNT}/{_CALL_COUNT}, 降并发 -> {workers}", flush=True)
        print(f"pass3 parallel done: {done_cnt}/{len(paths)} files", flush=True)
    else:
        for p in paths:
            _process_one(p, whitelist, WORK_DIR, review)
    with open(REVIEW_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(review))
    print(f"\nreview file: {REVIEW_FILE} ({len(review)} lines)")


if __name__ == "__main__":
    main()
