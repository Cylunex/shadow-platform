import hashlib
import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from shadow_sdk.agent import AgentAuthenticator, AgentAuthError

TOKEN = "agent-test-token-that-is-longer-than-32-bytes"


def prepare(tmp_path: Path):
    registry = tmp_path / "registry.yml"
    registry.write_text(
        """
version: 1
agents:
  health-assistant:
    owner_app: health
    audiences: [health]
    scopes: [health.read, health.write]
    credential_hash_files:
      - agents/health-assistant/current-token.sha256
""",
        encoding="utf-8",
    )
    secrets = tmp_path / "secrets"
    digest = secrets / "agents" / "health-assistant" / "current-token.sha256"
    digest.parent.mkdir(parents=True)
    digest.write_text(hashlib.sha256(TOKEN.encode()).hexdigest(), encoding="utf-8")
    return registry, secrets


def test_authenticates_locally_and_enforces_scope(tmp_path):
    registry, secrets = prepare(tmp_path)
    authenticator = AgentAuthenticator(registry, secrets_dir=secrets, audience="health")

    identity = authenticator.authenticate(f"Bearer {TOKEN}")
    identity.require_scope("health.write")
    assert identity.agent_id == "health-assistant"

    with pytest.raises(AgentAuthError, match="lacks"):
        identity.require_scope("health.admin")


def test_rejects_invalid_token(tmp_path):
    registry, secrets = prepare(tmp_path)
    authenticator = AgentAuthenticator(registry, secrets_dir=secrets, audience="health")

    with pytest.raises(AgentAuthError, match="invalid"):
        authenticator.authenticate(f"Bearer {'x' * 40}")


def test_example_agent_registry_matches_schema():
    root = Path(__file__).parents[1]
    schema = json.loads(
        (root / "contracts" / "agent-registry.schema.json").read_text(encoding="utf-8")
    )
    registry = yaml.safe_load(
        (root / "agents" / "registry.yml.example").read_text(encoding="utf-8")
    )

    jsonschema.Draft202012Validator(schema).validate(registry)
