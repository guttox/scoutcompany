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

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:5005/health || exit 1

# Production: gunicorn com 4 workers × 4 threads (até 16 requests paralelas).
# Substitui Flask dev server (frágil, single-process, sem auto-recovery).
# Se um worker travar, gunicorn reinicia ele sozinho.
# Healthcheck + restart: unless-stopped no compose = Docker reinicia se ficar unhealthy.
CMD ["gunicorn", "--bind", "0.0.0.0:5005", \
     "--workers", "4", "--threads", "4", \
     "--worker-class", "gthread", \
     "--timeout", "60", "--graceful-timeout", "20", \
     "--access-logfile", "-", "--error-logfile", "-", \
     "--chdir", "scripts", \
     "webhook_server:app"]
