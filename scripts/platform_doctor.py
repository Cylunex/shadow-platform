from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from shadow_sdk.catalog import CatalogError, load_app_catalog
from shadow_sdk.plugin_contracts import (
    PluginContractError,
    validate_capability_semantics,
    validate_plugin,
)


@dataclass(frozen=True, slots=True)
class CheckResult:
    check: str
    status: str
    detail: str


def inspect_platform(root: Path, *, strict: bool = False) -> list[CheckResult]:
    results: list[CheckResult] = []
    catalog_path = root / "catalog" / "apps.yml"
    try:
        catalog = load_app_catalog(catalog_path)
        results.append(CheckResult("catalog", "pass", f"{len(catalog)} apps loaded"))
    except (OSError, yaml.YAMLError, CatalogError) as exc:
        return [CheckResult("catalog", "fail", str(exc))]

    llm_registry_path = root / "llm" / ("registry.yml" if strict else "registry.yml.example")
    agent_registry_path = root / "agents" / ("registry.yml" if strict else "registry.yml.example")
    documents = (
        ("app catalog schema", catalog_path, root / "contracts" / "app-catalog.schema.json"),
        (
            "LLM registry schema",
            llm_registry_path,
            root / "contracts" / "llm-registry.schema.json",
        ),
        (
            "Agent registry schema",
            agent_registry_path,
            root / "contracts" / "agent-registry.schema.json",
        ),
        (
            "Agent capability manifest schema",
            root / "agents" / "capability-manifest.yml.example",
            root / "contracts" / "agent-capability-manifest.schema.json",
        ),
        (
            "Shadow plugin instance schema",
            root / "agents" / "plugin-instances.yml.example",
            root / "contracts" / "shadow-plugin-instance.schema.json",
        ),
        (
            "Agent profile schema",
            root / "agents" / "profiles" / "shadow-general.yml.example",
            root / "contracts" / "agent-profile.schema.json",
        ),
        (
            "Composition profile schema",
            root / "agents" / "profiles" / "shadow-daily-overview.yml.example",
            root / "contracts" / "agent-profile.schema.json",
        ),
        (
            "Nexus profile schema",
            root / "agents" / "profiles" / "shadow-nexus.yml.example",
            root / "contracts" / "agent-profile.schema.json",
        ),
    )
    loaded: dict[str, dict[str, Any]] = {}
    for name, document_path, schema_path in documents:
        try:
            document = yaml.safe_load(document_path.read_text(encoding="utf-8"))
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            jsonschema.Draft202012Validator(
                schema, format_checker=jsonschema.FormatChecker()
            ).validate(document)
            loaded[name] = document
            results.append(CheckResult(name, "pass", document_path.as_posix()))
        except (OSError, ValueError, yaml.YAMLError, jsonschema.ValidationError) as exc:
            results.append(CheckResult(name, "fail", str(exc)))

    llm = loaded.get("LLM registry schema", {})
    models = set(llm.get("models", {}))
    missing_models = sorted(
        {alias for app in catalog.values() for alias in app.llm_models if alias not in models}
    )
    results.append(
        CheckResult(
            "catalog LLM references",
            "fail" if missing_models else "pass",
            f"unknown aliases: {missing_models}" if missing_models else "all aliases exist",
        )
    )

    try:
        validate_plugin(root / "compositions" / "shadow-daily-overview", root)
        composition_error = None
    except PluginContractError as exc:
        composition_error = str(exc)
    results.append(
        CheckResult(
            "Shadow composition plugin",
            "fail" if composition_error else "pass",
            composition_error or "daily overview workflow and skill valid",
        )
    )

    agents = loaded.get("Agent registry schema", {}).get("agents", {})
    bad_agent_refs: list[str] = []
    for agent_id, agent in agents.items():
        owner = agent.get("owner_app")
        if owner not in catalog:
            bad_agent_refs.append(f"{agent_id}:owner={owner}")
        for audience in agent.get("audiences", []):
            app = catalog.get(audience)
            if not app or not app.agent_audience:
                bad_agent_refs.append(f"{agent_id}:audience={audience}")
    results.append(
        CheckResult(
            "catalog Agent references",
            "fail" if bad_agent_refs else "pass",
            ", ".join(bad_agent_refs) if bad_agent_refs else "all owners and audiences exist",
        )
    )

    capability_manifest = loaded.get("Agent capability manifest schema", {})
    capability_errors = _validate_capability_manifest(capability_manifest, catalog, agents)
    results.append(
        CheckResult(
            "Agent capability contracts",
            "fail" if capability_errors else "pass",
            ", ".join(capability_errors) if capability_errors else "skills and capabilities valid",
        )
    )

    try:
        validate_plugin(root / "fixtures" / "conformance-plugin", root)
        conformance_error = None
    except PluginContractError as exc:
        conformance_error = str(exc)
    results.append(
        CheckResult(
            "Shadow plugin conformance fixture",
            "fail" if conformance_error else "pass",
            conformance_error or "definition, descriptors, skills and tools valid",
        )
    )

    oidc_path = root / "auth" / ("configuration.yml" if strict else "oidc-clients.yml.example")
    oidc_error: str | None = None
    try:
        oidc = yaml.safe_load(oidc_path.read_text(encoding="utf-8"))
        client_items = oidc.get("identity_providers", {}).get("oidc", {}).get("clients", [])
        configured_clients = {
            item.get("client_id", "").removeprefix("shadow-") for item in client_items
        }
        clients_by_app = {
            item.get("client_id", "").removeprefix("shadow-"): item for item in client_items
        }
    except (OSError, AttributeError, TypeError, yaml.YAMLError) as exc:
        configured_clients = set()
        clients_by_app = {}
        oidc_error = str(exc)
    required_clients = {app.app_id for app in catalog.values() if app.auth.mode == "oidc"}
    missing_clients = sorted(required_clients - configured_clients)
    status = (
        "fail"
        if oidc_error or (strict and missing_clients)
        else "warn"
        if missing_clients
        else "pass"
    )
    results.append(
        CheckResult(
            "OIDC clients",
            status,
            (
                f"cannot read {oidc_path.as_posix()}: {oidc_error}"
                if oidc_error
                else f"missing clients: {missing_clients}"
                if missing_clients
                else "all OIDC apps configured"
            ),
        )
    )

    oidc_contract_errors: list[str] = []
    required_scopes = {"openid", "profile", "email", "groups"}
    for app in catalog.values():
        if app.auth.mode != "oidc" or not app.canonical_url:
            continue
        client = clients_by_app.get(app.app_id)
        if not client:
            continue
        callback = app.canonical_url.rstrip("/") + "/auth/callback"
        if callback not in set(client.get("redirect_uris") or []):
            oidc_contract_errors.append(f"{app.app_id}:callback")
        if app.canonical_url not in set(client.get("post_logout_redirect_uris") or []):
            oidc_contract_errors.append(f"{app.app_id}:post-logout")
        if client.get("require_pkce") is not True or client.get("pkce_challenge_method") != "S256":
            oidc_contract_errors.append(f"{app.app_id}:pkce")
        if not required_scopes.issubset(set(client.get("scopes") or [])):
            oidc_contract_errors.append(f"{app.app_id}:scopes")
        if client.get("grant_types") != ["authorization_code"]:
            oidc_contract_errors.append(f"{app.app_id}:grant")
        if client.get("response_types") != ["code"]:
            oidc_contract_errors.append(f"{app.app_id}:response")
    results.append(
        CheckResult(
            "OIDC client contracts",
            "fail" if oidc_contract_errors else "pass",
            ", ".join(oidc_contract_errors) if oidc_contract_errors else "callbacks and PKCE valid",
        )
    )

    auth_mode_errors: list[str] = []
    for app in catalog.values():
        if app.auth.mode == "oidc":
            if app.kind != "web":
                auth_mode_errors.append(f"{app.app_id}:oidc-kind={app.kind}")
            if not app.auth.groups:
                auth_mode_errors.append(f"{app.app_id}:oidc-without-groups")
        if app.auth.mode == "forward-auth":
            if app.kind != "web":
                auth_mode_errors.append(f"{app.app_id}:forward-auth-kind={app.kind}")
            if not app.auth.groups:
                auth_mode_errors.append(f"{app.app_id}:forward-auth-without-groups")
            if app.agent_audience:
                auth_mode_errors.append(f"{app.app_id}:forward-auth-with-agent-audience")
        if app.auth.mode == "service-bearer":
            if app.kind != "service":
                auth_mode_errors.append(f"{app.app_id}:service-bearer-kind={app.kind}")
            if app.auth.groups:
                auth_mode_errors.append(f"{app.app_id}:service-bearer-has-user-groups")
            if not app.agent_audience:
                auth_mode_errors.append(f"{app.app_id}:service-bearer-without-audience")
    results.append(
        CheckResult(
            "catalog auth boundaries",
            "fail" if auth_mode_errors else "pass",
            (
                ", ".join(auth_mode_errors)
                if auth_mode_errors
                else "OIDC, Forward Auth and service Bearer roles valid"
            ),
        )
    )

    production_without_health = sorted(
        app.app_id
        for app in catalog.values()
        if app.lifecycle == "production" and app.kind in {"web", "service"} and not app.health_path
    )
    results.append(
        CheckResult(
            "production health paths",
            "warn" if production_without_health else "pass",
            (
                f"missing health_path: {production_without_health}"
                if production_without_health
                else "all production services have health paths"
            ),
        )
    )

    if strict:
        placeholders = _find_placeholders(root)
        results.append(
            CheckResult(
                "production placeholders",
                "fail" if placeholders else "pass",
                ", ".join(placeholders) if placeholders else "no replacement markers found",
            )
        )
    return results


