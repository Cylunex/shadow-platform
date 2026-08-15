import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from scripts.render_llm_env import render_env
from shadow_sdk.llm import LLMConfigError, resolve_llm_config

REGISTRY = """
version: 1
providers:
  primary:
    protocol: openai-compatible
    base_url: https://llm.example.com/v1/
    credential_file: llm/{app_id}/primary-api-key
    timeout_seconds: 80
models:
  chat-default:
    provider: primary
    model: chat-v1
    fallbacks: [chat-backup]
  chat-backup:
    provider: primary
    model: chat-v0
apps:
  health:
    models:
      chat-default:
        model: health-chat-v2
        timeout_seconds: 120
"""


def test_example_registry_matches_published_schema():
    root = Path(__file__).parents[1]
    schema = json.loads(
        (root / "contracts" / "llm-registry.schema.json").read_text(encoding="utf-8")
    )
    registry = yaml.safe_load((root / "llm" / "registry.yml.example").read_text(encoding="utf-8"))

    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(
        registry
    )


def prepare(tmp_path: Path):
    registry = tmp_path / "registry.yml"
    registry.write_text(REGISTRY, encoding="utf-8")
    secrets = tmp_path / "secrets"
    key_file = secrets / "llm" / "health" / "primary-api-key"
    key_file.parent.mkdir(parents=True)
    key_file.write_text("private-test-key", encoding="utf-8")
    return registry, secrets, key_file


def test_resolves_app_override_and_per_app_key(tmp_path):
    registry, secrets, key_file = prepare(tmp_path)
    config = resolve_llm_config(
        registry,
        secrets_dir=secrets,
        app_id="health",
        alias="chat-default",
    )

    assert config.base_url == "https://llm.example.com/v1"
    assert config.model == "health-chat-v2"
    assert config.api_key_file == key_file
    assert config.read_api_key() == "private-test-key"
    assert config.timeout_seconds == 120
    assert config.fallbacks == ("chat-backup",)


def test_rendered_env_contains_key_path_but_not_key_value(tmp_path):
    registry, secrets, _ = prepare(tmp_path)
    config = resolve_llm_config(
        registry,
        secrets_dir=secrets,
        app_id="health",
        alias="chat-default",
    )
    output = render_env([("CHAT", config)])

    assert "SHADOW_LLM_CHAT_BASE_URL" in output
    assert "primary-api-key" in output
    assert "private-test-key" not in output


def test_rejects_inline_secret(tmp_path):
    registry = tmp_path / "registry.yml"
    registry.write_text(
        REGISTRY.replace("timeout_seconds: 80", "api_key: leaked"), encoding="utf-8"
    )

    with pytest.raises(LLMConfigError, match="inline secret"):
        resolve_llm_config(
            registry,
            secrets_dir=tmp_path,
            app_id="health",
            alias="chat-default",
            require_secret=False,
        )


def test_rejects_credential_path_escape(tmp_path):
    registry = tmp_path / "registry.yml"
    registry.write_text(
        REGISTRY.replace("llm/{app_id}/primary-api-key", "../../outside-key"),
        encoding="utf-8",
    )

    with pytest.raises(LLMConfigError, match="escapes"):
        resolve_llm_config(
            registry,
            secrets_dir=tmp_path / "secrets",
            app_id="health",
            alias="chat-default",
            require_secret=False,
        )
