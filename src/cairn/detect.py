"""Recognize how an application is built and draft a cairn.yaml contract."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from cairn.headers import DEFAULT_PORTS
from cairn.model import AppContract, AuthConfig, EdgeConfig, RuntimeConfig, dump_contract

IGNORE_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".cairn", "dist", "build"}
COMPOSE_NAMES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")
PY_DEP_FILES = ("requirements.txt", "pyproject.toml", "Pipfile")


class DetectError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


def detect_app(app_dir: Path) -> tuple[AppContract, list[str]]:
    root = app_dir.resolve()
    if not root.is_dir():
        raise DetectError([f"{app_dir} is not a directory."])
    reasons: list[str] = []
    markers = _python_markers(root)
    frameworks = set(markers)
    if (root / "manage.py").is_file():
        frameworks.add("django")
    web = frameworks & {"fastapi", "starlette", "flask", "django"}
    groups: list[str] = []
    if "fastapi" in web or "starlette" in web:
        groups.append("ASGI (FastAPI or Starlette)")
    if "flask" in web:
        groups.append("Flask")
    if "django" in web:
        groups.append("Django")
    if len(groups) > 1:
        raise DetectError(
            [
                "Found more than one Python web framework "
                f"({', '.join(groups)}). Keep a single runtime in the dependency manifest."
            ]
        )

    compose_paths = [root / name for name in COMPOSE_NAMES if (root / name).is_file()]
    if len(compose_paths) > 1:
        raise DetectError(
            ["Found multiple Compose files. Keep one of compose.yaml or docker-compose.yml at the app root."]
        )

    package = _package_json(root)
    paradigm: str | None = None
    port = 8080
    module: str | None = None
    public_service: str | None = None

    if compose_paths:
        paradigm = "compose"
        public_service, port = _compose_public_service(compose_paths[0])
        reasons.append(
            f"{compose_paths[0].name} declares services. Cairn wraps {public_service} with the edge instead of replacing the file."
        )
    elif (root / "manage.py").is_file() and "fastapi" not in frameworks and "flask" not in frameworks:
        paradigm = "django"
        module = _django_module(root)
        port = DEFAULT_PORTS["django"]
        reasons.append("manage.py marks this as Django. The WSGI module is " + (module or "unset") + ".")
    elif package is not None and _has_dep(package, "next"):
        paradigm = "next"
        port = DEFAULT_PORTS["next"]
        reasons.append("package.json depends on next. The service is a standalone Node process.")
    elif "fastapi" in frameworks or "starlette" in frameworks:
        paradigm = "fastapi"
        module = _python_module(root, ("app.py", "main.py"), "app")
        port = DEFAULT_PORTS["fastapi"]
        kind = "FastAPI" if "fastapi" in frameworks else "Starlette"
        reasons.append(f"{kind} is the ASGI app. uvicorn will load {module}.")
    elif "flask" in frameworks:
        paradigm = "flask"
        module = _python_module(root, ("app.py", "main.py"), "app")
        port = DEFAULT_PORTS["flask"]
        reasons.append(f"Flask is the WSGI app. gunicorn will load {module}.")
    elif _go_listens(root):
        paradigm = "go"
        port = DEFAULT_PORTS["go"]
        reasons.append("go.mod and a net/http listener mark this as a Go service.")
    elif package is not None and _is_node_service(root, package):
        paradigm = "node"
        port = DEFAULT_PORTS["node"]
        reasons.append("package.json start script marks this as a Node service. Express and Hono use this same path.")
    elif _is_static(root, package):
        paradigm = "static"
        port = DEFAULT_PORTS["static"]
        reasons.append("HTML or Vite assets mark this as a static site. Cairn serves them and /healthz.")
    elif (root / "Dockerfile").is_file():
        paradigm = "dockerfile"
        port = _dockerfile_port(root / "Dockerfile") or DEFAULT_PORTS["dockerfile"]
        reasons.append("A root Dockerfile is the build. Cairn will not rewrite it; the edge proxies its port.")
    else:
        raise DetectError(
            [
                "Could not tell how this app runs. Add a Compose file, package.json, Python web dependency, "
                "go.mod, index.html, or Dockerfile."
            ]
        )

    host = "localhost"
    contract = AppContract(
        name=_label_from_dir(root),
        paradigm=paradigm,
        port=port,
        health_path="/healthz",
        host=host,
        edge=EdgeConfig(tls="off", bind_port=8080),
        auth=AuthConfig(),
        runtime=RuntimeConfig(module=module, public_service=public_service),
    )
    return contract, reasons


def write_contract(app_dir: Path, contract: AppContract, force: bool = False) -> Path:
    target = app_dir / "cairn.yaml"
    if target.exists() and not force:
        raise DetectError([f"{target} already exists. Re-run with --force to replace it."])
    target.write_text(dump_contract(contract), encoding="utf-8")
    return target


def _label_from_dir(root: Path) -> str:
    raw = root.name.lower().replace("_", "-").replace(" ", "-")
    raw = re.sub(r"[^a-z0-9-]", "", raw).strip("-")
    raw = re.sub(r"-{2,}", "-", raw)
    if not raw:
        return "app"
    if len(raw) > 63:
        raw = raw[:63].rstrip("-")
    if raw[0].isdigit() or not raw[0].isalnum():
        raw = f"app-{raw}"[:63].rstrip("-")
    return raw


def _read(path: Path, limit: int = 200_000) -> str:
    return path.read_text(encoding="utf-8", errors="replace")[:limit]


def _python_markers(root: Path) -> set[str]:
    blob_parts: list[str] = []
    for name in PY_DEP_FILES:
        path = root / name
        if path.is_file():
            blob_parts.append(_read(path).lower())
    blob = "\n".join(blob_parts)
    found: set[str] = set()
    for name in ("fastapi", "starlette", "flask", "django"):
        if re.search(rf"(^|[^a-z0-9_]){name}([^a-z0-9_]|$)", blob):
            found.add(name)
    return found


def _package_json(root: Path) -> dict[str, object] | None:
    path = root / "package.json"
    if not path.is_file():
        return None
    try:
        document = json.loads(_read(path))
    except json.JSONDecodeError as exc:
        raise DetectError([f"package.json is not valid JSON: {exc.msg}."]) from exc
    if not isinstance(document, dict):
        raise DetectError(["package.json must be a JSON object."])
    return document


def _has_dep(package: dict[str, object], name: str) -> bool:
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        deps = package.get(key)
        if isinstance(deps, dict) and name in deps:
            return True
    return False


def _is_node_service(root: Path, package: dict[str, object]) -> bool:
    scripts = package.get("scripts")
    start = scripts.get("start") if isinstance(scripts, dict) else None
    if not isinstance(start, str) or not start.strip():
        return False
    if _has_dep(package, "next"):
        return False
    if _is_vite(root) and not (root / "server.js").is_file() and not (
        _has_dep(package, "express") or _has_dep(package, "hono")
    ):
        return False
    return True


def _is_vite(root: Path) -> bool:
    return any((root / name).is_file() for name in ("vite.config.ts", "vite.config.js", "vite.config.mjs"))


def _is_static(root: Path, package: dict[str, object] | None) -> bool:
    if package is not None and _has_dep(package, "next"):
        return False
    if _is_vite(root):
        return True
    return (root / "index.html").is_file() or (root / "public" / "index.html").is_file()


def _django_module(root: Path) -> str:
    matches = sorted(path for path in root.glob("*/wsgi.py") if IGNORE_DIRS.isdisjoint(path.parts))
    if not matches:
        return "app.wsgi:application"
    relative = matches[0].relative_to(root).with_suffix("")
    return ".".join(relative.parts) + ":application"


def _python_module(root: Path, candidates: tuple[str, ...], attribute: str) -> str:
    for name in candidates:
        if (root / name).is_file():
            return f"{name[:-3]}:{attribute}"
    return f"app:{attribute}"


def _go_listens(root: Path) -> bool:
    if not (root / "go.mod").is_file():
        return False
    for path in root.rglob("*.go"):
        if not IGNORE_DIRS.isdisjoint(path.parts):
            continue
        if len(path.relative_to(root).parts) > 4:
            continue
        try:
            text = _read(path, 100_000)
        except OSError:
            continue
        if "ListenAndServe" in text or "http.Server" in text:
            return True
    return False


def _dockerfile_port(path: Path) -> int | None:
    matches = re.findall(r"(?im)^EXPOSE\s+(\d+)", _read(path))
    if not matches:
        return None
    return int(matches[-1])


def _compose_public_service(path: Path) -> tuple[str, int]:
    document = yaml.safe_load(_read(path))
    if not isinstance(document, dict) or not isinstance(document.get("services"), dict) or not document["services"]:
        raise DetectError([f"{path.name} has no services to wrap."])
    services: dict[str, object] = document["services"]
    published: list[tuple[str, int]] = []
    for name, raw in services.items():
        if not isinstance(raw, dict):
            continue
        port = _published_port(raw.get("ports"))
        if port is not None:
            published.append((str(name), port))
    if len(published) == 1:
        return published[0]
    if len(published) > 1:
        names = ", ".join(name for name, _port in published)
        raise DetectError([f"More than one Compose service publishes a port ({names}). Expose a single public service."])
    if len(services) == 1:
        name = next(iter(services))
        return str(name), DEFAULT_PORTS["compose"]
    raise DetectError(["No Compose service publishes a port. Set ports on the service Cairn should wrap."])


def _published_port(ports: object) -> int | None:
    if not isinstance(ports, list) or not ports:
        return None
    entry = ports[0]
    if isinstance(entry, int) and not isinstance(entry, bool):
        return entry
    if isinstance(entry, dict):
        target = entry.get("target")
        if isinstance(target, int):
            return target
        return None
    if isinstance(entry, str):
        parts = entry.split(":")
        try:
            return int(parts[-1])
        except ValueError:
            return None
    return None
