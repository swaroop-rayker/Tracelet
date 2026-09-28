"""Telegram client.

Because Gate 1 declined an email provider, Telegram is both the notification channel
(F7) **and** a password-recovery channel (F8.AC7). That makes it security-critical
rather than a convenience: compromise of the owner's Telegram account enables a
password reset. Mitigated by the reset flow not bypassing TOTP, and by recovery codes
plus the CLI being two paths that do not involve Telegram at all (RISKS R18).

**Sent synchronously here, deliberately.** ADR-0009 lists ``telegram.password_reset``
among the outbox kinds, but the outbox exists to make a notification atomic with the
*visit* that triggered it (NFR5.AC2). A password reset has no such transaction, and
the admin is actively waiting: an immediate success or failure is better than a link
that arrives 30 seconds later from a retry, especially as the link is time-limited.
Visit alerts go through the outbox in M6 where the atomicity requirement is real.
Recorded as a deviation in docs/MILESTONES.md.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx
import structlog

log = structlog.get_logger(__name__)

API_BASE = "https://api.telegram.org"

# Short, because an admin is watching a spinner. A slow Telegram should surface as a
# failure they can retry, not a hang.
TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)

MAX_ATTEMPTS = 3
BACKOFF_BASE = 0.5


class TelegramError(RuntimeError):
    """Delivery failed. The message never contains the bot token.

    ``permanent`` distinguishes the two failures a caller must treat differently:
    Telegram *rejecting* the request (a wrong chat id, a blocked bot) will never
    succeed on retry and is usually the operator's input to correct, while a
    transport failure may well be transient and is the server's problem. Collapsing
    them loses the only thing the admin can act on.
    """

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


@dataclass(frozen=True, slots=True)
class SendResult:
    message_id: int
    chat_id: int


def _redact(text: str, token: str) -> str:
    """Strip the token from anything that might be logged or raised.

    Telegram puts the token in the URL path, so an httpx error message can contain
    it verbatim. Without this, one failed request writes a full bot credential into
    the log -- and the log redactor cannot help, because the token appears mid-string
    under a non-sensitive key.
    """
    return text.replace(token, "<bot-token>") if token else text


async def send_message(
    *,
    bot_token: str,
    chat_id: int,
    text: str,
    parse_mode: str | None = "HTML",
    disable_preview: bool = True,
) -> SendResult:
    """Send one message, retrying transient failures.

    Retries on network errors and 5xx. Does **not** retry a 4xx: a bad token, a
    blocked bot or an unknown chat will not fix itself, and retrying only delays a
    clear error.
    """
    url = f"{API_BASE}/bot{bot_token}/sendMessage"
    payload: dict[str, object] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": disable_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    last_error: str = "unknown"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.post(url, json=payload)

            if response.status_code == httpx.codes.OK:
                body = response.json()
                result = body.get("result", {})
                log.info("telegram_sent", chat_id=chat_id, attempt=attempt)
                return SendResult(
                    message_id=int(result.get("message_id", 0)),
                    chat_id=int(result.get("chat", {}).get("id", chat_id)),
                )

            detail = _redact(response.text[:300], bot_token)

            if 400 <= response.status_code < 500:
                # Permanent. Surface it immediately with Telegram's own description,
                # which is usually actionable ("chat not found", "bot was blocked").
                log.warning(
                    "telegram_rejected", status=response.status_code, detail=detail, chat_id=chat_id
                )
                msg = f"Telegram rejected the message (HTTP {response.status_code}): {detail}"
                raise TelegramError(msg, permanent=True)

            last_error = f"HTTP {response.status_code}: {detail}"

        except httpx.HTTPError as exc:
            last_error = _redact(f"{type(exc).__name__}: {exc}", bot_token)

        if attempt < MAX_ATTEMPTS:
            await asyncio.sleep(BACKOFF_BASE * (2 ** (attempt - 1)))

    log.error("telegram_failed", chat_id=chat_id, attempts=MAX_ATTEMPTS, detail=last_error)
    msg = f"Telegram delivery failed after {MAX_ATTEMPTS} attempts: {last_error}"
    raise TelegramError(msg)


async def get_me(*, bot_token: str) -> str:
    """Return the bot username. Used to verify the token is live."""
    url = f"{API_BASE}/bot{bot_token}/getMe"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        msg = _redact(f"Could not reach Telegram: {type(exc).__name__}: {exc}", bot_token)
        raise TelegramError(msg) from exc

    if response.status_code != httpx.codes.OK:
        msg = f"Telegram getMe failed (HTTP {response.status_code})"
        raise TelegramError(msg)
    return str(response.json().get("result", {}).get("username", "unknown"))


# ---------------------------------------------------------------------------
# Message bodies
# ---------------------------------------------------------------------------


def password_reset_message(*, display_name: str, url: str, minutes_valid: int) -> str:
    """The reset link.

    States that TOTP is still required, because an admin who reads "reset your
    password" and cannot find their authenticator needs to know a recovery code is
    the path -- not to assume the link alone restores access.
    """
    return (
        f"<b>Tracelet — password reset</b>\n\n"
        f"Hello {display_name},\n\n"
        f"A password reset was requested for your Tracelet admin account.\n\n"
        f'<a href="{url}">Set a new password</a>\n\n'
        f"This link works once and expires in {minutes_valid} minutes.\n\n"
        f"You will still need your authenticator code to sign in. If you have lost "
        f"it, use one of your recovery codes.\n\n"
        f"<i>If you did not request this, ignore the message — nothing has changed. "
        f"Your existing password still works.</i>"
    )


def chat_verification_message(*, display_name: str, code: str) -> str:
    return (
        f"<b>Tracelet — verify this chat</b>\n\n"
        f"Hello {display_name},\n\n"
        f"Enter this code in Tracelet to confirm this chat as your recovery channel:\n\n"
        f"<code>{code}</code>\n\n"
        f"Once verified, password reset links will be delivered here."
    )


def test_message() -> str:
    return (
        "<b>Tracelet — test message</b>\n\n"
        "If you can read this, the bot token and chat ID are correct and "
        "notifications will reach you."
    )
