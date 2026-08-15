import httpx
import pytest

from shadow_sdk.media import MediaClient, MediaClientError


def test_media_client_sends_service_token_and_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer app-secret"
        assert request.url.path == "/v1/uploads"
        return httpx.Response(
            201,
            json={
                "upload_id": "upload-1",
                "expires_at": "2026-08-15T00:00:00Z",
                "target": {"method": "PUT", "url": "https://upload.test", "headers": {}},
            },
        )

    with MediaClient(
        "https://media.test",
        "app-secret",
        transport=httpx.MockTransport(handler),
    ) as client:
        result = client.create_upload(
            owner_sub="user-1",
            resource_type="visit",
            resource_id="visit-1",
            visibility="private",
            original_filename="photo.jpg",
            content_type="image/jpeg",
            size_bytes=123,
        )

    assert result["upload_id"] == "upload-1"


def test_media_client_error_does_not_echo_response_body_or_token():
    transport = httpx.MockTransport(
        lambda _: httpx.Response(401, text="server accidentally echoed app-secret")
    )
    with (
        MediaClient("https://media.test", "app-secret", transport=transport) as client,
        pytest.raises(MediaClientError) as caught,
    ):
        client.grant_access("media-1")

    assert "app-secret" not in str(caught.value)
    assert "401" in str(caught.value)
