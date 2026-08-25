import json
import shutil
from pathlib import Path

import pytest
import yaml

from scripts.build_dsh_bundle import (
    _dsh_value_spec,
    _mcp_public_tool_name,
    _resolve_ref,
    build_bundle,
)
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


class _CordisLoader(yaml.SafeLoader):
    pass


_CordisLoader.add_constructor(
    "tag:yaml.org,2002:js", lambda loader, node: loader.construct_scalar(node)
)


def test_conformance_plugin_is_valid():
    plugin = validate_plugin(FIXTURE, ROOT)

    assert plugin.plugin_id == "shadow-conformance"
    assert plugin.version == "0.1.0"
    assert {item["risk_level"] for item in plugin.agent_manifest["capabilities"]} == {
        "L0",
        "L1",
        "L2",
        "L3",
        "L4",
    }


def test_capability_semantics_enforce_risk_confirmation_mapping():
    manifest = yaml.safe_load((FIXTURE / "agent" / "manifest.yaml").read_text(encoding="utf-8"))
    manifest["capabilities"][1]["confirmation"] = "none"

    errors = validate_capability_semantics(manifest)

    assert "conformance.drafts.create:confirmation-does-not-match-L1" in errors


def test_delete_capability_must_preserve_at_least_one_item():
    manifest = yaml.safe_load((FIXTURE / "agent" / "manifest.yaml").read_text(encoding="utf-8"))
    delete_capability = next(
        item for item in manifest["capabilities"] if item["effect"] == "delete"
    )
    delete_capability.pop("destructive_limits")

    errors = validate_capability_semantics(manifest)

    assert (
        "conformance.records.delete:delete-must-preserve-at-least-one-item" in errors
    )


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


