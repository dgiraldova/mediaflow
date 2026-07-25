"""File-size and format validation (spec section 12.1)."""

from __future__ import annotations

SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v"}
SUPPORTED_VIDEO_MIME_TYPES = {
    "video/mp4",
    "video/quicktime",
    "video/webm",
    "video/x-m4v",
}


class UnsupportedMediaError(ValueError):
    """Raised for media that must fail cleanly without retry (spec 11.5)."""


def validate_media_file(
    *,
    filename: str,
    mime_type: str,
    byte_size: int,
    max_upload_bytes: int,
) -> None:
    extension = _extension_of(filename)
    if extension not in SUPPORTED_VIDEO_EXTENSIONS and mime_type not in SUPPORTED_VIDEO_MIME_TYPES:
        raise UnsupportedMediaError(
            f"Unsupported file type '{extension or mime_type}'. "
            f"Supported formats: {', '.join(sorted(SUPPORTED_VIDEO_EXTENSIONS))}."
        )

    if byte_size <= 0:
        raise UnsupportedMediaError("File is empty.")

    if byte_size > max_upload_bytes:
        max_gib = max_upload_bytes / (1024**3)
        raise UnsupportedMediaError(f"File exceeds the maximum upload size of {max_gib:.1f} GiB.")


def _extension_of(filename: str) -> str:
    dot_index = filename.rfind(".")
    return filename[dot_index:].lower() if dot_index != -1 else ""
