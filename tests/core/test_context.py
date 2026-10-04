import os
import pwd

import pytest

from app.context import (
    RunContext,
    prepare_workspace,
    reset_run,
    resolve_in_workspace,
    set_run,
)
from app.exceptions import WorkspaceViolation


def _bind(tmp_path):
    workspace = prepare_workspace(tmp_path / "ws")
    return workspace, set_run(RunContext(run_id="r1", workspace=workspace))


def test_relative_paths_resolve_inside_workspace(tmp_path):
    workspace, token = _bind(tmp_path)
    try:
        assert resolve_in_workspace("notes/a.txt") == workspace / "notes" / "a.txt"
    finally:
        reset_run(token)


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", "~/x"])
def test_paths_outside_workspace_are_rejected(tmp_path, path):
    _, token = _bind(tmp_path)
    try:
        with pytest.raises(WorkspaceViolation):
            resolve_in_workspace(path)
    finally:
        reset_run(token)


def test_symlink_escape_is_rejected(tmp_path):
    workspace, token = _bind(tmp_path)
    try:
        os.symlink("/etc/passwd", workspace / "link")
        with pytest.raises(WorkspaceViolation):
            resolve_in_workspace("link")
    finally:
        reset_run(token)


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() != 0, reason="needs root to chown"
)
def test_prepare_workspace_does_not_chown_hard_links(tmp_path, monkeypatch):
    nobody = pwd.getpwnam("nobody")
    monkeypatch.setenv("OPENMANUS_EXEC_USER", "nobody")

    secret = tmp_path / "secret.key"
    secret.write_text("top secret")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    os.link(secret, workspace / "stolen.key")
    (workspace / "own.txt").write_text("mine")

    prepare_workspace(workspace)

    assert os.stat(secret).st_uid == 0
    assert os.stat(workspace / "own.txt").st_uid == nobody.pw_uid
    assert os.stat(workspace).st_uid == nobody.pw_uid
