from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from scripts.activate_shadow_profile import verify_release
from shadow_sdk.plugin_contracts import PluginContractError


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PluginContractError(f"cannot read deployment projection: {path.name}") from exc
    if not isinstance(value, dict):
        raise PluginContractError(f"deployment projection must be an object: {path.name}")
    return value


def inspect_deployment(
    release_dir: Path,
    *,
    environment: Mapping[str, str] | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Verify one immutable release and probe every configured domain without exposing secrets."""

    release = release_dir.resolve()
    lock = verify_release(release)
    runtime = _read_json(release / "shadow-nexus-runtime.json")
    values = os.environ if environment is None else environment
    owns_client = client is None
    http = client or httpx.Client(timeout=5.0, follow_redirects=False)
    domains: list[dict[str, Any]] = []
    try:
        for domain in runtime.get("domains", []):
            domain_id = str(domain.get("id", "unknown"))
            connection = domain.get("connection") or {}
            base_env = connection.get("base_url_env")
            token_env = connection.get("credential_env")
            health_path = connection.get("health_path")
            missing = [
                name
                for name in (base_env, token_env, *(connection.get("context_env") or {}).values())
                if not isinstance(name, str) or not values.get(name)
            ]
            result: dict[str, Any] = {
                "domain": domain_id,
                "instance_id": domain.get("instance_id"),
                "status": "degraded",
                "check": "configuration",
                "http_status": None,
                "reason": "missing runtime environment" if missing else "health path unavailable",
                "missing_environment": sorted(str(item) for item in missing),
            }
            if missing or not isinstance(health_path, str) or not health_path:
                domains.append(result)
                continue
            base_url = values[str(base_env)].rstrip("/")
            path = health_path if health_path.startswith("/") else f"/{health_path}"
            try:
                response = http.get(
                    f"{base_url}{path}",
                    headers={"Authorization": f"Bearer {values[str(token_env)]}"},
                )
                result["http_status"] = response.status_code
                result["check"] = "health"
                if 200 <= response.status_code < 300:
                    result["status"] = "ready"
                    result["reason"] = "health check passed"
                else:
                    result["status"] = "failed"
                    result["reason"] = "health check rejected"
            except httpx.HTTPError:
                result["status"] = "failed"
                result["check"] = "health"
                result["reason"] = "health check unreachable"
            domains.append(result)
    finally:
        if owns_client:
            http.close()

    ready = sum(item["status"] == "ready" for item in domains)
    degraded = sum(item["status"] == "degraded" for item in domains)
    failed = sum(item["status"] == "failed" for item in domains)
    return {
        "version": 1,
        "protocol": "shadow.deployment-doctor.v1",
        "deployment_id": lock["deployment_id"],
        "build_id": lock["build_id"],
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "release_integrity": "verified",
        "status": "failed" if failed else "degraded" if degraded else "ready",
        "summary": {
            "domains": len(domains),
            "ready": ready,
            "degraded": degraded,
            "failed": failed,
        },
        "domains": domains,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify a compiled Shadow release and probe configured domain health"
    )
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-degraded", action="store_true")
    args = parser.parse_args()
    try:
        report = inspect_deployment(args.release_dir)
    except PluginContractError as exc:
        parser.error(str(exc))
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    if report["status"] == "failed" or (
        report["status"] == "degraded" and not args.allow_degraded
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
