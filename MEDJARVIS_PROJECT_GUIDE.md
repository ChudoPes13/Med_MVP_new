# MedJarvis Project Guide

Актуальное состояние проекта на 2026-05-05. Этот файл заменяет старый архитектурный план с другого ПК и должен считаться главным контекстом для новых диалогов.

## Цель

Production-oriented MVP голосовой регистратуры медицинской клиники:

- принять входящее голосовое обращение;
- распознать речь локально;
- собрать заявку на запись;
- подобрать врача по жалобе/специальности;
- принять любой день и время в тестовом MVP-режиме;
- сохранить полную заявку и диалог в JSON;
- не хранить аудио.

## Текущий Deployment

- Репозиторий: `https://github.com/ChudoPes13/Med_MVP_new.git`.
- Рабочая ветка: `main`.
- Локальная папка разработки: `C:\ai25\Med_MVP_4`.
- Docker image для заказчика: `chudopes/medjarvis-registry:latest`.
- CI/CD: GitHub Actions `.github/workflows/docker-publish.yml`.
- Один контейнер содержит backend, frontend, STT/VAD/TTS и static UI.
- LLM не входит в контейнер: `llama-server` запускается отдельно на хосте.

## Архитектура

```text
Browser UI
  |
  | PCM16 mono 16 kHz over WebSocket
  v
FastAPI backend
  |
  +-- VAD: один слой, настройки из VAD.jpg
  +-- STT: faster-whisper / CTranslate2 on CUDA
  +-- Dialog manager: сценарий записи в клинику
  +-- LLM style layer: локальный llama-server OpenAI-compatible
  +-- TTS: Silero v5_5_ru.pt on CUDA
  +-- Storage: sessions/*.json and logs/backend.log
```

## Runtime Модели

| Компонент | Текущее значение |
|---|---|
| STT | `deepdml/faster-whisper-large-v3-turbo-ct2` |
| STT device | `cuda` |
| STT compute | `int8` |
| STT language | `ru` |
| LLM endpoint | `http://127.0.0.1:8080/v1` |
| LLM model name | `Ministral-3-3B-Instruct-2512-Q5_K_M.gguf` |
| TTS model | `v5_5_ru.pt` |
| TTS source URL | `https://models.silero.ai/models/tts/ru/v5_5_ru.pt` |
| TTS voice | `kseniya` |
| Warm-up | `WARMUP_ON_STARTUP=true` |

`distil-whisper/distil-large-v3.5-ct2` не используется как дефолт: на русском тестовом аудио дал непригодный результат, поэтому выбран multilingual CT2 large-v3-turbo.

## Важные Ограничения

- Никаких внешних LLM/STT/TTS API.
- GPU обязателен: `REQUIRE_GPU=true`.
- Аудио не сохранять.
- Транскрипты, диалог, слоты, статусы и логи сохранять всегда.
- `llama.cpp` не автозапускать внутри Docker.
- Путь к `llama.cpp` и GGUF-модели задает заказчик на своем host.
- Текст нормализуется через `num2words` только перед Silero TTS; текст ответа, transcript и JSON-сессия остаются исходными.

## VAD/VAC Настройки

Настройки жестко взяты из `VAD.jpg` и продублированы в `.env.example`, `Dockerfile`, `docker-compose.customer.yml`:

| Переменная | Значение |
|---|---:|
| `WLK_MIN_DURATION_REAL_SILENCE` | `1.0` |
| `VAD_ENERGY_THRESHOLD` | `0.004` |
| `VAD_SILENCE_RATIO` | `0.45` |
| `SILERO_VAD_THRESHOLD` | `0.45` |
| `SILERO_SPEECH_PAD_MS` | `120` |
| `SILERO_MIN_SILENCE_MS` | `250` |

В текущей реализации нет WhisperLiveKit и нет лишних VAD/VAC слоев. PCM идет напрямую в backend; STT получает numpy audio без ffmpeg и без временного аудиофайла.

## Диалоговый Pipeline

Порядок сбора данных:

1. ФИО.
2. Жалоба/что тревожит/зачем пациент обратился.
3. Возраст.
4. Желаемый день и время.
5. Подтверждение слота.
6. Телефон.
7. Финальное подтверждение.
8. Завершение разговора.

LLM используется не как источник фактов, а как слой мягкой переформулировки вопроса. Канонический смысл вопросов хранится в `config/question_style.json`; изменения должны быть минимальными и не должны менять смысл вопроса.

После подтверждения всех полей backend выставляет `status=finalized`, сохраняет заявку, произносит финальную фразу и закрывает WebSocket:

```text
Спасибо, <Имя>, запись создана. Желаем Вам хорошего здоровья. При необходимости перезвоните. До свидания
```

`<Имя>` берется из ФИО и доступно в шаблонах как `{first_name}`. Для `Иванов Иван Иванович` используется `Иван`; для короткого ответа `Георгий` используется `Георгий`.

## Прерывание И Отмена Ответа

Прерывание реализовано на двух уровнях:

- frontend хранит активные TTS audio source и немедленно останавливает их при речи пользователя поверх озвучки, ручном flush или текстовом вводе;
- frontend отправляет WebSocket event `barge_in`;
- backend отменяет текущую response task, поэтому новая реплика может прервать еще не законченную LLM/TTS-задачу.

Если микрофон слышит колонки, браузер может ошибочно принять TTS за речь пользователя. Поэтому для демонстрации предпочтительны наушники или хорошая echo cancellation.

