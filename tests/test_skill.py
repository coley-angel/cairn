import re
from pathlib import Path

import yaml

from tests.conftest import ROOT

SKILL = ROOT / "skills" / "cairn" / "SKILL.md"


def test_skill_matches_the_agent_skills_spec():
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    end = text.find("\n---\n", 4)
    assert end > 0
    front = yaml.safe_load(text[4:end])
    assert front["name"] == "cairn"
    assert SKILL.parent.name == front["name"]
    assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", front["name"])
    description = front["description"]
    assert isinstance(description, str)
    assert 1 <= len(description) <= 1024
    lowered = description.lower()
    for word in ("sso", "oidc", "deploy", "fastapi", "next.js", "django", "compose"):
        assert word in lowered
    assert len(text.splitlines()) < 500
    assert (SKILL.parent / "references" / "workflow.md").is_file()
