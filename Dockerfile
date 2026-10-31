# syntax=docker/dockerfile:1
# Multi-stage build: the runtime image has no compiler, no uv, and no build cache.
FROM python:3.12-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --create-home app

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

ARG BUILD_VERSION=dev
ARG BUILD_SHA=unknown
ENV BUILD_VERSION=${BUILD_VERSION} \
    BUILD_SHA=${BUILD_SHA}

LABEL org.opencontainers.image.title="telegram-auto-poll" \
      org.opencontainers.image.description="Scheduled Telegram polls, administered by DM" \
      org.opencontainers.image.source="https://github.com/ReivaxBoido/telegram-auto-poll" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${BUILD_VERSION}" \
      org.opencontainers.image.revision="${BUILD_SHA}"

USER 10001:10001
EXPOSE 8080
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/livez', timeout=2)"]

ENTRYPOINT ["python", "-m", "telegram_auto_poll"]
