# How to host an app with Cairn

This guide assumes you have Python 3.12 and, for the last step, Docker. You do not need a cloud account to detect, check, or render.

## 1. Install

```bash
git clone https://github.com/coley-angel/cairn.git
cd cairn
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cairn --version
```

## 2. Detect the app

From the application root:

```bash
cairn detect .
```

Cairn writes `cairn.yaml` and prints why it chose the paradigm. It will not overwrite that file unless you pass `--force`.

Detection stops at the first match:

1. FastAPI/Starlette, Flask, and Django together are an error. Keep one runtime.
2. Two Compose files are an error. Keep one.
3. Compose wraps the only service that publishes a port. A FastAPI tree that also has Compose stays Compose, so the edge does not throw away Redis or another sidecar.
4. `manage.py` means Django.
5. A `next` dependency means Next.js. A Dockerfile in the same tree does not change that.
6. FastAPI or Starlette means `uvicorn`.
7. Flask means `gunicorn`.
8. A Go module that calls `ListenAndServe` or builds an `http.Server` means Go.
9. A npm `start` script means Node. Express and Hono are this path. A Vite project without `server.js` stays static.
10. `index.html`, `public/index.html`, or a Vite config means static.
11. A root `Dockerfile` is the leftover case.
12. Otherwise Cairn stops and asks you to say which process listens.

Try the fixtures:

```bash
cairn detect fixtures/fastapi
cairn detect fixtures/next
cairn detect fixtures/compose
```

## 3. Read the contract

A detected FastAPI app looks like this, with the documentation issuer and domain still in place:

```yaml
apiVersion: cairn.dev/v1
kind: App
name: fastapi
paradigm: fastapi
port: 8000
healthPath: /healthz
host: localhost
edge:
  tls: "off"
  bindPort: 8080
auth:
  mode: edge-oidc
  issuer: https://idp.example.com
  clientIdEnv: OIDC_CLIENT_ID
  clientSecretEnv: OIDC_CLIENT_SECRET
  cookieSecretEnv: OAUTH2_PROXY_COOKIE_SECRET
  emailDomains:
    - example.com
  publicPaths:
    - /healthz
  allowAllEmails: false
```

`cairn check .` is valid at this point and prints two warnings. Those warnings are the work.

## 4. Point it at a real issuer

Set `auth.issuer` to the issuer URL from your IdP. It must be `https`, with a host, and with no username, password, query, or fragment.

| IdP | Issuer shape |
| --- | --- |
| Google | `https://accounts.google.com` |
| Okta | `https://your-org.okta.com` |
| Authentik | `https://auth.example.com/application/o/cairn/` |
| Entra | `https://login.microsoftonline.com/<tenant>/v2.0` |

Create a confidential web client. The redirect URL is printed in `.cairn/oauth2-proxy.cfg` as `redirect_url` after you render. For the default local contract it is:

```text
http://localhost:8080/oauth2/callback
```

Register that exact URL on the client. Request the scopes `openid`, `email`, and `profile`. If you set `auth.groups`, also request `groups`, and make sure the ID token actually contains a `groups` claim. Entra does not emit group object IDs unless you configure the optional claim.

Leave the client id and client secret in the IdP. Cairn only stores the names of the environment variables that will hold them.

## 5. Close the access rule

`edge-oidc` refuses to render an open proxy. Pick one:

- `emailDomains: [acme.example]` allows any verified mailbox at that domain.
- `emails: [ada@acme.example]` is an allow-list. Each address must sit inside `emailDomains` when you set both.
- `groups: [engineering]` requires the OIDC `groups` claim. If you set groups and no domains, any verified email in that group is eligible.
- `allowAllEmails: true` allows every verified email. Write that on purpose. `emailDomains: ["*"]` is rejected.

`example.com` is reserved for documentation. `cairn check` warns while it is the only domain.

## 6. Keep health public, and make APIs return 401

`publicPaths` skips the login redirect. `/healthz` is there so a probe does not get a 302.

```yaml
auth:
  publicPaths:
    - /healthz
  apiPaths:
    - /api/
```

A trailing slash is a prefix, so `/api/` covers `/api/ping`. Those routes still require a session. A missing session is `401`, not a redirect to the login page. Browser pages keep the `302` to `/oauth2/sign_in`.

Public paths delete identity headers on the way to the app. A client that sends `X-Auth-Request-User: attacker` to `/healthz` is not that user.

## 7. Choose edge SSO or app-owned SSO

`auth.mode: edge-oidc` is the default. The application can read:

- `X-Auth-Request-User`
- `X-Auth-Request-Email`
- `X-Auth-Request-Groups`
- `X-Auth-Request-Preferred-Username`

It should not read `X-Forwarded-User`. oauth2-proxy is configured with `pass_user_headers = false` and `pass_basic_auth = false`, and Caddy deletes those headers anyway.

Use `auth.mode: app-oidc` when the framework already has a login library. Cairn does not replace Auth.js, Better Auth, or Django's auth apps. It injects four variables into the app container:

| Variable | Value |
| --- | --- |
| `OIDC_ISSUER` | `auth.issuer` |
| `OIDC_CLIENT_ID` | from `clientIdEnv` |
| `OIDC_CLIENT_SECRET` | from `clientSecretEnv` |
| `OIDC_REDIRECT_URI` | the public origin plus `/callback` |

Wire them like this:

