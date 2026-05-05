from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .config import Settings

log = logging.getLogger(__name__)


class LlmClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{self.settings.llama_base_url.rstrip('/')}/models")
            return resp.status_code < 500
        except Exception:
            return False

    async def chat(self, messages: list[dict[str, str]], temperature: float = 0.1, max_tokens: int = 500) -> str:
        payload = {
            "model": self.settings.llama_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        async with httpx.AsyncClient(timeout=self.settings.llama_timeout_sec) as client:
            resp = await client.post(
                f"{self.settings.llama_base_url.rstrip('/')}/chat/completions",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

    async def extract(self, text: str, slots: dict[str, Any], specialties: list[str]) -> dict[str, Any]:
        system = (
            "Ты локальный JSON-экстрактор для медицинской регистратуры. "
            "Не давай медицинских советов. Верни только валидный JSON без markdown. "
            "Поля: fio, complaint, age, phone, desired_time_text, requested_specialty, "
            "requested_doctor, intent, confirmation, denial, emergency_level. "
            "confirmation/denial boolean или null. emergency_level: none|possible|urgent."
        )
        user = {
            "current_slots": slots,
            "available_specialties": specialties,
            "utterance": text,
        }
        try:
            raw = await self.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
                ],
                temperature=0.0,
                max_tokens=350,
            )
            start = raw.find("{")
            end = raw.rfind("}")
            if start >= 0 and end > start:
                raw = raw[start : end + 1]
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except Exception as exc:
            log.warning("LLM extraction failed: %s", exc)
        return {}

