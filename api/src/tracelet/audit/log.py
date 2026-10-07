"""Audit log writer.

Every authentication event, privilege change, configuration change and destructive
action writes a row here (F8.AC16, CLAUDE.md invariant 9).

Two properties make it worth having:

* **Append-only at the engine level.** ``tracelet_app`` has no UPDATE or DELETE on
  this table (migration 0002). A SQL-injection foothold in the application path
  cannot erase the evidence of itself.
* **Redacted before write.** The same rule as logging applies: a row must never
  contain what the database refuses to store in plaintext (F12.AC13). An audit
  entry recording "who decrypted this IP" must not itself contain the IP.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from tracelet.auth.models import AuditLog
from tracelet.db.engine import session_scope
from tracelet.logging import redact_processor

log = structlog.get_logger(__name__)


class Action:
    """The audit vocabulary.

    A closed set of constants rather than free strings, so
    ``/api/v1/...?action=admin.login_failed`` filtering stays reliable and a typo
    cannot silently create a new action nobody queries for.
    """

    # authentication
    LOGIN_SUCCEEDED: Final = "admin.login_succeeded"
    LOGIN_FAILED: Final = "admin.login_failed"
    LOGIN_LOCKED: Final = "admin.login_locked"
    MFA_FAILED: Final = "admin.mfa_failed"
    LOGOUT: Final = "admin.logout"
    SESSION_REVOKED: Final = "admin.session_revoked"
    SESSION_BINDING_MISMATCH: Final = "admin.session_binding_mismatch"

    # credentials
    PASSWORD_CHANGED: Final = "admin.password_changed"
    PASSWORD_RESET_REQUESTED: Final = "admin.password_reset_requested"
    PASSWORD_RESET_COMPLETED: Final = "admin.password_reset_completed"
    PASSWORD_RESET_CLI: Final = "admin.password_reset_cli"
    RECOVERY_CODE_USED: Final = "admin.recovery_code_used"
    RECOVERY_CODES_REGENERATED: Final = "admin.recovery_codes_regenerated"
    TOTP_ENROLLED: Final = "admin.totp_enrolled"
    TOTP_RESET: Final = "admin.totp_reset"

    # lifecycle
    ADMIN_CREATED: Final = "admin.created"
    ADMIN_UPDATED: Final = "admin.updated"
    ADMIN_DELETED: Final = "admin.deleted"
    ADMIN_ROLE_CHANGED: Final = "admin.role_changed"
    ADMIN_STATUS_CHANGED: Final = "admin.status_changed"
    ENROLLMENT_TOKEN_ISSUED: Final = "admin.enrollment_token_issued"
    ENROLLMENT_COMPLETED: Final = "admin.enrollment_completed"
    TELEGRAM_VERIFIED: Final = "admin.telegram_verified"

    # tracking links (M2, F1) -- every one is an owner action, CLAUDE.md invariant 9
    LINK_CREATED: Final = "link.created"
    LINK_UPDATED: Final = "link.updated"  # F1.AC8: names old and new destination
    LINK_CLONED: Final = "link.cloned"
    LINK_DEFAULT_CHANGED: Final = "link.default_changed"
    LINK_ARCHIVED: Final = "link.archived"
    LINK_DELETED: Final = "link.deleted"

    # geofences (M6, F6) -- owner-only configuration, invariant 9
    GEOFENCE_CREATED: Final = "geofence.created"
    GEOFENCE_UPDATED: Final = "geofence.updated"  # names every changed field, old and new
    GEOFENCE_DELETED: Final = "geofence.deleted"
    GEOFENCE_IMPORTED: Final = "geofence.imported"

    # notifications (M6, F7)
    OUTBOX_RETRIED: Final = "outbox.retried"  # a dead letter requeued by hand, F7.AC6
    TELEGRAM_TEST_SENT: Final = "telegram.test_sent"  # F7.AC8

    # visits (M2)
    IP_DECRYPTED: Final = "visit.ip_decrypted"  # F12.AC4

    # location inference (M3, F4.AC14) -- owner-only configuration, invariant 9
    INFERENCE_SETTINGS_CHANGED: Final = "inference.settings_changed"
    INFERENCE_SETTINGS_ROLLED_BACK: Final = "inference.settings_rolled_back"

    # reserved for later milestones, listed so the vocabulary is visible
    RETENTION_PURGED: Final = "retention.purged"  # M7
    SETTINGS_CHANGED: Final = "settings.changed"  # M6 (quiet hours), M7


async def record(
    session: AsyncSession,
    *,
    action: str,
    actor_admin_id: uuid.UUID | None = None,
    actor_ip_prefix: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    trace_id: str | None = None,
    detail: dict[str, Any] | None = None,
    independent: bool = False,
) -> None:
    """Append one audit row.

    Written on the caller's session, so it commits or rolls back **with** the action
    it describes. An audit row for an action that was rolled back would be a lie;
    a missing row for an action that succeeded would be a gap.

    ``actor_admin_id=None`` means the system acted -- a scheduled job or the CLI.

    Pass ``independent=True`` for an event that must be recorded **even though the
    request is about to fail**: a failed login, a rejected code, a lockout. Those
    are written on their own connection and committed immediately, because the
    request transaction may be rolled back and F8.AC16 requires the attempt to be
    recorded regardless. Without this, the only authentication events in the audit
    log would be the successful ones -- which is precisely backwards for anyone
    investigating an intrusion.
    """
    entry = AuditLog(
        action=action,
        actor_admin_id=actor_admin_id,
        actor_ip_prefix=actor_ip_prefix,
        target_type=target_type,
        target_id=target_id,
        trace_id=trace_id,
        # Same redaction pipeline as the logger, so the two cannot drift apart.
        detail=dict(redact_processor(None, "info", dict(detail or {}))),
    )

    if independent:
        async with session_scope() as own:
            own.add(entry)
    else:
        session.add(entry)

    log.info(
        "audit",
        action=action,
        actor=str(actor_admin_id) if actor_admin_id else "system",
        target=f"{target_type}:{target_id}" if target_type else None,
    )
