from __future__ import annotations

import argparse
from pathlib import Path

from shadow_sdk.plugin_contracts import PluginContractError, validate_plugin


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a Shadow plugin definition")
    parser.add_argument("plugin_root", type=Path)
    parser.add_argument("--platform-root", type=Path, default=Path(__file__).parents[1])
    args = parser.parse_args()
    try:
        plugin = validate_plugin(args.plugin_root.resolve(), args.platform_root.resolve())
    except PluginContractError as exc:
        parser.error(str(exc))
    print(f"{plugin.plugin_id}@{plugin.version}")


if __name__ == "__main__":
    main()
