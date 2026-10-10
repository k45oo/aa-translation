# 翻译进程管理（统一任务管理器）实现 + 运维实测 2026-08-29

方案：`doc/翻译进程管理方案_2026-08-29.md`（grilling 9 项决策拍板）。本文件记**已实现的事实**与**运维实测踩出的坑**。
核心结论：**实现已落地（5 阶段，6 commit），测试 226 全绿，并在真机「杀掉旧进程 + 管理器重启」运维中暴露 2 个真 bug + 2 个设计补丁**。

## 已落地组件（commit 序列）

| commit | 内容 |
|---|---|
| `96d3b725` | 方案定稿文档 |
| `3759e2ff` | 阶段1：`aa_web_app/task_manager.py` 核心（注册表/FIFO 队列/调度器 pid 锁/两档停止标志/CLI） |
| `45df8f42` | 阶段2：`scripts/common/task_report.py` 心跳+停止协议；translate_runner/dl_runner/aa_translate 章节/块边界干净退出 41 |
| `5c55dcfb` | 阶段3：usage.jsonl 打 `task` 字段（aa_translate/aa_backend/vision_classify 的 _log_usage） |
| `a286c4a2` | 阶段4：顶层《翻译任务》菜单 + tasks.html 任务页 + /api/tasks* 双入口 + 控制台改走管理器 |
| `28236287` | 阶段5：文档 + 端到端停止协议测试（假 runner 41/0） |
| `4f4f6d38` | 补丁：任务参数 env 透传（--free-mode/--env） |
| `81024090` | **修复**：`_pid_alive` 用 OpenProcess 判活（见坑①） |

## 数据模型/协议（实现后事实）

- 注册表目录 `aa_web_app/data/tasks/`（可用 `AA_TASKS_DIR` 覆盖）：`<task_id>.json`（管理器所有）、`.hb_<task_id>.json`（runner 心跳 sidecar，**与任务文件分开防争写**）、`.stop_<tid>.{chapter,asap}`（停止标志，runner 见到即删）、`queue.json`（FIFO 有序 id）、`.scheduler.lock`（`{pid, ts}` 调度器单例）。
- runner 协议 env：`AA_TASK_ID` / `AA_TASK_HB` / `AA_STOP_CHAPTER_FILE` / `AA_STOP_ASAP_FILE`；心跳写 phase/chapter_idx/chapter_total；退出前 `task_report.finalize(rc)` 写 rc 进 hb；**退出码 41=用户停止**。
- 停止粒度：章节边界 = runner 主循环/阶段边界 `check_stop()`（见到即删，asap 优先）；块边界 = `process_chapter` 块循环 `check_stop(only="asap")` 取消未开始块 + 收完在飞块 + `raise TaskStoppedError` 弃本章（ckpt 续跑）。块循环**必须 `only="asap"`**，否则会误消费 chapter 标志。
- 调度：全局并行 ≤ `MAX_PARALLEL`(2，`AA_TASK_MAX_PARALLEL` 可改)，同作品串行（enqueue 拒绝排队/运行中的同作品任务），FIFO 接续；落盘持久化，重启 pid 接管 running + 队列接续。
- 入口：CLI `python aa_web_app/task_manager.py enqueue|list|status|stop|kill|cancel [--free-mode/--env K=V]`；HTTP `POST /api/tasks`（body: type/work/chapters/force/no_gen/env）+ `/api/tasks/<tid>/{stop,kind,cancel}`。web 内嵌调度器线程随 `@app.on_event("startup")` 启动；CLI 在 web 没开时自动拉起独立调度器进程（DETACHED）排空队列后退出。
- 旧 `.runner.lock`（per-work）：enqueue 时仍检查（存活即拒）；`_start_task` 也写它兼容外部旧脚本。

## 坑①：Windows 上 `os.kill(pid,0)` 不能判活 —— 曾把所有运行中任务误标 crashed

**现象**：管理器 reconcile 每 2s 检查 running 任务 pid → 全部瞬间变 `crashed`（即使进程明显活着，tasklist 可见、心跳 hb_ts 一直在刷新）。

**根因**：本机 `os.kill(pid,0)` 对**任何 pid（包括存活的 21492，也包括已死的 99999999）都抛 `OSError: [WinError 87] 参数错误`**。旧 `_pid_alive` 把 `OSError` 当「进程不存在」→ 恒返回 False → 所有 running 被标 crashed。老的 main.py `_pid_alive` 同款逻辑（但有幸只在启动时检查一次锁，没暴露）。

**修复**（`81024090`）：Windows 分支改用 `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` 判活：
```python
def _pid_alive(pid):
    if not pid:
        return False
    try:
        if os.name == "nt":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
            if not h:
                return False
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        os.kill(int(pid), 0)
        return True
    except (OSError, ProcessLookupError, ValueError):
        return False
```
**判别口诀**：本机任何 `os.kill(pid,0)` 判活的代码都不可信；`OpenProcess` 返回句柄=活、None=死。用 `_pid_alive(os.getpid())` 应为 True / `_pid_alive(99999999)` 应为 False 单测锁定。

## 坑②：CLI 入队找不到 registry

`python aa_web_app/task_manager.py enqueue ...` 报 `未知作品 xx: No module named 'registry'`。registry 在 `scripts/works/registry.py`，而 task_manager 的 sys.path 只加了 `_SCRIPTS` 和 `_SCRIPTS/common`。修复：把 `os.path.join(_SCRIPTS, "works")` 也加进 sys.path（web 上下文的 main.py 本就加了，所以只有 CLI 踩）。

