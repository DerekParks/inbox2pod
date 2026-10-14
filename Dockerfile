FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

# Dependencies first: this layer only rebuilds when the lockfile changes
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY run.py ./
COPY inbox2pod/ ./inbox2pod/
RUN uv sync --frozen --no-dev

ENV DATABASE_URI=sqlite:////app/data/inbox2pod.db \
    RAW_STORAGE_PATH=/app/data/raw \
    MP3_STORAGE_PATH=/app/data/audio

RUN useradd --uid 10001 --create-home inbox2pod \
    && mkdir -p /app/data/raw /app/data/audio \
    && chown -R inbox2pod:inbox2pod /app

USER inbox2pod

EXPOSE 5003 2525

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','5003')+'/health', timeout=5)"

CMD ["python", "run.py"]
