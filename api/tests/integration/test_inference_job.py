"""The inference job against the real database (ADR-0015, F4.AC6, F4.AC11, F4.AC18).

The resolver is replaced in these tests: the suite must not depend on what the CI
runner's DNS happens to say, and in Docker Desktop it would say nothing at all (E27).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text

from tests.integration import capture_helpers as ch
from tracelet.capture.models import Visit, VisitStage
from tracelet.config import Settings
from tracelet.db.engine import session_scope
from tracelet.inference import consensus, engine
from tracelet.inference.models import VisitCandidate
from tracelet.inference.sources import rdns

pytestmark = pytest.mark.integration

MTNL_MUMBAI_PTR = "triband-mum-49.207.12.34.mtnl.net.in"

ENRICHMENT: dict[str, Any] = {
    "locale": {"tzIana": "Asia/Kolkata", "tzOffsetMin": 330, "languages": ["en-IN"]},
    "geolocation": {"state": "prompt"},
}


@pytest.fixture
def resolver(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str | None]]:
    """A resolver that answers PTR, with a per-test answer for the visitor's address."""
    answers: dict[str, str | None] = {ch.VISITOR_IP: MTNL_MUMBAI_PTR}

    async def lookup(ip: str, timeout_s: float) -> str | None:
        del timeout_s
        if ip == rdns.CANARY[0]:
            return rdns.CANARY[1]
        return answers.get(ip)

    rdns.reset_canary_for_tests()
    monkeypatch.setattr(rdns, "_lookup", lookup)
    yield answers
    rdns.reset_canary_for_tests()


async def _finalised_visit(client: AsyncClient, *, colo: str | None = "BOM") -> uuid.UUID:
    link = await ch.create_link()
    page = await ch.visit(client, link.slug)
    response = await client.post(
        f"/api/v1/s/{ch.nonce_from(page)}",
        json=ENRICHMENT,
        headers={"X-Tracelet-Peer-IP": ch.VISITOR_IP},
    )
    assert response.status_code == 204
    visit = await ch.latest_visit(link.id)
    if colo is not None:
        # Only ever set from a verified Cloudflare peer (F13.AC6); written directly here
        # because the test client is not one.
        async with session_scope() as db:
            await db.execute(
                text("UPDATE visits SET cf_colo = :c WHERE id = :id"), {"c": colo, "id": visit.id}
            )
    return visit.id


async def _candidates(visit_id: uuid.UUID) -> list[VisitCandidate]:
    async with session_scope() as db:
        return list(
            (
                await db.execute(
                    select(VisitCandidate)
                    .where(VisitCandidate.visit_id == visit_id)
                    .order_by(VisitCandidate.id)
                )
            ).scalars()
        )


def _absent(visit: Visit) -> dict[str, dict[str, Any]]:
    return {
        s["detail"]["source"]: s["detail"]
        for s in visit.signals
        if s["rule_id"] == "inference.source_absent"
    }


# ---------------------------------------------------------------------------
# The ordinary path
# ---------------------------------------------------------------------------


async def test_a_finalised_visit_is_located_with_its_whole_derivation(
    db_client: AsyncClient, integration_settings: Settings, resolver: dict[str, str | None]
) -> None:
    del resolver
    visit_id = await _finalised_visit(db_client)

    assert await engine.run_once(integration_settings, only=[visit_id]) == 1

    visit = await ch.get_visit(visit_id)
    assert visit.inferred_at is not None
    assert visit.inference_version is not None and visit.inference_version.startswith("m3.")
    assert visit.strict_country_code == "IN"
    assert visit.strict_admin1 == "Maharashtra"
    assert visit.advisory_city == "Mumbai"
    # Two network sources agree, but neither is a database or GPS; city stays advisory.
    assert visit.strict_city is None
    assert visit.abstain_reason["city"] == "below_threshold"

    by_source = {c.source.value: c for c in await _candidates(visit_id)}
    assert set(by_source) == {"rdns", "cf_colo", "timezone"}
    assert by_source["rdns"].evidence["matched_code"] == "mum"
    assert all(c.accepted for c in by_source.values())

    # F4.AC11: a source that produced nothing still appears, with why.
    absent = _absent(visit)
    assert absent["geolite2"]["reason"] == "database_not_installed"
    assert absent["latency"]["status"] == "disabled"
    assert absent["gps"]["status"] == "empty"


async def test_the_stored_ptr_carries_no_address(
    db_client: AsyncClient, integration_settings: Settings, resolver: dict[str, str | None]
) -> None:
    """CLAUDE.md invariant 4. The PTR embeds the visitor's address; the row must not."""
    del resolver
    visit_id = await _finalised_visit(db_client)
    await engine.run_once(integration_settings, only=[visit_id])

    visit = await ch.get_visit(visit_id)
    assert visit.rdns_ptr == "triband-mum-#.mtnl.net.in"
    assert ch.VISITOR_IP not in await ch.row_as_text(visit_id)
    async with session_scope() as db:
        evidence = (
            await db.execute(
                text(
                    "SELECT string_agg(evidence::text, ' ') FROM visit_candidates WHERE visit_id = :v"
                ),
                {"v": visit_id},
            )
        ).scalar_one()
    assert ch.VISITOR_IP not in str(evidence)
    assert "49.207" not in str(evidence)


