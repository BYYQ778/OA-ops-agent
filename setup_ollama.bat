@echo off
chcp 65001 >nul
echo ============================================
echo   Ollama 安装后一键配置脚本
echo ============================================
echo.

:: 检查 Ollama
where ollama >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Ollama 未安装，请先运行 OllamaSetup.exe
    pause
    exit /b 1
)

echo [1/3] Ollama 已安装
ollama --version
echo.

:: 拉取 qwen3:8b
echo [2/3] 拉取 qwen3:8b 模型 (约5GB，需要几分钟)...
ollama pull qwen3:8b
echo.

:: 验证
echo [3/3] 验证模型...
ollama list
echo.

echo ============================================
echo   配置完成! 运行以下命令启动 OA 系统:
echo   cd /d E:\YunweiAgent\oa-ops-agent
echo   python main.py
echo ============================================
pause
