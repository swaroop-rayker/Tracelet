# Tracelet — operations runbook

How to run production. Every procedure here has been done on the real box; the date says
when. Commands are copy-paste ready. Values that are secret or private (tokens, the chat ID,
passwords, the VM's address) never appear here: they live in the owner's password manager.

Production, today: one GCP e2-micro (Debian 12, 1 vCPU, 1 GB RAM, 2 GB swap, 30 GB disk),
the checkout at **`/opt/tracelet`** (owner `swaro`, group `docker`, mode 2775), on the free
subdomain **https://tracelet.duckdns.org** (ADR-0012 degraded mode; M9.1 moves it behind
Cloudflare). Images come from Docker Hub, `swarooprayker/tracelet-api` and
`swarooprayker/tracelet-caddy`, tagged with the 12-character commit (ADR-0026).

Reach the VM from the owner's PC:

```bash
gcloud compute ssh <instance> --zone <zone>
```

On the VM, **every compose command uses the production override** without a flag:
`.env` sets `COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml`.

---

## 1. What lives where

| Thing | Where | Notes |
|---|---|---|
| Configuration and secrets | `/opt/tracelet/.env` | `swaro:docker`, mode 0600. A copy is in the owner's password manager |
| IP and TOTP encryption key | `/opt/tracelet/secrets/ip_key` | uid 10001 (the api's user), mode 0400, 64 hex characters. A copy is in the password manager. **Not in any backup** (ADR-0007) |
| Database, backups, geo databases, certificates, fallback pages | Docker volumes `tracelet_*` | `docker compose down -v` deletes them all |
| What was deployed when | `/opt/tracelet/.deploy/history` | Time, tag, outcome, and the image digests that actually ran |
| DuckDNS token | `/etc/tracelet/duckdns.curl` | root, 0600, a curl config |
| Host baseline | `/etc/...` files from `deploy/host/` | §7 |

**The password-manager note** holds `.env` and `ip_key`, nothing else. Rules learned in the
restore drill (ERRORS E84):

- Store the file's lines exactly: no heading line inside the `.env` text.
- **Re-copy `.env` into the note after any change on the VM** -- a tuned value, a rotated
  token. On 2026-10-11 the note held two values from before the load-test tuning.
- `ip_key` is its own entry: 64 characters, `0-9a-f`, one line.

---

## 2. Regular duties

| When | What | Why |
|---|---|---|
| Weekly (the dashboard reminds after 7 days) | System Health → Backups → **Download** the newest backup to the PC | The only off-VM copy (F12.AC11, RISKS R11). A backup holds IPs only as ciphertext, so the file is safe to keep anywhere |
| When the banner shows | System Health: read the degradation banner | Stale geo database, a tripped breaker, a dead-lettered alert, low disk, a failed backup (F10.AC14) |
| After any `.env` change | Update the password-manager note | §1 |
| Monthly, automatic | Restore check of the newest backup into a scratch database | ADR-0022; the result is on System Health |

The box runs these itself (times in `TRACELET_REPORTING_TZ`, Asia/Kolkata):

| Time | Job | Setting |
|---|---|---|
| 02:00 | ASN profile rebuild, if a geo database changed (it slows every request for minutes, so only in the quiet hour; SPEC §11 row 34) | `TRACELET_GEODB_PROFILES_HOUR` |
| 03:00 | Nightly backup, then rotation (7 daily, 4 weekly) | `TRACELET_BACKUP_HOUR` |
| 1st of the month, after the backup | Restore check | ADR-0022 |
| Continuously | Geo database updates when due; a missing one is installed at once | F10.AC3 |
| Every 5 minutes | DuckDNS re-pointed at the VM's address | §8 |

---

## 3. Release and deploy (routine)

On the **owner's PC**, from a clean commit (the release is exactly that commit):

```bash
./scripts/tl verify
./scripts/tl release --push
```

`tl release` builds both images from a `git archive` of `HEAD`, refuses a dirty tree,
checks that neither image carries a local trust root, and pushes. It prints the commit to
deploy.

On the **VM**:

```bash
cd /opt/tracelet && git fetch && git checkout <full commit> && ./scripts/tl deploy
```

`tl deploy` checks the host baseline (warns, never blocks), pins `TRACELET_IMAGE_TAG` in
`.env`, pulls, starts the database, migrates **with the new image**, starts everything, and
waits up to 120 s for the api to be healthy. It records the outcome and the image digests in
`.deploy/history`. Run it detached when the ssh session may drop:

```bash
nohup sh -c './scripts/tl deploy; echo TL_EXIT=$?' > ~/deploy.log 2>&1 &
```

Then, from the PC:

```bash
./scripts/tl live-check https://tracelet.duckdns.org
```

Done on 2026-10-11 (`68d511cec87b`).

---

## 4. Rollback

Deploy the previous tag: check out the earlier commit and run `tl deploy`. The tags and the
digests that ran are in `.deploy/history`.

```bash
cd /opt/tracelet && tail -n 9 .deploy/history
git checkout <earlier full commit> && ./scripts/tl deploy
```

**Migrations are forward-only (F14.AC6).** If the release being rolled back applied a
migration, the old image may not run against the new schema; `alembic` will not go down.
Rolling back across a migration is a **restore** (§6) of the last backup taken before it --
and everything since that backup is lost. Read the release's commits for `alembic/versions`
before rolling back.

Docker Hub tags are mutable. If a tag was overwritten, pull by the digest recorded in
`.deploy/history` (`docker pull swarooprayker/tracelet-api@sha256:...`) and retag it.

---

## 5. First deploy on a new VM

1. **The VM:** e2-micro, Debian 12, 30 GB disk; firewall open on 22, 80 and 443 only; a 2 GB
   `/swapfile` in `/etc/fstab`. Docker Engine with the compose plugin; the deploying user in
   the `docker` group.
2. **The checkout:**
   ```bash
   sudo install -d -o "$USER" -g docker -m 2775 /opt/tracelet
   git clone https://github.com/swaroop-rayker/Tracelet.git /opt/tracelet
   cd /opt/tracelet && git checkout <released commit>
   ```
3. **Secrets.** Either fresh (a new system), which prints the names of what it made and
   never a value:
   ```bash
   ./scripts/tl secrets tracelet.duckdns.org
   ```
   then fill in by hand the Telegram bot token, `TRACELET_TELEGRAM_OWNER_CHAT_ID`, and the
   MaxMind, IP2Location and IPinfo credentials, and **copy `.env` and `secrets/ip_key` into
   the password manager now**. Or, rebuilding an existing system, paste them from the note
   (§6 step 4).
4. **Host baseline and DuckDNS** (§7, §8):
   ```bash
   ./scripts/tl host-setup
   ```
5. **Deploy and create the owner:**
   ```bash
   ./scripts/tl deploy
   ```
   ```bash
   docker compose run --rm cli tracelet admin bootstrap --email <owner email> --name <name>
   ```
   The owner runs the second command themselves: it prints a one-time enrollment link.
   Then, in the dashboard: set the password, enrol TOTP, **save the 10 recovery codes**,
   verify the Telegram chat (Settings), and press "Send test message".
6. From the PC: `./scripts/tl live-check https://tracelet.duckdns.org`.

---

## 6. Restore onto a new or wiped VM (ADR-0030)

What it needs: a backup `.dump` downloaded from the dashboard, and the password-manager note
(`.env`, `ip_key`). What it loses: everything since that backup was taken.

**Drill, 2026-10-11:** production wiped (`down -v`, `/opt/tracelet` removed, images pruned)
and rebuilt with exactly these steps. Counts and privileges equal to the pre-wipe record;
every `ip_enc` readable; the owner signed in with password and TOTP; MILESTONES M9.

1. **If the old system still runs**, take a fresh backup and download it: System Health →
   Backups → **Back up now**, wait for `ok`, **Download**. Check its SHA-256 against the row
   (`sha256sum` on the PC). Copy it to the VM:
   ```bash
   gcloud compute scp <file>.dump <instance>:~/ --zone <zone>
   ```
2. **Wipe**, only if rebuilding on the same VM (this deletes the database and every
   volume):
   ```bash
   cd /opt/tracelet && docker compose down -v && cd / && sudo rm -rf /opt/tracelet
   ```
3. **A fresh checkout** of the current release: §5 step 2.
4. **Secrets from the note**, by the owner (in the browser SSH session):
   ```bash
   sudo nano /opt/tracelet/.env
   sudo install -d -o swaro -g docker -m 2770 /opt/tracelet/secrets
   sudo nano /opt/tracelet/secrets/ip_key
   sudo chown swaro:docker /opt/tracelet/.env && sudo chmod 0600 /opt/tracelet/.env
   sudo chown 10001:10001 /opt/tracelet/secrets/ip_key && sudo chmod 0400 /opt/tracelet/secrets/ip_key
   ```
5. **Restore**, before anything else starts:
   ```bash
   cd /opt/tracelet && ./scripts/tl restore ~/<file>.dump
   ```
   It checks that the api image accepts `.env` (E84), refuses if the api is running or the
   database already has tables, restores in one transaction as the PostgreSQL superuser
   keeping owners and privileges, then **adopts** the backup: the file is copied into the
   backup volume and its row, which the dump holds as `running`, becomes `ok` with the
   restored counts; older rows whose files stayed behind become `pruned`. If the adoption
   alone fails, fix the cause and run the command it prints, **before** the api starts.
6. **Deploy:** `./scripts/tl deploy`. Migrations newer than the backup run now.
7. **Verify:**
   - The owner signs in with their existing password and TOTP code. The TOTP secret is
     sealed with `ip_key`, so this also proves the key.
   - System Health → Backups: the restored-from backup is `ok`; press **Verify now** on it.
   - A visit on a test link arrives as a Telegram alert.
   - `./scripts/tl live-check https://tracelet.duckdns.org` from the PC.
   - Certificates, the fallback pages and the geo databases come back by themselves within
     minutes (Let's Encrypt; the api; the scheduler). The ASN profiles wait for 02:00.

If the key is lost, the restore still works, but every `ip_enc` is unreadable and no admin
can pass TOTP. Generate a new key (`openssl rand -hex 32`, step 4's mode and owner), then the
owner clears and re-enrols two-factor with a one-time link (audited):

```bash
docker compose run --rm cli tracelet admin reset-totp --email <owner email>
```

---

## 7. Host baseline (ADR-0026)

Applied by `./scripts/tl host-setup` (idempotent, needs sudo) and checked, read-only, by
`./scripts/tl host-check` and at every deploy:

| Item | File in the repository | Effect |
|---|---|---|
| journald capped at 200 MB | `deploy/host/journald-tracelet.conf` | Logs cannot fill the disk |
| rsyslog rotated daily or at 100 MB, four kept | `deploy/host/logrotate-rsyslog` | Debian's weekly default grew unbounded |
| `vm.swappiness = 10` | `deploy/host/sysctl-tracelet.conf` | Page cache goes before program memory (E79) |
| Ops Agent and OS-config agent stopped and disabled, kept installed | -- | About 100 MB back (owner decision 2026-10-10); patching is unattended-upgrades' |
| DuckDNS updater | `deploy/host/duckdns-cron` | §8 |
| Container logs capped at 10 MB × 3 | `docker-compose.yml` | Travels with the repository |

Not automated, set once on the VM: Debian 12, the 2 GB `/swapfile` in `/etc/fstab`, the
firewall (22, 80, 443 only).

---

## 8. DuckDNS (free-subdomain path)

The VM's external address is **ephemeral**: it changes when the VM is stopped and started.
`/etc/cron.d/tracelet-duckdns` re-points `tracelet.duckdns.org` at boot and every five
minutes, with the token read from `/etc/tracelet/duckdns.curl` (root, 0600, one line:
`url = "https://www.duckdns.org/update?domains=<name>&token=<token>&ip="`). The last answer
is in `/var/lib/tracelet/duckdns.last` (`OK` or `KO`).

After a stop and start, expect up to five minutes of a stale name. To force it:

```bash
sudo sh -c 'curl -fsS -K /etc/tracelet/duckdns.curl -o /var/lib/tracelet/duckdns.last' && cat /var/lib/tracelet/duckdns.last
```

M9.1 (a purchased domain behind Cloudflare) replaces this.

---

## 9. Break-glass: locked out

In order of preference:

1. **A recovery code** on the sign-in page (10 were shown at enrolment).
2. **Reset over Telegram** from the sign-in page, if the chat was verified (F8.AC7).
3. **On the VM**, with shell access only (F8.AC8, audited):
   ```bash
   cd /opt/tracelet && docker compose run --rm cli tracelet admin reset-password --email <owner email>
   ```
   The owner runs it: it prints a one-time link.

---

## 10. One-off commands

Always as the `cli` service, **never `run api`**: a run of `api` inherits its fixed address,
which the running api holds, and fails with "Address already in use" (ERRORS E80).

```bash
docker compose run --rm cli tracelet --help
docker compose run --rm cli tracelet geodb status
docker compose run --rm cli tracelet check-config
docker compose exec db psql -U tracelet -d tracelet
```

Long jobs go detached (`nohup ... &`, then read the log): an ssh session's output is lost when
it backgrounds. A probe on a full disk writes its files to `/dev/shm`.
