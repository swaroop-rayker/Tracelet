# ADR-0007 — IP address at rest: AES-256-GCM with a TTL purge

**Status:** Accepted (Gate 1, 2026-09-25)
**Deciders:** repository owner
**Relates to:** F12.AC1–F12.AC5, RW-3, OOS4, NFR5

---

## Context

The brief stated that when a visitor denies geolocation permission, the data should be
"restricted to city-level granularity and no area or IP". It did not say what to do with
the IP on consented visits, and it did not say whether the IP could be retained at all.

Three options were put to the owner at Gate 1:

1. Never persist a raw IP for any visit.
2. Persist it only when geolocation consent was granted.
3. Encrypt at rest with a short TTL and auditable decryption.

**The owner chose option 3**, with the reasoning stated as: persist it encrypted, delete
it after a period, and ensure that in the event of a leak the values are unreadable.

The competing pressures are concrete. Without any retained address, a wrong geolocation
result cannot be re-examined after the fact — and re-examining wrong results is exactly
the work of M8 accuracy tuning. With a plaintext address, a stolen `pg_dump` exposes every
visitor network identity.

## Decision

**AES-256-GCM envelope encryption with a 30-day TTL and owner-only auditable decryption.**

| Aspect | Specification |
|---|---|
| Algorithm | AES-256-GCM, 96-bit random nonce per row |
| Stored form | `nonce ‖ ciphertext ‖ tag` in `visits.ip_enc bytea` |
| Additional authenticated data | The `visits.id`, so a ciphertext cannot be moved between rows undetected |
| Key location | A `0400` file **outside the database volume**, loaded into process memory at boot |
| Key in database | **Never.** Not in a table, not in a setting, not in a log, not in any API response |
| TTL | 30 days, configurable via `retention_policy.ip_days` |
| Purge | Sets `ip_enc = NULL`; `ip_hmac` and `ip_prefix` **persist indefinitely** |
| Rotation | `ip_key_version smallint` column plus a re-encryption job |
| Decryption | `owner` role only, rate-limited to 10/hr, **writes an `audit_log` row naming actor and visit** |
| Never encrypted, always durable | `HMAC(ip)`, the /24 or /48 prefix, the ASN |

**Why the durable fields survive the purge.** Analytics, rate-limit correlation, NAT
detection and visitor identity all depend on `ip_hmac` and `ip_prefix`. If those were
purged with the ciphertext, every chart would develop a hole at the 30-day mark. Keeping
them means the purge removes the ability to identify a *specific address* while preserving
the ability to reason about *networks*.

**Why there is no plaintext IP column at all.** Not "usually empty" — the column does not
exist in the schema (`DATA_MODEL.md` section 5.1). A column that could hold a plaintext
address is a column that eventually will.

## Threat model — what this does and does not defend

This is the part that must not be overstated.

| Threat | Defended? | Why |
|---|---|---|
| Stolen or leaked `pg_dump` | **Yes** | Backups contain ciphertext only. The key is not in the dump |
| Backup file exfiltrated from disk or from a manual download | **Yes** | Same reason |
| SQL injection reaching the data tables | **Yes** | Returns ciphertext; the key is in process memory, not reachable via SQL |
| Database credentials compromised | **Yes** | Same reason |
| A curious or malicious admin | **Partially** | Decryption is `owner`-only, rate-limited, and audit-logged. An owner can decrypt — but cannot do so invisibly |
| **Root compromise of the VM** | **No** | The attacker reads the key file and the process memory. Free tier means no KMS and no HSM; the key has to live somewhere on the same machine |
| A compromised application process | **No** | The key is in its memory by design |

**The honest summary: this defends data at rest, not a compromised host.** That limit is
recorded in `KICKOFF.md` section 7 and in `docs/private/05-SECURITY-MODEL.md` so it is
never implied to be stronger than it is.

## Alternatives considered

| Option | Why rejected |
|---|---|
| **Never persist the raw IP** (Gate 1 option 1) | Strongest privacy posture and my original recommendation. Rejected by the owner because it makes re-running inference on a historical visit impossible, which is directly useful during M8 accuracy tuning. Trade accepted: a 30-day window of recoverable addresses in exchange for the ability to diagnose a wrong result. |
| **Persist plaintext only on consented visits** (option 2) | A literal reading of the brief. Rejected: it makes the consent grant carry far more than the visitor would expect — they consented to share their location, not to have their network address retained in the clear. |
| **`pgcrypto`, encrypting inside the database** | Convenient, and wrong for this threat model: the key would pass through SQL, appear in query parameters, and potentially reach `pg_stat_statements` or the query log. Encrypting in the application keeps the key out of the database entirely. |
| **Full-disk encryption only** | Protects a stolen physical disk, which is not a realistic threat for a cloud VM. Provides nothing against a leaked dump, which is the realistic one. |
| **A cloud KMS** | The correct answer with a budget. Fails C6, free tier only. |
| **Storing only a truncated address** | Loses the ability to re-run inference, which is the entire reason the owner chose retention. |

## Consequences

**Positive**

- A leaked dump, backup, or injection foothold yields opaque bytes.
- Inference can be re-run against a historical visit for up to 30 days, which is what
  makes accuracy tuning empirical rather than speculative.
- Every decryption is attributable. "Who looked at this address, and when" is answerable.
- Analytics continuity survives the purge, because the durable fields are separate.
- AAD binding means a ciphertext cannot be silently transplanted between rows.

**Negative, and accepted**

- **Root compromise defeats it.** Stated plainly, in three documents, so it is never
  oversold.
- **The key must be backed up out-of-band.** It is deliberately not in the database backup
  — which means restoring a backup onto a fresh VM **without** the key file leaves every
  `ip_enc` value permanently unreadable. This belongs in the operational runbook as a
  first-class step, not a footnote.
- **Key rotation is a real job**, re-encrypting every non-purged row. Low volume makes it
  cheap, but it needs implementing and testing rather than assuming.
- **A second retention clock** (30-day IP, 180-day visit) means two purge paths and two
  sets of tests.
- `GET /api/v1/visits/{id}/ip` returning `410 IP_PURGED` is a normal outcome, not an
  error. The UI must present it that way or it will read as a fault.

## Revisit if

- The deployment moves somewhere a managed KMS is free, which would remove the
  same-host-key limitation.
- M8 tuning concludes early and the 30-day window is no longer needed, in which case
  shortening the TTL is a pure privacy gain with no cost.
