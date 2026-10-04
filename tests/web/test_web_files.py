"""Workspace files API: upload, listing, preview/download, delete, zip, confinement."""

import io
import os
import zipfile

import pytest
from web_helpers import (
    ADMIN,
    ADMIN_PASSWORD,
    login,
    make_client,
    new_conversation,
    running_app,
    send_message,
    wait_for_run,
)


pytestmark = pytest.mark.asyncio

SANDBOX_CSP = (
    "sandbox allow-scripts allow-popups; "
    "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob: https:"
)


async def _workspace(client, app, conversation_id):
    me = (await client.get("/api/auth/me")).json()
    return app.state.services.settings.workspace_path(me["id"], conversation_id)


async def test_upload_list_download_delete(user, app):
    conversation = await new_conversation(user)
    base = f"/api/conversations/{conversation['id']}"
    assert (await user.get(f"{base}/files")).json() == []

    response = await user.post(
        f"{base}/files",
        files=[
            ("files", ("notes.txt", b"hello world", "text/plain")),
            ("files", ("../../escape.md", b"# Title", "text/markdown")),
            ("files", (".hidden", b"secret", "application/octet-stream")),
        ],
    )
    assert response.status_code == 200, response.text
    entries = response.json()
    assert [e["path"] for e in entries] == [
        "uploads/notes.txt",
        "uploads/escape.md",
        "uploads/hidden",
    ]
    note = entries[0]
    assert set(note) == {"path", "name", "is_dir", "size", "modified_at", "mime"}
    assert note["name"] == "notes.txt" and note["size"] == 11
    assert note["is_dir"] is False and note["mime"] == "text/plain"
    assert note["modified_at"].endswith("Z")

    again = await user.post(f"{base}/files", files={"files": ("notes.txt", b"v2")})
    assert again.json()[0]["path"] == "uploads/notes (1).txt"
    custom = await user.post(
        f"{base}/files", files={"files": ("data.csv", b"a,b")}, data={"dir": "data/raw"}
    )
    assert custom.json()[0]["path"] == "data/raw/data.csv"

    workspace = await _workspace(user, app, conversation["id"])
    (workspace / ".git").mkdir()
    (workspace / ".git" / "config").write_text("x")
    (workspace / "node_modules").mkdir()
    (workspace / "node_modules" / "pkg.js").write_text("x")

    listing = (await user.get(f"{base}/files")).json()
    paths = [e["path"] for e in listing]
    assert paths == [
        "data",
        "data/raw",
        "data/raw/data.csv",
        "uploads",
        "uploads/escape.md",
        "uploads/hidden",
        "uploads/notes (1).txt",
        "uploads/notes.txt",
    ]
    assert next(e for e in listing if e["path"] == "data")["is_dir"] is True

    text = await user.get(f"{base}/files/uploads/notes.txt")
    assert text.status_code == 200 and text.content == b"hello world"
    assert text.headers["content-type"] == "text/plain; charset=utf-8"
    assert text.headers["content-disposition"].startswith("inline")
    assert text.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" not in text.headers

    download = await user.get(f"{base}/files/uploads/notes.txt?download=1")
    assert download.headers["content-disposition"].startswith("attachment")

    deleted = await user.delete(f"{base}/files/uploads/notes.txt")
    assert deleted.status_code == 204
    assert (await user.get(f"{base}/files/uploads/notes.txt")).status_code == 404
    assert (await user.delete(f"{base}/files/uploads/notes.txt")).status_code == 404
    assert (await user.delete(f"{base}/files/data")).status_code == 204
    assert not (workspace / "data").exists()


