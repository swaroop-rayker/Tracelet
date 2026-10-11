"""``tracelet backup adopt`` -- the last step of a bare-metal restore (ADR-0030).

``./scripts/tl restore <file>`` restores a downloaded backup into a new, empty database and
then runs this, before the api starts. The dump holds its own ``backups`` row as
``running``; adoption copies the file into the backup volume, marks that row ``ok`` with the
restored database's counts, and marks every other ``ok`` row whose file stayed on the old
volume ``pruned``. "Verify now" on the rebuilt system then checks the backup it came from.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from tracelet.config import get_settings
from tracelet.db.engine import dispose_engine, init_engine
from tracelet.errors import MaintenanceUnavailable
from tracelet.lifecycle import backups
from tracelet.logging import configure_logging


def register(sub: Any) -> None:
    parser = sub.add_parser("backup", help="backups (after a bare-metal restore)")
    commands = parser.add_subparsers(dest="backup_command", metavar="<backup command>")
    adopt = commands.add_parser(
        "adopt", help="after tl restore: record the restored-from backup as this system's own"
    )
    adopt.add_argument("file", type=Path, help="the .dump file the database was restored from")


async def _adopt(source: Path) -> int:
    settings = get_settings()
    configure_logging(level="WARNING", json_output=False)
    init_engine(settings)
    try:
        adopted = await backups.adopt_restored(source, settings)
    except (backups.JobFailedError, MaintenanceUnavailable, OSError) as exc:
        print(f"Not adopted: {exc}")
        return 1
    finally:
        await dispose_engine()
    print(f"Adopted {adopted.file_name} as backup {adopted.backup_id}: {adopted.tables} tables.")
    print(f"{adopted.pruned} older backup(s) marked pruned: their files stayed on the old volume.")
    return 0


def dispatch(args: argparse.Namespace) -> int:
    if getattr(args, "backup_command", None) == "adopt":
        return asyncio.run(_adopt(args.file))
    print("usage: tracelet backup adopt <file>")
    return 2
