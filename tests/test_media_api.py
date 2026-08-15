from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from media_service.app import create_app
from media_service.config import Settings


def png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (4, 3), (25, 40, 55)).save(output, format="PNG")
    return output.getvalue()


def make_client(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'media.db'}",
        storage_root=tmp_path / "objects",
        public_base_url="http://testserver",
        service_tokens={"travel": "travel-secret", "health": "health-secret"},
        access_signing_key="test-signing-key-with-sufficient-entropy",
    )
    return TestClient(create_app(settings))


def test_private_upload_complete_and_access(tmp_path):
    data = png_bytes()
    with make_client(tmp_path) as client:
        created = client.post(
            "/v1/uploads",
            headers={"Authorization": "Bearer travel-secret"},
            json={
                "owner_sub": "user-123",
                "resource_type": "visit",
                "resource_id": "visit-456",
                "visibility": "private",
                "original_filename": "meal.png",
                "content_type": "image/png",
                "size_bytes": len(data),
            },
        )
        assert created.status_code == 201
        upload = created.json()

        sent = client.put(
            f"/v1/uploads/{upload['upload_id']}/content",
            headers=upload["target"]["headers"],
            content=data,
        )
        assert sent.status_code == 204

        completed = client.post(
            f"/v1/uploads/{upload['upload_id']}/complete",
            headers={"Authorization": "Bearer travel-secret"},
        )
        assert completed.status_code == 200
        media = completed.json()
        assert media["width"] == 4
        assert media["height"] == 3
        assert media["content_type"] == "image/png"

        assert client.get(f"/v1/content/{media['id']}").status_code == 403
        granted = client.post(
            f"/v1/media/{media['id']}/access",
            headers={"Authorization": "Bearer travel-secret"},
        )
        assert granted.status_code == 200
        content = client.get(granted.json()["url"])
        assert content.status_code == 200
        assert content.content == data


def test_service_tokens_are_namespace_scoped(tmp_path):
    data = png_bytes()
    with make_client(tmp_path) as client:
        created = client.post(
            "/v1/uploads",
            headers={"Authorization": "Bearer travel-secret"},
            json={
                "owner_sub": "user-123",
                "resource_type": "place",
                "resource_id": "place-456",
                "visibility": "public",
                "original_filename": "cover.png",
                "content_type": "image/png",
                "size_bytes": len(data),
            },
        ).json()
        client.put(
            f"/v1/uploads/{created['upload_id']}/content",
            headers=created["target"]["headers"],
            content=data,
        )
        media = client.post(
            f"/v1/uploads/{created['upload_id']}/complete",
            headers={"Authorization": "Bearer travel-secret"},
        ).json()

        denied = client.get(
            f"/v1/media/{media['id']}",
            headers={"Authorization": "Bearer health-secret"},
        )
        assert denied.status_code == 404


def test_rejects_non_image_content(tmp_path):
    data = b"not an image"
    with make_client(tmp_path) as client:
        created = client.post(
            "/v1/uploads",
            headers={"Authorization": "Bearer travel-secret"},
            json={
                "owner_sub": "user-123",
                "resource_type": "place",
                "resource_id": "place-456",
                "visibility": "private",
                "original_filename": "fake.png",
                "content_type": "image/png",
                "size_bytes": len(data),
            },
        ).json()
        response = client.put(
            f"/v1/uploads/{created['upload_id']}/content",
            headers=created["target"]["headers"],
            content=data,
        )
        assert response.status_code == 400


def test_original_filename_is_reduced_to_a_safe_basename(tmp_path):
    data = png_bytes()
    with make_client(tmp_path) as client:
        created = client.post(
            "/v1/uploads",
            headers={"Authorization": "Bearer travel-secret"},
            json={
                "owner_sub": "user-123",
                "resource_type": "place",
                "resource_id": "place-456",
                "visibility": "public",
                "original_filename": "..\\private\\cover.png",
                "content_type": "image/png",
                "size_bytes": len(data),
            },
        ).json()
        client.put(
            f"/v1/uploads/{created['upload_id']}/content",
            headers=created["target"]["headers"],
            content=data,
        )
        media = client.post(
            f"/v1/uploads/{created['upload_id']}/complete",
            headers={"Authorization": "Bearer travel-secret"},
        ).json()
        assert media["original_filename"] == "cover.png"
