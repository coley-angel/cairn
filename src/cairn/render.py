"""Write a render tree and reject output that drops the security invariants."""

from __future__ import annotations

from pathlib import Path

import yaml

from cairn.caddy import render_caddyfile, stripped_headers
from cairn.compose import render_compose
from cairn.dockerfiles import render_dockerfile
from cairn.headers import COMPOSE_EDGE_IP, COPIED_FROM_AUTH, SIDECAR_AUTH_IP
from cairn.kubernetes import render_kubernetes
from cairn.model import AppContract
from cairn.proxy import render_email_file, render_oauth2_proxy

PLACEHOLDER_HTML = "<!doctype html><title>cairn</title><p>Replace this page.</p>\n"


def render_env_example(contract: AppContract) -> str:
    lines = [
        "# Leave this file as a template. Do not commit filled-in secrets.",
        "# Edge-OIDC cookie secret, using the operating system CSPRNG, as documented by oauth2-proxy:",
        "# python -c 'import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())'",
        f"{contract.auth.client_id_env}=",
        f"{contract.auth.client_secret_env}=",
    ]
    if contract.auth.mode == "edge-oidc":
        lines.append(f"{contract.auth.cookie_secret_env}=")
    if contract.paradigm == "django":
        lines.append("DJANGO_SECRET_KEY=")
    lines.append("")
    return "\n".join(lines)


