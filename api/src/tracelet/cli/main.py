"""The ``tracelet`` command-line entry point.

Exists in M0 as a working skeleton with an honest inventory of what each later
milestone adds, so the interface is discoverable before it is complete.

The commands that matter operationally:

``tracelet admin reset-password``
    The break-glass path (F8.AC8). Works with database and shell access only, so
    losing both the recovery codes and the Telegram account is not a permanent
    lockout. Always writes an audit row.

``tracelet geodb update``
    Streams to disk and validates in a memory-capped subprocess, because a failed
    update must never OOM the capture endpoint (F10.AC5, RISKS R4).
"""

from __future__ import annotations

import argparse
import sys

from tracelet import __version__
from tracelet.cli import admin as admin_cli
from tracelet.cli import analytics as analytics_cli
from tracelet.cli import geodb as geodb_cli
from tracelet.cli import inference as inference_cli

# Commands the milestones add. Listed here so `tracelet --help` is useful now and
# so the roadmap is visible from the tool itself.
# Commands later milestones add. Anything already shipped is marked, so the table
# stays honest rather than becoming a list of promises.
PLANNED: dict[str, tuple[str, str]] = {
    "admin telegram-test": ("M1", "SHIPPED — send a test message to the owner chat"),
    "link list": ("M2", "list tracking links"),
    "geodb status": ("M3", "SHIPPED — installed versions and staleness verdict"),
    "geodb update": ("M3", "SHIPPED — download, verify and atomically swap a geo database"),
    "geodb profiles": ("M3", "SHIPPED — recompute asn_profiles (automatic after an update)"),
    "inference reset-defaults": (
        "M3",
        "SHIPPED — save the built-in defaults as a new settings version",
    ),
    "analytics rebuild": ("M5", "SHIPPED — rebuild the analytics rollups for a range of days"),
    "label add": ("M8", "record ground truth for one visit"),
    "accuracy report": ("M8", "precision and coverage per level, with sample size"),
    "retention preview": ("M7", "dry run: exactly what a purge would delete"),
    "retention purge": ("M7", "execute a purge, batched and audit-logged"),
    "backup create": ("M7", "create a compressed dump with a checksum manifest"),
    "backup verify": ("M7", "restore into a scratch schema and assert row counts"),
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tracelet",
        description="Tracelet administration and operations.",
        epilog="Commands not yet implemented are listed by: tracelet roadmap",
    )
    parser.add_argument("--version", action="version", version=f"tracelet {__version__}")

    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.add_parser("roadmap", help="show which milestone adds which command")
    sub.add_parser("check-config", help="validate configuration and exit")
    admin_cli.register(sub)
    geodb_cli.register(sub)
    inference_cli.register(sub)
    analytics_cli.register(sub)
    return parser


def _cmd_roadmap() -> int:
    width = max(len(name) for name in PLANNED)
    print("Planned commands, by milestone:\n")
    for name, (milestone, description) in PLANNED.items():
        print(f"  {milestone}  {name:<{width}}  {description}")
    print("\nSee docs/MILESTONES.md.")
    return 0


def _cmd_check_config() -> int:
    """Validate configuration without starting the app.

    Useful before a deploy: a bad value fails here with a readable message rather
    than in a container that then restart-loops.
    """
    # Imported lazily on purpose: Settings validates at construction, so a
    # top-level import would make `tracelet --help` fail on a misconfigured host
    # -- exactly when you most need the help text.
    from tracelet.config import Settings  # noqa: PLC0415

    try:
        settings = Settings()
    except Exception as exc:  # noqa: BLE001 - a CLI reports, it does not traceback
        print(f"Configuration invalid:\n  {exc}", file=sys.stderr)
        return 1

    print("Configuration valid.")
    print(f"  environment       {settings.env}")
    print(f"  site address      {settings.site_address}")
    print(f"  behind cloudflare {settings.behind_cloudflare}")
    print(f"  workers           {settings.web_concurrency}")
    print(f"  db pool           {settings.db_pool_min}-{settings.db_pool_max}")
    print(
        f"  retention         visits {settings.retention_visit_days}d, ip {settings.retention_ip_days}d"
    )

    if not settings.behind_cloudflare:
        print(
            "\n  note: not behind Cloudflare. On the free-subdomain path this means no\n"
            "  edge TLS (~4x slower first byte for Indian visitors), no CF-Ray colo geo\n"
            "  signal, and no layer-0 DDoS absorption. See ADR-0012 and RISKS R10.",
            file=sys.stderr,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "roadmap":
        return _cmd_roadmap()
    if args.command == "check-config":
        return _cmd_check_config()
    if args.command == "admin":
        return admin_cli.dispatch(args)
    if args.command == "geodb":
        return geodb_cli.dispatch(args)
    if args.command == "inference":
        return inference_cli.dispatch(args)
    if args.command == "analytics":
        return analytics_cli.dispatch(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
