$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (!(Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (!(Test-Path $Python)) {
    $Python = Join-Path $Root "venv\Scripts\python.exe"
}
if (!(Test-Path $Python)) {
    throw "Python venv not found. Run scripts\setup_windows.ps1 first."
}

& $Python -m uvicorn app.main:app `
    --host 127.0.0.1 `
    --port 8000 `
    --ssl-certfile cert.pem `
    --ssl-keyfile cert-key.pem