- Auth.js: set `AUTH_URL` to the public origin and point the provider's `issuer`, `clientId`, and `clientSecret` at the four variables.
- Better Auth: pass the same issuer and client values into the OIDC plugin's `clientId`, `clientSecret`, and `discoveryUrl` (`$OIDC_ISSUER/.well-known/openid-configuration`).
- FastAPI: build the OIDC client from `OIDC_ISSUER`, `OIDC_CLIENT_ID`, and `OIDC_CLIENT_SECRET`. Do not also trust `X-Auth-Request-User` unless you have separately decided the edge is the authority.
- Django: configure the OIDC backend from those variables. Keep `DJANGO_SECRET_KEY` in the environment. Cairn's settings fixture reads `os.environ["DJANGO_SECRET_KEY"]` and has no default.

`auth.mode: none` is a reverse proxy with the same header stripping and no login. Use it only when you have another reason the app is already authenticated.

## 8. Put secrets in the environment

`cairn render` writes `.cairn/env.example` with empty values. Copy the names, not a filled-in file, into an untracked `.env` or your shell.

```bash
python -c 'import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())'
```

That is the oauth2-proxy documented cookie-secret command. It reads from the operating system CSPRNG. Export the result as `OAUTH2_PROXY_COOKIE_SECRET`. Export the IdP values as `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET`.

`cairn up` refuses to start when any required variable is empty or when the cookie secret is shorter than 16 characters. It does not print the values. It also will not start while `cairn.yaml` contains a secret-shaped key, a PEM block, or a URL with a password in it.

If a `.env` file is sitting in the app directory, `cairn check` warns you. Docker can still send that file as build context. Add `.env` to `.dockerignore` before you build. The FastAPI and static fixtures already do.

## 9. Render, then read the files

```bash
cairn check .
cairn render .
```

| File | What you should see |
| --- | --- |
| `.cairn/Caddyfile` | `request_header -X-Auth-Request-User` above `forward_auth`, then `copy_headers` of only the `X-Auth-Request-*` names |
| `.cairn/oauth2-proxy.cfg` | `reverse_proxy = true`, `trusted_proxy_ips = ["172.30.10.2"]`, `code_challenge_method = "S256"`, `insecure_oidc_skip_nonce = false` |
| `.cairn/docker-compose.yml` | edge published on `127.0.0.1` for a localhost host; no secret literals |
| `.cairn/k8s/` | a sidecar proxy, `secretKeyRef` to `<name>-oidc`, and a NetworkPolicy that lets only the edge talk to the app |
| `.cairn/Dockerfile.app` | present unless the paradigm is an existing Dockerfile or Compose |

Create the Kubernetes secret yourself. Cairn does not render a Secret object.

```bash
kubectl create secret generic fastapi-oidc \
  --from-literal=client-id="$OIDC_CLIENT_ID" \
  --from-literal=client-secret="$OIDC_CLIENT_SECRET" \
  --from-literal=cookie-secret="$OAUTH2_PROXY_COOKIE_SECRET"
```

The app image in the Deployment is `cairn.dev/<name>:local`. Retag your build to that name, or edit the image field, before you apply the manifests. The Compose file builds `.cairn/Dockerfile.app` itself.

For a hostname other than localhost, set `host` and `edge.tls: internal`. Cairn then publishes `0.0.0.0`, turns on Caddy's internal CA, and marks the session cookie `Secure` with the `__Host-cairn` name. `tls: off` on a public host is a check error.

## 10. Start the local stack

```bash
cairn up .
```

The edge listens on `http://127.0.0.1:8080` for the default contract. `/healthz` answers without a login. Any other path redirects to the IdP. The Docker network gives the edge container the address `172.30.10.2`, auth `172.30.10.3`, and the app `172.30.10.4`. oauth2-proxy trusts only the edge address for forwarded headers. Every service has a pinned address so Compose cannot give `172.30.10.2` to the app.

`cairn up` is the first command that needs Docker. `detect`, `check`, and `render` do not.

## 11. When check fails

| Message | Fix |
| --- | --- |
| more than one Python web framework | Delete the dependency you are not running. |
| multiple Compose files | Keep one of `compose.yaml` or `docker-compose.yml`. |
| more than one service publishes a port | Leave `ports` on the service users open. |
| could not tell how this app runs | Add a start script, a Python dependency, a Go listener, HTML, or a Dockerfile. |
| `edge.tls off` is only valid for localhost | Set `edge.tls: internal` and a real `host`. |
| issuer must be https | Remove userinfo and use TLS. |
| needs an access rule | Set a domain, an email, a group, or `allowAllEmails`. |
| `clientSecret` is not allowed | Rename the idea to `clientSecretEnv` and export the value. |
| PEM block or credential-shaped value | Remove it. Certificates are created at runtime by Caddy when TLS is internal. |
| cookie secret must be at least 16 characters | Regenerate it. The command in `.cairn/env.example` is enough. The value is not echoed. |

Warnings do not fail the command. A warning about `idp.example.com` or `example.com` means the file is safe to render and useless to log in with.

## 12. Add a paradigm

A new paradigm needs three things, and a fixture that serves `/healthz` on `PORT`:

1. A detector branch in `src/cairn/detect.py`, plus a test that a look-alike tree does not steal an earlier match.
2. A Dockerfile or a wrap rule. Do not `COPY` a secret file. Do not invent a crypto library; call the runtime you already trust (`uvicorn`, `gunicorn`, `node`, `go`).
3. A render test that the Caddy header deletions are still present. The edge does not change because the app language changed.

Run `python -m pytest -m "not integration"` before you open a pull request. Run the integration marker when Docker is available. The integration test is the proof that a spoofed `X-Auth-Request-User` cannot cross the edge.
