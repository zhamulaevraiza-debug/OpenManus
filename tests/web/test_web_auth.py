"""Authentication, sessions, throttling, registration and request guards."""

import pytest
from web_helpers import ADMIN, ADMIN_PASSWORD, login, make_client, running_app

from app.web.security import SESSION_COOKIE


pytestmark = pytest.mark.asyncio


async def test_health_is_public_and_api_requires_login(anon):
    health = await anon.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["version"]
    for path in ("/api/auth/me", "/api/status", "/api/conversations", "/api/agents"):
        response = await anon.get(path)
        assert response.status_code == 401, path
        assert response.json() == {"detail": "Not authenticated"}


async def test_login_sets_http_only_cookie_and_me_returns_user(anon):
    response = await login(anon, ADMIN, ADMIN_PASSWORD)
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    assert "Secure" not in cookie  # plain http with COOKIE_SECURE=auto
    user = response.json()
    assert set(user) == {"id", "username", "is_admin", "disabled", "created_at"}
    assert user["username"] == ADMIN and user["is_admin"] is True
    assert user["created_at"].endswith("Z")

    me = await anon.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["id"] == user["id"]
    status = (await anon.get("/api/status")).json()
    assert status["is_admin"] is True and status["llm_configured"] is True
    assert status["max_concurrent_runs"] == 4 and status["active_runs"] == 0


async def test_cookie_is_secure_behind_https_proxy(app, web_settings):
    async with make_client(app) as client:
        app.state.services.settings = web_settings.with_overrides(trust_proxy=True)
        response = await client.post(
            "/api/auth/login",
            json={"username": ADMIN, "password": ADMIN_PASSWORD},
            headers={"X-Forwarded-Proto": "https"},
        )
        assert "Secure" in response.headers["set-cookie"]


async def test_login_is_case_insensitive_and_wrong_password_fails(anon):
    assert (await login(anon, "ADMIN", ADMIN_PASSWORD)).status_code == 200
    wrong = await anon.post(
        "/api/auth/login", json={"username": ADMIN, "password": "nope-nope"}
    )
    assert wrong.status_code == 401
    unknown = await anon.post(
        "/api/auth/login", json={"username": "ghost", "password": "whatever1"}
    )
    assert unknown.status_code == 401


async def test_logout_clears_cookie(admin):
    response = await admin.post("/api/auth/logout")
    assert response.status_code == 204
    assert f'{SESSION_COOKIE}=""' in response.headers["set-cookie"]
    assert (await admin.get("/api/auth/me")).status_code == 401


async def test_forged_or_garbage_tokens_are_rejected(app):
    async with make_client(app) as client:
        client.cookies.set(SESSION_COOKIE, "not-a-jwt")
        assert (await client.get("/api/auth/me")).status_code == 401
        import jwt

        forged = jwt.encode(
            {"sub": "x", "pwd_v": 1, "exp": 9999999999}, "a-different-secret-key" * 2
        )
        client.cookies.set(SESSION_COOKIE, forged)
        assert (await client.get("/api/auth/me")).status_code == 401


async def test_login_throttle_blocks_after_ten_failures(anon):
    for _ in range(10):
        response = await anon.post(
            "/api/auth/login", json={"username": ADMIN, "password": "bad-password"}
        )
        assert response.status_code == 401
    blocked = await anon.post(
        "/api/auth/login", json={"username": ADMIN, "password": ADMIN_PASSWORD}
    )
    assert blocked.status_code == 429
    # Other usernames from the same address are not affected.
    other = await anon.post(
        "/api/auth/login", json={"username": "someone", "password": "bad-password"}
    )
    assert other.status_code == 401


async def test_disabled_user_cannot_log_in_and_loses_session(admin, make_user):
    bob = await make_user("bob")
    users = (await admin.get("/api/users")).json()
    bob_id = next(u["id"] for u in users if u["username"] == "bob")
    response = await admin.patch(f"/api/users/{bob_id}", json={"disabled": True})
    assert response.status_code == 200 and response.json()["disabled"] is True
    assert (await bob.get("/api/auth/me")).status_code == 401
    relogin = await bob.post(
        "/api/auth/login", json={"username": "bob", "password": "user-password-1"}
    )
    assert relogin.status_code == 403


