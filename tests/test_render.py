from dataclasses import replace
from pathlib import Path

import yaml

from cairn.detect import detect_app
from cairn.headers import CADDY_IMAGE, COMPOSE_APP_IP, COMPOSE_AUTH_IP, COMPOSE_EDGE_IP, OAUTH2_PROXY_IMAGE, SIDECAR_AUTH_IP
from cairn.model import dump_contract
from cairn.render import render_tree
from tests.conftest import FIXTURES, ROOT

GOLDEN = ROOT / "tests" / "golden" / "fastapi"


def _render(name: str, tmp_path: Path, **kwargs):
    contract, _ = detect_app(FIXTURES / name)
    dest = render_tree(FIXTURES / name, contract, dest=tmp_path / name, **kwargs)
    return contract, dest


def test_fastapi_golden(tmp_path: Path):
    _contract, dest = _render("fastapi", tmp_path)
    for relative in ("Caddyfile", "docker-compose.yml", "oauth2-proxy.cfg", "Dockerfile.app", "env.example"):
        assert (dest / relative).read_text(encoding="utf-8") == (GOLDEN / relative).read_text(encoding="utf-8")
    for name in ("configmap.yaml", "deployment-app.yaml", "deployment-edge.yaml", "networkpolicy.yaml"):
        assert (dest / "k8s" / name).read_text(encoding="utf-8") == (GOLDEN / "k8s" / name).read_text(encoding="utf-8")


def test_header_order_and_public_strip(tmp_path: Path):
    _contract, dest = _render("fastapi", tmp_path)
    caddy = (dest / "Caddyfile").read_text(encoding="utf-8")
    assert caddy.index("request_header -X-Auth-Request-User") < caddy.index("forward_auth") < caddy.index("copy_headers")
    assert "header_up -Authorization" in caddy
    assert "copy_headers X-Auth-Request-User X-Auth-Request-Email X-Auth-Request-Groups X-Auth-Request-Preferred-Username" in caddy
    assert "copy_headers X-Forwarded-User" not in caddy
    assert f"image: {CADDY_IMAGE}" in (dest / "docker-compose.yml").read_text(encoding="utf-8")


def test_proxy_pins_the_caddy_hop_and_refuses_legacy_headers(tmp_path: Path):
    _contract, dest = _render("fastapi", tmp_path)
    proxy = (dest / "oauth2-proxy.cfg").read_text(encoding="utf-8")
    assert f'trusted_proxy_ips = ["{COMPOSE_EDGE_IP}"]' in proxy
    assert "0.0.0.0/0" not in proxy
    assert "pass_basic_auth = false" in proxy
    assert "pass_user_headers = false" in proxy
    assert "pass_access_token = false" in proxy
    assert "insecure_oidc_skip_nonce = false" in proxy
    assert 'code_challenge_method = "S256"' in proxy
    assert "cookie_secure = false" in proxy
    assert "client_secret" not in proxy
    assert "cookie_secret" not in proxy
    compose = (dest / "docker-compose.yml").read_text(encoding="utf-8")
    assert f"image: {OAUTH2_PROXY_IMAGE}" in compose
    assert "OAUTH2_PROXY_CLIENT_SECRET: ${OIDC_CLIENT_SECRET}" in compose
    assert "127.0.0.1:8080:8080" in compose


def test_kubernetes_sidecar_and_secret_refs(tmp_path: Path):
    _contract, dest = _render("fastapi", tmp_path)
    configmap = (dest / "k8s" / "configmap.yaml").read_text(encoding="utf-8")
    edge = (dest / "k8s" / "deployment-edge.yaml").read_text(encoding="utf-8")
    app = (dest / "k8s" / "deployment-app.yaml").read_text(encoding="utf-8")
    policy = (dest / "k8s" / "networkpolicy.yaml").read_text(encoding="utf-8")
    assert f'trusted_proxy_ips = ["{SIDECAR_AUTH_IP}"]' in configmap
    assert "forward_auth 127.0.0.1:4180" in configmap
    assert "secretKeyRef" in edge
    assert "kind: Secret" not in edge
    assert "stringData" not in edge
    assert "automountServiceAccountToken: false" in app
    assert "runAsNonRoot: true" in app
    assert "cairn.dev/role: edge" in policy
    for path in (dest / "k8s").glob("*.yaml"):
        list(yaml.safe_load_all(path.read_text(encoding="utf-8")))


