"""File utility functions for the application."""

from pathlib import Path
from typing import Optional

from app.utils.logger import logger


def delete_temp_file(file_path: Optional[str | Path], silent: bool = False) -> bool:
    """Delete a temporary file safely.

    Args:
        file_path: Path to the file to delete. Can be str or Path object.
                   If None or empty, returns False without error.
        silent: If True, suppresses warning logs on failure. Default False.

    Returns:
        True if file was deleted successfully, False otherwise.
    """
    if not file_path:
        return False

    try:
        path = Path(file_path) if isinstance(file_path, str) else file_path

        if path.exists():
            path.unlink()
            logger.debug("Deleted temporary file")
            return True
        return False
    except Exception as exc:
        if not silent:
            logger.warning("Failed to delete temporary file: %s", type(exc).__name__)
        return False
