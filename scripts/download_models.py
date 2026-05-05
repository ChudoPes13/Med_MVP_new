from __future__ import annotations

from pathlib import Path

from huggingface_hub import snapshot_download


ROOT = Path(__file__).resolve().parents[1]
WHISPER_REPO = "deepdml/faster-whisper-large-v3-turbo-ct2"
WHISPER_DIR = ROOT / "models" / "whisper" / "faster-whisper-large-v3-turbo-ct2"


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


if __name__ == "__main__":
    main()
