import json
from pathlib import Path

import httpx
import jsonschema
import pytest

from scripts.build_shadow_profile import build_shadow_profile
from scripts.deployment_doctor import inspect_deployment
from shadow_sdk.conformance import (
    apply_evidence,
    gate_failures,
    restore_drill_to_evidence,
)
from shadow_sdk.observability import OperationContext, OperationContextError
from shadow_sdk.plugin_contracts import PluginContractError

ROOT = Path(__file__).parents[1]


def _release(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "0")
    return build_shadow_profile(
        platform_root=ROOT,
        deployment_path=ROOT / "fixtures" / "conformance-deployment.yml",
        catalog_path=ROOT / "fixtures" / "conformance-apps.yml",
        profile_path=ROOT / "fixtures" / "conformance-profile.yml",
        instances_path=ROOT / "fixtures" / "conformance-instances.yml",
        plugin_roots=[ROOT / "fixtures" / "conformance-plugin"],
        output_dir=tmp_path,
    )


def _observed_status(release: Path) -> dict:
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(204))
    ) as client:
        report = inspect_deployment(
            release,
            environment={
                "SHADOW_CONFORMANCE_BASE_URL": "https://conformance.example.test",
                "SHADOW_CONFORMANCE_TOKEN": "secret",
            },
            client=client,
            context=OperationContext(
                run_id="doctor-run",
                correlation_id="workflow-one",
                trace_id="trace-one",
                request_id="request-one",
            ),
        )
    status = report["capability_status"]
    evidence = {
        "version": 1,
        "protocol": "shadow.conformance-evidence.v1",
        "evidence_id": "domain-probe-run",
        "producer": {"project_id": "conformance", "component": "capability-probe"},
        "deployment_id": status["deployment_id"],
        "build_id": status["build_id"],
        "observed_at": report["generated_at"],
        "correlation": report["correlation"],
        "records": [
            {
                "capability_ref": item["capability_ref"],
                "stage": "observed",
                "status": "passed",
                "detail": "domain conformance probe exercised the capability",
            }
            for item in status["capabilities"]
            if item["selected"]
        ],
    }
    return apply_evidence(status, evidence, platform_root=ROOT)


def test_operation_context_has_stable_retry_and_transport_semantics():
    context = OperationContext(
        run_id="run-one",
        correlation_id="workflow-one",
        trace_id="trace-one",
        request_id="request-one",
        causation_id="request-zero",
        idempotency_key="mutation-one",
    )

    assert context.as_headers() == {
        "X-Shadow-Run-Id": "run-one",
        "X-Correlation-Id": "workflow-one",
        "X-Shadow-Trace-Id": "trace-one",
        "X-Request-Id": "request-one",
        "X-Causation-Id": "request-zero",
        "Idempotency-Key": "mutation-one",
    }
    with pytest.raises(OperationContextError, match="safe identifier"):
        OperationContext(
            run_id="bad value",
            correlation_id="workflow-one",
            trace_id="trace-one",
            request_id="request-one",
        )


def test_conformance_contracts_are_valid_draft_2020_schemas():
    for name in (
        "shadow-operation-context.schema.json",
        "shadow-capability-status.schema.json",
        "shadow-conformance-evidence.schema.json",
        "shadow-restore-drill.schema.json",
    ):
        schema = json.loads((ROOT / "contracts" / name).read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)


def test_cross_project_evidence_must_follow_capability_stage_order(tmp_path, monkeypatch):
    release = _release(tmp_path, monkeypatch)
    status = json.loads((release / "shadow-capability-status.json").read_text("utf-8"))
    capability_ref = status["capabilities"][0]["capability_ref"]
    evidence = {
        "version": 1,
        "protocol": "shadow.conformance-evidence.v1",
        "evidence_id": "external-observation",
        "producer": {"project_id": "conformance", "component": "contract-tests"},
        "deployment_id": status["deployment_id"],
        "build_id": status["build_id"],
        "observed_at": "2026-08-30T00:00:00Z",
        "correlation": {
            "run_id": "external-run",
            "correlation_id": "workflow-one",
            "trace_id": "trace-one",
            "request_id": "request-one",
        },
        "records": [
            {
                "capability_ref": capability_ref,
                "stage": "observed",
                "status": "passed",
                "detail": "consumer observed a successful response",
            }
        ],
    }

    with pytest.raises(PluginContractError, match="out of order"):
        apply_evidence(status, evidence, platform_root=ROOT)
    assert gate_failures(status, "client") == []
    assert gate_failures(status, "observed")


