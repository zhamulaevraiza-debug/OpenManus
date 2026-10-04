from typing import Any, Dict, Optional

from pydantic import Field

from app.agent.manus import Manus
from app.config import config
from app.context import current_run
from app.logger import logger
from app.prompt.manus import SYSTEM_PROMPT
from app.tool.ask_human import AskHuman
from app.tool.terminate import Terminate
from app.tool.tool_collection import ToolCollection


# Working directory inside the Daytona sandbox
SANDBOX_WORKSPACE = "/workspace"


class SandboxManus(Manus):
    """A versatile general-purpose agent working in a Daytona cloud sandbox.

    Its tools (browser, files, shell, vision) operate inside a sandbox created by
    :meth:`create`; MCP tools from the configuration are available as for Manus.
    The sandbox is deleted by :meth:`cleanup`.
    """

    name: str = "SandboxManus"
    description: str = "A versatile agent that can solve various tasks using multiple sandbox-tools including MCP-based tools"

    system_prompt: str = SYSTEM_PROMPT.format(directory=SANDBOX_WORKSPACE)

    # Sandbox tools are added by initialize_sandbox_tools()
    available_tools: ToolCollection = Field(
        default_factory=lambda: ToolCollection(AskHuman(), Terminate())
    )

    sandbox: Optional[Any] = Field(default=None, exclude=True)
    sandbox_link: Dict[str, Dict[str, str]] = Field(default_factory=dict)

    @classmethod
    async def create(cls, **kwargs) -> "SandboxManus":
        """Create the agent with MCP connections and a fresh Daytona sandbox."""
        instance = cls(**kwargs)
        await instance.initialize_mcp_servers()
        instance._initialized = True
        try:
            await instance.initialize_sandbox_tools()
        except BaseException:
            await instance.cleanup()
            raise
        return instance

    async def initialize_sandbox_tools(self, password: Optional[str] = None) -> None:
        """Create a sandbox and add the tools operating inside it.

        Web runs get a random VNC password (announced with ``sandbox.ready``); the CLI
        uses the configured ``VNC_password`` and logs the preview URLs.
        """
        # Imported lazily: the Daytona SDK is optional and needs an API key.
        from app.daytona.sandbox import get_sandbox_links, provision_sandbox
        from app.daytona.tool_base import SandboxToolsBase
        from app.tool.sandbox.sb_browser_tool import SandboxBrowserTool
        from app.tool.sandbox.sb_files_tool import SandboxFilesTool
        from app.tool.sandbox.sb_shell_tool import SandboxShellTool
        from app.tool.sandbox.sb_vision_tool import SandboxVisionTool

        if password is None and current_run() is None:
            password = config.daytona.VNC_password
        sandbox = await provision_sandbox(password)
        self.sandbox = sandbox
        vnc_url, website_url = await get_sandbox_links(sandbox)
        self.sandbox_link[sandbox.id] = {"vnc": vnc_url, "website": website_url}
        if current_run() is None:
            logger.info(f"VNC URL: {vnc_url}")
            logger.info(f"Website URL: {website_url}")
        SandboxToolsBase._urls_printed = True

        self.available_tools.add_tools(
            SandboxBrowserTool(sandbox),
            SandboxFilesTool(sandbox),
            SandboxShellTool(sandbox),
            SandboxVisionTool(sandbox),
        )

    async def delete_sandbox(self, sandbox_id: str) -> None:
        """Delete a sandbox by ID."""
        from app.daytona.sandbox import delete_sandbox

        await delete_sandbox(sandbox_id)
        logger.info(f"Sandbox {sandbox_id} deleted successfully")
        self.sandbox_link.pop(sandbox_id, None)

    async def cleanup(self):
        """Release tools and MCP connections, then delete the sandbox."""
        try:
            await super().cleanup()
        finally:
            sandbox, self.sandbox = self.sandbox, None
            if sandbox is not None:
                try:
                    await self.delete_sandbox(sandbox.id)
                except Exception as e:
                    logger.error(f"Error deleting sandbox {sandbox.id}: {e}")
