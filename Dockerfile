FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PRISMATIC_STATE_DIR=/app/prismatic_state \
    PRISMATIC_HOST=0.0.0.0 \
    PRISMATIC_PORT=9000 \
    PRISMATIC_CORS_ORIGINS=http://127.0.0.1:9000,http://localhost:9000

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY prismatic ./prismatic
COPY plugins ./plugins
COPY scripts ./scripts
COPY docs ./docs
COPY config ./config
COPY .env.example ./

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install -e ".[release]" \
    && mkdir -p /app/prismatic_state

EXPOSE 9000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python scripts/release_smoke.py >/tmp/prismatic-release-smoke.log 2>&1 || exit 1

CMD ["prismatic-gateway", "--host", "0.0.0.0", "--port", "9000"]
