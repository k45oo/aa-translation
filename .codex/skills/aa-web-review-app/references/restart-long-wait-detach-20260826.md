# 重启工作台「长等/卡死需手动中断」根治（2026-08-26 实测）

## 症状
用 `python -c "subprocess.Popen(['cmd','/c','start','','一键重启工作台.bat'], cwd=仓库根)"` 从
Hermes terminal 触发重启，命令**永不返回** → 表现为「触发后又长等」「卡死了」「我手动中断了」。
本会话被用户中断 4+ 次，是用户最反感的反复问题。

## 根因
`subprocess.Popen` 派生的进程**继承了 terminal 的 stdout/stderr 文件描述符**。
bat 里 `start` 起的独立窗口（uvicorn 长驻）不结束，terminal 工具检测到仍有子进程持有其 fd，
**会一直等所有派生进程结束** → 命令卡住，看似「触发了但没返回」。

## 根治（detach 触发）
用 python heredoc（不要 `python -c` 嵌套引号）：
```python
import subprocess, os
subprocess.Popen(['cmd', '/c', 'start', '', '一键重启工作台.bat'],
                 cwd=os.getcwd(), shell=False,
                 creationflags=0x8 | 0x200,   # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                 stdin=subprocess.DEVNULL, close_fds=True)
print('已detach触发')
```
`creationflags` + 三路 DEVNULL + `close_fds=True` 让进程**完全脱离** terminal 的 fd 与进程组 →
terminal 立即返回（实测通过）。

## 铁律
1. **触发即返回，绝不 sleep/验证端口/curl**——工作台是独立窗口异步启动，验证交给
   用户确认或下一次自然触发（每次「触发后又去 sleep 检查」都重新触发长等）。
2. 禁止用 `cmd //c "中文.bat"` 直接调（MSYS 编码乱码，见图 restart-bat-pitfalls）。
3. 杀旧端口进程用精确 PID：`Get-NetTCPConnection -LocalPort 8765 | Select OwningProcess | Stop-Process`,
   严禁 `Get-Process python*|Stop-Process`（会杀 Hermes TUI 自身）。

## 顺带：改 bat 的反斜杠转义坑（886f8b7）
python heredoc 写 bat 时 `scripts\tools\aa_web.py` 的 `\t`/`\a`/`\n` 会被解释成**控制字符**
（`\a`→BEL \u0007、`\t`→制表符）→ 启动命令被破坏 → python「找不到启动文件」→ 服务起不来。
修复：用 `r'...'` raw string + 逐行列表构建；写后 `data.decode('gbk')` 检查启动行无 `\x07`/`\t`
（勿信 repr——双重转义显示骗你）；bat 是 **GBK 编码**（写用 `encoding='gbk'`）+ CRLF 换行。

## 相关
- 重启后代码不生效/旧进程占端口 → references/restart-verify-pitfall-20260825.md
- bat 通用批处理坑（CRLF/timeout/中文名/前台）→ references/restart-bat-pitfalls.md
