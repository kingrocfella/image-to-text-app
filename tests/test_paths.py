"""Route ownership: app/paths.py, the /v1 mount, and the app-version gate."""

import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.routing import iter_route_contexts
from httpx import AsyncClient

from app.config import override_settings
from app.main import app
from app.paths import API_PREFIX, Api, Web, fill

ROOT = Path(__file__).resolve().parent.parent


def _served() -> set[tuple[str, str]]:
    # FastAPI keeps included routers nested, so app.routes does not list the
    # real routes; walk the contexts instead.
    served = set()
    for context in iter_route_contexts(app.routes):
        route = context.route
        for method in getattr(route, "methods", None) or ():
            served.add((method, context.path))
    return served


def _constants(cls: type) -> list[str]:
    return [v for k, v in vars(cls).items() if k.isupper() and isinstance(v, str)]


def test_every_api_path_is_served_under_v1_and_at_its_legacy_location():
    paths = {path for _method, path in _served()}
    for path in _constants(Api):
        assert f"{API_PREFIX}{path}" in paths, path
        assert path in paths, f"{path} (legacy mount for pre-/v1 builds)"


def test_web_paths_are_never_versioned():
    paths = {path for _method, path in _served()}
    for path in (Web.HEALTH, Web.READY, Web.VERIFY_EMAIL, Web.RESET_PASSWORD_PAGE):
        assert path in paths
        assert f"{API_PREFIX}{path}" not in paths


def test_no_route_path_is_written_as_a_literal():
    """Decorators reference app.paths; a quoted path in app/routes is a bug."""
    literal = re.compile(r"@\w*router\.(get|post|put|delete|patch)\(\s*[\"\']")
    offenders = [
        path.name
        for path in (ROOT / "app" / "routes").glob("*.py")
        if literal.search(path.read_text())
    ]
    assert offenders == []


def test_fill_url_encodes_parameters():
    assert fill(Api.JOB, message_id="a/b c") == "/job/a%2Fb%20c"


def test_the_mobile_route_mirror_is_up_to_date():
    mirror = ROOT.parent / "image-text-react" / "src" / "api" / "routes.generated.ts"
    if not mirror.parent.is_dir():
        pytest.skip("the mobile repository is not checked out beside this one")
    result = subprocess.run(
        [sys.executable, "-m", "scripts.generate_routes", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_old_builds_are_refused_with_426_once_the_minimum_is_raised(
    client: AsyncClient,
):
    body = {"email": "nobody@example.com", "password": "whatever-it-is"}
    with override_settings(minimum_app_version="1.1.0"):
        legacy = await client.post("/auth/login", json=body)
        old = await client.post(
            "/v1/auth/login", json=body, headers={"X-App-Version": "1.0.9"}
        )
        current = await client.post(
            "/v1/auth/login", json=body, headers={"X-App-Version": "1.1.0"}
        )
        health = await client.get("/health")
        verify = await client.get("/auth/verify-email")

    # A build from before /v1 sends no version at all and counts as 0.0.0.
    assert legacy.status_code == 426
    assert old.status_code == 426
    assert old.headers["x-minimum-app-version"] == "1.1.0"
    assert "update" in old.json()["detail"].lower()
    assert current.status_code == 401
    # Health checks and emailed links are never gated.
    assert health.status_code == 200
    assert verify.status_code == 400


@pytest.mark.asyncio
async def test_security_headers_and_request_id_on_api_responses(client: AsyncClient):
    response = await client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-request-id"]
