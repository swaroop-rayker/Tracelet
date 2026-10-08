"""Backups and the restore check, against the real database (F10.AC11, F12.AC9-AC10,
ADR-0022).

``pg_dump`` and ``pg_restore`` run for real, as ``tracelet_maint``, and the restore goes
into the real scratch database made from ``tracelet_verify_template`` -- the only stand-in
is the backup directory, a temporary one. The suite shares the dev database, so the rows
these tests create are removed afterwards (the application itself never deletes them).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import uuid
import zoneinfo
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text

from tests.integration import helpers
from tests.integration.helpers import ClientFactory, SignedIn, TotpClock
from tracelet.audit import log as audit
from tracelet.auth.models import AdminRole
from tracelet.config import Settings
from tracelet.db.engine import session_scope
from tracelet.lifecycle import backups
from tracelet.lifecycle.models import BackupKind

pytestmark = pytest.mark.integration

BACKUPS = "/api/v1/health/backups"


@pytest.fixture
def integration_settings(integration_settings: Settings, tmp_path: Path) -> Settings:
    if integration_settings.maint_database_url is None:
        pytest.skip("TRACELET_MAINT_DATABASE_URL is not set")
    return integration_settings.model_copy(update={"backup_dir": tmp_path / "backups"})


@pytest.fixture(autouse=True)
async def _own_rows_removed(db_app: object) -> AsyncIterator[None]:
    del db_app
    async with session_scope() as db:
        since = (await db.execute(text("SELECT now()"))).scalar_one()
    yield
    async with session_scope() as db:
        await db.execute(text("DELETE FROM restore_checks WHERE started_at >= :t"), {"t": since})
        await db.execute(text("DELETE FROM backups WHERE started_at >= :t"), {"t": since})


async def _finish() -> None:
    tasks = list(backups._TASKS)
    assert tasks, "no job was started"
    await asyncio.gather(*tasks)


async def _backed_up(owner: SignedIn) -> dict[str, object]:
    started = await owner.client.post(BACKUPS, headers=owner.headers())
    assert started.status_code == 202, started.text
    await _finish()
    listing = (await owner.client.get(BACKUPS)).json()
    (row,) = [b for b in listing["backups"] if b["id"] == started.json()["id"]]
    return dict(row)


async def _live_count(table: str) -> int:
    async with session_scope() as db:
        return int((await db.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one())  # noqa: S608 -- a literal from this file


async def test_a_backup_is_complete_checksummed_and_downloadable(
    owner: SignedIn, integration_settings: Settings
) -> None:
    row = await _backed_up(owner)
    assert row["status"] == "ok", row["error"]
    assert row["kind"] == "manual"
    path = integration_settings.backup_dir / str(row["file_name"])
    assert path.is_file()
    assert not list(integration_settings.backup_dir.glob("*.partial"))
    assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]
    assert row["size_bytes"] == path.stat().st_size
    assert isinstance(row["tables"], int) and row["tables"] > 10
    async with session_scope() as db:
        counts = (
            await db.execute(
                text("SELECT row_counts FROM backups WHERE id = :id"), {"id": row["id"]}
            )
        ).scalar_one()
    # The dump's own counts: nothing else writes while the suite runs.
    assert counts["links"] == await _live_count("links")
    assert counts["audit_log"] >= 1
    assert "spatial_ref_sys" not in counts  # PostGIS's own table, from the template

    downloaded = await owner.client.get(f"{BACKUPS}/{row['id']}/download")
    assert downloaded.status_code == 200
    assert hashlib.sha256(downloaded.content).hexdigest() == row["sha256"]
    assert downloaded.headers["x-content-sha256"] == row["sha256"]
    assert "attachment" in downloaded.headers["content-disposition"]
    listing = (await owner.client.get(BACKUPS)).json()
    assert listing["download"]["overdue"] is False
    actions = await helpers.audit_actions(owner.id)
    assert audit.Action.BACKUP_REQUESTED in actions
    assert audit.Action.BACKUP_DOWNLOADED in actions


async def test_a_backup_restores_with_every_count_equal(owner: SignedIn) -> None:
    row = await _backed_up(owner)
    started = await owner.client.post(
        f"{BACKUPS}/{row['id']}/verify-restore", headers=owner.headers()
    )
    assert started.status_code == 202, started.text
    await _finish()

    listing = (await owner.client.get(BACKUPS)).json()
    check = listing["last_restore_check"]
    assert check["status"] == "passed", (check["error"], check["mismatches"])
    assert check["mismatches"] is None
    (verified,) = [b for b in listing["backups"] if b["id"] == row["id"]]
    assert verified["last_restore_check"]["status"] == "passed"
    async with session_scope() as db:
        scratch = (
            await db.execute(
                text("SELECT count(*) FROM pg_database WHERE datname = 'tracelet_verify'")
            )
        ).scalar_one()
    assert scratch == 0  # dropped afterwards


async def test_a_short_restore_fails_and_says_which_table(
    owner: SignedIn, integration_settings: Settings
) -> None:
    row = await _backed_up(owner)
    async with session_scope() as db:
        await db.execute(
            text(
                "UPDATE backups SET row_counts = jsonb_set(row_counts, '{links}', "
                "to_jsonb((row_counts->>'links')::int + 1)) WHERE id = :id"
            ),
            {"id": row["id"]},
        )
    check_id = await backups.begin_restore_check(uuid.UUID(str(row["id"])), BackupKind.MANUAL, None)
    result = await backups.run_restore_check(check_id, integration_settings)
    assert result.status.value == "failed"
    assert result.mismatches is not None
    assert set(result.mismatches) == {"links"}
    assert result.mismatches["links"]["restored"] == result.mismatches["links"]["expected"] - 1


async def test_a_damaged_file_fails_the_check_with_pg_restores_reason(
    owner: SignedIn, integration_settings: Settings
) -> None:
    row = await _backed_up(owner)
    path = integration_settings.backup_dir / str(row["file_name"])
    path.write_bytes(path.read_bytes()[: path.stat().st_size // 3])
    check_id = await backups.begin_restore_check(uuid.UUID(str(row["id"])), BackupKind.MANUAL, None)
    result = await backups.run_restore_check(check_id, integration_settings)
    assert result.status.value == "failed"
    assert result.error is not None and "pg_restore" in result.error
    assert "PGPASSWORD" not in result.error


async def test_one_backup_at_a_time(owner: SignedIn) -> None:
    held = await backups.begin_backup(BackupKind.MANUAL, None)
    try:
        refused = await owner.client.post(BACKUPS, headers=owner.headers())
        assert refused.status_code == 409
        assert refused.json()["code"] == "LIFECYCLE_JOB_RUNNING"
        assert (await owner.client.get(BACKUPS)).json()["backup_running"] is True
    finally:
        async with session_scope() as db:
            await db.execute(text("DELETE FROM backups WHERE id = :id"), {"id": held})


async def test_a_failed_backup_has_no_file_to_download(owner: SignedIn) -> None:
    failed = await backups.begin_backup(BackupKind.MANUAL, None)
    await backups._finish_backup(failed, error="itest: made to fail")
    download = await owner.client.get(f"{BACKUPS}/{failed}/download")
    assert download.status_code == 409
    assert download.json()["code"] == "BACKUP_UNAVAILABLE"
    verify = await owner.client.post(f"{BACKUPS}/{failed}/verify-restore", headers=owner.headers())
    assert verify.status_code == 409


async def test_the_nightly_backup_runs_once_a_night(integration_settings: Settings) -> None:
    # "Tonight" starts at the top of the current hour, so the shared database's own
    # scheduled backups (all earlier) do not count -- and are not touched.
    hour = dt.datetime.now(zoneinfo.ZoneInfo(integration_settings.reporting_tz)).hour
    settings = integration_settings.model_copy(update={"backup_hour": hour})
    async with session_scope() as db:
        this_hour = (
            await db.execute(
                text(
                    "SELECT count(*) FROM backups WHERE kind = 'scheduled' "
                    "AND started_at >= date_trunc('hour', now())"
                )
            )
        ).scalar_one()
    if this_hour:
        pytest.skip("a scheduled backup already ran this hour in this database")
    assert await backups.run_scheduled_once(settings) == "backup"
    assert await backups.run_scheduled_once(settings) != "backup"
    # Rotation may prune tonight's backup in favour of a newer one the same day; it still
    # ran, so the night is done (ERRORS E70: it used to run again every 15 minutes).
    async with session_scope() as db:
        await db.execute(
            text(
                "UPDATE backups SET status = 'pruned' WHERE kind = 'scheduled' "
                "AND started_at >= date_trunc('hour', now())"
            )
        )
    assert await backups.run_scheduled_once(settings) != "backup"


async def test_backup_writes_are_the_owners(
    new_client: ClientFactory, integration_settings: Settings, totp_clock: TotpClock
) -> None:
    invited = await helpers.invite(integration_settings, role=AdminRole.ANALYST)
    analyst = await helpers.enroll(await new_client(), invited, totp_clock)
    assert (await analyst.client.get(BACKUPS)).status_code == 200
    some = uuid.uuid4()
    for method, path in (
        ("POST", BACKUPS),
        ("GET", f"{BACKUPS}/{some}/download"),
        ("POST", f"{BACKUPS}/{some}/verify-restore"),
    ):
        response = await analyst.client.request(method, path, headers=analyst.headers())
        assert response.status_code == 403, (method, path, response.text)
