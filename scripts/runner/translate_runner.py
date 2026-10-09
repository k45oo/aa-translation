# -*- coding: utf-8 -*-
"""通用翻译 runner（2026-08-21）：翻译 → 定向清理。烧 token。

用法：
    python scripts/runner/translate_runner.py --work <id> [--chapters <eids|1-20|all|new>] [--force] [--no-proofread] [--workers N]

--chapters 默认 new（只译未译章；汉化/ 缺的 = 新章）：
    new        = 汉化/ 缺 _汉化.txt 的章节
    eids       = 逗号分隔 eid 列表（如 74899,65495）
    1-20       = 按原站顺序取前 20 话
    all        = 全部（配合 --force 全量重译）

--force  : 重译已有 _汉化.txt 的章（否则跳过）
--no-proofread : 禁用校对(默认开, 用 refine_aa 新校对——段级重建+术语注入, 废旧 proofread.py)'
--workers : 章节并行数（传给 aa_translate 的 AA_CHAPTER_WORKERS）

流程：aa_translate --pilot → scan_width/fix_width → 校对(refine_aa)（只处理本次章节）→ scan 复验。
     （2026-09-13 起不再自动调 dl_runner 补缺原文；缺章请先跑 dl_runner。）
     （2026-10-06 起不再生成合集 HTML——合集一律手动 `common/gen_aa_html.py --work <id>`
       或工作台「重生成合集」按钮。）
"""
import json, os, re, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRIPTS = os.path.join(ROOT, "scripts")
_COMMON = os.path.join(_SCRIPTS, "common")
_WORKS = os.path.join(_SCRIPTS, "works")
sys.path.insert(0, _COMMON)
sys.path.insert(0, _WORKS)

import registry
from aa_spawn import child_env, py_prefix   # 起子任务统一前缀（冻结 exe 走 --cli）
import task_report as TR   # 2026-08-29 任务管理器: 心跳/停止(common 已 sys.path, 直跑 no-op)
import work_pipeline as WP  # 2026-09-22 管线归属(方案 D10/D11)

# ---- 流程参数（2026-08-27 env 化, 默认=历史行为不变）----
# 2026-09-07 grilling: pass2/pass3 已从翻译链移除(漏译清理统一 refine_aa), 相关常量删除
# fix_width 文件级并发
_CLEAN_FILE_WORKERS = int(os.environ.get("AA_CLEAN_FILE_WORKERS", "4"))
# leak-fix 单行补译参数
_LEAKFIX_MAX_TOKENS = int(os.environ.get("AA_LEAKFIX_MAX_TOKENS", "600"))
_LEAKFIX_TIMEOUT = int(os.environ.get("AA_LEAKFIX_TIMEOUT", "60"))
# proofread 前置工具子进程超时(extract_dialogue)
_TOOL_TIMEOUT = 300
# 校对(refine_aa)子进程限额（2026-09-14；事故背景见 _run_refine_proofread 注释）
_REFINE_SCRIPT = os.path.join(_SCRIPTS, "fix", "refine_aa.py")
_REFINE_PER_CHAPTER = int(os.environ.get("AA_REFINE_PER_CHAPTER", "900"))     # 单章预算秒(实测 2~15min)
_REFINE_TOTAL_TIMEOUT = int(os.environ.get("AA_REFINE_TIMEOUT", "0"))         # 0=总预算按章数估算
_REFINE_IDLE_TIMEOUT = int(os.environ.get("AA_REFINE_IDLE_TIMEOUT", "1800"))  # 无新输出判挂死


def log(msg):
    print(msg, flush=True)


def _hb(phase, idx=None, total=None):
    """2026-08-29 任务管理器: 阶段心跳(不经管理器直跑时 no-op)。"""
    TR.heartbeat(phase=phase, idx=idx, total=total, force=True)


def _stop_check(log):
    """两档停止检测(阶段/章边界): 见标志即干净退出 RC_STOPPED(进度保留可续跑)。"""
    mode = TR.check_stop()
    if mode:
        log(f"[task] 收到停止请求({mode}), 在安全边界退出(已译进度保留, 可续跑)")
        raise SystemExit(TR.RC_STOPPED)


def _normalize_eid(e):
    m = re.search(r"(\d+)", str(e))
    return m.group(1) if m else str(e)


def _local_tr_eids(cfg):
    """汉化/ 目录已有 _汉化.txt 的 eid 集合。"""
    out = set()
    d = cfg.get("out_dir")
    if not d or not os.path.isdir(d):
        return out
    for fn in os.listdir(d):
        m = re.search(r"(\d+)", fn)
        if m and fn.endswith("_汉化.txt"):
            out.add(m.group(1))
    return out


def _local_aa_eids(cfg):
    """aa/ 目录已有 .txt 的 eid 集合（原始提取层）。"""
    out = set()
    d = cfg.get("aa_dir")
    if not d or not os.path.isdir(d):
        return out
    for fn in os.listdir(d):
        m = re.search(r"(\d+)", fn)
        if m and fn.endswith(".txt"):
            out.add(m.group(1))
    return out


