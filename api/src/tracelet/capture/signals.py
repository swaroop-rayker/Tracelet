"""Server-side signal extraction for one capture request (F3.AC1).

Everything here is derived from the HTTP request alone -- no JavaScript is involved,
which is what makes the visit authoritative (ADR-0004).

**The privacy boundary lives in this module.** ``request_headers`` is the one column
that could smuggle a plaintext IP into the database: ``X-Forwarded-For``,
``CF-Connecting-IP`` and a dozen relatives carry the client address by design, and a
visitor can put an address in any header they like. Three layers, each covering what
the others cannot:

1. **The observed client address is masked everywhere, exactly.** This is what CLAUDE.md
   invariant 4 is about, and matching the one known string has no false positives.
2. **Headers that carry an address by protocol are dropped by name**, whatever they hold.
3. **Any other IP literal is masked** in header values, the referer and UTM values --
   defence in depth against a proxy nobody thought to list.

**Layer 3 is deliberately NOT applied to version-bearing headers.** Chrome's reduced
user agent reports its version as ``131.0.0.0``, which is a syntactically valid IPv4
address. Masking by syntax wiped the browser version from every Chrome visit; syntax
alone cannot tell a version number from an address, so on the headers where versions
live only the exact observed address is masked (docs/ERRORS.md E23).

**Header order is not captured, because it cannot be.** Caddy is written in Go, whose
HTTP server parses headers into a map before any handler runs and writes them back out
sorted. Measured on 2026-09-28: a request sent ``Zzz-Last``, ``Aaa-First``,
``Mmm-Middle`` and the application received them alphabetically. A hash of that order
would be identical for every client with the same header *set* -- a value that looks
exactly like a fingerprint and carries none of the information. So
``visits.header_order_hash`` stays ``NULL`` (docs/RISKS.md R19).
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final

# ---------------------------------------------------------------------------
# Header sanitisation
# ---------------------------------------------------------------------------

# Dropped by name. Every one either carries a client address, carries a credential,
# is hop-by-hop plumbing, or is our own internal header set by Caddy.
_DROP_EXACT: Final[frozenset[str]] = frozenset(
    {
        # IP-bearing
        "x-forwarded-for",
        "x-real-ip",
        "forwarded",
        "true-client-ip",
        "x-client-ip",
        "x-cluster-client-ip",
        "fastly-client-ip",
        "x-original-forwarded-for",
        "x-originating-ip",
        "x-remote-ip",
        "x-remote-addr",
        "client-ip",
        "via",
        # credentials -- the capture surface sets no cookie, so any that arrive
        # belong to someone else's session on a shared domain
        "cookie",
        "authorization",
        "proxy-authorization",
        # hop-by-hop
        "connection",
        "keep-alive",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "proxy-connection",
    }
)

# Dropped by prefix. `cf-*` carries CF-Connecting-IP; the verified values Tracelet
# wants from Cloudflare go to dedicated columns instead. `x-forwarded-*` and
# `x-tracelet-*` are set by our own edge, not by the visitor.
_DROP_PREFIXES: Final[tuple[str, ...]] = ("cf-", "x-forwarded-", "x-tracelet-")

# Bounds, so a hostile client cannot turn one visit into a large row.
MAX_HEADERS: Final = 64
MAX_VALUE_LEN: Final = 512

# Stricter than the log redactor, which keeps private and documentation ranges
# because they are container plumbing in an error message. A stored header value is
# visitor-controlled: there is no reason to keep ANY address literal in one.
_IP_CANDIDATE: Final = re.compile(
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b"  # IPv4
    r"|\b(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}\b"  # IPv6
)
IP_MASK: Final = "[ip]"


def mask_addresses(text: str) -> str:
    """Replace every valid IP literal in ``text``. Times and version strings survive,
    because each candidate is parsed and kept if it is not really an address."""

    def replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            ipaddress.ip_address(raw)
        except ValueError:
            return raw
        return IP_MASK

    return _IP_CANDIDATE.sub(replace, text)


# Headers whose values routinely contain dotted version numbers. Only the exact
# observed address is masked in these -- see the module docstring, layer 3.
_VERSION_BEARING: Final[frozenset[str]] = frozenset(
    {
        "user-agent",
        "sec-ch-ua",
        "sec-ch-ua-full-version",
        "sec-ch-ua-full-version-list",
        "sec-ch-ua-platform-version",
        "sec-ch-ua-model",
    }
)


def mask_client(text: str, client_ip: str | None) -> str:
    """Replace every occurrence of the observed client address.

    Bounded on both sides so ``1.2.3.4`` does not match inside ``11.2.3.45``. A
    trailing full stop is allowed -- only ``.`` followed by a digit continues an
    address -- because a privacy mask should err towards masking, and an address that
    ends a sentence is still an address.
    """
    if not client_ip:
        return text
    pattern = rf"(?<![0-9A-Fa-f.:]){re.escape(client_ip)}(?![0-9A-Fa-f:]|\.[0-9])"
    return re.sub(pattern, IP_MASK, text, flags=re.IGNORECASE)


def _clean(value: str, client_ip: str | None, limit: int = MAX_VALUE_LEN) -> str:
    """Layers 1 and 3: the exact address, then any other literal."""
    return mask_addresses(mask_client(value[:limit], client_ip))


def sanitise_headers(raw: Iterable[tuple[str, str]], *, client_ip: str | None) -> dict[str, str]:
    """The header set as it may be stored: lowercased names, no addresses, bounded.

    Duplicate names are joined with ``", "``, which is what HTTP itself says a repeated
    header means.
    """
    kept: dict[str, str] = {}
    for name, value in raw:
        key = name.lower()
        if key in _DROP_EXACT or key.startswith(_DROP_PREFIXES):
            continue
        if key not in kept and len(kept) >= MAX_HEADERS:
            continue
        if key == "referer":
            # The same cut as visits.referer: its origin, never path or query (row 28).
            origin = clean_referer(value, client_ip=client_ip)
            if origin is None:
                continue
            cleaned = origin
        elif key in _VERSION_BEARING:
            cleaned = mask_client(value[:MAX_VALUE_LEN], client_ip)
        else:
            cleaned = _clean(value, client_ip)
        kept[key] = f"{kept[key]}, {cleaned}" if key in kept else cleaned
    return kept


# ---------------------------------------------------------------------------
# Referral
# ---------------------------------------------------------------------------

UTM_KEYS: Final[tuple[str, ...]] = (
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
)


def utm_from(query: Mapping[str, str], *, client_ip: str | None) -> dict[str, str] | None:
    """Campaign parameters, if any. Read for attribution only -- a query parameter
    never influences where the visitor is sent (F1.AC7)."""
    found = {key: _clean(query[key], client_ip, 256) for key in UTM_KEYS if query.get(key)}
    return found or None


# scheme "://" authority -- the rest (path, query, fragment) is never kept.
_ORIGIN: Final = re.compile(r"^\s*([A-Za-z][A-Za-z0-9+.\-]*)://([^/?#\s]+)")
MAX_ORIGIN_LEN: Final = 255


def referrer_origin(value: str) -> str | None:
    """The Referer's origin -- ``https://l.instagram.com``, ``android-app://com.google.android.gm``
    -- lower-cased, without any user info. ``None`` for a value with no scheme and host.

    A referrer's path and query can carry personal data (a search, a profile, a token), so
    only the origin is kept (SPEC section 11 row 28, F3.AC1)."""
    match = _ORIGIN.match(value)
    if match is None:
        return None
    host = match.group(2).rsplit("@", 1)[-1].lower()
    return f"{match.group(1).lower()}://{host}"[:MAX_ORIGIN_LEN] if host else None


def clean_referer(value: str | None, *, client_ip: str | None) -> str | None:
    """The origin of the Referer, with any address in it masked (layers 1 and 3)."""
    origin = referrer_origin(value) if value else None
    return _clean(origin, client_ip, MAX_ORIGIN_LEN) if origin else None


def clean_user_agent(value: str | None, *, client_ip: str | None) -> str | None:
    """Only the exact observed address: a user agent is full of version numbers that
    are syntactically IPv4 addresses (layer 3 exemption, E23)."""
    return mask_client(value[:1024], client_ip) if value else None


# ---------------------------------------------------------------------------
# Transport and edge
# ---------------------------------------------------------------------------

# Cloudflare edge colos are three-letter IATA codes: "…-BLR".
_CF_RAY = re.compile(r"-([A-Za-z]{3})$")
_COUNTRY = re.compile(r"^[A-Z]{2}$")


@dataclass(frozen=True, slots=True)
class EdgeSignals:
    """What the Cloudflare edge said about this request -- only if it is believed."""

    cf_colo: str | None
    cf_country: str | None


def edge_signals(
    *, cf_ray: str | None, cf_ipcountry: str | None, edge_verified: bool
) -> EdgeSignals:
    """Cloudflare's colo and country, believed only from a verified edge (F13.AC6).

    An unverified ``CF-IPCountry`` is a visitor choosing their own country. Storing it
    would be storing attacker-chosen location data as if it were an observation --
    worse than storing nothing, because it looks like evidence.
    """
    if not edge_verified:
        return EdgeSignals(cf_colo=None, cf_country=None)

    colo = None
    if cf_ray and (match := _CF_RAY.search(cf_ray.strip())):
        colo = match.group(1).upper()

    country = None
    if cf_ipcountry:
        candidate = cf_ipcountry.strip().upper()
        # XX is "unknown" and T1 is Tor; neither is a country.
        if _COUNTRY.match(candidate) and candidate not in {"XX", "T1"}:
            country = candidate

    return EdgeSignals(cf_colo=colo, cf_country=country)


def normalise_http_version(value: str | None) -> str | None:
    """Caddy's ``{http.request.proto}``: ``HTTP/2.0`` -> ``HTTP/2``."""
    if not value:
        return None
    cleaned = value.strip().upper()
    return {"HTTP/2.0": "HTTP/2", "HTTP/3.0": "HTTP/3"}.get(cleaned, cleaned)[:16]


