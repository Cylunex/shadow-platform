from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from scripts.build_dsh_bundle import build_bundle
from shadow_sdk.catalog import AppDescriptor, load_app_catalog
from shadow_sdk.plugin_contracts import (
    PluginContractError,
    ValidatedPlugin,
    contract_schema_path,
    load_document,
    validate_document,
    validate_plugin,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(_sha256(path)))
    return digest.hexdigest()


def _plugin_input_paths(plugin: ValidatedPlugin) -> list[Path]:
    paths = {plugin.root / "shadow-plugin.yaml", *plugin.descriptor_paths.values()}
    for capability in plugin.agent_manifest["capabilities"]:
        for tool in capability["tools"]:
            paths.add((plugin.root / tool["contract_ref"]).resolve())
    for skill in plugin.agent_manifest.get("skills", []):
        skill_path = (plugin.root / skill["path"]).resolve()
        root = skill_path if skill_path.is_dir() else skill_path.parent
        paths.update(path for path in root.rglob("*") if path.is_file())
    return sorted(path.resolve() for path in paths)


def _operation_catalog(plugin: ValidatedPlugin) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    documents: dict[Path, dict[str, Any]] = {}
    for capability in plugin.agent_manifest["capabilities"]:
        for tool in capability["tools"]:
            if tool["transport"] != "http":
                continue
            contract = (plugin.root / tool["contract_ref"]).resolve()
            document = documents.setdefault(contract, load_document(contract))
            operation_id = tool["operation_id"]
            matches = [
                {"operation_id": operation_id, "method": method.upper(), "path": path}
                for path, path_item in document.get("paths", {}).items()
                for method, operation in path_item.items()
                if method.lower() in {"get", "post", "put", "patch", "delete"}
                and isinstance(operation, dict)
                and operation.get("operationId") == operation_id
            ]
            if len(matches) != 1:
                raise PluginContractError(
                    f"{plugin.plugin_id}: operation {operation_id} must appear exactly once"
                )
            projected = {
                **matches[0],
                "capability_id": capability["id"],
                "tool_name": tool["name"],
                "effect": capability["effect"],
                "risk_level": capability["risk_level"],
                "confirmation_resource": capability.get("confirmation_resource"),
            }
            previous = catalog.setdefault(operation_id, projected)
            if previous != projected:
                raise PluginContractError(
                    f"{plugin.plugin_id}: operation id collision: {operation_id}"
                )
    return catalog


def _surface_document(plugin: ValidatedPlugin, platform_root: Path) -> dict[str, Any] | None:
    path = plugin.descriptor_paths.get("surfaces")
    if path is None:
        return None
    document = load_document(path)
    validate_document(
        document,
        contract_schema_path(platform_root, "shadow-surfaces.schema.json"),
        label=f"{plugin.plugin_id} runtime surfaces",
    )
    capability_ids = {item["id"] for item in plugin.agent_manifest["capabilities"]}
    operations = _operation_catalog(plugin)
    for surface in document["surfaces"]:
        capability = surface.get("capability")
        if capability is not None and capability not in capability_ids:
            raise PluginContractError(
                f"{plugin.plugin_id}: surface references unknown capability {capability}"
            )
        operation_id = surface.get("operation_id")
        if operation_id is not None and operation_id not in operations:
            raise PluginContractError(
                f"{plugin.plugin_id}: surface references unknown operation {operation_id}"
            )
    for operation_id in (document.get("review") or {}).values():
        if not isinstance(operation_id, str) or operation_id in {
            "shadow.review.v1",
            "commit",
            "create-only",
        }:
            continue
        if operation_id not in operations:
            raise PluginContractError(
                f"{plugin.plugin_id}: review references unknown operation {operation_id}"
            )
    return document


def _presentation(
    product_id: str,
    product: dict[str, Any],
    surfaces: dict[str, Any] | None,
) -> dict[str, Any]:
    declared = surfaces.get("presentation") if surfaces else None
    configured = product.get("presentation")
    value = declared or configured
    if value is None:
        raise PluginContractError(f"{product_id}: presentation is required")
    if declared is not None and declared["short_id"] != product["short_id"]:
        raise PluginContractError(f"{product_id}: short_id does not match surfaces")
    return {
        "short_id": product["short_id"],
        "title": value["title"],
        "caption": value["caption"],
        "icon": value["icon"],
        "color": value["color"],
        "order": value["order"],
    }


