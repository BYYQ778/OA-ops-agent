@echo off
rem ============================================
rem  OA运维智能Agent - 打包绿色版 exe
rem  产物: dist\OA运维Agent\OA运维Agent.exe
rem         （业务代码外置在 app\ 目录，以后更新代码无需重新打包）
rem ============================================
cd /d "%~dp0.."

set "DISTDIR=dist\OA运维Agent"
set "BAKDIR=dist\_oa_backup"

rem ---- 备份运行时数据（打包会重建 DISTDIR，避免丢失 data/ 与配置） ----
if exist "%DISTDIR%" (
    if exist "%BAKDIR%" rmdir /s /q "%BAKDIR%"
    mkdir "%BAKDIR%"
    if exist "%DISTDIR%\data"       xcopy "%DISTDIR%\data" "%BAKDIR%\data\" /E /I /Q /Y >nul
    if exist "%DISTDIR%\.env"       copy /Y "%DISTDIR%\.env" "%BAKDIR%\.env" >nul
    if exist "%DISTDIR%\config.yaml" copy /Y "%DISTDIR%\config.yaml" "%BAKDIR%\config.yaml" >nul
)

env_new\Scripts\python.exe -m PyInstaller oa_agent.spec --noconfirm --clean --distpath dist --workpath build
if %errorlevel% neq 0 (
    echo [错误] 打包失败，请检查控制台输出
    pause
    exit /b 1
)

rem ---- 恢复运行时数据 ----
if exist "%BAKDIR%\data"       xcopy "%BAKDIR%\data" "%DISTDIR%\data\" /E /I /Q /Y >nul
if exist "%BAKDIR%\.env"       copy /Y "%BAKDIR%\.env" "%DISTDIR%\.env" >nul
if exist "%BAKDIR%\config.yaml" copy /Y "%BAKDIR%\config.yaml" "%DISTDIR%\config.yaml" >nul
rmdir /s /q "%BAKDIR%"

rem ---- 同步外置业务代码到 app\ ----
call scripts\更新绿色版代码.bat /nopause

echo.
echo [完成] 绿色版已生成: dist\OA运维Agent\OA运维Agent.exe
echo        整个 OA运维Agent 文件夹可拷贝到任意电脑直接运行（免装 Python）
pause
