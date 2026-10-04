import asyncio
import base64
import time
import uuid
from typing import Any, Dict, Literal, Optional

from pydantic import Field

from app.context import get_workspace
from app.daytona.tool_base import Sandbox, SandboxToolsBase
from app.tool.base import ToolResult
from app.utils.logger import logger


API_PORT = 8000
REQUEST_TIMEOUT_SECONDS = 30


KEYBOARD_KEYS = [
    "a",
    "b",
    "c",
    "d",
    "e",
    "f",
    "g",
    "h",
    "i",
    "j",
    "k",
    "l",
    "m",
    "n",
    "o",
    "p",
    "q",
    "r",
    "s",
    "t",
    "u",
    "v",
    "w",
    "x",
    "y",
    "z",
    "0",
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "enter",
    "esc",
    "backspace",
    "tab",
    "space",
    "delete",
    "ctrl",
    "alt",
    "shift",
    "win",
    "up",
    "down",
    "left",
    "right",
    "f1",
    "f2",
    "f3",
    "f4",
    "f5",
    "f6",
    "f7",
    "f8",
    "f9",
    "f10",
    "f11",
    "f12",
    "ctrl+c",
    "ctrl+v",
    "ctrl+x",
    "ctrl+z",
    "ctrl+a",
    "ctrl+s",
    "alt+tab",
    "alt+f4",
    "ctrl+alt+delete",
]
MOUSE_BUTTONS = ["left", "right", "middle"]
_COMPUTER_USE_DESCRIPTION = """\
A comprehensive computer automation tool that allows interaction with the desktop environment.
* This tool provides commands for controlling mouse, keyboard, and taking screenshots
* It maintains state including current mouse position
* Use this when you need to automate desktop applications, fill forms, or perform GUI interactions
Key capabilities include:
* Mouse Control: Move, click, drag, scroll
* Keyboard Input: Type text, press keys or key combinations
* Screenshots: Capture and save screen images
* Waiting: Pause execution for specified duration
"""


