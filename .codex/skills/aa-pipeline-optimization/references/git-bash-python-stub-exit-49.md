# git-bash 裸 `python` 是 WindowsApps stub(exit 49,无输出)— 2026-08-19 实测

## 症状
git-bash 里 `python --version` 输出**空**且 `exit=49`,`python temp.py` 同样 exit 49 无任何 stdout/stderr。这不是 Python 崩了,是 MSYS 命中了 Windows 的 **App Execution Alias stub**:
- `which python` → `<user>/AppData/Local/Microsoft/WindowsApps/python`(不是真 python)
- 该 stub 只在 Windows Store Python 未安装时存在,直接调起失败(49)或弹商店

## 根因
Git-bash 的 PATH 里 `WindowsApps` 目录排在真实 Python 之前。系统里没有 `py` 启动器(实测 `py: command not found`)。**uv 的 python 实际存在**(2026-08-29 `uv python list` 实测,旧文"为空/不存在"已过时):`<appdata>\uv\python\cpython-3.11.15-windows-x86_64-none\python.exe` 与 `<user>\.local\bin\python3.11.exe`。

## 正确处理(本机可用 python)
1. **uv 管理的 Python 3.11.15(AA 管线首选,2026-08-29 实测)**: `<user>/.local/bin/python3.11.exe`(或 `<appdata>/uv/python/cpython-3.11.15-windows-x86_64-none/python.exe`)。**AA 翻译脚本用 `tomllib`(py3.11 标准库),AGENTS 要求 Python 3.11**——裸 `python` 是 stub;项目 `.venv` 是 **3.12.13(非 3.11)**;Hermes venv 虽是 3.11.15 但那是 Hermes 自家的,不宜挂靠项目管线。所以后台跑 translate_runner/aa_translate 一律用这个 uv 3.11.15 全路径。
2. **Hermes venv python**: `<hermes>/hermes-agent/venv/Scripts/python.exe`(同样 3.11.15,可在 uv 3.11 路径不可用时兜底)
3. 项目 `.venv`(AA 项目 `scripts/tools/aa_web.py` 启动器会弹掉 PYTHONPATH 劫持;注意它 **3.12**,跑翻译脚本时与 AGENTS 的 3.11 约定不符,优先第 1 条)
4. 显式路径,勿裸 `python`

**验证过的后台启动命令(2026-08-29 pon_slime 免费模式实测)**: `cd <repo> && env AA_FREE_MODE=1 AA_FREE_WORKERS=6 <user>/.local/bin/python3.11.exe -u scripts/runner/translate_runner.py --work <id> --no-dl > <work>/runner_log.txt 2>&1`。失败特征=exit_code 49 + "bash: no job control in this shell"——别用裸 `python` 起后台任务。

## 影响
- `python temp_sf_test.py` 在 git-bash 跑 exit 49 → 误判"脚本执行异常"
- **heredoc `python - <<'PYEOF'` 同样命中 stub**(exit 49 无输出)——所以"用 heredoc 跑修复脚本"的旧建议在裸 `python` 下也不可用,必须用 venv python 全路径
- 与 AA 翻译 skill 里"hook 命令用 venv python 全路径,别裸写 python"的既有规则同根——本文件解释 WHY

## 验证
```bash
which python                # → WindowsApps stub(坏)
<hermes>/hermes-agent/venv/Scripts/python.exe --version   # → Python 3.x(好)
```
