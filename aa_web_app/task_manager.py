# -*- coding: utf-8 -*-
"""统一任务管理器（2026-08-29，方案 doc/方案归档/翻译进程管理方案_2026-08-29.md）。

职责：翻译/下载 runner 任务的注册表 + FIFO 队列 + 调度（全局并行上限）+
两档优雅停止（章节边界/块边界）+ 落盘持久化（重启接管）+ **定时启动**（2026-09-29）。

单一事实源 = 注册表文件（aa_web_app/data/tasks/，可用 AA_TASKS_DIR 覆盖）：
    <task_id>.json            任务记录（管理器所有）
    .hb_<task_id>.json        心跳 sidecar（runner 进程所有，task_report 写）
    .stop_<task_id>.chapter   停止标志：译完本章后停
    .stop_<task_id>.asap      停止标志：尽快停（块边界）
    queue.json                {"queue": [task_id, ...]} FIFO
    .scheduler.lock           {"pid": N, "ts": ...} 调度器单例锁

status 取值：queued | scheduled（定时等待中，非终态）| starting | running | stopping |
             done | failed | stopped | crashed | cancelled | stalled

入口：web（main.py 路由）/ CLI（python aa_web_app/task_manager.py ...）/ agent。
调度器全局只有一个（pid 锁）：工作台内嵌线程 或 CLI 拉起的独立进程。

runner 协议（scripts/common/task_report.py 实现）：
    env 传入 AA_TASK_ID / AA_TASK_HB / AA_STOP_CHAPTER_FILE / AA_STOP_ASAP_FILE
    runner 周期写心跳（phase/chapter_idx/chapter_total），退出前写 rc；
    轮询停止标志文件，见到即在约定边界干净退出，退出码 RC_STOPPED=41。
"""
import json
import os
import re
import subprocess
import sys
import threading
import time

_PKG = os.path.dirname(os.path.abspath(__file__))        # aa_web_app/
ROOT = os.path.dirname(_PKG)                              # 仓库根
_SCRIPTS = os.path.join(ROOT, "scripts")
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "works")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 数据根（2026-10-09 方案 §5）：任务注册表/队列是可写状态，落数据根；AA_BASE 未设时 = 代码根。
import paths as _paths
_DATA = _paths.BASE
from aa_spawn import child_env, py_prefix   # 起子任务统一前缀（冻结 exe 走 --cli）

TASKS_DIR = os.environ.get("AA_TASKS_DIR") or os.path.join(_DATA, "aa_web_app", "data", "tasks")
# 并行上限（模块常量=默认值；运行时经 _parallel_limits() 读 data/tasks_parallel.json 可覆盖，改文件即时生效）
MAX_PARALLEL = int(os.environ.get("AA_TASK_MAX_PARALLEL", "2"))
# 2026-08-30: 纯下载并行上限 +2（下载零 token、不抢翻译配额）；翻译仍受 MAX_PARALLEL 限制
MAX_PARALLEL_DL = int(os.environ.get("AA_TASK_MAX_PARALLEL_DL", "4"))
# 2026-09-01: 免费链翻译任务并行上限(默认 1)——免费模型限流/不稳定, 多任务并发易互相拖垮
MAX_PARALLEL_FREE = int(os.environ.get("AA_TASK_MAX_PARALLEL_FREE", "1"))
_PARALLEL_CFG = os.path.join(_DATA, "aa_web_app", "data", "tasks_parallel.json")


def _parallel_limits():
    """运行时并行上限：(translate, download, free_translate)。优先 data/tasks_parallel.json
    （{translate:N, download:N, free_translate:N}），缺失字段回退模块常量。每次调度读盘——
    改配置文件即时生效，无需重启工作台/调度器。

    free_translate(2026-09-01): 免费链(AA_FREE_MODE=1)翻译任务的任务级并行上限=1——
    免费模型限流/不稳定, 多任务并发易互相拖垮; 付费翻译走 translate(默认 10)。
    免费任务单章串行(章节级=1)、章内块级并行 AA_FREE_WORKERS=10(见 _start_task 注入)。
    """
    try:
        d = json.load(open(_PARALLEL_CFG, encoding="utf-8"))
        tr = int(d.get("translate", MAX_PARALLEL))
        dl = int(d.get("download", MAX_PARALLEL_DL))
        ft = int(d.get("free_translate", MAX_PARALLEL_FREE))
        if tr >= 1 and dl >= 1 and ft >= 1:
            return tr, dl, ft
    except Exception:
        pass
    return MAX_PARALLEL, MAX_PARALLEL_DL, MAX_PARALLEL_FREE


def set_parallel(translate=None, download=None, free_translate=None):
    """写 data/tasks_parallel.json 调整并行上限（调度器每次读盘，即时生效）。

    返回 {ok, translate, download, free_translate}。不传的字段保留现值（读不到配置则用模块默认）。
    """
    try:
        cur_tr, cur_dl, cur_ft = _parallel_limits()
    except Exception:
        cur_tr, cur_dl, cur_ft = MAX_PARALLEL, MAX_PARALLEL_DL, MAX_PARALLEL_FREE
    if translate is None:
        translate = cur_tr
    if download is None:
        download = cur_dl
    if free_translate is None:
        free_translate = cur_ft
    if translate < 1 or download < 1 or free_translate < 1:
        return {"ok": False, "error": "并行上限必须 ≥1"}
    os.makedirs(os.path.dirname(_PARALLEL_CFG), exist_ok=True)
    with open(_PARALLEL_CFG, "w", encoding="utf-8") as f:
        json.dump({"translate": translate, "download": download, "free_translate": free_translate},
                  f, ensure_ascii=False, indent=1)
    return {"ok": True, "translate": translate, "download": download, "free_translate": free_translate}
# 2026-08-31 假死检测: translate 任务 usage(API 调用) 停滞超阈值判假死——自动杀+重入队(ckpt 续跑)
# 2026-09-02 阈值 20→40: 免费链退避封顶 5min×12模型遍历+重试 ≈ 最坏 27min 才到轮次上限抛异常;
#   20min 会在免费链正常退避时误杀(bar_miku 反复"1小时假死"根因——限流时段 20min 无 usage 被误判)。
#   40min > 27min 最坏退避, 真死(请求永久挂起)40min 内仍能兜住。
STALL_MINUTES = int(os.environ.get("AA_TASK_STALL_MINUTES", "40"))
STALL_MAX_RESTARTS = int(os.environ.get("AA_TASK_STALL_MAX_RESTARTS", "3"))
# 2026-09-01: 免费翻译任务章内块级并行(AA_FREE_WORKERS 注入值, 单章内 API 调用并发)。
# 章节级并行免费模式强制串行(translate_runner:261-262), 章内块级并行才是免费榨额度点。
MAX_CHAPTER_WORKERS_FREE = int(os.environ.get("AA_CHAPTER_WORKERS_FREE", "10"))
RC_STOPPED = 41                    # runner 约定：用户停止的退出码
TYPES = ("translate", "download", "proofread", "pokemon", "wiki", "board")
# 框架预留扩展(2026-09-07 +proofread: 独立校对=refine_aa; 2026-09-22 +pokemon: 宝可梦专属链;
# 2026-10-06 +wiki: 作品官方设定 wiki 本地化/连载同步 gen_work_wiki.py --update, 零模型成本;
# 2026-10-10 +board: 揭示板 dat 下载/更新 board_runner.py, 零 token)
# board 任务的 work 是**伪 id** `board:<板id>`（非 registry 作品，不下发 registry 检查；
# 同板串行 = 该站禁并发/防重复爬索引；揭示板目录名放 params["dir"]）

