# ---------------------------------------------------------------------------
# Tracelet edge image: the built SPA, served by Caddy.
#
# The frontend and the reverse proxy live in one image on purpose. Caddy serves
# the static build, so there is nothing to synchronise between an image and a
# volume, and no Node runtime in production (F14.AC4, ADR-0003).
#
# Build context is the REPOSITORY ROOT, because the Caddyfile lives there:
#     build: { context: ., dockerfile: web/Dockerfile }
# ---------------------------------------------------------------------------

FROM node:24-alpine AS build

WORKDIR /build

# Local TLS-inspection roots (api/certs/README.md), for npm only. Empty in CI and in
# production, so the fallback is the image's own bundle; on a development machine
# whose antivirus re-signs HTTPS, npm cannot reach the registry without it. Scoped to
# the install step: nothing that runs later inherits the extra trust.
COPY api/certs/ /tmp/local-ca/
RUN if ls /tmp/local-ca/*.crt >/dev/null 2>&1; \
      then cat /tmp/local-ca/*.crt > /tmp/npm-ca.pem; \
      else cp /etc/ssl/certs/ca-certificates.crt /tmp/npm-ca.pem; fi

# Lockfile-aware install, so a dependency cannot drift between builds.
COPY web/package.json web/package-lock.json* ./
RUN if [ -f package-lock.json ]; then NODE_EXTRA_CA_CERTS=/tmp/npm-ca.pem npm ci; \
    else NODE_EXTRA_CA_CERTS=/tmp/npm-ca.pem npm install; fi

COPY web/ ./

# tsc --noEmit runs as part of `npm run build`: a type error fails the image
# build rather than shipping. ES1 is enforced here as well as in CI.
RUN npm run build


# ---------------------------------------------------------------------------
FROM caddy:2-alpine AS runtime

COPY --from=build /build/dist /srv
COPY Caddyfile /etc/caddy/Caddyfile

# 80 and 443 for HTTP/1.1 and HTTP/2; 443/udp for HTTP/3.
EXPOSE 80 443 443/udp

# Fail the container start on a malformed Caddyfile rather than serving a
# half-configured edge.
RUN caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile

CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
