from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from shadow_sdk.catalog import CatalogError, load_app_catalog


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

    oidc_path = root / "auth" / ("configuration.yml" if strict else "oidc-clients.yml.example")
    oidc_error: str | None = None
    try:
        oidc = yaml.safe_load(oidc_path.read_text(encoding="utf-8"))
        configured_clients = {
            item.get("client_id", "").removeprefix("shadow-")
            for item in oidc.get("identity_providers", {}).get("oidc", {}).get("clients", [])
        }
    except (OSError, AttributeError, TypeError, yaml.YAMLError) as exc:
        configured_clients = set()
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
