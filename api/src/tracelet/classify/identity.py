"""Visitor identity: the canonical fingerprint and its three HMACs (ADR-0006, F12.AC6).

| Field | Key | Input | For |
|---|---|---|---|
| ``visitor_id`` | stable pepper | canonical + prefix | new vs returning |
| ``session_fp`` | today's key from the rotating pepper | canonical + prefix | short-window correlation |
| ``fingerprint_id`` | fingerprint pepper | canonical only | proxy collision, impossible travel |

**Stability bucketing.** Continuous values are coarsened before hashing -- DPR to the
nearest quarter, memory to the reported power of two, the language list to its first
two tags -- so a browser minor version or a zoom change does not mint a new visitor.

**Server-only visits** get ``visitor_id`` and ``session_fp`` from the server part alone and
no ``fingerprint_id`` (ADR-0006 amendment, item 2): every Chrome 131 user with ``en-IN``
would share one, and the collision rule would read ordinary people as one device on many
networks.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
from dataclasses import dataclass
from typing import Final

from tracelet.capture.useragent import parse

# Headers whose *presence* characterises a browser family; their sorted names are the
# header-set component (ADR-0006 amendment, item 1). Values that vary per request --
# sec-fetch-site, the referer -- are deliberately not hashed.
CHARACTERISTIC_HEADERS: Final = (
    "accept-language",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
    "sec-fetch-dest",
    "sec-fetch-mode",
    "sec-fetch-user",
    "upgrade-insecure-requests",
    "dnt",
    "sec-gpc",
)
DIGEST_BYTES: Final = 16  # 128 bits, ADR-0006


@dataclass(frozen=True, slots=True)
class IdentityInput:
    user_agent: str | None
    headers: dict[str, str]
    ip_prefix: str | None
    enriched: bool
    screen_w: int | None = None
    screen_h: int | None = None
    dpr: float | None = None
    color_depth: int | None = None
    tz_iana: str | None = None
    languages: tuple[str, ...] = ()
    cpu_cores: int | None = None
    device_memory_gb: float | None = None
    gpu_vendor: str | None = None
    gpu_renderer: str | None = None
    hashes: tuple[bytes | None, ...] = ()  # canvas, audio, font, webgl


@dataclass(frozen=True, slots=True)
class Identity:
    visitor_id: bytes | None
    session_fp: bytes | None
    fingerprint_id: bytes | None
    server_only: bool
    missing_peppers: tuple[str, ...]


def _bucket_dpr(dpr: float | None) -> str:
    return "" if dpr is None else f"{round(dpr * 4) / 4:.2f}"


def server_part(inp: IdentityInput) -> list[str]:
    agent = parse(inp.user_agent)
    language = inp.headers.get("accept-language", "").split(",")[0].strip().lower()
    major = (agent.ua_version or "").split(".")[0]
    present = sorted(h for h in CHARACTERISTIC_HEADERS if h in inp.headers)
    return [
        f"ua={agent.ua_family or '?'}/{major}",
        f"os={inp.headers.get('sec-ch-ua-platform', '').strip(chr(34)) or agent.os_family or '?'}",
        f"lang={language}",
        f"hdrs={','.join(present)}",
    ]


def client_part(inp: IdentityInput) -> list[str]:
    hashes = ",".join(h.hex() if h else "" for h in inp.hashes)
    return [
        f"screen={inp.screen_w or ''}x{inp.screen_h or ''}@{_bucket_dpr(inp.dpr)}/{inp.color_depth or ''}",
        f"tz={inp.tz_iana or ''}",
        f"langs={','.join(t.lower() for t in inp.languages[:2])}",
        f"hw={inp.cpu_cores or ''}/{inp.device_memory_gb or ''}",
        f"gpu={(inp.gpu_vendor or '').lower()}|{(inp.gpu_renderer or '').lower()}",
        f"hashes={hashes}",
    ]


def canonical(inp: IdentityInput) -> bytes:
    parts = server_part(inp)
    if inp.enriched:
        parts += client_part(inp)
    return "\n".join(parts).encode()


def _mac(key: bytes, *parts: bytes) -> bytes:
    return hmac.new(key, b"\x00".join(parts), hashlib.sha256).digest()[:DIGEST_BYTES]


def day_key(rotating_pepper: bytes, day: dt.date) -> bytes:
    """ADR-0006 amendment, item 3: the daily rotation is derived, not operated."""
    return hmac.new(
        rotating_pepper, f"session_fp:{day.isoformat()}".encode(), hashlib.sha256
    ).digest()


def derive(
    inp: IdentityInput,
    *,
    pepper_stable: bytes | None,
    pepper_rotating: bytes | None,
    pepper_fp: bytes | None,
    day: dt.date,
) -> Identity:
    fp = canonical(inp)
    prefix = (inp.ip_prefix or "").encode()
    missing = tuple(
        name
        for name, pepper in (
            ("pepper_stable", pepper_stable),
            ("pepper_rotating", pepper_rotating),
            ("pepper_fp", pepper_fp),
        )
        if not pepper
    )
    return Identity(
        visitor_id=_mac(pepper_stable, b"visitor.v1", fp, prefix) if pepper_stable else None,
        session_fp=(
            _mac(day_key(pepper_rotating, day), b"session.v1", fp, prefix)
            if pepper_rotating
            else None
        ),
        fingerprint_id=(
            _mac(pepper_fp, b"fingerprint.v1", fp) if pepper_fp and inp.enriched else None
        ),
        server_only=not inp.enriched,
        missing_peppers=missing,
    )
