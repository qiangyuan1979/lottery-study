@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=C:\Users\18910\AppData\Local\Python\pythoncore-3.14-64\python.exe
if not exist "%PY%" set PY=python
set PYTHONIOENCODING=utf-8
echo [%date% %time%] start update_daily %*>> "%~dp0data\update.log"
"%PY%" update_daily.py %* >> "%~dp0data\update.log" 2>&1
echo [%date% %time%] exit %errorlevel%>> "%~dp0data\update.log"