_LOCK = threading.RLock()          # 进程内串行化
_watch_procs = {}                  # task_id -> Popen（本进程启动的子进程）

# 终态集合
_FINAL = ("done", "failed", "stopped", "crashed", "cancelled", "stalled")


# ---------------------------------------------------------------- 基础工具

def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _pid_alive(pid):
    if not pid:
        return False
    try:
        if os.name == "nt":
            # 2026-08-29: 本机 os.kill(pid,0) 对所有 pid(含存活)抛 WinError 87, 不可用作存活检测。
            # 2026-08-31 修复: OpenProcess 对已死进程仍可能返回句柄(截断/pid复用), 加
            #   GetExitCodeProcess == STILL_ACTIVE(259) 才判活——taskkill 后立刻判定不再误报。
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not h:
                return False
            try:
                code = ctypes.c_ulong(0)
                ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
                if ok and code.value == STILL_ACTIVE:
                    return True
                return False
            finally:
                ctypes.windll.kernel32.CloseHandle(h)
        os.kill(int(pid), 0)
        return True
    except (OSError, ProcessLookupError, ValueError):
        return False


def _pid_of(path):
    """读 pid 文件，无/坏返回 0。"""
    try:
        with open(path) as f:
            return int(f.read().strip() or "0")
    except Exception:
        return 0


class _FileLock:
    """跨进程互斥（O_EXCL 占位文件）。多开 TUI / web+CLI 双入口安全。"""

    def __init__(self, path, timeout=5.0, stale=30.0):
        self.path = path + ".lk"
        self.timeout = timeout
        self.stale = stale

    def __enter__(self):
        t0 = time.time()
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > self.stale:
                        os.remove(self.path)
                        continue
                except OSError:
                    pass
                if time.time() - t0 > self.timeout:
                    raise TimeoutError(f"file lock timeout: {self.path}")
                time.sleep(0.05)

    def __exit__(self, *a):
        try:
            os.remove(self.path)
        except OSError:
            pass


