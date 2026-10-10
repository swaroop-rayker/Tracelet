# ADR-0026 — Production images are built on the owner's PC and pulled from Docker Hub

**Status:** Accepted (2026-10-10). The owner chose Docker Hub over a CI-built registry image.
**Deciders:** repository owner
**Relates to:** ADR-0012 (deployment topology), ADR-0007 (key file, out-of-band secrets),
ADR-0014 and ADR-0022 (backups, restore), ADR-0025 (development tunnel); SPEC F14.AC1-AC6,
NFR6, SC2, §11 row 33; ERRORS E21 (the local TLS-inspection root); CLAUDE.md §5 (memory)

---

## Context

M9 deploys to the e2-micro for the first time. Three facts shape how the code gets there.

1. **The box cannot build comfortably.** The Caddy image runs a Vite build and the API image
   a pip install; on 1 GB with the stack running, either would push the database into swap,
   and the e2-micro has one shared vCPU. Building there would also mean a stopped stack.
2. **The development PC re-signs HTTPS.** Its antivirus intercepts TLS (E21), so image builds
   on it need a local root, and today `api/Dockerfile` installs that root into the
   **production stage's** trust store: Python's clients there would believe any certificate
   it signs. The Dockerfile's comment assumed production images come from CI, where the
   folder is empty. An image built on this PC for production would carry it.
