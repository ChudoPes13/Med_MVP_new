# МедЖарвис.Регистратура — Полное руководство по проекту

> **За 30 секунд:** AI-голосовой регистратор для частных клиник. Пациент звонит/открывает браузер → говорит голосом → бот уточняет имя, телефон, жалобу → находит свободный слот → подтверждает запись → отправляет SMS и Telegram уведомления. Без операторов.

---

## 📋 Содержание

1. [Что это и зачем](#1-что-это-и-зачем)
2. [Архитектура системы](#2-архитектура-системы)
3. [Стек технологий](#3-стек-технологий)
4. [Структура репозитория](#4-структура-репозитория)
5. [Как работает диалог](#5-как-работает-диалог)
6. [Мультиклиника](#6-мультиклиника)
7. [REST и WebSocket API](#7-rest-и-websocket-api)
8. [Конфигурация клиники](#8-конфигурация-клиники)
9. [Модели и GPU](#9-модели-и-gpu) (в т.ч. **пресет VAD «Скорость»**)
10. [Развёртывание](#10-развёртывание)
11. [Тестирование](#11-тестирование)
12. [Известные ограничения и TODO](#12-известные-ограничения-и-todo)

---

## 1. Что это и зачем

### Продукт
**МедЖарвис.Регистратура** — AI-голосовой оператор для частных медицинских клиник. Заменяет живого администратора на входящих звонках.

### Бизнес-сценарий

```
Пациент звонит в клинику
        │
        ▼
[МедЖарвис отвечает за 1 секунду]
        │
        ▼
Голосовой диалог:
  • Как вас зовут?
  • Номер телефона?
  • Что беспокоит?
  • К какому специалисту?
  • Когда удобно?
        │
        ▼
[Слот найден и зарезервирован]
        │
        ▼
SMS пациенту + Telegram клинике
        │
        ▼
Запись в календаре регистратуры
```

### Три режима клиник
| Режим | Пример | Особенности |
|-------|--------|-------------|
| `general` | МедЭкспресс | Терапевт, кардиолог, невролог и др. |
| `dental` | СтомаКлиник | Спрашивает об аллергии на анестезию |
| `cosmetology` | ЭстетикПро | Спрашивает о беременности |

---

## 2. Архитектура системы

### Общая схема

```
┌─────────────────────────────────────────────────────────────┐
│                        КЛИЕНТ                               │
│  Браузер (React/Vite :5173)  или  SIP-телефония (будущее)  │
│                    ▲         │                               │
│                    │ HTTP    │ WebSocket (аудио + события)  │
└────────────────────┼─────────┼───────────────────────────────┘
                     │         │
┌────────────────────┼─────────▼───────────────────────────────┐
│                 DOCKER COMPOSE                                │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐     │
│  │           main-agent :8000  (FastAPI)               │     │
│  │                                                     │     │
│  │  WebSocket ──► AudioProcessor (WhisperLiveKit)      │     │
│  │                      │ транскрипт                  │     │
│  │                      ▼                             │     │
│  │              _run_agent_turn()                     │     │
│  │                   │         │                      │     │
│  │            script_engine  schedule.py              │     │
│  │            (слоты/стейт)  (свободные слоты)        │     │
│  │                   │                                │     │
│  │             llm_companion ──► LLM HTTP             │     │
│  │                   │                                │     │
│  │              tts_engine ──► Silero TTS (GPU)       │     │
│  │                   │                                │     │
│  │         sessions/*.json  clinics/<id>/             │     │
│  └─────────────────────────────────────────────────────┘     │
│                                                               │
│  ┌──────────────┐                                            │
│  │  frontend    │  React + FullCalendar + VAD панель         │
│  │  nginx :80   │                                            │
│  └──────────────┘                                            │
└───────────────────────────────────────────────────────────────┘
                     │
┌────────────────────▼───────────────────────────────────────────┐
│                      ХОСТ Windows                              │
│                                                                │
│  llama-server :8080   Ollama :11434   NVIDIA RTX 4080 12GB    │
│  (Ministral-8B GGUF)  (fallback LLM)  (STT + TTS + LLM)      │
│                                                                │
│  D:\4lin\models\  ──► /models (Docker volume)                 │
│  STT: faster-whisper-large-v3-turbo  •  TTS: Silero .pt (SILERO_MODEL_PATH) │
│  (Silero VAD внутри WLK — отдельно от файла TTS)               │
└────────────────────────────────────────────────────────────────┘
```

### Поток голосового звонка

```
Микрофон
   │ PCM 48kHz 16-bit
   ▼
[audio-processor.worklet.js]  ← TTS suppression (barge-in)
   │ Float32Array chunks
   ▼
WebSocket → main-agent
   │
   ├─► EnergyVAD (только barge-in детекция)
   │
   └─► AudioProcessor.process_audio()
            │ pcm_buffer
            ▼
       WhisperLiveKit
       Silero VAD (см. §9: дефолт в runtime ~0.35 / pad 200 мс / min_silence 400 мс;
       образ Docker патчит «сырой» WLK; живые значения — через API и `.env`)
            │ финальный текст
            ▼
       _run_agent_turn()
            │
            ├─► apply_fast_slot_rules()   ← быстрые правила без LLM
            ├─► extractor.py              ← LLM извлечение слотов
            ├─► script_engine.next_intent() ← следующий шаг диалога
            ├─► llm_companion → LLM       ← генерация ответа
            │
            ▼
       normalize_tts_text()  ← числа/даты → слова
            │
            ▼
       Silero TTS → WAV chunks → WebSocket → браузер → колонки
```

### Поток текстового ввода (тест/отладка)

```
Браузер отправляет: {"event": "user_text", "text": "да"}
   │
   └─► тот же _run_agent_turn() без STT
```

---

## 3. Стек технологий

### Backend (main-agent)

| Компонент | Технология | Назначение |
|-----------|------------|------------|
| Web framework | FastAPI + Uvicorn | REST API + WebSocket |
| STT | WhisperLiveKit + faster-whisper large-v3-turbo | Распознавание речи |
| VAD внешний | EnergyVAD (кастомный) | Barge-in детекция |
| VAD внутренний | Silero VAD (в WLK) | Фильтрация речи перед Whisper |
| LLM | Ministral-8B-Q4_K_M.gguf via llama-server | Генерация ответов |
| LLM fallback | Ollama | Резервный LLM |
| TTS | Silero v5 (kseniya, 48kHz) | Синтез речи |
| GPU | NVIDIA RTX 4080 Mobile 12GB | STT + TTS + LLM |
| Уведомления | Telethon (Telegram) + SMSC (SMS) | Оповещения |
| RAG | rank_bm25 | Поиск по базе знаний клиники |

### Frontend

| Компонент | Технология |
|-----------|------------|
| Framework | React + TypeScript + Vite |
| Аудио | AudioContext + AudioWorklet |
| Календарь | FullCalendar |
| Сервинг | nginx |

### Инфраструктура

| Компонент | Детали |
|-----------|--------|
| Контейнеры | Docker Compose (2 сервиса) |
| GPU доступ | nvidia-container-toolkit |
| База образа | nvidia/cuda:12.9.0-runtime-ubuntu22.04 |
| Хранилище | JSON файлы (sessions/, clinics/) |

---

## 4. Структура репозитория

```
D:\4lin\NEW\
│
├── 🧠 ЯДРО БЭКЕНДА
│   ├── main.py              (137 KB) — FastAPI: WS, REST, VAD, TTS, оркестрация
│   ├── script_engine.py     (108 KB) — Диалоговый движок, слоты, нормализация RU
│   ├── agent_logic.py       (81 KB)  — Дополнительная логика (исторический слой)
│   ├── llm_companion.py     (27 KB)  — LLM вызовы, промпты, форматирование
│   ├── extractor.py         (13 KB)  — Извлечение слотов через LLM
│   ├── schedule.py          (9 KB)   — Слоты, конфликты, расписание
│   ├── clinic_config.py     (18 KB)  — Загрузка конфигов, RAG, кэш клиник
│   ├── tts_engine.py               — Silero TTS движок
│   ├── knowledge_base.py    (3 KB)  — BM25 RAG по knowledge.md
│   ├── sms_service.py              — SMSC интеграция
│   └── telegram_service.py         — Telegram Bot API + Telethon
│
├── 🏥 КОНФИГУРАЦИИ КЛИНИК
│   └── clinics/
│       ├── _shared/
│       │   ├── symptom_specialty_map.json  — 20+ специальностей, 300+ паттернов
│       │   └── specialty_aliases.json      — Алиасы: «лор» → «оториноларинголог»
│       ├── medexpress/           — Многопрофильная клиника (general)
│       │   ├── config.json       — Название, адрес, часы, флаги
│       │   ├── doctors.json      — Список врачей со специальностями
│       │   ├── knowledge.md      — База знаний для RAG
│       │   └── prices.json       — Прайс-лист
│       ├── dental_template/      — Стоматология (dental mode)
│       └── cosmo_template/       — Косметология (cosmetology mode)
│
├── 🎨 ФРОНТЕНД
│   └── frontend/
│       ├── src/App.tsx      (54 KB) — Всё: регистратура + календарь + VAD панель
│       ├── src/main.tsx            — Entry point
│       └── public/
│           └── audio-processor.worklet.js — AudioWorklet: приём аудио + TTS suppression
│
├── 🧪 ТЕСТЫ
│   ├── run_stress_test_50.py    — 50 сценариев (general, медэкспресс)
│   ├── run_stress_test_dental.py — 33 сценария (dental)
│   └── run_tg_auth.py           — Одноразовая авторизация Telethon
│
├── 🐳 ИНФРАСТРУКТУРА
│   ├── docker-compose.yml
│   ├── Dockerfile.main-agent    — CUDA 12.9, патчи WLK/Silero VAD
│   ├── requirements.txt
│   └── .env                     — Все настройки (не коммитить!)
│
└── 📚 ДОКУМЕНТАЦИЯ / ДИАГНОСТИКА
    ├── PROJECT_FULL_STATUS_REPORT.md  (427 KB) — Полная история разработки
    ├── diag/                           — Снимок для диагностики
    └── docs/                           — Доп. документация
```

---

## 5. Как работает диалог

### Состояние сессии (`CallSessionState`)

```python
# Основные слоты которые собирает бот:
slots = {
    "fio": "Георгий Востров",
    "phone": "+7-996-949-72-60",
    "complaint": "болит зуб",
    "specialty": "стоматолог-терапевт",
    "doctor_name": "Бабайлова",
    "doctor_fio": "Бабайлова Екатерина Сергеевна",
    "appointment_datetime_iso": "2026-05-08T11:00",
}

extra_slots = {
    "phone_confirmed": True,
    "allergy_anesthesia": False,    # dental
    "call_reason": "booking",       # booking|lab|reschedule|price_query|...
}

phase = "finalized"  # collecting → awaiting_confirmation → finalized
```

### Диаграмма диалога

```
[Старт сессии]
      │
      ▼
  greeting ──────────────────────────────────────────────────────┐
      │                                                           │
      ▼                                                           │ АОН пробросился?
  ask_name                                                        │ Знаем пациента?
      │                                                    ┌──── ▼
      ▼                                             ДА ────► Персональное приветствие
  ask_phone ◄──────────────────────────────────────        «Здравствуйте, Георгий!»
      │
      ▼
  ask_phone_confirm ─── «нет» ────► ask_phone
      │ «да»
      ▼
  ask_reason / detect_call_reason
      │
      ├── booking ──► ask_complaint ──► ask_specialty (или авто)
      │                   │
      │                   ▼
      │              ask_datetime ──► pending_slot ──► ask_datetime_confirm
      │                                                       │ «да»
      │                                                       ▼
      │                                                    confirm
      │                                                       │ «да»
      │                                                       ▼
      ├── lab ──► ask_lab_type ──► [ask_ct_contrast] ──► ask_datetime ──► confirm
      │
      ├── reschedule ──► find_session ──► verify_ownership ──► ask_new_datetime
      │
      ├── price_query / result_query / doctor_schedule ──► info_only ──► offer_booking
      │
      └── [финализация] ──► SMS + Telegram ──► finalized
```

### Защита от хулиганов

```
Входящий текст
      │
      ├── detect_prompt_injection? ──► предупреждение → abandon (2 попытки)
      ├── detect_abuse? ──────────────► 3 уровня → disconnect
      ├── is_spam_input? ─────────────► 3 попытки → abandon
      ├── detect_multi_booking? ──────► отказ
      └── MAX_TURNS exceeded? ────────► перевести на оператора
```

---

## 6. Мультиклиника

### Как работает

Каждая WebSocket сессия передаёт `clinic_id` в URL:

```
ws://localhost:8000/ws?session_id=xxx&clinic_id=dental_template
```

Бэкенд загружает конфиг из кэша:

```python
session_clinic_cfg = get_clinic_config("dental_template")
session_doctors    = get_doctors("dental_template")
session_kb         = get_knowledge_base("dental_template")
```

### Добавление новой клиники

1. Создать папку `clinics/my_clinic/`
2. Добавить `config.json`, `doctors.json`, `knowledge.md`, `prices.json`
3. Вызвать `POST /admin/clinic/cache/invalidate?clinic_id=my_clinic`
4. Готово — перезапуск не нужен

### Переключение в UI

В браузере появляются кнопки:
```
🏥 МедЭкспресс  🦷 СтомаКлиник  💆 ЭстетикПро
```

При нажатии WS переподключается с новым `clinic_id`.

---

## 7. REST и WebSocket API

### Публичные

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/health` | `{"status": "ok"}` |
| GET | `/healthz` | Алиас для оркестраторов |
| GET | `/clinics` | Список доступных клиник |
| WS | `${WS_PATH:-/ws}?session_id=X&clinic_id=Y` | Голосовая сессия (`WS_PATH` из `.env`, по умолчанию `/ws`) |

### Данные и экспорт

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/sessions` | Все сессии (для календаря) |
| GET | `/slots?days=14` | Сводка слотов |
| GET | `/admin/export/csv` | Экспорт записей в CSV |
| GET | `/admin/export/ical` | Экспорт в iCalendar |

### Администрирование

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/admin/stats` | Сводная статистика |
| GET | `/admin/heatmap` | Тепловая карта / активность |
| GET | `/admin/vad-settings` | Текущие настройки VAD |
| POST | `/admin/vad-settings` | Изменить VAD без перезапуска (query-параметры, см. §9) |
| GET | `/admin/ai-mode` | Режим ИИ по жалобе: `simple` или `enhanced` (см. §9.1) |
| POST | `/admin/ai-mode?mode=simple` | Переключить режим без перезапуска (`simple` \| `enhanced`) |
| GET | `/admin/blacklist` | Список заблокированных номеров |
| POST | `/admin/blacklist/add` | Добавить в blacklist |
| POST | `/admin/blacklist/remove` | Убрать из blacklist |
| GET | `/admin/suspicious` | Подозрительные сессии |
| POST | `/admin/clinic/cache/invalidate` | Сбросить кэш клиники |

### WebSocket события (клиент → сервер)

| Событие | Данные | Описание |
|---------|--------|----------|
| Binary frame | PCM bytes | Аудио от микрофона |
| `user_text` | `{"text": "да"}` | Текстовый ввод |
| `audio_chunk_b64` | base64 PCM | Альтернативный формат аудио |
| `flush_audio` | — | Принудительно закоммитить транскрипт |
| `commit_transcript` | — | Закоммитить накопленный STT (режим WLK) |
| `vad_silence` | — | Сигнал тишины / конец сегмента (зависит от клиента) |

### WebSocket события (сервер → клиент)

| Событие | Данные | Описание |
|---------|--------|----------|
| `assistant_response` | `{"text": "..."}` | Текстовый ответ бота |
| `partial_transcript` | `{"text": "..."}` | Промежуточный транскрипт |
| `final_transcript` | `{"text": "..."}` | Финальный текст сегмента STT |
| `slots_update` | `{"slots": {...}, "phase": "..."}` | Обновление слотов |
| `assistant_tts_start` | — | TTS начался |
| `assistant_tts_end` | — | TTS закончился |
| `barge_in` | — | Пациент перебил бота |
| Binary frames | WAV bytes | Аудио TTS |

---

## 8. Конфигурация клиники

### `config.json`

Режим клиники (`general` / `dental` / `cosmetology`) для шаблонов задаётся в их `config.json`; у **medexpress** поле `clinic_mode` не обязательно — используется профиль **general** в коде. Пример **реального** `clinics/medexpress/config.json` (фрагмент):

```json
{
  "id": "medexpress",
  "name": "МедЭкспресс",
  "greeting": "Здравствуйте, вас приветствует клиника МедЭкспресс. Подскажите, как к вам можно обращаться?",
  "phone": "+7-352-454-55-55",
  "address": "г. Чебоксары, ул. С. Михайлова, дом 1, помещение 16 (филиал: ул. Ярмарочная, д. 5, пом. 1)",
  "work_hours": {
    "mon": { "open": "08:00", "close": "20:00" },
    "tue": { "open": "08:00", "close": "20:00" },
    "wed": { "open": "08:00", "close": "20:00" },
    "thu": { "open": "08:00", "close": "20:00" },
    "fri": { "open": "08:00", "close": "20:00" },
    "sat": { "open": "08:00", "close": "20:00" },
    "sun": { "open": "09:00", "close": "18:00" }
  },
  "requires_passport": true,
  "requires_policy": true,
  "oms_accepted": true,
  "dms_accepted": true,
  "parking": "…",
  "emergency_redirect": "103",
  "timezone": "Europe/Moscow"
}
```

### `doctors.json`

```json
[
  {
    "name": "Автаева",
    "fio": "Автаева Дарья Александровна",
    "specialty": "терапевт",
    "work_days": ["mon", "tue", "wed", "thu", "fri"],
    "work_hours": {"open": "08:00", "close": "16:00"},
    "slot_minutes": 30
  }
]
```

### `knowledge.md`

Свободный текст — база знаний для RAG. Отвечает на вопросы о клинике: цены, услуги, подготовка к анализам, часто задаваемые вопросы.

### Карты симптомов (`_shared/`)

```json
// symptom_specialty_map.json
[
  {
    "specialty": "кардиолог",
    "priority": 20,
    "patterns": ["сердц.*бол", "аритм", "давлени.*высок", ...]
  }
]
```

Приоритет: чем выше — тем раньше проверяется. Онкология (30) > кардиолог (20) > терапевт (10).

---

## 9. Модели и GPU

### Модели на диске (`D:\4lin\models` → `/models` в контейнере)

| Модель | Размер | Назначение |
|--------|--------|------------|
| `faster-whisper-large-v3-turbo` | ~1.5 GB | STT (речь → текст), кэш под `WHISPER_MODEL_CACHE_DIR` |
| Файл Silero TTS (`SILERO_MODEL_PATH`, напр. `v5_4_ru.pt`) | ~150–300 MB | **Только TTS** (озвучка ответа) |
| `Ministral-3-8B-Instruct-2512-Q4_K_M.gguf` (пример) | ~5 GB | LLM на llama-server |
| Другие GGUF | по размеру | Резерв / сравнение моделей |

### GPU распределение (RTX 4080 12GB)

```
┌─────────────────────────────────────┐
│         RTX 4080 12GB               │
│                                     │
│  [Whisper ~2GB] [Silero ~0.5GB]     │
│  [llama-server ~5GB]                │
│  Остаток: ~4.5GB                    │
└─────────────────────────────────────┘
```

### VAD: переменные окружения и код (`main.py`)

Стартовые значения берутся из **`.env`**; если не заданы — используются дефолты в коде. Образ **Docker** дополнительно патчит исходники WhisperLiveKit/Silero (см. `Dockerfile.main-agent`): «сырой» SimulWhisper получает меньшую паузу тишины, Silero в библиотеке — порог **0.35**, pad **200** ms, min silence **400** ms; поверх этого **`main.py`** подставляет значения из `_vad_settings` и **`POST /admin/vad-settings`** (в т.ч. на уже запущенные сессии — патч iterator на AudioProcessor).

| Переменная / ключ API | Дефолт в коде | Назначение |
|------------------------|----------------|------------|
| `WLK_MIN_DURATION_REAL_SILENCE` / `wlk_min_duration_real_silence` | **0.6** с (если не задано в `.env`) | Пауза тишины до финализации сегмента SimulWhisper (меньше — быстрее ответ, риск обрезать хвост фразы) |
| `VAD_ENERGY_THRESHOLD` / `vad_energy_threshold` | **0.006** | Порог энергии **EnergyVAD** (barge-in / перебивание TTS) |
| `VAD_SILENCE_RATIO` / `vad_silence_ratio` | **0.52** | Связь порога «тишины» с порогом речи для EnergyVAD |
| `SILERO_VAD_THRESHOLD` / `silero_threshold` | **0.35** | Порог **Silero VAD** внутри WLK (ниже — тише/короче слышит речь) |
| `SILERO_SPEECH_PAD_MS` / `silero_speech_pad_ms` | **200** ms | Паддинг вокруг сегмента речи Silero |
| `SILERO_MIN_SILENCE_MS` / `silero_min_silence_ms` | **400** ms | Мин. длительность тишины для конца фразы Silero |

Query-параметры для `POST /admin/vad-settings` те же, что в колонке «ключ API» (все опциональны в одном запросе).

---

### Рекомендуемый пресет «Скорость» (хорошо себя показывает на практике)

Быстрый отклик, короткие «да/нет» не теряются; при шумной линии можно сместиться к «точности» (ниже).

| Подпись в UI | Значение «Скорость» | Подсказка по краям диапазона |
|----------------|---------------------|------------------------------|
| **Пауза до отправки (сек)** | **1** | **0.5** — быстрее, но режет слова; **3.0** — медленнее, зато точнее цельный сегмент |
| **Порог обнаружения речи** | **0.004** | **0.003** — ловит шёпот; **0.01** — только громкая речь |
| **Чувствительность к тишине** | **0.45** | **0.3** — быстрее переключается на «тишину»; **0.8** — медленнее, меньше ложных срабатываний |
| **Silero: порог речи** | **0.45** | **0.35** — тихие короткие «да»; **0.5** — типичный дефолт upstream WLK |
| **Silero: паддинг речи (мс)** | **120** | **200** ms — мягче края, не режет начало/конец; **30** ms — дефолт WLK в «сыром» виде |
| **Silero: пауза до конца фразы (мс)** | **250** | **400** ms — не рвёт фразу на короткой паузе; **100** ms — дефолт WLK до патча образа |

Эквивалент одним запросом (подставьте свой хост/порт):

```http
POST /admin/vad-settings?wlk_min_duration_real_silence=1&vad_energy_threshold=0.004&vad_silence_ratio=0.45&silero_threshold=0.45&silero_speech_pad_ms=120&silero_min_silence_ms=250
```

После сохранения новые значения применяются к глобальным настройкам и к активным сессиям (перепривязка Silero VAC).

### 9.1. Режим ИИ по жалобе (`AI_MODE` и `/admin/ai-mode`)

Переменная **`AI_MODE`** в `.env` (или runtime через API) задаёт, как подбирается специальность по тексту жалобы, пока слот `specialty` ещё пуст:

| Значение | Поведение |
|----------|-----------|
| **`simple`** (дефолт) | Только быстрые правила `suggest_specialty_from_complaint_by_mode` в `script_engine.py` — без дополнительных LLM-вызовов на этом шаге. |
| **`enhanced`** | Regex как раньше, если ответ однозначен; иначе параллельно вызываются LLM-классификация по списку специальностей из `doctors.json` (с учётом пола/возраста и последних реплик пациента) и при необходимости — один уточняющий вопрос по расплывчатой жалобе. Ответ с уточнением уходит через основной `generate_companion_reply` (подсказка `clarification_question` в `dialog_hints`). |

Пример переключения без пересборки:

```http
POST /admin/ai-mode?mode=enhanced
```

Во фронтенде (панель «Настройки распознавания») добавлен переключатель «Простой режим / Улучшенный ИИ», который бьёт в те же эндпоинты.

---

## 10. Развёртывание

### Быстрый старт

```powershell
# 1. Настроить окружение
cd D:\4lin\NEW
# Создайте .env вручную (в репозитории может не быть .env.example):
# скопируйте из бэкапа или соберите из раздела «Ключевые переменные» ниже.

# 2. Запустить llama-server на хосте
.\llama-server.exe -m models\Ministral-3-8B-Q4_K_M.gguf `
  --port 8080 --n-gpu-layers 99 --flash-attn --parallel 2

# 3. Собрать и запустить контейнеры
docker compose build
docker compose up -d

# 4. Дождаться готовности (~90 сек для загрузки Whisper)
Start-Sleep 90
curl http://localhost:8000/healthz

# 5. Открыть UI
Start-Process "http://localhost:5173"
```

### Ключевые переменные `.env`

```env
# Клиника по умолчанию
CLINIC_ID=medexpress

# LLM
LLAMA_BASE_URL=http://host.docker.internal:8080/v1
LLAMA_MODEL=Ministral-3-8B-Instruct-2512-Q4_K_M

# STT (часть ключей дублирует пресет из §9)
STT_WHISPER_MODEL=large-v3-turbo
WLK_MIN_DURATION_REAL_SILENCE=1.0
SILERO_VAD_THRESHOLD=0.35
SILERO_SPEECH_PAD_MS=200
SILERO_MIN_SILENCE_MS=400

# TTS
SILERO_SPEAKER=kseniya
SILERO_DEVICE=cuda

# Уведомления
TG_BOT_TOKEN=...
TG_CLINIC_CHAT_ID=...
SMSC_LOGIN=...
SMSC_PASSWORD=...
```

### Docker Compose сервисы

Упрощённо (полный файл — `docker-compose.yml` в корне):

```yaml
services:
  main-agent:
    build:
      context: .
      dockerfile: Dockerfile.main-agent
    env_file: [.env]
    ports: ["${APP_PORT:-8000}:8000"]
    volumes:
      - ./sessions:/app/sessions
      - ./clinics:/app/clinics
      - D:/4lin/models:/models   # кэш Whisper + место под веса
    gpus: all

  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    ports: ["${FRONTEND_PORT:-5173}:80"]
    depends_on: [main-agent]
```

### Обновление кода

```powershell
# Только Python код изменился:
docker compose build main-agent
docker compose up -d main-agent

# Только фронт изменился:
cd frontend && npm run build
docker compose restart frontend

# Только .env изменился (без secrets):
docker compose restart main-agent
```

---

## 11. Тестирование

### Стресс-тесты

```powershell
# Очистить тестовые сессии
Remove-Item D:\4lin\NEW\sessions\*.json -Force

# МедЭкспресс — 50 сценариев (цель: 50/50)
python run_stress_test_50.py > stress_report.txt

# Dental — 33 сценария (цель: 33/33)
python run_stress_test_dental.py > stress_dental_report.txt
```

### Результаты последних прогонов

| Тест | Результат | Дата |
|------|-----------|------|
| medexpress (50 сценариев) | **50/50 (100%)** | 2026-05-04 |
| dental_template (33 сценария) | **33/33 (100%)** | 2026-05-04 |

### Категории тестов (medexpress)

```
base          ✅ 5/5   — базовые записи
auto_specialty ✅ 14/14 — автоопределение специальности
datetime      ✅ 5/5   — форматы дат и времени
correction    ✅ 5/5   — исправление данных
lab           ✅ 3/3   — анализы (КТ, колоноскопия, кровь)
slot_query    ✅ 2/2   — запросы свободных окон
call_reason_switch ✅ 3/3 — смена цели звонка
abuse         ✅ 1/1   — защита от хулиганов
```

### Категории тестов (dental)

```
base          ✅ 3/3   — кариес, консультация, каналы
surgery       ✅ 5/5   — удаление, флюс, абсцесс
prosthetics   ✅ 3/3   — коронки, мосты, виниры
orthodontics  ✅ 5/5   — брекеты, элайнеры, прикус
child         ✅ 2/2   — детская стоматология
allergy       ✅ 2/2   — аллергия на анестезию
```

---

## 12. Известные ограничения и TODO

### Текущие ограничения

| Проблема | Статус | Приоритет |
|----------|--------|-----------|
| STT галлюцинации на коротких «да» | Частично решено (init_prompt, коррекции) | 🟡 |
| Silero VAD во время работы | Применяется runtime-патч iterator после `POST /admin/vad-settings` | ✅ |
| schedule.py использует глобальный clinic_doctors | TODO: параметризация | 🟢 |
| extractor.py ленивый импорт clinic_doctors | TODO: параметризация | 🟢 |
| Сессии не разделены по клиникам | TODO: `sessions/{clinic_id}/` | 🟢 |
| Устаревший год в датах из STT / «N месяца» | Частично снято: подъём года в ISO, цикл для даты без года (`script_engine`) | 🟡 |

### Ближайший роадмап

```
✅ Сделано:
   Мультиклиника (Вариант Б) через WS-параметр
   Стресс-тест 50/50 + 33/33
   VAD панель в UI
   Календарь с фильтрами
   Защита от хулиганов (10 механизмов)
   АОН + персональное приветствие

🔄 В работе:
   Улучшение распознавания коротких фраз
✅ Недавно:
   Выбор слота по времени / «вторник после нет» / отсечение «спасибо» как «да» на подтверждении

📋 Планируется:
   FreePBX + SIP Ростелеком
   bridge.py (SIP ↔ WebSocket)
   Исходящие напоминания через AMI
   Интеграция с МИС (webhook)
   ask_last_dental_visit + ask_xray_available (dental flow)
```

---

## 📞 Контакты и контекст

**Разработчик:** Кереметь-ИТ (keremet-it.ru), Чебоксары  
**Продукт:** МедЖарвис.Регистратура  
**Экосистема:** Галленов Триумф  
**Статус:** MVP в разработке, прошёл стресс-тест 100%  

---

*Документ согласован с репозиторием `D:\4lin\NEW` (код, docker-compose, medexpress `config.json`). Актуален на 2026-05-04; пресет VAD «Скорость» — рабочая конфигурация с UI.*
