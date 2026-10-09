# 工作台重启中断翻译任务 + 弹窗修复（2026-08-31）

## 症状
- 重启工作台（bat）后，任务管理器的翻译任务全部 crashed（同作品两个任务同一时刻 finished=重启时刻，hb rc=None，心跳显示翻译/校对进行中）。
- 跑 `test_task_manager` 时反复弹黑色 cmd 窗口。

## 根因链
1. **中断**：bat 杀 8765 的 uvicorn，runner 是 uvicorn 的**直接子进程且同 console**（`_start_task` 的 Popen 无 detach flags）→ 被杀连坐。
2. **误标 crashed**：runner 被杀没写心跳 rc（`task_report.finalize(rc)` 没跑）→ 新工作台 reconcile 读心跳 rc=None → 判 crashed。
3. **弹窗**：`_start_task` 用 `DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP`——DETACHED 脱离 console 但**不抑制窗口**；且 DETACHED_PROCESS 与 CREATE_NO_WINDOW **互斥**（Windows 文档：指定 DETACHED 时 CREATE_NO_WINDOW 被忽略）——所以后来加的 CREATE_NO_WINDOW 没生效。

## 修复（task_manager.py）
### Popen flags（两处：`_start_task` runner + `ensure_scheduler` 独立调度器）
```python
_spawn_kwargs = dict(cwd=_SCRIPTS, env=env, stdout=logf,
                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, close_fds=True)
if os.name == "nt":
    _spawn_kwargs["creationflags"] = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        | subprocess.CREATE_NEW_PROCESS_GROUP)
```
重启不中断的保证 = runner 心跳自收尾 + 新 scheduler reconcile 接管，**不依赖 DETACHED**。

### `_reconcile_task` 竞态 v3
```python
if not _pid_alive(t["pid"]):
    if t["task_id"] in _watch_procs:
        return t          # 本进程 _watch 线程将收尾，别抢判
    hb = _read_json(hb_path(t["task_id"])) or {}
    rc = hb.get("rc")
    if rc is None:
        started = t.get("started_ts") or 0
        if time.time() - started < 10:
            return t      # 收尾窗口：交给 _watch/下轮，不抢判 crashed
        t["status"] = "crashed"
    ...
```

## 调试路径（重要）
1. **先确认任务文件状态**：`aa_web_app/data/tasks/*.json` 的 status/pid/finished/rc + 心跳 `.hb_<tid>.json` 的 rc。两个任务同一 finished 时刻 = 被外部杀，不是自然失败。
2. **隔离 Popen 行为**：独立脚本测 `CREATE_NO_WINDOW|CREATE_NEW_PROCESS_GROUP` 下 `python -c pass` 是否立即退出 rc=0——证明 Popen 本身没问题，问题在 reconcile 竞态。
3. **复现循环**：30 次 trial 循环（每次独立 tmp tasks 目录 + 假 registry），失败时打印 `_watch_procs.keys()`、任务文件、心跳、`p.poll()`——实锤 `_watch_procs` 空 + 心跳文件不存在 → reconcile 兜底判 crashed。
4. **git-bash 验证转义坑**：`taskkill //F //PID` 在 git-bash 里 `//` 被 MSYS 转义报错（GBK 乱码）；改用 Python `subprocess.run(['taskkill','/F','/PID',...])` 或 PowerShell。`tasklist` 输出 GBK，`text=True` 用 utf-8 解码崩——用 bytes + `str(pid).encode() in stdout`。

## 验证结果
- `test_task_manager` 24 用例 8 连全过（修复前 5/6 偶发失败）；`test_task_manager + test_aaweb` 67 全过。
- 杀父进程存活测试：父（uvicorn 角色）taskkill 自杀后 runner 存活 + 心跳 rc=0（父 console=0 证明 detach 生效）。
- 弹窗消除：creationflags 实际值 = `0x08000000 | 0x200`（CREATE_NO_WINDOW|CREATE_NEW_PROCESS_GROUP），grep 确认无 DETACHED_PROCESS 实际使用（注释里的字样不算）。

## 同项目行尾不一致陷阱（task_manager.py 踩过）
- **task_manager.py 是 LF，main.py 是 CRLF**——同一仓库不同文件行尾不同！用统一 `

→
→

` 脚本改 LF 文件会把它整体转 CRLF → `git diff --stat` 显示 1672 行变动（实际只改 8 行）。
- 判定：改前 `git show HEAD:<file> | file -` 查 HEAD 行尾；改后 `git diff --ignore-all-space --stat` 看真实改动行数；`git diff --stat` 异常大先怀疑行尾被重写。
- 修回：`open(p,'rb').read().replace(b'\r\n', b'\n')` 写回即恢复 LF。
