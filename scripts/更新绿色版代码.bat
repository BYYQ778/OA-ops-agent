@echo off
rem ============================================
rem  OA运维智能Agent - 更新绿色版外置代码（无需重新打包）
rem  把最新源码同步到 dist\OA运维Agent\app\，重启应用即生效
rem  用法: 更新绿色版代码.bat [/nopause]  （/nopause 供打包脚本内联调用）
rem ============================================
set "SRC=%~dp0.."
set "DST=%SRC%\dist\OA运维Agent\app"
if not exist "%DST%" mkdir "%DST%"
robocopy "%SRC%\ui"     "%DST%\ui"     /E /XD __pycache__ /NFL /NDL /NJH /NJS >nul
robocopy "%SRC%\agents" "%DST%\agents" /E /XD __pycache__ /NFL /NDL /NJH /NJS >nul
robocopy "%SRC%\utils"  "%DST%\utils"  /E /XD __pycache__ /NFL /NDL /NJH /NJS >nul
copy /Y "%SRC%\main.py" "%DST%\main.py" >nul
echo [完成] 绿色版代码已更新: dist\OA运维Agent\app\
echo        重启 OA运维Agent.exe 即生效
if /i "%~1" neq "/nopause" pause
