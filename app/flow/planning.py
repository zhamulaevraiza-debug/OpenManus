"""Team flow: plan a request, run every step on the tagged agent, write the answer.

The planner assigns each step to one team member with a ``[key]`` tag. Steps run
sequentially; a member given as an :class:`AgentSpec` gets a fresh agent for every
step (created and cleaned up in the flow's task). A failing step is marked
``blocked`` and the flow moves on, so a plan always terminates. Each step's result
summary is stored as the step's notes and handed to the following steps and to the
final answer. Progress is reported with ``plan.*`` events.
"""

import json
import re
import uuid
from enum import Enum
from typing import Dict, List, Optional, Tuple

from pydantic import Field, PrivateAttr

from app.agent.base import BaseAgent
from app.agent.registry import DEFAULT_AGENT_KEY, AgentSpec, dispose_agent
from app.config import config
from app.context import emit, get_workspace
from app.flow.answer import agent_transcript, generate_final_answer, last_agent_reply
from app.flow.base import BaseFlow, FlowAgent
from app.flow.history import History, format_history, normalize_history
from app.llm import LLM
from app.logger import logger
from app.prompt.planning import (
    PLAN_REQUEST_PROMPT,
    PLANNER_SYSTEM_PROMPT,
    STEP_PROMPT,
    TEAM_RECORD_TEMPLATE,
)
from app.schema import Message, ToolChoice
from app.tool.planning import PlanningTool
from app.utils.text import truncate


# "[key] text", tolerating a leading enumeration such as "1." or "Step 2:"
STEP_TAG_RE = re.compile(
    r"^\s*(?:(?:step\s*)?\d+\s*[.):-]\s*)?\[([A-Za-z0-9_\-]+)\]\s*", re.IGNORECASE
)
STEP_SUMMARY_MAX_CHARS = 2000
PLAN_ATTEMPTS = 2
TITLE_MAX_CHARS = 80

# Alternative tags models tend to use, mapped to registry keys.
_TAG_ALIASES = {
    "swe": "coder",
    "code": "coder",
    "coding": "coder",
    "programmer": "coder",
    "developer": "coder",
    "engineer": "coder",
    "data_analysis": "data_analyst",
    "analyst": "data_analyst",
    "analysis": "data_analyst",
    "data": "data_analyst",
    "research": "researcher",
    "search": "researcher",
    "web": "browser",
    "browse": "browser",
    "browser_agent": "browser",
    "write": "writer",
    "writing": "writer",
    "author": "writer",
    "general": "manus",
    "default": "manus",
}


class PlanStepStatus(str, Enum):
    """Enum class defining possible statuses of a plan step"""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"

    @classmethod
    def get_all_statuses(cls) -> list[str]:
        """Return a list of all possible step status values"""
        return [status.value for status in cls]

    @classmethod
    def get_active_statuses(cls) -> list[str]:
        """Return a list of values representing active statuses (not started or in progress)"""
        return [cls.NOT_STARTED.value, cls.IN_PROGRESS.value]

    @classmethod
    def get_status_marks(cls) -> Dict[str, str]:
        """Return a mapping of statuses to their marker symbols"""
        return {
            cls.COMPLETED.value: "[✓]",
            cls.IN_PROGRESS.value: "[→]",
            cls.BLOCKED.value: "[!]",
            cls.NOT_STARTED.value: "[ ]",
        }


