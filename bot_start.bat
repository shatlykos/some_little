@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Нужны права администратора: если их нет — перезапускаемся с ними (один запрос Windows).
net session >nul 2>&1 || (powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList 'elevated' -Verb RunAs" & exit /b)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0service.ps1" start || goto :error
exit /b 0
:error
rem Окно задерживается только при ошибке, чтобы её можно было прочитать.
if "%~1"=="elevated" timeout /t 60
exit /b 1
