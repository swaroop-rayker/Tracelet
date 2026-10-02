"""Candidate producers, one module per source (F4.AC5).

Every producer has the same shape: an async function from ``SourceInput`` to a list of
candidates. It may return an empty list (it looked and found nothing) or raise
``SourceUnavailable`` (it could not look). The engine times each one, applies its
timeout, and records the outcome either way -- a source that produced nothing still
appears in the derivation trail (F4.AC11).
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

from tracelet.inference.types import AsnInfo

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class SourceUnavailable(Exception):  # noqa: N818 - a state, not an error the caller fixes
    """The source could not answer at all -- as distinct from answering "nothing"."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Gps:
    lat: float
    lng: float
    accuracy_m: float | None


@dataclass(frozen=True, slots=True)
class SourceInput:
    """Everything a producer may read about one visit. Never persisted as a whole.

    ``ip`` is the decrypted address, held in memory for the length of one inference
    (ADR-0007 keeps the ciphertext precisely so inference can run and re-run). It is
    never logged and never written anywhere (CLAUDE.md invariant 4).
    """

    ip: IPAddress | None
    gps: Gps | None = None
    tz_iana: str | None = None
    cf_colo: str | None = None
    asn: AsnInfo = field(default_factory=AsnInfo)
