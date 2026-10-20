"""Cairn command line."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from cairn import __version__
from cairn.detect import DetectError, detect_app, write_contract
from cairn.model import ContractError, load_contract_review
from cairn.render import render_tree

WEAK_SECRETS = {"changeme", "password", "secret", "example", "cairn"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cairn", description="Golden path for hosted apps.")
    parser.add_argument("--version", action="version", version=f"cairn {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    detect = sub.add_parser("detect", help="write cairn.yaml for an app directory")
    _add_path(detect)
    detect.add_argument("--force", action="store_true", help="replace an existing cairn.yaml")

    check = sub.add_parser("check", help="validate cairn.yaml")
    _add_path(check)

    render = sub.add_parser("render", help="render Compose, Caddy, and Kubernetes from cairn.yaml")
    _add_path(render)

    up = sub.add_parser("up", help="render if needed, then start the local Compose stack")
    _add_path(up)

    args = parser.parse_args(argv)
    try:
        if args.command == "detect":
            return _detect(args.path, args.force)
        if args.command == "check":
            return _check(args.path)
        if args.command == "render":
            return _render(args.path)
        if args.command == "up":
            return _up(args.path)
    except (DetectError, ContractError) as exc:
        for line in exc.errors:
            print(line, file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 1


def _add_path(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", nargs="?", default=".", type=Path, help="app directory (default: .)")


def _detect(path: Path, force: bool) -> int:
    contract, reasons = detect_app(path)
    target = write_contract(path, contract, force=force)
    for reason in reasons:
        print(reason)
    print(f"wrote {target}")
    return 0


def _check(path: Path) -> int:
    contract_path = path / "cairn.yaml"
    if not contract_path.is_file():
        print("cairn.yaml is missing. Run cairn detect first.", file=sys.stderr)
        return 2
    _contract, review = load_contract_review(contract_path)
    for warning in review.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if review.errors:
        for error in review.errors:
            print(error, file=sys.stderr)
        return 2
    print("cairn.yaml is valid")
    return 0


def _render(path: Path) -> int:
    code = _check(path)
    if code != 0:
        return code
    contract, _review = load_contract_review(path / "cairn.yaml")
    dest = render_tree(path, contract)
    print(f"rendered {dest}")
    return 0


def _up(path: Path) -> int:
    code = _render(path)
    if code != 0:
        return code
    if shutil.which("docker") is None:
        print("docker is not on PATH. Render succeeded; start Docker and run cairn up again.", file=sys.stderr)
        return 2
    contract, _review = load_contract_review(path / "cairn.yaml")
    _load_env_file(path / ".env")
    missing = _missing_runtime_env(contract)
    if missing:
        print("Refusing to start. Export these variables, or put them in an untracked .env file:", file=sys.stderr)
        for name in missing:
            print(f"  {name}", file=sys.stderr)
        print("Generate the cookie secret with the oauth2-proxy CSPRNG one-liner in .cairn/env.example.", file=sys.stderr)
        return 2
    weak = _weak_cookie(contract)
    if weak:
        print(weak, file=sys.stderr)
        return 2
    compose = path / ".cairn" / "docker-compose.yml"
    completed = subprocess.run(
        ["docker", "compose", "-f", str(compose), "up", "--build", "--remove-orphans"],
        cwd=path / ".cairn",
        check=False,
    )
    return completed.returncode


def _missing_runtime_env(contract: object) -> list[str]:
    from cairn.model import AppContract

    assert isinstance(contract, AppContract)
    required: list[str] = []
    if contract.auth.mode in {"edge-oidc", "app-oidc"}:
        required.extend([contract.auth.client_id_env, contract.auth.client_secret_env])
    if contract.auth.mode == "edge-oidc":
        required.append(contract.auth.cookie_secret_env)
    if contract.paradigm == "django":
        required.append("DJANGO_SECRET_KEY")
    missing = []
    for name in required:
        if not os.environ.get(name, "").strip():
            missing.append(name)
    return missing


def _weak_cookie(contract: object) -> str | None:
    from cairn.model import AppContract

    assert isinstance(contract, AppContract)
    if contract.auth.mode != "edge-oidc":
        return None
    value = os.environ.get(contract.auth.cookie_secret_env, "")
    if any(char.isspace() for char in value) or len(value) < 16 or value.lower() in WEAK_SECRETS:
        return (
            f"{contract.auth.cookie_secret_env} must be at least 16 non-space characters from a CSPRNG. "
            "The value was not printed."
        )
    return None


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


if __name__ == "__main__":
    raise SystemExit(main())