async def test_password_change_revokes_other_sessions(app, make_user):
    first = await make_user("carol", "first-password")
    async with make_client(app) as second:
        await login(second, "carol", "first-password")
        wrong = await first.post(
            "/api/auth/change-password",
            json={"current_password": "nope-nope", "new_password": "second-password"},
        )
        assert wrong.status_code == 400
        short = await first.post(
            "/api/auth/change-password",
            json={"current_password": "first-password", "new_password": "short"},
        )
        assert short.status_code == 422
        changed = await first.post(
            "/api/auth/change-password",
            json={
                "current_password": "first-password",
                "new_password": "second-password",
            },
        )
        assert changed.status_code == 204
        assert SESSION_COOKIE in changed.headers["set-cookie"]
        assert (await first.get("/api/auth/me")).status_code == 200
        assert (await second.get("/api/auth/me")).status_code == 401
        await login(second, "carol", "second-password")


async def test_registration_toggle(admin, anon):
    assert (await anon.get("/api/auth/config")).json() == {"allow_registration": False}
    closed = await anon.post(
        "/api/auth/register", json={"username": "dave", "password": "dave-password"}
    )
    assert closed.status_code == 403

    enabled = await admin.put("/api/settings", json={"allow_registration": True})
    assert enabled.status_code == 200 and enabled.json()["allow_registration"] is True
    assert (await anon.get("/api/auth/config")).json() == {"allow_registration": True}

    bad_name = await anon.post(
        "/api/auth/register", json={"username": "d", "password": "dave-password"}
    )
    assert bad_name.status_code == 400
    weak = await anon.post(
        "/api/auth/register", json={"username": "dave", "password": "short"}
    )
    assert weak.status_code == 400
    registered = await anon.post(
        "/api/auth/register", json={"username": "Dave", "password": "dave-password"}
    )
    assert registered.status_code == 200
    assert registered.json()["username"] == "dave"
    assert registered.json()["is_admin"] is False
    assert (await anon.get("/api/auth/me")).json()["username"] == "dave"
    taken = await anon.post(
        "/api/auth/register", json={"username": "dave", "password": "dave-password"}
    )
    assert taken.status_code == 409


async def test_cross_origin_mutations_are_rejected(admin):
    evil = await admin.post(
        "/api/conversations", json={}, headers={"Origin": "https://evil.example"}
    )
    assert evil.status_code == 403
    null_origin = await admin.post(
        "/api/conversations", json={}, headers={"Origin": "null"}
    )
    assert null_origin.status_code == 403
    same = await admin.post(
        "/api/conversations", json={}, headers={"Origin": "http://testserver"}
    )
    assert same.status_code == 200
    # Safe methods are not checked.
    listing = await admin.get(
        "/api/conversations", headers={"Origin": "https://evil.example"}
    )
    assert listing.status_code == 200


async def test_trusted_cors_origin_is_accepted(web_settings, fake_runner):
    settings = web_settings.with_overrides(
        cors_origins=("https://ui.example",), data_dir=web_settings.data_dir
    )
    async with running_app(settings, fake_runner) as other:
        async with make_client(other) as client:
            await login(client, ADMIN, ADMIN_PASSWORD)
            response = await client.post(
                "/api/conversations", json={}, headers={"Origin": "https://ui.example"}
            )
            assert response.status_code == 200
            assert (
                response.headers["access-control-allow-origin"] == "https://ui.example"
            )


async def test_non_json_bodies_are_rejected(admin):
    form = await admin.post(
        "/api/conversations",
        content=b"title=x",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert form.status_code == 415
    text = await admin.post(
        "/api/auth/logout", content=b"x", headers={"Content-Type": "text/plain"}
    )
    assert text.status_code == 415


async def test_oversized_json_body_is_rejected(admin):
    response = await admin.post(
        "/api/conversations",
        content=b'{"title": "' + b"x" * (1024 * 1024 + 10) + b'"}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
