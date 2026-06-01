@echo off
chcp 65001 >nul
title 打包离线部署包

echo ╔══════════════════════════════════════════════╗
echo ║   打包 OA运维系统 离线部署包                ║
echo ╚══════════════════════════════════════════════╝
echo.
echo 此脚本在有网络的机器上运行一次，生成完整离线包。
echo 然后把整个文件夹拷贝到内网服务器即可双击启动。
echo.

set "PKG_DIR=..\OA运维系统-离线部署包"
set "OFFLINE_DIR=%PKG_DIR%\offline"

echo [1/4] 创建目录结构...
if exist "%PKG_DIR%" rd /s /q "%PKG_DIR%"
mkdir "%PKG_DIR%\oa-ops-agent" 2>nul
mkdir "%OFFLINE_DIR%\wheels" 2>nul
echo   ✓ 目录创建完成

echo.
echo [2/4] 复制项目代码（排除虚拟环境和临时文件）...
xcopy "." "%PKG_DIR%\oa-ops-agent\" /E /I /Y /Q ^
  /EXCLUDE:.gitignore >nul 2>&1
:: 手动排除大文件
if exist "%PKG_DIR%\oa-ops-agent\env_new" rd /s /q "%PKG_DIR%\oa-ops-agent\env_new" 2>nul
if exist "%PKG_DIR%\oa-ops-agent\.git" rd /s /q "%PKG_DIR%\oa-ops-agent\.git" 2>nul
if exist "%PKG_DIR%\oa-ops-agent\.gradio" rd /s /q "%PKG_DIR%\oa-ops-agent\.gradio" 2>nul
if exist "%PKG_DIR%\oa-ops-agent\__pycache__" rd /s /q "%PKG_DIR%\oa-ops-agent\__pycache__" 2>nul
echo   ✓ 代码复制完成

echo.
echo [3/4] 下载离线依赖...

:: --- Python wheels ---
echo   - 下载 Python 依赖包...
if exist "env_new\Scripts\python.exe" (
    env_new\Scripts\python.exe -m pip download -r requirements.txt -d "%OFFLINE_DIR%\wheels" -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
) else (
    python -m pip download -r requirements.txt -d "%OFFLINE_DIR%\wheels" -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
)
echo   ✓ Python 依赖包下载完成

:: --- Ollama 安装程序 ---
echo   - 下载 Ollama 安装程序...
powershell -Command "Invoke-WebRequest -Uri 'https://ollama.com/download/OllamaSetup.exe' -OutFile '%OFFLINE_DIR%\OllamaSetup.exe'" 2>nul
if %errorlevel% neq 0 (
    echo   ⚠ Ollama 下载失败（可选，可手动下载放入 offline 目录）
)
echo   ✓ Ollama 安装程序下载完成

echo.
echo [4/4] 生成启动说明...
(
echo OA运维智能Agent巡检系统 - 离线部署包
echo ========================================
echo.
echo 使用方法:
echo   1. 将此文件夹拷贝到目标服务器
echo   2. 双击 oa-ops-agent\启动.bat
echo   3. 首次启动会自动安装 Ollama + 模型 + Python 依赖
echo   4. 浏览器自动打开 http://127.0.0.1:7860
echo.
echo 已包含:
echo   - 项目代码 (oa-ops-agent\)
echo   - Ollama 安装程序 (offline\OllamaSetup.exe)
echo   - Python 依赖包 (offline\wheels\)
echo.
echo 还需手动准备（因为文件太大）:
echo   1. 在有网机器上运行: ollama pull qwen2.5:7b
echo   2. 拷贝 C:\Users\^<用户名^>\.ollama 到目标机器
echo   3. 或在目标机器首次运行时让脚本自动拉取
) > "%PKG_DIR%\README.txt"
echo   ✓ 启动说明生成

echo.
echo ╔══════════════════════════════════════════════╗
echo ║  离线部署包打包完成！                      ║
echo ║  位置: %PKG_DIR%                           ║
echo ╚══════════════════════════════════════════════╝
echo.
echo 下一步:
echo   1. (可选) 在有网机器上运行: ollama pull qwen2.5:7b
echo      然后拷贝 C:\Users\%USERNAME%\.ollama 到 %PKG_DIR%\ollama-models\
echo   2. 把 %PKG_DIR% 整个文件夹拷贝到目标服务器
echo   3. 在目标服务器上双击 oa-ops-agent\启动.bat
echo.
pause
