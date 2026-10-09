"""The accuracy fixture: labelled cases, anonymised, for ``tracelet accuracy check``.

An **allow-list**, not a deny-list (ADR-0024 decision 4): a field reaches the file only by
being named below. Kept: the truth's place names, connection kind, VPN flag and network
family; each candidate's source, level, place names and raw confidence; the network flags
and modal fields; the timezone's countries; consented or not; the path. Never kept: any id,
timestamp, address, prefix, HMAC, ASN number, organisation, PTR, coordinate or note.

The repository is public, so the real fixture lives in the ``ACCURACY_FIXTURE`` Actions
secret; only the synthetic one is committed. Cases are written in a content-derived order,
so the file says nothing about when visits happened.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from tracelet.accuracy.replay import Case, Truth, strip_candidates, without_identity
from tracelet.inference.config import InferenceConfig
from tracelet.inference.types import AsnInfo, Candidate, GeoLevel, InferenceSource

FORMAT: Final = "tracelet.accuracy-fixture"
VERSION: Final = 1


class FixtureError(ValueError):
    """The file is not a fixture this code can read."""


@dataclass(frozen=True, slots=True)
class Fixture:
    cases: tuple[Case, ...]
    # The settings the cases are scored under: the box's active version at export, or
    # None for the code default (the synthetic fixture).
    settings_version: int | None
    config: InferenceConfig | None


def _candidate(c: Candidate) -> dict[str, Any]:
    return {
        "source": c.source.value,
        "level": c.level.value,
        "country_code": c.country_code,
        "admin1": c.admin1,
        "admin2": c.admin2,
        "city": c.city,
        "raw_confidence": round(c.raw_confidence, 3),
    }


def _case(case: Case) -> dict[str, Any]:
    asn = without_identity(case.asn)
    return {
        "truth": {
            "country_code": case.truth.country_code,
            "admin1": case.truth.admin1,
            "admin2": case.truth.admin2,
            "city": case.truth.city,
        },
        "consented": case.consented,
        "path": case.path,
        "network": case.network,
        "connection_kind": case.connection_kind,
        "vpn_used": case.vpn_used,
        "asn": {
            "is_mobile": asn.is_mobile,
            "is_hosting": asn.is_hosting,
            "is_cgnat": asn.is_cgnat,
            "is_tor": asn.is_tor,
            "modal_city": asn.modal_city,
            "modal_admin1": asn.modal_admin1,
            "modal_share": asn.modal_share,
        },
        "tz_countries": sorted(case.tz_countries) if case.tz_countries is not None else None,
        "candidates": [_candidate(c) for c in strip_candidates(case.candidates)],
    }


def dumps(
    cases: Sequence[Case], *, settings_version: int | None, config: InferenceConfig | None
) -> str:
    body = [_case(c) for c in cases]
    # Content order, not visit order: nothing in the file hints at when a visit happened.
    body.sort(key=lambda c: hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest())
    return json.dumps(
        {
            "format": FORMAT,
            "version": VERSION,
            "settings_version": settings_version,
            "settings": config.model_dump(mode="json") if config is not None else None,
            "cases": body,
        },
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )


def write(path: Path, text: str) -> None:
    """Gzipped when the name ends in ``.gz``, which is what the secret holds."""
    data = text.encode("utf-8")
    path.write_bytes(gzip.compress(data, mtime=0) if path.suffix == ".gz" else data)


def _str(raw: object, where: str) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        msg = f"{where} must be a string or null"
        raise FixtureError(msg)
    return raw


def _parse_case(raw: Any, index: int) -> Case:
    where = f"cases[{index}]"
    try:
        truth = raw["truth"]
        country = _str(truth["country_code"], f"{where}.truth.country_code")
        if country is None:
            msg = f"{where}.truth.country_code is required"
            raise FixtureError(msg)
        asn = raw["asn"]
        share = asn.get("modal_share")
        tz = raw.get("tz_countries")
        path = raw.get("path", "direct")
        if path not in ("cloudflare", "direct"):
            msg = f"{where}.path must be cloudflare or direct"
            raise FixtureError(msg)
        return Case(
            truth=Truth(
                country_code=country,
                admin1=_str(truth.get("admin1"), f"{where}.truth.admin1"),
                admin2=_str(truth.get("admin2"), f"{where}.truth.admin2"),
                city=_str(truth.get("city"), f"{where}.truth.city"),
            ),
            consented=bool(raw["consented"]),
            path=path,
            asn=AsnInfo(
                is_mobile=bool(asn.get("is_mobile", False)),
                is_hosting=bool(asn.get("is_hosting", False)),
                is_cgnat=bool(asn.get("is_cgnat", False)),
                is_tor=bool(asn.get("is_tor", False)),
                modal_city=_str(asn.get("modal_city"), f"{where}.asn.modal_city"),
                modal_admin1=_str(asn.get("modal_admin1"), f"{where}.asn.modal_admin1"),
                modal_share=float(share) if share is not None else None,
            ),
            tz_countries=frozenset(str(c) for c in tz) if tz is not None else None,
            candidates=tuple(
                Candidate(
                    source=InferenceSource(c["source"]),
                    level=GeoLevel(c["level"]),
                    country_code=_str(c.get("country_code"), f"{where}.candidate"),
                    admin1=_str(c.get("admin1"), f"{where}.candidate"),
                    admin2=_str(c.get("admin2"), f"{where}.candidate"),
                    city=_str(c.get("city"), f"{where}.candidate"),
                    raw_confidence=float(c["raw_confidence"]),
                )
                for c in raw["candidates"]
            ),
            network=_str(raw.get("network"), f"{where}.network"),
            connection_kind=_str(raw.get("connection_kind"), f"{where}.connection_kind"),
            vpn_used=raw.get("vpn_used"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, FixtureError):
            raise
        msg = f"{where} is malformed: {type(exc).__name__}: {exc}"
        raise FixtureError(msg) from exc


def loads(text: str) -> Fixture:
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        msg = f"not JSON: {exc}"
        raise FixtureError(msg) from exc
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        msg = f"not a {FORMAT} file"
        raise FixtureError(msg)
    if doc.get("version") != VERSION:
        msg = f"fixture version {doc.get('version')!r}; this code reads version {VERSION}"
        raise FixtureError(msg)
    settings = doc.get("settings")
    version = doc.get("settings_version")
    return Fixture(
        cases=tuple(_parse_case(c, i) for i, c in enumerate(doc.get("cases") or [])),
        settings_version=int(version) if version is not None else None,
        config=InferenceConfig.from_stored(settings) if settings is not None else None,
    )


def read(path: Path) -> Fixture:
    data = path.read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return loads(data.decode("utf-8"))
