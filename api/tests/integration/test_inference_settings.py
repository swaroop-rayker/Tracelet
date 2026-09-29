"""Versioned inference settings (F4.AC14, API section 10, CLAUDE.md invariant 9).

Settings versions cannot be deleted -- that is the point of them -- so each test puts the
previously active version back rather than cleaning up rows.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from tests.integration import capture_helpers as ch
from tests.integration import helpers
from tests.integration.helpers import ClientFactory, SignedIn, TotpClock
from tracelet.auth.models import AdminRole
from tracelet.cli import inference as inference_cli
from tracelet.config import Settings
from tracelet.db.engine import get_engine, session_scope
from tracelet.inference import engine, store
from tracelet.inference.config import DEFAULT_CONFIG
from tracelet.inference.sources import rdns

pytestmark = pytest.mark.integration

SETTINGS = "/api/v1/health/inference"


@pytest.fixture(autouse=True)
async def _restore_active_version(db_app: object) -> AsyncIterator[None]:
    del db_app
    async with session_scope() as db:
        before = (await store.active_settings(db)).version
    yield
    async with session_scope() as db:
        await store.activate_version(db, before)


def _raised_city_threshold(current: dict[str, Any], value: float) -> dict[str, Any]:
    changed = dict(current)
    changed["thresholds"] = {**current["thresholds"], "city": value}
    return changed


async def test_reading_the_settings_seeds_version_one_from_the_defaults(owner: SignedIn) -> None:
    response = await owner.client.get(SETTINGS)

    assert response.status_code == 200
    body = response.json()
    assert body["inference_version"] == f"{body['engine_revision']}+s{body['active_version']}"
    assert any(v["version"] == 1 for v in body["versions"])
    assert "latency" not in body["settings"]["sources"], "S10 was dropped (SPEC 11 row 12)"


async def test_a_change_is_a_new_version_and_is_audited(owner: SignedIn) -> None:
    before = (await owner.client.get(SETTINGS)).json()

    response = await owner.client.patch(
        SETTINGS,
        json={"settings": _raised_city_threshold(before["settings"], 0.9), "note": "stricter"},
        headers=owner.headers(),
    )

    assert response.status_code == 200
    after = response.json()
    assert after["active_version"] > before["active_version"]
    assert after["settings"]["thresholds"]["city"] == 0.9
    retained = {v["version"] for v in after["versions"]}
    assert before["active_version"] in retained, "the old version is kept"

    rows = await ch.audit_details_for_action("inference.settings_changed")
    assert rows[-1]["to_version"] == after["active_version"]
    assert rows[-1]["changed"] == ["thresholds.city"]


async def test_rollback_reactivates_an_earlier_version_and_is_audited(owner: SignedIn) -> None:
    first = (await owner.client.get(SETTINGS)).json()
    changed = await owner.client.patch(
        SETTINGS,
        json={"settings": _raised_city_threshold(first["settings"], 0.95)},
        headers=owner.headers(),
    )
    assert changed.status_code == 200

    back = await owner.client.post(
        f"{SETTINGS}/rollback/{first['active_version']}", headers=owner.headers()
    )

    assert back.status_code == 200
    assert back.json()["active_version"] == first["active_version"]
    assert back.json()["settings"] == first["settings"]
    rows = await ch.audit_details_for_action("inference.settings_rolled_back")
    assert rows[-1] == {
        "from_version": changed.json()["active_version"],
        "to_version": first["active_version"],
    }


async def test_rolling_back_to_a_version_that_does_not_exist_is_a_404(owner: SignedIn) -> None:
    response = await owner.client.post(f"{SETTINGS}/rollback/999999", headers=owner.headers())
    assert response.status_code == 404


async def test_invalid_settings_are_rejected_whole(owner: SignedIn) -> None:
    current = (await owner.client.get(SETTINGS)).json()
    response = await owner.client.patch(
        SETTINGS,
        json={"settings": _raised_city_threshold(current["settings"], 1.7)},
        headers=owner.headers(),
    )
    assert response.status_code == 422
    assert (await owner.client.get(SETTINGS)).json()["active_version"] == current["active_version"]


async def test_an_analyst_can_read_but_not_change_or_roll_back(
    owner: SignedIn,
    integration_settings: Settings,
    new_client: ClientFactory,
    totp_clock: TotpClock,
) -> None:
    current = (await owner.client.get(SETTINGS)).json()
    invited = await helpers.invite(integration_settings, role=AdminRole.ANALYST)
    analyst = await helpers.enroll(await new_client(), invited, totp_clock)

    assert (await analyst.client.get(SETTINGS)).status_code == 200
    patched = await analyst.client.patch(
        SETTINGS, json={"settings": current["settings"]}, headers=analyst.headers()
    )
    rolled = await analyst.client.post(f"{SETTINGS}/rollback/1", headers=analyst.headers())
    assert patched.status_code == rolled.status_code == 403


async def test_settings_are_not_public(new_client: ClientFactory) -> None:
    assert (await (await new_client()).get(SETTINGS)).status_code == 401


async def test_the_application_role_cannot_rewrite_or_delete_a_version(db_app: object) -> None:
    """Migration 0005: add versions and move the active flag, nothing else."""
    del db_app
    async with get_engine().connect() as conn:
        role = str((await conn.execute(text("SELECT current_user"))).scalar_one())
    if role != "tracelet_app":
        pytest.skip(f"connected as {role!r}; the role split is not exercised")

    for statement in (
        "UPDATE inference_settings SET settings = '{}'::jsonb",
        "DELETE FROM inference_settings",
    ):
        with pytest.raises(Exception, match="permission denied"):
            async with session_scope() as db:
                await db.execute(text(statement))


async def test_a_visit_inferred_after_a_change_carries_the_new_version(
    owner: SignedIn,
    db_client: AsyncClient,
    integration_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F4.AC16: the stamp names the settings that produced the result."""

    async def no_ptr(ip: str, timeout_s: float) -> str | None:
        del timeout_s
        return rdns.CANARY[1] if ip == rdns.CANARY[0] else None

    rdns.reset_canary_for_tests()
    monkeypatch.setattr(rdns, "_lookup", no_ptr)
    current = (await owner.client.get(SETTINGS)).json()
    changed = (
        await owner.client.patch(
            SETTINGS,
            json={"settings": _raised_city_threshold(current["settings"], 0.85)},
            headers=owner.headers(),
        )
    ).json()

    link = await ch.create_link()
    page = await ch.visit(db_client, link.slug)
    await db_client.post(
        f"/api/v1/s/{ch.nonce_from(page)}", json={}, headers={"X-Tracelet-Peer-IP": ch.VISITOR_IP}
    )
    visit_id: uuid.UUID = (await ch.latest_visit(link.id)).id
    await engine.run_once(integration_settings, only=[visit_id])

    assert (await ch.get_visit(visit_id)).inference_version == changed["inference_version"]
    detail = await owner.client.get(f"/api/v1/visits/{visit_id}")
    assert detail.json()["inference_version"] == changed["inference_version"]
    assert detail.json()["inferred_at"] is not None
    rdns.reset_canary_for_tests()


