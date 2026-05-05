from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from time import monotonic

import numpy as np

from .audio import pcm16_bytes_to_float32, rms_float32
from .config import Settings

log = logging.getLogger(__name__)


@dataclass
class VadSettings:
    wlk_min_duration_real_silence: float
    vad_energy_threshold: float
    vad_silence_ratio: float
    silero_threshold: float
    silero_speech_pad_ms: int
    silero_min_silence_ms: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "VadSettings":
        return cls(
            wlk_min_duration_real_silence=settings.wlk_min_duration_real_silence,
            vad_energy_threshold=settings.vad_energy_threshold,
            vad_silence_ratio=settings.vad_silence_ratio,
            silero_threshold=settings.silero_threshold,
            silero_speech_pad_ms=settings.silero_speech_pad_ms,
            silero_min_silence_ms=settings.silero_min_silence_ms,
        )

    def asdict(self) -> dict:
        return asdict(self)


class VadSegmenter:
    """Single PCM endpointing layer: energy starts/stops, Silero validates and crops."""

    _silero_model = None

    def __init__(self, settings: Settings, vad_settings: VadSettings):
        self.settings = settings
        self.vad_settings = vad_settings
        self.sample_rate = settings.pcm_sample_rate
        self._buffer = bytearray()
        self._pre_roll = bytearray()
        self._in_speech = False
        self._last_voice_at: float | None = None
        self._speech_started_at: float | None = None
        self._load_silero()

    def _load_silero(self) -> None:
        if VadSegmenter._silero_model is not None:
            return
        try:
            from silero_vad import load_silero_vad

            VadSegmenter._silero_model = load_silero_vad()
            log.info("Silero VAD loaded")
        except Exception as exc:
            if self.settings.require_silero_vad:
                raise RuntimeError("Silero VAD is required but could not be loaded") from exc
            log.warning("Silero VAD unavailable, using energy endpointing only: %s", exc)

    def update_settings(self, vad_settings: VadSettings) -> None:
        self.vad_settings = vad_settings

    def accept_pcm16(self, chunk: bytes) -> bytes | None:
        audio = pcm16_bytes_to_float32(chunk)
        now = monotonic()
        rms = rms_float32(audio)
        speech_threshold = self.vad_settings.vad_energy_threshold
        silence_threshold = speech_threshold * self.vad_settings.vad_silence_ratio

        if not self._in_speech:
            self._pre_roll.extend(chunk)
            max_pre_roll_bytes = int(self.sample_rate * 2 * 0.35)
            if len(self._pre_roll) > max_pre_roll_bytes:
                del self._pre_roll[: len(self._pre_roll) - max_pre_roll_bytes]
            if rms >= speech_threshold:
                self._in_speech = True
                self._speech_started_at = now
                self._last_voice_at = now
                self._buffer.extend(self._pre_roll)
                self._pre_roll.clear()
                self._buffer.extend(chunk)
            return None

        self._buffer.extend(chunk)
        if rms >= silence_threshold:
            self._last_voice_at = now

        silence_for = now - (self._last_voice_at or now)
        if silence_for >= self.vad_settings.wlk_min_duration_real_silence:
            return self._finalize()
        return None

    def flush(self) -> bytes | None:
        if not self._buffer:
            return None
        return self._finalize()

    def _finalize(self) -> bytes | None:
        raw = bytes(self._buffer)
        self._buffer.clear()
        self._pre_roll.clear()
        self._in_speech = False
        self._last_voice_at = None
        self._speech_started_at = None
        if len(raw) < self.sample_rate * 2 * 0.25:
            return None
        return self._crop_with_silero(raw)

    def _crop_with_silero(self, raw: bytes) -> bytes | None:
        if VadSegmenter._silero_model is None:
            return raw
        try:
            from silero_vad import get_speech_timestamps

            audio = pcm16_bytes_to_float32(raw)
            timestamps = get_speech_timestamps(
                audio,
                VadSegmenter._silero_model,
                sampling_rate=self.sample_rate,
                threshold=self.vad_settings.silero_threshold,
                min_silence_duration_ms=self.vad_settings.silero_min_silence_ms,
                speech_pad_ms=self.vad_settings.silero_speech_pad_ms,
                return_seconds=False,
            )
            if not timestamps:
                return None
            start = max(0, int(timestamps[0]["start"]))
            end = min(len(audio), int(timestamps[-1]["end"]))
            cropped = np.ascontiguousarray(audio[start:end])
            if cropped.size < self.sample_rate * 0.2:
                return None
            return (np.clip(cropped, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        except Exception as exc:
            if self.settings.require_silero_vad:
                raise
            log.warning("Silero crop failed, keeping raw segment: %s", exc)
            return raw

