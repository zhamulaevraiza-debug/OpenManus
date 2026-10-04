"""Single entry point for running a user request (used by the web backend).

``run_task`` must be awaited inside the task that owns the run (with the RunContext
set): agents are created, run and cleaned up in that same task.
"""

import asyncio
import base64
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from app.agent.registry import (
    AgentSpec,
    available_agents,
    dispose_agent,
    get_agent_spec,
    list_agents,
)
from app.context import current_run, emit, resolve_in_workspace
from app.exceptions import WorkspaceViolation
from app.flow.answer import (
    agent_transcript,
    answer_delta_sink,
    generate_final_answer,
    last_agent_reply,
)
from app.flow.history import History, history_messages, normalize_history
from app.flow.planning import PlanningFlow
from app.flow.router import RouteDecision, route
from app.llm import LLM
from app.logger import logger
from app.prompt.answer import CHAT_SYSTEM_PROMPT
from app.schema import Message
from app.utils.images import IMAGE_MIME_BY_EXTENSION


AUTO_MODE = "auto"
CHAT_MODE = "chat"
TEAM_MODE = "team"
AGENT_MODE = "agent"
MAX_IMAGE_ATTACHMENT_BYTES = 5 * 1024 * 1024
USER_SELECTED_REASON = "Mode selected by the user"

# (file name, base64 data) of an image attached to the request
ImageAttachment = Tuple[str, str]


def _validate_mode(mode: str) -> str:
    """Check that ``mode`` can run now; raises ValueError with a user-facing message."""
    if mode in (AUTO_MODE, CHAT_MODE):
        return mode
    if mode == TEAM_MODE:
        if not available_agents(team_only=True):
            raise ValueError("No team agents are available")
        return mode
    try:
        spec = get_agent_spec(mode)
    except ValueError:
        keys = ", ".join(spec.key for spec in list_agents())
        raise ValueError(
            f"Unknown mode '{mode}'. Use auto, chat, team or an agent: {keys}"
        ) from None
    available, reason = spec.is_available()
    if not available:
        raise ValueError(f"Agent '{spec.name}' is not available: {reason}")
    return mode


def _with_attachment_note(request: str, attachments: Sequence[str]) -> str:
    if not attachments:
        return request
    files = "\n".join(f"- {path}" for path in attachments)
    return f"{request}\n\nAttached files (paths relative to the workspace):\n{files}"


def _read_image(path: Path) -> Optional[bytes]:
    if path.stat().st_size > MAX_IMAGE_ATTACHMENT_BYTES:
        return None
    return path.read_bytes()


async def _load_images(attachments: Sequence[str]) -> List[ImageAttachment]:
    """Read image attachments (png/jpg/jpeg/webp/gif up to 5 MB) from the workspace."""
    images: List[ImageAttachment] = []
    for attachment in attachments:
        if Path(attachment).suffix.lower() not in IMAGE_MIME_BY_EXTENSION:
            continue
        try:
            data = await asyncio.to_thread(
                _read_image, resolve_in_workspace(attachment)
            )
        except (WorkspaceViolation, OSError) as e:
            logger.warning(f"Cannot attach image {attachment}: {e}")
            continue
        if data is None:
            logger.warning(f"Image {attachment} is larger than 5 MB, not attached")
            continue
        images.append((Path(attachment).name, base64.b64encode(data).decode("ascii")))
    return images


def _request_messages(prompt: str, images: List[ImageAttachment]) -> List[Message]:
    """The request as user messages: the first image is attached to the request
    itself, further images (one per message) precede it."""
    extra_images = [
        Message.user_message(f"Attached image: {name}", base64_image=data)
        for name, data in images[1:]
    ]
    first_image = images[0][1] if images else None
    return extra_images + [Message.user_message(prompt, base64_image=first_image)]


async def _decide(mode: str, request: str, history: History) -> RouteDecision:
    if mode == AUTO_MODE:
        return await route(request, history, available_agents())
    if mode in (CHAT_MODE, TEAM_MODE):
        decision = RouteDecision(mode, None, USER_SELECTED_REASON)
    else:
        decision = RouteDecision(AGENT_MODE, mode, USER_SELECTED_REASON)
    emit(
        "router.decision",
        mode=decision.mode,
        agent=decision.agent,
        reason=decision.reason,
    )
    return decision


async def _run_chat(
    prompt: str, history: History, images: List[ImageAttachment]
) -> str:
    """Answer with a single (streamed) LLM call."""
    return await LLM().ask(
        history_messages(history) + _request_messages(prompt, images),
        system_msgs=[Message.system_message(CHAT_SYSTEM_PROMPT)],
        on_delta=answer_delta_sink(),
    )


async def _run_agent(
    spec: AgentSpec, prompt: str, history: History, images: List[ImageAttachment]
) -> str:
    """Run one fresh agent on the request, then write the final answer."""
    agent = await spec.factory()
    try:
        seeded = history_messages(history)
        agent.memory.add_messages(seeded + _request_messages(prompt, images))
        await agent.run()
        try:
            return await generate_final_answer(
                prompt, agent_transcript(agent, skip=len(seeded)), history=history
            )
        except Exception as e:
            reply = last_agent_reply(agent, skip=len(seeded))
            if not reply:
                raise
            logger.error(f"Final answer generation failed ({e}); using the last reply")
            return reply
    finally:
        await dispose_agent(agent)


async def _run_team(prompt: str, history: History) -> str:
    """Plan the request and execute it with all available team agents."""
    members = {spec.key: spec for spec in available_agents(team_only=True)}
    return await PlanningFlow(members, history=history).execute(prompt)


def _emit_usage() -> None:
    run = current_run()
    if run is not None:
        emit(
            "usage",
            input_tokens=run.usage.get("input_tokens", 0),
            completion_tokens=run.usage.get("completion_tokens", 0),
        )


async def run_task(
    request: str,
    mode: str = AUTO_MODE,
    history: Optional[List[dict]] = None,
    attachments: Optional[List[str]] = None,
) -> str:
    """Handle one user request and return the final answer (Markdown).

    Args:
        request: The user's message.
        mode: ``auto`` (routed), ``chat``, ``team`` or an agent key.
        history: Prior turns ``[{"role": "user"|"assistant", "content": str}]``.
        attachments: Workspace-relative paths of uploaded files. They are listed in
            the prompt; images are also sent to vision-capable models.

    Emits ``router.decision``, the agent/plan events, ``answer.delta``, ``final``
    and ``usage``. Agents are always cleaned up in this task, also on cancellation.

    Raises:
        ValueError: For an unknown or unavailable mode (user-facing message).
    """
    mode = _validate_mode((mode or AUTO_MODE).strip())
    history = normalize_history(history)
    attachments = [path for path in attachments or [] if path]
    prompt = _with_attachment_note(request, attachments)

    try:
        decision = await _decide(mode, prompt, history)
        images = (
            await _load_images(attachments)
            if attachments and decision.mode != TEAM_MODE and LLM().supports_images
            else []
        )

        if decision.mode == CHAT_MODE:
            answer = await _run_chat(prompt, history, images)
        elif decision.mode == TEAM_MODE:
            answer = await _run_team(prompt, history)
        else:
            answer = await _run_agent(
                get_agent_spec(decision.agent), prompt, history, images
            )

        emit("final", content=answer)
        return answer
    finally:
        _emit_usage()
