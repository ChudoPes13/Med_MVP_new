$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (!(Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}

if (!(Test-Path ".venv")) {
    python -m venv .venv
}

$Python = Join-Path $Root ".venv\Scripts\python.exe"
& $Python -m pip install --upgrade pip setuptools wheel
& $Python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
& $Python -m pip install -r requirements.txt
& $Python scripts\download_models.py

Push-Location frontend
npm install
Pop-Location

Write-Host "Setup complete."

