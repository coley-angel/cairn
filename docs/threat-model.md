# Threat model

Cairn's job is to make the boring edge the same for every app: one OIDC login, one access rule, and no way for a browser to invent an identity header. It is not a sandbox, a multi-tenant platform, or a substitute for an application security review.

## What we are protecting

- The decision of who may open the app.
- The identity headers the app is allowed to believe.
- The IdP client secret and the session cookie secret, which must stay out of git and out of the rendered config.
- The hop that is allowed to tell oauth2-proxy what the original scheme and host were.

## Identity headers

oauth2-proxy's auth subrequest answers with `X-Auth-Request-User`, `X-Auth-Request-Email`, `X-Auth-Request-Groups`, and `X-Auth-Request-Preferred-Username` when `set_xauthrequest` is on. Caddy's `copy_headers` copies those response headers onto the request that continues to the app.

That is safe only if a client cannot send the same headers first. [GHSA-7r4p-vjf4-gxv4](https://github.com/caddyserver/caddy/security/advisories/GHSA-7r4p-vjf4-gxv4) describes a Caddy 2.10 regression: `copy_headers` does not delete a client-supplied header when the auth response omits that header. A caller with any valid session, or a caller hitting a route that skips auth, can then supply `X-Auth-Request-User`.

Cairn does both of the following:

1. It pins the edge image to `caddy:2.9.1`, which is before that regression.
2. It deletes the identity headers on the request before `forward_auth`, deletes them again on the auth subrequest, and deletes them on every public reverse proxy. `copy_headers` is what puts the proxy's values back, and only those values.

The integration test requests `/edge-probe` with `X-Auth-Request-User: attacker` and expects the app to see no user. It then presents the test cookie plus the same spoofed header and expects the stub user, not the attacker.

Legacy headers are turned off at the source. `pass_basic_auth` and `pass_user_headers` default to true in oauth2-proxy; Cairn sets both to false, and it does not set `pass_access_token` or `pass_authorization_header`. The app therefore has no business reading `X-Forwarded-User` or a bearer token inserted by the edge.

In `edge-oidc` mode the client `Authorization` header is stripped. The browser session is the cookie. `app-oidc` and `none` leave `Authorization` alone so the application can run its own bearer or session protocol, and they still strip the proxy identity headers.

## The proxy hop

With `--reverse-proxy` and no trusted proxy list, oauth2-proxy trusts every source address for `X-Forwarded-*`. Cairn never leaves that default in place.

- Compose gives the edge container `172.30.10.2` and sets `trusted_proxy_ips` to that address. Auth is `172.30.10.3` and the app is `172.30.10.4`, so another container cannot take the trusted hop. The auth container has no published host port.
- Kubernetes runs oauth2-proxy in the edge pod and listens on `127.0.0.1:4180`, trusted as `127.0.0.1`. Other pods are not on that loopback. A NetworkPolicy also limits app ingress to pods labeled as the edge.

`0.0.0.0/0` in a rendered file fails the audit inside `cairn render`.

## Session cookie

The cookie is HttpOnly and SameSite=Lax. On `tls: internal` it is Secure and named `__Host-cairn`, which forbids a Domain attribute. On localhost HTTP, Secure would prevent the browser from storing it, so `cookie_secure` is false and the listener is bound to `127.0.0.1` only.

`tls: off` combined with any host other than `localhost` or `127.0.0.1` is a check error. A LAN hostname needs `tls: internal`.

The cookie secret is not generated into a file. `cairn up` requires `OAUTH2_PROXY_COOKIE_SECRET` to be at least 16 non-space characters and refuses well-known placeholders. Generate it with the oauth2-proxy command, which calls `os.urandom`:

```bash
python -c 'import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())'
```

Cairn does not implement a cipher. oauth2-proxy encrypts the cookie. PKCE is S256. `insecure_oidc_skip_nonce` is set to false because the upstream default is to skip the nonce. Provider certificate verification stays on.

## Secrets and certificates

The contract may name environment variables. It may not hold a client secret, a cookie secret, a cloud key, a PAT, a PEM certificate, or a URL with userinfo. The loader's error text does not echo the rejected value.

No certificate is committed. `edge.tls: internal` asks Caddy to create a local CA at runtime. That CA is for a private name on a machine you control. Before you point a real hostname at a public certificate, inspect it:

```bash
openssl x509 -text -noout -in <certificate-file>
```

Check the dates, a public key of at least RSA 2048 or P-256, a SHA-2 signature, and whether the issuer and subject are the same. A self-signed certificate from Caddy's internal CA is expected for local TLS and is not a public-web certificate.

Django's `SECRET_KEY` is `DJANGO_SECRET_KEY` in the environment. The fixture settings module has no fallback string.

## What this does not stop

- A vulnerability in the application after a request has been authenticated.
- A malicious or compromised IdP.
- An application that opens its own port on the host and bypasses Caddy. Compose does not publish the app port; do not add one back.
- A person who builds a Docker context that still contains `.env`. `cairn check` warns. `.dockerignore` is the fix.
- Cluster policy beyond the rendered NetworkPolicy. The edge Service accepts ingress on its HTTP port so a load balancer can reach it. Narrow that to your ingress namespace if the cluster is shared.
- The integration stub. It lives under `src/cairn/stub/` and is copied only when tests call `render_tree(..., auth_backend="stub")`. There is no CLI switch. The stub cookie is a fixed test fixture, not an IdP credential, and it is useless against oauth2-proxy.

## Reporting

Do not open a public issue that contains a live client secret, cookie secret, or certificate. Describe the render inputs and the header or config field you expected.
