# Управление фоновой работой бота через Планировщик заданий Windows.
#   service.ps1 install  — включить автозапуск и запустить бота в фоне
#   service.ps1 remove   — выключить автозапуск и остановить бота
#   service.ps1 start | stop | status
param([string]$Action = "status")

$ErrorActionPreference = "Stop"
$Name = "bronbot"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Py = Join-Path $Root "venv\Scripts\pythonw.exe"

function Show-Log {
    $log = Join-Path $Root "bot.log"
    if (Test-Path $log) {
        Write-Host "`nПоследние строки bot.log:" -ForegroundColor Cyan
        Get-Content $log -Tail 12 -Encoding UTF8
    }
}

function Get-Task { Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue }

try {
    switch ($Action) {
        "install" {
            if (-not (Test-Path $Py)) { throw "Не найден $Py. Сначала запустите install.bat" }
            $act = New-ScheduledTaskAction -Execute $Py -Argument "-m bot" -WorkingDirectory $Root
            $trigger = New-ScheduledTaskTrigger -AtStartup
            $settings = New-ScheduledTaskSettingsSet `
                -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
                -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable `
                -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
            $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
            Register-ScheduledTask -TaskName $Name -Action $act -Trigger $trigger `
                -Settings $settings -Principal $principal -Force | Out-Null
            Start-ScheduledTask -TaskName $Name
            Start-Sleep -Seconds 8
            Write-Host "Готово: бот работает в фоне и будет запускаться сам при включении сервера." -ForegroundColor Green
            Write-Host "Окно run.bat больше не нужно (и не запускайте его одновременно с фоновым ботом)."
            Show-Log
        }
        "remove" {
            if (Get-Task) {
                Stop-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
                Unregister-ScheduledTask -TaskName $Name -Confirm:$false
            }
            Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
                Where-Object { $_.CommandLine -like "*-m bot*" } |
                ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
            Write-Host "Автозапуск выключен, фоновый бот остановлен." -ForegroundColor Yellow
        }
        "start" {
            if (-not (Get-Task)) { throw "Фоновый режим не включён. Запустите background_on.bat" }
            Start-ScheduledTask -TaskName $Name
            Start-Sleep -Seconds 8
            Write-Host "Бот запущен в фоне." -ForegroundColor Green
            Show-Log
        }
        "stop" {
            if (Get-Task) { Stop-ScheduledTask -TaskName $Name }
            Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
                Where-Object { $_.CommandLine -like "*-m bot*" } |
                ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
            Write-Host "Бот остановлен (автозапуск при включении сервера остаётся)." -ForegroundColor Yellow
        }
        default {
            $t = Get-Task
            if (-not $t) {
                Write-Host "Фоновый режим не включён." -ForegroundColor Yellow
            } else {
                $state = $t.State
                $running = [bool](Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
                    Where-Object { $_.CommandLine -like "*-m bot*" })
                if ($running) { Write-Host "Бот работает в фоне." -ForegroundColor Green }
                else { Write-Host "Бот НЕ работает (задание: $state). Смотрите bot.log ниже." -ForegroundColor Red }
            }
            Show-Log
        }
    }
} catch {
    Write-Host "Ошибка: $($_.Exception.Message)" -ForegroundColor Red
}
Read-Host "`nНажмите Enter, чтобы закрыть"
