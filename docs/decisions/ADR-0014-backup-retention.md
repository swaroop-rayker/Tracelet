# ADR-0014 — Backup, restore verification, and retention

**Status:** Accepted (Gate 1 and Gate 2, 2026-09-25)
**Deciders:** repository owner
**Relates to:** F10.AC11, F10.AC12, F12.AC7–F12.AC12, NFR5, C6

---

## Context

The brief required a managed backup system and a retention system based on time period
("data lifecycle"), and asked explicitly what must never be lost.

Constraints:

- **30 GB of disk**, shared with the operating system, Docker images, the database, geo
  databases and backups (ARCHITECTURE section 7).
- **At Gate 1 the owner declined a cloud-storage account.** That removes every automated
  off-VM backup destination — R2, S3, Google Drive, Backblaze — because each requires a
  signup.
- **A single VM with no failover** (ADR-0012).
- IP ciphertext has its own separate 30-day clock (ADR-0007), independent of the visit
  retention clock.

## Decision

### Backup

| Aspect | Specification |
|---|---|
| Method | `pg_dump`, custom format, compressed |
| Schedule | Nightly, off-peak |
| Rotation | 7 daily + 4 weekly |
| Integrity | SHA-256 manifest per backup, recorded in the `backups` table |
| Location | Local disk, plus **manual download from the dashboard** |
| Contents | Full database — reference data, config, visits, audit log. **IP values are ciphertext**, so the backup is safe to download and store (ADR-0007) |
| **Not in the backup** | **The IP encryption key and the three HMAC peppers.** Deliberately |

### Restore verification — monthly, automated

The newest backup is restored into a **scratch schema** on the live instance and row counts
are asserted against expectations. Result and timestamp are surfaced on the System Health
page (F12.AC10).

**A backup that has never been restored is not a backup.** It is a file that is believed to
be a backup. The most common backup failure is not the backup job — it is discovering at
restore time that the dump was truncated, the schema drifted, or an extension was missing.
Automating the restore converts that from a discovery under pressure into a monthly
assertion.

Restoring into a scratch schema rather than a separate instance is a memory-budget
compromise: a second PostgreSQL would not fit. The trade-off is that this verifies the dump
is **restorable and complete**, not that a bare-metal rebuild works. That second property is
covered by the M9 restore drill, done once, by hand, against a fresh VM.

### Retention

| Category | Default | Purge behaviour |
|---|---|---|
| `visits.ip_enc` | **30 days** | Column set to `NULL`; `ip_hmac` and `ip_prefix` persist |
| `visits` + `visit_candidates` | **180 days** | Batched delete, cascade to candidates |
| `audit_log` | **365 days** | Deleted by the `tracelet_maint` role, since the app role cannot delete |
| `sessions`, `rate_limit_buckets`, `geo_cache` | Expiry-driven | Continuous cleanup |
| `outbox` done rows | 30 days | `dead` rows retained until acknowledged |
| **`rollup_*`** | **Indefinite** | **Never purged** |
| Reference and config | **Indefinite** | Never purged, always backed up |

Purges are transactional, batched to avoid long locks, **dry-runnable with exact counts
before execution**, and audit-logged with the counts actually deleted (F12.AC8).

**Rollups being permanent is the load-bearing design choice.** Raw rows expire at 180 days,
but nightly aggregates are kilobytes and are kept forever — so long-term trend analysis
survives the purge (NFR5.AC4). The retention policy therefore costs detail, not history.

### Must never be lost (NFR5.AC3, F12.AC12)

`admins`, `admin_recovery_codes`, `links`, `geofences`, `inference_settings`,
`retention_policy`, `app_settings`, `audit_log`, `rollup_*`.

All small, all slow-changing, all in every backup. The unifying property: **none of them can
be reconstructed from anything else.** A lost visit row is lost data; a lost `geofences` row
is lost configuration the owner would have to redraw from memory.

### The gap, stated plainly

**Off-VM copies are manual.** If the VM is lost, everything since the last manual download
is gone. This is a direct consequence of the Gate 1 decision to add no cloud-storage
account, is accepted, and is recorded as RISKS R11. The mitigation is a dashboard reminder
when the last download exceeds a configurable age — a nag, not a solution.

## Alternatives considered

| Option | Why rejected |
|---|---|
| **Automated off-VM backup (Cloudflare R2, Backblaze B2, Google Drive)** | The correct answer. All have genuinely free tiers adequate for roughly 200 MB of compressed dumps. **Declined at Gate 1** to avoid another account signup. The integration is small and remains the highest-value single improvement available to this ADR. |
| **Continuous archiving with `pg_basebackup` + WAL** | Point-in-time recovery, which is strictly better than nightly dumps. Costs continuous WAL disk, more moving parts, and a restore procedure that is harder to test. Disproportionate for 500 visits a day where losing a day is tolerable. |
| **Litestream-style continuous replication** | Designed for SQLite; not applicable to PostgreSQL, and the PostgreSQL equivalents need a remote destination we declined. |
| **A logical-replication standby** | Requires a second host or a second PostgreSQL instance. Does not fit the memory or the budget. |
| **Filesystem snapshots (GCP disk snapshots)** | Genuinely good, and **not free** beyond a small allowance; snapshot storage bills separately. Fails C6. Worth revisiting if the budget ever changes, because it would cover the VM-loss case that manual download does not. |
| **No restore verification** | What most small projects do, and how backups are discovered to be broken. The cost here is one scheduled job. |
| **Purging rollups with raw data** | Simpler retention story, one clock instead of two. Would destroy all history beyond 180 days for kilobytes of savings. |
| **Keeping visits indefinitely** | No purge complexity at all. Conflicts with the data-minimisation posture of RW-3 and OOS4, and grows the largest table without bound. |

## Consequences

**Positive**

- Nightly backups with integrity manifests and a rotation that bounds disk use.
- Monthly automated restore verification, so a broken dump is found on a schedule rather
  than in an emergency.
- Retention is configurable per category, with a dry run that prevents an irreversible
  mistake.
- Permanent rollups mean retention costs detail, not history.
- Backups are safe to download and store, because IP values are ciphertext.
- The audit log can only be purged by a separate role, so an application-path compromise
  cannot erase its own traces.

**Negative, and accepted**

- **VM loss loses everything since the last manual download.** The single largest
  operational risk in the system (RISKS R11).
- **The IP key and HMAC peppers are not in the backup.** This is correct — including them
  would defeat ADR-0007 — and it means **restoring onto a fresh VM without those secrets
  leaves every `ip_enc` value permanently unreadable and breaks all historical
  `visitor_id` linkage.** Backing up those secrets out-of-band is a mandatory, first-class
  step in the operations runbook, not a footnote.
- **Restore verification uses a scratch schema, not a separate instance**, so it does not
  prove a bare-metal rebuild works. That is the M9 one-time drill.
- **Two retention clocks** (30-day IP, 180-day visit) means two purge paths and two sets of
  tests.
- **`pg_dump` holds a consistent snapshot**, which on a 1 vCPU box during a traffic spike
  could contend. Scheduled off-peak, and roughly 40 MB of transient memory is budgeted for
  it (ARCHITECTURE §6.3).

## Revisit if

- The owner reconsiders a cloud-storage account — **this is the single highest-value change
  available here**, and it directly retires RISKS R11.
- Database size approaches roughly 5 GB, at which point `pg_dump` duration and the disk
  budget both need re-examining.
- Point-in-time recovery becomes worth its complexity, which would mean the tolerance for
  losing a day of data has changed.