class ComputerUseTool(SandboxToolsBase):
    """Computer automation tool for controlling the desktop environment."""

    name: str = "computer_use"
    description: str = _COMPUTER_USE_DESCRIPTION
    parameters: dict = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "move_to",
                    "click",
                    "scroll",
                    "typing",
                    "press",
                    "wait",
                    "mouse_down",
                    "mouse_up",
                    "drag_to",
                    "hotkey",
                    "screenshot",
                ],
                "description": "The computer action to perform",
            },
            "x": {"type": "number", "description": "X coordinate for mouse actions"},
            "y": {"type": "number", "description": "Y coordinate for mouse actions"},
            "button": {
                "type": "string",
                "enum": MOUSE_BUTTONS,
                "description": "Mouse button for click/drag actions",
                "default": "left",
            },
            "num_clicks": {
                "type": "integer",
                "description": "Number of clicks",
                "enum": [1, 2, 3],
                "default": 1,
            },
            "amount": {
                "type": "integer",
                "description": "Scroll amount (positive for up, negative for down)",
                "minimum": -10,
                "maximum": 10,
            },
            "text": {"type": "string", "description": "Text to type"},
            "key": {
                "type": "string",
                "enum": KEYBOARD_KEYS,
                "description": "Key to press",
            },
            "keys": {
                "type": "string",
                "enum": KEYBOARD_KEYS,
                "description": "Key combination to press",
            },
            "duration": {
                "type": "number",
                "description": "Duration in seconds to wait",
                "default": 0.5,
            },
        },
        "required": ["action"],
        "dependencies": {
            "move_to": ["x", "y"],
            "click": [],
            "scroll": ["amount"],
            "typing": ["text"],
            "press": ["key"],
            "wait": [],
            "mouse_down": [],
            "mouse_up": [],
            "drag_to": ["x", "y"],
            "hotkey": ["keys"],
            "screenshot": [],
        },
    }
    # aiohttp.ClientSession, created on first request (aiohttp is imported lazily)
    session: Optional[Any] = Field(default=None, exclude=True)
    mouse_x: int = Field(default=0, exclude=True)
    mouse_y: int = Field(default=0, exclude=True)
    api_base_url: Optional[str] = Field(default=None, exclude=True)

    def __init__(self, sandbox: Optional[Sandbox] = None, **data):
        """Initialize with optional sandbox."""
        super().__init__(**data)
        if sandbox is not None:
            self._sandbox = sandbox

    @classmethod
    def create_with_sandbox(cls, sandbox: Sandbox) -> "ComputerUseTool":
        """Factory method to create a tool with sandbox."""
        return cls(sandbox=sandbox)  # 通过构造函数初始化

    async def _get_session(self):
        """Get or create aiohttp session for API requests."""
        import aiohttp

        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
            )
        return self.session

    async def _get_api_base_url(self) -> str:
        """Preview URL of the automation API inside the sandbox."""
        if self.api_base_url is None:
            sandbox = await self._ensure_sandbox()
            link = await asyncio.to_thread(sandbox.get_preview_link, API_PORT)
            self.api_base_url = link.url
        return self.api_base_url

    async def _api_request(
        self, method: str, endpoint: str, data: Optional[Dict] = None
    ) -> Dict:
        """Send request to automation service API."""
        try:
            session = await self._get_session()
            url = f"{await self._get_api_base_url()}/api{endpoint}"
            logger.debug(f"Computer API request: {method} {endpoint}")
            if method.upper() == "GET":
                async with session.get(url) as response:
                    result = await response.json()
            else:  # POST
                async with session.post(url, json=data) as response:
                    result = await response.json()
            return result
        except Exception as e:
            logger.error(f"Computer API request {endpoint} failed: {str(e)}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def _save_screenshot(image_b64: str) -> str:
        """Store a screenshot in the workspace; returns its workspace-relative path."""
        relative = (
            f"screenshots/screenshot_{time.strftime('%Y%m%d_%H%M%S')}_"
            f"{uuid.uuid4().hex[:6]}.png"
        )
        target = get_workspace() / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(image_b64))
        return relative

    async def execute(
        self,
        action: Literal[
            "move_to",
            "click",
            "scroll",
            "typing",
            "press",
            "wait",
            "mouse_down",
            "mouse_up",
            "drag_to",
            "hotkey",
            "screenshot",
        ],
        x: Optional[float] = None,
        y: Optional[float] = None,
        button: str = "left",
        num_clicks: int = 1,
        amount: Optional[int] = None,
        text: Optional[str] = None,
        key: Optional[str] = None,
        keys: Optional[str] = None,
        duration: float = 0.5,
        **kwargs,
    ) -> ToolResult:
        """
        Execute a specified computer automation action.
        Args:
            action: The action to perform
            x: X coordinate for mouse actions
            y: Y coordinate for mouse actions
            button: Mouse button for click/drag actions
            num_clicks: Number of clicks to perform
            amount: Scroll amount (positive for up, negative for down)
            text: Text to type
            key: Key to press
            keys: Key combination to press
            duration: Duration in seconds to wait
            **kwargs: Additional arguments
        Returns:
            ToolResult with the action's output or error
        """
        try:
            if action == "move_to":
                if x is None or y is None:
                    return ToolResult(error="x and y coordinates are required")
                x_int = int(round(float(x)))
                y_int = int(round(float(y)))
                result = await self._api_request(
                    "POST", "/automation/mouse/move", {"x": x_int, "y": y_int}
                )
                if result.get("success", False):
                    self.mouse_x = x_int
                    self.mouse_y = y_int
                    return ToolResult(output=f"Moved to ({x_int}, {y_int})")
                else:
                    return ToolResult(
                        error=f"Failed to move: {result.get('error', 'Unknown error')}"
                    )
            elif action == "click":
                x_val = x if x is not None else self.mouse_x
                y_val = y if y is not None else self.mouse_y
                x_int = int(round(float(x_val)))
                y_int = int(round(float(y_val)))
                num_clicks = int(num_clicks)
                result = await self._api_request(
                    "POST",
                    "/automation/mouse/click",
                    {
                        "x": x_int,
                        "y": y_int,
                        "clicks": num_clicks,
                        "button": button.lower(),
                    },
                )
                if result.get("success", False):
                    self.mouse_x = x_int
                    self.mouse_y = y_int
                    return ToolResult(
                        output=f"{num_clicks} {button} click(s) performed at ({x_int}, {y_int})"
                    )
                else:
                    return ToolResult(
                        error=f"Failed to click: {result.get('error', 'Unknown error')}"
                    )
            elif action == "scroll":
                if amount is None:
                    return ToolResult(error="Scroll amount is required")
                amount = int(float(amount))
                amount = max(-10, min(10, amount))
                result = await self._api_request(
                    "POST",
                    "/automation/mouse/scroll",
                    {"clicks": amount, "x": self.mouse_x, "y": self.mouse_y},
                )
                if result.get("success", False):
                    direction = "up" if amount > 0 else "down"
                    steps = abs(amount)
                    return ToolResult(
                        output=f"Scrolled {direction} {steps} step(s) at position ({self.mouse_x}, {self.mouse_y})"
                    )
                else:
                    return ToolResult(
                        error=f"Failed to scroll: {result.get('error', 'Unknown error')}"
                    )
            elif action == "typing":
                if text is None:
                    return ToolResult(error="Text is required for typing")
                text = str(text)
                result = await self._api_request(
                    "POST",
                    "/automation/keyboard/write",
                    {"message": text, "interval": 0.01},
                )
                if result.get("success", False):
                    return ToolResult(output=f"Typed: {text}")
                else:
                    return ToolResult(
                        error=f"Failed to type: {result.get('error', 'Unknown error')}"
                    )
            elif action == "press":
                if key is None:
                    return ToolResult(error="Key is required for press action")
                key = str(key).lower()
                result = await self._api_request(
                    "POST", "/automation/keyboard/press", {"keys": key, "presses": 1}
                )
                if result.get("success", False):
                    return ToolResult(output=f"Pressed key: {key}")
                else:
                    return ToolResult(
                        error=f"Failed to press key: {result.get('error', 'Unknown error')}"
                    )
            elif action == "wait":
                duration = float(duration)
                duration = max(0, min(10, duration))
                await asyncio.sleep(duration)
                return ToolResult(output=f"Waited {duration} seconds")
            elif action == "mouse_down":
                x_val = x if x is not None else self.mouse_x
                y_val = y if y is not None else self.mouse_y
                x_int = int(round(float(x_val)))
                y_int = int(round(float(y_val)))
                result = await self._api_request(
                    "POST",
                    "/automation/mouse/down",
                    {"x": x_int, "y": y_int, "button": button.lower()},
                )
                if result.get("success", False):
                    self.mouse_x = x_int
                    self.mouse_y = y_int
                    return ToolResult(
                        output=f"{button} button pressed at ({x_int}, {y_int})"
                    )
                else:
                    return ToolResult(
                        error=f"Failed to press button: {result.get('error', 'Unknown error')}"
                    )
            elif action == "mouse_up":
                x_val = x if x is not None else self.mouse_x
                y_val = y if y is not None else self.mouse_y
                x_int = int(round(float(x_val)))
                y_int = int(round(float(y_val)))
                result = await self._api_request(
                    "POST",
                    "/automation/mouse/up",
                    {"x": x_int, "y": y_int, "button": button.lower()},
                )
                if result.get("success", False):
                    self.mouse_x = x_int
                    self.mouse_y = y_int
                    return ToolResult(
                        output=f"{button} button released at ({x_int}, {y_int})"
                    )
                else:
                    return ToolResult(
                        error=f"Failed to release button: {result.get('error', 'Unknown error')}"
                    )
            elif action == "drag_to":
                if x is None or y is None:
                    return ToolResult(error="x and y coordinates are required")
                target_x = int(round(float(x)))
                target_y = int(round(float(y)))
                start_x = self.mouse_x
                start_y = self.mouse_y
                result = await self._api_request(
                    "POST",
                    "/automation/mouse/drag",
                    {"x": target_x, "y": target_y, "duration": 0.3, "button": "left"},
                )
                if result.get("success", False):
                    self.mouse_x = target_x
                    self.mouse_y = target_y
                    return ToolResult(
                        output=f"Dragged from ({start_x}, {start_y}) to ({target_x}, {target_y})"
                    )
                else:
                    return ToolResult(
                        error=f"Failed to drag: {result.get('error', 'Unknown error')}"
                    )
            elif action == "hotkey":
                if keys is None:
                    return ToolResult(error="Keys are required for hotkey action")
                keys = str(keys).lower().strip()
                key_sequence = keys.split("+")
                result = await self._api_request(
                    "POST",
                    "/automation/keyboard/hotkey",
                    {"keys": key_sequence, "interval": 0.01},
                )
                if result.get("success", False):
                    return ToolResult(output=f"Pressed key combination: {keys}")
                else:
                    return ToolResult(
                        error=f"Failed to press keys: {result.get('error', 'Unknown error')}"
                    )
            elif action == "screenshot":
                result = await self._api_request("POST", "/automation/screenshot")
                if "image" in result:
                    base64_str = result["image"]
                    saved = await asyncio.to_thread(self._save_screenshot, base64_str)
                    return ToolResult(
                        output=f"Screenshot saved as {saved}",
                        base64_image=base64_str,
                    )
                else:
                    return ToolResult(error="Failed to capture screenshot")
            else:
                return ToolResult(error=f"Unknown action: {action}")
        except Exception as e:
            return ToolResult(error=f"Computer action failed: {str(e)}")

    async def cleanup(self):
        """Clean up resources."""
        if self.session and not self.session.closed:
            await self.session.close()
        self.session = None
