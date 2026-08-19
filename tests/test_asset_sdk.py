import httpx
import pytest

from shadow_sdk.assets import AssetClient, AssetClientError


def test_asset_client_creates_idempotent_upload_session():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer app-secret"
        assert request.headers["idempotency-key"] == "report:1:file"
        assert request.url.path == "/v1/upload-sessions"
        return httpx.Response(
            201,
            json={
                "upload_session_id": "upload-1",
                "expires_at": "2026-08-20T00:00:00Z",
                "target": {"method": "PUT", "url": "https://upload.test", "headers": {}},
            },
        )

    with AssetClient(
        "https://assets.test",
        "app-secret",
        transport=httpx.MockTransport(handler),
    ) as client:
        result = client.create_upload_session(
            owner_id="10000000-0000-4000-8000-000000000001",
            original_filename="report.pdf",
            content_type="application/pdf",
            size_bytes=123,
            idempotency_key="report:1:file",
        )

    assert result["upload_session_id"] == "upload-1"


def test_asset_client_error_never_echoes_response_body_or_token():
    transport = httpx.MockTransport(
        lambda _: httpx.Response(403, text="server accidentally echoed app-secret")
    )
    with (
        AssetClient("https://assets.test", "app-secret", transport=transport) as client,
        pytest.raises(AssetClientError) as caught,
    ):
        client.get_asset("asset-1")

    assert "app-secret" not in str(caught.value)
    assert "403" in str(caught.value)
