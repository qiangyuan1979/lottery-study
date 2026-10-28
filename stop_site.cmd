@echo off
chcp 65001 >nul
cd /d "%~dp0"
set FOUND=0

echo 正在停止彩票预估网站 ...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /r /c:":8123 .*LISTENING"') do (
  set FOUND=1
  taskkill /PID %%a /F >nul 2>&1
  if errorlevel 1 (
    echo [失败] 无法停止进程 %%a
  ) else (
    echo [成功] 已停止进程 %%a
  )
)

if "%FOUND%"=="0" (
  echo [提示] 网站未在运行
  exit /b 0
)

ping -n 2 127.0.0.1 >nul
netstat -ano | findstr /r /c:":8123 .*LISTENING" >nul
if errorlevel 1 (
  echo [完成] 端口 8123 已释放
) else (
  echo [警告] 端口 8123 仍被占用
)
exit /b 0
