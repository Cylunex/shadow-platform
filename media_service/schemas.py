from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Visibility = Literal["public", "private", "scoped"]


class UploadCreate(BaseModel):
    owner_sub: str = Field(min_length=1, max_length=255)
    resource_type: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9._-]*$")
    resource_id: str = Field(min_length=1, max_length=255)
    visibility: Visibility = "private"
    original_filename: str = Field(min_length=1, max_length=512)
    content_type: str = Field(min_length=1, max_length=100)
    size_bytes: int = Field(gt=0)


class UploadTarget(BaseModel):
    method: Literal["PUT"] = "PUT"
    url: str
    headers: dict[str, str]


class UploadCreated(BaseModel):
    upload_id: str
    expires_at: datetime
    target: UploadTarget


class MediaView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    app_id: str
    owner_sub: str
    resource_type: str
    resource_id: str
    visibility: Visibility
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    width: int
    height: int
    status: str
    created_at: datetime


class AccessGrant(BaseModel):
    url: str
    expires_at: datetime | None


class DeleteResult(BaseModel):
    id: str
    status: Literal["deleted"]
    delete_after: datetime
