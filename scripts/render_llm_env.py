from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from collections.abc import Sequence
from pathlib import Path

from shadow_sdk.llm import ResolvedLLMConfig, resolve_llm_config

BINDING_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")


def parse_binding(value: str) -> tuple[str, str]:
    name, separator, alias = value.partition("=")
    if not separator or not BINDING_PATTERN.fullmatch(name) or not alias:
        raise argparse.ArgumentTypeError("binding must look like CHAT=chat-default")
    return name, alias


def render_env(configs: Sequence[tuple[str, ResolvedLLMConfig]]) -> str:
    if not configs:
        raise ValueError("at least one LLM binding is required")
    version = configs[0][1].registry_version
    lines = [f"SHADOW_LLM_CONFIG_VERSION={json.dumps(str(version))}"]
    for name, config in configs:
        prefix = f"SHADOW_LLM_{name}"
        values = {
            "PROTOCOL": config.protocol,
            "BASE_URL": config.base_url,
            "MODEL": config.model,
            "API_KEY_FILE": str(config.api_key_file),
            "TIMEOUT_SECONDS": str(config.timeout_seconds),
            "FALLBACKS": ",".join(config.fallbacks),
        }
        lines.extend(f"{prefix}_{key}={json.dumps(value)}" for key, value in values.items())
    return "\n".join(lines) + "\n"


def write_private_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as output:
            output.write(content)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Render direct LLM provider config for one app")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--secrets-dir", type=Path, required=True)
    parser.add_argument("--app", required=True)
    parser.add_argument("--binding", type=parse_binding, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    configs = [
        (
            name,
            resolve_llm_config(
                args.registry,
                secrets_dir=args.secrets_dir,
                app_id=args.app,
                alias=alias,
            ),
        )
        for name, alias in args.binding
    ]
    write_private_file(args.output, render_env(configs))


if __name__ == "__main__":
    main()
