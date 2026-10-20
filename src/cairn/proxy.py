"""oauth2-proxy config. Secrets stay in the environment, never in this file."""

from __future__ import annotations

from cairn.headers import AUTH_PORT, COMPOSE_EDGE_IP, SIDECAR_AUTH_IP
from cairn.model import AppContract, effective_email_domains, public_origin


def render_oauth2_proxy(contract: AppContract, *, sidecar: bool) -> str:
    auth = contract.auth
    origin = public_origin(contract)
    trusted = SIDECAR_AUTH_IP if sidecar else COMPOSE_EDGE_IP
    listen = f"{SIDECAR_AUTH_IP}:{AUTH_PORT}" if sidecar else f"0.0.0.0:{AUTH_PORT}"
    secure = contract.edge.tls == "internal"
    domains = effective_email_domains(auth)
    scope = "openid email profile"
    if auth.groups:
        scope = f"{scope} groups"
    lines = [
        'provider = "oidc"',
        f"oidc_issuer_url = {_toml(auth.issuer or '')}",
        f"redirect_url = {_toml(origin + '/oauth2/callback')}",
        'upstreams = ["static://202"]',
        f"http_address = {_toml(listen)}",
        "reverse_proxy = true",
        f"trusted_proxy_ips = [{_toml(trusted)}]",
        "email_domains = [" + ", ".join(_toml(domain) for domain in domains) + "]",
        "set_xauthrequest = true",
        "set_basic_auth = false",
        "set_authorization_header = false",
        "pass_basic_auth = false",
        "pass_user_headers = false",
        "pass_access_token = false",
        "pass_authorization_header = false",
        "skip_auth_strip_headers = true",
        f"cookie_secure = {'true' if secure else 'false'}",
        "cookie_httponly = true",
        'cookie_samesite = "lax"',
        f"cookie_name = {_toml('__Host-cairn' if secure else '_cairn')}",
        'code_challenge_method = "S256"',
        "insecure_oidc_skip_nonce = false",
        "insecure_oidc_allow_unverified_email = false",
        "ssl_insecure_skip_verify = false",
        "show_debug_on_error = false",
        "display_htpasswd_form = false",
        "session_cookie_minimal = true",
        "force_https = " + ("true" if secure else "false"),
        f"whitelist_domains = [{_toml(contract.host)}]",
        f"scope = {_toml(scope)}",
        'oidc_groups_claim = "groups"',
        'real_client_ip_header = "X-Real-IP"',
    ]
    if auth.emails:
        lines.append('authenticated_emails_file = "/etc/cairn/emails.txt"')
    if auth.groups:
        lines.append("allowed_groups = [" + ", ".join(_toml(group) for group in auth.groups) + "]")
    if auth.api_paths:
        routes = ", ".join(_toml(_api_regex(path)) for path in auth.api_paths)
        lines.append(f"api_routes = [{routes}]")
    lines.append("")
    return "\n".join(lines)


def render_email_file(contract: AppContract) -> str:
    return "".join(f"{email}\n" for email in contract.auth.emails)


def _api_regex(path: str) -> str:
    if path.endswith("/"):
        base = path.rstrip("/")
        return f"^{base}/"
    return f"^{path}$"


def _toml(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
