from __future__ import annotations

from typing import Any, Literal

import httpx


class AssetClientError(RuntimeError):
    pass


class AssetClient:
    """Synchronous service client for the Shadow Asset control plane."""

    def __init__(
        self,
        base_url: str,
        service_token: str,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {service_token}"},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> AssetClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            request_id = response.headers.get("x-request-id", "unknown")
            raise AssetClientError(
                f"asset API returned {response.status_code}; request_id={request_id}"
            ) from exc
        payload = response.json()
        if not isinstance(payload, dict):
            raise AssetClientError("asset API returned a non-object response")
        return payload

    def create_upload_session(
        self,
        *,
        owner_id: str,
        original_filename: str,
        content_type: str,
        size_bytes: int,
        ownership_mode: Literal["user_owned", "app_managed", "derived"] = "app_managed",
        access_mode: Literal["private", "delegated", "public"] = "private",
        sensitivity: Literal["normal", "sensitive", "restricted"] = "normal",
        retention_policy_key: str = "standard",
        display_name: str | None = None,
        initial_reference: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        return self._json(
            self._client.post(
                "/v1/upload-sessions",
                headers=headers,
                json={
                    "owner_id": owner_id,
                    "ownership_mode": ownership_mode,
                    "access_mode": access_mode,
                    "sensitivity": sensitivity,
                    "retention_policy_key": retention_policy_key,
                    "display_name": display_name,
                    "original_filename": original_filename,
                    "content_type": content_type,
                    "size_bytes": size_bytes,
                    "initial_reference": initial_reference,
                },
            )
        )

    def upload_bytes(self, target: dict[str, Any], content: bytes) -> None:
        response = self._client.request(
            str(target.get("method", "PUT")),
            str(target["url"]),
            headers=dict(target.get("headers") or {}),
            content=content,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AssetClientError(f"asset upload returned {response.status_code}") from exc

    def complete_upload(self, upload_session_id: str) -> dict[str, Any]:
        return self._json(self._client.post(f"/v1/upload-sessions/{upload_session_id}/complete"))

    def create_version_upload_session(
        self,
        asset_id: str,
        *,
        original_filename: str,
        content_type: str,
        size_bytes: int,
        change_reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        return self._json(
            self._client.post(
                f"/v1/assets/{asset_id}/version-upload-sessions",
                headers=headers,
                json={
                    "original_filename": original_filename,
                    "content_type": content_type,
                    "size_bytes": size_bytes,
                    "change_reason": change_reason,
                },
            )
        )

    def get_asset(self, asset_id: str) -> dict[str, Any]:
        return self._json(self._client.get(f"/v1/assets/{asset_id}"))

    def trash_asset(self, asset_id: str) -> dict[str, Any]:
        return self._json(self._client.post(f"/v1/assets/{asset_id}/trash"))

    def restore_asset(self, asset_id: str) -> dict[str, Any]:
        return self._json(self._client.post(f"/v1/assets/{asset_id}/restore"))

    def create_reference(
        self,
        *,
        asset_id: str,
        resource_uri: str,
        usage_role: str,
        reference_key: str,
        binding_mode: Literal["pinned", "latest"] = "pinned",
        pinned_version_id: str | None = None,
    ) -> dict[str, Any]:
        return self._json(
            self._client.post(
                "/v1/asset-references",
                json={
                    "asset_id": asset_id,
                    "resource_uri": resource_uri,
                    "usage_role": usage_role,
                    "reference_key": reference_key,
                    "binding_mode": binding_mode,
                    "pinned_version_id": pinned_version_id,
                },
            )
        )

    def release_reference(self, reference_id: str) -> dict[str, Any]:
        return self._json(self._client.delete(f"/v1/asset-references/{reference_id}"))

    def create_derivative(
        self,
        *,
        source_version_id: str,
        derived_version_id: str,
        recipe_key: str,
        recipe_version: str,
        generator: str,
        generator_version: str,
        parameters: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        return self._json(
            self._client.post(
                "/v1/asset-derivatives",
                json={
                    "source_version_id": source_version_id,
                    "derived_version_id": derived_version_id,
                    "recipe_key": recipe_key,
                    "recipe_version": recipe_version,
                    "parameters": parameters or {},
                    "generator": generator,
                    "generator_version": generator_version,
                },
            )
        )

    def grant_access(
        self,
        version_id: str,
        *,
        operation: Literal["inline", "download"] = "inline",
    ) -> dict[str, Any]:
        return self._json(
            self._client.post(
                f"/v1/asset-versions/{version_id}/access-grants",
                json={"operation": operation},
            )
        )
