"""Identity headers the edge must delete before it trusts an upstream."""

from __future__ import annotations

# Client-supplied copies of these headers are never identity.
# X-Auth-Request-* is copied back only from the oauth2-proxy response.
EDGE_IDENTITY_HEADERS: tuple[str, ...] = (
    "X-Auth-Request-User",
    "X-Auth-Request-Email",
    "X-Auth-Request-Groups",
    "X-Auth-Request-Preferred-Username",
    "X-Auth-Request-Access-Token",
    "X-Forwarded-User",
    "X-Forwarded-Email",
    "X-Forwarded-Groups",
    "X-Forwarded-Preferred-Username",
    "X-Forwarded-Access-Token",
)

# Edge mode also drops a caller-supplied Authorization header.
# The browser session is the cookie; the app does not accept a bearer from the client.
EDGE_AUTHORIZATION_HEADER = "Authorization"

COPIED_FROM_AUTH: tuple[str, ...] = (
    "X-Auth-Request-User",
    "X-Auth-Request-Email",
    "X-Auth-Request-Groups",
    "X-Auth-Request-Preferred-Username",
)

CADDY_IMAGE = "caddy:2.9.1"
OAUTH2_PROXY_IMAGE = "quay.io/oauth2-proxy/oauth2-proxy:v7.15.2"
PYTHON_IMAGE = "python:3.12-slim"
NODE_IMAGE = "node:22-alpine"
GO_IMAGE = "golang:1.22-bookworm"
GO_RUNTIME_IMAGE = "debian:bookworm-slim"

COMPOSE_SUBNET = "172.30.10.0/24"
COMPOSE_EDGE_IP = "172.30.10.2"
COMPOSE_AUTH_IP = "172.30.10.3"
COMPOSE_APP_IP = "172.30.10.4"
COMPOSE_FIRST_EXTRA_HOST = 5
SIDECAR_AUTH_IP = "127.0.0.1"
AUTH_PORT = 4180

PARADIGMS: tuple[str, ...] = (
    "static",
    "node",
    "next",
    "fastapi",
    "flask",
    "django",
    "go",
    "dockerfile",
    "compose",
)

DEFAULT_PORTS: dict[str, int] = {
    "static": 8080,
    "node": 3000,
    "next": 3000,
    "fastapi": 8000,
    "flask": 8000,
    "django": 8000,
    "go": 8080,
    "dockerfile": 8080,
    "compose": 8000,
}
