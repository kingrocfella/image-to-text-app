"""Utility functions for image-to-text conversion."""

from pathlib import Path

import numpy as np
from fastapi import HTTPException, UploadFile

from app.utils.logger import logger


def convert_numpy_to_python(obj):
    """Convert numpy arrays and types to Python native types for JSON serialization."""
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.integer, np.floating)):
        return obj.item()
    if isinstance(obj, (list, tuple)):
        return [convert_numpy_to_python(item) for item in obj]
    return obj


def extract_rec_texts(result):
    """Extract rec_texts from PaddleOCR result."""
    rec_texts = []
    if isinstance(result, list) and len(result) > 0:
        first_item = result[0]
        if isinstance(first_item, dict):
            rec_texts = first_item.get("rec_texts", [])
    elif isinstance(result, dict):
        rec_texts = result.get("rec_texts", [])
    return rec_texts


def convert_result_to_text(result):
    """Convert result to text."""
    if isinstance(result, list):
        return " ".join(result)
    if isinstance(result, dict):
        return " ".join(result.get("rec_texts", []))
    return ""


def validate_image_file(file: UploadFile) -> None:
    """Validate that the uploaded file is an image."""

    # Allowed image extensions
    allowed_extensions = {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".bmp",
        ".webp",
        ".tiff",
        ".tif",
    }

    # Allowed MIME types
    allowed_mime_types = {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/gif",
        "image/bmp",
        "image/webp",
        "image/tiff",
    }

    # Check file extension
    if file.filename:
        file_extension = Path(file.filename).suffix.lower()
        if file_extension not in allowed_extensions:
            logger.warning("Invalid image extension: %s", file_extension)
            raise HTTPException(
                status_code=400,
                detail="Invalid file type.",
            )

    # Check MIME type
    if file.content_type and file.content_type not in allowed_mime_types:
        logger.warning("Invalid image MIME type: %s", file.content_type)
        raise HTTPException(
            status_code=400,
            detail=f"Invalid MIME type. Expected image file, got: {file.content_type}",
        )


def validate_sound_file(file: UploadFile) -> bool:
    """Validate that the uploaded file is a sound."""

    # Allowed sound extensions
    allowed_extensions = {
        ".mp3",
        ".wav",
        ".ogg",
        ".m4a",
        ".aac",
        ".flac",
        ".wma",
        ".mp4",
    }

    # Allowed MIME types
    allowed_mime_types = {
        "audio/mpeg",
        "audio/wav",
        "audio/ogg",
        "audio/aac",
        "audio/flac",
        "audio/wma",
        "audio/mp4",
        "audio/mp3",
        "audio/x-m4a",
    }

    # Check file extension
    if file.filename:
        file_extension = Path(file.filename).suffix.lower()
        if file_extension not in allowed_extensions:
            logger.warning("Invalid sound extension: %s", file_extension)
            raise HTTPException(
                status_code=400,
                detail="Invalid sound file.",
            )

    # Check MIME type
    if file.content_type and file.content_type not in allowed_mime_types:
        logger.warning("Invalid sound MIME type: %s", file.content_type)
        raise HTTPException(
            status_code=400,
            detail="Invalid sound file.",
        )

    return True
