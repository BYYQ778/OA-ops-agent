@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title OA运维智能Agent巡检系统

echo.
echo ╔══════════════════════════════════════════════╗
echo ║   OA运维多智能Agent巡检问答系统 v2.0        ║
echo ╚══════════════════════════════════════════════╝
echo.

:: ============================================
:: 第1步: 检查 Python
:: ============================================
echo [1/5] 检查 Python 环境...

:: 优先用项目自带虚拟环境
if exist "env_new\Scripts\python.exe" (
    set "PYTHON_EXE=env_new\Scripts\python.exe"
    echo   ✓ 使用项目虚拟环境
    goto :check_ollama
)

:: 系统 Python
where python >nul 2>&1
if %errorlevel%==0 (
    for /f "delims=" %%i in ('where python') do set "PYTHON_EXE=%%i"
    echo   ✓ 使用系统 Python
    goto :check_ollama
)

echo   ✗ 未找到 Python 3.9+
echo   请安装 Python 后重试
pause
exit /b 1

:: ============================================
:: 第2步: 检查/安装 Ollama (离线优先)
:: ============================================
:check_ollama

:: 读取配置
for /f "tokens=2" %%a in ('findstr "provider:" config.yaml') do set LLM_PROVIDER=%%a
echo [2/5] LLM: %LLM_PROVIDER%

echo %LLM_PROVIDER% | findstr /i "ollama" >nul
if errorlevel 1 (
    echo   ✓ 云端模式，跳过本地模型
    goto :install_deps
)

:: Ollama 已安装?
where ollama >nul 2>&1
if %errorlevel%==0 (
    echo   ✓ Ollama 已安装
    goto :ensure_service
)

:: 离线安装 Ollama
set "OLLAMA_EXE=..\offline\OllamaSetup.exe"
if not exist "%OLLAMA_EXE%" set "OLLAMA_EXE=.\offline\OllamaSetup.exe"
if not exist "%OLLAMA_EXE%" set "OLLAMA_EXE=ollama-install.exe"

if not exist "%OLLAMA_EXE%" (
    echo   ✗ 未找到 Ollama 安装程序！
    echo   请先在有网机器上运行「打包离线部署包.bat」
    echo   或手动下载 OllamaSetup.exe 放入 offline 目录
    pause
    exit /b 1
)

echo   正在安装 Ollama（离线）...
start /wait "" "%OLLAMA_EXE%" /S
echo   ✓ Ollama 安装完成

:: ============================================
:: 第3步: 启动 Ollama + 检查模型
:: ============================================
:ensure_service
echo [3/5] 检查 Ollama 服务...

:: 确保 Ollama 在运行
tasklist /FI "IMAGENAME eq ollama.exe" 2>nul | find /i "ollama.exe" >nul
if %errorlevel% neq 0 (
    echo   正在启动 Ollama...
    start "" "ollama" serve >nul 2>&1
    timeout /t 3 /nobreak >nul
)

:: 读取模型名
set "OLLAMA_MODEL=qwen2.5:7b"
for /f "tokens=2" %%a in ('findstr "model:" config.yaml ^| findstr /v "api_key\|base_url\|temperature\|max_tokens"') do set "OLLAMA_MODEL=%%a"

ollama list 2>nul | findstr /i "%OLLAMA_MODEL%" >nul
if %errorlevel%==0 (
    echo   ✓ 模型 %OLLAMA_MODEL% 已就绪
    goto :install_deps
)

:: 检查离线模型目录
set "MODEL_DIR=..\ollama-models"
if not exist "%MODEL_DIR%" set "MODEL_DIR=.\ollama-models"

if exist "%MODEL_DIR%" (
    echo   正在导入离线模型...
    xcopy "%MODEL_DIR%" "%USERPROFILE%\.ollama\" /E /I /Y /Q >nul 2>&1
    ollama list 2>nul | findstr /i "%OLLAMA_MODEL%" >nul
    if %errorlevel%==0 (
        echo   ✓ 离线模型导入成功
        goto :install_deps
    )
)

echo   ✗ 模型未找到！
echo.
echo   请选择:
echo     [1] 联网拉取（需要网络）
echo     [2] 从有网机器拷贝 .ollama 目录到 ollama-models
echo     [3] 放弃，改用云端 API
echo.
choice /c 123 /n /m "请选择 (1/2/3): "
if errorlevel 3 goto :use_cloud
if errorlevel 2 goto :offline_guide
if errorlevel 1 goto :online_pull

