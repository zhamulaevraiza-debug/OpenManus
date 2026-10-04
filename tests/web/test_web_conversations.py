"""Conversation CRUD, search, pinning, export and per-user isolation."""

import pytest
from web_helpers import new_conversation, send_message, wait_for_run


pytestmark = pytest.mark.asyncio


async def test_create_list_get_update_conversation(user):
    created = await new_conversation(user)
    assert set(created) == {
        "id",
        "title",
        "mode",
        "pinned",
        "created_at",
        "updated_at",
        "last_message_preview",
        "active_run_id",
    }
    assert created["mode"] == "auto" and created["title"] == ""
    assert created["pinned"] is False and created["active_run_id"] is None

    other = await new_conversation(user, title="  Trip plan ", mode="coder")
    assert other["title"] == "Trip plan" and other["mode"] == "coder"
    assert (
        await user.post("/api/conversations", json={"mode": "x"})
    ).status_code == 400

    listing = (await user.get("/api/conversations")).json()
    assert [c["id"] for c in listing] == [other["id"], created["id"]]

    patched = await user.patch(
        f"/api/conversations/{created['id']}",
        json={"title": "Renamed", "pinned": True, "mode": "chat"},
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "Renamed"
    assert patched.json()["pinned"] is True and patched.json()["mode"] == "chat"
    bad_mode = await user.patch(
        f"/api/conversations/{created['id']}", json={"mode": "nope"}
    )
    assert bad_mode.status_code == 400

    listing = (await user.get("/api/conversations")).json()
    assert listing[0]["id"] == created["id"]  # pinned first

    detail = (await user.get(f"/api/conversations/{created['id']}")).json()
    assert detail["conversation"]["title"] == "Renamed"
    assert detail["messages"] == [] and detail["runs"] == []


async def test_first_message_sets_title_and_preview(user):
    conversation = await new_conversation(user)
    long_text = "Please research the history of the printing press in Europe " * 3
    result = await send_message(user, conversation["id"], long_text)
    await wait_for_run(user, result["run"]["id"])
    detail = (await user.get(f"/api/conversations/{conversation['id']}")).json()
    title = detail["conversation"]["title"]
    assert title.startswith("Please research the history") and len(title) <= 60
    assert title.endswith("…")
    assert detail["conversation"]["last_message_preview"].startswith("Echo: Please")

    # A second message does not change the title.
    result = await send_message(user, conversation["id"], "Another question")
    await wait_for_run(user, result["run"]["id"])
    detail = (await user.get(f"/api/conversations/{conversation['id']}")).json()
    assert detail["conversation"]["title"] == title
    assert [m["role"] for m in detail["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert len(detail["runs"]) == 2


async def test_search_matches_titles_and_message_content(user):
    travel = await new_conversation(user, title="Поездка в Казань")
    code = await new_conversation(user, title="Code review")
    result = await send_message(user, code["id"], "Refactor the PaymentService class")
    await wait_for_run(user, result["run"]["id"])

    async def search(query):
        response = await user.get("/api/conversations", params={"q": query})
        assert response.status_code == 200
        return [c["id"] for c in response.json()]

    assert await search("казань") == [travel["id"]]  # Unicode case-insensitive
    assert await search("paymentservice") == [code["id"]]
    assert await search("100%") == []
    assert set(await search("")) == {travel["id"], code["id"]}


async def test_delete_conversation_removes_data(user, app):
    conversation = await new_conversation(user)
    result = await send_message(user, conversation["id"], "make files")
    await wait_for_run(user, result["run"]["id"])
    upload = await user.post(
        f"/api/conversations/{conversation['id']}/files",
        files={"files": ("a.txt", b"hello")},
    )
    assert upload.status_code == 200
    settings = app.state.services.settings
    me = (await user.get("/api/auth/me")).json()
    workspace = settings.workspace_path(me["id"], conversation["id"])
    assert workspace.exists()

    response = await user.delete(f"/api/conversations/{conversation['id']}")
    assert response.status_code == 204
    assert not workspace.exists()
    assert (
        await user.get(f"/api/conversations/{conversation['id']}")
    ).status_code == 404
    assert (await user.get(f"/api/runs/{result['run']['id']}")).status_code == 404


async def test_export_markdown(user):
    conversation = await new_conversation(user, title="Отчёт по задаче")
    result = await send_message(user, conversation["id"], "Hello **world**")
    await wait_for_run(user, result["run"]["id"])
    response = await user.get(f"/api/conversations/{conversation['id']}/export.md")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''" in disposition
    text = response.text
    assert text.startswith("# Отчёт по задаче")
    assert "**User**" in text and "Hello **world**" in text
    assert "**Assistant**" in text and "Echo: Hello **world**" in text


async def test_other_users_resources_are_not_found(user, make_user):
    conversation = await new_conversation(user, title="private")
    result = await send_message(user, conversation["id"], "secret")
    run_id = result["run"]["id"]
    await wait_for_run(user, run_id)
    upload = await user.post(
        f"/api/conversations/{conversation['id']}/files",
        files={"files": ("secret.txt", b"top secret")},
    )
    assert upload.status_code == 200

    mallory = await make_user("mallory")
    cid = conversation["id"]
    for method, path in (
        ("GET", f"/api/conversations/{cid}"),
        ("PATCH", f"/api/conversations/{cid}"),
        ("DELETE", f"/api/conversations/{cid}"),
        ("GET", f"/api/conversations/{cid}/export.md"),
        ("POST", f"/api/conversations/{cid}/messages"),
        ("POST", f"/api/conversations/{cid}/retry"),
        ("GET", f"/api/conversations/{cid}/files"),
        ("GET", f"/api/conversations/{cid}/files/uploads/secret.txt"),
        ("DELETE", f"/api/conversations/{cid}/files/uploads/secret.txt"),
        ("GET", f"/api/conversations/{cid}/files.zip"),
        ("GET", f"/api/runs/{run_id}"),
        ("GET", f"/api/runs/{run_id}/events"),
        ("GET", f"/api/runs/{run_id}/events.json"),
        ("POST", f"/api/runs/{run_id}/cancel"),
        ("GET", f"/api/runs/{run_id}/artifacts/000001.png"),
    ):
        body = {"content": "x"} if path.endswith("messages") else None
        if method == "PATCH":
            body = {"title": "pwned"}
        response = await mallory.request(method, path, json=body)
        assert response.status_code == 404, (method, path, response.status_code)
    answer = await mallory.post(
        f"/api/runs/{run_id}/answer", json={"question_id": "x", "answer": "y"}
    )
    assert answer.status_code == 404
    upload = await mallory.post(
        f"/api/conversations/{cid}/files", files={"files": ("x.txt", b"x")}
    )
    assert upload.status_code == 404
    assert (await mallory.get("/api/conversations")).json() == []
    # The owner still sees everything unchanged.
    detail = (await user.get(f"/api/conversations/{cid}")).json()
    assert detail["conversation"]["title"] == "private"


async def test_non_admin_cannot_use_admin_endpoints(user):
    for method, path in (
        ("GET", "/api/settings"),
        ("PUT", "/api/settings"),
        ("POST", "/api/settings/test-llm"),
        ("GET", "/api/users"),
        ("POST", "/api/users"),
        ("PATCH", "/api/users/x"),
        ("DELETE", "/api/users/x"),
    ):
        response = await user.request(
            method, path, json={} if method != "GET" else None
        )
        assert response.status_code == 403, (method, path)
    assert (await user.get("/api/status")).json()["is_admin"] is False
