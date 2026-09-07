from pathlib import Path

import pytest

from shadow_sdk.commands import (
    canonical_json,
    command_arguments_sha256,
    committed_result,
    validate_command,
    validate_execution_result,
)
from shadow_sdk.plugin_contracts import PluginContractError

ROOT = Path(__file__).resolve().parents[1]
CAPABILITY = "shadow://capabilities/shadow-health/health-primary/health.records.write"


def command(**updates):
    value = {
        "protocol": "shadow.command.v1",
        "command_id": "cmd_example123",
        "capability_ref": CAPABILITY,
        "operation_id": "execute_health_command",
        "schema_version": 1,
        "arguments": {"recordType": "metric", "weightKg": "70.2"},
        "target_refs": [],
        "source_refs": ["turn:user-1"],
    }
    value.update(updates)
    return value


def test_command_and_committed_result_contracts():
    value = command()
    validate_command(value, platform_root=ROOT)
    result = committed_result(
        command=value,
        resource_ref="shadow://health/metrics/2026-09-07",
        receipt_ref="shadow://health/operations/cmd_example123",
        completed_at="2026-09-07T00:00:00Z",
        replayed=False,
        fields={"weight_kg": "70.2"},
    )
    validate_execution_result(result, platform_root=ROOT)
    validate_execution_result(result)
    assert result["operation_id"] == value["operation_id"]


def test_committed_result_requires_receipt_and_resource():
    with pytest.raises(PluginContractError):
        validate_execution_result(
            {
                "protocol": "shadow.execution-result.v1",
                "command_id": "cmd_example123",
                "capability_ref": CAPABILITY,
                "operation_id": "execute_health_command",
                "status": "committed",
                "result_kind": "record",
                "replayed": False,
            },
            platform_root=ROOT,
        )


def test_canonical_command_hash_is_stable_and_rejects_nan():
    left = {"中文": [1, None], "a": {"z": "值"}}
    right = {"a": {"z": "值"}, "中文": [1, None]}
    assert canonical_json(left) == canonical_json(right)
    assert command_arguments_sha256(left) == command_arguments_sha256(right)
    with pytest.raises(PluginContractError):
        canonical_json({"bad": float("nan")})
