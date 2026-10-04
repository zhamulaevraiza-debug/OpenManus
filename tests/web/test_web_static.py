"""SPA static hosting, cache headers and security headers."""

import pytest
from web_helpers import make_client, running_app


pytestmark = pytest.mark.asyncio

APP_CSP = (
    "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
    "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
    "frame-src 'self' blob:; worker-src 'self'; manifest-src 'self'; "
    "base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
)


def assert_security_headers(response):
    assert response.headers["content-security-policy"] == APP_CSP
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "same-origin"
    assert (
        response.headers["permissions-policy"]
        == "camera=(self), microphone=(self), geolocation=()"
    )
    assert response.headers["x-frame-options"] == "SAMEORIGIN"


async def test_spa_files_cache_headers_and_fallback(anon):
    index = await anon.get("/")
    assert index.status_code == 200 and "<title>OpenManus" in index.text
    assert index.headers["cache-control"] == "no-cache"
    assert_security_headers(index)

    asset = await anon.get("/assets/app-123.js")
    assert asset.status_code == 200 and asset.text == "console.log(1)"
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "javascript" in asset.headers["content-type"]

    for path in ("/sw.js", "/manifest.webmanifest", "/index.html"):
        response = await anon.get(path)
        assert response.status_code == 200, path
        assert response.headers["cache-control"] == "no-cache", path
    manifest = await anon.get("/manifest.webmanifest")
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    icon = await anon.get("/icon.png")
    assert icon.headers["cache-control"] == "public, max-age=3600"

    for route in ("/chat/123", "/settings/model", "/login"):
        fallback = await anon.get(route)
        assert fallback.status_code == 200 and "<title>OpenManus" in fallback.text
        assert fallback.headers["cache-control"] == "no-cache"

    assert (await anon.get("/assets/missing-1.js")).status_code == 404
    assert (await anon.get("/missing.png")).status_code == 404
    traversal = await anon.get("/..%2F..%2F..%2Fetc%2Fpasswd")
    assert "root:" not in traversal.text
    head = await anon.head("/")
    assert head.status_code == 200


async def test_unknown_api_paths_are_json_404(anon):
    for path in ("/api/nope", "/api", "/api/conversations/x/y/z"):
        response = await anon.get(path)
        assert response.status_code in (401, 404), path
        assert response.headers["content-type"] == "application/json"
    response = await anon.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert_security_headers(response)


async def test_missing_static_dir_shows_note(web_settings, fake_runner, tmp_path):
    settings = web_settings.with_overrides(static_dir=tmp_path / "no-build")
    async with running_app(settings, fake_runner) as app:
        async with make_client(app) as client:
            response = await client.get("/")
            assert response.status_code == 200
            assert "web interface has not been built" in response.text
            assert response.headers["content-type"].startswith("text/html")
            assert (await client.get("/api/health")).status_code == 200


async def test_api_responses_carry_security_headers(admin):
    response = await admin.get("/api/conversations")
    assert_security_headers(response)