async def test_a_visit_is_inferred_exactly_once(
    db_client: AsyncClient, integration_settings: Settings, resolver: dict[str, str | None]
) -> None:
    del resolver
    visit_id = await _finalised_visit(db_client)
    await engine.run_once(integration_settings, only=[visit_id])
    rows = len(await _candidates(visit_id))

    assert await engine.run_once(integration_settings, only=[visit_id]) == 0
    assert len(await _candidates(visit_id)) == rows


async def test_a_rate_limited_visit_never_enters_the_queue(
    integration_settings: Settings, resolver: dict[str, str | None]
) -> None:
    """DATA_MODEL 5.3 invariant 8: shed visits carry no inference."""
    del resolver
    link = await ch.create_link()
    async with session_scope() as db:
        visit_id = (
            await db.execute(
                text(
                    "INSERT INTO visits (id, link_id, stage, finalized_at, classifier_version) "
                    "VALUES (gen_random_uuid(), :l, 'rate_limited', now(), 'test') RETURNING id"
                ),
                {"l": link.id},
            )
        ).scalar_one()

    assert await engine.run_once(integration_settings, only=[visit_id]) == 0
    assert (await ch.get_visit(visit_id)).inferred_at is None


async def test_an_unfinalised_visit_is_not_inferred(
    db_client: AsyncClient, integration_settings: Settings, resolver: dict[str, str | None]
) -> None:
    del resolver
    link = await ch.create_link()
    await ch.visit(db_client, link.slug)
    pending = await ch.latest_visit(link.id)
    assert pending.stage is VisitStage.SERVER

    assert await engine.run_once(integration_settings, only=[pending.id]) == 0


# ---------------------------------------------------------------------------
# Failure never reaches the visitor, and never wedges the queue
# ---------------------------------------------------------------------------


async def test_a_resolver_that_cannot_answer_ptr_is_not_reported_as_no_record(
    db_client: AsyncClient, integration_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ERRORS.md E27: every lookup failing reads as "no PTR" unless a canary says why."""

    async def blind(ip: str, timeout_s: float) -> str | None:
        del ip, timeout_s
        return None  # what Docker Desktop's resolver answers for everything

    rdns.reset_canary_for_tests()
    monkeypatch.setattr(rdns, "_lookup", blind)
    visit_id = await _finalised_visit(db_client)

    await engine.run_once(integration_settings, only=[visit_id])

    absent = _absent(await ch.get_visit(visit_id))
    assert absent["rdns"] == {
        "source": "rdns",
        "status": "unavailable",
        "latency_ms": absent["rdns"]["latency_ms"],
        "reason": "resolver_cannot_answer_ptr",
    }
    rdns.reset_canary_for_tests()


async def test_with_every_source_silent_the_country_abstains_with_a_reason(
    db_client: AsyncClient, integration_settings: Settings, resolver: dict[str, str | None]
) -> None:
    """F4.AC18."""
    resolver[ch.VISITOR_IP] = None
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


async def test_an_engine_failure_is_written_as_an_abstention(
    db_client: AsyncClient,
    integration_settings: Settings,
    resolver: dict[str, str | None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del resolver

    def broken(*_args: object, **_kwargs: object) -> consensus.Decision:
        msg = "consensus exploded"
        raise RuntimeError(msg)

    monkeypatch.setattr(consensus, "decide", broken)
    visit_id = await _finalised_visit(db_client)

    assert await engine.run_once(integration_settings, only=[visit_id]) == 1

    visit = await ch.get_visit(visit_id)
    assert visit.inferred_at is not None, "a poisoned visit must leave the queue"
    assert visit.strict_country_code is None
    assert visit.abstain_reason["country"] == "engine_error"


async def test_a_write_the_database_refuses_does_not_wedge_the_queue(
    db_client: AsyncClient,
    integration_settings: Settings,
    resolver: dict[str, str | None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A candidate the CHECK constraints refuse is replaced by an engine-error write, and
    its neighbour in the same batch is unaffected."""
    del resolver
    poisoned = await _finalised_visit(db_client)
    healthy = await _finalised_visit(db_client)
    original = engine._candidate_rows

    def refuse_one(result: engine.Inferred) -> list[dict[str, Any]]:
        rows = original(result)
        if result.visit_id == poisoned and rows:
            rows[0]["raw_confidence"] = 5  # violates raw_confidence_range
        return rows

    monkeypatch.setattr(engine, "_candidate_rows", refuse_one)

    assert await engine.run_once(integration_settings, only=[poisoned, healthy]) == 2

    bad, good = await ch.get_visit(poisoned), await ch.get_visit(healthy)
    assert bad.inferred_at is not None
    assert bad.abstain_reason["country"] == "write_refused"
    assert await _candidates(poisoned) == []
    assert good.strict_country_code == "IN"