## Warm-Up

При `WARMUP_ON_STARTUP=true` startup прогревает Silero VAD, Whisper STT, Silero TTS и LLM endpoint. Результат виден в `GET /health` в поле `warmup`.

LLM warm-up не валит приложение, если `llama-server` еще не поднят: статус будет `unavailable` или `failed`, а сам backend продолжит работу.

## Расписание И Врачи

- `docs/clinic_schedule.md` содержит демо-расписание, врачей и специальности.
- В текущем MVP даты в расписании не считаются актуальным календарем.
- Любой день и время, которые назвал пациент, принимаются как доступные для теста.
- Backend создает слот с `source=test_flexible`.
- ФИО врача и специальность выбираются из `docs/clinic_schedule.md`.

## Срочность И Безопасность

При симптомах потенциальной неотложки диалог не должен просто оформлять запись. Сессия переводится в `needs_operator`, а пациенту проговаривается рекомендация вызвать `103`, если состояние острое сейчас.

Система не дает медицинские советы по лечению, дозировкам, диагнозам и препаратам. Она только маршрутизирует к врачу и оформляет заявку.

## Хранение

Сессии сохраняются в `sessions/*.json`.

Имя файла заявки формируется в формате:

```text
ДЕНЬ-МЕСЯЦ-ГОД-ВРЕМЯ-3_РАНДОМНЫХ_СИМВОЛА.json
```

Внутри JSON должны быть собранные поля, статус, слот, transcript/dialogue и служебные события. Логи backend пишутся в `logs/backend.log`.

## Основные Файлы

| Путь | Назначение |
|---|---|
| `app/main.py` | FastAPI app, HTTP routes, WebSocket, static frontend mount |
| `app/config.py` | Env defaults and paths |
| `app/vad.py` | VAD segmenter and exact VAD preset |
| `app/stt.py` | faster-whisper direct PCM transcription |
| `app/tts.py` | Silero package load and WAV synthesis |
| `app/text_preprocessor.py` | TTS-only Russian text preprocessing and `num2words` |
| `app/dialog.py` | Dialogue state machine |
| `app/schedule.py` | Doctors, specialty matching, flexible MVP slots |
| `app/session_store.py` | JSON session persistence |
| `config/question_style.json` | Per-question wording rules and examples |
| `config/symptom_specialty_map.json` | Symptom to specialty routing |
| `docs/clinic_scenarios.md` | Examples for dialogue tone and behavior |
| `docs/clinic_schedule.md` | Doctors/specialties and demo slots |
| `Dockerfile` | Single customer image |
| `docker-compose.customer.yml` | Customer runtime compose |
| `.github/workflows/docker-publish.yml` | Docker Hub release workflow |

## Customer Runbook

Главная инструкция запуска у заказчика находится в `README.md`. Коротко:

1. На host запускается `llama-server` на `http://127.0.0.1:8080/v1`.
2. Проверяется `GET http://127.0.0.1:8080/v1/models`.
3. Запускается `docker compose -f docker-compose.customer.yml up -d`.
4. UI открывается на `http://127.0.0.1:8000`.
5. Проверка backend: `GET http://127.0.0.1:8000/health`.

Для Ubuntu/Linux используется `network_mode: host`, поэтому контейнер видит host-level `127.0.0.1:8080`.

Для Windows Docker Desktop, если host networking недоступен, временный fallback:

- `llama-server --host 0.0.0.0 --port 8080`;
- в compose заменить `LLAMA_BASE_URL` на `http://host.docker.internal:8080/v1`;
- убрать `network_mode: host`;
- добавить `ports: ["8000:8000"]`.

Дефолт проекта остается `http://127.0.0.1:8080/v1`.

Официальные ссылки для первичной подготовки окружения:

- Docker Engine Ubuntu: `https://docs.docker.com/installation/ubuntulinux/`;
- NVIDIA Container Toolkit: `https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/`;
- Docker host network driver: `https://docs.docker.com/engine/network/drivers/host/`;
- Docker Desktop Windows: `https://docs.docker.com/desktop/setup/install/windows-install/`.

## Docker Release

GitHub Actions собирает Docker image на push в `main`.

Repository secrets:

- `DOCKERHUB_USERNAME`;
- `DOCKERHUB_TOKEN`;
- `TTS_MODEL_URL=https://models.silero.ai/models/tts/ru/v5_5_ru.pt`;
- `HF_TOKEN` опционален.

Последняя проверенная публикация `chudopes/medjarvis-registry:latest` прошла успешно через GitHub Actions.

## Local Development

Windows:

```powershell
Set-Location C:\ai25\Med_MVP_4
.\scripts\setup_windows.ps1
.\scripts\start_llama.ps1
.\scripts\start_backend.ps1
```

Frontend:

```powershell
.\scripts\start_frontend.ps1
```

Открыть:

```text
https://127.0.0.1:5173
```

Проверки:

```powershell
.\venv\Scripts\python.exe -m pytest -q
npm run build
docker compose -f docker-compose.customer.yml config --quiet
```

## Что Не Реализовано В MVP

- Нет SIP/Asterisk интеграции.
- Нет админки.
- Нет реального календаря/CRM.
- Нет Docker-автозапуска llama.cpp.
- Нет хранения аудио.
- Нет записи в внешнюю МИС: создается локальная JSON-заявка.
