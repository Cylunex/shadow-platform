import hashlib

import pytest

from scripts.generate_agent_token import create_agent_token


def test_generator_returns_token_but_persists_only_digest(tmp_path):
    output = tmp_path / "agent.sha256"
    token = create_agent_token(output)

    assert len(token) >= 32
    assert token not in output.read_text(encoding="utf-8")
    assert output.read_text(encoding="utf-8").strip() == hashlib.sha256(token.encode()).hexdigest()


def test_generator_refuses_to_replace_existing_digest(tmp_path):
    output = tmp_path / "agent.sha256"
    output.write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError):
        create_agent_token(output)
    assert output.read_text(encoding="utf-8") == "keep"