def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, obj):
    """原子写：tmp + replace。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ---------------------------------------------------------------- 路径

def task_path(tid):
    return os.path.join(TASKS_DIR, tid + ".json")


def hb_path(tid):
    return os.path.join(TASKS_DIR, ".hb_" + tid + ".json")


def stop_path(tid, mode):
    return os.path.join(TASKS_DIR, f".stop_{tid}.{mode}")


def queue_path():
    return os.path.join(TASKS_DIR, "queue.json")


def scheduler_lock_path():
    return os.path.join(TASKS_DIR, ".scheduler.lock")


def _is_board_task(work, ttype=None):
    """揭示板任务判定：work = 伪 id `board:<板id>`（非 registry 作品）。"""
    return ttype == "board" or str(work or "").startswith("board:")


def board_log_path():
    """揭示板任务的 runner 日志（不挂作品目录；与作品任务互不覆盖）。

    注意 `_start_task` 会再拼一层 `runner_log.txt` → 实际落 `揭示板/runner_log.txt`。
    """
    return os.path.join(_DATA, "揭示板", "runner_log.txt")


def _work_dir_of(work, ttype=None):
    """任务日志/锁所在目录：普通作品 = registry work_dir；揭示板 = 揭示板/。"""
    if _is_board_task(work, ttype):
        return os.path.dirname(board_log_path())
    import registry
    return registry.get(work)["work_dir"]


def _legacy_lock(work, ttype=None):
    if _is_board_task(work, ttype):
        return ""                      # 揭示板任务无作品目录，不维护旧式锁
    import registry
    return os.path.join(registry.get(work)["work_dir"], ".runner.lock")


# ---------------------------------------------------------------- 任务 CRUD

def _load_task(tid):
    return _read_json(task_path(tid))


def _save_task(t):
    _write_json(task_path(t["task_id"]), t)


def _load_queue():
    return _read_json(queue_path(), {"queue": []}).get("queue", [])


def _save_queue(q):
    _write_json(queue_path(), {"queue": q})


def parse_schedule_at(v):
    """把 `schedule_at` 解析成 unix 秒；无法解析/空返回 None（2026-09-29 定时任务）。

    接受：unix 秒（数字或纯数字串）| `YYYY-MM-DD HH:MM[:SS]` | `YYYY-MM-DDTHH:MM[:SS]`（datetime-local）。
    **返回值可能早于当前时间**——调用方决定是「拒绝」还是「立即执行」（到点才提交的边界情况不该报错）。
    """
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("T", " ")
    if re.fullmatch(r"\d{9,}", s):
        return float(s)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return time.mktime(time.strptime(s, fmt))
        except ValueError:
            continue
    return None


def schedule_due(t, now=None):
    """任务是否已到定时时刻（无 schedule_at / 解析不出 → True = 可立即执行）。"""
    ts = (t.get("params") or {}).get("schedule_at")
    if not ts:
        return True
    try:
        return float(ts) <= (time.time() if now is None else now)
    except (TypeError, ValueError):
        return True


def _build_command(ttype, work, params):
    """runner 命令行（测试可 patch）。"""
    if ttype == "translate":
        cmd = py_prefix(["-u"]) + [os.path.join(_SCRIPTS, "runner", "translate_runner.py"),
               "--work", work, "--chapters", params.get("chapters") or "new"]
        if params.get("force"):
            cmd.append("--force")
        if params.get("no_proofread"):
            cmd.append("--no-proofread")   # 2026-09-07: 校对默认开, 可选禁用(refine_aa)
        # 2026-10-06: 不再有 --gen（合集一律手动生成, 见 gen_aa_html.py / 工作台按钮）
        return cmd
    if ttype == "pokemon":
        # 2026-09-22 方案 D8: 宝可梦专属链（pipeline=pokemon 的唯一合法入口）
        cmd = py_prefix(["-u"]) + [os.path.join(_SCRIPTS, "works", "pokemon_runner.py"),
               "--work", work, "--chapters", params.get("chapters") or "new"]
        if params.get("force"):
            cmd.append("--force")
        if params.get("no_proofread"):
            cmd.append("--no-proofread")
        if params.get("rebuild_table"):
            cmd.append("--rebuild-table")
        return cmd
    if ttype == "download":
        return py_prefix(["-u"]) + [os.path.join(_SCRIPTS, "runner", "dl_runner.py"),
                "--work", work, "--chapters", params.get("chapters") or "new"]
    if ttype == "proofread":
        # 2026-09-07: 独立校对任务 = refine_aa(段级重建+术语注入, 废旧 proofread.py)
        cmd = py_prefix(["-u"]) + [os.path.join(_SCRIPTS, "fix", "refine_aa.py"), work, "--write"]
        if params.get("chapters"):
            cmd += ["--eids", str(params["chapters"])]
        return cmd
    if ttype == "wiki":
        # 2026-10-06: 作品官方设定 wiki 本地化 / 连载同步（每部作品独立配置 work_wikis.json）
        # 默认 --update = 重抓原站全部页（零模型成本）+ 行级缓存增量翻译 + 重建；mode=all 走 --all。
        cmd = py_prefix(["-u"]) + [os.path.join(_SCRIPTS, "tools", "gen_work_wiki.py"),
               "--work", work]
        cmd.append("--all" if params.get("mode") == "all" else "--update")
        return cmd
    if ttype == "board":
        # 2026-10-10: 揭示板 dat 下载/更新（零 token；work = 伪 id `board:<板id>`，
        # 揭示板目录/关键词/作者/范围都在 params）
        cmd = py_prefix(["-u"]) + [os.path.join(_SCRIPTS, "runner", "board_runner.py"),
               "--board", str(params.get("board") or "FOX")]
        for key, flag in (("host", "--host"), ("match", "--match"), ("author", "--author"),
                          ("dir", "--dir"), ("title", "--title"), ("work_id", "--work"),
                          ("label", "--label")):
            if params.get(key):
                cmd += [flag, str(params[key])]
        for key, flag in (("kako_from", "--kako-from"), ("kako_to", "--kako-to")):
            if params.get(key) not in (None, ""):
                cmd += [flag, str(params[key])]
        for key, flag in (("scan_kako", "--scan-kako"), ("no_kako", "--no-kako"),
                          ("force", "--force"), ("dry_run", "--dry-run")):
            if params.get(key):
                cmd.append(flag)
        return cmd
    raise ValueError(f"未知任务类型: {ttype}")


def wiki_config_path():
    """作品 wiki 生成器配置（每部作品一块，扁平结构：顶层键=作品 id）。"""
    return os.path.join(_SCRIPTS, "config", "work_wikis.json")


def wiki_configured(work):
    """本作品是否配了官方设定 wiki（`scripts/config/work_wikis.json` 有该作品块）。

    没配置的作品不该入队 wiki 任务（否则 gen_work_wiki.py 起不来，任务白跑一趟标 failed）。
    以 `_` 开头的键（`_note`/`_字段`）是配置说明，不是作品。"""
    if not work or str(work).startswith("_"):
        return False
    try:
        cfg = _read_json(wiki_config_path()) or {}
    except Exception:
        return False
    return isinstance(cfg.get(work), dict)


def enqueue(ttype, work, params=None, _stall_retry_of=None):
    """唯一启动入口。返回 {ok, task_id|error}。同作品串行（排队/运行中即拒绝）。
    _stall_retry_of(2026-08-31): 假死自动重入队来源 task_id——豁免"该任务已标 stalled"的同作品占用检查。
    2026-09-29 定时任务: `params["schedule_at"]`（unix 秒 / ISO 串）在未来 → status=`scheduled`（非终态，
    **不占同作品串行**，到点由 scheduler_tick 转 queued 再按 FIFO 启动）；已过期/解析不出 = 立即排队。"""
    params = params or {}
    if ttype not in TYPES:
        return {"ok": False, "error": f"未知任务类型 {ttype}（支持 {TYPES}）"}
    if ttype == "wiki" and not wiki_configured(work):
        return {"ok": False,
                "error": f"作品 {work} 未配置官方设定 wiki（scripts/config/work_wikis.json 缺该作品块）"}
    if ttype != "board":
        # 揭示板任务的 work 是伪 id（board:<板id>），不是 registry 作品 → 跳过校验
        try:
            import registry
            registry.get(work)
        except Exception as e:
            return {"ok": False, "error": f"未知作品 {work}: {e}"}
    os.makedirs(TASKS_DIR, exist_ok=True)
    sched = parse_schedule_at(params.get("schedule_at"))
    if sched is not None:
        params = dict(params)
        params["schedule_at"] = sched           # 归一成 unix 秒落盘（前端/CLI 都按它显示）
    with _FileLock(os.path.join(TASKS_DIR, "registry")):
        # 同作品串行：有排队/运行中的同作品任务即拒绝
        # 2026-09-29: `scheduled`（定时等待中）不算占用——否则一个 00:00 的定时任务会把该作品锁一整天
        for t in list_tasks(_reconcile=False):
            if (t.get("work") == work and t.get("status") not in _FINAL
                    and t.get("status") not in ("stalled", "scheduled")):
                # 假死重入队: 豁免来源任务(已标 stalled, 进程已杀)
                if _stall_retry_of and t["task_id"] == _stall_retry_of and t.get("status") == "stalled":
                    continue
                return {"ok": False,
                        "error": f"作品 {work} 已有任务 {t['task_id']}（{t['status']}），同作品串行"}
        # 兼容期：旧 .runner.lock（外部旧入口/旧脚本）存活即拒绝
        lf = _legacy_lock(work, ttype)
        old_pid = _pid_of(lf) if lf else 0
        if old_pid and _pid_alive(old_pid):
            return {"ok": False, "error": f"作品 {work} 有旧式任务在跑 (pid {old_pid})，请等待完成"}
        tid = "t" + time.strftime("%Y%m%d-%H%M%S") + "-" + ("%04x" % (int(time.time() * 1000) % 0xFFFF))
        status = "scheduled" if (sched is not None and sched > time.time()) else "queued"
        t = {"task_id": tid, "type": ttype, "work": work, "params": params,
             "status": status, "pid": None, "rc": None,
             "created": _now(), "created_ts": time.time(),
             "started": None, "started_ts": None, "finished": None,
             "stop_mode": None, "killed": False}
        _save_task(t)
        q = _load_queue()
        q.append(tid)
        _save_queue(q)
    ensure_scheduler()
    if status == "scheduled":
        _wt = time.strftime("%m-%d %H:%M", time.localtime(sched))
        return {"ok": True, "task_id": tid,
                "msg": f"定时任务已排队（{_wt} 启动）: {tid}", "scheduled": True}
    return {"ok": True, "task_id": tid, "msg": f"任务已入队: {tid}"}


def request_stop(tid, mode):
    """优雅停止：mode=chapter(译完本章后停) | asap(尽快停/块边界)。写标志文件。"""
    if mode not in ("chapter", "asap"):
        return {"ok": False, "error": "mode 须为 chapter|asap"}
    t = _load_task(tid)
    if not t:
        return {"ok": False, "error": "任务不存在"}
    if t.get("status") not in ("running", "starting", "stopping"):
        return {"ok": False, "error": f"任务未在运行（{t.get('status')}）"}
    with open(stop_path(tid, mode), "w") as f:
        f.write(_now())
    t["stop_mode"] = mode
    t["status"] = "stopping"
    _save_task(t)
    return {"ok": True, "msg": f"已请求停止（{'译完本章后停' if mode == 'chapter' else '尽快停'}），runner 将在安全边界退出"}


def _kill_tree(pid):
    """杀整棵进程树。Windows: taskkill /T /F(原生整树, 最快); 失败再 os.kill 后代兜底;
    最后等待进程退出(最长 ~8s)。修复 2026-08-31: 原 force_kill 只杀直接子进程,
    aa_translate 等孙子进程成孤儿残留。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return
    # 1) Windows 主方案: taskkill /T /F 杀整树(原生, 含并发新子)
    if os.name == "nt":
        try:
            import subprocess as _sp
            _sp.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                    capture_output=True, timeout=30)  # 输出 GBK, 不 text 解码
        except Exception:
            pass
    # 2) 兜底: 收集后代(Win32_Process) 自底向上 os.kill
    try:
        if _pid_alive(pid):
            os.kill(pid, 15)
    except (OSError, ValueError):
        pass
    # 3) 等待退出(Windows 进程树终止有延迟, 最长 ~8s)
    for _ in range(16):
        if not _pid_alive(pid):
            return
        time.sleep(0.5)


def force_kill(tid):
    """强制终止（兜底，可能写坏 ckpt——ckpt 有 __lines 指纹兜底）。
    2026-08-31 修复: 杀整棵子进程树(translate_runner→aa_translate→孙子), 防孤儿残留。"""
    t = _load_task(tid)
    if not t or not t.get("pid"):
        return {"ok": False, "error": "无运行中任务"}
    _kill_tree(t["pid"])
    t["killed"] = True
    _save_task(t)
    return {"ok": True, "msg": f"已强制终止 pid={t['pid']} 整棵进程树（可能残留半成品，ckpt 指纹会防错位）"}


