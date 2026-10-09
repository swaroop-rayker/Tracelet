# ADR-0025 — A development-only tunnel whose forwarded address is trusted

**Status:** Accepted (2026-10-09). The owner chose this over waiting for the M9 deploy.
**Deciders:** repository owner
**Relates to:** SPEC F13.AC6, §11 row 31; ADR-0012 (deployment topology), ADR-0024 (accuracy by
replay); MILESTONES M2 (a trust setting declined), M8; RISKS R29; CLAUDE.md invariants 1 and 4,
§5 (memory)

---

## Context

M8's ground truth needs the owner to visit their own links from their phone on each Indian
network, so the engine's network sources (the databases, rDNS, the ASN) can be compared with
where they really were. The development stack runs on the owner's PC at `https://localhost`.
A phone reaches it only through a Cloudflare **quick tunnel**: a `cloudflared` container that
dials out to Cloudflare and forwards requests to Caddy.

F13.AC6 believes `CF-Connecting-IP` only when the TCP peer is a published Cloudflare address.
Through the tunnel the peer is the local `cloudflared` container, so the app records the
container, not the phone. The owner's phone visits of 6–7 October have no ASN and no IP
geolocation at all. A label on such a visit measures nothing M8 cares about. In M2 a
development trust setting was declined, deliberately: an address chosen by a header is the
easiest way to defeat rate limiting and geolocation together.

Waiting for M9's deploy, where Caddy or a verified Cloudflare edge sees the real address, was
offered and declined: the owner wants to start labelling now.

## Decision

**A tunnel on its own Docker network, and one development-only setting that trusts
`CF-Connecting-IP` from that network alone.**

1. **The tunnel is a compose service** (`tunnel`, profile `tunnel`, `cloudflare/cloudflared`
   pinned at `2026.8.0`, `mem_limit: 96m`), started by `./scripts/tl tunnel`, which prints the
   `*.trycloudflare.com` address. It sits on a dedicated network, `tunnel` (`172.31.254.0/29`),
   that only it and Caddy join. It reaches Caddy as `https://caddy` with the `localhost` host
   name, so Caddy's existing site and certificate serve it unchanged.
2. **`TRACELET_DEV_TRUSTED_TUNNEL`** names that network as a CIDR. When the TCP peer
   (`X-Tracelet-Peer-IP`, set by Caddy from the connection itself) is inside it, the address in
   `CF-Connecting-IP` is the visitor's.
3. **It cannot reach production.** Settings refuse to load when it is set and `TRACELET_ENV`
   is not `development`. The CIDR must be a private range of at most 256 addresses: the tunnel
   network, never "anything". The app logs a warning at startup whenever it is set.
4. **Only the address is believed.** `edge_verified` stays false, so `CF-Ray` and
   `CF-IPCountry` (S8, the colo) are still ignored. That makes the tunnelled visits the
   **direct** path that ADR-0012's free-subdomain deployment has, and no Cloudflare data mixes
   into the labels.
5. **Every visit that used it says so:** a signal `edge.dev_tunnel_address` (category
   `network`, weight 0). A labelled visit's provenance is then always visible.

Off by default; `.env.example` documents it commented out.

## Consequences

- The owner can label real network visits now, from any network the phone is on.
- While it is on, anything that can reach the `tunnel` network can choose its apparent
  address. Only `cloudflared` and Caddy are on it, and only in development.
- The labels are the direct path's. Cloudflare-path accuracy (S8) still needs M9's deploy.
- One more image in the dependency ledger. It is development-only and pulled only with the
  `tunnel` profile.
- Caddy joins a second network in every environment. Without the profile and the setting it
  carries nothing.

## Alternatives considered

| Option | Why not |
|---|---|
| **Wait for the M9 deploy** | Recommended, and declined by the owner: labelling would wait for the whole of M9. |
| **Deploy early, only for labelling** | Brings M9's exposure forward without its hardening. |
| **Trust the compose `default` network** | Every container is on it (the api, the database, the tools), so any of them could forge an address. A dedicated network keeps the trusted set to one container. |
| **Trust `X-Forwarded-For` from any private peer** | Caddy already trusts private ranges as proxies for its own purposes. Trusting a client-supplied chain is precisely what F13.AC6 forbids. |
| **Also believe `CF-Ray` and `CF-IPCountry` through the tunnel** | It would mix the Cloudflare path's S8 into labels taken on what production may serve directly. Kept out until M9 can measure both paths properly. |
