$ErrorActionPreference = "Stop"

$LlamaDir = if ($env:LLAMA_CPP_DIR) { $env:LLAMA_CPP_DIR } else { "C:\Users\Master\Downloads\llama-b8955-bin-win-cuda-12.4-x64" }
$LlamaServer = Join-Path $LlamaDir "llama-server.exe"
$ModelPath = if ($env:LLAMA_MODEL_PATH) { $env:LLAMA_MODEL_PATH } else { "C:\ai25\v2v_RAG_gpt52\Ministral-3-3B-Instruct-2512-Q5_K_M.gguf" }

if (!(Test-Path $LlamaServer)) {
    throw "llama-server.exe not found: $LlamaServer"
}

if (!(Test-Path $ModelPath)) {
    throw "GGUF model not found: $ModelPath"
}

$Existing = Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue
if ($Existing) {
    Write-Host "Port 8080 already has a listener; skipping llama-server start."
    exit 0
}

$Args = @(
    "-m", $ModelPath,
    "--host", "127.0.0.1",
    "--port", "8080",
    "--ctx-size", "4096",
    "--n-gpu-layers", "99",
    "--parallel", "2",
    "--flash-attn", "auto"
)

Start-Process -FilePath $LlamaServer -ArgumentList $Args -WorkingDirectory $LlamaDir -WindowStyle Hidden
Write-Host "llama-server started on http://127.0.0.1:8080"
