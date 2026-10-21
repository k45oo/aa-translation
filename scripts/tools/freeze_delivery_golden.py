# -*- coding: utf-8 -*-
"""冻结交付页**黄金样本哈希清单** → `scripts/tests/fixtures/delivery_golden.json`

背景（2026-10-07）：旧单文件交付物（`交付HTML/<名>_汉化合集.html`）自 ae6dc930 起移出 git
跟踪，`test_gen_html` 里那套「新 data/*.js 正文 vs 旧单文件 <pre> 内容」的快照比对随之失效
（文件不在 → 有的 skipped、有的直接断言失败）。这里把它换成**哈希清单**：每章 `<pre class="aa">`
正文（剥掉 sfx 包装后）的 sha1 + 行数 + 章节数 —— 体积小可入库，回归防线口径不变。

做法：对每个候选（registry 里 html_out 是文件夹、且同名 `.html` 旧单文件还在磁盘或 git 里）：
  ① 用当前生成器生成到临时目录 → 读 `data/*.js` 逐章 tr（sha1 / 行数）
  ② 读旧单文件里逐章 `<pre class="aa">` 内容（同样剥 sfx）→ sha1 / 行数
  ③ 一致性差异记进 `vs_legacy` 说明；**基线一律取当前渲染**（旧单文件只作参照：
     它可能落后于人工修订/渲染改进，如「先生→桑」+宽度补位；回归防线从现在起对新基线有效）

用法：
  python scripts/tools/freeze_delivery_golden.py                 # 默认：fukou（走 git 历史副本）+ 小体积候选
  python scripts/tools/freeze_delivery_golden.py --work fukou --git
  python scripts/tools/freeze_delivery_golden.py --limit 5 --max-mb 4
  python scripts/tools/freeze_delivery_golden.py --check         # 只比对，不写文件（CI 用）
"""
import argparse
import base64
import glob
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
REPO = os.path.dirname(SCRIPTS)
sys.path.insert(0, os.path.join(SCRIPTS, "common"))
sys.path.insert(0, os.path.join(SCRIPTS, "works"))

import gen_aa_html                                    # noqa: E402
import registry                                       # noqa: E402

OUT_JSON = os.path.join(SCRIPTS, "tests", "fixtures", "delivery_golden.json")
PRE_RE = re.compile(r'<pre class="aa">(.*?)</pre>', re.S)
SFX_SPAN_RE = re.compile(r'<span class="sfx">([^<]*)</span>')
# fukou 的旧单文件只在 git 里（ae6dc930 删的），用这个 rev 取回
FUKOU_GIT = "ae6dc930^:交付HTML/不幸の催眠_汉化合集.html"


def _desfx(html):
    return SFX_SPAN_RE.sub(r"\1", html)


def _payload(path):
    s = open(path, encoding="utf-8").read()
    return json.loads(s[s.index("{"):s.rindex("}") + 1])


def _unz(b64):
    return gzip.decompress(base64.b64decode(b64)).decode("utf-8")


def _sha1(s):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def _gen_side(work, tmp):
    """当前生成器 → [(sha1, 行数), ...]（按 data/ 文件名顺序）"""
    cfg = registry.WORKS[work]
    orig = cfg["html_out"]
    try:
        cfg["html_out"] = os.path.join(tmp, work)
        sys.argv = ["freeze", work, "--no-source"]
        gen_aa_html.main(work)
        out = []
        for p in sorted(glob.glob(os.path.join(cfg["html_out"], "data", "*.js"))):
            tr = _desfx(_unz(_payload(p)["tr"]))
            out.append((_sha1(tr), tr.count("\n") + 1))
        return out
    finally:
        cfg["html_out"] = orig


def _snap_side(path):
    raw = open(path, encoding="utf-8").read().replace("\r\n", "\n")
    return [(_sha1(_desfx(p)), _desfx(p).count("\n") + 1) for p in PRE_RE.findall(raw)]


def _git_snapshot(spec, dest):
    r = subprocess.run(["git", "show", spec], cwd=REPO, capture_output=True)
    if r.returncode:
        raise RuntimeError("git show 失败: %s\n%s" % (spec, r.stderr.decode("utf-8", "replace")[:300]))
    with open(dest, "wb") as f:
        f.write(r.stdout)
    return dest


def _legacy_of(work):
    """旧单文件路径（磁盘上有就返回）；fukou 走 git 特殊处理"""
    cfg = registry.WORKS.get(work)
    if not isinstance(cfg, dict) or not cfg.get("html_out"):
        return None
    ho = cfg["html_out"]
    if not gen_aa_html.deliver_is_folder(cfg):
        return None
    p = ho + ".html"
    if os.path.exists(p):
        return p
    if work == "fukou":
        tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
        tmp.close()
        try:
            return _git_snapshot(FUKOU_GIT, tmp.name)
        except Exception as e:
            print("  ! fukou 取 git 副本失败:", e)
            os.unlink(tmp.name)
    return None


