import asyncio
import json
from typing import Any, List, Optional, Union

from pydantic import Field

from app.agent.react import ReActAgent
from app.context import current_run, emit
from app.exceptions import TokenLimitExceeded
from app.logger import logger
from app.prompt.toolcall import NEXT_STEP_PROMPT, SYSTEM_PROMPT
from app.schema import TOOL_CHOICE_TYPE, Message, ToolCall, ToolChoice
from app.tool.base import ToolResult
from app.tool.create_chat_completion import CreateChatCompletion
from app.tool.terminate import Terminate
from app.tool.tool_collection import ToolCollection
from app.utils.text import truncate


TOOL_CALL_REQUIRED = "Tool calls required but none provided"

# Size limits for event payloads (UI) and log previews.
EVENT_OUTPUT_LIMIT = 8000
LOG_PREVIEW_LIMIT = 500


def _event_arguments(raw_arguments: Optional[str]) -> dict:
    """Tool call arguments for events: the JSON object, or ``{"raw": text}``."""
    try:
        parsed = json.loads(raw_arguments or "{}")
    except (TypeError, json.JSONDecodeError):
        parsed = None
    if not isinstance(parsed, dict):
        return {"raw": truncate(raw_arguments or "", EVENT_OUTPUT_LIMIT)}
    return {
        key: truncate(value, EVENT_OUTPUT_LIMIT) if isinstance(value, str) else value
        for key, value in parsed.items()
    }


def _log_detail(message: str) -> None:
    """Log thoughts, arguments and results (truncated): visible on the CLI, DEBUG
    level in web runs where they belong to the user's event stream."""
    logger.log(
        "INFO" if current_run() is None else "DEBUG",
        truncate(message, LOG_PREVIEW_LIMIT),
    )


def _is_error_result(result: Any) -> bool:
    """Whether a tool's return value reports a failure."""
    if isinstance(result, ToolResult):
        return bool(result.error)
    if isinstance(result, dict) and "success" in result:
        return not result["success"]
    return False


