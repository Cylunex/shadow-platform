import json
from pathlib import Path

import pytest
import yaml

from scripts.activate_shadow_profile import activate_release, verify_release
from scripts.build_shadow_profile import build_shadow_profile
from shadow_sdk.plugin_contracts import (
    PluginContractError,
    contract_schema_path,
    validate_document,
)

ROOT = Path(__file__).parents[1]


def _arguments(tmp_path: Path) -> dict[str, object]:
    return {
        "platform_root": ROOT,
        "deployment_path": ROOT / "fixtures" / "conformance-deployment.yml",
        "catalog_path": ROOT / "fixtures" / "conformance-apps.yml",
        "profile_path": ROOT / "fixtures" / "conformance-profile.yml",
        "instances_path": ROOT / "fixtures" / "conformance-instances.yml",
        "plugin_roots": [ROOT / "fixtures" / "conformance-plugin"],
        "output_dir": tmp_path,
    }


def test_profile_compiler_projects_one_source_to_all_runtimes(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "0")
    target = build_shadow_profile(**_arguments(tmp_path))

    dsh = json.loads((target / "shadow-dsh-runtime.json").read_text("utf-8"))
    nexus = json.loads((target / "shadow-nexus-runtime.json").read_text("utf-8"))
    app = json.loads((target / "shadow-app-runtime.json").read_text("utf-8"))
    lock = json.loads((target / "shadow-deployment.lock").read_text("utf-8"))

    assert dsh["profile_id"] == "shadow-conformance"
    assert nexus["domains"][0]["id"] == "conformance"
    assert nexus["domains"][0]["surfaces"][0]["operation"] == {
        "operation_id": "get_record",
        "method": "GET",
        "path": "/api/agent/records/{record_id}",
        "capability_id": "conformance.records.read",
        "tool_name": "conformance.records.get",
        "effect": "read",
        "risk_level": "L0",
        "confirmation_resource": None,
    }
    assert nexus["domains"][0]["connection"]["base_url_env"] == (
        "SHADOW_CONFORMANCE_BASE_URL"
    )
    assert app["schemaVersion"] == 4
    assert app["modules"][0]["product_id"] == "shadow-conformance"
    assert app["modules"][0]["canonical_url"] == "https://conformance.example.com/"
    assert lock["build_id"] == nexus["build_id"] == app["platform"]["buildId"]
    assert (target / lock["dsh_bundle"]["path"] / "package.json").is_file()
    assert target.name == lock["build_id"]
    assert {item["path"] for item in lock["outputs"]} == {
        "shadow-dsh-runtime.json",
        "shadow-nexus-runtime.json",
        "shadow-app-runtime.json",
    }
    assert verify_release(target)["build_id"] == lock["build_id"]


def test_profile_activation_is_atomic_and_old_release_remains_available(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "0")
    target = build_shadow_profile(**_arguments(tmp_path / "releases"))
    current = tmp_path / "runtime" / "current"

    lock = activate_release(target, current)

    assert current.is_symlink()
    assert current.resolve() == target.resolve()
    assert target.is_dir()
    assert lock["build_id"] == target.name


def test_profile_verification_rejects_modified_projection(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "0")
    target = build_shadow_profile(**_arguments(tmp_path))
    (target / "shadow-nexus-runtime.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(PluginContractError, match="failed verification"):
        verify_release(target)


def test_profile_compiler_rejects_instance_version_drift(tmp_path):
    instances = yaml.safe_load(
        (ROOT / "fixtures" / "conformance-instances.yml").read_text("utf-8")
    )
    instances["instances"]["conformance-test"]["plugin_version"] = "0.2.0"
    path = tmp_path / "instances.yml"
    path.write_text(yaml.safe_dump(instances), encoding="utf-8")
    arguments = _arguments(tmp_path / "output")
    arguments["instances_path"] = path

    with pytest.raises(PluginContractError, match="instance does not match plugin version"):
        build_shadow_profile(**arguments)


def test_standard_review_envelope_contract() -> None:
    validate_document(
        {
            "protocol": "shadow.review.v1",
            "review_id": "review-example",
            "reference": "shadow://example/reviews/review-example",
            "revision": 1,
            "domain": "example",
            "intent": "example.record",
            "summary": "Example review",
            "fields": {"value": 1},
            "risk_level": "L2",
            "state": "pending",
            "created_at": "2026-08-26T00:00:00Z",
            "source_refs": [],
            "trace_id": "trace-example",
            "receipt": None,
            "replayed": False,
        },
        contract_schema_path(ROOT, "shadow-review.schema.json"),
        label="review fixture",
    )
