"""baseline

Revision ID: 0001
Revises: None
Created: 2026-09-26

M0 creates no application tables -- that is correct, not an oversight. Its job
is to establish the migration chain and to fail loudly and early if the database
was not initialised by ``db/init/01-extensions-and-roles.sh``.

That check matters because the extensions and the three roles are created on
first boot by the Postgres entrypoint, which only runs against an EMPTY volume.
A database restored or provisioned some other way will be missing them, and the
resulting failures appear much later and much less clearly -- a geofence query
failing on an unknown type is a confusing way to learn that PostGIS was never
installed.

Tables arrive from M1 onward:
  M1  admins, sessions, admin_recovery_codes, admin_enrollment_tokens, audit_log
  M2  links, visits
  M3  visit_candidates, asn_profiles, rdns_city_codes, geo_databases,
      inference_settings, geo_cache
  M6  geofences, outbox
  M7  retention_policy, app_settings, backups, rate_limit_buckets
  M5  rollup_visit_daily, rollup_visit_hourly
  M8  ground_truth_labels, accuracy_runs

See docs/DATA_MODEL.md.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REQUIRED_EXTENSIONS = ("postgis", "citext")
REQUIRED_ROLES = ("tracelet_app", "tracelet_maint", "tracelet_migrate")


def upgrade() -> None:
    conn = op.get_bind()

    missing_ext = [
        name
        for name in REQUIRED_EXTENSIONS
        if conn.execute(
            sa.text("SELECT 1 FROM pg_extension WHERE extname = :n"), {"n": name}
        ).scalar()
        is None
    ]
    if missing_ext:
        msg = (
            f"Required extension(s) not installed: {', '.join(missing_ext)}. "
            "These are created on first boot by db/init/01-extensions-and-roles.sh, "
            "which only runs against an empty database volume. Either recreate the "
            "volume (docker compose down -v) or install them by hand as superuser."
        )
        raise RuntimeError(msg)

    missing_roles = [
        name
        for name in REQUIRED_ROLES
        if conn.execute(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :n"), {"n": name}
        ).scalar()
        is None
    ]
    if missing_roles:
        msg = (
            f"Required role(s) missing: {', '.join(missing_roles)}. The three-role "
            "split is what makes the audit log append-only at the engine level "
            "(NFR5.AC5, docs/DATA_MODEL.md §12). See "
            "db/init/01-extensions-and-roles.sh."
        )
        raise RuntimeError(msg)


def downgrade() -> None:
    # Nothing to undo. The extensions and roles are created outside the migration
    # chain and are not this revision's to remove.
    pass
