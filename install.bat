@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Создаю виртуальное окружение...
python -m venv venv || (echo Не найден Python. Установите Python 3.11+ с python.org и отметьте "Add to PATH". & pause & exit /b 1)
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt || (pause & exit /b 1)
if not exist config.yaml copy config.example.yaml config.yaml
if not exist .env copy .env.example .env
echo.
echo Готово. Теперь заполните .env и config.yaml (см. README.md), затем authorize.bat и check.bat
pause
