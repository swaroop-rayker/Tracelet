# ADR-0008 — Admin auth: opaque server-side sessions, mandatory TOTP, Telegram recovery

**Status:** Accepted (Gate 1 and Gate 2, 2026-09-25)
**Deciders:** repository owner
**Relates to:** F8, F10.AC9, F13.AC7, C6, NFR6

---

## Context

The dashboard holds visitor telemetry: networks, devices, inferred locations, and — for
up to 30 days — decryptable IP addresses. It is the most sensitive surface in the system,
and the brief called for "fail proof and protected", "secure cookies", and
"production-grade password recovery systems".

Three constraints shaped the design:

1. **There are 1 to 2 admins and 2 concurrent sessions** (NFR1.AC3). This is not a
   multi-tenant system, and stateless scalability has no value here.
2. **Gate 1 declined an email or SMTP provider.** Every conventional password-reset flow
   assumes email. Without it, recovery needs a different channel.
3. **The box has 1 GB of RAM** (NFR6), and Argon2 is deliberately memory-hard — a naive
   configuration is a denial-of-service vector against ourselves.

## Decision

### Sessions: opaque, server-side, revocable

| Aspect | Specification |
|---|---|
| Token | 256-bit cryptographically random, opaque |
| Storage | **SHA-256 hash** in `sessions.token_hash`. The token itself is never stored |
| Cookie | `__Host-tracelet_session`; `HttpOnly; Secure; SameSite=Strict; Path=/` |
| Expiry | Absolute expiry **and** a sliding idle timeout |
| Binding | IP prefix and a UA hash |
| Revocation | Immediate, per session, listable by its owner |
| CSRF | Double-submit token from `sessions.csrf_secret` **plus** `Origin` validation |

`SameSite=Strict` rather than `Lax`: there are no legitimate cross-site entry points into
this dashboard, so `Strict` is both stronger and sufficient. If a flow ever breaks under
it, `Lax` plus the CSRF token is the documented fallback — not the default.

### Passwords: Argon2id, pinned

`m=32MiB, t=3, p=1`.

**This pinning is a hard requirement, not a tuning preference.** Several Argon2 wrappers
default to 64 MiB, and some guidance recommends up to 1 GiB. On a 1 GB box with 210 MB
committed to PostgreSQL, a single login at a 1 GiB memory cost would invoke the OOM
killer. Two concurrent logins at 32 MiB is 64 MB transient, which fits the headroom.
Logins are additionally serialised by rate limiting. This is recorded in
`CLAUDE.md` section 5 because it is the kind of default that gets "helpfully" restored.

### Second factor: TOTP, mandatory

Enrolled at first login. **An account cannot reach any dashboard route before enrolment
completes** — enforced by the `admins` invariant that `status='active'` requires
`totp_enrolled_at IS NOT NULL`. The last accepted counter is stored, so a code cannot be
replayed within its window.

### Recovery: three independent paths, no email

1. **10 single-use recovery codes**, Argon2-hashed, displayed exactly once at enrolment,
   with a warning below three remaining.
2. **A Telegram-delivered reset link** to the verified owner chat: single-use,
   time-limited, signed.
3. **A break-glass CLI** — `tracelet admin reset-password` — requiring database and shell
   access, always audit-logged.

Three paths because each fails differently: codes can be lost, a Telegram account can be
locked out, and the CLI needs server access. For a solo-admin system with no password-reset
help desk, a single recovery channel is a single point of total lockout.

### Enumeration and timing resistance

Login, password-reset-request and recovery-code endpoints return **identical** bodies and
take indistinguishable time for existing and non-existing accounts, including a **dummy
Argon2 verification** on unknown identifiers so the timing signal does not leak account
existence. `/auth/reset/request` returns `202` unconditionally.

### Bootstrap

The first owner is created by `make bootstrap`, which prints a **one-time enrollment
token**. **No default password exists anywhere in the system** — not in a seed file, not
in `.env.example`, not in documentation.

## Alternatives considered

| Option | Why rejected |
|---|---|
| **JWT access tokens** | The fashionable default, and wrong here. A JWT **cannot be revoked** before expiry without a server-side denylist — at which point you have server-side session state anyway, with worse ergonomics and key-rotation complexity added. Statelessness buys horizontal scalability, which is worth nothing for two concurrent admins on one VM. |
| **JWT plus a refresh-token denylist** | All of the above complexity, to arrive at the properties opaque sessions have natively. |
| **bcrypt** | Well understood, but not memory-hard, so it is materially weaker against GPU attack. Argon2id is the current recommendation. |
| **scrypt** | Acceptable, but Argon2id has better tooling and clearer parameter guidance. |
| **Email-based password reset** | The universal default. **Fails C6 as scoped at Gate 1** — no SMTP provider. Telegram is already a required dependency (F7), so reusing it for recovery removes an external service rather than adding one. |
| **WebAuthn / passkeys** | Stronger than TOTP and genuinely tempting. Rejected for v1: a single-device passkey with no recovery channel is a lockout risk, and doing it properly needs multiple registered authenticators, which is disproportionate for one admin. Recorded as a future improvement. |
| **Optional TOTP** | Would leave a password as the only barrier to decryptable IP addresses and full visitor history. Not defensible for this data. |

## Consequences

**Positive**

- Instant revocation of any session, which JWT structurally cannot offer.
- No token-signing key to rotate, and no key-compromise blast radius.
- Session binding plus `SameSite=Strict` plus CSRF double-submit plus `Origin` validation
  closes the common session-hijacking paths.
- Recovery needs no external service beyond Telegram, which is already required.
- Argon2 pinned to a value that cannot OOM the host.
- Three recovery paths, so no single loss causes permanent lockout.

**Negative, and accepted**

- **A database round-trip per authenticated request.** Negligible at this volume, and the
  session lookup is a single indexed read.
- **All of this is hand-rolled** (ADR-0001 consequence). Sessions, CSRF, TOTP, recovery
  codes and lockout are code we own and must test. Mitigated by using audited primitives
  and covering each with unit tests.
- **Recovery codes are shown exactly once.** If the owner does not save them and also
  loses Telegram access, only the CLI remains — and if there is no shell access, the
  account is unrecoverable. This is the correct security property and a real operational
  hazard; it is documented in `KICKOFF.md` section 4 and in the runbook.
- **IP-prefix binding breaks a session on a network change.** Mobile admins will be logged
  out when switching from Wi-Fi to cellular. Accepted deliberately: for two admins, an
  occasional re-login is a small cost for invalidating a stolen cookie used from elsewhere.
- **Telegram becomes security-critical**, not merely a notification channel. Compromise of
  the owner Telegram account enables a password reset. This raises the value of TOTP,
  which the reset flow does **not** bypass.

## Revisit if

- WebAuthn with multiple registered authenticators becomes worth the complexity — the
  right trigger is a second regular admin, not a solo owner.
- Session binding proves too disruptive in practice, in which case bind on the UA hash
  alone and record the reduction.
