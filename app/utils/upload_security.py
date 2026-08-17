"""Bounded, content-based validation for untrusted uploads."""

import os
import warnings
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from PIL import Image
from pypdf import PdfReader
from pypdf.errors import PdfReadError


IMAGE_MAX_BYTES = int(os.getenv("IMAGE_MAX_BYTES", str(10 * 1024 * 1024)))
AUDIO_MAX_BYTES = int(os.getenv("AUDIO_MAX_BYTES", str(20 * 1024 * 1024)))
PDF_MAX_BYTES = int(os.getenv("PDF_MAX_BYTES", str(20 * 1024 * 1024)))
IMAGE_MAX_PIXELS = int(os.getenv("IMAGE_MAX_PIXELS", "40000000"))
IMAGE_MAX_FRAMES = int(os.getenv("IMAGE_MAX_FRAMES", "20"))
PDF_MAX_PAGES = int(os.getenv("PDF_MAX_PAGES", "100"))

for name, value in {
    "IMAGE_MAX_BYTES": IMAGE_MAX_BYTES,
    "AUDIO_MAX_BYTES": AUDIO_MAX_BYTES,
    "PDF_MAX_BYTES": PDF_MAX_BYTES,
    "IMAGE_MAX_PIXELS": IMAGE_MAX_PIXELS,
    "IMAGE_MAX_FRAMES": IMAGE_MAX_FRAMES,
    "PDF_MAX_PAGES": PDF_MAX_PAGES,
}.items():
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")


async def read_upload_limited(upload: UploadFile, max_bytes: int) -> bytes:
    """Read an upload incrementally without ever retaining more than its limit."""
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(64 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Uploaded file is too large.",
            )
        chunks.append(chunk)
    if total == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded file is empty.",
        )
    return b"".join(chunks)


def validate_image_content(content: bytes) -> None:
    """Require a decodable, bounded image in an explicitly supported format."""
    allowed_formats = {"BMP", "GIF", "JPEG", "PNG", "TIFF", "WEBP"}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                detected_format = image.format
                width, height = image.size
                frames = getattr(image, "n_frames", 1)
                if detected_format not in allowed_formats:
                    raise ValueError("unsupported image format")
                if width <= 0 or height <= 0 or width * height > IMAGE_MAX_PIXELS:
                    raise ValueError("image dimensions exceed limit")
                if frames > IMAGE_MAX_FRAMES:
                    raise ValueError("image frame count exceeds limit")
                image.verify()
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        ValueError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or unsafe image file.",
        ) from exc


def validate_pdf_content(content: bytes) -> None:
    """Require a parseable, unencrypted PDF with a bounded page count."""
    if not content.startswith(b"%PDF-"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid PDF file.",
        )
    try:
        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise ValueError("encrypted PDFs are not supported")
        page_count = len(reader.pages)
        if page_count < 1 or page_count > PDF_MAX_PAGES:
            raise ValueError("PDF page count exceeds limit")
    except (OSError, PdfReadError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or unsafe PDF file.",
        ) from exc


def validate_sound_content(content: bytes, filename: str | None) -> None:
    """Reject audio whose bytes do not match a supported container signature."""
    suffix = Path(filename or "").suffix.lower()
    signatures = {
        ".aac": lambda data: len(data) >= 2
        and data[0] == 0xFF
        and data[1] & 0xF6 == 0xF0,
        ".flac": lambda data: data.startswith(b"fLaC"),
        ".m4a": lambda data: len(data) >= 12 and data[4:8] == b"ftyp",
        ".mp3": lambda data: data.startswith(b"ID3")
        or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0),
        ".mp4": lambda data: len(data) >= 12 and data[4:8] == b"ftyp",
        ".ogg": lambda data: data.startswith(b"OggS"),
        ".wav": lambda data: len(data) >= 12
        and data.startswith(b"RIFF")
        and data[8:12] == b"WAVE",
        ".wma": lambda data: data.startswith(
            bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c")
        ),
    }
    validator = signatures.get(suffix)
    if validator is None or not validator(content):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or unsafe sound file.",
        )
