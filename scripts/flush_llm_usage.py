from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from scripts.render_llm_env import write_private_file


def flush_outbox(
    outbox: Path,
    *,
    endpoint: str,
    token: str,
    batch_size: int = 100,
    transport: httpx.BaseTransport | None = None,
) -> tuple[int, int]:
    if not 1 <= batch_size <= 100:
        raise ValueError("batch_size must be between 1 and 100")
    _validate_endpoint(endpoint)
    sending = outbox.with_suffix(f"{outbox.suffix}.sending")
    rejected = outbox.with_suffix(f"{outbox.suffix}.rejected")
    sent = 0
    rejected_count = 0
    with httpx.Client(
        base_url=f"{endpoint.rstrip('/')}/",
        headers={"authorization": f"Bearer {token}"},
        timeout=15,
        transport=transport,
    ) as client:
        while sending.exists() or outbox.exists():
            if not sending.exists():
                os.replace(outbox, sending)
            valid, invalid = _load_events(sending)
            if invalid:
                rejected.parent.mkdir(parents=True, exist_ok=True)
                with rejected.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.writelines(f"{line}\n" for line in invalid)
                rejected_count += len(invalid)
            if not valid:
                sending.unlink(missing_ok=True)
                continue
            for offset in range(0, len(valid), batch_size):
                batch = valid[offset : offset + batch_size]
                response = client.post("v1/llm-usage/events", json={"events": batch})
                if response.status_code != 202:
                    remaining = valid[offset:]
                    _write_events(sending, remaining)
                    raise RuntimeError(f"telemetry collector returned HTTP {response.status_code}")
                sent += len(batch)
                _write_events(sending, valid[offset + len(batch) :])
            sending.unlink(missing_ok=True)
    return sent, rejected_count


def _load_events(path: Path) -> tuple[list[dict], list[str]]:
    valid = []
    invalid = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            invalid.append(line)
            continue
        if isinstance(value, dict):
            valid.append(value)
        else:
            invalid.append(line)
    return valid, invalid


def _write_events(path: Path, events: list[dict]) -> None:
    content = "".join(
        f"{json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n" for event in events
    )
    write_private_file(path, content)


def _validate_endpoint(endpoint: str) -> None:
    parsed = urlsplit(endpoint)
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if not parsed.hostname or (
        parsed.scheme != "https" and not (local and parsed.scheme == "http")
    ):
        raise ValueError("telemetry endpoint must use HTTPS except on localhost")


def main() -> None:
    parser = argparse.ArgumentParser(description="Flush the metadata-only LLM usage outbox")
    parser.add_argument("--outbox", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    token = args.token_file.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise ValueError("service token file is empty or too short")
    sent, rejected = flush_outbox(
        args.outbox,
        endpoint=args.endpoint,
        token=token,
        batch_size=args.batch_size,
    )
    print(f"sent={sent} rejected={rejected}")


if __name__ == "__main__":
    main()
