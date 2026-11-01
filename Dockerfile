FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# curl for the health check; build tools only for tgcrypto, removed again
RUN apt-get update && apt-get install -y --no-install-recommends curl gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt && apt-get purge -y gcc libc6-dev && apt-get autoremove -y

COPY . .
RUN useradd -m -u 10001 bot && mkdir -p /app/data && chown -R bot /app
USER bot
ENV SESSION_DIR=/app/data
VOLUME /app/data

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -sf http://localhost:${PORT:-8080}/health || exit 1

CMD ["python", "bot.py"]
