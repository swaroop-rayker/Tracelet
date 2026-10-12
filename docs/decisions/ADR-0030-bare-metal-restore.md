# ADR-0030 — Bare-metal restore: `tl restore`, as the superuser, then adopt the backup

**Status:** Accepted (2026-10-11). The owner chose the superuser restore with adoption.
**Deciders:** repository owner
**Amends:** ADR-0014 (restore procedure), ADR-0022 (the bare-metal rebuild it left to M9)
**Relates to:** ADR-0007 (the key outside the backup), ADR-0026 (release host), DATA_MODEL
§8.8 and §12, NFR5.AC5, RISKS R11, MILESTONES M9 (the restore drill)

---

## Context

ADR-0022 proves every night's dump restores completely, into a scratch database, as
`tracelet_maint`, without owners or privileges. It said the bare-metal rebuild -- a new VM,
an empty database volume, a downloaded backup and the owner's off-VM `.env` and `ip_key` --
was M9's drill. Nothing did that yet. Three things decide how it must work, each found by a
spike on 2026-10-11: the development database dumped exactly as the backup job dumps it, then
restored into a fresh `postgis/postgis:16-3.4-alpine` container whose empty volume ran the
real `db/init/`, and compared object by object.

1. **Privileges are part of the data.** `audit_log` is append-only because `tracelet_app` has
   only `SELECT, INSERT` on it (NFR5.AC5), and other tables have narrower grants than the
   default. `db/init` also sets *default* privileges for `tracelet_migrate`, which give
   `tracelet_app` full read-write on every table that role creates. **Restoring as
   `tracelet_migrate` (`--role`, `--no-owner`) silently gave `tracelet_app` UPDATE and DELETE
   on `audit_log`** -- and on `accuracy_runs` and `inference_settings` -- because the
   default privileges applied as each table was created and the dump's own grants only add.
   No error; the restore looked perfect.
2. **Restoring as the superuser, keeping owners and privileges, reproduced the source
   exactly:** every relation's owner and ACL, the schema's ACL, the default privileges, the
   extensions, and all 33 tables' row counts equal. A superuser-created table does not take
   `tracelet_migrate`'s default privileges; the dump then sets its owner and its grants.
3. **The dump's own history is half-finished.** `backups` is dumped inside the backup job, so
   the dump holds its own row as `running`, with no file, size, checksum or counts; the
   restored system would mark it "Abandoned" two hours later. Older `ok` rows name files that
   stayed on the old volume. The restored history would therefore offer downloads that fail
   and lose the one backup the system was rebuilt from.

## Decision

**`./scripts/tl restore <file>` restores a downloaded backup into a new, empty database
volume, before `tl deploy`. Then `tracelet backup adopt` makes the restored history true.**

1. **Preconditions, checked:** a `.env`; the api is not running; the database has **no table
   of its own** in `public` (it refuses otherwise and says that only a new volume is
   restored into -- it never overwrites data); the file exists. On a release host it pins
   `TRACELET_IMAGE_TAG` to the checked-out commit and pulls the api image, as `tl deploy`
   does, because the adoption runs in it.
2. **The restore runs in the `db` container as the PostgreSQL superuser**, over the local
   socket (no password leaves `.env`): `pg_restore --exit-on-error --single-transaction`,
   **keeping owners and privileges**. All or nothing. `alembic_version` comes with the dump,
   so `tl deploy`'s migration step afterwards applies only migrations newer than the backup.
   The roles, their passwords and `pg_read_all_stats` are cluster objects the dump does not
   hold; `db/init` made them from the restored `.env` when the volume was created.
3. **Adoption, in the api image as `tracelet backup adopt <file>`.** The file must be named
   as the backup job names it (`<UTC stamp>-<kind>.dump`, with or without the download's
   `tracelet-` prefix), and the database must hold **exactly one** `running` backup whose
   kind matches and which started at most two hours before the stamp: the dump's own row. It
   then: copies the file into the `backups` volume (`.partial`, then renamed); **counts every
   table before writing anything** -- the database is untouched since the restore, so these
   are the dump's own counts; marks that row `ok` with the file's name, size, SHA-256 and those
   counts (`finished_at` is the adoption time); marks every other `ok` row whose file is not
   on this volume `pruned` (its file is gone, which is what `pruned` means; rows are never
   deleted); and writes one `backup.adopted` audit row. The counts come first so the audit row
   is not among them.
4. **The rebuilt system checks itself:** "Verify now" restores the adopted file into the
   scratch database and compares it with the adopted counts (ADR-0022). Equality proves the
   bare-metal restore lost nothing.
5. **What proves the secrets**, recorded by the drill: every `ip_enc` decrypts with the
   restored key and lies inside its row's stored `ip_prefix` (the AAD is the visit id, so a
   wrong key or a moved row fails authentication); the owner signs in with their TOTP, whose
   secret is sealed with the same key; a Telegram alert arrives.

## Alternatives considered

| Option | Why rejected |
|---|---|
| Restore as `tracelet_migrate` (`--role`, `--no-owner`) | Measured: `db/init`'s default privileges widen `tracelet_app` to UPDATE/DELETE on `audit_log`; silent |
| ADR-0022's restore (`--no-owner --no-privileges`) | Fine for counting rows in a throwaway database; on production it drops every narrowed grant |
| Superuser restore after clearing the default privileges first | Works, but adds a step whose omission is silent; the superuser restore needs none |
| `tl deploy` first, then restore over the migrated schema | Every table already exists; `--clean` would drop and re-create under a different owner -- the same privilege question, plus partial states |
| Leave the history as restored | Downloads that fail, the restored-from backup marked "Abandoned", nothing on the new box to verify |
| Recreate the dump's row from the live system's values | The live system is gone by definition; the file and the restored database are what exist |

## Consequences

**Positive:** one command restores; privileges are exactly the source's; the rebuilt box
holds and can verify the backup it came from; refusing a non-empty database makes the command
safe to type on a live host.

**Negative, accepted:**
- **Only into a new volume.** Restoring over a running system is a wipe first
  (`docker compose down -v`), deliberately: there is no partial restore.
- **The superuser restores.** It runs only by hand, over the container's local socket, from a
  file the owner downloaded from their own system.
- **`finished_at` of the adopted backup is the adoption time**, not the original finish: the
  original was never in the dump.
- **Geo databases, Let's Encrypt certificates and Caddy's fallback pages are not in the
  dump.** They come back as on a first deploy: the scheduler (or `tracelet geodb update`)
  installs any missing database, Caddy issues a certificate, the api writes the pages.
