"""Compose for a generated app, or a wrap of an existing Compose file."""

from __future__ import annotations

from pathlib import Path

import yaml

from cairn.headers import (
    CADDY_IMAGE,
    COMPOSE_APP_IP,
    COMPOSE_AUTH_IP,
    COMPOSE_EDGE_IP,
    COMPOSE_FIRST_EXTRA_HOST,
    COMPOSE_SUBNET,
    OAUTH2_PROXY_IMAGE,
    PYTHON_IMAGE,
)
from cairn.model import AppContract, publish_address


def render_compose(
    contract: AppContract,
    app_dir: Path,
    *,
    auth_backend: str = "oauth2-proxy",
) -> str:
    if contract.paradigm == "compose":
        return _wrap(contract, app_dir, auth_backend=auth_backend)
    return _generated(contract, auth_backend=auth_backend)


def _generated(contract: AppContract, *, auth_backend: str) -> str:
    publish = publish_address(contract)
    port = contract.edge.bind_port
    app_env = _app_environment(contract)
    services = [
        _edge_service(contract, publish, port, auth_backend),
        _auth_service(contract, auth_backend),
        "  app:",
        "    build:",
        "      context: ..",
        f"      dockerfile: {_dockerfile_path(contract)}",
        "    environment:",
        *app_env,
        "    networks:",
        "      cairn:",
        f"        ipv4_address: {COMPOSE_APP_IP}",
    ]
    lines = [f"name: {contract.name}", "services:"]
    lines.extend(services)
    lines.extend(_network())
    return "\n".join(lines) + "\n"


def _dockerfile_path(contract: AppContract) -> str:
    if contract.paradigm == "dockerfile":
        return "Dockerfile"
    return ".cairn/Dockerfile.app"


def _edge_service(contract: AppContract, publish: str, port: int, auth_backend: str) -> str:
    depends = ["auth", _upstream_name(contract)] if contract.auth.mode == "edge-oidc" or auth_backend == "stub" else [_upstream_name(contract)]
    if contract.auth.mode != "edge-oidc" and auth_backend != "stub":
        depends = [_upstream_name(contract)]
    dep_lines = "\n".join(f"      - {item}" for item in depends)
    return f"""\
  edge:
    image: {CADDY_IMAGE}
    ports:
      - "{publish}:{port}:{port}"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
    networks:
      cairn:
        ipv4_address: {COMPOSE_EDGE_IP}
    depends_on:
{dep_lines}"""


def _auth_service(contract: AppContract, auth_backend: str) -> str:
    if contract.auth.mode != "edge-oidc" and auth_backend != "stub":
        return ""
    if auth_backend == "stub":
        return f"""\
  auth:
    image: {PYTHON_IMAGE}
    command: ["python", "/opt/cairn/auth_stub.py"]
    volumes:
      - ./stub/auth_stub.py:/opt/cairn/auth_stub.py:ro
    environment:
      PORT: "4180"
    networks:
      cairn:
        ipv4_address: {COMPOSE_AUTH_IP}"""
    volume_lines = ["      - ./oauth2-proxy.cfg:/etc/cairn/oauth2-proxy.cfg:ro"]
    if contract.auth.emails:
        volume_lines.append("      - ./emails.txt:/etc/cairn/emails.txt:ro")
    volumes = "\n".join(volume_lines)
    return f"""\
  auth:
    image: {OAUTH2_PROXY_IMAGE}
    command: ["--config", "/etc/cairn/oauth2-proxy.cfg"]
    environment:
      OAUTH2_PROXY_CLIENT_ID: ${{{contract.auth.client_id_env}}}
      OAUTH2_PROXY_CLIENT_SECRET: ${{{contract.auth.client_secret_env}}}
      OAUTH2_PROXY_COOKIE_SECRET: ${{{contract.auth.cookie_secret_env}}}
    volumes:
{volumes}
    networks:
      cairn:
        ipv4_address: {COMPOSE_AUTH_IP}"""


def _app_environment(contract: AppContract) -> list[str]:
    lines = [f'      PORT: "{contract.port}"']
    if contract.paradigm == "django":
        lines.append("      DJANGO_SECRET_KEY: ${DJANGO_SECRET_KEY}")
        lines.append("      DJANGO_SETTINGS_MODULE: app.settings")
    if contract.auth.mode == "app-oidc" and contract.auth.issuer:
        origin = _origin_for_env(contract)
        lines.append(f"      OIDC_ISSUER: {contract.auth.issuer}")
        lines.append(f"      OIDC_CLIENT_ID: ${{{contract.auth.client_id_env}}}")
        lines.append(f"      OIDC_CLIENT_SECRET: ${{{contract.auth.client_secret_env}}}")
        lines.append(f"      OIDC_REDIRECT_URI: {origin}/callback")
    return lines


def _origin_for_env(contract: AppContract) -> str:
    from cairn.model import public_origin

    return public_origin(contract)


def _upstream_name(contract: AppContract) -> str:
    if contract.paradigm == "compose" and contract.runtime.public_service:
        return contract.runtime.public_service
    return "app"


