from __future__ import annotations

import logging

import numpy as np

from .audio import wav_bytes_from_float32
from .config import Settings
from .text_preprocessor import RussianTextPreprocessor

log = logging.getLogger(__name__)


class SileroTts:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None
        self._preprocessor = RussianTextPreprocessor()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self._model is not None or not self.settings.tts_enabled:
            return
        if not self.settings.silero_tts_path.exists():
            raise FileNotFoundError(f"Silero TTS file not found: {self.settings.silero_tts_path}")
        import torch

        log.info("Loading Silero TTS from %s", self.settings.silero_tts_path)
        package = torch.package.PackageImporter(str(self.settings.silero_tts_path))
        model = package.load_pickle("tts_models", "model")
        model.to(self.settings.silero_tts_device)
        self._model = model

    def synthesize_wav(self, text: str) -> bytes | None:
        if not self.settings.tts_enabled:
            return None
        self.load()
        import torch

        normalized = text.strip()
        if not normalized:
            return None
        tts_text = self._preprocessor.preprocess(normalized)
        with torch.no_grad():
            audio = self._model.apply_tts(
                text=tts_text,
                speaker=self.settings.silero_tts_speaker,
                sample_rate=self.settings.silero_tts_sample_rate,
                put_accent=True,
                put_yo=True,
            )
        if hasattr(audio, "detach"):
            audio_np = audio.detach().cpu().numpy()
        else:
            audio_np = np.asarray(audio, dtype=np.float32)
        audio_np = audio_np.astype(np.float32, copy=False)
        return wav_bytes_from_float32(audio_np, self.settings.silero_tts_sample_rate)
