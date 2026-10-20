import os
import subprocess
import sys
from pathlib import Path

from tests.conftest import FIXTURES, ROOT


def _run(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged["PYTHONPATH"] = str(ROOT / "src")
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, "-m", "cairn", *args],
        cwd=cwd,
        env=merged,
        text=True,
        capture_output=True,
        check=False,
    )


def test_detect_check_render_and_refuse_overwrite(tmp_path: Path):
    app = tmp_path / "app"
    app.mkdir()
    for path in (FIXTURES / "fastapi").iterdir():
        if path.name == ".dockerignore":
            continue
        target = app / path.name
        if path.is_file():
            target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    first = _run(["detect", "."], app)
    assert first.returncode == 0, first.stderr
    assert (app / "cairn.yaml").is_file()
    again = _run(["detect", "."], app)
    assert again.returncode == 2
    assert "--force" in again.stderr
    checked = _run(["check", "."], app)
    assert checked.returncode == 0, checked.stderr
    assert "example.com" in checked.stderr
    rendered = _run(["render", "."], app)
    assert rendered.returncode == 0, rendered.stderr
    assert (app / ".cairn" / "Caddyfile").is_file()
    assert (app / ".cairn" / "k8s" / "deployment-edge.yaml").is_file()


def test_up_refuses_missing_secrets_without_printing_them(tmp_path: Path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "requirements.txt").write_text("fastapi==0.115.6\nuvicorn==0.34.0\n", encoding="utf-8")
    (app / "app.py").write_text("app = None\n", encoding="utf-8")
    assert _run(["detect", "."], app).returncode == 0
    (app / ".env").write_text("OIDC_CLIENT_SECRET=SENTINELSECRETVALUE\n", encoding="utf-8")
    result = _run(["up", "."], app, env={"OIDC_CLIENT_ID": "client-id"})
    assert result.returncode == 2
    assert "OAUTH2_PROXY_COOKIE_SECRET" in result.stderr
    assert "SENTINELSECRETVALUE" not in result.stderr
    assert "SENTINELSECRETVALUE" not in result.stdout


def test_version():
    result = _run(["--version"], ROOT)
    assert result.returncode == 0
    assert "cairn 0.1.0" in result.stdout
