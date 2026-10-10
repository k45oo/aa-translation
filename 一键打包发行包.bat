@echo off
chcp 65001 >nul
title AA Release Packager
cd /d "%~dp0"

rem ============================================================
rem  一键打包发行包（干净源码 + AA工作台.exe + 示例作品 + 宝可梦wiki素材）
rem  顺序: staging -> 变换 -> 脱敏 -> 硬门禁 -> 打 exe -> 压包 -> 解包冒烟
rem  用法:  一键打包发行包.bat               默认输出 导出\
rem          一键打包发行包.bat --no-verify  跳过解包冒烟（快，但不保险）
rem  说明:  若 导出\ 里跑着工作台实例（数据根），构建脚本拒绝覆盖它，
rem          本脚本自动改用 导出_新\ 重跑，你的实例不受影响。
rem ============================================================

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [x] 没找到工作台 venv: %PY%
    echo     先双击「安装依赖.bat」，再跑本脚本。
    pause
    exit /b 1
)

set "EXTRA=%*"
set "OUTARG=--out 导出"
if not "%EXTRA%"=="" set "OUTARG="

echo [*] 开始打包发行包（约 5~10 分钟：会跑包内单测 + exe 冒烟）
echo     "%PY%" scripts\tools\build_release.py %OUTARG% %EXTRA%
echo.
"%PY%" scripts\tools\build_release.py %OUTARG% %EXTRA%
set "RC=%ERRORLEVEL%"

if "%EXTRA%"=="" if "%RC%"=="6" goto retry
if not "%RC%"=="0" goto fail
goto ok

:retry
echo.
echo [i] 导出\ 里检测到工作台实例的数据（.env / aaManga 等），构建脚本拒绝覆盖它。
echo     自动改用 导出_新\ 重跑一次（你的实例不受影响）。
echo.
"%PY%" scripts\tools\build_release.py --out 导出_新
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto fail
set "OUTARG=--out 导出_新"

:ok
echo.
echo [ok] 打包完成。产物（目录 + zip）:
for /f "delims=" %%z in ('dir /b /o-d "导出\*.zip" "导出_新\*.zip" 2^>nul') do echo      %%z
echo.
echo     下一步: 把 zip 发出去即可。
pause
exit /b 0

:fail
echo.
echo [x] 打包失败（退出码 %RC%）。往上翻最后几行看原因，常见:
echo     - 脱敏残留 / 密钥扫描命中 -> 按提示改源码后再跑
echo     - 包内单测失败            -> 先修测试（基线外的失败会直接拦下）
echo     - exe 探测失败            -> 看探测那一行的说明
pause
exit /b %RC%
