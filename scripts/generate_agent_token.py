from __future__ import annotations

import argparse
import hashlib
import secrets
from pathlib import Path

from scripts.render_llm_env import write_private_file


def create_agent_token(digest_output: Path, *, force: bool = False) -> str:
    if digest_output.exists() and not force:
        raise FileExistsError(f"refusing to overwrite existing digest: {digest_output}")
    token = secrets.token_urlsafe(48)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest() + "\n"
    write_private_file(digest_output, digest)
    return token


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a high-entropy Agent token and store only its SHA-256"
    )
    parser.add_argument("--digest-output", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    token = create_agent_token(args.digest_output, force=args.force)
    print("Agent token (shown once; give it only to the Agent):")
    print(token)


if __name__ == "__main__":
    main()
