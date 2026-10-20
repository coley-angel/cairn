---
name: cairn
description: Detect how an app is built and render a host with edge SSO, access rules, health checks, and identity-header stripping. Use when the user wants to deploy, host, publish, or add SSO, OIDC, OAuth, login, or access control for a static site, Next.js, Node, Express, Hono, FastAPI, Starlette, Flask, Django, Go, Dockerfile, or Compose service.
license: MIT
compatibility: Python 3.12+. Docker is required only for cairn up. No cloud credentials are required to detect, check, or render.
metadata:
  author: coley-angel
  version: "1.0"
---

# Cairn

Cairn turns an application directory into a host contract. The contract is `cairn.yaml`. The CLI renders Caddy, oauth2-proxy, Compose, and Kubernetes. Do not invent a second login layer when edge SSO fits.

## Install

From a checkout of `github.com/coley-angel/cairn`:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Workflow

1. Read [references/workflow.md](references/workflow.md) and follow it in order.
2. Run `cairn detect <app-dir>`.
3. Edit access rules and the issuer. Never write a client secret, cookie secret, password, token, or PEM block into `cairn.yaml`.
4. Run `cairn check <app-dir>`. Fix every error. Warnings name placeholder values that must change before a real login.
5. Run `cairn render <app-dir>`.
6. Read `.cairn/Caddyfile` and confirm identity headers are deleted before `forward_auth`. Do not remove those deletions.
7. Put secrets in the environment or an untracked `.env`. `cairn up` refuses to start when they are missing, and it does not print their values.
8. Run `cairn up` only after the user asks to start the stack.

## Decide the auth mode

- `edge-oidc` is the default. The app does not implement login. It may read `X-Auth-Request-User`, `X-Auth-Request-Email`, `X-Auth-Request-Groups`, and `X-Auth-Request-Preferred-Username`. It must not trust `X-Forwarded-User` or a client-supplied `Authorization` header.
- `app-oidc` is for an app that already uses Auth.js, Better Auth, FastAPI's OIDC client, or Django. Cairn injects `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, and `OIDC_REDIRECT_URI`. It still strips proxy identity headers.
- `none` is a plain reverse proxy with the same header stripping. Use it only when the user explicitly does not want SSO.

## Paradigms

Detection order is in [references/workflow.md](references/workflow.md). Compose wraps the existing public service. A root Dockerfile is the fallback, not a higher priority than Next.js, Python, Go, or Node.

## Stop

Stop and tell the user when `cairn check` reports an ambiguous runtime, when two Compose services both publish ports, or when the issuer and email domain are still the documentation placeholders and the user asked for a real login. Do not put the stub auth server from the test suite into a deployment.
