@echo off
chcp 65001 >nul
cd /d "%~dp0app"
echo Skein 启动中...
python main.py
echo.
echo （Skein 已退出。若上方有报错，请截图发给开发。）
pause
