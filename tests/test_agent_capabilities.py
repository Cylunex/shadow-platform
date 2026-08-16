import json
from pathlib import Path

import jsonschema
import yaml

from scripts.platform_doctor import _validate_capability_manifest
from shadow_sdk.catalog import load_app_catalog


def _published_documents():
    root = Path(__file__).parents[1]
    schema = json.loads(
        (root / "contracts" / "agent-capability-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    manifest = yaml.safe_load(
        (root / "agents" / "capability-manifest.yml.example").read_text(encoding="utf-8")
    )
    registry = yaml.safe_load(
        (root / "agents" / "registry.yml.example").read_text(encoding="utf-8")
    )
    catalog = load_app_catalog(root / "catalog" / "apps.yml")
    return schema, manifest, registry, catalog


def test_capability_manifest_example_matches_schema_and_platform_references():
    schema, manifest, registry, catalog = _published_documents()

    jsonschema.Draft202012Validator(schema).validate(manifest)

    assert _validate_capability_manifest(manifest, catalog, registry["agents"]) == []


def test_capability_manifest_semantics_reject_unsafe_mutation_and_unknown_reference():
    _, manifest, registry, catalog = _published_documents()
    manifest["skills"][0]["capabilities"].append("travel.unknown.write")
    manifest["capabilities"][1].update(
        effect="write", confirmation="never", idempotency_required=False
    )

    errors = _validate_capability_manifest(manifest, catalog, registry["agents"])

    assert any("unknown" in error for error in errors)
    assert any("write-without-confirmation" in error for error in errors)
    assert any("mutation-without-idempotency" in error for error in errors)
