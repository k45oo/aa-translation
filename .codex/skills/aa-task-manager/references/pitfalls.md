# CRLF 多行编辑现场记录（2026-08-29 统一任务管理器改造）

## 背景
本仓库 `scripts/runner/translate_runner.py`、`dl_runner.py`、`scripts/aa_translate.py`、`aa_web_app/main.py`、`logic.py` 及所有 `.md`/模板均为 **CRLF**。按仓库约定（AGENTS.md 坑），对 CRLF 文件**禁用 patch 工具**（会写坏产生 ESC 控制字符），改用 python 脚本编辑。

## 标准 CRLF 安全编辑模板
```python
def load(p):  return open(p,'rb').read().decode('utf-8').replace('\r\n','\n').replace('\r','\n')
def save(p,t): open(p,'wb').write(t.replace('\n','\r\n').encode('utf-8'))
def rep(t, old, new, n=1):
    c = t.count(old); assert c == n, f'锚点不匹配({c}!={n}): {old[:60]!r}'
    return t.replace(old, new, n)
```
改完 `python -m py_compile <file>` 校验（对 CRLF 无副作用）。

## 核心陷阱：`str.replace` / `count` 的缩进子串误匹配
给 `aa_translate.py` 的串行 legacy 循环插停止检测，锚点用了 12 空格版：`'            print(f"DONE {f}: ...")'`，`text.count()` 返回 **2**（预期 1）报错。根因：

```
                     # 20 空格的并行版 DONE 行
    for fut in as_completed(futs):        # 12 空格
        ...
                    print(f"DONE {f}: ...")   # ← 20 空格
为 for f in files:  # 4 空格
            print(f"DONE {f}: ...")           # ← 12 空格
```
20 空格行前缀里，从第 **8** 格起是 12 个空格，正好接第 20 格的 `'p'` → 命中 12 空格锚点。`str.replace`/`count` 是子串匹配，不看行首逻辑，于是同一锚点匹配到两条不同缩进的行。

### 修复
- 用**整行完整缩进**作锚点（能区分），或
- 锚定**整块**（含上下文唯一行），如把 `for f in files:` … `sys.exit(30)` 整块作为 old，一次替换；或
- `count == 1` 断言兜底，宁可崩也不静默改错。

## 其他本会话踩到的点
- **裸 `python` = WindowsApps stub**，跑 `python -m unittest …` 返回 `exit 49`，无输出。用 `source .venv/Scripts/activate` 或 `.venv/Scripts/python.exe`（`python -m py_compile` 同样受影响）。
- **`open(path,'w')` 在 Windows 文本模式写入会转 CRLF**：一次用 heredoc `open(p,'w',encoding='utf-8').write(...)` 编辑 LF 测试文件，把文件悄悄转成 CRLF，后续 patch 工具对它报 `\r`。
- **`scripts/tests` 不是包**：`python -m unittest scripts.tests.test_task_manager` 没反应（exit 0 无输出）；`tail` 管道吞 exit code 让人误判。正确跑法 `python -m unittest discover -s scripts/tests -p "test_task_manager.py"`。
- **FastAPI/starlette TestClient 需要 `httpx`**（仓库 .venv 未装），`from fastapi.testclient import TestClient` 直接 RuntimeError。路由冒烟改用 introspect：`[getattr(r,'path',None) for r in app.routes]` 检查路径是否注册。
- **`ResourceWarning: unclosed file`**：`json.load(open(...))` 不改 with 会刷警告；正则包裹 with 时注意内层缩进 `+1`（`re.sub` 捕获前导空白再加 `    rec = json.load(_f)`），一次直接 4 空格就 `IndentationError: expected an indented block after 'with'`。

## 假 runner 测试双（端到端停止协议）
`scripts/tests/_fake_runner.py`：心跳→循环 `check_stop()`→见停止 `finalize(41)`+`sys.exit(41)`，3s 超时 `finalize(0)`+`exit 0`。配合 `test_task_manager.TestE2EStop`（patch `TM._build_command=(lambda tt,w,p:[sys.executable,fake])`）验证：enqueue→scheduler_tick 启动→request_stop(tid,'asap')→reconcile 终态 `stopped`/rc=41。

## 运行中任务处理（2026-08-29 现场）
- **旧式直启进程无法被管理器接管**：工作台外终端直启的 `translate_runner.py --work X` 不在注册表（`data/tasks/` 空/无对应任务文件、无 `.runner.lock`/`runner_task.json`），管理器查不到。判定法：查注册表目录 + `work_dir` 下遗留锁文件 + `wmic process where "CommandLine like '%translate_runner.py --work X%'"`。要归入管理需**杀掉旧进程树 + 用 `task_manager enqueue` 重入队**（ckpt 可续跑，不丢进度）。
- **taskkill /T /F 杀进程树**：`wmic ... get ProcessId` 会自匹配到 wmic 自身进程 → 抓到的 pid 列表含噪音（tasklist 查无对应进程即噪音，忽略）。杀用 `taskkill /PID <pid> /T /F`。
- **`_pid_alive` WinError 87**：见 SKILL.md 坑。检测到运行中任务被误标 crashed 时先验证 `os.kill(pid,0)` 行为（对 21492/23800/99999999 全抛 WinError 87 即中招），再改 OpenProcess。
- **浏览器测试误点操作按钮**：tasks 页的「强制终止/译完本章后停」是真请求，浏览器自动化点错会真触发停止/强杀。操作前看清 ref，验证「分区刷新」用 `.log-box` 的 `style.display` / `dataset.loaded` 断言，别点按钮。
