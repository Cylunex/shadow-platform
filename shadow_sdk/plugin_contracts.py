from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import total_ordering
from pathlib import Path
from typing import Any

import jsonschema
import yaml


class PluginContractError(ValueError):
    """Raised when a Shadow plugin definition or referenced contract is invalid."""


_SEMVER = re.compile(
    r"^(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?$"
)
_COMPARATOR = re.compile(r"^(?P<operator>>=|<=|>|<|=)?(?P<version>.+)$")


@total_ordering
@dataclass(frozen=True, slots=True)
class _SemVer:
    major: int
    minor: int
    patch: int
    prerelease: tuple[int | str, ...]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, _SemVer):
            return NotImplemented
        core = (self.major, self.minor, self.patch)
        other_core = (other.major, other.minor, other.patch)
        if core != other_core:
            return core < other_core
        if not self.prerelease:
            return False if not other.prerelease else False
        if not other.prerelease:
            return True
        for left, right in zip(self.prerelease, other.prerelease, strict=False):
            if left == right:
                continue
            if isinstance(left, int) and isinstance(right, str):
                return True
            if isinstance(left, str) and isinstance(right, int):
                return False
            return left < right
        return len(self.prerelease) < len(other.prerelease)


def _parse_semver(value: str) -> _SemVer:
    match = _SEMVER.fullmatch(value)
    if match is None:
        raise PluginContractError(f"unsupported semantic version: {value}")
    prerelease = tuple(
        int(part) if part.isdigit() else part
        for part in (match.group("prerelease") or "").split(".")
        if part
    )
    return _SemVer(
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        prerelease,
    )


def semver_satisfies(version: str, requirement: str) -> bool:
    """Evaluate the deliberately small comparator-set grammar used by Shadow contracts."""

    candidate = _parse_semver(version)
    comparators = requirement.split()
    if not comparators:
        raise PluginContractError("semantic version requirement cannot be empty")
    for raw in comparators:
        match = _COMPARATOR.fullmatch(raw)
        if match is None:
            raise PluginContractError(f"unsupported semantic version requirement: {requirement}")
        operator = match.group("operator") or "="
        expected = _parse_semver(match.group("version"))
        accepted = {
            "=": candidate == expected,
            ">": candidate > expected,
            ">=": candidate >= expected,
            "<": candidate < expected,
            "<=": candidate <= expected,
        }[operator]
        if not accepted:
            return False
    return True


@dataclass(frozen=True, slots=True)
class ValidatedPlugin:
    root: Path
    definition: dict[str, Any]
    agent_manifest: dict[str, Any]
    descriptor_paths: dict[str, Path]

    @property
    def plugin_id(self) -> str:
        return str(self.definition["metadata"]["id"])

    @property
    def version(self) -> str:
        return str(self.definition["metadata"]["version"])


