# MedJarvis Registry MVP

Локальная production-oriented заготовка голосовой регистратуры медицинской клиники. MVP работает без внешних API: браузер отправляет микрофон как PCM16 16 kHz, backend режет речь VAD, распознает через `faster-whisper`/CTranslate2, ведет диалог, озвучивает ответы Silero TTS и сохраняет заявку в JSON.

## Ключевые решения

- Windows 11 для тестов, позже Ubuntu 24/26.04.
- Без Docker на первом этапе.
- Runtime полностью локальный.
- STT: `deepdml/faster-whisper-large-v3-turbo-ct2`, `cuda`, `int8`.
- LLM: локальный `llama-server` OpenAI-compatible на `http://127.0.0.1:8080/v1`.
- TTS: `v5_5_ru.pt`, голос `kseniya`.
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

## Docker Для Заказчика

Docker-образ запускает приложение, frontend, VAD/STT/TTS и хранение заявок. `llama-server` не стартует автоматически: его нужно поднять отдельно на машине заказчика, потому что путь к `llama.cpp` и GGUF-модели может отличаться.

Ручной запуск `llama-server` на Windows:

```powershell
$env:LLAMA_CPP_DIR = "D:\path\to\llama.cpp-build"
$env:LLAMA_MODEL_PATH = "D:\models\Ministral-3-3B-Instruct-2512-Q5_K_M.gguf"
.\scripts\start_llama.ps1
```

Эквивалентная команда напрямую:

```powershell
D:\path\to\llama.cpp-build\llama-server.exe `
  -m "D:\models\Ministral-3-3B-Instruct-2512-Q5_K_M.gguf" `
  --host 0.0.0.0 `
  --port 8080 `
  --ctx-size 4096 `
  --n-gpu-layers 99 `
  --parallel 2 `
  --flash-attn auto
```

После этого контейнер можно запускать так:

```powershell
docker compose -f docker-compose.customer.yml up -d
```

UI будет доступен на `http://localhost:8000`. В compose задан `network_mode: host` и `LLAMA_BASE_URL=http://127.0.0.1:8080/v1`, чтобы контейнер обращался к host-level `llama-server` по стандартному localhost.

Локальная сборка Docker-образа:

```powershell
.\scripts\docker_build_local.ps1
```

Скрипт положит Silero TTS модель в ignored `docker_assets/` и соберет образ `chudopes/medjarvis-registry:latest`.

Для GitHub Actions Docker publish нужны secrets:

- `DOCKERHUB_USERNAME`: `chudopes`
- `DOCKERHUB_TOKEN`: Docker Hub access token
- `TTS_MODEL_URL`: URL, откуда workflow скачает `v5_5_ru.pt`, например `https://models.silero.ai/models/tts/ru/v5_5_ru.pt`
- `HF_TOKEN`: только если `TTS_MODEL_URL` ведет на приватный Hugging Face файл

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

## Тестовый режим расписания

Для MVP-тестов любой названный пациентом день и время считаются доступными. Backend создает слот с `source=test_flexible`, сохраняя реального врача из `docs/clinic_schedule.md` по выбранной специальности. Это убирает блокировки на этапе проверки диалога, когда демо-расписание неполное или не совпадает с текущей датой.

## Git

Репозиторий настроен на `https://github.com/ChudoPes13/Med_MVP_new.git`, ветка `main`.
