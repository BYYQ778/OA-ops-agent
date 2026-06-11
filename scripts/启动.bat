@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0.."
chcp 65001 >nul
title OA运维智能Agent巡检系统

echo.
echo ╔══════════════════════════════════════════════╗
echo ║  OA运维多智能Agent巡检问答系统 v2.2         ║
echo ╚══════════════════════════════════════════════╝
echo.

:: ============================================
:: 第1步: 检查 Python
:: ============================================
echo [1/5] 检查 Python 环境...

if exist "env_new\Scripts\python.exe" (
    set "PYTHON_EXE=env_new\Scripts\python.exe"
    echo    √ 使用项目虚拟环境
    goto :check_ollama
)

where python >nul 2>&1
if %errorlevel%==0 (
    for /f "delims=" %%i in ('where python') do set "PYTHON_EXE=%%i"
    echo    √ 使用系统 Python
    goto :check_ollama
)

echo    × 未找到 Python
echo   请安装 Python 3.9+ 后重试
pause
exit /b 1

:: ============================================
:: 第2步: 检查 Ollama
:: ============================================
:check_ollama
for /f "tokens=2" %%a in ('findstr "provider:" config.yaml') do set LLM_PROVIDER=%%a
echo [2/5] LLM 后端: %LLM_PROVIDER%

echo %LLM_PROVIDER% | findstr /i "ollama" >nul
if errorlevel 1 (
    echo    √ 云端模式，跳过本地模型
    goto :install_deps
)

where ollama >nul 2>&1
if %errorlevel%==0 (
    echo    √ Ollama 已安装
    goto :ensure_service
)

echo    × 未找到 Ollama
echo   请先运行 scripts\setup_ollama.bat 安装 Ollama
pause
exit /b 1

:: ============================================
:: 第3步: 启动 Ollama + 检查模型
:: ============================================
:ensure_service
echo [3/5] 检查 Ollama 服务...

tasklist /FI "IMAGENAME eq ollama.exe" 2>nul | find /i "ollama.exe" >nul
if %errorlevel% neq 0 (
    echo   正在启动 Ollama...
    start "" "ollama" serve >nul 2>&1
    timeout /t 3 /nobreak >nul
)

set "OLLAMA_MODEL=qwen3:8b"
for /f "tokens=2" %%a in ('findstr "model:" config.yaml ^| findstr /v "api_key\|base_url\|temperature\|max_tokens"') do set "OLLAMA_MODEL=%%a"

ollama list 2>nul | findstr /i "%OLLAMA_MODEL%" >nul
if %errorlevel%==0 (
    echo    √ 模型 %OLLAMA_MODEL% 已就绪
    goto :install_deps
)

echo    × 模型 %OLLAMA_MODEL% 未找到，正在拉取...
ollama pull %OLLAMA_MODEL%
if %errorlevel%==0 goto :install_deps
echo    × 拉取失败，请检查网络或手动运行 ollama pull %OLLAMA_MODEL%
pause
exit /b 1

:: ============================================
:: 第4步: 安装依赖
:: ============================================
:install_deps
echo [4/5] 检查 Python 依赖...
%PYTHON_EXE% -c "import fastapi, langchain" >nul 2>&1
if %errorlevel%==0 (
    echo    √ 依赖已就绪
    goto :start_app
)

echo   正在安装依赖...
%PYTHON_EXE% -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn >nul 2>&1
if %errorlevel% neq 0 (
    echo    × 依赖安装失败
    pause
    exit /b 1
)
echo    √ 依赖安装完成

:: ============================================
:: 第5步: 端口清理 + 启动 + 自检
:: ============================================
:start_app
echo [5/5] 启动 OA运维系统...

set "PORT=7860"
echo   检查端口 %PORT%...

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING" 2^>nul') do (
    set "OLD_PID=%%a"
    goto :kill_old
)
goto :no_old

:kill_old
echo   ! 发现旧进程 (PID: %OLD_PID%) 占用端口 %PORT%，正在终止...
taskkill /PID %OLD_PID% /F >nul 2>&1
if %errorlevel%==0 (
    echo   √ 旧进程已终止
) else (
    echo   ! 无法终止旧进程，请手动关闭后重试
    pause
    exit /b 1
)
timeout /t 2 /nobreak >nul

:no_old
echo   √ 端口 %PORT% 空闲

echo   正在启动服务...
start "OA-Ops-Agent" /MIN %PYTHON_EXE% main.py --host 127.0.0.1

echo   等待服务就绪（最多30秒）...
set "OK=0"
for /l %%i in (1,1,15) do (
    timeout /t 2 /nobreak >nul
    netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul 2>&1
    if not errorlevel 1 (
        set "OK=1"
    )
    if "!OK!"=="1" (
        echo   √ 服务启动成功 (%%i x 2秒)
        echo.
        echo   ╔══════════════════════════════════════╗
        echo   ║  服务已就绪                         ║
        echo   ║  浏览器打开 http://127.0.0.1:7860   ║
        echo   ║  关闭此窗口可停止服务               ║
        echo   ╚══════════════════════════════════════╝
        echo.
        start "" http://127.0.0.1:7860
        goto :start_ok
    )
    echo   ...%%i/15
)

echo   × 服务启动超时（30秒）！
echo  请检查 Python 环境和 config.yaml 配置
pause
exit /b 1

:start_ok
echo   按任意键打开浏览器，或等待5秒自动打开...
echo   关闭此窗口可停止服务
goto :eof
