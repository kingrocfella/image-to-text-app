"""Worker functions for processing sound-to-text jobs."""

from pathlib import Path
from typing import Dict, Any

import librosa
import numpy as np

from app.utils import delete_temp_file
from app.utils.logger import logger


_MODEL_NAME = "openai/whisper-medium"
_PROCESSOR = None
_MODEL = None
_DEVICE = None


def _load_model():
    """Lazy load the Whisper model and processor."""
    global _PROCESSOR, _MODEL, _DEVICE  # pylint: disable=global-statement

    if _PROCESSOR is None or _MODEL is None:
        import torch
        from transformers import WhisperProcessor, WhisperForConditionalGeneration

        logger.info("Loading Whisper model: %s", _MODEL_NAME)
        _PROCESSOR = WhisperProcessor.from_pretrained(_MODEL_NAME)
        _MODEL = WhisperForConditionalGeneration.from_pretrained(_MODEL_NAME)
        _MODEL.eval()
        _DEVICE = torch.device("cpu")
        _MODEL.to(_DEVICE)  # type: ignore[method-call]
        logger.info("Whisper model loaded successfully")

    return _PROCESSOR, _MODEL, _DEVICE


def _chunk_audio(audio: np.ndarray, chunk_size: int = 30_000):
    """
    Splits audio into overlapping chunks to avoid memory issues.
    """
    chunks = []
    for start in range(0, len(audio), chunk_size):
        end = min(start + chunk_size, len(audio))
        chunks.append(audio[start:end])
    return chunks


def process_sound_job_sync(job_data: Dict[str, Any]) -> Dict[str, Any]:
    """Synchronous implementation of sound-to-text job processing."""
    audio_file_path = job_data["audio_file_path"]
    filename = job_data.get("filename", "audio.wav")

    logger.info("Processing sound-to-text job")

    temp_file_path = None
    try:
        # Lazy load model and processor
        processor, model, device = _load_model()

        # Check if file exists
        if not Path(audio_file_path).exists():
            raise ValueError("Audio file not found")

        temp_file_path = audio_file_path

        # Load audio from file path and resample to 16 kHz
        audio_np, sr = librosa.load(temp_file_path, sr=16000)
        logger.info("Loaded audio with sample rate %d", sr)

        # Split into chunks
        audio_chunks = _chunk_audio(audio_np, chunk_size=32_000)
        logger.info("Split audio into %d chunks", len(audio_chunks))

        # Lazy import torch for no_grad context
        import torch

        transcription = ""
        for i, chunk in enumerate(audio_chunks):
            logger.info("Processing chunk %d/%d", i + 1, len(audio_chunks))
            inputs = processor(chunk, sampling_rate=sr, return_tensors="pt")
            input_features = inputs.input_features.to(device)
            attention_mask = (
                inputs.attention_mask.to(device) if "attention_mask" in inputs else None
            )

            with torch.no_grad():
                predicted_ids = model.generate(
                    input_features,
                    attention_mask=attention_mask,
                    task="transcribe",
                    language="en",
                )

            text = processor.batch_decode(predicted_ids, skip_special_tokens=True)[0]
            transcription += text + " "

        result_text = transcription.strip()
        logger.info("Transcription completed: %d characters", len(result_text))

        return {
            "content": result_text,
            "filename": filename,
        }
    except Exception as exc:
        logger.error(
            "Error processing sound-to-text job: %s",
            type(exc).__name__,
            exc_info=True,
        )
        raise
    finally:
        delete_temp_file(temp_file_path)
