# ADR-0022 — Backups run from the API; restore-verification uses a scratch database

**Status:** Accepted (2026-10-07). The owner chose the scratch database.
**Deciders:** repository owner
**Amends:** ADR-0014 (restore verification), F12.AC10 (SPEC §11 row 22); DATA_MODEL §12
**Relates to:** ADR-0009 (scheduler), ADR-0012 (one VM), ARCHITECTURE §6.3, RISKS R11

---

## Context

ADR-0014 decided what is backed up, how often, and that the newest backup is restored once a
month and checked. It left three things open, and got one wrong:

1. **Where `pg_dump` runs.** The API image has no PostgreSQL client, the `db` container has no
   scheduler, and no container may reach the Docker socket.
2. **What "asserts row counts" compares against.** Counts taken a moment after the dump differ
   from the dump whenever a visit arrives in between, so a check against live counts either
   fails at random or has to tolerate a difference -- and then cannot see a short restore.
3. **How the maintenance role connects.** `tracelet_maint` exists (DATA_MODEL §12) but nothing
   connects as it.
4. **"A scratch schema on the live instance" does not work.** Spiked on 2026-10-07 against the
   dev database: `pg_restore` re-creates every object in the schema it was dumped from
   (`public`), and PostGIS, which owns the `geography` type, can be installed only once per
   database. Retargeting a dump into another schema means rewriting its SQL, which is exactly
   the kind of fragile step that makes a restore fail when it matters. Restoring the same dump
   into a **separate database on the same server** worked first time: 1 s to dump (1.7 MB),
   3 s to restore, every table's row count equal.

## Decision

1. **Backups run as subprocesses of the API.** The advisory-locked scheduler (ADR-0009) runs
   the nightly backup and the monthly restore-verification; the manual "Back up now" and
   "Verify now" controls queue the same jobs. `pg_dump` and `pg_restore` come from the
   **PostgreSQL 16 client** in PostgreSQL's own apt repository, matching the server's major
   version. Debian 13's packaged client is 17, whose `pg_restore` emits settings a 16 server
   rejects (`SET transaction_timeout`).
2. **They connect as `tracelet_maint`**, through a new `TRACELET_MAINT_DATABASE_URL`, set in
   `docker-compose.yml` from `TRACELET_DB_MAINT_PASSWORD` the way the migration URL is. The
   application role keeps its narrower grants.
3. **Exact counts, from the same snapshot.** The backup job opens a repeatable-read
   transaction, exports its snapshot, counts every table in it, and runs `pg_dump
   --snapshot=<that snapshot>`. The counts are therefore those of the dump itself, stored with
   the backup, and the restore check requires **equality, table by table** -- no tolerance.
4. **Restore-verification restores into a scratch database**, `tracelet_verify`, on the same
   PostgreSQL server, created from a template database `tracelet_verify_template` that already
   has PostGIS and citext. The dump's extension entries are left out of the restore (they
   already exist, and commenting on an extension needs its owner). Counts are compared, and the
   scratch database is dropped whether the check passed or not; a scratch database left by a
   crash is dropped before the next run.
5. **Two one-time superuser steps**, because PostGIS is not a trusted extension and
   `tracelet_maint` must not be a superuser: `tracelet_maint` gets `CREATEDB`, and the template
   database is created with the extensions installed. Both are in `db/init/` for a new volume
   and in `./scripts/tl db-setup`, idempotent, for an existing one.
6. **Files** live in the `backups` volume as `<UTC timestamp>-<scheduled|manual>.dump`
   (custom format, compression level 6), described by a `backups` row: status, size, SHA-256,
   the counts, and the restore results. **Rotation keeps the newest successful backup of each
   of the last 7 days and of each of the last 4 ISO weeks**, and always the newest; other files
   are deleted and their rows marked `pruned`, so the history stays.

## Alternatives considered

| Option | Why rejected |
|---|---|
| A scratch schema, by rewriting the dump's SQL | Fragile with extension-owned types, and it would test the rewrite rather than the dump |
| A second PostgreSQL server for the check | Does not fit the 1 GB box (ADR-0014 already rejected it) |
| A backup container (a `postgres:16` image running cron) | A long-lived process and a second scheduler, for a job the existing scheduler can run; costs idle memory |
| `docker exec` into the `db` container | Needs the Docker socket inside the API: root on the host |
| Debian's client 17 | Version skew; the restore emits errors on a 16 server, and a check that tolerates errors proves less |
| Counting live tables after the dump | Not the dump's own counts; a race makes the check flaky or loose |

## Consequences

**Positive:** the check is exact; no new long-lived process; the maintenance role is finally
used for what DATA_MODEL §12 gave it; a new VM gets the setup from `db/init/`.

**Negative, accepted:**
- **About 4 MB more in the API image**: only `pg_dump` and `pg_restore` are copied from a build
  stage, linking the image's own `libpq5`. Installing the package whole adds ~120 MB, nearly
  all Perl for its wrapper scripts (measured 2026-10-07). And a transient `pg_dump` or
  `pg_restore` process of roughly 40 MB inside the API's memory limit while a job runs
  (ARCHITECTURE §6.3), off-peak.
- **The scratch database briefly doubles the database's disk use** during a check. The check
  refuses to start when free disk is below the database's size plus a margin, and says so.
- **Existing volumes need `tl db-setup` once**, run as the PostgreSQL superuser. Without it,
  restore-verification fails with a reason naming the step.
- As ADR-0014 said, this proves the dump restores completely; the bare-metal rebuild is the
  M9 drill.
