from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

from media_service.app import create_app
from media_service.asset_lifecycle import apply_asset_lifecycle, gc_candidate_ids
from media_service.asset_models import (
    Asset,
    AssetBlob,
    AssetBlobLocation,
    AssetReference,
    AssetVersion,
)
from media_service.config import Settings
from shadow_sdk.service_auth import hash_service_token

TRAVEL_TOKEN = "travel-secret-token-at-least-32-bytes"
HEALTH_TOKEN = "health-secret-token-at-least-32-bytes"
OWNER_ID = "10000000-0000-4000-8000-000000000001"


def png_bytes(color=(25, 40, 55)) -> bytes:
    output = BytesIO()
    Image.new("RGB", (4, 3), color).save(output, format="PNG")
    return output.getvalue()


def jpeg_with_exif() -> bytes:
    output = BytesIO()
    exif = Image.Exif()
    exif[270] = "private location note"
    Image.new("RGB", (4, 3), (25, 40, 55)).save(output, format="JPEG", exif=exif)
    return output.getvalue()


def make_client(tmp_path, **overrides):
    values = dict(
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'asset.db'}",
        storage_root=tmp_path / "objects",
        public_base_url="http://testserver",
        service_token_hashes={
            "travel": (hash_service_token(TRAVEL_TOKEN),),
            "health": (hash_service_token(HEALTH_TOKEN),),
        },
        access_signing_key="test-signing-key-with-sufficient-entropy",
    )
    values.update(overrides)
    settings = Settings(**values)
    return TestClient(create_app(settings)), settings