def _order_file_eids(cfg):
    """work/chapter_order.json 的 eid 顺序（原站顺序，dl_runner 生成）；无则 None。"""
    wd = cfg.get("work_dir")
    if not wd:
        return None
    try:
        order = json.load(open(os.path.join(wd, "chapter_order.json"), encoding="utf-8")).get("order") or []
    except Exception:
        return None
    out = []
    for x in order:
        e = _normalize_eid(x)
        if e.isdigit() and e not in out:
            out.append(e)
    return out or None


def load_entries(cfg):
    """章节清单 = **本地 aa/ 目录已有原文的章节**（原站顺序优先，见 chapter_order.json）。

    2026-09-13：翻译不再实时拉 yaruobook/r401 分类页。下载已与翻译解耦，且
    aa_translate 只处理 aa/ 里存在的 txt——实时清单给出的「原站有但本地没下」的章
    根本译不了，白白每次拉 1000+ 条（真・女神転生... 实测 1030 条）：既慢又让
    `--chapters new` 选出一堆空转章。缺原文章请先跑 dl_runner / 工作台「更新」。
    """
    local = _local_aa_eids(cfg)
    if not local:
        return None
    order = _order_file_eids(cfg) or []
    ordered = [e for e in order if e in local]
    rest = sorted(local - set(ordered), key=int)
    log(f"本地 aa/ 章节 {len(local)} 条（原站顺序 {len(ordered)} + 补 {len(rest)}"
        + (f"；顺序源 chapter_order.json" if order else "；无 chapter_order.json，按 eid 升序") + "）")
    return [{"eid": e} for e in ordered + rest]


def select_eids(cfg, spec):
    """解析 --chapters 为 eid 列表。None 表示 keep（跳过已有）。"""
    entries = load_entries(cfg)
    all_eids = [_normalize_eid(e["eid"]) for e in entries] if entries else None
    if all_eids is None:
        # 无清单：fallback 到 aa/ 目录现有章节
        all_eids = sorted(_local_aa_eids(cfg), key=int)
    if spec in (None, "", "new"):
        tr = _local_tr_eids(cfg)
        return [e for e in all_eids if e not in tr], entries
    if spec == "all":
        return list(all_eids), entries
    if re.fullmatch(r"\d+-\d+", spec):
        a, b = map(int, spec.split("-"))
        if b < a:
            a, b = b, a
        return list(all_eids[a - 1:b]), entries
    return [e.strip() for e in spec.split(",") if e.strip()], entries


