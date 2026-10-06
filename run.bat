@echo off
chcp 65001 >nul
cd /d "%~dp0"
call venv\Scripts\activate.bat
:loop
python -m bot
echo Бот остановился. Перезапуск через 10 секунд... (закройте окно, чтобы остановить)
timeout /t 10 /nobreak >nul
goto loop
