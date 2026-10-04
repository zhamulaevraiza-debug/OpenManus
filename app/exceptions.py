class ToolError(Exception):
    """Raised when a tool encounters an error."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class WorkspaceViolation(ToolError):
    """Raised when a path escapes the workspace of the current run."""


class OpenManusError(Exception):
    """Base exception for all OpenManus errors"""


class TokenLimitExceeded(OpenManusError):
    """Exception raised when the token limit is exceeded"""