def render_tree(
    app_dir: Path,
    contract: AppContract,
    *,
    auth_backend: str = "oauth2-proxy",
    dest: Path | None = None,
) -> Path:
    if auth_backend not in {"oauth2-proxy", "stub"}:
        raise ValueError("auth_backend must be oauth2-proxy or stub.")
    target = dest or (app_dir / ".cairn")
    target.mkdir(parents=True, exist_ok=True)
    caddy = render_caddyfile(contract, auth_upstream="auth", sidecar=False)
    sidecar_caddy = render_caddyfile(contract, auth_upstream="auth", sidecar=True)
    proxy = render_oauth2_proxy(contract, sidecar=False) if contract.auth.mode == "edge-oidc" else None
    sidecar_proxy = render_oauth2_proxy(contract, sidecar=True) if contract.auth.mode == "edge-oidc" else None
    dockerfile = render_dockerfile(contract, app_dir)
    compose = render_compose(contract, app_dir, auth_backend=auth_backend)
    (target / "Caddyfile").write_text(caddy, encoding="utf-8")
    (target / "docker-compose.yml").write_text(compose, encoding="utf-8")
    (target / "env.example").write_text(render_env_example(contract), encoding="utf-8")
    if proxy is not None and auth_backend == "oauth2-proxy":
        (target / "oauth2-proxy.cfg").write_text(proxy, encoding="utf-8")
    elif (target / "oauth2-proxy.cfg").exists():
        (target / "oauth2-proxy.cfg").unlink()
    if contract.auth.emails and auth_backend == "oauth2-proxy":
        (target / "emails.txt").write_text(render_email_file(contract), encoding="utf-8")
    if dockerfile is not None:
        (target / "Dockerfile.app").write_text(dockerfile, encoding="utf-8")
    if contract.paradigm == "static" and not (app_dir / "server.py").is_file():
        from importlib.resources import files

        (target / "static_server.py").write_text(
            files("cairn.stub").joinpath("static_server.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        if not (app_dir / "public").is_dir() and not (app_dir / "index.html").is_file():
            (target / "placeholder-index.html").write_text(PLACEHOLDER_HTML, encoding="utf-8")
    if auth_backend == "stub":
        from importlib.resources import files

        stub_dir = target / "stub"
        stub_dir.mkdir(exist_ok=True)
        (stub_dir / "auth_stub.py").write_text(
            files("cairn.stub").joinpath("auth_stub.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    k8s = target / "k8s"
    k8s.mkdir(exist_ok=True)
    for name, body in render_kubernetes(contract, caddyfile=sidecar_caddy, proxy_config=sidecar_proxy).items():
        (k8s / name).write_text(body, encoding="utf-8")
    problems = audit_tree(target, contract, auth_backend=auth_backend)
    if problems:
        raise RuntimeError("rendered output failed its own audit:\n" + "\n".join(problems))
    return target


def audit_tree(dest: Path, contract: AppContract, *, auth_backend: str) -> list[str]:
    errors: list[str] = []
    caddy = (dest / "Caddyfile").read_text(encoding="utf-8")
    errors.extend(_audit_caddy(caddy, contract))
    compose = (dest / "docker-compose.yml").read_text(encoding="utf-8")
    errors.extend(_audit_text(compose, "docker-compose.yml"))
    if contract.auth.mode == "edge-oidc" and auth_backend == "oauth2-proxy":
        proxy_path = dest / "oauth2-proxy.cfg"
        if not proxy_path.is_file():
            errors.append("oauth2-proxy.cfg is missing.")
        else:
            errors.extend(_audit_proxy(proxy_path.read_text(encoding="utf-8"), COMPOSE_EDGE_IP))
    for path in (dest / "k8s").glob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        errors.extend(_audit_text(text, path.name))
        if "stringData:" in text or "kind: Secret" in text:
            errors.append(f"{path.name} must not embed a Secret. Reference secretKeyRef instead.")
        try:
            list(yaml.safe_load_all(text))
        except yaml.YAMLError as exc:
            errors.append(f"{path.name} is not valid YAML: {exc}")
    if contract.auth.mode == "edge-oidc":
        configmap = (dest / "k8s" / "configmap.yaml").read_text(encoding="utf-8")
        if f"trusted_proxy_ips = [\"{SIDECAR_AUTH_IP}\"]" not in configmap:
            errors.append("Kubernetes auth config must trust only the sidecar loopback address.")
        if "insecure_oidc_skip_nonce = false" not in configmap:
            errors.append("Kubernetes auth config must verify the OIDC nonce.")
    try:
        yaml.safe_load((dest / "docker-compose.yml").read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        errors.append(f"docker-compose.yml is not valid YAML: {exc}")
    return errors


def _audit_caddy(caddy: str, contract: AppContract) -> list[str]:
    errors: list[str] = []
    errors.extend(_audit_text(caddy, "Caddyfile"))
    for header in stripped_headers(contract.auth.mode):
        if f"request_header -{header}" not in caddy:
            errors.append(f"Caddyfile does not delete client-supplied {header} before authentication.")
    if contract.auth.mode == "edge-oidc":
        strip_at = caddy.find("request_header -X-Auth-Request-User")
        auth_at = caddy.find("forward_auth")
        copy_at = caddy.find("copy_headers")
        if -1 in {strip_at, auth_at, copy_at} or not strip_at < auth_at < copy_at:
            errors.append("Caddyfile must delete identity headers, then forward_auth, then copy_headers.")
        for header in stripped_headers("edge-oidc"):
            if f"header_up -{header}" not in caddy:
                errors.append(f"forward_auth does not delete {header} on the auth subrequest.")
        copy_lines = [line.strip() for line in caddy.splitlines() if line.strip().startswith("copy_headers")]
        for line in copy_lines:
            names = line.split()[1:]
            if tuple(names) != COPIED_FROM_AUTH:
                errors.append("copy_headers must copy only X-Auth-Request identity headers from oauth2-proxy.")
        if "X-Forwarded-User" in " ".join(copy_lines):
            errors.append("copy_headers must not copy X-Forwarded-User.")
    else:
        if "forward_auth" in caddy:
            errors.append("app-oidc and none modes must not insert forward_auth.")
        if "request_header -Authorization" in caddy:
            errors.append("app-oidc and none modes must leave Authorization for the application.")
    return errors


def _audit_proxy(text: str, trusted_ip: str) -> list[str]:
    errors: list[str] = []
    required = {
        "reverse_proxy = true": "reverse_proxy must be true.",
        f'trusted_proxy_ips = ["{trusted_ip}"]': "trusted_proxy_ips must be the Caddy hop only.",
        "pass_basic_auth = false": "pass_basic_auth must be false so legacy X-Forwarded-User is not emitted.",
        "pass_user_headers = false": "pass_user_headers must be false.",
        "pass_access_token = false": "pass_access_token must be false.",
        "pass_authorization_header = false": "pass_authorization_header must be false.",
        "set_xauthrequest = true": "set_xauthrequest must be true.",
        'code_challenge_method = "S256"': "PKCE must use S256.",
        "insecure_oidc_skip_nonce = false": "OIDC nonce verification must stay on.",
        "ssl_insecure_skip_verify = false": "provider TLS verification must stay on.",
        "cookie_httponly = true": "the session cookie must be HttpOnly.",
    }
    for snippet, message in required.items():
        if snippet not in text:
            errors.append(message)
    if "0.0.0.0/0" in text or "::/0" in text:
        errors.append("trusted proxies must not be the whole internet.")
    for forbidden in ("client_secret", "cookie_secret", "client_id"):
        if forbidden in text:
            errors.append(f"oauth2-proxy.cfg must not contain {forbidden}. Put it in the environment.")
    errors.extend(_audit_text(text, "oauth2-proxy.cfg"))
    return errors


def _audit_text(text: str, name: str) -> list[str]:
    errors: list[str] = []
    if "-----BEGIN " in text:
        errors.append(f"{name} contains a PEM block.")
    for prefix in ("AKIA", "ASIA", "ghp_", "sk_live_", "sk_test_"):
        if prefix in text:
            errors.append(f"{name} contains a credential-shaped value.")
    return errors
