@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=C:\Users\18910\AppData\Local\Python\pythoncore-3.14-64\python.exe
if not exist "%PY%" set PY=python
set URL=http://127.0.0.1:8123

netstat -ano | findstr /r /c:":8123 .*LISTENING" >nul
if not errorlevel 1 goto already

echo 正在启动彩票预估网站 ...
start "彩票预估网站 :8123" /min cmd /c "%PY% server.py --port 8123 --host 127.0.0.1 >> data\server.log 2>&1"

ping -n 4 127.0.0.1 >nul
netstat -ano | findstr /r /c:":8123 .*LISTENING" >nul
if errorlevel 1 (
  echo [失败] 端口 8123 未监听，请查看 data\server.log
  pause
  exit /b 1
)

echo [成功] 已启动 %URL%
echo 访问 %URL%    日志 data\server.log    停止请运行 stop_site.cmd
start "" "%URL%"
exit /b 0

:already
echo [提示] 网站已在运行 %URL%
start "" "%URL%"
exit /b 0
