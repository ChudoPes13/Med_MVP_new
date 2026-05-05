from __future__ import annotations

from pydantic import BaseModel, Field


class VadSettingsPayload(BaseModel):
    wlk_min_duration_real_silence: float = Field(1.0, ge=0.1, le=5.0)
    vad_energy_threshold: float = Field(0.004, ge=0.0001, le=0.1)
    vad_silence_ratio: float = Field(0.45, ge=0.05, le=1.0)
    silero_threshold: float = Field(0.45, ge=0.01, le=0.99)
    silero_speech_pad_ms: int = Field(120, ge=0, le=1000)
    silero_min_silence_ms: int = Field(250, ge=50, le=2000)


class UserTextPayload(BaseModel):
    text: str


class HealthPayload(BaseModel):
    status: str
    app: str
    gpu_required: bool
    stt_loaded: bool
    tts_loaded: bool
    llm_ok: bool