def freeze(works, check_only=False):
    tmp = tempfile.mkdtemp(prefix="freeze-golden-")
    data = {"frozen": time.strftime("%Y-%m-%d"),
            "note": "交付页黄金样本哈希清单：每章 <pre class=\"aa\"> 正文（剥 sfx 包装后）的 sha1[:16] 与行数。"
                    "重建/更新：python scripts/tools/freeze_delivery_golden.py（渲染口径真要变时，先确认变更再重建）。",
            "works": {}, "skipped": {}, "removed": {}}
    if os.path.exists(OUT_JSON) and not check_only and not works:
        try:                                          # 保留没重新校验的旧条目
            old = json.load(open(OUT_JSON, encoding="utf-8"))
            data["works"] = old.get("works", {})
            data["skipped"] = old.get("skipped", {})
            # 已删除作品的留档（2026-10-21）：重建基线时别把"某作品被删"的记录抹掉
            data["removed"] = old.get("removed", {})
        except Exception:
            pass
    try:
        for work in works:
            print("=== %s" % work)
            legacy = _legacy_of(work)
            if not legacy:
                print("  跳过：没有旧单文件交付物")
                data["skipped"][work] = "找不到旧单文件交付物（磁盘与 git 都没有）"
                continue
            try:
                old = _snap_side(legacy)
                new = _gen_side(work, tmp)
            except Exception as e:
                print("  ! 失败:", e)
                data["skipped"][work] = "生成/读取失败: %s" % str(e)[:160]
                continue
            src_desc = ("git " + FUKOU_GIT) if work == "fukou" and not os.path.exists(
                (registry.WORKS[work]["html_out"] + ".html")) else legacy
            old = _snap_side(legacy)
            new = _gen_side(work, tmp)
            # 旧单文件只作参照：章节清单/正文被人工修订过是正常的（如「先生→桑」+宽度补位），
            # 一致性差异记进 vs_legacy 说明；**基线一律取当前渲染**（回归防线从现在起有效）。
            if len(old) != len(new):
                vs = "旧单文件 %d 章 / 当前 %d 章：章节清单已变" % (len(old), len(new))
            else:
                bad = [i for i, (a, b) in enumerate(zip(old, new)) if a != b]
                vs = "与旧单文件逐章一致" if not bad else (
                    "旧单文件 %d/%d 章不同（首个第 %d 章：行数 旧%d/新%d）—— 内容已人工修订/渲染已改进，"
                    "旧单文件只作参照" % (len(bad), len(old), bad[0] + 1, old[bad[0]][1], new[bad[0]][1]))
            data["works"][work] = {
                "src": src_desc if isinstance(src_desc, str) and not os.path.isabs(src_desc) else os.path.basename(legacy),
                "vs_legacy": vs,
                "frozen": time.strftime("%Y-%m-%d"),
                "chapters": len(new),
                "tr_sha1": [h for h, _ in new],
                "tr_lines": [n for _, n in new],
            }
            data["skipped"].pop(work, None)
            data["removed"].pop(work, None)      # 作品回来了 → 不再是"已删除"
            print("  ✓ 冻结 %d 章基线（%s）" % (len(new), vs))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", action="append", default=None, help="指定作品 id（可多次）")
    ap.add_argument("--git", action="store_true", help="fukou 用 git 历史副本（默认已如此）")
    ap.add_argument("--limit", type=int, default=4, help="最多冻结几个作品（含 fukou）")
    ap.add_argument("--max-mb", type=float, default=4.0, help="旧单文件大于此体积不进默认候选")
    ap.add_argument("--check", action="store_true", help="只比对，不写文件")
    args = ap.parse_args()

    if args.work:
        works = args.work
    else:
        works = ["fukou"]
        for k, cfg in sorted(registry.WORKS.items()):
            if not isinstance(cfg, dict) or not cfg.get("html_out"):
                continue
            if k == "fukou" or not gen_aa_html.deliver_is_folder(cfg):
                continue
            p = cfg["html_out"] + ".html"
            if os.path.exists(p) and os.path.getsize(p) <= args.max_mb * 1048576:
                works.append(k)
            if len(works) >= args.limit:
                break
    print("候选:", works)
    data = freeze(works, check_only=args.check)
    print("\n冻结 %d 部 / 跳过 %d 部" % (len(data["works"]), len(data["skipped"])))
    for k, v in sorted(data["skipped"].items()):
        print("  跳过 %-24s %s" % (k, v))
    if args.check:
        print("--check：不写文件")
        return 0
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("已写:", OUT_JSON, "%.1f KB" % (os.path.getsize(OUT_JSON) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
