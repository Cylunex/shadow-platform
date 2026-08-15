from __future__ import annotations

import argparse
import json
import secrets
from pathlib import Path

from scripts.render_llm_env import write_private_file
from shadow_sdk.service_auth import (
    SERVICE_ID_PATTERN,
    build_service_token_registry,
    hash_service_token,
    load_service_token_hashes,
)


def rotate_service_token(path: Path, app_id: str, *, replace: bool = False) -> str:
    if not SERVICE_ID_PATTERN.fullmatch(app_id):
        raise ValueError(f"invalid app_id: {app_id!r}")
    existing: dict[str, list[str]] = {}
    if path.exists():
        existing = {key: list(values) for key, values in load_service_token_hashes(path).items()}
    token = secrets.token_urlsafe(48)
    digest = hash_service_token(token)
    current = [] if replace else existing.get(app_id, [])
    existing[app_id] = [digest, *current][:2]
    content = json.dumps(build_service_token_registry(existing), indent=2) + "\n"
    write_private_file(path, content)
    return token


def retire_previous_token(path: Path, app_id: str) -> None:
    existing = {key: list(values) for key, values in load_service_token_hashes(path).items()}
    if app_id not in existing:
        raise ValueError(f"service token entry does not exist: {app_id}")
    existing[app_id] = existing[app_id][:1]
    content = json.dumps(build_service_token_registry(existing), indent=2) + "\n"
    write_private_file(path, content)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a service token and store only its SHA-256 digest"
    )
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--app", required=True)
    parser.add_argument("--token-output", type=Path)
    parser.add_argument(
        "--replace", action="store_true", help="replace instead of retaining the previous digest"
    )
    parser.add_argument("--retire-previous", action="store_true")
    args = parser.parse_args()
    if args.retire_previous:
        retire_previous_token(args.registry, args.app)
        print(f"Retired previous service token digest for {args.app}")
        return
    token = rotate_service_token(args.registry, args.app, replace=args.replace)
    if args.token_output:
        write_private_file(args.token_output, f"{token}\n")
        print(f"Service token for {args.app} written to {args.token_output}")
    else:
        print(f"Service token for {args.app} (shown once):")
        print(token)


if __name__ == "__main__":
    main()
