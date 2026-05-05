# MedJarvis Registry MVP

Локальная голосовая регистратура медицинской клиники. Приложение заменяет первичный call-центр для входящего обращения: принимает голос из браузера, режет речь по VAD, распознает через Whisper CT2, ведет диалог, озвучивает ответы Silero TTS и сохраняет заявку в JSON.

Текущий production-style путь для заказчика: готовый Docker-образ `chudopes/medjarvis-registry:latest` плюс отдельный `llama-server` на хосте. `llama.cpp` не входит в контейнер и не запускается автоматически, потому что у заказчика путь к сборке и GGUF-модели может отличаться.

## Текущее Состояние

- Репозиторий: `https://github.com/ChudoPes13/Med_MVP_new.git`, ветка `main`.
- Docker image: `chudopes/medjarvis-registry:latest`.
- GitHub Actions workflow: `.github/workflows/docker-publish.yml`.
- Frontend и backend упакованы в один контейнер, UI доступен на `http://127.0.0.1:8000`.
- LLM внешний: OpenAI-compatible `llama-server` на `http://127.0.0.1:8080/v1`.
- STT: `deepdml/faster-whisper-large-v3-turbo-ct2`, CUDA, `int8`.
- TTS: Silero `v5_5_ru.pt`, голос `kseniya`.
- Аудио не сохраняется. JSON-сессии и логи сохраняются всегда.

## Быстрый Запуск У Заказчика

### 1. Требования

Рекомендуемый production host: Ubuntu 24.04/26.04 с NVIDIA GPU.

Нужно заранее установить:

- NVIDIA driver;
- Docker Engine и Docker Compose plugin;
- NVIDIA Container Toolkit для `--gpus all`;
- рабочий `llama-server` из `llama.cpp`;
- GGUF-модель, например `Ministral-3-3B-Instruct-2512-Q5_K_M.gguf`.

Официальные инструкции, которые стоит использовать при первичной установке окружения:

- Docker Engine Ubuntu: `https://docs.docker.com/installation/ubuntulinux/`;
- NVIDIA Container Toolkit: `https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/`;
- Docker host network driver: `https://docs.docker.com/engine/network/drivers/host/`;
- Docker Desktop Windows: `https://docs.docker.com/desktop/setup/install/windows-install/`.

Для Windows 11 тестов нужен Docker Desktop с WSL2 и GPU support. Если используется `network_mode: host`, в Docker Desktop должна быть включена поддержка host networking. Если host networking недоступен, см. раздел "Windows fallback".

### 2. Подготовить Папку

Linux:

```bash
mkdir -p /opt/medjarvis/{sessions,logs}
cd /opt/medjarvis
```

Windows PowerShell:

```powershell
New-Item -ItemType Directory -Force C:\MedJarvis\sessions, C:\MedJarvis\logs | Out-Null
Set-Location C:\MedJarvis
```

Положите рядом файл `docker-compose.customer.yml` из репозитория или создайте его с таким содержимым:

```yaml
services:
  medjarvis:
    image: chudopes/medjarvis-registry:latest
    container_name: medjarvis-registry
    restart: unless-stopped
    network_mode: host
    gpus: all
    volumes:
      - ./sessions:/app/sessions
      - ./logs:/app/logs
    environment:
      APP_TIMEZONE: Europe/Moscow
      LLAMA_BASE_URL: http://127.0.0.1:8080/v1
      LLAMA_MODEL: Ministral-3-3B-Instruct-2512-Q5_K_M.gguf
      REQUIRE_GPU: "true"
      WHISPER_COMPUTE_TYPE: int8
      WLK_MIN_DURATION_REAL_SILENCE: "1.0"
      VAD_ENERGY_THRESHOLD: "0.004"
      VAD_SILENCE_RATIO: "0.45"
      SILERO_VAD_THRESHOLD: "0.45"
      SILERO_SPEECH_PAD_MS: "120"
      SILERO_MIN_SILENCE_MS: "250"
```

### 3. Запустить LLM На Хосте

Linux:

```bash
/opt/llama.cpp/llama-server \
  -m /opt/models/Ministral-3-3B-Instruct-2512-Q5_K_M.gguf \
  --host 127.0.0.1 \
  --port 8080 \
  --ctx-size 4096 \
  --n-gpu-layers 99 \
  --parallel 2 \
  --flash-attn auto
```

Windows:

```powershell
D:\path\to\llama.cpp\llama-server.exe `
  -m "D:\models\Ministral-3-3B-Instruct-2512-Q5_K_M.gguf" `
  --host 127.0.0.1 `
  --port 8080 `
  --ctx-size 4096 `
  --n-gpu-layers 99 `
  --parallel 2 `
  --flash-attn auto
```

Проверка LLM:

```bash
curl http://127.0.0.1:8080/v1/models
```

PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/v1/models
```

### 4. Запустить MedJarvis

Если Docker Hub image приватный, сначала выполните:

```bash
docker login -u chudopes
```

Дальше:

```bash
docker pull chudopes/medjarvis-registry:latest
docker compose -f docker-compose.customer.yml up -d
```

Проверка:

```bash
docker logs -f medjarvis-registry
curl http://127.0.0.1:8000/health
```

Открыть UI:

```text
http://127.0.0.1:8000
```

### 5. Остановка И Обновление

Остановить:

```bash
docker compose -f docker-compose.customer.yml down
```

Обновить образ:

```bash
docker compose -f docker-compose.customer.yml down
docker pull chudopes/medjarvis-registry:latest
docker compose -f docker-compose.customer.yml up -d
```

Сессии и логи останутся в папках `sessions/` и `logs/`.

## Windows Fallback

Если Docker Desktop не умеет `network_mode: host` или контейнер не видит `127.0.0.1:8080`, используйте fallback:

1. Запустите `llama-server` с `--host 0.0.0.0`.
2. В `docker-compose.customer.yml` удалите строку `network_mode: host`.
3. Добавьте проброс порта:

```yaml
    ports:
      - "8000:8000"
```

4. Замените:

```yaml
      LLAMA_BASE_URL: http://127.0.0.1:8080/v1
```

на:

```yaml
      LLAMA_BASE_URL: http://host.docker.internal:8080/v1
```

Основной дефолт проекта все равно остается `http://127.0.0.1:8080/v1`.

## Локальная Разработка В Этом Репозитории

Windows:

```powershell
Set-Location C:\ai25\Med_MVP_4
.\scripts\setup_windows.ps1
.\scripts\start_llama.ps1
.\scripts\start_backend.ps1
```

В отдельном терминале:

```powershell
.\scripts\start_frontend.ps1
```

Открыть:

```text
https://127.0.0.1:5173
```

Локальные `cert.pem` и `cert-key.pem` используются только для dev HTTPS/WSS и не коммитятся.

## Локальная Docker Сборка

Обычно заказчику локальная сборка не нужна: используется готовый образ из Docker Hub. Для проверки своей сборки:

```powershell
.\scripts\docker_build_local.ps1
```

Скрипт подготовит `docker_assets/models/tts/v5_5_ru.pt`. Если файла нет локально, скачает его из `https://models.silero.ai/models/tts/ru/v5_5_ru.pt`.

## GitHub Actions Release

Workflow `.github/workflows/docker-publish.yml` собирает и пушит Docker image при push в `main`.

Нужные repository secrets:

- `DOCKERHUB_USERNAME`: `chudopes`;
- `DOCKERHUB_TOKEN`: Docker Hub access token;
- `TTS_MODEL_URL`: `https://models.silero.ai/models/tts/ru/v5_5_ru.pt`;
- `HF_TOKEN`: опционально, только если Whisper/HF download начнет упираться в rate limit или приватный доступ.

## Диалоговый Порядок

1. ФИО.
2. Что тревожит пациента и зачем он обратился.
3. Возраст.
4. Желаемый день и время.
5. Подтверждение найденного слота.
6. Телефон.
7. Финальное подтверждение заявки.

Срочные симптомы переводят заявку в `needs_operator`; пациенту проговаривается безопасная рекомендация вызвать `103`, если состояние острое сейчас.

## Расписание В MVP

Файл `docs/clinic_schedule.md` используется как справочник врачей и специальностей. Для MVP-теста любой названный пациентом день и время считаются доступными: backend создает слот с `source=test_flexible`, сохраняя реального врача по специальности.

## Проверки

```powershell
.\venv\Scripts\python.exe -m pytest -q
npm run build
docker compose -f docker-compose.customer.yml config --quiet
```

Последняя проверенная сборка: GitHub Actions `Docker Publish` завершился успешно, образ `chudopes/medjarvis-registry:latest` опубликован.
