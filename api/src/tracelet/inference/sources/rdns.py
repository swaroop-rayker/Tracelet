"""S6 -- the operator's own PTR record, matched against a city-code lexicon (F4.AC5).

Spike A measured this at 2.0 % coverage for Indian ISP address space (RISKS R3), so it
is rare -- and when it fires, it is the strongest free signal there is, because it comes
from the operator's own naming rather than a registry record.

Three things here are easy to get wrong:

* **A PTR often contains the address itself** (``49.204.83.225.actcorp.in``). Matching
  runs on the real name in memory; only ``mask_ptr``'s output is ever stored, in
  ``visits.rdns_ptr`` or in candidate evidence (CLAUDE.md invariant 4).
* **"No PTR record" and "the resolver cannot answer PTR" look identical** to
  ``gethostbyaddr``. Docker Desktop's resolver does the second for every address
  (docs/ERRORS.md E27). A canary lookup of a known address decides which one a failure
  means; while it fails, S6 reports itself unavailable instead of reporting "no record".
* **The lookup blocks.** It runs on a small dedicated thread pool so a slow resolver can
  never starve the event loop's default executor.
"""

from __future__ import annotations

import asyncio
import json
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from importlib import resources
from typing import Any, Final

from tracelet.inference.sources import IPAddress, SourceInput, SourceUnavailable
from tracelet.inference.types import Candidate, GeoLevel, InferenceSource

CANARY: Final = ("8.8.8.8", "dns.google")
CANARY_TTL_S: Final = 600.0

_EXECUTOR: Final = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rdns")

# h_errno values from <netdb.h>, as surfaced by socket.herror.
_HOST_NOT_FOUND: Final = 1
_NO_DATA: Final = 4


# ---------------------------------------------------------------------------
# Masking -- what may be stored
# ---------------------------------------------------------------------------

_HEX_GROUPS = re.compile(r"(?i)(?<![0-9a-z])(?:[0-9a-f]{1,4}[.:-]){2,}[0-9a-f]{1,4}(?![0-9a-z])")
_DIGITS = re.compile(r"\d+")


def mask_ptr(ptr: str) -> str:
    """The PTR with every run that could encode an address replaced by ``#``.

    Over-masks on purpose: a hex-looking word lost from a hostname costs nothing, an
    address kept in one violates invariant 4. City codes are letters, so the part of the
    name that carries location survives.
    """
    return _DIGITS.sub("#", _HEX_GROUPS.sub("#", ptr.lower()))


# ---------------------------------------------------------------------------
# The lexicon
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LexiconEntry:
    pattern: str
    code: str
    city: str | None
    admin1: str | None
    country_code: str
    lat: float | None
    lng: float | None
    confidence: float
    lexicon_version: int
    isp_hint: str | None = None


def token_pattern(code: str) -> str:
    """A code bounded by anything that is not a letter: ``blr`` matches ``x.blr.isp.in``
    and ``blr01``, not ``blrx``."""
    return rf"(?:^|[^a-z]){re.escape(code)}(?:[^a-z]|$)"


def seed_entries() -> tuple[int, str, list[LexiconEntry]]:
    """The versioned seed file: (lexicon_version, domain gate, entries)."""
    raw: dict[str, Any] = json.loads(
        resources.files("tracelet.inference.data").joinpath("rdns_lexicon.json").read_text()
    )
    version = int(raw["lexicon_version"])
    entries: list[LexiconEntry] = []
    for e in raw["entries"]:
        for code in e["codes"]:
            entries.append(
                LexiconEntry(
                    pattern=e.get("pattern") or token_pattern(code),
                    code=code,
                    city=e["city"],
                    admin1=e["admin1"],
                    country_code=e.get("country_code", "IN"),
                    lat=e["lat"],
                    lng=e["lng"],
                    confidence=float(e["confidence"]),
                    lexicon_version=version,
                    isp_hint=e.get("isp_hint"),
                )
            )
    return version, str(raw["in_domain_gate"]), entries


