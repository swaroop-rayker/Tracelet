"""Typed application configuration.

Every setting arrives from the environment with a documented default, validated
once at boot (F14.AC5). Two rules shape this module:

1. **Secrets are never stored in the database and never logged** (F12.AC3).
   Anything sensitive is a ``SecretStr``, so an accidental ``repr`` or log of the
   settings object prints ``**********`` rather than the value.

2. **A setting a later milestone needs must not block this one from booting.**
   Milestone-gated settings are optional here and validated at the point of use
   by :func:`require`, which raises a message naming the variable and the
   feature. The alternative -- requiring every key up front -- would mean M0
   could not start until a Telegram token existed.
"""

from __future__ import annotations

import functools
import zoneinfo
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    """Runtime configuration, read once from the environment."""

    model_config = SettingsConfigDict(
        env_prefix="TRACELET_",
        env_file=None,  # the container receives real env vars; no file reading
        extra="ignore",
        frozen=True,
    )

    # --- core --------------------------------------------------------------
    env: Environment = "development"
    log_level: LogLevel = "INFO"
    web_concurrency: int = Field(default=2, ge=1, le=4)

    # --- domain and edge (ADR-0012, F13.AC5) -------------------------------
    site_address: str = "localhost"
    acme_email: str = ""

    # When true, the real client IP may be taken from CF-Connecting-IP -- but
    # only when the TCP peer is a verified Cloudflare address (F13.AC6). The
    # header alone is never trusted.
    behind_cloudflare: bool = False

    # --- database ----------------------------------------------------------
    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://tracelet_app:change-me-app@db:5432/tracelet"
    )
    migrate_database_url: SecretStr = SecretStr(
        "postgresql+psycopg://tracelet_migrate:change-me-migrate@db:5432/tracelet"
    )
    # tracelet_maint: retention purges, backups and the restore check (ADR-0022). Optional
    # so an instance without it still serves; those jobs say what is missing.
    maint_database_url: SecretStr | None = None
    db_pool_min: int = Field(default=5, ge=1, le=20)
    db_pool_max: int = Field(default=10, ge=1, le=20)

    # The server's own max_connections, mirrored here so the pool arithmetic below
    # can be validated against reality instead of a magic number. Must match the
    # value in docker-compose.yml.
    db_max_connections: int = Field(default=24, ge=4, le=100)

    # Connections held back for maintenance: pg_dump, the restore-verify job, a
    # psql session, and a superuser slot. Without this reserve, a backup can fail
    # simply because the application pool is saturated -- which is exactly when
    # you least want it to.
    db_reserved_connections: int = Field(default=4, ge=1, le=20)

    # --- secrets, milestone-gated ------------------------------------------
    # Optional so M0 boots without them. Each is required by the milestone that
    # introduces its feature; use require() at the point of use.
    session_secret: SecretStr | None = None
    ip_key_file: Path = Path("/run/secrets/ip_key")
    pepper_stable: SecretStr | None = None
    pepper_rotating: SecretStr | None = None
    pepper_fp: SecretStr | None = None

    telegram_bot_token: SecretStr | None = None
    telegram_owner_chat_id: int | None = None
    quiet_hours_start: str = "23:00"
    quiet_hours_end: str = "07:00"

    # --- geolocation sources (F4.AC5) --------------------------------------
    geo_data_dir: Path = Path("/data/geoip")
    external_geo_enabled: bool = True
    # Vendor credentials for the keyed databases (Gate 1 accounts; section 11 row 3).
    # Read under the names .env.example documents, with the TRACELET_ form accepted
    # too. Empty means "not configured": that database is skipped with a reason, and
    # nothing else is affected.
    maxmind_account_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MAXMIND_ACCOUNT_ID", "TRACELET_MAXMIND_ACCOUNT_ID"),
    )
    maxmind_license_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("MAXMIND_LICENSE_KEY", "TRACELET_MAXMIND_LICENSE_KEY"),
    )
    ip2location_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("IP2LOCATION_TOKEN", "TRACELET_IP2LOCATION_TOKEN"),
    )
    ipinfo_token: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("IPINFO_TOKEN", "TRACELET_IPINFO_TOKEN")
    )

    # --- data lifecycle (F12.AC7) ------------------------------------------
    # The retention_* values seed the retention_policy row the first time it is read;
    # after that the dashboard edits the row (DATA_MODEL section 8.5).
    retention_visit_days: int = Field(default=180, ge=1)
    retention_ip_days: int = Field(default=30, ge=1)
    retention_audit_days: int = Field(default=365, ge=1)
    backup_dir: Path = Path("/data/backups")
    backup_daily_keep: int = Field(default=7, ge=1)
    backup_weekly_keep: int = Field(default=4, ge=1)
    backup_download_reminder_days: int = Field(default=7, ge=1)
    # Local hour (reporting_tz) for the nightly backup; the monthly restore check runs on
    # the first day of the month, an hour later. Off-peak for an Indian audience.
    backup_hour: int = Field(default=3, ge=0, le=23)

    # --- system health (F10) -----------------------------------------------
    # The host's /proc and /sys, mounted read-only (F10.AC15). Inside the container,
    # /proc is the container's own view, which is not what System Health reports.
    host_proc_path: Path = Path("/host/proc")
    host_sys_path: Path = Path("/host/sys")
    disk_warn_percent: int = Field(default=85, ge=50, le=99)
    disk_critical_percent: int = Field(default=95, ge=50, le=99)
    memory_warn_percent: int = Field(default=90, ge=50, le=99)
    swap_warn_percent: int = Field(default=50, ge=5, le=99)
    # Load shedding (F15.AC6, health/pressure.py): shed capture work while the host's memory
    # pressure ("some avg10", percent) is at or above this; 0 turns shedding off. The
    # fallback, on a kernel without pressure information, is the swap-in rate.
    shed_memory_pressure: float = Field(default=20.0, ge=0, le=100)
    shed_swapin_pages_per_s: int = Field(default=256, ge=1)

    # --- analytics (ADR-0016) ----------------------------------------------
    # Days and hours are bucketed in this zone. India is UTC+05:30, so UTC buckets
    # would straddle every Indian hour and split an Indian evening across two days.
    # Changing it requires `tracelet analytics rebuild`.
    reporting_tz: str = "Asia/Kolkata"

    # --- derived -----------------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def public_base_url(self) -> str:
        """Always https.

        Caddy terminates TLS on every path, including localhost via its internal CA,
        so there is no deployment where an http link is correct. It would also be
        actively broken: the session cookie carries the Secure flag, so a browser
        would refuse to store it over http and enrolment would silently fail to sign
        the admin in (docs/ERRORS.md E8).
        """
        return f"https://{self.site_address}"

    @model_validator(mode="before")
    @classmethod
    def _treat_blank_as_unset(cls, data: Any) -> Any:
        """Discard blank environment variables so field defaults apply.

        An empty variable is not an absent one. ``TRACELET_TELEGRAM_OWNER_CHAT_ID=``
        arrives as ``""``, which cannot be parsed as ``int | None`` -- so the whole
        process refuses to boot over a setting that is not needed until M6.

        This matters because ``.env.example`` deliberately ships every
        milestone-gated key present but blank, and says that leaving them blank is
        correct. Without this, that instruction would be a boot failure for every
        optional typed field, one at a time, as each milestone arrives.

        Handled once here rather than per field: the next optional ``int`` or
        ``Path`` setting added would otherwise reintroduce it. See docs/ERRORS.md E5.
        """
        if isinstance(data, dict):
            return {
                key: value
                for key, value in data.items()
                if not (isinstance(value, str) and not value.strip())
            }
        return data

    @field_validator("reporting_tz")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        """Refuse to boot on a zone neither Python nor tzdata knows (ADR-0016)."""
        try:
            zoneinfo.ZoneInfo(value)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError) as exc:
            msg = f"TRACELET_REPORTING_TZ={value!r} is not an IANA timezone name."
            raise ValueError(msg) from exc
        return value

    @model_validator(mode="after")
    def _check_cross_field_invariants(self) -> Settings:
        if self.db_pool_max < self.db_pool_min:
            msg = (
                f"TRACELET_DB_POOL_MAX ({self.db_pool_max}) must be >= "
                f"TRACELET_DB_POOL_MIN ({self.db_pool_min})."
            )
            raise ValueError(msg)

        # The pool is PER PROCESS, so the real bound is pool x workers, and the
        # maintenance reserve has to come out of the same budget. An oversized
        # pool would exhaust the server rather than provide backpressure, which is
        # the pool's job as rate-limit layer 3 (ADR-0010).
        peak = self.db_pool_max * self.web_concurrency
        available = self.db_max_connections - self.db_reserved_connections
        if peak > available:
            msg = (
                f"Connection budget exceeded: TRACELET_DB_POOL_MAX ({self.db_pool_max}) "
                f"x TRACELET_WEB_CONCURRENCY ({self.web_concurrency}) = {peak}, but only "
                f"{available} are available (max_connections {self.db_max_connections} "
                f"minus {self.db_reserved_connections} reserved for maintenance). "
                "Lower the pool, lower the worker count, or raise max_connections in "
                "docker-compose.yml and TRACELET_DB_MAX_CONNECTIONS together."
            )
            raise ValueError(msg)

        # Two retention clocks on purpose: the encrypted IP expires long before
        # the visit row does (ADR-0007). An IP TTL longer than the visit
        # retention is meaningless, because the row carrying it is already gone.
        if self.retention_ip_days > self.retention_visit_days:
            msg = (
                f"TRACELET_RETENTION_IP_DAYS ({self.retention_ip_days}) cannot exceed "
                f"TRACELET_RETENTION_VISIT_DAYS ({self.retention_visit_days})."
            )
            raise ValueError(msg)

        return self

    def require(self, field: str, feature: str) -> str:
        """Return a milestone-gated secret, or explain precisely what is missing.

        Used at the point of use rather than at boot, so an unconfigured feature
        produces an actionable message instead of preventing startup.
        """
        value = getattr(self, field, None)
        if value is None or (isinstance(value, SecretStr) and not value.get_secret_value()):
            msg = f"{feature} requires TRACELET_{field.upper()} to be set. See .env.example."
            raise RuntimeError(msg)
        return value.get_secret_value() if isinstance(value, SecretStr) else str(value)


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton.

    Cached so configuration is parsed and validated exactly once. Tests clear the
    cache via ``get_settings.cache_clear()``.
    """
    return Settings()