## 补丁：任务级 env 透传（task 行为不依赖调度器环境）

**为什么**：web 调度器（bat 启动）的进程 env 未必有 `AA_FREE_MODE`/`AA_BACKEND`；若 runner 的 env 全靠 `dict(os.environ of scheduler)`，重入队免费任务会不小心走付费链。修复 = 任务参数 `params["env"]` 透传进 runner 环境：
```python
# _start_task 内
env = dict(os.environ); env[...task ids...]
env.update({str(k): str(v) for k, v in (t.get("params") or {}).get("env", {}).items()})
```
enqueue 接受 `params["env"]`；CLI 加 `--free-mode`（`AA_FREE_MODE=1`）与 `--env K=V`（可多次）；HTTP body 加 `env`。测试：假命令子进程断言 `os.environ['AA_FREE_MODE']=='1'`（exit 0 表示透传成功）。

**⚠️ 部署顺序教训**：改 task_manager 后**必须先重启工作台**（bat 加载新代码）再入队，否则运行中的 web 调度器还是旧代码、env 透传不生效。本会话就因「先 commit 后重启」间漏了一次重启，导致重入队任务走了不同行为。

## 坑③：孤儿 runner 进程不可被管理器接管

**现象**：旧式 CLI 直启的 `translate_runner --work pon_slime --no-dl`（父进程是 shell，无 `data/tasks/` 注册表、无 `.runner.lock`/`runner_task.json`）——它是**孤儿**，管理器无法认领（list_tasks 只扫注册表目录，不会发现未知进程）。

**关键事实**：**工作台重启（bat `taskkill` 端口 8765）不会杀 translate_runner**——它不是 web 子进程（父进程是启动它的 shell，独立进程树），bat 只杀监听 8765 的进程。所以「重启工作台」对孤儿翻译零影响。

**要归管理必须**：`taskkill /PID <孤儿pid> /T /F` 杀掉整棵树（含 aa_translate 子进程），清掉残留，再 `task_manager enqueue` 重跑（**从 ckpt 续跑**，多数进度保留）。注意 `wmic process where "CommandLine like '%translate_runner.py --work X%'"` 会**自匹配 wmic 自身进程**（4 个假残留 pid），用 `tasklist /FI "PID eq N"` 逐个核实，或用 PowerShell `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? CommandLine -like '*translate_runner*X*'`。

## 坑④：CRLF 文件用 python 改，别用 patch 工具 / 别用会误匹配的锚点

- main.py/logic.py/translate_runner.py/dl_runner.py/aa_translate.py/base.html 全 CRLF → **patch 工具禁用**（会写坏/产生 ESC）。用 python 脚本：`open(p,'rb').read().decode('utf-8').replace('\r\n','\n')` 编辑 → `.replace('\n','\r\n').encode()` 写回。
- **`str.replace` / `str.count` 锚点缩进陷阱**：编辑时用「20 空格行」为锚点替换，但该行**包含「12 空格 + 同文本」作为子串**（20 空格前缀中 index=8 处恰有 12 空格接 'p'）→ `count` 算出 2 而实际 1，替换拼接错位 → IndentationError。**修复**：锚点必须用**整行/整块含精确缩进**的文本，或 `assert text.count(anchor)==1` 先校验再 replace；本会话 translate_runner 的 DONE print 就因这个炸了（20sp 行误匹配 12sp 子串）。
- 编辑完 `python -m py_compile` + `grep -c $'\r'`（注意 MSYS grep 对 CRLF 计数可能返回 0，用 `od -c` 核实）。
- 新增文件（task_manager.py/task_report.py）是 LF，write_file 正常；测试文件若用 `open(p,'w')` 写会因 Windows 文本模式转成 CRLF（本会话 test_task_report.py 变 CRLF 的坑——如需 LF 用 `open(p,'w',newline='')`）。

## 坑⑤：注册表无「删除已结束任务」接口

管理器只有 enqueue/stop/kill/cancel/list/status，**没有 remove**。已结束/被弃的 crashed 任务会一直留在 `/tasks` 页（历史记录）。要清清单需手动删 `aa_web_app/data/tasks/<tid>.json` + `.hb_<tid>.json`（+ `.stop_<tid>.*` 残留标志）。如需自动化，给 task_manager 加 `remove(tid)`（仅终态可删）。

## 端到端验证方法（加进测试，2 个）

`scripts/tests/_fake_runner.py`（子进程：心跳 → 循环 `check_stop()` → 见标志 `finalize(41)` 退出；超时 exit 0）。测试 `TestE2EStop`：
- `test_stop_asap_end_to_end`：enqueue → scheduler_tick → 假 runner running → `request_stop(tid,'asap')` → 等 status==stopped + rc==41 + 队列不含。
- `test_no_stop_exits_zero`：不停止 → status==done + rc==0。
- `test_env_passthrough_to_runner` / `test_no_env_passthrough_default`：假命令断言子进程 env `AA_FREE_MODE`。
- `TestPidAlive`：`_pid_alive(os.getpid())` True / `(99999999)` False——锁坑①。

## 本会话运维实测结论（少走弯路）

- 重入队免费任务要 `--free-mode` 保持免费链；但只要调度器是 web 的且 web 没加载 env 透传代码，`--free-mode` 也丢——**先重启工作台再入队**。
- 杀旧孤儿进程后，旧 task 在注册表留一条 crashed（历史），新 enqueue 建新 task（running）。两者并存是正常的。
- `os.kill(pid,0)` 判活的坑也存在于老 main.py 的 `_pid_alive`（现已废弃不用）；今后任何 Windows 判活一律 `ctypes.OpenProcess`。
