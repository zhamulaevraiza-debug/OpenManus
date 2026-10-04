"""Helpers for base64 encoded images exchanged with tools and LLMs."""

import base64
import binascii
from typing import Union


DEFAULT_IMAGE_MIME = "image/jpeg"

# File extensions of images that can be attached to LLM requests.
IMAGE_MIME_BY_EXTENSION = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

# 32 base64 characters decode to 24 bytes: enough for every signature below.
_B64_HEAD_CHARS = 32


def detect_image_mime(
    data: Union[bytes, str], default: str = DEFAULT_IMAGE_MIME
) -> str:
    """Detect the MIME type of a PNG/JPEG/WebP/GIF image from its magic bytes.

    Args:
        data: Raw image bytes or base64 text (without a ``data:`` prefix).
        default: Returned when the format is not recognised.
    """
    if isinstance(data, str):
        head = data.strip()[:_B64_HEAD_CHARS]
        head = head[: len(head) - len(head) % 4]
        try:
            data = base64.b64decode(head)
        except (binascii.Error, ValueError):
            return default

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return default


def image_data_url(base64_image: str) -> str:
    """Build a ``data:`` URL for a base64 image with the detected MIME type."""
    return f"data:{detect_image_mime(base64_image)};base64,{base64_image}"
