FROM python:3.11.9-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/Sao_Paulo \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        tzdata curl ca-certificates fonts-dejavu-core \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Deps primeiro (cache layer)
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# Código
COPY scripts ./scripts
COPY brand ./brand
COPY data/blocklist.txt ./data/blocklist.txt

# Volumes persistentes (dados que não vão pra DB ainda)
RUN mkdir -p /app/data /app/conversas /app/mensagens /app/logs /app/relatorios

EXPOSE 5005

# Default: webhook server. Cron jobs usam `docker compose exec` ou override.
CMD ["python", "scripts/webhook_server.py"]