def cancel_queued(tid):
    """取消排队/定时等待中的任务（2026-09-29: `scheduled` 同权——它还没启动，取消零副作用）。"""
    t = _load_task(tid)
    if not t:
        return {"ok": False, "error": "任务不存在"}
    if t.get("status") not in ("queued", "scheduled"):
        return {"ok": False, "error": f"任务不在队列（{t.get('status')}），运行中请用停止"}
    with _FileLock(os.path.join(TASKS_DIR, "registry")):
        q = _load_queue()
        if tid in q:
            q.remove(tid)
            _save_queue(q)
        t["status"] = "cancelled"
        t["finished"] = _now()
        _save_task(t)
    return {"ok": True, "msg": f"已取消排队任务 {tid}"}


def remove_task(tid):
    """删除已结束任务（终态才能删；运行中拒绝）。删除任务文件+心跳+停止标志+队列。"""
    t = _load_task(tid)
    if not t:
        return {"ok": False, "error": "任务不存在"}
    if t.get("status") not in _FINAL:
        return {"ok": False, "error": f"任务未结束（{t.get('status')}），不能删除"}
    with _FileLock(os.path.join(TASKS_DIR, "registry")):
        q = _load_queue()
        if tid in q:
            q.remove(tid)
            _save_queue(q)
        for f in (task_path(tid), hb_path(tid),
                  stop_path(tid, "chapter"), stop_path(tid, "asap")):
            try:
                if os.path.exists(f):
                    os.remove(f)
            except OSError:
                pass
    return {"ok": True, "msg": f"已删除任务 {tid}"}


# ---------------------------------------------------------------- 自动清理过期任务

def _task_finish_ts(t):
    """任务终态时间戳：优先 finished/finished_ts，否则 created_ts。用于判断"3天以上"。"""
    for k in ("finished_ts",):
        if t.get(k):
            return float(t[k])
    ft = t.get("finished")
    if ft:
        try:
            import time as _time
            return _time.mktime(_time.strptime(str(ft), "%Y-%m-%d %H:%M:%S"))
        except Exception:
            pass
    return float(t.get("created_ts") or 0)


def prune_stale_tasks(days=3):
    """删除超过 days 天的终态任务（非活跃 = status 已终态 + 结束/创建超 days 天）。

    防区：仅清 _FINAL 状态；运行中/排队中任务不删；3 天内终态任务保留（可查日志/usage）。
    返回删除的 task_id 列表。"""
    import time as _time
    cutoff = _time.time() - days * 86400
    removed = []
    for t in list_tasks(_reconcile=False):
        if t.get("status") not in _FINAL:
            continue
        try:
            ts = _task_finish_ts(t)
        except Exception:
            ts = 0
        if ts and ts < cutoff:
            r = remove_task(t["task_id"])
            if r.get("ok"):
                removed.append(t["task_id"])
    return removed


def prune_finished_tasks():
    """一键清理**全部已结束任务**（终态：done/failed/stopped/crashed/cancelled/stalled）。

    2026-09-19 用户要求（翻译任务页「一键清理结束任务」按钮）：与 prune_stale_tasks
    的区别是不看天数，一次清空所有终态任务。
    **保留**：running/starting/stopping（运行中/启动中/停止中）与 queued（排队待跑）
    ——删掉排队任务会静默丢待跑工作，不能算「结束」。
    删除只是移除任务记录/心跳/停止标志（已产出的汉化文件、runner_log、usage 不动）。

    返回 {ok, removed:[tid], count, kept:{status: n}, msg}。
    """
    removed, kept = [], {}
    for t in list_tasks():                       # _reconcile=True：先把死 pid 收尾成终态
        st = t.get("status")
        if st not in _FINAL:
            kept[st] = kept.get(st, 0) + 1
            continue
        r = remove_task(t["task_id"])
        if r.get("ok"):
            removed.append(t["task_id"])
        else:
            kept[st] = kept.get(st, 0) + 1
    msg = f"已清理 {len(removed)} 个结束任务"
    if kept:
        msg += "（保留 " + "、".join(f"{k} {v}" for k, v in sorted(kept.items())) + "）"
    return {"ok": True, "removed": removed, "count": len(removed), "kept": kept, "msg": msg}


# ---------------------------------------------------------------- 状态读取

def _merge_hb(t):
    """合并心跳 sidecar（phase/chapter 进度）。"""
    hb = _read_json(hb_path(t["task_id"]))
    if hb:
        for k in ("phase", "chapter_idx", "chapter_total", "detail", "hb_ts", "rc"):
            if k in hb and hb[k] is not None:
                t.setdefault("hb", {})[k] = hb[k]
                if k == "rc":
                    t["hb_rc"] = hb["rc"]
    return t


def _reconcile_task(t):
    """pid 校正：running/starting/stopping 但 pid 已死 → 按心跳 rc 定终态。

    2026-08-31 竞态修复（v3）：进程退出与 _watch/finalize 写终态之间存在异步窗口——
    pid 死 + 心跳无 rc 时先不判 crashed：任务启动 <10s（收尾窗口内）保持 running，
    交给 _watch 线程或下轮 reconcile 收尾；启动久且无 rc 才判 crashed（真崩溃）。
    修复前快速退出的 runner（测试/短任务）会被误判 crashed。"""
    if t.get("status") in ("running", "starting", "stopping") and t.get("pid"):
        if not _pid_alive(t["pid"]):
            if t["task_id"] in _watch_procs:
                return t          # 本进程 _watch 线程将收尾，别抢判（等它写终态）
            hb = _read_json(hb_path(t["task_id"])) or {}
            rc = hb.get("rc")
            if rc is None:
                # 收尾窗口：刚启动就退出（<10s）可能是 runner 正常退出但 rc 未落盘，
                # 也可能是真崩溃——交给 _watch 线程或下轮 reconcile 再定，不抢判 crashed
                started = t.get("started_ts") or 0
                if time.time() - started < 10:
                    return t
                t["status"] = "crashed"      # 启动久且没留终态 → 崩溃
            elif rc == 0:
                t["status"] = "done"
            elif rc == RC_STOPPED:
                t["status"] = "stopped"
            else:
                t["status"] = "failed"
            t["rc"] = rc
            t["finished"] = t.get("finished") or _now()
            _save_task(t)
            _clear_legacy_lock(t)
    return t


def list_tasks(_reconcile=True):
    out = []
    if not os.path.isdir(TASKS_DIR):
        return out
    for fn in os.listdir(TASKS_DIR):
        if fn.startswith(".") or not fn.endswith(".json") or fn == "queue.json":
            continue
        t = _read_json(os.path.join(TASKS_DIR, fn))
        if not t:
            continue
        if _reconcile:
            with _LOCK:
                t = _reconcile_task(t)
        out.append(_merge_hb(t))
    out.sort(key=lambda x: x.get("created_ts") or 0, reverse=True)
    return out


def get_task(tid, log_tail=0):
    t = _load_task(tid)
    if not t:
        return None
    with _LOCK:
        t = _reconcile_task(t)
    t = _merge_hb(t)
    if log_tail:
        t["log"] = _log_tail(t, log_tail)
    t["queue"] = _load_queue()
    t["usage"] = aggregate_usage(t)
    return t


def _log_tail(t, n=20):
    # 2026-08-30: registry.get 抛 SystemExit（作品被删/改名）→ WORKS.get 容错
    # 2026-10-10: 揭示板任务（伪 id board:<板id>）日志在 揭示板/board_runner_log.txt
    try:
        lp = os.path.join(_work_dir_of(t.get("work"), t.get("type")), "runner_log.txt")
    except Exception:
        return ""
    if not os.path.exists(lp):
        return ""
    try:
        # 2026-08-31 修复: 原 f.readlines()[-n:] 读全文件——翻译日志持续增长(几 MB),
        # 任务页 3s 轮询 × 多任务全量读 → IO/内存暴涨卡死系统。改尾部 seek 只读最后 n 行。
        with open(lp, "rb") as f:
            f.seek(0, 2)          # 到文件尾
            size = f.tell()
            chunk = b""
            # 从尾部回读最多 64KB（n 行足够）
            back = min(size, 65536)
            f.seek(size - back)
            chunk = f.read(back)
        text = chunk.decode("utf-8", errors="replace")
        lines = text.splitlines()
        return "\n".join(lines[-n:])
    except Exception:
        return ""


