"""``tracelet inference ...`` -- inference settings from the host (F4.AC14).

``reset-defaults`` saves the built-in defaults as a **new** version and activates it.
Versions are immutable, so a database seeded before a change to the defaults keeps its
old version 1 until something saves a new one; this is that something, for hosts with no
dashboard yet (M5). Shell access to the host is owner-level already -- the same standing
as ``tracelet admin reset-password`` -- and the change writes an audit row marked as the
CLI's (CLAUDE.md invariant 9).
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Coroutine
from typing import Any

from tracelet.audit import log as audit
from tracelet.config import get_settings
from tracelet.db.engine import dispose_engine, init_engine, session_scope
from tracelet.inference import store
from tracelet.inference.config import DEFAULT_CONFIG, inference_version
from tracelet.logging import configure_logging


def register(sub: Any) -> None:
    parser = sub.add_parser("inference", help="inference settings versions")
    commands = parser.add_subparsers(dest="inference_command", metavar="<inference command>")
    reset = commands.add_parser(
        "reset-defaults", help="save the built-in defaults as a new, active settings version"
    )
    reset.add_argument("--note", default="Built-in defaults, saved from the CLI.")


def _run(coro: Coroutine[Any, Any, int]) -> int:
    async def wrapper() -> int:
        configure_logging(level="WARNING", json_output=False)
        init_engine(get_settings())
        try:
            return await coro
        finally:
            await dispose_engine()

    return asyncio.run(wrapper())


async def _reset_defaults(note: str) -> int:
    async with session_scope() as db:
        before = await store.active_settings(db)
        if before.config == DEFAULT_CONFIG:
            print(f"Version {before.version} is already the built-in defaults. Nothing saved.")
            return 0
        row = await store.save_new_version(db, DEFAULT_CONFIG, note=note, actor=None)
        await audit.record(
            db,
            action=audit.Action.INFERENCE_SETTINGS_CHANGED,
            target_type="inference_settings",
            target_id=str(row.version),
            detail={
                "from_version": before.version,
                "to_version": row.version,
                "note": note,
                "via": "cli_reset_defaults",
            },
        )
    print(f"Saved the built-in defaults as version {row.version} (was {before.version}).")
    print(f"Visits inferred from now on are stamped {inference_version(row.version)}.")
    print(f"Roll back with: POST /api/v1/health/inference/rollback/{before.version}")
    return 0


def dispatch(args: argparse.Namespace) -> int:
    if getattr(args, "inference_command", None) == "reset-defaults":
        return _run(_reset_defaults(str(args.note)))
    print("usage: tracelet inference reset-defaults [--note NOTE]")
    return 2
