from __future__ import annotations

from typing import Any, Literal

import httpx


class MediaClientError(RuntimeError):
    pass


class MediaClient:
    """Synchronous server-side client for the Shadow Media control plane."""

    def __init__(
        self,
        base_url: str,
        service_token: str,
        *,
        timeout: float = 15.0,
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

    def __enter__(self) -> MediaClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            request_id = response.headers.get("x-request-id", "unknown")
            raise MediaClientError(
                f"media API returned {response.status_code}; request_id={request_id}"
            ) from exc
        payload = response.json()
        if not isinstance(payload, dict):
            raise MediaClientError("media API returned a non-object response")
        return payload

    def create_upload(
        self,
        *,
        owner_sub: str,
        resource_type: str,
        resource_id: str,
        visibility: Literal["public", "private", "scoped"],
        original_filename: str,
        content_type: str,
        size_bytes: int,
    ) -> dict[str, Any]:
        return self._json(
            self._client.post(
                "/v1/uploads",
                json={
                    "owner_sub": owner_sub,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "visibility": visibility,
                    "original_filename": original_filename,
                    "content_type": content_type,
                    "size_bytes": size_bytes,
                },
            )
        )

    def upload_bytes(self, target: dict[str, Any], content: bytes) -> None:
        response = self._client.request(
            str(target["method"]),
            str(target["url"]),
            headers=dict(target.get("headers") or {}),
            content=content,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise MediaClientError(f"upload target returned {response.status_code}") from exc

    def complete_upload(self, upload_id: str) -> dict[str, Any]:
        return self._json(self._client.post(f"/v1/uploads/{upload_id}/complete"))

    def grant_access(self, media_id: str) -> dict[str, Any]:
        return self._json(self._client.post(f"/v1/media/{media_id}/access"))

    def delete(self, media_id: str) -> dict[str, Any]:
        return self._json(self._client.delete(f"/v1/media/{media_id}"))
