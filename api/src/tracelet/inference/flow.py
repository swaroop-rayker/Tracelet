"""The inference flow diagram's model (F10.AC8): the levels, their order, what is on, and --
for a chosen visit -- what fired and where it was suppressed.

The diagram is the brief's "a diagram flow model that displays different inference /
derivation system levels". It is built from the **active settings** (which sources are
enabled, the thresholds) and, for a sample visit, from what inference recorded for it:
its candidates (accepted, or suppressed and why), the sources that did not answer (with
their reason, in ``signals``), and each level's strict and advisory outcome. Nothing is
recomputed: the overlay shows what actually happened to that visit, under the settings
version it was inferred with.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Final

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tracelet.capture.models import Visit
from tracelet.inference.config import InferenceConfig
from tracelet.inference.models import VisitCandidate
from tracelet.inference.types import FAMILY, LEVELS, InferenceSource

CODES: Final = {
    InferenceSource.GPS: "S1",
    InferenceSource.GEOLITE2: "S2",
    InferenceSource.IP2LOCATION: "S3",
    InferenceSource.IPINFO: "S4",
    InferenceSource.DBIP: "S5",
    InferenceSource.RDNS: "S6",
    InferenceSource.ASN_ORG: "S7",
    InferenceSource.CF_COLO: "S8",
    InferenceSource.EXTERNAL_API: "S9",
    InferenceSource.TIMEZONE: "S11",
}
LABELS: Final = {
    InferenceSource.GPS: "Device location (consented)",
    InferenceSource.GEOLITE2: "MaxMind GeoLite2",
    InferenceSource.IP2LOCATION: "IP2Location LITE",
    InferenceSource.IPINFO: "IPinfo Lite",
    InferenceSource.DBIP: "DB-IP Lite",
    InferenceSource.RDNS: "City code in reverse DNS",
    InferenceSource.ASN_ORG: "Place in the network's name",
    InferenceSource.CF_COLO: "Cloudflare edge",
    InferenceSource.EXTERNAL_API: "ipwho.is",
    InferenceSource.TIMEZONE: "Browser time zone (rejects, never proposes)",
}
FAMILY_LABELS: Final = {
    "client": "The visitor's device",
    "database": "Registry databases",
    "network": "The operator's own naming",
    "edge": "The network edge",
    "context": "Context checks",
}
RULES: Final = (
    (
        "registry_artifact",
        "Registry artifact",
        "A database put most of this network's addresses on one point: its registration address, not the visitor (the B1 fix).",
    ),
    (
        "mobile_asn",
        "Mobile network",
        "Mobile carriers route a whole region through few gateways; their city is not believed.",
    ),
    (
        "hosting_asn",
        "Hosting network",
        "A data-centre address says where the server is, not the person.",
    ),
    (
        "tz_mismatch",
        "Time-zone mismatch",
        "A country the browser's time zone contradicts loses most of its weight.",
    ),
    ("outvoted", "Outvoted", "Other sources agreed on something else."),
    (
        "below_threshold",
        "Below threshold",
        "Not confident enough for the strict field; still the advisory best guess.",
    ),
)


@dataclass(frozen=True, slots=True)
class SourceNode:
    source: str
    code: str
    label: str
    family: str
    order: int
    enabled: bool
    timeout_ms: int
    priors: dict[str, float]


@dataclass(frozen=True, slots=True)
class Fired:
    level: str
    value: str
    accepted: bool
    suppressed_reason: str | None
    effective_weight: float


@dataclass(frozen=True, slots=True)
class SourceOutcome:
    source: str
    # fired: a candidate was accepted; suppressed: every candidate was suppressed; disabled,
    # unavailable (could not be asked) or empty (asked, nothing to say), as inference
    # recorded it; silent: nothing was recorded (a visit inferred before M3's trail).
    status: str
    reason: str | None
    candidates: list[Fired] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class LevelOutcome:
    level: str
    strict: str | None
    advisory: str | None
    confidence: float | None
    abstain_reason: str | None


@dataclass(frozen=True, slots=True)
class Sample:
    visit_id: str
    inference_version: str | None
    classification: str
    geo_source_primary: str | None
    geofence_state: str | None
    sources: list[SourceOutcome]
    levels: list[LevelOutcome]
    rules_fired: list[str]
    alert: dict[str, Any] | None


def sources(config: InferenceConfig) -> list[SourceNode]:
    nodes: list[SourceNode] = []
    for order, source in enumerate(CODES, start=1):
        settings = config.sources.get(source)
        nodes.append(
            SourceNode(
                source=source.value,
                code=CODES[source],
                label=LABELS[source],
                family=FAMILY[source].value,
                order=order,
                enabled=bool(settings and settings.enabled),
                timeout_ms=settings.timeout_ms if settings else 0,
                priors=(
                    {level.value: settings.priors.at(level) for level in LEVELS} if settings else {}
                ),
            )
        )
    return nodes


def _value(c: VisitCandidate) -> str:
    parts = [p for p in (c.city, c.admin2, c.admin1, c.country_code) if p]
    return ", ".join(parts) if parts else "--"


async def sample(db: AsyncSession, visit_id: uuid.UUID) -> Sample | None:
    visit = await db.get(Visit, visit_id)
    if visit is None:
        return None
    candidates = list(
        (
            await db.execute(
                select(VisitCandidate)
                .where(VisitCandidate.visit_id == visit_id)
                .order_by(VisitCandidate.id)
            )
        ).scalars()
    )
    absent: dict[str, tuple[str, str | None]] = {}
    for signal in visit.signals or []:
        if signal.get("rule_id") == "inference.source_absent":
            detail = signal.get("detail") or {}
            reason = detail.get("reason")
            absent[str(detail.get("source"))] = (
                str(detail.get("status") or "unavailable"),
                str(reason) if reason else None,
            )
    by_source: dict[str, list[VisitCandidate]] = {}
    for c in candidates:
        by_source.setdefault(c.source.value, []).append(c)
    outcomes: list[SourceOutcome] = []
    for source in CODES:
        mine = by_source.get(source.value, [])
        if mine:
            accepted = any(c.accepted for c in mine)
            reasons = sorted({c.suppressed_reason for c in mine if c.suppressed_reason})
            outcomes.append(
                SourceOutcome(
                    source=source.value,
                    status="fired" if accepted else "suppressed",
                    reason=", ".join(reasons) or None,
                    candidates=[
                        Fired(
                            level=c.level.value,
                            value=_value(c),
                            accepted=c.accepted,
                            suppressed_reason=c.suppressed_reason,
                            effective_weight=float(c.effective_weight),
                        )
                        for c in mine
                    ],
                )
            )
        elif source.value in absent:
            status, reason = absent[source.value]
            outcomes.append(SourceOutcome(source=source.value, status=status, reason=reason))
        else:
            outcomes.append(SourceOutcome(source=source.value, status="silent", reason=None))
    abstain = visit.abstain_reason or {}
    levels = [
        LevelOutcome(
            level=level.value,
            strict=getattr(
                visit, f"strict_{'country_code' if level.value == 'country' else level.value}"
            ),
            advisory=getattr(
                visit, f"advisory_{'country_code' if level.value == 'country' else level.value}"
            ),
            confidence=(
                float(conf)
                if (conf := getattr(visit, f"confidence_{level.value}")) is not None
                else None
            ),
            abstain_reason=abstain.get(level.value),
        )
        for level in LEVELS
    ]
    rules = sorted({c.suppressed_reason for c in candidates if c.suppressed_reason})
    alert_row = (
        await db.execute(
            text(
                "SELECT priority, status, dedup_key FROM outbox "
                "WHERE payload->>'visit_id' = :v ORDER BY id LIMIT 1"
            ),
            {"v": str(visit_id)},
        )
    ).one_or_none()
    return Sample(
        visit_id=str(visit_id),
        inference_version=visit.inference_version,
        classification=visit.classification.value,
        geo_source_primary=visit.geo_source_primary.value if visit.geo_source_primary else None,
        geofence_state=visit.geofence_state.value if visit.geofence_state else None,
        sources=outcomes,
        levels=levels,
        rules_fired=rules,
        alert=(
            {
                "priority": str(alert_row[0]),
                "status": str(alert_row[1]),
                "upgrade": str(alert_row[2] or "").endswith((":upgrade", ":confirmed")),
            }
            if alert_row is not None
            else None
        ),
    )
