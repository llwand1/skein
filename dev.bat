@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Skein 开发模式（改 app 下 .py 会自动重启）。关掉这个窗口 = 停止监视。
echo.
python dev-watch.py
echo.
echo （监视器已退出，Skein 仍在运行。）
pause
