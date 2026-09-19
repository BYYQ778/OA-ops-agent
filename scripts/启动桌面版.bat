@echo off
rem ============================================
rem  OA运维智能Agent - 桌面版启动
rem  说明: 用 pythonw 无控制台启动 desktop_app.py
rem        桌面快捷方式也指向 pythonw + desktop_app.py
rem ============================================
cd /d "%~dp0.."
start "" "env_new\Scripts\pythonw.exe" "desktop_app.py"