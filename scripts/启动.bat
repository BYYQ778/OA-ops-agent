@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title OA杩愮淮鏅鸿兘Agent宸℃绯荤粺

echo.
echo 鈺斺晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晽
echo 鈺?  OA杩愮淮澶氭櫤鑳紸gent宸℃闂瓟绯荤粺 v2.0        鈺?
echo 鈺氣晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨暆
echo.

:: ============================================
:: 绗?姝? 妫€鏌?Python
:: ============================================
echo [1/5] 妫€鏌?Python 鐜...

:: 浼樺厛鐢ㄩ」鐩嚜甯﹁櫄鎷熺幆澧?
if exist "env_new\Scripts\python.exe" (
    set "PYTHON_EXE=env_new\Scripts\python.exe"
    echo   鉁?浣跨敤椤圭洰铏氭嫙鐜
    goto :check_ollama
)

:: 绯荤粺 Python
where python >nul 2>&1
if %errorlevel%==0 (
    for /f "delims=" %%i in ('where python') do set "PYTHON_EXE=%%i"
    echo   鉁?浣跨敤绯荤粺 Python
    goto :check_ollama
)

echo   鉁?鏈壘鍒?Python 3.9+
echo   璇峰畨瑁?Python 鍚庨噸璇?
pause
exit /b 1

:: ============================================
:: 绗?姝? 妫€鏌?瀹夎 Ollama (绂荤嚎浼樺厛)
:: ============================================
:check_ollama

:: 璇诲彇閰嶇疆
for /f "tokens=2" %%a in ('findstr "provider:" config.yaml') do set LLM_PROVIDER=%%a
echo [2/5] LLM: %LLM_PROVIDER%

echo %LLM_PROVIDER% | findstr /i "ollama" >nul
if errorlevel 1 (
    echo   鉁?浜戠妯″紡锛岃烦杩囨湰鍦版ā鍨?
    goto :install_deps
)

:: Ollama 宸插畨瑁?
where ollama >nul 2>&1
if %errorlevel%==0 (
    echo   鉁?Ollama 宸插畨瑁?
    goto :ensure_service
)

:: 绂荤嚎瀹夎 Ollama
set "OLLAMA_EXE=..\offline\OllamaSetup.exe"
if not exist "%OLLAMA_EXE%" set "OLLAMA_EXE=.\offline\OllamaSetup.exe"
if not exist "%OLLAMA_EXE%" set "OLLAMA_EXE=ollama-install.exe"

if not exist "%OLLAMA_EXE%" (
    echo   鉁?鏈壘鍒?Ollama 瀹夎绋嬪簭锛?
    echo   璇峰厛鍦ㄦ湁缃戞満鍣ㄤ笂杩愯銆屾墦鍖呯绾块儴缃插寘.bat銆?
    echo   鎴栨墜鍔ㄤ笅杞?OllamaSetup.exe 鏀惧叆 offline 鐩綍
    pause
    exit /b 1
)

echo   姝ｅ湪瀹夎 Ollama锛堢绾匡級...
start /wait "" "%OLLAMA_EXE%" /S
echo   鉁?Ollama 瀹夎瀹屾垚

:: ============================================
:: 绗?姝? 鍚姩 Ollama + 妫€鏌ユā鍨?
:: ============================================
:ensure_service
echo [3/5] 妫€鏌?Ollama 鏈嶅姟...

:: 纭繚 Ollama 鍦ㄨ繍琛?
tasklist /FI "IMAGENAME eq ollama.exe" 2>nul | find /i "ollama.exe" >nul
if %errorlevel% neq 0 (
    echo   姝ｅ湪鍚姩 Ollama...
    start "" "ollama" serve >nul 2>&1
    timeout /t 3 /nobreak >nul
)

:: 璇诲彇妯″瀷鍚?
set "OLLAMA_MODEL=qwen2.5:7b"
for /f "tokens=2" %%a in ('findstr "model:" config.yaml ^| findstr /v "api_key\|base_url\|temperature\|max_tokens"') do set "OLLAMA_MODEL=%%a"

ollama list 2>nul | findstr /i "%OLLAMA_MODEL%" >nul
if %errorlevel%==0 (
    echo   鉁?妯″瀷 %OLLAMA_MODEL% 宸插氨缁?
    goto :install_deps
)

:: 妫€鏌ョ绾挎ā鍨嬬洰褰?
set "MODEL_DIR=..\ollama-models"
if not exist "%MODEL_DIR%" set "MODEL_DIR=.\ollama-models"

if exist "%MODEL_DIR%" (
    echo   姝ｅ湪瀵煎叆绂荤嚎妯″瀷...
    xcopy "%MODEL_DIR%" "%USERPROFILE%\.ollama\" /E /I /Y /Q >nul 2>&1
    ollama list 2>nul | findstr /i "%OLLAMA_MODEL%" >nul
    if %errorlevel%==0 (
        echo   鉁?绂荤嚎妯″瀷瀵煎叆鎴愬姛
        goto :install_deps
    )
)

echo   鉁?妯″瀷鏈壘鍒帮紒
echo.
echo   璇烽€夋嫨:
echo     [1] 鑱旂綉鎷夊彇锛堥渶瑕佺綉缁滐級
echo     [2] 浠庢湁缃戞満鍣ㄦ嫹璐?.ollama 鐩綍鍒?ollama-models
echo     [3] 鏀惧純锛屾敼鐢ㄤ簯绔?API
echo.
choice /c 123 /n /m "璇烽€夋嫨 (1/2/3): "
if errorlevel 3 goto :use_cloud
if errorlevel 2 goto :offline_guide
if errorlevel 1 goto :online_pull