:online_pull
echo   正在拉取模型 %OLLAMA_MODEL%（首次约 5-10 分钟）...
ollama pull %OLLAMA_MODEL%
if %errorlevel%==0 goto :install_deps
echo   ✗ 拉取失败
pause
exit /b 1

:offline_guide
echo.
echo   离线导入步骤:
echo     1. 在有网机器上: ollama pull %OLLAMA_MODEL%
echo     2. 拷贝 C:\Users\^<用户名^>\.ollama 文件夹
echo     3. 放入本项目的 ollama-models 目录
echo     4. 重新运行 启动.bat
echo.
pause
exit /b 1

:use_cloud
echo   已跳过本地模型，请修改 config.yaml:
echo     llm.provider: deepseek
pause
exit /b 1

:: ============================================
:: 第4步: 安装 Python 依赖（离线优先）
:: ============================================
:install_deps
echo [4/5] 检查 Python 依赖...
%PYTHON_EXE% -c "import gradio, langchain" >nul 2>&1
if %errorlevel%==0 (
    echo   ✓ 依赖已就绪
    goto :start_app
)

echo   正在安装依赖...

:: 离线优先
set "WHEELS_DIR=..\offline\wheels"
if not exist "%WHEELS_DIR%" set "WHEELS_DIR=.\offline\wheels"

if exist "%WHEELS_DIR%\*.whl" (
    echo   (离线安装)...
    %PYTHON_EXE% -m pip install --no-index --find-links="%WHEELS_DIR%" -r requirements.txt >nul 2>&1
) else (
    echo   (在线安装)...
    %PYTHON_EXE% -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn >nul 2>&1
)

if %errorlevel% neq 0 (
    echo   ✗ 依赖安装失败
    pause
    exit /b 1
)
echo   ✓ 依赖安装完成

:: ============================================
:: 第5步: 端口清理 + 启动 + 自检
:: ============================================
:start_app
echo [5/5] 启动 OA运维系统...

:: --- 5a. 清理旧进程 ---
set "PORT=7860"
echo   检查端口 %PORT%...

:: 查找占用端口的进程PID
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING" 2^>nul') do (
    set "OLD_PID=%%a"
    goto :kill_old
)
goto :no_old

:kill_old
echo   ⚠ 发现旧进程 (PID: %OLD_PID%) 占用端口 %PORT%，正在终止...
taskkill /PID %OLD_PID% /F >nul 2>&1
if %errorlevel%==0 (
    echo   ✓ 旧进程已终止
) else (
    echo   ⚠ 无法终止旧进程，请手动关闭后重试
    pause
    exit /b 1
)
timeout /t 2 /nobreak >nul

:no_old
echo   ✓ 端口 %PORT% 空闲

:: --- 5b. 启动服务 ---
echo   正在启动服务...
start "OA-Ops-Agent" /MIN %PYTHON_EXE% main.py --host 127.0.0.1

:: --- 5c. 轮询等待 + 自检 ---
echo   等待服务就绪（最多30秒）...
set "OK=0"
for /l %%i in (1,1,15) do (
    timeout /t 2 /nobreak >nul
    netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul 2>&1
    if not errorlevel 1 (
        set "OK=1"
    )
    if "!OK!"=="1" (
        echo   ✓ 服务启动成功 ^(%%i x 2秒^)
        echo.
        echo   ╔═══════════════════════════════════════╗
        echo   ║  服务已就绪                           ║
        echo   ║  浏览器打开 http://127.0.0.1:7860    ║
        echo   ║  关闭此窗口停止服务                   ║
        echo   ║  登录: admin / admin123               ║
        echo   ╚═══════════════════════════════════════╝
        echo.
        start "" http://127.0.0.1:7860
        goto :start_ok
    )
    echo   ...%%i/15
)

echo   ✗ 服务启动超时（30秒）！
echo   ============================================
echo   请检查:
echo     1. Python 环境是否正常
echo     2. config.yaml 配置是否正确
echo     3. 依赖是否完整安装
echo     4. 模型是否已下载
pause
exit /b 1

:start_ok
echo   按任意键打开浏览器，或等待5秒自动打开...
echo   关闭本窗口可停止服务
goto :eof
