"""``tracelet analytics rebuild`` -- rebuild the rollups for a range (ADR-0016).

Needed after changing ``TRACELET_REPORTING_TZ`` (every day's buckets move), after
restoring a backup, or to carry a projection fix back into history. Rollups are derived
data: rebuilding re-reads the visits that still exist and loses nothing else, so it is
not an audited action. A day whose visits have already been purged is rebuilt as
empty, so ``--since`` defaults to the start of the visit retention window rather than
reaching back past it.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
from typing import Any

from tracelet.analytics import rollup
from tracelet.config import get_settings
from tracelet.db.engine import dispose_engine, init_engine
from tracelet.logging import configure_logging


def register(sub: Any) -> None:
    parser = sub.add_parser("analytics", help="analytics rollups")
    commands = parser.add_subparsers(dest="analytics_command", metavar="<analytics command>")
    rebuild = commands.add_parser("rebuild", help="rebuild the rollups for a range of days")
    rebuild.add_argument(
        "--since",
        type=dt.date.fromisoformat,
        default=None,
        help="first local day (YYYY-MM-DD); default: the start of visit retention",
    )
    rebuild.add_argument(
        "--until",
        type=dt.date.fromisoformat,
        default=None,
        help="last local day (YYYY-MM-DD); default: today",
    )


async def _rebuild(since: dt.date | None, until: dt.date | None) -> int:
    settings = get_settings()
    configure_logging(level="WARNING", json_output=False)
    init_engine(settings)
    try:
        today = rollup.today(settings.reporting_tz)
        last = until or today
        first = since or today - dt.timedelta(days=settings.retention_visit_days)
        if first > last:
            print(f"--since {first} is after --until {last}. Nothing rebuilt.")
            return 2
        built = await rollup.rebuild(first, last, settings)
    finally:
        await dispose_engine()
    print(f"Rebuilt {built} day(s), {first} to {last}, in {settings.reporting_tz}.")
    return 0


def dispatch(args: argparse.Namespace) -> int:
    if getattr(args, "analytics_command", None) == "rebuild":
        return asyncio.run(_rebuild(args.since, args.until))
    print("usage: tracelet analytics rebuild [--since YYYY-MM-DD] [--until YYYY-MM-DD]")
    return 2