def _app_projection(
    *,
    product_id: str,
    product: dict[str, Any],
    descriptor: AppDescriptor,
    presentation: dict[str, Any],
) -> dict[str, Any]:
    if descriptor.canonical_url is None:
        raise PluginContractError(f"{product_id}: App channel requires canonical_url")
    return {
        "id": product["module_id"],
        "product_id": product_id,
        "plugin_id": product.get("plugin_id"),
        "name": presentation["title"],
        "description": presentation["caption"],
        "canonical_url": descriptor.canonical_url,
        "aliases": list(descriptor.aliases),
        "auth": {"mode": descriptor.auth.mode, "groups": list(descriptor.auth.groups)},
        "health_path": descriptor.health_path,
        "icon": presentation["icon"],
        "color": presentation["color"],
        "order": presentation["order"],
        "enabled": product["app"]["enabled"],
        "capabilities": product["app"]["capabilities"],
    }


def _nexus_projection(
    *,
    product_id: str,
    product: dict[str, Any],
    plugin: ValidatedPlugin,
    instance: dict[str, Any],
    surfaces: dict[str, Any],
    presentation: dict[str, Any],
    app_descriptor: AppDescriptor | None,
) -> dict[str, Any]:
    operations = _operation_catalog(plugin)
    projected_surfaces: list[dict[str, Any]] = []
    for surface in surfaces["surfaces"]:
        projected = dict(surface)
        operation_id = projected.get("operation_id")
        if operation_id is not None:
            projected["operation"] = operations[operation_id]
        projected_surfaces.append(projected)
    review = surfaces.get("review")
    has_app_link = any(surface["type"] == "app-link" for surface in surfaces["surfaces"])
    if has_app_link and "app" not in product["channels"]:
        raise PluginContractError(f"{product_id}: app-link surface requires the App channel")
    if has_app_link and (app_descriptor is None or app_descriptor.canonical_url is None):
        raise PluginContractError(f"{product_id}: app-link surface requires an App Catalog URL")
    projected_review = None
    if review is not None:
        projected_review = {
            **review,
            "operations": {
                key.removesuffix("_operation_id"): operations[value]
                for key, value in review.items()
                if key.endswith("_operation_id")
            },
        }
    return {
        "id": product["short_id"],
        "product_id": product_id,
        "plugin_id": plugin.plugin_id,
        "plugin_version": plugin.version,
        "instance_id": product["instance_id"],
        "presentation": presentation,
        "connection": {
            "base_url_env": instance.get("base_url_env"),
            "credential_env": instance.get("credential_env"),
            "health_path": instance.get("health_path"),
            "context_env": instance.get("context_env", {}),
        },
        "surfaces": projected_surfaces,
        "review": projected_review,
        "app_id": product.get("app_id"),
        "app": None
        if not has_app_link
        else {
            "canonical_url": app_descriptor.canonical_url,
            "aliases": list(app_descriptor.aliases),
        },
    }


