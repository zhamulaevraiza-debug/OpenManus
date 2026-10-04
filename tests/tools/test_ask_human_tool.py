"""ask_human delegates to the run's human-input provider."""

import pytest

from app.context import RunContext, reset_run, set_run
from app.tool.ask_human import NO_HUMAN_AVAILABLE, AskHuman


pytestmark = pytest.mark.asyncio


async def test_uses_run_context_provider(workspace):
    questions = []

    async def provider(question: str) -> str:
        questions.append(question)
        return "  blue  "

    token = set_run(RunContext(run_id="r", workspace=workspace, ask_human=provider))
    try:
        answer = await AskHuman().execute(inquire="Favourite colour?")
    finally:
        reset_run(token)
    assert questions == ["Favourite colour?"]
    assert answer == "blue"


async def test_run_without_provider_does_not_read_stdin(monkeypatch, run_ctx):
    def fail(*args):
        raise AssertionError("input() must not be used inside a web run")

    monkeypatch.setattr("builtins.input", fail)
    assert await AskHuman().execute(inquire="anyone?") == NO_HUMAN_AVAILABLE


async def test_cli_falls_back_to_input(monkeypatch):
    prompts = []
    monkeypatch.setattr(
        "builtins.input", lambda prompt: prompts.append(prompt) or " yes "
    )
    assert await AskHuman().execute(inquire="Continue?") == "yes"
    assert "Continue?" in prompts[0]
