import json
import shutil
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import pytest

from cairn.detect import detect_app
from cairn.render import render_tree
from tests.conftest import FIXTURES


pytestmark = pytest.mark.integration


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    completed = subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return completed.returncode == 0


@pytest.fixture
def docker_daemon():
    if not _docker_ready():
        pytest.skip("Docker daemon is not available")


def _curl(url: str, headers: list[str] | None = None) -> tuple[int, str, str]:
    command = ["curl", "-sS", "--http1.1", "--max-time", "10", "-D", "-", "-o", "-", url]
    for header in headers or []:
        command.extend(["-H", header])
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    raw = completed.stdout
    split_at = raw.find("\r\n\r\n")
    if split_at < 0:
        split_at = raw.find("\n\n")
        gap = 2
    else:
        gap = 4
    header_text = raw[:split_at] if split_at >= 0 else raw
    body = raw[split_at + gap :] if split_at >= 0 else ""
    status = 0
    for line in header_text.splitlines():
        if line.startswith("HTTP/"):
            status = int(line.split()[1])
    return status, header_text, body


def _wait(url: str, timeout: int = 180) -> tuple[int, str, str]:
    deadline = time.time() + timeout
    last = (0, "", "")
    while time.time() < deadline:
        try:
            last = _curl(url)
        except subprocess.TimeoutExpired:
            last = (0, "", "")
        if last[0] == 200:
            return last
        time.sleep(1)
    return last


def _up(dest: Path, project: str) -> None:
    completed = subprocess.run(
        ["docker", "compose", "-p", project, "-f", str(dest / "docker-compose.yml"), "up", "-d", "--build"],
        cwd=dest,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stdout[-4000:] + "\n" + completed.stderr[-4000:])


def _down(dest: Path, project: str) -> None:
    subprocess.run(
        ["docker", "compose", "-p", project, "-f", str(dest / "docker-compose.yml"), "down", "-v", "--remove-orphans"],
        cwd=dest,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _logs(dest: Path, project: str) -> str:
    completed = subprocess.run(
        ["docker", "compose", "-p", project, "-f", str(dest / "docker-compose.yml"), "logs", "--no-color"],
        cwd=dest,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.stdout + completed.stderr


def test_static_and_fastapi_edges(tmp_path: Path, docker_daemon: None):
    cases = (
        ("static", 18081, "cairnstatic"),
        ("fastapi", 18082, "cairnfapi"),
    )
    for name, port, project in cases:
        app = tmp_path / name
        shutil.copytree(FIXTURES / name, app)
        contract, _reasons = detect_app(app)
        contract = replace(
            contract,
            edge=replace(contract.edge, bind_port=port),
            auth=replace(contract.auth, public_paths=("/healthz", "/edge-probe"), api_paths=("/api/",)),
        )
        dest = render_tree(app, contract, auth_backend="stub")
        try:
            _up(dest, project)
            status, _headers, body = _wait(f"http://127.0.0.1:{port}/healthz")
            assert status == 200, _logs(dest, project)
            if name == "fastapi":
                assert json.loads(body) == {"ok": True}
            else:
                assert body == "ok\n"
            spoof = ["X-Auth-Request-User: attacker", "X-Auth-Request-Email: attacker@example.com"]
            status, _headers, body = _curl(f"http://127.0.0.1:{port}/edge-probe", spoof)
            assert status == 200, _logs(dest, project)
            assert "attacker" not in body
            status, headers, _body = _curl(f"http://127.0.0.1:{port}/whoami", spoof)
            assert status == 302, headers
            assert "/oauth2/sign_in" in headers
            status, _headers, _body = _curl(f"http://127.0.0.1:{port}/api/ping", spoof)
            assert status == 401
            status, _headers, body = _curl(
                f"http://127.0.0.1:{port}/whoami",
                spoof + ["Cookie: cairn_session=demo"],
            )
            assert status == 200, _logs(dest, project)
            assert "attacker" not in body
            if name == "fastapi":
                identity = json.loads(body)
                assert identity["user"] == "ada"
                assert identity["email"] == "ada@example.com"
            else:
                assert body == "ada"
        finally:
            _down(dest, project)
