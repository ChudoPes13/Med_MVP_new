from __future__ import annotations

import logging
from pathlib import Path

from .audio import pcm16_bytes_to_float32
from .config import Settings

log = logging.getLogger(__name__)


class WhisperStt:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _model_ref(self) -> str:
        local = self.settings.whisper_local_dir
        if local.exists() and any(local.iterdir()):
            return str(local)
        return self.settings.whisper_model

    def load(self) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel

        model_ref = self._model_ref()
        log.info(
            "Loading Whisper model %s on %s (%s)",
            model_ref,
            self.settings.whisper_device,
            self.settings.whisper_compute_type,
        )
        self._model = WhisperModel(
            model_ref,
            device=self.settings.whisper_device,
            compute_type=self.settings.whisper_compute_type,
            download_root=str(self.settings.whisper_local_dir.parent),
        )

    def transcribe_pcm16(self, pcm: bytes) -> str:
        self.load()
        audio = pcm16_bytes_to_float32(pcm)
        if audio.size < self.settings.pcm_sample_rate * 0.25:
            return ""
        segments, _info = self._model.transcribe(
            audio,
            language=self.settings.whisper_language,
            beam_size=self.settings.whisper_beam_size,
            vad_filter=False,
            condition_on_previous_text=False,
            temperature=0.0,
            without_timestamps=True,
            initial_prompt=(
                "Русская речь пациента медицинской клиники. "
                "Короткие ответы да, нет, время, имя, телефон, симптомы."
            ),
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return text

