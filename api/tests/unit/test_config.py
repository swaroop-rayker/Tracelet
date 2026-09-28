"""Configuration validation.

Two things are guarded here, both of which cost a broken boot when they regress:

1. **Blank environment variables behave as unset.** ``.env.example`` ships every
   milestone-gated key present but blank, and says leaving them blank is correct.
   Without the ``mode="before"`` validator that is a boot failure for every
   optional typed field, arriving one milestone at a time (docs/ERRORS.md E5).

2. **The connection budget.** The pool is per process, so an innocent-looking
   worker-count change can silently starve ``pg_dump`` of a connection
   (docs/ERRORS.md E2).
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from tracelet.config import Settings

# Keys .env.example ships blank until the milestone that needs them.
BLANK_IN_ENV_EXAMPLE = [
    "TRACELET_TELEGRAM_OWNER_CHAT_ID",  # int | None  -- the one that broke the boot
    "TRACELET_TELEGRAM_BOT_TOKEN",  # SecretStr | None
    "TRACELET_SESSION_SECRET",  # SecretStr | None
    "TRACELET_PEPPER_STABLE",
    "TRACELET_PEPPER_ROTATING",
    "TRACELET_PEPPER_FP",
]


@pytest.mark.parametrize("key", BLANK_IN_ENV_EXAMPLE)
def test_blank_env_var_is_treated_as_unset(key: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(key, "")
    settings = Settings()  # must not raise
    assert settings.telegram_owner_chat_id is None or isinstance(
        settings.telegram_owner_chat_id, int
    )


def test_whitespace_only_env_var_is_also_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A trailing space after `=` in a hand-edited .env is easy to introduce."""
    monkeypatch.setenv("TRACELET_TELEGRAM_OWNER_CHAT_ID", "   ")
    assert Settings().telegram_owner_chat_id is None


def test_a_real_value_still_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    """The blank-stripping must not swallow legitimate values."""
    monkeypatch.setenv("TRACELET_TELEGRAM_OWNER_CHAT_ID", "123456789")
    assert Settings().telegram_owner_chat_id == 123456789


def test_connection_budget_rejects_an_oversubscribed_pool() -> None:
    """E2: pool x workers must leave the maintenance reserve intact.

    10 x 4 = 40 against 24 - 4 = 20 available. A backup would fail at peak traffic,
    so this refuses to start instead.
    """
    with pytest.raises(ValidationError, match="Connection budget exceeded"):
        Settings(db_pool_max=10, web_concurrency=4)


def test_connection_budget_accepts_the_documented_defaults() -> None:
    """The defaults must actually be valid -- E2 was exactly this failing."""
    settings = Settings()
    peak = settings.db_pool_max * settings.web_concurrency
    available = settings.db_max_connections - settings.db_reserved_connections
    assert peak <= available, f"{peak} > {available}"


def test_pool_min_cannot_exceed_pool_max() -> None:
    with pytest.raises(ValidationError, match="must be >="):
        Settings(db_pool_min=10, db_pool_max=5)


def test_ip_ttl_cannot_outlive_visit_retention() -> None:
    """Two retention clocks, and the IP one has to be the shorter (ADR-0007).

    An IP TTL longer than the visit retention is meaningless: the row carrying the
    ciphertext is already gone.
    """
    with pytest.raises(ValidationError, match="cannot exceed"):
        Settings(retention_ip_days=200, retention_visit_days=180)


def test_require_names_the_variable_and_the_feature() -> None:
    """A milestone-gated secret must fail with an actionable message.

    Checked at the point of use rather than at boot, so an unconfigured feature
    does not prevent the process from starting.
    """
    settings = Settings()
    with pytest.raises(RuntimeError, match="TRACELET_TELEGRAM_BOT_TOKEN"):
        settings.require("telegram_bot_token", "Telegram notifications")


def test_secrets_are_not_exposed_by_repr() -> None:
    """A settings object reaching a log must not print its secrets."""
    settings = Settings(session_secret=SecretStr("super-secret-value"))
    dumped = repr(settings)
    assert "super-secret-value" not in dumped
    assert "**********" in dumped


@pytest.mark.parametrize(
    "site_address",
    ["localhost", "localhost:8443", "127.0.0.1", "tracelet.example.com", "sub.domain.example"],
)
def test_public_base_url_is_always_https(site_address: str) -> None:
    """docs/ERRORS.md E8 — there is no deployment where an http link is correct.

    Caddy terminates TLS on every path, including localhost through its internal
    CA. An http enrollment link is not merely untidy: the session cookie carries
    ``Secure``, so the browser refuses to store it and enrolment completes while
    silently failing to sign the admin in.
    """
    url = Settings(site_address=site_address).public_base_url
    assert url == f"https://{site_address}"
    assert not url.startswith("http://")