def test_tls_internal_cookie_and_publish(tmp_path: Path):
    contract, _ = detect_app(FIXTURES / "fastapi")
    contract = replace(
        contract,
        host="app.example.com",
        edge=replace(contract.edge, tls="internal", bind_port=8443),
        auth=replace(contract.auth, issuer="https://login.example.com", email_domains=("example.com",)),
    )
    dest = render_tree(FIXTURES / "fastapi", contract, dest=tmp_path / "tls")
    proxy = (dest / "oauth2-proxy.cfg").read_text(encoding="utf-8")
    caddy = (dest / "Caddyfile").read_text(encoding="utf-8")
    compose = (dest / "docker-compose.yml").read_text(encoding="utf-8")
    assert "cookie_secure = true" in proxy
    assert 'cookie_name = "__Host-cairn"' in proxy
    assert "tls internal" in caddy
    assert "-----BEGIN " not in caddy
    assert "0.0.0.0:8443:8443" in compose
    assert "https://app.example.com:8443/oauth2/callback" in proxy


def test_app_oidc_does_not_strip_authorization(tmp_path: Path):
    contract, _ = detect_app(FIXTURES / "fastapi")
    contract = replace(contract, auth=replace(contract.auth, mode="app-oidc"))
    dest = render_tree(FIXTURES / "fastapi", contract, dest=tmp_path / "appoidc")
    caddy = (dest / "Caddyfile").read_text(encoding="utf-8")
    compose = (dest / "docker-compose.yml").read_text(encoding="utf-8")
    assert "forward_auth" not in caddy
    assert "request_header -X-Auth-Request-User" in caddy
    assert "request_header -Authorization" not in caddy
    assert "OIDC_CLIENT_SECRET: ${OIDC_CLIENT_SECRET}" in compose
    assert "oauth2-proxy" not in compose
    assert not (dest / "oauth2-proxy.cfg").exists()


def test_groups_and_api_paths(tmp_path: Path):
    contract, _ = detect_app(FIXTURES / "fastapi")
    contract = replace(
        contract,
        auth=replace(
            contract.auth,
            email_domains=(),
            groups=("engineering",),
            api_paths=("/api/",),
            emails=("ada@example.com",),
        ),
    )
    dest = render_tree(FIXTURES / "fastapi", contract, dest=tmp_path / "groups")
    proxy = (dest / "oauth2-proxy.cfg").read_text(encoding="utf-8")
    caddy = (dest / "Caddyfile").read_text(encoding="utf-8")
    assert 'allowed_groups = ["engineering"]' in proxy
    assert "authenticated_emails_file" in proxy
    assert "handle /api/*" in caddy
    assert "respond 401" in caddy
    assert (dest / "emails.txt").read_text(encoding="utf-8") == "ada@example.com\n"


def test_compose_wrap_keeps_the_private_service(tmp_path: Path):
    _contract, dest = _render("compose", tmp_path)
    document = yaml.safe_load((dest / "docker-compose.yml").read_text(encoding="utf-8"))
    web = document["services"]["web"]
    assert "ports" not in web
    assert "8000" in {str(item) for item in web["expose"]}
    assert web["build"]["context"] == ".."
    assert document["services"]["redis"]["image"] == "redis:7-alpine"
    assert document["services"]["edge"]["networks"]["cairn"]["ipv4_address"] == COMPOSE_EDGE_IP
    assert document["services"]["auth"]["networks"]["cairn"]["ipv4_address"] == COMPOSE_AUTH_IP
    assert document["services"]["web"]["networks"]["cairn"]["ipv4_address"] == COMPOSE_APP_IP
    assert document["services"]["redis"]["networks"]["cairn"]["ipv4_address"] == "172.30.10.5"
    assert "forward_auth auth:4180" in (dest / "Caddyfile").read_text(encoding="utf-8")
    assert "reverse_proxy web:8000" in (dest / "Caddyfile").read_text(encoding="utf-8")


def test_other_paradigms_render(tmp_path: Path):
    expectations = {
        "static": "/srv/server.py",
        "node": 'CMD ["node", "server.js"]',
        "next": "npm run build",
        "flask": "gunicorn",
        "django": "DJANGO_SECRET_KEY: ${DJANGO_SECRET_KEY}",
        "go": "go build",
        "dockerfile": "dockerfile: Dockerfile",
    }
    for name, snippet in expectations.items():
        _contract, dest = _render(name, tmp_path)
        blob = "\n".join(path.read_text(encoding="utf-8") for path in dest.rglob("*") if path.is_file())
        assert snippet in blob
        assert "-----BEGIN " not in blob


def test_env_example_has_empty_values(tmp_path: Path):
    _contract, dest = _render("django", tmp_path)
    text = (dest / "env.example").read_text(encoding="utf-8")
    assert "OIDC_CLIENT_SECRET=\n" in text
    assert "OAUTH2_PROXY_COOKIE_SECRET=\n" in text
    assert "DJANGO_SECRET_KEY=\n" in text
    assert "os.urandom" in text
    dumped = dump_contract(detect_app(FIXTURES / "django")[0])
    assert "SECRET" not in dumped or "SecretEnv" in dumped or "cookieSecretEnv" in dumped
