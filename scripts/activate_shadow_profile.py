from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from shadow_sdk.plugin_contracts import PluginContractError


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(_sha256(path)))
    return digest.hexdigest()


def _release_path(release: Path, relative: object) -> Path:
    path = (release / str(relative)).resolve()
    if not path.is_relative_to(release):
        raise PluginContractError("deployment lock path escapes the release")
    return path


def verify_release(release_dir: Path) -> dict[str, Any]:
    release = release_dir.resolve()
    lock_path = release / "shadow-deployment.lock"
    if not lock_path.is_file():
        raise PluginContractError("release is missing shadow-deployment.lock")
    try:
        lock = json.loads(lock_path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise PluginContractError(f"cannot read deployment lock: {exc}") from exc
    if not isinstance(lock, dict) or lock.get("version") != 1:
        raise PluginContractError("unsupported deployment lock")
    if release.name != lock.get("build_id"):
        raise PluginContractError("release directory does not match build_id")
    for output in lock.get("outputs", []):
        if not isinstance(output, dict):
            raise PluginContractError("deployment lock has an invalid output")
        path = _release_path(release, output.get("path", ""))
        if not path.is_file() or _sha256(path) != output.get("sha256"):
            raise PluginContractError(f"release output failed verification: {path.name}")
    dsh = lock.get("dsh_bundle")
    if not isinstance(dsh, dict):
        raise PluginContractError("deployment lock is missing dsh_bundle")
    dsh_path = _release_path(release, dsh.get("path", ""))
    if not dsh_path.is_dir() or _tree_sha256(dsh_path) != dsh.get("tree_sha256"):
        raise PluginContractError("DSH bundle failed verification")
    app = json.loads((release / "shadow-app-runtime.json").read_text("utf-8"))
    nexus = json.loads((release / "shadow-nexus-runtime.json").read_text("utf-8"))
    build_id = lock["build_id"]
    if app.get("platform", {}).get("buildId") != build_id:
        raise PluginContractError("App projection build_id drift")
    if nexus.get("build_id") != build_id:
        raise PluginContractError("Nexus projection build_id drift")
    return lock


def activate_release(release_dir: Path, current_link: Path) -> dict[str, Any]:
    lock = verify_release(release_dir)
    release = release_dir.resolve()
    link = current_link.absolute()
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() and not link.is_symlink():
        raise PluginContractError("current target exists and is not a symlink")
    temporary = link.with_name(f".{link.name}.{lock['build_id']}.tmp")
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(release, target_is_directory=True)
    os.replace(temporary, link)
    return lock


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify and atomically activate a Shadow release")
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--current-link", type=Path)
    args = parser.parse_args()
    try:
        lock = (
            activate_release(args.release_dir, args.current_link)
            if args.current_link is not None
            else verify_release(args.release_dir)
        )
    except PluginContractError as exc:
        parser.error(str(exc))
    print(json.dumps({"deployment_id": lock["deployment_id"], "build_id": lock["build_id"]}))


if __name__ == "__main__":
    main()
