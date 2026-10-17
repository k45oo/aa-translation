# -*- coding: utf-8 -*-
"""AA 文本 / 元数据包压缩工具：把 .txt / meta .json 转成 .gz（透明读写，省磁盘）。

用法：
    .venv/Scripts/python.exe scripts/tools/compress_text_pkg.py --work <id> [选项]
    .venv/Scripts/python.exe scripts/tools/compress_text_pkg.py --all-works [选项]

    --what {txt,meta,all}  压什么（默认 txt）
                           txt  = aa/*.txt（不含 _台词）+ 汉化/*_汉化.txt → .txt.gz
                           meta = work/line_meta_v2/*.json → .json.gz
                                  （实测压到 6.8%，全库 7.1GB → 约 0.49GB）
                           all  = 两者都压
    --keep                 保留原文件（默认删除，省空间）
    --verify               压完逐文件解压比对 md5（meta 模式恒做校验）
    --all-works            对注册表全部作品执行
    --force                跳过「有任务在跑」保护（不推荐）

透明读口径（压缩后调用方零改动，2026-10-17 全库接线完成）：
    aa/汉化 文本 → aa_translate.txt_read / txt_existing / txt_path
    meta         → aa_translate.meta_load / meta_path / meta_exists / meta_list（写用 meta_dump）
    直接 open(...'/x.json') 或 glob('*.json') 的旧写法压缩后会读不到——新读取点必须走 helper。
"""
import sys, os, gzip, json, argparse, hashlib, glob

BASE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(BASE, "scripts"))
sys.path.insert(0, os.path.join(BASE, "scripts", "works"))

import registry

_TASKS_DIR = os.path.join(BASE, "aa_web_app", "data", "tasks")


def _gz_write(path, raw):
    with gzip.open(path, "wb") as f:
        f.write(raw)


def _md5(b):
    return hashlib.md5(b).hexdigest()


def _safe_remove(p):
    try:
        os.remove(p)
    except OSError:
        pass


def _busy_tasks():
    """压缩期间有任务在写盘会打架：返回非终态任务的 id 列表。"""
    out = []
    for p in glob.glob(os.path.join(_TASKS_DIR, "*.json")):
        if os.path.basename(p).startswith("."):
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if (d.get("status") or "") in ("running", "starting", "stopping", "queued", "scheduled"):
            out.append(d.get("task_id") or os.path.basename(p))
    return out


def compress_dir(d, suffix, delete=True, verify=False):
    """把目录里以 suffix 结尾（非 _台词）的 .txt 转 .gz。suffix='' 压缩所有正文 txt。
    返回 (新增gz, 删除txt, 校验失败列表)。suffix 为 '_汉化.txt' 只压汉化文件。"""
    made = 0
    removed = 0
    fails = []
    for name in os.listdir(d):
        p = os.path.join(d, name)
        if not os.path.isfile(p):
            continue
        if name.endswith("_台词.txt") or name.endswith("_台词.txt.gz"):
            continue
        if suffix:
            if not (name.endswith(suffix) or name.endswith(suffix + ".gz")):
                continue
        else:
            if not (name.endswith(".txt") or name.endswith(".txt.gz")):
                continue
        if name.endswith(".gz"):
            continue  # 已压缩
        src = open(p, "rb").read()
        gz = p + ".gz"
        _gz_write(gz, src)
        bad = False
        if verify:
            # 解压比对
            try:
                chk = gzip.open(gz, "rb").read()
                if _md5(chk) != _md5(src):
                    bad = True
                    fails.append(name)
            except Exception as e:
                bad = True
                fails.append(f"{name}: {e}")
        if bad:
            _safe_remove(gz)          # 校验失败不留坏 .gz，原文件也不删（下轮重试）
            continue
        made += 1
        if delete:
            os.remove(p)
            removed += 1
    return made, removed, fails


