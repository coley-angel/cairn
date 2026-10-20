# Cairn

Cairn is the golden path for putting a web app behind a host. It detects how the app is built, writes a contract, and renders an edge that does login, access control, and health checks the same way every time.

The app does not have to grow its own SSO integration. Caddy asks [oauth2-proxy](https://oauth2-proxy.github.io/oauth2-proxy/) before it forwards a request. The proxy speaks OIDC to Okta, Entra, Authentik, or Google. The app receives identity only in `X-Auth-Request-*` headers that the edge copied from the proxy, after it deleted any copy the client sent.

```bash
cairn detect .
cairn check .
cairn render .
# export OIDC_CLIENT_ID, OIDC_CLIENT_SECRET, and OAUTH2_PROXY_COOKIE_SECRET
cairn up .
```

This is a contract and a local edge, not a multi-tenant platform. Compose is the path you can boot on a laptop. Kubernetes manifests are the same contract for a cluster, with the proxy as a sidecar and secrets referenced rather than stored.

## Five-minute render

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"

cp -R fixtures/fastapi /tmp/cairn-demo
cd /tmp/cairn-demo
cairn detect .
cairn check .
cairn render .
```

`cairn check` warns that the issuer is `https://idp.example.com` and the only email domain is `example.com`. Those values are documentation placeholders. The render is still valid, and login will not succeed until you replace them.

Open `.cairn/Caddyfile`, `.cairn/oauth2-proxy.cfg`, and `.cairn/k8s/`. The how-to walks through each edit: [docs/how-to.md](docs/how-to.md).

## Paradigms

| App | How Cairn recognizes it | How it runs |
| --- | --- | --- |
| Static site | `index.html`, `public/index.html`, or Vite | Python static server, `GET /healthz` |
| Node | `package.json` `start` script, including Express and Hono | `node server.js` |
| Next.js | `next` dependency, even if a Dockerfile is present | `next start` after `next build` |
| FastAPI, Starlette | Python dependency | `uvicorn` |
| Flask | Python dependency | `gunicorn` |
| Django | `manage.py` | `gunicorn` and `DJANGO_SECRET_KEY` from the environment |
| Go | `go.mod` and a `net/http` listener | `go build` |
| Dockerfile | a root `Dockerfile` and none of the above | the existing image, proxied |
| Compose | `compose.yaml` or `docker-compose.yml` | the published service is wrapped; other services stay |

Every fixture under `fixtures/` listens for `PORT` and serves `GET /healthz`. Detection order and the ambiguous cases are in [docs/how-to.md](docs/how-to.md).

## What the edge guarantees

- Client-supplied `X-Auth-Request-*`, `X-Forwarded-User`, and, in edge mode, `Authorization` are removed before `forward_auth`.
- oauth2-proxy is configured with `reverse_proxy = true` and a trusted proxy of exactly one hop: the Compose edge address, or `127.0.0.1` when the proxy is a sidecar.
- PKCE uses S256. The OIDC nonce is verified. Access tokens are not copied into upstream headers.
- `edge.tls: off` is allowed only for `localhost` and `127.0.0.1`, and that listener is published on `127.0.0.1`. Any other host uses `tls internal`.
- Secrets are environment variables. `cairn.yaml` and the rendered config do not contain them.

The threat model is [docs/threat-model.md](docs/threat-model.md). Agents should follow [docs/agents.md](docs/agents.md) or the installable skill at `skills/cairn/`.

## Tests

```bash
python -m pytest -m "not integration"
python -m pytest -m integration   # requires a running Docker daemon
```

GitHub Actions runs both. The integration test boots the static and FastAPI fixtures with a local auth stub, checks `/healthz`, and proves a spoofed identity header does not survive the edge.

## License

MIT. Copyright 2026 coley-angel.
