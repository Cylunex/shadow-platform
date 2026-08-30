from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.activate_shadow_profile import verify_release
from shadow_sdk.conformance import load_json_object, restore_drill_to_evidence
from shadow_sdk.plugin_contracts import PluginContractError


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate an isolated backup restore drill and emit conformance evidence"
    )
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--drill", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    release = args.release_dir.resolve()
    platform_root = Path(__file__).parents[1]
    try:
        verify_release(release)
        status = load_json_object(
            release / "shadow-capability-status.json", label="capability status"
        )
        evidence = restore_drill_to_evidence(
            args.drill.resolve(), status, platform_root=platform_root
        )
    except PluginContractError as exc:
        parser.error(str(exc))
    encoded = json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
