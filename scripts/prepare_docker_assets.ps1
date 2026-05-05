param(
    [string]$ModelFile = $(if ($env:TTS_MODEL_FILE) { $env:TTS_MODEL_FILE } else { "v5_5_ru.pt" }),
    [string]$ModelUrl = $(if ($env:TTS_MODEL_URL) { $env:TTS_MODEL_URL } else { "https://models.silero.ai/models/tts/ru/v5_5_ru.pt" })
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$TtsSource = Join-Path $Root $ModelFile
$TtsTargetDir = Join-Path $Root "docker_assets\models\tts"
$TtsTarget = Join-Path $TtsTargetDir $ModelFile

New-Item -ItemType Directory -Force $TtsTargetDir | Out-Null

if (Test-Path $TtsSource) {
    Copy-Item -Force $TtsSource $TtsTarget
} elseif (!(Test-Path $TtsTarget)) {
    Write-Host "Downloading Silero TTS model from $ModelUrl"
    Invoke-WebRequest -Uri $ModelUrl -OutFile $TtsTarget
}

Write-Host "Docker assets prepared:"
Write-Host " - docker_assets\models\tts\$ModelFile"
