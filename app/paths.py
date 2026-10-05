"""Every path the server serves, in one place (AGENTS.md §3).

Never write a route path as a literal anywhere else: route registration,
redirects, emails and HTML forms all reference these constants.
``scripts/generate_routes.py`` turns this module into the mobile app's
``src/api/routes.generated.ts``; `make check-routes` fails when that mirror
is stale.

- ``Api`` paths are the JSON API the app calls. They are served under
  ``API_PREFIX`` and, until pre-``/v1`` installs are retired, at their old
  unversioned location too.
- ``Web`` paths are opened by browsers and email links. Emails already
  delivered point at them, so they are never versioned or moved.
"""

from urllib.parse import quote

API_PREFIX = "/v1"


class Api:
    AUTH_REGISTER = "/auth/register"
    AUTH_RESEND_VERIFICATION = "/auth/resend-verification"
    AUTH_LOGIN = "/auth/login"
    AUTH_GOOGLE = "/auth/google"
    AUTH_APPLE = "/auth/apple"
    AUTH_REFRESH = "/auth/refresh"
    AUTH_LOGOUT = "/auth/logout"
    AUTH_FORGOT_PASSWORD = "/auth/forgot-password"
    AUTH_ACCOUNT = "/auth/account"

    ME = "/me"

    IMAGE_TO_TEXT = "/convert/image/text"
    SOUND_TO_TEXT = "/convert/sound/text"
    PDF_RESPONSE = "/pdf/get/response"
    JOB = "/job/{message_id}"

    CLIENT_LOGS = "/client-logs"

    BILLING_VERIFY = "/billing/purchases/verify"
    BILLING_RECOVER = "/billing/purchases/recover"


class Web:
    HEALTH = "/health"
    READY = "/ready"

    # Links sent in emails.
    VERIFY_EMAIL = "/auth/verify-email"
    RESET_PASSWORD_PAGE = "/auth/reset-password"
    RESET_PASSWORD_FORM = "/auth/reset-password/form"

    # App Store Server Notifications V2 (server to server; never version-gated).
    APPLE_NOTIFICATIONS = "/webhooks/apple"

    # FastAPI's interactive docs; served outside production only.
    DOCS = "/docs"
    DOCS_OAUTH_REDIRECT = "/docs/oauth2-redirect"
    REDOC = "/redoc"
    OPENAPI = "/openapi.json"


class Admin:
    """Admin console paths, relative to ADMIN_DASHBOARD_PATH.

    The console's own location is configuration, not a constant: an
    unguessable path is part of its protection (docs/security.md).
    """

    HOME = ""
    LOGIN = "/login"
    LOGOUT = "/logout"


def fill(template: str, **params: object) -> str:
    """Substitute URL-encoded path parameters into a path template."""
    return template.format(**{k: quote(str(v), safe="") for k, v in params.items()})
