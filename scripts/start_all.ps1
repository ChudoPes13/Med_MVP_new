$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

& "$PSScriptRoot\start_llama.ps1"

$Backend = Start-Process powershell `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "$PSScriptRoot\start_backend.ps1") `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -PassThru

$Frontend = Start-Process powershell `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "$PSScriptRoot\start_frontend.ps1") `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -PassThru

Write-Host "Backend PID: $($Backend.Id)"
Write-Host "Frontend PID: $($Frontend.Id)"
Write-Host "Open https://127.0.0.1:5173"

