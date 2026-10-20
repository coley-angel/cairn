"""Caddyfile generation. Identity headers are deleted before forward_auth."""

from __future__ import annotations

from cairn.headers import (
    AUTH_PORT,
    COPIED_FROM_AUTH,
    EDGE_AUTHORIZATION_HEADER,
    EDGE_IDENTITY_HEADERS,
    SIDECAR_AUTH_IP,
)
from cairn.model import AppContract


def stripped_headers(mode: str) -> tuple[str, ...]:
    if mode == "edge-oidc":
        return EDGE_IDENTITY_HEADERS + (EDGE_AUTHORIZATION_HEADER,)
    return EDGE_IDENTITY_HEADERS


def render_caddyfile(contract: AppContract, *, auth_upstream: str, sidecar: bool = False) -> str:
    upstream = _app_upstream(contract)
    lines: list[str] = ["{"]
    if contract.edge.tls == "off":
        lines.append("\tauto_https off")
    lines.append("\tadmin off")
    lines.append("}")
    lines.append("")
    lines.append(_site_address(contract) + " {")
    if contract.edge.tls == "internal":
        lines.append("\ttls internal")
    stripped = stripped_headers(contract.auth.mode)
    lines.append("\troute {")
    for header in stripped:
        lines.append(f"\t\trequest_header -{header}")
    if contract.auth.mode == "edge-oidc":
        lines.extend(_oauth_handle(auth_upstream))
        for path in contract.auth.public_paths:
            lines.extend(
                _proxy_handle(
                    _matcher(path),
                    upstream,
                    auth=False,
                    auth_upstream=auth_upstream,
                    deny="redirect",
                    strip=stripped,
                )
            )
        for path in contract.auth.api_paths:
            lines.extend(
                _proxy_handle(
                    _matcher(path),
                    upstream,
                    auth=True,
                    auth_upstream=auth_upstream,
                    deny="unauthorized",
                    strip=stripped,
                )
            )
        lines.extend(
            _proxy_handle("", upstream, auth=True, auth_upstream=auth_upstream, deny="redirect", strip=stripped)
        )
    else:
        lines.extend(
            _proxy_handle("", upstream, auth=False, auth_upstream=auth_upstream, deny="redirect", strip=stripped)
        )
    lines.append("\t}")
    lines.append("}")
    lines.append("")
    text = "\n".join(lines)
    if sidecar:
        text = text.replace(f"{auth_upstream}:{AUTH_PORT}", f"{SIDECAR_AUTH_IP}:{AUTH_PORT}")
    return text


def _site_address(contract: AppContract) -> str:
    if contract.edge.tls == "internal":
        port = contract.edge.bind_port
        if port == 443:
            return f"https://{contract.host}"
        return f"https://{contract.host}:{port}"
    return f":{contract.edge.bind_port}"


def _app_upstream(contract: AppContract) -> str:
    name = contract.runtime.public_service if contract.paradigm == "compose" and contract.runtime.public_service else "app"
    return f"{name}:{contract.port}"


def _matcher(path: str) -> str:
    if path.endswith("/"):
        base = path.rstrip("/") or ""
        return f"{base}/*"
    return path


def _oauth_handle(auth_upstream: str) -> list[str]:
    return [
        "\t\thandle /oauth2/* {",
        f"\t\t\treverse_proxy {auth_upstream}:{AUTH_PORT} {{",
        "\t\t\t\theader_up X-Real-IP {remote_host}",
        "\t\t\t\theader_up X-Forwarded-Uri {uri}",
        "\t\t\t}",
        "\t\t}",
    ]


def _proxy_handle(
    matcher: str,
    upstream: str,
    *,
    auth: bool,
    auth_upstream: str,
    deny: str,
    strip: tuple[str, ...],
) -> list[str]:
    opener = f"\t\thandle {matcher} {{".rstrip()
    if matcher:
        opener = f"\t\thandle {matcher} {{"
    else:
        opener = "\t\thandle {"
    lines = [opener]
    if auth:
        lines.append(f"\t\t\tforward_auth {auth_upstream}:{AUTH_PORT} {{")
        lines.append("\t\t\t\turi /oauth2/auth")
        for header in EDGE_IDENTITY_HEADERS + (EDGE_AUTHORIZATION_HEADER,):
            lines.append(f"\t\t\t\theader_up -{header}")
        lines.append("\t\t\t\theader_up X-Real-IP {remote_host}")
        lines.append("\t\t\t\theader_up X-Forwarded-Uri {uri}")
        lines.append("\t\t\t\tcopy_headers " + " ".join(COPIED_FROM_AUTH))
        lines.append("\t\t\t\t@denied status 401")
        lines.append("\t\t\t\thandle_response @denied {")
        if deny == "unauthorized":
            lines.append("\t\t\t\t\trespond 401")
        else:
            lines.append("\t\t\t\t\tredir * /oauth2/sign_in?rd={scheme}://{host}{uri} 302")
        lines.append("\t\t\t\t}")
        lines.append("\t\t\t}")
    if auth:
        lines.append(f"\t\t\treverse_proxy {upstream}")
    else:
        lines.append(f"\t\t\treverse_proxy {upstream} {{")
        for header in strip:
            lines.append(f"\t\t\t\theader_up -{header}")
        lines.append("\t\t\t}")
    lines.append("\t\t}")
    return lines
