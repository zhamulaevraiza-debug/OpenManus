import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, Dict, Optional

from pydantic import ConfigDict, Field, PrivateAttr

from app.daytona.sandbox import (
    get_daytona,
    provision_sandbox,
    start_supervisord_session,
)
from app.tool.base import BaseTool
from app.utils.files_utils import clean_path
from app.utils.logger import logger


if TYPE_CHECKING:  # pragma: no cover - typing only
    from daytona import Sandbox
else:
    Sandbox = Any


@dataclass
class ThreadMessage:
    """
    Represents a message to be added to a thread.
    """

    type: str
    content: Dict[str, Any]
    is_llm_message: bool = False
    metadata: Optional[Dict[str, Any]] = None
    timestamp: Optional[float] = field(
        default_factory=lambda: datetime.now().timestamp()
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert the message to a dictionary for API calls"""
        return {
            "type": self.type,
            "content": self.content,
            "is_llm_message": self.is_llm_message,
            "metadata": self.metadata or {},
            "timestamp": self.timestamp,
        }


class SandboxToolsBase(BaseTool):
    """Base class for all sandbox tools that provides project-based sandbox access.

    A sandbox is either injected (shared by the tools of one agent) or created lazily
    on first use. All Daytona SDK calls are executed in worker threads.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Kept for callers that mark the sandbox links as already announced.
    _urls_printed: ClassVar[bool] = False

    # Required fields
    project_id: Optional[str] = None

    workspace_path: str = Field(default="/workspace", exclude=True)

    _sandbox: Optional[Sandbox] = PrivateAttr(default=None)
    _sessions: Dict[str, str] = PrivateAttr(default_factory=dict)
    _sandbox_lock: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)

    async def _ensure_sandbox(self) -> Sandbox:
        """Ensure we have a running sandbox, creating or restarting it if needed."""
        async with self._sandbox_lock:
            if self._sandbox is None:
                try:
                    self._sandbox = await provision_sandbox(project_id=self.project_id)
                except Exception as e:
                    logger.error(f"Error retrieving or starting sandbox: {str(e)}")
                    raise
            else:
                from daytona import SandboxState

                if self._sandbox.state in (SandboxState.ARCHIVED, SandboxState.STOPPED):
                    logger.info(
                        f"Sandbox is in {self._sandbox.state} state. Starting..."
                    )
                    try:
                        await asyncio.to_thread(get_daytona().start, self._sandbox)
                        await start_supervisord_session(self._sandbox)
                    except Exception as e:
                        logger.error(f"Error starting sandbox: {e}")
                        raise
            return self._sandbox

    @property
    def sandbox(self) -> Sandbox:
        """Get the sandbox instance, ensuring it exists."""
        if self._sandbox is None:
            raise RuntimeError("Sandbox not initialized. Call _ensure_sandbox() first.")
        return self._sandbox

    @property
    def sandbox_id(self) -> str:
        """Get the sandbox ID, ensuring it exists."""
        return self.sandbox.id

    def clean_path(self, path: str) -> str:
        """Clean and normalize a path to be relative to /workspace."""
        cleaned_path = clean_path(path, self.workspace_path)
        logger.debug(f"Cleaned path: {path} -> {cleaned_path}")
        return cleaned_path
