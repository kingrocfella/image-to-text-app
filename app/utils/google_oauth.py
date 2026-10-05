"""Verify Google ID tokens for mobile OAuth."""

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from app.config import get_settings


class GoogleOAuthConfigError(ValueError):
    """Raised when the Google OAuth web client ID is missing."""


class _TimeoutGoogleRequest(google_requests.Request):
    """Google auth request wrapper with a bounded network timeout."""

    def __call__(
        self,
        url: str,
        method: str = "GET",
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        **kwargs: object,
    ) -> object:
        return super().__call__(
            url=url,
            method=method,
            body=body,
            headers=headers,
            timeout=(
                get_settings().google_oauth_timeout_s if timeout is None else timeout
            ),
            **kwargs,
        )


def verify_google_id_token(token: str) -> dict[str, object]:
    """Validate a Google ID token issued for the backend's web client ID."""
    audience = get_settings().google_web_client_id
    if not audience:
        raise GoogleOAuthConfigError("Set GOOGLE_WEB_CLIENT_ID")
    request = _TimeoutGoogleRequest()
    claims: dict[str, object] = id_token.verify_oauth2_token(
        token, request, audience=audience
    )
    return claims
