from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional during bootstrap
    load_dotenv = None


ROOT_DIR = Path(__file__).resolve().parents[1]

if load_dotenv:
    load_dotenv(ROOT_DIR / ".env")


def _path(value: str | None, default: str) -> Path:
    raw = value or default
    p = Path(raw)
    if p.is_absolute():
        return p
    return ROOT_DIR / p


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return float(raw.replace(",", "."))


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "MedJarvis Registry")
    clinic_id: str = os.getenv("CLINIC_ID", "medcenter")
    timezone: str = os.getenv("APP_TIMEZONE", "Europe/Moscow")

    host: str = os.getenv("APP_HOST", "127.0.0.1")
    port: int = _int("APP_PORT", 8000)
    ssl_certfile: Path = _path(os.getenv("SSL_CERTFILE"), "cert.pem")
    ssl_keyfile: Path = _path(os.getenv("SSL_KEYFILE"), "cert-key.pem")

    data_dir: Path = _path(os.getenv("DATA_DIR"), "sessions")
    logs_dir: Path = _path(os.getenv("LOGS_DIR"), "logs")
    docs_dir: Path = _path(os.getenv("DOCS_DIR"), "docs")
    config_dir: Path = _path(os.getenv("CONFIG_DIR"), "config")
    models_dir: Path = _path(os.getenv("MODELS_DIR"), "models")
    static_dir: Path = _path(os.getenv("STATIC_DIR"), "frontend/dist")

    whisper_model: str = os.getenv("WHISPER_MODEL", "deepdml/faster-whisper-large-v3-turbo-ct2")
    whisper_local_dir: Path = _path(
        os.getenv("WHISPER_LOCAL_DIR"),
        "models/whisper/faster-whisper-large-v3-turbo-ct2",
    )
    whisper_device: str = os.getenv("WHISPER_DEVICE", "cuda")
    whisper_compute_type: str = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
    whisper_language: str = os.getenv("WHISPER_LANGUAGE", "ru")
    whisper_beam_size: int = _int("WHISPER_BEAM_SIZE", 1)

    llama_base_url: str = os.getenv("LLAMA_BASE_URL", "http://127.0.0.1:8080/v1")
    llama_model: str = os.getenv("LLAMA_MODEL", "Ministral-3-3B-Instruct-2512-Q5_K_M.gguf")
    llama_timeout_sec: float = _float("LLAMA_TIMEOUT_SEC", 8.0)

    silero_tts_path: Path = _path(os.getenv("SILERO_TTS_PATH"), "v5_4_ru.pt")
    silero_tts_device: str = os.getenv("SILERO_TTS_DEVICE", "cuda")
    silero_tts_speaker: str = os.getenv("SILERO_TTS_SPEAKER", "kseniya")
    silero_tts_sample_rate: int = _int("SILERO_TTS_SAMPLE_RATE", 48000)
    tts_enabled: bool = _bool("TTS_ENABLED", True)

    pcm_sample_rate: int = _int("PCM_SAMPLE_RATE", 16000)

    # VAD values are intentionally the VAD.jpg preset.
    wlk_min_duration_real_silence: float = _float("WLK_MIN_DURATION_REAL_SILENCE", 1.0)
    vad_energy_threshold: float = _float("VAD_ENERGY_THRESHOLD", 0.004)
    vad_silence_ratio: float = _float("VAD_SILENCE_RATIO", 0.45)
    silero_threshold: float = _float("SILERO_VAD_THRESHOLD", 0.45)
    silero_speech_pad_ms: int = _int("SILERO_SPEECH_PAD_MS", 120)
    silero_min_silence_ms: int = _int("SILERO_MIN_SILENCE_MS", 250)

    require_gpu: bool = _bool("REQUIRE_GPU", True)
    require_silero_vad: bool = _bool("REQUIRE_SILERO_VAD", True)

    @property
    def clinic_config_path(self) -> Path:
        return self.config_dir / "clinic.json"

    @property
    def question_style_path(self) -> Path:
        return self.config_dir / "question_style.json"

    @property
    def symptom_map_path(self) -> Path:
        return self.config_dir / "symptom_specialty_map.json"

    @property
    def schedule_markdown_path(self) -> Path:
        return self.docs_dir / "clinic_schedule.md"


settings = Settings()
