import json
import shutil
from pathlib import Path

import pytest
import yaml

from scripts.build_dsh_bundle import _dsh_value_spec, _resolve_ref, build_bundle
from shadow_sdk.plugin_contracts import (
    PluginContractError,
    contract_schema_path,
    semver_satisfies,
    validate_capability_semantics,
    validate_document,
    validate_plugin,
)

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "fixtures" / "conformance-plugin"


def test_conformance_plugin_is_valid():
    plugin = validate_plugin(FIXTURE, ROOT)

    assert plugin.plugin_id == "shadow-conformance"
    assert plugin.version == "0.1.0"
    assert {item["risk_level"] for item in plugin.agent_manifest["capabilities"]} == {
        "L0",
        "L1",
        "L2",
        "L3",
    }


def test_capability_semantics_enforce_risk_confirmation_mapping():
    manifest = yaml.safe_load((FIXTURE / "agent" / "manifest.yaml").read_text(encoding="utf-8"))
    manifest["capabilities"][1]["confirmation"] = "none"

    errors = validate_capability_semantics(manifest)

    assert "conformance.drafts.create:confirmation-does-not-match-L1" in errors


def test_dsh_semver_ranges_handle_prereleases():
    assert semver_satisfies("0.1.1-rc.2", ">=0.1.1-rc.1 <0.2.0")
    assert not semver_satisfies("0.1.0-rc.8", ">=0.1.1-rc.1 <0.2.0")


def test_openapi_refs_keep_siblings_and_anyof_maps_to_dsh_union():
    document = {
        "components": {
            "schemas": {
                "RecordId": {"type": "string", "description": "base description"}
            }
        }
    }

    resolved = _resolve_ref(
        document,
        {"$ref": "#/components/schemas/RecordId", "description": "usage description"},
    )
    union = _dsh_value_spec(
        document,
        {"anyOf": [{"type": "string"}, {"type": "null"}]},
    )

    assert resolved == {"type": "string", "description": "usage description"}
    assert union == {"oneOf": [{"type": "string"}, {"type": "null"}]}


def test_plugin_definition_and_agent_versions_must_match(tmp_path):
    copied = tmp_path / "plugin"
    shutil.copytree(FIXTURE, copied)
    definition_path = copied / "shadow-plugin.yaml"
    definition = yaml.safe_load(definition_path.read_text(encoding="utf-8"))
    definition["metadata"]["version"] = "0.2.0"
    definition_path.write_text(yaml.safe_dump(definition), encoding="utf-8")

    with pytest.raises(PluginContractError, match="plugin version"):
        validate_plugin(copied, ROOT)