def normalise_tls_version(value: str | None) -> str | None:
    """Caddy's ``{http.request.tls.version}``: ``tls1.3`` -> ``TLS 1.3``."""
    if not value:
        return None
    match = re.fullmatch(r"tls\s*1\.(\d)", value.strip(), re.IGNORECASE)
    return f"TLS 1.{match.group(1)}" if match else None


# ---------------------------------------------------------------------------
# Exploit probes (F5.AC13)
# ---------------------------------------------------------------------------

# Scanner and exploit payloads that arrive on the capture path's query string. Matched
# on keys and values; only the *name* of what matched is stored, never the payload --
# a payload is attacker-controlled text and has no business in the database.
EXPLOIT_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (
        "sql_injection",
        re.compile(
            r"union\s+(all\s+)?select|'\s*or\s+'?\d|sleep\s*\(\s*\d|benchmark\s*\(|information_schema",
            re.I,
        ),
    ),
    ("script_injection", re.compile(r"<\s*script|javascript:|onerror\s*=|onload\s*=", re.I)),
    ("path_traversal", re.compile(r"\.\./|\.\.%2f|%2e%2e[/%]", re.I)),
    ("jndi_lookup", re.compile(r"\$\{\s*jndi:", re.I)),
    ("command_injection", re.compile(r";\s*(cat|wget|curl|bash|sh)\s|\$\(|`[^`]*`", re.I)),
    ("sensitive_file", re.compile(r"/etc/passwd|\.env\b|wp-config|\.git/", re.I)),
)


def exploit_probes(query: Mapping[str, str]) -> list[str]:
    """Names of exploit patterns present anywhere in the query string."""
    text = " ".join(f"{k} {v}" for k, v in query.items())[:4096]
    return [name for name, pattern in EXPLOIT_PATTERNS if pattern.search(text)]
