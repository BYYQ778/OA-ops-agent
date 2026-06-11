@echo off
cd /d "%~dp0.."
@echo off
chcp 65001 >nul
title 鎵撳寘绂荤嚎閮ㄧ讲鍖?

echo 鈺斺晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晽
echo 鈺?  鎵撳寘 OA杩愮淮绯荤粺 绂荤嚎閮ㄧ讲鍖?               鈺?
echo 鈺氣晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨暆
echo.
echo 姝よ剼鏈湪鏈夌綉缁滅殑鏈哄櫒涓婅繍琛屼竴娆★紝鐢熸垚瀹屾暣绂荤嚎鍖呫€?
echo 鐒跺悗鎶婃暣涓枃浠跺す鎷疯礉鍒板唴缃戞湇鍔″櫒鍗冲彲鍙屽嚮鍚姩銆?
echo.

set "PKG_DIR=..\OA杩愮淮绯荤粺-绂荤嚎閮ㄧ讲鍖?
set "OFFLINE_DIR=%PKG_DIR%\offline"

echo [1/4] 鍒涘缓鐩綍缁撴瀯...
if exist "%PKG_DIR%" rd /s /q "%PKG_DIR%"
mkdir "%PKG_DIR%\oa-ops-agent" 2>nul
mkdir "%OFFLINE_DIR%\wheels" 2>nul
echo   鉁?鐩綍鍒涘缓瀹屾垚

echo.
echo [2/4] 澶嶅埗椤圭洰浠ｇ爜锛堟帓闄よ櫄鎷熺幆澧冨拰涓存椂鏂囦欢锛?..
xcopy "." "%PKG_DIR%\oa-ops-agent\" /E /I /Y /Q ^
  /EXCLUDE:.gitignore >nul 2>&1
:: 鎵嬪姩鎺掗櫎澶ф枃浠?
if exist "%PKG_DIR%\oa-ops-agent\env_new" rd /s /q "%PKG_DIR%\oa-ops-agent\env_new" 2>nul
if exist "%PKG_DIR%\oa-ops-agent\.git" rd /s /q "%PKG_DIR%\oa-ops-agent\.git" 2>nul
if exist "%PKG_DIR%\oa-ops-agent\.gradio" rd /s /q "%PKG_DIR%\oa-ops-agent\.gradio" 2>nul
if exist "%PKG_DIR%\oa-ops-agent\__pycache__" rd /s /q "%PKG_DIR%\oa-ops-agent\__pycache__" 2>nul
echo   鉁?浠ｇ爜澶嶅埗瀹屾垚

echo.
echo [3/4] 涓嬭浇绂荤嚎渚濊禆...

:: --- Python wheels ---
echo   - 涓嬭浇 Python 渚濊禆鍖?..
if exist "env_new\Scripts\python.exe" (
    env_new\Scripts\python.exe -m pip download -r requirements.txt -d "%OFFLINE_DIR%\wheels" -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
) else (
    python -m pip download -r requirements.txt -d "%OFFLINE_DIR%\wheels" -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
)
echo   鉁?Python 渚濊禆鍖呬笅杞藉畬鎴?

:: --- Ollama 瀹夎绋嬪簭 ---
echo   - 涓嬭浇 Ollama 瀹夎绋嬪簭...
powershell -Command "Invoke-WebRequest -Uri 'https://ollama.com/download/OllamaSetup.exe' -OutFile '%OFFLINE_DIR%\OllamaSetup.exe'" 2>nul
if %errorlevel% neq 0 (
    echo   鈿?Ollama 涓嬭浇澶辫触锛堝彲閫夛紝鍙墜鍔ㄤ笅杞芥斁鍏?offline 鐩綍锛?
)
echo   鉁?Ollama 瀹夎绋嬪簭涓嬭浇瀹屾垚

echo.
echo [4/4] 鐢熸垚鍚姩璇存槑...
(
echo OA杩愮淮鏅鸿兘Agent宸℃绯荤粺 - 绂荤嚎閮ㄧ讲鍖?
echo ========================================
echo.
echo 浣跨敤鏂规硶:
echo   1. 灏嗘鏂囦欢澶规嫹璐濆埌鐩爣鏈嶅姟鍣?
echo   2. 鍙屽嚮 oa-ops-agent\鍚姩.bat
echo   3. 棣栨鍚姩浼氳嚜鍔ㄥ畨瑁?Ollama + 妯″瀷 + Python 渚濊禆
echo   4. 娴忚鍣ㄨ嚜鍔ㄦ墦寮€ http://127.0.0.1:7860
echo.
echo 宸插寘鍚?
echo   - 椤圭洰浠ｇ爜 (oa-ops-agent\)
echo   - Ollama 瀹夎绋嬪簭 (offline\OllamaSetup.exe)
echo   - Python 渚濊禆鍖?(offline\wheels\)
echo.
echo 杩橀渶鎵嬪姩鍑嗗锛堝洜涓烘枃浠跺お澶э級:
echo   1. 鍦ㄦ湁缃戞満鍣ㄤ笂杩愯: ollama pull qwen2.5:7b
echo   2. 鎷疯礉 C:\Users\^<鐢ㄦ埛鍚峖>\.ollama 鍒扮洰鏍囨満鍣?
echo   3. 鎴栧湪鐩爣鏈哄櫒棣栨杩愯鏃惰鑴氭湰鑷姩鎷夊彇
) > "%PKG_DIR%\README.txt"
echo   鉁?鍚姩璇存槑鐢熸垚

echo.
echo 鈺斺晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晽
echo 鈺? 绂荤嚎閮ㄧ讲鍖呮墦鍖呭畬鎴愶紒                      鈺?
echo 鈺? 浣嶇疆: %PKG_DIR%                           鈺?
echo 鈺氣晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨暆
echo.
echo 涓嬩竴姝?
echo   1. (鍙€? 鍦ㄦ湁缃戞満鍣ㄤ笂杩愯: ollama pull qwen2.5:7b
echo      鐒跺悗鎷疯礉 C:\Users\%USERNAME%\.ollama 鍒?%PKG_DIR%\ollama-models\
echo   2. 鎶?%PKG_DIR% 鏁翠釜鏂囦欢澶规嫹璐濆埌鐩爣鏈嶅姟鍣?
echo   3. 鍦ㄧ洰鏍囨湇鍔″櫒涓婂弻鍑?oa-ops-agent\鍚姩.bat
echo.
pause
