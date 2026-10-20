"""cairn.yaml contract: load, validate, and emit. No secret values."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import yaml

from cairn.headers import DEFAULT_PORTS, PARADIGMS

ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DOMAIN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?)+$")
GROUP = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._~/-]*$")

# Normalized by lowercasing and removing underscores and hyphens.
FORBIDDEN_KEYS = frozenset(
    {
        "clientsecret",
        "cookiesecret",
        "password",
        "passwd",
        "apikey",
        "apitoken",
        "token",
        "secret",
        "privatekey",
        "accesskey",
        "accesstoken",
        "refreshtoken",
        "authorization",
    }
)

CREDENTIAL_PREFIXES = (
    "AKIA",
    "ASIA",
    "ghp_",
    "gho_",
    "github_pat_",
    "sk_live_",
    "sk_test_",
    "xox",
    "eyJ",
)


class ContractError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass
class Review:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class EdgeConfig:
    tls: str = "off"
    bind_port: int = 8080


@dataclass(frozen=True)
class AuthConfig:
    mode: str = "edge-oidc"
    issuer: str | None = "https://idp.example.com"
    client_id_env: str = "OIDC_CLIENT_ID"
    client_secret_env: str = "OIDC_CLIENT_SECRET"
    cookie_secret_env: str = "OAUTH2_PROXY_COOKIE_SECRET"
    email_domains: tuple[str, ...] = ("example.com",)
    emails: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    public_paths: tuple[str, ...] = ("/healthz",)
    api_paths: tuple[str, ...] = ()
    allow_all_emails: bool = False


@dataclass(frozen=True)
class RuntimeConfig:
    module: str | None = None
    public_service: str | None = None


@dataclass(frozen=True)
class AppContract:
    name: str
    paradigm: str
    port: int
    health_path: str
    host: str
    edge: EdgeConfig
    auth: AuthConfig
    runtime: RuntimeConfig


def _norm_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _walk(node: object, path: str = "") -> list[tuple[str, object, object]]:
    found: list[tuple[str, object, object]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            found.append((here, key, value))
            found.extend(_walk(value, here))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_walk(value, f"{path}[{index}]"))
    return found


def forbidden_material(document: object) -> list[str]:
    """Reject secret-shaped keys and values without echoing the value."""
    errors: list[str] = []
    for path, key, value in _walk(document):
        if _norm_key(key) in FORBIDDEN_KEYS:
            errors.append(
                f"{path} is not allowed in cairn.yaml. "
                "Reference environment variable names (clientSecretEnv, cookieSecretEnv) instead."
            )
        if isinstance(value, str):
            if "-----BEGIN " in value:
                errors.append(f"{path} contains a PEM block. Cairn does not store certificates or private keys.")
            stripped = value.strip()
            if any(stripped.startswith(prefix) for prefix in CREDENTIAL_PREFIXES):
                errors.append(f"{path} contains a credential-shaped value. Remove it and use an environment variable.")
            if re.search(r"://[^/\s:]+:[^/\s@]+@", value):
                errors.append(f"{path} contains a URL with embedded credentials. Remove the userinfo.")
    return errors


def _as_tuple(value: object, path: str, errors: list[str]) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        errors.append(f"{path} must be a list of strings.")
        return ()
    return tuple(value)


def _mapping(value: object, path: str, errors: list[str]) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path} must be a mapping.")
        return {}
    return value


def contract_from_mapping(document: object) -> tuple[AppContract | None, list[str]]:
    errors = forbidden_material(document)
    if not isinstance(document, dict):
        return None, ["cairn.yaml must be a mapping."]
    if document.get("apiVersion") != "cairn.dev/v1":
        errors.append("apiVersion must be cairn.dev/v1.")
    if document.get("kind") != "App":
        errors.append("kind must be App.")

    name = document.get("name")
    if not isinstance(name, str) or not DNS_LABEL.fullmatch(name):
        errors.append("name must be a DNS label: lowercase letters, digits, and hyphens.")
        name = "invalid"

    paradigm = document.get("paradigm")
    if paradigm not in PARADIGMS:
        errors.append(f"paradigm must be one of: {', '.join(PARADIGMS)}.")
        paradigm = "static"

    port = document.get("port", DEFAULT_PORTS.get(str(paradigm), 8080))
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        errors.append("port must be an integer from 1 to 65535.")
        port = 8080

    health = document.get("healthPath", "/healthz")
    if not isinstance(health, str) or not health.startswith("/") or ".." in health:
        errors.append("healthPath must be an absolute path.")
        health = "/healthz"

    host = document.get("host", "localhost")
    if not isinstance(host, str) or not host or "/" in host or " " in host:
        errors.append("host must be a hostname without a scheme or path.")
        host = "localhost"

    edge_raw = _mapping(document.get("edge"), "edge", errors)
    tls = edge_raw.get("tls", "off")
    if tls not in {"off", "internal"}:
        errors.append("edge.tls must be off or internal.")
        tls = "off"
    bind_port = edge_raw.get("bindPort", 8080)
    if not isinstance(bind_port, int) or isinstance(bind_port, bool) or not 1 <= bind_port <= 65535:
        errors.append("edge.bindPort must be an integer from 1 to 65535.")
        bind_port = 8080

    auth_raw = _mapping(document.get("auth"), "auth", errors)
    mode = auth_raw.get("mode", "edge-oidc")
    if mode not in {"edge-oidc", "app-oidc", "none"}:
        errors.append("auth.mode must be edge-oidc, app-oidc, or none.")
        mode = "edge-oidc"
    issuer = auth_raw.get("issuer")
    if issuer is not None and not isinstance(issuer, str):
        errors.append("auth.issuer must be a string.")
        issuer = None
    client_id_env = str(auth_raw.get("clientIdEnv", "OIDC_CLIENT_ID"))
    client_secret_env = str(auth_raw.get("clientSecretEnv", "OIDC_CLIENT_SECRET"))
    cookie_secret_env = str(auth_raw.get("cookieSecretEnv", "OAUTH2_PROXY_COOKIE_SECRET"))
    domains = _as_tuple(auth_raw.get("emailDomains", []), "auth.emailDomains", errors)
    emails = _as_tuple(auth_raw.get("emails", []), "auth.emails", errors)
    groups = _as_tuple(auth_raw.get("groups", []), "auth.groups", errors)
    public_paths = _as_tuple(auth_raw.get("publicPaths", ["/healthz"]), "auth.publicPaths", errors)
    api_paths = _as_tuple(auth_raw.get("apiPaths", []), "auth.apiPaths", errors)
    allow_all = auth_raw.get("allowAllEmails", False)
    if not isinstance(allow_all, bool):
        errors.append("auth.allowAllEmails must be true or false.")
        allow_all = False

    runtime_raw = _mapping(document.get("runtime"), "runtime", errors)
    module = runtime_raw.get("module")
    if module is not None and not isinstance(module, str):
        errors.append("runtime.module must be a string.")
        module = None
    public_service = runtime_raw.get("publicService")
    if public_service is not None and not isinstance(public_service, str):
        errors.append("runtime.publicService must be a string.")
        public_service = None

    contract = AppContract(
        name=name if isinstance(name, str) else "invalid",
        paradigm=paradigm if isinstance(paradigm, str) else "static",
        port=port if isinstance(port, int) else 8080,
        health_path=health if isinstance(health, str) else "/healthz",
        host=host if isinstance(host, str) else "localhost",
        edge=EdgeConfig(tls=tls if isinstance(tls, str) else "off", bind_port=bind_port if isinstance(bind_port, int) else 8080),
        auth=AuthConfig(
            mode=mode if isinstance(mode, str) else "edge-oidc",
            issuer=issuer if isinstance(issuer, str) or issuer is None else None,
            client_id_env=client_id_env,
            client_secret_env=client_secret_env,
            cookie_secret_env=cookie_secret_env,
            email_domains=domains,
            emails=emails,
            groups=groups,
            public_paths=public_paths,
            api_paths=api_paths,
            allow_all_emails=allow_all,
        ),
        runtime=RuntimeConfig(
            module=module if isinstance(module, str) else None,
            public_service=public_service if isinstance(public_service, str) else None,
        ),
    )
    return contract, errors


def review_contract(contract: AppContract, app_dir: Path | None = None) -> Review:
    review = Review()
    auth = contract.auth
    if contract.edge.tls == "off" and contract.host not in {"localhost", "127.0.0.1"}:
        review.errors.append("edge.tls off is only valid for host localhost or 127.0.0.1. Use edge.tls: internal for any other host.")
    if auth.mode in {"edge-oidc", "app-oidc"}:
        _review_issuer(auth.issuer, review)
        for label, env_name in (
            ("auth.clientIdEnv", auth.client_id_env),
            ("auth.clientSecretEnv", auth.client_secret_env),
        ):
            if not ENV_NAME.fullmatch(env_name):
                review.errors.append(f"{label} must be an environment variable name like OIDC_CLIENT_ID.")
        if auth.mode == "edge-oidc" and not ENV_NAME.fullmatch(auth.cookie_secret_env):
            review.errors.append("auth.cookieSecretEnv must be an environment variable name like OAUTH2_PROXY_COOKIE_SECRET.")
    if auth.mode == "edge-oidc":
        _review_access(auth, review)
    for collection, label in (
        (auth.public_paths, "auth.publicPaths"),
        (auth.api_paths, "auth.apiPaths"),
    ):
        for item in collection:
            if not PATH_RE.fullmatch(item) or ".." in item:
                review.errors.append(f"{label} entry {item!r} must be an absolute path without '..'.")
    overlap = set(auth.public_paths) & set(auth.api_paths)
    if overlap:
        review.errors.append(f"a path cannot be both public and an API route: {', '.join(sorted(overlap))}.")
    if contract.health_path not in auth.public_paths and auth.mode == "edge-oidc":
        review.warnings.append(
            f"{contract.health_path} is not in auth.publicPaths, so probes must present a session cookie."
        )
    if contract.paradigm == "compose" and not contract.runtime.public_service:
        review.errors.append("runtime.publicService is required when paradigm is compose.")
    if app_dir is not None and (app_dir / ".env").is_file():
        review.warnings.append(
            "A .env file is present. Cairn will not print it, but a Docker build context can still send it. "
            "Add .env to .dockerignore before cairn up."
        )
    if app_dir is not None and contract.paradigm == "fastapi":
        text = _dep_text(app_dir)
        if "uvicorn" not in text:
            review.warnings.append("FastAPI render runs uvicorn. Add uvicorn to requirements.txt or pyproject.toml.")
    if app_dir is not None and contract.paradigm in {"flask", "django"}:
        text = _dep_text(app_dir)
        if "gunicorn" not in text:
            review.warnings.append(f"{contract.paradigm} render runs gunicorn. Add gunicorn to the dependency manifest.")
    return review


def _review_issuer(issuer: str | None, review: Review) -> None:
    if not issuer:
        review.errors.append("auth.issuer is required for edge-oidc and app-oidc.")
        return
    parsed = urlparse(issuer)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        review.errors.append("auth.issuer must be an https URL with a host and no userinfo, query, or fragment.")
        return
    if parsed.hostname == "idp.example.com":
        review.warnings.append("auth.issuer still points at idp.example.com. Replace it with your OIDC issuer before login will work.")


def _review_access(auth: AuthConfig, review: Review) -> None:
    if any(domain == "*" or "*" in domain for domain in auth.email_domains):
        review.errors.append("auth.emailDomains cannot contain '*'. Set auth.allowAllEmails: true to allow every verified email.")
    for domain in auth.email_domains:
        if not DOMAIN.fullmatch(domain):
            review.errors.append(f"auth.emailDomains entry {domain!r} is not a lowercase domain.")
    for email in auth.emails:
        if not EMAIL.fullmatch(email) or email != email.lower():
            review.errors.append(f"auth.emails entry {email!r} must be a lowercase email address.")
            continue
        domain = email.split("@", 1)[1]
        if auth.email_domains and domain not in auth.email_domains and not auth.allow_all_emails:
            review.errors.append(f"{email} is not inside auth.emailDomains.")
    for group in auth.groups:
        if not GROUP.fullmatch(group):
            review.errors.append(f"auth.groups entry {group!r} has characters that cannot be rendered safely.")
    closed = bool(auth.email_domains or auth.emails or auth.groups or auth.allow_all_emails)
    if not closed:
        review.errors.append(
            "edge-oidc needs an access rule: emailDomains, emails, groups, or allowAllEmails: true."
        )
    if auth.allow_all_emails and auth.email_domains:
        review.errors.append("auth.allowAllEmails and auth.emailDomains cannot both be set.")
    if auth.email_domains == ("example.com",) and not auth.emails and not auth.groups and not auth.allow_all_emails:
        review.warnings.append(
            "The only email domain is example.com, which is reserved for documentation. Replace it before a real deployment."
        )


def effective_email_domains(auth: AuthConfig) -> tuple[str, ...]:
    if auth.allow_all_emails:
        return ("*",)
    if auth.email_domains:
        return auth.email_domains
    if auth.groups and not auth.emails:
        return ("*",)
    derived = tuple(dict.fromkeys(email.split("@", 1)[1] for email in auth.emails))
    return derived


def _dep_text(app_dir: Path) -> str:
    parts: list[str] = []
    for name in ("requirements.txt", "pyproject.toml", "Pipfile"):
        path = app_dir / name
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8", errors="replace")[:200_000].lower())
    return "\n".join(parts)


def load_contract(path: Path) -> AppContract:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    contract, errors = contract_from_mapping(document)
    review = review_contract(contract, path.parent) if contract else Review()
    all_errors = errors + (review.errors if contract else [])
    if all_errors or contract is None:
        raise ContractError(all_errors or ["cairn.yaml could not be read."])
    return contract


def load_contract_review(path: Path) -> tuple[AppContract, Review]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    contract, errors = contract_from_mapping(document)
    if contract is None:
        raise ContractError(errors or ["cairn.yaml could not be read."])
    review = review_contract(contract, path.parent)
    review.errors = errors + review.errors
    return contract, review


def _yaml_str(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]*", value) and value.lower() not in {
        "true",
        "false",
        "null",
        "yes",
        "no",
        "on",
        "off",
    }:
        return value
    return json.dumps(value)


def _block_list(lines: list[str], key: str, values: tuple[str, ...] | list[str], indent: int) -> None:
    pad = " " * indent
    if not values:
        lines.append(f"{pad}{key}: []")
        return
    lines.append(f"{pad}{key}:")
    for value in values:
        lines.append(f"{pad}  - {_yaml_str(value)}")


def dump_contract(contract: AppContract) -> str:
    auth = contract.auth
    runtime = contract.runtime
    lines = [
        "apiVersion: cairn.dev/v1",
        "kind: App",
        f"name: {contract.name}",
        f"paradigm: {contract.paradigm}",
        f"port: {contract.port}",
        f"healthPath: {contract.health_path}",
        f"host: {contract.host}",
        "edge:",
        f"  tls: {_yaml_str(contract.edge.tls)}",
        f"  bindPort: {contract.edge.bind_port}",
        "auth:",
        f"  mode: {auth.mode}",
    ]
    if auth.issuer:
        lines.append(f"  issuer: {_yaml_str(auth.issuer)}")
    lines.extend(
        [
            f"  clientIdEnv: {auth.client_id_env}",
            f"  clientSecretEnv: {auth.client_secret_env}",
            f"  cookieSecretEnv: {auth.cookie_secret_env}",
        ]
    )
    _block_list(lines, "emailDomains", auth.email_domains, 2)
    _block_list(lines, "emails", auth.emails, 2)
    _block_list(lines, "groups", auth.groups, 2)
    _block_list(lines, "publicPaths", auth.public_paths, 2)
    _block_list(lines, "apiPaths", auth.api_paths, 2)
    lines.append(f"  allowAllEmails: {'true' if auth.allow_all_emails else 'false'}")
    lines.append("runtime:")
    lines.append(f"  module: {runtime.module if runtime.module else 'null'}")
    lines.append(f"  publicService: {runtime.public_service if runtime.public_service else 'null'}")
    return "\n".join(lines) + "\n"


def public_origin(contract: AppContract) -> str:
    scheme = "https" if contract.edge.tls == "internal" else "http"
    port = contract.edge.bind_port
    if (scheme == "https" and port == 443) or (scheme == "http" and port == 80):
        return f"{scheme}://{contract.host}"
    return f"{scheme}://{contract.host}:{port}"


def publish_address(contract: AppContract) -> str:
    if contract.host in {"localhost", "127.0.0.1"}:
        return "127.0.0.1"
    return "0.0.0.0"