def test_dsh_result_modes_preserve_run_identity_and_bounded_structured_payload() -> None:
    source = (ROOT / "scripts" / "build_dsh_bundle.py").read_text(encoding="utf-8")
    assert "'run_id', 'status', 'mode', 'kind', 'run_resource_uri'" in source
    assert "tool.resultMode === 'structured'" in source
    assert "value.model_payload !== undefined ? value.model_payload : value.data" in source

    schema = json.loads(
        (ROOT / "contracts" / "agent-capability-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert "structured" in schema["$defs"]["tool"]["properties"]["result_mode"]["enum"]


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
    patch = yaml.load(
        (first / "cordis.patch.yml").read_text(encoding="utf-8"), Loader=_CordisLoader
    )
    generated = (first / "profile.generated.js").read_text(encoding="utf-8")
    generated_profile = json.loads(generated.removeprefix("export const PROFILE = "))

    assert package["dsh"]["bundle"]["patch"] == "./cordis.patch.yml"
    assert package["peerDependencies"]["@deepseek-ai/dsh-tools"] == "0.1.1-rc.2"
    assert package["engines"]["node"] == "^22.19.0 || >=24.0.0"
    assert package["dependencies"]["@deepseek-ai/dsh-mcp-client"] == "0.1.1-rc.2"
    assert package["version"].startswith("0.0.0-shadow.")
    assert len(package["version"].removeprefix("0.0.0-shadow.")) == 12
    assert lock["profile_id"] == "shadow-conformance"
    assert lock["version"] == 4
    assert lock["build_id"]
    assert lock["package_version"] == package["version"]
    assert lock["runtime_distribution_version"] == "0.1.1-rc.2"
    assert lock["runtime_tools_api_version"] == "0.1.1-rc.2"
    assert lock["source_date_epoch"] == 0
    assert lock["model_exposure"]["tool_catalog_chars"] > 0
    assert lock["model_exposure"]["skill_catalog_chars"] > 0
    assert [row["id"] for row in patch[0]["insert"]] == [
        "shadow-policy",
        "shadow-mcp-conformance-test",
        "shadow-domain-conformance",
    ]
    assert "shadow_conformance_records_get" in generated
    assert "shadow_conformance_records_publish" in generated
    assert "shadow_conformance_mcp_read" in generated
    mcp_delete = next(
        tool
        for domain in generated_profile["domains"]
        for tool in domain["tools"]
        if tool["shadowName"] == "conformance.mcp.delete"
    )
    assert "shadow_confirmation" not in mcp_delete["parameters"]
    assert mcp_delete["confirmationArgument"] == "shadow_confirmation"
    assert "resourcePath" in generated
    assert "resourceBase" in (first / "domain.js").read_text(encoding="utf-8")
    assert "config.instanceId" in (first / "domain.js").read_text(encoding="utf-8")
    runtime = (first / "runtime.js").read_text(encoding="utf-8")
    assert "readBoundedJson" in runtime
    assert "tool.retryPolicy === 'idempotent'" in runtime
    assert "tool.maxModelChars" in runtime
    assert "executeMcp" in runtime
    assert "issueConfirmation" in runtime
    policy = (first / "policy.js").read_text(encoding="utf-8")
    assert "agent.ctx.tools.restrict" in policy
    assert "isAuthorizedNestedMcp" in policy
    assert "restoredSkillNames" in policy
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


def test_composition_plugin_dispatches_selected_read_only_domain_tools(tmp_path):
    target = build_bundle(
        platform_root=ROOT,
        profile_path=ROOT / "fixtures" / "composition-profile.yml",
        instances_path=ROOT / "fixtures" / "composition-instances.yml",
        plugin_roots=[
            ROOT / "fixtures" / "composition-health-plugin",
            ROOT / "fixtures" / "composition-ledger-plugin",
            ROOT / "compositions" / "shadow-daily-overview",
        ],
        output_dir=tmp_path / "output",
    )

    generated = (target / "profile.generated.js").read_text(encoding="utf-8")
    runtime = (target / "runtime.js").read_text(encoding="utf-8")
    assert "shadow_shadow_daily_overview_read" in generated
    assert "shadow_health_summary_read" in generated
    assert "shadow_ledger_summary_get" in generated
    assert '"runtimeName":"shadow_health_summary_read"' in generated
    assert '"runtimeName":"shadow_ledger_summary_get"' in generated
    assert "executeComposition" in runtime
    assert "parent: exec.token" in runtime


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


def test_high_risk_profile_requires_confirmation_signing(tmp_path):
    profile_path = tmp_path / "profile.yml"
    profile = yaml.safe_load(
        (ROOT / "fixtures" / "conformance-profile.yml").read_text(encoding="utf-8")
    )
    profile["policy"].pop("confirmation")
    profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")

    with pytest.raises(PluginContractError, match="must configure confirmation signing"):
        build_bundle(
            platform_root=ROOT,
            profile_path=profile_path,
            instances_path=ROOT / "fixtures" / "conformance-instances.yml",
            plugin_roots=[FIXTURE],
            output_dir=tmp_path / "output",
        )


def test_high_risk_mcp_requires_reserved_confirmation_argument(tmp_path):
    copied = tmp_path / "plugin"
    shutil.copytree(FIXTURE, copied)
    manifest_path = copied / "agent" / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    capability = next(
        item for item in manifest["capabilities"] if item["id"] == "conformance.mcp.delete"
    )
    capability["tools"][0].pop("confirmation_argument")
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")

    with pytest.raises(PluginContractError, match="receipt-argument"):
        validate_plugin(copied, ROOT)


def test_selected_transport_requires_its_instance_configuration(tmp_path):
    instances_path = tmp_path / "instances.yml"
    instances = yaml.safe_load(
        (ROOT / "fixtures" / "conformance-instances.yml").read_text(encoding="utf-8")
    )
    instances["instances"]["conformance-test"].pop("base_url_env")
    instances_path.write_text(yaml.safe_dump(instances), encoding="utf-8")

    with pytest.raises(PluginContractError, match="HTTP tools require"):
        build_bundle(
            platform_root=ROOT,
            profile_path=ROOT / "fixtures" / "conformance-profile.yml",
            instances_path=instances_path,
            plugin_roots=[FIXTURE],
            output_dir=tmp_path / "output",
        )


def test_composition_rejects_mutating_target_capabilities(tmp_path):
    copied_health = tmp_path / "health-plugin"
    shutil.copytree(ROOT / "fixtures" / "composition-health-plugin", copied_health)
    manifest_path = copied_health / "agent" / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    capability = manifest["capabilities"][0]
    capability.update(
        effect="write",
        risk_level="L1",
        confirmation="notify",
        reversible=True,
        idempotency_required=True,
    )
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")

    with pytest.raises(PluginContractError, match="composition workflows are read-only"):
        build_bundle(
            platform_root=ROOT,
            profile_path=ROOT / "fixtures" / "composition-profile.yml",
            instances_path=ROOT / "fixtures" / "composition-instances.yml",
            plugin_roots=[
                copied_health,
                ROOT / "fixtures" / "composition-ledger-plugin",
                ROOT / "compositions" / "shadow-daily-overview",
            ],
            output_dir=tmp_path / "output",
        )


def test_mcp_public_name_matches_official_dsh_algorithm():
    assert _mcp_public_tool_name("health", "summary") == "mcp__health__summary"
    assert _mcp_public_tool_name("health", "a tool with spaces") == (
        "mcp__health__a_tool_with_spaces_8faca585f35f"
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


def test_shadow_nexus_model_profile_is_read_only() -> None:
    profile = yaml.safe_load(
        (ROOT / "agents" / "profiles" / "shadow-nexus.yml.example").read_text(
            encoding="utf-8"
        )
    )
    selected = {
        capability
        for plugin in profile["plugins"]
        for capability in plugin["capabilities"]
    }

    assert selected
    assert all(capability.endswith(".read") for capability in selected)
    assert "health.records.draft" not in selected
    assert "ledger.records.draft" not in selected


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
