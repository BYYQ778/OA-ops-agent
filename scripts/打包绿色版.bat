@echo off
rem ============================================
rem  OA运维智能Agent - 打包绿色版 exe
rem  产物: dist\OA运维Agent\OA运维Agent.exe
rem ============================================
cd /d "%~dp0.."
env_new\Scripts\python.exe -m PyInstaller oa_agent.spec --noconfirm --clean --distpath dist --workpath build
if %errorlevel% neq 0 (
    echo [错误] 打包失败，请检查控制台输出
    pause
    exit /b 1
)
echo.
echo [完成] 绿色版已生成: dist\OA运维Agent\OA运维Agent.exe
echo        整个 OA运维Agent 文件夹可拷贝到任意电脑直接运行（免装 Python）
pause