def test_dsh_bundle_build_is_deterministic_and_native(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "0")
    arguments = {
        "platform_root": ROOT,
        "profile_path": ROOT / "fixtures" / "conformance-profile.yml",
        "instances_path": ROOT / "fixtures" / "conformance-instances.yml",
        "plugin_roots": [FIXTURE],
    }
    first = build_bundle(output_dir=tmp_path / "first", **arguments)
    second = build_bundle(output_dir=tmp_path / "second", **arguments)

    first_files = sorted(path.relative_to(first) for path in first.rglob("*") if path.is_file())
    second_files = sorted(path.relative_to(second) for path in second.rglob("*") if path.is_file())
    assert first_files == second_files
    for relative in first_files:
        assert (first / relative).read_bytes() == (second / relative).read_bytes()

    package = json.loads((first / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((first / "agent-bundle.lock").read_text(encoding="utf-8"))
    patch = yaml.safe_load((first / "cordis.patch.yml").read_text(encoding="utf-8"))
    generated = (first / "profile.generated.js").read_text(encoding="utf-8")

    assert package["dsh"]["bundle"]["patch"] == "./cordis.patch.yml"
    assert package["peerDependencies"]["@deepseek-ai/dsh-tools"] == "0.1.1-rc.2"
    assert package["engines"]["node"] == "^22.19.0 || >=24.0.0"
    assert "dependencies" not in package
    assert package["version"].startswith("0.0.0-shadow.")
    assert len(package["version"].removeprefix("0.0.0-shadow.")) == 12
    assert lock["profile_id"] == "shadow-conformance"
    assert lock["version"] == 3
    assert lock["build_id"]
    assert lock["package_version"] == package["version"]
    assert lock["runtime_distribution_version"] == "0.1.1-rc.2"
    assert lock["runtime_tools_api_version"] == "0.1.1-rc.2"
    assert lock["source_date_epoch"] == 0
    assert lock["model_exposure"]["tool_catalog_chars"] > 0
    assert lock["model_exposure"]["skill_catalog_chars"] > 0
    assert [row["id"] for row in patch[0]["insert"]] == [
        "shadow-policy",
        "shadow-domain-conformance",
    ]
    assert "shadow_conformance_records_get" in generated
    assert "shadow_conformance_records_publish" in generated
    assert "resourcePath" in generated
    assert "resourceBase" in (first / "domain.js").read_text(encoding="utf-8")
    assert "config.instanceId" in (first / "domain.js").read_text(encoding="utf-8")
    runtime = (first / "runtime.js").read_text(encoding="utf-8")
    assert "readBoundedJson" in runtime
    assert "tool.retryPolicy === 'idempotent'" in runtime
    assert "tool.maxModelChars" in runtime
    runtime_manifest = json.loads(
        (first / "shadow-runtime-manifest.json").read_text(encoding="utf-8")
    )
    assert runtime_manifest["adapter"] == "shadow-dsh"
    assert runtime_manifest["build_id"] == lock["build_id"]
    assert runtime_manifest["domains"][0]["instance_id"] == "conformance-test"
    assert (
        first
        / "skills"
        / "shadow-conformance"
        / "shadow-conformance"
        / "references"
        / "result-format.md"
    ).is_file()


def test_dsh_bundle_rejects_incompatible_runtime(tmp_path):
    profile_path = tmp_path / "profile.yml"
    profile = yaml.safe_load(
        (ROOT / "fixtures" / "conformance-profile.yml").read_text(encoding="utf-8")
    )
    profile["runtime"]["distribution_version"] = "0.1.0-rc.8"
    profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")

    with pytest.raises(PluginContractError, match="requires DSH distribution"):
        build_bundle(
            platform_root=ROOT,
            profile_path=profile_path,
            instances_path=ROOT / "fixtures" / "conformance-instances.yml",
            plugin_roots=[FIXTURE],
            output_dir=tmp_path / "output",
        )


def test_hidden_tools_are_not_registered(tmp_path):
    copied = tmp_path / "plugin"
    shutil.copytree(FIXTURE, copied)
    manifest_path = copied / "agent" / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["capabilities"][3]["tools"][0]["exposure"] = "hidden"
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")

    target = build_bundle(
        platform_root=ROOT,
        profile_path=ROOT / "fixtures" / "conformance-profile.yml",
        instances_path=ROOT / "fixtures" / "conformance-instances.yml",
        plugin_roots=[copied],
        output_dir=tmp_path / "output",
    )

    generated = (target / "profile.generated.js").read_text(encoding="utf-8")
    assert "shadow_conformance_records_publish" not in generated


def test_skill_resources_reject_sensitive_files(tmp_path):
    copied = tmp_path / "plugin"
    shutil.copytree(FIXTURE, copied)
    forbidden = (
        copied
        / "agent"
        / "skills"
        / "shadow-conformance"
        / "references"
        / "private.key"
    )
    forbidden.write_text("not-a-real-key", encoding="utf-8")

    with pytest.raises(PluginContractError, match="forbidden file"):
        build_bundle(
            platform_root=ROOT,
            profile_path=ROOT / "fixtures" / "conformance-profile.yml",
            instances_path=ROOT / "fixtures" / "conformance-instances.yml",
            plugin_roots=[copied],
            output_dir=tmp_path / "output",
        )


def test_profile_cannot_select_unknown_capability(tmp_path):
    profile_path = tmp_path / "profile.yml"
    profile = yaml.safe_load(
        (ROOT / "fixtures" / "conformance-profile.yml").read_text(encoding="utf-8")
    )
    profile["plugins"][0]["capabilities"] = ["conformance.unknown.read"]
    profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")

    with pytest.raises(PluginContractError, match="unknown capabilities"):
        build_bundle(
            platform_root=ROOT,
            profile_path=profile_path,
            instances_path=ROOT / "fixtures" / "conformance-instances.yml",
            plugin_roots=[FIXTURE],
            output_dir=tmp_path / "output",
        )


def test_profile_model_exposure_budget_is_enforced(tmp_path):
    profile_path = tmp_path / "profile.yml"
    profile = yaml.safe_load(
        (ROOT / "fixtures" / "conformance-profile.yml").read_text(encoding="utf-8")
    )
    profile["budgets"]["max_tool_catalog_chars"] = 1024
    profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")

    with pytest.raises(PluginContractError, match="max_tool_catalog_chars"):
        build_bundle(
            platform_root=ROOT,
            profile_path=profile_path,
            instances_path=ROOT / "fixtures" / "conformance-instances.yml",
            plugin_roots=[FIXTURE],
            output_dir=tmp_path / "output",
        )


def test_profile_rejects_duplicate_plugin_instances(tmp_path):
    profile_path = tmp_path / "profile.yml"
    profile = yaml.safe_load(
        (ROOT / "fixtures" / "conformance-profile.yml").read_text(encoding="utf-8")
    )
    profile["plugins"].append(dict(profile["plugins"][0]))
    profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")

    with pytest.raises(PluginContractError, match="same plugin more than once"):
        build_bundle(
            platform_root=ROOT,
            profile_path=profile_path,
            instances_path=ROOT / "fixtures" / "conformance-instances.yml",
            plugin_roots=[FIXTURE],
            output_dir=tmp_path / "output",
        )


def test_runtime_neutral_result_and_confirmation_contracts():
    result = {
        "version": 1,
        "kind": "resource",
        "summary": "记录已生成，可在领域应用中查看。",
        "resource_refs": [{"uri": "shadow://conformance/records/example"}],
        "meta": {
            "sensitivity": "personal",
            "truncated": False,
            "provenance": {
                "plugin_id": "shadow-conformance",
                "capability_id": "conformance.records.read",
            },
        },
    }
    receipt = {
        "version": 1,
        "receipt_id": "receipt-example",
        "issuer": "shadow-platform",
        "actor": "user-example",
        "audience": "conformance",
        "plugin_id": "shadow-conformance",
        "capability_id": "conformance.records.publish",
        "tool_name": "conformance.records.publish",
        "effect": "publish",
        "arguments_sha256": "a" * 64,
        "resource_uri": "shadow://conformance/records/example",
        "issued_at": "2026-08-22T00:00:00Z",
        "expires_at": "2026-08-22T00:05:00Z",
        "nonce": "example-nonce-1234",
        "single_use": True,
        "signature": {
            "algorithm": "EdDSA",
            "key_id": "example-key",
            "value": "REPLACE_WITH_SIGNATURE_VALUE_0123456789",
        },
    }

    validate_document(
        result,
        contract_schema_path(ROOT, "shadow-tool-result.schema.json"),
        label="tool result",
    )
    validate_document(
        receipt,
        contract_schema_path(ROOT, "confirmation-receipt.schema.json"),
        label="confirmation receipt",
    )