def authorization(token: str = TRAVEL_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def upload_asset(
    client: TestClient,
    data: bytes,
    *,
    key: str,
    content_type: str = "image/png",
    ownership_mode: str = "app_managed",
    access_mode: str = "private",
    token: str = TRAVEL_TOKEN,
):
    headers = {**authorization(token), "Idempotency-Key": f"upload:{key}"}
    created = client.post(
        "/v1/upload-sessions",
        headers=headers,
        json={
            "owner_id": OWNER_ID,
            "ownership_mode": ownership_mode,
            "access_mode": access_mode,
            "sensitivity": "normal",
            "display_name": key,
            "original_filename": key,
            "content_type": content_type,
            "size_bytes": len(data),
            "initial_reference": {
                "resource_uri": f"shadow://travel/assets/{key}",
                "usage_role": "file",
                "reference_key": f"travel:asset:{key}",
                "binding_mode": "pinned",
            },
        },
    )
    assert created.status_code == 201, created.text
    replay = client.post(
        "/v1/upload-sessions",
        headers=headers,
        json={
            "owner_id": OWNER_ID,
            "ownership_mode": ownership_mode,
            "access_mode": access_mode,
            "sensitivity": "normal",
            "display_name": key,
            "original_filename": key,
            "content_type": content_type,
            "size_bytes": len(data),
            "initial_reference": {
                "resource_uri": f"shadow://travel/assets/{key}",
                "usage_role": "file",
                "reference_key": f"travel:asset:{key}",
                "binding_mode": "pinned",
            },
        },
    )
    assert replay.json() == created.json()
    upload = created.json()
    sent = client.put(
        f"/v1/upload-sessions/{upload['upload_session_id']}/content",
        headers=upload["target"]["headers"],
        content=data,
    )
    assert sent.status_code == 204, sent.text
    completed = client.post(
        f"/v1/upload-sessions/{upload['upload_session_id']}/complete",
        headers=authorization(token),
    )
    assert completed.status_code == 200, completed.text
    return completed.json()


def test_upload_session_advertises_local_targets_and_exact_cors(tmp_path):
    client, _ = make_client(
        tmp_path,
        asset_upload_base_urls=(
            "http://nas.example.test:18080/platform/assets",
            "https://assets-lan.example.test",
        ),
        asset_cors_origins=("https://garden.example.test",),
        allow_insecure_asset_upload_targets=True,
    )
    data = png_bytes()
    with client:
        created = client.post(
            "/v1/upload-sessions",
            headers=authorization(),
            json={
                "owner_id": OWNER_ID,
                "original_filename": "direct.png",
                "content_type": "image/png",
                "size_bytes": len(data),
            },
        )
        assert created.status_code == 201
        payload = created.json()
        assert payload["target"]["route"] == "canonical"
        assert payload["target"]["url"].startswith("http://testserver/")
        assert [target["route"] for target in payload["alternate_targets"]] == [
            "alternate-1",
            "alternate-2",
        ]
        assert payload["alternate_targets"][0]["url"].startswith(
            "http://nas.example.test:18080/platform/assets/"
        )
        assert payload["alternate_targets"][0]["headers"] == payload["target"]["headers"]

        preflight = client.options(
            "/v1/upload-sessions/example/content",
            headers={
                "Origin": "https://garden.example.test",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "https://garden.example.test"


def test_insecure_upload_target_requires_explicit_opt_in(tmp_path):
    with pytest.raises(ValueError, match="must use HTTPS"):
        make_client(
            tmp_path,
            asset_upload_base_urls=("http://nas.example.test:18080/platform/assets",),
        )


def test_asset_upload_preserves_original_and_deduplicates_only_blob(tmp_path):
    data = jpeg_with_exif()
    client, _ = make_client(tmp_path)
    with client:
        first = upload_asset(
            client, data, key="first.jpg", content_type="image/jpeg", access_mode="delegated"
        )
        second = upload_asset(
            client, data, key="second.jpg", content_type="image/jpeg", access_mode="delegated"
        )
        assert first["id"] != second["id"]
        assert first["current_version"]["source_fidelity"] == "original"

        grant = client.post(
            f"/v1/asset-versions/{first['current_version_id']}/access-grants",
            headers=authorization(),
            json={"operation": "download"},
        )
        assert grant.status_code == 200
        downloaded = client.get(grant.json()["url"])
        assert downloaded.content == data
        with Image.open(BytesIO(downloaded.content)) as image:
            assert image.getexif()[270] == "private location note"

        with client.app.state.session_factory() as db:
            assert db.scalar(select(func.count()).select_from(Asset)) == 2
            assert db.scalar(select(func.count()).select_from(AssetVersion)) == 2
            assert db.scalar(select(func.count()).select_from(AssetBlob)) == 1
            assert db.scalar(select(func.count()).select_from(AssetBlobLocation)) == 1


def test_asset_new_version_and_trash_restore_round_trip(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        asset = upload_asset(client, png_bytes(), key="versioned.png")
        replacement = png_bytes((120, 30, 60))
        created = client.post(
            f"/v1/assets/{asset['id']}/version-upload-sessions",
            headers={**authorization(), "Idempotency-Key": "versioned.png:v2"},
            json={
                "original_filename": "versioned-v2.png",
                "content_type": "image/png",
                "size_bytes": len(replacement),
                "change_reason": "replace scan",
            },
        )
        assert created.status_code == 201, created.text
        upload = created.json()
        sent = client.put(
            f"/v1/upload-sessions/{upload['upload_session_id']}/content",
            headers=upload["target"]["headers"],
            content=replacement,
        )
        assert sent.status_code == 204, sent.text
        completed = client.post(
            f"/v1/upload-sessions/{upload['upload_session_id']}/complete",
            headers=authorization(),
        )
        assert completed.status_code == 200, completed.text
        current = completed.json()
        assert current["id"] == asset["id"]
        assert current["current_version"]["version_number"] == 2

        trashed = client.post(f"/v1/assets/{asset['id']}/trash", headers=authorization())
        assert trashed.status_code == 200
        assert trashed.json()["lifecycle_state"] == "trashed"
        denied = client.post(
            f"/v1/asset-versions/{current['current_version_id']}/access-grants",
            headers=authorization(),
            json={"operation": "inline"},
        )
        assert denied.status_code == 404
        restored = client.post(f"/v1/assets/{asset['id']}/restore", headers=authorization())
        assert restored.status_code == 200
        assert restored.json()["lifecycle_state"] == "active"

        with client.app.state.session_factory() as db:
            versions = list(
                db.scalars(
                    select(AssetVersion)
                    .where(AssetVersion.asset_id == asset["id"])
                    .order_by(AssetVersion.version_number)
                )
            )
            assert [version.version_number for version in versions] == [1, 2]
            assert versions[1].change_reason == "replace scan"


def test_delegated_asset_can_be_referenced_by_another_app(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        asset = upload_asset(client, png_bytes(), key="delegated.png", access_mode="delegated")
        created = client.post(
            "/v1/asset-references",
            headers=authorization(HEALTH_TOKEN),
            json={
                "asset_id": asset["id"],
                "resource_uri": "shadow://health/reports/1",
                "usage_role": "attachment",
                "reference_key": "health:report:1:attachment",
                "binding_mode": "pinned",
                "pinned_version_id": asset["current_version_id"],
            },
        )
        assert created.status_code == 201, created.text
        granted = client.post(
            f"/v1/asset-versions/{asset['current_version_id']}/access-grants",
            headers=authorization(HEALTH_TOKEN),
            json={"operation": "inline"},
        )
        assert granted.status_code == 200


def test_pinned_reference_must_use_version_from_same_asset(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        first = upload_asset(client, png_bytes(), key="first.png", access_mode="delegated")
        second = upload_asset(
            client, png_bytes((90, 80, 70)), key="second.png", access_mode="delegated"
        )
        response = client.post(
            "/v1/asset-references",
            headers=authorization(HEALTH_TOKEN),
            json={
                "asset_id": first["id"],
                "resource_uri": "shadow://health/reports/2",
                "usage_role": "attachment",
                "reference_key": "health:report:2:attachment",
                "binding_mode": "pinned",
                "pinned_version_id": second["current_version_id"],
            },
        )
        assert response.status_code == 400

        invalid_latest = client.post(
            "/v1/asset-references",
            headers=authorization(HEALTH_TOKEN),
            json={
                "asset_id": first["id"],
                "resource_uri": "shadow://health/reports/3",
                "usage_role": "attachment",
                "reference_key": "health:report:3:attachment",
                "binding_mode": "latest",
                "pinned_version_id": first["current_version_id"],
            },
        )
        assert invalid_latest.status_code == 422


def test_app_managed_zero_reference_moves_to_trash_but_history_remains_reachable(tmp_path):
    client, settings = make_client(tmp_path)
    with client:
        asset_view = upload_asset(client, png_bytes(), key="orphan.png")
        with client.app.state.session_factory() as db:
            reference = db.scalar(
                select(AssetReference).where(AssetReference.asset_id == asset_view["id"])
            )
            reference_id = reference.id
        released = client.delete(f"/v1/asset-references/{reference_id}", headers=authorization())
        assert released.status_code == 200

        first_run_at = datetime.now(UTC) + timedelta(days=settings.asset_orphan_grace_days + 1)
        with client.app.state.session_factory() as db:
            result = apply_asset_lifecycle(db, settings, now=first_run_at)
            asset = db.get(Asset, asset_view["id"])
            blob_id = db.get(AssetVersion, asset_view["current_version_id"]).blob_id
            assert result.orphaned_trashed == 1
            assert asset.lifecycle_state == "trashed"
            assert blob_id not in result.gc_candidates

        purge_at = first_run_at + timedelta(days=settings.asset_trash_retention_days + 1)
        with client.app.state.session_factory() as db:
            result = apply_asset_lifecycle(db, settings, now=purge_at)
            assert result.purged_assets == 1
            assert blob_id in result.gc_candidates


def test_active_asset_historical_versions_are_gc_roots(tmp_path):
    client, settings = make_client(tmp_path)
    with client:
        asset_view = upload_asset(client, png_bytes(), key="versions.png")
        with client.app.state.session_factory() as db:
            asset = db.get(Asset, asset_view["id"])
            old_version = db.get(AssetVersion, asset.current_version_id)
            new_blob = AssetBlob(
                id=str(uuid.uuid4()),
                digest_algorithm="sha256",
                digest="f" * 64,
                size_bytes=4,
                integrity_state="healthy",
                created_at=datetime.now(UTC) - timedelta(days=10),
            )
            db.add(new_blob)
            db.flush()
            new_version = AssetVersion(
                id=str(uuid.uuid4()),
                asset_id=asset.id,
                version_number=2,
                blob_id=new_blob.id,
                original_filename="versions-v2.bin",
                declared_mime="application/octet-stream",
                detected_mime="application/octet-stream",
                media_family="binary",
                technical_metadata={},
                source_fidelity="original",
                created_by="service:travel",
                state="ready",
                created_at=datetime.now(UTC),
            )
            db.add(new_version)
            db.flush()
            asset.current_version_id = new_version.id
            db.commit()
            candidates = gc_candidate_ids(db, settings, now=datetime.now(UTC) + timedelta(days=2))
            assert old_version.blob_id not in candidates
            assert new_blob.id not in candidates


def test_derivative_cycle_is_rejected(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        assets = [
            upload_asset(
                client,
                png_bytes((index * 20 + 10, 30, 40)),
                key=f"derived-{index}.png",
                ownership_mode="derived",
            )
            for index in range(3)
        ]

        def derive(source_index: int, target_index: int, recipe: str):
            return client.post(
                "/v1/asset-derivatives",
                headers=authorization(),
                json={
                    "source_version_id": assets[source_index]["current_version_id"],
                    "derived_version_id": assets[target_index]["current_version_id"],
                    "recipe_key": recipe,
                    "recipe_version": "1",
                    "parameters": {},
                    "generator": "test",
                    "generator_version": "1",
                },
            )

        assert derive(0, 1, "image.preview").status_code == 201
        assert derive(1, 2, "image.thumbnail").status_code == 201
        cycle = derive(2, 0, "image.public-sanitized")
        assert cycle.status_code == 409
