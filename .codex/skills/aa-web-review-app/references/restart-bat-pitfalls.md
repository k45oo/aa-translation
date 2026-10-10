# 一键重启 bat 的 Windows 批处理坑（2026-08-11 实测）

场景：`<repo>\一键重启工作台.bat`（杀 8765 旧实例 → 延时 → 清 PYTHONPATH → 启动 aa_web.py）。

## 坑 ①：.bat 必须 CRLF 行尾（write_file 写的是 LF）

- 症状链：LF-only bat 被 cmd 解析器**错误拼接行**——`rem` 注释被当命令执行（`'等端口释�?' 不是内部或外部命令`）、`findstr` 报 `'dstr' 不是内部或外部命令`、`start` 的标题串被当命令。
- 根因：cmd.exe 批处理解析器按 CR 分行，LF-only 文件整段进缓冲区后多字节字符处错切。
- 正确生成：
  ```python
  with open(BAT, "w", encoding="utf-8", newline="") as f:
      f.write("\r\n".join(lines) + "\r\n")
  ```
- 验证：`python -c` 数字节——`b.count(b'\r\n')` 应等于 `b.count(b'\n')` 且 >15，裸 LF = 0。

## 坑 ②：bat 内延时禁用 `timeout` 命令

- 症状：`timeout: invalid time interval '/t'`（MSYS/Git-Bash 的 `/usr/bin/timeout` 抢在 System32 的 timeout.exe 前面——任何从 git-bash 继承 PATH 的调用环境都会中招）。
- 用户双击 Explorer 时 PATH 正常不会触发，但为环境无关性：**用 `ping -n 2 127.0.0.1 >nul`**（≈1s，无依赖）。

## 坑 ③：中文名 bat 的测试调用方式

- git-bash 里 `cmd //c "一键重启工作台.bat"` → MSYS 把 UTF-8 参数转 ANSI 失败 → cmd 直接进**交互模式**（banner + 提示符），bat 根本没跑。
- 正确：`powershell -NoProfile -Command "& '<repo>\一键重启工作台.bat'"`（PowerShell 原生 Unicode 路径，等价双击）。
- 注意：bat 前台跑 python 会阻塞 → 测试时该命令放 `terminal(background=true)`，sleep 后另查端口。

## 坑 ④：启动模型选前台直跑，不用 `start "title" cmd /k`

- `start "AA汉化矫正工作台" cmd /k ".venv\Scripts\python.exe scripts\tools\aa_web.py"` 从 PowerShell 调用 bat 时**静默失败**（窗口未起、无错误输出）。
- 定稿模型：bat 前台直接 `".venv\Scripts\python.exe" scripts\tools\aa_web.py`——双击窗口即服务控制台（uvicorn 日志可见），关闭窗口即停服务，语义直观、零引号地狱。末尾 `pause` 兜底看错误。

## 坑 ⑤：echo/rem 里的 `>`（含 `->`）被当重定向

- 症状：每次运行 bat 都多打一行 `The filename, directory name, or volume label syntax is incorrect.`——`echo Starting ... -> http://127.0.0.1:8765/` 里的 `>` 被 cmd 当重定向，目标 `http:/...` 非法。
- 规则：bat 里**任何非重定向用途的 `>` 都不要写**——提示语用 `at` 而不是 `->`；`rem` 注释里也别出现 `>`/`<`/`|`（rem 行同样会做重定向解析）。改完用 dry-run 看输出里有没有多出来的报错行。

## 坑 ⑥：端口改成可配后，bat 必须跟着读 `.env`

- `AA_PORT` 落到 `.env` 后，bat 里的 `netstat ... findstr :8765` 会**杀错对象**（服务在新端口，旧端口可能被别的进程占）。
- 解析（实测 LF 与 CRLF 都正确剥离行尾 CR，比 `findstr` 稳）：
  ```bat
  if defined AA_PORT set "AA_PORT=%AA_PORT%"
  if not defined AA_PORT set "AA_PORT=8765"
  if exist "%~dp0.env" for /f "usebackq tokens=1,* delims==" %%a in ("%~dp0.env") do if /i "%%a"=="AA_PORT" set "AA_PORT=%%b"
  ```
  注释行/其它键自然被 `%%a` 不等于 `AA_PORT` 掉掉；无需去 CR。
- **别在 `for /f` 反引号里用带引号的绝对路径调 python 去读 `.env`**（`\`"F:\...\python.exe" -c "..."\`` 解析失败、静默落到 fallback 8765），也不用 `findstr`（值自带 CR 会匹配不上）。
- `AA_PORT` 的优先级要与 python 侧一致：真实环境变量 > `.env` > 8765；两边不一致时以 python `aa_env.get('AA_PORT') or 8765` 的结果为准。

## 坑 ⑦：改 bat 的验证姿势（不要真重启）

- 把 `taskkill /f /pid` 行换成 `echo WOULD-KILL` 做 dry-run（放在仓库根，让 `%~dp0` 指向真实目录），确认端口解析 + netstat 命中真实监听 pid + wmic 命中 `aa_web.py` 双进程。
- **替换出来的 `echo` 行记得去掉行尾 `>nul 2>nul`** —— 否则连自己的输出一起吞，看起来像「循环没匹配到」，会误判成解析错了。
- 编码：写回用 `encoding='gbk'` + `newline=''` + 手拼 `\r\n`；**文案保持全 ASCII**（`chcp 65001` 的窗口里 GBK 中文 rem 有解码歧义，没必要冒风险），落盘后数一下 CRLF 数与控制字符。

## 双层转义坑（bat 生成器链路）

- 通过 terminal heredoc 写 python 字符串 `"scripts\tools\aa_web.py"`：工具参数 JSON 层把 `\\t` 减半成 `\t` → heredoc 内容进 python 字符串字面量 → `\t` 再被解释成**真实 TAB**（`\a` → BELL）→ 生成文件里是 `scripts<TAB>ools<BELL>a_web.py`。
- 伪装症状：python 报 `can't find '__main__' module in '<repo>\scripts'`（脚本路径被 TAB 截断成目录），看文件内容"好像正常"。
- 修复：**write_file 写生成器脚本**（内容原样保留，无 JSON/heredoc 层）+ 含反斜杠部分用 raw 字符串 `r"scripts\tools\aa_web.py"`，行尾 `"\r\n"` 单独拼；落盘后 `od -c`/字节计数校验目标行。详见 skill patch-escaping。
