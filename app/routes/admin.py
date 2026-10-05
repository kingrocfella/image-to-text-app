"""Read-only admin console (docs/operations.md, docs/security.md).

Registered by ``app/main.py`` only when ADMIN_DASHBOARD_TOKEN is set, at
ADMIN_DASHBOARD_PATH. It shows the same snapshot the operations monitor
alerts on, and changes nothing.
"""

from datetime import datetime
from html import escape

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.database.postgres import AsyncSessionLocal
from app.paths import Admin
from app.services.admin import SESSION_COOKIE, AdminAuth, AdminDenied, AdminLocked
from app.services.ops import Snapshot, take_snapshot
from app.utils.logger import logger
from app.utils.rate_limit import limiter

# The API's default policy is `default-src 'none'`, which would also block the
# console's own inline stylesheet; nothing else is allowed here either.
_NO_STORE = {
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex, nofollow",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    ),
}

_STYLE = """
body{font:15px/1.5 system-ui,sans-serif;margin:0;background:#f8fafc;color:#0f172a}
main{max-width:880px;margin:0 auto;padding:32px 16px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px}
p.muted{color:#64748b;margin:0 0 16px}
table{border-collapse:collapse;width:100%;background:#fff;border:1px solid #e2e8f0}
td{padding:8px 12px;border-top:1px solid #e2e8f0}td.n{text-align:right;
font-variant-numeric:tabular-nums}
.alert{padding:10px 12px;border-radius:6px;margin:6px 0;background:#fef3c7}
.alert.critical{background:#fee2e2}.ok{padding:10px 12px;background:#dcfce7;
border-radius:6px}
form.inline{display:inline}button{font:inherit;padding:6px 14px;cursor:pointer}
label{display:block;margin:12px 0 4px}input{font:inherit;padding:8px;width:100%;
max-width:360px;box-sizing:border-box}
"""


def _page(title: str, body: str) -> str:
    return (
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><main>{body}</main></body></html>"
    )


def _login_page(action: str, message: str = "") -> str:
    notice = f"<p class='alert'>{escape(message)}</p>" if message else ""
    return _page(
        "ScanGenAI admin",
        f"<h1>ScanGenAI admin</h1><p class='muted'>Read-only operations console.</p>"
        f"{notice}<form method='post' action='{escape(action)}'>"
        "<label for='username'>Username</label>"
        "<input id='username' name='username' autocomplete='username' required>"
        "<label for='token'>Token</label>"
        "<input id='token' name='token' type='password' "
        "autocomplete='current-password' required>"
        "<p><button type='submit'>Sign in</button></p></form>",
    )


def _when(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC") if value else "never"


def _console_page(snapshot: Snapshot, logout_action: str) -> str:
    alerts = (
        "".join(
            f"<div class='alert {a.severity}'><strong>{escape(a.severity)}: "
            f"{escape(a.subject)}</strong><br>{escape(a.detail)}</div>"
            for a in snapshot.alerts
        )
        or "<div class='ok'>Nothing needs attention.</div>"
    )
    backup_size = (
        f"{snapshot.last_backup_bytes / 1_048_576:.1f} MB"
        if snapshot.last_backup_bytes
        else "—"
    )
    rows = [
        ("Users", snapshot.users_total),
        ("Verified users", snapshot.users_verified),
        ("Sign-ups, last 7 days", snapshot.signups_7d),
        ("Jobs started, last 24 hours", snapshot.jobs_24h),
        ("Jobs failed, last 24 hours", snapshot.jobs_failed_24h),
        ("Jobs queued for too long", snapshot.jobs_stuck),
        ("Stored PDF collections", snapshot.pdf_collections),
        ("Users at a monthly limit", snapshot.users_at_quota),
    ]
    table = "".join(
        f"<tr><td>{escape(label)}</td><td class='n'>{value:,}</td></tr>"
        for label, value in rows
    )
    return _page(
        "ScanGenAI admin",
        f"<form class='inline' method='post' action='{escape(logout_action)}' "
        "style='float:right'><button type='submit'>Sign out</button></form>"
        f"<h1>ScanGenAI admin</h1><p class='muted'>As of {_when(snapshot.taken_at)}. "
        "Read-only; refresh to update.</p>"
        f"<h2>Alerts</h2>{alerts}"
        f"<h2>Activity</h2><table>{table}</table>"
        f"<h2>Backups</h2><table><tr><td>Newest verified backup</td>"
        f"<td class='n'>{_when(snapshot.last_backup_at)}</td></tr>"
        f"<tr><td>Size</td><td class='n'>{backup_size}</td></tr></table>",
    )


def build_admin_router(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
) -> APIRouter:
    base = settings.admin_dashboard_path
    login_path = base + Admin.LOGIN
    logout_path = base + Admin.LOGOUT
    auth = AdminAuth(settings, session_factory)
    secure = settings.is_production
    router = APIRouter(include_in_schema=False)

    def _with_session(response: Response) -> Response:
        response.set_cookie(
            SESSION_COOKIE,
            auth.issue_session(),
            max_age=settings.admin_auto_logout_minutes * 60,
            path=base,
            httponly=True,
            secure=secure,
            samesite="strict",
        )
        return response

    @router.get(base + Admin.HOME)
    async def console(request: Request) -> Response:
        if not auth.valid_session(request.cookies.get(SESSION_COOKIE)):
            return HTMLResponse(_login_page(login_path), headers=_NO_STORE)
        async with session_factory() as session:
            snapshot = await take_snapshot(session, settings)
        # Sliding expiry: every view extends the idle timeout.
        return _with_session(
            HTMLResponse(_console_page(snapshot, logout_path), headers=_NO_STORE)
        )

    @router.post(login_path)
    @limiter.limit("10/minute")
    async def login(
        request: Request,  # noqa: ARG001 - required by the rate limiter
        username: str = Form(..., max_length=200),
        token: str = Form(..., max_length=500),
    ) -> Response:
        try:
            await auth.authorize(username, token)
        except AdminLocked:
            return HTMLResponse(
                _login_page(login_path, "Too many failed attempts. Try again later."),
                status_code=423,
                headers=_NO_STORE,
            )
        except AdminDenied:
            logger.warning("Admin console sign-in failed")
            return HTMLResponse(
                _login_page(login_path, "Those credentials are not valid."),
                status_code=401,
                headers=_NO_STORE,
            )
        logger.info("Admin console sign-in")
        return _with_session(RedirectResponse(base + Admin.HOME, 303))

    @router.post(logout_path)
    async def logout() -> Response:
        response = RedirectResponse(base + Admin.HOME, 303)
        response.delete_cookie(SESSION_COOKIE, path=base)
        return response

    return router
