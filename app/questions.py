from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any


class QuestionBank:
    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text(encoding="utf-8"))

    def get(self, key: str, context: dict[str, Any] | None = None) -> str:
        context = context or {}
        item = self.data["questions"].get(key)
        if not item:
            return "Уточните, пожалуйста."
        variants = item.get("variants") or [item["canonical"]]
        idx_seed = f"{context.get('session_id', '')}:{key}:{context.get('turns', 0)}"
        rnd = random.Random(idx_seed)
        template = rnd.choice(variants)
        safe_context = {k: ("" if v is None else v) for k, v in context.items()}
        try:
            return template.format(**safe_context)
        except KeyError:
            return template

    def instruction(self, key: str) -> dict[str, Any]:
        return self.data["questions"].get(key, {})

