import json
from pathlib import Path

import pytest
import yaml

from cairn.detect import DetectError, detect_app
from cairn.model import dump_contract
from tests.conftest import FIXTURES


def test_each_fixture_paradigm():
    expected = {
        "static": "static",
        "node": "node",
        "next": "next",
        "fastapi": "fastapi",
        "flask": "flask",
        "django": "django",
        "go": "go",
        "dockerfile": "dockerfile",
        "compose": "compose",
    }
    for name, paradigm in expected.items():
        contract, reasons = detect_app(FIXTURES / name)
        assert contract.paradigm == paradigm, reasons
        assert contract.health_path == "/healthz"
        assert contract.auth.mode == "edge-oidc"
        assert contract.auth.public_paths == ("/healthz",)
        assert reasons


def test_fixture_ports_and_modules():
    fastapi, _ = detect_app(FIXTURES / "fastapi")
    assert fastapi.port == 8000
    assert fastapi.runtime.module == "app:app"
    django, _ = detect_app(FIXTURES / "django")
    assert django.runtime.module == "app.wsgi:application"
    compose, _ = detect_app(FIXTURES / "compose")
    assert compose.runtime.public_service == "web"
    assert compose.port == 8000
    dockerfile, _ = detect_app(FIXTURES / "dockerfile")
    assert dockerfile.port == 8000
    node, _ = detect_app(FIXTURES / "node")
    assert node.port == 3000


def test_detect_roundtrip_yaml():
    contract, _ = detect_app(FIXTURES / "fastapi")
    loaded = yaml.safe_load(dump_contract(contract))
    from cairn.model import contract_from_mapping

    again, errors = contract_from_mapping(loaded)
    assert errors == []
    assert again == contract


def test_next_beats_dockerfile_and_start_script(tmp_path: Path):
    (tmp_path / "Dockerfile").write_text("FROM scratch\nEXPOSE 3000\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"start": "next start"}, "dependencies": {"next": "15.1.0"}}),
        encoding="utf-8",
    )
    contract, _ = detect_app(tmp_path)
    assert contract.paradigm == "next"


def test_express_and_hono_are_node(tmp_path: Path):
    for package_name in ("express", "hono"):
        app = tmp_path / package_name
        app.mkdir()
        (app / "package.json").write_text(
            json.dumps({"scripts": {"start": "node server.js"}, "dependencies": {package_name: "1.0.0"}}),
            encoding="utf-8",
        )
        (app / "server.js").write_text("/* listener */\n", encoding="utf-8")
        contract, _ = detect_app(app)
        assert contract.paradigm == "node"


def test_vite_without_server_is_static(tmp_path: Path):
    (tmp_path / "vite.config.ts").write_text("export default {}\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"start": "vite preview"}}),
        encoding="utf-8",
    )
    contract, _ = detect_app(tmp_path)
    assert contract.paradigm == "static"


def test_vite_with_server_js_is_node(tmp_path: Path):
    (tmp_path / "vite.config.ts").write_text("export default {}\n", encoding="utf-8")
    (tmp_path / "server.js").write_text("/* listener */\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"start": "node server.js"}}),
        encoding="utf-8",
    )
    contract, _ = detect_app(tmp_path)
    assert contract.paradigm == "node"


def test_starlette_uses_fastapi_path(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("starlette==0.41.3\nuvicorn==0.34.0\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("app = None\n", encoding="utf-8")
    contract, reasons = detect_app(tmp_path)
    assert contract.paradigm == "fastapi"
    assert any("Starlette" in reason for reason in reasons)


def test_ambiguous_python_frameworks(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("fastapi==0.115.6\nflask==3.1.0\n", encoding="utf-8")
    with pytest.raises(DetectError) as caught:
        detect_app(tmp_path)
    assert "single runtime" in caught.value.errors[0]


def test_compose_wins_over_fastapi_source(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("fastapi==0.115.6\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  api:\n    build: .\n    ports: ['8000:8000']\n",
        encoding="utf-8",
    )
    contract, reasons = detect_app(tmp_path)
    assert contract.paradigm == "compose"
    assert contract.runtime.public_service == "api"
    assert any("wraps" in reason for reason in reasons)


def test_two_published_services_are_ambiguous(tmp_path: Path):
    (tmp_path / "compose.yaml").write_text(
        "services:\n  a:\n    image: example/a\n    ports: ['1:1']\n  b:\n    image: example/b\n    ports: ['2:2']\n",
        encoding="utf-8",
    )
    with pytest.raises(DetectError):
        detect_app(tmp_path)


def test_multiple_compose_files(tmp_path: Path):
    (tmp_path / "compose.yaml").write_text("services: {web: {image: example/web}}\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services: {web: {image: example/web}}\n", encoding="utf-8")
    with pytest.raises(DetectError):
        detect_app(tmp_path)


def test_empty_directory(tmp_path: Path):
    with pytest.raises(DetectError):
        detect_app(tmp_path)


def test_invalid_package_json(tmp_path: Path):
    (tmp_path / "package.json").write_text("{", encoding="utf-8")
    with pytest.raises(DetectError) as caught:
        detect_app(tmp_path)
    assert "package.json" in caught.value.errors[0]
