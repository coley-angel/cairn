# Workflow

Run these commands from a checkout where `cairn` is on `PATH`.

```bash
cairn detect .
cairn check .
cairn render .
cairn up .
```

`detect` writes `cairn.yaml` and refuses to replace it unless the user passed `--force`.

## Detection order

1. More than one of FastAPI/Starlette, Flask, and Django is an error.
2. More than one Compose file is an error.
3. A Compose file wraps the single service that publishes a port.
4. `manage.py` is Django.
5. A `next` dependency is Next.js, even if a Dockerfile is also present.
6. FastAPI or Starlette is the ASGI path (`uvicorn`).
7. Flask is the WSGI path (`gunicorn`).
8. A Go module with `ListenAndServe` or `http.Server` is Go.
9. A `package.json` start script is Node. Express and Hono are this path. Vite without `server.js` stays static.
10. `index.html`, `public/index.html`, or Vite is static.
11. A root Dockerfile is the escape hatch.
12. Anything else is an error. Ask the user which process listens, and do not guess a paradigm outside this list.

## Access rules

`edge-oidc` requires at least one of `emailDomains`, `emails`, `groups`, or `allowAllEmails: true`.

- A trailing slash on a public or API path is a prefix (`/api/` matches `/api/ping`).
- API paths return 401 instead of a browser redirect.
- `/healthz` is public by default so probes do not need a session.
- `example.com` and `https://idp.example.com` are documentation placeholders. Replace both before a real login.
- `allowAllEmails: true` allows every verified email. Do not hide that behind `emailDomains: ["*"]`; the check rejects the star.

## Secrets

Allowed in the contract: `clientIdEnv`, `clientSecretEnv`, `cookieSecretEnv`.

Not allowed: secret values, PATs, cloud access keys, PEM certificates, or URLs with userinfo.

Generate the cookie secret outside the repo:

```bash
python -c 'import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())'
```

Export it as `OAUTH2_PROXY_COOKIE_SECRET`. Export the IdP client id and secret as `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET`. Django also needs `DJANGO_SECRET_KEY`. An untracked `.env` is acceptable. A committed `.env` is not.

## What render writes

- `.cairn/Caddyfile` deletes client identity headers, then calls oauth2-proxy, then copies only `X-Auth-Request-*` from that response.
- `.cairn/oauth2-proxy.cfg` sets `reverse_proxy`, a single trusted proxy address, PKCE S256, and nonce verification. It has no client secret.
- `.cairn/docker-compose.yml` publishes `127.0.0.1` when the host is localhost, and `0.0.0.0` only after the host is a real name with `edge.tls: internal`.
- `.cairn/k8s/` runs oauth2-proxy as a sidecar on `127.0.0.1` and references a Secret named `<app>-oidc`. It does not contain that Secret.
- `.cairn/env.example` has empty values.

## Header invariant

Do not "simplify" the Caddyfile by deleting `request_header -X-Auth-Request-*` or `header_up -X-Auth-Request-*`. Caddy 2.10+ can keep a client-supplied identity header when `copy_headers` does not replace it (GHSA-7r4p-vjf4-gxv4). Cairn pins Caddy 2.9.1 and still deletes the headers.

On a public path such as `/healthz`, the reverse proxy also deletes those headers. A probe must not be able to present `X-Auth-Request-User: attacker`.
