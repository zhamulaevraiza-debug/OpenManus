"""Request router: answer directly (chat), delegate to one agent, or use the team."""

import json
from dataclasses import dataclass
from typing import Optional, Sequence

from app.agent.registry import DEFAULT_AGENT_KEY, AgentSpec
from app.context import emit
from app.flow.history import History, format_history
from app.llm import LLM
from app.logger import logger
from app.prompt.router import ROUTER_REQUEST_PROMPT, ROUTER_SYSTEM_PROMPT
from app.schema import Message, ToolChoice
from app.utils.text import truncate


MODES = ("chat", "agent", "team")
ROUTER_HISTORY_CHARS = 3000
ROUTER_REQUEST_CHARS = 4000
REASON_MAX_CHARS = 300


@dataclass(frozen=True)
class RouteDecision:
    """How a request is handled: ``mode`` chat/agent/team, ``agent`` key for agent mode."""

    mode: str
    agent: Optional[str]
    reason: str


def _route_tool(agent_keys: Sequence[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "route",
            "description": "Choose how to handle the latest user request.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": list(MODES)},
                    "agent": {
                        "type": "string",
                        "enum": list(agent_keys),
                        "description": "Key of the agent; required when mode is 'agent'.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "One short sentence explaining the choice.",
                    },
                },
                "required": ["mode", "reason"],
            },
        },
    }


async def _ask_router(
    request: str, history: History, agents: Sequence[AgentSpec]
) -> RouteDecision:
    keys = [spec.key for spec in agents]
    system_prompt = ROUTER_SYSTEM_PROMPT.format(
        agents="\n".join(f"- {spec.key}: {spec.description}" for spec in agents)
    )
    user_prompt = ROUTER_REQUEST_PROMPT.format(
        history=format_history(history, ROUTER_HISTORY_CHARS),
        request=truncate(request, ROUTER_REQUEST_CHARS),
    )
    response = await LLM().ask_tool(
        [Message.user_message(user_prompt)],
        system_msgs=[Message.system_message(system_prompt)],
        tools=[_route_tool(keys)],
        tool_choice=ToolChoice.REQUIRED,
        temperature=0,
    )
    calls = [
        call
        for call in (response.tool_calls if response else None) or []
        if call.function.name == "route"
    ]
    if not calls:
        raise ValueError("the model did not call the route function")
    arguments = json.loads(calls[0].function.arguments or "{}")

    mode = str(arguments.get("mode", "")).strip().lower()
    reason = truncate(str(arguments.get("reason") or "").strip(), REASON_MAX_CHARS)
    if mode == "chat":
        return RouteDecision("chat", None, reason)
    if mode == "team":
        if sum(1 for spec in agents if spec.team_member) < 2:
            raise ValueError("fewer than two team agents are available")
        return RouteDecision("team", None, reason)
    if mode == "agent":
        agent = str(arguments.get("agent") or "").strip().lower()
        if agent not in keys:
            raise ValueError(f"unknown agent '{agent}'")
        return RouteDecision("agent", agent, reason)
    raise ValueError(f"unknown mode '{mode}'")


async def route(
    request: str, history: Optional[History], agents: Sequence[AgentSpec]
) -> RouteDecision:
    """Decide how to handle ``request`` with a single LLM tool call.

    Falls back to the general-purpose agent on any error. Emits ``router.decision``.

    Args:
        request: The user's request.
        history: Earlier conversation turns.
        agents: The agents that are currently available.
    """
    try:
        decision = await _ask_router(request, history or [], agents)
    except Exception as e:
        logger.warning(f"Routing failed ({e}); using the general-purpose agent")
        decision = RouteDecision(
            "agent",
            DEFAULT_AGENT_KEY,
            "Routing was not possible; using the general-purpose agent",
        )
    emit(
        "router.decision",
        mode=decision.mode,
        agent=decision.agent,
        reason=decision.reason,
    )
    return decision
