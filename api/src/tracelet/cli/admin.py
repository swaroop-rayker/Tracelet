"""``tracelet admin`` — the paths that work without a browser.

The important one is ``reset-password``: the **break-glass** path (F8.AC8). It needs
only database and shell access, which is what stops "lost the recovery codes AND lost
the Telegram account" from being permanent lockout. It uses the same service functions
as the API, so it writes the same audit rows and enforces the same password policy --
a CLI that bypassed either would be a back door.

``bootstrap`` creates the first owner. It sets no password: it prints a one-time
enrollment URL, because there is no default password anywhere in this system (F8.AC15).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from collections.abc import Coroutine
from typing import Any

import structlog
from sqlalchemy import func, select, update

from tracelet.audit import log as audit
from tracelet.auth import recovery, sessions
from tracelet.auth.models import Admin, AdminRole, AdminStatus
from tracelet.auth.service import (
    get_by_email,
    issue_enrollment_token,
    validate_password,
)
from tracelet.config import get_settings
from tracelet.crypto.hashing import argon2_parameters, hash_password
from tracelet.db.engine import dispose_engine, init_engine, session_scope
from tracelet.errors import ValidationFailed
from tracelet.logging import configure_logging
from tracelet.notify import telegram

log = structlog.get_logger(__name__)


def _run(coro: Coroutine[Any, Any, int]) -> int:
    """Run one command with the engine initialised, then dispose it.

    The CLI owns its own engine lifecycle because it does not go through the app
    lifespan -- and leaving connections open would hold slots from the reserve that
    exists for pg_dump (ARCHITECTURE §6.2).
    """

    async def wrapper() -> int:
        settings = get_settings()
        configure_logging(level="WARNING", json_output=False)
        init_engine(settings)
        try:
            return await coro
        finally:
            await dispose_engine()

    return asyncio.run(wrapper())


# ---------------------------------------------------------------------------


async def _bootstrap(email: str, display_name: str) -> int:
    settings = get_settings()
    async with session_scope() as db:
        total = int((await db.execute(select(func.count()).select_from(Admin))).scalar_one())
        existing = await get_by_email(db, email)

        if existing is not None:
            print(f"An admin with that address already exists (status: {existing.status.value}).")
            print("Issuing a fresh enrollment link instead.")
            admin_id = existing.id
        elif total > 0:
            print(
                "Admins already exist. bootstrap creates only the FIRST owner.\n"
                "Use the dashboard, or `tracelet admin create`, to invite another.",
                file=sys.stderr,
            )
            return 1
        else:
            admin = Admin(
                email=email,
                display_name=display_name,
                role=AdminRole.OWNER,
                status=AdminStatus.PENDING_ENROLLMENT,
            )
            db.add(admin)
            await db.flush()
            admin_id = admin.id
            await audit.record(
                db,
                action=audit.Action.ADMIN_CREATED,
                target_type="admin",
                target_id=str(admin_id),
                detail={"role": "owner", "via": "cli_bootstrap"},
            )

        offer = await issue_enrollment_token(db, settings, admin_id=admin_id, created_by=None)

    print()
    print("  First owner created. No password has been set — by design.")
    print()
    print("  Open this one-time link to set a password and enrol two-factor auth:")
    print()
    print(f"    {offer.url}")
    print()
    print(f"  It expires at {offer.expires_at:%Y-%m-%d %H:%M UTC}.")
    print()
    print("  You will be shown 10 recovery codes EXACTLY ONCE. Save them somewhere")
    print("  you will actually find them: with no codes and no Telegram access, only")
    print("  `tracelet admin reset-password` can recover the account.")
    print()
    return 0


async def _list() -> int:
    async with session_scope() as db:
        rows = (await db.execute(select(Admin).order_by(Admin.created_at))).scalars().all()
        if not rows:
            print("No admins. Run: tracelet admin bootstrap --email you@example.com")
            return 0
        print(f"{'EMAIL':<34} {'ROLE':<9} {'STATUS':<20} {'2FA':<5} {'TG':<4} CODES")
        for row in rows:
            codes = await recovery.remaining(db, row.id)
            print(
                f"{row.email:<34} {row.role.value:<9} {row.status.value:<20} "
                f"{'yes' if row.totp_enrolled else 'no':<5} "
                f"{'yes' if row.telegram_verified_at else 'no':<4} {codes}"
            )
    return 0


async def _create(email: str, display_name: str, role: str) -> int:
    settings = get_settings()
    async with session_scope() as db:
        if await get_by_email(db, email) is not None:
            print("An admin with that address already exists.", file=sys.stderr)
            return 1
        admin = Admin(
            email=email,
            display_name=display_name,
            role=AdminRole(role),
            status=AdminStatus.PENDING_ENROLLMENT,
        )
        db.add(admin)
        await db.flush()
        await audit.record(
            db,
            action=audit.Action.ADMIN_CREATED,
            target_type="admin",
            target_id=str(admin.id),
            detail={"role": role, "via": "cli"},
        )
        offer = await issue_enrollment_token(db, settings, admin_id=admin.id, created_by=None)

    print(f"Created {email} ({role}), pending enrolment.")
    print(f"One-time enrollment link (expires {offer.expires_at:%Y-%m-%d %H:%M UTC}):")
    print(f"  {offer.url}")
    return 0


async def _reset_password(email: str, *, revoke_sessions: bool) -> int:
    """Break-glass reset (F8.AC8).

    Reads the new password from a TTY prompt, never an argument: a command-line
    argument lands in shell history and in the process table, where any other user on
    the box can read it.
    """
    settings = get_settings()
    async with session_scope() as db:
        admin = await get_by_email(db, email)
        if admin is None:
            print(f"No admin with address {email}.", file=sys.stderr)
            return 1

        if sys.stdin.isatty():
            first = getpass.getpass("New password: ")
            second = getpass.getpass("Confirm: ")
            if first != second:
                print("Passwords do not match.", file=sys.stderr)
                return 1
            new_password = first
        else:
            # Non-interactive use, e.g. a runbook step piping from a password manager.
            new_password = sys.stdin.readline().rstrip("\n")
            if not new_password:
                print("No password supplied on stdin.", file=sys.stderr)
                return 1

        try:
            validate_password(new_password, email=admin.email, site_address=settings.site_address)
        except ValidationFailed as exc:
            print("Password rejected:", file=sys.stderr)
            for err in exc.errors:
                print(f"  - {err.message}", file=sys.stderr)
            return 1

        await db.execute(
            update(Admin)
            .where(Admin.id == admin.id)
            .values(
                password_hash=hash_password(new_password),
                password_params=argon2_parameters(),
                password_changed_at=func.now(),
                failed_login_count=0,
                locked_until=None,
            )
        )
        revoked = 0
        if revoke_sessions:
            revoked = await sessions.revoke_all_for_admin(db, admin.id, reason="password_reset_cli")

        # Audited like every other privileged action. A break-glass path that left no
        # trace would be the most useful thing in the system to an attacker.
        await audit.record(
            db,
            action=audit.Action.PASSWORD_RESET_CLI,
            target_type="admin",
            target_id=str(admin.id),
            detail={"sessions_revoked": revoked, "via": "cli"},
        )

    print(f"Password reset for {email}.")
    if revoke_sessions:
        print(f"Revoked {revoked} active session(s).")
    print("Two-factor authentication is unchanged — you still need your authenticator.")
    return 0


async def _reset_totp(email: str) -> int:
    """Clear TOTP enrolment, forcing re-enrolment.

    Deactivates the account, because the CHECK constraint forbids an active account
    without TOTP. Re-enrolment happens through a fresh enrollment link.
    """
    settings = get_settings()
    async with session_scope() as db:
        admin = await get_by_email(db, email)
        if admin is None:
            print(f"No admin with address {email}.", file=sys.stderr)
            return 1

        await db.execute(
            update(Admin)
            .where(Admin.id == admin.id)
            .values(
                totp_secret_enc=None,
                totp_key_version=None,
                totp_enrolled_at=None,
                totp_last_counter=None,
                status=AdminStatus.PENDING_ENROLLMENT,
            )
        )
        await sessions.revoke_all_for_admin(db, admin.id, reason="totp_reset")
        await audit.record(
            db,
            action=audit.Action.TOTP_RESET,
            target_type="admin",
            target_id=str(admin.id),
            detail={"via": "cli"},
        )
        offer = await issue_enrollment_token(db, settings, admin_id=admin.id, created_by=None)

    print(f"Two-factor authentication cleared for {email}; the account is now pending.")
    print(f"Re-enrol here (expires {offer.expires_at:%Y-%m-%d %H:%M UTC}):")
    print(f"  {offer.url}")
    return 0


async def _telegram_test() -> int:
    settings = get_settings()
    try:
        token = settings.require("telegram_bot_token", "Telegram test")
        chat_id = settings.telegram_owner_chat_id
        if chat_id is None:
            print(
                "TRACELET_TELEGRAM_OWNER_CHAT_ID is not set. See .env.example.",
                file=sys.stderr,
            )
            return 1
        username = await telegram.get_me(bot_token=token)
        await telegram.send_message(bot_token=token, chat_id=chat_id, text=telegram.test_message())
    except (telegram.TelegramError, RuntimeError) as exc:
        print(f"Telegram test failed: {exc}", file=sys.stderr)
        return 1

    print(f"Sent a test message as @{username}. Check your Telegram.")
    return 0


# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    admin = subparsers.add_parser("admin", help="manage admin accounts")
    sub = admin.add_subparsers(dest="admin_command", metavar="<subcommand>")

    p = sub.add_parser("bootstrap", help="create the first owner and print an enrollment link")
    p.add_argument("--email", required=True)
    p.add_argument("--name", default="Owner")

    sub.add_parser("list", help="list admins with role, status and code count")

    p = sub.add_parser("create", help="invite an admin")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--role", choices=[r.value for r in AdminRole], default=AdminRole.ANALYST.value)

    p = sub.add_parser("reset-password", help="break-glass password reset (needs shell access)")
    p.add_argument("--email", required=True)
    p.add_argument(
        "--keep-sessions",
        action="store_true",
        help="do not revoke active sessions (not recommended)",
    )

    p = sub.add_parser("reset-totp", help="clear two-factor enrolment and re-issue a link")
    p.add_argument("--email", required=True)

    sub.add_parser("telegram-test", help="send a test message to the owner chat")


def dispatch(args: argparse.Namespace) -> int:
    command = getattr(args, "admin_command", None)

    if command == "bootstrap":
        return _run(_bootstrap(args.email, args.name))
    if command == "list":
        return _run(_list())
    if command == "create":
        return _run(_create(args.email, args.name, args.role))
    if command == "reset-password":
        return _run(_reset_password(args.email, revoke_sessions=not args.keep_sessions))
    if command == "reset-totp":
        return _run(_reset_totp(args.email))
    if command == "telegram-test":
        return _run(_telegram_test())

    print("Specify a subcommand. Try: tracelet admin --help", file=sys.stderr)
    return 1


__all__ = ["dispatch", "register"]
