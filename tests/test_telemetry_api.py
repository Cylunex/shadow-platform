from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
from fastapi.testclient import TestClient

from scripts.flush_llm_usage import flush_outbox
from shadow_sdk.service_auth import hash_service_token
from telemetry_service.app import create_app
from telemetry_service.config import Settings

TOKEN = "travel-telemetry-token-at-least-32-bytes"


def event(request_id: str = "request-1") -> dict:
    return {
        "request_id": request_id,
        "app_id": "travel",
        "agent_id": "travel-planner",
        "model_alias": "chat-default",
        "provider": "primary",
        "actual_model": "chat-snapshot",
        "protocol": "openai-compatible",
        "api": "responses",
        "status": "success",
        "latency_ms": 123,
        "input_tokens": 10,
        "output_tokens": 4,
        "cached_tokens": 3,
        "retry_count": 1,
        "streamed": False,
        "started_at": datetime.now(UTC).isoformat(),
    }


def make_client(tmp_path):
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'telemetry.db'}",
        service_token_hashes={"travel": (hash_service_token(TOKEN),)},
    )
    return TestClient(create_app(settings))


def test_ingest_is_namespaced_idempotent_and_queryable(tmp_path):
    headers = {"authorization": f"Bearer {TOKEN}"}
    with make_client(tmp_path) as client:
        first = client.post("/v1/llm-usage/events", headers=headers, json={"events": [event()]})
        duplicate = client.post("/v1/llm-usage/events", headers=headers, json={"events": [event()]})
        summary = client.get("/v1/llm-usage/summary", headers=headers)
        ready = client.get("/readyz")

    assert first.status_code == 202
    assert first.json() == {"accepted": 1, "duplicates": 0}
    assert duplicate.json() == {"accepted": 0, "duplicates": 1}
    assert summary.status_code == 200
    assert summary.json()["buckets"][0] == {
        "model_alias": "chat-default",
        "provider": "primary",
        "actual_model": "chat-snapshot",
        "status": "success",
        "request_count": 1,
        "input_tokens": 10,
        "output_tokens": 4,
        "cached_tokens": 3,
        "total_latency_ms": 123,
        "retry_count": 1,
    }
    assert ready.status_code == 200


def test_ingest_rejects_cross_app_event(tmp_path):
    bad = event()
    bad["app_id"] = "health"
    with make_client(tmp_path) as client:
        response = client.post(
            "/v1/llm-usage/events",
            headers={"authorization": f"Bearer {TOKEN}"},
            json={"events": [bad]},
        )

    assert response.status_code == 403


def test_outbox_flushes_batches_and_quarantines_invalid_lines(tmp_path):
    outbox = tmp_path / "usage.jsonl"
    outbox.write_text(
        f"{json.dumps(event('one'))}\nnot-json\n{json.dumps(event('two'))}\n",
        encoding="utf-8",
    )
    batches = []

    def handler(request: httpx.Request):
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        batches.append(json.loads(request.content)["events"])
        return httpx.Response(202, json={"accepted": len(batches[-1]), "duplicates": 0})

    sent, rejected = flush_outbox(
        outbox,
        endpoint="https://telemetry.example",
        token=TOKEN,
        batch_size=1,
        transport=httpx.MockTransport(handler),
    )

    assert sent == 2
    assert rejected == 1
    assert [batch[0]["request_id"] for batch in batches] == ["one", "two"]
    assert not outbox.exists()
    assert "not-json" in (tmp_path / "usage.jsonl.rejected").read_text(encoding="utf-8")


def test_outbox_retains_unsent_events_on_collector_failure(tmp_path):
    outbox = tmp_path / "usage.jsonl"
    outbox.write_text(f"{json.dumps(event())}\n", encoding="utf-8")

    try:
        flush_outbox(
            outbox,
            endpoint="https://telemetry.example",
            token=TOKEN,
            transport=httpx.MockTransport(lambda _: httpx.Response(503)),
        )
    except RuntimeError as exc:
        assert "503" in str(exc)
    else:
        raise AssertionError("collector failure should be reported to the flush job")

    sending = tmp_path / "usage.jsonl.sending"
    assert json.loads(sending.read_text(encoding="utf-8"))["request_id"] == "request-1"
