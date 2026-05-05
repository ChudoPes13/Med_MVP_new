$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

& "$PSScriptRoot\prepare_docker_assets.ps1"

$Tag = if ($env:DOCKER_IMAGE_TAG) { $env:DOCKER_IMAGE_TAG } else { "chudopes/medjarvis-registry:latest" }
docker build --progress=plain -t $Tag .
Write-Host "Built image: $Tag"

