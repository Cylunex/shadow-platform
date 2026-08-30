import json
import shutil
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
    report = json.loads((target / "shadow-deployment-report.json").read_text("utf-8"))
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
    assert nexus["domains"][0]["surfaces"][0]["display"]["metrics"][0] == {
        "id": "records",
        "label": "Records",
        "value_pointer": "/count",
        "unit": "items",
    }
    assert nexus["domains"][0]["surfaces"][2]["action"]["intent"] == (
        "conformance.draft.note"
    )
    assert nexus["domains"][0]["connection"]["base_url_env"] == (
        "SHADOW_CONFORMANCE_BASE_URL"
    )
    assert nexus["domains"][0]["app"] == {
        "canonical_url": "https://conformance.example.com/",
        "aliases": ["https://nas.example.com/conformance/"],
    }
    assert app["schemaVersion"] == 5
    assert app["platform"]["homeModuleId"] == "conformance"
    assert app["modules"][0]["product_id"] == "shadow-conformance"
    assert app["modules"][0]["canonical_url"] == "https://conformance.example.com/"
    assert lock["build_id"] == nexus["build_id"] == app["platform"]["buildId"]
    assert report["build_id"] == lock["build_id"]
    assert report["summary"] == {
        "products": 1,
        "dsh_plugins": 1,
        "nexus_domains": 1,
        "app_modules": 1,
        "degraded": 0,
        "filtered": 0,
        "incompatible": 0,
    }
    assert report["products"][0]["status"] == "enabled"
    assert (target / lock["dsh_bundle"]["path"] / "package.json").is_file()
    assert target.name == lock["build_id"]
    assert {item["path"] for item in lock["outputs"]} == {
        "shadow-dsh-runtime.json",
        "shadow-nexus-runtime.json",
        "shadow-app-runtime.json",
        "shadow-deployment-report.json",
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
            "field_schema_version": 2,
            "field_schema_digest": "a" * 64,
            "expires_at": "2026-08-27T00:00:00Z",
            "source_refs": [],
            "trace_id": "trace-example",
            "receipt": None,
            "replayed": False,
        },
        contract_schema_path(ROOT, "shadow-review.schema.json"),
        label="review fixture",
    )


def test_context_capture_and_suggestion_contracts() -> None:
    validate_document(
        {
            "protocol": "shadow.context.v1",
            "context_id": "ctx_example123",
            "session_id": "session-a",
            "source_domain": "health",
            "resource_refs": ["shadow://health/weight-series/latest"],
            "time_range": {
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-08-27T00:00:00Z",
            },
            "goal": "解释最近体重变化",
            "asset_refs": [],
            "capability_grants": ["health.summary.read"],
            "created_at": "2026-08-27T00:00:00Z",
            "expires_at": "2026-08-28T00:00:00Z",
        },
        contract_schema_path(ROOT, "shadow-context-pack.schema.json"),
        label="context fixture",
    )
    validate_document(
        {
            "protocol": "shadow.capture.v1",
            "capture_id": "cap_example123",
            "source_type": "android.share.text",
            "occurred_at": "2026-08-27T00:00:00Z",
            "received_at": "2026-08-27T00:00:01Z",
            "text": "一段准备分类的分享文本",
            "content_refs": [],
            "source_app": "com.example.browser",
            "content_hash": "b" * 64,
            "privacy_class": "personal",
            "candidate_domains": [],
            "trace_id": "trace-capture",
        },
        contract_schema_path(ROOT, "shadow-capture-envelope.schema.json"),
        label="capture fixture",
    )
    validate_document(
        {
            "protocol": "shadow.suggestion.v1",
            "suggestion_id": "sug_example123",
            "domain": "health",
            "rule_id": "health.weekly-review",
            "dedupe_key": "health:weekly:2026-W35",
            "title": "本周健康回顾",
            "summary": "体重记录完整，训练记录偏少。",
            "reason": "基于最近七天已确认数据。",
            "evidence_refs": ["shadow://health/weekly-reviews/2026-W35"],
            "importance": "normal",
            "confidence": 0.8,
            "allowed_actions": ["ignore", "snooze", "mute", "view_evidence"],
            "created_at": "2026-08-27T00:00:00Z",
            "valid_until": "2026-09-03T00:00:00Z",
            "data_freshness": {
                "observed_at": "2026-08-26T23:59:59Z",
                "missing_ratio": 0.1,
            },
        },
        contract_schema_path(ROOT, "shadow-suggestion.schema.json"),
        label="suggestion fixture",
    )


def test_search_surface_requires_generic_item_projection() -> None:
    document = {
        "version": 1,
        "presentation": {
            "short_id": "archive",
            "title": "Archive",
            "caption": "Personal archive",
            "icon": "archive",
            "color": "#112233",
            "order": 10,
        },
        "surfaces": [
            {
                "id": "search",
                "type": "search",
                "capability": "archive.records.search",
                "operation_id": "search_archive",
                "display": {"collection_pointer": "/items"},
            }
        ],
    }

    with pytest.raises(PluginContractError, match="item_title_pointer"):
        validate_document(
            document,
            contract_schema_path(ROOT, "shadow-surfaces.schema.json"),
            label="search surface fixture",
        )


def test_quick_action_must_match_one_capture_surface(tmp_path) -> None:
    root = tmp_path / "plugin"
    shutil.copytree(ROOT / "fixtures" / "conformance-plugin", root)
    surfaces_path = root / "contracts" / "surfaces.yaml"
    surfaces = yaml.safe_load(surfaces_path.read_text("utf-8"))
    quick_action = next(
        item for item in surfaces["surfaces"] if item["type"] == "quick-action"
    )
    quick_action["action"]["intent"] = "conformance.unknown.note"
    surfaces_path.write_text(yaml.safe_dump(surfaces), encoding="utf-8")
    arguments = _arguments(tmp_path / "output")
    arguments["plugin_roots"] = [root]

    with pytest.raises(PluginContractError, match="must match one capture surface"):
        build_shadow_profile(**arguments)
