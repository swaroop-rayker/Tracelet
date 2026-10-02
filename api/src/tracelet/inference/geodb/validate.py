"""Validate one downloaded database, in a subprocess with a hard memory cap.

Run as ``python -m tracelet.inference.geodb.validate <kind> <path> [<database_type>]``.
Prints one JSON line and exits 0 when the file is usable, non-zero otherwise.

Why a subprocess: opening a corrupt database can make a reader allocate without bound,
and the API workers share a 1 GB box (CLAUDE.md section 5). Here the worst case is this
process hitting its ``RLIMIT_AS`` and dying, which the installer reports as a failed
validation -- the serving version is never touched.

"Usable" means more than "parses": the file must answer known lookups the way that kind
of database should. A truncated download that still opens fails here.
"""

from __future__ import annotations

import json
import os
import resource
import sys
from pathlib import Path
from typing import Any, Final

# Well-known public addresses; a real database answers at least two of three.
PROBES: Final = ("8.8.8.8", "1.1.1.1", "49.207.12.34")
# Headroom above the file itself, which counts toward the address space when mapped.
HEADROOM_BYTES: Final = 384 * 1024 * 1024


def _cap(path: Path) -> None:
    limit = path.stat().st_size + HEADROOM_BYTES
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def _mmdb(path: Path, database_type: str | None) -> dict[str, Any]:
    import maxminddb  # noqa: PLC0415 - imported after the memory cap is in place

    with maxminddb.open_database(str(path), maxminddb.MODE_MMAP) as reader:
        meta = reader.metadata()
        if database_type and database_type.casefold() not in meta.database_type.casefold():
            msg = f"database_type {meta.database_type!r} is not {database_type!r}"
            raise ValueError(msg)
        answered = sum(1 for ip in PROBES if reader.get(ip))
        if answered < 2:
            msg = f"answered {answered} of {len(PROBES)} probe lookups"
            raise ValueError(msg)
        return {"database_type": meta.database_type, "build_epoch": meta.build_epoch}


def _ip2location(path: Path) -> dict[str, Any]:
    import IP2Location  # noqa: PLC0415 - imported after the memory cap is in place

    db = IP2Location.IP2Location(str(path), "SHARED_MEMORY")
    try:
        answered = sum(
            1
            for ip in PROBES
            if (db.get_country_short(ip) or "-") not in ("-", "INVALID IP ADDRESS")
        )
    finally:
        db.close()
    if answered < 2:
        msg = f"answered {answered} of {len(PROBES)} probe lookups"
        raise ValueError(msg)
    return {"answered": answered}


def _geonames_cities(path: Path) -> dict[str, Any]:
    rows = india = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 19:
                msg = f"line {rows + 1} has {len(fields)} fields, not 19"
                raise ValueError(msg)
            rows += 1
            india += fields[8] == "IN"
    if rows < 100_000 or india < 1_000:
        msg = f"{rows} places ({india} in India) -- too few for cities1000"
        raise ValueError(msg)
    return {"places": rows, "india": india}


def _geonames_admin1(path: Path) -> dict[str, Any]:
    codes = set()
    with path.open(encoding="utf-8") as f:
        for line in f:
            fields = line.split("\t")
            if len(fields) >= 2:
                codes.add(fields[0])
    if len(codes) < 3_000 or "IN.19" not in codes:
        msg = f"{len(codes)} admin1 codes -- expected thousands, including IN.19"
        raise ValueError(msg)
    return {"codes": len(codes)}


def _tor_exits(path: Path) -> dict[str, Any]:
    import ipaddress  # noqa: PLC0415 - only this kind needs it

    count = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            entry = line.strip()
            if not entry or entry.startswith("#"):
                continue
            ipaddress.ip_address(entry)  # anything else in the file is a corrupt download
            count += 1
    if count < 300:
        msg = f"{count} exit addresses -- the live list has well over a thousand"
        raise ValueError(msg)
    return {"exits": count}


def validate(kind: str, path: Path, database_type: str | None) -> dict[str, Any]:
    if kind == "mmdb":
        return _mmdb(path, database_type)
    if kind == "ip2location_bin":
        return _ip2location(path)
    if kind == "geonames_cities":
        return _geonames_cities(path)
    if kind == "geonames_admin1":
        return _geonames_admin1(path)
    if kind == "tor_exits":
        return _tor_exits(path)
    msg = f"unknown kind {kind!r}"
    raise ValueError(msg)


def main(argv: list[str]) -> int:
    kind, path = argv[1], Path(argv[2])
    database_type = argv[3] if len(argv) > 3 and argv[3] else None
    try:
        _cap(path)
        detail = validate(kind, path, database_type)
    except Exception as exc:  # noqa: BLE001 - the verdict is the output; nothing propagates
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}))
        return 1
    print(json.dumps({"ok": True, **detail}))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    sys.exit(main(sys.argv))