def _network() -> list[str]:
    return [
        "networks:",
        "  cairn:",
        "    driver: bridge",
        "    ipam:",
        "      config:",
        f"        - subnet: {COMPOSE_SUBNET}",
        "          gateway: 172.30.10.1",
    ]


def _wrap(contract: AppContract, app_dir: Path, *, auth_backend: str) -> str:
    path = _compose_path(app_dir)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        document = {}
    services = document.get("services")
    if not isinstance(services, dict):
        services = {}
    public = contract.runtime.public_service or ""
    if public in services and isinstance(services[public], dict):
        service = services[public]
        service.pop("ports", None)
        expose = service.get("expose")
        if not isinstance(expose, list):
            expose = []
        if str(contract.port) not in {str(item) for item in expose}:
            expose.append(str(contract.port))
        service["expose"] = expose
        if "build" in service:
            service["build"] = _rewrite_build(service["build"])
    publish = publish_address(contract)
    edge, auth = _service_maps(contract, publish, auth_backend)
    services = {"edge": edge, "auth": auth, **services} if auth else {"edge": edge, **services}
    _pin_wrapped_services(services, public)
    rendered = {
        "name": contract.name,
        "services": services,
        "networks": {
            "cairn": {
                "driver": "bridge",
                "ipam": {"config": [{"subnet": COMPOSE_SUBNET, "gateway": "172.30.10.1"}]},
            }
        },
    }
    return yaml.safe_dump(rendered, sort_keys=False)


def _service_maps(contract: AppContract, publish: str, auth_backend: str) -> tuple[dict[str, object], dict[str, object] | None]:
    port = contract.edge.bind_port
    upstream = _upstream_name(contract)
    edge: dict[str, object] = {
        "image": CADDY_IMAGE,
        "ports": [f"{publish}:{port}:{port}"],
        "volumes": ["./Caddyfile:/etc/caddy/Caddyfile:ro"],
        "networks": {"cairn": {"ipv4_address": COMPOSE_EDGE_IP}},
        "depends_on": [upstream],
    }
    if contract.auth.mode != "edge-oidc" and auth_backend != "stub":
        return edge, None
    edge["depends_on"] = ["auth", upstream]
    if auth_backend == "stub":
        auth: dict[str, object] = {
            "image": PYTHON_IMAGE,
            "command": ["python", "/opt/cairn/auth_stub.py"],
            "volumes": ["./stub/auth_stub.py:/opt/cairn/auth_stub.py:ro"],
            "environment": {"PORT": "4180"},
            "networks": {"cairn": {"ipv4_address": COMPOSE_AUTH_IP}},
        }
        return edge, auth
    volumes = ["./oauth2-proxy.cfg:/etc/cairn/oauth2-proxy.cfg:ro"]
    if contract.auth.emails:
        volumes.append("./emails.txt:/etc/cairn/emails.txt:ro")
    auth = {
        "image": OAUTH2_PROXY_IMAGE,
        "command": ["--config", "/etc/cairn/oauth2-proxy.cfg"],
        "environment": {
            "OAUTH2_PROXY_CLIENT_ID": f"${{{contract.auth.client_id_env}}}",
            "OAUTH2_PROXY_CLIENT_SECRET": f"${{{contract.auth.client_secret_env}}}",
            "OAUTH2_PROXY_COOKIE_SECRET": f"${{{contract.auth.cookie_secret_env}}}",
        },
        "volumes": volumes,
        "networks": {"cairn": {"ipv4_address": COMPOSE_AUTH_IP}},
    }
    return edge, auth


def _pin_service(service: dict[str, object], address: str) -> None:
    service["networks"] = {"cairn": {"ipv4_address": address}}


def _pin_wrapped_services(services: dict[str, object], public: str) -> None:
    reserved = {"edge", "auth"}
    if public in services and isinstance(services[public], dict):
        _pin_service(services[public], COMPOSE_APP_IP)
        reserved.add(public)
    host = COMPOSE_FIRST_EXTRA_HOST
    for name, service in services.items():
        if name in reserved or not isinstance(service, dict):
            continue
        if host > 254:
            raise ValueError("the Compose network has no free address for every service")
        _pin_service(service, f"172.30.10.{host}")
        host += 1


def _rewrite_build(build: object) -> object:
    if isinstance(build, str):
        return {"context": _prefix_relative(build), "dockerfile": "Dockerfile"}
    if isinstance(build, dict):
        updated = dict(build)
        updated["context"] = _prefix_relative(str(updated.get("context", ".")))
        return updated
    return build


def _prefix_relative(context: str) -> str:
    if context.startswith("/") or "://" in context:
        return context
    trimmed = context[2:] if context.startswith("./") else context
    if trimmed in {"", "."}:
        return ".."
    if trimmed.startswith("../"):
        return "../" + trimmed
    return "../" + trimmed


def _compose_path(app_dir: Path) -> Path:
    for name in ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml"):
        path = app_dir / name
        if path.is_file():
            return path
    raise FileNotFoundError(f"no Compose file in {app_dir}")