def _validate_capability_manifest(
    manifest: dict[str, Any], catalog: dict[str, Any], agents: dict[str, Any]
) -> list[str]:
    if not manifest:
        return []
    errors = validate_capability_semantics(manifest)
    capability_items = manifest.get("capabilities", [])
    manifest_app_id = manifest.get("app_id")
    if manifest_app_id not in catalog:
        errors.append(f"unknown app_id={manifest_app_id}")
    for capability in capability_items:
        capability_id = capability.get("id")
        audience = capability.get("audience")
        audience_app = catalog.get(audience)
        if not audience_app or not audience_app.agent_audience:
            errors.append(f"{capability_id}:audience={audience}")
        required = set(capability.get("scopes", []))
        covered = any(
            audience in agent.get("audiences", [])
            and required.issubset(set(agent.get("scopes", [])))
            for agent in agents.values()
        )
        if agents and not covered:
            errors.append(f"{capability_id}:no-principal-covers-scopes")
    return errors


def _find_placeholders(root: Path) -> list[str]:
    paths = [
        root / "auth" / "configuration.yml",
        root / "auth" / "users_database.yml",
        root / "llm" / "registry.yml",
        root / "agents" / "registry.yml",
    ]
    found = []
    for path in paths:
        if not path.exists():
            found.append(f"missing:{path.relative_to(root).as_posix()}")
            continue
        if "REPLACE_WITH" in path.read_text(encoding="utf-8"):
            found.append(f"placeholder:{path.relative_to(root).as_posix()}")
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Shadow Platform configuration")
    parser.add_argument("--root", type=Path, default=Path(__file__).parents[1])
    parser.add_argument("--strict", action="store_true", help="validate deploy-time files")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    results = inspect_platform(args.root.resolve(), strict=args.strict)
    if args.as_json:
        print(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2))
    else:
        for result in results:
            print(f"[{result.status.upper():4}] {result.check}: {result.detail}")
    if any(result.status == "fail" for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
