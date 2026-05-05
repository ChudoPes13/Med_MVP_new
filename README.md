# MedJarvis Registry MVP

Локальная production-oriented заготовка голосовой регистратуры медицинской клиники. MVP работает без внешних API: браузер отправляет микрофон как PCM16 16 kHz, backend режет речь VAD, распознает через `faster-whisper`/CTranslate2, ведет диалог, озвучивает ответы Silero TTS и сохраняет заявку в JSON.

## Ключевые решения

- Windows 11 для тестов, позже Ubuntu 24/26.04.
- Без Docker на первом этапе.
- Runtime полностью локальный.
- STT: `deepdml/faster-whisper-large-v3-turbo-ct2`, `cuda`, `int8`.
- LLM: локальный `llama-server` OpenAI-compatible на `http://127.0.0.1:8080/v1`.
- TTS: `v5_4_ru.pt`, голос `kseniya`.
- Аудио не сохраняется. Диалог, транскрипты, слоты и события сохраняются в `sessions/*.json`.
- VAD defaults жестко соответствуют `VAD.jpg`: `1.0`, `0.004`, `0.45`, `0.45`, `120`, `250`.

Изначально рассматривался `distil-whisper/distil-large-v3.5-ct2`, но он помечен как English ASR и на русском аудио дает непригодный результат. Для русскоязычной клиники дефолт переключен на multilingual CT2 large-v3-turbo.

## Установка Windows

```powershell
Set-Location C:\ai25\Med_MVP_4
.\scripts\setup_windows.ps1
```

Скрипт создаст `.env`, установит Python/Node зависимости и скачает CT2 Whisper в `models/`.

Локальные `cert.pem` и `cert-key.pem` используются для HTTPS/WSS тестов, но не коммитятся в публичный репозиторий. Для новой машины положите свои dev-сертификаты в корень проекта или поменяйте пути в `.env`.

## Запуск

```powershell
.\scripts\start_llama.ps1
.\scripts\start_backend.ps1
```

В отдельном терминале:

```powershell
.\scripts\start_frontend.ps1
```

Открыть: `https://127.0.0.1:5173`

Можно также запустить все фоном:

```powershell
.\scripts\start_all.ps1
```

## Проверки

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Диалоговый порядок

1. ФИО.
2. Что тревожит и причина обращения.
3. Возраст.
4. Желаемый день и время.
5. Телефон.
6. Финальное подтверждение.

Срочные симптомы переводят заявку в `needs_operator` и проговаривают безопасную рекомендацию вызвать `103`, если состояние острое сейчас.

## Git

Репозиторий настроен на `https://github.com/ChudoPes13/Med_MVP_new.git`, ветка `main`.
