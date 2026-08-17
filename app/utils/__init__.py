"""Utility functions for the application."""

from app.utils.auth_utils import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_verification_token,
    get_password_hash,
    token_fingerprint,
    verify_openai_password,
    verify_password,
)
from app.utils.constants import model_names, models_supported
from app.utils.file_utils import delete_temp_file
from app.utils.rag_cloudmodel_response import get_rag_cloudmodel_response
from app.utils.rag_ollama_response import get_rag_ollama_response
from app.utils.upload_security import (
    AUDIO_MAX_BYTES,
    IMAGE_MAX_BYTES,
    PDF_MAX_BYTES,
    read_upload_limited,
    validate_image_content,
    validate_pdf_content,
    validate_sound_content,
)
from app.utils.utils import (
    convert_numpy_to_python,
    convert_result_to_text,
    extract_rec_texts,
    validate_image_file,
)

__all__ = [
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "generate_verification_token",
    "verify_openai_password",
    "get_password_hash",
    "token_fingerprint",
    "verify_password",
    "convert_numpy_to_python",
    "convert_result_to_text",
    "extract_rec_texts",
    "validate_image_file",
    "delete_temp_file",
    "get_rag_ollama_response",
    "get_rag_cloudmodel_response",
    "models_supported",
    "model_names",
    "AUDIO_MAX_BYTES",
    "IMAGE_MAX_BYTES",
    "PDF_MAX_BYTES",
    "read_upload_limited",
    "validate_image_content",
    "validate_pdf_content",
    "validate_sound_content",
]
