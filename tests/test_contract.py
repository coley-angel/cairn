from pathlib import Path

import yaml

from cairn.model import contract_from_mapping, dump_contract, load_contract_review, review_contract
from cairn.detect import detect_app
from tests.conftest import FIXTURES


def _document(**updates: object) -> dict[str, object]:
    contract, _ = detect_app(FIXTURES / "fastapi")
    document = yaml.safe_load(dump_contract(contract))
    document.update(updates)
    return document


def test_rejects_secret_key_without_echoing_the_value():
    document = _document()
    document["auth"]["clientSecret"] = "SENTINELSECRETVALUE"
    _contract, errors = contract_from_mapping(document)
    assert errors
    assert "clientSecret" in errors[0]
    assert "SENTINELSECRETVALUE" not in "\n".join(errors)


def test_rejects_pem_and_credential_shaped_values():
    pem = _document()
    pem["auth"]["issuer"] = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"
    _contract, errors = contract_from_mapping(pem)
    assert any("PEM" in error for error in errors)
    assert "MIIB" not in "\n".join(errors)

    keyed = _document()
    keyed["host"] = "AKIAexamplevalue.example.com"
    _contract, errors = contract_from_mapping(keyed)
    assert any("credential-shaped" in error for error in errors)


def test_rejects_issuer_userinfo_and_plaintext_http():
    document = _document()
    document["auth"]["issuer"] = "https://user:secret@idp.example.com"
    _contract, errors = contract_from_mapping(document)
    assert any("userinfo" in error or "embedded credentials" in error for error in errors)
    assert "secret" not in "\n".join(errors).split("userinfo")[-1]

    document = _document()
    document["auth"]["issuer"] = "http://idp.example.com"
    contract, errors = contract_from_mapping(document)
    review = review_contract(contract)
    assert any("https" in error for error in errors + review.errors)


def test_tls_off_is_local_only():
    document = _document(host="app.example.com")
    contract, errors = contract_from_mapping(document)
    review = review_contract(contract)
    assert any("edge.tls" in error for error in errors + review.errors)


def test_open_edge_is_rejected(tmp_path: Path):
    document = _document()
    document["auth"]["emailDomains"] = []
    document["auth"]["allowAllEmails"] = False
    contract, errors = contract_from_mapping(document)
    review = review_contract(contract)
    assert any("access rule" in error for error in errors + review.errors)


def test_wildcard_domain_must_use_the_explicit_flag():
    document = _document()
    document["auth"]["emailDomains"] = ["*"]
    contract, errors = contract_from_mapping(document)
    review = review_contract(contract)
    assert any("allowAllEmails" in error for error in errors + review.errors)


def test_placeholder_values_warn_but_parse(tmp_path: Path):
    target = tmp_path / "cairn.yaml"
    contract, _ = detect_app(FIXTURES / "fastapi")
    target.write_text(dump_contract(contract), encoding="utf-8")
    loaded, review = load_contract_review(target)
    assert loaded == contract
    assert review.ok
    assert any("idp.example.com" in warning for warning in review.warnings)
    assert any("example.com" in warning for warning in review.warnings)


def test_env_file_warns_without_reading_it_aloud(tmp_path: Path, capsys):
    (tmp_path / ".env").write_text("OIDC_CLIENT_SECRET=SENTINELSECRETVALUE\n", encoding="utf-8")
    contract, _ = detect_app(FIXTURES / "fastapi")
    review = review_contract(contract, tmp_path)
    text = "\n".join(review.warnings)
    assert ".env" in text
    assert "SENTINELSECRETVALUE" not in text
    assert "SENTINELSECRETVALUE" not in capsys.readouterr().out