def run(cmd, cwd=None, env=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    if r.stdout:
        for line in r.stdout.strip().splitlines()[-30:]:
            log("  " + line)
    if r.stderr:
        for line in r.stderr.strip().splitlines()[-8:]:
            log("  ERR " + line)
    if r.returncode != 0:
        if r.returncode == TR.RC_STOPPED:
            raise SystemExit(TR.RC_STOPPED)   # 2026-08-29 用户停止透传(嵌套 dl_runner/aa_translate)
        raise SystemExit(f"命令失败 [{r.returncode}]: {' '.join(cmd)}")
    return r


def gen_worker(cmdlist, cwd=None):
    """生成合集（可能耗时，返回结果）"""
    return run(cmdlist, cwd=cwd)


def _run_translate(work, cfg, env, pilot, log):
    """跑 aa_translate --work --pilot 子进程（流式透传）。

    流式透传(2026-09-01): Popen + readline 边跑边写 log, stderr 独立线程收防死锁。
    退出码透传(2026-09-07 曾有的审查防护重试轮次已移除——不再需要)。
    返回: 子进程退出码
    """
    proc = subprocess.Popen(
        py_prefix(["-u"]) + [os.path.join(_SCRIPTS, "aa_translate.py"),
         "--work", work, "--pilot", pilot], cwd=_SCRIPTS,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        encoding="utf-8", errors="replace", bufsize=1)
    _err_lines = []
    def _drain_err():
        for ln in proc.stderr or []:
            _err_lines.append(ln)
    import threading as _th
    _t = _th.Thread(target=_drain_err, daemon=True)
    _t.start()
    for line in proc.stdout or []:
        line = line.rstrip("\r\n")
        if line:
            log("  " + line)
    proc.wait()
    _t.join(timeout=2)
    for ln in _err_lines[-8:]:
        if ln.strip():
            log("  ERR " + ln.rstrip("\r\n"))
    return proc.returncode
def main():
    args = sys.argv[1:]
    work, chapters = None, "new"
    force = "--force" in args
    ordered = "--ordered" in args
    no_prep = "--no-prep" in args
    no_iter = "--no-iter" in args
    # 2026-09-07: 校对默认启用(用 refine_aa 新校对——段级重建+术语注入, 不是旧 proofread 词级
    #   替换顶歪 mixed art 段)。显式 --no-proofread 才禁用(供不需要时手动关)。
    #   旧 proofread.py 已废弃(2026-09-07)。
    do_proofread = "--no-proofread" not in args
    workers = int(os.environ.get("AA_CHAPTER_WORKERS", "3"))
    if os.environ.get("AA_FREE_MODE") in ("1", "true", "True"):
        workers = 1   # 2026-08-26 免费模式: 章节串行防限流
    if "--work" in args:
        i = args.index("--work")
        work = args[i + 1] if i + 1 < len(args) else None
    if "--chapters" in args:
        i = args.index("--chapters")
        chapters = args[i + 1] if i + 1 < len(args) else "new"
    if "--workers" in args:
        i = args.index("--workers")
        if i + 1 < len(args):
            workers = int(args[i + 1])
    if not work:
        raise SystemExit("usage: translate_runner.py --work <id> [--chapters new|all|eids|1-20] [--force] [--no-proofread] [--workers N]")

    cfg = registry.get(work)
    # 2026-09-22 方案 D10: 专属管线的作品（pipeline=pokemon）拒绝走通用链。
    #   豁免：专属 runner 内部交接（AA_PIPELINE_OWNER=pokemon）或人工逃生口 AA_ALLOW_GENERIC=1。
    _pl = WP.pipeline_of(work)
    if _pl != WP.GENERIC:
        _owner = WP.owner_process()
        _allow = os.environ.get("AA_ALLOW_GENERIC") in ("1", "true", "True")
        if not _allow and _owner != _pl:
            raise SystemExit(
                f"[pipeline] 作品「{work}」归属专属链 pipeline={_pl}，通用 translate_runner 拒绝执行。\n"
                f"  正确入口：python scripts/works/pokemon_runner.py --work {work} --chapters <...>\n"
                f"  （工作台：任务管理器 → 新建 → 类型选 pokemon）\n"
                f"  确认要用通用链重跑（不推荐，会绕过固定内容表与 wiki 术语绑定）：\n"
                f"    AA_ALLOW_GENERIC=1 python scripts/runner/translate_runner.py --work {work} ...")
    # 2026-09-15 P3 TM: 翻译前**增量回填** TM（只收已定稿章 = 有 refine_ckpt；零 API）。
    # 目标章的已定稿行会被后续章复用（AA_USE_TM=1 时每行零调用直出）。
    try:
        import aa_tm as _TM
        if os.environ.get("AA_TM_NO_BUILD") not in ("1", "true", "True"):
            _st = _TM.build(work, quiet=True)
            if _st.get("kept"):
                print(f"[tm] 回填 {_st['kept']} 行（TM 共 {_st.get('entries', 0)} 条）", flush=True)
    except Exception as _e:
        pass
    # 2026-08-22 后端统一: 未显式设 AA_BACKEND 时按作品 r18 自动选后端(deepinfra 首选+deepseek 兜底),
    # 并让翻译/清理(fix_width 直接 import 调函数/refine_aa 子进程)都走 call_api→call_with_fallback(自动降级)。
    # 2026-08-25 修复: low r18_level → 不设全局 AA_BACKEND(章节级 prep 驱动按章选链);
    #   仅 high 或无 prep 时按作品级设。
    # 2026-08-26: AA_FREE_MODE=1 时不设 AA_BACKEND(免费链接管, 避免误导日志/意外回退付费)
    if not os.environ.get("AA_BACKEND") and os.environ.get("AA_FREE_MODE") not in ("1", "true", "True"):
        try:
            import json as _j3
            _lvl3 = ""
            try:
                _m3 = _j3.load(open(os.path.join(ROOT, "aa_web_app", "data", "work_meta.json"), encoding="utf-8"))
                _lvl3 = (_m3.get(work, {}) or {}).get("r18_level", "")
            except Exception:
                pass
            # 2026-08-26 四态分级: 仅空 level(=no,非R18)设 AA_BACKEND(normal 链首);
            #   high/medium/low 由每章 _chapter_work_r18 判定驱动, 不设全局覆盖。
            # low 也走 normal 链首(章节级判定驱动: yes 章 low_r18 → r18 链兜底)。
            _is_no3 = _lvl3 not in ("high", "low", "medium")
            if _is_no3:
                import aa_config as _AC
                _b, _m = _AC.auto_backend(bool(cfg.get("r18")))
                os.environ["AA_BACKEND"] = _b
                _m_env = _AC.model_env(_b)
                os.environ.setdefault(_m_env, _m)
                print(f"[auto-backend] {work} -> {_b} / {_m}", flush=True)
            else:
                # 2026-08-28 修复: high/medium 也设 AA_BACKEND=commandcode(r18 链首)。
                #   不设的代价: backend()=cloud → call_api 走云端直接模式(mimo-v2.5),
                #   mimo 对 50 行大块输出不全 → 大量漏译(akari_haisin 19.9% 中文行事故)。
                #   设 commandcode → call_with_fallback 走 r18 链(muse-spark 主力+自动降级)。
                #   low: 走 normal 链首(章节级判定驱动 yes→r18 链兜底)。
                if _lvl3 == "low":
                    # low: 设 normal 链首(章节级判定驱动: yes 章 low_r18→r18 链兜底)
                    import aa_config as _AC
                    _b, _m = _AC.auto_backend(False)   # normal 链首
                    os.environ["AA_BACKEND"] = _b
                    if _m:
                        os.environ.setdefault(_AC.model_env(_b), _m)
                    print(f"[auto-backend] {work} low/章节级判定 -> {_b}/{_m}(normal链首)", flush=True)
                else:
                    # high/medium: 显式设 r18 链首(commandcode), 避免 cloud 直连 mimo 漏译
                    import aa_config as _AC2
                    _b2, _m2 = _AC2.auto_backend(True)   # r18 链首
                    os.environ["AA_BACKEND"] = _b2
                    if _m2:
                        os.environ.setdefault(_AC2.model_env(_b2), _m2)
                    print(f"[auto-backend] {work} {_lvl3}/章节级判定 -> {_b2}/{_m2}(r18链首, 防cloud直连mimo漏译)", flush=True)
        except Exception as _e:
            print(f"[auto-backend] 选后端失败({_e}), 用默认", flush=True)
        # 2026-09-15: 上面设的是**自动**选的后端(非用户手动指定) → 打标记:
        #   aa_backend 的章内 pin / aa_prompt_prefix 只在用户真显式指定 AA_BACKEND 时才让位。
        if os.environ.get("AA_BACKEND"):
            os.environ["AA_BACKEND_AUTO"] = "1"
    import _log
    _log.init(cfg.get("work_dir", os.path.join(ROOT, "temp")),
              None if "--log" not in args else args[args.index("--log") + 1])
    global log  # rebind 模块级 log：print + 写日志文件
    log = _log.make_log()
    log(f"=== translate_runner [{work}] {cfg.get('title')} ===")
    t0 = time.time()

    # 1. 选择章节
    sel_eids, entries = select_eids(cfg, chapters)
    # 用户显式指定的章节范围（过滤已译前），only_cleanup 时用于限定清理范围
    requested_eids = {_normalize_eid(e) for e in sel_eids} if chapters not in (None, "", "new", "all") else None
    if force:
        # --force：重译指定章（含已译）。显式 eids/all 时**直接用目标清单**——绝不与
        # 实时清单/本地求交（求交会因 eid 口径不一致静默清空勾选章 → only_cleanup
        # 误对全部已译章跑清理 = "强制重译跑了所有章节" bug, 2026-09-07）。
        # 仅默认 spec(=new, 未指定章节)时退化为全部本地 aa 章。
        if chapters in (None, "", "new"):
            sel_eids = sorted(_local_aa_eids(cfg), key=int)
        # spec == all 时 sel_eids 已是全部清单；显式 eids 保持用户勾选原值。
    else:
        # 非 force：所有模式都过滤已译（显式 eids 也只在未译时处理）
        tr = _local_tr_eids(cfg)
        sel_eids = [e for e in sel_eids if e not in tr]
    sel_eids = sorted({_normalize_eid(e) for e in sel_eids}, key=int)
    if ordered:
        order_path = os.path.join(cfg.get("work_dir", ""), "chapter_order.json")
        order = []
        try:
            order = json.load(open(order_path, encoding="utf-8")).get("order", [])
        except Exception:
            pass
        if order:
            sel_set = set(sel_eids)
            sel_eids = [e for e in order if e in sel_set] + sorted(sel_set - set(order), key=int)
    # ——清理可续跑（2026-08-22）：无新译章但有已译章时，转入"仅清理+生成"——
    only_cleanup = False
    if not sel_eids:
        done = sorted(_local_tr_eids(cfg), key=int)
        if requested_eids is not None:
            # 显式指定章节时仅清理这些章，不全量
            done = [e for e in done if e in requested_eids]
        if done:
            log(f"无待翻译章节；对已译 {len(done)} 话仅做清理+生成（--force 可重译）")
            sel_eids = done
            only_cleanup = True
        else:
            log("无待翻译章节且无已译章。")
            return 0
    log(f"待处理: {len(sel_eids)} 话 -> {sel_eids}")

    # 0. 不再自动补下载（2026-09-13）：翻译只管翻译，原文缺章由 dl_runner / 工作台
    #    「更新」单独跑；缺原文的章会在翻译阶段报错。

    # 1.5 章节预处理（零 API）：本章术语统计 + R18 文本判定 + prep json
    if not no_prep and not only_cleanup:
        try:
            import chapter_prep as CP
            _hb("prep", 0, len(sel_eids))
            log("=== 章节预处理 (chapter_prep) ===")
            CP.prep_work(cfg, sel_eids, work=work)
        except Exception as e:
            log(f"  [prep] 失败(不阻塞): {e}")
    # 1.6 章节简介生成（2026-08-27 作品知识库）：翻译前为本次章节补章节简介（增量 skip 已有键）
    #   未译章翻译时 system 注入本章简介；已译章跳过（不重译）。失败不阻塞。
    if not only_cleanup:
        try:
            _hb("简介", 0, len(sel_eids))
            log("=== 章节简介 (gen_chapter_summaries) ===")
            sys.path.insert(0, os.path.join(_SCRIPTS, "common"))
            import gen_chapter_summaries as GCS
            _eids_str = ",".join(sel_eids)
            GCS.gen_summaries(work, force=False, chapters=_eids_str)
        except Exception as e:
            log(f"  [summary] 失败(不阻塞): {e}")
    # 1.7 作品术语表检查（2026-09-06 用户规则: 术语表只手动触发, 翻译前不再自动生成/刷新）
    #   翻译前必须已有作品术语表(glossary_<work>.json 有 manual 或 auto 条目)——缺失/空表
    #   时中止并提示用户先手动生成(工作台 work 页「生成术语候选…」按钮 → 候选确认 →
    #   「写入作品表」; 或 CLI gen_glossary_ai --work <id> --promote)。
    if not only_cleanup:
        try:
            _hb("术语表", 0, 1)
            log("=== 作品术语表检查 ===")
            sys.path.insert(0, os.path.join(_SCRIPTS, "common"))
            import gen_work_glossary as GWG
            _gl_path = GWG.work_glossary_path(cfg["work_dir"], work)
            _has = False
            if os.path.exists(_gl_path):
                try:
                    import json as _j
                    _d = _j.load(open(_gl_path, encoding="utf-8"))
                    _has = len(_d.get("glossary", [])) > 0
                except Exception:
                    _has = False
            if not _has:
                raise SystemExit(
                    f"[glossary] 无作品术语表 {_gl_path}\n"
                    f"  请先手动生成:\n"
                    f"  ① 快速派生(零 token, 从全局角色表/术语表派生 auto):\n"
                    f"     python scripts/common/gen_work_glossary.py --work {work} --force\n"
                    f"  ② 完整流程(AI 提取+人工确认): 工作台 work 页「生成术语候选…」→\n"
                    f"     候选确认 →「写入作品表」, 或 CLI:\n"
                    f"     python scripts/common/gen_glossary_ai.py --work {work} --full\n"
                    f"  翻译已中止(术语表是硬前置)。")
            log(f"  [glossary] 作品术语表存在 ✓ ({_gl_path})")
        except SystemExit as _e:
            log(str(_e))
            sys.exit(_e.code if isinstance(_e.code, int) else 3)
        except Exception as e:
            log(f"  [glossary] 检查失败(不阻塞): {e}")
    # 2. 翻译
    _stop_check(log)
    _hb("翻译", 0, len(sel_eids))
    if only_cleanup:
        log(f"=== 仅清理+生成（跳过翻译，{len(sel_eids)} 话已译）===")
    else:
        log("=== 翻译 (aa_translate) ===")
    env = child_env()       # 子任务环境（冻结 exe 下带上 AA_BASE/AA_ENV）
    env["AA_CHAPTER_WORKERS"] = str(workers)
    pilot = ",".join(sel_eids)
    if force:
        # --force 时不能靠"跳过已有"，直接用 --pilot 全译（aa_translate 遇到已有也会覆盖？）
        # aa_translate --pilot 会过滤已有 _汉化.txt —— 为支持 force，需临时改名已有文件。
        # 简化：force 语义 = 把 --pilot 改为只对未译的，已有章由清理阶段统一处理。
        # 实际实现：aa_translate 不支持强制重译，这里对已有章先改名 .bak 再译再恢复。
        out_dir = cfg["out_dir"]
        work_dir = cfg["work_dir"]
        bak = {}
        for eid in sel_eids:
            # 找到 entry_<eid>_汉化.txt
            for fn in os.listdir(out_dir):
                m = re.search(r"(\d+)", fn)
                if m and m.group(1) == eid and fn.endswith("_汉化.txt"):
                    bk = fn + ".bak"
                    os.replace(os.path.join(out_dir, fn), os.path.join(out_dir, bk))
                    bak[os.path.join(out_dir, bk)] = os.path.join(out_dir, fn)
                    break
            # 2026-09-05 fix: force 必须同时移走 ckpt——aa_translate 续跑靠
            # work/entry_<eid>.txt.ckpt.json(不是汉化文件), 不移 ckpt 则 done 全载入
            # → 主块 0 chunks, force 退化成只翻 mixed 段(整章重译失效根因)
            ckpt_p = os.path.join(work_dir, "entry_%s.txt.ckpt.json" % eid)
            if os.path.exists(ckpt_p):
                os.replace(ckpt_p, ckpt_p + ".bak")
                bak[ckpt_p + ".bak"] = ckpt_p
        try:
            _rc = _run_translate(work, cfg, env, pilot, log)
            if _rc == TR.RC_STOPPED:
                raise SystemExit(TR.RC_STOPPED)
        finally:
            for bk, orig in bak.items():
                if os.path.exists(bk) and not os.path.exists(orig):
                    os.replace(bk, orig)
    elif not only_cleanup:
        _rc = _run_translate(work, cfg, env, pilot, log)
        if _rc == TR.RC_STOPPED:
            raise SystemExit(TR.RC_STOPPED)
    # (only_cleanup 时跳过 aa_translate——已译章不进模型重译)

    # 3. 定向清理（只处理本次章节）
    tag_env = dict(os.environ)
    # 2026-08-24 清理按作品级 r18_level 定后端链: high→AA_R18=1(r18链), low→0(normal链,免费),
    #   空→保持 env。清理是修已译文本不走新R18内容, 精确到章无必要。
    #   注意: 清理模块函数(fix_width 直接 import)同进程内直接 import 调 call_api → 读 os.environ,
    #   不能只放 tag_env(那是子进程 env)。
    try:
        import json as _json2
        _meta2 = _json2.load(open(os.path.join(ROOT, "aa_web_app", "data", "work_meta.json"), encoding="utf-8"))
        _lvl = (_meta2.get(work, {}) or {}).get("r18_level", "")
        # 2026-08-26 四态分级: 清理是修已译文本不走新R18内容。
        #   high→AA_R18=1(r18链); medium/low/空→0(normal链,免费)
        if _lvl == "high":
            os.environ["AA_R18"] = "1"
        else:
            os.environ["AA_R18"] = "0"
        print(f"[cleanup-backend] {work} r18_level={_lvl} AA_R18={os.environ.get('AA_R18', '(env默认)')}", flush=True)
    except Exception:
        pass
    target_files = sorted(f"entry_{e}_汉化.txt" for e in sel_eids)
    if cfg.get("name_style") == "eid_title":
        # 白头系文件名是 <eid>_<标题>_汉化.txt，按 eid 前缀匹配
        target_files = []
        for fn in os.listdir(cfg["out_dir"]):
            m = re.search(r"^(\d+)", fn)
            if m and m.group(1) in set(sel_eids) and fn.endswith("_汉化.txt"):
                target_files.append(fn)
        target_files.sort()

    # 定向清理（只处理本次章节）——2026-09-07 grilling: 去掉 pass2/pass3(砍mixed后只剩
    # text 漏译, 与 refine 重叠且整行送模型清不动 art 密集行), 漏译清理统一 refine_aa 段级。
    import fix_width as FW

    _stop_check(log)
    _hb("清理", 0, len(target_files))
    log("=== scan_width (本次章) ===")
    _scan_width(work, target_files, tag_env)

    log("=== 定向 fix_width ===")
    tag_env["AA_USAGE_TAG"] = "fix_width"
    _dir_fix_width(FW, cfg, target_files, tag_env)

    log("=== scan_width 复验 ===")
    _scan_width(work, target_files, tag_env)

    # 3.5 迭代术语更新（零 API）：每章后候选写入 glossary_iter.json
    #    only_cleanup 时跳过（无新译文本，术语候选已被先前 run 生成过）
    if not no_iter and not only_cleanup:
        try:
            import iter_glossary as IG
            _hb("术语迭代")
            log("=== 迭代术语更新 (iter_glossary) ===")
            IG.iter_work(cfg, sel_eids, use_ai=False)
        except Exception as e:
            log(f"  [iter] 失败(不阻塞): {e}")
    # 3.6 校对(2026-09-07 grilling: 唯一漏译清理链——refine_aa 段级重建覆盖 text 漏译+
    #   mixed 残留+art 还原, 取代 pass2/pass3; only_cleanup 也跑(已译章清理场景正是 refine 用武之地)。
    #   旧注释: proofread 词级替换顶歪 art 段(17808), 已废弃; refine_aa 段级安全, 默认开。
    proofread_err = None
    if do_proofread:
        try:
            _stop_check(log)
            _hb("校对", 0, len(sel_eids))
            log("=== 校对 (refine_aa) ===")
            _run_refine_proofread(work, cfg, sel_eids, log)
        except SystemExit:
            raise
        except Exception as e:
            # 2026-09-14: 校对没跑完不再静默 rc=0（曾致「任务 done 却有 8 章没校对」）。
            #   后续阶段(角色残留扫描/合集)照跑, 收尾前按失败上报 rc≠0。
            proofread_err = str(e)
            log(f"[refine-proofread] 失败: {e}")
            log("[refine-proofread] ⚠️ 校对阶段未完成——翻译产物完好, 任务按失败上报(补跑命令见上)")
    # 3.7 全局角色名残留扫描（2026-08-28 接入, 零 API 零 token）:
    #   汉化文本残留全局角色日文名(未译) → 写 <work_dir>/role_residual.txt 报告
    try:
        import scan_role_residual as SRR
        log("=== 角色名残留扫描 ===")
        hits, work_dir = SRR.scan_work(work, SRR.load_global_roles())
        if hits:
            log(f"  角色名残留 {len(hits)} 处 → {os.path.join(work_dir, 'role_residual.txt')}（见报告, 人工处理）")
        else:
            log("  角色名残留 0 处 ✓")
    except Exception as e:
        log(f"[role_residual] 失败(不阻塞): {e}")
    # 4. 生成合集（2026-10-06 用户指定：不自动生成, 一律手动跑 common/gen_aa_html.py
    #    或工作台「重生成合集」按钮）

    log(f"=== translate_runner DONE in {time.time()-t0:.0f}s ===")
    if proofread_err:
        # 翻译/清理产物已完整落盘, 仅校对阶段未跑完 —— 按失败上报(rc≠0), 卡片显红可见。
        log(f"=== ⚠️ 校对阶段未完成: {proofread_err} ===")
        log("=== 翻译产物完好, 补跑校对: task_manager.py enqueue --type proofread --work "
            f"{work} --chapters <未校对eid> ===")
        return 1
    return 0


class ProofreadIncomplete(RuntimeError):
    """校对(refine_aa)子进程超时/异常终止——阶段未跑完（上层按失败上报，见 main 尾部）。"""


def _refine_missing(eids, seen_bases):
    """refine_aa 每章打印 `[entry_<eid>] ...`（无问题/三类修复都打）——据此算出未处理到的章。

    用于失败时给出「未校对 eid 列表」供精确补跑；正常跑完应为空。
    """
    return [e for e in eids if f"entry_{e}" not in seen_bases]


def _run_stream_proc(cmd, cwd, log, on_line=None, total_limit=None, idle_limit=None):
    """流式跑子进程 + 活性超时（2026-09-14）。

    stdout 逐行**实时**透传（先 on_line 回调、再 log），stderr 起独立 daemon 线程收尾
    （防管道填满死锁）；看门狗线程按两个阈值杀进程：① 总时长 total_limit；
    ② 无新输出 idle_limit（真挂死/半开连接）。返回 (rc, killed_reason, err_tail)，
    killed_reason=None = 正常跑完。

    为何不用 `subprocess.run(capture_output=True, timeout=N)`：全程输出吞在管道里，
    超时/异常路径一行都拿不到 → 阶段日志整段缺失（校对阶段「日志不全」根因）。
    """
    import threading
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            encoding="utf-8", errors="replace", bufsize=1)
    err_lines = []
    t0 = time.time()
    _state = {"why": None}
    _last = {"ts": t0}

    def _drain_err():
        for ln in proc.stderr or []:
            err_lines.append(ln.rstrip("\r\n"))

    def _watch():
        while proc.poll() is None:
            time.sleep(0.5)
            now = time.time()
            why = None
            if total_limit and now - t0 > total_limit:
                why = f"总时长超 {total_limit}s"
            elif idle_limit and now - _last["ts"] > idle_limit:
                why = f"无新输出超 {idle_limit}s"
            if why:
                _state["why"] = why
                try:
                    proc.kill()
                except Exception:
                    pass
                return

    tw = threading.Thread(target=_watch, daemon=True)
    te = threading.Thread(target=_drain_err, daemon=True)
    te.start()
    tw.start()
    for line in proc.stdout or []:
        line = line.rstrip("\r\n")
        _last["ts"] = time.time()
        if on_line:
            try:
                on_line(line)
            except Exception:
                pass
        if line:
            log("  " + line)
    rc = proc.wait()
    te.join(timeout=2)
    tw.join(timeout=1)
    for _p in (proc.stdout, proc.stderr):    # 关管道免 ResourceWarning/句柄泄漏
        try:
            _p.close()
        except Exception:
            pass
    return rc, _state["why"], err_lines[-8:]


