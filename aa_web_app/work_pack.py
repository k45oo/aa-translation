# -*- coding: utf-8 -*-
"""作品导入导出（2026-10-01）：把一部作品打成单个 rar 包，在另一处导入。

设计要点
- 打包用 **WinRAR 的 CLI `Rar.exe`**（GUI 版不带命令行能力用不了）：`rar a -m3 -y <包> @<清单>`。
  清单里写「相对仓库根」的路径、cwd 也设成仓库根 → 包内路径天然是
  `aaManga/<作品目录>/...`、`aaManga/交付HTML/<...>_合集.html`，导入端直接解到仓库根即可归位。
- 只打**资产**：`aa/`（原文本+colormap）、`汉化/`（成品+台词文件）、`raw/`（可选）、
  `work/` **白名单资产**、交付合集 HTML；`work/` 的过程文件（ckpt / 缓存 / 日志 /
  审核记录 / 备份 / 译文记忆表）一律不进包。
- 包内 `_aa_workpack.json` 存作品 id / 标题 / 目录名 / registry 条目 / work_meta 条目，
  导入端据此自动注册；**路径按 BASE 相对写**（`BASE + r"\\aaManga\\..."`），禁硬编码盘符。
- 往返都验过：日文/中文文件名、`-x` 排除掩码、`rar e` 单取 manifest 均正常（RAR 5.60）。
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
for _p in (os.path.join(_REPO, "scripts"), os.path.join(_REPO, "scripts", "works"),
           os.path.join(_REPO, "scripts", "common")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

FORMAT = "aa-work-pack/1"
MANIFEST_NAME = "_aa_workpack.json"
# 数据根（2026-10-09 方案 §5）：作品元数据是可写状态，落数据根（AA_BASE 未设时 = 代码根）。
import paths as _paths
_DATA = _paths.BASE
WORK_META_FILE = os.path.join(_DATA, "aa_web_app", "data", "work_meta.json")   # 可被测试覆盖

# work/ 里的人工资产（白名单；表外的都是过程文件/缓存/日志/记录，不进包）
ASSET_FILES = {
    "chapter_order.json",       # 章节顺序（人工排过）
    "chapters.json",            # 章节清单（dl_runner chapters_source=cache 消费）
    "site_url.json",            # 原站分类页地址（更新检查用）
    "title_translations.json",  # 章标题译名
    "description_zh.txt",       # 作品简介中文
    "r18_summary.json",         # 章节级 R18 判定结果
    "manual_fix.txt",           # 人工修复清单
    "glossary_candidates.json", # 术语候选池（人工确认过）
    "glossary_iter.json",       # 术语迭代候选（人工审校过）
}
ASSET_DIRS = ("gold_dialogue", "line_meta_overrides")   # 人工台词标注 / 人工行类型覆盖
ASSET_PREFIX_SUFFIX = (
    ("glossary_", ".json"),        # 作品术语表 glossary_<id>.json
    ("pokemon_fixed_", ".json"),   # 宝可梦固定内容表（手改入口）
)
ASSET_SUFFIX = ("_knowledge.json",)   # 作品知识库 <id>_knowledge.json

# 传给 rar 的排除掩码（备份/临时）：目录内容按掩码排除，白名单文件本就不在清单里
EXCLUDE_MASKS = ("*.bak*", "*.tmp", "backup_*", "*~")


class PackError(Exception):
    """导出/导入失败；消息直接展示给用户。"""


# ---------- 基础工具 ----------

def base_dir():
    import registry
    return registry.BASE


def find_rar():
    """定位 Rar.exe：env AA_RAR > 常见安装目录 > PATH。"""
    p = (os.environ.get("AA_RAR") or "").strip()
    if p and os.path.exists(p):
        return p
    for c in (r"C:\Program Files\WinRAR\Rar.exe", r"C:\Program Files (x86)\WinRAR\Rar.exe"):
        if os.path.exists(c):
            return c
    for d in (os.environ.get("PATH") or "").split(os.pathsep):
        if d and os.path.exists(os.path.join(d, "Rar.exe")):
            return os.path.join(d, "Rar.exe")
    return ""


def excluded_name(name):
    """与 EXCLUDE_MASKS 同口径（用于导出预览的统计，别和 rar 实际行为漂移）。"""
    n = name.lower()
    return (".bak" in n) or n.startswith("backup_") or n.endswith(".tmp") or n.endswith("~")


def is_work_asset(name):
    """work/ 下是否属于「人工资产」（白名单）。"""
    if name in ASSET_FILES:
        return True
    if name.endswith(ASSET_SUFFIX):
        return True
    for pre, suf in ASSET_PREFIX_SUFFIX:
        if name.startswith(pre) and name.endswith(suf):
            return True
    return False


def _rel(base, p):
    return os.path.relpath(os.path.normpath(p), os.path.normpath(base))


def _walk_stat(abs_path):
    """(文件数, 字节数)，排除排除掩码命中的文件。"""
    if os.path.isfile(abs_path):
        return (1, os.path.getsize(abs_path)) if not excluded_name(os.path.basename(abs_path)) else (0, 0)
    n = b = 0
    for root, dirs, files in os.walk(abs_path):
        dirs[:] = [d for d in dirs if not excluded_name(d)]
        for f in files:
            if excluded_name(f):
                continue
            try:
                b += os.path.getsize(os.path.join(root, f))
                n += 1
            except OSError:
                pass
    return n, b


def _temp_dir(base, tag):
    d = os.path.join(base, "temp", "aa_pack", "%s_%s" % (tag, time.strftime("%Y%m%d_%H%M%S")))
    os.makedirs(d, exist_ok=True)
    return d


def _rm_partial(p):
    """失败时清掉半成品压缩包（别在导出目录留个坏包）。"""
    try:
        if os.path.exists(p):
            os.remove(p)
    except OSError:
        pass


def _decode(b):
    for enc in ("utf-8", "gbk"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("utf-8", "replace")


def _run_rar(rar, args, cwd=None):
    """跑一次 rar（静默开关由 args 带）；返回 (rc, 输出文本)。"""
    try:
        p = subprocess.run([rar] + list(args), cwd=cwd, capture_output=True)
    except OSError as e:
        raise PackError("调用 Rar.exe 失败: %s" % e)
    return p.returncode, _decode((p.stdout or b"") + (p.stderr or b""))


# ---------- 导出 ----------

def export_dirs(cfg):
    """cfg → {aa/out/work/raw: 相对仓库根路径}（只含有目录的键）。"""
    base = base_dir()
    out = {}
    for key, ck in (("aa", "aa_dir"), ("out", "out_dir"), ("work", "work_dir"), ("raw", "raw_dir")):
        p = cfg.get(ck)
        if p and os.path.isdir(p):
            out[key] = _rel(base, p)
    return out


def root_rel_of(cfg, dirs=None):
    """作品根（相对仓库根）= 各目录的公共父目录。"""
    base = base_dir()
    dirs = dirs if dirs is not None else export_dirs(cfg)
    cands = []
    for key in ("aa", "out", "work", "raw"):
        if key in dirs:
            cands.append(os.path.dirname(dirs[key]))
    cands = [c for c in cands if c]
    if not cands:
        return ""
    return os.path.commonpath(cands) if len(cands) > 1 else cands[0]


def html_files(cfg, dirs=None):
    """交付物件（cfg.html_out 优先，否则按作品目录名 glob 白名单后缀，兼容旧单文件）。

    2026-10-06 起交付物是**文件夹**（`aaManga/交付HTML/<名>_汉化合集/`，入口 index.html；
    2026-10-10 起交付物统一挂 aaManga/ 下，根目录为历史位置仍兼容）；
    html_out 仍带 `.html` 的少数作品按旧语义取该文件。文件夹格式**不做** glob 回退——
    否则会把历史遗留的旧单文件当成交付物打进去（内容已过期）。
    """
    base = base_dir()
    out = []
    ho = (cfg.get("html_out") or "").strip()
    if ho.endswith(".html"):
        if os.path.exists(ho):
            return [_rel(base, ho)]
    else:
        folder = ho[:-5] if ho.endswith(".html") else ho
        if folder and os.path.exists(folder):
            return [_rel(base, folder)]
        if ho:
            return []           # 明确配了 html_out 但还没生成 → 不带交付物
    dirs = dirs if dirs is not None else export_dirs(cfg)
    dn = os.path.basename(os.path.dirname(os.path.join(base, dirs["aa"]))) if "aa" in dirs else ""
    if dn:
        for ddir in (os.path.join(base, "aaManga", "交付HTML"),
                     os.path.join(base, "交付HTML")):
            if not os.path.isdir(ddir):
                continue
            for suf in ("_合集.html", "_汉化合集.html"):
                for f in sorted(glob.glob(os.path.join(ddir, dn + "*" + suf))):
                    out.append(_rel(base, f))
            if out:
                break
    return out


def plan_export(cfg, work_id, include_raw=False):
    """导出计划（纯计算，不落盘）。

    返回 {items:[相对仓库根路径], dirs:{aa/out/work/raw:名字}, root, html, files, bytes,
          raw_files, raw_bytes, work_assets:[名字], work_skipped:[名字]}
    """
    base = base_dir()
    dirs = export_dirs(cfg)
    if "aa" not in dirs and "out" not in dirs:
        raise PackError("作品既没有 aa 原文目录也没有汉化目录，无可导出内容")
    root = root_rel_of(cfg, dirs)
    items, files, nbytes = [], 0, 0
    # 1. aa / 汉化（成品）
    for key in ("aa", "out"):
        if key in dirs:
            items.append(dirs[key])
            n, b = _walk_stat(os.path.join(base, dirs[key]))
            files += n
            nbytes += b
    # 2. work 白名单资产
    assets, skipped = [], []
    if "work" in dirs:
        wabs = os.path.join(base, dirs["work"])
        for name in sorted(os.listdir(wabs)):
            p = os.path.join(wabs, name)
            if os.path.isdir(p):
                (assets if name in ASSET_DIRS else skipped).append(name)
            else:
                (assets if is_work_asset(name) else skipped).append(name)
        for name in assets:
            rel = dirs["work"] + "/" + name
            items.append(rel)
            n, b = _walk_stat(os.path.join(base, rel))
            files += n
            nbytes += b
    # 3. raw（可选）
    raw_files = raw_bytes = 0
    if "raw" in dirs:
        raw_files, raw_bytes = _walk_stat(os.path.join(base, dirs["raw"]))
        if include_raw:
            items.append(dirs["raw"])
            files += raw_files
            nbytes += raw_bytes
    # 4. 交付合集 HTML
    htmls = html_files(cfg, dirs)
    for h in htmls:
        items.append(h)
        n, b = _walk_stat(os.path.join(base, h))
        files += n
        nbytes += b
    return {"work": work_id, "root": root, "dirs": {k: os.path.basename(v) for k, v in dirs.items()},
            "dirs_rel": dirs, "items": items, "files": files, "bytes": nbytes,
            "packed_keys": [k for k in ("aa", "out", "work", "raw")
                            if k in dirs and (k != "raw" or include_raw)],
            "raw_files": raw_files, "raw_bytes": raw_bytes, "include_raw": bool(include_raw),
            "html": htmls, "work_asset_names": assets, "work_skipped_names": skipped}


def build_manifest(cfg, work_id, plan, extra=None):
    """包内 manifest：作品 id/标题/目录名/registry 条目/work_meta 条目（供导入端自动注册）。"""
    import registry
    from aa_web_app import logic
    entry = {k: v for k, v in (cfg or {}).items()}
    # registry 条目里只留非路径配置（路径由导入端按 BASE 重新拼）
    for k in ("aa_dir", "out_dir", "work_dir", "raw_dir", "html_out"):
        entry.pop(k, None)
    meta = {}
    try:
        wm = logic.load_work_meta(WORK_META_FILE)
        meta = dict(wm.get(work_id, {}))
    except Exception:
        meta = {}
    m = {
        "format": FORMAT,
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "work": work_id,
        "title": (cfg or {}).get("title") or meta.get("cn_name") or work_id,
        "root_rel": plan["root"],
        "dirs": plan["dirs"],
        "packed_dirs": plan.get("packed_keys") or [k for k in plan["dirs"] if k != "raw"],
        "html_rel": plan["html"],
        "include_raw": plan["include_raw"],
        "files": plan["files"],
        "bytes": plan["bytes"],
        "registry": entry,
        "work_meta": meta,
    }
    if extra:
        m.update(extra)
    return m


def listfile_content(items):
    """rar 清单内容：一行一个相对路径（反斜杠），UTF-8 BOM 让 rar 按 Unicode 读。"""
    lines = [it.replace("/", "\\") for it in items]
    return "\ufeff" + "\n".join(lines) + "\n"


def run_export(cfg, work_id, include_raw=False, out_dir=None,
               progress=None, extra=None):
    """执行导出 → 返回 {ok, path, manifest, bytes, secs, log}。异常抛 PackError。

    progress: 可选回调 fn(dict)，用于把中间状态抛给上层（跳进度）。
    """
    def _p(msg, **kw):
        if progress:
            progress(dict(msg=msg, **kw))

    rar = find_rar()
    if not rar:
        raise PackError("未找到 Rar.exe（请安装 WinRAR，或用环境变量 AA_RAR 指定 Rar.exe 全路径）")
    base = base_dir()
    plan = plan_export(cfg, work_id, include_raw)
    if not plan["items"]:
        raise PackError("没有可打包的内容")
    outdir = out_dir or (os.environ.get("AA_EXPORT_DIR") or "").strip() or os.path.join(base, "导出")
    os.makedirs(outdir, exist_ok=True)
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in work_id)[:60] or "work"
    arch = os.path.abspath(os.path.join(outdir, "%s_%s.rar" % (safe, time.strftime("%Y%m%d_%H%M%S"))))
    manifest = build_manifest(cfg, work_id, plan, extra=extra)
    manifest["out_name"] = os.path.basename(arch)
    tmp = _temp_dir(base, "export")
    lst = os.path.join(tmp, "items.lst")
    with open(lst, "w", encoding="utf-8") as f:
        f.write(listfile_content(plan["items"]))
    _p("打包 %d 个文件（%.1f MB）…" % (plan["files"], plan["bytes"] / 1048576.0))
    t0 = time.time()
    # 条目按相对仓库根写、cwd 也是仓库根 → 包内路径 = aaManga/<作品目录>/…（导入端解到仓库根即归位）
    args = ["a", "-m3", "-y", arch, "@" + lst] + ["-x" + m for m in EXCLUDE_MASKS]
    rc, txt = _run_rar(rar, args, cwd=base)
    if rc != 0 or not os.path.exists(arch):
        _rm_partial(arch)
        raise PackError("rar 打包失败（rc=%s）: %s" % (rc, (txt or "").strip()[-500:]))
    _p("写入 manifest…", bytes_done=os.path.getsize(arch))
    with open(os.path.join(tmp, MANIFEST_NAME), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    rc2, txt2 = _run_rar(rar, ["a", "-m3", "-y", "-idq", arch, MANIFEST_NAME], cwd=tmp)
    if rc2 != 0:
        _rm_partial(arch)
        raise PackError("写入 manifest 失败（rc=%s）: %s" % (rc2, (txt2 or "").strip()[-300:]))
    shutil.rmtree(tmp, ignore_errors=True)
    size = os.path.getsize(arch)
    _p("完成", path=arch, bytes_done=size)
    return {"ok": True, "path": arch, "name": os.path.basename(arch), "size": size,
            "files": plan["files"], "bytes": plan["bytes"], "secs": round(time.time() - t0, 1),
            "manifest": manifest, "log": (txt or "")[-2000:]}


# ---------- 导入 ----------

def peek_manifest(rar_path):
    """只从包里取出 _aa_workpack.json（不整包解压）→ manifest dict。"""
    rar = find_rar()
    if not rar:
        raise PackError("未找到 Rar.exe（请安装 WinRAR，或用环境变量 AA_RAR 指定 Rar.exe 全路径）")
    if not os.path.exists(rar_path):
        raise PackError("压缩包不存在: %s" % rar_path)
    base = base_dir()
    tmp = _temp_dir(base, "peek")
    try:
        rc, txt = _run_rar(rar, ["e", "-idq", "-y", "-o+", rar_path, MANIFEST_NAME, tmp + os.sep], cwd=tmp)
        mf = os.path.join(tmp, MANIFEST_NAME)
        if not os.path.exists(mf):
            raise PackError("这不是工作台导出的作品包（包内缺 %s）" % MANIFEST_NAME)
        with open(mf, "r", encoding="utf-8") as f:
            m = json.load(f)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if m.get("format") != FORMAT:
        raise PackError("包格式不支持: %r（期望 %s）" % (m.get("format"), FORMAT))
    if not m.get("work"):
        raise PackError("包内 manifest 缺 work 字段")
    return m


def _dir_size(p):
    try:
        return sum(os.path.getsize(os.path.join(r, f))
                   for r, _, fs in os.walk(p) for f in fs)
    except OSError:
        return 0


def inspect_import(manifest, rar_path=None):
    """导入预览：目标目录/冲突（不改盘）。"""
    import registry
    base = base_dir()
    work = manifest["work"]
    root_rel = manifest.get("root_rel") or ""
    root_abs = os.path.join(base, root_rel) if root_rel else ""
    info = {
        "work": work, "title": manifest.get("title") or work,
        "exported_at": manifest.get("exported_at"), "include_raw": manifest.get("include_raw"),
        "files": manifest.get("files"), "bytes": manifest.get("bytes"),
        "root_rel": root_rel, "dirs": manifest.get("dirs") or {},
        "html_rel": manifest.get("html_rel") or [],
        "exists_registry": work in registry.WORKS,
        "registry_title": (registry.WORKS.get(work) or {}).get("title"),
        "root_abs": root_abs,
        "root_exists": bool(root_abs) and os.path.isdir(root_abs),
        "root_size": _dir_size(root_abs) if root_abs and os.path.isdir(root_abs) else 0,
        "rar_size": os.path.getsize(rar_path) if rar_path and os.path.exists(rar_path) else None,
        "html_conflict": [h for h in (manifest.get("html_rel") or [])
                          if os.path.exists(os.path.join(base, h))],
    }
    info["conflicts"] = []
    if info["exists_registry"]:
        info["conflicts"].append("作品 id「%s」已在 registry 里注册（%s）" % (work, info["registry_title"] or ""))
    if info["root_exists"] and info["root_size"] > 0:
        info["conflicts"].append("目标目录 %s 已存在且非空（%.1f MB）" % (root_rel, info["root_size"] / 1048576.0))
    return info


def _merge_move(src, dst, skip=(MANIFEST_NAME,)):
    """把 staging 树合并进仓库（父目录已存在时逐层并、同名文件覆盖）。返回移动文件数。"""
    n = 0
    for name in os.listdir(src):
        if name in skip:
            continue
        s, d = os.path.join(src, name), os.path.join(dst, name)
        if os.path.isdir(s):
            os.makedirs(d, exist_ok=True)
            n += _merge_move(s, d, skip=skip)
        else:
            os.makedirs(os.path.dirname(d), exist_ok=True)
            if os.path.exists(d):
                os.remove(d)
            shutil.move(s, d)
            n += 1
    return n


def install(rar_path, options=None, progress=None):
    """解包 + 归位 + 恢复 registry/work_meta。返回结果 dict。异常抛 PackError。

    options: {"registry": True/False(默认True), "work_meta": True/False}
    """
    import registry
    from aa_web_app import logic
    options = options or {}
    rar = find_rar()
    if not rar:
        raise PackError("未找到 Rar.exe")
    base = base_dir()

    def _p(msg):
        if progress:
            progress({"msg": msg})

    manifest = peek_manifest(rar_path)
    info = inspect_import(manifest, rar_path)
    if info["conflicts"] and not options.get("force"):
        raise PackError("导入被拒绝：" + "；".join(info["conflicts"]) + "（请先删除该作品再导入）")
    staging = _temp_dir(base, "import")
    try:
        _p("解压中（%.1f MB）…" % ((info.get("rar_size") or 0) / 1048576.0))
        rc, txt = _run_rar(rar, ["x", "-idq", "-y", "-o+", rar_path, staging + os.sep], cwd=staging)
        if rc != 0:
            raise PackError("解压失败（rc=%s）: %s" % (rc, (txt or "").strip()[-400:]))
        # 校验目录确实解出来了
        root_rel = manifest.get("root_rel") or ""
        dirs = manifest.get("dirs") or {}
        packed = manifest.get("packed_dirs") or [k for k in dirs if k != "raw"]
        for key in packed:
            sub = dirs.get(key)
            if not sub:
                continue
            p = os.path.join(staging, root_rel, sub) if root_rel else os.path.join(staging, sub)
            if not os.path.isdir(p):
                raise PackError("包内容与 manifest 不符：缺目录 %s/%s" % (root_rel, sub))
        _p("归位到仓库…")
        n = _merge_move(staging, base)
        # registry 条目（路径按 BASE 相对写）
        added_reg = False
        if options.get("registry", True) and manifest.get("registry") is not None:
            entry = _entry_for_import(manifest, base)
            _append_registry_source(registry_entry_source(manifest["work"], entry, base))
            added_reg = True
        # work_meta
        added_meta = False
        if options.get("work_meta", True) and manifest.get("work_meta"):
            mf = WORK_META_FILE
            meta = logic.load_work_meta(mf)
            cur = meta.get(manifest["work"], {})
            new = {k: v for k, v in manifest["work_meta"].items() if k != "_meta"}
            cur.update(new)
            meta[manifest["work"]] = cur
            if not logic.save_work_meta(mf, meta):
                raise PackError("写入 work_meta.json 失败")
            added_meta = True
        _p("完成")
        return {"ok": True, "work": manifest["work"], "files": n, "registry": added_reg,
                "work_meta": added_meta, "root_rel": root_rel, "name": os.path.basename(rar_path)}
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _entry_for_import(manifest, base):
    """manifest 的 registry 条目 → 本机绝对路径（导入后注册用）。"""
    e = dict(manifest.get("registry") or {})
    root = os.path.join(base, manifest.get("root_rel") or "")
    d = manifest.get("dirs") or {}
    if "aa" in d:
        e["aa_dir"] = os.path.join(root, d["aa"])
    if "out" in d:
        e["out_dir"] = os.path.join(root, d["out"])
    if "work" in d:
        e["work_dir"] = os.path.join(root, d["work"])
    if "raw" in d:
        e["raw_dir"] = os.path.join(root, d["raw"])
    htmls = manifest.get("html_rel") or []
    if htmls:
        e["html_out"] = os.path.join(base, htmls[0])
    return e


# ---------- registry.py 源码生成 / 追加 ----------

def registry_entry_source(work_id, entry, base=None):
    """生成 registry.py 条目源码：路径写成 `BASE + r"\\aaManga\\..."`（不硬编码盘符）。"""
    base = base or base_dir()
    lines = ['    "%s": dict(' % work_id]
    for k, v in entry.items():
        lines.append("        %s=%s," % (k, _py_lit(v, base)))
    lines.append("    ),")
    return "\n".join(lines)


def _py_lit(v, base):
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        b = os.path.normpath(base)
        n = os.path.normpath(v)
        if n.startswith(b + os.sep):
            return 'BASE + r"%s"' % n[len(b):]
        return repr(v) if "\\" in v else '"%s"' % v.replace("\\", "\\\\").replace('"', '\\"')
    if isinstance(v, (list, tuple)):
        inner = ", ".join(_py_lit(x, base) for x in v)
        if len(v) == 1:
            inner += ","
        return "(" + inner + ")"
    if v is None:
        return "None"
    return repr(v)


def registry_path():
    import registry
    return registry.__file__


def _append_registry_source(block):
    """把条目源码插到 WORKS dict 末尾（最后一个条目之后、`}` 之前）。

    行尾跟随目标文件（LF/CRLF 都吃）；锚点用正则匹配，兼容 LF 与 CRLF 两种 registry.py。
    """
    import re as _re
    rp = registry_path()
    with open(rp, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    crlf = text.count("\r\n")
    nl = "\r\n" if crlf > text.count("\n") - crlf else "\n"
    m = None
    for pat in (r"\r?\n\r?\n\r?def get\(work\)", r"\r?\ndef get\(work\)"):
        m = _re.search(pat, text)
        if m:
            break
    if not m:
        raise PackError("registry.py 结构异常：找不到 def get(work)")
    try:
        pos = text.rindex("    ),", 0, m.start()) + len("    ),")
    except ValueError:
        raise PackError("registry.py 结构异常：WORKS 里没有条目锚点 `    ),`")
    add = nl + block.replace("\n", nl)
    text = text[:pos] + add + text[pos:]
    with open(rp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return rp
