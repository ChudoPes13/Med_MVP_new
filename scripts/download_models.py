from __future__ import annotations

from pathlib import Path

from huggingface_hub import snapshot_download


ROOT = Path(__file__).resolve().parents[1]
WHISPER_REPO = "deepdml/faster-whisper-large-v3-turbo-ct2"
WHISPER_DIR = ROOT / "models" / "whisper" / "faster-whisper-large-v3-turbo-ct2"
GGUF_PATH = Path(r"C:\ai25\v2v_RAG_gpt52\Ministral-3-3B-Instruct-2512-Q5_K_M.gguf")


def main() -> None:
    WHISPER_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {WHISPER_REPO} to {WHISPER_DIR}")
    snapshot_download(
        repo_id=WHISPER_REPO,
        local_dir=WHISPER_DIR,
        local_dir_use_symlinks=False,
        allow_patterns=["*.bin", "*.json", "*.txt", "*.model", "vocabulary.*"],
    )
    print("Whisper CT2 model is ready.")
    if GGUF_PATH.exists():
        print(f"Found local Ministral GGUF: {GGUF_PATH}")
    else:
        print(f"Ministral GGUF not found at {GGUF_PATH}; update scripts/start_llama.ps1 if needed.")


if __name__ == "__main__":
    main()
