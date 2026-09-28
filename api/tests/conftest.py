"""Shared fixtures.

Integration tests run against a **real** PostgreSQL + PostGIS instance and never
mock the database (ES3, F14.AC7). Locally that is the ``db`` compose service;
in CI it is a ``postgis/postgis`` service container. The connection URL comes
from ``TRACELET_DATABASE_URL`` either way, so there is exactly one code path.

Two details in here are load-bearing rather than incidental:

**The client speaks https.** The session cookie carries ``Secure``, and a cookie
jar will not return a ``Secure`` cookie to an ``http://`` URL -- so a client with
an http base URL stores the session and then never sends it, and every
authenticated test fails for a reason that has nothing to do with the code under
test. The base URL also has to match ``site_address``, because CSRF validates the
request ``Origin`` (F8.AC11).

**The encryption key is generated here.** ``/run/secrets/ip_key`` is bind-mounted
locally but does not exist in CI, and the suite must not need it: it writes its own
key, so TOTP enrolment exercises the real AES-256-GCM envelope either way.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from tracelet.config import Settings
from tracelet.crypto.envelope import KEY_BYTES, reset_key_cache
from tracelet.db.engine import dispose_engine, init_engine
from tracelet.main import create_app

# The suite's own site address. Every Origin it sends, and the base URL of every
# client, is derived from this so the CSRF checks see a coherent origin.
SITE_ADDRESS = "localhost"
BASE_URL = f"https://{SITE_ADDRESS}"


@pytest.fixture(scope="session")
def ip_key_file(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A real AES-256 key for the suite, independent of the bind mount."""
    path = tmp_path_factory.mktemp("secrets") / "ip_key"
    path.write_text(secrets.token_hex(KEY_BYTES), encoding="ascii")
    reset_key_cache()
    yield path
    reset_key_cache()


@pytest.fixture
def settings(ip_key_file: Path) -> Settings:
    """Settings for a unit test. No database is touched."""
    return Settings(
        env="development",
        log_level="WARNING",
        site_address=SITE_ADDRESS,
        ip_key_file=ip_key_file,
    )


@pytest.fixture
def app(settings: Settings) -> Iterator[object]:
    """An app instance with no database engine initialised.

    Suitable for unit tests of the error contract and routing. Anything touching
    the database belongs in tests/integration.
    """
    yield create_app(settings)


@pytest.fixture
async def client(app: object) -> AsyncIterator[AsyncClient]:
    """In-process HTTP client. Exercises the real middleware and handler stack."""
    transport = ASGITransport(app=app)  # type: ignore[arg-type]  # fixture is typed loosely to avoid importing FastAPI here
    async with AsyncClient(transport=transport, base_url=BASE_URL) as ac:
        yield ac


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


@pytest.fixture
def integration_settings(ip_key_file: Path) -> Settings:
    url = os.environ.get("TRACELET_DATABASE_URL")
    if not url:
        pytest.skip("TRACELET_DATABASE_URL is not set; integration tests need a real database")
    return Settings(
        env="development",
        log_level="WARNING",
        database_url=SecretStr(url),
        # Pinned rather than inherited from the environment: the CSRF origin check
        # and the enrollment URLs both derive from it, so a developer's .env must
        # not be able to change what the suite asserts.
        site_address=SITE_ADDRESS,
        ip_key_file=ip_key_file,
        # A placeholder, never a real token. Present so the Telegram code paths are
        # reachable and reach their error branch honestly; the one test that asserts
        # successful delivery intercepts the send instead of letting it out.
        telegram_bot_token=SecretStr("integration-test-bot-token"),
        # The capture path degrades rather than failing when these are absent -- so a
        # suite without them would pass while testing visits with no ip_hmac and no
        # enrichment nonce. Present, so the real code paths run.
        pepper_stable=SecretStr("integration-test-pepper-stable-0123456789abcdef"),
        pepper_rotating=SecretStr("integration-test-pepper-rotating-0123456789abcd"),
        pepper_fp=SecretStr("integration-test-pepper-fp-0123456789abcdef0123"),
        session_secret=SecretStr("integration-test-session-secret-0123456789abcd"),
    )


@pytest.fixture
async def db_app(integration_settings: Settings) -> AsyncIterator[object]:
    """An app with a live engine against the real database."""
    application = create_app(integration_settings)
    init_engine(integration_settings)
    try:
        yield application
    finally:
        await dispose_engine()


@pytest.fixture
async def db_client(db_app: object) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=db_app)  # type: ignore[arg-type]  # see client fixture
    async with AsyncClient(transport=transport, base_url=BASE_URL) as ac:
        yield ac