async def test_with_every_source_disabled_the_visit_still_abstains_with_reasons(
    owner: SignedIn,
    db_client: AsyncClient,
    integration_settings: Settings,
) -> None:
    """M3 done-check, F4.AC18: every source switched off through settings -- not merely
    silent -- still yields an inferred visit with country=NULL and a reason for each."""
    current = (await owner.client.get(SETTINGS)).json()
    off = dict(current["settings"])
    off["sources"] = {k: {**v, "enabled": False} for k, v in current["settings"]["sources"].items()}
    saved = await owner.client.patch(
        SETTINGS, json={"settings": off, "note": "all off"}, headers=owner.headers()
    )
    assert saved.status_code == 200

    link = await ch.create_link()
    page = await ch.visit(db_client, link.slug)
    await db_client.post(
        f"/api/v1/s/{ch.nonce_from(page)}", json={}, headers={"X-Tracelet-Peer-IP": ch.VISITOR_IP}
    )
    visit_id = (await ch.latest_visit(link.id)).id
    await engine.run_once(integration_settings, only=[visit_id])

    visit = await ch.get_visit(visit_id)
    assert visit.inferred_at is not None
    assert visit.strict_country_code is None
    assert visit.abstain_reason["country"] == "no_candidates"
    statuses = {
        s["detail"]["source"]: s["detail"]["status"]
        for s in visit.signals
        if s["rule_id"] == "inference.source_absent"
    }
    assert set(statuses.values()) == {"disabled"}
    assert len(statuses) == len(off["sources"])


async def test_the_cli_saves_the_built_in_defaults_as_a_new_audited_version(db_app: object) -> None:
    """`tracelet inference reset-defaults`: a new version, never an edit, and audited."""
    del db_app
    stale = DEFAULT_CONFIG.model_copy(
        update={"thresholds": DEFAULT_CONFIG.thresholds.model_copy(update={"admin1": 0.70})}
    )
    async with session_scope() as db:
        await store.save_new_version(db, stale, note="pre-calibration", actor=None)

    assert await inference_cli._reset_defaults("test reset") == 0

    async with session_scope() as db:
        assert (await store.active_settings(db)).config == DEFAULT_CONFIG
    rows = await ch.audit_details_for_action("inference.settings_changed")
    assert rows[-1]["via"] == "cli_reset_defaults"
    assert await inference_cli._reset_defaults("again") == 0, "already defaults: a no-op"
    assert len(await ch.audit_details_for_action("inference.settings_changed")) == len(rows)