def _run_refine_proofread(work, cfg, sel_eids, log):
    """校对环节(2026-09-07 换 impl, 废旧 proofread.py): 对本次章节跑 refine_aa --write。

    refine_aa = 段级重建+术语注入的新校对(refine_aa.py), 逐章三类修复:
    错位修正(段级重建)/漏译补译(swallow)/错译还原(纯 art 行还原 SRC)。
    自带 work/backup_refine_aa_<ts>/ 备份。

    2026-09-14 修复（やる夫のハーレム大王国 20 章事故）——原实现三宗罪：
      ① `timeout=3600` 总时长硬上限与单章预算严重错配（本作单章 150~3500 行、refine 调用
         密集，20 章实测需 ~95min），跑在第 12 章被砍；
      ② 超时被上层当「不阻塞」吞掉 → runner rc=0，任务报 done，卡片「待译 8」其实是
         8 章从未校对；
      ③ capture_output 把全程 stdout 吞在管道里、超时路径一行不打 → 校对阶段日志整段缺失。
    现改：逐行实时透传；总预算 = AA_REFINE_TIMEOUT 或 max(3600, 单章预算×章数)；到点或
    长时间无输出即杀，并抛 ProofreadIncomplete（上层记失败、rc≠0），日志列出「未校对 eid」
    供精确补跑（独立 proofread 任务直跑 refine_aa，不经本包装）。
    """
    eids = [str(e) for e in sel_eids]
    n = len(eids)
    total = _REFINE_TOTAL_TIMEOUT or max(3600, _REFINE_PER_CHAPTER * n)
    cmd = py_prefix(["-u"]) + [_REFINE_SCRIPT, work, "--eids", ",".join(eids), "--write"]
    log(f"  refine_aa: {n} 章, 总预算 {total}s / 无输出上限 {_REFINE_IDLE_TIMEOUT}s")
    seen = set()

    def _on_line(line):
        m = re.match(r"\[(entry_\d+)\]", line)
        if m:
            seen.add(m.group(1))

    rc, killed, err_tail = _run_stream_proc(cmd, _SCRIPTS, log, on_line=_on_line,
                                            total_limit=total, idle_limit=_REFINE_IDLE_TIMEOUT)
    for ln in err_tail:
        if ln.strip():
            log("  ERR " + ln)
    missing = _refine_missing(eids, seen)
    if killed:
        log(f"[refine-proofread] ⏱ {killed} → 已校对 {n - len(missing)}/{n} 章")
    if rc == TR.RC_STOPPED:
        log("[refine] 收到停止请求, 校对在章节边界退出")
        raise SystemExit(TR.RC_STOPPED)
    if killed or rc != 0 or missing:
        why = killed or (f"退出码 {rc}" if rc != 0 else "未跑到全部章节")
        if missing:
            log(f"[refine-proofread] 未校对 {len(missing)}/{n} 章: {','.join(missing)}")
            log(f"[refine-proofread] 补跑: python aa_web_app/task_manager.py enqueue "
                f"--type proofread --work {work} --chapters {','.join(missing)}")
        log(f"[refine-proofread] ✗ 校对未完成({why})——翻译产物完好, 任务按失败上报")
        raise ProofreadIncomplete(f"{why}; 未校对 {len(missing)}/{n} 章")
    log(f"  refine_aa 退出码 0 ✓ ({n} 章全部校对完成)")