:online_pull
echo   姝ｅ湪鎷夊彇妯″瀷 %OLLAMA_MODEL%锛堥娆＄害 5-10 鍒嗛挓锛?..
ollama pull %OLLAMA_MODEL%
if %errorlevel%==0 goto :install_deps
echo   鉁?鎷夊彇澶辫触
pause
exit /b 1

:offline_guide
echo.
echo   绂荤嚎瀵煎叆姝ラ:
echo     1. 鍦ㄦ湁缃戞満鍣ㄤ笂: ollama pull %OLLAMA_MODEL%
echo     2. 鎷疯礉 C:\Users\^<鐢ㄦ埛鍚峖>\.ollama 鏂囦欢澶?
echo     3. 鏀惧叆鏈」鐩殑 ollama-models 鐩綍
echo     4. 閲嶆柊杩愯 鍚姩.bat
echo.
pause
exit /b 1

:use_cloud
echo   宸茶烦杩囨湰鍦版ā鍨嬶紝璇蜂慨鏀?config.yaml:
echo     llm.provider: deepseek
pause
exit /b 1

:: ============================================
:: 绗?姝? 瀹夎 Python 渚濊禆锛堢绾夸紭鍏堬級
:: ============================================
:install_deps
echo [4/5] 妫€鏌?Python 渚濊禆...
%PYTHON_EXE% -c "import fastapi, langchain" >nul 2>&1
if %errorlevel%==0 (
    echo   鉁?渚濊禆宸插氨缁?
    goto :start_app
)

echo   姝ｅ湪瀹夎渚濊禆...

:: 绂荤嚎浼樺厛
set "WHEELS_DIR=..\offline\wheels"
if not exist "%WHEELS_DIR%" set "WHEELS_DIR=.\offline\wheels"

if exist "%WHEELS_DIR%\*.whl" (
    echo   (绂荤嚎瀹夎)...
    %PYTHON_EXE% -m pip install --no-index --find-links="%WHEELS_DIR%" -r requirements.txt >nul 2>&1
) else (
    echo   (鍦ㄧ嚎瀹夎)...
    %PYTHON_EXE% -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn >nul 2>&1
)

if %errorlevel% neq 0 (
    echo   鉁?渚濊禆瀹夎澶辫触
    pause
    exit /b 1
)
echo   鉁?渚濊禆瀹夎瀹屾垚

:: ============================================
:: 绗?姝? 绔彛娓呯悊 + 鍚姩 + 鑷
:: ============================================
:start_app
echo [5/5] 鍚姩 OA杩愮淮绯荤粺...

:: --- 5a. 娓呯悊鏃ц繘绋?---
set "PORT=7860"
echo   妫€鏌ョ鍙?%PORT%...

:: 鏌ユ壘鍗犵敤绔彛鐨勮繘绋婸ID
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING" 2^>nul') do (
    set "OLD_PID=%%a"
    goto :kill_old
)
goto :no_old

:kill_old
echo   鈿?鍙戠幇鏃ц繘绋?(PID: %OLD_PID%) 鍗犵敤绔彛 %PORT%锛屾鍦ㄧ粓姝?..
taskkill /PID %OLD_PID% /F >nul 2>&1
if %errorlevel%==0 (
    echo   鉁?鏃ц繘绋嬪凡缁堟
) else (
    echo   鈿?鏃犳硶缁堟鏃ц繘绋嬶紝璇锋墜鍔ㄥ叧闂悗閲嶈瘯
    pause
    exit /b 1
)
timeout /t 2 /nobreak >nul

:no_old
echo   鉁?绔彛 %PORT% 绌洪棽

:: --- 5b. 鍚姩鏈嶅姟 ---
echo   姝ｅ湪鍚姩鏈嶅姟...
start "OA-Ops-Agent" /MIN %PYTHON_EXE% main.py --host 127.0.0.1

:: --- 5c. 杞绛夊緟 + 鑷 ---
echo   绛夊緟鏈嶅姟灏辩华锛堟渶澶?0绉掞級...
set "OK=0"
for /l %%i in (1,1,15) do (
    timeout /t 2 /nobreak >nul
    netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul 2>&1
    if not errorlevel 1 (
        set "OK=1"
    )
    if "!OK!"=="1" (
        echo   鉁?鏈嶅姟鍚姩鎴愬姛 ^(%%i x 2绉抆)
        echo.
        echo   鈺斺晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
        echo   鈺? 鏈嶅姟宸插氨缁?                          鈺?
        echo   鈺? 娴忚鍣ㄦ墦寮€ http://127.0.0.1:7860    鈺?
        echo   鈺? 鍏抽棴姝ょ獥鍙ｅ仠姝㈡湇鍔?                  鈺?
        echo   鈺? 鐧诲綍: admin / admin123               鈺?
        echo   鈺氣晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
        echo.
        start "" http://127.0.0.1:7860
        goto :start_ok
    )
    echo   ...%%i/15
)

echo   鉁?鏈嶅姟鍚姩瓒呮椂锛?0绉掞級锛?
echo   ============================================
echo   璇锋鏌?
echo     1. Python 鐜鏄惁姝ｅ父
echo     2. config.yaml 閰嶇疆鏄惁姝ｇ‘
echo     3. 渚濊禆鏄惁瀹屾暣瀹夎
echo     4. 妯″瀷鏄惁宸蹭笅杞?
pause
exit /b 1

:start_ok
echo   鎸変换鎰忛敭鎵撳紑娴忚鍣紝鎴栫瓑寰?绉掕嚜鍔ㄦ墦寮€...
echo   鍏抽棴鏈獥鍙ｅ彲鍋滄鏈嶅姟
goto :eof
