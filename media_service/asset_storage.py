from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
import zipfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from .storage import StorageError, inspect_image


@dataclass(frozen=True, slots=True)
class InspectedAsset:
    detected_mime: str
    media_family: str
    technical_metadata: dict[str, object]


@dataclass(frozen=True, slots=True)
class StagedAsset:
    size_bytes: int
    sha256: str
    inspected: InspectedAsset


def _same_mime(declared: str, detected: str) -> bool:
    declared = declared.split(";", 1)[0].strip().lower()
    detected = detected.split(";", 1)[0].strip().lower()
    if declared == detected:
        return True
    return {declared, detected} <= {"image/heic", "image/heif"}


def _inspect_zip(path: Path, declared_mime: str) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "mimetype" in names:
                mime = archive.read("mimetype")[:128].decode("ascii", errors="ignore").strip()
                if mime == "application/epub+zip":
                    return mime
            if "[Content_Types].xml" in names:
                if any(name.startswith("word/") for name in names):
                    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                if any(name.startswith("xl/") for name in names):
                    return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                if any(name.startswith("ppt/") for name in names):
                    return (
                        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                    )
    except (OSError, zipfile.BadZipFile) as exc:
        raise StorageError("file is not a valid ZIP-based document") from exc
    if declared_mime in {"application/zip", "application/x-zip-compressed"}:
        return declared_mime
    return "application/zip"


def inspect_asset(path: Path, declared_mime: str) -> InspectedAsset:
    declared = declared_mime.split(";", 1)[0].strip().lower()
    sample = path.read_bytes()[:8192]
    metadata: dict[str, object] = {}

    image_signature = sample.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF8"))
    image_signature = image_signature or sample.startswith(b"RIFF") and sample[8:12] == b"WEBP"
    if declared.startswith("image/") or image_signature:
        detected, width, height = inspect_image(path)
        metadata.update(width=width, height=height)
    elif sample.startswith(b"%PDF-"):
        detected = "application/pdf"
    elif sample.startswith(b"PK\x03\x04"):
        detected = _inspect_zip(path, declared)
    elif sample.startswith(b"WARC/"):
        detected = "application/warc"
    elif sample.startswith(b"ID3") or sample[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
        detected = "audio/mpeg"
    elif sample.startswith(b"RIFF") and sample[8:12] == b"WAVE":
        detected = "audio/wav"
    elif len(sample) >= 12 and sample[4:8] == b"ftyp":
        detected = "video/mp4"
    elif sample.startswith(b"\x1aE\xdf\xa3"):
        detected = "video/webm" if declared.startswith("video/") else "application/x-matroska"
    elif declared.startswith("text/") or declared in {"application/json", "application/xml"}:
        try:
            text = sample.decode("utf-8")
            if declared == "application/json" and path.stat().st_size <= 8 * 1024 * 1024:
                json.loads(path.read_text(encoding="utf-8"))
            detected = declared
            metadata["encoding"] = "utf-8"
            metadata["sample_characters"] = len(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StorageError("declared text content is not valid UTF-8") from exc
    elif declared == "application/octet-stream":
        detected = declared
    else:
        # Formats without a stable signature remain constrained by the configured MIME allowlist.
        detected = declared

    if not _same_mime(declared, detected):
        raise StorageError(f"detected MIME {detected} does not match declaration {declared}")

    if detected.startswith("image/"):
        family = "image"
    elif detected.startswith("video/"):
        family = "video"
    elif detected.startswith("audio/"):
        family = "audio"
    elif detected in {
        "application/pdf",
        "application/epub+zip",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    } or detected.startswith("text/"):
        family = "document"
    elif detected in {"application/warc", "application/zip", "application/x-zip-compressed"}:
        family = "archive"
    else:
        family = "binary"
    return InspectedAsset(
        detected_mime=detected,
        media_family=family,
        technical_metadata=metadata,
    )


class AssetLocalStorage:
    """Immutable content-addressed storage with staging on the same filesystem by default."""

    backend_id = "local-nas"

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.staging_root = self.root / "staging"
        self.blob_root = self.root / "blobs"
        self.staging_root.mkdir(parents=True, exist_ok=True)
        self.blob_root.mkdir(parents=True, exist_ok=True)

    def staging_path(self, key: str) -> Path:
        return self._safe_path(self.staging_root, key)

    def blob_path(self, object_key: str) -> Path:
        return self._safe_path(self.root, object_key)

    @staticmethod
    def _safe_path(root: Path, key: str) -> Path:
        path = (root / key).resolve()
        if root != path and root not in path.parents:
            raise StorageError("invalid asset storage key")
        return path

    async def write_staging(
        self,
        key: str,
        chunks: AsyncIterator[bytes],
        *,
        declared_mime: str,
        declared_size: int,
        max_size: int,
    ) -> StagedAsset:
        destination = self.staging_path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
        digest = hashlib.sha256()
        size = 0
        try:
            with temporary.open("xb") as output:
                os.chmod(temporary, 0o600)
                async for chunk in chunks:
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_size or size > declared_size:
                        raise StorageError("uploaded file exceeds declared or configured size")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if size != declared_size:
                raise StorageError("uploaded file size does not match declaration")
            inspected = inspect_asset(temporary, declared_mime)
            os.replace(temporary, destination)
            return StagedAsset(size_bytes=size, sha256=digest.hexdigest(), inspected=inspected)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def object_key_for(self, sha256: str) -> str:
        if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
            raise StorageError("invalid SHA-256 digest")
        return f"blobs/sha256/{sha256[:2]}/{sha256[2:4]}/{sha256}"

    def finalize_staging(self, key: str, *, sha256: str, size_bytes: int) -> str:
        source = self.staging_path(key)
        object_key = self.object_key_for(sha256)
        destination = self.blob_path(object_key)
        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.is_file():
            self._verify_file(destination, sha256=sha256, size_bytes=size_bytes)
            source.unlink(missing_ok=True)
            return object_key
        if not source.is_file():
            raise StorageError("staged upload is missing")

        source_device = source.stat().st_dev
        destination_device = destination.parent.stat().st_dev
        if source_device == destination_device:
            os.replace(source, destination)
            os.chmod(destination, 0o600)
        else:
            # rename is only atomic within one filesystem. Cross-device finalization copies to a
            # temporary destination, verifies it, then atomically switches inside the target FS.
            copied = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.copy")
            try:
                with source.open("rb") as input_file, copied.open("xb") as output_file:
                    shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
                    output_file.flush()
                    os.fsync(output_file.fileno())
                self._verify_file(copied, sha256=sha256, size_bytes=size_bytes)
                os.replace(copied, destination)
                os.chmod(destination, 0o600)
                source.unlink()
            finally:
                copied.unlink(missing_ok=True)

        self._verify_file(destination, sha256=sha256, size_bytes=size_bytes)
        return object_key

    @staticmethod
    def _verify_file(path: Path, *, sha256: str, size_bytes: int) -> None:
        if path.stat().st_size != size_bytes:
            raise StorageError("stored Blob size does not match")
        digest = hashlib.sha256()
        with path.open("rb") as content:
            for chunk in iter(lambda: content.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != sha256:
            raise StorageError("stored Blob checksum does not match")

    def delete_staging(self, key: str) -> None:
        self.staging_path(key).unlink(missing_ok=True)
