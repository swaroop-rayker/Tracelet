"""``tracelet geodb ...`` -- install, inspect and recompute the offline databases (F10.AC3).

The same code the daily job runs, so what an operator runs by hand is what production
does on its own. No command prints a download URL: two vendors put their token in it.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
from collections.abc import Coroutine
from typing import Any

from sqlalchemy import select

from tracelet.config import get_settings
from tracelet.db.engine import dispose_engine, init_engine, session_scope
from tracelet.inference.geodb import maintenance
from tracelet.inference.geodb.catalog import BY_NAME, CATALOG
from tracelet.inference.models import GeoDatabase, GeoDbStatus
from tracelet.logging import configure_logging


def register(sub: Any) -> None:
    geodb = sub.add_parser("geodb", help="install and inspect the offline geo databases")
    commands = geodb.add_subparsers(dest="geodb_command", metavar="<geodb command>")
    commands.add_parser("status", help="installed versions and staleness verdict")
    update = commands.add_parser(
        "update", help="download, verify and atomically install what is due"
    )
    update.add_argument("names", nargs="*", help=f"any of: {', '.join(BY_NAME)}")
    update.add_argument(
        "--force", action="store_true", help="install the named databases even if not due"
    )
    commands.add_parser("profiles", help="recompute asn_profiles from the installed databases")


def _run(coro: Coroutine[Any, Any, int]) -> int:
    async def wrapper() -> int:
        configure_logging(level="INFO", json_output=False)
        init_engine(get_settings())
        try:
            return await coro
        finally:
            await dispose_engine()

    return asyncio.run(wrapper())


async def _status() -> int:
    now = dt.datetime.now(dt.UTC)
    async with session_scope() as db:
        rows = {
            r.name: r
            for r in (
                await db.execute(
                    select(GeoDatabase).where(GeoDatabase.status == GeoDbStatus.INSTALLED)
                )
            ).scalars()
        }
    settings = get_settings()
    for spec in CATALOG:
        row = rows.get(spec.name)
        configured = spec.download(settings, now.date()) is not None
        if row is None:
            state = "not installed" if configured else "not configured (credentials missing)"
            print(f"  {spec.name:<24} {state}")
            continue
        installed = row.installed_at or row.created_at
        age = (now - installed).days
        verdict = "STALE" if age > row.staleness_threshold_days else "fresh"
        size = f"{(row.size_bytes or 0) / 1_048_576:.0f} MB"
        print(f"  {spec.name:<24} {row.version or '?':<20} {size:>7}  {age:>3}d  {verdict}")
    return 0


async def _update(names: list[str], force: bool) -> int:
    unknown = [n for n in names if n not in BY_NAME]
    if unknown:
        print(f"Unknown database(s): {', '.join(unknown)}. Known: {', '.join(BY_NAME)}")
        return 2
    results = await maintenance.update_all(only=names or None, force=force)
    if not results:
        print("Nothing is due.")
    for r in results:
        print(f"  {r.name:<24} {r.status:<10} {r.version or ''} {r.detail or ''}".rstrip())
    return 1 if any(r.status == "failed" for r in results) else 0


async def _profiles() -> int:
    count = await maintenance.recompute_profiles()
    print(f"asn_profiles: {count} ASNs profiled.")
    return 0


def dispatch(args: argparse.Namespace) -> int:
    command = getattr(args, "geodb_command", None)
    if command == "status":
        return _run(_status())
    if command == "update":
        return _run(_update(list(args.names), bool(args.force)))
    if command == "profiles":
        return _run(_profiles())
    print("usage: tracelet geodb {status,update,profiles}")
    return 2
