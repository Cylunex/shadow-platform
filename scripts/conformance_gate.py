from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts.activate_shadow_profile import verify_release
from shadow_sdk.conformance import apply_evidence, gate_failures, load_json_object
from shadow_sdk.plugin_contracts import PluginContractError


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge cross-project evidence and gate one immutable Shadow release"
    )
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, action="append", default=[])
    parser.add_argument(
        "--require-stage",
        choices=["contract", "client", "deployed", "observed", "restore-tested"],
        default="contract",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    platform_root = Path(__file__).parents[1]
    try:
        verify_release(args.release_dir.resolve())
        status = load_json_object(
            args.release_dir.resolve() / "shadow-capability-status.json",
            label="capability status",
        )
        for evidence_path in args.evidence:
            evidence = load_json_object(evidence_path.resolve(), label="conformance evidence")
            status = apply_evidence(status, evidence, platform_root=platform_root)
        failures = gate_failures(status, args.require_stage)
    except PluginContractError as exc:
        parser.error(str(exc))
    encoded = json.dumps(status, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    if failures:
        print(
            f"conformance gate {args.require_stage} failed: {', '.join(failures)}",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