def _dir_fix_width(FW, cfg, target_files, env):
    """定向 fix_width：只修本次章节的宽度错位行。"""
    import concurrent.futures as cf
    AA_DIR = cfg["aa_dir"]
    OUT_DIR = cfg["out_dir"]
    WORK_DIR = cfg["work_dir"]
    tgt_src = {f.replace("_汉化.txt", ".txt") for f in target_files}
    rows = [r for r in FW.collect(AA_DIR, OUT_DIR) if r[0] in tgt_src]
    log(f"  collect {len(rows)} mismatch rows (本次章节)")
    if not rows:
        return
    by_file = {}
    for r in rows:
        by_file.setdefault(r[0], []).append(r)
    all_fixed, all_failed = {}, []
    for fname, frows in by_file.items():
        try:
            fixed, failed = FW.fix_batch(frows)
        except Exception as e:
            log(f"  {fname}: fix_batch 异常 {e}")
            continue
        dst = os.path.join(OUT_DIR, fname.replace(".txt", "_汉化.txt"))
        lines = open(dst, encoding="utf-8").read().split("\n")
        for idx, nl in fixed.items():
            lines[idx] = nl
        open(dst, "w", encoding="utf-8").write("\n".join(lines))
        all_fixed.update(fixed)
        all_failed.extend(failed)
        log(f"  {fname}: fixed {len(fixed)}/{len(frows)}, failed {len(failed)}")
    if all_failed:
        with open(os.path.join(WORK_DIR, "manual_fix.txt"), "w", encoding="utf-8") as f:
            for fname, idx, src, tr in all_failed:
                f.write(f"### {fname} line {idx} (src_w={FW.C.disp_width(src)} tr_w={FW.C.disp_width(tr)})\n"
                        f"SRC: {src!r}\nTR : {tr!r}\n\n")
        log(f"  失败 {len(all_failed)} 行 -> {WORK_DIR}/manual_fix.txt")


