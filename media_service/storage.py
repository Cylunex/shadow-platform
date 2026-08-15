from __future__ import annotations

import hashlib
import os
import warnings
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # optional HEIC support
    pass


FORMAT_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
    "HEIF": "image/heif",
    "HEIC": "image/heic",
}

# 约 40MP，足够手机照片，同时限制小文件解压为超大位图的资源消耗。
Image.MAX_IMAGE_PIXELS = 40_000_000

MIME_EXTENSION = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/heif": ".heif",
    "image/heic": ".heic",
}


@dataclass(frozen=True, slots=True)
class StoredImage:
    size_bytes: int
    sha256: str
    content_type: str
    width: int
    height: int


class StorageError(ValueError):
    pass


class LocalStorage:
    def __init__(self, root: Path, *, strip_metadata: bool = True):
        self.root = root.resolve()
        self.strip_metadata = strip_metadata
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root != path and self.root not in path.parents:
            raise StorageError("invalid storage key")
        return path

    async def put(
        self,
        key: str,
        chunks: AsyncIterator[bytes],
        *,
        declared_size: int,
        max_size: int,
    ) -> StoredImage:
        destination = self.path_for(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        digest = hashlib.sha256()
        size = 0

        try:
            with temporary.open("wb") as output:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_size or size > declared_size:
                        raise StorageError("uploaded file exceeds declared or configured size")
                    digest.update(chunk)
                    output.write(chunk)
            if size != declared_size:
                raise StorageError("uploaded file size does not match declaration")

            content_type, width, height = inspect_image(temporary)
            if self.strip_metadata:
                strip_image_metadata(temporary, content_type)
                size = temporary.stat().st_size
                if size > max_size:
                    raise StorageError("sanitized image exceeds configured size")
                digest = hashlib.sha256(temporary.read_bytes())
            os.replace(temporary, destination)
            return StoredImage(
                size_bytes=size,
                sha256=digest.hexdigest(),
                content_type=content_type,
                width=width,
                height=height,
            )
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def delete(self, key: str) -> None:
        self.path_for(key).unlink(missing_ok=True)


def inspect_image(path: Path) -> tuple[str, int, int]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                image.load()
                content_type = FORMAT_MIME.get((image.format or "").upper())
                if not content_type:
                    raise StorageError("unsupported decoded image format")
                width, height = image.size
                if width <= 0 or height <= 0:
                    raise StorageError("invalid image dimensions")
                return content_type, width, height
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
        raise StorageError("file is not a valid supported image") from exc


def strip_image_metadata(path: Path, content_type: str) -> None:
    temporary = path.with_suffix(f"{path.suffix}.sanitized")
    try:
        with Image.open(path) as image:
            sensitive_info = {"exif", "icc_profile", "comment", "xmp", "XML:com.adobe.xmp"}
            textual_metadata = any(isinstance(value, str) for value in image.info.values())
            has_sensitive_metadata = bool(image.getexif()) or bool(
                sensitive_info.intersection(image.info)
            )
            if not has_sensitive_metadata and not textual_metadata:
                return
            image.load()
            if getattr(image, "is_animated", False):
                raise StorageError("animated images with metadata are not accepted")
            if content_type == "image/jpeg":
                image.convert("RGB").save(temporary, format="JPEG", quality=90, optimize=True)
            elif content_type == "image/png":
                image.save(temporary, format="PNG", optimize=True)
            elif content_type == "image/webp":
                image.save(temporary, format="WEBP", quality=90, method=4)
            elif content_type in {"image/heic", "image/heif"}:
                image.save(temporary, format="HEIF", quality=90)
            elif content_type == "image/gif":
                image.save(temporary, format="GIF")
            else:
                raise StorageError("metadata stripping is unsupported for this image type")
        os.replace(temporary, path)
    except (OSError, KeyError) as exc:
        raise StorageError("image metadata could not be removed safely") from exc
    finally:
        temporary.unlink(missing_ok=True)