def test_restore_drill_emits_restore_tested_evidence(tmp_path, monkeypatch):
    release = _release(tmp_path / "release", monkeypatch)
    status = _observed_status(release)
    capability_ref = status["capabilities"][0]["capability_ref"]
    drill = {
        "version": 1,
        "protocol": "shadow.restore-drill.v1",
        "drill_id": "restore-drill-one",
        "project_id": "conformance",
        "deployment_id": status["deployment_id"],
        "build_id": status["build_id"],
        "capability_refs": [capability_ref],
        "correlation": {
            "run_id": "restore-run-one",
            "correlation_id": "continuity-one",
            "trace_id": "restore-trace-one",
            "request_id": "restore-request-one",
        },
        "backup": {
            "backup_id": "backup-one",
            "created_at": "2026-08-30T00:00:00Z",
            "immutable": True,
            "sha256": "a" * 64,
        },
        "restore": {
            "source_backup_id": "backup-one",
            "target_kind": "isolated",
            "production": False,
            "started_at": "2026-08-30T00:01:00Z",
            "completed_at": "2026-08-30T00:02:00Z",
            "cleanup_completed": True,
            "rpo_seconds": 60,
            "rto_seconds": 60,
        },
        "checks": [
            {"name": "schema-contract", "category": "contract", "status": "passed"},
            {"name": "record-count", "category": "data", "status": "passed"},
            {"name": "ready-probe", "category": "health", "status": "passed"},
        ],
    }
    drill_path = tmp_path / "restore-drill.json"
    drill_path.write_text(json.dumps(drill), encoding="utf-8")

    evidence = restore_drill_to_evidence(drill_path, status, platform_root=ROOT)
    merged = apply_evidence(status, evidence, platform_root=ROOT)

    assert evidence["records"][0]["stage"] == "restore-tested"
    restored = next(
        item for item in merged["capabilities"] if item["capability_ref"] == capability_ref
    )
    assert restored["maturity"] == "restore-tested"
    assert merged["summary"]["restore_tested"] == 1


def test_restore_drill_rejects_failed_or_incomplete_checks(tmp_path, monkeypatch):
    release = _release(tmp_path / "release", monkeypatch)
    status = _observed_status(release)
    drill = {
        "version": 1,
        "protocol": "shadow.restore-drill.v1",
        "drill_id": "restore-drill-failed",
        "project_id": "conformance",
        "deployment_id": status["deployment_id"],
        "build_id": status["build_id"],
        "capability_refs": [status["capabilities"][0]["capability_ref"]],
        "correlation": {
            "run_id": "restore-run-two",
            "correlation_id": "continuity-two",
            "trace_id": "restore-trace-two",
            "request_id": "restore-request-two",
        },
        "backup": {
            "backup_id": "backup-two",
            "created_at": "2026-08-30T00:00:00Z",
            "immutable": True,
            "sha256": "b" * 64,
        },
        "restore": {
            "source_backup_id": "backup-two",
            "target_kind": "isolated",
            "production": False,
            "started_at": "2026-08-30T00:01:00Z",
            "completed_at": "2026-08-30T00:02:00Z",
            "cleanup_completed": True,
            "rpo_seconds": 60,
            "rto_seconds": 60,
        },
        "checks": [
            {"name": "schema-contract", "category": "contract", "status": "passed"},
            {"name": "record-count", "category": "data", "status": "failed"},
            {"name": "ready-probe", "category": "health", "status": "passed"},
        ],
    }
    drill_path = tmp_path / "restore-drill-failed.json"
    drill_path.write_text(json.dumps(drill), encoding="utf-8")

    with pytest.raises(PluginContractError, match="missing passed checks"):
        restore_drill_to_evidence(drill_path, status, platform_root=ROOT)