def _scan_width(work, target_files, env):
    """scan_width 复验（2026-08-27 统一口径）：只验本次章节。

    调 common/scan_width.collect_mismatch（line_kind/allow_overwidth 唯一口径,
    与工作台 save_line 同源）——不再内联无豁免版本(旧版把 text 行译文自然变长
    也报错位, 产生假阳性), 也删除了 exec_module 加载 common 版却未使用的死代码。
    """
    import importlib.util
    spec_path = os.path.join(_COMMON, "scan_width.py")
    spec = importlib.util.spec_from_file_location("scan_width_impl", spec_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cfg = registry.get(work)
    src_files = [f.replace("_汉化.txt", ".txt") for f in target_files]
    rows, rows_artalign, (n_names, skipped, count_mismatch) = mod.collect_mismatch(
        cfg["aa_dir"], cfg["out_dir"], files=src_files,
        check_art_align=True, WORK_DIR=cfg["work_dir"])
    for fname, ns, nt in count_mismatch:
        log(f"  !! LINE COUNT MISMATCH {fname}: src={ns} tr={nt}")
    if rows_artalign:
        # 2026-09-05: art 段列位失配(宽度对但图形被压缩/左移)——跑 fix_mixed_drift 重建
        log(f"  !! ART-ALIGN {len(rows_artalign)} 行 mixed art 列位失配"
            " -> 需 scripts/fix/fix_mixed_drift.py 重建")
        for fname, i in rows_artalign[:20]:
            log(f"  ART[{fname} L{i+1}] art 列位与原文不符")
    if rows:
        for fname, i, wa, wb in rows[:20]:
            mark = "SHORT" if wb < wa else "LONG"
            log(f"  WIDTH[{mark}] {fname} L{i+1}: src_w={wa} tr_w={wb}（art 行超宽/缺宽）")
        if len(rows) > 20:
            log(f"  ... 其余 {len(rows) - 20} 行见报告")
        log(f"  scan_width: {len(rows)} 行错位（仍有问题，请人工复查 manual_fix.txt）")
    else:
        log(f"  scan_width: 0 错位 ✓")


if __name__ == "__main__":
    _rc = 1
    try:
        _rc = main() or 0
    except SystemExit as _e:
        _rc = _e.code if isinstance(_e.code, int) else 1
        raise
    finally:
        try:
            TR.finalize(_rc)
        except Exception:
            pass
    # 2026-09-14: main() 的非零返回必须变成**进程退出码**——否则任务管理器 _watch 读
    #   proc.returncode=0 → 标 done（校对失败却报完成的根因之一，实测 rc=1 只写进心跳）。
    sys.exit(_rc)

