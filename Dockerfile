# syntax=docker/dockerfile:1.7

ARG CUDA_VERSION=12.8.1
ARG UBUNTU_VERSION=24.04

FROM node:22-bookworm AS frontend-builder
WORKDIR /src/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nvidia/cuda:${CUDA_VERSION}-runtime-ubuntu${UBUNTU_VERSION} AS runtime
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    libgomp1 \
    python3 \
    python3-pip \
    python3-venv \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN python3 -m venv /opt/venv \
  && /opt/venv/bin/python -m pip install --upgrade pip setuptools wheel \
  && /opt/venv/bin/python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128 \
  && /opt/venv/bin/python -m pip install -r requirements.txt
ENV PATH="/opt/venv/bin:${PATH}"

COPY --from=frontend-builder /src/frontend/dist /app/static

COPY app/ /app/app/
COPY config/ /app/config/
COPY docs/ /app/docs/
COPY scripts/download_models.py /app/scripts/download_models.py
COPY docker/entrypoint.sh /app/docker/entrypoint.sh
COPY docker_assets/ /tmp/docker_assets/

RUN chmod +x /app/docker/entrypoint.sh \
  && mkdir -p /app/models/tts /app/models/whisper /app/sessions /app/logs \
  && if [ -f /tmp/docker_assets/models/tts/v5_4_ru.pt ]; then cp /tmp/docker_assets/models/tts/v5_4_ru.pt /app/models/tts/v5_4_ru.pt; fi \
  && rm -rf /tmp/docker_assets

RUN --mount=type=secret,id=tts_model_url,required=false \
    --mount=type=secret,id=hf_token,required=false \
    set -eu; \
    HF_TOKEN_VALUE=""; \
    if [ -s /run/secrets/hf_token ]; then HF_TOKEN_VALUE="$(cat /run/secrets/hf_token)"; fi; \
    if [ ! -f /app/models/tts/v5_4_ru.pt ] && [ -s /run/secrets/tts_model_url ]; then \
      TTS_URL="$(cat /run/secrets/tts_model_url)"; \
      if [ -n "${HF_TOKEN_VALUE}" ]; then \
        curl -fL -H "Authorization: Bearer ${HF_TOKEN_VALUE}" -o /app/models/tts/v5_4_ru.pt "${TTS_URL}"; \
      else \
        curl -fL -o /app/models/tts/v5_4_ru.pt "${TTS_URL}"; \
      fi; \
    fi; \
    python /app/scripts/download_models.py; \
    test -f /app/models/tts/v5_4_ru.pt

ENV APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    STATIC_DIR=/app/static \
    DATA_DIR=/app/sessions \
    LOGS_DIR=/app/logs \
    DOCS_DIR=/app/docs \
    CONFIG_DIR=/app/config \
    MODELS_DIR=/app/models \
    REQUIRE_GPU=true \
    REQUIRE_SILERO_VAD=true \
    WHISPER_MODEL=deepdml/faster-whisper-large-v3-turbo-ct2 \
    WHISPER_LOCAL_DIR=/app/models/whisper/faster-whisper-large-v3-turbo-ct2 \
    WHISPER_DEVICE=cuda \
    WHISPER_COMPUTE_TYPE=int8 \
    WHISPER_LANGUAGE=ru \
    LLAMA_BASE_URL=http://host.docker.internal:8080/v1 \
    LLAMA_MODEL=Ministral-3-3B-Instruct-2512-Q5_K_M.gguf \
    SILERO_TTS_PATH=/app/models/tts/v5_4_ru.pt \
    SILERO_TTS_DEVICE=cuda \
    SILERO_TTS_SPEAKER=kseniya \
    TTS_ENABLED=true

EXPOSE 8000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