# ---- usage.jsonl 增量缓存（2026-08-30：任务页轮询 3s × 全量扫 68K 行 → 13s 卡死）----
# usage.jsonl 是 append-only（aa_backend._log_usage open("a")），
# 用 (mtime, size) 指纹 + 只读新增行增量聚合，避免每次全量解析。
_USAGE_CACHE = {"key": None, "by_task": {}, "window": []}
# 轮换文件（usage.jsonl.1/.2/.3）的预聚合缓存（2026-10-11）：内容只在 aa_usage 滚动时变
_USAGE_ROT = {"keys": None, "by_task": None, "window": None}


def _new_usage():
    """usage 聚合累加器：{prompt, completion, total, calls, backends:set, tags:{tag:计数}}。"""
    return {"prompt": 0, "completion": 0, "total": 0, "calls": 0, "backends": set(), "tags": {}}


def _acc_usage(d, r):
    """把一条 usage 行累加进聚合 dict（含按 tag 的明细）。"""
    for k in ("prompt", "completion", "total"):
        d[k] += int(r.get(k) or 0)
    d["calls"] += 1
    if r.get("backend"):
        d["backends"].add(str(r["backend"]))
    tg = str(r.get("tag") or "")
    if tg:
        t = d["tags"].setdefault(tg, {"prompt": 0, "completion": 0, "total": 0, "calls": 0})
        for k in ("prompt", "completion", "total"):
            t[k] += int(r.get(k) or 0)
        t["calls"] += 1


def _merge_usage(dst, src):
    """把 src 聚合（_new_usage 形状）并进 dst（不改 src）。"""
    for k in ("prompt", "completion", "total", "calls"):
        dst[k] += src.get(k) or 0
    dst["backends"] |= set(src.get("backends") or ())
    for tg, t in (src.get("tags") or {}).items():
        o = dst["tags"].setdefault(tg, {"prompt": 0, "completion": 0, "total": 0, "calls": 0})
        for k in ("prompt", "completion", "total", "calls"):
            o[k] += t.get(k) or 0


def _rotated_index():
    """轮换文件（usage.jsonl.1/.2/.3）的预聚合索引（2026-10-11）。

    动机：_usage_index 只读主文件，而 aa_usage.rotate_if_needed 在 10MB 时把整份主文件
    改名成 .1 —— **跨滚动的任务（翻译任务先翻译后校对）在任务卡上就只剩后半段的消耗**
    （实测 t20260929-180702-bd35：翻译阶段 614 行落在 .1，界面只显示校对）。
    这里把轮换文件读一次并按 task 预聚合（内容只在滚动时变 → 按 (path,mtime,size) 指纹缓存，
    不至于每次轮询全量解析；预聚合而非留原始行，内存只有几十 KB）。

    返回 (by_task, window)：by_task[tid] = _new_usage() 形状；
    window = [(ts, prompt, completion, total, backend)]（无 task 归因的历史行，供时间窗兜底）。
    """
    uf = os.path.join(ROOT, "usage.jsonl")
    keys = []
    for i in (1, 2, 3):
        p = f"{uf}.{i}"
        try:
            st = os.stat(p)
            keys.append((p, st.st_mtime, st.st_size))
        except OSError:
            keys.append((p, None, None))
    if _USAGE_ROT["keys"] == keys and _USAGE_ROT["by_task"] is not None:
        return _USAGE_ROT["by_task"], _USAGE_ROT["window"]
    by_task, window = {}, []
    for p, mt, _sz in keys:
        if mt is None:
            continue
        try:
            with open(p, "rb") as f:
                data = f.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        for line in data.split("\n"):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            tid = r.get("task")
            if tid:
                _acc_usage(by_task.setdefault(tid, _new_usage()), r)
            else:
                window.append((r.get("ts") or 0, int(r.get("prompt") or 0),
                               int(r.get("completion") or 0), int(r.get("total") or 0),
                               r.get("backend")))
    _USAGE_ROT.update(keys=keys, by_task=by_task, window=window)
    return by_task, window

def _usage_index(force=False):
    """增量构建 usage 索引：{task_id: 聚合行列表}, window: 无 task 记录列表。
    返回 (by_task, window)。

    缓存键含**路径**（2026-10-11 修）：只有 (mtime,size) 时，换 ROOT/换文件（单测、
    数据根切换）只要 mtime+size 撞上就会走增量分支、从旧 size 续读 → 静默丢掉前半段行。
    """
    uf = os.path.join(ROOT, "usage.jsonl")
    try:
        st = os.stat(uf)
        key = (uf, st.st_mtime, st.st_size)
    except OSError:
        return {}, []
    if not force and _USAGE_CACHE["key"] == key:
        return _USAGE_CACHE["by_task"], _USAGE_CACHE["window"]
    # 从头读（首次或文件变化）；只读新增行（若 key 变了但 size 增长则从旧 size 续读）
    same_file = bool(_USAGE_CACHE["key"]) and _USAGE_CACHE["key"][0] == uf
    old_size = _USAGE_CACHE["key"][2] if same_file else 0
    keep = same_file and key[2] >= old_size
    by_task = _USAGE_CACHE["by_task"] if keep else {}
    window = _USAGE_CACHE["window"] if keep else []
    try:
        with open(uf, "rb") as f:
            if old_size and key[2] > old_size:
                f.seek(old_size)
            elif old_size and key[2] == old_size:
                pass
            else:
                f.seek(0)
            new_lines = f.read().decode("utf-8", errors="replace").split("\n")
    except OSError:
        return {}, []
    for line in new_lines:
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        tid = r.get("task")
        if tid:
            by_task.setdefault(tid, []).append(r)
        else:
            window.append(r)
    _USAGE_CACHE["key"] = key
    _USAGE_CACHE["by_task"] = by_task
    _USAGE_CACHE["window"] = window
    return by_task, window


def aggregate_usage(t, _force=False):
    """按 task 聚合 usage.jsonl（主文件 + 轮换文件 .1/.2/.3）；无 task 匹配时按时间窗兜底
    （approx，可能混入并行任务）。

    返回 {prompt, completion, total, calls, backends:[去重后端], tags:{tag:计数}}。
    2026-10-11：**加 tags 分阶段明细** —— 翻译任务是「先翻译后校对」两个阶段、同一个 task id，
    任务卡只给一个合计数时看不出翻译那半段（各分支写同一 task 字段，合计数本就是两阶段之和；
    分阶段后「翻译 X · 校对 Y」一目了然，翻译阶段 0 也照实显示）。
    另修：轮换文件（aa_usage 按 10MB 滚动）里的历史归因一并计入，否则跨滚动的任务只剩后半段。
    """
    uf = os.path.join(ROOT, "usage.jsonl")
    if not os.path.exists(uf):
        return None
    tid = t["task_id"]
    t0 = t.get("created_ts") or 0
    t1 = time.time() if t.get("status") not in _FINAL else (t.get("finished_ts") or time.time())
    by_task, window = _usage_index(force=_force)
    rot_task, rot_window = _rotated_index()
    by_task_rows = by_task.get(tid) or []
    rot_agg = rot_task.get(tid)
    acc = _new_usage()
    approx = False
    if by_task_rows or rot_agg:
        for r in by_task_rows:
            _acc_usage(acc, r)
        if rot_agg:
            _merge_usage(acc, rot_agg)
    else:
        # 时间窗兜底：只对无 task 记录的任务用（含轮换文件里无归因的历史行，按时间过滤）
        for r in window:
            if t0 <= (r.get("ts") or 0) <= t1 + 5:
                _acc_usage(acc, r)
        for ts, p, c, tot, bk in rot_window:
            if t0 <= ts <= t1 + 5:
                acc["prompt"] += p
                acc["completion"] += c
                acc["total"] += tot
                acc["calls"] += 1
                if bk:
                    acc["backends"].add(str(bk))
        approx = True
    out = {"prompt": acc["prompt"], "completion": acc["completion"],
           "total": acc["total"], "calls": acc["calls"]}
    if approx:
        out["approx"] = True
    if acc["backends"]:
        out["backends"] = sorted(acc["backends"])
    if acc["tags"]:
        out["tags"] = acc["tags"]
    return out


