from __future__ import annotations

import re

from num2words import num2words


class RussianTextPreprocessor:
    def __init__(self):
        self.pronunciation_dict = {
            r"\bGPU\b": "джи пи у",
            r"\bCPU\b": "си пи у",
            r"\bAPI\b": "а пи ай",
            r"\biPhone\b": "айфон",
            r"\bYouTube\b": "ютуб",
            r"\bWi-Fi\b": "вай фай",
            r"\bЖК\b": "же ка",
        }

    def preprocess(self, text: str) -> str:
        for pattern, replacement in self.pronunciation_dict.items():
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        return self._convert_numbers(text)

    def _convert_numbers(self, text: str) -> str:
        def num_to_words(match: re.Match[str]) -> str:
            num = match.group()
            try:
                return num2words(int(num), lang="ru")
            except Exception:
                return num

        return re.sub(r"\b\d+\b", num_to_words, text)