def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class PlanningFlow(BaseFlow):
    """A flow that plans a task and executes the steps with a team of agents."""

    llm: LLM = Field(default_factory=LLM)
    planning_tool: PlanningTool = Field(default_factory=PlanningTool)
    executor_keys: List[str] = Field(default_factory=list)
    active_plan_id: str = Field(default_factory=lambda: f"plan_{uuid.uuid4().hex}")
    current_step_index: Optional[int] = None
    max_plan_steps: int = Field(default_factory=lambda: config.team.max_plan_steps)
    history: History = Field(default_factory=list)
    step_agents: List[str] = Field(
        default_factory=list, description="Agent key of every plan step"
    )

    _request: str = PrivateAttr(default="")

    def __init__(self, agents, **data):
        if "executors" in data:
            data["executor_keys"] = data.pop("executors")
        if "plan_id" in data:
            data["active_plan_id"] = data.pop("plan_id")
        if "history" in data:
            data["history"] = normalize_history(data["history"])

        super().__init__(agents, **data)

        self.executor_keys = [
            k for k in self.executor_keys if k in self.agents
        ] or list(self.agents.keys())

    # ------------------------------------------------------------------ members

    @property
    def default_agent_key(self) -> str:
        """Agent for untagged or unknown steps (the general agent when present)."""
        if DEFAULT_AGENT_KEY in self.executor_keys:
            return DEFAULT_AGENT_KEY
        return self.executor_keys[0]

    def _resolve_agent_key(self, tag: Optional[str]) -> str:
        """Map a step tag (key, alias or agent name, any case) to an executor key."""
        if tag:
            tag = tag.strip().lower().replace("-", "_")
            for candidate in (tag, _TAG_ALIASES.get(tag)):
                if candidate in self.executor_keys:
                    return candidate
            for key in self.executor_keys:
                if self.agents[key].name.lower().replace(" ", "_") == tag:
                    return key
        return self.default_agent_key

    def get_executor(self, step_type: Optional[str] = None) -> FlowAgent:
        """The team member for a step tag (the default agent when unknown)."""
        return self.agents[self._resolve_agent_key(step_type)]

    def _member_description(self, key: str) -> str:
        return self.agents[key].description or key

    # ---------------------------------------------------------------- execution

    async def execute(self, input_text: str) -> str:
        """Plan and execute ``input_text``; returns the final answer (Markdown)."""
        if not self.executor_keys:
            raise ValueError("No agents are available for the team")
        self._request = input_text

        await self._create_initial_plan(input_text)
        emit("plan.created", **self._plan_snapshot())

        # Every iteration finishes one step, so the cap is only a safety net.
        for _ in range(2 * len(self._plan_data()["steps"])):
            index = self._next_step_index()
            if index is None:
                break
            await self._execute_step(index)
        else:
            await self._block_remaining_steps("Stopped: iteration limit reached")

        return await self._finalize_plan()

    async def _create_initial_plan(self, request: str) -> None:
        """Ask the LLM for a tagged plan (with one retry) and store it."""
        logger.info(f"Creating initial plan with ID: {self.active_plan_id}")
        system_message = Message.system_message(
            PLANNER_SYSTEM_PROMPT.format(
                agents="\n".join(
                    f"- [{key}] {self._member_description(key)}"
                    for key in self.executor_keys
                ),
                max_steps=self.max_plan_steps,
                example_agent=self.default_agent_key,
            )
        )
        user_message = Message.user_message(
            PLAN_REQUEST_PROMPT.format(
                history=format_history(self.history), request=request
            )
        )

        plan = None
        for attempt in range(1, PLAN_ATTEMPTS + 1):
            response = await self.llm.ask_tool(
                messages=[user_message],
                system_msgs=[system_message],
                tools=[self.planning_tool.to_param()],
                tool_choice=ToolChoice.REQUIRED,
            )
            plan = self._parse_plan(response)
            if plan:
                break
            logger.warning(f"The planner returned no usable plan (attempt {attempt})")

        if plan:
            title, raw_steps = plan
        else:
            title = _shorten(request, TITLE_MAX_CHARS) or "Plan"
            raw_steps = [f"[{self.default_agent_key}] {request}"]

        steps, agents = [], []
        for raw_step in raw_steps[: self.max_plan_steps]:
            agent_key, text = self._split_step(raw_step)
            steps.append(text)
            agents.append(agent_key)
        self.step_agents = agents

        await self.planning_tool.execute(
            command="create", plan_id=self.active_plan_id, title=title, steps=steps
        )

    def _parse_plan(self, response) -> Optional[Tuple[str, List[str]]]:
        """(title, steps) from the planner's tool call, or None if unusable."""
        for call in (response.tool_calls if response else None) or []:
            if call.function.name != self.planning_tool.name:
                continue
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                continue
            raw_steps = arguments.get("steps") if isinstance(arguments, dict) else None
            if not isinstance(raw_steps, list):
                continue
            steps = [s.strip() for s in raw_steps if isinstance(s, str) and s.strip()]
            if steps:
                title = str(arguments.get("title") or "").strip()
                return _shorten(title, TITLE_MAX_CHARS) or "Plan", steps
        return None

    def _split_step(self, raw_step: str) -> Tuple[str, str]:
        """Split ``"[tag] text"`` into (agent key, text without the tag)."""
        match = STEP_TAG_RE.match(raw_step)
        if not match:
            return self.default_agent_key, raw_step.strip()
        text = raw_step[match.end() :].strip() or raw_step.strip()
        return self._resolve_agent_key(match.group(1)), text

    async def _execute_step(self, index: int) -> None:
        """Run one step on its agent and record the outcome (never raises Exception)."""
        agent_key = self.step_agents[index]
        step_text = self._plan_data()["steps"][index]
        self.current_step_index = index

        await self._mark_step(index, PlanStepStatus.IN_PROGRESS)
        emit("plan.step_started", index=index, agent=agent_key, text=step_text)
        emit("plan.updated", **self._plan_snapshot())

        try:
            status, summary = await self._run_step(agent_key, self._step_prompt(index))
        except Exception as e:
            logger.error(f"Team step {index + 1} ({agent_key}) failed: {e}")
            status, summary = PlanStepStatus.BLOCKED, f"Error: {e or type(e).__name__}"

        summary = truncate(summary or "(no result reported)", STEP_SUMMARY_MAX_CHARS)
        await self._mark_step(index, status, summary)
        emit(
            "plan.step_finished",
            index=index,
            agent=agent_key,
            status=status.value,
            summary=summary,
        )
        emit("plan.updated", **self._plan_snapshot())

    async def _run_step(
        self, agent_key: str, prompt: str
    ) -> Tuple[PlanStepStatus, str]:
        member = self.agents[agent_key]
        if isinstance(member, AgentSpec):
            agent = await member.factory()
            try:
                return await self._run_agent(agent, prompt)
            finally:
                await dispose_agent(agent)
        return await self._run_agent(member, prompt)

    @staticmethod
    async def _run_agent(agent: BaseAgent, prompt: str) -> Tuple[PlanStepStatus, str]:
        """Run ``agent`` on a step prompt; returns the step status and result summary."""
        skip = len(agent.memory.messages)
        await agent.run(prompt)
        if agent.finish_reason == "error":
            last_message = agent.memory.messages[-1] if agent.memory.messages else None
            detail = last_message.content if last_message else ""
            return PlanStepStatus.BLOCKED, f"Error: {detail or 'the agent failed'}"
        summary = (
            last_agent_reply(agent, skip)
            or agent_transcript(agent, skip)[-STEP_SUMMARY_MAX_CHARS:]
        )
        return PlanStepStatus.COMPLETED, summary

    def _step_prompt(self, index: int) -> str:
        plan = self._plan_data()
        notes = [
            f"Step {i + 1} [{self.step_agents[i]}] ({plan['step_statuses'][i]}): {note}"
            for i, note in enumerate(plan["step_notes"])
            if note and i != index
        ]
        return STEP_PROMPT.format(
            request=self._request,
            history=format_history(self.history),
            plan=self._plan_overview(),
            notes="\n\n".join(notes) or "(none yet)",
            number=index + 1,
            total=len(plan["steps"]),
            step=plan["steps"][index],
            workspace=get_workspace(),
        )

    async def _block_remaining_steps(self, note: str) -> None:
        index = self._next_step_index()
        while index is not None:
            await self._mark_step(index, PlanStepStatus.BLOCKED, note)
            index = self._next_step_index()
        emit("plan.updated", **self._plan_snapshot())

    async def _finalize_plan(self) -> str:
        """Write the final answer from all step results.

        If the answer cannot be generated but some steps completed, a plain summary
        of the step results is returned instead.
        """
        try:
            return await generate_final_answer(
                self._request, self._team_record(), history=self.history, llm=self.llm
            )
        except Exception as e:
            if PlanStepStatus.COMPLETED.value not in self._plan_data()["step_statuses"]:
                raise
            logger.error(f"Final answer generation failed ({e}); using step results")
            return self._fallback_answer()

    # -------------------------------------------------------------- plan state

    def _plan_data(self) -> dict:
        return self.planning_tool.plans[self.active_plan_id]

    async def _mark_step(
        self, index: int, status: PlanStepStatus, notes: Optional[str] = None
    ) -> None:
        await self.planning_tool.execute(
            command="mark_step",
            plan_id=self.active_plan_id,
            step_index=index,
            step_status=status.value,
            step_notes=notes,
        )

    def _next_step_index(self) -> Optional[int]:
        active = PlanStepStatus.get_active_statuses()
        for index, status in enumerate(self._plan_data()["step_statuses"]):
            if status in active:
                return index
        return None

    def _plan_snapshot(self) -> dict:
        """Full plan state for ``plan.*`` events."""
        plan = self._plan_data()
        return {
            "plan_id": self.active_plan_id,
            "title": plan["title"],
            "steps": [
                {
                    "index": i,
                    "text": text,
                    "agent": self.step_agents[i],
                    "status": plan["step_statuses"][i],
                    "notes": plan["step_notes"][i],
                }
                for i, text in enumerate(plan["steps"])
            ],
        }

    def _plan_overview(self) -> str:
        plan = self._plan_data()
        marks = PlanStepStatus.get_status_marks()
        lines = [f"Plan: {plan['title']}"]
        for i, (text, status) in enumerate(zip(plan["steps"], plan["step_statuses"])):
            lines.append(
                f"{i + 1}. {marks.get(status, '[ ]')} [{self.step_agents[i]}] {text}"
            )
        return "\n".join(lines)

    def _team_record(self) -> str:
        plan = self._plan_data()
        steps = "\n\n".join(
            f"Step {i + 1} [{self.step_agents[i]}]: {text}\n"
            f"Status: {plan['step_statuses'][i]}\n"
            f"Result: {plan['step_notes'][i] or '(none)'}"
            for i, text in enumerate(plan["steps"])
        )
        return TEAM_RECORD_TEMPLATE.format(title=plan["title"], steps=steps)

    def _fallback_answer(self) -> str:
        plan = self._plan_data()
        sections = [f"**{plan['title']}**"]
        for i, text in enumerate(plan["steps"]):
            icon = "✅" if plan["step_statuses"][i] == PlanStepStatus.COMPLETED else "⚠️"
            sections.append(f"{i + 1}. {icon} **{text}**\n\n{plan['step_notes'][i]}")
        return "\n\n".join(sections)