def compress_meta(meta_dir, delete=True):
    """work/line_meta_v2/*.json → *.json.gz。

    **恒做解压 md5 比对**：不一致就删掉刚生成的 .gz、原 .json 原样保留（宁可不压也不留坏数据）。
    已有同名 .json.gz 的章跳过（幂等）。返回 (压缩数, 删除数, 跳过数, 失败列表)。
    """
    made = removed = skipped = 0
    fails = []
    if not os.path.isdir(meta_dir):
        return made, removed, skipped, fails
    for name in sorted(os.listdir(meta_dir)):
        if name.endswith(".json.gz") or not name.endswith(".json"):
            continue
        p = os.path.join(meta_dir, name)
        if not os.path.isfile(p):
            continue
        gz = p + ".gz"
        if os.path.exists(gz):
            skipped += 1
            continue
        src = open(p, "rb").read()
        _gz_write(gz, src)
        try:
            chk = gzip.open(gz, "rb").read()
        except Exception as e:
            fails.append(f"{name}: 解压失败 {e}")
            _safe_remove(gz)
            continue
        if _md5(chk) != _md5(src):
            fails.append(f"{name}: 内容不一致")
            _safe_remove(gz)
            continue
        made += 1
        if delete:
            os.remove(p)
            removed += 1
    return made, removed, skipped, fails


def main():
    ap = argparse.ArgumentParser(description="压缩 AA 文本 / meta 为 .gz")
    ap.add_argument("--work", help="作品 id")
    ap.add_argument("--all-works", action="store_true", help="对注册表全部作品执行")
    ap.add_argument("--what", choices=("txt", "meta", "all"), default="txt",
                    help="压什么：txt（默认）/ meta（line_meta_v2）/ all")
    ap.add_argument("--keep", action="store_true", help="保留原文件（默认删除）")
    ap.add_argument("--verify", action="store_true", help="压缩后解压校验（meta 恒校验）")
    ap.add_argument("--force", action="store_true", help="有任务在跑时也压（不推荐）")
    a = ap.parse_args()
    if not a.work and not a.all_works:
        raise SystemExit("需要 --work <id> 或 --all-works")
    if a.work and a.all_works:
        raise SystemExit("--work 与 --all-works 只能给一个")
    delete = not a.keep

    if not a.force:
        run = _busy_tasks()
        if run:
            raise SystemExit("有 %d 个任务在跑（%s…）——压缩会与写盘打架，等空闲或加 --force"
                             % (len(run), ", ".join(run[:3])))

    ids = sorted(registry.WORKS) if a.all_works else [a.work]
    tot = {"tx": 0, "tr": 0, "tm": 0, "mr": 0, "mskip": 0}
    all_fails = []
    for wid in ids:
        try:
            cfg = registry.get(wid)
        except Exception as e:
            print(f"[{wid}] registry.get 失败：{e}")
            continue
        aa_dir, out_dir, work_dir = cfg.get("aa_dir"), cfg.get("out_dir"), cfg.get("work_dir")
        line = [f"[{wid}]"]
        if a.what in ("txt", "all"):
            m1, r1, f1 = compress_dir(aa_dir, "", delete=delete, verify=a.verify) if aa_dir and os.path.isdir(aa_dir) else (0, 0, [])
            m2, r2, f2 = compress_dir(out_dir, "_汉化.txt", delete=delete, verify=a.verify) if out_dir and os.path.isdir(out_dir) else (0, 0, [])
            tot["tx"] += m1 + m2
            tot["tr"] += r1 + r2
            all_fails += [f"{wid}/{x}" for x in f1 + f2]
            if m1 or m2:
                line.append(f"txt: 压 {m1 + m2}（aa {m1} / 汉化 {m2}），删 {r1 + r2}")
        if a.what in ("meta", "all"):
            md = os.path.join(work_dir, "line_meta_v2") if work_dir else ""
            m3, r3, s3, f3 = compress_meta(md) if md else (0, 0, 0, [])
            tot["tm"] += m3
            tot["mr"] += r3
            tot["mskip"] += s3
            all_fails += [f"{wid}/{x}" for x in f3]
            if m3 or s3 or f3:
                line.append(f"meta: 压 {m3}，删 {r3}，跳过 {s3}")
        if len(line) > 1:
            print(" ".join(line), flush=True)

    print("\n=== 合计 ===")
    if a.what in ("txt", "all"):
        print(f"txt  ：压缩 {tot['tx']} 个，删除 {tot['tr']} 个原文件")
    if a.what in ("meta", "all"):
        print(f"meta ：压缩 {tot['tm']} 个，删除 {tot['mr']} 个 .json（跳过 {tot['mskip']}）")
    print(f"校验失败 {len(all_fails)}")
    for x in all_fails[:40]:
        print("  FAIL:", x)
    if all_fails:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
