$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Tag = if ($env:DOCKER_IMAGE_TAG) { $env:DOCKER_IMAGE_TAG } else { "chudopes/medjarvis-registry:latest" }

New-Item -ItemType Directory -Force sessions, logs | Out-Null

docker run --rm --gpus all `
  --name medjarvis-registry `
  -p 8000:8000 `
  -v "${Root}\sessions:/app/sessions" `
  -v "${Root}\logs:/app/logs" `
  $Tag

