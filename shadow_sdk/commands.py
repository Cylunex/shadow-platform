from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from shadow_sdk.plugin_contracts import (
    PluginContractError,
    contract_schema_path,
    validate_document,
)


def canonical_json(value: Any) -> bytes:
    """Canonical JSON shared by command hashes and cross-language fixtures."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PluginContractError("command value must be lossless JSON") from exc


def command_arguments_sha256(arguments: Any) -> str:
    return hashlib.sha256(canonical_json(arguments)).hexdigest()


def validate_command(document: Mapping[str, Any], *, platform_root: Path | None = None) -> None:
    root = platform_root or Path(__file__).resolve().parent
    validate_document(
        dict(document),
        contract_schema_path(root, "shadow-command.schema.json"),
        label="shadow command",
    )


def validate_execution_result(
    document: Mapping[str, Any], *, platform_root: Path | None = None
) -> None:
    root = platform_root or Path(__file__).resolve().parent
    validate_document(
        dict(document),
        contract_schema_path(root, "shadow-execution-result.schema.json"),
        label="shadow execution result",
    )


def committed_result(
    *,
    command: Mapping[str, Any],
    resource_ref: str,
    receipt_ref: str,
    completed_at: str,
    replayed: bool,
    resource_revision: int | str | None = None,
    summary: str | None = None,
    fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "protocol": "shadow.execution-result.v1",
        "command_id": command["command_id"],
        "capability_ref": command["capability_ref"],
        "status": "committed",
        "result_kind": "record",
        "resource_ref": resource_ref,
        "receipt_ref": receipt_ref,
        "completed_at": completed_at,
        "replayed": replayed,
    }
    if resource_revision is not None:
        result["resource_revision"] = resource_revision
    if summary is not None:
        result["summary"] = summary
    if fields is not None:
        result["fields"] = dict(fields)
    return result
