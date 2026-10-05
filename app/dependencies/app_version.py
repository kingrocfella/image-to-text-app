"""Refuse API requests from app builds older than MINIMUM_APP_VERSION."""

import re

from fastapi import HTTPException, Request, status

from app.config import get_settings

APP_VERSION_HEADER = "X-App-Version"
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse_version(value: str | None) -> tuple[int, int, int]:
    """``"1.2.3"`` -> ``(1, 2, 3)``. Missing or malformed counts as 0.0.0."""
    match = _VERSION_RE.match((value or "").strip())
    if not match:
        return (0, 0, 0)
    major, minor, patch = (int(part) for part in match.groups())
    return (major, minor, patch)


async def require_supported_app_version(request: Request) -> None:
    """426 when the calling build is older than the configured minimum.

    Builds from before ``/v1`` send no version header, so they count as 0.0.0
    and are retired by raising MINIMUM_APP_VERSION above 0.0.0.
    """
    minimum = get_settings().minimum_app_version
    if parse_version(request.headers.get(APP_VERSION_HEADER)) < parse_version(minimum):
        raise HTTPException(
            status_code=status.HTTP_426_UPGRADE_REQUIRED,
            detail="Please update ScanGenAI to the latest version to continue.",
            headers={"X-Minimum-App-Version": minimum},
        )
