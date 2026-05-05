$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Tag = if ($env:DOCKER_IMAGE_TAG) { $env:DOCKER_IMAGE_TAG } else { "chudopes/medjarvis-registry:latest" }
$NetworkMode = if ($env:DOCKER_NETWORK_MODE) { $env:DOCKER_NETWORK_MODE } else { "host" }
$PortArgs = @()

if ($NetworkMode -ne "host") {
  $PortArgs = @("-p", "8000:8000")
}

New-Item -ItemType Directory -Force sessions, logs | Out-Null

docker run --rm --gpus all `
  --name medjarvis-registry `
  --network $NetworkMode `
  @PortArgs `
  -v "${Root}\sessions:/app/sessions" `
  -v "${Root}\logs:/app/logs" `
  $Tag
