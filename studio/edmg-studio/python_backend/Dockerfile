FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    EDMG_STUDIO_HOME=/studio \
    EDMG_STUDIO_DATA_DIR=/studio/data \
    EDMG_STUDIO_MODELS_DIR=/studio/models \
    EDMG_STUDIO_CACHE_DIR=/studio/cache \
    EDMG_STUDIO_LOGS_DIR=/studio/logs \
    EDMG_STUDIO_EXTERNAL_DIR=/studio/external \
    EDMG_FFMPEG_PATH=ffmpeg \
    EDMG_STUDIO_BACKEND_HOST=0.0.0.0 \
    EDMG_STUDIO_BACKEND_PORT=7863

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    ffmpeg \
    git \
    libgomp1 \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/edmg/python_backend

COPY . /opt/edmg/python_backend

RUN python -m pip install --upgrade pip "setuptools==70.0.0" wheel \
    && python -m pip install -e ".[studio_bundle]"

RUN mkdir -p /studio/data /studio/models /studio/cache /studio/logs /studio/external

EXPOSE 7863

VOLUME ["/studio/data", "/studio/models", "/studio/cache", "/studio/logs", "/studio/external"]

CMD ["sh", "-lc", "edmg-studio-backend serve --host ${EDMG_STUDIO_BACKEND_HOST:-0.0.0.0} --port ${EDMG_STUDIO_BACKEND_PORT:-7863}"]