# ---------------------------------------------------------------- 调度器

def _scheduler_alive():
    lk = _read_json(scheduler_lock_path())
    return bool(lk and _pid_alive(lk.get("pid")))


def _write_scheduler_lock():
    _write_json(scheduler_lock_path(), {"pid": os.getpid(), "ts": time.time()})


def ensure_scheduler():
    """入队后保证有调度器：锁活即返回；否则拉起独立调度器进程（彻底 detach）。
    web 内嵌调度器在 main.py 启动时挂线程；本函数主要服务 CLI/无 web 场景。"""
    if _scheduler_alive():
        return False
    if os.environ.get("AA_TASK_MANAGER_NO_SPAWN"):   # 测试/内嵌线程场景
        return False
    kwargs = dict(cwd=ROOT, stdin=subprocess.DEVNULL,
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    if os.name == "nt":
        # CREATE_NO_WINDOW|CREATE_NEW_PROCESS_GROUP: 不弹窗 + 独立进程组(console 关闭事件不广播)
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            | subprocess.CREATE_NEW_PROCESS_GROUP)
    subprocess.Popen(py_prefix() + [os.path.abspath(__file__), "--scheduler"], **kwargs)
    return True


def _finalize(t, rc):
    """写终态（幂等）。"""
    if t.get("status") in _FINAL:
        return
    t["rc"] = rc
    if rc == 0:
        t["status"] = "done"
    elif rc == RC_STOPPED:
        t["status"] = "stopped"
    elif rc is None:
        t["status"] = "crashed"
    else:
        t["status"] = "failed"
    t["finished"] = _now()
    t["finished_ts"] = time.time()
    _save_task(t)
    _clear_legacy_lock(t)
    _watch_procs.pop(t["task_id"], None)
    _chain_followup(t, rc)     # 2026-09-13: download → translate 链式后续


def _clear_legacy_lock(t):
    try:
        lf = _legacy_lock(t["work"], t.get("type"))
        if not lf:
            return
        pid = _pid_of(lf)
        if not pid or pid == t.get("pid") or not _pid_alive(pid):
            os.remove(lf)
    except OSError:
        pass


def _chain_followup(t, rc):
    """链式后续任务（2026-09-13）：`params["then"] = {"type": "translate", "chapters": ...}`。

    工作台「更新 → 下载并翻译」靠它实现 download → translate：前置任务**正常完成**(rc=0)
    后才入队翻译（此时新章已在 aa/；translate_runner 已不再自动补下载）。
    停止/失败/崩溃不入队——避免对半成品直接烧 token；结果写回 t["followup"] 供任务页看。
    """
    spec = (t.get("params") or {}).get("then")
    if not spec:
        return
    if rc != 0:
        t["followup"] = {"type": spec.get("type") if isinstance(spec, dict) else str(spec),
                         "ok": False,
                         "error": f"前置任务未正常完成(rc={rc})，不链式"}
        _save_task(t)
        return
    spec = dict(spec) if isinstance(spec, dict) else {"type": str(spec)}
    ntype = spec.pop("type", "translate")
    params = dict(spec.pop("params", None) or {})
    params.update(spec)          # 平铺键(chapters/force/gen/env…)直接透传
    params.setdefault("chapters", (t.get("params") or {}).get("chapters") or "new")
    try:
        r = enqueue(ntype, t["work"], params)
    except Exception as e:       # 链式失败绝不反噬前置任务状态
        r = {"ok": False, "error": f"{e}"}
    t["followup"] = {"type": ntype, "ok": bool(r.get("ok")),
                     "task_id": r.get("task_id"), "error": r.get("error")}
    _save_task(t)


def _start_task(t):
    """启动 runner 子进程 + watchdog 线程。"""
    import registry
    work = t["work"]
    cmd = _build_command(t["type"], work, t.get("params") or {})
    tid = t["task_id"]
    env = child_env()      # 子任务环境（冻结 exe 下带上 AA_BASE/AA_ENV）
    env["PYTHONIOENCODING"] = "utf-8"   # 2026-09-08: 子进程 stdout 强制 utf-8(否则 GBK 写 runner_log.txt
    # → _log_tail utf-8 读乱码 + print 日文/､(U+FF64) 崩 UnicodeEncodeError, refine_aa 校对事故根因)
    env["AA_TASK_ID"] = tid
    env["AA_TASK_HB"] = hb_path(tid)
    env["AA_STOP_CHAPTER_FILE"] = stop_path(tid, "chapter")
    env["AA_STOP_ASAP_FILE"] = stop_path(tid, "asap")
    # 2026-08-29 任务参数 env 透传(如 AA_FREE_MODE/AA_BACKEND): task 级行为不依赖调度器环境
    env.update({str(k): str(v) for k, v in (t.get("params") or {}).get("env", {}).items()})
    # 2026-09-01: 免费翻译任务章内块级并行提到 10(AA_FREE_WORKERS→MAX_WORKERS, 单章内 API 调用
    # 并发榨干免费额度; 章节级并行 AA_CHAPTER_WORKERS 免费模式由 translate_runner 强制串行=1,
    # 不注入——避免误解)。付费任务不注入, 保持各自默认。仅 translate 类型。
    if t["type"] == "translate" and env.get("AA_FREE_MODE") == "1" and "AA_FREE_WORKERS" not in env:
        env["AA_FREE_WORKERS"] = str(MAX_CHAPTER_WORKERS_FREE)
    lp = os.path.join(_work_dir_of(work, t["type"]), "runner_log.txt")
    os.makedirs(os.path.dirname(lp), exist_ok=True)
    logf = open(lp, "w", encoding="utf-8", errors="replace")
    # 2026-08-31 重启不中断: runner 用 CREATE_NO_WINDOW|CREATE_NEW_PROCESS_GROUP 启动
    # ——不弹窗(测试/后台跑 unittest 不闪黑窗) + 独立进程组(bat 杀 uvicorn 的 console 关闭事件
    # 不广播到 runner)。重启不中断的保证 = runner 心跳自收尾(AA_TASK_HB finalize rc)
    # + 新工作台 scheduler reconcile 读心跳接管，不依赖 DETACHED_PROCESS(它和 CREATE_NO_WINDOW 互斥)。
    _spawn_kwargs = dict(cwd=_SCRIPTS, env=env, stdout=logf,
                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, close_fds=True)
    if os.name == "nt":
        _spawn_kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            | subprocess.CREATE_NEW_PROCESS_GROUP)
    p = subprocess.Popen(cmd, **_spawn_kwargs)
    t["pid"] = p.pid
    t["status"] = "running"
    t["started"] = _now()
    t["started_ts"] = time.time()
    _save_task(t)
    # 兼容期：维护旧 .runner.lock（外部旧脚本能感知）
    try:
        _lk = _legacy_lock(work, t["type"])
        if _lk:
            with open(_lk, "w") as f:
                f.write(str(p.pid))
    except OSError:
        pass
    _watch_procs[tid] = p

    def _watch(proc=p, task_id=tid, lf=logf):
        rc = proc.wait()
        try:
            lf.flush()
            lf.close()
        except Exception:
            pass
        with _LOCK:
            cur = _load_task(task_id)
            if cur:
                _finalize(cur, rc)

    threading.Thread(target=_watch, daemon=True).start()


