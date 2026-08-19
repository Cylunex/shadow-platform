from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

OwnershipMode = Literal["user_owned", "app_managed", "derived"]
AccessMode = Literal["private", "delegated", "public"]
Sensitivity = Literal["normal", "sensitive", "restricted"]
BindingMode = Literal["pinned", "latest"]


class InitialReferenceCreate(BaseModel):
    resource_uri: str = Field(
        min_length=10, max_length=1024, pattern=r"^shadow://[a-z][a-z0-9-]{1,63}/.+$"
    )
    usage_role: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9._-]*$")
    reference_key: str = Field(min_length=1, max_length=512)
    binding_mode: BindingMode = "pinned"


class AssetUploadCreate(BaseModel):
    owner_id: UUID
    ownership_mode: OwnershipMode = "app_managed"
    access_mode: AccessMode = "private"
    sensitivity: Sensitivity = "normal"
    retention_policy_key: str = Field(
        default="standard", min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9._-]*$"
    )
    display_name: str | None = Field(default=None, min_length=1, max_length=512)
    original_filename: str = Field(min_length=1, max_length=512)
    content_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    initial_reference: InitialReferenceCreate | None = None

    @model_validator(mode="after")
    def validate_policy(self) -> AssetUploadCreate:
        if self.sensitivity == "restricted" and self.access_mode == "public":
            raise ValueError("restricted assets cannot be public")
        if self.ownership_mode == "derived" and self.initial_reference is None:
            raise ValueError("derived uploads require an initial reference")
        return self


class AssetUploadTarget(BaseModel):
    method: Literal["PUT"] = "PUT"
    url: str
    headers: dict[str, str]
    route: str = "canonical"


class AssetUploadCreated(BaseModel):
    upload_session_id: str
    expires_at: datetime
    target: AssetUploadTarget
    alternate_targets: list[AssetUploadTarget] = Field(default_factory=list)


class AssetVersionUploadCreate(BaseModel):
    original_filename: str = Field(min_length=1, max_length=512)
    content_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    change_reason: str | None = Field(default=None, min_length=1, max_length=255)


class AssetVersionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    asset_id: str
    version_number: int
    original_filename: str
    declared_mime: str
    detected_mime: str
    media_family: str
    technical_metadata: dict[str, object]
    source_fidelity: str
    state: str
    created_at: datetime


class AssetView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    owner_id: str
    created_by_app_id: str
    ownership_mode: OwnershipMode
    display_name: str
    access_mode: AccessMode
    sensitivity: Sensitivity
    retention_policy_key: str
    lifecycle_state: str
    current_version_id: str
    zero_referenced_at: datetime | None
    created_at: datetime
    trashed_at: datetime | None
    purge_after: datetime | None
    current_version: AssetVersionView


class AssetReferenceCreate(BaseModel):
    asset_id: str = Field(min_length=36, max_length=36)
    resource_uri: str = Field(
        min_length=10, max_length=1024, pattern=r"^shadow://[a-z][a-z0-9-]{1,63}/.+$"
    )
    usage_role: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9._-]*$")
    reference_key: str = Field(min_length=1, max_length=512)
    binding_mode: BindingMode = "pinned"
    pinned_version_id: str | None = Field(default=None, min_length=36, max_length=36)

    @model_validator(mode="after")
    def validate_binding(self) -> AssetReferenceCreate:
        if self.binding_mode == "pinned" and self.pinned_version_id is None:
            raise ValueError("pinned references require pinned_version_id")
        if self.binding_mode == "latest" and self.pinned_version_id is not None:
            raise ValueError("latest references cannot set pinned_version_id")
        return self


class AssetReferenceView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    asset_id: str
    app_id: str
    resource_uri: str
    usage_role: str
    reference_key: str
    binding_mode: BindingMode
    pinned_version_id: str | None
    state: str
    created_at: datetime
    released_at: datetime | None


class AssetReferenceRelease(BaseModel):
    id: str
    state: Literal["released"]
    released_at: datetime


class AssetDerivativeCreate(BaseModel):
    source_version_id: str = Field(min_length=36, max_length=36)
    derived_version_id: str = Field(min_length=36, max_length=36)
    recipe_key: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9._-]*$")
    recipe_version: str = Field(min_length=1, max_length=64)
    parameters: dict[str, object] = Field(default_factory=dict)
    generator: str = Field(min_length=1, max_length=128)
    generator_version: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_not_self(self) -> AssetDerivativeCreate:
        if self.source_version_id == self.derived_version_id:
            raise ValueError("an asset version cannot derive from itself")
        return self


class AssetDerivativeView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_version_id: str
    derived_version_id: str
    recipe_key: str
    recipe_version: str
    parameters_hash: str
    generator: str
    generator_version: str
    state: str
    created_at: datetime


class AssetAccessGrant(BaseModel):
    asset_id: str
    version_id: str
    url: str
    operation: Literal["inline", "download"]
    expires_at: datetime | None


class AssetAccessRequest(BaseModel):
    operation: Literal["inline", "download"] = "inline"