3. **The VM was shared with other projects.** Measured 2026-10-10 after cleaning them away
   and rebooting: about 195 MB of unreclaimable host memory with no container running
   (Docker, and GCP's guest and OS-config agents), against ARCHITECTURE §6's 180 MB; and
   3.5 GB of logs, nearly all the Google Cloud Ops Agent failing every export with
   `PermissionDenied` (the VM's service account lacks the writer roles).

## Decision

**Build on the owner's PC from a clean commit, push to public Docker Hub repositories, and
have the VM only pull.**

1. **`./scripts/tl release`** builds the two images from a `git archive` of `HEAD` (so the
   image is exactly a commit, never a dirty tree; it refuses uncommitted changes), tags them
   `<repo>/tracelet-api:<sha>` and `<repo>/tracelet-caddy:<sha>` (`TRACELET_IMAGE_REPO`,
   default `swarooprayker`), checks them, and pushes. The SPA is still built at image-build
   time (F14.AC4).
2. **No local root in a release image.** A local root reaches a build only as a BuildKit
   secret, mounted for the steps that download and never written to a layer. The release
   check fails if the runtime image's trust stores (system and certifi) differ from the
   base image's. Development keeps working: the dev stack gets the root at **run time**
   from a read-only mount, not baked into the image.
3. **A production compose override**, `docker-compose.prod.yml`, replaces `build:` with
   `image:` at `${TRACELET_IMAGE_TAG}` for `api` and `caddy`. The VM holds a checkout of the
   same commit for the compose files, `db/init/` and `scripts/tl`; `./scripts/tl deploy`
   there pulls, migrates with the new image, and restarts. Rolling back is deploying the
   previous tag (migrations are forward-only, F14.AC6, so a rollback across a migration is a
   restore, said in the runbook).
4. **The images are public.** They contain no secret: `.env`, `secrets/` and `docs/private/`
   are excluded by `.dockerignore`, and item 2 keeps the local root out. The repository is
   public already. The VM pulls without a Docker Hub token on its disk.
5. **The host baseline is part of the deployment**, written in the runbook and checked by
   `tl deploy`:
   - Debian 12, the 2 GB `/swapfile`;
   - journald capped at 200 MB; rsyslog's copy rotated daily or at 100 MB, four kept;
   - every container's json-file log capped (10 MB × 3) in `docker-compose.yml`, so the
     cap travels with the repository rather than living in a host file;
   - the Ops Agent stopped and disabled, kept installed (owner decision 2026-10-10);
   - the firewall open on 22, 80 and 443 only (two stale rules deleted 2026-10-10).
6. **The free-subdomain name follows the IP.** The VM's address is ephemeral and changes on a
   stop and start. A host-level DuckDNS update runs at boot and every five minutes (a
   `curl`, no container, no resident memory), with the token in a root-only file. Only on the
   free-subdomain path; M9.1's Cloudflare DNS replaces it.
7. **Production secrets are fresh**, generated on the VM, and the owner keeps a copy off
   the VM: the `.env` secrets and `secrets/ip_key` together. The restore drill (ADR-0014)
   proves that copy, by restoring from it alone.

## Alternatives considered

| Option | Why not |
|---|---|
| **CI builds and pushes to GHCR** | Recommended. Images would come from a clean runner, with no local root anywhere near them. The owner preferred Docker Hub from this PC. Item 2 provides the clean-runner guarantee locally instead. |
| **Build on the VM** | Literally SC2's path, and the simplest. It swaps heavily on 1 GB, needs the stack stopped, and puts a build toolchain's downloads on the production disk. |
| **`docker save \| ssh docker load`** | No registry, but every deploy pushes about 200 MB over the owner's uplink, and a deploy needs this PC online throughout. |
| **Private Docker Hub repositories** | Nothing to hide. A read token would have to live on the VM. |
| **Remove the Ops Agent** | Recommended, with about 73-97 MB back. The owner kept it installed but stopped, to turn on later with the IAM roles it needs. |

## Consequences

- A deploy needs this PC and Docker Hub. If either is unavailable, SC2's build-from-clone
  path still works, on the VM or anywhere else.
- SC2 is unchanged: a fresh clone builds and runs locally in five commands.
- Docker Hub's anonymous pull limits apply to the VM; a deploy pulls two images.
- Tags are mutable on Docker Hub, so `tl deploy` records the pulled image digests in the
  deploy log, and the runbook names the digest to roll back to.
- A local root can no longer silently reach production. The check in item 2 is what makes
  that true, not the comment.
- The host baseline lives in the runbook and in `tl deploy`'s checks; a new VM repeats it.

## Amended 2026-10-10 (M9, E79): two guards for the redirect under memory pressure

The first deploy's `asn_profiles` rebuild (ERRORS E79) showed two ways the box itself, not
the app, failed requests under memory pressure. The owner approved both guards.

1. **Caddy reaches the api by a fixed address.** One 502 during the rebuild was Caddy's
   `dial tcp: lookup api: i/o timeout`: Docker's embedded DNS is answered by `dockerd` on the
   host, and `dockerd` had been partly swapped out. The compose `default` network now has a
   fixed subnet, `172.31.253.0/24`, with automatic addresses only from `172.31.253.128/25`;
   the api holds `172.31.253.10`, and Caddy's upstream is `TRACELET_API_UPSTREAM` (that
   address; `api:8000` when unset). No name is resolved on the redirect path. The api's own
   connections to `db` still resolve by name, but the pool keeps them open, so a lookup
   happens only on a new connection.
2. **`vm.swappiness = 10`** in the host baseline (`deploy/host/sysctl-tracelet.conf`, applied
   and checked by `tl host-setup` and `tl host-check`). At Debian's default of 60 each worker
   had about 40 % of its memory swapped to the network disk while 426 MB was page cache.
   Measured during the rebuild, it lowered the median `/healthz` from 1.0 s to 0.76 s; it is
   not the fix on its own (E79's is), but it keeps program memory resident in general.

Changing the default network's subnet makes compose recreate it: the first deploy after this
needs `docker compose down` once, said in the runbook.

**One-off commands run as the `cli` service (ERRORS E80).** A `docker compose run` of the api
inherits its fixed address, which the running api holds, so the first deploy after this
amendment failed at its migration with "Address already in use". The api's settings moved to
an `x-api` anchor shared by `api` (the server, with the address) and `cli` (one-offs, without
it, behind a `cli` profile). `tl migrate`, `tl bootstrap` and the documented break-glass
`docker compose run --rm cli tracelet admin reset-password` use it, whether the api is up or
not.
