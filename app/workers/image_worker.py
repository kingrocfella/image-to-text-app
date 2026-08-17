"""Worker functions for processing image-to-text jobs."""

from pathlib import Path
from typing import Dict, Any

from paddleocr import PaddleOCR

from app.utils import (
    convert_numpy_to_python,
    convert_result_to_text,
    delete_temp_file,
    extract_rec_texts,
)
from app.utils.logger import logger


# Lazy-loaded OCR instance
_OCR = None


def _get_ocr() -> PaddleOCR:
    """Get or create OCR instance."""
    global _OCR  # pylint: disable=global-statement
    if _OCR is None:
        logger.info("Initializing PaddleOCR...")
        _OCR = PaddleOCR(
            text_detection_model_name="PP-OCRv5_server_det",
            text_recognition_model_name="PP-OCRv5_server_rec",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
        logger.info("PaddleOCR initialized successfully")
    return _OCR


def process_image_job_sync(job_data: Dict[str, Any]) -> Dict[str, Any]:
    """Synchronous implementation of image-to-text job processing."""
    image_file_path = job_data["image_file_path"]
    filename = job_data.get("filename", "image.png")

    logger.info("Processing image-to-text job")

    try:
        # Check if file exists
        if not Path(image_file_path).exists():
            raise ValueError("Image file not found")

        # Get OCR instance
        ocr = _get_ocr()

        # Run OCR prediction
        logger.info("Running OCR")
        result = ocr.predict(image_file_path)

        # Extract only rec_texts from the result
        rec_texts = extract_rec_texts(result)

        # Convert numpy arrays to Python native types for JSON serialization
        serializable_texts = convert_numpy_to_python(rec_texts)
        text_result = convert_result_to_text(serializable_texts)

        logger.info(
            "OCR conversion successful - Extracted %d text segments",
            len(rec_texts),
        )

        return {
            "content": text_result,
            "filename": filename,
            "segments_count": len(rec_texts),
        }
    except Exception as exc:
        logger.error(
            "Error processing image-to-text job: %s",
            type(exc).__name__,
            exc_info=True,
        )
        raise
    finally:
        delete_temp_file(image_file_path)
