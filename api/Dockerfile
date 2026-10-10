# ---------------------------------------------------------------------------
# Tracelet API image.
#
# Target RSS ~230 MB for Gunicorn plus 2 Uvicorn workers
# (docs/ARCHITECTURE.md §6). Kept lean deliberately: no compiler in the final
# image, no dev dependencies in production, non-root user.
# ---------------------------------------------------------------------------

# --- PostgreSQL 16 client binaries (ADR-0022) ------------------------------
#
# pg_dump and pg_restore for backups and the restore check, matching the
# server's major version. Debian's own client is 17, whose pg_restore emits
# settings a 16 server rejects. Only the two binaries are copied into the image:
# the package's wrapper scripts are Perl, and installing it whole adds ~120 MB
# for nothing they need. They link the image's libpq5, which is ABI-stable.
# The repository key is vendored (fingerprint B97B0AFC...ACCC4CF8), not fetched.
#
# A release build (ADR-0026) has no root in certs/, so a local root arrives as the
# BuildKit secret `local_ca` instead. This stage never ships -- only two binaries leave
# it -- so trusting the root here leaks nothing into the runtime image.
FROM python:3.13-slim AS pgclient
COPY certs/ /usr/local/share/ca-certificates/
COPY docker/pgdg.asc /usr/share/keyrings/pgdg.asc
RUN --mount=type=secret,id=local_ca \
    if [ -s /run/secrets/local_ca ]; then \
        cp /run/secrets/local_ca /usr/local/share/ca-certificates/zz-build-only.crt; \
    fi \
 && update-ca-certificates \
 && . /etc/os-release \
 && echo "deb [signed-by=/usr/share/keyrings/pgdg.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-16 \
 && rm -rf /var/lib/apt/lists/*


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
      ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=pgclient /usr/lib/postgresql/16/bin/pg_dump /usr/lib/postgresql/16/bin/pg_restore /usr/local/bin/

# --- local trust anchors, for a development machine only -------------------
#
# Empty in CI and in a release image, where this whole block is a no-op costing one
# cached layer. A release is built from `git archive`, where certs/*.crt cannot exist
# (git-ignored), and `tl release` checks the result (ADR-0026). It exists for a machine whose antivirus, VPN or corporate proxy
# performs TLS interception: browsers trust the re-signing root because the OS
# does, but a container carries its own CA bundle and does not — so every
# outbound HTTPS request fails, starting with pip in the next stage.
# See api/certs/README.md.
#
# Both stores are updated, and that is the part worth knowing: Python's HTTP
# clients do NOT read the system store. `pip` and `httpx` both use certifi, so
# installing the root system-wide alone fixes apt and leaves Telegram delivery
# failing in a way that looks unrelated (docs/ERRORS.md E21).
COPY certs/ /usr/local/share/ca-certificates/
RUN update-ca-certificates

# pip reads its own vendored bundle, so it is pointed at the system store, which
# update-ca-certificates has just refreshed.
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt


# --- dependency layer ------------------------------------------------------
# Copied alone so a source change does not invalidate the dependency install.
FROM base AS deps

COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    mkdir -p src/tracelet && touch src/tracelet/__init__.py \
 && pip install --no-cache-dir -e ".[dev]" \
 && for crt in /usr/local/share/ca-certificates/*.crt; do \
        [ -e "$crt" ] && cat "$crt" >> "$(python -m certifi)"; \
    done; true


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

# The `local_ca` secret (a release build on a machine that re-signs HTTPS, ADR-0026) is
# trusted by pip for this one step only: the combined bundle is deleted in the same
# layer, and the secret itself is never written to one.
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=secret,id=local_ca \
    if [ -s /run/secrets/local_ca ]; then \
        cat /etc/ssl/certs/ca-certificates.crt /run/secrets/local_ca > /tmp/build-ca.pem; \
        export PIP_CERT=/tmp/build-ca.pem; \
    fi \
 && pip install --no-cache-dir . \
 && rm -f /tmp/build-ca.pem \
 && for crt in /usr/local/share/ca-certificates/*.crt; do \
        [ -e "$crt" ] && cat "$crt" >> "$(python -m certifi)"; \
    done; true

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