def _stall_check(t):
    """假死检测(2026-08-31): translate 任务 usage 停滞 ≥STALL_MINUTES 且 pid 存活 → 判假死。
    返回 (stalled, reason)。仅 translate 任务; 无 usage 记录(尚未调用 API)不判, 防误杀启动初期。
    """
    if t.get("type") not in ("translate", "pokemon") or t.get("status") not in ("running", "starting"):
        return False, ""
    pid = t.get("pid")
    if not pid or not _pid_alive(pid):
        return False, "pid 已死(走 reconcile)"
    # 2026-08-31 修复: 原 force=True 每次全量重读 usage.jsonl——调度器每 2s tick ×
    # 每个 running 任务 → 持续全量扫 68K+ 行 usage, 任务页开久了系统卡死。
    # 非 force 用 (mtime,size) 指纹增量缓存: 翻译中 usage 在增长(自动重读), 停滞时文件
    # 不变(直接命中缓存) —— 检测语义不变, 开销从全量扫降到 O(新增行)。
    by_task, _ = _usage_index()
    rows = by_task.get(t["task_id"]) or []
    if not rows:
        return False, "无 usage 记录(未开始调 API)"
    last_ts = max(r.get("ts") or 0 for r in rows)
    age = time.time() - last_ts
    if age >= STALL_MINUTES * 60:
        return True, f"usage 停滞 {age/60:.0f}min (最后 {time.strftime('%H:%M:%S', time.localtime(last_ts))})"
    return False, ""


def _handle_stalled(t):
    """假死处理: 杀整树 + 标记 stalled + 自动重入队(ckpt 断点续跑)。连续假死 ≥STALL_MAX_RESTARTS 标 failed。"""
    tid = t["task_id"]
    pid = t.get("pid")
    inherited = int((t.get("params") or {}).get("_stall_count") or 0)
    n = max(int(t.get("stall_count") or 0), inherited) + 1
    t["stall_count"] = n
    t["status"] = "stalled"
    t["stalled_at"] = _now()
    t["stall_reason"] = t.pop("stall_reason", "")
    _save_task(t)          # 先落盘再杀: 若 _kill_tree 抛异常/卡住, stalled 终态已持久化, 调度不会重复处理
    if pid:
        _kill_tree(pid)
    _clear_legacy_lock(t)
    _watch_procs.pop(tid, None)
    print(f"[task_manager] 假死检测: {t['work']} 停滞 {n}/{STALL_MAX_RESTARTS} 次, pid={pid} 已杀", flush=True)
    if n >= STALL_MAX_RESTARTS:
        t["status"] = "failed"
        t["finished"] = _now()
        t["finished_ts"] = time.time()
        t["stall_reason"] += " | 连续假死达上限, 停止重入队"
        _save_task(t)
        print(f"[task_manager] {t['work']} 连续假死 {n} 次, 标 failed 停止", flush=True)
        return
    # 自动重入队: 同类型同参数(ckpt 断点续跑); stall_count 继承防上限失效
    params = dict(t.get("params") or {})
    params["_stall_count"] = n
    try:
        enqueue(t.get("type", "translate"), t.get("work", ""), params,
                _stall_retry_of=tid)
        print(f"[task_manager] {t['work']} 已自动重入队(续跑)", flush=True)
    except Exception as e:
        print(f"[task_manager] 重入队失败: {e}", flush=True)


def scheduler_tick():
    """调度一轮：校正 running → 补位启动。返回本轮新启动的 task_id 列表。"""
    started = []
    with _LOCK:
        tasks = {_t["task_id"]: _reconcile_task(_t)
                 for _t in list_tasks(_reconcile=False)
                 if _t.get("status") not in _FINAL}
        # 2026-09-01: stalled 是"已杀+已重入队"的终态, 从并行计数彻底排除——否则连续假死的任务
        # 占着名额不释放(同作品串行检查也把它当占用), 队列头永远起不来(akari_series 第三次重入队被压掉事故)
        running = [t for t in tasks.values()
                   if t.get("status") in ("running", "starting", "stopping")]
        # 假死检测只对真正 running 的任务
        stall_candidates = list(running)
        # 2026-08-31 假死检测: usage 停滞 ≥STALL_MINUTES 的 translate 任务 → 杀+重入队(ckpt 续跑)
        stalled_any = False
        for t in list(stall_candidates):
            # 2026-09-07: proofread(refine_aa)任务不参与 usage 假死检测——refine_aa 直连
            # deepinfra 不记 usage, 判停滞必误杀; 校对单章几十秒~数分钟, 靠两档停止+强制终止兜底
            if t.get("type") == "proofread":
                continue
            try:
                st, reason = _stall_check(t)
            except Exception as e:
                print(f"[task_manager] 假死检测异常 {t.get('task_id')}: {e}", flush=True)
                continue
            if st:
                t["stall_reason"] = reason
                _handle_stalled(t)
                stalled_any = True
                running.remove(t)
        if stalled_any:
            # 重入队任务已排队, 本 tick 直接继续正常调度(它会被补位启动)
            tasks = {_t["task_id"]: _reconcile_task(_t)
                     for _t in list_tasks(_reconcile=False)
                     if _t.get("status") not in _FINAL}
        n_running = len(running)
        n_dl = sum(1 for t in running if t.get("type") == "download")
        # 2026-09-01: 免费/付费翻译分开计数——免费限1、付费可到10, 互不占名额
        def _is_free(t):
            return (t.get("type") in ("translate", "pokemon")
                    and (t.get("params") or {}).get("env", {}).get("AA_FREE_MODE") == "1")
        n_tr_free = sum(1 for t in running if _is_free(t))
        # 2026-09-22: pokemon 类型（宝可梦专属链）同属"翻译"池——它也是翻译任务（预填+通用链翻译）
        n_tr_paid = sum(1 for t in running
                        if t.get("type") in ("translate", "pokemon") and not _is_free(t))
        # 2026-08-30: 类型感知并行——翻译 ≤ 上限，下载 ≤ MAX_PARALLEL_DL
        # 2026-08-31: 并行上限动态读 data/tasks_parallel.json（_parallel_limits），改文件即时生效
        # 2026-09-01: free_translate(默认1) 单独成池, 与付费 translate(默认10) 互不占名额
        mp_tr, mp_dl, mp_ft = _parallel_limits()
        # 先粗筛：各池上限之和(细筛按类型再精确拦)
        if n_running >= (mp_tr + mp_dl + mp_ft):
            return started
        q = _load_queue()
        if not q:
            return started
        # 2026-09-29 定时任务: 逐条过队列——① 剔除失效条目（任务文件丢了/已被取消）；② 把**到点**的
        # `scheduled` 转成 `queued`；③ 未到点的 `scheduled` 留在队列但**不作候选**（不阻塞它后面的任务，
        # 其余任务保持原 FIFO 相对顺序）。候选 = 队列里第一个 `queued`。
        q2, changed, head = [], False, None
        for _tid in q:
            _t = _load_task(_tid)
            if not _t:
                changed = True
                continue
            _st = _t.get("status")
            if _st == "scheduled":
                if not schedule_due(_t):
                    q2.append(_tid)          # 到点前一直在队列里等着
                    continue
                _t["status"] = "queued"      # 到点 → 交回正常排队
                _save_task(_t)
                changed = True
                _st = "queued"
            elif _st != "queued":
                changed = True
                continue                     # 已取消/外部改动 → 出队
            q2.append(_tid)
            if head is None:
                head = _t
        if changed:
            _save_queue(q2)
        if head is None:
            return started
        # 类型上限：head 是 translate 且翻译已满 / head 是 download 且下载已满 → 等下轮
        # 2026-09-01: 免费翻译任务(AA_FREE_MODE=1)用免费池上限(默认1), 付费翻译用 translate(默认10)
        head_free = bool((head.get("params") or {}).get("env", {}).get("AA_FREE_MODE") == "1")
        if head.get("type") in ("translate", "pokemon"):
            head_tr_limit = mp_ft if head_free else mp_tr
            n_now = n_tr_free if head_free else n_tr_paid
            if n_now >= head_tr_limit:
                return started
        if head.get("type") == "download" and n_dl >= mp_dl:
            return started
        # 同作品占用检查（含旧锁）
        busy_works = {t["work"] for t in running}
        if head["work"] in busy_works:
            return started
        pid = _pid_of(_legacy_lock(head["work"], head.get("type")) or "")
        if pid and _pid_alive(pid) and pid not in {t.get("pid") for t in running}:
            return started    # 外部旧任务占用，严格 FIFO 等下轮
        q2.remove(head["task_id"])
        _save_queue(q2)
        _start_task(head)
        started.append(head["task_id"])
    return started


