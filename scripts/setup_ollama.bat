@echo off
cd /d "%~dp0.."
@echo off
chcp 65001 >nul
echo ============================================
echo   Ollama 瀹夎鍚庝竴閿厤缃剼鏈?
echo ============================================
echo.

:: 妫€鏌?Ollama
where ollama >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Ollama 鏈畨瑁咃紝璇峰厛杩愯 OllamaSetup.exe
    pause
    exit /b 1
)

echo [1/3] Ollama 宸插畨瑁?
ollama --version
echo.

:: 鎷夊彇 qwen3:8b
echo [2/3] 鎷夊彇 qwen3:8b 妯″瀷 (绾?GB锛岄渶瑕佸嚑鍒嗛挓)...
ollama pull qwen3:8b
echo.

:: 楠岃瘉
echo [3/3] 楠岃瘉妯″瀷...
ollama list
echo.

echo ============================================
echo   閰嶇疆瀹屾垚! 杩愯浠ヤ笅鍛戒护鍚姩 OA 绯荤粺:
echo   cd /d E:\YunweiAgent\oa-ops-agent
echo   python main.py
echo ============================================
pause
