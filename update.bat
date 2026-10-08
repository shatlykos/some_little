@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "ROOT=%~dp0"
rem Нужны права администратора: если их нет — перезапускаемся с ними (один запрос Windows).
net session >nul 2>&1 || (powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList 'elevated' -Verb RunAs" & exit /b)
rem Обновление: скачать новую версию; если она есть — остановить бота,
rem при необходимости доустановить библиотеки и запустить снова.
rem Всё в одном блоке ( ): git pull может изменить и этот файл, а блок читается целиком заранее.
(
  for /f %%i in ('git rev-parse HEAD') do set OLD=%%i
  git pull -q || goto :error
  for /f %%i in ('git rev-parse HEAD') do set NEW=%%i
  call :apply || goto :error
  exit /b 0
)
:apply
if "%OLD%"=="%NEW%" (
  echo Обновлений нет, бот продолжает работать.
  exit /b 0
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%service.ps1" stop || exit /b 1
git diff --quiet %OLD% %NEW% -- requirements.txt || (
  call venv\Scripts\activate.bat
  pip install -q -r requirements.txt || exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%service.ps1" install || exit /b 1
exit /b 0
:error
rem Окно задерживается только при ошибке, чтобы её можно было прочитать.
if "%~1"=="elevated" timeout /t 60
exit /b 1