def build_shadow_profile(
    *,
    platform_root: Path,
    deployment_path: Path,
    catalog_path: Path,
    profile_path: Path,
    instances_path: Path,
    plugin_roots: list[Path],
    output_dir: Path,
) -> Path:
    deployment = load_document(deployment_path)
    profile = load_document(profile_path)
    instances_document = load_document(instances_path)
    validate_document(
        deployment,
        contract_schema_path(platform_root, "shadow-deployment.schema.json"),
        label="deployment",
    )
    validate_document(
        profile,
        contract_schema_path(platform_root, "agent-profile.schema.json"),
        label="profile",
    )
    validate_document(
        instances_document,
        contract_schema_path(platform_root, "shadow-plugin-instance.schema.json"),
        label="plugin instances",
    )
    catalog = load_app_catalog(catalog_path)
    plugins_list = [validate_plugin(root, platform_root) for root in plugin_roots]
    plugins = {plugin.plugin_id: plugin for plugin in plugins_list}
    if len(plugins) != len(plugins_list):
        raise PluginContractError("duplicate plugin ids in profile inputs")

    named_inputs: dict[str, Path] = {
        "deployment": deployment_path,
        "catalog": catalog_path,
        "profile": profile_path,
        "instances": instances_path,
    }
    for plugin in plugins_list:
        for path in _plugin_input_paths(plugin):
            relative = path.relative_to(plugin.root.resolve()).as_posix()
            named_inputs[f"plugins/{plugin.plugin_id}/{relative}"] = path
    for name in (
        "shadow-deployment.schema.json",
        "shadow-surfaces.schema.json",
        "shadow-review.schema.json",
        "shadow-context-pack.schema.json",
        "shadow-capture-envelope.schema.json",
        "shadow-suggestion.schema.json",
        "shadow-plugin.schema.json",
        "shadow-plugin-instance.schema.json",
        "agent-profile.schema.json",
    ):
        named_inputs[f"compiler/contracts/{name}"] = contract_schema_path(platform_root, name)
    named_inputs["compiler/scripts/build_shadow_profile.py"] = Path(__file__).resolve()
    named_inputs["compiler/scripts/build_dsh_bundle.py"] = (
        platform_root / "scripts" / "build_dsh_bundle.py"
    )
    named_inputs["compiler/shadow_sdk/catalog.py"] = platform_root / "shadow_sdk" / "catalog.py"
    named_inputs["compiler/shadow_sdk/plugin_contracts.py"] = (
        platform_root / "shadow_sdk" / "plugin_contracts.py"
    )
    inputs = [
        {"name": name, "sha256": _sha256(path.resolve())}
        for name, path in sorted(named_inputs.items())
    ]
    build_id = hashlib.sha256(_json_bytes(inputs)).hexdigest()

    nexus_domains: list[dict[str, Any]] = []
    app_modules: list[dict[str, Any]] = []
    identities: list[dict[str, Any]] = []
    selected_profile_plugins = {item["plugin_id"] for item in profile["plugins"]}
    for product_id, product in deployment["products"].items():
        if product_id != f"shadow-{product['short_id']}":
            raise PluginContractError(f"{product_id}: product id and short_id do not match")
        channels = set(product["channels"])
        plugin = plugins.get(product.get("plugin_id"))
        surfaces = _surface_document(plugin, platform_root) if plugin is not None else None
        presentation = _presentation(product_id, product, surfaces)
        app_descriptor = catalog.get(product.get("app_id"))
        instance = None
        if channels & {"dsh", "nexus"}:
            if plugin is None:
                raise PluginContractError(f"{product_id}: plugin input is missing")
            instance = instances_document["instances"].get(product["instance_id"])
            if instance is None:
                raise PluginContractError(f"{product_id}: instance is missing")
            if not instance["enabled"]:
                raise PluginContractError(f"{product_id}: instance is disabled")
            if (
                instance["plugin_id"] != plugin.plugin_id
                or instance["plugin_version"] != plugin.version
            ):
                raise PluginContractError(f"{product_id}: instance does not match plugin version")
        if "dsh" in channels and plugin.plugin_id not in selected_profile_plugins:
            raise PluginContractError(
                f"{product_id}: DSH channel is absent from the selected profile"
            )
        if "nexus" in channels:
            if surfaces is None:
                raise PluginContractError(f"{product_id}: Nexus channel requires surfaces")
            nexus_domains.append(
                _nexus_projection(
                    product_id=product_id,
                    product=product,
                    plugin=plugin,
                    instance=instance,
                    surfaces=surfaces,
                    presentation=presentation,
                    app_descriptor=app_descriptor,
                )
            )
        if "app" in channels:
            descriptor = app_descriptor
            if descriptor is None:
                raise PluginContractError(f"{product_id}: App Catalog entry is missing")
            app_modules.append(
                _app_projection(
                    product_id=product_id,
                    product=product,
                    descriptor=descriptor,
                    presentation=presentation,
                )
            )
        identities.append(
            {
                "product_id": product_id,
                "short_id": product["short_id"],
                "plugin_id": product.get("plugin_id"),
                "instance_id": product.get("instance_id"),
                "app_id": product.get("app_id"),
                "module_id": product.get("module_id"),
                "legacy_aliases": product.get("legacy_aliases", []),
                "channels": product["channels"],
            }
        )

    release_root = output_dir.resolve() / deployment["id"]
    target = release_root / build_id
    if target.exists():
        lock_path = target / "shadow-deployment.lock"
        if lock_path.is_file():
            existing = json.loads(lock_path.read_text("utf-8"))
            if existing.get("build_id") == build_id:
                return target
        raise PluginContractError(f"release target already exists and is invalid: {target}")
    release_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{build_id}.", dir=release_root))
    dsh_target = build_bundle(
        platform_root=platform_root,
        profile_path=profile_path,
        instances_path=instances_path,
        plugin_roots=plugin_roots,
        output_dir=staging / "dsh",
    )
    dsh_runtime = json.loads((dsh_target / "shadow-runtime-manifest.json").read_text("utf-8"))
    _write_json(staging / "shadow-dsh-runtime.json", dsh_runtime)
    _write_json(
        staging / "shadow-nexus-runtime.json",
        {
            "version": 1,
            "protocol": "shadow.nexus.runtime.v1",
            "deployment_id": deployment["id"],
            "build_id": build_id,
            "domains": sorted(nexus_domains, key=lambda item: item["presentation"]["order"]),
        },
    )
    _write_json(
        staging / "shadow-app-runtime.json",
        {
            "schemaVersion": 4,
            "platform": {
                "catalogVersion": 1,
                "deploymentId": deployment["id"],
                "buildId": build_id,
                "identityIssuer": deployment["identity_issuer"],
            },
            "modules": sorted(app_modules, key=lambda item: item["order"]),
        },
    )
    report_products = [
        {
            **identity,
            "status": "enabled",
            "projections": {
                "dsh": "dsh" in identity["channels"],
                "nexus": "nexus" in identity["channels"],
                "app": "app" in identity["channels"],
            },
            "reason": "selected by deployment",
        }
        for identity in identities
    ]
    _write_json(
        staging / "shadow-deployment-report.json",
        {
            "version": 1,
            "protocol": "shadow.deployment-report.v1",
            "deployment_id": deployment["id"],
            "build_id": build_id,
            "profile_id": profile["id"],
            "summary": {
                "products": len(report_products),
                "dsh_plugins": sum("dsh" in item["channels"] for item in identities),
                "nexus_domains": len(nexus_domains),
                "app_modules": len(app_modules),
                "degraded": 0,
                "filtered": 0,
                "incompatible": 0,
            },
            "products": report_products,
            "warnings": [],
            "notes": [
                "Secrets are resolved only by runtime environment variables.",
                "Build compatibility does not replace live deployment checks.",
            ],
        },
    )
    lock = {
        "version": 1,
        "deployment_id": deployment["id"],
        "build_id": build_id,
        "profile_id": profile["id"],
        "dsh_bundle": {
            "path": str(dsh_target.relative_to(staging)),
            "build_id": dsh_runtime["build_id"],
            "tree_sha256": _tree_sha256(dsh_target),
        },
        "products": identities,
        "inputs": inputs,
        "outputs": [
            {
                "path": name,
                "sha256": _sha256(staging / name),
            }
            for name in (
                "shadow-dsh-runtime.json",
                "shadow-nexus-runtime.json",
                "shadow-app-runtime.json",
                "shadow-deployment-report.json",
            )
        ],
    }
    _write_json(staging / "shadow-deployment.lock", lock)
    try:
        os.replace(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile one Shadow deployment for all runtimes")
    parser.add_argument("--platform-root", type=Path, default=Path(__file__).parents[1])
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--instances", type=Path, required=True)
    parser.add_argument("--plugin-root", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        target = build_shadow_profile(
            platform_root=args.platform_root.resolve(),
            deployment_path=args.deployment.resolve(),
            catalog_path=args.catalog.resolve(),
            profile_path=args.profile.resolve(),
            instances_path=args.instances.resolve(),
            plugin_roots=[path.resolve() for path in args.plugin_root],
            output_dir=args.output_dir.resolve(),
        )
    except PluginContractError as exc:
        parser.error(str(exc))
    print(target)


if __name__ == "__main__":
    main()
