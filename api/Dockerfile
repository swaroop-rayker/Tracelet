# ---------------------------------------------------------------------------
# Tracelet API image.
#
# Target RSS ~230 MB for Gunicorn plus 2 Uvicorn workers
# (docs/ARCHITECTURE.md §6). Kept lean deliberately: no compiler in the final
# image, no dev dependencies in production, non-root user.
# ---------------------------------------------------------------------------

FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl is not installed: the healthcheck uses Python's stdlib instead, which is
# already present and keeps the image smaller.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      # libpq for psycopg (used by Alembic only)
      libpq5 \
 && rm -rf /var/lib/apt/lists/*


# --- dependency layer ------------------------------------------------------
# Copied alone so a source change does not invalidate the dependency install.
FROM base AS deps

COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    mkdir -p src/tracelet && touch src/tracelet/__init__.py \
 && pip install --no-cache-dir -e ".[dev]"


# --- development / CI ------------------------------------------------------
# Includes the dev toolchain, so ruff, mypy and pytest run inside the container
# and need no Python on the host.
FROM deps AS dev

COPY . .
RUN pip install --no-cache-dir -e ".[dev]"

ENV TRACELET_ENV=development
EXPOSE 8000
CMD ["uvicorn", "tracelet.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]


# --- production ------------------------------------------------------------
FROM base AS production

COPY pyproject.toml ./
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir .

# Non-root. The only writable paths are the two mounted data volumes.
RUN useradd --system --create-home --uid 10001 tracelet \
 && mkdir -p /data/geoip /data/backups \
 && chown -R tracelet:tracelet /data
USER tracelet

EXPOSE 8000

# Gunicorn supervises the Uvicorn workers, which gives request recycling —
# max-requests bounds the impact of any slow memory growth on a 1 GB box.
# Worker count comes from TRACELET_WEB_CONCURRENCY (2 in production; drop to 1
# as the first lever if memory gets tight — see ADR-0012 revisit conditions).
CMD ["sh", "-c", "exec gunicorn tracelet.main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers ${TRACELET_WEB_CONCURRENCY:-2} \
    --bind 0.0.0.0:8000 \
    --max-requests 2000 \
    --max-requests-jitter 200 \
    --graceful-timeout 20 \
    --timeout 30 \
    --access-logfile - \
    --error-logfile -"]