class ToolCallAgent(ReActAgent):
    """Base agent class for handling tool/function calls with enhanced abstraction"""

    name: str = "toolcall"
    description: str = "an agent that can execute tool calls."

    system_prompt: str = SYSTEM_PROMPT
    next_step_prompt: str = NEXT_STEP_PROMPT

    available_tools: ToolCollection = Field(
        default_factory=lambda: ToolCollection(CreateChatCompletion(), Terminate())
    )
    tool_choices: TOOL_CHOICE_TYPE = ToolChoice.AUTO  # type: ignore
    special_tool_names: List[str] = Field(default_factory=lambda: [Terminate().name])

    tool_calls: List[ToolCall] = Field(default_factory=list)
    _current_base64_image: Optional[str] = None
    _current_tool_error: bool = False

    max_steps: int = 30
    max_observe: Optional[Union[int, bool]] = None
    cleanup_after_run: bool = Field(
        default=True, description="Release tool resources at the end of every run()"
    )

    def _step_prompt(self) -> Optional[str]:
        """The next-step prompt, prefixed by a pending stuck hint."""
        hint = self.consume_stuck_hint()
        if hint and self.next_step_prompt:
            return f"{hint}\n{self.next_step_prompt}"
        return hint or self.next_step_prompt

    async def think(self) -> bool:
        """Process current state and decide next actions using tools"""
        prompt = self._step_prompt()
        if prompt:
            self.memory.add_message(Message.user_message(prompt))

        try:
            # Get response with tool options
            response = await self.llm.ask_tool(
                messages=self.messages,
                system_msgs=(
                    [Message.system_message(self.system_prompt)]
                    if self.system_prompt
                    else None
                ),
                tools=self.available_tools.to_params(),
                tool_choice=self.tool_choices,
            )
        except TokenLimitExceeded as e:
            logger.error(f"🚨 Token limit error: {e}")
            self.memory.add_message(
                Message.assistant_message(
                    f"Maximum token limit reached, cannot continue execution: {e}"
                )
            )
            self.finish("error")
            return False

        self.tool_calls = tool_calls = (
            response.tool_calls if response and response.tool_calls else []
        )
        content = response.content if response and response.content else ""

        if content:
            emit(
                "agent.thought",
                agent=self.name,
                step=self.current_step,
                content=content,
            )
            _log_detail(f"✨ {self.name}'s thoughts: {content}")
        logger.info(
            f"🛠️ {self.name} selected {len(tool_calls)} tools to use"
            + (f": {[call.function.name for call in tool_calls]}" if tool_calls else "")
        )
        for call in tool_calls:
            _log_detail(f"🔧 {call.function.name} arguments: {call.function.arguments}")

        try:
            if response is None:
                raise RuntimeError("No response received from the LLM")

            # Handle different tool_choices modes
            if self.tool_choices == ToolChoice.NONE:
                if tool_calls:
                    logger.warning(
                        f"🤔 Hmm, {self.name} tried to use tools when they weren't available!"
                    )
                if content:
                    self.memory.add_message(Message.assistant_message(content))
                    return True
                return False

            # Create and add assistant message
            assistant_msg = (
                Message.from_tool_calls(content=content, tool_calls=self.tool_calls)
                if self.tool_calls
                else Message.assistant_message(content)
            )
            self.memory.add_message(assistant_msg)

            if self.tool_choices == ToolChoice.REQUIRED and not self.tool_calls:
                return True  # Will be handled in act()

            # In 'auto' mode a reply without tool calls is the agent's answer.
            if self.tool_choices == ToolChoice.AUTO and not self.tool_calls:
                self.finish("no_action")
                return bool(content)

            return bool(self.tool_calls)
        except Exception as e:
            logger.error(f"🚨 Oops! The {self.name}'s thinking process hit a snag: {e}")
            self.memory.add_message(
                Message.assistant_message(
                    f"Error encountered while processing: {str(e)}"
                )
            )
            return False

    async def act(self) -> str:
        """Execute tool calls and handle their results"""
        if not self.tool_calls:
            if self.tool_choices == ToolChoice.REQUIRED:
                raise ValueError(TOOL_CALL_REQUIRED)

            # Return last message content if no tool calls
            return self.messages[-1].content or "No content or commands to execute"

        results = []
        for command in self.tool_calls:
            # Reset per-call side channels of execute_tool
            self._current_base64_image = None
            self._current_tool_error = False
            emit(
                "tool.call",
                agent=self.name,
                step=self.current_step,
                call_id=command.id,
                name=command.function.name,
                arguments=_event_arguments(command.function.arguments),
            )

            result = await self.execute_tool(command)

            if self.max_observe:
                result = result[: self.max_observe]

            _log_detail(f"🎯 Tool '{command.function.name}' completed! Result: {result}")
            event = {
                "agent": self.name,
                "step": self.current_step,
                "call_id": command.id,
                "name": command.function.name,
                "output": truncate(result, EVENT_OUTPUT_LIMIT),
                "error": self._current_tool_error,
            }
            if self._current_base64_image:
                event["image_b64"] = self._current_base64_image
            emit("tool.result", **event)

            # Add tool response to memory
            tool_msg = Message.tool_message(
                content=result,
                tool_call_id=command.id,
                name=command.function.name,
                base64_image=self._current_base64_image,
            )
            self.memory.add_message(tool_msg)
            results.append(result)

        return "\n\n".join(results)

    async def execute_tool(self, command: ToolCall) -> str:
        """Execute a single tool call with robust error handling.

        Besides the returned observation, the base64 image of the result and whether
        it failed are recorded in ``_current_base64_image`` / ``_current_tool_error``.
        """
        if not command or not command.function or not command.function.name:
            self._current_tool_error = True
            return "Error: Invalid command format"

        name = command.function.name
        if name not in self.available_tools.tool_map:
            self._current_tool_error = True
            return f"Error: Unknown tool '{name}'"

        try:
            # Parse arguments
            args = json.loads(command.function.arguments or "{}")

            # Execute the tool
            logger.info(f"🔧 Activating tool: '{name}'...")
            result = await self.available_tools.execute(name=name, tool_input=args)

            # Handle special tools
            await self._handle_special_tool(name=name, result=result)

            # Keep the screenshot for the tool message and the tool.result event
            if getattr(result, "base64_image", None):
                self._current_base64_image = result.base64_image
            self._current_tool_error = _is_error_result(result)

            # Format result for display (standard case)
            observation = (
                f"Observed output of cmd `{name}` executed:\n{str(result)}"
                if result
                else f"Cmd `{name}` completed with no output"
            )

            return observation
        except json.JSONDecodeError:
            self._current_tool_error = True
            error_msg = f"Error parsing arguments for {name}: Invalid JSON format"
            logger.error(
                f"📝 Oops! The arguments for '{name}' don't make sense - invalid JSON, "
                f"arguments: {truncate(command.function.arguments or '', LOG_PREVIEW_LIMIT)}"
            )
            return f"Error: {error_msg}"
        except Exception as e:
            self._current_tool_error = True
            error_msg = f"⚠️ Tool '{name}' encountered a problem: {str(e)}"
            logger.exception(error_msg)
            return f"Error: {error_msg}"

    async def _handle_special_tool(self, name: str, result: Any, **kwargs):
        """Handle special tool execution and state changes"""
        if not self._is_special_tool(name):
            return

        if self._should_finish_execution(name=name, result=result, **kwargs):
            logger.info(f"🏁 Special tool '{name}' has completed the task!")
            self.finish("terminated")

    @staticmethod
    def _should_finish_execution(**kwargs) -> bool:
        """Determine if tool execution should finish the agent"""
        return True

    def _is_special_tool(self, name: str) -> bool:
        """Check if a tool is special, by name or by its original (e.g. MCP) name"""
        special = {n.lower() for n in self.special_tool_names}
        tool = self.available_tools.get_tool(name) if self.available_tools else None
        original_name = getattr(tool, "original_name", "") or ""
        return name.lower() in special or original_name.lower() in special

    async def cleanup(self):
        """Clean up resources used by the agent's tools."""
        logger.debug(f"🧹 Cleaning up resources for agent '{self.name}'...")
        for tool_name, tool_instance in self.available_tools.tool_map.items():
            if hasattr(tool_instance, "cleanup") and asyncio.iscoroutinefunction(
                tool_instance.cleanup
            ):
                try:
                    logger.debug(f"🧼 Cleaning up tool: {tool_name}")
                    await tool_instance.cleanup()
                except Exception as e:
                    logger.opt(exception=e).error(
                        f"🚨 Error cleaning up tool '{tool_name}': {e}"
                    )
        logger.debug(f"✨ Cleanup complete for agent '{self.name}'.")

    async def run(self, request: Optional[str] = None) -> str:
        """Run the agent, releasing tool resources afterwards (``cleanup_after_run``)."""
        try:
            return await super().run(request)
        finally:
            if self.cleanup_after_run:
                await self.cleanup()