async def test_active_content_is_sandboxed(user, app):
    conversation = await new_conversation(user)
    base = f"/api/conversations/{conversation['id']}"
    workspace = await _workspace(user, app, conversation["id"])
    workspace.mkdir(parents=True)
    (workspace / "page.html").write_text("<script>alert(document.cookie)</script>")
    (workspace / "chart.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (workspace / "feed.xml").write_text("<x/>")
    (workspace / "doc.pdf").write_bytes(b"%PDF-1.4")
    (workspace / "app.js").write_text("alert(1)")
    (workspace / "archive.zip").write_bytes(b"PK")

    html = await user.get(f"{base}/files/page.html")
    assert html.headers["content-type"] == "text/html; charset=utf-8"
    assert html.headers["content-security-policy"] == SANDBOX_CSP
    assert html.headers["content-disposition"].startswith("inline")
    # The app-wide policy must not replace the sandbox policy.
    assert "frame-ancestors" not in html.headers["content-security-policy"]
    svg = await user.get(f"{base}/files/chart.svg")
    assert svg.headers["content-type"] == "image/svg+xml"
    assert svg.headers["content-security-policy"] == SANDBOX_CSP
    xml = await user.get(f"{base}/files/feed.xml")
    assert xml.headers["content-type"].startswith("text/plain")
    assert xml.headers["content-security-policy"] == SANDBOX_CSP
    pdf = await user.get(f"{base}/files/doc.pdf")
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["content-disposition"].startswith("inline")
    script = await user.get(f"{base}/files/app.js")
    assert script.headers["content-type"] == "text/plain; charset=utf-8"
    archive = await user.get(f"{base}/files/archive.zip")
    assert archive.headers["content-disposition"].startswith("attachment")


async def test_path_traversal_is_rejected(user, app, tmp_path):
    conversation = await new_conversation(user)
    base = f"/api/conversations/{conversation['id']}"
    workspace = await _workspace(user, app, conversation["id"])
    workspace.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    os.symlink(outside, workspace / "link")
    os.symlink(outside / "secret.txt", workspace / "secret-link.txt")

    for path in (
        "..%2F..%2F..%2Fetc%2Fpasswd",
        "%2Fetc%2Fpasswd",
        "link/secret.txt",
        "secret-link.txt",
        "uploads/..%2F..%2F..%2Fsecret",
    ):
        response = await user.get(f"{base}/files/{path}")
        assert response.status_code in (400, 404), path
        assert b"secret" not in response.content

    assert (await user.delete(f"{base}/files/link%2F..%2F..%2Fx")).status_code == 400
    assert (await user.delete(f"{base}/files/..%2Fdata")).status_code == 400
    # Deleting a symlink removes the link, never its target.
    assert (await user.delete(f"{base}/files/secret-link.txt")).status_code == 204
    assert (outside / "secret.txt").exists()

    bad_dir = await user.post(
        f"{base}/files", files={"files": ("a.txt", b"a")}, data={"dir": "../up"}
    )
    assert bad_dir.status_code == 400
    via_link = await user.post(
        f"{base}/files", files={"files": ("a.txt", b"a")}, data={"dir": "link"}
    )
    assert via_link.status_code == 400
    assert not (outside / "a.txt").exists()
    listing = (await user.get(f"{base}/files")).json()
    assert all(not e["path"].startswith("link") for e in listing)


async def test_upload_limits(web_settings, fake_runner):
    settings = web_settings.with_overrides(max_upload_mb=1)
    async with running_app(settings, fake_runner) as app:
        async with make_client(app) as client:
            await login(client, ADMIN, ADMIN_PASSWORD)
            conversation = await new_conversation(client)
            base = f"/api/conversations/{conversation['id']}"
            too_big = await client.post(
                f"{base}/files",
                files={"files": ("big.bin", b"x" * (1024 * 1024 + 1))},
            )
            assert too_big.status_code == 413
            assert (await client.get(f"{base}/files")).json() == []
            assert list(settings.uploads_tmp_dir.iterdir()) == []

            many = [("files", (f"f{i}.txt", b"x")) for i in range(21)]
            assert (await client.post(f"{base}/files", files=many)).status_code == 400
            assert list(settings.uploads_tmp_dir.iterdir()) == []

            no_files = await client.post(f"{base}/files", files={"dir": (None, "x")})
            assert no_files.status_code == 400
            not_multipart = await client.post(f"{base}/files", json={"x": 1})
            assert not_multipart.status_code == 400

            ok = await client.post(
                f"{base}/files", files={"files": ("ok.bin", b"x" * 1024 * 1024)}
            )
            assert ok.status_code == 200 and ok.json()[0]["size"] == 1024 * 1024


async def test_zip_download(user, app):
    conversation = await new_conversation(user)
    base = f"/api/conversations/{conversation['id']}"
    await user.post(
        f"{base}/files",
        files=[
            ("files", ("a.txt", b"alpha")),
            ("files", ("b.bin", os.urandom(300000))),
        ],
    )
    workspace = await _workspace(user, app, conversation["id"])
    (workspace / ".env").write_text("SECRET=1")
    response = await user.get(f"{base}/files.zip")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"].startswith("attachment")
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert sorted(archive.namelist()) == ["uploads/a.txt", "uploads/b.bin"]
    assert archive.read("uploads/a.txt") == b"alpha"
    assert len(archive.read("uploads/b.bin")) == 300000

    empty = await new_conversation(user)
    response = await user.get(f"/api/conversations/{empty['id']}/files.zip")
    assert zipfile.ZipFile(io.BytesIO(response.content)).namelist() == []


async def test_attachments_are_validated_and_passed(user, app, fake_runner):
    conversation = await new_conversation(user)
    base = f"/api/conversations/{conversation['id']}"
    await user.post(f"{base}/files", files={"files": ("photo.png", b"\x89PNG")})
    missing = await user.post(
        f"{base}/messages", json={"content": "look", "attachments": ["nope.png"]}
    )
    assert missing.status_code == 400
    escape = await user.post(
        f"{base}/messages", json={"content": "look", "attachments": ["../x"]}
    )
    assert escape.status_code == 400
    result = await send_message(
        user, conversation["id"], "look", attachments=["./uploads/photo.png"]
    )
    assert result["message"]["attachments"] == ["uploads/photo.png"]
    await wait_for_run(user, result["run"]["id"])
    assert fake_runner.calls[-1]["attachments"] == ["uploads/photo.png"]


async def test_stale_staged_uploads_are_removed_at_startup(web_settings, fake_runner):
    web_settings.uploads_tmp_dir.mkdir(parents=True)
    stale = web_settings.uploads_tmp_dir / "leftover"
    stale.write_bytes(b"partial upload")
    async with running_app(web_settings, fake_runner):
        assert not stale.exists()
        assert web_settings.uploads_tmp_dir.is_dir()
