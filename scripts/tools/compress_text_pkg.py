# -*- coding: utf-8 -*-
"""AA 文本包压缩工具：把指定作品的 aa/*.txt 与 汉化/*_汉化.txt 转成 .txt.gz（透明读写，省磁盘）。

用法：
    .venv/Scripts/python.exe scripts/tools/compress_text_pkg.py --work <id> [--keep] [--verify]
    --keep     压缩后保留原 .txt（默认删除，省空间）
    --verify   压缩后逐章校验 gz 解压的行内容与原文件一致（防压坏）

r401 系作品 aa_dir 是 aaManga/<作品>/aa，汉化在 out_dir。aa/*.txt.gz 与 aa/*.txt 并存时
优先读 .gz（txt_read 透明逻辑）。压缩后阅读器/管线读 .gz 无需代码改动。
"""
import sys, os, gzip, argparse, hashlib

BASE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(BASE, "scripts"))
sys.path.insert(0, os.path.join(BASE, "scripts", "works"))

import registry


def _gz_write(path, raw):
    with gzip.open(path, "wb") as f:
        f.write(raw)


def _md5(b):
    return hashlib.md5(b).hexdigest()


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
        if verify:
            # 解压比对
            try:
                chk = gzip.open(gz, "rb").read()
                if _md5(chk) != _md5(src):
                    fails.append(name)
            except Exception as e:
                fails.append(f"{name}: {e}")
        made += 1
        if delete and not fails:
            os.remove(p)
            removed += 1
    return made, removed, fails


def main():
    ap = argparse.ArgumentParser(description="压缩 AA 文本为 .txt.gz")
    ap.add_argument("--work", required=True)
    ap.add_argument("--keep", action="store_true", help="保留原 .txt（默认删除）")
    ap.add_argument("--verify", action="store_true", help="压缩后解压校验")
    a = ap.parse_args()

    cfg = registry.get(a.work)
    aa_dir = cfg["aa_dir"]
    out_dir = cfg["out_dir"]
    if not os.path.isdir(aa_dir):
        raise SystemExit(f"aa_dir 不存在: {aa_dir}")
    delete = not a.keep

    # 压缩 aa 原文（全部正文 txt）
    m1, r1, f1 = compress_dir(aa_dir, "", delete=delete, verify=a.verify)
    # 压缩汉化
    m2, r2, f2 = compress_dir(out_dir, "_汉化.txt", delete=delete, verify=a.verify) if os.path.isdir(out_dir) else (0, 0, [])
    print(f"aa/ : 压 {m1} 个 .gz，删 {r1} 个 .txt，校验失败 {len(f1)}")
    print(f"汉化/: 压 {m2} 个 .gz，删 {r2} 个 .txt，校验失败 {len(f2)}")
    for x in f1 + f2:
        print("  FAIL:", x)


if __name__ == "__main__":
    main()
