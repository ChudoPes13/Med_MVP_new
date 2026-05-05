from __future__ import annotations

from app.text_preprocessor import RussianTextPreprocessor


def test_russian_tts_preprocessor_rewrites_numbers_and_terms():
    preprocessor = RussianTextPreprocessor()
    text = preprocessor.preprocess("API работает на GPU, кабинет 101, звоните 103.")
    assert "а пи ай" in text
    assert "джи пи у" in text
    assert "сто один" in text
    assert "сто три" in text
