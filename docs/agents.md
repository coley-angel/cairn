# Agent workflow

Use this when a person asks to deploy, host, publish, or add SSO to an application. The installable skill is `skills/cairn/SKILL.md`. The procedure below is the same one.

## Do this

1. Install Cairn from this repository. Do not reimplement the Caddyfile from memory.
2. Run `cairn detect <app-dir>` in the application root.
3. If detect reports two frameworks or two published Compose services, stop and ask which one is the public process.
4. Edit `cairn.yaml` only for the issuer, host, access rule, public paths, and auth mode.
5. Run `cairn check <app-dir>`. Treat warnings about `idp.example.com` and `example.com` as unfinished work when the user wants a real login.
6. Run `cairn render <app-dir>`.
7. Open `.cairn/Caddyfile` and confirm `request_header -X-Auth-Request-User` appears before `forward_auth`. If a later edit removes it, put it back. Do not replace the generated file with a shorter proxy.
8. Ask the user to export `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, and `OAUTH2_PROXY_COOKIE_SECRET`. Do not write the values into the contract, the render tree, a test, or a commit.
9. Run `cairn up <app-dir>` only after the user asks to start the stack.

## Do not do this

- Do not copy the integration-test auth stub into a deployment. The CLI has no flag for it.
- Do not set `pass_user_headers`, `pass_basic_auth`, or `pass_access_token` back to true.
- Do not widen `trusted_proxy_ips` to `0.0.0.0/0`.
- Do not set `insecure_oidc_skip_nonce` to true, and do not turn off provider TLS verification.
- Do not commit `.env`, `.cairn/emails.txt` if it contains a private allow-list the user did not intend to publish, or a filled-in Kubernetes Secret.
- Do not tell the app to trust `X-Forwarded-User`. The header the edge stands behind is `X-Auth-Request-User`.

## Pick a mode

Choose `edge-oidc` unless the app already has Auth.js, Better Auth, a FastAPI OIDC client, or Django login. In that case set `auth.mode: app-oidc` and read the variable table in `docs/how-to.md`. Choose `none` only when the user says the app should not gain a login.

## Prove it

From a checkout:

```bash
python -m pytest -m "not integration"
```

With Docker:

```bash
python -m pytest -m integration
```

The integration test is the acceptance check for header stripping. A unit test that only greps the Caddyfile is not a substitute when you changed the edge.