class Lexicon:
    def __init__(self, entries: list[LexiconEntry], in_domain_gate: str) -> None:
        self._gate = re.compile(in_domain_gate)
        # Most specific first: an ISP-specific pattern, then a city over a state, then
        # the more confident entry, then the longer code.
        ordered = sorted(
            entries,
            key=lambda e: (e.isp_hint is None, e.city is None, -e.confidence, -len(e.code)),
        )
        self._compiled = [(re.compile(e.pattern), e) for e in ordered]

    def __len__(self) -> int:
        return len(self._compiled)

    def match(self, ptr: str) -> LexiconEntry | None:
        name = ptr.lower().rstrip(".")
        in_domain = bool(self._gate.search(name))
        for pattern, entry in self._compiled:
            if entry.country_code == "IN" and not in_domain:
                continue
            if entry.isp_hint and not name.endswith(entry.isp_hint):
                continue
            if pattern.search(name):
                return entry
        return None

    def match_name(self, words: set[str]) -> LexiconEntry | None:
        """S7's use: a whole place *name* among ``words`` -- ``pune`` or ``bengaluru``,
        never a short code such as ``del`` that is also an ordinary word fragment."""
        for _, entry in self._compiled:
            if entry.isp_hint or entry.code not in words:
                continue
            is_name = entry.city is not None and entry.code == entry.city.casefold()
            if is_name or len(entry.code) >= 5:
                return entry
        return None


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Canary:
    healthy: bool | None = None
    checked_at: float = 0.0


_canary = _Canary()


def _gethostbyaddr(ip: str) -> str | None:
    """``None`` means the name server said there is no record; anything else raises."""
    try:
        return socket.gethostbyaddr(ip)[0]
    except socket.herror as exc:
        if exc.errno in (_HOST_NOT_FOUND, _NO_DATA):
            return None
        raise


async def _lookup(ip: str, timeout_s: float) -> str | None:
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(_EXECUTOR, _gethostbyaddr, ip), timeout=timeout_s
    )


async def resolver_answers_ptr(timeout_s: float = 2.0) -> bool:
    """E27's guard: can this resolver answer a PTR query at all? Cached for ten minutes."""
    now = time.monotonic()
    if _canary.healthy is not None and now - _canary.checked_at < CANARY_TTL_S:
        return _canary.healthy
    try:
        name = await _lookup(CANARY[0], timeout_s)
        healthy = name is not None and name.rstrip(".").endswith(CANARY[1])
    except (OSError, TimeoutError):
        healthy = False
    _canary.healthy, _canary.checked_at = healthy, now
    return healthy


async def resolve(ip: IPAddress, timeout_s: float) -> str | None:
    """The PTR name, or ``None`` for "no record". Raises ``SourceUnavailable`` when the
    resolver could not answer -- never reports that as "no record"."""
    if not await resolver_answers_ptr():
        raise SourceUnavailable("resolver_cannot_answer_ptr")
    try:
        return await _lookup(str(ip), timeout_s)
    except TimeoutError as exc:
        raise SourceUnavailable("timeout") from exc
    except OSError as exc:
        raise SourceUnavailable(f"resolver_error:{type(exc).__name__}") from exc


# ---------------------------------------------------------------------------
# The producer
# ---------------------------------------------------------------------------


def produce(ptr: str | None, lexicon: Lexicon) -> list[Candidate]:
    """Match an already-resolved PTR. Resolution is the engine's context phase, because
    the masked PTR is stored on the visit whether or not it matches."""
    if not ptr:
        return []
    entry = lexicon.match(ptr)
    if entry is None:
        return []
    return [
        Candidate(
            source=InferenceSource.RDNS,
            level=GeoLevel.CITY if entry.city else GeoLevel.ADMIN1,
            country_code=entry.country_code,
            admin1=entry.admin1,
            city=entry.city,
            lat=entry.lat,
            lng=entry.lng,
            raw_confidence=entry.confidence,
            evidence={
                "ptr": mask_ptr(ptr),
                "matched_code": entry.code,
                "lexicon_version": entry.lexicon_version,
            },
        )
    ]


def reset_canary_for_tests() -> None:
    _canary.healthy, _canary.checked_at = None, 0.0


__all__ = [
    "Lexicon",
    "LexiconEntry",
    "SourceInput",
    "mask_ptr",
    "produce",
    "resolve",
    "resolver_answers_ptr",
    "seed_entries",
]
