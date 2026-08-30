from __future__ import annotations

import copy
import json
import os
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shadow_sdk.observability import OperationContext
from shadow_sdk.plugin_contracts import (
    PluginContractError,
    ValidatedPlugin,
    contract_schema_path,
    sha256_file,
    validate_document,
)

STAGES = ("contract", "client", "deployed", "observed", "restore_tested")
EVIDENCE_STAGES = {
    "contract": "contract",
    "client": "client",
    "deployed": "deployed",
    "observed": "observed",
    "restore-tested": "restore_tested",
}


def generated_at() -> str:
    source_epoch = os.getenv("SOURCE_DATE_EPOCH")
    moment = (
        datetime.fromtimestamp(int(source_epoch), UTC)
        if source_epoch is not None
        else datetime.now(UTC)
    )
    return moment.isoformat().replace("+00:00", "Z")


def capability_reference(plugin_id: str, instance_id: str, capability_id: str) -> str:
    return f"shadow://capabilities/{plugin_id}/{instance_id}/{capability_id}"


def _stage(
    status: str,
    *,
    checked_at: str | None = None,
    evidence_id: str | None = None,
    detail: str | None = None,
    correlation: dict[str, str] | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {"status": status}
    if checked_at is not None:
        value["checked_at"] = checked_at
    if evidence_id is not None:
        value["evidence_id"] = evidence_id
    if detail is not None:
        value["detail"] = detail
    if correlation is not None:
        value["correlation"] = correlation
    return value


def _maturity(capability: dict[str, Any]) -> str:
    if not capability["selected"]:
        return "not-selected"
    stages = capability["stages"]
    if any(stages[name]["status"] == "failed" for name in STAGES):
        return "failed"
    highest = "none"
    for internal, public in zip(
        STAGES,
        ("contract", "client", "deployed", "observed", "restore-tested"),
        strict=True,
    ):
        if stages[internal]["status"] != "passed":
            break
        highest = public
    return highest


def refresh_capability_status(report: dict[str, Any]) -> dict[str, Any]:
    capabilities = report["capabilities"]
    for item in capabilities:
        item["maturity"] = _maturity(item)
    selected = [item for item in capabilities if item["selected"]]
    report["summary"] = {
        "capabilities": len(capabilities),
        "selected": len(selected),
        "contract": sum(
            item["stages"]["contract"]["status"] == "passed" for item in selected
        ),
        "client": sum(
            item["stages"]["client"]["status"] == "passed" for item in selected
        ),
        "deployed": sum(
            item["stages"]["deployed"]["status"] == "passed" for item in selected
        ),
        "observed": sum(
            item["stages"]["observed"]["status"] == "passed" for item in selected
        ),
        "restore_tested": sum(
            item["stages"]["restore_tested"]["status"] == "passed" for item in selected
        ),
        "failed": sum(item["maturity"] == "failed" for item in selected),
    }
    return report


def build_capability_status(
    *,
    platform_root: Path,
    deployment_id: str,
    build_id: str,
    profile_id: str,
    products: dict[str, dict[str, Any]],
    plugins: dict[str, ValidatedPlugin],
    profile: dict[str, Any],
    nexus_domains: list[dict[str, Any]],
) -> dict[str, Any]:
    timestamp = generated_at()
    context = OperationContext(
        run_id=f"build-{build_id[:32]}",
        correlation_id=f"deployment-{deployment_id}",
        trace_id=build_id[:32],
        request_id=f"build-{build_id[:32]}",
    )
    dsh_selection: dict[tuple[str, str], set[str]] = {}
    for item in profile["plugins"]:
        selected_capabilities = item["capabilities"]
        if selected_capabilities == "*":
            plugin = plugins[item["plugin_id"]]
            selected = {capability["id"] for capability in plugin.agent_manifest["capabilities"]}
        else:
            selected = set(selected_capabilities)
        dsh_selection[(item["plugin_id"], item["instance_id"])] = selected
    nexus_selection = {
        domain["instance_id"]: {
            surface["capability"]
            for surface in domain["surfaces"]
            if surface.get("capability") is not None
        }
        for domain in nexus_domains
    }
    capabilities: list[dict[str, Any]] = []
    for product_id, product in sorted(products.items()):
        plugin_id = product.get("plugin_id")
        instance_id = product.get("instance_id")
        plugin = plugins.get(plugin_id)
        if plugin is None or not isinstance(instance_id, str):
            continue
        channels = set(product["channels"])
        selected_by_dsh = dsh_selection.get((plugin_id, instance_id), set())
        selected_by_nexus = nexus_selection.get(instance_id, set())
        selected_by_app = set((product.get("app") or {}).get("capabilities", []))
        for capability in plugin.agent_manifest["capabilities"]:
            capability_id = capability["id"]
            client_channels = sorted(
                channel
                for channel, selected_ids in (
                    ("app", selected_by_app),
                    ("dsh", selected_by_dsh),
                    ("nexus", selected_by_nexus),
                )
                if channel in channels and capability_id in selected_ids
            )
            selected = bool(client_channels)
            capabilities.append(
                {
                    "capability_ref": capability_reference(
                        plugin_id, instance_id, capability_id
                    ),
                    "plugin_id": plugin_id,
                    "plugin_version": plugin.version,
                    "instance_id": instance_id,
                    "product_id": product_id,
                    "app_id": plugin.agent_manifest["app_id"],
                    "capability_id": capability_id,
                    "effect": capability["effect"],
                    "risk_level": capability["risk_level"],
                    "selected": selected,
                    "client_channels": client_channels,
                    "contract_sha256": sha256_file(plugin.descriptor_paths["agent"]),
                    "maturity": "none",
                    "stages": {
                        "contract": _stage(
                            "passed",
                            checked_at=timestamp,
                            evidence_id=f"build:{build_id}:contract",
                            detail="plugin schema and semantic validation passed",
                            correlation=context.as_dict(),
                        ),
                        "client": _stage(
                            "passed" if selected else "not_applicable",
                            checked_at=timestamp,
                            evidence_id=f"build:{build_id}:client" if selected else None,
                            detail=(
                                f"compiled for {', '.join(client_channels)}"
                                if selected
                                else "capability is not selected by this deployment"
                            ),
                            correlation=context.as_dict() if selected else None,
                        ),
                        "deployed": _stage("unknown"),
                        "observed": _stage("unknown"),
                        "restore_tested": _stage("unknown"),
                    },
                }
            )
    capability_refs = [item["capability_ref"] for item in capabilities]
    if len(capability_refs) != len(set(capability_refs)):
        raise PluginContractError(
            "deployment produces duplicate capability references; plugin instances must be unique"
        )
    report = {
        "version": 1,
        "protocol": "shadow.capability-status.v1",
        "deployment_id": deployment_id,
        "build_id": build_id,
        "profile_id": profile_id,
        "generated_at": timestamp,
        "correlation": context.as_dict(),
        "summary": {},
        "capabilities": capabilities,
    }
    refresh_capability_status(report)
    validate_document(
        report,
        contract_schema_path(platform_root, "shadow-capability-status.schema.json"),
        label="capability status",
    )
    return report


def load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PluginContractError(f"cannot load {label}: {path}") from exc
    if not isinstance(value, dict):
        raise PluginContractError(f"{label} must contain an object")
    return value


def apply_evidence(
    report: dict[str, Any],
    evidence: dict[str, Any],
    *,
    platform_root: Path,
) -> dict[str, Any]:
    validate_document(
        report,
        contract_schema_path(platform_root, "shadow-capability-status.schema.json"),
        label="capability status",
    )
    validate_document(
        evidence,
        contract_schema_path(platform_root, "shadow-conformance-evidence.schema.json"),
        label="conformance evidence",
    )
    for key in ("deployment_id", "build_id"):
        if evidence[key] != report[key]:
            raise PluginContractError(f"conformance evidence targets a different {key}")
    merged = copy.deepcopy(report)
    by_ref = {item["capability_ref"]: item for item in merged["capabilities"]}
    for record in evidence["records"]:
        item = by_ref.get(record["capability_ref"])
        if item is None:
            raise PluginContractError(
                f"unknown capability evidence target: {record['capability_ref']}"
            )
        internal_stage = EVIDENCE_STAGES[record["stage"]]
        current = item["stages"][internal_stage]
        checked_at = evidence["observed_at"]
        if current.get("checked_at") and _parse_time(current["checked_at"]) > _parse_time(
            checked_at
        ):
            continue
        desired = "passed" if record["status"] == "passed" else "failed"
        if desired == "passed":
            stage_index = STAGES.index(internal_stage)
            if stage_index and item["stages"][STAGES[stage_index - 1]]["status"] != "passed":
                raise PluginContractError(
                    f"{record['capability_ref']}: {record['stage']} evidence is out of order"
                )
        item["stages"][internal_stage] = _stage(
            desired,
            checked_at=checked_at,
            evidence_id=evidence["evidence_id"],
            detail=record["detail"],
            correlation=evidence["correlation"],
        )
    if _parse_time(evidence["observed_at"]) >= _parse_time(merged["generated_at"]):
        merged["generated_at"] = evidence["observed_at"]
        merged["correlation"] = evidence["correlation"]
    refresh_capability_status(merged)
    validate_document(
        merged,
        contract_schema_path(platform_root, "shadow-capability-status.schema.json"),
        label="merged capability status",
    )
    return merged


def gate_failures(report: dict[str, Any], required_stage: str) -> list[str]:
    target = EVIDENCE_STAGES.get(required_stage, required_stage)
    if target not in STAGES:
        raise PluginContractError(f"unsupported conformance gate stage: {required_stage}")
    required_index = STAGES.index(target)
    failures: list[str] = []
    for capability in report["capabilities"]:
        if not capability["selected"]:
            continue
        if capability["maturity"] == "failed":
            failures.append(f"{capability['capability_ref']}:failed")
            continue
        for stage in STAGES[: required_index + 1]:
            if capability["stages"][stage]["status"] != "passed":
                failures.append(f"{capability['capability_ref']}:{stage}")
                break
    return failures


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _verify_artifacts(drill_path: Path, artifacts: Iterable[dict[str, str]]) -> None:
    root = drill_path.resolve().parent
    for artifact in artifacts:
        target = (root / artifact["path"]).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise PluginContractError(f"restore artifact is unavailable: {artifact['path']}")
        digest = sha256_file(target)
        if digest != artifact["sha256"]:
            raise PluginContractError(f"restore artifact hash mismatch: {artifact['path']}")


def restore_drill_to_evidence(
    drill_path: Path,
    capability_status: dict[str, Any],
    *,
    platform_root: Path,
) -> dict[str, Any]:
    drill = load_json_object(drill_path, label="restore drill")
    validate_document(
        drill,
        contract_schema_path(platform_root, "shadow-restore-drill.schema.json"),
        label="restore drill",
    )
    for key in ("deployment_id", "build_id"):
        if drill[key] != capability_status[key]:
            raise PluginContractError(f"restore drill targets a different {key}")
    if drill["restore"]["source_backup_id"] != drill["backup"]["backup_id"]:
        raise PluginContractError("restore source_backup_id does not match backup_id")
    if _parse_time(drill["restore"]["started_at"]) < _parse_time(
        drill["backup"]["created_at"]
    ):
        raise PluginContractError("restore started before the backup was created")
    if _parse_time(drill["restore"]["completed_at"]) < _parse_time(
        drill["restore"]["started_at"]
    ):
        raise PluginContractError("restore completed before it started")
    checks = drill["checks"]
    categories = {item["category"] for item in checks if item["status"] == "passed"}
    missing_categories = {"contract", "data", "health"} - categories
    if missing_categories:
        raise PluginContractError(
            f"restore drill is missing passed checks: {sorted(missing_categories)}"
        )
    failed_checks = [item["name"] for item in checks if item["status"] != "passed"]
    if failed_checks:
        raise PluginContractError(f"restore drill checks failed: {failed_checks}")
    known_refs = {item["capability_ref"] for item in capability_status["capabilities"]}
    unknown_refs = sorted(set(drill["capability_refs"]) - known_refs)
    if unknown_refs:
        raise PluginContractError(f"restore drill references unknown capabilities: {unknown_refs}")
    _verify_artifacts(drill_path, drill.get("artifacts", []))
    evidence = {
        "version": 1,
        "protocol": "shadow.conformance-evidence.v1",
        "evidence_id": drill["drill_id"],
        "producer": {
            "project_id": drill["project_id"],
            "component": "shadow-restore-verifier",
        },
        "deployment_id": drill["deployment_id"],
        "build_id": drill["build_id"],
        "observed_at": drill["restore"]["completed_at"],
        "correlation": drill["correlation"],
        "records": [
            {
                "capability_ref": capability_ref,
                "stage": "restore-tested",
                "status": "passed",
                "detail": (
                    f"isolated restore {drill['drill_id']} passed contract, data, and health checks"
                ),
                "checks": checks,
            }
            for capability_ref in drill["capability_refs"]
        ],
    }
    validate_document(
        evidence,
        contract_schema_path(platform_root, "shadow-conformance-evidence.schema.json"),
        label="restore conformance evidence",
    )
    return evidence
