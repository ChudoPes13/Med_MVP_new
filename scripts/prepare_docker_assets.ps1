$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$TtsSource = Join-Path $Root "v5_4_ru.pt"
$TtsTargetDir = Join-Path $Root "docker_assets\models\tts"

New-Item -ItemType Directory -Force $TtsTargetDir | Out-Null

if (!(Test-Path $TtsSource)) {
    throw "Silero TTS model not found: $TtsSource"
}

Copy-Item -Force $TtsSource (Join-Path $TtsTargetDir "v5_4_ru.pt")

Write-Host "Docker assets prepared:"
Write-Host " - docker_assets\models\tts\v5_4_ru.pt"
