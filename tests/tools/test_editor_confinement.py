"""Workspace confinement and behaviour of str_replace_editor / file operators."""

import os

import pytest

from app.context import RunContext, reset_run, set_run
from app.tool import file_operators
from app.tool.file_operators import LocalFileOperator
from app.tool.str_replace_editor import StrReplaceEditor
from app.tool.tool_collection import ToolCollection


pytestmark = pytest.mark.asyncio


async def edit(editor: StrReplaceEditor = None, **kwargs) -> str:
    tools = ToolCollection(editor or StrReplaceEditor())
    return str(await tools.execute(name="str_replace_editor", tool_input=kwargs))


async def test_relative_paths_resolve_in_workspace(run_ctx):
    out = await edit(command="create", path="notes/todo.md", file_text="a\nb\n")
    target = run_ctx.workspace / "notes" / "todo.md"
    assert f"File created successfully at: {target}" in out
    assert target.read_text() == "a\nb\n"


async def test_absolute_path_inside_workspace_is_allowed(run_ctx):
    target = run_ctx.workspace / "abs.txt"
    await edit(command="create", path=str(target), file_text="x")
    assert target.read_text() == "x"


@pytest.mark.parametrize(
    "path", ["/etc/passwd", "../outside.txt", "sub/../../outside.txt", "~/x"]
)
async def test_paths_outside_workspace_are_rejected(run_ctx, path):
    out = await edit(command="view", path=path)
    assert out.startswith("Error: Access denied")


async def test_create_outside_workspace_is_rejected(run_ctx, tmp_path):
    out = await edit(command="create", path=str(tmp_path / "evil.txt"), file_text="x")
    assert out.startswith("Error: Access denied")
    assert not (tmp_path / "evil.txt").exists()


async def test_symlink_escape_is_rejected(run_ctx, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    os.symlink(tmp_path, run_ctx.workspace / "link")
    out = await edit(command="view", path="link/secret.txt")
    assert out.startswith("Error: Access denied")
    assert "top secret" not in out


async def test_edit_cycle(run_ctx):
    editor = StrReplaceEditor()
    target = run_ctx.workspace / "f.py"
    await edit(
        editor, command="create", path="f.py", file_text="def f():\n    return 1\n"
    )
    out = await edit(
        editor,
        command="str_replace",
        path="f.py",
        old_str="return 1",
        new_str="return 2",
    )
    assert "has been edited" in out
    await edit(editor, command="insert", path="f.py", insert_line=0, new_str="# header")
    assert target.read_text().startswith("# header\n")
    await edit(editor, command="undo_edit", path=str(target))
    await edit(editor, command="undo_edit", path="f.py")
    assert "return 1" in target.read_text()
    view = await edit(editor, command="view", path="f.py", view_range=[2, 2])
    assert "return 1" in view


async def test_directory_listing_quotes_shell_metacharacters(run_ctx):
    weird = run_ctx.workspace / "dir; touch PWNED $(touch PWNED2)"
    weird.mkdir()
    (weird / "inner.txt").write_text("x")
    out = await edit(command="view", path=weird.name)
    assert "inner.txt" in out
    assert not (run_ctx.workspace / "PWNED").exists()
    assert not (run_ctx.workspace / "PWNED2").exists()


async def test_file_history_is_per_instance(run_ctx):
    first, second = StrReplaceEditor(), StrReplaceEditor()
    await first.execute(command="create", path="h.txt", file_text="one")
    assert first._file_history is not second._file_history
    assert first._sandbox_operator is None and second._sandbox_operator is None
    out = await ToolCollection(second).execute(
        name="str_replace_editor",
        tool_input={"command": "undo_edit", "path": "h.txt"},
    )
    assert "No edit history" in str(out)


async def test_description_mentions_workspace_relative_paths():
    tool = StrReplaceEditor()
    assert (
        "relative to the workspace"
        in tool.parameters["properties"]["path"]["description"]
    )
    assert "Relative paths are resolved against the workspace" in tool.description


async def test_unrestricted_context_allows_outside_paths(workspace, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("free")
    token = set_run(
        RunContext(run_id="r", workspace=workspace, restrict_to_workspace=False)
    )
    try:
        out = await edit(command="view", path=str(outside))
    finally:
        reset_run(token)
    assert "free" in out


async def test_local_operator_runs_commands_in_workspace(monkeypatch, run_ctx):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    code, out, err = await LocalFileOperator().run_command("pwd; env")
    assert code == 0
    assert out.splitlines()[0] == str(run_ctx.workspace)
    assert "OPENAI_API_KEY" not in out


async def test_local_operator_timeout(run_ctx):
    with pytest.raises(TimeoutError):
        await LocalFileOperator().run_command("sleep 10", timeout=0.3)


async def test_new_files_are_handed_to_exec_user(monkeypatch, run_ctx):
    chowned = []
    monkeypatch.setattr(file_operators, "exec_user_ids", lambda: (4242, 4243))
    monkeypatch.setattr(
        file_operators.os,
        "chown",
        lambda path, uid, gid, follow_symlinks=True: chowned.append(
            (str(path), uid, gid)
        ),
    )
    target = run_ctx.workspace / "a" / "b" / "c.txt"
    await LocalFileOperator().write_file(target, "data")
    assert [path for path, _, _ in chowned] == [
        str(run_ctx.workspace / "a"),
        str(run_ctx.workspace / "a" / "b"),
        str(target),
    ]
    assert {(uid, gid) for _, uid, gid in chowned} == {(4242, 4243)}
