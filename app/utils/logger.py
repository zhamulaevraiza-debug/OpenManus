"""structlog logger used by tools and the Daytona integration.

Every structlog event is forwarded to the process-wide loguru logger (``app.logger``)
so that all logs share the same sinks, levels and rotation.
"""

import logging

import structlog

from app.logger import logger as _loguru


_LEVEL_ALIASES = {
    "warn": "WARNING",
    "exception": "ERROR",
    "fatal": "CRITICAL",
    "msg": "INFO",
}
_CALLSITE_KEYS = ("filename", "func_name", "lineno")


def _forward_to_loguru(_logger, method_name: str, event_dict: dict):
    """Final structlog processor: emit the event through loguru and stop."""
    level = _LEVEL_ALIASES.get(method_name, method_name.upper())
    message = str(event_dict.pop("event", ""))
    exception = event_dict.pop("exception", None)
    filename, func_name, lineno = (event_dict.pop(key, None) for key in _CALLSITE_KEYS)
    event_dict.pop("level", None)

    if event_dict:
        extras = " ".join(f"{key}={value!r}" for key, value in event_dict.items())
        message = f"{message} | {extras}"
    if filename:
        message = f"[{filename}:{func_name}:{lineno}] {message}"
    if exception:
        message = f"{message}\n{exception}"

    _loguru.log(level, message)
    raise structlog.DropEvent


structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.CallsiteParameterAdder(
            {
                structlog.processors.CallsiteParameter.FILENAME,
                structlog.processors.CallsiteParameter.FUNC_NAME,
                structlog.processors.CallsiteParameter.LINENO,
            }
        ),
        structlog.processors.format_exc_info,
        _forward_to_loguru,
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
    logger_factory=structlog.ReturnLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger: structlog.typing.FilteringBoundLogger = structlog.get_logger()