def scheduler_loop(embedded=False, interval=2.0):
    """调度主循环。全局单例（pid 锁）；CLI 独立进程在队列+运行清空后退出。"""
    if _scheduler_alive():
        print(f"[task_manager] 已有调度器在跑，退出", flush=True)
        return
    _write_scheduler_lock()
    print(f"[task_manager] 调度器启动 pid={os.getpid()} embedded={embedded}", flush=True)
    _last_prune = 0.0
    try:
        while True:
            _write_scheduler_lock()       # 续约
            scheduler_tick()
            # 2026-09-05: 自动删除超过 3 天的终态任务（每 10 分钟清一次，避免高频扫目录）
            _now2 = time.time()
            if _now2 - _last_prune >= 600:
                _last_prune = _now2
                try:
                    prune_stale_tasks(3)
                except Exception:
                    pass
            if not embedded:
                active = [t for t in list_tasks(_reconcile=False)
                          if t.get("status") not in _FINAL]
                if not active and not _load_queue():
                    break                  # 独立调度器：排空即退
            time.sleep(interval)
    finally:
        if not embedded:
            try:
                os.remove(scheduler_lock_path())
            except OSError:
                pass
    print(f"[task_manager] 队列已排空，调度器退出", flush=True)


# ---------------------------------------------------------------- CLI

def _fmt_task(t):
    hb = t.get("hb") or {}
    prog = ""
    if hb.get("chapter_total"):
        prog = f" [{hb.get('phase') or ''} {hb.get('chapter_idx', '?')}/{hb['chapter_total']}]"
    # 2026-09-29: 定时任务显示启动时刻（未到点不显示为"排队中"会让人以为卡了）
    sched = (t.get("params") or {}).get("schedule_at")
    if t.get("status") == "scheduled" and sched:
        try:
            prog += " [定时 %s 启动]" % time.strftime("%m-%d %H:%M", time.localtime(float(sched)))
        except (TypeError, ValueError):
            pass
    return (f"{t['task_id']}  {t['status']:<9} {t['type']:<9} {t['work']}{prog}  "
            f"pid={t.get('pid')}  {t.get('created')}")


def cli(argv):
    import argparse
    ap = argparse.ArgumentParser(description="AA 翻译任务管理器")
    ap.add_argument("--scheduler", action="store_true", help="以独立调度器进程运行（排空队列后退出）")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("enqueue", help="入队新任务")
    p.add_argument("--type", required=True, choices=TYPES)
    p.add_argument("--work", required=True)
    p.add_argument("--chapters", default="new")
    p.add_argument("--force", action="store_true")
    # 2026-10-06: 移除 --gen（合集一律手动生成——gen_aa_html.py 或工作台「重生成合集」）
    p.add_argument("--free-mode", action="store_true", help="等价 AA_FREE_MODE=1(免费链)")
    p.add_argument("--env", action="append", default=[], metavar="K=V",
                   help="透传环境变量给 runner(可多次, 如 --env AA_FREE_MODE=1 --env AA_BACKEND=siliconflow)")
    p.add_argument("--then-translate", action="store_true",
                   help="前置任务正常完成后自动接翻译（链式 params['then']，2026-09-13）")
    p.add_argument("--schedule-at", default=None, metavar="TIME",
                   help="定时启动（2026-09-29）：unix 秒 或 'YYYY-MM-DD HH:MM'(也收 'YYYY-MM-DDTHH:MM')；"
                        "未到点不入队列头、不占同作品串行")
    sub.add_parser("list", help="列出任务")
    p = sub.add_parser("status", help="单任务详情")
    p.add_argument("task_id")
    p = sub.add_parser("stop", help="优雅停止")
    p.add_argument("task_id")
    p.add_argument("--mode", default="chapter", choices=["chapter", "asap"])
    p = sub.add_parser("kill", help="强制终止（兜底）")
    p.add_argument("task_id")
    p = sub.add_parser("cancel", help="取消排队任务")
    p.add_argument("task_id")
    p = sub.add_parser("remove", help="删除已结束任务")
    p.add_argument("task_id")
    p = sub.add_parser("set-parallel", help="运行时调整并行上限（写 data/tasks_parallel.json，即时生效）")
    p.add_argument("--translate", type=int, default=None, help="付费翻译并行上限")
    p.add_argument("--download", type=int, default=None, help="下载并行上限")
    p.add_argument("--free-translate", type=int, default=None, help="免费链翻译并行上限(默认1)")
    a = ap.parse_args(argv)

    if a.scheduler:
        scheduler_loop(embedded=False)
        return 0
    if a.cmd == "enqueue":
        penv = {k: v for kv in a.env for k, v in [kv.split("=", 1)]} if a.env else {}
        if a.free_mode:
            penv["AA_FREE_MODE"] = "1"
        params = {"chapters": a.chapters, "force": a.force, "env": penv}
        if a.schedule_at:
            params["schedule_at"] = a.schedule_at        # 2026-09-29 定时启动
        if a.then_translate and a.type != "translate":
            params["then"] = {"type": "translate", "chapters": a.chapters,
                              **({"env": penv} if penv else {})}
        r = enqueue(a.type, a.work, params)
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r.get("ok") else 1
    if a.cmd == "list":
        for t in list_tasks():
            print(_fmt_task(t))
        return 0
    if a.cmd == "status":
        t = get_task(a.task_id, log_tail=20)
        if not t:
            print("任务不存在")
            return 1
        print(_fmt_task(t))
        if t.get("usage"):
            print("usage:", json.dumps(t["usage"], ensure_ascii=False))
        if t.get("log"):
            print("---- log tail ----")
            print(t["log"])
        return 0
    if a.cmd == "stop":
        r = request_stop(a.task_id, a.mode)
    elif a.cmd == "kill":
        r = force_kill(a.task_id)
    elif a.cmd == "cancel":
        r = cancel_queued(a.task_id)
    elif a.cmd == "remove":
        r = remove_task(a.task_id)
    elif a.cmd == "set-parallel":
        r = set_parallel(translate=a.translate, download=a.download, free_translate=a.free_translate)
    else:
        ap.print_help()
        return 0
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
