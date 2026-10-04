import asyncio
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from app.context import current_run, emit
from app.llm import LLM
from app.logger import logger
from app.schema import ROLE_TYPE, AgentState, Memory, Message


STUCK_PROMPT = (
    "Observed duplicate responses. Consider new strategies and avoid repeating "
    "ineffective paths already attempted."
)


class BaseAgent(BaseModel, ABC):
    """Abstract base class for managing agent state and execution.

    Provides foundational functionality for state transitions, memory management,
    and a step-based execution loop. Subclasses must implement the `step` method.

    While a run is active the loop reports ``agent.started``, ``agent.step``,
    ``agent.stuck`` and ``agent.finished`` events through :func:`app.context.emit`.
    """

    # Core attributes
    name: str = Field(..., description="Unique name of the agent")
    description: Optional[str] = Field(None, description="Optional agent description")
    title: Optional[str] = Field(
        None, description="Display name reported in events (defaults to the name)"
    )

    # Prompts
    system_prompt: Optional[str] = Field(
        None, description="System-level instruction prompt"
    )
    next_step_prompt: Optional[str] = Field(
        None, description="Prompt for determining next action"
    )

    # Dependencies
    llm: LLM = Field(default_factory=LLM, description="Language model instance")
    memory: Memory = Field(default_factory=Memory, description="Agent's memory store")
    state: AgentState = Field(
        default=AgentState.IDLE, description="Current agent state"
    )

    # Execution control
    max_steps: int = Field(default=10, description="Maximum steps before termination")
    current_step: int = Field(default=0, description="Current step in execution")

    duplicate_threshold: int = 2

    # Why the current run finished (reported in the agent.finished event)
    _finish_reason: Optional[str] = PrivateAttr(default=None)
    # Strategy hint injected into the next step only, after a stuck state was detected
    _stuck_hint: Optional[str] = PrivateAttr(default=None)

    # Allow extra fields for flexibility in subclasses
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    @model_validator(mode="after")
    def initialize_agent(self) -> "BaseAgent":
        """Initialize agent with default settings if not provided."""
        if self.llm is None or not isinstance(self.llm, LLM):
            self.llm = LLM(config_name=self.name.lower())
        if not isinstance(self.memory, Memory):
            self.memory = Memory()
        return self

    @asynccontextmanager
    async def state_context(self, new_state: AgentState):
        """Context manager for safe agent state transitions.

        Args:
            new_state: The state to transition to during the context.

        Yields:
            None: Allows execution within the new state.

        Raises:
            ValueError: If the new_state is invalid.
        """
        if not isinstance(new_state, AgentState):
            raise ValueError(f"Invalid state: {new_state}")

        previous_state = self.state
        self.state = new_state
        try:
            yield
        except Exception as e:
            self.state = AgentState.ERROR  # Transition to ERROR on failure
            raise e
        finally:
            self.state = previous_state  # Revert to previous state

    def update_memory(
        self,
        role: ROLE_TYPE,  # type: ignore
        content: str,
        base64_image: Optional[str] = None,
        **kwargs,
    ) -> None:
        """Add a message to the agent's memory.

        Args:
            role: The role of the message sender (user, system, assistant, tool).
            content: The message content.
            base64_image: Optional base64 encoded image.
            **kwargs: Additional arguments (e.g., tool_call_id for tool messages).

        Raises:
            ValueError: If the role is unsupported.
        """
        message_map = {
            "user": Message.user_message,
            "system": Message.system_message,
            "assistant": Message.assistant_message,
            "tool": lambda content, **kw: Message.tool_message(content, **kw),
        }

        if role not in message_map:
            raise ValueError(f"Unsupported message role: {role}")

        # Create message with appropriate parameters based on role
        kwargs = {"base64_image": base64_image, **(kwargs if role == "tool" else {})}
        self.memory.add_message(message_map[role](content, **kwargs))

    @property
    def finish_reason(self) -> Optional[str]:
        """Why the last run ended: terminated, no_action, max_steps, error or cancelled."""
        return self._finish_reason

    def finish(self, reason: str) -> None:
        """End the current run after this step; ``reason`` is reported in events."""
        self.state = AgentState.FINISHED
        self._finish_reason = reason

    async def run(self, request: Optional[str] = None) -> str:
        """Execute the agent's main loop asynchronously.

        Args:
            request: Optional initial user request to process.

        Returns:
            A string summarizing the execution results.

        Raises:
            RuntimeError: If the agent is not in IDLE state at start.
        """
        if self.state != AgentState.IDLE:
            raise RuntimeError(f"Cannot run agent from state: {self.state}")

        if request:
            self.update_memory("user", request)

        self.current_step = 0
        self._finish_reason = None
        self._stuck_hint = None
        emit(
            "agent.started",
            agent=self.name,
            title=self.title or self.name,
            max_steps=self.max_steps,
        )

        results: List[str] = []
        reason = "max_steps"
        try:
            async with self.state_context(AgentState.RUNNING):
                while (
                    self.current_step < self.max_steps
                    and self.state != AgentState.FINISHED
                ):
                    self.current_step += 1
                    logger.info(
                        f"{self.name}: executing step {self.current_step}/{self.max_steps}"
                    )
                    emit(
                        "agent.step",
                        agent=self.name,
                        step=self.current_step,
                        max_steps=self.max_steps,
                    )
                    step_result = await self.step()

                    # Check for stuck state
                    if self.is_stuck():
                        self.handle_stuck_state()

                    results.append(f"Step {self.current_step}: {step_result}")

                if self.state == AgentState.FINISHED:
                    reason = self._finish_reason or "terminated"
                else:
                    results.append(f"Terminated: Reached max steps ({self.max_steps})")
        except asyncio.CancelledError:
            reason = "cancelled"
            raise
        except Exception:
            reason = "error"
            raise
        finally:
            self._finish_reason = reason
            emit(
                "agent.finished",
                agent=self.name,
                steps=self.current_step,
                reason=reason,
            )

        # The docker sandbox client is process-global: only the single-user CLI may
        # tear it down after a run (web runs would destroy each other's sandbox).
        if current_run() is None:
            from app.sandbox.client import SANDBOX_CLIENT

            await SANDBOX_CLIENT.cleanup()
        return "\n".join(results) if results else "No steps executed"

    @abstractmethod
    async def step(self) -> str:
        """Execute a single step in the agent's workflow.

        Must be implemented by subclasses to define specific behavior.
        """

    def handle_stuck_state(self):
        """Ask the model to change strategy on its next step (one-shot hint)."""
        self._stuck_hint = STUCK_PROMPT
        logger.warning(f"Agent {self.name} detected stuck state, adding a hint")
        emit("agent.stuck", agent=self.name, step=self.current_step)

    def consume_stuck_hint(self) -> Optional[str]:
        """Return the pending stuck hint (if any) and clear it."""
        hint, self._stuck_hint = self._stuck_hint, None
        return hint

    @staticmethod
    def _response_signature(message: Message) -> Optional[Tuple]:
        """What an assistant message said and did (None when it is empty)."""
        calls = tuple(
            (call.function.name, call.function.arguments)
            for call in message.tool_calls or []
        )
        if not message.content and not calls:
            return None
        return message.content or "", calls

    def is_stuck(self) -> bool:
        """Check if the latest assistant response repeats earlier ones verbatim."""
        responses = [m for m in self.memory.messages if m.role == "assistant"]
        if len(responses) < 2:
            return False

        last = self._response_signature(responses[-1])
        if last is None:
            return False

        duplicate_count = sum(
            1 for msg in responses[:-1] if self._response_signature(msg) == last
        )
        return duplicate_count >= self.duplicate_threshold

    @property
    def messages(self) -> List[Message]:
        """Retrieve a list of messages from the agent's memory."""
        return self.memory.messages

    @messages.setter
    def messages(self, value: List[Message]):
        """Set the list of messages in the agent's memory."""
        self.memory.messages = value
