# Frontend image: builds the SPA, then serves it from Caddy, which also proxies
# /api/* to the backend (deploy/caddy/Caddyfile). Build context is the
# repository root: the pnpm workspace and lockfile live there.
#
#   docker build -f frontend/Dockerfile -t nptc-frontend \
#     --build-arg VITE_OIDC_ISSUER=http://localhost:8080/realms/nptc \
#     --build-arg VITE_OIDC_CLIENT_ID=nptc-frontend .

FROM node:22.22.2-slim AS build
# The version comes from "packageManager" in the root package.json.
RUN corepack enable
WORKDIR /app

COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY frontend/package.json frontend/package.json
RUN pnpm install --frozen-lockfile --filter nptc-frontend...

COPY frontend frontend
# A test file that `tsc -b` type-checks imports this document.
COPY docs/api/openapi.json docs/api/openapi.json

# Vite inlines these into the published bundle, so they are public facts about
# a public OIDC client and never secrets. Both are required at build time; the
# issuer must be the address the browser reaches Keycloak on.
ARG VITE_OIDC_ISSUER
ARG VITE_OIDC_CLIENT_ID
ARG VITE_API_BASE_URL
ENV VITE_OIDC_ISSUER=$VITE_OIDC_ISSUER \
    VITE_OIDC_CLIENT_ID=$VITE_OIDC_CLIENT_ID \
    VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN pnpm --filter nptc-frontend build

FROM caddy:2.11.4-alpine AS runtime
# The base image declares /data and /config as root-owned volumes, so Caddy's
# state goes to a directory this user owns. The official binary carries the
# capability to bind port 80 without root.
RUN addgroup -S -g 10001 nptc \
    && adduser -S -u 10001 -G nptc -H nptc \
    && mkdir -p /tmp/caddy/data /tmp/caddy/config \
    && chown -R nptc:nptc /tmp/caddy
ENV XDG_DATA_HOME=/tmp/caddy/data \
    XDG_CONFIG_HOME=/tmp/caddy/config
COPY deploy/caddy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/frontend/dist /srv
USER nptc
EXPOSE 80