def load_document(path: Path) -> dict[str, Any]:
    try:
        if path.suffix == ".json":
            value = json.loads(path.read_text(encoding="utf-8"))
        else:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise PluginContractError(f"cannot load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PluginContractError(f"{path} must contain an object")
    return value


def validate_document(document: dict[str, Any], schema_path: Path, *, label: str) -> None:
    schema = load_document(schema_path)
    try:
        jsonschema.Draft202012Validator(
            schema, format_checker=jsonschema.FormatChecker()
        ).validate(document)
    except jsonschema.ValidationError as exc:
        location = ".".join(str(item) for item in exc.absolute_path) or "<root>"
        raise PluginContractError(f"{label} invalid at {location}: {exc.message}") from exc


def contract_schema_path(platform_root: Path, name: str) -> Path:
    """Resolve a contract from a source checkout or the installed SDK wheel."""

    source_path = platform_root.resolve() / "contracts" / name
    if source_path.is_file():
        return source_path
    packaged_path = Path(__file__).resolve().parent / "contracts" / name
    if packaged_path.is_file():
        return packaged_path
    raise PluginContractError(f"Shadow contract schema is unavailable: {name}")


def resolve_inside(root: Path, relative: str, *, label: str) -> Path:
    resolved_root = root.resolve()
    resolved = (resolved_root / relative).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise PluginContractError(f"{label} escapes plugin root: {relative}")
    if not resolved.is_file():
        raise PluginContractError(f"{label} does not exist: {relative}")
    return resolved


def validate_capability_semantics(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    capabilities = manifest.get("capabilities", [])
    capability_ids = [item.get("id") for item in capabilities]
    if len(capability_ids) != len(set(capability_ids)):
        errors.append("duplicate capability ids")
    known_capabilities = set(capability_ids)
    skill_ids = [item.get("id") for item in manifest.get("skills", [])]
    if len(skill_ids) != len(set(skill_ids)):
        errors.append("duplicate skill ids")
    for skill in manifest.get("skills", []):
        unknown = sorted(set(skill.get("capabilities", [])) - known_capabilities)
        if unknown:
            errors.append(f"{skill.get('id')}:unknown={unknown}")

    confirmation_by_risk = {
        "L0": {"none"},
        "L1": {"notify"},
        "L2": {"policy"},
        "L3": {"explicit"},
        "L4": {"elevated"},
    }
    mutation_effects = {"draft", "write", "delete", "publish", "execute", "permission"}
    high_impact_effects = {"delete", "publish", "export", "permission"}
    tool_names: list[str] = []
    for capability in capabilities:
        capability_id = capability.get("id")
        effect = capability.get("effect")
        risk_level = capability.get("risk_level")
        confirmation = capability.get("confirmation")
        idempotent = capability.get("idempotency_required")
        reversible = capability.get("reversible")
        tools = capability.get("tools", [])
        tool_names.extend(tool.get("name") for tool in tools)
        if confirmation not in confirmation_by_risk.get(risk_level, set()):
            errors.append(f"{capability_id}:confirmation-does-not-match-{risk_level}")
        if effect in mutation_effects and idempotent is not True:
            errors.append(f"{capability_id}:mutation-without-idempotency")
        if risk_level == "L1" and reversible is not True:
            errors.append(f"{capability_id}:l1-must-be-reversible")
        if effect in {"read", "analyze"} and risk_level not in {"L0", "L2"}:
            errors.append(f"{capability_id}:read-analysis-risk-invalid")
        if effect in high_impact_effects and risk_level not in {"L3", "L4"}:
            errors.append(f"{capability_id}:high-impact-risk-too-low")
        if effect == "execute" and risk_level not in {"L3", "L4"}:
            errors.append(f"{capability_id}:execute-risk-too-low")
        if effect == "delete":
            limits = capability.get("destructive_limits")
            if not isinstance(limits, dict) or limits.get("min_remaining", 0) < 1:
                errors.append(f"{capability_id}:delete-must-preserve-at-least-one-item")
        elif "destructive_limits" in capability:
            errors.append(f"{capability_id}:destructive-limits-only-valid-for-delete")
        resource = capability.get("confirmation_resource")
        if resource is not None:
            placeholders = set(re.findall(r"\{([A-Za-z][A-Za-z0-9_]*)\}", resource["template"]))
            declared = set(resource["arguments"])
            if placeholders != declared:
                errors.append(f"{capability_id}:confirmation-resource-arguments-mismatch")
        for tool in tools:
            if tool.get("retry_policy") == "idempotent" and not (
                effect in {"read", "analyze"} or idempotent is True
            ):
                errors.append(f"{tool.get('name')}:unsafe-idempotent-retry")
            if (
                tool.get("transport") == "mcp"
                and risk_level in {"L3", "L4"}
                and not tool.get("confirmation_argument")
            ):
                errors.append(f"{tool.get('name')}:confirmed-mcp-tool-needs-receipt-argument")
    if len(tool_names) != len(set(tool_names)):
        errors.append("duplicate tool names")
    return errors


def validate_plugin(plugin_root: Path, platform_root: Path) -> ValidatedPlugin:
    root = plugin_root.resolve()
    definition_path = root / "shadow-plugin.yaml"
    definition = load_document(definition_path)
    validate_document(
        definition,
        contract_schema_path(platform_root, "shadow-plugin.schema.json"),
        label="shadow-plugin.yaml",
    )

    descriptor_paths: dict[str, Path] = {}
    for name, relative in definition["spec"]["descriptors"].items():
        descriptor_paths[name] = resolve_inside(root, relative, label=f"descriptor {name}")

    manifest = load_document(descriptor_paths["agent"])
    validate_document(
        manifest,
        contract_schema_path(platform_root, "agent-capability-manifest.schema.json"),
        label="agent manifest",
    )
    semantic_errors = validate_capability_semantics(manifest)
    if semantic_errors:
        raise PluginContractError("agent manifest semantic errors: " + ", ".join(semantic_errors))

    if definition["metadata"]["version"] != manifest["package_version"]:
        raise PluginContractError("plugin version must equal agent package_version")
    if definition["kind"] == "ShadowDomainPlugin":
        expected = f"shadow-{manifest['app_id']}"
        if definition["metadata"]["id"] != expected:
            raise PluginContractError(f"domain plugin id must be {expected}")

    transports = {
        tool["transport"]
        for capability in manifest["capabilities"]
        for tool in capability["tools"]
    }
    if definition["kind"] == "ShadowCompositionPlugin" and transports != {"composition"}:
        raise PluginContractError("composition plugins may contain only composition tools")
    if definition["kind"] == "ShadowDomainPlugin" and "composition" in transports:
        raise PluginContractError("domain plugins cannot contain composition tools")

    agent_dir = descriptor_paths["agent"].parent
    for skill in manifest["skills"]:
        resolve_inside(agent_dir, skill["path"], label=f"skill {skill['id']}")
    for capability in manifest["capabilities"]:
        for tool in capability["tools"]:
            contract_path = resolve_inside(
                root, tool["contract_ref"], label=f"tool {tool['name']} contract"
            )
            if tool["transport"] == "mcp":
                catalog = load_document(contract_path)
                validate_document(
                    catalog,
                    contract_schema_path(platform_root, "mcp-tool-catalog.schema.json"),
                    label=f"MCP catalog for {tool['name']}",
                )
                matching = [
                    item for item in catalog["tools"] if item["name"] == tool["operation_id"]
                ]
                if len(matching) != 1:
                    raise PluginContractError(
                        f"MCP tool not found exactly once: {tool['operation_id']}"
                    )
                confirmation_argument = tool.get("confirmation_argument")
                if confirmation_argument is not None:
                    properties = matching[0]["inputSchema"].get("properties", {})
                    if confirmation_argument not in properties:
                        raise PluginContractError(
                            f"{tool['name']}: MCP confirmation argument is absent from inputSchema"
                        )
            elif tool["transport"] == "composition":
                workflow = load_document(contract_path)
                validate_document(
                    workflow,
                    contract_schema_path(platform_root, "composition-workflow.schema.json"),
                    label=f"composition workflow for {tool['name']}",
                )
                matching = [
                    item
                    for item in workflow["workflows"]
                    if item["operation_id"] == tool["operation_id"]
                ]
                if len(matching) != 1:
                    raise PluginContractError(
                        f"composition workflow not found exactly once: {tool['operation_id']}"
                    )
    return ValidatedPlugin(root, definition, manifest, descriptor_paths)


def dsh_tool_name(tool_name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", tool_name.lower()).strip("_")
    return f"shadow_{normalized}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
